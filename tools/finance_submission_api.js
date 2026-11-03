/* 提交选项取学校HTTP确认正文；只修改明确的合同/打印选择，流程节点另行处理。 */
function reimbursementSupplementaryInventory(doc){
  const rows=[];
  for(const cell of doc.querySelectorAll('[onclick]')){
    const call=cell.getAttribute('onclick')||'',m=call.match(/^pageOpen_v1\('特殊事项','([^']{1,200})'\)$/);
    if(!m)continue;
    const tr=cell.closest('tr'),cells=[...tr?.cells||[]].map(x=>x.textContent.trim());
    if(cells.length!==4||!/^\d+&[^&\r\n]+$/.test(m[1])||cells[0]!==m[1].split('&')[0]){rows.push({xtype:m[1],cells,read_only_unparsed:true});continue;}
    rows.push({xtype:m[1],spec_type:cells[0],caption:cells[1],response:cells[2],hint:cells[3]});
  }
  if(new Set(rows.map(r=>r.xtype)).size!==rows.length)throw Error('SUBMISSION_API_SUPPLEMENTARY_DUPLICATE');
  return rows;
}
function reimbursementSubmissionInventory(doc,serial,mode='daily'){
  const tight=doc.body.textContent.replace(/\s+/g,'');
  const ids=[...tight.matchAll(/报销单号[:：](\d+)/g)].map(m=>m[1]);
  const sections=mode==='remuneration'?tight.includes('中国科学技术大学薪酬业务单'):mode==='daily'&&tight.includes('基本信息')&&tight.includes('报销项信息')&&tight.includes('支付信息');
  if(ids.length!==1||ids[0]!==serial||!sections||!tight.includes('财务审批意见')||!tight.includes('会计审核'))throw Error('SUBMISSION_API_DOCUMENT_INCOMPLETE');
  const controls=[...doc.querySelectorAll('input[name="form_grantcwprint"],input[name^="form_proof_choose"]')];
  const groups=new Map();
  for(const input of controls){
    const name=input.getAttribute('name'),value=input.getAttribute('value');
    if(input.getAttribute('type')!=='radio'||!['Y','N'].includes(value)||input.disabled)throw Error('SUBMISSION_API_RADIO_CONTRACT');
    if(!groups.has(name))groups.set(name,[]);groups.get(name).push(input);
  }
  const original={print:null,proofs:[]};
  for(const [name,inputs]of groups){
    if(inputs.length!==2||new Set(inputs.map(i=>i.getAttribute('value'))).size!==2||inputs.filter(i=>i.checked).length!==1)throw Error('SUBMISSION_API_RADIO_GROUP');
    const selected=inputs.find(i=>i.checked).getAttribute('value'),container=inputs[0].closest('ul');
    if(!container||inputs.some(i=>i.closest('ul')!==container))throw Error('SUBMISSION_API_RADIO_OWNER');
    if(name==='form_grantcwprint'){
      if(container.getAttribute('onclick')!=="pageOpen('委托财务处打印')")throw Error('SUBMISSION_API_PRINT_HANDLER_CHANGED');
      original.print=selected;
    }else{
      const row=inputs[0].closest('tr'),cells=[...row?.cells||[]].map(c=>c.textContent.trim());
      const handler=container.getAttribute('onchange'),match=/^pageOpen_v1\('辅助证据必选','([^']+)'\)$/.exec(handler||'');
      const args=match?.[1].split('&');
      if(!/^form_proof_choose_\d+$/.test(name)||!args||args.length!==3||name!=='form_proof_choose_'+args[0]||cells.length!==4||!cells[0]||!cells[1]||!args[1]||!args[2])throw Error('SUBMISSION_API_PROOF_ROW_CHANGED');
      const contract=/^合同号(?:\[|$)/.test(cells[0]);
      if(contract&&(!/^共\s*0\s*笔$/.test(cells[1])||args[1]!=='合同号'||args[2]!=='合同号'))throw Error('SUBMISSION_API_EXISTING_CONTRACT');
      original.proofs.push({name,cells:cells.slice(0,3),ord:args[0],type:args[1],guid:args[2],selected,contract});
    }
  }
  original.proofs.sort((a,b)=>a.name.localeCompare(b.name));
  if(original.print===null)throw Error('SUBMISSION_API_PRINT_GROUP_MISSING');
  if(!original.proofs.some(p=>p.contract)&&tight.includes('合同号'))throw Error('SUBMISSION_API_CONTRACT_SCOPE_UNKNOWN');
  return original;
}

async function reimbursementSubmissionPrepare(c){
  const requests=[],h=createReimbursementHttp(c.expected_userid,{prefix:'SUBMISSION_API',requests,timeout:15000,allowed:['definition','detailsTemplate','printDocument']});
  const hash=async s=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s)))].map(n=>n.toString(16).padStart(2,'0')).join('');
  if(c.plan.contract_exists!==false||c.plan.delegate_finance_print!==false||c.action!=='inspect'&&c.plan.submit_authorized!==true)throw Error('SUBMISSION_API_PLAN_SCOPE');
  const read=await reimbursementBusinessApi({...c,action:'bill-read',domain:'basic',contracts:c.basic_contracts});
  if(read.bill['t.status']!=='-99'||read.bill['t.lc_finish']!=='F')throw Error('SUBMISSION_API_NOT_DRAFT');
  const forms=read.sections.filter(s=>s.funcno==='60'&&s.kind==='form'&&s.rows.length===1);
  if(forms.length!==1||forms[0].rows[0].ABST!==c.plan.summary||![forms[0].rows[0].REQ_MOBILE,forms[0].rows[0].REQ_TELE].includes(c.plan.phone))throw Error('SUBMISSION_API_BASIC_MISMATCH');
  const d=await h.post('loadDefinition.action?type=W',{winno:'41',projid:'WF_YBX'}),contract=c.submission_contract;
  const projected=Object.fromEntries(['wins','funcs','funcMap','actMap'].map(k=>[k,d[k]]));
  if(await hash(JSON.stringify(ReimbursementNativeForm.canonical(projected)))!==contract.definition_sha256)throw Error('SUBMISSION_API_DEFINITION_CHANGED');
  const shell=await h.post('redirectCustomPage.action',{url:'Customize/customPrint/bx_frame_print.jsp'},'text');
  const inner=await h.post('redirectCustomPage.action',{url:'Customize/customPrint/bx_dtl.jsp'},'text');
  if(await hash(shell)!==contract.shell_sha256||await hash(inner)!==contract.inner_sha256)throw Error('SUBMISSION_API_NATIVE_HANDLERS_CHANGED');
  const html=await h.post('ybxPrint_yb4PrintHtml.action',{printModel:'P',printType:'报销确认',serialno:c.serial,
    byear:'',dbserver:'',bxId:'',appName:'YBX',bxOrderid:'',sign:'',jsonData:encodeURI('YB!P!NEW!P!'+c.serial+'!P!报销确认!P!'+c.expected_userid+'!P!undefined')},'text');
  const doc=new DOMParser().parseFromString(html,'text/html'),original=reimbursementSubmissionInventory(doc,c.serial,c.system_mode||'daily');
  if(c.system_mode==='remuneration'){
    if(original.proofs.length)throw Error('SUBMISSION_API_PAYROLL_UNEXPECTED_PROOFS');
    const salary=await reimbursementRemunerationInspect(c);reimbursementRemunerationPreview(doc,salary,c);
    const payments=await reimbursementBusinessApi({...c,action:'bill-read',domain:'payments',contracts:c.payment_contracts});reimbursementRemunerationNoExtraPayments(payments);
    requests.push(...salary.requests,...payments.requests);
  }
  const target={print:'N',proofs:original.proofs.map(p=>({...p,cells:p.contract?[p.cells[0],p.cells[1],'']:[...p.cells],selected:p.contract?'N':p.selected}))};
  const changes=[];
  for(const p of original.proofs)if(p.contract&&p.selected!=='N')changes.push({kind:'proof',ord:p.ord,type:p.type,guid:p.guid,value:'N'});
  if(original.print!=='N')changes.push({kind:'print',value:'N'});
  return {ok:true,action:'submission-options-prepare',complete:true,case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,
    business_transport:'native_http',bill:read.bill,original,target,changes,definition_sha256:contract.definition_sha256,
    document_sha256:await hash(html),supplementary_notes:reimbursementSupplementaryInventory(doc),native_error_present:doc.body.textContent.includes('*错误*'),
    system_write:false,save_dispatched:false,page_context_modified:false,requests:[...read.requests,...requests]};
}

async function reimbursementSubmissionOptions(c){
  let dispatched=false,prepared=null;const requests=[],steps=[];
  try{
    if(c.execute!==true||!c.pending_registered||!c.expected_plan)throw Error('SUBMISSION_API_PENDING_REQUIRED');
    if(c.plan?.submit_authorized!==true)throw Error('SUBMISSION_API_PLAN_SCOPE');
    prepared=await reimbursementSubmissionPrepare(c);
    for(const key of ['original','target','changes','definition_sha256'])
      if(JSON.stringify(ReimbursementNativeForm.canonical(prepared[key]))!==JSON.stringify(ReimbursementNativeForm.canonical(c.expected_plan[key])))throw Error('SUBMISSION_API_PLAN_DRIFT');
    const h=createReimbursementHttp(c.expected_userid,{prefix:'SUBMISSION_API',requests,timeout:15000,allowed:['button']});
    const {data:context,set}=h.contextCopy();set('61-serialno',c.serial);
    for(const change of prepared.changes){
      let button;
      if(change.kind==='print'){
        button=c.submission_contract.buttons['委托财务处打印'];set('61-bx_btinfo_params','N');
      }else{
        button=c.submission_contract.buttons['辅助证据必选'];
        set('61-proof_type',change.type);set('61-proof_is_choose','N');set('61-proof_guid',change.guid);
      }
      steps.push({...change,state:'dispatched'});dispatched=true;
      const raw=await h.post('commonUpdate_responseButtonEvent.action',{cmd:ReimbursementNativeProtocol.command(button.check_func,button.procs,context,{}),projid:'WF_YBX',userContext:JSON.stringify(context),async:false},'text');
      steps.at(-1).native_response=raw;
      const keys=[...raw.matchAll(/(?:local\(|;)([^():;]+):/g)].map(m=>m[1]);
      if(keys.some(k=>!(/^(?:61-(?!serialno$|node_id$|wf_name$)[A-Za-z0-9_.]+|0-jump_flag)$/i.test(k))))throw Error('SUBMISSION_API_OPTION_CONTEXT');
      // Auxiliary refresh hints are retained, never applied to UC or executed.
      if(!ReimbursementNativeProtocol.response(raw,keys).accepted)throw Error('SUBMISSION_API_OPTION_REJECTED');
      steps.at(-1).state='accepted';
    }
    return {...prepared,action:'submission-options-save',state:dispatched?'saved_independent_readback_required':'already_matches_readback_required',
      system_write:dispatched,save_dispatched:dispatched,steps,requests:[...prepared.requests,...requests],requires_readback:true,workflow_started_by_action:false,finalApproval:false};
  }catch(error){return {ok:false,action:'submission-options-save',case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,
    business_transport:'native_http',system_write:dispatched,save_dispatched:dispatched,prepared,steps,requests,
    state:dispatched?'unknown_save_readback_required':'rejected_before_save',error:String(error.message),requires_readback:true,finalApproval:false};}
}
