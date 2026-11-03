"""逐票减报原意图、独立持久回读和原marker恢复；全部使用合成材料。"""
from contextlib import nullcontext
import copy
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'tools'),str(Path(__file__).resolve().parents[1])]
from assistant_api import AssistantService,atomic_json,read_json
from reimburse import acknowledge
import finance_native_amount as n


class NativeAmountTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve()
        self.service=AssistantService(self.root,[self.root]);self.service.new_case('amount_fixture');self.bundle,self.paths=self.service.resolve('amount_fixture')
        form=read_json(self.paths['form']);form.update(serial='123',amount='10.00',expected_userid='TEST001');atomic_json(self.paths['form'],form)
        self.original=self.root/'original.pdf';self.original.write_bytes(b'synthetic exact claim source')
        self.pending=self.paths['state'].parent/'pending_write.json';self.jobs={};self.steps=[];self.counter=0;self.amount='12.00'
        self.params={'group':'测试组','subject':'30201','expected_userid':'TEST001','tab_id':'7'}
        self.guid='A'*32

    def now(self):return datetime(2028,1,1,tzinfo=timezone.utc)+timedelta(seconds=self.counter*10)

    def body(self,c,rows):
        amount=self.amount;entry={'group':'测试组','subject':'30201','parameters':'测试组&30201&测试组!P!x','amount':amount,'title':'fixed','handler':'fixed'}
        row={'t.t_desc':'发票'+ '9'*20,'t.invoice_amt_xs':'12.00','t.zf_zmt':'0.00','t.t_amt':amount,'t.invoice_amt':'12.00',
             't.old_amt':amount,'t.serial_no':'123','t.tk_guid':self.guid,'t.ord':'1'}
        result={'ok':True,'complete':True,'action':'amount-inspect','case_id':'amount_fixture','serial':'123','expected_userid':'TEST001',
            'business_transport':'native_http','definition_sha256':c['amount_contract']['definition_sha256'],'system_write':False,'save_dispatched':False,
            'bill':{'t.serial_no':'123','t.total_amt':amount,'t.status':'-99'},'basic_sections':[],'entries':[entry],'entry':entry,
            'target_group_found':True,'rows':[row]if rows else None,'amount_rows_complete':rows,'allocation_tree':[],'allocation_tree_complete':True}
        if getattr(self,'whole_bill',False):
            from decimal import Decimal
            entry['amount']=str(Decimal(amount)-4)
            result['entries'].append(dict(entry,parameters='other&30201&other',group='other',amount='4.00'))
            if rows:result.update(editor_scope='whole_bill',owned_invoice_rows=[{'t.serial_no':'123','t.tk_guid':self.guid,'t.tk_total_amt':'12.00','t.tk_code_abst':'9'*20}])
        return result

    def runtime(self,c,method,output,extra=None):
        self.counter+=1;self.steps.append(method);output=Path(output);script=output.with_suffix('.js');script.write_text(n.script(c,method,extra),encoding='utf-8',newline='\n')
        if method=='reimbursementAmountInitialize':
            self.assertTrue(self.pending.exists());body={**self.body(c,False),'action':'amount-initialize','initialized':True,'save_dispatched':True,'system_write':True,'financial_amount_save_requested':False}
        elif method=='reimbursementAmountSave':
            self.assertTrue(self.pending.exists());self.amount='10.00';body={**self.body(c,True),'action':'amount-save','save_dispatched':True,'system_write':True,
                'financial_amount_save_requested':True,'branch':{'allocation_required':False,'count':0},'persistence_refresh':{'ok':True,'initialized':True}}
        else:body=self.body(c,c['initialization_verified'])
        envelope={'phase':'script_completed','tab_id':'7','script':str(script),'source_sha256':n.sha(script),'result':body,
            'dispatch_intent_at_utc':self.now().isoformat(),'executed_at_utc':(self.now()+timedelta(seconds=1)).isoformat()}
        atomic_json(output,envelope);return envelope

    def typed(self,case,job,artifact,operation,actions):
        folder=self.jobs[job];request=read_json(folder/'request.json')
        self.assertEqual(case,'amount_fixture');self.assertEqual(operation,'expense_amount');self.assertIn(request['parameters']['action'],actions)
        return folder/'output.json',read_json(folder/'output.json'),request

    def job(self,name,action,**extra):
        folder=self.root/name;folder.mkdir();self.jobs[name]=folder
        request={'case_id':'amount_fixture','operation':'expense_amount','parameters':{**self.params,'action':action,**extra},'execute':action in {'open','save'},'request_id':name}
        atomic_json(folder/'request.json',request)
        with patch.object(self.service,'_typed_case_artifact',side_effect=self.typed),patch.object(self.service,'job_folder',side_effect=lambda case,job:self.jobs[job]):
            n.command(self.service,request,folder)
        with patch.object(n,'browser',side_effect=self.runtime):n.run(folder/'native-amount-input.json',folder/'output.json')
        return folder

    def prepare_rows(self):
        self.job('inventory','inspect');self.job('open','open',source_job_id='inventory',source_artifact_id='output')
        self.job('rows','inspect',job_id='open',artifact_id='output')
        acknowledge(self.paths,self.jobs['rows']/'output.json','合成初始化后逐票完整读取','staged')
        self.assertFalse(self.pending.exists())
        claims=[{'invoice_guid':self.guid,'invoice_number':'9'*20,'face_amount':'12.00','claim_amount':'10.00','amount_origin':'document_content',
                 'source_refs':[{'path':str(self.original),'sha256':n.sha(self.original),'page':1}]}]
        path=self.root/'claims.json';atomic_json(path,claims);return path

    def save_read(self):
        path=self.prepare_rows();self.job('save','save',source_job_id='rows',source_artifact_id='output',claims_path=str(path))
        pending=read_json(self.pending);self.job('persisted','inspect',job_id='save',artifact_id='output');return pending,read_json(self.jobs['persisted']/'output.json')

    def test_completed_initialization_can_close_without_claiming_editor_ready(self):
        self.job('inventory','inspect');self.job('open','open',source_job_id='inventory',source_artifact_id='output')
        pending=read_json(self.pending);self.job('financial_values','inspect');observed=read_json(self.jobs['financial_values']/'output.json')
        proof=n.verify_ack(pending,observed,'staged')
        self.assertFalse(proof['editor_rows_verified']);self.assertFalse(proof['financial_amount_verified'])
        with self.assertRaises(ValueError):n.verify_ack(pending,observed,'saved')
        changed=copy.deepcopy(observed);changed['result']['bill']['t.total_amt']='11.00'
        with self.assertRaises(ValueError):n.verify_ack(pending,changed,'staged')
    def test_financial_save_still_requires_editor_rows(self):
        pending,observed=self.save_read()
        self.job('save_without_rows','inspect')
        with self.assertRaises(ValueError):n.verify_ack(pending,read_json(self.jobs['save_without_rows']/'output.json'),'saved')

    def test_original_init_then_one_save_and_independent_persistent_rows_ack(self):
        pending,observed=self.save_read();proof=n.verify_ack(pending,observed,'saved')
        self.assertTrue(proof['invoice_row_persistence_verified']);self.assertTrue(proof['final_target_reached']);self.assertEqual(proof['claim_total'],'10.00')
        acknowledge(self.paths,self.jobs['persisted']/'output.json','逐票持久金额与原分组、总额完整匹配','saved')
        self.assertFalse(self.pending.exists());self.assertEqual(self.steps.count('reimbursementAmountSave'),1)

    def test_cross_category_full_bill_editor_checks_all_categories_and_invoice_rows(self):
        self.whole_bill=True;pending,observed=self.save_read()
        self.assertTrue(n.verify_ack(pending,observed,'saved')['invoice_row_persistence_verified'])
        bad=copy.deepcopy(observed);bad['result']['entries'][0]['amount']='5.00';bad['result']['entries'][1]['amount']='5.00'
        with self.assertRaises(ValueError):n.verify_ack(pending,bad,'saved')
        bad=copy.deepcopy(observed);bad['result']['entries'][1]['amount']='3.00'
        with self.assertRaises(ValueError):n.verify_ack(pending,bad,'saved')

    def test_foreign_invoice_changed_protected_field_old_read_and_wrong_total_refused(self):
        pending,observed=self.save_read()
        for mode in ['guid','face','self_pay','total','time']:
            bad=copy.deepcopy(observed)
            if mode=='guid':bad['result']['rows'][0]['t.tk_guid']='B'*32
            if mode=='face':bad['result']['rows'][0]['t.invoice_amt']='13.00'
            if mode=='self_pay':bad['result']['rows'][0]['t.zf_zmt']='2.00'
            if mode=='total':bad['result']['bill']['t.total_amt']='9.00'
            if mode=='time':bad['dispatch_intent_at_utc']='2020-01-01T00:00:00+00:00'
            with self.subTest(mode=mode),self.assertRaises(ValueError):n.verify_ack(pending,bad,'saved')
        self.assertTrue(self.pending.exists())

    def test_changed_source_stops_before_money_dispatch(self):
        path=self.prepare_rows();folder=self.root/'save';folder.mkdir();request={'case_id':'amount_fixture','parameters':{**self.params,'action':'save','source_job_id':'rows','source_artifact_id':'output','claims_path':str(path)},'execute':True,'request_id':'save'}
        with patch.object(self.service,'_typed_case_artifact',side_effect=self.typed),patch.object(self.service,'job_folder',side_effect=lambda case,job:self.jobs[job]):n.command(self.service,request,folder)
        self.original.write_bytes(b'changed original')
        with patch.object(n,'browser')as browser:
            with self.assertRaises(ValueError):n.run(folder/'native-amount-input.json',folder/'output.json')
            browser.assert_not_called()
        self.assertFalse(self.pending.exists())

    def test_unknown_save_recovers_original_only_and_preserves_receipt(self):
        pending,_=self.save_read();path=Path(pending['verification']['save_receipt']);old=read_json(path);body=old['result']
        old.update(phase='await_original_marker',result_marker='reimbursement-result-'+'a'*32);atomic_json(path,old);original=path.read_bytes();folder=self.root/'recovery';folder.mkdir()
        fixture=self
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):return fixture.now()+timedelta(seconds=2)
        with patch.object(n,'browser',side_effect=self.runtime),patch.object(n,'datetime',Clock),patch('finance_backend.assert_original_transport'),patch('edge_runtime.browser_lock',nullcontext),patch('edge_runtime.read_marker',return_value=body)as marker:
            n.run(Path(pending['verification']['input']['path']),folder/'output.json',recover=True)
        self.assertEqual(path.read_bytes(),original);self.assertEqual(self.steps.count('reimbursementAmountSave'),1);marker.assert_called_once()
        self.assertTrue(n.verify_ack(pending,read_json(folder/'output.json'),'saved')['financial_amount_verified'])


    def test_known_legacy_cross_category_error_recovers_on_new_tab_without_save_replay(self):
        import finance_amount_recovery as recovery
        self.whole_bill=True;pending,_=self.save_read()
        path=Path(pending['verification']['save_receipt']);old=read_json(path);c=read_json(pending['verification']['input']['path'])
        prepared=read_json(pending['verification']['prepare']['path'])['result']
        old['result'].update(ok=False,error='AMOUNT_API_PERSISTED_GROUP_TOTAL',prepared=prepared)
        atomic_json(path,old);original_bytes=path.read_bytes();folder=self.root/'repair';folder.mkdir()
        request={'case_id':'amount_fixture','parameters':{**self.params,'tab_id':'8','action':'recover','job_id':'save'},'execute':False}
        proof={'input':n.ref(Path(pending['verification']['input']['path'])),'prepare':pending['verification']['prepare'],'receipt':n.ref(path)}
        with self.assertRaises(ValueError):recovery.original(proof)
        with patch.object(recovery,'LEGACY_ADAPTER_SHA256',c['adapter']['sha256']):
            recovery.command(self.service,request,folder,Path(proof['input']['path']),path)
            v=read_json(folder/'native-amount-recovery-input.json');reader=v['reader'];self.counter+=1
            body=self.body(reader,True);body['persistence_recovery']={'original':proof,'monetary_save_replayed':False,'temporary_editor_refreshed':True,
                'refresh':{'ok':True,'initialized':True,'financial_amount_save_requested':False,'action':'amount-initialize',**{k:c[k]for k in ['case_id','serial','expected_userid']}}}
            script=folder/'output.js';script.write_text(n.script(reader,'reimbursementAmountRecoverPersisted'))
            envelope={'phase':'script_completed','tab_id':'8','script':str(script),'source_sha256':n.sha(script),'result':body,
                'dispatch_intent_at_utc':self.now().isoformat(),'amount_persistence_recovery':n.ref(folder/'native-amount-recovery-input.json')}
            self.assertTrue(n.verify_ack(pending,envelope,'saved')['invoice_row_persistence_verified'])
            self.assertFalse(n.verify_ack(pending,envelope,'saved')['original_write_replayed'])
            for change in ['row','refresh','time','tab','allocation']:
                bad=copy.deepcopy(envelope)
                if change=='row':bad['result']['rows'][0]['t.t_amt']='9.00'
                if change=='refresh':bad['result']['persistence_recovery']['temporary_editor_refreshed']=False
                if change=='time':bad['dispatch_intent_at_utc']='2000-01-01T00:00:00+00:00'
                if change=='tab':bad['tab_id']='7'
                if change=='allocation':bad['result']['allocation_tree']=[{'other':'new'}]
                with self.subTest(change=change),self.assertRaises(ValueError):n.verify_ack(pending,bad,'saved')
        self.assertEqual(path.read_bytes(),original_bytes);self.assertEqual(self.steps.count('reimbursementAmountSave'),1)
