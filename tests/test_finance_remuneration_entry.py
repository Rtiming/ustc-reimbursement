import json
from node_support import node_run
import sys
import unittest
import tempfile
import hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from finance_remuneration_entry import SCRIPT, activity_context

class RemunerationEntryTests(unittest.TestCase):
 def run_case(self,changes):
  js='''const vm=require('node:vm');const o=JSON.parse(process.argv[1]);let clicked=0;
  const e={getClientRects:()=>o.hidden?[]:[1],textContent:o.wrongLabel?'其他业务':'薪酬发放',click:()=>clicked++};
  const $={getProjID:()=> 'WF_YBX',active:o.busy?1:0,page:{currPage:'6'},UC:{bindData:k=>k.includes('userid')?(o.wrongUser?'OTHER':'TEST01'):(o.bound?'123':'')}};
  const ctx={$,location:{origin:'https://cwzh.ustc.edu.cn'},document:{getElementById:()=>e,querySelectorAll:()=>o.dialog?[e]:[]}};
  (async()=>{let result,error;try{result=await vm.runInNewContext(process.argv[2],ctx)({case_id:'test_case',expected_userid:'TEST01'})}catch(e){error=e.message}
  console.log(JSON.stringify({result,error,clicked}));})();'''
  r=node_run(js,json.dumps(changes),SCRIPT,capture_output=True,text=True,check=True);return json.loads(r.stdout)
 def test_only_known_home_entry_opens(self):
  r=self.run_case({});self.assertEqual(r['clicked'],1);self.assertFalse(r['result']['saveRequested']);self.assertFalse(r['result']['submitRequested'])
  for flag in ['wrongUser','hidden','wrongLabel','busy','bound','dialog']:
   r=self.run_case({flag:True});self.assertEqual(r['clicked'],0);self.assertTrue(r['error'])

class MaterialScopeTests(unittest.TestCase):
 def test_only_verified_source_person_and_budget_are_prepared(self):
  with tempfile.TemporaryDirectory()as folder:
   root=Path(folder);source=root/'original.jpg';source.write_bytes(b'fixture original')
   original={'id':'event_one','summary':'测试活动劳务报销','amount_status':'materials_matched_pending_native_validation','invoice_count':0,'claimed_amount':'900','signed_total':'900','budget_amount':'1000','recipients':[{'student_id':'SA11000001','name':'测试甲','amount':'450'},{'student_id':'BA11000002','name':'测试乙','amount':'450'}],'sources':[{'path':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}],'business_date':'2026-05-31'}
   review=root/'review.json'
   def bind(row):
    review.write_text(json.dumps({'case_id':'test_case','documents':[row]}))
    return {'case_id':'test_case','material_context':{'remuneration_document_id':'event_one','labor_material_review':{'labor_review':{'path':str(review),'sha256':hashlib.sha256(review.read_bytes()).hexdigest()}}}}
   case=bind(original);self.assertEqual(activity_context(case,'SA11000001')['period_start'],'2026-05-31')
   with self.assertRaises(ValueError):activity_context(case,'SA11000099')
   for change in [{'amount_status':'awaiting_budget_scope_confirmation'},{'budget_amount':'400'},{'signed_total':'1000'},{'invoice_count':1}]:
    with self.assertRaises(ValueError):activity_context(bind({**original,**change}))
   case=bind(original);source.write_bytes(b'changed original')
   with self.assertRaisesRegex(ValueError,'SHA'):activity_context(case)
   review.write_text('{}')
   with self.assertRaisesRegex(ValueError,'SHA'):activity_context(case)

if __name__=='__main__':unittest.main()
