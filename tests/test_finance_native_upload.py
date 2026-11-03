"""HTTP上传正式入口/pending/回读/原marker恢复，全部为合成字节，无网络。"""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from assistant_api import AssistantService,atomic_json,read_json
from finance_native_upload import command,run,verify_ack,SCHEMA


class NativeUploadTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name).resolve()
        self.service=AssistantService(self.root,[self.root]);self.service.new_case('fixture')
        self.bundle,self.paths=self.service.resolve('fixture')
        form=read_json(self.paths['form']);form.update(serial=None,amount='10.00',expected_userid='TEST001')
        atomic_json(self.paths['form'],form)
        self.raw=self.root/'invoice.pdf';self.raw.write_bytes(b'synthetic invoice bytes, never sent')
        manifest=read_json(self.paths['invoices']);manifest.update(expected_userid='TEST001',serial=None,
            original_files=[{'path':str(self.raw),'sha256':hashlib.sha256(self.raw.read_bytes()).hexdigest(),
                'invoice_number_candidates':['9'*20],'visual_check':'verified'}])
        atomic_json(self.paths['invoices'],manifest)
        self.folder=self.root/'job';self.folder.mkdir();self.pending=self.paths['state'].parent/'pending_write.json'
        self.request={'case_id':'fixture','operation':'invoice','parameters':{'action':'upload','tab_id':'7','count':1},
            'request_id':'stable-fixture','execute':True}
        command(self.service,self.request,self.folder);self.input=self.folder/'native-upload-input.json'
        self.c=read_json(self.input);self.actions=[];self.lose_upload=False;self.lose_save=False

    def body(self,action):
        files=[{**f,'filecode':'c'*32,'received':f['size'],'phase':'upload_received'}for f in self.c['batch']['files']]
        result={'ok':True,'action':action,'case_id':'fixture','expected_userid':'TEST001','business_transport':'native_http',
            'token':self.c['token'],'batch':self.c['batch'],'files':files,'save_dispatched':action=='save',
            'phase':'save_accepted'if action=='save'else action,'system_write':action in {'upload','save'}}
        if action=='status':result.update(save_dispatched=True,phase='save_requested')
        if action=='prepare':result.update(complete=True,sources=self.c['sources'],original_guids=[],definition_sha256=self.c['upload_contract']['definition_sha256'])
        return result

    def runtime(self,args):
        script=Path(args[2]);receipt=Path(args[args.index('--output')+1]);text=script.read_text()
        c=json.loads(text.rsplit('return await reimbursementUploadApi(',1)[1].removesuffix(');})()'));action=c['action']
        self.actions.append(action)
        self.assertEqual(self.pending.exists(),action!='prepare')
        record={'phase':'script_completed','source_sha256':hashlib.sha256(script.read_bytes()).hexdigest(),
            'operation_kind':args[args.index('--operation-kind')+1],'result':self.body(action),
            'created_at_utc':'2028-01-01T00:00:00+00:00','dispatch_intent_at_utc':'2028-01-01T00:00:00+00:00',
            'executed_at_utc':'2028-01-01T00:00:01+00:00','result_marker':'reimbursement-result-'+'a'*32}
        if action=='upload' and self.lose_upload:record['phase']='unknown';record['result']={'ok':False}
        if action=='save' and self.lose_save:record['phase']='unknown';record['result']={'ok':False}
        atomic_json(receipt,record)
        return SimpleNamespace(returncode=1 if action=='upload'and self.lose_upload or action=='save'and self.lose_save else 0)

    def execute(self,**kwargs):
        with patch('registered_page_owner.RegisteredPageOwner.before_child_dispatch'):
            return run(self.input,self.folder/'output.json',runtime=self.runtime,**kwargs)

    def inventory(self,*,error=False):
        pool=[]if error else[{'t.tk_no':'9'*20,'t.tk_guid':'b'*32,'t.tk_mod_flag':'NORMAL','t.auth_rst_desc':'正常(已认证)'}]
        recognition=[{'t.show_filename':'invoice.pdf','t.filesequcode':'c'*32,'t.show_err_msg':'synthetic recognition error'}]if error else[]
        return {'phase':'script_completed','dispatch_intent_at_utc':'2028-01-01T00:00:02+00:00','executed_at_utc':'2028-01-01T00:00:03+00:00',
            'result':{'ok':True,'complete':True,'action':'invoice-inventory','case_id':'fixture','expected_userid':'TEST001',
                'business_transport':'native_http','system_write':False,'projid':'WF_YBX','identity':{'userid':'TEST001'},
                'grids':[{'funcno':'53','rows':pool,'records':len(pool),'complete':True},
                    {'funcno':'113','rows':recognition,'records':len(recognition),'complete':True}]}}

    def test_pending_precedes_multipart_and_save_then_fresh_complete_inventory_ack(self):
        self.assertEqual(self.execute(),0)
        self.assertEqual(self.actions,['prepare','start','append','upload','save','finish'])
        self.assertTrue(self.pending.exists());self.assertFalse(read_json(self.folder/'output.json')['complete'])
        previous=read_json(self.pending);proof=verify_ack(self.paths,previous,self.inventory(),'saved')
        self.assertTrue(proof['independent_http_readback']);self.assertEqual(proof['accounted_files'][0]['native_guid'],'b'*32)
        read=self.folder/'independent-inventory.json';atomic_json(read,self.inventory())
        from reimburse import acknowledge
        acknowledge(self.paths,read,'synthetic HTTP source/filecode/GUID full readback','saved')
        self.assertFalse(self.pending.exists());self.assertEqual(read_json(self.paths['state'])['last_native_upload_batch']['schema'],SCHEMA)

    def test_unknown_upload_recovers_only_old_marker_and_never_reuploads(self):
        self.lose_upload=True
        with self.assertRaises(ValueError):self.execute()
        self.assertTrue(self.pending.exists());self.assertNotIn('save',self.actions)
        before=list(self.actions);self.lose_upload=False
        with patch('finance_backend.assert_original_transport'),patch('edge_runtime.read_marker',return_value=self.body('upload'))as recover:
            self.execute(resume=True)
        self.assertEqual(self.actions,before+['save','finish']);recover.assert_called_once()
        self.assertEqual(self.actions.count('upload'),1)
        self.assertTrue((self.folder/'file-0-upload-original-marker.json').is_file())

    def test_independent_read_must_preserve_account_scope_completeness_and_exact_filecodes(self):
        self.execute();previous=read_json(self.pending)
        for mode in ['old_read','other_account','incomplete','missing','wrong_guid','not_authenticated','source_changed']:
            read=self.inventory()
            if mode=='old_read':read['dispatch_intent_at_utc']='2027-01-01T00:00:00+00:00'
            if mode=='other_account':read['result']['expected_userid']='OTHER'
            if mode=='incomplete':read['result']['grids'][0]['complete']=False
            if mode=='missing':read['result']['grids'][0].update(rows=[],records=0)
            if mode=='wrong_guid':read['result']['grids'][0]['rows'][0]['t.tk_guid']='invalid'
            if mode=='not_authenticated':read['result']['grids'][0]['rows'][0]['t.auth_rst_desc']='pending'
            if mode=='source_changed':self.raw.write_bytes(b'changed')
            with self.subTest(mode=mode),self.assertRaises(ValueError):verify_ack(self.paths,previous,read,'saved')
        self.assertTrue(self.pending.exists())

    def test_lost_save_response_reads_original_status_then_inventory_without_saving_twice(self):
        self.lose_save=True
        with self.assertRaises(ValueError):self.execute()
        self.lose_save=False;before=list(self.actions)
        with patch('finance_backend.assert_original_transport'),patch('edge_runtime.read_marker',return_value={'ok':False,'error':'original HTTP response lost'}):
            self.execute(resume=True)
        self.assertEqual(self.actions,before+['status']);self.assertEqual(self.actions.count('save'),1)
        proof=verify_ack(self.paths,read_json(self.pending),self.inventory(),'saved')
        self.assertTrue(proof['saved'])

    def test_only_proved_local_failure_before_multipart_can_be_not_applied(self):
        original=self.runtime
        def reject_local(args):
            result=original(args)
            if self.actions[-1]=='append':
                path=Path(args[args.index('--output')+1]);r=read_json(path);r['result']={'ok':False,'error':'local staging refused'};atomic_json(path,r)
                return SimpleNamespace(returncode=1)
            return result
        with patch('registered_page_owner.RegisteredPageOwner.before_child_dispatch'):
            with self.assertRaises(ValueError):run(self.input,self.folder/'output.json',runtime=reject_local)
        self.assertNotIn('upload',self.actions)
        proof=verify_ack(self.paths,read_json(self.pending),self.inventory(),'not_applied')
        self.assertTrue(proof['no_upload_dispatched'])
        receipt=self.folder/'file-0-append-0.json';r=read_json(receipt);r['phase']='unknown';atomic_json(receipt,r)
        with self.assertRaises(ValueError):verify_ack(self.paths,read_json(self.pending),self.inventory(),'not_applied')

    def test_proved_window_mismatch_before_save_allows_only_first_dispatch_after_binding(self):
        original=self.runtime
        def reject_binding(args):
            result=original(args)
            if self.actions[-1]=='save':
                path=Path(args[args.index('--output')+1]);r=read_json(path)
                r.update(phase='not_dispatched',result={'ok':False,'injection_acknowledged':False,'error':'Mac财务桥拒绝/失败：tab_window_mismatch'})
                atomic_json(path,r);return SimpleNamespace(returncode=1)
            return result
        with patch('registered_page_owner.RegisteredPageOwner.before_child_dispatch'):
            with self.assertRaises(ValueError):run(self.input,self.folder/'output.json',runtime=reject_binding)
        self.assertEqual(read_json(self.folder/'upload-save.json')['phase'],'not_dispatched')
        self.execute(resume=True)
        self.assertTrue((self.folder/'upload-save-after-binding.json').is_file())
        self.assertEqual(read_json(self.folder/'upload-save.json')['phase'],'not_dispatched')
        self.assertTrue(verify_ack(self.paths,read_json(self.pending),self.inventory(),'saved')['saved'])

    def test_missing_manifest_identity_uses_explicit_case_identity_without_editing_original(self):
        manifest=read_json(self.paths['invoices']);manifest.pop('expected_userid');atomic_json(self.paths['invoices'],manifest)
        digest=hashlib.sha256(self.paths['invoices'].read_bytes()).hexdigest();folder=self.root/'explicit';folder.mkdir()
        command(self.service,self.request,folder)
        c=read_json(folder/'native-upload-input.json');self.assertEqual(c['identity_source'],'explicit_execution_identity')
        self.assertEqual(c['expected_userid'],'TEST001');self.assertEqual(hashlib.sha256(self.paths['invoices'].read_bytes()).hexdigest(),digest)
        self.assertNotIn('expected_userid',read_json(self.paths['invoices']))

    def test_recognition_error_is_not_success_and_requires_matching_batch_filecode(self):
        self.execute();previous=read_json(self.pending);read=self.inventory(error=True)
        with self.assertRaises(ValueError):verify_ack(self.paths,previous,read,'saved')
        proof=verify_ack(self.paths,previous,read,'needs_correction');self.assertFalse(proof['saved'])
        read['result']['grids'][1]['rows'][0]['t.filesequcode']='d'*32
        with self.assertRaises(ValueError):verify_ack(self.paths,previous,read,'needs_correction')

    def test_multi_invoice_medical_code_number_and_partial_recognition_are_accounted(self):
        manifest=read_json(self.paths['invoices']);row=manifest['original_files'][0]
        row.update(invoice_number_candidates=['9'*20,'1234567890'],invoice_identities=[
            {'kind':'vat','code':'','number':'9'*20},{'kind':'medical','code':'12345678','number':'1234567890'}])
        atomic_json(self.paths['invoices'],manifest)
        other=self.root/'multi';other.mkdir();command(self.service,self.request,other)
        self.folder=other;self.input=other/'native-upload-input.json';self.c=read_json(self.input)
        self.execute();previous=read_json(self.pending);read=self.inventory()
        medical={'t.tk_guid':'d'*32,'t.tk_mod_flag':'NORMAL','t.auth_rst_desc':'正常(已认证)',
            't.tk_type':'MEDICAL','t.tk_code':'12345678','t.tk_code_abst':'医/12345678/1234567890'}
        read['result']['grids'][0]['rows'].append(medical);read['result']['grids'][0]['records']=2
        proof=verify_ack(self.paths,previous,read,'saved');self.assertEqual(len(proof['accounted_files'][0]['recognized_invoices']),2)
        medical['t.tk_code']='87654321'
        with self.assertRaises(ValueError):verify_ack(self.paths,previous,read,'saved')
        read=self.inventory();read['result']['grids'][1].update(rows=self.inventory(error=True)['result']['grids'][1]['rows'],records=1)
        proof=verify_ack(self.paths,previous,read,'needs_correction');entry=proof['accounted_files'][0]
        self.assertTrue(entry['partial_recognition']);self.assertEqual(len(entry['unresolved_identities']),1)
        with self.assertRaises(ValueError):verify_ack(self.paths,previous,read,'saved')

    def test_medical_without_code_duplicate_or_unlisted_identity_rejected_before_dispatch(self):
        original=read_json(self.paths['invoices'])
        for mode in ['missing_code','mismatch','duplicate']:
            manifest=copy.deepcopy(original);row=manifest['original_files'][0]
            if mode=='missing_code':row['invoice_number_candidates']=['1234567890']
            elif mode=='mismatch':row['invoice_identities']=[{'kind':'vat','code':'','number':'8'*20}]
            else:row['invoice_identities']=[{'kind':'vat','code':'','number':'9'*20}]*2
            atomic_json(self.paths['invoices'],manifest);folder=self.root/mode;folder.mkdir()
            with self.subTest(mode=mode),self.assertRaises(ValueError):command(self.service,self.request,folder)
            self.assertFalse(self.pending.exists())

    def test_new_request_cannot_bypass_pending_or_existing_serial_or_changed_source(self):
        self.execute();other=self.root/'other';other.mkdir()
        command(self.service,{**self.request,'request_id':'another'},other)
        with patch('registered_page_owner.RegisteredPageOwner.before_child_dispatch')as bridge:
            with self.assertRaises(ValueError):run(other/'native-upload-input.json',other/'output.json',runtime=self.runtime)
            bridge.assert_not_called()
        form=read_json(self.paths['form']);form['serial']='12345';atomic_json(self.paths['form'],form)
        with self.assertRaises(ValueError):command(self.service,self.request,self.root/'no-created-case-upload')


if __name__=='__main__':unittest.main()
