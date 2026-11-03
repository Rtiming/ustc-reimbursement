"""Original approval intent and readback contract; synthetic, no financial access."""
import copy,json,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from test_finance_applicant_verification import ApplicantVerificationTests
from assistant_api import atomic_json,read_json
from finance_native_create import ref
import finance_native_applicant as native

class NativeApplicantTests(ApplicantVerificationTests):
    def setUp(self):
        super().setUp();self.plan['source']=str(self.authority)+'#sha256='+ref(self.authority)['sha256'];self.writes=0
        self.request={'case_id':'verify_fixture','request_id':'agree-original','operation':'submission','execute':True,'parameters':{'action':'applicant-agree','tab_id':'99','plan':self.plan}}
    def runtime(self,c,method,output,extra=None,write=False):
        output=Path(output);target=output.with_suffix('.js');target.write_text(native.script(c,method,extra));after=c.get('readback')is True
        if method=='reimbursementApplicantAgree':
            self.writes+=1;r={'ok':True,'case_id':c['case_id'],'serial':c['serial'],'expected_userid':c['expected_userid'],'approval_dispatched':True,'save_dispatched':True};stamp='2026-10-31T12:00:02+00:00'
        else:
            r={'ok':True,'complete':True,'action':'applicant-http-inspect','case_id':c['case_id'],'serial':c['serial'],'expected_userid':c['expected_userid'],'business_transport':'native_http','system_write':False,'save_dispatched':False,'completed':after,'verification_required':False,'original':{'bill':{'t.serial_no':'123','t.REQ_INFO':'TEST001-Test','t.total_amt':'10.00','t.abst':'Purpose','t.basetable_key':'123','t.uni_key':'456','t.wf_name':'YBX_LC','t.node_id':'510'if after else'508'},'history':[]}};stamp='2026-10-31T12:00:03+00:00'if after else'2026-10-31T12:00:01+00:00'
        e={'phase':'script_completed','tab_id':'99','result':r,'script':str(target),'source_sha256':ref(target)['sha256'],'dispatch_intent_at_utc':stamp,'executed_at_utc':stamp};atomic_json(output,e);return e
    def test_fixed_original_handoff_has_no_creation_or_code_input(self):
        self.s.command(self.request,self.folder);p=self.folder/'native-applicant-input.json';out=self.folder/'output.json'
        with patch.object(native,'browser',side_effect=self.runtime):self.assertEqual(native.run(p,out),0)
        _,paths=self.s.resolve('verify_fixture');pending=read_json(paths['state'].parent/'pending_write.json');c=read_json(p);c.update(action='applicant-readback',readback=True,readback_source={'input':ref(p),'prepare':ref(self.folder/'native-applicant-prepare.json'),'save':ref(out)})
        observed=self.runtime(c,'reimbursementApplicantInspect',self.folder/'readback.json');self.assertTrue(native.verify_ack(pending,observed,'saved')['applicant_completed'])
        with patch.object(native,'browser',side_effect=self.runtime):
            with self.assertRaisesRegex(ValueError,'旧写入'):native.run(p,out)
        self.assertEqual(self.writes,1)
        for mutation in ['amount','node','earlier','script']:
            e=copy.deepcopy(observed)
            if mutation=='amount':e['result']['original']['bill']['t.total_amt']='11.00'
            if mutation=='node':e['result']['completed']=False
            if mutation=='earlier':e['dispatch_intent_at_utc']='2026-10-31T12:00:00+00:00'
            if mutation=='script':e['source_sha256']='0'*64
            with self.assertRaises(ValueError):native.verify_ack(pending,e,'saved')
    def test_scope_and_source_drift_rejected(self):
        for key,value in [('amount','11.00'),('applicant_agreement_authorized',False),('source','unhashed')]:
            r=copy.deepcopy(self.request);r['parameters']['plan'][key]=value
            with self.assertRaises(ValueError):self.s.command(r,self.folder)

    def test_legacy_sms_stop_requires_original_receipt_and_fresh_same_bill(self):
        self.s.command(self.request,self.folder);c=read_json(self.folder/'native-applicant-input.json')
        legacy_script=self.folder/'legacy.js';legacy_script.write_text('original legacy adapter')
        saved=self.folder/'legacy-output.json';plan=self.folder/'legacy-plan.json';atomic_json(plan,self.plan)
        prior={'phase':'script_completed','script':str(legacy_script),'source_sha256':ref(legacy_script)['sha256'],'executed_at_utc':'2026-10-31T11:00:00+00:00','result':{'ok':True,'action':'applicant_agree_requested','verification_required':True,'native_verification_enabled':True,'applicant_userid':'TEST001','serial':'123'}}
        atomic_json(saved,prior)
        pending={'operation':'submission:applicant-agree','case_id':'verify_fixture','serial':'123','verification':{'save_receipt':str(saved),'submission_plan':str(plan)}}
        observed=self.runtime(c,'reimbursementApplicantInspect',self.folder/'legacy-read.json');observed['result']['original']['applicant']={}
        self.assertFalse(native.verify_legacy_sms_unapplied(pending,observed,'not_applied')['financial_action_replayed'])
        for field,value in [('verification_required',False),('serial','999'),('applicant_userid','OTHER')]:
            bad=copy.deepcopy(prior);bad['result'][field]=value;atomic_json(saved,bad)
            with self.assertRaises(ValueError):native.verify_legacy_sms_unapplied(pending,observed,'not_applied')
        atomic_json(saved,prior);observed['result']['completed']=True
        with self.assertRaises(ValueError):native.verify_legacy_sms_unapplied(pending,observed,'not_applied')
