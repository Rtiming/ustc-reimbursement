/* 原生printer.action导出：固定八列，冻结清单集合并核对全部数据；不打开导出对话框。 */
async function reimbursementGridExportApi(c) {
  const key='__ustcGridHttpExportV1',http=createReimbursementHttp(c.expected_userid,{prefix:'GRID_EXPORT_API',requests:[],
    timeout:30000,allowed:['definition']});
  const hash=async bytes=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(v=>v.toString(16).padStart(2,'0')).join('');
  function state(){const value=Object.getOwnPropertyDescriptor(window,key)?.value;
    if (!value || value.token!==c.session_token || value.case_id!==c.case_id || value.format!==c.export_format) throw Error('GRID_EXPORT_API_ORIGINAL_SESSION');return value;}
  function metadata(s){return {ok:true,kind:'native_grid_export',business_transport:'native_http',system_write:false,
    financial_write:false,session_token:s.token,case_id:c.case_id,size:s.bytes.length,sha256:s.sha256,
    records:s.records,columns:s.columns,content_type:s.content_type,format:s.format,requests:s.requests};}
  if (['chunk','finish'].includes(c.action)) {
    const s=state();if (s.phase!=='ready') throw Error('GRID_EXPORT_API_NOT_READY');
    if (c.action==='finish'){const result=metadata(s);delete window[key];return {...result,transport_finished:true};}
    if (!Number.isSafeInteger(c.offset) || !Number.isSafeInteger(c.length) || c.offset<0 || c.length<1 || c.length>131072 || c.offset+c.length>s.bytes.length) throw Error('GRID_EXPORT_API_CHUNK_BOUNDS');
    const bytes=s.bytes.subarray(c.offset,c.offset+c.length);let raw='';for(let i=0;i<bytes.length;i+=16384)raw+=String.fromCharCode(...bytes.subarray(i,i+16384));
    return {...metadata(s),offset:c.offset,chunk_size:bytes.length,chunk_sha256:await hash(bytes),base64:btoa(raw)};
  }
  if (c.action!=='begin' || !/^[a-f0-9]{32}$/.test(c.session_token) || !Array.isArray(c.scope) || !c.scope.length || !['EXCEL','PDF'].includes(c.export_format)) throw Error('GRID_EXPORT_API_PARAMETERS');
  if (Object.hasOwn(window,key)) throw Error('GRID_EXPORT_API_PENDING_ORIGINAL');
  const fields=['t.serial_no','t.req_date','t.uni_prj_code','t.abst','t.status_name','t.total_amt','t.real_info','t.req_mobile'];
  const captions=['报销单号','填报时间','经费项目号','摘要','状态','报销金额','实际报销人信息','报销人电话'];
  const definition=await http.post('loadDefinition.action?type=W',{winno:'70',projid:'WF_YBX'}),grid=definition.funcs?.find(f=>String(f.funcno)==='101');
  const columns=fields.map(name=>{const col=grid?.colModel?.find(c=>c.name===name);
    if (!col?.isshow || col.hidecond || col.datatype!=='CHAR') throw Error('GRID_EXPORT_API_COLUMN_CONTRACT');return col;});
  if (!['all','serials','ready_for_delivery'].includes(c.scope_query)) throw Error('GRID_EXPORT_API_SCOPE_QUERY');
  const read=await reimbursementBusinessApi({...c,action:'bills-list',
    ...(c.scope_query==='serials'?{serials:c.scope.map(r=>r['t.serial_no'])}:c.scope_query==='ready_for_delivery'?{list_view:c.scope_query}:{})});
  const expected=new Map(c.scope.map(r=>[r['t.serial_no'],r]));
  if (read.rows.length!==expected.size || new Set(read.rows.map(r=>r['t.serial_no'])).size!==expected.size) throw Error('GRID_EXPORT_API_SNAPSHOT_CHANGED');
  for (const row of read.rows) for (const field of fields)
    if (row[field]!==expected.get(row['t.serial_no'])?.[field]) throw Error('GRID_EXPORT_API_SNAPSHOT_CHANGED');
  const options={p_type:c.export_format,title:'reimbursement_'+c.session_token.slice(0,12),pagesize:'A4',fontsize:'10',
    title_bold:'',title_italic:'',t_border:c.export_format==='PDF'?'有':'无',scope:'whole',cols_cn:captions,cols_en:fields.map(n=>n.slice(2).toUpperCase()),
    cols_hint:columns.map(col=>col.hint||''),types:columns.map(col=>col.datatype),auto_rownum:false,
    pageOrientation:c.export_format==='PDF'?'landscape':'portrait',funcno:'101',rawFuncno:'',projid:'WF_YBX'};
  const s={token:c.session_token,case_id:c.case_id,format:c.export_format,phase:'download_requested',requests:read.requests,records:read.total_records,columns:fields};
  Object.defineProperty(window,key,{value:s,configurable:true});http.guard();const at=performance.now();
  // 与学校原生表单相同：set值先encodeURIComponent，再由form-urlencoded编码。
  const response=await fetch('printer.action',{method:'POST',credentials:'same-origin',redirect:'error',
    headers:{'Content-Type':'application/x-www-form-urlencoded;charset=UTF-8'},
    body:new URLSearchParams({set:encodeURIComponent(JSON.stringify(options))}),signal:AbortSignal.timeout(30000)});
  if (!response.ok) throw Error('GRID_EXPORT_API_HTTP_'+response.status);
  const bytes=new Uint8Array(await response.arrayBuffer());http.guard();
  const valid=c.export_format==='PDF'?String.fromCharCode(...bytes.slice(0,5))==='%PDF-':bytes.slice(0,8).join(',')==='208,207,17,224,161,177,26,225';
  if (bytes.length<512 || bytes.length>64*1024*1024 || !valid) throw Error('GRID_EXPORT_API_FILE_BYTES');
  s.requests.push({endpoint:'printer.action',status:'received',elapsed_ms:Math.round(performance.now()-at)});
  s.bytes=bytes;s.sha256=await hash(bytes);s.content_type=response.headers.get('content-type');s.phase='ready';
  return metadata(s);
}
