/* A native, visible handoff for the person. Never sends/reads an SMS code. */
async function reimbursementApproverVerification(c){
  if(c.authorized!==true)throw Error('APPROVER_VERIFICATION_AUTHORITY');
  const b=await reimbursementApproverBuild(c),serial=c.target['t.serial_no'],node=c.target['t.node_id'];
  const scope={case_id:c.case_id,serial,node_id:node,expected_userid:c.expected_userid,decision:c.decision,opinion:c.opinion};
  if(b.report.completed)return{ok:true,state:'already_processed',...scope,system_write:false,approval_dispatched:false,code_read:false};
  if(b.report.state==='ready')return{ok:true,state:'already_verified',...scope,system_write:false,approval_dispatched:false,code_read:false};
  if(b.report.state!=='verification_required')throw Error('APPROVER_VERIFICATION_ORIGINAL_CHANGED');
  const $=window.jQuery,visible=e=>!!e?.getClientRects().length;
  const hash=async s=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s)))].map(n=>n.toString(16).padStart(2,'0')).join('');
  for(const[fn,key]of [[window.yzm_jy_1362,'verification_callback_sha256'],[$.page.openExternal,'page_open_external_sha256'],[$.UC.setData,'context_set_data_sha256']]){
    if(await hash(String(fn))!==c.contract[key])throw Error('APPROVER_VERIFICATION_HANDLER_CHANGED');
  }
  const definition=await b.http.post('loadDefinition.action?type=W',{winno:'1160',projid:'WF_YBX'});
  const core=Object.fromEntries(['wins','funcs','funcMap','actMap'].map(k=>[k,definition[k]]));
  if(await hash(JSON.stringify(ReimbursementNativeForm.canonical(core)))!==c.contract.verification_definition_sha256)throw Error('APPROVER_VERIFICATION_WINDOW_CHANGED');
  const caption=c.decision==='agree'?'同意':'拒绝',code=document.getElementById('formWF_YBX_1362_t-code');
  const same=()=>$.UC.bindData('#61-serialno#')===serial&&String($.UC.bindData('#61-node_id#'))===node;
  const report=()=>({ok:true,state:'verification_required',case_id:c.case_id,serial,node_id:node,expected_userid:c.expected_userid,
    decision:c.decision,opinion:c.opinion,business_transport:'native_http',human_interface:'school_native_renderer',
    verification_window_visible:visible(document.getElementById('formWF_YBX_1362_t-code')),
    system_write:false,approval_dispatched:false,sms_sent:false,code_read:false,native_user_confirmation_may_continue_this_item:true});
  if(visible(code)){
    const remark=document.getElementById('formWF_YBX_109_t-remark');
    if(!same()||$.UC.bindData('#0-YZM_CAPTION#')!==caption||!remark||remark.value!==c.opinion)throw Error('APPROVER_DIFFERENT_VERIFICATION_ACTIVE');
    return report();
  }
  if(Number($.active||0)!==0||[...document.querySelectorAll('.ui-dialog')].some(visible)||[...document.querySelectorAll('input[type="file"]')].some(e=>e.files?.length))throw Error('APPROVER_EXISTING_WORK_PRESERVED');
  const editors=[...document.querySelectorAll('input,select,textarea')].filter(e=>visible(e)&&!e.disabled&&!e.readOnly&&e.type!=='hidden');
  const page=String($.page.currPage),listReady=page==='70'&&visible(document.getElementById('gridWF_YBX_101'))||page==='74'&&visible(document.getElementById('gridWF_YBX_105'));
  const listControl=e=>/^formWF_YBX_(?:99|514)_/.test(e.id)||/^(?:cb_gridWF_YBX_(?:101|105)|jqg_gridWF_YBX_(?:101_101|105_105)_\d+)$/.test(e.id)||!(e.closest('form')?.id)&&(e.type==='radio'||!!e.closest('.ui-pg-table'));
  const sameOriginal=page==='76'&&same(),cleanHome=page==='6'&&editors.length===0;
  const blockers=editors.filter(e=>!(listReady&&listControl(e))&&!(sameOriginal&&e.id==='formWF_YBX_109_t-remark'&&['',c.opinion].includes(e.value)));
  if(!(listReady||sameOriginal||cleanHome)||blockers.length)throw Error('APPROVER_EXISTING_EDITOR_PRESERVED');
  const set=(key,value)=>$.UC.setData('WF_YBX$'+key.toUpperCase(),value);
  const values={'61-serialno':serial,'61-spinfo_serialno':serial,'61-node_id':node,'61-wf_name':'YBX_LC','61-uni_key':c.target['t.uni_key'],
    '61-collab_users':c.target['t.collab_users']||'','61-collab_ord':'none','61-isNeedCollab':'F','105-t.signtype':c.target['t.signtype'],
    '109-wf_inst.basetable_key':serial,'109-wf_inst.node_id':node,'109-wf_inst.wf_name':'YBX_LC','109-wf_inst.uni_key':c.target['t.uni_key'],
    '109-wf_inst.collab_ord':'none','109-wf_inst_user.collab_ord':'none'};
  for(const[key,value]of Object.entries(values))set(key,value);
  const wait=async predicate=>{const started=Date.now();while(Date.now()-started<15000){b.http.guard();if(predicate()&&Number($.active||0)===0)return;await new Promise(r=>setTimeout(r,100));}throw Error('APPROVER_VERIFICATION_VIEW_PENDING');};
  if(!sameOriginal)$.page.openExternal('WF_YBX','76','本人核对审批意见');
  await wait(()=>visible(document.getElementById('formWF_YBX_109_t-remark')));
  if(!same())throw Error('APPROVER_OPENED_ROLE_CHANGED');
  // This is a visible, authorized handoff draft. It is not a hidden approval:
  // the person sees the opinion and completes the school's native verification.
  const remark=document.getElementById('formWF_YBX_109_t-remark');
  if(sameOriginal&&!['',c.opinion].includes(remark.value))throw Error('APPROVER_EXISTING_OPINION_PRESERVED');
  remark.value=c.opinion;set('109-t.remark',c.opinion);
  $.UC.setData('0-YZM_CAPTION',caption);
  b.http.guard();$.page.openExternal('WF_YBX','1160','本人短信验证');
  await wait(()=>visible(document.getElementById('formWF_YBX_1362_t-code')));
  return report();
}
