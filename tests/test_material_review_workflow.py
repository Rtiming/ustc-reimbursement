import json
import subprocess
import types
import unittest
from unittest.mock import patch

import test_material_review as fixture
from material_review_model import ReviewError, atomic_json, sha
from material_review_store import append
import material_review_workflow as workflow


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.f = fixture.ReviewTest();self.f.setUp()
        self.root,self.ws = self.f.root,self.f.ws
        self.f.data['items']['1'].update(bill_number='1001',claim_total='10.00',queue='先补现有草稿')
        self.f.publish()
        program = self.root/'formal';(program/'tools').mkdir(parents=True)
        (program/'assistant.py').write_text('')
        (program/'tools'/'reimbursement.sh').write_text('# fixture only')
        self.source = self.root/'native.json'
        self.cases = self.root/'cases.json'
        self.native = {'operation_kind':'read','phase':'script_completed','result':{
            'ok':True,'complete':True,'action':'bills-list','system_write':False,'business_transport':'native_http',
            'expected_userid':'TEST001','case_id':'batch_fixture','observed_at':'2026-10-30T08:00:00Z',
            'total_records':2,'rows':[
                {'t.serial_no':'1001','t.REQ_INFO':'TEST001-合成用户','t.total_amt':'10.00','t.status':'-99',
                 't.status_name':'草稿','t.node_id':'','t.lc_finish':'F'},
                {'t.serial_no':'9999','t.REQ_INFO':'TEST001-合成用户','t.abst':'UNRELATED_CASE'}]}}
        atomic_json(self.cases,{'ok':True,'cases':[{'case_id':'test_case','serial':'1001'}]})
        self.config = {'tool_root':str(program),'batch_case_id':'batch_fixture','expected_userid':'TEST001',
                       'source_job_id':'test-job','source':{},'cases_source':{'path':str(self.cases),'sha256':sha(self.cases)}}
        self.cp = self.root/'config.json'
        self.local_root = patch.object(workflow,'ROOT',self.root);self.local_root.start()
        self.publish()

    def tearDown(self):
        self.local_root.stop();self.f.tearDown()

    def publish(self):
        atomic_json(self.source,self.native)
        self.config['source']={'path':str(self.source),'sha256':sha(self.source)}
        atomic_json(self.cp,self.config)

    def test_import_keeps_material_issues_separate_from_existing_draft(self):
        workflow.import_status(self.ws,self.cp)
        result=workflow.summary(self.ws)
        self.assertEqual(result['counts'],{'resume':1})
        self.assertEqual(result['items']['1']['case_id'],'test_case')
        self.assertNotIn('UNRELATED_CASE',json.dumps(result))
        self.assertTrue(result['items']['1']['can_prepare'])
        self.assertFalse(result['items']['1']['submission_ready'])
        self.assertEqual(result['items']['1']['open_issues'],['Q1'])

    def test_explicit_draft_scope_preserves_open_issues_and_never_allows_submission(self):
        self.f.data['items']['1'].update(bill_number='',draft_authority={'scope':'draft_only','path':'fixture.txt','sha256':'a'*64})
        self.f.publish();workflow.import_status(self.ws,self.cp)
        row=workflow.summary(self.ws)['items']['1']
        self.assertEqual(row['route'],'draft_candidate');self.assertTrue(row['can_prepare'])
        self.assertFalse(row['submission_ready']);self.assertEqual(row['open_issues'],['Q1'])
        self.f.data['items']['1']['queue']='按用户安排暂缓';self.f.publish();workflow.import_status(self.ws,self.cp)
        self.assertEqual(workflow.summary(self.ws)['items']['1']['route'],'hold')

    def test_partial_or_drift_does_not_replace_last_complete(self):
        workflow.import_status(self.ws,self.cp)
        old=(self.ws/'workflow'/'current.json').read_bytes()
        self.native['result']['complete']=False;self.publish()
        with self.assertRaises(ReviewError):workflow.import_status(self.ws,self.cp)
        self.assertEqual(old,(self.ws/'workflow'/'current.json').read_bytes())
        self.source.write_text('{}')
        with self.assertRaises(ReviewError):workflow.import_status(self.ws,self.cp)

    def test_delivery_never_routes_to_new_draft(self):
        self.native['result']['rows'][0].update({'t.status':'10','t.lc_finish':'T','t.status_name':'已预约待投递'})
        self.publish();workflow.import_status(self.ws,self.cp)
        row=workflow.summary(self.ws)['items']['1']
        self.assertEqual(row['route'],'delivery');self.assertFalse(row['can_prepare'])

    def test_verified_workflow_log_supersedes_stale_list_without_reapproval(self):
        row=self.native['result']['rows'][0];row.update({'t.status':'-2','t.node_id':'508','t.status_name':'申请人[Next]','t.uni_key':'700','t.wf_name':'YBX_LC'})
        history=[{'t.node_id':'508','t.operator':'TEST001','t.op_comment':'同意','t.appinfo':'-'},
                 {'t.node_id':'449','t.operator':'','t.node_name':'预约流程-证明人/验收人','t.appinfo':'NEXT-Person'}]
        proof={'phase':'script_completed','operation_kind':'read','executed_at_utc':'2026-10-30T08:01:00+00:00','result':{
            'action':'applicant-http-inspect','ok':True,'complete':True,'completed':True,'system_write':False,'save_dispatched':False,
            'business_transport':'native_http','serial':'1001','case_id':'test_case','expected_userid':'TEST001',
            'original':{'bill':dict(row),'history':history,'workflow_node_id':'449'}}}
        path=self.root/'applicant.json';atomic_json(path,proof);self.config['applicant_receipts']=[{'path':str(path),'sha256':sha(path)}];self.publish()
        workflow.import_status(self.ws,self.cp);item=workflow.summary(self.ws)['items']['1']
        self.assertEqual(item['route'],'approval');self.assertEqual(item['node_id'],'449');self.assertEqual(item['main_list_node_id'],'508');self.assertFalse(item['can_prepare'])
        for field,value in [('expected_userid','OTHER'),('serial','9999'),('completed',False)]:
            original=proof['result'][field];proof['result'][field]=value;atomic_json(path,proof);self.config['applicant_receipts'][0]['sha256']=sha(path);self.publish()
            with self.assertRaises(ReviewError):workflow.import_status(self.ws,self.cp)
            proof['result'][field]=original
        proof['executed_at_utc']='2026-10-30T07:59:00+00:00';atomic_json(path,proof);self.config['applicant_receipts'][0]['sha256']=sha(path);self.publish()
        with self.assertRaises(ReviewError):workflow.import_status(self.ws,self.cp)

    def test_reviewed_issue_allows_preparation_but_not_submission(self):
        workflow.import_status(self.ws,self.cp)
        append(self.ws,self.f.req('review',checks=[True,True],evidence=[{'asset_id':self.f.aid,'page':1}]))
        result=workflow.summary(self.ws)
        self.assertEqual(result['items']['1']['route'],'resume');self.assertFalse(result['system_write'])

    def test_binding_identity_and_amount_are_checked(self):
        for field,value in [('t.total_amt','11.00'),('t.REQ_INFO','OTHER-user')]:
            original=self.native['result']['rows'][0][field]
            self.native['result']['rows'][0][field]=value;self.publish()
            with self.assertRaises(ReviewError):workflow.import_status(self.ws,self.cp)
            self.native['result']['rows'][0][field]=original

    def test_preflight_only_calls_fixed_official_overview(self):
        workflow.import_status(self.ws,self.cp)
        overview={'ok':True,'case_id':'test_case','serial':'1001','local_parameter_errors':[],
                  'input_validation':{'ok':True},'next_action':{'tool':'reimbursement_stage','stage':'basic'}}
        def invoke(argv,**kwargs):
            self.assertEqual(argv[2:4],['call','--input'])
            request=json.loads(__import__('pathlib').Path(argv[4]).read_text())
            self.assertEqual(request,{'method':'overview','params':{'case_id':'test_case'}})
            self.assertNotIn('--execute',argv)
            return types.SimpleNamespace(returncode=0,stdout=json.dumps(overview),stderr='')
        request={'item_id':'1','version':self.f.version,'revision':0}
        with patch.object(workflow.subprocess,'run',side_effect=invoke):
            result=workflow.inspect_formal(self.ws,request)
        self.assertFalse(result['receipt']['system_write']);self.assertEqual(result['receipt']['material_issues'],['Q1'])
        for bad in [{**request,'execute':True},{**request,'revision':1}]:
            with self.assertRaises(ReviewError):workflow.inspect_formal(self.ws,bad)
        overview['serial']='DIFFERENT'
        with patch.object(workflow.subprocess,'run',side_effect=invoke):
            with self.assertRaises(ReviewError):workflow.inspect_formal(self.ws,request)

    def test_new_material_version_invalidates_business_binding(self):
        workflow.import_status(self.ws,self.cp)
        self.f.data['title']='new source version';self.f.publish()
        self.assertFalse(workflow.summary(self.ws)['available'])

    def test_unbuilt_without_issues_is_candidate_not_approved(self):
        self.f.data['items']['1'].update(bill_number='',issue_ids=[])
        self.f.publish();workflow.import_status(self.ws,self.cp)
        row=workflow.summary(self.ws)['items']['1']
        self.assertEqual(row['route'],'candidate')
        self.assertFalse(row['can_inspect_formal'])

    def test_hold_and_unknown_existing_status_never_become_new_candidates(self):
        self.f.data['items']['1'].update(bill_number='', issue_ids=[], queue='按用户安排暂缓')
        self.f.publish();workflow.import_status(self.ws,self.cp)
        self.assertEqual(workflow.summary(self.ws)['items']['1']['route'],'hold')
        self.f.data['items']['1'].update(bill_number='1001', queue='')
        self.f.publish()
        self.native['result']['rows'][0]['t.status']='unknown'
        self.publish();workflow.import_status(self.ws,self.cp)
        row=workflow.summary(self.ws)['items']['1']
        self.assertEqual(row['route'],'existing');self.assertFalse(row['can_prepare'])

    def test_handoff_does_not_export_financial_binding_or_preflights(self):
        import zipfile
        from material_review_package import export_bundle
        workflow.import_status(self.ws,self.cp)
        atomic_json(self.ws/'workflow'/'preflights'/'private.json',{'secret':'LOCAL_PREFLIGHT_ONLY'})
        path, _ = export_bundle(self.ws, mode='handoff')
        with zipfile.ZipFile(path) as archive:
            names=archive.namelist()
            self.assertIn('tools/material_review_workflow.py',names)
            self.assertFalse(any(name.startswith('workspace/workflow/') or '.workflow.json' in name for name in names))


if __name__=='__main__':unittest.main()
