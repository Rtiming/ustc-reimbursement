import copy,json,sys,tempfile,unittest
from unittest.mock import patch
from contextlib import nullcontext
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from finance_allowance import validate_sources,original,validate_standalone_scope
from finance_native_create import ref

class AllowanceSources(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        def save(name,value):
            p=self.root/name;p.write_text(json.dumps(value));return ref(p)
        e={'person_id':'TEST001','name':'Synthetic','date':'2026-07-03','departure_city':'合肥','arrival_city':'上海','amount':'180.00','source_expense_id':'E1','source_sha256':'a'*64}
        audit=save('audit.json',{'expenses':[{'费用编号':'E1','源SHA':'a'*64,'人员':'Synthetic'}]})
        source=save('source.json',{'schema':'ustc-travel-allowance-v1','case_id':'fixture','authorization':'Confirmed two-day plan','source_audit':audit,'amount':'180.00','people':[{'id':'TEST001','name':'Synthetic'}],'entries':[e]})
        prepared={'complete':True,'system_write':False,'source_amount':'180.00','groups':[{'entries':[e],'values':{'slry':'1','ylry':'0','elry':'0','sdate':e['date'],'edate':e['date'],'cllx':'1','feeamount':'180.00','hsbt':'100','jtbt':'80'}}]}
        self.c={'case_id':'fixture','expected_amount':'180.00','tab_id':'123','source':source,'prepared':prepared,'prepared_ref':save('prepared.json',{'phase':'script_completed','tab_id':'123','result':prepared}),'form_before':{'serial':None,'expected_invoice_count':0,'payments':[{'mode':'bank_transfer','person_id':'TEST001','name':'Synthetic','amount':'180.00'}],'funding':[{'amount':'180.00'}]}}
    def tearDown(self):self.tmp.cleanup()
    def test_source_and_personal_payment(self):validate_sources(self.c)
    def test_different_payee_and_amount_rejected(self):
        for field,value in [('person_id','OTHER'),('amount','360.00')]:
            c=copy.deepcopy(self.c);c['form_before']['payments'][0][field]=value
            with self.assertRaises(ValueError):validate_sources(c)
    def test_tampered_source_rejected(self):
        Path(self.c['source']['path']).write_text('{}')
        with self.assertRaises(ValueError):validate_sources(self.c)
    def test_ticket_count_rejected(self):
        c=copy.deepcopy(self.c);c['form_before']['expected_invoice_count']=1
        with self.assertRaises(ValueError):validate_sources(c)
    def test_parent_travel_needs_explicit_separate_claim_source(self):
        with self.assertRaises(ValueError):validate_standalone_scope({'authorization':'按规范做单'})
        plan={'logical_parent_case_id':'travel','authorization':'按规范做单'}
        with self.assertRaisesRegex(ValueError,'须同单'):validate_standalone_scope(plan)
        plan['separate_claim_authorization']={'explicit_separate_claim':True,'source':'this chat','user_text':'补助单独做一张'}
        validate_standalone_scope(plan)
        plan['separate_claim_authorization']['explicit_separate_claim']=False
        with self.assertRaises(ValueError):validate_standalone_scope(plan)
    def test_unknown_marker_recovery_is_read_once_and_has_stable_receipt_time(self):
        adapter=self.root/'adapter.js';adapter.write_text('/* synthetic adapter */');prepare=self.root/'create-prepare.json';prepare.write_text(json.dumps({'result':{'ok':True}}))
        c={'adapter':ref(adapter),'tab_id':'123','creation_prepare':ref(prepare)}
        script=self.root/'create.js';script.write_text('(async()=>{'+adapter.read_text()+';return await reimbursementAllowanceCreate('+json.dumps({**c,'creation_prepare':{'ok':True},'pending_registered':True},ensure_ascii=False)+');})()')
        runtime=self.root/'create.json';runtime.write_text(json.dumps({'tab_id':'123','phase':'await_original_marker','script':str(script),'source_sha256':ref(script)['sha256'],'result_marker':'reimbursement-result-'+'a'*32,'executed_at_utc':None}))
        with patch('finance_backend.assert_original_transport'),patch('edge_runtime.browser_lock',return_value=nullcontext()),patch('edge_runtime.read_marker',return_value={'ok':True,'serial':'12345'})as marker:
            first=original(c,runtime,True);second=original(c,runtime,True)
        self.assertEqual(marker.call_count,1);self.assertEqual(first,second);self.assertIsInstance(first[1],str)
        runtime.write_text('{}')
        with self.assertRaises((ValueError,KeyError)):original(c,runtime,True)

if __name__=='__main__':unittest.main()
