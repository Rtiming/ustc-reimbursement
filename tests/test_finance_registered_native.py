"""登记批次的新入口只编排正式HTTP动作；已匹配和本人动作边界不写入。"""
import json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from assistant_api import AssistantService,atomic_json,read_json
from finance_registered_native import run_registered_native,no_change

class RegisteredNativeTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve();self.service=AssistantService(self.root,[self.root]);self.service.new_case('test_batch_one');_,self.paths=self.service.resolve('test_batch_one');form=read_json(self.paths['form']);form.update(serial='123',amount='10.00',contract={'exists':False,'source':'synthetic case fact'},expense_categories=[],explanations={},pre_review=[],review={});atomic_json(self.paths['form'],form)
        self.auth=self.root/'authorization.json';atomic_json(self.auth,{'submit_authorized':True});self.session=SimpleNamespace(case='test_batch',folder=self.root/'registered',cfg={'expected_userid':'TEST001'},state={'tab_id':'7'},job=self.job);self.session.folder.mkdir();self.calls=[];self.terminal=False;self.item={'case_id':'test_batch_one','sequence':1,'ready_for_draft':True};self.body=None
    def job(self,case,operation,parameters,label,execute=False,request_id=None):
        self.calls.append((operation,parameters,execute));n=len(self.calls);art={'name':'output.json','artifact_id':'a0','sha256':'synthetic'};result={'job_id':'synthetic_'+str(n),'artifacts':[art]}
        if operation=='submission':body={'original':{'bill':{'t.status':'10'if self.terminal else'-99','t.lc_finish':'T'if self.terminal else'F','t.status_name':'已预约待投递'if self.terminal else'草稿'},'options':{'print':'N','proofs':[]}},'native_error_present':False,'promise_sha256':'fixed'}
        else:body={'original':{},'changes':{}}
        return result,body
    def test_fill_skips_matching_stages_without_ui_or_save(self):
        result=run_registered_native(self.service,self.session,[self.item],self.auth,'fill');self.assertTrue(result['ok']);self.assertEqual(result['filled_count'],1);self.assertTrue(all(not execute for _,_,execute in self.calls));self.assertTrue(all(op in {'business','submission'}for op,_,_ in self.calls));self.assertEqual({p['action']for op,p,_ in self.calls if op=='business'},{'funding-prepare','basic-prepare','expenses-prepare','payments-prepare'})
    def test_ready_requires_actual_case_promise_acknowledgement_without_next(self):
        result=run_registered_native(self.service,self.session,[self.item],self.auth,'ready');self.assertEqual(result['results'][0]['status'],'held_promise_acknowledgement');self.assertEqual(len(self.calls),1);self.assertFalse(result['sms_sent']);self.assertFalse(result['final_applicant_approval_executed'])
    def test_original_pending_blocks_before_any_new_business_request(self):
        atomic_json(self.paths['state'].parent/'pending_write.json',{'operation':'synthetic_untested'});r=run_registered_native(self.service,self.session,[self.item],self.auth,'ready');self.assertEqual(r['results'][0]['status'],'held_original_pending');self.assertEqual(self.calls,[])
    def test_completed_bill_is_recognized_without_restarting_workflow(self):
        self.terminal=True;r=run_registered_native(self.service,self.session,[self.item],self.auth,'ready');self.assertEqual(r['results'][0]['status'],'already_approved_ready_verified');self.assertEqual(len(self.calls),1)
    def test_new_card_or_corporate_identity_is_not_skipped_as_no_change(self):
        self.assertFalse(no_change('payments',{'changes':{},'card_bindings':[{'selection_required':True}]}));self.assertFalse(no_change('payments',{'changes':{},'corporate_bindings':[{'resolve_required':True}]}))

if __name__=='__main__':unittest.main()
