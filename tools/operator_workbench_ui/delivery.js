window.WorkbenchDelivery=async function(ctx){
  const {value,content,button,table,api,perform,tool,onResult,isCurrent}=ctx;
  const node=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
  const labels={planned:'待确认导出',queued:'排队中',running:'正在导出',verified:'PDF已核验',needs_review:'原导出需要核对'};
  const status=plan_id=>api('/api/read',{tool:tool.name,arguments:{case_id:value.case_id,action:'status',plan_id}});
  async function request(args,confirm=false,summary){const r=await perform(tool,{case_id:value.case_id,...args},confirm,{show:false,summary});if(r&&isCurrent())onResult(r);return r;}
  if(value.schema==='ustc-case-delivery-v1'){
    const p=value.plan,scope=[['学校单号',p.serial],['本人账号',value.expected_userid],['摘要',p.summary],['申请金额',p.amount],['业务日期',p.business_date],['本批序号',p.sequence],
      ['导出内容',p.view_kind==='details'?'学校明细PDF，供核对使用':p.include_invoice_attachments?'学校正式打印，包含票据附件':'学校正式打印，仅财务信息'],['保存位置',value.destination]];
    content.append(node('h2',labels[value.state]||value.state),table(['本次交付','确认内容'],scope),node('p',value.note));
    if(value.state==='planned')content.append(button('确认并导出本单',()=>request({action:'export',plan_id:value.plan_id,execute:true},true,scope),true));
    if(value.state==='needs_review'){
      content.append(node('p',value.verification?.error||(value.verification?.needed_inputs||[]).map(r=>r.detail).join('；')||'请核对原导出任务，已有文件保持原样。'));
      content.append(button('接续原导出任务',()=>request({action:'resume',plan_id:value.plan_id,execute:true},true,scope)));
    }
    if(value.download_url){const a=node('a','下载已核验PDF');a.href=value.download_url;a.className='primary';content.append(a);}
    content.append(button('更新导出进度',async()=>onResult(await status(value.plan_id))),button('返回本活动交付',()=>request({action:'inspect'})));
    if(['queued','running'].includes(value.state))setTimeout(async()=>{if(isCurrent()){try{const r=await status(value.plan_id);if(isCurrent())onResult(r);}catch{}}},1200);
    return;
  }
  content.append(node('p','从本活动直接生成学校明细或正式打印PDF。材料对应日期和批次序号由你核定；程序携带本案单号、账号和原任务。'));
  content.append(table(['学校单号','摘要','申请金额'],[[value.serial||'尚未建单',value.summary,value.amount]]));
  if(value.plans.length)content.append(table(['已有交付','业务日期','当前状态',''],value.plans.map(p=>[p.plan.summary,p.plan.business_date,labels[p.state]||p.state,button('查看与接续',async()=>onResult(await status(p.plan_id)))])));
  if(!value.serial){content.append(node('p','先在财务办理中取得并登记学校单号，再导出学校文件。'));return;}
  const form=node('form');form.onsubmit=e=>e.preventDefault();
  function field(label,type){const wrap=node('label',label),e=node(type==='select'?'select':'input');if(type!=='select')e.type=type;wrap.append(e);form.append(wrap);return e;}
  const date=field('材料对应的业务日期','date'),sequence=field('本批明确序号','number');sequence.min='1';sequence.step='1';
  const kind=field('导出内容','select');for(const[v,t]of [['details','学校明细PDF（供核对）'],['formal','学校正式打印']])kind.append(new Option(t,v));
  const attachments=field('正式打印的票据附件','select');for(const[v,t]of [['','请选择'],['yes','包含票据附件'],['no','只打印财务信息']])attachments.append(new Option(t,v));attachments.disabled=true;kind.onchange=()=>attachments.disabled=kind.value!=='formal';
  const basis=field('业务日期、序号的核定依据','text');basis.placeholder='例如：本批已确认的报销澄清表第2项';
  form.append(button('查看完整导出范围',()=>{if(!date.value||!Number.isInteger(Number(sequence.value))||Number(sequence.value)<1||!basis.value.trim())throw Error('请填写本批业务日期、明确序号及依据');if(kind.value==='formal'&&!attachments.value)throw Error('请选择正式打印是否包含附件');return request({action:'plan',business_date:date.value,sequence:Number(sequence.value),kind:kind.value,...(kind.value==='formal'?{include_invoice_attachments:attachments.value==='yes'}:{}),confirmation_note:basis.value.trim()});},true));
  content.append(form);
};
