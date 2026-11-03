"""从已观察的智能报销首页打开薪酬入口；不填写金额、不保存或提交。"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from datetime import date
from decimal import Decimal

SCRIPT=r'''(async c=>{
 const visible=e=>!!e?.getClientRects().length;
 if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX'||$.UC.bindData('#0-userinfo.userid#')!==c.expected_userid)throw Error('财务账号或系统不符');
 if(String($.UC.bindData('#61-serialno#')||'').trim()||Number($.active||0)!==0)throw Error('须本账号无单号且就绪的原生页面');
 if(!['lookup-person','clear-length-rejection','confirm-added','close-person'].includes(c.action)&&[...document.querySelectorAll('.ui-dialog')].some(visible))throw Error('存在尚未处理的原生窗口');
 if(c.action==='close-person'){
  if(['unino','premark','amt'].some(k=>document.getElementById('formWF_YBX_282_dd-'+k)?.value))throw Error('保留尚有内容的人员编辑窗口');
  const def=$.page.list['WF_YBX_199']?.wins?.flatMap(w=>w.func_btns||[]).find(b=>b.btnguid==='2A8B1C8CAC4B47DCA3F995879A79F774');
  if(!def||def.caption!=='取消'||def.call_proc!==false||def.acts!=='1==1:198'||String(def.afterclick)!=='function(op){cj_unsel_memwin(282,199);}')throw Error('关闭人员窗口的原生定义变化');
  const buttons=[...document.querySelectorAll('button[btnname="取消"]')].filter(visible);if(buttons.length!==1)throw Error('原生取消按钮不唯一');buttons[0].click();
  return{ok:true,action:'close_empty_remuneration_editor',case_id:c.case_id,saveRequested:false,requiresIndependentReadback:true};
 }
 if(c.action==='confirm-added'){
  const dialogs=[...document.querySelectorAll('.ui-dialog')].filter(visible).filter(e=>e.textContent.includes('成功添加'+c.person_name));
  if(dialogs.length!==1)throw Error('本笔人员添加成功提示不唯一');
  const buttons=[...dialogs[0].querySelectorAll('button')].filter(e=>visible(e)&&e.textContent.trim()==='确认');
  if(buttons.length!==1)throw Error('本笔成功提示确认不唯一');buttons[0].click();
  return{ok:true,action:'confirm_added_person_notice',case_id:c.case_id,student_id:c.student_id,newSaveRequested:false,requiresIndependentReadback:true};
 }
 if(c.action==='clear-length-rejection'){
  const dialogs=[...document.querySelectorAll('.ui-dialog')].filter(visible).filter(e=>e.textContent.includes('工作内容填写最大长度大于设定长度20字'));
  const reason=document.getElementById('formWF_YBX_282_dd-premark'),amt=document.getElementById('formWF_YBX_282_dd-amt');
  if(dialogs.length!==1||document.getElementById('formWF_YBX_282_dd-unino')?.value!==c.student_id||Number(amt?.value)!==Number(c.amount)||reason?.value.length<=20)throw Error('未见本笔明确长度拒绝现场');
  const buttons=[...dialogs[0].querySelectorAll('button')].filter(e=>visible(e)&&e.textContent.trim()==='确认');
  if(buttons.length!==1)throw Error('原生提示确认不唯一');buttons[0].click();
  $.F.setInputData(reason.id,'textarea','','');$.F.setInputData(amt.id,'text','','');$.F.getDataToContext('282');
  return{ok:true,action:'clear_rejected_remuneration_fields',case_id:c.case_id,saveRequested:false};
 }
 if(c.action==='lookup-person'){
  const key='formWF_YBX_282_dd-unino',e=document.getElementById(key);
  if(String($.page.currPage)!=='198'||!visible(e)||e.disabled||e.readOnly||document.getElementById('formWF_YBX_282_dd-amt')?.value)throw Error('须空金额的单笔录入窗口');
  const field=$.F.list['282']?.formFields?.find(f=>f.id==='dd-unino');
  if(!field?.Events?.some(x=>x.event==='blur'&&x.callBack==='cj_get_unino_dtl_v1();'))throw Error('人员查询原生绑定变化');
  if(e.value&&e.value!==c.student_id&&(!c.student_ids.includes(e.value)||document.getElementById('formWF_YBX_282_dd-premark')?.value))throw Error('存在非本案人员或已填事由，不覆盖');
  $.F.setInputData(key,'text',c.student_id,'');$.F.getDataToContext('282');$('#'+key).trigger('blur');
  return{ok:true,action:'remuneration_person_lookup_requested',case_id:c.case_id,document_id:c.document_id,student_id:c.student_id,amounts_entered:false,saveRequested:false,requiresIndependentReadback:true};
 }
 if(c.action==='inspect'){
  if(String($.page.currPage)!=='198')throw Error('当前不是薪酬清单');
  const nature=$('#formWF_YBX_277_dd-abst').data('xmSelect');
  const setter=String($.F.setInputData),at=setter.indexOf('xmselect');
  return{ok:true,action:'inspect_remuneration_entry',case_id:c.case_id,system_write:false,
   category:document.getElementById('formWF_YBX_277_dd-state_ybx')?.value,
   nature_options:nature?.options?.data||[],nature_selected:nature?.getValue()||[],
   nature_context:$.UC.bindData('#277-dd.abst#'),nature_html:document.getElementById('formWF_YBX_277_dd-abst')?.outerHTML.slice(0,16000),
   input_setter_excerpt:at<0?null:setter.slice(Math.max(0,at-180),at+1900),
   nature_before_source:typeof cj_change_abst_before==='function'?String(cj_change_abst_before).slice(0,12000):null,
   period_min:$.UC.bindData('#61-SDQJ_RQS#'),period_max:$.UC.bindData('#61-SDQJ_RQE#'),
   date_validation_source:typeof ybx_cj_ffdates==='function'?String(ybx_cj_ffdates).slice(0,14000):null};
 }
 if(c.action==='activity-setup'){
  if(String($.page.currPage)!=='198'||document.getElementById('formWF_YBX_277_dd-state_ybx')?.value!=='学生人员一般薪酬')throw Error('须先核实学生一般薪酬页面');
  const grid=$('#gridWF_YBX_280');if(!grid[0]?.grid||Number(grid.jqGrid('getGridParam','records'))!==0||grid.jqGrid('getDataIDs').length)throw Error('已有薪酬明细，不重设性质和期间');
  const waitIdle=async()=>{const start=Date.now();while(Number($.active||0)>0&&Date.now()-start<10000)await new Promise(r=>setTimeout(r,100));if(Number($.active||0)>0)throw Error('原生联动尚未完成，只回读原操作');if([...document.querySelectorAll('.ui-dialog')].some(visible))throw Error('原生联动出现提示，先核对');};
  const id='formWF_YBX_277_dd-abst',control=$('#'+id).data('xmSelect'),nature='学生其他劳务';
  if(!visible(document.getElementById(id))||!control||control.options.data.filter(o=>o.name===nature&&o.value===nature).length!==1)throw Error('学生其他劳务不是唯一原生候选');
  const selected=control.getValue().map(o=>o.value);
  if(selected.length&&!(selected.length===1&&selected[0]===nature))throw Error('已有其他酬金性质，不覆盖');
  if(!selected.length){
   $.F.setInputData(id,'xmselect',nature,'');
   // 原生before函数从277上下文读取性质；仅改控件会被它按旧空值清回。
   $.F.getDataToContext('277');
   if($.UC.bindData('#277-dd.abst#')!==nature)throw Error('性质上下文尚未绑定，不触发原生联动');
   $('#'+id).find('xm-select').trigger('change');await waitIdle();
  }
  if($('#'+id).data('xmSelect').getValue().map(o=>o.value).join(',')!==nature||$.F.getInputData(id,'xmselect')!==nature)throw Error('酬金性质未完成原生绑定');
  for(const [field,value]of [['ff_startdate',c.period_start],['ff_enddate',c.period_end]]){
   const min=String($.UC.bindData('#61-SDQJ_RQS#')),max=String($.UC.bindData('#61-SDQJ_RQE#')),key='formWF_YBX_277_dd-'+field,e=document.getElementById(key);
   if(!/^\d{4}-\d{2}-\d{2}$/.test(value)||!/^\d{4}-\d{2}-\d{2}$/.test(min)||!/^\d{4}-\d{2}-\d{2}$/.test(max)||value<min||value>max||!visible(e)||e.disabled)throw Error('材料期间不在原生允许日期范围');
   if(e.value!==value){$.F.setInputData(key,'layui-date',value,'');$.F.getDataToContext('277');$('#'+key).trigger('change');await waitIdle();}
   if(e.value!==value)throw Error('所得期间未绑定');
  }
  $.F.getDataToContext('277');
  if($.UC.bindData('#277-dd.ff_startdate#')!==c.period_start||$.UC.bindData('#277-dd.ff_enddate#')!==c.period_end)throw Error('所得期间原生上下文不一致');
  return{ok:true,action:'prepare_student_activity_remuneration',case_id:c.case_id,document_id:c.document_id,
   nature,period_start:c.period_start,period_end:c.period_end,source_sha256:c.source_sha256,
   amounts_entered:false,claim_created:false,submitRequested:false,requiresIndependentReadback:true};
 }
 if(c.action==='open-person'){
  if(String($.page.currPage)!=='198'||document.getElementById('formWF_YBX_277_dd-state_ybx')?.value!=='学生人员一般薪酬'||$.F.getInputData('formWF_YBX_277_dd-abst','xmselect')!=='学生其他劳务')throw Error('先完成本案学生劳务基本信息');
  if(document.getElementById('formWF_YBX_277_dd-ff_startdate')?.value!==c.period_start||document.getElementById('formWF_YBX_277_dd-ff_enddate')?.value!==c.period_end)throw Error('所得期间与原件不符');
  const buttons=[...document.querySelectorAll('button[btnname="单笔录入"]')].filter(visible);
  if(buttons.length!==1||buttons[0].disabled)throw Error('单笔录入入口不唯一');
  buttons[0].click();return{ok:true,action:'open_remuneration_person_editor_requested',case_id:c.case_id,document_id:c.document_id,amounts_entered:false,saveRequested:false,requiresIndependentReadback:true};
 }
 if(c.action==='student-category'){
  const e=document.getElementById('formWF_YBX_277_dd-state_ybx'),category='学生人员一般薪酬';
  if(String($.page.currPage)!=='198'||!visible(e)||e.tagName!=='SELECT'||e.disabled||[...e.options].filter(o=>o.value===category&&o.text===category).length!==1)throw Error('学生薪酬类别控件或候选变化');
  if(e.value===category)return{ok:true,action:'student_category_already_selected',case_id:c.case_id,system_write:false};
  if(e.value!=='')throw Error('已有其他薪酬类别，不自动覆盖');
  const grids=[...document.querySelectorAll('[id^="gridWF_YBX_"]')].filter(x=>x.grid&&visible(x));
  if(grids.length!==1||Number($(grids[0]).jqGrid('getGridParam','records'))!==0||$(grids[0]).jqGrid('getDataIDs').length)throw Error('已有薪酬明细，不能重新选择类别');
  e.value=category;e.dispatchEvent(new Event('change',{bubbles:true}));
  return{ok:true,action:'student_category_selected',case_id:c.case_id,category,system_write:false,client_state_changed:true,requiresIndependentReadback:true};
 }
 if(c.action&&c.action!=='open')throw Error('未接通的薪酬入口动作');
 if(String($.page.currPage)!=='6')throw Error('须从原生首页打开薪酬入口');
 const e=document.getElementById('resultBX_8_8__title');
 if(!visible(e)||e.textContent.trim()!=='薪酬发放')throw Error('已观察的薪酬入口不符');
 e.click();
 return{ok:true,action:'open_remuneration_entry_requested',case_id:c.case_id,identity:{userid:c.expected_userid},
  client_state_changed:true,system_write:false,saveRequested:false,submitRequested:false,claim_creation_verified:false,requiresIndependentReadback:true};
})'''

def activity_context(case, student_id=None):
 context=case.get('material_context',{});ref=context['labor_material_review']['labor_review'];raw=Path(ref['path']).read_bytes()
 if hashlib.sha256(raw).hexdigest()!=ref['sha256']:raise ValueError('劳务来源核对记录SHA变化')
 review=json.loads(raw);rows=[d for d in review['documents']if d['id']==context.get('remuneration_document_id')]
 if review.get('case_id')!=case['case_id'] or len(rows)!=1:raise ValueError('须明确本案唯一待办劳务事项')
 d=rows[0]
 draft_only=d.get('amount_status')=='materials_matched_draft_only'
 actual_authorized=d.get('amount_status')=='materials_matched_actual_submission_authorized'
 if actual_authorized:
  authority=d.get('submission_authority',{})
  if (authority.get('scope')!='existing_claim_submission' or not authority.get('path') or
      hashlib.sha256(Path(authority['path']).read_bytes()).hexdigest()!=authority.get('sha256') or
      not case.get('serial') or
      not any(x.get('status')=='confirmed' and x.get('scope')=='existing_claim_submission'
              and x.get('document_id')==d['id'] and x.get('source')==authority for x in case.get('decisions',[])) or
      any(x.get('status')=='requires_business_correction' for x in case.get('decisions',[]))):
   raise ValueError('按实际签领继续提交须已有原单、逐项用户授权及SHA，不代替审批或解除未解决的业务问题')
 if draft_only:
  authority=d.get('draft_authority',{})
  if (authority.get('scope')!='draft_only' or not authority.get('path') or
      hashlib.sha256(Path(authority['path']).read_bytes()).hexdigest()!=authority.get('sha256') or
      not any(x.get('status')=='requires_business_correction' and x.get('document_id')==d['id']
              for x in case.get('decisions',[]))):
   raise ValueError('劳务仅存草稿须明确来源及对应提交阻断，不能当作预算已批准')
 if (d['amount_status'] not in ['materials_matched_pending_native_validation','materials_matched_draft_only','materials_matched_actual_submission_authorized'] or d['invoice_count']!=0 or Decimal(d['claimed_amount'])<=0
     or Decimal(d['claimed_amount'])!=Decimal(d['signed_total']) or (not draft_only and not actual_authorized and Decimal(d['claimed_amount'])>Decimal(d['budget_amount']))
     or sum(Decimal(p['amount'])for p in d['recipients'])!=Decimal(d['claimed_amount'])
     or any(not re.fullmatch(r'(?:PB|SA|BA|SB)[0-9]{8}',p['student_id'])for p in d['recipients'])):
  raise ValueError('本项金额、学生身份或预算范围未核清，不能预填')
 for source in d['sources']:
  if hashlib.sha256(Path(source['path']).read_bytes()).hexdigest()!=source['sha256']:raise ValueError('劳务原件SHA变化')
 period=date.fromisoformat(d['business_date']).isoformat()
 result={'document_id':d['id'],'period_start':period,'period_end':period,'source_sha256':ref['sha256'],'claim_amount':d['claimed_amount'],'summary':d['summary'],'approved_people':{p['student_id']:{'name':p['name'],'amount':p['amount']}for p in d['recipients']}}
 if draft_only:result.update(draft_only=True,budget_review_required=True,approved_budget=d['budget_amount'])
 if actual_authorized:result.update(actual_submission_authorized=True,submission_authority=authority,historical_budget_amount=d['budget_amount'],budget_approval_inferred=False)
 if student_id is not None:
  people=[p for p in d['recipients']if p['student_id']==student_id]
  if len(people)!=1:raise ValueError('查询人员必须来自本项已核名单')
  result.update(student_id=student_id,student_ids=[p['student_id']for p in d['recipients']],person_name=people[0]['name'],amount=str(Decimal(people[0]['amount']).quantize(Decimal('.01'))),approved_people={p['student_id']:{'name':p['name'],'amount':p['amount']}for p in d['recipients']},reason=d['summary'].removesuffix('报销'))
 if 'reason'in result and len(result['reason'])>20:raise ValueError('原生工作内容限20字，须先核定本项简短事由')
 return result

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--case',required=True,type=Path);p.add_argument('--tab-id',required=True);p.add_argument('--expected-userid',required=True);p.add_argument('--output',required=True,type=Path);p.add_argument('--action',choices=['open','student-category','inspect','activity-setup','open-person','lookup-person','add-person','clear-length-rejection','confirm-added','close-person','create-claim','confirm-create'],default='open');p.add_argument('--student-id');p.add_argument('--source',type=Path);a=p.parse_args()
 case=json.loads(a.case.read_text());cid=case.get('case_id')
 if not isinstance(cid,str) or not re.fullmatch(r'[a-z][a-z0-9_-]{1,63}',cid) or case.get('serial'):
  raise ValueError('薪酬入口仅用于本案尚未建单阶段')
 if a.action not in {'inspect','confirm-added','close-person','confirm-create'}and(a.case.parent/'pending_write.json').exists():raise ValueError('本案仍有原写入待核，不重新打开业务入口')
 if not re.fullmatch(r'[0-9]+',a.tab_id) or not re.fullmatch(r'[A-Za-z0-9]{2,40}',a.expected_userid):raise ValueError('须明确原生标签及已核账号')
 if a.output.exists():raise ValueError('保留原导航回执，不覆盖')
 a.output.parent.mkdir(parents=True,exist_ok=True);script=a.output.with_suffix('.js')
 context={'case_id':cid,'expected_userid':a.expected_userid,'action':a.action}
 if a.action in {'lookup-person','add-person','clear-length-rejection','confirm-added'}and not a.student_id:raise ValueError('人员查询须明确材料学号')
 if a.action in {'activity-setup','open-person','lookup-person','add-person','clear-length-rejection','confirm-added','create-claim'}:context.update(activity_context(case,a.student_id))
 if a.action=='confirm-added':
  pending=json.loads((a.case.parent/'pending_write.json').read_text());ref=pending['verification'];raw=Path(ref['plan']).read_bytes();original=json.loads(raw)
  if pending['operation']!='remuneration:add-person'or original['student_id']!=a.student_id or original['case_id']!=cid or original['expected_userid']!=a.expected_userid or hashlib.sha256(raw).hexdigest()!=ref['sha256']:raise ValueError('成功提示须承接本案原添加意图')
 body=SCRIPT
 if a.action=='confirm-create':
  pending_path=a.case.parent/'pending_write.json';pending=json.loads(pending_path.read_text());ref=pending['verification'];raw=Path(ref['plan']).read_bytes();context=json.loads(raw)
  if pending['operation']!='remuneration:create-claim' or context['case_id']!=cid or context['expected_userid']!=a.expected_userid or hashlib.sha256(raw).hexdigest()!=ref['sha256'] or pending.get('continuation_output'):raise ValueError('只接续同案原建单确认，保留已派发接续请求')
  from finance_remuneration_save import CREATE_SCRIPT
  context['confirm']=True;body=CREATE_SCRIPT
  pending['continuation_output']=str(a.output);pending_path.write_text(json.dumps(pending,ensure_ascii=False,indent=2)+'\n')
 if a.action in {'add-person','create-claim'}:
  from finance_remuneration_save import bind_add, ADD_SCRIPT, bind_create, CREATE_SCRIPT
  if not a.source:raise ValueError('薪酬添加须引用独立人员核对回执')
  context=(bind_add if a.action=='add-person'else bind_create)(context,a.source);body=ADD_SCRIPT if a.action=='add-person'else CREATE_SCRIPT
 with script.open('x')as f:f.write('('+body+')('+json.dumps(context,ensure_ascii=False)+')')
 script.chmod(0o600)
 if a.action in {'add-person','create-claim'}:
  sys.path.insert(0,str(Path(__file__).resolve().parents[1]));from reimburse import begin_write
  plan=a.output.with_name('remuneration_input.json');plan.write_text(json.dumps(context,ensure_ascii=False,indent=2)+'\n');plan.chmod(0o600)
  begin_write({'state':a.case.parent/'state.json'},case,'remuneration:'+a.action,a.output.parent,{'plan':str(plan),'sha256':hashlib.sha256(plan.read_bytes()).hexdigest()})
 return subprocess.run([sys.executable,str(Path(__file__).parent/'edge_runtime.py'),str(script),'--tab-id',a.tab_id,'--output',str(a.output),'--quiet','--operation-kind','write'if a.action in {'add-person','create-claim','confirm-create'}else'browser-local']).returncode

if __name__=='__main__':raise SystemExit(main())
