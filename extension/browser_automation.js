/* USTC WF_YBX 原生控件驱动。参数来自案件配置，宿主可为 Edge 或扩展 MAIN world。 */
(function(root){
 'use strict';
 const $=root.$,delay=ms=>new Promise(r=>setTimeout(r,ms));
 const visible=e=>!!e&&e.getClientRects().length>0;
 const one=(es,message)=>{if(es.length!==1)throw new Error(message);return es[0];};
 const cents=s=>{s=String(s).replaceAll(',','').trim();if(!/^\d+(?:\.\d{1,2})?$/.test(s))throw new Error('金额格式错误');const[a,b='']=s.split('.');return Number(a)*100+Number(b.padEnd(2,'0'));};
 function guard(c,write=true){if(c.system_mode&&c.system_mode!=='daily')throw new Error('系统模式尚未实测接通：'+c.system_mode);if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX'||!/^\d+$/.test(c.serial)||$.UC.bindData('#61-serialno#')!==c.serial)throw new Error('业务系统或单据身份不符');if(write&&visible(document.querySelector('.foot_winWF_YBX_76 button[btnname="同意"]')))throw new Error('当前为申请人审核页，不能继续按草稿填写');}
 async function wait(test,message){for(let i=0;i<80;i++){if(test())return;await delay(100);}throw new Error(message);}
 function click(scope,caption){one([...scope.querySelectorAll('button')].filter(e=>visible(e)&&(e.getAttribute('btnname')===caption||e.innerText.trim()===caption)),'原生按钮不唯一：'+caption).click();}
 function dialog(fieldId){return one([...document.querySelectorAll('.ui-dialog')].filter(e=>visible(e)&&e.querySelector('#'+fieldId)),'原生窗口不唯一：'+fieldId);}
 function set(id,value){const e=document.getElementById(id);if(!visible(e))throw new Error('字段未加载：'+id);if(e.readOnly||e.disabled)throw new Error('字段不可编辑：'+id);if(e.options&&!Array.from(e.options).some(o=>o.value===value))throw new Error('字段选项变化：'+id);$(e).val(value).trigger('change').trigger('blur');return e;}
 async function navigate(c,section){guard(c);const routes={basic:'XG基本',funding:'选择项目',expenses:'项目明细',payments:'XG支付',explanation:'33&33'};if(!routes[section])throw new Error('未知填报环节');const f=document.getElementById('iframepage_print');await wait(()=>f?.contentWindow.pageOpen&&f.contentDocument.body.innerText.includes('报销单号：'+c.serial),'报销单预览未就绪');if(section==='explanation'){if(!f.contentDocument.querySelector('[onclick="pageOpen_v1(\'特殊事项\',\'33&33\')"]'))throw new Error('总体说明入口变化');f.contentWindow.pageOpen_v1('特殊事项',routes[section]);}else f.contentWindow.pageOpen(routes[section]);const targets={basic:'formWF_YBX_60_t-abst',funding:'gridWF_YBX_242',expenses:'gridWF_YBX_307',payments:'formWF_YBX_19_t-bx_amt',explanation:'formWF_YBX_76_t-spec_response'};await wait(()=>visible(document.getElementById(targets[section])),'目标填报页面未就绪');return{ok:true,action:'navigate',serial:c.serial,section};}
 async function fillBasic(c,save){guard(c);const b=c.basic;for(const[k,v]of Object.entries(b.fields))set('formWF_YBX_60_'+k,v);const id='formWF_YBX_60_t-lc_accepter';set(id,b.teacher_id);await wait(()=>$('#formWF_YBX_60_t-lc_accepter_name').val()===b.teacher_name,'证明人工号未解析成确认的教师姓名');$.F.getDataToContext('60');if(save)click(dialog(id),'保存');return{ok:true,action:'fill_basic',serial:c.serial,teacher:b.teacher_id,name:b.teacher_name,saveRequested:!!save,requiresReadback:!!save};}
 function fillExpenses(c,save){
  if(c.funding_policy?.invoice_level_required)throw new Error('本案经费有限定票据用途/个人额度，必须逐票拆分，不能只修改费用分摊总额');
  guard(c);const grid=$('#gridWF_YBX_307');if(!visible(grid[0]))throw new Error('费用分摊未打开');
  const rows=grid.jqGrid('getDataIDs').map(id=>({id,r:grid.jqGrid('getRowData',id)}));if(rows.some(x=>x.r['t.serial_no']!==c.serial))throw new Error('分摊表混入其他单据');
  const leaves=rows.filter(x=>x.r.TLEVEL==='2'),plans=c.expense_allocations;
  if(plans.length!==leaves.length)throw new Error('必须显式配置所有预算叶节点，包括0元节点');
  const changes=plans.map(p=>{const x=one(leaves.filter(x=>x.r['t.uni_prj_code']===p.project&&x.r['t.jj_subj']===p.subject&&x.r['t.bu_code']===p.budget),'预算节点不唯一');const input=one([...document.getElementById(x.id).querySelectorAll('input[cname="t.amt"]')],'金额控件不唯一');if(input.readOnly)throw new Error('叶节点金额不可编辑');if(cents(p.amount)>cents(x.r['t.bu_code_balance']))throw new Error('预算余额不足');return{...p,input,current:cents(input.value),next:cents(p.amount)};});
  if(changes.reduce((n,x)=>n+x.next,0)!==cents(c.amount))throw new Error('分摊总额不符');
  for(const parent of rows.filter(x=>x.r.TLEVEL==='0')){const sum=changes.filter(x=>x.subject===parent.r['t.jj_subj']).reduce((n,x)=>n+x.next,0);const input=document.getElementById(parent.id).querySelector('input[cname="t.amt"]');if(sum!==cents(input.value))throw new Error('经济科目分摊总额不符：'+parent.r['t.jj_subjname']);}
  for(const fund of c.funding){if(changes.filter(x=>x.project===fund.code).reduce((n,x)=>n+x.next,0)!==cents(fund.amount))throw new Error('项目分摊总额不符：'+fund.code);}
  changes.sort((a,b)=>(a.next-a.current)-(b.next-b.current)).forEach(x=>$(x.input).val(x.amount).trigger('change').trigger('blur'));
  if(save)click(one([...document.querySelectorAll('.foot_winWF_YBX_216')].filter(visible),'费用分摊保存窗口不唯一'),'保存');
  return{ok:true,action:'fill_expenses',serial:c.serial,allocations:changes.map(({input,current,next,...x})=>x),saveRequested:!!save,requiresReadback:!!save};
 }
 async function fillTransfers(c,range){
  guard(c);const plans=c.payments.filter(p=>p.mode==='bank_transfer');if(!visible(document.getElementById('formWF_YBX_19_t-bx_amt'))||cents($('#formWF_YBX_19_t-bx_amt').val())!==cents(c.amount))throw new Error('结算页面或金额不符');
  if($.F.list['22'].multiCount>plans.length)throw new Error('现有转卡行数超过配置，须检查');
  while($.F.list['22'].multiCount<plans.length){const count=$.F.list['22'].multiCount;const plus=[...document.querySelectorAll('.addoneformWF_YBX_22')].filter(visible);if(!plus.length)throw new Error('转卡添加入口缺失');plus[0].click();await wait(()=>$.F.list['22'].multiCount===count+1,'原生新增转卡行未完成');}
  const start=range.start||0,limit=range.limit||5;if(start<0||limit<1||limit>5)throw new Error('每次只处理1至5个已配置收款人');const results=[];
  for(let i=start;i<Math.min(start+limit,plans.length);i++){
   const p=plans[i],prefix='formWF_YBX_22_t-',input=document.getElementById(prefix+'sno_'+i),name=document.getElementById(prefix+'sname_'+i),bank=document.getElementById(prefix+'cnt1_'+i),amount=document.getElementById(prefix+'amount_'+i);
   if(cents(amount.value||'0')>0&&(input.value!==p.person_id||name.value!==p.name||cents(amount.value)!==cents(p.amount)))throw new Error('现有非零支付与配置冲突，停止：'+i);
   if(input.value!==p.person_id||name.value!==p.name||!bank.value){
    input.focus();input.value=p.person_id;$(input).trigger('focus').trigger('keyup').trigger($.Event('keydown',{keyCode:40,which:40}));
    const match=e=>{const text=e.innerText.replace(/^\d+\/\d+:\s*/,'');return visible(e)&&text.startsWith(p.name+'>'+p.person_id+'>');};
    await wait(()=>[...document.querySelectorAll('.ac_results li')].some(match),'工号无匹配候选：'+p.person_id);
    const li=one([...document.querySelectorAll('.ac_results li')].filter(match),'姓名工号匹配候选不唯一：'+p.person_id);
    $(li).trigger('mouseover').trigger('mousedown');
    // 后台标签页 HTMLElement.blur() 不保证触发 onblur；jQuery 原生事件同时调用页面 inline 解析。
    $(input).trigger('blur');
    if(name.value!==p.name){
     if(input.getAttribute('onblur')!=='_3splitCardUnit(this,22);'||typeof root._3splitCardUnit!=='function')throw new Error('系统人员解析回调变化');
     root._3splitCardUnit(input,22);
    }
    $(li).trigger('mouseup').trigger('click');
    await wait(()=>input.value===p.person_id&&name.value===p.name&&!!bank.value,'人员或银行卡未完成原生联动：'+p.person_id);
   }
   set(prefix+'amount_'+i,p.amount);set(prefix+'remark_'+i,p.remark||c.payment_remark||c.basic?.fields?.['t-abst']||'费用报销');
   results.push({index:i,person_id:p.person_id,name:p.name,amount:p.amount,bankBound:!!bank.value});
  }
  $.F.getDataToContext('22');$.F.getDataToContext('19');return{ok:true,action:'fill_transfers',serial:c.serial,results,transferRows:plans.length,total:$('#formWF_YBX_19_t-yt_amt').val(),remaining:$('#formWF_YBX_19_t-wt_amt').val(),saveRequested:false};
 }
 function fillExplanation(c,save){guard(c);const notes=c.explanations;if(!notes?.general)throw new Error('补充说明未配置');if(notes.record_only===true)throw new Error('当前说明按用户最新要求仅留档，不自动改写现有单据');const text=[notes.general,...(notes.card_notes||[]).map(x=>x.text)].join('\n\n');set('formWF_YBX_76_t-spec_response',text);$.F.getDataToContext('76');if(save)click(dialog('formWF_YBX_76_t-spec_response'),'保存');return{ok:true,action:'fill_explanation',serial:c.serial,text,cardNoteCount:(notes.card_notes||[]).length,saveRequested:!!save,requiresReadback:!!save};}
 function noContract(c){guard(c);if(c.contract?.exists!==false)throw new Error('未确认无合同；有合同案件须走关联和附件流程');const f=document.getElementById('iframepage_print');if(!f?.contentDocument.body.innerText.includes('报销单号：'+c.serial))throw new Error('预览不符');const es=[...f.contentDocument.querySelectorAll('input[name^="form_proof_choose"][value="N"]')];if(es.length!==1||!es[0].closest('tr')?.innerText.includes('合同号'))throw new Error('合同选择结构变化');if(!es[0].checked)es[0].click();return{ok:true,action:'choose_no_contract',serial:c.serial,requiresReadback:true};}
 function queryMoney(value){const n=cents(value),whole=String(Math.floor(n/100)),fraction=String(n%100).padStart(2,'0').replace(/0+$/,'');return fraction?whole+'.'+fraction:whole;}
 async function fillFunding(c,save){
  guard(c);const grid=$('#gridWF_YBX_242');if(!visible(grid[0]))throw new Error('经费选择窗口未打开');
  const multiple=c.funding.length>1?'Y':'N',radio=one([...document.querySelectorAll('input[name="rdIsmulti_Proj"]')].filter(x=>visible(x)&&x.value===multiple),'项目模式不唯一');
  if(!radio.checked)radio.click();await wait(()=>$.UC.bindData('#61-ismulti_proj#')===multiple,'项目模式尚未绑定');
  const rows=grid.jqGrid('getDataIDs').map(id=>({id,r:grid.jqGrid('getRowData',id)}));
  if(rows.some(x=>x.r['t.select_flag']==='已选'&&!c.funding.some(p=>p.code===x.r['t.uni_prj_code'])))throw new Error('存在配置以外的已选项目');
  for(const p of c.funding){const x=one(rows.filter(x=>x.r['t.uni_prj_code']===p.code),'经费项目不唯一：'+p.code);if(x.r['t.select_flag']!=='已选'){const b=document.getElementById(x.id).querySelector('[btnname="选择"]');if(!b)throw new Error('原生项目选择缺失');b.click();await wait(()=>grid.jqGrid('getRowData',x.id)['t.select_flag']==='已选','项目选择未完成');}}
  if(save)click(dialog('gridWF_YBX_242'),'保存');return{ok:true,action:'fill_funding',serial:c.serial,projects:c.funding.map(x=>x.code),saveRequested:!!save,requiresReadback:!!save};
 }
 async function preReview(c,index){
  guard(c);const p=c.pre_review[index];if(!p||index<0||index>3)throw new Error('预审人员配置不存在');
  const frame=document.getElementById('iframepage_print');if(!frame?.contentDocument.body.innerText.includes('报销单号：'+c.serial))throw new Error('预览身份不符');
  const token='ustc_xmysr'+(index||'');const e=one([...frame.contentDocument.querySelectorAll('[onclick]')].filter(x=>x.getAttribute('onclick').startsWith("pageOpen_v1('选择流程参数','WORKFLOW_V1&"+token+'&')),'预审入口不唯一');
  e.click();await wait(()=>visible(document.getElementById('formWF_YBX_1297_t-param_key3'))&&$('#formWF_YBX_1297_t-param_show_name').val()==='[项目预审人'+(index+1)+']','预审窗口未就绪');
  // 该原生窗口明确接受“工号/姓名”格式，保存过程校验并解析；不凭蓝框判完成。
  set('formWF_YBX_1297_t-param_key3',p.id+'/'+p.name);$.F.getDataToContext('1297');click(dialog('formWF_YBX_1297_t-param_key3'),'保存');
  return{ok:true,action:'save_pre_review',serial:c.serial,index,...p,requiresReadback:true};
 }
 async function cardQuery(c,index){
  guard(c);const p=c.payments.filter(x=>x.mode==='corporate_card')[index];if(!p?.transaction)throw new Error('公务卡交易条件未配置');
  if(!visible(document.getElementById('formWF_YBX_792_t-no'))){click(document,'选择消费记录');await wait(()=>visible(document.getElementById('formWF_YBX_792_t-no')),'消费查询未打开');}
  const search=queryMoney(p.transaction.search_amount||p.amount);set('formWF_YBX_792_t-no',p.person_id);set('formWF_YBX_792_t-pay_amt',search);set('formWF_YBX_792_t-pay_time',p.transaction.search_period||'A');$.F.getDataToContext('792');
  if($.UC.bindData('#792-t.no#')!==p.person_id||$.UC.bindData('#792-t.pay_amt#')!==search)throw new Error('消费查询条件未绑定');
  let done=false;const handler=(_event,_xhr,settings)=>{if(settings.url.includes('commonQuery_doQuery.action?funcno=793&'))done=true;};
  $(document).on('ajaxComplete.reimbursementCard',handler);
  try{click(dialog('formWF_YBX_792_t-no'),'查询');await wait(()=>done,'消费查询未完成');}
  finally{$(document).off('ajaxComplete.reimbursementCard',handler);}
  const g=$('#gridWF_YBX_793'),rows=g.jqGrid('getRowData');return{ok:true,action:'query_card',serial:c.serial,person_id:p.person_id,searchAmount:search,rowCount:rows.length,candidates:rows.map(r=>({name:r['t.accname'],date:r['t.pay_time'],merchant:r['t.pay_addr'],amount:r['t.pay_amt'],cardSuffix:r['t.account'].slice(-4)}))};
 }
 function cardSelect(c,index){
  guard(c);const p=c.payments.filter(x=>x.mode==='corporate_card')[index],t=p?.transaction;if(!t?.date||!t.merchant||!/^\d{4}$/.test(t.card_suffix))throw new Error('交易匹配条件不完整');
  const g=$('#gridWF_YBX_793');if(!visible(g[0]))throw new Error('消费候选未打开');
  const id=one(g.jqGrid('getDataIDs').filter(id=>{const r=g.jqGrid('getRowData',id);return r['t.accname']===p.name&&r['t.pay_time'].startsWith(t.date)&&r['t.pay_addr']===t.merchant&&cents(r['t.pay_amt'])===cents(t.search_amount||p.amount)&&r['t.account'].endsWith(t.card_suffix);}), '公务卡候选不唯一或不存在');
  const selected=g.jqGrid('getGridParam','selarrrow')||[];if(selected.some(x=>x!==id))throw new Error('存在其他已选公务卡记录');if(!selected.includes(id))g.jqGrid('setSelection',id,true);
  const input=one([...document.getElementById(id).querySelectorAll('input[cname="t.real_amount"]')],'实报金额控件不唯一');$(input).val(p.amount).trigger('change').trigger('blur');click(one([...document.querySelectorAll('.foot_winWF_YBX_585')].filter(visible),'消费保存窗口不唯一'),'保存');return{ok:true,action:'select_card',serial:c.serial,person_id:p.person_id,amount:p.amount,requiresReadback:true};
 }
 async function audit(c){
  guard(c);const frame=document.getElementById('iframepage_print'),text=frame?.contentDocument?.body?.innerText||'',previewLoaded=text.includes('报销单号：'+c.serial);const tables=previewLoaded?[...frame.contentDocument.querySelectorAll('table')].map(t=>[...t.rows].map(r=>[...r.cells].map(x=>x.innerText.trim()))):[];
  const issues=[];if(!previewLoaded)issues.push('当前不是已保存单据预览；编辑器值不能当保存回执');
  for(const p of c.payments){const amount=cents(p.amount)/100,formatted=amount.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});const matched=tables.flat().some(row=>row.includes(p.name)&&row.includes(formatted)&&(p.mode!=='bank_transfer'||row.includes(p.person_id)));if(!matched)issues.push('支付回读缺失或金额不同：'+p.person_id+'/'+p.mode);}
  const paymentTotal=previewLoaded&&text.includes('共填写'+c.payments.length+'条支付信息,小计 '+(cents(c.amount)/100).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})+'元');if(!paymentTotal)issues.push('支付总额/条数尚未获得保存回读');
  for(const p of c.pre_review||[]){if(!text.includes(p.id+'('+p.name+')'))issues.push('预审人未回读：'+p.id);}
  let explanationVerified=false;
  if(c.explanations&&previewLoaded){
   const expected=[c.explanations.general,...(c.explanations.card_notes||[]).map(x=>x.text)].join('\n\n');
   if(!visible(document.getElementById('formWF_YBX_76_t-spec_response')))await navigate(c,'explanation');
   await wait(()=>visible(document.getElementById('formWF_YBX_76_t-spec_response'))&&[...document.querySelectorAll('.ui-dialog')].filter(e=>visible(e)&&e.querySelector('#formWF_YBX_76_t-spec_response')).length===1,'说明回读窗口未打开');
   explanationVerified=$('#formWF_YBX_76_t-spec_response').val()===expected;
   if(!explanationVerified)issues.push('已保存说明内容与配置不同');
   click(dialog('formWF_YBX_76_t-spec_response'),'返回');
  }

  return{ok:issues.length===0,action:'audit_saved_preview',serial:c.serial,previewLoaded,paymentTotal,explanationVerified,issues,materialsComplete:false,materialsStatus:'附件与原件关联另按材料计划核对'};
 }
 async function prepareReview(c){const checked=await audit(c);if(!checked.ok)throw new Error('保存回读未通过：'+checked.issues.join('；'));await wait(()=>visible(document.querySelector('.foot_winWF_YBX_41 button[btnname="下一步"]')),'下一步未就绪');click(one([...document.querySelectorAll('.foot_winWF_YBX_41')].filter(visible),'单据窗口不唯一'),'下一步');return{ok:true,action:'prepare_review_requested',serial:c.serial,requiresReadback:true,finalApproval:false};}
 function acceptPromise(c){guard(c);if(c.review?.workflow_started===true)throw new Error('本案流程已创建，不能再次确认承诺生成流程');if(c.review?.ordinary_promise_authorized!==true)throw new Error('普通承诺未获本案授权');click(one([...document.querySelectorAll('.foot_winWF_YBX_512')].filter(visible),'承诺页未就绪'),'已阅读遵守且提交');return{ok:true,action:'accept_ordinary_promise',serial:c.serial,requiresReadback:true,finalApproval:false};}
 async function departmentApprovers(c){
  guard(c);const results=[];
  for(let i=0;i<c.funding.length;i++){const p=c.funding[i],a=p.department_approver,id='formWF_YBX_1369_t-drive_condition_'+i,label=$('#formWF_YBX_1369_t-condition_name_'+i).val()||'';if(!label.includes(p.code)||!label.includes('单位负责人（院级）'))throw new Error('审批节点身份变化');const control=$('#'+id).data('xmSelect');one((control?.options?.data||[]).filter(x=>x.value===a.id&&x.name===a.id+'('+a.name+')'),'审批人候选不唯一：'+a.id);$.F.setInputData(id,'xmselect',a.id,'');await wait(()=>control.getValue().map(x=>x.value).join(',')===a.id&&$.F.getInputData(id,'xmselect')===a.id,'审批人未完成业务绑定');$('#'+id).find('xm-select').trigger('change');results.push({project:p.code,...a});}
  $.F.getDataToContext('1369');if($.UC.bindData('#1369-t.drive_condition#')!==results.map(x=>x.id).join('$|'))throw new Error('审批人上下文不同');click(dialog('formWF_YBX_1369_t-drive_condition_0'),'保存');return{ok:true,action:'save_department_approvers',serial:c.serial,results,requiresReadback:true,finalApproval:false};
 }
 function status(c){guard(c,false);return{ok:true,action:'status',serial:c.serial,node:$.UC.bindData('#61-node_id#'),captchaRequired:$.UC.bindData('#0-NEED_SP_YZM#'),applicantAgreementVisible:visible(document.querySelector('.foot_winWF_YBX_76 button[btnname="同意"]')),editablePaymentVisible:visible(document.getElementById('formWF_YBX_19_t-bx_amt')),previewVisible:visible(document.getElementById('iframepage_print')),finalApprovalExecuted:false};}
 async function waitSection(c,section){guard(c,false);const tests={promise:()=>visible(document.querySelector('.foot_winWF_YBX_512 button[btnname="已阅读遵守且提交"]')),department:()=>visible(document.getElementById('formWF_YBX_1369_t-condition_name_0')),applicant:()=>visible(document.querySelector('.foot_winWF_YBX_76 button[btnname="同意"]'))};if(!tests[section])throw new Error('未知等待环节');await wait(tests[section],'目标环节未完成加载：'+section);return{...status(c),section};}
 function savePayments(c){guard(c);if(cents($('#formWF_YBX_19_t-bx_amt').val())!==cents(c.amount)||cents($('#formWF_YBX_19_t-yt_amt').val())!==cents(c.amount)||cents($('#formWF_YBX_19_t-wt_amt').val())!==0)throw new Error('支付总额尚未闭合');const plans=c.payments.filter(p=>p.mode==='bank_transfer');for(let i=0;i<plans.length;i++){const p=plans[i];if($('#formWF_YBX_22_t-sno_'+i).val()!==p.person_id||$('#formWF_YBX_22_t-sname_'+i).val()!==p.name||cents($('#formWF_YBX_22_t-amount_'+i).val())!==cents(p.amount)||!$('#formWF_YBX_22_t-cnt1_'+i).val())throw new Error('转卡身份、金额或银行卡不符：'+i);}const cards=c.payments.filter(p=>p.mode==='corporate_card');for(let i=0;i<cards.length;i++){const p=cards[i];if($('#formWF_YBX_285_t-gwk_banksname_'+i).val()!==p.name||cents($('#formWF_YBX_285_t-amount_'+i).val())!==cents(p.amount)||!$('#formWF_YBX_285_t-gwk_bankacnt_'+i).val()||!$('#formWF_YBX_285_t-gwk_seq_no_'+i).val())throw new Error('公务卡记录未完成系统绑定：'+i);}$.F.getDataToContext('22');$.F.getDataToContext('285');$.F.getDataToContext('19');click(one([...document.querySelectorAll('.foot_winWF_YBX_13')].filter(visible),'结算保存窗口不唯一'),'保存');return{ok:true,action:'save_payments',serial:c.serial,expectedAmount:c.amount,requiresReadback:true};}
 root.ReimbursementAutomation={version:'0.3.1',guard,wait,click,dialog,set,cents,navigate,fillBasic,fillExpenses,fillTransfers,fillExplanation,noContract,queryMoney,fillFunding,preReview,cardQuery,cardSelect,audit,prepareReview,acceptPromise,departmentApprovers,status,waitSection,savePayments};
})(globalThis);
