/* 项目预审人由本单预览参数槽绑定；只修改声明的人员，保留其余全部槽。 */
async function reimbursementPreReviewBuild(c) {
  if (!Array.isArray(c.pre_review) || c.pre_review.length>4 || c.pre_review.some(p=>
      !/^[A-Za-z0-9]{2,40}$/.test(p.id) || typeof p.name!=='string' || !p.name.trim() || /[\r\n/]/.test(p.name))) throw Error('PRE_REVIEW_API_PARAMETERS');
  const read=await reimbursementBusinessApi({...c,action:'bill-read',domain:'pre_review'});
  const requests=[],http=createReimbursementHttp(c.expected_userid,{prefix:'PRE_REVIEW_API',requests,timeout:15000,allowed:['definition','button']});
  const native=await ReimbursementNativeForm.definition(http,c.pre_review_contract);
  const slots=read.workflow_parameters,original={},changes={};
  if (!Array.isArray(slots) || slots.length<c.pre_review.length) throw Error('PRE_REVIEW_API_PARAMETER_SCOPE');
  for (const slot of slots) {
    const sections=read.sections.filter(s=>s.slot?.index===slot.index && s.funcno==='1297');
    if (sections.length!==1 || sections[0].rows.length!==1) throw Error('PRE_REVIEW_API_BINDING_COUNT');
    const row=sections[0].rows[0];
    if (row.SERIAL_NO!==c.serial || row.LC_PARAMS_NAME!==slot.name || row.PARAM_SHOW_NAME!=='[项目预审人'+(slot.index+1)+']' ||
        ['LC_APPROVER','LC_APPROVER_NAME',...native.form.formFields.map(f=>f.fieldname.slice(2).toUpperCase())].some(k=>
          !Object.hasOwn(row,k) || row[k]!==null && typeof row[k]!=='string')) throw Error('PRE_REVIEW_API_BOUND_IDENTITY');
    original[slot.index]=Object.fromEntries(Object.entries(row).map(([k,v])=>[k,v??'']));
    const p=c.pre_review[slot.index];
    if (p && (row.LC_APPROVER!==p.id || row.LC_APPROVER_NAME!==p.name)) {
      if (slot.source_table!=='QT' || slot.multi_choice!=='N' || Number(slot.count)<=0) throw Error('PRE_REVIEW_API_INPUT_NOT_VISIBLE');
      changes[slot.index]={PARAM_KEY3:p.id+'/'+p.name,LC_APPROVER:p.id,LC_APPROVER_NAME:p.name};
    }
  }
  return {report:{ok:true,complete:true,action:'pre-review-prepare',case_id:c.case_id,serial:c.serial,
    expected_userid:c.expected_userid,business_transport:'native_http',system_write:false,save_dispatched:false,
    page_context_modified:false,bill:read.bill,definition_sha256:native.hash,original,changes,slots,
    requests:[...read.requests,...requests],observed_at:new Date().toISOString()},native,http,requests};
}
async function reimbursementPreReviewPrepare(c) {return (await reimbursementPreReviewBuild(c)).report;}
async function reimbursementPreReviewSave(c) {
  let dispatched=false,prepared=null;
  try {
    if (c.execute!==true || !c.expected_plan) throw Error('PRE_REVIEW_API_WRITE_INTENT');
    const built=await reimbursementPreReviewBuild(c);prepared=built.report;
    if (prepared.bill['t.status']!=='-99' || prepared.bill['t.lc_finish']!=='F') throw Error('PRE_REVIEW_API_DRAFT_REQUIRED');
    for (const key of ['original','changes','slots','definition_sha256'])
      if (JSON.stringify(prepared[key])!==JSON.stringify(c.expected_plan[key])) throw Error('PRE_REVIEW_API_PLAN_DRIFT:'+key);
    for (const slot of prepared.slots) {
      if (!prepared.changes[slot.index]) continue;
      const {data:context,set,get}=built.http.contextCopy();
      set('61-serialno',c.serial);reimbursementPreReviewContext(set,slot);
      const target={...prepared.original[slot.index],...prepared.changes[slot.index]};
      ReimbursementNativeForm.bind(built.native.form,target,set);
      // QT单选且候选数非0时，官方workflow_params_init只显示param_key3输入。
      ReimbursementNativeForm.validate(built.native.form,new Set(['t.param_key3']),get,['t.param_key3']);
      const cmd=ReimbursementNativeProtocol.command(built.native.save.check_func,built.native.save.procs,context,built.native.form.typeMap);
      built.http.guard();dispatched=true;
      const raw=await built.http.post('commonUpdate_responseButtonEvent.action',{cmd,projid:'WF_YBX',userContext:JSON.stringify(context),async:false},'text');
      if (!ReimbursementNativeProtocol.response(raw,[]).accepted) throw Error('PRE_REVIEW_API_SAVE_REJECTED');
    }
    return {...prepared,action:'pre-review-save',state:dispatched?'saved_independent_readback_required':'already_matches_readback_required',
      system_write:dispatched,save_dispatched:dispatched,requires_readback:true};
  } catch (error) {
    return {ok:false,complete:false,action:'pre-review-save',case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,
      business_transport:'native_http',system_write:dispatched,save_dispatched:dispatched,prepared,requires_readback:true,
      state:dispatched?'unknown_save_readback_required':'rejected_before_save',error:String(error.message)};
  }
}
