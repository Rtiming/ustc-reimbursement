/* Current-actor approvals from a frozen Q105 row. Fixed HTTP only; no UI clicks. */
const ApproverBinding = (() => {
  const fields=['t.serial_no','t.req_name','t.remark','t.uni_prj','t.wfname','t.wf_name','t.uni_key','t.node_id','t.collab_ord','t.isNeedCollab','t.signtype','t.nodecnt','t.curr_mans'];
  function project(row){return {...Object.fromEntries(fields.map(key=>[key,row[key]])),amount_cents:ReimbursementHttpValues.moneyCents(row['t.amount']).toString()};}
  function equal(a,b){return JSON.stringify(ReimbursementNativeForm.canonical(project(a)))===JSON.stringify(ReimbursementNativeForm.canonical(project(b)));}
  function historyCount(rows,who,node){return rows.filter(r=>r['t.node_id']===node&&r['t.operator']===who).length;}
  function headerMatches(rows,target){const current=rows.filter(row=>row['t.serial_no']===target['t.serial_no']&&row['t.uni_key']===target['t.uni_key']&&row['t.wfname']===target['t.wfname']);return current.length>0&&current.every(row=>row['t.remark']===target['t.remark']&&ReimbursementHttpValues.moneyCents(row['t.amount'])===ReimbursementHttpValues.moneyCents(target['t.amount']));}
  return {project,equal,historyCount,headerMatches};
})();

async function reimbursementApproverBuild(c){
  const target=c.target,who=c.expected_userid,requests=[];
  if(!target||!/^\d+$/.test(target['t.serial_no'])||!/^\d+$/.test(target['t.uni_key'])||!/^\d+$/.test(target['t.node_id'])||
     target['t.wfname']!=='YBX_LC'||target['t.wf_name']!=='YBX_LC'||!['agree','reject'].includes(c.decision)||
     typeof c.opinion!=='string'||!c.opinion.trim()||c.opinion.length>2000)throw Error('APPROVER_INPUT_SCOPE');
  const serial=target['t.serial_no'],node=target['t.node_id'];
  const http=createReimbursementHttp(who,{prefix:'APPROVER_API',requests,timeout:15000,
    allowed:['definition','binding','query','button','applicantVerificationStatus','applicantCheck','applicantSeal','workflowSecretary','workflowCollab','printDocument','workflowRefresh']});
  const hash=async value=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value)))].map(n=>n.toString(16).padStart(2,'0')).join('');
  const $=window.jQuery;
  for(const[name,source]of Object.entries({verification_guard:window.before_sp_yzm,agreement_guard:window.before_lc_agree,
      rejection_guard:window.before_lc_reject,generate_command:$.page?.btn?.generateCmd,button_engine:$.button?.btnClickFunc,
      signature_model:window.getLcSignModel,pdf_compensation:window.lcpdf_blnew})){
    if(await hash(String(source))!==c.contract[name+'_sha256'])throw Error('APPROVER_HANDLER_CHANGED:'+name);
  }
  let button;
  for(const [winno,key]of [['74','list_definition_sha256'],['76','approval_definition_sha256']]){
    const d=await http.post('loadDefinition.action?type=W',{winno,projid:'WF_YBX'});
    const core=Object.fromEntries(['wins','funcs','funcMap','actMap'].map(k=>[k,d[k]]));
    if(await hash(JSON.stringify(ReimbursementNativeForm.canonical(core)))!==c.contract[key])throw Error('APPROVER_DEFINITION_CHANGED:'+winno);
    if(winno==='76'){
      const expected=c.contract.buttons[c.decision];button=d.wins.find(w=>String(w.winno)==='76')?.func_btns.find(b=>b.btnguid===expected.btnguid);
      if(!button||await hash(String(button.beforeclick))!==expected.before_sha256||await hash(String(button.afterclick))!==expected.after_sha256)throw Error('APPROVER_BUTTON_HOOK_CHANGED');
    }
  }
  const queue=await reimbursementBusinessApi({case_id:c.case_id,expected_userid:who,action:'approvals-list',approval_serial:serial,contracts:c.queue_contracts});
  const targetReference={serial,workflow:target['t.wfname'],instance:target['t.uni_key'],source_rows:[target],source:c.source};
  const log=await reimbursementBusinessApi({case_id:c.case_id,expected_userid:who,action:'approval-log',approval_target:targetReference,
    contracts:c.log_contracts,approval_history_contracts:c.history_contracts});
  if(!queue.complete||!log.complete)throw Error('APPROVER_INCOMPLETE_READ');
  const matches=queue.rows.filter(row=>row['t.serial_no']===serial&&row['t.uni_key']===target['t.uni_key']&&row['t.node_id']===node);
  const doneCount=ApproverBinding.historyCount(log.history,who,node);
  const beforeCount=c.baseline?.completed_count;
  const matchingComments=log.history.filter(r=>r['t.node_id']===node&&r['t.operator']===who&&r['t.op_comment']===c.opinion);
  const beforeComments=c.baseline?.matching_comment_count;
  const complete=matches.length===0&&Number.isSafeInteger(beforeCount)&&Number.isSafeInteger(beforeComments)&&doneCount===beforeCount+1&&matchingComments.length===beforeComments+1&&ApproverBinding.headerMatches(log.approved_rows,target);
  const baseRequests=queue.requests.concat(log.requests);
  const report={ok:true,complete:true,action:'approver-http-inspect',case_id:c.case_id,serial,expected_userid:who,node_id:node,
    decision:c.decision,opinion:c.opinion,business_transport:'native_http',system_write:false,approval_dispatched:false,
    completed:complete,state:complete?'approval_step_verified':matches.length?'pending':'no_longer_pending_needs_review',
    completed_count:doneCount,matching_comment_count:matchingComments.length,original:{target,pending_rows:matches,history:log.history,approved_rows:log.approved_rows},
    requests:baseRequests.concat(requests)};
  if(complete||c.readback===true||matches.length===0)return {report,http,requests,base_requests:baseRequests,button};
  if(matches.length!==1||!ApproverBinding.equal(matches[0],target))throw Error('APPROVER_ROLE_AMOUNT_OR_SUMMARY_CHANGED');
  const row=matches[0];
  if(row['t.collab_ord']!=='none'||row['t.isNeedCollab']!=='F'||row['t.nodecnt']!=='1'||!['','no'].includes(row['t.signtype'])||
     !String(row['t.curr_mans']).startsWith(who+' - '))throw Error('APPROVER_SPECIAL_ROLE_OR_SIGNATURE_REQUIRES_HANDOFF');
  if(log.history.filter(r=>r['t.node_id']===node&&!r['t.operator']).length!==1)throw Error('APPROVER_PENDING_NODE_NOT_UNIQUE');
  // Applicant confirmation must still belong to this applicant, even in a mixed batch.
  if(node==='508'){
    const applicant=await reimbursementBusinessApi({case_id:c.case_id,expected_userid:who,action:'bill-read',domain:'submission',serial,
      expected_amount:row['t.amount'].replaceAll(',',''),contracts:c.submission_contracts});
    if(!applicant.complete||applicant.bill['t.uni_key']!==row['t.uni_key']||applicant.bill['t.node_id']!==node||applicant.bill['t.abst']!==row['t.remark'])throw Error('APPROVER_APPLICANT_BINDING');
    baseRequests.push(...applicant.requests);
  }
  const {data:context,set,get}=http.contextCopy();
  for(const key of Object.keys(context))if(!key.startsWith('0-')||/CODE|CAPTCHA|OTP/.test(key))delete context[key];
  if(String(get('0-MANUAL_NODE')||'').split(',').includes(node)||get('0-is_jbrfzrbmfzr')==='Y')throw Error('APPROVER_NATIVE_FORWARDING_REQUIRED');
  if(c.decision==='agree'&&get('0-is_argee_default')==='Y')throw Error('APPROVER_NATIVE_DEFAULT_OPINION_REQUIRES_REVIEW');
  if(get('0-sys_wf_pdf_seq'))throw Error('APPROVER_OTHER_SIGNATURE_CONTEXT');
  for(const[key,value]of Object.entries({'61-serialno':serial,'61-spinfo_serialno':serial,'61-node_id':node,'61-wf_name':row['t.wfname'],
    '61-uni_key':row['t.uni_key'],'109-t.remark':c.opinion,'109-wf_inst.basetable_key':serial,'109-wf_inst.node_id':node,
    '109-wf_inst.wf_name':row['t.wfname'],'109-wf_inst.uni_key':row['t.uni_key']}))set(key,value);
  if(c.decision==='reject'){
    const fields=await reimbursementBusinessApi({case_id:c.case_id,expected_userid:who,action:'approval-context',approval_target:targetReference,
      contracts:c.context_contracts,approval_history_contracts:c.history_contracts});
    if(fields.approval_fields?.length!==1||!Object.hasOwn(fields.approval_fields[0],'FJ')||fields.approval_fields[0].FJ!==null&&typeof fields.approval_fields[0].FJ!=='string')throw Error('APPROVER_REJECTION_ATTACHMENT_BINDING');
    set('109-t.fj',fields.approval_fields[0].FJ??'');baseRequests.push(...fields.requests);
  }
  const sms=await http.post('activation_ifsend.action',{userid:who});
  if(!['yyz','wyz'].includes(sms?.status))throw Error('APPROVER_VERIFICATION_UNKNOWN');
  report.verification_required=sms.status!=='yyz';report.state=report.verification_required?'verification_required':'ready';
  report.pdf_compensation=get('0-lcpdf_isbl')==='Y';report.original.binding=ApproverBinding.project(row);
  report.requests=baseRequests.concat(requests);
  return {report,http,context,set,get,button,requests,base_requests:baseRequests};
}

async function reimbursementApproverInspect(c){
  // The shared data-only read cache omits native function properties. This owner
  // pins before/after hooks, so definitions must retain their live function values.
  return (await reimbursementApproverBuild(c)).report;
}

async function reimbursementApproverDecide(c){
  let dispatched=false,mutated=false;const steps=[],started=performance.now();
  const serial=c.target?.['t.serial_no'];
  const result=extra=>({action:'approver-http-decide',case_id:c.case_id,serial,expected_userid:c.expected_userid,
    node_id:c.target?.['t.node_id'],decision:c.decision,opinion:c.opinion,business_transport:'native_http',
    approval_dispatched:dispatched,save_dispatched:mutated,system_write:mutated,sms_sent:false,steps,elapsed_ms:Math.round(performance.now()-started),...extra});
  try{
    if(c.pending_registered!==true||c.authorized!==true||!c.prepared)throw Error('APPROVER_EXPLICIT_AUTHORITY_REQUIRED');
    const b=await reimbursementApproverBuild(c);
    if(b.report.completed)return result({ok:true,state:'already_completed_readback_required'});
    if(b.report.verification_required)return result({ok:true,state:'verification_required'});
    if(b.report.state!=='ready')return result({ok:false,state:'original_scope_needs_review',observed_state:b.report.state});
    if(b.report.completed_count!==c.prepared.completed_count||JSON.stringify(b.report.original.binding)!==JSON.stringify(c.prepared.original.binding))throw Error('APPROVER_PREPARE_DRIFT');
    const node=c.target['t.node_id'],key=c.target['t.uni_key'],who=c.expected_userid,opinion=c.opinion;
    if(c.decision==='agree'){
      if(b.report.pdf_compensation){
        mutated=true;const raw=await b.http.post('ybxPrint_yb4PrintHtml.action',{printModel:'P',printType:'流程PDF补录',serialno:serial,byear:'',dbserver:'',bxId:'',appName:'YBX',bxOrderid:'',sign:'',jsonData:'YB!P!NEW!P!'+serial+'!P!流程PDF补录!P!T'},'text');
        steps.push({phase:'pdf_compensation',accepted:typeof raw==='string'&&!raw.startsWith('error')});if(typeof raw!=='string'||raw.startsWith('error'))throw Error('APPROVER_PDF_COMPENSATION_FAILED');
      }
      const check=await b.http.post('MainOperateAction_approveCheck.action',{remark:opinion,wf_name:'YBX_LC',uni_key:key,node_id:node,userid:who});
      steps.push({phase:'native_check',response:check});if(check?.status!=='success')throw Error('APPROVER_NATIVE_CHECK_REQUIRES_HANDOFF');
      mutated=true;const seal=await b.http.post('MainOperateAction_approveSealReq.action',{remark:opinion,wf_name:'YBX_LC',uni_key:key,node_id:node,userid:who,serial_no:serial,username:b.get('0-userinfo.username'),xtflag:'N'});
      steps.push({phase:'seal_check',response:seal});if(seal?.status!=='sealPass')throw Error('APPROVER_HUMAN_SEAL_REQUIRED');
    }
    const secretary=await b.http.post('workflow_getSecretaryDef.action',{projId:'WF_YBX',wfName:'YBX_LC',unikey:key,nodeId:node});
    if(secretary?.code!=='success'||!Array.isArray(secretary.data)||secretary.data.length)throw Error('APPROVER_SECRETARY_REQUIRED');
    const collab=await b.http.post('wfNew_getCollabStatus.action',{wfName:'YBX_LC',wfAct:'APPROVAL',wfNodeId:node,wfUniKey:key,projid:'WF_YBX'},'text');
    if(collab!=='ok')throw Error('APPROVER_COLLAB_REQUIRED');
    if((await b.http.post('activation_ifsend.action',{userid:who}))?.status!=='yyz')return result({ok:true,state:'verification_required'});
    const workflow={wfName:'YBX_LC',wfAct:'APPROVAL',wfNodeId:node,wfState:c.decision,wfUniKey:key,wfVars:':[]',wfKeyVal:serial,wfComment:opinion,wfCollab:'none',wfSecretaryRoleGuid:'',wfPdf:''};
    const command=ReimbursementNativeProtocol.command(b.button.check_func,b.button.procs,b.context,{},workflow);
    mutated=true;dispatched=true;
    const raw=await b.http.post('commonUpdate_responseButtonEvent.action',{cmd:command,projid:'WF_YBX',userContext:JSON.stringify(b.context),async:false},'text');
    return result({ok:true,state:'submitted_independent_readback_required',native_response:raw,requests:b.base_requests.concat(b.requests)});
  }catch(error){return result({ok:false,state:dispatched?'unknown_original_recovery_required':mutated?'native_check_readback_required':'rejected_before_dispatch',error:String(error.message)});}
}

// 补齐原生审批后的文档生成与缓存刷新，独立于审批命令；失败只续此步。
async function reimbursementApproverFinalize(c){
  if(c.authorized!==true)throw Error('APPROVER_FINALIZE_AUTHORITY');
  const b=await reimbursementApproverBuild({...c,readback:true});
  if(b.report.completed!==true)throw Error('APPROVER_FINALIZE_REQUIRES_ORIGINAL_COMPLETED_LOG');
  const serial=c.target['t.serial_no'],documents=[];
  const hash=async s=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s)))].map(n=>n.toString(16).padStart(2,'0')).join('');
  if(c.decision==='agree'){
    for(const name of ['after_lc_agree','after_lc_agree_bxpdf']){
      if(await hash(String(window[name]))!==c.contract[name+'_sha256'])throw Error('APPROVER_DOCUMENT_HANDLER_CHANGED');
    }
    for(const type of ['打印首次','报销单列表','财务报销单打印','条码审核单']){
      const data=type==='打印首次'?'YB!P!NEW!P!'+serial+'!P!打印首次':type==='条码审核单'?'YB!P!条码审核单!P!'+serial+'!P!条码审核单':'YB!P!NEW!P!'+serial+'!P!报销单列表!P!'+c.expected_userid;
      const raw=await b.http.post('ybxPrint_yb4PrintHtml.action',{printModel:'P',printType:type,serialno:serial,byear:'',dbserver:'',bxId:'',appName:'YBX',bxOrderid:'',sign:'',jsonData:data},'text');
      if(typeof raw!=='string'||!raw.trim()||/^error/i.test(raw))throw Error('APPROVER_DOCUMENT_NOT_READY:'+type);
      documents.push({type,response_sha256:await hash(raw),response_length:raw.length});
    }
  }
  const refreshed=await b.http.post('MainOperateAction_updateLcRedis.action',{serialnos:serial},'text');
  if(typeof refreshed!=='string'||/^error/i.test(refreshed))throw Error('APPROVER_CACHE_REFRESH_UNKNOWN');
  return {ok:true,action:'approver-http-finalize',case_id:c.case_id,serial,node_id:c.target['t.node_id'],expected_userid:c.expected_userid,
    decision:c.decision,opinion:c.opinion,business_transport:'native_http',approval_dispatched:false,
    system_write:true,documents,cache_refreshed:true,whole_claim_completed:false,requests:b.base_requests.concat(b.requests)};
}
