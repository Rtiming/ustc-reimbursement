import json,tempfile,unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import applicant_http_batch as batch

class FakeService:
    def __init__(self,root):self.root=Path(root);self.jobs={};self.writes=0;self.verified=True;self.approved=False;self.fail=False
    def enqueue(self,cid,op,p,request,execute=False):
        if request in self.jobs:return{'job_id':request}
        action=p.get('action');r={'ok':True};status='completed'
        if action=='wake-interface':r['window_id']='12'
        if action=='applicant-verification-open':r.update(state='verification_required',verification_window_visible=True)
        if action in {'applicant-inspect','applicant-readback','applicant-recover'}:r.update(completed=self.approved,verification_required=not self.verified,original={'bill':{'t.node_id':'449'if self.approved else'508'}})
        if action=='applicant-agree':
            self.writes+=1;self.approved=not self.fail;r.update(approval_dispatched=True)
            if self.fail:status='failed';r['ok']=False
        path=self.root/(str(len(self.jobs))+'.json');path.write_text(json.dumps({'result':r}));self.jobs[request]=(path,status)
        return{'job_id':request}
    def result(self,cid,j):return{'status':self.jobs[j][1],'exit_code':0 if self.jobs[j][1]=='completed'else 2,'artifacts':[{'name':'output.json','artifact_id':'a0'}]}
    def artifact(self,cid,j,a):return self.jobs[j][0]
    def overview(self,cid):return{'unverified_write':False}

class ApplicantHttpBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.s=FakeService(self.root)
        self.c={'workspace':str(self.root),'material_roots':[str(self.root)],'manifest':{'path':str(self.root/'manifest.json'),'sha256':'a'*64},'case_id':'fixture','tab_id':'99','execute':True,'request_id':'fixture-batch'}
        self.value={'show_verification_interface':False,'cases':[{'case_id':'fixture','serial':'123','plan':{'serial':'123'}}]}
    def run_batch(self):
        with patch.object(batch,'AssistantService',return_value=self.s),patch.object(batch,'manifest',return_value=self.value),patch('applicant_batch.check_case_binding',return_value=(None,{})),patch('finance_native_applicant.authority',return_value={}):return batch.run(self.c,self.root/'result.json')
    def test_completed_original_skipped_on_resume(self):
        a=self.run_batch();self.assertEqual(a['state'],'completed');self.assertEqual(a['submitted_count'],1);self.assertEqual(self.s.writes,1)
        b=self.run_batch();self.assertEqual(b['submitted_count'],1);self.assertEqual(self.s.writes,1);self.assertEqual(b['created_claims'],0)
    def test_sms_stops_without_write_then_same_batch_continues(self):
        self.s.verified=False;a=self.run_batch();self.assertEqual(a['state'],'verification_required');self.assertEqual(self.s.writes,0)
        self.assertTrue(a['results'][0]['verification_handoff']['result']['verification_window_visible']);self.assertTrue(a['next_action']['requires_user_input'])
        self.s.verified=True;b=self.run_batch();self.assertEqual(b['state'],'completed');self.assertEqual(self.s.writes,1);self.assertGreaterEqual(b['human_wait_seconds'],0)
    def test_unknown_never_reissues_same_write(self):
        self.s.fail=True;a=self.run_batch();self.assertEqual(a['state'],'original_job_requires_recovery');self.assertEqual(self.s.writes,1)
        b=self.run_batch();self.assertEqual(b['state'],'original_job_requires_recovery');self.assertEqual(self.s.writes,1)

    def test_incomplete_historical_timing_is_excluded_from_mean(self):
        self.run_batch();p=self.root/'.local/applicant_batches'/('a'*64)/'state.json';v=json.loads(p.read_text());v['cases']['fixture'].pop('submission_seconds');v['cases']['fixture']['active_seconds']=0;v['cases']['fixture']['timings'][0]['seconds']=100;p.write_text(json.dumps(v))
        report=self.run_batch();self.assertEqual(report['submitted_count'],1);self.assertIsNone(report['mean_completed_submission_seconds']);self.assertEqual(report['excluded_incomplete_timing_cases'],['fixture'])

    def test_public_preview_then_execute_uses_same_request_without_input_collision(self):
        from assistant_api import AssistantService
        service=AssistantService(self.root,[self.root])
        with patch.object(batch,'manifest',return_value=self.value),patch.object(batch,'run',return_value={'ok':True})as run:
            for execute in [False,True,True]:
                service.applicant_batch('fixture',self.c['manifest'],'99','same-request',execute)
        self.assertEqual(run.call_count,3)

    def test_registered_entry_uses_manifest_scope_and_only_preserves_unfinished_page(self):
        from applicant_batch import run_applicant_batch
        manifest=self.root/'registered.json';manifest.write_text(json.dumps({'schema':'ustc-applicant-http-batch-v1','case_id':'fixture'}))
        modes=[];session=SimpleNamespace(case='parent_batch',state={'tab_id':'99'},ensure=lambda **kw:modes.append(kw['preserve_original']))
        with patch.object(batch,'manifest',return_value=self.value),patch.object(self.s,'applicant_batch',create=True,return_value={'ok':True})as invoke:
            run_applicant_batch(self.s,session,manifest,execute=True)
            self.assertEqual(invoke.call_args.args[0],'fixture');self.assertEqual(session.ids,['fixture']);self.assertEqual(modes,[False])
            with patch.object(self.s,'overview',return_value={'unverified_write':True}):run_applicant_batch(self.s,session,manifest,execute=True)
            self.assertEqual(modes,[False,True])

if __name__=='__main__':unittest.main()
