"""Synthetic authorization tests; never contact the financial server."""
import copy,json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from assistant_api import AssistantService,atomic_json,read_json
from finance_applicant_verification import command
from finance_native_create import ref
from submission import view_plan

class ApplicantVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.s=AssistantService(self.root,[self.root]);self.s.new_case('verify_fixture');_,paths=self.s.resolve('verify_fixture')
        f=read_json(paths['form']);f.update(serial='123',amount='10.00',expected_userid='TEST001',contract={'exists':False});f['basic']['fields'].update({'t-abst':'Purpose','t-req_mobile':'13000000000'});atomic_json(paths['form'],f)
        self.plan=view_plan(f,'TEST001','Current user authorization');self.plan['applicant_agreement_authorized']=True
        self.auth={'schema':'ustc-applicant-submission-authority-v1','user_text':'Continue these original bills','expected_userid':'TEST001','approval_opinion':'同意','applicant_agreement_authorized':True,'preserve_sms_verification':True,'create_new_claims':False,'entries':[{'case_id':'verify_fixture','serial':'123','current_amount':'10.00'}]}
        self.authority=self.root/'authority.json';atomic_json(self.authority,self.auth);self.folder=self.root/'job';self.folder.mkdir()
        self.request={'case_id':'verify_fixture','request_id':'original-human-handoff','execute':True,'operation':'access','parameters':{'action':'applicant-verification-open','tab_id':'99','plan':self.plan,'authority':ref(self.authority)}}

    def test_fixed_original_handoff_has_no_creation_or_code_input(self):
        cmd=self.s.command(self.request,self.folder)
        self.assertIn('--operation-kind',cmd);self.assertEqual(cmd[cmd.index('--operation-kind')+1],'read')
        c=read_json(self.folder/'verification-input.json')['config'];self.assertEqual(c['serial'],'123');self.assertEqual(c['expected_amount'],'10.00');self.assertNotIn('code',c)
        self.assertEqual(cmd,command(self.s,self.request,self.folder))

    def test_scope_and_source_drift_rejected(self):
        for mutate in ['amount','serial','account','authority','create','sms','plan','sha','extra','execute']:
            with self.subTest(mutate=mutate):
                r=copy.deepcopy(self.request);a=copy.deepcopy(self.auth)
                if mutate in ['amount','serial']:a['entries'][0]['current_amount'if mutate=='amount'else'serial']='999'
                if mutate=='account':a['expected_userid']='OTHER'
                if mutate=='authority':a['applicant_agreement_authorized']=False
                if mutate=='create':a['create_new_claims']=True
                if mutate=='sms':a['preserve_sms_verification']=False
                if mutate=='plan':r['parameters']['plan']['applicant_agreement_authorized']=False
                if mutate=='extra':r['parameters']['code']='forbidden'
                if mutate=='execute':r['execute']=False
                atomic_json(self.authority,a);r['parameters']['authority']=ref(self.authority)
                if mutate=='sha':self.authority.write_text('{}')
                with self.assertRaises(ValueError):command(self.s,r,self.folder)

if __name__=='__main__':unittest.main()
