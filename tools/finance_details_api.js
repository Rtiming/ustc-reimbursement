/* 学校“查看明细”HTTP导出。打印历史来源保持预览身份，不升级为正式预约单。 */
async function reimbursementDetailsApi(c) {
  const stateSlot='__ustcDetailsPdfApiV1',requests=[],started=performance.now();let current=null;
  const http=createReimbursementHttp(c.expected_userid,{prefix:'DETAILS_API',requests,timeout:30000,
    allowed:['definition','button','printDocument','detailsTemplate'],phase:()=>current?.phase||'preparing'});
  const hash=async bytes=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(n=>n.toString(16).padStart(2,'0')).join('');
  const textHash=text=>hash(new TextEncoder().encode(text));
  const definitionHash=d=>textHash(JSON.stringify(ReimbursementNativeForm.canonical(Object.fromEntries(['wins','funcs','funcMap','actMap'].map(k=>[k,d[k]])))));
  if (!/^\d+$/.test(c.serial)||!c.case_id||typeof c.expected_summary!=='string'||!c.expected_summary||!/^\d+\.\d{2}$/.test(c.expected_amount)) throw Error('DETAILS_API_CASE_BINDING');
  function state() {
    const s=Object.getOwnPropertyDescriptor(window,stateSlot)?.value;
    if (!s||s.case_id!==c.case_id||s.serial!==c.serial||s.userid!==c.expected_userid||s.token!==c.session_token||
        s.expected_amount!==c.expected_amount||s.expected_summary!==c.expected_summary||JSON.stringify(s.contract)!==JSON.stringify(c.details_contract)) throw Error('DETAILS_API_ORIGINAL_SESSION');
    return s;
  }
  function metadata(s) {return {ok:true,kind:'system_view_preview_not_final_print',case_id:s.case_id,serial:s.serial,userid:s.userid,
    origin:location.origin,pathname:location.pathname,project:'WF_YBX',node:s.node,session_token:s.token,
    pdf_fingerprint:s.sha256,pdf_serial:s.serial,pages:null,size:s.bytes.length,sha256:s.sha256,
    viewer_path:location.origin+'/YBX/fileSystem_getAction.action',view_kind:'details',details_transport:'native_http',
    business_transport:'native_http',system_write:true,financial_write:false,system_effects:['school_details_initialization','preview_pdf_generation'],
    approval_verified:false,final_print_verified:false,native_status:s.native_status,requests:s.requests,
    generated_at_utc:s.generated_at_utc,elapsed_ms:s.elapsed_ms};}
  async function download(s) {
    const parts=String(s.reference).split('&');
    if (![3,4].includes(parts.length)||!/^[-A-Za-z0-9_]{1,100}$/.test(parts[0])||!/^\.pdf$/i.test(parts[2])||
        parts[3]&&!/^[-A-Za-z0-9_]{1,100}$/.test(parts[3])) throw Error('DETAILS_API_PDF_REFERENCE_INVALID');
    const url=new URL('fileSystem_getAction.action',location.href);
    url.search=new URLSearchParams({seq:parts[0],minFile:'0',type:parts[2],projid:'YBX',...(parts[3]?{dest:parts[3]}:{})});
    const at=performance.now();http.guard();
    let response;
    try {response=await fetch(url,{credentials:'same-origin',redirect:'error',signal:AbortSignal.timeout(30000)});}
    catch {throw Error('DETAILS_API_DOWNLOAD_UNKNOWN');}
    if (!response.ok) throw Error('DETAILS_API_FILE_DOWNLOAD_FAILED');
    const bytes=new Uint8Array(await response.arrayBuffer());http.guard();
    if (bytes.length<8||bytes.length>c.max_bytes||String.fromCharCode(...bytes.subarray(0,5))!=='%PDF-') throw Error('DETAILS_API_INVALID_PDF_BYTES');
    s.requests.push({endpoint:'fileSystem_getAction.action',status:'received',elapsed_ms:Math.round(performance.now()-at)});
    s.bytes=bytes;s.sha256=await hash(bytes);s.elapsed_ms=Math.round(performance.now()-started);s.phase='ready';
    return {...metadata(s),action:'pdf_begin'};
  }
  if (['chunk','finish'].includes(c.action)) {
    const s=state();current=s;
    if (s.phase!=='ready') throw Error('DETAILS_API_BYTES_NOT_READY');
    if (c.action==='finish') {const result={...metadata(s),action:'pdf_finish',transport_finished:true,reverified:false,content_verified:false};delete window[stateSlot];return result;}
    if (!Number.isSafeInteger(c.offset)||!Number.isSafeInteger(c.length)||c.offset<0||c.length<1||c.length>c.max_chunk||c.offset+c.length>s.bytes.length) throw Error('DETAILS_API_CHUNK_BOUNDS');
    const bytes=s.bytes.subarray(c.offset,c.offset+c.length);let raw='';
    for(let i=0;i<bytes.length;i+=16384)raw+=String.fromCharCode(...bytes.subarray(i,i+16384));
    return {...metadata(s),action:'pdf_chunk',offset:c.offset,chunk_size:bytes.length,chunk_sha256:await hash(bytes),base64:btoa(raw)};
  }
  if (c.action==='begin'&&c.recovery) {
    if(c.recovery.kind==='native_details_read_rejection'){
      if(!c.recovery.source_sha256)throw Error('DETAILS_API_RECOVERY_BINDING');
      const s=state();current=s;
      if(s.phase!=='preparing'||!s.requests.some(r=>r.endpoint==='commonQuery_doQuery.action')||
          s.requests.some(r=>!['loadDefinition.action','commonQuery_doQuery.action','common_bindSQLDataBackend.action'].includes(r.endpoint)||r.status!=='received'))throw Error('DETAILS_API_REJECTION_PHASE');
      const read=await reimbursementBusinessApi({...c,action:'bill-read',domain:'basic',contracts:c.basic_contracts});
      if(read.ok!==true||read.complete!==true||read.bill?.['t.serial_no']!==c.serial||
          !read.bill['t.REQ_INFO']?.startsWith(c.expected_userid+'-')||read.bill['t.status']!=='-99'||
          ReimbursementHttpValues.moneyCents(read.bill['t.total_amt'])!==ReimbursementHttpValues.moneyCents(c.expected_amount)||
          read.bill['t.abst']!==c.expected_summary)throw Error('DETAILS_API_REJECTION_CHANGED');
      const result={ok:true,kind:'school_details_rejected',school_rejected:true,rejection_code:'draft_not_downloadable',
        case_id:c.case_id,serial:c.serial,native_status:{code:'-99',label:read.bill['t.status_name']},
        school_message:'当前为草稿，保存原生数据，不生成历史明细PDF',business_transport:'native_http',
        financial_write:false,system_write:false,pdf_generation_requested:false,original_rejection_recovered:true,
        requests:[...s.requests,...read.requests]};delete window[stateSlot];return result;
    }
    if (c.recovery.kind!=='native_details_original'||!c.recovery.source_sha256) throw Error('DETAILS_API_RECOVERY_BINDING');
    const s=state();current=s;
    if (s.phase==='ready')return {...metadata(s),action:'pdf_begin',reused_generated_file:true};
    if (s.phase!=='generated'||!s.reference) throw Error('DETAILS_API_GENERATION_NOT_CONFIRMED');
    return {...await download(s),reused_generated_file:true};
  }
  if (!['inspect','begin'].includes(c.action)) throw Error('DETAILS_API_ACTION');
  if (Object.hasOwn(window,stateSlot)) throw Error('DETAILS_API_PENDING_ORIGINAL');
  if (c.action==='begin'&&!/^[a-f0-9]{32}$/.test(c.session_token)) throw Error('DETAILS_API_TOKEN');
  const s={case_id:c.case_id,serial:c.serial,userid:c.expected_userid,expected_amount:c.expected_amount,
    expected_summary:c.expected_summary,token:c.session_token,contract:c.details_contract,phase:'preparing',requests};current=s;
  if (c.action==='begin')Object.defineProperty(window,stateSlot,{value:s,configurable:true});
  const contract=c.details_contract;
  const read=await reimbursementBusinessApi({...c,action:'bill-read',domain:'basic',contracts:c.basic_contracts});requests.push(...read.requests);
  if (read.ok!==true||read.complete!==true||read.bill?.['t.serial_no']!==c.serial||!read.bill['t.REQ_INFO']?.startsWith(c.expected_userid+'-')||
      ReimbursementHttpValues.moneyCents(read.bill['t.total_amt'])!==ReimbursementHttpValues.moneyCents(c.expected_amount)||
      read.bill['t.abst']!==c.expected_summary) throw Error('DETAILS_API_BILL_STATE_OR_SUMMARY');
  if(read.bill['t.status']==='-99'){
    const result={ok:true,kind:'school_details_rejected',school_rejected:true,rejection_code:'draft_not_downloadable',
      case_id:c.case_id,serial:c.serial,native_status:{code:'-99',label:read.bill['t.status_name']},
      school_message:'当前为草稿，保存原生数据，不生成历史明细PDF',business_transport:'native_http',
      financial_write:false,system_write:false,pdf_generation_requested:false,requests};delete window[stateSlot];return result;
  }
  s.native_status={code:read.bill['t.status'],label:read.bill['t.status_name']};s.node=read.bill['t.node_id']||'';
  const list=await http.post('loadDefinition.action?type=W',{winno:'70',projid:'WF_YBX'});
  const details=await http.post('loadDefinition.action?type=W',{winno:'69',projid:'WF_YBX'});
  if (await definitionHash(list)!==contract.list_definition_sha256||await definitionHash(details)!==contract.details_definition_sha256||
      details.funcMap?.['69']?.url!=='Customize/customPrint/bx_pdf_mx.jsp') throw Error('DETAILS_API_DEFINITION_CHANGED');
  const buttons=list.wins.filter(w=>String(w.winno)==='71').flatMap(w=>w.func_btns||[]).filter(b=>b.btnguid===contract.list_button);
  const init='`$663f836de1606b5bebc5b97c75ca1c6e:#61-serialno#;#0-status_name_dtl#;#0-userinfo.userid#;#0-dy_init_flag#;#61-node_id#';
  const check='`$befac77e186c7e6ab77274901c30f0dc:#101-MultiRows#;#101-MultiCount#';
  if (buttons.length!==1||buttons[0].check_func!==check||buttons[0].procs?.length||buttons[0].acts!=='1==1:69'||
      JSON.stringify(details.wins.find(w=>String(w.winno)==='69')?.beforeLoadProcs)!==JSON.stringify([init])) throw Error('DETAILS_API_NATIVE_ROUTE');
  // 从学校固定静态源核回调语义，避免依赖旧页已加载的函数或缓存版本；不执行该源码。
  const callbackAt=performance.now();http.guard();
  const callbackResponse=await fetch('customjs/wf_ybx.js',{credentials:'same-origin',redirect:'error',cache:'no-cache',signal:AbortSignal.timeout(15000)});
  if (!callbackResponse.ok) throw Error('DETAILS_API_CALLBACK_SOURCE_UNAVAILABLE');
  const callbackSource=await callbackResponse.text();http.guard();
  if (callbackSource.length>8*1024*1024) throw Error('DETAILS_API_CALLBACK_SOURCE_LIMIT');
  for (const [name,expected]of Object.entries(contract.callbacks)) {
    if (!['before_apply_list_dtl','after_apply_list_dtl'].includes(name)) throw Error('DETAILS_API_CALLBACK_NAME');
    const marker='function '+name+'(',at=callbackSource.indexOf(marker);
    if (at<0||callbackSource.indexOf(marker,at+1)>=0) throw Error('DETAILS_API_CALLBACK_CHANGED');
    const next=callbackSource.indexOf('\nfunction ',at+marker.length),section=callbackSource.slice(at,next<0?undefined:next);
    const body=section.slice(0,section.lastIndexOf('}')+1);
    if (await textHash(body)!==expected) throw Error('DETAILS_API_CALLBACK_CHANGED');
  }
  requests.push({endpoint:'customjs/wf_ybx.js',status:'verified',elapsed_ms:Math.round(performance.now()-callbackAt)});
  const outer=await http.post('redirectCustomPage.action',{url:'Customize/customPrint/bx_pdf_mx.jsp'},'text');
  const shell=await http.post('redirectCustomPage.action',{url:'Customize/customPrint/pdf_print.jsp'},'text');
  const serverIds=[...shell.matchAll(/<div\b(?=[^>]*\bid\s*=\s*["']sys_abc["'])[^>]*>\s*([A-Za-z0-9]{2,40})\s*<\/div>/gi)];
  if (serverIds.length!==1||serverIds[0][1]!==c.expected_userid) throw Error('DETAILS_API_SERVER_IDENTITY_MISMATCH');
  const scripts=[...shell.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)].filter(m=>!/\bsrc\s*=/i.test(m[1])).map(m=>m[2]).join('\n');
  if (await textHash(outer)!==contract.outer_template_sha256||await textHash(scripts)!==contract.shell_inline_scripts_sha256) throw Error('DETAILS_API_TEMPLATE_CHANGED');
  const {data:context,set,get}=http.contextCopy();
  for (const [name,value]of Object.entries(read.bill))set('101-'+name,value);
  const billType=get('101-t.bx_type');
  if (typeof billType!=='string'||!billType) throw Error('DETAILS_API_BILL_TYPE_REQUIRED');
  set('101-MultiRows',c.serial);set('101-MultiCount','1');set('61-serialno',c.serial);set('61-spinfo_serialno',c.serial);
  set('61-bx_ord',billType);set('61-BX_JBXX_BX_TYPE',billType);set('0-sp_title','审批意见填写');set('0-sp_remark','审批意见');
  set('0-status_name_dtl',read.bill['t.status']);set('61-node_id',s.node);set('0-dy_init_flag',get('0-dy_init_flag')||'');
  s.phase='view_check';
  const checkRaw=await http.post('commonUpdate_responseButtonEvent.action',{cmd:ReimbursementNativeProtocol.command(check,[],context,{}),
    projid:'WF_YBX',userContext:JSON.stringify(context),async:false},'text');
  if (!ReimbursementNativeProtocol.response(checkRaw,[]).accepted) throw Error('DETAILS_API_VIEW_CHECK_REJECTED');
  s.phase='view_initialize';
  const raw=await http.post('commonUpdate_responseButtonEvent.action',{cmd:ReimbursementNativeProtocol.command('',[init],context,{}),
    projid:'WF_YBX',userContext:JSON.stringify(context),async:false},'text');
  const parsed=ReimbursementNativeProtocol.applyResponse(raw,context,contract.initialization_keys);
  if (Object.keys(parsed.updates).length!==contract.initialization_keys.length) throw Error('DETAILS_API_INITIALIZATION_INCOMPLETE');
  const pdfControl=get('0-PDFPRINT_CONTROL')||'',detailControl=get('0-PDFPRINT_CONTROL_CKMX')||'';
  if (!['','N','Y'].includes(pdfControl)||!['','N','Y','A'].includes(detailControl)||
      pdfControl==='Y'&&['-2','-99'].includes(read.bill['t.status'])||detailControl==='Y'||
      get('0-is_show_ysppdf')!=='Y'||get('0-ysp_special_html')||get('0-init_ckmx_btn_info')) throw Error('DETAILS_API_DOWNLOAD_DISABLED_OR_UNSUPPORTED');
  if (c.action==='inspect')return {ok:true,complete:true,action:'details_api_inspect',case_id:c.case_id,serial:c.serial,
    business_transport:'native_http',financial_write:false,system_write:true,pdf_generation_requested:false,download_allowed:true,requests};
  s.phase='pdf_generate';
  const reference=await http.post('ybxPrint_yb4PrintHtml.action',{printModel:'P',printType:'打印历史',serialno:c.serial,
    byear:'',dbserver:'',bxId:'',appName:'YBX',bxOrderid:get('10005-ORDER_ID')||'',sign:'',
    jsonData:encodeURI('YB!P!HIS!P!'+c.serial+'!P!打印历史')},'text');
  s.reference=reference;s.generated_at_utc=new Date().toISOString();s.phase='generated';
  return await download(s);
}
