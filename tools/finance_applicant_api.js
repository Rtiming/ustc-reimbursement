/* Existing applicant node 508 only. Fixed HTTP; no UI, code entry or other-role approval. */
async function reimbursementApplicantBuild(c,readback=false){
  const requests=[],http=createReimbursementHttp(c.expected_userid,{prefix:'APPLICANT_API',requests,timeout:15000,allowed:['definition','query','button','applicantVerificationStatus','applicantCheck','applicantSeal','workflowSecretary','workflowCollab','workflowRefresh','printDocument']});
  const hash=async s=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s)))].map(n=>n.toString(16).padStart(2,'0')).join('');
  const $=window.jQuery,contract=c.applicant_contract;
  for(const[name,value]of [['verification_guard',window.before_sp_yzm],['agreement_guard',window.before_lc_agree],['generate_command',$.page?.btn?.generateCmd],['open_sign',$.openSign],['pdf_compensation',window.lcpdf_blnew],['button_engine',$.button?.btnClickFunc]])
    if(await hash(String(value))!==contract[name+'_sha256'])throw Error('APPLICANT_HANDLER_CHANGED:'+name);
  for(const[winno,key]of [['76','applicant_definition_sha256'],['74','list_definition_sha256']]){
    const d=await http.post('loadDefinition.action?type=W',{winno,projid:'WF_YBX'});
    const core=Object.fromEntries(['wins','funcs','funcMap','actMap'].map(k=>[k,d[k]]));
    if(await hash(JSON.stringify(ReimbursementNativeForm.canonical(core)))!==contract[key])throw Error('APPLICANT_DEFINITION_CHANGED:'+winno);
    if(winno==='74')c={...c,list_definition:d};
  }
  const observed=await reimbursementBusinessApi({...c,action:'bill-read',domain:'submission',contracts:c.submission_contracts});
  const bill=observed.bill,history=observed.sections.find(s=>s.funcno==='1816');
  if(!observed.complete||history?.complete!==true||bill['t.abst']!==c.plan.summary||bill['t.basetable_key']!==c.serial||bill['t.wf_name']!=='YBX_LC'||!/^\d+$/.test(bill['t.uni_key']))throw Error('APPLICANT_ORIGINAL_CHANGED');
  const applicantHistory=history.rows.filter(r=>r['t.node_id']==='508'&&r['t.node_name']==='预约流程-申请人');
  const completed=applicantHistory.length===1&&applicantHistory[0]['t.operator']===c.expected_userid&&applicantHistory[0]['t.op_comment']==='同意';
  let workflowNode=bill['t.node_id'];
  if(completed){
    const pending=history.rows.filter(r=>!r['t.operator']&&r['t.appinfo']&&r['t.appinfo']!=='-');
    if(pending.some(r=>r['t.node_id']==='508')||pending.length!==1&&bill['t.lc_finish']!=='T')throw Error('APPLICANT_NEXT_NODE_NOT_PROVEN');
    if(pending.length===1)workflowNode=pending[0]['t.node_id'];
  }else if(bill['t.node_id']!=='508'||applicantHistory.length!==1||applicantHistory[0]['t.operator']||!applicantHistory[0]['t.appinfo'].startsWith(c.expected_userid+'-'))throw Error('APPLICANT_ROLE_CHANGED');
  const report={ok:true,complete:true,action:'applicant-http-inspect',case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,business_transport:'native_http',system_write:false,save_dispatched:false,completed,original:{bill,history:history.rows,workflow_node_id:workflowNode,main_list_node_stale:workflowNode!==bill['t.node_id']},requests:observed.requests.concat(requests)};
  if(completed||readback)return{report,http};
  const{data:context,set,get}=http.contextCopy();
  // Only global school configuration and this original's explicit workflow context.
  for(const key of Object.keys(context))if(!key.startsWith('0-')||/CODE|CAPTCHA|OTP/.test(key))delete context[key];
  const grid=c.list_definition.funcs.find(f=>String(f.funcno)==='105');
  if(!grid?.tablenames.startsWith('@{ybx_query_pc_package.wfplc_query_approval_v1:'))throw Error('APPLICANT_LIST_CHANGED');
  for(const m of grid.tablenames.matchAll(/#514-([^#]+)#/g))set('514-'+m[1],'');
  set('514-t.serial_no',c.serial);set('105-COND','');set('105-CONDOBJ',[]);set('105-CURRPAGE',1);
  const data=await http.post('commonQuery_doQuery.action?funcno=105&projid=WF_YBX&needcount=true',{_search:false,nd:Date.now(),page:1,rows:'100',sidx:'',sord:'',userData:JSON.stringify(context),itemArr:'',sectionArr:''});
  if(Number(data?.records)!==1||Number(data.total)!==1||Number(data.page)!==1||data.rows?.length!==1||data.rows[0].cell?.length!==grid.colModel.length)throw Error('APPLICANT_CURRENT_TASK_NOT_UNIQUE');
  const row=Object.fromEntries(grid.colModel.map((col,i)=>[col.name,ReimbursementHttpValues.decode(data.rows[0].cell[i])]));
  if(row['t.serial_no']!==c.serial||row['t.basetable_key']!==c.serial||row['t.node_id']!=='508'||row['t.wfname']!=='YBX_LC'||row['t.wf_name']!=='YBX_LC'||row['t.uni_key']!==bill['t.uni_key']||row['t.nodecnt']!=='1'||row['t.collab_ord']!=='none'||row['t.isNeedCollab']!=='F'||!['','no'].includes(row['t.signtype'])||!row['t.curr_mans'].startsWith(c.expected_userid+' - ')||!row['t.node_name1'].startsWith('申请人/审批('+c.expected_userid+' - ')||row['t.remark']!==c.plan.summary||ReimbursementHttpValues.moneyCents(row['t.amount'])!==ReimbursementHttpValues.moneyCents(c.expected_amount))throw Error('APPLICANT_CURRENT_ROLE_AMOUNT_OR_SIGNATURE_CHANGED');
  for(const[k,v]of Object.entries({'61-serialno':c.serial,'61-spinfo_serialno':c.serial,'61-node_id':'508','61-wf_name':'YBX_LC','61-uni_key':bill['t.uni_key'],'109-t.remark':'同意','109-wf_inst.basetable_key':c.serial,'109-wf_inst.node_id':'508','109-wf_inst.wf_name':'YBX_LC','109-wf_inst.uni_key':bill['t.uni_key']}))set(k,v);
  const verification=await http.post('activation_ifsend.action',{userid:c.expected_userid});
  if(!['yyz','wyz'].includes(verification?.status))throw Error('APPLICANT_VERIFICATION_UNKNOWN');
  if(get('0-sys_wf_pdf_seq'))throw Error('APPLICANT_OTHER_SIGNATURE_CONTEXT');
  report.original.applicant=row;report.verification_required=verification.status!=='yyz';report.state=report.verification_required?'verification_required':'ready';
  report.pdf_compensation=get('0-lcpdf_isbl')==='Y';report.requests=observed.requests.concat(requests);
  return{report,http,context,get,set,requests,base_requests:observed.requests};
}
async function reimbursementApplicantInspect(c){return ReimbursementHttpReadScope.run(c.expected_userid,async()=>(await reimbursementApplicantBuild(c,c.readback===true)).report);}
async function reimbursementApplicantAgree(c){
  let approval=false,mutated=false;const steps=[],started=performance.now();
  const result=(extra)=>({case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,action:'applicant-http-agree',business_transport:'native_http',approval_dispatched:approval,save_dispatched:mutated,system_write:mutated,sms_sent:false,steps,elapsed_ms:Math.round(performance.now()-started),...extra});
  try{
    if(c.pending_registered!==true||c.plan.applicant_agreement_authorized!==true||c.plan.approval_opinion!=='同意')throw Error('APPLICANT_EXPLICIT_AUTHORITY_REQUIRED');
    const b=await ReimbursementHttpReadScope.run(c.expected_userid,()=>reimbursementApplicantBuild(c));
    if(b.report.completed)return result({ok:true,state:'already_submitted_readback_required'});
    if(b.report.verification_required)return result({ok:true,state:'verification_required'});
    if(JSON.stringify(ReimbursementNativeForm.canonical(b.report.original))!==JSON.stringify(ReimbursementNativeForm.canonical(c.prepared.original)))throw Error('APPLICANT_PREPARE_DRIFT');
    const key=b.report.original.bill['t.uni_key'],who=c.expected_userid;
    if(b.report.pdf_compensation){mutated=true;const p=await b.http.post('ybxPrint_yb4PrintHtml.action',{printModel:'P',printType:'流程PDF补录',serialno:c.serial,byear:'',dbserver:'',bxId:'',appName:'YBX',bxOrderid:'',sign:'',jsonData:'YB!P!NEW!P!'+c.serial+'!P!流程PDF补录!P!T'},'text');steps.push({phase:'pdf_compensation',accepted:typeof p==='string'&&!p.startsWith('error')});if(typeof p!=='string'||p.startsWith('error'))throw Error('APPLICANT_PDF_COMPENSATION_FAILED');}
    const check=await b.http.post('MainOperateAction_approveCheck.action',{remark:'同意',wf_name:'YBX_LC',uni_key:key,node_id:'508',userid:who});steps.push({phase:'approval_check',response:check});if(check?.status!=='success')throw Error('APPLICANT_NATIVE_CHECK_REQUIRED');
    mutated=true;const seal=await b.http.post('MainOperateAction_approveSealReq.action',{remark:'同意',wf_name:'YBX_LC',uni_key:key,node_id:'508',userid:who,serial_no:c.serial,username:b.get('0-userinfo.username'),xtflag:'N'});steps.push({phase:'seal_check',response:seal});if(seal?.status!=='sealPass')throw Error('APPLICANT_HUMAN_SEAL_REQUIRED');
    const secretary=await b.http.post('workflow_getSecretaryDef.action',{projId:'WF_YBX',wfName:'YBX_LC',unikey:key,nodeId:'508'});if(secretary?.code!=='success'||!Array.isArray(secretary.data)||secretary.data.length)throw Error('APPLICANT_SECRETARY_REQUIRED');
    const collab=await b.http.post('wfNew_getCollabStatus.action',{wfName:'YBX_LC',wfAct:'APPROVAL',wfNodeId:'508',wfUniKey:key,projid:'WF_YBX'},'text');if(collab!=='ok')throw Error('APPLICANT_COLLAB_REQUIRED');
    // Re-check the school SMS gate immediately before this one non-retried dispatch.
    if((await b.http.post('activation_ifsend.action',{userid:who}))?.status!=='yyz')return result({ok:true,state:'verification_required'});
    const wf={wfName:'YBX_LC',wfAct:'APPROVAL',wfNodeId:'508',wfState:'agree',wfUniKey:key,wfVars:':[]',wfKeyVal:c.serial,wfComment:'同意',wfCollab:'none',wfSecretaryRoleGuid:'',wfPdf:''};
    const button=c.applicant_contract.button,cmd=ReimbursementNativeProtocol.command(button.check_func,button.procs,b.context,{},wf);
    approval=true;const raw=await b.http.post('commonUpdate_responseButtonEvent.action',{cmd,projid:'WF_YBX',userContext:JSON.stringify(b.context),async:false},'text');
    // Native W76 afterclick regenerates the four workflow documents.
    const documents=await reimbursementApplicantDocuments(c);
    return result({ok:true,state:'submitted_independent_readback_required',native_response:raw,documents,requests:b.base_requests.concat(b.requests)});
  }catch(error){return result({ok:false,state:approval?'unknown_original_recovery_required':mutated?'preapproval_check_requires_readback':'rejected_before_dispatch',error:String(error.message)});}
}
async function reimbursementApplicantRefresh(c){
  const b=await reimbursementBusinessApi({...c,action:'bill-read',domain:'submission',contracts:c.submission_contracts});
  const log=b.sections.find(s=>s.funcno==='1816');
  if(!b.complete||log?.complete!==true||log.rows.filter(r=>r['t.node_id']==='508'&&r['t.operator']===c.expected_userid&&r['t.op_comment']==='同意').length!==1)throw Error('APPLICANT_REFRESH_REQUIRES_COMPLETED_ORIGINAL_LOG');
  const requests=[],h=createReimbursementHttp(c.expected_userid,{prefix:'APPLICANT_REFRESH',requests,timeout:15000,allowed:['workflowRefresh']});
  const raw=await h.post('MainOperateAction_updateLcRedis.action',{serialnos:c.serial},'text');
  return{ok:true,action:'applicant-cache-refreshed',case_id:c.case_id,serial:c.serial,approval_dispatched:false,system_write:false,cache_refreshed:true,native_response:raw,requests};
}
async function reimbursementApplicantDocuments(c){
  const b=await reimbursementBusinessApi({...c,action:'bill-read',domain:'submission',contracts:c.submission_contracts});
  const log=b.sections.find(s=>s.funcno==='1816');
  if(!b.complete||log?.complete!==true||log.rows.filter(r=>r['t.node_id']==='508'&&r['t.operator']===c.expected_userid&&r['t.op_comment']==='同意').length!==1)throw Error('APPLICANT_DOCUMENTS_REQUIRE_COMPLETED_ORIGINAL_LOG');
  const hash=async s=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s)))].map(n=>n.toString(16).padStart(2,'0')).join('');
  for(const name of ['after_lc_agree','after_lc_agree_bxpdf'])if(await hash(String(window[name]))!==c.applicant_contract[name+'_sha256'])throw Error('APPLICANT_DOCUMENT_HANDLER_CHANGED');
  const requests=[],h=createReimbursementHttp(c.expected_userid,{prefix:'APPLICANT_DOCUMENTS',requests,timeout:30000,allowed:['printDocument']}),documents=[];
  for(const type of ['打印首次','报销单列表','财务报销单打印','条码审核单']){
    const data=type==='打印首次'?'YB!P!NEW!P!'+c.serial+'!P!打印首次':type==='条码审核单'?'YB!P!条码审核单!P!'+c.serial+'!P!条码审核单':'YB!P!NEW!P!'+c.serial+'!P!报销单列表!P!'+c.expected_userid;
    const raw=await h.post('ybxPrint_yb4PrintHtml.action',{printModel:'P',printType:type,serialno:c.serial,byear:'',dbserver:'',bxId:'',appName:'YBX',bxOrderid:'',sign:'',jsonData:data},'text');
    if(typeof raw!=='string')throw Error('APPLICANT_DOCUMENT_RESPONSE_UNKNOWN:'+type);
    documents.push({type,status:/^error/i.test(raw)?'not_ready':'generated',...(/^error/i.test(raw)?{message:raw.slice(0,500)}:{}),response_sha256:await hash(raw),response_length:raw.length});
  }
  return{ok:true,action:'applicant-documents-generated',case_id:c.case_id,serial:c.serial,approval_dispatched:false,system_write:false,documents,requests};
}
