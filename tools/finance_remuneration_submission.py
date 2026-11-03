"""薪酬单提交前完整核对；复用普通承诺和审批流程，不套用发票张数。"""
import hashlib
import json
from pathlib import Path
from decimal import Decimal
from finance_remuneration_save import validate_bound_case,verify_created


def preparation(form,plan):
 validate_bound_case(form)
 if form.get('payments')or form.get('pre_review'):raise ValueError('本分支仅正常薪酬转卡，不叠加普通转卡或额外预审')
 if len(form.get('funding',[]))!=1 or Decimal(form['funding'][0]['amount'])!=Decimal(form['amount']):raise ValueError('薪酬经费总额不闭合')
 ref=plan.get('remuneration_zero_payment_receipt',{});path=Path(ref.get('path',''));raw=path.read_bytes()
 if hashlib.sha256(raw).hexdigest()!=ref.get('sha256')or form['case_id']not in path.parts:raise ValueError('须同案零额额外支付检查回执及SHA')
 result=json.loads(raw).get('result',{})
 if (result.get('ok')is not True or result.get('action')!='close_payments_without_save'or result.get('serial')!=form['serial']or result.get('fields_modified')is not False or result.get('saveRequested')is not False or result.get('returnedToPreview')is not True):raise ValueError('额外支付不是已核空白状态')
 creation=json.loads(Path(form['remuneration']['creation_plan']['plan']).read_text())
 return {'form':form,'plan':plan,'rows':creation['before'],'period_start':creation['period_start'],'period_end':creation['period_end']}

SCRIPT=r'''(async q=>{
 const c=q.form,p=q.plan,visible=e=>!!e?.getClientRects().length;
 const cents=x=>{const s=String(x).replace(/,/g,'');if(!/^\d+(\.\d{1,2})?$/.test(s))throw Error('金额格式不符');return Math.round(Number(s)*100);};
 const observe=()=>{
  if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX'||$.UC.bindData('#0-userinfo.userid#')!==c.expected_userid||$.UC.bindData('#61-serialno#')!==c.serial||Number($.active||0))throw Error('薪酬提交身份或就绪状态不符');
  const f=document.getElementById('iframepage_print'),d=f?.contentDocument,text=d?.body?.innerText||'';
  if(!visible(f)||!text.includes('中国科学技术大学薪酬业务单')||!text.includes('报销单号：'+c.serial)||!text.includes('财务审批意见')||!text.includes('会计审核')||/\*错误\*|未推荐出经费/.test(text))throw Error('完整薪酬预览未就绪或仍有错误');
  const tables=[...d.querySelectorAll('table')].map(t=>[...t.rows].map(r=>[...r.cells].map(x=>x.innerText.trim()))),header=['序号','工号','姓名','人员类型','支付方式','卡号','户名','发放事由','金额'];
  const wages=tables.filter(t=>t.some(r=>JSON.stringify(r)===JSON.stringify(header)));if(wages.length!==1)throw Error('发放明细表不唯一');
  const rows=wages[0].filter(r=>r.length===9&&/^\d+$/.test(r[0])).map(r=>({student_id:r[1],name:r[2],amount:(cents(r[8])/100).toFixed(2),reason:r[7]})).sort((a,b)=>a.student_id.localeCompare(b.student_id));
  if(JSON.stringify(rows)!==JSON.stringify(q.rows))throw Error('薪酬人员、金额或事由变化');
  const total=[...text.matchAll(/金额总计[：:]\s*([\d,.]+)/g)];if(total.length!==1||cents(total[0][1])!==cents(c.amount))throw Error('薪酬总额不符');
  const basic=tables.flat(),summary=basic.filter(r=>r.length===2&&r[0]==='摘要'),notes=basic.filter(r=>r.length===2&&r[0]==='备注');
  const normalize=s=>String(s).replace(/，/g,',').replace(/\s/g,'');
  if(summary.length!==1||summary[0][1]!==p.summary||notes.length!==1||normalize(notes[0][1])!==normalize(c.basic.fields['t-cl_abst'])||!text.includes(p.phone)||!text.includes(c.basic.teacher_id+' '+c.basic.teacher_name))throw Error('摘要、联系人或证明人不符');
  if(!text.match(new RegExp('所得开始时间\\s*'+q.period_start+'\\s*所得结束时间\\s*'+q.period_end))||!text.match(/酬金性质\s+学生其他劳务/))throw Error('性质或期间不符');
  const fund=c.funding[0];if(!text.includes(fund.code+'/'+fund.name+'/')||!basic.some(r=>r.length===5&&r[0]==='学生-学生其他劳务'&&r[2].startsWith(fund.code+'/')&&cents(r[4])===cents(c.amount)))throw Error('薪酬预算或经费不符');
  const salary=[...d.querySelectorAll('input[name="form_cj_paytype"]')];if(salary.length!==2||salary.filter(e=>e.checked).length!==1||!salary.some(e=>e.value==='N'&&e.checked))throw Error('须已核正常薪酬转卡');
  if([...text.matchAll(/共填写\s*(\d+)\s*条支付信息[,，]\s*小计\s*([\d,.]+)元/g)].some(m=>Number(m[1])!==0||cents(m[2])!==0))throw Error('存在另行填写支付，不重复报酬');
  if(d.querySelectorAll('input[name^="form_proof_choose"]').length)throw Error('本薪酬预览出现未适配辅助证明选择');
  const prints=[...d.querySelectorAll('input[name="form_grantcwprint"]')];if(prints.length!==2||prints.filter(e=>e.checked).length!==1||!prints.some(e=>e.value==='N'))throw Error('委托打印选项不完整');
  return{f,d,text,prints};
 };
 const o=observe();
 if(q.action==='options'){
  if(p.delegate_finance_print!==false||p.contract_exists!==false)throw Error('本项只支持自行交材料');
  const e=o.prints.find(e=>e.value==='N');if(!visible(e)||e.disabled)throw Error('打印选项不可操作');if(!e.checked)e.click();
  return{ok:true,action:'select_remuneration_options',serial:c.serial,delegate_finance_print:false,requiresIndependentReadback:true};
 }
 if(q.action==='next'){
  if(!o.prints.some(e=>e.value==='N'&&e.checked))throw Error('须先回读不委托打印');
  const buttons=[...document.querySelectorAll('.foot_winWF_YBX_41 button[btnname="下一步"]')].filter(visible);if(buttons.length!==1||buttons[0].disabled)throw Error('薪酬下一步入口不唯一');
  buttons[0].click();return{ok:true,action:'submission_next',serial:c.serial,requiresReadback:true};
 }
 throw Error('未适配薪酬提交动作');
})'''

def script(form,plan,action):
 if action not in ['options','next']:raise ValueError('未适配薪酬提交动作')
 return '('+SCRIPT+')('+json.dumps({**preparation(form,plan),'action':action},ensure_ascii=False)+')'

def verify_options(form,result):
 validate_bound_case(form);creation=json.loads(Path(form['remuneration']['creation_plan']['plan']).read_text());verify_created(creation,{'result':result},form['serial'])
 f=next(f for f in result['frames']if '报销单号：'+form['serial']in f['visibleText']);fields=f['fields']
 for name in ['form_grantcwprint','form_cj_paytype']:
  choices=[e for e in fields if e.get('name')==name]
  if len(choices)!=2 or [e['value']for e in choices if e.get('checked')]!=['N']:raise ValueError('薪酬打印或正常转卡选项未保存')
 if any(e.get('name','').startswith('form_proof_choose')for e in fields):raise ValueError('薪酬辅助证明控件尚未适配')
 return {'ok':True,'native_business_type':'remuneration','delegate_finance_print':False,'payment_mode':'normal_payroll'}
