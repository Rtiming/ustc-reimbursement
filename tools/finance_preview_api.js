/* 从学校确认页HTTP正文取得本单流程参数身份；只解析数据，不执行返回脚本。 */
async function reimbursementPreviewData(c, bill) {
  const {moneyCents}=ReimbursementHttpValues;
  if (bill?.['t.serial_no']!==c.serial || !bill['t.REQ_INFO']?.startsWith(c.expected_userid+'-') ||
      moneyCents(bill['t.total_amt'])!==moneyCents(c.expected_amount)) throw Error('PREVIEW_API_BILL_BINDING');
  const requests=[],http=createReimbursementHttp(c.expected_userid,{prefix:'PREVIEW_API',requests,
    timeout:15000,allowed:['definition','printDocument']});
  const definition=await http.post('loadDefinition.action?type=W',{winno:'41',projid:'WF_YBX'});
  if (definition.funcMap?.['41']?.type!=='C' || definition.funcMap['41'].url!=='Customize/customPrint/bx_frame_print.jsp') throw Error('PREVIEW_API_TEMPLATE_CHANGED');
  const html=await http.post('ybxPrint_yb4PrintHtml.action',{printModel:'P',printType:'报销确认',serialno:c.serial,
    byear:'',dbserver:'',bxId:'',appName:'YBX',bxOrderid:'',sign:'',
    jsonData:encodeURI('YB!P!NEW!P!'+c.serial+'!P!报销确认!P!'+c.expected_userid+'!P!undefined')},'text');
  const doc=new DOMParser().parseFromString(html,'text/html');
  if (!doc.body.textContent.replace(/\s+/g,'').includes('报销单号：'+c.serial)) throw Error('PREVIEW_API_DOCUMENT_IDENTITY');
  const slots=[],names=new Set();let notes=false;
  for (const control of doc.querySelectorAll('[onclick]')) {
    const action=control.getAttribute('onclick');
    if (action==="pageOpen_v1('特殊事项','33&33')") notes=true;
    const match=/^pageOpen_v1\('选择流程参数','([^']+)'\)$/.exec(action);
    if (!match) continue;
    const p=match[1].split('&'),name=p[1];
    if (!/^ustc_xmysr[1-3]?$/.test(name||'')) continue;
    if (p.length!==8 || p[0]!=='WORKFLOW_V1' || !/^\d+$/.test(p[2]) || !/^[a-fA-F0-9]{32}$/.test(p[3]) ||
        p[4]!=='QT' || p[5]!=='N' || p[6]!=='A' || !/^[A-Za-z0-9_-]*$/.test(p[7]) || names.has(name)) throw Error('PREVIEW_API_PARAMETER_CONTRACT');
    names.add(name);slots.push({index:name==='ustc_xmysr'?0:Number(name.slice(-1)),name,count:p[2],uuid:p[3],
      source_table:p[4],multi_choice:p[5],select_or_and:p[6],project_code:p[7]});
  }
  slots.sort((a,b)=>a.index-b.index);
  if (slots.some((s,i)=>s.index!==i)) throw Error('PREVIEW_API_PARAMETER_SEQUENCE');
  const sha=[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(html)))].map(v=>v.toString(16).padStart(2,'0')).join('');
  return {ok:true,complete:true,serial:c.serial,pre_review_slots:slots,notes_available:notes,
    document_sha256:sha,requests,business_transport:'native_http',system_write:false,navigation_requested:false};
}

function reimbursementPreReviewContext(set,slot) {
  const values={lc_params_jump_flag:'WORKFLOW_V1',lc_params_name:slot.name,lc_params_count:slot.count,
    lc_params_uuid:slot.uuid,param_list_src_table:slot.source_table,is_multi_choice:slot.multi_choice,
    select_or_and:slot.select_or_and,lcsp_uni_prj_code:slot.project_code};
  for (const [key,value] of Object.entries(values)) set('61-'+key,value);
}
