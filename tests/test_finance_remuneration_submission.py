import json
import sys
import unittest
from pathlib import Path
from node_support import node_run
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from finance_remuneration_submission import SCRIPT

class PayrollSubmissionTests(unittest.TestCase):
 def run_fixture(self,changes):
  js=r'''const vm=require('node:vm'),o=JSON.parse(process.argv[1]);let clicked=0;
  const person={student_id:'SA11000001',name:'测试甲',amount:'450.00',reason:'活动劳务'};
  const c={serial:'99001',system_mode:'remuneration',amount:'450.00',expected_userid:'TEST01',funding:[{code:'TESTFUND',name:'测试项目'}],basic:{teacher_id:'11001',teacher_name:'测试老师',fields:{'t-cl_abst':'联系人测试'}}};
  const p={summary:'测试活动劳务报销',phone:'13800000000',delegate_finance_print:false,contract_exists:false};
  const cells=[['序号','工号','姓名','人员类型','支付方式','卡号','户名','发放事由','金额'],['1',person.student_id,person.name,'学生','转卡','****4321',person.name,person.reason,o.wrongPersonAmount?'451.00':'450.00'],['学生-学生其他劳务','其他临时','TESTFUND/A99902','测试老师','450.00'],['摘要',p.summary],['备注','联系人测试']];
  const text='中国科学技术大学薪酬业务单 报销单号：99001 财务审批意见 会计审核 金额总计：'+(o.wrongTotal?'900.00':'450.00')+' 酬金性质 学生其他劳务 所得开始时间 2026-05-31 所得结束时间 2026-05-31 11001 测试老师 13800000000 TESTFUND/测试项目/测试单位';
  const visible=()=>[1],prints=['Y','N'].map(value=>({value,checked:value===(o.action==='next'?'N':'Y'),getClientRects:visible,click(){clicked++;prints.forEach(x=>x.checked=x===this)}}));
  const salary=[{value:'Y',checked:!!o.wrongPayment},{value:'N',checked:!o.wrongPayment}];
  const d={body:{innerText:text},querySelectorAll:s=>s==='table'?[{rows:cells.map(r=>({cells:r.map(innerText=>({innerText}))}))}]:s.includes('form_cj_paytype')?salary:s.includes('form_grantcwprint')?prints:[]};
  const f={getClientRects:visible,contentDocument:d},button={getClientRects:visible,click(){clicked++}};
  const document={getElementById:()=>f,querySelectorAll:()=>[button]};
  const $={active:0,getProjID:()=> 'WF_YBX',UC:{bindData:k=>k.includes('userinfo')?(o.wrongUser?'OTHER':'TEST01'):'99001'}};
  (async()=>{let error;try{await vm.runInNewContext(process.argv[2],{$,document,location:{origin:'https://cwzh.ustc.edu.cn'}})({form:c,plan:p,rows:[person],period_start:'2026-05-31',period_end:'2026-05-31',action:o.action||'options'})}catch(e){error=e.message}console.log(JSON.stringify({clicked,error}));})();'''
  return json.loads(node_run(js,json.dumps(changes),SCRIPT,text=True,capture_output=True,check=True).stdout)
 def test_options_and_next_require_current_payroll_people_money_and_payment_mode(self):
  for action in ['options','next']:
   result=self.run_fixture({'action':action});self.assertEqual(result['clicked'],1,result)
   for field in ['wrongUser','wrongTotal','wrongPersonAmount','wrongPayment']:
    result=self.run_fixture({'action':action,field:True});self.assertEqual(result['clicked'],0,result);self.assertTrue(result['error'])

if __name__=='__main__':unittest.main()
