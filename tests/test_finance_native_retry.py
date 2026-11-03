"""HTTP清理沿原preview/confirm/readback/pending闭环；全部使用合成资料。"""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from assistant_api import AssistantService,read_json,atomic_json
import finance_invoice_retry as retry
from finance_native_retry import confirm_evidence,verify_ack,SCHEMA,native_plan_request
from reimburse import acknowledge,begin_write

class NativeRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.service=AssistantService(self.root,[self.root]);self.service.new_case('retry_fixture')
        self.bundle,self.paths=self.service.resolve('retry_fixture')
        source=self.root/'original.pdf';source.write_bytes(b'%PDF-synthetic-single-invoice');sha=retry.sha(source)
        self.row={'t.filesequcode':'a'*32,'t.show_filename':sha+'.pdf','t.show_err_msg':'无法识别图像'}
        self.protected={'t.filesequcode':'b'*32,'t.show_filename':'2'*64+'.pdf','t.show_err_msg':'其他票错误'}
        self.manifest={'case_id':'retry_fixture','expected_userid':'TEST001','original_files':[{'path':str(source),'sha256':sha,'invoice_number_candidates':['99000000000000001234']}]}
        self.request={'case_id':'retry_fixture','tab_id':'99','expected_userid':'TEST001','grid':'113','action':'cleanup',
                      'targets':[{'invoice_number':'99000000000000001234','source_sha256':sha,'filecode':'a'*32}]}
        self.button=read_json(Path(retry.ROOT)/'data/finance_retry_contract.json')['grids']['113']['cleanup_button']
        self.pool=self.capture('53',[],'12:00:00')
        self.recognition=self.capture('113',[self.row,self.protected],'12:00:00')
        self.inspection=self.inspect('12:00:00')
        plan=retry.preview(self.request,self.manifest,self.pool,self.recognition,self.inspection)
        inputs={}
        for name,value in [('request',self.request),('manifest',self.manifest),('pool',self.pool),('recognition',self.recognition),('inspection',self.inspection)]:
            path=self.root/(name+'.json');atomic_json(path,value);inputs[name]={'path':str(path),'sha256':retry.sha(path)}
        self.plan={**plan,'inputs_evidence':inputs};self.plan_path=self.root/'plan.json';atomic_json(self.plan_path,self.plan)
    def capture(self,grid,rows,time):
        return {'schema':'ustc-edge-operation-v2','phase':'script_completed','tab_id':'99','result':{
            'ok':True,'projid':'WF_YBX','identity':{'userid':'TEST001'},'business_transport':'native_http','mode':'native_http_full_grid',
            'complete':True,'scopeComplete':True,'page_context_modified':False,'observed_at':'2026-10-29T'+time+'Z',
            'grids':[{'funcno':grid,'rows':copy.deepcopy(rows),'records':len(rows),'rowCount':len(rows),'loading':False,'complete':True,'allRecordsVisible':False}]}}
    def inspect(self,time):
        return {'phase':'script_completed','tab_id':'99','result':{'ok':True,'schema':'ustc-finance-invoice-retry-inspect-v1',
            'business_transport':'native_http','identity':{'userid':'TEST001'},'projid':'WF_YBX','observed_at':'2026-10-29T'+time+'Z',
            'definitionCaptureComplete':True,'grid':copy.deepcopy(self.recognition['result']['grids'][0]),'buttonDefinitions':[self.button]}}
    def action(self,action='confirm',time='12:01:00'):
        return {'schema':'ustc-edge-operation-v2','phase':'script_completed','tab_id':'99','executed_at_utc':'2026-10-29T'+time+'Z',
            'result':{'ok':True,'case_id':'retry_fixture','business_transport':'native_http','request_id':'fixed_request','action':action,
                'server_accepted':True,'server_status':'pass',
                'marker':{'plan_digest':retry.digest(self.plan),'phase':'confirm_http_received' if action=='confirm'else'awaiting_http_confirmation'}}}
    def test_native_preview_has_no_fabricated_selection_and_rebuilds_all_evidence(self):
        self.assertTrue(self.plan['ready']);self.assertEqual(self.plan['business_transport'],'native_http')
        self.assertIsNone(self.plan['before_selected_row_ids']);retry.verify_plan_evidence(self.plan)
        script=retry.action_script(self.plan,'fixed_request')
        self.assertIn('reimbursementRetryApi',script);self.assertNotIn('.click()',script)
    def test_confirmation_needs_new_http_inspection_and_unchanged_target(self):
        arm=self.action('cleanup');fresh=self.inspect('12:01:01');self.assertTrue(confirm_evidence(self.plan,arm,fresh))
        with self.assertRaises(ValueError):confirm_evidence(self.plan,arm,self.inspection)
        fresh['result']['grid']['rows'][0]['t.show_err_msg']='different'
        with self.assertRaises(ValueError):confirm_evidence(self.plan,arm,fresh)
    def saved_readback(self,protected=True):
        folder=self.paths['state'].parent/'receipts'/'confirm';folder.mkdir(parents=True)
        native=self.action();script=folder/'native.js';script.write_text(retry.action_script(self.plan,'fixed_request',True),encoding='utf-8',newline='\n')
        native.update(script=str(script),source_sha256=retry.sha(script));nativepath=folder/'native.json';atomic_json(nativepath,native)
        pool=self.capture('53',[],'12:02:00');rec=self.capture('113',[self.protected]if protected else[],'12:02:00')
        result=retry.readback(self.plan,pool,rec,native)
        refs={}
        for name,value in [('pool',pool),('recognition',rec)]:
            p=self.root/('after-'+name+'.json');atomic_json(p,value);refs[name]={'path':str(p),'sha256':retry.sha(p)}
        result['inputs_evidence']={'plan':{'path':str(self.plan_path),'sha256':retry.sha(self.plan_path)},'receipt':{'path':str(nativepath),'sha256':retry.sha(nativepath)},**refs}
        output=self.root/'verified.json';atomic_json(output,result)
        pending={'case_id':'retry_fixture','serial':None,'operation':'invoice:http-cleanup','output_directory':str(folder),
                 'started_at_utc':'2026-10-29T12:00:30Z','verification':{'schema':SCHEMA,'plan':str(self.plan_path),'plan_sha256':retry.sha(self.plan_path)}}
        atomic_json(self.paths['state'].parent/'pending_write.json',pending)
        return pending,output
    def test_independent_inventory_and_original_confirm_unlock_original_pending(self):
        pending,out=self.saved_readback();proof=verify_ack(pending,out);self.assertTrue(proof['native_cleanup_verified'])
        result=acknowledge(self.paths,out,'目标记录消失，其他记录保留','saved')
        self.assertTrue(result['pending_cleared']);self.assertFalse((self.paths['state'].parent/'pending_write.json').exists())
    def test_missing_protected_record_cannot_unlock_or_begin_another_write(self):
        pending,out=self.saved_readback(False)
        with self.assertRaises(ValueError):verify_ack(pending,out)
        with self.assertRaises(ValueError):acknowledge(self.paths,out,'不能解锁','saved')
        with self.assertRaises(ValueError):begin_write(self.paths,read_json(self.paths['form']),'step:basic',self.root)
        self.assertTrue((self.paths['state'].parent/'pending_write.json').exists())
    def test_unknown_or_wrong_original_action_cannot_be_readback_success(self):
        for phase in ['unknown','not_dispatched']:
            receipt=self.action();receipt['phase']=phase
            with self.assertRaises(ValueError):retry.readback(self.plan,self.capture('53',[],'12:02:00'),self.capture('113',[self.protected],'12:02:00'),receipt)
    def test_legacy_plan_cannot_pass_native_dispatch_gate(self):
        req={'case_id':'retry_fixture','parameters':{'action':'confirm'},'workspace':str(self.root),'material_roots':[str(self.root)]}
        with patch.object(AssistantService,'_typed_case_artifact',return_value=(None,self.plan,None)):
            self.assertTrue(native_plan_request(req))
        with patch.object(AssistantService,'_typed_case_artifact',return_value=(None,{'ready':True},None)):
            self.assertFalse(native_plan_request(req))

    def test_confirm_persists_original_pending_before_http_and_cannot_repeat(self):
        arm=self.root/'arm.json';fresh=self.root/'new-inspection.json'
        atomic_json(arm,self.action('cleanup'));atomic_json(fresh,self.inspect('12:01:01'))
        calls=[]
        def runtime(source,request,output,kind):
            pending=read_json(self.paths['state'].parent/'pending_write.json')
            self.assertEqual(pending['operation'],'invoice:http-cleanup');self.assertEqual(pending['verification']['schema'],SCHEMA)
            self.assertEqual(kind,'write');calls.append(source)
            return self.action()
        def argv(output,request_id):
            return ['retry','confirm','--plan',str(self.plan_path),'--plan-sha256',retry.sha(self.plan_path),
                '--receipt',str(arm),'--receipt-sha256',retry.sha(arm),'--inspection',str(fresh),
                '--request-id',request_id,'--bundle',str(self.bundle),'--execute','--output',str(output)]
        first=self.root/'confirm'/'output.json';first.parent.mkdir()
        with patch.object(retry,'ROOT',self.root),patch.object(retry,'run_browser',side_effect=runtime):
            with patch.object(sys,'argv',argv(first,'first_request')):self.assertEqual(retry.main(),0)
            with patch.object(sys,'argv',argv(self.root/'second.json','different_request')):
                with self.assertRaises(ValueError):retry.main()
        self.assertEqual(len(calls),1)

    def test_unknown_recovery_reads_only_original_marker_and_preserves_old_receipt(self):
        from finance_native_retry import recover_original
        from registered_page_owner import RegisteredPageOwner
        folder=self.root/'original';folder.mkdir();script=folder/'native.js'
        script.write_text(retry.action_script(self.plan,'fixed_request',True),encoding='utf-8',newline='\n');receipt=folder/'native.json'
        old={'schema':'ustc-edge-operation-v2','phase':'unknown','tab_id':'99','script':str(script),
             'source_sha256':retry.sha(script),'result_marker':'reimbursement-result-'+'d'*32}
        atomic_json(receipt,old);original_bytes=receipt.read_bytes()
        with patch.object(RegisteredPageOwner,'before_child_dispatch'),patch('finance_backend.assert_original_transport'),patch('edge_runtime.read_marker',return_value=self.action()['result'])as marker:
            recovered=recover_original(self.plan,'fixed_request','confirm',receipt,retry.sha(receipt))
        marker.assert_called_once_with(old['result_marker'],'99',timeout=45)
        self.assertFalse(recovered['original_marker_recovery']['script_reexecuted'])
        self.assertEqual(receipt.read_bytes(),original_bytes)
        changed={**old,'phase':'not_dispatched'};atomic_json(receipt,changed)
        with patch('edge_runtime.read_marker')as marker:
            with self.assertRaises(ValueError):recover_original(self.plan,'fixed_request','confirm',receipt,retry.sha(receipt))
        marker.assert_not_called()

    def test_explicit_server_rejection_requires_unchanged_targets_before_not_applied(self):
        pending,out=self.saved_readback()
        data=read_json(out);refs=data['inputs_evidence'];native=read_json(refs['receipt']['path'])
        native['result'].update(server_accepted=False,server_status='error')
        native['result']['marker']['phase']='confirm_http_rejected'
        atomic_json(refs['receipt']['path'],native)
        rec=self.capture('113',[self.row,self.protected],'12:02:00');atomic_json(refs['recognition']['path'],rec)
        result=retry.readback(self.plan,read_json(refs['pool']['path']),rec,native)
        for ref in refs.values():ref['sha256']=retry.sha(ref['path'])
        atomic_json(out,{**result,'inputs_evidence':refs})
        self.assertTrue(verify_ack(pending,out,'not_applied')['native_cleanup_verified'])
        with self.assertRaises(ValueError):verify_ack(pending,out,'saved')
        self.assertTrue(acknowledge(self.paths,out,'明确拒绝，目标及非目标记录仍在','not_applied')['pending_cleared'])

    def as_reidentify(self):
        self.request['action']='reidentify'
        self.button=read_json(Path(retry.ROOT)/'data/finance_retry_contract.json')['grids']['113']['reidentify_button']
        self.inspection['result']['buttonDefinitions']=[self.button]
        self.inspection['result']['native_ocr_contract_verified']=True
        plan=retry.preview(self.request,self.manifest,self.pool,self.recognition,self.inspection)
        self.assertTrue(plan['ready'])
        refs=copy.deepcopy(self.plan['inputs_evidence'])
        for name,value in [('request',self.request),('inspection',self.inspection)]:
            atomic_json(refs[name]['path'],value);refs[name]['sha256']=retry.sha(refs[name]['path'])
        self.plan={**plan,'inputs_evidence':refs};atomic_json(self.plan_path,self.plan)

    def reidentify_readback(self,pool,recognition):
        pending,out=self.saved_readback()
        pending['operation']='invoice:http-reidentify'
        from finance_native_retry import REIDENTIFY_SCHEMA
        pending['verification']['schema']=REIDENTIFY_SCHEMA
        atomic_json(self.paths['state'].parent/'pending_write.json',pending)
        refs=read_json(out)['inputs_evidence'];native=read_json(refs['receipt']['path'])
        native['result'].update(server_status='success',ocr_status='pass' if pool else 'fail')
        atomic_json(refs['receipt']['path'],native)
        for name,value in [('pool',self.capture('53',pool,'12:02:00')),('recognition',self.capture('113',recognition,'12:02:00'))]:
            atomic_json(refs[name]['path'],value)
        result=retry.readback(self.plan,read_json(refs['pool']['path']),read_json(refs['recognition']['path']),native)
        for ref in refs.values():ref['sha256']=retry.sha(ref['path'])
        atomic_json(out,{**result,'inputs_evidence':refs});return pending,out

    def test_reidentify_needs_unique_normal_authenticated_pool_after_original_ocr(self):
        self.as_reidentify()
        row={'t.tk_guid':'c'*32,'t.tk_code_abst':'普/全电票/99000000000000001234',
             't.tk_mod_flag':'NORMAL','t.auth_rst_desc':'正常(已认证)'}
        pending,out=self.reidentify_readback([row],[self.protected])
        result=verify_ack(pending,out,'saved');self.assertTrue(result['native_reidentify_verified'])
        self.assertFalse(result['claim_created']);self.assertFalse(result['buyer_fields_verified'])
        self.assertTrue(acknowledge(self.paths,out,'原OCR后完整回读正常认证，未建单','saved')['pending_cleared'])

    def test_reidentify_error_is_needs_correction_and_processing_cannot_unlock(self):
        self.as_reidentify();pending,out=self.reidentify_readback([],[self.row,self.protected])
        self.assertTrue(verify_ack(pending,out,'needs_correction')['native_reidentify_verified'])
        with self.assertRaises(ValueError):verify_ack(pending,out,'saved')
        refs=read_json(out)['inputs_evidence'];rec=read_json(refs['recognition']['path'])
        rec['result']['grids'][0]['rows'][0]['t.show_err_msg']=''
        atomic_json(refs['recognition']['path'],rec)
        native=read_json(refs['receipt']['path']);result=retry.readback(self.plan,read_json(refs['pool']['path']),rec,native)
        refs['recognition']['sha256']=retry.sha(refs['recognition']['path']);atomic_json(out,{**result,'inputs_evidence':refs})
        with self.assertRaises(ValueError):verify_ack(pending,out,'needs_correction')

    def upload_binding(self):
        from finance_native_upload import SCHEMA as upload_schema,acknowledged_error_bindings
        source=self.manifest['original_files'][0]
        folder=self.paths['state'].parent/'receipts'/'upload';folder.mkdir(parents=True)
        batch={'caseId':'retry_fixture','expectedUserid':'TEST001'}
        c={'schema':upload_schema,'case_id':'retry_fixture','expected_userid':'TEST001','batch':batch,
           'sources':[{'name':'original.pdf','path':source['path'],'sha256':source['sha256'],
                       'invoice_numbers':['99000000000000001234']}]}
        input_path=folder/'native-upload-input.json';atomic_json(input_path,c)
        ack={'case_id':'retry_fixture','operation':'invoice:http-upload','outcome':'needs_correction','status':'operator_readback_recorded',
             'verification':{'schema':upload_schema,'input':str(input_path),'input_sha256':retry.sha(input_path)},
             'upload_reconciliation':{'schema':upload_schema,'batch':batch,'independent_http_readback':True,'accounted_files':[
                 {'state':'recognition_error','name':'original.pdf','sha256':source['sha256'],'filecode':'a'*32}]}}
        ack_path=folder/'operator_readback_ack.json';atomic_json(ack_path,ack)
        state=read_json(self.paths['state']);state['native_upload_acknowledgements']=[{'path':str(ack_path),'sha256':retry.sha(ack_path)}]
        atomic_json(self.paths['state'],state)
        return acknowledged_error_bindings(self.service,'retry_fixture','TEST001')[0]

    def test_original_filename_is_bound_by_original_upload_ack_not_filename_guess(self):
        binding=self.upload_binding();self.manifest['native_upload_bindings']=[binding]
        self.row['t.show_filename']='original.pdf'
        self.recognition['result']['grids'][0]['rows'][0]=copy.deepcopy(self.row)
        self.inspection['result']['grid']['rows'][0]=copy.deepcopy(self.row)
        plan=retry.preview(self.request,self.manifest,self.pool,self.recognition,self.inspection)
        self.assertTrue(plan['ready']);self.assertEqual(plan['targets'][0]['native_filename'],'original.pdf')
        self.assertEqual(plan['targets'][0]['native_upload_binding']['source_sha256'],self.manifest['original_files'][0]['sha256'])
        self.manifest.pop('native_upload_bindings')
        with self.assertRaises(ValueError):retry.preview(self.request,self.manifest,self.pool,self.recognition,self.inspection)

    def test_upload_error_binding_rejects_actor_and_changed_certificate(self):
        from finance_native_upload import validate_error_binding
        binding=self.upload_binding()
        with self.assertRaises(ValueError):validate_error_binding(binding,'retry_fixture','OTHER')
        path=Path(binding['acknowledgement']['path']);path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaises(ValueError):validate_error_binding(binding,'retry_fixture','TEST001')
