/* 用户对照原件核对，写入仍由intake_review与update_case负责。 */
window.WorkbenchMaterialReview = async function (context) {
  const {api, perform, tool, caseId, jobId, heading, content, button, table, notice, onNext, isCurrent} = context;
  const make=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
  let offset=0;
  let current;
  async function load() {
    const value=await api('/api/read',{tool:'reimbursement_intake_review',arguments:{case_id:caseId,intake_job_id:jobId,offset,limit:50}});
    if(!isCurrent())return;
    current=value;
    content.replaceChildren();
    content.append(make('p',`共${value.total_materials}份展开材料。先对照原件确认类别和票号；登记为候选后再进行票面审核。`,'muted'));
    content.append(table(['材料','格式','页数','核对状态',''],value.rows.map(row=>[
      row.name,row.kind,row.page_count || '待核对',row.registered_review?'已登记来源':'待核对',button('查看并核对',()=>edit(row))])));
    const actions=make('div','','actions');
    if(value.can_split_activities&&value.total_materials>1)actions.append(button('这个包含多个活动：分别分配材料',()=>onNext({tool:'reimbursement_intake_groups',arguments:{case_id:caseId,intake_job_id:jobId}})));
    if(offset)actions.append(button('上一页',()=>{offset=Math.max(0,offset-50);return load();}));
    if(value.has_more)actions.append(button('下一页',()=>{offset+=50;return load();}));
    content.append(actions);
  }
  async function edit(row) {
    content.replaceChildren();notice('');
    content.append(make('h2',row.name));
    const layout=make('div','','material-review-layout');
    const viewer=make('div','','material-preview'),fields=make('div','','material-review-fields');
    layout.append(viewer,fields);content.append(layout);
    const pageSelect=make('select');for(let page=1;page<=Math.max(1,row.page_count);page++)pageSelect.add(new Option('第'+page+'页',String(page)));
    pageSelect.setAttribute('aria-label','原件页码');viewer.append(pageSelect);
    const display=make('div');viewer.append(display);
    async function showPage(){
      display.replaceChildren();
      const params=new URLSearchParams({case_id:caseId,intake_job_id:jobId,material_id:row.material_id,page:pageSelect.value});
      if(['pdf','png','jpg','jpeg','webp','tif','tiff'].includes(row.kind)){
        const image=make('img');image.src='/api/material-preview?'+params;image.alt=row.name+'原件第'+pageSelect.value+'页';image.style.cursor='zoom-in';image.onclick=()=>{const dialog=make('dialog','','material-zoom');const full=make('img');full.src=image.src;full.alt=image.alt;dialog.append(button('关闭放大',()=>{dialog.close();dialog.remove();}),make('p','可滚动查看原件细节。','help'),full);document.body.append(dialog);dialog.showModal();dialog.oncancel=()=>dialog.remove();};image.onerror=()=>{display.replaceChildren(make('p','原件预览未完成，请核对来源或下载原件查看。'));};display.append(button('放大当前原件',()=>image.click()),image);
      }else{
        const result=await api('/api/read',{tool:'reimbursement_intake_review',arguments:{case_id:caseId,intake_job_id:jobId,action:'preview',material_id:row.material_id,page:Number(pageSelect.value)}});
        if(isCurrent())display.append(make('pre',result.text || result.reason || '请下载原件查看。'));
      }
    }
    pageSelect.onchange=()=>showPage().catch(e=>notice(e.message));
    const download=make('a','下载本件原件','link');download.href='/api/material-original?'+new URLSearchParams({case_id:caseId,intake_job_id:jobId,material_id:row.material_id});viewer.append(download);
    const roleLabel=make('label','这份材料是什么？'),role=make('select');role.setAttribute('aria-label','材料类别');
    for(const [key,label]of [['other','待分类'],['invoice','普通电子发票'],['payment','付款凭证'],['supporting','辅助材料']])role.add(new Option(label,key));
    role.value=row.registered_review?.type || 'other';fields.append(roleLabel,role);
    const ticketFields=make('div'),inputs=[];fields.append(ticketFields);
    function addRecord(record={number:'',page:1}){
      const part=make('div','','invoice-review-line'),number=make('input'),page=make('input');
      number.type='text';number.inputMode='numeric';number.value=record.number;number.placeholder='对照原件确认20位完整票号';number.setAttribute('aria-label','完整发票号码');
      page.type='number';page.min='1';page.max=String(row.page_count||1);page.value=record.page;page.setAttribute('aria-label','该发票所在页码');
      const entry={number,page};inputs.push(entry);part.append(make('label','完整票号'),number,make('label','所在页码'),page,button('移除此票',()=>{part.remove();inputs.splice(inputs.indexOf(entry),1);}));ticketFields.append(part);
    }
    const original=row.registered_review?.invoice_records || row.invoice_candidates;
    (original.length?original:[{number:'',page:1}]).forEach(addRecord);
    ticketFields.append(button('同文件还有一张发票',()=>addRecord()));
    function roleChanged(){ticketFields.hidden=role.value!=='invoice';}
    role.onchange=roleChanged;roleChanged();
    fields.append(make('p','识别出的号码只是候选。其他类型票据保留对应专用流程；不把订单或辅助材料当作发票。','help'));
    const basis=make('textarea');basis.setAttribute('aria-label','本次核对说明');basis.placeholder='记录核对依据或需要保留的问题';basis.value=row.registered_review?.intake_review?.confirmation_note || '';
    fields.append(make('label','本次核对说明'),basis);
    const confirmed=make('input');confirmed.type='checkbox';confirmed.id='confirm-material-source';const confirmationLabel=make('label','我已对照原件核对本项输入');confirmationLabel.htmlFor=confirmed.id;fields.append(confirmed,confirmationLabel);
    const actions=make('div','','actions');fields.append(actions);
    actions.append(button('保存本项核对',async()=>{
      if(!confirmed.checked)throw Error('请先对照原件核对，并勾选确认。');
      if(!basis.value.trim())throw Error('请填写本次核对说明。');
      const record={material_id:row.material_id,role:role.value,invoice_records:role.value==='invoice'?inputs.map(e=>({number:e.number.value.trim(),page:Number(e.page.value)})):[]};
      if(role.value==='invoice')for(const invoice of record.invoice_records){if(!/^[0-9]{20}$/.test(invoice.number))throw Error('请对照原件填写20位完整票号。');if(!Number.isInteger(invoice.page)||invoice.page<1||invoice.page>row.page_count)throw Error(`页码请填写1至${row.page_count}，并与原件对应。`);}
      const args={case_id:caseId,intake_job_id:jobId,action:'register',records:[record],expected_revision:current.revision,confirmation_note:basis.value.trim(),execute:true};
      const result=await perform(tool,args,true,{show:false,summary:[['材料',row.name],['材料类别',role.selectedOptions[0].textContent],['票号',record.invoice_records.map(r=>r.number+'（第'+r.page+'页）').join('\n')||'不登记为发票'],['核对依据',basis.value.trim()],['保存范围','只登记本活动材料候选；不选票、不改申请额、不提交财务']]});
      if(!result || !isCurrent())return;
      if(result.saved){current.revision=result.revision;notice('本项核对已保存。发票仍需票面审核，尚未进行学校选票。');content.replaceChildren(make('h2','本项核对已保存'),make('p',row.name,'muted'));const next=make('div','','actions');next.append(button('返回材料清单',load));if(result.next_action?.tool)next.append(button('继续核对票源',()=>onNext(result.next_action),true));content.append(next);}
      else throw Error(result.error || '未取得材料保存回执，请先核对原结果。');
    },true),button('返回清单',load));
    await showPage();
  }
  await load();
};
