/* 当前角色审批。选择范围、逐字意见、本人验证及独立回读各有明确入口。 */
window.WorkbenchApprover = async function(context) {
  const {value,content,button,table,perform,api,tool,onResult,notice}=context;
  const make=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
  const titles={planned:'等待确认',running:'逐项办理中',verification_required:'等待本人验证',ready_to_resume:'可以接续',paused:'后续项目已暂停',unknown_result:'原结果待核实',result_needs_review:'待独立核对',blocked_before_dispatch:'条件已变化',documents_pending:'文档整理待接续',completed:'本批审批步骤已回读',not_started:'尚未办理',awaiting_verification:'等待本人验证',dispatch_intent:'原派发待回读',verified:'本角色步骤已回读'};
  const request=async args=>perform(tool,{...args,request_id:'approver-'+crypto.randomUUID().replaceAll('-','')},false,{show:false});
  const open=async plan_id=>onResult(await api('/api/read',{tool:tool.name,arguments:{action:'status',plan_id}}));
  if(value.plans){
    content.append(make('p','批次会保留在这里。更换页面或重启工作台后，从原批次接续。'));
    if(!value.plans.length){content.append(make('p','尚无审批批次。先从“本人账号与财务收件箱”读取自己的待审批清单。'));return;}
    content.append(table(['账号','意见','项目数','当前进度',''],value.plans.map(row=>[row.expected_userid,row.opinion,row.items.length,titles[row.state]||row.state,button('查看原批次',()=>open(row.plan_id))])));return;
  }
  if(value.schema==='ustc-approver-selection-v1'){
    content.append(make('p','只列本次完整读取的本人待办。勾选所要办理的项目，再填写本次统一意见；不同意见请分别建批次。'));
    if(!value.items.length){content.append(make('p','本次未查到本人待审批项。'));return;}
    const selected=new Set(),boxes=[];
    const total=make('p','已选择0项'),all=make('input');all.type='checkbox';all.setAttribute('aria-label','选择全部可办理项');
    const update=()=>{total.textContent='已选择'+selected.size+'项（每批最多100项）';all.checked=boxes.length>0&&boxes.every(b=>b.checked);all.indeterminate=selected.size>0&&!all.checked;submit.disabled=selected.size===0||selected.size>100||!opinion.value.trim();submit.dataset.keepDisabled=String(submit.disabled);};
    const rows=value.items.map(row=>{
      const checkbox=make('input');checkbox.type='checkbox';checkbox.disabled=!row.supported;checkbox.setAttribute('aria-label','选择单号'+row.serial);
      if(row.supported)boxes.push(checkbox);
      checkbox.onchange=()=>{checkbox.checked?selected.add(row.item_id):selected.delete(row.item_id);update();};checkbox.dataset.item=row.item_id;
      return [checkbox,row.serial,row.applicant,row.summary,row.amount,row.role,row.reason||'可核对后办理'];
    });
    all.onchange=()=>{for(const b of boxes){b.checked=all.checked;b.checked?selected.add(b.dataset.item):selected.delete(b.dataset.item);}update();};
    const decision=make('select');decision.setAttribute('aria-label','本批审批决定');decision.append(new Option('同意','agree'),new Option('拒绝','reject'));
    const opinion=make('textarea');opinion.maxLength=2000;opinion.setAttribute('aria-label','本批逐字审批意见');opinion.placeholder='填写您对所选项目的实际审批意见';opinion.oninput=update;
    const submit=button('核对本批范围',async()=>{
      const result=await request({action:'plan',connection_id:value.connection_id,source_job_id:value.source_job_id,selected_ids:[...selected],decision:decision.value,opinion:opinion.value});
      if(result)onResult(result);
    },true);
    content.append(total,table(['全选', '说明'],[[all,'只选择本清单内可办理项目']]),table(['选择','单号','申请人','摘要','金额','本次角色','提示'],rows),make('label','本批决定'),decision,make('label','本批意见（将原样写入学校系统）'),opinion,submit);update();return;
  }
  if(value.schema!=='ustc-approver-batch-v1')return;
  const inconsistent=value.state==='completed'&&(!value.items.length||value.items.some(r=>r.state!=='verified'||r.finalized!==true));
  const state=inconsistent?'result_needs_review':value.state;
  content.append(make('h3',titles[state]||state),make('p','本人账号：'+value.expected_userid+'；本批决定：'+(value.decision==='agree'?'同意':'拒绝')));
  const opinion=make('blockquote',value.opinion);opinion.style.whiteSpace='pre-wrap';content.append(opinion);
  content.append(table(['单号','摘要','金额','本次角色','进度','文档整理'],value.items.map(row=>[row.serial,row.summary,row.amount,row.role,titles[row.state]||row.state,row.finalized?'完成':'待完成'])));
  content.append(make('p',inconsistent?'批次汇总与逐项进度不一致，请核对原任务；尚不能显示完成。':value.error||value.note));
  async function act(action,confirm=false){
    const execute=['run','resume','verification','finalize'].includes(action),args={action,plan_id:value.plan_id,execute,request_id:'approver-'+crypto.randomUUID().replaceAll('-','')};
    if(action==='run')args.authorization_note='本人在工作台核对并点击确认：仅办理本计划所列单号、角色、金额，决定及逐字意见如计划；保留学校本人验证。';
    const summary=[['本人账号',value.expected_userid],['本批决定',value.decision==='agree'?'同意':'拒绝'],['原样意见',value.opinion],...value.items.map(r=>[r.serial,`${r.summary}；金额${r.amount}；角色${r.role}`])];
    const result=await perform(tool,args,confirm,{show:false,summary});
    if(result)onResult(result);
  }
  if(state==='planned')content.append(button('确认所列范围并开始办理',()=>act('run',true),true));
  if(state==='verification_required'){
    content.append(make('p','学校原生验证可能在本人确认后完成当前这一项。验证完成后回到这里接续，程序先查原结果，再继续其余项目。'));
    content.append(button('打开本人的学校验证页',()=>act('verification'),true),button('本人已完成验证，接续原批次',()=>act('resume')));
  }
  if(['ready_to_resume','paused'].includes(state))content.append(button('继续未完成项目',()=>act('resume'),true));
  if(state==='unknown_result')content.append(button('只恢复原审批结果',()=>act('recover'),true));
  if(state==='documents_pending')content.append(button('只补齐已审批项目的文档整理',()=>act('finalize'),true));
  if(!['completed','paused','planned'].includes(state))content.append(button(value.pause_requested?'已请求暂停后续项目':'暂停后续项目',async()=>{await request({action:'pause',plan_id:value.plan_id});notice('已请求暂停；当前项目的原回读和本人验证保留。');await open(value.plan_id);}));
  content.append(button('更新本批进度',()=>open(value.plan_id)));
};
