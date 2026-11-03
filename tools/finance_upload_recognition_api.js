/* Complete the native post-save PDF/OFD recognition, without executing UI callbacks. */
async function reimbursementUploadRecognition(c) {
  if(c.recognition_protocol==='native_guarded_ocr_v1')return reimbursementRecognizeSavedUpload(c,false);
  const requests=[], h=createReimbursementHttp(c.expected_userid,{prefix:'UPLOAD_RECOGNITION',requests,timeout:120000,allowed:['invoiceOcr']});
  const hash=async value=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value)))].map(n=>n.toString(16).padStart(2,'0')).join('');
  for(const [name,expected] of Object.entries(c.contract.callbacks)) {
    const value=name==='callInterface'?window.jQuery.I?.callInterface:window[name];
    if(typeof value!=='function'||await hash(String(value))!==expected)throw Error('UPLOAD_RECOGNITION_CALLBACK_CHANGED');
  }
  const f=c.file;
  if(c.execute!==true||!c.pending_registered||!c.original_save_accepted||!/^[a-fA-F0-9]{32}$/.test(f.filecode)||
    !/^[a-f0-9]{64}$/.test(f.sha256)||!['pdf','ofd'].includes(f.name.split('.').at(-1).toLowerCase()))throw Error('UPLOAD_RECOGNITION_INTENT');
  const inventory=await reimbursementBusinessApi({...c,action:'invoice-inventory',contracts:c.inventory_contracts});
  const pool=inventory.grids.find(g=>g.funcno==='53'), queue=inventory.grids.find(g=>g.funcno==='113');
  if(!inventory.complete||!pool?.complete||!queue?.complete)throw Error('UPLOAD_RECOGNITION_INCOMPLETE');
  const matches=queue.rows.filter(r=>r['t.filesequcode']===f.filecode);
  if(matches.length!==1||matches[0]['t.show_filename']!==f.name||matches[0]['t.show_err_msg']||
     !['PDF','OFD'].includes(matches[0]['t.show_type'])||matches[0]['t.show_status']!=='识别中'||
     matches[0]['t.tk_filecode']!==f.filecode+'&'+f.name+'&.'+f.name.split('.').at(-1)+'&WF_YBX')throw Error('UPLOAD_RECOGNITION_QUEUE_CHANGED');
  if(pool.rows.some(r=>f.invoice_numbers.some(n=>r['t.tk_no']===n||String(r['t.tk_code_abst']||'').match(/(?<!\d)\d{20}(?!\d)/g)?.includes(n))))throw Error('UPLOAD_RECOGNITION_ALREADY_IN_POOL');
  // pdf_v2 passes pdfsign2 first; only its explicit error response invokes OCR_cutRecognition_HH.
  const isPdf=matches[0]['t.show_type']==='PDF';
  const first=JSON.parse(await h.post('thirdPartyInterface.action',{interfaceName:isPdf?'pdfsign2':'OCR_cutRecognition_HH',arguments:encodeURIComponent('jsonData|'+f.filecode+(isPdf?'!S!'+c.expected_userid:'!P!WF_YBX')),projId:'WF_YBX'},'text'));
  if(first.stateCode!=='SUCCESS'||!first.data||typeof first.data.status!=='string')throw Error('UPLOAD_RECOGNITION_RESPONSE_UNKNOWN');
  let result=first;
  if(isPdf&&first.data.status==='error') {
    if(String(first.data.seq).toLowerCase()!==f.filecode.toLowerCase())throw Error('UPLOAD_RECOGNITION_TARGET_CHANGED');
    result=JSON.parse(await h.post('thirdPartyInterface.action',{interfaceName:'OCR_cutRecognition_HH',arguments:encodeURIComponent('jsonData|'+f.filecode+'!P!WF_YBX'),projId:'WF_YBX'},'text'));
    if(result.stateCode!=='SUCCESS'||!result.data)throw Error('UPLOAD_RECOGNITION_RESPONSE_UNKNOWN');
  }
  if(result.data.status==='pass'&&String(result.data.seq).toLowerCase()!==f.filecode.toLowerCase())throw Error('UPLOAD_RECOGNITION_TARGET_CHANGED');
  return {ok:true,action:'upload-recognize',case_id:c.case_id,expected_userid:c.expected_userid,filecode:f.filecode,
    original_upload_job_id:c.original_upload_job_id,business_transport:'native_http',system_write:true,
    native_response:result,requests:[...inventory.requests,...requests],requires_independent_readback:true};
}

/* A distinct native reidentification after a documented client timeout, guarded by W79. */
async function reimbursementRecoverUploadRecognition(c) {
  if(c.recovery!==true)throw Error('UPLOAD_RECOVERY_INTENT');
  return reimbursementRecognizeSavedUpload(c,true);
}
async function reimbursementRecognizeSavedUpload(c,recovering) {
  if(c.execute!==true||!c.pending_registered||!c.original_save_accepted)throw Error('UPLOAD_RECOVERY_INTENT');
  const f=c.file;
  const inventory=await reimbursementRetryApi({...c,grid:'113',action:'inspect'});
  if(!inventory.complete||!inventory.native_ocr_contract_verified)throw Error('UPLOAD_RECOVERY_CONTRACT');
  const pool=inventory.grids?.find(g=>g.funcno==='53');
  if(!pool?.complete||pool.rows.some(r=>f.invoice_numbers.some(n=>r['t.tk_no']===n||String(r['t.tk_code_abst']||'').match(/(?<!\d)\d{20}(?!\d)/g)?.includes(n))))throw Error('UPLOAD_RECOVERY_ALREADY_IN_POOL');
  const found=inventory.grid.rows.filter(r=>r['t.filesequcode']===f.filecode);
  if(found.length!==1||found[0]['t.show_filename']!==f.name||found[0]['t.show_err_msg'])throw Error('UPLOAD_RECOVERY_ORIGINAL_QUEUE');
  const plan={business_transport:'native_http',case_id:c.case_id,ready:true,action:'reidentify',
    request:{expected_userid:c.expected_userid,grid:'113'},button_definition:c.retry_contract.grids['113'].reidentify_button,
    targets:[{native_key:'filesequcode',native_value:f.filecode,native_row:found[0],native_filename:f.name,source_sha256:f.sha256}],
    protected_native_ids:inventory.grid.rows.filter(r=>r['t.filesequcode']!==f.filecode).map(r=>r['t.filesequcode'])};
  const encoded=JSON.stringify(ReimbursementNativeForm.canonical({plan,original_input_sha256:c.original_input_sha256,prior_timeout:c.failed_receipts?.find(x=>x.filecode===f.filecode)}));
  const digest=[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(encoded)))].map(x=>x.toString(16).padStart(2,'0')).join('');
  const args={...c,grid:'113',plan,plan_digest:digest};
  const guarded=await reimbursementRetryApi({...args,action:'reidentify'});
  if(!guarded.requires_confirmation||!['pass','success','judge'].includes(guarded.marker?.native_guard))throw Error('UPLOAD_RECOVERY_NATIVE_GUARD');
  const result=await reimbursementRetryApi({...args,action:'confirm'});
  return {...result,action:recovering?'upload-recover-recognition':'upload-recognize',filecode:f.filecode,
    original_upload_job_id:c.original_upload_job_id,native_guard:guarded.marker.native_guard,
    original_pdf_request_replayed:false,requires_independent_readback:true};
}
