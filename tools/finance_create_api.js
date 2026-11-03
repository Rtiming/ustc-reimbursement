/* 日常建单的固定数据协议；本地选择集、创建请求、独立归属回读分别留证。 */
async function reimbursementCreatePrepare(c) {
  'use strict';
  const requests=[],contract=c.create_contract;
  const billType=c.bill_type||'1';if(!['1','2'].includes(billType))throw Error('CREATE_API_BILL_TYPE');
  const h=createReimbursementHttp(c.expected_userid,{prefix:'CREATE_API',requests,timeout:15000,
    allowed:['definition','binding','formTemplate']});
  const {data:context,set,get}=h.contextCopy();
  const hash=async value=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value)))].map(n=>n.toString(16).padStart(2,'0')).join('');
  const d=await h.post('loadDefinition.action?type=W',{winno:'33',projid:'WF_YBX'});
  const projected=Object.fromEntries(['wins','funcs','funcMap','actMap'].map(k=>[k,d[k]]));
  if(await hash(JSON.stringify(ReimbursementNativeForm.canonical(projected)))!==contract.definition_sha256)throw Error('CREATE_API_DEFINITION_CHANGED');
  const form=d.funcs.find(f=>String(f.funcno)==='45'),win=d.wins.find(w=>String(w.winno)==='39');
  if(form?.bindSql!==contract.bind_sql||form.formhtml!=='@'+contract.template_filename||
      JSON.stringify(d.wins.find(w=>String(w.winno)==='33')?.beforeLoadProcs)!==JSON.stringify(contract.view_init))throw Error('CREATE_API_FORM_CONTRACT');
  for(const name of ['guard_button','create_button']){
    const found=win?.func_btns.filter(b=>b.btnguid===contract[name].btnguid);
    if(found?.length!==1||JSON.stringify(ReimbursementNativeForm.canonical(found[0]))!==JSON.stringify(ReimbursementNativeForm.canonical(contract[name])))throw Error('CREATE_API_BUTTON_CHANGED');
  }
  for(const [name,expected]of Object.entries(contract.callbacks)){
    if(typeof window[name]!=='function'||await hash(String(window[name]))!==expected)throw Error('CREATE_API_CALLBACK_CHANGED:'+name);
  }
  const template=await h.post('common_getCustomFormFileContent.action',{filename:contract.template_filename},'text');
  if(await hash(template)!==contract.template_sha256)throw Error('CREATE_API_TEMPLATE_CHANGED');
  // The normal daily entry has no foreign/procurement/preapproval association.
  // These are the same data fields cleared by the pinned native entry, never UC mutations.
  for(const [key,value]of Object.entries({'61-bxxginfo_serialno':'','61-BX_JBXX_BX_TYPE':billType,
      '0-g0Serialno':'','61-serialno':'','61-trip_ids':'','61-BX_JKINFO':'','0-tk_sx_id':''}))set(key,value);
  const [sql,refs]=contract.bind_sql.split('|');
  const cvstr=refs.split(',').map(ref=>{
    const key=ref.slice(1,-1),value=get(key);
    if(typeof value!=='string'||value.includes('$$')||value.includes('::'))throw Error('CREATE_API_BINDING_VALUE');
    return ref+'::'+value;
  }).join('$$');
  const bound=ReimbursementHttpValues.formRows(await h.post('common_bindSQLDataBackend.action',{sqlid:sql.slice(1),cvstr,projid:'WF_YBX'},'text'));
  if(bound.length!==1)throw Error('CREATE_API_DEFAULT_ROWS');
  const row=bound[0];
  if(!Object.hasOwn(row,'TKS_REMARK')&&form.formFields.some(f=>f.fieldname==='ytp.tks_remark'&&f.initdata===''))row.TKS_REMARK='';
  const original=ReimbursementNativeForm.bind(form,row,set);
  if(original['ym.req_sno']!==c.expected_userid||original['ym.bx_type']!==billType||original['ytp.tks_guid']||original['ytp.tks_remark'])throw Error('CREATE_API_NATIVE_DEFAULT_SCOPE');
  // No hidden F45 fields are replaced with the later basic-information form.
  const visible=ReimbursementNativeForm.visible({...form,formhtml:template},'');
  ReimbursementNativeForm.validate(form,visible,get,[]);
  const pool=await reimbursementBusinessApi({...c,action:'invoices-list',contracts:c.pool_contracts});
  if(pool.ok!==true||pool.complete!==true||!Array.isArray(pool.rows))throw Error('CREATE_API_POOL_INCOMPLETE');
  const targets=[];
  if(c.creation_eligible){
    if(!Array.isArray(c.selected)||!c.selected.length||new Set(c.selected.map(t=>t.guid.toLowerCase())).size!==c.selected.length)throw Error('CREATE_API_SELECTION');
    for(const ticket of c.selected){
      if(!/^[a-fA-F0-9]{32}$/.test(ticket.guid))throw Error('CREATE_API_GUID');
      const found=pool.rows.filter(r=>r['t.tk_guid']===ticket.guid);
      if(found.length!==1||JSON.stringify(ReimbursementNativeForm.canonical(found[0]))!==JSON.stringify(ReimbursementNativeForm.canonical(ticket.native_row)))throw Error('CREATE_API_SELECTED_ROW_CHANGED');
      const r=found[0];
      const verifiedReceipt=ticket.source_kind==='nonvat'&&ticket.type==='MULTITK'&&r['t.tk_type']==='MULTITK'&&r['t.tk_code_abst']==='批/其他发票'&&r['t.auth_rst_desc']==='正常';
      if(r['t.tk_mod_flag']!=='NORMAL'||!verifiedReceipt&&r['t.auth_rst_desc']!=='正常(已认证)'||r['t.serial_no']||!['','N','0'].includes(r['t.PC_Has_chosen'])||r['t.tk_type']==='MEDICAL'||ReimbursementHttpValues.moneyCents(r['t.tk_total_amt'])!==ReimbursementHttpValues.moneyCents(ticket.amount))throw Error('CREATE_API_SELECTED_ROW_INELIGIBLE');
      targets.push(r);
    }
    if(c.selected.reduce((s,t)=>s+ReimbursementHttpValues.moneyCents(t.amount),0n)!==ReimbursementHttpValues.moneyCents(c.expected_amount))throw Error('CREATE_API_AMOUNT');
  }
  const staticKeys=['0-ZHEJIANGLAB_ISOPEN','0-YGCG_VUE_TYPE','0-YGCG_BXORD','0-CUSTOMER_NAME'];
  const policy=Object.fromEntries(staticKeys.map(k=>[k,window.jQuery.UC.bindData('#'+k+'#')]));
  if(Object.values(policy).some(v=>typeof v!=='string')||policy['0-ZHEJIANGLAB_ISOPEN']==='Y'||policy['0-YGCG_VUE_TYPE']||policy['0-CUSTOMER_NAME']==='南京审计大学')throw Error('CREATE_API_SPECIAL_ENTRY');
  return {ok:true,action:'create-prepare',case_id:c.case_id,expected_userid:c.expected_userid,business_transport:'native_http',
    creation_eligible:c.creation_eligible,definition_sha256:contract.definition_sha256,template_sha256:contract.template_sha256,
    original,targets,policy,complete:true,page_context_modified:false,financial_write:false,system_write:false,requests};
}

async function reimbursementCreateExecute(c) {
  'use strict';
  if(c.execute!==true||!c.pending_registered||c.creation_eligible!==true)throw Error('CREATE_API_PENDING_REQUIRED');
  const plan=await reimbursementCreatePrepare(c),expected=c.prepared;
  for(const key of ['original','targets','policy','definition_sha256','template_sha256'])
    if(JSON.stringify(ReimbursementNativeForm.canonical(plan[key]))!==JSON.stringify(ReimbursementNativeForm.canonical(expected[key])))throw Error('CREATE_API_PREPARE_CHANGED');
  const requests=[],contract=c.create_contract,registryKey='__ustcNativeClaimCreateV1';
  let registry=Object.getOwnPropertyDescriptor(window,registryKey)?.value;
  if(!registry){registry=Object.create(null);Object.defineProperty(window,registryKey,{value:registry,configurable:true});}
  if(Object.hasOwn(registry,c.token))throw Error('CREATE_API_ORIGINAL_INTENT');
  const marker={case_id:c.case_id,userid:c.expected_userid,token:c.token,phase:'prepared',create_dispatched:false};registry[c.token]=marker;
  const h=createReimbursementHttp(c.expected_userid,{prefix:'CREATE_API',requests,timeout:15000,phase:()=>marker.phase,allowed:['button']});
  const {data:context,set,get}=h.contextCopy();
  for(const key of ['0-OUTER-UNIKEY','0-USERINFO.ALL_USERROLE']){
    const value=window.jQuery.UC.bindData('#'+key+'#');if(typeof value!=='string')throw Error('CREATE_API_VIEW_CONTEXT');set(key,value);
  }
  for(const [key,value]of Object.entries(plan.original))set('45-'+key,value);
  for(const [key,value]of Object.entries({'61-bxxginfo_serialno':'','61-BX_JBXX_BX_TYPE':c.bill_type||'1','0-g0Serialno':'','61-serialno':'',
      '61-trip_ids':'','61-BX_JKINFO':'','0-tk_sx_id':'','61-bxsxid':'','976-T.ID':'',
      '53-MultiRows':c.selected.map(t=>t.guid).join(';'),'53-MultiCount':String(c.selected.length),'0-CURRFUNC':'53'}))set(key,value);
  const call=async(check,procs,keys)=>{
    const raw=await h.post('commonUpdate_responseButtonEvent.action',{cmd:ReimbursementNativeProtocol.command(check,procs,context,{}),projid:'WF_YBX',userContext:JSON.stringify(context),async:false},'text');
    (marker.native_responses??=[]).push({phase:marker.phase,raw});
    let allowed=keys;
    if(marker.phase==='create_requested'){
      // 新单响应还含预览开关，只作为惰性数据读取；不将额外字段写进页面或调用返回动作。
      const extras=[...raw.matchAll(/(?:local\(|;)([^():;]+):/g)].map(m=>m[1]);
      if(extras.some(k=>!(/^(?:0|61)-[A-Za-z0-9_.]+$/i.test(k)||k==='pgWrite_item24-cj_Display')||/^0-userinfo\./i.test(k)))throw Error('CREATE_API_RESPONSE_KEYS');
      allowed=[...new Set([...keys,...extras])];
    }
    const parsed=ReimbursementNativeProtocol.response(raw,allowed),applied=new Set(keys.map(k=>k.toUpperCase()));
    if(parsed.accepted)for(const [key,value]of Object.entries(parsed.updates))if(applied.has(key))set(key,value);
    return parsed;
  };
  const result=(extra)=>({case_id:c.case_id,expected_userid:c.expected_userid,business_transport:'native_http',
    action:'create-draft',token:c.token,marker:{...marker},requests,requires_independent_readback:true,
    finalApproval:false,submitted:false,system_write:marker.create_dispatched,...extra});
  try{
    marker.phase='view_init_requested';const init=await call('',contract.view_init,[]);
    if(!init.accepted){marker.phase='view_init_rejected';return result({ok:false,state:'rejected_before_create',create_dispatched:false});}
    // No serial from a prior page can become the new claim's identity.
    set('0-G0SERIALNO','');marker.phase='branch_guard_requested';
    const guarded=await call(contract.guard_button.check_func,contract.guard_button.procs,contract.guard_update_keys);
    if(!guarded.accepted){marker.phase='branch_guard_rejected';return result({ok:false,state:guarded.confirmation_required?'needs_native_confirmation':'rejected_before_create',create_dispatched:false});}
    if(get('0-G0SERIALNO'))throw Error('CREATE_API_SERIAL_BEFORE_CREATE');
    const required=['61-BXSX_COUNT','61-BXCG_COUNT','61-BXYYF_MZ_COUNT','61-BXYYF_ZY_COUNT'];
    // Dynamic branch counts must come from this guard's response, not a cached UC value.
    if(required.some(k=>!Object.hasOwn(guarded.updates,k)||!/^\d+$/.test(get(k))))throw Error('CREATE_API_BRANCH_COUNTS_UNVERIFIED');
    const choices=required.filter(k=>get(k)!=='0');
    for(const key of ['61-BX_TIPS','61-GZSXY_BX_TIPS','61-ISSY_TZJSD','61-CGBX_BOX'])if(get(key)==='Y')choices.push(key);
    for(const key of ['61-FPLH_TIPS','61-VUE_TYPE','61-TKCONTINUE_HINT'])if(get(key))choices.push(key);
    if(choices.length){marker.phase='native_branch_required';return result({ok:false,state:'needs_native_branch',needed_inputs:choices,create_dispatched:false});}
    marker.phase='create_requested';marker.create_dispatched=true;
    const created=await call(contract.create_button.check_func,contract.create_button.procs,contract.create_update_keys);
    if(!created.accepted){marker.phase='create_rejected';return result({ok:false,state:'create_response_rejected',server_status:created.status,create_dispatched:true});}
    const serial=created.updates['0-G0SERIALNO'];
    if(!/^\d{1,20}$/.test(serial||''))throw Error('CREATE_API_NEW_SERIAL_UNVERIFIED');
    if(created.updates['0-BX_TYPE']&&created.updates['0-BX_TYPE']!==(c.bill_type||'1'))throw Error('CREATE_API_CREATED_TYPE_CONFLICT');
    if(Object.entries(created.updates).some(([k,v])=>['61-SERIALNO','61-BXXGINFO_SERIALNO'].includes(k)&&v&&v!==serial))throw Error('CREATE_API_SERIAL_CONFLICT');
    marker.phase='create_http_received';marker.serial=serial;
    return result({ok:true,state:'created_pending_readback',serial,create_dispatched:true,server_accepted:true});
  }catch(error){marker.phase=marker.create_dispatched?'create_result_unknown':'stopped_before_create';return result({ok:false,error:String(error.message),state:marker.phase,create_dispatched:marker.create_dispatched});}
}
