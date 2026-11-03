import copy,json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import finance_domestic_allowance as native

class DomesticAllowanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        audit=self.root/'audit.json';audit.write_text(json.dumps({'expenses':[{'费用编号':'E1','源SHA':'a'*64,'人员':'甲'}]}))
        self.plan={'schema':'ustc-travel-allowance-v1','case_id':'fixture','authorization':'fixture source','people':[{'id':'STUDENT1','name':'甲'}],
          'entries':[{'person_id':'STUDENT1','name':'甲','date':day,'departure_city':'合肥','arrival_city':'上海','amount':'180.00','source_expense_id':'E1','source_sha256':'a'*64}for day in ['2026-07-03','2026-07-11']],
          'amount':'360.00','source_audit':native.ref(audit)}

    def test_source_dates_and_original_audit_are_required(self):
        native.validate_plan(self.plan,'fixture')
        for mutate in [lambda p:p['entries'].append(copy.deepcopy(p['entries'][0])),lambda p:p['entries'][0].update(source_sha256='b'*64),lambda p:p.update(amount='720.00'),lambda p:p['people'].append(copy.deepcopy(p['people'][0]))]:
            p=copy.deepcopy(self.plan);mutate(p)
            with self.assertRaises(ValueError):native.validate_plan(p,'fixture')

    def final_fixture(self):
        adapter=self.root/'adapter.js';adapter.write_text('/* synthetic closed reader */')
        c={'case_id':'fixture','serial':'123456','tab_id':'99','expected_userid':'TEST001','expected_amount':'460.00','invoice_amount':'100.00','source_plan':self.plan,'expected_guids':['g1'],'adapter':native.ref(adapter)}
        script=self.root/'read.js';script.write_text(native.script(c,'reimbursementDomesticAllowanceFinalReadback'))
        body={'ok':True,'complete':True,'case_id':'fixture','serial':'123456','expected_userid':'TEST001','system_write':False,'invoice_amount':'100.00','allowance_amount':'360.00','people_count':1,'source_person_dates':2,
          'components':{'国内出差伙食补贴':'200.00','国内出差交通补贴':'160.00'},
          'bill':{'t.serial_no':'123456','t.REQ_INFO':'TEST001-申请人','t.status':'-99','t.bx_type':'2','t.lc_finish':'F','t.total_amt':'460.00'},
          'allowances':[{'t.sno':'STUDENT1','t.sname':'甲','t.serial_no':'123456','t.startdate':'2026-07-03 00:00:00','t.staydays':'9','t.STAYDAYS_HS':'2','t.STAYDAYS_JT':'2','t.hsbt_amt':'200.00','t.jtbt_amt':'160.00','t.total_amt':'360.00','t.cg_cq_amt':'0','t.bg_hotel_amt':'0'}],
          'used_rows':[{'t.tk_guid':'g1','t.tk_total_amt':'100.00'}]}
        return c,{'phase':'script_completed','tab_id':'99','script':str(script),'source_sha256':native.ref(script)['sha256'],'result':body}

    def test_final_proof_requires_same_claim_invoices_people_days_and_money(self):
        c,envelope=self.final_fixture();self.assertEqual(native.verify_final(c,envelope)['amount'],'460.00')
        for mutate in [lambda b:b['bill'].update({'t.status':'0'}),lambda b:b['used_rows'][0].update({'t.tk_guid':'other'}),lambda b:b['used_rows'][0].update({'t.tk_total_amt':'99.00'}),lambda b:b['allowances'][0].update({'t.STAYDAYS_HS':'9'}),lambda b:b['allowances'][0].update({'t.total_amt':'1620.00'}),lambda b:b['components'].update({'国内出差伙食补贴':'199.00'})]:
            bad=copy.deepcopy(envelope);mutate(bad['result'])
            with self.assertRaises(ValueError):native.verify_final(c,bad)
        Path(envelope['script']).write_text('/* changed reader */')
        with self.assertRaises(ValueError):native.verify_final(c,envelope)

if __name__=='__main__':unittest.main()
