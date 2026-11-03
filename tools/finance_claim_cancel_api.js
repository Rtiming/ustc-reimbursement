/* 仅撤销本案明确指定的本人未提交误单；票据和原件保留。 */
async function reimbursementClaimCancelPrepare(c){
  const requests=[],http=createReimbursementHttp(c.expected_userid,{prefix:'CLAIM_CANCEL_API',requests,timeout:15000,allowed:['definition','button']});
  const d=await http.post('loadDefinition.action?type=W',{winno:'70',projid:'WF_YBX'}),projection=Object.fromEntries(['wins','funcs','funcMap','actMap'].map(k=>[k,d[k]]));
  const digest=[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(ReimbursementNativeForm.canonical(projection)))))].map(n=>n.toString(16).padStart(2,'0')).join('');
  const buttons=d.wins.find(w=>String(w.winno)==='71')?.func_btns.filter(b=>b.btnguid===c.cancel_contract.button.btnguid)||[];
  if(digest!==c.cancel_contract.definition_sha256||buttons.length!==1||JSON.stringify(ReimbursementNativeForm.canonical(buttons[0]))!==JSON.stringify(ReimbursementNativeForm.canonical(c.cancel_contract.button)))throw Error('CLAIM_CANCEL_DEFINITION_CHANGED');
  const read=await reimbursementBusinessApi({...c,action:'bill-read',domain:'used_invoices',contracts:c.used_contracts}),bill=read.bill;
  if(!read.complete||bill['t.serial_no']!==c.serial||!bill['t.REQ_INFO']?.startsWith(c.expected_userid+'-')||ReimbursementHttpValues.moneyCents(bill['t.total_amt'])!==ReimbursementHttpValues.moneyCents(c.expected_amount)||bill['t.status']!=='-99'||bill['t.lc_finish']!=='F'||bill['t.wf_name']||bill['t.uni_key']||bill['t.node_id'])throw Error('CLAIM_CANCEL_ONLY_OWN_UNSUBMITTED_DRAFT');
  const sections=read.sections.filter(s=>s.funcno==='533'&&s.complete===true),rows=sections[0]?.rows;
  if(sections.length!==1||!Array.isArray(rows)||JSON.stringify(rows.map(r=>r['t.tk_guid']).sort())!==JSON.stringify([...c.expected_guids].sort()))throw Error('CLAIM_CANCEL_COMPLETE_INVOICE_SET');
  const {data:context,set}=http.contextCopy();set('61-serialno',c.serial);set('101-t.serial_no',c.serial);set('101-MultiRows',c.serial);set('101-MultiCount','1');
  const raw=await http.post('commonUpdate_responseButtonEvent.action',{cmd:ReimbursementNativeProtocol.command(buttons[0].check_func,[],context,{}),projid:'WF_YBX',userContext:JSON.stringify(context),async:false},'text');
  const keys=[...raw.matchAll(/(?:local\(|;)([^():;]+):/g)].map(m=>m[1]);
  if(keys.some(k=>!/^61-[A-Za-z0-9_.]+$/i.test(k)))throw Error('CLAIM_CANCEL_GUARD_CONTEXT');
  const guard=ReimbursementNativeProtocol.response(raw,keys);
  return{ok:guard.accepted||guard.confirmation_required,complete:true,action:'claim-cancel-prepare',case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,
    bill,rows,guard_response:raw,guard_status:guard.status,guard_message:guard.message,definition_sha256:digest,system_write:false,save_dispatched:false,business_transport:'native_http',requests:[...read.requests,...requests]};
}

async function reimbursementClaimCancel(c){
  if(!c.execute||!c.pending_registered||!c.prepared?.ok)throw Error('CLAIM_CANCEL_ORIGINAL_PENDING_REQUIRED');
  const fresh=await reimbursementClaimCancelPrepare(c);
  for(const k of ['bill','rows','guard_response','definition_sha256'])if(JSON.stringify(ReimbursementNativeForm.canonical(fresh[k]))!==JSON.stringify(ReimbursementNativeForm.canonical(c.prepared[k])))throw Error('CLAIM_CANCEL_PREPARE_CHANGED');
  const http=createReimbursementHttp(c.expected_userid,{prefix:'CLAIM_CANCEL_API',requests:[],timeout:30000,allowed:['button']}),{data:context,set}=http.contextCopy();
  set('61-serialno',c.serial);set('101-t.serial_no',c.serial);set('101-MultiRows',c.serial);set('101-MultiCount','1');
  const registry='__ustcClaimCancelV1';if(!window[registry])Object.defineProperty(window,registry,{value:Object.create(null)});
  if(window[registry][c.token])throw Error('CLAIM_CANCEL_ORIGINAL_INTENT_ONLY');window[registry][c.token]={dispatched:true,case_id:c.case_id,serial:c.serial};
  const r={ok:false,action:'claim-cancel',case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,token:c.token,save_dispatched:true,system_write:true,business_transport:'native_http'};
  try{
    // judge是原生确认步骤；仅对本案明确撤销安排、仍为-99且无流程的单据执行原生过程。
    const check=fresh.guard_status==='judge'?'':c.cancel_contract.button.check_func;
    r.native_response=await http.post('commonUpdate_responseButtonEvent.action',{cmd:ReimbursementNativeProtocol.command(check,c.cancel_contract.button.procs,context,{}),projid:'WF_YBX',userContext:JSON.stringify(context),async:false},'text');
    r.ok=/^(pass|success)(?=[:;]|local\(|$)/.test(r.native_response);r.state=r.ok?'cancel_received_readback_required':'cancel_result_unconfirmed';
  }catch(e){r.error=e.message;r.state='cancel_result_unknown';}
  return r;
}

async function reimbursementClaimCancelReadback(c){
  const list=await reimbursementBusinessApi({...c,action:'bills-list'}),pool=await reimbursementBusinessApi({...c,action:'invoices-list',contracts:c.pool_contracts});
  if(!list.complete||list.account_scope!=='current_applicant'||list.rows.some(r=>r['t.serial_no']===c.serial)||!pool.complete)throw Error('CLAIM_CANCEL_NOT_CONFIRMED_ABSENT');
  const released=c.expected_guids.map(guid=>{
    const hits=pool.rows.filter(r=>r['t.tk_guid']===guid);
    if(hits.length!==1||hits[0]['t.serial_no']||hits[0]['t.tk_mod_flag']!=='NORMAL')throw Error('CLAIM_CANCEL_INVOICE_NOT_RELEASED');
    const original=c.prepared.rows.find(r=>r['t.tk_guid']===guid),row=hits[0],nonvat=(c.expected_nonvat_guids||[]).includes(guid);
    if(nonvat?original['t.tk_code_abst']!=='批/其他发票'||row['t.tk_type']!=='MULTITK'||row['t.auth_rst_desc']!=='正常':row['t.tk_type']==='MULTITK'||row['t.auth_rst_desc']!=='正常(已认证)')throw Error('CLAIM_CANCEL_RELEASED_SOURCE_TYPE');
    if(ReimbursementHttpValues.moneyCents(original['t.tk_total_amt'])!==ReimbursementHttpValues.moneyCents(row['t.tk_total_amt'])||original['t.tk_code_abst']!==row['t.tk_code_abst'])throw Error('CLAIM_CANCEL_RELEASED_SOURCE_CHANGED');return row;
  });
  return{ok:true,complete:true,action:'claim-cancel-readback',case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,claim_absent:true,released,
    account_scope:'current_applicant',business_transport:'native_http',system_write:false,requests:[...list.requests,...pool.requests]};
}
