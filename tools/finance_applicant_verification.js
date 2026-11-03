/* Human SMS handoff only. No automatic opinion, approval, SMS send or code read. */
async function reimbursementApplicantVerification(c) {
  const requests=[],h=createReimbursementHttp(c.expected_userid,{prefix:'APPLICANT_VERIFICATION',requests,timeout:15000,allowed:['definition','applicantVerificationStatus']});
  const hash=async s=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s)))].map(n=>n.toString(16).padStart(2,'0')).join('');
  const visible=e=>!!e?.getClientRects().length,$=window.jQuery;
  if(c.authorized!==true||c.approval_opinion!=='同意')throw Error('APPLICANT_AUTHORITY_REQUIRED');
  for(const [actual,expected]of [[String(window.before_sp_yzm),c.contract.verification_guard_sha256],[String(window.before_lc_agree),c.contract.agreement_guard_sha256],[String(window.yzm_jy_1362),c.contract.verification_callback_sha256],[String($.page.openExternal),c.contract.page_open_external_sha256],[String($.UC.setData),c.contract.context_set_data_sha256]])
    if(await hash(actual)!==expected)throw Error('APPLICANT_NATIVE_HANDLER_CHANGED');
  for(const[winno,key]of [['76','applicant_definition_sha256'],['1160','verification_definition_sha256']]){
    const d=await h.post('loadDefinition.action?type=W',{winno,projid:'WF_YBX'});
    const core=Object.fromEntries(['wins','funcs','funcMap','actMap'].map(k=>[k,d[k]]));
    if(await hash(JSON.stringify(ReimbursementNativeForm.canonical(core)))!==c.contract[key])throw Error('APPLICANT_WINDOW_CHANGED');
  }
  const b=await reimbursementBusinessApi({...c,action:'bill-read',domain:'submission',contracts:c.submission_contracts});
  const bill=b.bill,log=b.sections.find(s=>s.funcno==='1816');
  if(!b.complete||bill['t.serial_no']!==c.serial||ReimbursementHttpValues.moneyCents(bill['t.total_amt'])!==ReimbursementHttpValues.moneyCents(c.expected_amount)||bill['t.node_id']!=='508'||bill['t.wf_name']!=='YBX_LC'||bill['t.abst']!==c.summary||!/^\d+$/.test(bill['t.uni_key'])||log?.complete!==true||
    log.rows.filter(r=>r['t.node_id']==='508'&&r['t.node_name']==='预约流程-申请人'&&String(r['t.appinfo']||'').startsWith(c.expected_userid+'-')&&!r['t.operator']).length!==1)throw Error('APPLICANT_ORIGINAL_ROLE_CHANGED');
  const state=await h.post('activation_ifsend.action',{userid:c.expected_userid});
  if(state?.status==='yyz')return{ok:true,state:'already_verified',case_id:c.case_id,serial:c.serial,system_write:false,approval_dispatched:false,code_requested:false};
  if(state?.status!=='wyz')throw Error('APPLICANT_VERIFICATION_STATUS_UNKNOWN');
  const code=document.getElementById('formWF_YBX_1362_t-code');
  const result=()=>({ok:true,state:'verification_required',case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,business_transport:'native_http',human_interface:'school_native_renderer',system_write:false,approval_dispatched:false,code_requested:false,code_read:false,verification_window_visible:visible(document.getElementById('formWF_YBX_1362_t-code')),requests});
  if(visible(code)){
    if($.UC.bindData('#61-serialno#')!==c.serial||String($.UC.bindData('#61-node_id#'))!=='508')throw Error('APPLICANT_OTHER_VERIFICATION_ACTIVE');
    return result();
  }
  if(Number($.active||0)!==0||[...document.querySelectorAll('.ui-dialog')].some(visible)||[...document.querySelectorAll('input[type="file"]')].some(e=>e.files?.length))throw Error('APPLICANT_EXISTING_WORK_PRESERVED');
  const editors=[...document.querySelectorAll('input,select,textarea')].filter(e=>visible(e)&&!e.disabled&&!e.readOnly&&e.type!=='hidden');
  // W70's query controls, row selectors and pager do not edit a bill.
  const page=String($.page.currPage),listReady=page==='70'&&visible(document.getElementById('gridWF_YBX_101'))||page==='74'&&visible(document.getElementById('gridWF_YBX_105'));
  const listControl=e=>/^formWF_YBX_(?:99|514)_/.test(e.id)||
    /^(?:cb_gridWF_YBX_(?:101|105)|jqg_gridWF_YBX_(?:101_101|105_105)_\d+)$/.test(e.id)||
    !(e.closest('form')?.id)&&(e.type==='radio'||!!e.closest('.ui-pg-table'));
  const sameApplicant=page==='76'&&$.UC.bindData('#61-serialno#')===c.serial&&String($.UC.bindData('#61-node_id#'))==='508';
  const cleanHome=page==='6'&&editors.length===0;
  const blockers=editors.filter(e=>!(listReady&&listControl(e))&&!(sameApplicant&&e.id==='formWF_YBX_109_t-remark'&&['','同意'].includes(e.value))).map(e=>({id:e.id,type:e.type}));
  if(!(listReady||sameApplicant||cleanHome)||blockers.length)throw Error('APPLICANT_EXISTING_EDITOR_PRESERVED:'+JSON.stringify({page,listReady,sameApplicant,cleanHome,blockers}));
  const set=(key,value)=>$.UC.setData('WF_YBX$'+key.toUpperCase(),value);
  for(const[key,value]of Object.entries({'61-serialno':c.serial,'61-spinfo_serialno':c.serial,'61-node_id':'508','61-wf_name':'YBX_LC','61-uni_key':bill['t.uni_key'],'109-wf_inst.basetable_key':c.serial,'109-wf_inst.node_id':'508','109-wf_inst.wf_name':'YBX_LC','109-wf_inst.uni_key':bill['t.uni_key']}))set(key,value);
  h.guard();
  const wait=async predicate=>{const start=Date.now();while(Date.now()-start<15000){h.guard();if(predicate()&&Number($.active||0)===0)return;await new Promise(r=>setTimeout(r,100));}throw Error('APPLICANT_VIEW_NOT_READY_READ_ORIGINAL_ONLY');};
  if(!sameApplicant)$.page.openExternal('WF_YBX','76','申请人确认');
  await wait(()=>visible(document.getElementById('formWF_YBX_109_t-remark')));
  if($.UC.bindData('#61-serialno#')!==c.serial||String($.UC.bindData('#61-node_id#'))!=='508')throw Error('APPLICANT_OPENED_ROLE_CHANGED');
  // The user's native SMS confirmation may continue this exact authorized original.
  $.UC.setData('0-YZM_CAPTION','同意');
  $.page.openExternal('WF_YBX','1160','短信验证码');
  await wait(()=>visible(document.getElementById('formWF_YBX_1362_t-code')));
  return result();
}
