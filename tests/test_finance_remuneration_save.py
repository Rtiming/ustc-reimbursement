import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from finance_remuneration_save import bind_add, normalized_rows, verify_saved, ADD_SCRIPT, DEFINITION_KEYS, GUID, verify_created, CREATE_SCRIPT
from node_support import node_run

class RemunerationSaveTests(unittest.TestCase):
 def test_readback_rejects_wrong_person_amount_extra_rows_and_changed_plan(self):
  with tempfile.TemporaryDirectory()as folder:
   p=Path(folder)/'plan.json';row={'student_id':'SA11000001','name':'测试甲','amount':'450.00','reason':'活动现场劳务'}
   plan={'case_id':'test_case','document_id':'event_one','expected_userid':'TEST01','after':[row]};p.write_text(json.dumps(plan))
   previous={'case_id':'test_case','verification':{'plan':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}}
   body={'projid':'WF_YBX','identity':{'userid':'TEST01'},'serial':None,'grids':[{'funcno':'280','records':1,'allRecordsVisible':True,'loading':False,'rows':[{'t.unino':row['student_id'],'t.name':row['name'],'t.AMT':row['amount'],'t.premark':row['reason']}]}]}
   self.assertEqual(verify_saved(previous,{'result':body},'saved')['saved_people'],1)
   for key,value in [('t.unino','SA11000002'),('t.AMT','451'),('t.name','其他人'),('t.premark','其他事由')]:
    bad=copy.deepcopy(body);bad['grids'][0]['rows'][0][key]=value
    with self.assertRaises(ValueError):verify_saved(previous,{'result':bad},'saved')
   bad=copy.deepcopy(body);bad['identity']['userid']='OTHER'
   with self.assertRaises(ValueError):verify_saved(previous,{'result':bad},'saved')
   bad=copy.deepcopy(body);bad['grids'][0]['allRecordsVisible']=False
   with self.assertRaises(ValueError):verify_saved(previous,{'result':bad},'saved')
   with self.assertRaises(ValueError):verify_saved(previous,{'result':body},'not_applied')
   p.write_text('{}')
   with self.assertRaisesRegex(ValueError,'SHA'):verify_saved(previous,{'result':body},'saved')
 def test_native_add_happens_once_only_when_person_bank_and_definition_match(self):
  js=r"""const vm=require('node:vm');const o=JSON.parse(process.argv[1]);let clicks=0;const c={case_id:'test_case',document_id:'event_one',expected_userid:'TEST01',student_id:'SA11000001',person_name:'测试甲',card_tail:'4321',amount:'450.00',reason:'活动现场劳务',period_start:'2026-05-31',period_end:'2026-05-31',before:[],definition:{btnguid:'289FA79F8B1D4C6F8E427D84000E92D9',caption:'添加'}};
  const values={unino:o.wrongPerson?'SA11000002':c.student_id,sname:c.person_name,state:'学生',amt:'',premark:'',bankno:'registered',cardno:o.wrongBank?'12349999':'12344321'};
  const fields=Object.fromEntries(Object.entries(values).map(([k,value])=>['formWF_YBX_282_dd-'+k,{id:'formWF_YBX_282_dd-'+k,value,getClientRects:()=>[1]}]));
  for(const[k,value]of Object.entries({state_ybx:'学生人员一般薪酬',ff_startdate:c.period_start,ff_enddate:c.period_end,hastax:'T'}))fields['formWF_YBX_277_dd-'+k]={value};
  const grid={0:{grid:{hDiv:{loading:false}}},jqGrid:(fn,key)=>fn==='getDataIDs'?[]:0};const $=s=>grid;$.active=0;$.getProjID=()=> 'WF_YBX';$.UC={bindData:k=>k.includes('userinfo')?'TEST01':k.includes('serialno')?'':fields['formWF_YBX_282_dd-'+k.slice(8,-1)]?.value};
  $.F={getInputData:()=> '学生其他劳务',setInputData:(id,type,value)=>fields[id].value=value,getDataToContext:()=>{}};$.page={list:{WF_YBX_199:{wins:[{func_btns:[{...c.definition,caption:o.changedDefinition?'改名':'添加'}]}]}}};
  const button={getClientRects:()=>[1],click:()=>clicks++};const document={getElementById:id=>fields[id],querySelectorAll:()=>[button]};
  (async()=>{let error;try{await vm.runInNewContext(process.argv[2],{$,document,location:{origin:'https://cwzh.ustc.edu.cn'}})(c)}catch(e){error=e.message}console.log(JSON.stringify({clicks,error}));})();"""
  def run(options):return json.loads(node_run(js,json.dumps(options),ADD_SCRIPT,check=True,capture_output=True,text=True).stdout)
  result=run({});self.assertEqual(result['clicks'],1,result)
  for flag in ['wrongPerson','wrongBank','changedDefinition']:
   result=run({flag:True});self.assertEqual(result['clicks'],0);self.assertTrue(result['error'])

 def test_created_payroll_must_match_native_dates_people_total_and_account(self):
  row={'student_id':'SA11000001','name':'测试甲','amount':'450.00','reason':'活动劳务'}
  plan={'expected_userid':'TEST01','period_start':'2026-05-31','period_end':'2026-05-31','claim_amount':'450.00','before':[row]}
  text='中国科学技术大学薪酬业务单 报销单号：99001 酬金性质 学生其他劳务 所得开始时间 2026-05-31 所得结束时间 2026-05-31 金额总计：450.00'
  header=['序号','工号','姓名','人员类型','支付方式','卡号','户名','发放事由','金额']
  receipt={'result':{'ok':True,'projid':'WF_YBX','serial':'99001','identity':{'userid':'TEST01'},'frames':[{'visibleText':text,'tables':[[header,['1','SA11000001','测试甲','学生','转卡','****4321','测试甲','活动劳务','450.00']]]}]}}
  self.assertEqual(verify_created(plan,receipt,'99001'),[row])
  for old,new in [('所得开始时间 2026-05-31','所得开始时间 2026-06-01'),('金额总计：450.00','金额总计：900.00'),('酬金性质 学生其他劳务','酬金性质 科研劳务')]:
   bad=copy.deepcopy(receipt);bad['result']['frames'][0]['visibleText']=text.replace(old,new)
   with self.assertRaises(ValueError):verify_created(plan,bad,'99001')
  bad=copy.deepcopy(receipt);bad['result']['frames'][0]['tables'][0][-1][1]='SA11000002'
  with self.assertRaises(ValueError):verify_created(plan,bad,'99001')
  with self.assertRaises(ValueError):verify_created(plan,receipt,'99002')
  node_run("new (require('node:vm').Script)('('+process.argv[1]+')')",CREATE_SCRIPT,check=True,capture_output=True,text=True)

 def test_script_parses_without_execution(self):
  node_run("new (require('node:vm').Script)('('+process.argv[1]+')')",ADD_SCRIPT,check=True,capture_output=True,text=True)

if __name__=='__main__':unittest.main()
