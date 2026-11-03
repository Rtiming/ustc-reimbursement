window.WorkbenchCaseMaterials = async function(ctx) {
  const {api,perform,tool,caseId,content,button,table,notice,isCurrent,onNext}=ctx;
  const make=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
  let current,materials=[];
  const read=args=>api('/api/read',{tool:tool.name,arguments:{case_id:caseId,...args}});
  function field(parent,label,value='',type='text') {const wrap=make('label',label),input=make(type==='textarea'?'textarea':'input');if(type!=='textarea')input.type=type;input.value=value??'';input.setAttribute('aria-label',label);wrap.className='field';wrap.append(input);parent.append(wrap);return input;}
  function source(parent,label,value) {
    const wrap=make('div'),select=make('select');select.setAttribute('aria-label',label);select.add(new Option('请选择已导入的原件',''));
    materials.forEach(row=>select.add(new Option(row.name+(materials.filter(x=>x.name===row.name).length>1?'（'+row.sha256.slice(0,6)+'）':''),row.key)));
    select.value=value?.key||'';wrap.append(make('label',label),select);parent.append(wrap);
    const page=field(wrap,'页码',value?.page||1,'number');page.min=1;
    wrap.append(button('查看此原件',async()=>{const row=materials.find(x=>x.key===select.value);if(!row)throw Error('请先选原件');const result=await api('/api/read',{tool:'reimbursement_intake_review',arguments:{...row.preview_action.arguments,page:Number(page.value)}});const view=make('section');if(result.preview_path){const image=make('img');image.src='/api/material-preview?'+new URLSearchParams({case_id:caseId,intake_job_id:row.job_id,material_id:row.material_id,page:page.value});image.alt=row.name+'第'+page.value+'页';image.style.maxWidth='100%';view.append(image);}else if(result.text)view.append(make('pre',result.text));else if(result.preview?.text)view.append(make('pre',result.preview.text));else {const pre=make('pre',JSON.stringify(result,null,2));view.append(pre);}view.append(button('收起原件',()=>view.remove()));wrap.append(view);}));
    return {select,get:()=>{const row=materials.find(x=>x.key===select.value);if(!row)throw Error('请先选择本活动原件');const value=Number(page.value);if(!Number.isInteger(value)||value<1||value>row.pages)throw Error('页码须为1至'+row.pages+'的整数');return {key:select.value,page:value};}};
  }
  function choices(parent,label,items,value) {const wrap=make('label',label),select=make('select');select.setAttribute('aria-label',label);items.forEach(([v,t])=>select.add(new Option(t,v)));select.value=value||items[0][0];wrap.append(select);parent.append(wrap);return select;}
  function sourceList(parent,label,values=[]) {const section=make('section'),rows=[];section.append(make('h3',label));parent.append(section);function add(v){const row=make('div'),s=source(row,label+'原件',v);const entry={get:s.get};rows.push(entry);row.append(button('移除此关联',()=>{rows.splice(rows.indexOf(entry),1);row.remove();}));section.append(row);}values.forEach(add);section.append(button('增加'+label,()=>add()));return()=>rows.map(r=>r.get());}
  function evidence(parent,row,keys) {const section=make('fieldset'),get={};parent.append(section);const origin=source(section,'依据原件',row?.source);get.source=origin.get;for(const[k,label]of keys){const f=field(section,label,row?.[k]);get[k]=()=>f.value.trim();if(k==='number')section.append(button('采用本页票号候选',()=>{const ref=origin.get(),material=materials.find(x=>x.key===ref.key),candidates=material.invoice_candidates.filter(x=>x.page===ref.page);if(candidates.length!==1)throw Error('本页没有唯一票号候选，请对照原件填写');f.value=candidates[0].number;notice('已填入机器候选，仍请对照原件逐位核对');}));}return {section,get:()=>Object.fromEntries(Object.entries(get).map(([k,v])=>[k,v()]))};}
  async function save(action,values,basis) {const result=await perform(tool,{case_id:caseId,action,values,expected_revision:current.revision,confirmation_note:basis.trim()||'用户对照本活动原件核对并保存',execute:true},false,{show:false});if(result?.saved&&isCurrent()){notice('已保存本项核对记录，其他费用可以继续处理。');await load();}return result;}
  function header(title) {content.replaceChildren(make('h2',title));content.append(button('返回本活动材料',render));}
  function documentForm() {
    header('明白纸与活动资料');const d=current.document||{},form=make('div');form.className='fields';content.append(form);
    const name=field(form,'原活动文件夹名称',d.folder_name),src=source(form,'明白纸或本次确认材料',d.source),date=field(form,'明白纸业务日期',d.business_date,'date'),seq=field(form,'交付起始序号',d.first_sequence||1,'number');seq.min=1;
    const contacts={};for(const[key,title]of [['responsible_person','活动负责人'],['form_filler','填表人']]){const box=make('fieldset');box.append(make('legend',title));content.append(box);const old=d[key]||{},status=choices(box,'原件中的'+title,[['unknown','尚未核清'],['known','已核对姓名及联系信息'],['blank','已确认原件此处空白']],old.blank?'blank':old.name?'known':'unknown');const fields={};for(const[k,l]of [['name','姓名'],['student_id','学号'],['employee_id','工号'],['phone','电话'],['wechat','微信'],['qq','QQ'],['email','邮箱']])fields[k]=field(box,title+l,old[k]);contacts[key]=()=>status.value==='unknown'?undefined:status.value==='blank'?{blank:true}:Object.fromEntries(Object.entries(fields).filter(([k,v])=>v.value.trim()).map(([k,v])=>[k,v.value.trim()]));}
    const note=field(content,'核对依据','','textarea');content.append(button('保存活动材料信息',()=>{const values={folder_name:name.value.trim(),source:src.get(),first_sequence:Number(seq.value)};if(date.value)values.business_date=date.value;for(const[k,get]of Object.entries(contacts)){const value=get();if(value)values[k]=value;}return save('save_document',values,note.value);},true));
  }
  function expenseForm(old={}) {
    header(old.expense_id?'修改本项费用':'新增费用与材料关系');content.append(make('p','将同一费用范围的发票放在一项中。收款安排和付款截图分别登记；同一份合并付款可以关联多项费用。金额或收款人尚未核清时，可先保存已有信息。'));
    const description=field(content,'费用说明',old.description),invoices=[],invoiceBox=make('section');content.append(invoiceBox);invoiceBox.append(make('h3','本费用的发票'));
    function addInvoice(row){const e=evidence(invoiceBox,row,[['number','20位完整票号'],['face_amount','票面金额（元）']]);invoices.push(e);e.section.append(button('移除此票',()=>{invoices.splice(invoices.indexOf(e),1);e.section.remove();}));}
    (old.invoices||[{}]).forEach(addInvoice);invoiceBox.append(button('增加本费用的另一张发票',()=>addInvoice({})));
    const amounts=[],amountBox=make('section');content.append(amountBox);amountBox.append(make('h3','同范围申请额或清单额（已有时填写）'));
    function addAmount(row={}){const e=evidence(amountBox,row,[['amount','同范围金额（元）']]),kind=choices(e.section,'金额依据类型',[['request','申请金额'],['list','清单合计']],row.kind);const entry={get:()=>({...e.get(),kind:kind.value})};amounts.push(entry);e.section.append(button('移除此金额依据',()=>{amounts.splice(amounts.indexOf(entry),1);e.section.remove();}));}(old.amount_evidence||[]).forEach(addAmount);amountBox.append(button('增加同范围金额依据',()=>addAmount()));
    const payees=[],payBox=make('section');content.append(payBox);payBox.append(make('h3','这项费用实际打给谁'));
    function addPayee(row={}){const e=evidence(payBox,row,[['name','实际收款人或单位'],['payee_id','收款学工号或本案唯一单位编号'],['amount','该收款方应收金额（元）']]),mode=choices(e.section,'收款方式',[['bank_transfer','个人转账'],['corporate_transfer','对公转账'],['corporate_card','公务卡还款']],row.mode);const entry={get:()=>({...e.get(),mode:mode.value})};payees.push(entry);e.section.append(button('移除此收款安排',()=>{payees.splice(payees.indexOf(entry),1);e.section.remove();}));}(old.payees||[]).forEach(addPayee);payBox.append(button('增加实际收款安排',()=>addPayee()));
    const payments=sourceList(content,'已有付款凭证',old.payment_materials),itineraries=sourceList(content,'行程材料',old.itinerary_materials),note=field(content,'本项核对依据','','textarea');
    content.append(button('保存本项费用',()=>save('save_expenses',[{expense_id:old.expense_id||'expense_'+crypto.randomUUID().replaceAll('-','').slice(0,16),description:description.value.trim(),invoices:invoices.map(r=>r.get()),amount_evidence:amounts.map(r=>r.get()),payees:payees.map(r=>r.get()),payment_materials:payments(),itinerary_materials:itineraries()}],note.value),true));
  }
  function issueForm(old={}) {header('登记具体补件问题');const title=field(content,'问题简述',old.title),request=field(content,'需要补充或答复什么',old.request,'textarea'),owner=field(content,'请谁处理',old.owner),refs=sourceList(content,'相关材料',old.materials),note=field(content,'发现问题的依据',old.basis,'textarea');content.append(button('保存补件问题',()=>save('save_issue',{issue_id:old.issue_id||'issue_'+crypto.randomUUID().replaceAll('-','').slice(0,16),title:title.value.trim(),request:request.value.trim(),owner:owner.value.trim(),materials:refs()},note.value),true));}
  async function plan() {const result=await read({action:'plan'});if(!isCurrent())return;header('本活动分单结果');const p=result.plan;content.append(make('p','分单结果只用于核对本次材料。学校账号、经费、角色、原票认证和提交仍分别检查。'));if(p){for(const d of p.documents||[]){content.append(table(['拟报销单','票数','申请额','材料状态'],(d.forms||[]).map(f=>[f.summary,(f.invoice_ids||[]).length,f.claim_amount??'待核',f.status==='ready'?'材料计划可复核':'存在待核项'])));}content.append(make('p','结构：'+(p.structure_readiness.status==='ready'?'已就绪':'需核对')+'；金额：'+(p.amount_readiness.status==='ready'?'已就绪':'需核对')+'；交付命名：'+(p.export_readiness.status==='ready'?'已就绪':'需核对')));}const issues=result.needed_inputs||[];if(issues.length)content.append(table(['待核内容','说明'],issues.map(r=>[r.field,r.detail||r.code])));else content.append(make('p','当前离线规划未发现缺项。'));if(result.next_action?.tool==='reimbursement_standards_read')content.append(button('核对本活动规则',()=>onNext({ui_group:'rules'})));content.append(button('返回补齐材料',render));}


  function dispositionsForm() {
    header('逐页确认电子附件与纸质原件');
    content.append(make('p','电子材料会进入附件PDF；纸票扫描件只登记原件安排。排除页须说明理由。先看原件，再确认各页去向。'));
    const entries=[],body=make('section');content.append(body);
    for(const material of materials)for(let page=1;page<=material.pages;page++){
      const old=(current.dispositions||[]).find(x=>x.source.key===material.key&&x.source.page===page)||{};
      const row=make('fieldset');row.append(make('legend',material.name+' · 第'+page+'页'));
      const disposition=choices(row,'第'+page+'页去向',[['unknown','尚未核对'],['electronic','电子附件'],['paper','纸质原件，仅登记'],['exclude','排除并说明理由']],old.disposition);
      const reason=field(row,'本页去向依据',old.reason),location=field(row,'纸质原件安排或待定位说明',old.paper_location);
      entries.push({source:{key:material.key,page},disposition,reason,location});body.append(row);
    }
    const basis=field(content,'本次逐页核对依据','','textarea');
    content.append(button('本批均为电子材料，按此设置全部页',()=>{if(!basis.value.trim())throw Error('请先填写确认整批均为电子材料的依据');for(const row of entries){row.disposition.value='electronic';row.reason.value=basis.value.trim();}}));
    content.append(button('保存已核对的材料页去向',()=>{const values=entries.filter(x=>x.disposition.value!=='unknown').map(x=>({source:x.source,disposition:x.disposition.value,reason:x.reason.value.trim(),...(x.disposition.value==='paper'?{paper_location:x.location.value.trim()}:{})}));if(!values.length)throw Error('请先核对至少一页');return save('save_dispositions',values,basis.value);},true));
  }
  function showChildren(value) {
    header('本活动分单已登记');content.append(make('p',value.note||'继续各单核对原件和填报资料。'));
    content.append(table(['本地报销单','票数','核定申请额','学校单号',''],(value.cases||[]).map(row=>[row.title,row.invoice_count,row.amount,row.serial||'尚未创建',button('继续本单',()=>onNext(row.next_action||{tool:'reimbursement_case_journey',arguments:{case_id:row.case_id}}))])));
    if(value.held_forms?.length)content.append(make('p','还有'+value.held_forms.length+'张分单保留待核，不影响上面已明确的独立分单。'));
  }
  async function materialize() {
    const preview=await read({action:'materialize'});if(!isCurrent())return;
    if(preview.schema==='ustc-materialized-cases-v1')return showChildren(preview);
    header('确认分单并继续填报');content.append(make('p','下面仅登记本地子案，核对后从各单继续；学校建单仍由财务向导单独确认。'));
    content.append(table(['拟报销单','票数','申请额','完整票号'],(preview.forms||[]).map(row=>[row.summary,row.invoice_count,row.claim_amount,(row.invoice_numbers||[]).join('、')])));
    const note=field(content,'本次分单确认依据','','textarea');
    content.append(button('确认这些分单并继续',async()=>{if(!note.value.trim())throw Error('请填写本次分单确认依据');const result=await perform(tool,{case_id:caseId,action:'materialize',values:{plan_sha256:preview.plan_sha256},expected_revision:preview.revision,confirmation_note:note.value.trim(),execute:true},false,{show:false});if(result?.cases&&isCurrent())showChildren(result);},true));
  }
  async function delivery() {
    const preview=await read({action:'delivery_plan'});if(!isCurrent())return;header('本活动清单与附件交付');
    content.append(make('p','导出问题总结、拟收款与材料清单，以及已确认的电子附件。学校正式信息面从本案系统交付入口取得后再接入。'));
    const issues=preview.needed_inputs||[];if(issues.length)content.append(table(['需要核对','说明'],issues.map(x=>[x['人员/费用']||x.field||x.code||x.title,x['问题']||x.detail||x.message||x.reason||x.label||'请按材料逐页核对'])));
    content.append(make('p',preview.can_build_pdf?'电子附件范围已明确，可生成A4附件包。':'电子附件范围仍有待核项，先导出问题清单并继续核对。'));
    const note=field(content,'本次交付核对依据','','textarea');content.append(button('生成本活动清单与可用附件',async()=>{if(!note.value.trim())throw Error('请填写本次交付核对依据');const result=await perform(tool,{case_id:caseId,action:'delivery_build',expected_revision:current.revision,confirmation_note:note.value.trim(),execute:true},false,{show:false});if(!result||!isCurrent())return;const downloads=make('section');for(const artifact of result.artifacts||[]){const link=make('a','下载'+(artifact.name||artifact.filename||artifact.kind));link.href='/api/material-delivery-file?'+new URLSearchParams({case_id:caseId,delivery_id:result.delivery_id,artifact_name:artifact.name||artifact.filename});link.download='';downloads.append(link,make('br'));}content.append(downloads);notice(result.note||'材料交付已生成，请从本页下载。');},true));
  }

  function render() {
    if(!isCurrent())return;content.replaceChildren(make('p',current.note));const actions=make('div');actions.className='actions';content.append(actions);
    actions.append(button('逐页确认纸电材料',dispositionsForm),button('生成本活动清单与附件',delivery),button('查看或确认本活动分单',materialize));
    actions.append(button('核对明白纸与活动资料',documentForm),button('新增费用与付款、行程关联',()=>expenseForm(),true),button('登记补件问题',()=>issueForm()),button('按本活动规则规划分单',plan),button('打开材料核对与补件',async()=>{const result=await perform(tool,{case_id:caseId,action:'review_open',execute:true},false,{show:false});if(result?.url&&isCurrent()){const link=make('a','进入本活动补件工作台');link.href=result.url;link.target='_blank';link.rel='noopener';content.append(link);link.click();}}));
    content.append(make('h2','本活动已导入材料（'+current.total_materials+'份）'));if(!materials.length)content.append(button('先导入原件',()=>onNext({ui_group:'materials'})));else content.append(table(['原件','页数','机器票号候选'],materials.map(r=>[r.name,r.pages,r.invoice_candidates.map(x=>x.number).join('、')||'待核对'])));
    content.append(make('h2','已登记费用（'+current.expenses.length+'项）'),table(['费用','发票数','付款凭证','行程',''],current.expenses.map(r=>[r.description,r.invoices.length,(r.payment_materials||[]).length,(r.itinerary_materials||[]).length,button('查看与修改',()=>expenseForm(r))])));
    if(current.issues.length)content.append(make('h2','待补件记录'),table(['问题','所需答复','处理人',''],current.issues.map(r=>[r.title,r.request,r.owner,button('查看问题',()=>issueForm(r))])));
  }
  async function load() {current=await read({});materials=[...current.materials];let next=current.next_page_action;while(next){const more=await api('/api/read',{tool:tool.name,arguments:next.arguments});materials.push(...more.materials);current.expenses.push(...more.expenses);next=more.next_page_action;}render();}
  await load();
};
