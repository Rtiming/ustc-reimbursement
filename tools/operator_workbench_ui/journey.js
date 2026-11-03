window.WorkbenchJourney = function(context) {
  const {value,content,button,table,onNext,api,perform,tools,notice}=context;
  const make=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
  if(value.schema==='ustc-case-journey-v1') {
    content.append(make('h2','本案下一步'),make('p',value.summary||value.note,'muted'));
    if(value.next_action)content.append(button(value.next_action.label||'继续',()=>onNext(value.next_action),true));
    const labels={reviewed_with_gaps:'差项已查看，仍需补齐',complete:'本步已核对',needs_input:'需要补充',needs_review:'待核对',created:'已建单',not_started:'尚未开始'};
    content.append(table(['步骤','当前状态','要做什么',''],value.steps.map(row=>[row.title,labels[row.state]||row.state,row.detail,button(row.action.label,()=>onNext(row.action))])));
    return;
  }
  content.append(make('h2','票面核对 · '+value.name),make('p','逐页对照原件核对票号、抬头税号、日期、金额及实际费用。保存的是本地人工核对记录，官方认证和费用范围另外核验。','muted'));
  const page=make('select');page.setAttribute('aria-label','票面页码');for(let i=1;i<=value.page_count;i++)page.add(new Option('第'+i+'页',String(i)));
  const image=make('img');image.alt='本案发票原件';image.style.maxWidth='100%';
  const zoom=()=>{const d=make('dialog','','material-zoom'),full=make('img');full.src=image.src;full.alt=image.alt;d.append(button('关闭放大',()=>{d.close();d.remove();}),full);document.body.append(d);d.showModal();d.oncancel=()=>d.remove();};
  image.onclick=zoom;image.style.cursor='zoom-in';
  const load=()=>{image.src='/api/invoice-preview?'+new URLSearchParams({case_id:value.case_id,index:value.index,page:page.value});};page.onchange=load;load();
  const layout=make('div','','material-review-layout'),viewer=make('div','','material-preview'),fields=make('div','','material-review-fields');layout.append(viewer,fields);content.append(layout);viewer.append(page,button('放大票面',zoom),image);
  const numbers=make('textarea');numbers.value=value.invoice_numbers.join('\n');numbers.setAttribute('aria-label','核对后的完整票号');fields.append(make('label','完整票号（每行一个）'),numbers);
  const checks=[];
  for(const text of ['票号与原件一致','抬头、税号或适用票种信息已核对','日期、金额、人员和实际费用已核对']){const label=make('label',undefined,'check-line'),input=make('input');input.type='checkbox';label.append(input,document.createTextNode(text));checks.push(input);fields.append(label);}
  const note=make('textarea');note.placeholder='记录核对结果和依据；存在未解决问题时返回补充，不勾选通过。';note.value=value.note||'';note.setAttribute('aria-label','票面核对说明');fields.append(make('label','票面核对说明'),note);
  fields.append(button('保存票面核对并继续',async()=>{
    if(checks.some(e=>!e.checked))throw Error('请完成三项票面核对后再保存。');
    const invoice_numbers=numbers.value.split(/\s+/).filter(Boolean);
    if(!invoice_numbers.length||invoice_numbers.some(n=>! /^(?:[0-9]{8}|[0-9]{10}|[0-9]{20})$/.test(n)))throw Error('请填写原件上的完整票号。');
    if(!note.value.trim())throw Error('请记录本次核对结果和依据。');
    const result=await perform(tools.find(t=>t.name==='reimbursement_verify_invoice'),{case_id:value.case_id,index:value.index,invoice_numbers,note:note.value.trim(),expected_revision:value.revision},false,{show:false});
    if(result?.ok){notice('本件票面核对已保存。');await onNext({tool:'reimbursement_case_journey',arguments:{case_id:value.case_id},label:'本案下一步'});}
    else throw Error(result?.error||'没有取得保存回执，请查看原操作。');
  },true),button('返回本案进度',()=>onNext({tool:'reimbursement_case_journey',arguments:{case_id:value.case_id},label:'本案下一步'})));
};
