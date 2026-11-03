"""经SHA绑定原件及原生窗口核对的单笔劳务添加；独立回读前保留pending。"""
import hashlib
import json
from decimal import Decimal
from pathlib import Path

FIELDS={'student_id':'t.unino','name':'t.name','amount':'t.AMT','reason':'t.premark'}
GUID='289FA79F8B1D4C6F8E427D84000E92D9'
DEFINITION_KEYS=['btnguid','caption','pre_compute','pre_check','call_proc','check_func','procs','post_check','acts','affects','beforeclick','afterclick']

def normalized_rows(body):
 grids=[g for g in body.get('grids',[])if str(g['funcno'])=='280']
 if len(grids)!=1:raise ValueError('须有唯一薪酬清单')
 g=grids[0]
 if g.get('allRecordsVisible')is not True or g.get('loading')or g['records']!=len(g['rows']):raise ValueError('薪酬清单未完整加载')
 rows=[]
 for r in g['rows']:
  row={k:str(r.get(f,r.get(f.replace('.','-'),''))).strip()for k,f in FIELDS.items()}
  row['amount']=str(Decimal(row['amount'].replace(',','')).quantize(Decimal('.01')))
  rows.append(row)
 return sorted(rows,key=lambda r:r['student_id'])

def signature(definition):
 return {k:definition[k].get('source')if isinstance(definition[k],dict)and definition[k].get('nativeType')=='function' else definition[k]for k in DEFINITION_KEYS}

def bind_add(context,source):
 raw=Path(source).read_bytes();read=json.loads(raw);body=read.get('result',{})
 if body.get('ok')is not True or body.get('projid')!='WF_YBX' or body.get('serial') or body.get('identity',{}).get('userid')!=context['expected_userid']:raise ValueError('人员核对来源账号或页面不符')
 forms={str(f['funcno']):f for f in body.get('forms',[])}
 fields={f['id']:(f.get('control')or{})for f in forms['282']['fields']}
 expected={'dd-unino':context['student_id'],'dd-sname':context['person_name'],'dd-state':'学生'}
 if any(str(fields[k].get('value','')).strip()!=v for k,v in expected.items()):raise ValueError('学号、原生姓名或学生性质与原件不符')
 if fields['dd-amt'].get('value')or fields['dd-premark'].get('value'):raise ValueError('保留已有金额或事由，不覆盖')
 card=str(fields['dd-cardno'].get('value',''));bank=fields['dd-bankno']
 if not card or not card[-4:].isdigit()or not bank.get('value')or len([o for o in bank['options']if o['selected']and o['value']])!=1:raise ValueError('银行卡未由原生人员查询确定')
 before=normalized_rows(body)
 if any(r['student_id']==context['student_id']for r in before):raise ValueError('该学号已在清单，不重复添加')
 for row in before:
  if row['student_id']not in context['approved_people']or row['name']!=context['approved_people'][row['student_id']]['name']or Decimal(row['amount'])!=Decimal(context['approved_people'][row['student_id']]['amount']):raise ValueError('已有清单含本项之外的人员或金额')
 definitions=[x['definition']for x in body['loadedButtons']['definitions']if x['guid']==GUID and x.get('definition_capture_complete')]
 if len(definitions)!=1 or definitions[0]['caption']!='添加':raise ValueError('原生添加定义未唯一核实')
 after=sorted(before+[{'student_id':context['student_id'],'name':context['person_name'],'amount':context['amount'],'reason':context['reason']}],key=lambda r:r['student_id'])
 return {**context,'before':before,'after':after,'card_tail':card[-4:],'definition':signature(definitions[0]),'source':{'path':str(Path(source).resolve()),'sha256':hashlib.sha256(raw).hexdigest()}}

ADD_SCRIPT=r'''(async c=>{
 const visible=e=>!!e?.getClientRects().length;
 if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX'||$.UC.bindData('#0-userinfo.userid#')!==c.expected_userid||$.UC.bindData('#61-serialno#')||Number($.active||0))throw Error('薪酬添加身份或就绪状态不符');
 const field=k=>document.getElementById('formWF_YBX_282_dd-'+k),value=k=>String(field(k)?.value||'').trim();
 if(!visible(field('unino'))||value('unino')!==c.student_id||value('sname')!==c.person_name||value('state')!=='学生'||value('amt')||value('premark')||!value('bankno')||!value('cardno').endsWith(c.card_tail))throw Error('人员窗口发生变化，不添加');
 if(document.getElementById('formWF_YBX_277_dd-state_ybx')?.value!=='学生人员一般薪酬'||$.F.getInputData('formWF_YBX_277_dd-abst','xmselect')!=='学生其他劳务'||document.getElementById('formWF_YBX_277_dd-ff_startdate')?.value!==c.period_start||document.getElementById('formWF_YBX_277_dd-ff_enddate')?.value!==c.period_end||document.getElementById('formWF_YBX_277_dd-hastax')?.value!=='T')throw Error('性质、期间或税前方式变化');
 const grid=$('#gridWF_YBX_280'),ids=grid.jqGrid('getDataIDs');if(grid[0].grid.hDiv.loading||Number(grid.jqGrid('getGridParam','records'))!==ids.length)throw Error('薪酬清单未完整加载');
 const before=ids.map(id=>{const r=grid.jqGrid('getRowData',id);return{student_id:String(r['t.unino']).trim(),name:String(r['t.name']).trim(),amount:Number(String(r['t.AMT']).replace(/,/g,'')).toFixed(2),reason:String(r['t.premark']).trim()};}).sort((a,b)=>a.student_id.localeCompare(b.student_id));
 if(JSON.stringify(before)!==JSON.stringify(c.before))throw Error('人员清单已变化');
 const defs=$.page.list['WF_YBX_199']?.wins?.flatMap(w=>w.func_btns||[]).filter(b=>b.btnguid==='289FA79F8B1D4C6F8E427D84000E92D9')||[];
 if(defs.length!==1||Object.entries(c.definition).some(([k,v])=>JSON.stringify(typeof defs[0][k]==='function'?String(defs[0][k]):defs[0][k])!==JSON.stringify(v)))throw Error('原生添加定义变化');
 const buttons=[...document.querySelectorAll('button[btnname="添加"]')].filter(visible);if(buttons.length!==1||buttons[0].disabled)throw Error('添加按钮不唯一');
 for(const [key,v]of [['premark',c.reason],['amt',c.amount]]){const e=field(key);if(!visible(e)||e.readOnly||e.disabled)throw Error('金额或事由不可编辑');$.F.setInputData(e.id,key==='premark'?'textarea':'text',v,'');}
 $.F.getDataToContext('282');if($.UC.bindData('#282-dd.amt#')!=c.amount||$.UC.bindData('#282-dd.premark#')!==c.reason)throw Error('金额或事由未绑定');
 buttons[0].click();return{ok:true,action:'remuneration_add_person_requested',case_id:c.case_id,document_id:c.document_id,student_id:c.student_id,amount:c.amount,saveRequested:true,requiresIndependentReadback:true};
})'''

def verify_saved(previous,read,outcome):
 ref=previous['verification'];raw=Path(ref['plan']).read_bytes()
 if hashlib.sha256(raw).hexdigest()!=ref['sha256']:raise ValueError('原薪酬添加计划SHA变化')
 plan=json.loads(raw);body=read['result']
 if plan['case_id']!=previous['case_id']or body.get('identity',{}).get('userid')!=plan['expected_userid']or body.get('serial')or body.get('projid')!='WF_YBX':raise ValueError('薪酬清单身份不符')
 if outcome=='not_applied':
  fields={f['id']:(f.get('control')or{}).get('value')for form in body.get('forms',[])if str(form['funcno'])=='282' for f in form['fields']}
  if ('工作内容填写最大长度大于设定长度20字' not in body.get('visibleText','') or len(plan['reason'])<=20
      or normalized_rows(body)!=plan['before'] or fields.get('dd-unino')!=plan['student_id'] or fields.get('dd-premark')!=plan['reason'] or Decimal(fields.get('dd-amt')or'0')!=Decimal(plan['amount'])):
   raise ValueError('未见同笔明确长度拒绝及原清单，保留pending')
  return {'rejected':True,'reason':'native_work_reason_length_20','rows':plan['before']}
 if outcome!='saved':raise ValueError('薪酬回读结果不支持')
 if normalized_rows(body)!=plan['after']:raise ValueError('薪酬人员、金额、事由或条数与添加计划不符')
 return {'document_id':plan['document_id'],'rows':plan['after'],'saved_people':len(plan['after'])}

CREATE_GUID='394EF3609EDB471E812179D639C5DD95'
def bind_create(context,source):
 raw=Path(source).read_bytes();body=json.loads(raw).get('result',{})
 if body.get('ok')is not True or body.get('projid')!='WF_YBX' or body.get('serial') or body.get('identity',{}).get('userid')!=context['expected_userid']:raise ValueError('建单来源身份不符')
 rows=normalized_rows(body)
 if len(rows)!=len(context['approved_people'])or set(r['student_id']for r in rows)!=set(context['approved_people']):raise ValueError('本项人员不完整或重复')
 for row in rows:
  person=context['approved_people'][row['student_id']]
  if row['name']!=person['name']or Decimal(row['amount'])!=Decimal(person['amount']):raise ValueError('清单人员金额与原件不符')
 if sum(Decimal(r['amount'])for r in rows)!=Decimal(context['claim_amount']):raise ValueError('本项总额不符')
 grid=next(g for g in body['grids']if str(g['funcno'])=='280')
 if any(str(r.get('t.tips','')).strip()for r in grid['rows']):raise ValueError('原生清单仍有错误提示')
 defs=[x['definition']for x in body['loadedButtons']['definitions']if x['guid']==CREATE_GUID and x.get('definition_capture_complete')]
 if len(defs)!=1 or defs[0]['caption']!='前往报销':raise ValueError('原生建单入口未唯一核实')
 return{**context,'before':rows,'definition':signature(defs[0]),'source':{'path':str(Path(source).resolve()),'sha256':hashlib.sha256(raw).hexdigest()}}

CREATE_SCRIPT=r'''(async c=>{
 const visible=e=>!!e?.getClientRects().length;
 if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX'||$.UC.bindData('#0-userinfo.userid#')!==c.expected_userid||$.UC.bindData('#61-serialno#')||Number($.active||0))throw Error('薪酬建单身份或就绪状态不符');
 if(document.getElementById('formWF_YBX_277_dd-state_ybx')?.value!=='学生人员一般薪酬'||$.F.getInputData('formWF_YBX_277_dd-abst','xmselect')!=='学生其他劳务'||document.getElementById('formWF_YBX_277_dd-ff_startdate')?.value!==c.period_start||document.getElementById('formWF_YBX_277_dd-ff_enddate')?.value!==c.period_end||document.getElementById('formWF_YBX_277_dd-hastax')?.value!=='T')throw Error('本项性质、期间或税前方式变化');
 const grid=$('#gridWF_YBX_280'),ids=grid.jqGrid('getDataIDs');if(grid[0].grid.hDiv.loading||Number(grid.jqGrid('getGridParam','records'))!==ids.length)throw Error('清单未完整加载');
 const rows=ids.map(id=>{const r=grid.jqGrid('getRowData',id);if(String(r['t.tips']||'').trim())throw Error('清单有错误');return{student_id:String(r['t.unino']).trim(),name:String(r['t.name']).trim(),amount:Number(String(r['t.AMT']).replace(/,/g,'')).toFixed(2),reason:String(r['t.premark']).trim()};}).sort((a,b)=>a.student_id.localeCompare(b.student_id));
 if(JSON.stringify(rows)!==JSON.stringify(c.before))throw Error('清单已变化');
 const defs=$.page.list['WF_YBX_198']?.wins?.flatMap(w=>w.func_btns||[]).filter(b=>b.btnguid==='394EF3609EDB471E812179D639C5DD95')||[];
 if(defs.length!==1||Object.entries(c.definition).some(([k,v])=>JSON.stringify(typeof defs[0][k]==='function'?String(defs[0][k]):defs[0][k])!==JSON.stringify(v)))throw Error('原生建单定义变化');
 if(c.confirm){
  const dialogs=[...document.querySelectorAll('.ui-dialog')].filter(visible).filter(e=>{const m=e.textContent.match(/正确发放记录共\s*(\d+)\s*条，合计\s*([\d.]+)\s*元/);return m&&Number(m[1])===c.before.length&&Number(m[2])===Number(c.claim_amount);});
  if(dialogs.length!==1)throw Error('本次人员金额确认提示不唯一');
  const buttons=[...dialogs[0].querySelectorAll('button')].filter(e=>visible(e)&&e.textContent.replace(/\s/g,'')==='继续');if(buttons.length!==1)throw Error('继续按钮不唯一');
  buttons[0].click();return{ok:true,action:'remuneration_claim_continue_requested',case_id:c.case_id,amount:c.claim_amount,requiresIndependentReadback:true};
 }
 if([...document.querySelectorAll('.ui-dialog')].some(visible))throw Error('已有原生提示，保留原请求');
 const buttons=[...document.querySelectorAll('button[btnname="前往报销"]')].filter(visible);if(buttons.length!==1||buttons[0].disabled)throw Error('建单按钮不唯一');
 buttons[0].click();return{ok:true,action:'remuneration_claim_creation_requested',case_id:c.case_id,document_id:c.document_id,amount:c.claim_amount,submitRequested:false,requiresIndependentReadback:true};
})'''

def verify_created(plan,read,serial):
 body=read.get('result',{})
 if body.get('ok')is not True or body.get('projid')!='WF_YBX'or body.get('serial')!=serial or body.get('identity',{}).get('userid')!=plan['expected_userid']:raise ValueError('薪酬新单身份不符')
 frames=[f for f in body.get('frames',[])if '中国科学技术大学薪酬业务单'in f.get('visibleText','')and '报销单号：'+serial in f.get('visibleText','')]
 if len(frames)!=1:raise ValueError('须有唯一同号薪酬业务单预览')
 f=frames[0];text=f['visibleText']
 import re
 if not re.search(r'酬金性质\s+学生其他劳务(?:\s|$)',text)or not re.search(r'所得开始时间\s*'+re.escape(plan['period_start'])+r'\s*所得结束时间\s*'+re.escape(plan['period_end']),text):raise ValueError('薪酬性质或所得期间不符')
 tables=[t for t in f['tables']if any(row==['序号','工号','姓名','人员类型','支付方式','卡号','户名','发放事由','金额']for row in t)]
 if len(tables)!=1:raise ValueError('发放明细表不唯一')
 rows=[{'student_id':r[1],'name':r[2],'amount':str(Decimal(r[8].replace(',','')).quantize(Decimal('.01'))),'reason':r[7]}for r in tables[0]if len(r)==9 and str(r[0]).isdigit()]
 if sorted(rows,key=lambda r:r['student_id'])!=plan['before']:raise ValueError('新单发放人员或金额与原创建清单不符')
 import re
 total=re.search(r'金额总计：\s*([\d,.]+)',text)
 if not total or Decimal(total[1].replace(',',''))!=Decimal(plan['claim_amount']):raise ValueError('新单总额不符')
 return rows

def bind_created(paths,receipt,serial):
 from datetime import datetime
 from operator_support import stamp,write_json
 lock=paths['state'].parent/'pending_write.json';pending=json.loads(lock.read_text());ref=pending['verification'];raw=Path(ref['plan']).read_bytes();plan=json.loads(raw);read=json.loads(Path(receipt).read_text())
 if pending['operation']!='remuneration:create-claim'or plan['case_id']!=pending['case_id']or hashlib.sha256(raw).hexdigest()!=ref['sha256']or not pending.get('continuation_output'):raise ValueError('须有本案原薪酬建单及接续意图')
 if datetime.fromisoformat(read['executed_at_utc'])<=datetime.fromisoformat(pending['started_at_utc']):raise ValueError('新单回读必须晚于原建单')
 verify_created(plan,read,serial)
 for key in ['case','form','invoices','state']:
  value=json.loads(paths[key].read_text())
  if value['case_id']!=plan['case_id']or value.get('serial')not in [None,serial]:raise ValueError('本地绑定身份变化')
 for key in ['case','form','invoices','state']:
  value=json.loads(paths[key].read_text());value['serial']=serial
  if key=='form':value.update(system_mode='remuneration',expected_userid=plan['expected_userid'],amount=plan['claim_amount'],expected_invoice_count=0,remuneration={'document_id':plan['document_id'],'creation_plan':ref,'creation_readback':{'path':str(Path(receipt).resolve()),'sha256':hashlib.sha256(Path(receipt).read_bytes()).hexdigest()}})
  if key=='invoices':value.update(expected_count=0,expected_amount='0.00',selected_invoices=[])
  if key=='state':value.update(status='remuneration_draft_created_readback',creation_readback=str(receipt),updated_at_utc=stamp())
  write_json(paths[key],value)
 pending.update(status='draft_bound_readback_recorded',outcome='saved',bound_serial=serial,readback_receipt=str(receipt));write_json(Path(pending['output_directory'])/'operator_readback_ack.json',pending);lock.unlink()
 return {'ok':True,'serial':serial,'system_mode':'remuneration','amount':plan['claim_amount'],'system_write':False,'submitted':False}

def validate_bound_case(form):
 if form.get('remuneration',{}).get('native_schema')=='ustc-native-student-remuneration-v1':
  from finance_native_remuneration import verify_bound_case
  verify_bound_case(form);return
 data=form['remuneration'];p=data['creation_plan'];r=data['creation_readback']
 raw=Path(p['plan']).read_bytes();read=Path(r['path']).read_bytes()
 if hashlib.sha256(raw).hexdigest()!=p['sha256']or hashlib.sha256(read).hexdigest()!=r['sha256']:raise ValueError('薪酬绑定来源SHA变化')
 plan=json.loads(raw)
 if plan['case_id']!=form['case_id']or plan['expected_userid']!=form['expected_userid']or Decimal(plan['claim_amount'])!=Decimal(form['amount']):raise ValueError('薪酬绑定身份、金额变化')
 verify_created(plan,json.loads(read),form['serial'])

def audit_material_sources(case,form):
 """已核薪酬材料独立于普通发票审计；不把照片当纸质交付回执。"""
 from finance_remuneration_entry import activity_context
 context=activity_context(case);validate_bound_case(form)
 if context['document_id']!=form['remuneration']['document_id']or Decimal(context['claim_amount'])!=Decimal(form['amount']):raise ValueError('薪酬材料事项与已绑定单据不符')
 custody=case.get('material_context',{}).get('labor_paper_custody')
 return {'case_id':case['case_id'],'serial':form['serial'],'business_type':'student_other_remuneration','source_review_sha256':context['source_sha256'],'ready_for_material_review':True,'ready_for_material_delivery':False,'invoice_requirement':'not_applicable_to_bound_native_remuneration','paper_custody':custody or 'awaiting_confirmation','missing_or_pending':[]if custody else[{'role':'paper_originals','issue':'custody_unconfirmed','instruction':'本批照片所示签字表、审批表等纸质原件持有人待核，不继承其他案件的保管记录'}],'system_write':False,'note':'原件来源已按SHA核对；纸质投递及正式系统单交付分别登记，不把照片打印成原件。'}
