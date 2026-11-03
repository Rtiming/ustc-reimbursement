"""建单写入均使用合成票据/传输；验证真实接口的生命周期，不创建测试财务单。"""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import test_finance_native_selection as selected_fixture
import finance_native_create as native
import finance_native_selection as selection
import invoice_selection as old
from assistant_api import read_json,atomic_json


class NativeCreateTests(unittest.TestCase):
    browser=selected_fixture.NativeSelectionTests.browser
    plan=selected_fixture.NativeSelectionTests.plan

    def setUp(self):
        selected_fixture.NativeSelectionTests.setUp(self)
        lock=patch('edge_runtime.LOCK_PATH',self.root/'fixture-browser.lock');lock.start();self.addCleanup(lock.stop)
        f=read_json(self.paths['form']);f['expected_userid']='TEST001';atomic_json(self.paths['form'],f)
        p,path=self.plan()
        with patch.object(old,'run_browser',side_effect=self.browser):selection.apply(p,path,self.bundle,self.root/'select'/'output.json','select-original')
        self.folder=self.root/'create';self.folder.mkdir();self.write_calls=0;self.read_calls=0

    def freeze(self,execute=True):
        request={'case_id':'selection_fixture','parameters':{'action':'create-draft','tab_id':'99'},'request_id':'create-original','execute':execute}
        native.command(self.service,request,self.folder)
        return self.folder/'native-create-input.json'

    def runtime(self,c,method,output,extra=None,*,write=False):
        config={**c,**(extra or{})};source='(async()=>{'+native.checked_text(c['adapter'])+';return await '+method+'('+json.dumps(config,ensure_ascii=False)+');})()'
        script=Path(output).with_suffix('.js');script.write_text(source,encoding='utf-8',newline='\n')
        at='2026-10-29T13:00:00+00:00'
        if method=='reimbursementCreatePrepare':
            body={'ok':True,'case_id':c['case_id'],'expected_userid':c['expected_userid'],'creation_eligible':True,'complete':True,'financial_write':False,
                'targets':[t['native_row']for t in c['selected']],'original':{'ym.req_sno':'TEST001'},'policy':{},
                'definition_sha256':c['create_contract']['definition_sha256'],'template_sha256':c['create_contract']['template_sha256']}
        elif method=='reimbursementCreateExecute':
            self.write_calls+=1;pending=read_json(self.paths['state'].parent/'pending_write.json')
            self.assertEqual(pending['operation'],'invoice:create-draft');self.assertTrue(read_json(self.paths['invoices'])['draft_creation_requested']);self.assertTrue(write)
            body={'ok':True,'case_id':c['case_id'],'expected_userid':c['expected_userid'],'state':'created_pending_readback','serial':'123456789',
                  'token':c['token'],'marker':{'serial':'123456789'},'create_dispatched':True,'server_accepted':True}
        else:
            self.read_calls+=1;self.assertEqual(method,'reimbursementBusinessApi');self.assertFalse(write);at='2026-10-29T13:01:00+00:00'
            rows=[{'t.tk_guid':t['guid'],'t.serial_no':'123456789','t.tk_total_amt':t['amount'],'t.tk_code_abst':t['native_row']['t.tk_code_abst']}for t in c['selected']]
            body={'ok':True,'case_id':c['case_id'],'expected_userid':c['expected_userid'],'system_write':False,'business_transport':'native_http','serial':'123456789',
                  'domain':'used_invoices','complete':True,'page_context_modified':False,'bill':{'t.serial_no':'123456789','t.status':'-99','t.lc_finish':'F','t.total_amt':'1.00','t.REQ_INFO':'TEST001-Synthetic'},
                  'sections':[{'funcno':'533','complete':True,'rows':rows}]}
        envelope={'schema':'ustc-edge-operation-v2','phase':'script_completed','tab_id':'99','result':body,'executed_at_utc':at,'script':str(script),'source_sha256':native.sha(script)}
        atomic_json(output,envelope);return envelope

    def run_create(self):
        inp=self.freeze()
        with patch.object(native,'browser',side_effect=self.runtime):self.assertEqual(native.run(inp,self.folder/'output.json'),0)
        return read_json(self.folder/'output.json')

    def test_preview_does_not_run_browser_or_set_creation_flag(self):
        inp=self.freeze(False);before=self.paths['invoices'].read_bytes()
        with patch.object(native,'browser')as browser:self.assertEqual(native.run(inp,self.folder/'output.json'),0)
        browser.assert_not_called();self.assertEqual(self.paths['invoices'].read_bytes(),before)
        self.assertFalse((self.paths['state'].parent/'pending_write.json').exists())

    def test_successful_domestic_response_still_reads_automatic_allowance_total(self):
        f=read_json(self.paths['form']);f['native_bill_type']='2';atomic_json(self.paths['form'],f)
        inp=self.freeze();proof={'kind':'native_domestic_initial_with_automatic_allowance'}
        verified={'serial':'123456789','invoice_amount':'1.00','amount':'181.00','initial_native_allowance':'180.00'}
        with patch.object(native,'browser',side_effect=self.runtime),patch('finance_domestic_initial.reconcile',return_value=(proof,verified,{'path':'synthetic-readback','sha256':'a'*64}))as reconcile:
            self.assertEqual(native.run(inp,self.folder/'output.json'),0)
        self.assertEqual(self.write_calls,1);self.assertEqual(self.read_calls,0);self.assertEqual(reconcile.call_args.args[0]['bill_type'],'2');self.assertTrue(reconcile.call_args.args[1]['ok'])
        result=read_json(self.folder/'output.json');self.assertEqual(result['verification']['amount'],'181.00');self.assertEqual(result['reconciliation'],proof)

    def test_creation_pending_precedes_dispatch_and_binding_needs_full_guid_readback(self):
        value=self.run_create();self.assertEqual(self.write_calls,1);self.assertTrue(value['draft_created']);self.assertFalse(value['local_bound'])
        self.assertIsNone(read_json(self.paths['form'])['serial']);self.assertTrue((self.paths['state'].parent/'pending_write.json').exists())
        result=native.bind_created(self.paths,self.folder/'output.json','123456789');self.assertTrue(result['ok'])
        for key in ['case','form','invoices','state']:self.assertEqual(read_json(self.paths[key])['serial'],'123456789')
        self.assertFalse((self.paths['state'].parent/'pending_write.json').exists());self.assertEqual(self.write_calls,1)

    def test_changed_request_cannot_replay_original_creation(self):
        self.run_create();other=self.root/'second';other.mkdir()
        with self.assertRaises(ValueError):native.command(self.service,{'case_id':'selection_fixture','parameters':{'action':'create-draft','tab_id':'99'},'request_id':'changed','execute':True},other)
        self.assertEqual(self.write_calls,1)

    def test_readback_failure_resumes_only_reading_original_created_claim(self):
        inp=self.freeze()
        def fail_read(c,method,*args,**kwargs):
            if method=='reimbursementBusinessApi':raise TimeoutError('independent read failed')
            return self.runtime(c,method,*args,**kwargs)
        with patch.object(native,'browser',side_effect=fail_read):
            with self.assertRaises(TimeoutError):native.run(inp,self.folder/'output.json')
        original=(self.folder/'native-create.json').read_bytes();out=self.root/'resume';out.mkdir()
        with patch.object(native,'browser',side_effect=self.runtime):self.assertEqual(native.run(inp,out/'output.json',resume=True),0)
        self.assertEqual(self.write_calls,1);self.assertEqual(self.read_calls,1);self.assertEqual((self.folder/'native-create.json').read_bytes(),original)
        native.bind_created(self.paths,out/'output.json','123456789')

    def test_original_marker_recovery_consumes_structured_runtime_result(self):
        value=self.run_create();c=native.checked(value['input']);path=self.folder/'native-create.json'
        envelope=read_json(path);body=envelope['result'];envelope.update(phase='await_original_marker',result_marker='reimbursement-result-'+'a'*32);atomic_json(path,envelope)
        folder=self.root/'marker-result';folder.mkdir()
        with patch('finance_backend.assert_original_transport'),patch('edge_runtime.read_marker',return_value=body)as marker:
            result,receipt,_=native.original_result(c,path,folder/'output.json',resume=True)
        self.assertEqual(result,body);self.assertFalse(native.checked(receipt)['script_reexecuted']);marker.assert_called_once()

    def test_complete_guid_set_requires_no_extra_or_missing_or_duplicate_ticket(self):
        value=self.run_create();c=native.checked(value['input']);original=native.checked(value['original_creation']);observed=native.checked(value['readback'])
        for mutation in ['extra','missing','duplicate','amount','actor','earlier']:
            bad=copy.deepcopy(observed);rows=bad['result']['sections'][0]['rows']
            if mutation=='extra':rows.append({**rows[0],'t.tk_guid':'b'*32})
            elif mutation=='missing':rows.clear()
            elif mutation=='duplicate':rows.append(rows[0])
            elif mutation=='amount':rows[0]['t.tk_total_amt']='2.00'
            elif mutation=='actor':bad['result']['bill']['t.REQ_INFO']='OTHER-Synthetic'
            else:bad['executed_at_utc']='2026-10-29T12:59:00+00:00'
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):native.verify_used(c,original['result'],bad,original['executed_at_utc'])

    def test_metadata_or_source_drift_and_bound_case_stop_before_dispatch(self):
        inp=self.freeze();m=read_json(self.paths['invoices']);m['selected_invoices'][0]['amount']='2.00';atomic_json(self.paths['invoices'],m)
        with patch.object(native,'browser')as browser:
            with self.assertRaises(ValueError):native.run(inp,self.folder/'output.json')
        browser.assert_not_called();self.assertFalse((self.paths['state'].parent/'pending_write.json').exists())

    def test_partial_local_binding_can_finish_without_creating_again(self):
        self.run_create();f=read_json(self.paths['form']);f['serial']='123456789';atomic_json(self.paths['form'],f)
        result=native.bind_created(self.paths,self.folder/'output.json','123456789');self.assertTrue(result['ok']);self.assertEqual(self.write_calls,1)

    def test_wrong_serial_or_modified_receipt_keeps_original_pending(self):
        self.run_create()
        with self.assertRaises(ValueError):native.bind_created(self.paths,self.folder/'output.json','999999999')
        value=read_json(self.folder/'output.json');p=Path(value['readback']['path']);p.write_text(p.read_text()+' ')
        with self.assertRaises(ValueError):native.bind_created(self.paths,self.folder/'output.json','123456789')
        self.assertTrue((self.paths['state'].parent/'pending_write.json').exists())

    def test_unknown_create_is_bound_only_by_complete_unique_original_guid_ownership(self):
        inp=self.freeze()
        def actual(c,method,output,extra=None,**kwargs):
            envelope=self.runtime(c,method,output,extra,**kwargs)
            if method=='reimbursementCreateExecute':
                body=envelope['result'];body.update(ok=False,state='create_result_unknown',marker={'case_id':c['case_id'],'userid':c['expected_userid'],'token':c['token'],'create_dispatched':True});body.pop('serial');body.pop('server_accepted')
            elif extra and extra.get('action')=='bills-list':
                bill=envelope['result']['bill'];envelope['result']={'ok':True,'case_id':c['case_id'],'expected_userid':c['expected_userid'],'action':'bills-list','complete':True,'account_scope':'current_applicant','business_transport':'native_http','system_write':False,'total_records':1,'rows':[bill]}
            atomic_json(output,envelope);return envelope
        with patch.object(native,'browser',side_effect=actual):self.assertEqual(native.run(inp,self.folder/'output.json'),0)
        value=read_json(self.folder/'output.json');self.assertEqual(value['reconciliation']['original_creation_outcome'],'unknown_preserved');self.assertFalse(value['reconciliation']['creation_replayed']);self.assertEqual(self.write_calls,1)
        self.assertFalse(native.checked(value['original_creation'])['result']['ok'])
        result=native.bind_created(self.paths,self.folder/'output.json','123456789');self.assertTrue(result['ok']);self.assertEqual(self.write_calls,1)


if __name__=='__main__':unittest.main()
