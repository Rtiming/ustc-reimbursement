/* 本人被拒绝原单的修改入口；只回到同号草稿，不建单、不审批。 */
function reimbursementReturnedIdentity(c, read, draft=false) {
  const b=read.bill;
  if(!read.ok||!read.complete||read.serial!==c.serial||read.expected_userid!==c.expected_userid||
    b['t.serial_no']!==c.serial||!b['t.REQ_INFO']?.startsWith(c.expected_userid+'-')||
    ReimbursementHttpValues.moneyCents(b['t.total_amt'])!==ReimbursementHttpValues.moneyCents(c.expected_amount)||
    b['t.bx_type']!=='1'||b['t.lc_finish']!=='F'||b['t.status']!==(draft?'-99':'-3')||
    !draft&&(b['t.node_id']!=='578'||b['t.wf_name']!=='YBX_LC'||!/^\d+$/.test(b['t.uni_key'])))throw Error('RETURNED_API_OWN_REJECTED_DAILY_REQUIRED');
  const sections=read.sections.filter(s=>s.funcno==='533'&&s.complete===true),rows=sections[0]?.rows;
  if(sections.length!==1||!Array.isArray(rows)||JSON.stringify(rows.map(r=>r['t.tk_guid']).sort())!==JSON.stringify([...c.expected_guids].sort()))throw Error('RETURNED_API_INVOICE_SET');
  return {bill:b,rows};
}
async function reimbursementReturnedPrepare(c) {
  const requests=[],http=createReimbursementHttp(c.expected_userid,{prefix:'RETURNED_API',requests,timeout:15000,allowed:['definition']});
  const d=await http.post('loadDefinition.action?type=W',{winno:'70',projid:'WF_YBX'});
  const projection=Object.fromEntries(['wins','funcs','funcMap','actMap'].map(k=>[k,d[k]]));
  const hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(ReimbursementNativeForm.canonical(projection))));
  const digest=[...new Uint8Array(hash)].map(n=>n.toString(16).padStart(2,'0')).join('');
  const buttons=d.wins.find(w=>String(w.winno)==='71')?.func_btns.filter(b=>b.btnguid===c.returned_contract.button.btnguid)||[];
  if(digest!==c.returned_contract.definition_sha256||buttons.length!==1||JSON.stringify(ReimbursementNativeForm.canonical(buttons[0]))!==JSON.stringify(ReimbursementNativeForm.canonical(c.returned_contract.button))||buttons[0].caption!=='修改'||buttons[0].wf_enabled)throw Error('RETURNED_API_DEFINITION_CHANGED');
  const read=await reimbursementBusinessApi({...c,action:'bill-read',domain:'used_invoices',contracts:c.used_contracts});
  const original=reimbursementReturnedIdentity(c,read);
  return {ok:true,complete:true,action:'returned-prepare',case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,...original,
    definition_sha256:digest,business_transport:'native_http',system_write:false,save_dispatched:false,requests:[...read.requests,...requests]};
}
async function reimbursementReturnedOpen(c) {
  if(!c.execute||!c.pending_registered||!c.prepared?.ok)throw Error('RETURNED_API_PENDING_REQUIRED');
  if(c.action==='returned-confirm'&&c.confirmation?.native_response!=='judge:如您需要修改，将会使报销单进入草稿状态，需重新提交后进入流程审批')throw Error('RETURNED_API_CONFIRMATION_REQUIRED');
  const fresh=await reimbursementReturnedPrepare(c);
  for(const k of ['bill','rows','definition_sha256'])if(JSON.stringify(ReimbursementNativeForm.canonical(fresh[k]))!==JSON.stringify(ReimbursementNativeForm.canonical(c.prepared[k])))throw Error('RETURNED_API_SOURCE_CHANGED');
  const requests=[],http=createReimbursementHttp(c.expected_userid,{prefix:'RETURNED_API',requests,timeout:30000,allowed:['button']}),{data:context,set}=http.contextCopy();
  set('61-serialno',c.serial);set('101-t.serial_no',c.serial);set('101-MultiRows',c.serial);set('101-MultiCount','1');
  const registry='__ustcReturnedOriginalV1';if(!window[registry])Object.defineProperty(window,registry,{value:Object.create(null)});
  if(window[registry][c.token])throw Error('RETURNED_API_ORIGINAL_INTENT_ONLY');window[registry][c.token]={case_id:c.case_id,serial:c.serial};
  const r={ok:false,action:'returned-open',case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,token:c.token,
    business_transport:'native_http',system_write:true,save_dispatched:true,requests};
  try {
    const b=c.returned_contract.button;
    r.native_response=await http.post('commonUpdate_responseButtonEvent.action',{cmd:ReimbursementNativeProtocol.command(c.action==='returned-confirm'?'':b.check_func,b.procs,context,{}),projid:'WF_YBX',userContext:JSON.stringify(context),async:false},'text');
    r.ok=/^(pass|success)(?=[:;]|local\(|$)/.test(r.native_response);
    r.state=r.ok?'original_opened_readback_required':'original_result_unconfirmed';
  } catch(e) {r.error=e.message;r.state='original_result_unknown';}
  return r;
}
async function reimbursementReturnedReadback(c) {
  const read=await reimbursementBusinessApi({...c,action:'bill-read',domain:'used_invoices',contracts:c.used_contracts});
  const current=reimbursementReturnedIdentity(c,read,true);
  const sort=rows=>ReimbursementNativeForm.canonical([...rows].sort((a,b)=>a['t.tk_guid'].localeCompare(b['t.tk_guid'])).map(row=>Object.fromEntries(Object.entries(row).filter(([k])=>k!=='t.status_name'))));
  if(JSON.stringify(sort(current.rows))!==JSON.stringify(sort(c.prepared.rows)))throw Error('RETURNED_API_ORIGINAL_INVOICES_CHANGED');
  for(const k of ['t.serial_no','t.abst','t.total_amt','t.REQ_INFO','t.req_mobile','t.uni_prj_code','t.banksname','t.bx_type'])if(current.bill[k]!==c.prepared.bill[k])throw Error('RETURNED_API_FINANCIAL_FIELDS_CHANGED');
  return {ok:true,complete:true,action:'returned-readback',case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,
    ...current,original_serial_preserved:true,original_invoice_set_preserved:true,business_transport:'native_http',system_write:false,requests:read.requests};
}
