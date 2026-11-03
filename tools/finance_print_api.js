/* 学校正式打印接口适配器。只调用固定HTTP端点，不导航、不调用按钮。 */
async function reimbursementPrintApi(c) {
  const $=window.jQuery, cacheKey='__ustcFormalPrintApiV1', definitionKey='__ustcPrintDefinitionV1';
  const started=performance.now(), requests=[];
  const hash=async bytes=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
  const http=createReimbursementHttp(c.expected_userid,{prefix:'PRINT_API',requests,timeout:30000,
    allowed:['definition','query','button','printOption','printDocument'],phase:()=>s.phase});
  const identity=http.guard,post=(url,data,type='text')=>http.post(url,data,type);
  if(!/^\d+$/.test(c.serial)||!c.case_id)throw Error('PRINT_API_BINDING_REQUIRED');
  function state(){
    const s=Object.getOwnPropertyDescriptor(window,cacheKey)?.value;
    if(!s||s.token!==c.session_token||s.case_id!==c.case_id||s.serial!==c.serial||s.userid!==c.expected_userid||s.include_invoice_attachments!==c.include_invoice_attachments)throw Error('PRINT_API_ORIGINAL_SESSION_REQUIRED');
    return s;
  }
  function metadata(s){return{ok:true,kind:'system_formal_print',case_id:s.case_id,serial:s.serial,userid:s.userid,
    origin:location.origin,pathname:location.pathname,project:'WF_YBX',node:s.node,session_token:s.token,
    pdf_fingerprint:s.sha256,pdf_serial:s.serial,pages:null,size:s.bytes.length,sha256:s.sha256,
    viewer_path:location.origin+'/YBX/fileSystem_getAction.action',view_kind:'formal_print_api',
    include_invoice_attachments:s.include_invoice_attachments,business_transport:'native_http',
    system_write:true,system_effects:['print_option','school_print_preparation'],approval_verified:false,
    final_print_verified:true,requests:s.requests,native_status:s.native_status,elapsed_ms:s.elapsed_ms};}
  if(c.action==='status'){
    const s=Object.getOwnPropertyDescriptor(window,cacheKey)?.value;
    return{ok:true,action:'print_api_status',session:s?{case_id:s.case_id,serial:s.serial,phase:s.phase,requests:s.requests}:null,system_write:false};
  }
  if(['chunk','finish'].includes(c.action)){
    const s=state();if(s.phase!=='ready'||!s.bytes)throw Error('PRINT_API_BYTES_NOT_READY');
    if(c.action==='finish'){
      const result={...metadata(s),action:'pdf_finish',transport_finished:true,reverified:false,content_verified:false};
      delete window[cacheKey];return result;
    }
    if(!Number.isSafeInteger(c.offset)||!Number.isSafeInteger(c.length)||c.offset<0||c.length<1||c.length>c.max_chunk||c.offset+c.length>s.bytes.length)throw Error('PRINT_API_CHUNK_BOUNDS');
    const bytes=s.bytes.subarray(c.offset,c.offset+c.length),sha=await hash(bytes);identity();state();
    let raw='';for(let i=0;i<bytes.length;i+=16384)raw+=String.fromCharCode(...bytes.subarray(i,i+16384));
    return{...metadata(s),action:'pdf_chunk',offset:c.offset,chunk_size:bytes.length,chunk_sha256:sha,base64:btoa(raw)};
  }
  async function download(s,reference){
    const parts=reference.split('&');
    if(![3,4].includes(parts.length)||!/^[-A-Za-z0-9_]{1,100}$/.test(parts[0])||!/^\.pdf$/i.test(parts[2])||parts[3]&&!/^[-A-Za-z0-9_]{1,100}$/.test(parts[3]))throw Error('PRINT_API_PDF_REFERENCE_INVALID:'+reference.slice(0,250));
    const file=new URL('fileSystem_getAction.action',location.href);file.search=new URLSearchParams({seq:parts[0],minFile:'0',type:parts[2],projid:'YBX',...(parts[3]?{dest:parts[3]}:{})});
    s.phase='pdf_download';const at=performance.now();
    const fetched=await fetch(file,{credentials:'same-origin',redirect:'error',signal:AbortSignal.timeout(30000)});
    if(!fetched.ok)throw Error('PRINT_API_PDF_DOWNLOAD_FAILED:'+fetched.status);
    const bytes=new Uint8Array(await fetched.arrayBuffer());identity();
    if(bytes.length<8||bytes.length>c.max_bytes||String.fromCharCode(...bytes.subarray(0,5))!=='%PDF-')throw Error('PRINT_API_INVALID_PDF_BYTES');
    s.requests.push({endpoint:'fileSystem_getAction.action',elapsed_ms:Math.round(performance.now()-at),status:'received'});
    s.bytes=bytes;s.sha256=await hash(bytes);s.elapsed_ms=Math.round(performance.now()-started);s.phase='ready';
    return{...metadata(s),action:'pdf_begin',option_response:s.option_response};
  }
  if(c.action==='begin'&&c.recovery){
    const s=state(),last=s.requests.at(-1);
    if(c.recovery.kind==='read_rejection'){
      if(s.phase!=='query'||!s.requests.length||s.requests.some(r=>r.phase!=='query'||r.status!=='received')||last.endpoint!=='commonQuery_doQuery.action')throw Error('PRINT_API_READ_REJECTION_RECOVERY_MISMATCH');
      delete window[cacheKey];return{ok:true,kind:'school_print_rejected',school_rejected:true,rejection_code:'input_binding_mismatch',case_id:c.case_id,serial:c.serial,school_message:'登记金额或摘要与学校当前单据不符，未发起打印',system_write:false,business_transport:'native_http',requests:s.requests};
    }
    if(s.phase!=='pdf_generate'||last?.endpoint!=='ybxPrint_yb4PrintHtml.action'||last.status!=='received'||!c.recovery.source_sha256)throw Error('PRINT_API_GENERATED_RECOVERY_MISMATCH');
    return await download(s,c.recovery.reference);
  }
  if(!['inspect','begin'].includes(c.action))throw Error('PRINT_API_ACTION_UNSUPPORTED');
  if(Object.prototype.hasOwnProperty.call(window,cacheKey))throw Error('PRINT_API_PENDING_RECOVER_ORIGINAL');
  if(c.action==='begin'&&(typeof c.include_invoice_attachments!=='boolean'||!/^\d+\.\d{2}$/.test(c.expected_amount)||typeof c.expected_summary!=='string'||!c.expected_summary||!/^\w{32}$/.test(c.session_token)))throw Error('PRINT_API_EXPORT_PLAN_REQUIRED');
  const s={token:c.session_token,case_id:c.case_id,serial:c.serial,userid:c.expected_userid,include_invoice_attachments:c.include_invoice_attachments,phase:'query',requests};
  if(c.action==='begin')Object.defineProperty(window,cacheKey,{value:s,configurable:true});
  async function proc(check,procs){
    const context={},cmd=$.page.btn.generateCmd(check,procs,null,context);
    // 固定引用字段必须进入上下文；当前工作页无需渲染101表格。
    for(const spec of [check,...procs])for(const m of spec.matchAll(/#([^#]+)#/g)){
      const key=m[1].toUpperCase();context[key.startsWith('0-')?key:'WF_YBX$'+key]=$.UC.bindData(m[0]);
    }
    const response=await post('commonUpdate_responseButtonEvent.action',{cmd,projid:'WF_YBX',userContext:JSON.stringify(context),async:false});
    const parts=response.split('local('),passed=/^(pass|success)([:;]|$)/.test(parts[0]);
    if(passed)for(const part of parts.slice(1))for(const pair of part.slice(0,part.lastIndexOf(')')).split(';')){
      const items=pair.split(':');if(items.length===2)$.UC.setData(items[0],items[1]);
    }
    return{passed,message:parts[0],response};
  }
  const specs=[
    {btnguid:'179CD38964434F7E89CEEFE72D6D3E35',check_func:'`$94b256ac92dda44bf3c48aefdac300bc:#101-t.serial_no#;#101-MultiRows#;#101-MultiCount#;#0-userinfo.userid#',procs:['`$d264de40319173e29c4db20af0188ea8:#101-t.serial_no#;#101-MultiRows#;#101-MultiCount#;#0-userinfo.userid#'],acts:''},
    {btnguid:'59372DA65E17470B96019B939A19CA88',check_func:'`$52fe7915fe470195e821dab7add0fd35:#101-t.serial_no#;#101-MultiCount#;#0-userinfo.userid#',procs:['`$5ea22eedd011091c5bd082bb1713e428:#101-t.serial_no#;#101-MultiRows#;#101-MultiCount#'],acts:'1==1:351'}
  ];
  let definitions=Object.getOwnPropertyDescriptor(window,definitionKey)?.value;
  if(!definitions||definitions.userid!==c.expected_userid){
    const list=await post('loadDefinition.action?type=W',{winno:'70',projid:'WF_YBX'},'json');
    const print=await post('loadDefinition.action?type=W',{winno:'351',projid:'WF_YBX'},'json');
    const grid=list.funcs?.find(f=>String(f.funcno)==='101'),buttons=[];
    function collect(value){if(Array.isArray(value))value.forEach(collect);else if(value&&typeof value==='object'){if(value.btnguid)buttons.push(value);Object.values(value).forEach(collect);}}
    collect(list.wins);
    if(!grid?.tablenames?.startsWith('(@{ybx_query_pc_package.wf_query_main_list_v1:')||grid.multifields!=='t.serial_no')throw Error('PRINT_API_QUERY_DEFINITION_CHANGED');
    for(const spec of specs){const found=buttons.filter(b=>b.btnguid===spec.btnguid);if(!found.length||found.some(b=>Object.keys(spec).some(k=>JSON.stringify(b[k])!==JSON.stringify(spec[k]))||!b.pre_check||!b.post_check||!b.call_proc||b.wf||b.wf_enabled||b.wf_trans||b.sign_enabled||b.jscontent||b.customFunc))throw Error('PRINT_API_CHECK_DEFINITION_CHANGED');}
    const init='`$ae59a69ccf76351707cc931841b7f33a:#61-serialno#;#0-userinfo.userid#;#0-dy_init_flag#';
    if(print.wins?.length!==1||JSON.stringify(print.wins[0].beforeLoadProcs)!==JSON.stringify([init])||print.funcMap?.['351']?.url!=='Customize/customPrint/bx_bxddy_frame.jsp')throw Error('PRINT_API_TEMPLATE_DEFINITION_CHANGED');
    const initializers=['`$bd8a1cd165467591385b30ac59ad3f99:#0-userinfo.userid#;','`$1a2afdf516c239ff7602263a9a005c3a:#0-USERINFO.USERID#'];
    if(JSON.stringify(list.wins.map(w=>w.beforeLoadProcs))!==JSON.stringify(initializers.map(p=>[p])))throw Error('PRINT_API_LIST_INIT_CHANGED');
    for(const p of initializers){const r=await proc('@',[p]);if(!r.passed)throw Error('PRINT_API_LIST_INIT_REJECTED');}
    definitions={userid:c.expected_userid,grid,init};Object.defineProperty(window,definitionKey,{value:definitions,configurable:true});
  }
  const grid=definitions.grid;
  for(const token of grid.tablenames.matchAll(/#99-([^#]+)#/g))$.UC.setData('99-'+token[1],'');
  $.UC.setData('99-t.serial_no',c.serial);$.UC.setData('99-t.cx_type','USER');$.UC.setData('61-mainlist_params','BX_YBXALL');
  $.UC.setData('101-COND','');$.UC.setData('101-CONDOBJ',[]);$.UC.setData('101-CURRPAGE',1);
  // 与原生查询一致：不附加列定制queryJson；它会改变学校的查询语义。
  const query=await post('commonQuery_doQuery.action?funcno=101&projid=WF_YBX&needcount=true',{
    _search:false,nd:Date.now(),page:1,rows:'100',sidx:'',sord:'',userData:JSON.stringify($.UC.userData),itemArr:'',sectionArr:''
  },'json');
  if(!query||Number(query.records)!==1||query.rows?.length!==1||query.rows[0].cell.length!==grid.colModel.length)throw Error('PRINT_API_UNIQUE_BILL_REQUIRED');
  const {decode,moneyCents}=ReimbursementHttpValues;
  const row=Object.fromEntries(grid.colModel.map((col,i)=>[col.name,decode(query.rows[0].cell[i])]));
  if(row['t.serial_no']!==c.serial||!row['t.REQ_INFO'].startsWith(c.expected_userid+'-'))throw Error('PRINT_API_BILL_OWNER_MISMATCH');
  if(c.action==='inspect')return{ok:true,action:'print_api_inspect',row,requests,business_transport:'native_http',system_write:false,elapsed_ms:Math.round(performance.now()-started)};
  let amountMatches=false;
  try{amountMatches=moneyCents(row['t.total_amt'])===moneyCents(c.expected_amount);}catch{}
  if(!amountMatches||row['t.abst']!==c.expected_summary){
    delete window[cacheKey];return{ok:true,kind:'school_print_rejected',school_rejected:true,rejection_code:'input_binding_mismatch',case_id:c.case_id,serial:c.serial,school_message:'登记金额或摘要与学校当前单据不符，未发起打印',observed:{amount:row['t.total_amt'],summary:row['t.abst']},system_write:false,business_transport:'native_http',requests};
  }
  s.native_status={code:row['t.status'],label:row['t.status_name']};s.node=row['t.node_id'];
  function rejected(message){s.phase='rejected';const result={ok:true,kind:'school_print_rejected',school_rejected:true,case_id:c.case_id,serial:c.serial,native_status:s.native_status,school_message:message,requests,business_transport:'native_http',system_write:true,final_print_verified:false};delete window[cacheKey];return result;}
  if(row['t.status']==='-99')return rejected('当前仍为草稿');
  $.UC.appendDataType(grid.typeMap);
  for(const [key,value] of Object.entries(row))$.UC.setData('101-'+key,value);
  $.UC.setData('101-MultiRows',c.serial);$.UC.setData('101-MultiCount','1');
  s.phase='print_check';let response=await proc(specs[0].check_func,specs[0].procs);
  if(!response.passed){if(!/^(error|err):/.test(response.message))throw Error('PRINT_API_AMBIGUOUS_SCHOOL_RESPONSE:'+response.message);return rejected(response.message);}
  s.phase='print_option';response=await post('MainOperateAction_updateYbxMain.action',{serialNo:c.serial,isPrint:c.include_invoice_attachments?'Y':'N'},'json');
  s.option_response=response;
  if(response?.status!=='success')throw Error('PRINT_API_OPTION_NOT_CONFIRMED');
  $.UC.setData('61-pldy_isPrint',c.include_invoice_attachments?'Y':'N');
  s.phase='print_prepare';response=await proc(specs[1].check_func,specs[1].procs);
  if(!response.passed){if(!/^(error|err):/.test(response.message))throw Error('PRINT_API_AMBIGUOUS_SCHOOL_RESPONSE:'+response.message);return rejected(response.message);}
  $.UC.setData('61-serialno',c.serial);$.UC.setData('61-spinfo_serialno',c.serial);$.UC.setData('0-status_name_dtl',row['t.status']);
  s.phase='print_initialize';response=await proc('@',[definitions.init]);
  if(!response.passed)throw Error('PRINT_API_PRINT_INIT_REJECTED:'+response.message);
  const params={printModel:'P',printType:'报销单列表',serialno:c.serial,byear:'',dbserver:'',bxId:'',appName:'YBX',bxOrderid:$.UC.bindData('#10005-ORDER_ID#'),sign:'',jsonData:encodeURI('YB!P!NEW!P!'+c.serial+'!P!报销单列表!P!'+c.expected_userid)};
  $.UC.setData('0-dy_init_flag','');s.phase='pdf_generate';
  const reference=await post('ybxPrint_yb4PrintHtml.action',params);
  s.reference=reference;return await download(s,reference);
}
