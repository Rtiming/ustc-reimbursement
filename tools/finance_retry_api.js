/* 固定异常清理HTTP合同；目标来自同案原件和完整库存，不读取或改变页面勾选。 */
async function reimbursementRetryApi(c) {
  const requests=[],h=createReimbursementHttp(c.expected_userid,{prefix:'RETRY_API',requests,timeout:15000,
    allowed:['definition','button','invoiceOcr']}),canonical=ReimbursementNativeForm.canonical;
  const hash=async s=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s)))].map(n=>n.toString(16).padStart(2,'0')).join('');
  const same=(a,b)=>JSON.stringify(canonical(a))===JSON.stringify(canonical(b));
  const contract=c.retry_contract.grids[c.grid];
  if (!contract||!['inspect','refresh','cleanup','reidentify','confirm'].includes(c.action))throw Error('RETRY_API_ACTION');
  if (await hash(String(window.$.Q.loadMultiRows))!==c.retry_contract.multirows_decoder_sha256)throw Error('RETRY_API_MULTIROWS_CONTRACT');
  async function inspect() {
    const d=await h.post('loadDefinition.action?type=W',{winno:contract.winno,projid:'WF_YBX'});
    const projection=Object.fromEntries(['wins','funcs','funcMap','actMap'].map(k=>[k,d[k]]));
    if(await hash(JSON.stringify(canonical(projection)))!==contract.definition_sha256)throw Error('RETRY_API_DEFINITION_CHANGED');
    const buttons=d.wins.filter(w=>String(w.winno)===contract.host_winno).flatMap(w=>w.func_btns||[]).filter(b=>b.caption==='批量删除');
    if(buttons.length!==1||!same(buttons[0],contract.cleanup_button)||!buttons[0].clickquery||!buttons[0].clickqueryhint||buttons[0].acts||buttons[0].customFunc||buttons[0].jscontent)throw Error('RETRY_API_DELETE_CONTRACT');
    if(contract.reidentify_button){
      const reidentify=d.wins.filter(w=>String(w.winno)===contract.host_winno).flatMap(w=>w.func_btns||[]).filter(b=>b.caption==='重新识别');
      if(reidentify.length!==1||!same(reidentify[0],contract.reidentify_button)||reidentify[0].procs.length||!reidentify[0].pre_check||reidentify[0].clickquery)throw Error('RETRY_API_REIDENTIFY_CONTRACT');
      buttons.push(reidentify[0]);
    }
    let ocrContractVerified=Boolean(contract.reidentify_button);
    if(ocrContractVerified)for(const [name,expected]of Object.entries(c.retry_contract.ocr_callbacks)){
      const f=name==='callInterface'?window.$.I?.callInterface:window[name];
      if(typeof f!=='function'||await hash(String(f))!==expected)ocrContractVerified=false;
    }
    const inventory=await reimbursementBusinessApi({...c,action:'invoice-inventory',contracts:c.inventory_contracts});
    if(!inventory.ok||!inventory.complete)throw Error('RETRY_API_INVENTORY_INCOMPLETE');
    const grids=inventory.grids.map(g=>({...g,rowCount:g.rows.length,loading:false,allRecordsVisible:false,display_evidence:false}));
    const grid=grids.find(g=>g.funcno===c.grid);
    if(!grid||!grid.complete||grid.records!==grid.rows.length)throw Error('RETRY_API_SCOPE');
    return {ok:true,case_id:c.case_id,schema:'ustc-finance-invoice-retry-inspect-v1',mode:'native_http_invoice_retry_inspect',
      business_transport:'native_http',complete:true,scopeComplete:true,projid:'WF_YBX',identity:{userid:c.expected_userid},
      observed_at:new Date().toISOString(),page_context_modified:false,display_evidence:false,
      grid,grids,buttonDefinitions:[buttons[0]],definitionCaptureComplete:true,definition_sha256:contract.definition_sha256,native_ocr_contract_verified:ocrContractVerified,
      system_write:false,selection_write:false,requests:[...requests,...inventory.requests]};
  }
  const observed=await inspect();
  if(c.action==='inspect')return observed;
  const p=c.plan;
  if(p?.business_transport!=='native_http'||p.request.expected_userid!==c.expected_userid||p.request.grid!==c.grid||
    p.case_id!==c.case_id||!p.ready||!/^[a-f0-9]{64}$/.test(c.plan_digest)||!same(p.button_definition,p.action==='reidentify'?contract.reidentify_button:contract.cleanup_button))throw Error('RETRY_API_PLAN_BINDING');
  const registryKey='__ustcNativeInvoiceRetryV1';
  let registry=Object.getOwnPropertyDescriptor(window,registryKey)?.value;
  if(!registry){registry=Object.create(null);Object.defineProperty(window,registryKey,{value:registry,configurable:true});}
  if(c.action==='refresh')return {...observed,action:'refresh',case_id:c.case_id,request_id:c.request_id,
    marker:{plan_digest:c.plan_digest,phase:'refresh_http_received'},requires_independent_readback:true};
  if(!['cleanup','reidentify'].includes(p.action)||p.action==='reidentify'&&(c.grid!=='113'||p.targets?.length!==1)||!p.targets?.length||p.targets.length>20||new Set(p.targets.map(t=>t.native_value)).size!==p.targets.length)throw Error('RETRY_API_CLEANUP_ONLY');
  const field=(r,k)=>r['t.'+k]??r[k]??'';
  for(const t of p.targets){
    if(!/^[a-fA-F0-9]{32}$/.test(t.native_value)||t.native_key!==(c.grid==='53'?'tk_guid':'filesequcode'))throw Error('RETRY_API_TARGET');
    const rows=observed.grid.rows.filter(r=>field(r,t.native_key)===t.native_value);
    if(rows.length!==1||!same(rows[0],t.native_row))throw Error('RETRY_API_TARGET_CHANGED');
    if(c.grid==='53'&&(!['ERROR','ABNORMAL','FAILED','INVALID','EXCEPTION'].includes(field(rows[0],'tk_mod_flag'))||field(rows[0],'serial_no')||!['','N','0'].includes(String(field(rows[0],'PC_Has_chosen')))))throw Error('RETRY_API_PROTECTED_INVOICE');
    if(c.grid==='113'&&(field(rows[0],'show_filename')!==(t.native_filename||t.source_sha256+'.pdf')||p.action==='cleanup'&&!String(field(rows[0],'show_err_msg')).trim()))throw Error('RETRY_API_PROTECTED_RECOGNITION');
  }
  const ids=observed.grid.rows.map(r=>field(r,c.grid==='53'?'tk_guid':'filesequcode'));
  const planned=[...p.protected_native_ids,...p.targets.map(t=>t.native_value)];
  if(ids.length!==planned.length||!same([...ids].sort(),[...planned].sort()))throw Error('RETRY_API_SCOPE_CHANGED');
  if(c.action==='cleanup'||c.action==='reidentify'){
    if(Object.hasOwn(registry,c.plan_digest))throw Error('RETRY_API_ORIGINAL_INTENT');
    const marker={case_id:c.case_id,userid:c.expected_userid,plan_digest:c.plan_digest,phase:'awaiting_http_confirmation',targets:p.targets.map(t=>t.native_value)};
    registry[c.plan_digest]=marker;
    if(p.action==='reidentify'){
      for(const [name,expected]of Object.entries(c.retry_contract.ocr_callbacks)){
        const f=name==='callInterface'?window.$.I.callInterface:window[name];
        if(typeof f!=='function'||await hash(String(f))!==expected)throw Error('RETRY_API_OCR_CALLBACK_CHANGED');
      }
      const {data:context,set}=h.contextCopy();set('113-MultiRows',marker.targets[0]);set('113-MultiCount','1');
      set('113-t.filesequcode',marker.targets[0]);set('113-t.tk_filecode',p.targets[0].native_row['t.tk_filecode']||'');set('0-CURRFUNC','113');
      marker.phase='reidentify_guard_requested';
      const raw=await h.post('commonUpdate_responseButtonEvent.action',{cmd:ReimbursementNativeProtocol.command(contract.reidentify_button.check_func,[],context,{}),
        projid:'WF_YBX',userContext:JSON.stringify(context),async:false},'text');
      const parsed=ReimbursementNativeProtocol.response(raw,[]);
      if(!['pass','success','judge'].includes(parsed.status))throw Error('RETRY_API_REIDENTIFY_GUARD_REJECTED');
      if(parsed.status==='judge'){
        const expectedName=p.targets[0].native_filename||p.targets[0].source_sha256+'.pdf';
        const names=[...parsed.message.matchAll(/图片名称为\[([^\]\r\n]+)\]/g)].map(m=>m[1].trim());
        const legacy=parsed.message.match(/[a-f0-9]{64}\.pdf/g)||[];
        if(!parsed.message.includes('重新识别')||!(names.length===1&&names[0]===expectedName||!names.length&&legacy.length===1&&legacy[0]===expectedName))throw Error('RETRY_API_CONFIRMATION_SOURCE_MISMATCH');
      }
      marker.native_guard=parsed.status;marker.phase='awaiting_http_confirmation';
    }
    return {ok:true,action:p.action,case_id:c.case_id,request_id:c.request_id,business_transport:'native_http',marker,
      requires_confirmation:true,requires_independent_readback:true,system_write:false,selection_write:false,deleted:false};
  }
  const marker=registry[c.plan_digest];
  if(!marker||marker.case_id!==c.case_id||marker.userid!==c.expected_userid||marker.phase!=='awaiting_http_confirmation'||!same(marker.targets,p.targets.map(t=>t.native_value)))throw Error('RETRY_API_ORIGINAL_CONFIRMATION');
  if(c.execute!==true||!c.pending_registered)throw Error('RETRY_API_PENDING_REQUIRED');
  const {data:context,set}=h.contextCopy();set(c.grid+'-MultiRows',marker.targets.join(';'));set(c.grid+'-MultiCount',String(marker.targets.length));
  if(p.action==='reidentify'){
    if(!['pass','success','judge'].includes(marker.native_guard))throw Error('RETRY_API_ORIGINAL_GUARD_REQUIRED');
    marker.phase='ocr_http_requested';h.guard();
    const ocrHttp=createReimbursementHttp(c.expected_userid,{prefix:'RETRY_API',requests,timeout:120000,allowed:['invoiceOcr']});
    const raw=await ocrHttp.post('thirdPartyInterface.action',{interfaceName:'OCR_cutRecognition_HH',
      arguments:encodeURIComponent('jsonData|'+marker.targets[0]+'!P!WF_YBX'),projId:'WF_YBX'},'text');
    let response;try{response=JSON.parse(raw);}catch{throw Error('RETRY_API_OCR_RESPONSE_UNKNOWN');}
    if(!response||typeof response.stateCode!=='string'||!response.stateCode)throw Error('RETRY_API_OCR_RESPONSE_UNKNOWN');
    let ocrStatus=null;
    if(response.stateCode==='SUCCESS'){
      if(response.data==='ybx_ocr_back fail')ocrStatus='ybx_ocr_back fail';
      else if(response.data&&typeof response.data.status==='string'&&response.data.status.length<=1000){
        ocrStatus=response.data.status;
        if(ocrStatus==='pass'&&String(response.data.seq).toLowerCase()!==marker.targets[0].toLowerCase())throw Error('RETRY_API_OCR_RESPONSE_TARGET');
      }else throw Error('RETRY_API_OCR_RESPONSE_UNKNOWN');
    }
    const accepted=response.stateCode==='SUCCESS';marker.phase=accepted?'confirm_http_received':'confirm_http_rejected';
    return {ok:true,case_id:c.case_id,action:'confirm',business_transport:'native_http',request_id:c.request_id,marker,
      server_accepted:accepted,server_status:accepted?'success':'error',ocr_status:ocrStatus,system_write:true,
      selection_write:false,requires_independent_readback:true,recognized:false,requests};
  }
  const button=contract.cleanup_button;
  const cmd=ReimbursementNativeProtocol.command(button.check_func,button.procs,context,{});
  marker.phase='confirm_http_requested';h.guard();
  const raw=await h.post('commonUpdate_responseButtonEvent.action',{cmd,projid:'WF_YBX',userContext:JSON.stringify(context),async:false},'text');
  const response=ReimbursementNativeProtocol.response(raw,[]);
  marker.phase=response.accepted?'confirm_http_received':'confirm_http_rejected';
  return {ok:true,server_accepted:response.accepted,server_status:response.status,case_id:c.case_id,action:'confirm',business_transport:'native_http',request_id:c.request_id,
    marker,system_write:true,selection_write:false,requires_independent_readback:true,deleted:false,requests};
}
