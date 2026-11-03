/* 日常原件HTTP上传。分块只构造内存File，multipart与保存均先登记单次意图。 */
async function reimbursementUploadApi(c) {
  const key='__ustcInvoiceHttpUploadV1',requests=[],http=createReimbursementHttp(c.expected_userid,
    {prefix:'UPLOAD_API',requests,timeout:30000,allowed:['definition','button']});
  const hash=async bytes=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(n=>n.toString(16).padStart(2,'0')).join('');
  const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
  function identities(f) {
    const ids=f.invoice_identities||f.invoice_numbers?.map(number=>({kind:'vat',code:'',number}));
    if (!Array.isArray(ids)||ids.length<1||ids.length>100||!same(ids.map(x=>x.number),f.invoice_numbers)||ids.some(x=>
      !x||Object.keys(x).sort().join(',')!=='code,kind,number'||!(x.kind==='vat'&&x.code===''&&/^\d{20}$/.test(x.number)||
      x.kind==='medical'&&/^\d{8}$/.test(x.code)&&/^\d{10}$/.test(x.number)))) throw Error('UPLOAD_API_IDENTITIES');
    return ids;
  }
  function matches(row,x) {
    return x.kind==='medical'?row['t.tk_type']==='MEDICAL'&&row['t.tk_code']===x.code&&
      row['t.tk_code_abst']==='医/'+x.code+'/'+x.number:
      row['t.tk_no']===x.number||String(row['t.tk_code_abst']||'').match(/(?<!\d)\d{20}(?!\d)/g)?.includes(x.number);
  }
  function metadata(s) {return {ok:true,action:c.action,case_id:c.case_id,expected_userid:c.expected_userid,
    business_transport:'native_http',token:s.token,batch:s.batch,phase:s.phase,
    files:s.files.map(f=>({name:f.meta.name,size:f.meta.size,sha256:f.meta.sha256,received:f.received,
      phase:f.phase,filecode:f.filecode||null})),save_dispatched:s.save_dispatched,
    save_status:s.save_status||null,system_write:s.files.some(f=>f.phase.startsWith('upload'))||s.save_dispatched,
    requests:s.requests,requires_independent_readback:true};}
  function state() {
    const s=Object.getOwnPropertyDescriptor(window,key)?.value;
    if (!s || s.token!==c.token || s.case_id!==c.case_id || s.userid!==c.expected_userid || !same(s.batch,c.batch)) throw Error('UPLOAD_API_ORIGINAL_SESSION');
    return s;
  }
  if (!['prepare','status'].includes(c.action) && (c.execute!==true || c.upload_execution_eligible!==true)) throw Error('UPLOAD_API_WRITE_INTENT');
  if (c.action==='status') return metadata(state());
  if (c.action==='prepare') {
    if (!/^[a-f0-9]{32}$/.test(c.token) || c.batch?.caseId!==c.case_id || c.batch.expectedUserid!==c.expected_userid ||
        c.batch.offset!==c.offset || c.batch.count!==c.batch.files?.length || c.batch.count<1 || c.batch.count>10 ||
        !Array.isArray(c.sources) || c.sources.length!==c.batch.count || c.sources.some((f,i)=>f.sha256!==c.batch.files[i].sha256 ||
          f.name!==c.batch.files[i].name)) throw Error('UPLOAD_API_PARAMETERS');
    const ids=c.sources.flatMap(identities);
    if (new Set(ids.map(x=>[x.kind,x.code,x.number].join(':'))).size!==ids.length) throw Error('UPLOAD_API_DUPLICATE_IDENTITY');
    const native=await ReimbursementNativeForm.definition(http,c.upload_contract);
    const field=native.form.formFields.find(f=>f.fieldname==='t.img');
    if (field?.inputtype!=='uploadfile' || field.includebase64code!=='false' || field.includeMD5Code!=='true' ||
        field.needTransPic!=='true' || native.win.beforeLoadProcs.length || c.upload_contract.source!=='YBX' || c.upload_contract.trip_id!=='') throw Error('UPLOAD_API_NATIVE_CONTRACT');
    const pool=await reimbursementBusinessApi({...c,action:'invoices-list',contracts:c.pool_contracts});
    for (const source of c.sources) {
      if (pool.rows.some(r=>identities(source).some(x=>matches(r,x)))) throw Error('UPLOAD_API_ALREADY_IN_POOL');
    }
    const plugin=await fetch('uploadFile/jquery.uploadifive.js',{credentials:'same-origin',redirect:'error',signal:AbortSignal.timeout(15000)});
    if (!plugin.ok || await hash(new Uint8Array(await plugin.arrayBuffer()))!==c.upload_contract.upload_plugin_sha256) throw Error('UPLOAD_API_PLUGIN_CHANGED');
    const context=http.contextCopy(),allowed=String(context.get('0-fpsc_filetypes')||'').toLowerCase().split(';').map(x=>x.trim().replace(/^\*\./,''));
    const limit=String(context.get('0-fpsc_filesize')||'5242880'),parts=/^(\d+)(KB|MB|GB)?$/.exec(limit);
    if (!parts) throw Error('UPLOAD_API_NATIVE_SIZE_CONTRACT');
    // 学校当前uploadifive：数值按KiB；带单位按1.024*parseInt再乘十进制单位。
    const nativeLimit=Number(parts[1])*(parts[2]?1.024*({KB:1e3,MB:1e6,GB:1e9})[parts[2]]:1024);
    for (const f of c.batch.files) {
      const extension=f.name.slice(f.name.lastIndexOf('.')+1).toLowerCase();
      if (!['pdf','ofd','png','jpg','jpeg'].includes(extension) || /[&|\r\n]/.test(f.name) ||
          !Number.isSafeInteger(f.size) || f.size<1 || f.size>Math.min(nativeLimit,c.upload_contract.max_file_bytes) || !/^[a-f0-9]{64}$/.test(f.sha256) ||
          allowed.some(Boolean) && !allowed.includes('*') && !allowed.includes(extension)) throw Error('UPLOAD_API_FILE_CONTRACT');
    }
    return {ok:true,complete:true,action:'upload-prepare',case_id:c.case_id,expected_userid:c.expected_userid,
      business_transport:'native_http',definition_sha256:native.hash,batch:c.batch,sources:c.sources,
      upload_execution_eligible:c.upload_execution_eligible===true,scope:'current_user_daily_unclaimed_pool_only_not_global_duplicate_clearance',
      original_guids:pool.rows.map(r=>r['t.tk_guid']),system_write:false,save_dispatched:false,
      requests:[...requests,...pool.requests]};
  }
  if (c.action==='start') {
    if (Object.hasOwn(window,key)) {
      const old=Object.getOwnPropertyDescriptor(window,key)?.value;
      if (!c.prior_reconciled_token || old?.token!==c.prior_reconciled_token || old?.userid!==c.expected_userid ||
          !(old.save_dispatched || c.prior_reconciled_without_upload===true && old.files.every(f=>!f.phase.startsWith('upload')))) throw Error('UPLOAD_API_PENDING_ORIGINAL');
      delete window[key];
    }
    const prepared=await reimbursementUploadApi({...c,action:'prepare'});
    if (!same(prepared.batch,c.expected_plan.batch) || !same(prepared.sources,c.expected_plan.sources) ||
        !same(prepared.original_guids,c.expected_plan.original_guids) || prepared.definition_sha256!==c.expected_plan.definition_sha256) throw Error('UPLOAD_API_PLAN_DRIFT');
    const s={token:c.token,case_id:c.case_id,userid:c.expected_userid,batch:c.batch,phase:'staging',
      files:c.batch.files.map(meta=>({meta,bytes:new Uint8Array(meta.size),received:0,phase:'staging'})),
      save_dispatched:false,requests:prepared.requests};
    Object.defineProperty(window,key,{value:s,configurable:true});return metadata(s);
  }
  const s=state();
  if (!['append','upload','save','finish'].includes(c.action)) throw Error('UPLOAD_API_ACTION');
  if (c.action==='finish') {
    if (s.phase!=='save_accepted') throw Error('UPLOAD_API_SAVE_NOT_CONFIRMED');
    // 只释放本地字节；正式完成仍由新的完整HTTP清单及原pending决定。
    const result=metadata(s);delete window[key];return {...result,transport_finished:true};
  }
  if (c.action==='save') {
    if (s.save_dispatched || s.files.some(f=>f.phase!=='upload_received')) throw Error('UPLOAD_API_SAVE_ONCE');
    const native=await ReimbursementNativeForm.definition(http,c.upload_contract),{data:context,set}=http.contextCopy();
    set('84-t.img',s.files.map(f=>f.filecode+'&'+f.meta.name+'&.'+f.meta.name.split('.').at(-1)+'&WF_YBX').join('|'));
    set('84-t.docs','');set('61-tksFilesSrc','YBX');set('61-TRIP_ID','');
    const cmd=ReimbursementNativeProtocol.command(native.save.check_func,native.save.procs,context,native.form.typeMap);
    s.phase='save_requested';s.save_dispatched=true;http.guard();
    const raw=await http.post('commonUpdate_responseButtonEvent.action',{cmd,projid:'WF_YBX',userContext:JSON.stringify(context),async:false},'text');
    const status=/^(pass|success|error|judge)(?=[:;]|local\(|$)/.exec(raw)?.[1];
    s.save_status=status||'unknown';s.requests.push(...requests);s.phase=['pass','success'].includes(status)?'save_accepted':'save_rejected_or_unknown';
    return metadata(s);
  }
  if (!Number.isSafeInteger(c.index) || c.index<0 || c.index>=s.files.length) throw Error('UPLOAD_API_INDEX');
  const f=s.files[c.index];
  if (c.action==='append') {
    if (f.phase!=='staging' || c.offset!==f.received || typeof c.base64!=='string' || c.base64.length>174764) throw Error('UPLOAD_API_CHUNK_SEQUENCE');
    const raw=Uint8Array.from(atob(c.base64),x=>x.charCodeAt(0));
    if (!raw.length || raw.length>131072 || f.received+raw.length>f.meta.size || await hash(raw)!==c.chunk_sha256) throw Error('UPLOAD_API_CHUNK_SHA');
    f.bytes.set(raw,f.received);f.received+=raw.length;
    if (f.received===f.meta.size) {
      if (await hash(f.bytes)!==f.meta.sha256) {f.phase='hash_failed';throw Error('UPLOAD_API_FILE_SHA');}
      f.phase='staged';
    }
    return metadata(s);
  }
  if (f.phase!=='staged' || f.received!==f.meta.size) throw Error('UPLOAD_API_UPLOAD_ONCE');
  const data=new FormData();data.append('doc',new File([f.bytes],f.meta.name,{type:f.meta.type}));
  f.phase='upload_requested';s.phase='uploading';http.guard();
  const trace={endpoint:'fileSystem_saveAction.action',file_index:c.index,status:'dispatched'},at=performance.now();s.requests.push(trace);
  const response=await fetch('fileSystem_saveAction.action?projid=WF_YBX&needBase64=false&needMd5=true&needTransPic=true',
    {method:'POST',credentials:'same-origin',redirect:'error',body:data,signal:AbortSignal.timeout(30000)});
  if (!response.ok) throw Error('UPLOAD_API_HTTP_'+response.status);
  const code=await response.text();http.guard();
  if (!/^[a-fA-F0-9]{32}$/.test(code) || s.files.some(x=>x.filecode?.toLowerCase()===code.toLowerCase())) throw Error('UPLOAD_API_FILECODE');
  f.filecode=code;f.phase='upload_received';delete f.bytes;trace.status='received';trace.elapsed_ms=Math.round(performance.now()-at);
  return metadata(s);
}
