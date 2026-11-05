/* 逐票金额维护：HTTP正文定位固定分组，原生初始化和多行读取分别留证。 */
async function reimbursementAmountDefinition(http,winno,expected){
  const d=await http.post('loadDefinition.action?type=W',{winno,projid:'WF_YBX'});
  const p=Object.fromEntries(['wins','funcs','funcMap','actMap'].map(k=>[k,d[k]]));
  const hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(ReimbursementNativeForm.canonical(p)))))].map(n=>n.toString(16).padStart(2,'0')).join('');
  if(hash!==expected)throw Error('AMOUNT_API_DEFINITION_CHANGED:'+winno);
  return d;
}

function reimbursementAmountInventory(doc,serial,allowEmpty=false){
  reimbursementSubmissionInventory(doc,serial); // 同号、完整确认正文及辅助证据结构。
  const entries=[];
  for(const td of doc.querySelectorAll('td[onclick]')){
    const handler=td.getAttribute('onclick')||'';
    if(!handler.includes('报销项详情'))continue;
    const match=/^pageOpen_v1\('报销项详情','([^']+)'\)$/.exec(handler),params=match?.[1].split('&');
    if(!params||params.length!==3||!params[0]||!/^(?:\d{2,12}|\*)$/.test(params[1].trim())||!params[2].startsWith(params[0]+'!P!')||td.getAttribute('title')!=='可点击修改报销金额，修改的金额不能大于发票金额')throw Error('AMOUNT_API_ENTRY_CHANGED');
    const amount=td.textContent.trim();ReimbursementHttpValues.moneyCents(amount);
    entries.push({group:params[0],subject:params[1].trim(),parameters:match[1],handler,amount,title:td.getAttribute('title')});
  }
  if(!entries.length&&!allowEmpty||new Set(entries.map(e=>e.parameters)).size!==entries.length)throw Error('AMOUNT_API_ENTRY_NOT_UNIQUE');
  return entries;
}

async function reimbursementAmountAllocationTree(c){
  const requests=[],http=createReimbursementHttp(c.expected_userid,{prefix:'AMOUNT_ALLOCATION_API',requests,timeout:15000,allowed:['definition','query']});
  const d=await reimbursementAmountDefinition(http,'276',c.amount_contract.branch_definition_sha256),contract=c.amount_contract.branch_query;
  const query=d.funcs.find(f=>String(f.funcno)==='385');
  if(query?.tablenames!==contract.tablenames||query.needcount!==false||query.treeGrid!==true||query.prjfields!==contract.wireColumns.join(',')||JSON.stringify(query.colModel.map(v=>v.name))!==JSON.stringify(contract.columns))throw Error('AMOUNT_API_ALLOCATION_QUERY_CHANGED');
  const {data:context,set}=http.contextCopy();
  for(const name of Object.keys(context))if(name.startsWith('WF_YBX$385-'))delete context[name];
  set('61-serialno',c.serial);set('385-COND','');set('385-CONDOBJ',[]);set('385-CURRPAGE',1);
  const reply=await http.post('commonQuery_doQuery.action?funcno=385&projid=WF_YBX',{_search:false,nd:Date.now(),page:1,rows:'10000',sidx:'',sord:'',userData:JSON.stringify(context),itemArr:'',sectionArr:''});
  // 学校不计数树在零结果时直接返回[]；仅接受这个明确的空数组，不把null/空对象当完整结果。
  if(Array.isArray(reply)&&reply.length===0)return{rows:[],complete:true,native_empty_array:true,requests};
  if(!Array.isArray(reply?.rows)||reply.rows.length>=10000||![0,1].includes(Number(reply.page))||![0,1].includes(Number(reply.total)))throw Error('AMOUNT_API_ALLOCATION_TREE_INCOMPLETE:'+JSON.stringify({page:reply?.page,total:reply?.total,records:reply?.records,rows:Array.isArray(reply?.rows)?reply.rows.length:null,keys:reply&&typeof reply==='object'?Object.keys(reply):null}));
  const rows=reply.rows.map(row=>{
    if(!Array.isArray(row.cell)||row.cell.length!==contract.wireColumns.length)throw Error('AMOUNT_API_ALLOCATION_COLUMNS:'+row.cell?.length);
    return Object.fromEntries(contract.wireColumns.map((key,index)=>[key,ReimbursementHttpValues.decode(row.cell[index])]));
  });
  if(new Set(rows.map(row=>row['t.ord'])).size!==rows.length)throw Error('AMOUNT_API_ALLOCATION_DUPLICATE_ORD');
  return {rows,complete:true,requests};
}

function reimbursementAmountAllocationTarget(c,rows,ownedGuids){
  const plans=c.budget_allocations,approved=c.funding||[],policy=c.funding_policy||{};
  if(policy.invoice_level_required)throw Error('AMOUNT_API_USE_POLICY_BOUND_SPLIT_ADAPTER');
  if(!Array.isArray(plans)||!plans.length||!rows.length||!approved.length)throw Error('AMOUNT_API_EXPLICIT_FULL_ALLOCATION_REQUIRED');
  if(!Array.isArray(ownedGuids)||!ownedGuids.length)throw Error('AMOUNT_API_ALLOCATION_OWNED_INVOICES_REQUIRED');
  const owned=new Set(ownedGuids.map(guid=>guid.toUpperCase()));
  const nodes=new Map(rows.map(row=>[row['t.ord'],row])),children=new Map(),clone=rows.map(row=>({...row})),byId=new Map(clone.map(row=>[row['t.ord'],row]));
  if(nodes.size!==rows.length||rows.some(row=>!/^[A-Za-z0-9_.-]{1,100}$/.test(row['t.ord'])||!['true','false'].includes(row['t.tleaf'])))throw Error('AMOUNT_API_ALLOCATION_IDENTITY');
  const invoice=row=>{
    const ids=new Set(),visited=new Set();let node=row;
    while(node){
      const id=node['t.ord'];if(visited.has(id))throw Error('AMOUNT_API_ALLOCATION_CYCLE');visited.add(id);
      const guid=node['t.tk_guid'];if(guid){if(!/^[a-f0-9]{32}$/i.test(guid))throw Error('AMOUNT_API_ALLOCATION_GUID');ids.add(guid.toUpperCase());}
      const parent=node['t.tparent'];
      if(nodes.has(parent))node=nodes.get(parent);else{if(!['','0','null'].includes(parent))throw Error('AMOUNT_API_ALLOCATION_ORPHAN');node=null;}
    }
    if(ids.size!==1||!owned.has([...ids][0]))throw Error('AMOUNT_API_ALLOCATION_INVOICE_ANCESTRY');return[...ids][0];
  };
  for(const row of rows){
    const parent=row['t.tparent'];if(nodes.has(parent)){if(!children.has(parent))children.set(parent,[]);children.get(parent).push(row['t.ord']);}
    invoice(row);
  }
  const leaves=rows.filter(row=>row['t.tleaf']==='true');
  if(!leaves.length||leaves.length!==plans.length||rows.some(row=>(row['t.tleaf']==='true')===children.has(row['t.ord'])))throw Error('AMOUNT_API_ALLOCATION_FULL_LEAVES');
  const money=ReimbursementHttpValues.moneyCents,decimal=n=>String(n/100n)+'.'+String(n%100n).padStart(2,'0'),seen=new Set(),totals=new Map(),beforeFunds=new Map(),afterFunds=new Map();
  for(const row of leaves){
    const guid=invoice(row),project=row['t.uni_prj_code'],budget=row['t.bu_code'];
    if(!approved.some(f=>f.code===project)||!budget)throw Error('AMOUNT_API_ALLOCATION_UNAPPROVED_PROJECT');
    const matches=plans.filter(p=>p.invoice_guid.toUpperCase()===guid&&p.project===project&&p.budget===budget);
    if(matches.length!==1||seen.has(matches[0]))throw Error('AMOUNT_API_ALLOCATION_TARGET_NOT_UNIQUE');
    const p=matches[0],value=money(p.amount);seen.add(p);byId.get(row['t.ord'])['t.t_amt']=p.amount;
    totals.set(guid,(totals.get(guid)||0n)+value);beforeFunds.set(project,(beforeFunds.get(project)||0n)+money(row['t.t_amt']||'0'));afterFunds.set(project,(afterFunds.get(project)||0n)+value);
  }
  const roots=rows.filter(row=>!nodes.has(row['t.tparent']));
  if(new Set(roots.map(invoice)).size!==roots.length)throw Error('AMOUNT_API_ALLOCATION_DUPLICATE_INVOICE_ROOT');
  for(const root of roots){
    const guid=invoice(root),claim=c.reduction.rows.find(p=>p.invoice_guid.toUpperCase()===guid),wanted=claim?money(claim.claim_amount):money(root['t.t_amt']);
    if(totals.get(guid)!==wanted||wanted>money(root['t.invoice_amt']))throw Error('AMOUNT_API_ALLOCATION_INVOICE_TOTAL');
  }
  for(const fund of approved){
    const cap=money(fund.amount),original=beforeFunds.get(fund.code)||0n,value=afterFunds.get(fund.code)||0n;
    if(value>(cap>original?cap:original))throw Error('AMOUNT_API_ALLOCATION_FUND_CAP');
  }
  const summary=(id,path=new Set())=>{
    if(path.has(id))throw Error('AMOUNT_API_ALLOCATION_CYCLE');path=new Set(path);path.add(id);
    const child=children.get(id)||[];
    if(!child.length)return money(byId.get(id)['t.t_amt']);
    const value=child.reduce((sum,cid)=>sum+summary(cid,path),0n);byId.get(id)['t.t_amt']=decimal(value);return value;
  };
  roots.forEach(root=>summary(root['t.ord']));return clone;
}

async function reimbursementAmountAllocate(c){
  const read=await reimbursementAmountAllocationTree(c),meta=await reimbursementAmountInspect({...c,initialization_verified:false});
  const total=ReimbursementHttpValues.moneyCents(meta.bill['t.total_amt']),actual=String(total/100n)+'.'+String(total%100n).padStart(2,'0');
  const invoices=await reimbursementBusinessApi({...c,action:'bill-read',domain:'used_invoices',contracts:c.used_contracts,expected_amount:actual});
  const sections=invoices.sections.filter(section=>section.funcno==='533'&&section.complete===true);
  if(sections.length!==1||!sections[0].rows.length)throw Error('AMOUNT_API_ALLOCATION_OWNED_INVOICES_INCOMPLETE');
  const guids=sections[0].rows.map(row=>row['t.tk_guid']),target=reimbursementAmountAllocationTarget(c,read.rows,guids);
  const requests=[],http=createReimbursementHttp(c.expected_userid,{prefix:'AMOUNT_ALLOCATION_API',requests,timeout:15000,allowed:['definition','button']});
  await reimbursementAmountDefinition(http,'276',c.amount_contract.branch_definition_sha256);
  const {data:context,set}=http.contextCopy();set('61-serialno',c.serial);
  const wire=target.map(row=>c.amount_contract.branch_query.wireColumns.map(key=>{
    const value=row[key];if(typeof value!=='string'||/^\s*<(?:input|select)\b/i.test(value))throw Error('AMOUNT_API_ALLOCATION_EXECUTABLE_CELL');
    return value.replaceAll('&','＆').replaceAll(';','；');
  }).join('&')).join(';');
  set('385-ALLTABLEDATA',wire);const button=c.amount_contract.branch_save;
  if(button.caption!=='确认保存'||button.procs.length!==1)throw Error('AMOUNT_API_ALLOCATION_SAVE_CHANGED');
  const cmd=ReimbursementNativeProtocol.command(button.check_func,button.procs,context,{});
  const raw=await http.post('commonUpdate_responseButtonEvent.action',{cmd,projid:'WF_YBX',userContext:JSON.stringify(context),async:false},'text');
  if(!ReimbursementNativeProtocol.response(raw,['61-tksbcodecnt']).accepted)throw Error('AMOUNT_API_ALLOCATION_SAVE_REJECTED');
  const after=await reimbursementAmountAllocationTree(c);
  if(JSON.stringify(ReimbursementNativeForm.canonical(after.rows))!==JSON.stringify(ReimbursementNativeForm.canonical(target)))throw Error('AMOUNT_API_ALLOCATION_READBACK_DIFFERENT');
  return{state:'saved',original:read.rows,target,owned_invoice_guids:guids,independent_query_readback:true,requests:[...read.requests,...meta.requests,...invoices.requests,...requests,...after.requests]};
}

async function reimbursementAmountInspect(c){
  const requests=[],http=createReimbursementHttp(c.expected_userid,{prefix:'AMOUNT_API',requests,timeout:15000,allowed:['definition','binding','detailsTemplate','printDocument']});
  if(http.contextCopy().get('0-SHOW_ZFJE')==='Y')throw Error('AMOUNT_API_SPECIAL_SELF_PAY_FORMULA_NOT_IN_SCOPE');
  const list=await reimbursementBusinessApi({...c,action:'bills-list',serials:[c.serial]}),hits=list.rows.filter(r=>r['t.serial_no']===c.serial);
  if(!list.complete||hits.length!==1||hits[0]['t.status']!=='-99'||hits[0]['t.lc_finish']!=='F')throw Error('AMOUNT_API_OWNED_DRAFT_REQUIRED');
  const bill=hits[0],cents=ReimbursementHttpValues.moneyCents(bill['t.total_amt']),actual=String(cents/100n)+'.'+String(cents%100n).padStart(2,'0');
  const basic=await reimbursementBusinessApi({...c,action:'bill-read',domain:'basic',contracts:c.basic_contracts,expected_amount:actual});
  const contract=c.amount_contract;
  const main=await reimbursementAmountDefinition(http,'41',contract.main_definition_sha256);
  const open=main.wins.flatMap(w=>w.func_btns||[]).filter(b=>b.btnguid===contract.open_button.btnguid);
  if(open.length!==1||JSON.stringify(ReimbursementNativeForm.canonical(open[0]))!==JSON.stringify(ReimbursementNativeForm.canonical(contract.open_button)))throw Error('AMOUNT_API_OPEN_DEFINITION_CHANGED');
  const hash=async text=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text)))].map(n=>n.toString(16).padStart(2,'0')).join('');
  for(const [name,expected]of [['bx_frame_print.jsp',c.submission_contract.shell_sha256],['bx_dtl.jsp',c.submission_contract.inner_sha256]]){
    const text=await http.post('redirectCustomPage.action',{url:'Customize/customPrint/'+name},'text');
    if(await hash(text)!==expected)throw Error('AMOUNT_API_PRINT_HANDLERS_CHANGED');
  }
  const html=await http.post('ybxPrint_yb4PrintHtml.action',{printModel:'P',printType:'报销确认',serialno:c.serial,byear:'',dbserver:'',bxId:'',appName:'YBX',bxOrderid:'',sign:'',jsonData:encodeURI('YB!P!NEW!P!'+c.serial+'!P!报销确认!P!'+c.expected_userid+'!P!undefined')},'text');
  const doc=new DOMParser().parseFromString(html,'text/html'),payroll=Boolean(c.remuneration_native_source)&&c.system_mode==='remuneration';
  let salary=null;
  if(payroll){
    if(c.initialization_verified||bill['t.bx_type']!=='8')throw Error('AMOUNT_API_PAYROLL_READ_ONLY');
    const options=reimbursementSubmissionInventory(doc,c.serial,'remuneration');if(options.proofs.length)throw Error('AMOUNT_API_PAYROLL_UNEXPECTED_PROOFS');
    const read=await reimbursementRemunerationInspect(c);salary=reimbursementRemunerationPreview(doc,read,c);requests.push(...read.requests);
  }
  const entries=payroll?[]:reimbursementAmountInventory(doc,c.serial,Boolean(c.allowance_native_source)&&bill['t.bx_type']==='2');
  const selected=entries.filter(e=>e.group===c.group&&e.subject===c.subject);
  if(selected.length>1)throw Error('AMOUNT_API_TARGET_GROUP_NOT_UNIQUE');
  const report={ok:true,complete:true,action:'amount-inspect',case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,business_transport:'native_http',
    bill,basic_sections:basic.sections,entries,entry:selected[0]||null,target_group_found:selected.length===1,rows:null,amount_rows_complete:false,system_write:false,save_dispatched:false,
    page_context_modified:false,definition_sha256:contract.definition_sha256,document_sha256:await hash(html),requests:[...list.requests,...basic.requests,...requests]};
  if(c.include_categories===true)report.categories=reimbursementCategoriesInventory(doc,c.serial,c.allow_category_project_rows===true);
  if(payroll)report.salary=salary;
  if(c.initialization_verified===true){
    if(selected.length!==1)throw Error('AMOUNT_API_INITIALIZED_GROUP_MISSING');
    const native=await ReimbursementNativeForm.definition(http,contract);
    if(native.form.bindSql!==contract.bindSql||native.form.multiRec!==true)throw Error('AMOUNT_API_BINDING_CHANGED');
    const data=ReimbursementHttpValues.formRows(await http.post('common_bindSQLDataBackend.action',{sqlid:contract.bindSql.split('|')[0].slice(1),cvstr:'#61-serialno#::'+c.serial,projid:'WF_YBX'},'text'));
    if(!data.length||data.length>100)throw Error('AMOUNT_API_ROW_COUNT');
    const {set}=http.contextCopy(),rows=data.map(row=>ReimbursementNativeForm.bind(native.form,row,set)),guids=new Set(),ords=new Set();let total=0n;
    for(const row of rows){
      const numbers=[...new Set(String(row['t.t_desc']).match(/(?<!\d)\d{20}(?!\d)/g)||[])],guid=row['t.tk_guid'],ord=row['t.ord'];
      if(row['t.serial_no']!==c.serial||!/^[a-f0-9]{32}$/i.test(guid)||guids.has(guid.toLowerCase())||!/^\d+$/.test(ord)||ords.has(ord)||numbers.length!==1)throw Error('AMOUNT_API_ROW_IDENTITY');
      const face=ReimbursementHttpValues.moneyCents(row['t.invoice_amt']),current=ReimbursementHttpValues.moneyCents(row['t.t_amt']);
      if(face<=0n||current>face||ReimbursementHttpValues.moneyCents(row['t.invoice_amt_xs'])!==face)throw Error('AMOUNT_API_ROW_AMOUNT');
      guids.add(guid.toLowerCase());ords.add(ord);total+=current;
    }
    if(total!==ReimbursementHttpValues.moneyCents(selected[0].amount)){
      const used=await reimbursementBusinessApi({...c,action:'bill-read',domain:'used_invoices',contracts:c.used_contracts,expected_amount:actual});
      const sections=used.sections.filter(s=>s.funcno==='533'&&s.complete===true),owned=sections.length===1?sections[0].rows:[];
      if(total!==cents||owned.length!==rows.length||new Set(owned.map(r=>r['t.tk_guid'])).size!==rows.length||rows.some(row=>{
        const hits=owned.filter(r=>r['t.tk_guid']===row['t.tk_guid']);return hits.length!==1||hits[0]['t.serial_no']!==c.serial||
          ReimbursementHttpValues.moneyCents(hits[0]['t.tk_total_amt'])!==ReimbursementHttpValues.moneyCents(row['t.invoice_amt'])||
          !String(hits[0]['t.tk_code_abst']).includes(String(row['t.t_desc']).match(/(?<!\d)\d{20}(?!\d)/)[0]);
      }))throw Error('AMOUNT_API_EDITOR_SCOPE_NOT_COMPLETE_BILL');
      report.editor_scope='whole_bill';report.owned_invoice_rows=owned;requests.push(...used.requests);
    }
    report.rows=rows;report.amount_rows_complete=true;report.requests=[...list.requests,...basic.requests,...requests];
    if(c.include_invoice_allocations===true){
      const allocation=await reimbursementAmountAllocationTree(c);report.allocation_tree=allocation.rows;report.allocation_tree_complete=true;report.requests.push(...allocation.requests);
    }
  }
  return report;
}

async function reimbursementAmountInitialize(c){
  let dispatched=false,prepared=null,nativeResponse=null;const requests=[];
  try{
    if(c.execute!==true||!c.pending_registered||!c.expected_plan||c.initialization_verified)throw Error('AMOUNT_API_ORIGINAL_PENDING_REQUIRED');
    prepared=await reimbursementAmountInspect(c);
    if(prepared.target_group_found!==true)throw Error('AMOUNT_API_TARGET_GROUP_NOT_FOUND');
    for(const key of ['bill','basic_sections','entries','entry','definition_sha256'])if(JSON.stringify(ReimbursementNativeForm.canonical(prepared[key]))!==JSON.stringify(ReimbursementNativeForm.canonical(c.expected_plan[key])))throw Error('AMOUNT_API_PREPARE_DRIFT');
    const http=createReimbursementHttp(c.expected_userid,{prefix:'AMOUNT_API',requests,timeout:15000,allowed:['button']}),{data:context,set}=http.contextCopy();
    set('61-xtype',prepared.entry.parameters);set('61-serialno',c.serial);
    const b=c.amount_contract.open_button,cmd=ReimbursementNativeProtocol.command(b.check_func,b.procs,context,{});
    dispatched=true;const raw=await http.post('commonUpdate_responseButtonEvent.action',{cmd,projid:'WF_YBX',userContext:JSON.stringify(context),async:false},'text');
    if(typeof raw==='string'&&raw.length<=65536)nativeResponse=raw;
    const parsed=ReimbursementNativeProtocol.response(raw,['61-tksbcodecnt','61-xtype']);
    if(!parsed.accepted)throw Error('AMOUNT_API_INITIALIZE_REJECTED');
    return {...prepared,action:'amount-initialize',initialized:true,save_dispatched:true,system_write:true,financial_amount_save_requested:false,
      state:'initialized_independent_group_read_required',native_updates:parsed.updates,requires_independent_readback:true,requests:[...prepared.requests,...requests]};
  }catch(error){return{ok:false,action:'amount-initialize',case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,business_transport:'native_http',
    save_dispatched:dispatched,system_write:dispatched,financial_amount_save_requested:false,prepared,native_response:nativeResponse,
    state:dispatched?'unknown_initialization_recover_original':'rejected_before_dispatch',error:String(error.message),requests,requires_independent_readback:true};}
}

function reimbursementAmountVerifyGroups(before,after){
  const money=ReimbursementHttpValues.moneyCents,whole=before.editor_scope==='whole_bill';
  const delta=money(before.bill['t.total_amt'])-money(after.bill['t.total_amt']);
  if(delta<0n||after.entries.length!==before.entries.length)throw Error('AMOUNT_API_POST_GROUP_SET');
  let originalTotal=0n,currentTotal=0n;
  for(const entry of before.entries){
    const found=after.entries.filter(e=>e.parameters===entry.parameters);
    if(found.length!==1)throw Error('AMOUNT_API_POST_GROUP_SET');
    for(const key of Object.keys(entry))if(key!=='amount'&&found[0][key]!==entry[key])throw Error('AMOUNT_API_OTHER_GROUP_CHANGED');
    const old=money(entry.amount),now=money(found[0].amount);originalTotal+=old;currentTotal+=now;
    if(whole){if(now>old||now<0n)throw Error('AMOUNT_API_GROUP_AMOUNT_INCREASED');}
    else if(now!==old-(entry.parameters===before.entry.parameters?delta:0n))throw Error('AMOUNT_API_OTHER_GROUP_CHANGED');
  }
  if(whole&&(originalTotal!==money(before.bill['t.total_amt'])||currentTotal!==money(after.bill['t.total_amt'])))throw Error('AMOUNT_API_GROUP_TOTAL_NOT_COMPLETE');
}

async function reimbursementAmountSave(c){
  let dispatched=false,prepared=null;const requests=[];
  try{
    if(c.execute!==true||!c.pending_registered||!c.expected_plan||c.initialization_verified!==true||c.reduction?.schema!=='ustc-native-invoice-reduction-preparation-v1')throw Error('AMOUNT_API_REDUCTION_INTENT_REQUIRED');
    prepared=await reimbursementAmountInspect(c);
    if(!prepared.amount_rows_complete)throw Error('AMOUNT_API_COMPLETE_ROWS_REQUIRED');
    for(const key of ['bill','basic_sections','entries','entry','rows','definition_sha256',...(c.include_invoice_allocations?['allocation_tree']:[])])if(JSON.stringify(ReimbursementNativeForm.canonical(prepared[key]))!==JSON.stringify(ReimbursementNativeForm.canonical(c.expected_plan[key])))throw Error('AMOUNT_API_REDUCTION_DRIFT');
    const claims=c.reduction.rows;
    if(!Array.isArray(claims)||claims.length!==prepared.rows.length||new Set(claims.map(r=>r.invoice_guid)).size!==claims.length)throw Error('AMOUNT_API_FULL_CLAIMS_REQUIRED');
    let before=0n,after=0n;const target=[];
    for(const row of prepared.rows){
      const matched=claims.filter(r=>r.invoice_guid===row['t.tk_guid']);
      if(matched.length!==1||matched[0].ord!==row['t.ord']||JSON.stringify(ReimbursementNativeForm.canonical(matched[0].native_fields))!==JSON.stringify(ReimbursementNativeForm.canonical(row)))throw Error('AMOUNT_API_CLAIM_ROW_BINDING');
      const claim=matched[0],current=ReimbursementHttpValues.moneyCents(row['t.t_amt']),value=ReimbursementHttpValues.moneyCents(claim.claim_amount);
      if(value>current||ReimbursementHttpValues.moneyCents(claim.current_amount)!==current||ReimbursementHttpValues.moneyCents(claim.face_amount)!==ReimbursementHttpValues.moneyCents(row['t.invoice_amt']))throw Error('AMOUNT_API_REDUCTION_ONLY');
      before+=current;after+=value;target.push({...row,'t.t_amt':claim.claim_amount});
    }
    const delta=before-after,billTotal=ReimbursementHttpValues.moneyCents(prepared.bill['t.total_amt'])-delta;
    if(before!==ReimbursementHttpValues.moneyCents(c.reduction.current_total)||after!==ReimbursementHttpValues.moneyCents(c.reduction.claim_total)||billTotal<ReimbursementHttpValues.moneyCents(c.expected_final_amount))throw Error('AMOUNT_API_TOTAL_OR_FINAL_GOAL');
    const decimal=n=>String(n/100n)+'.'+String(n%100n).padStart(2,'0');
    const groupAfter=ReimbursementHttpValues.moneyCents(prepared.entry.amount)-delta;
    if(groupAfter<0n)throw Error('AMOUNT_API_GROUP_REDUCTION_EXCEEDS_GROUP');
    const report={...prepared,action:'amount-save',target_rows:target,target_group_total:prepared.editor_scope==='whole_bill'?null:decimal(groupAfter),expected_bill_total:decimal(billTotal),
      final_target_reached:billTotal===ReimbursementHttpValues.moneyCents(c.expected_final_amount),requires_independent_readback:true};
    if(!delta)return{...report,ok:true,system_write:false,save_dispatched:false,financial_amount_save_requested:false,
      state:'already_matches_independent_readback_required',branch:{source:'no_save_required',count:null,allocation_required:false}};
    const http=createReimbursementHttp(c.expected_userid,{prefix:'AMOUNT_API',requests,timeout:15000,allowed:['definition','button']});
    const native=await ReimbursementNativeForm.definition(http,c.amount_contract),{data:context,set}=http.contextCopy();
    set('61-serialno',c.serial);set('61-xtype',prepared.entry.parameters);set('61-tksbcodecnt','');
    for(const name of ['t.tk_guid','t.t_amt','t.ord','t.invoice_amt']){
      if(target.some(row=>typeof row[name]!=='string'||row[name].includes('$|')||/[\r\n]/.test(row[name])))throw Error('AMOUNT_API_WIRE_SEPARATOR');
      set('390-'+name,target.map(row=>row[name]).join('$|'));
    }
    const cmd=ReimbursementNativeProtocol.command(native.save.check_func,native.save.procs,context,native.form.typeMap);
    dispatched=true;const raw=await http.post('commonUpdate_responseButtonEvent.action',{cmd,projid:'WF_YBX',userContext:JSON.stringify(context),async:false},'text');
    const parsed=ReimbursementNativeProtocol.response(raw,['61-tksbcodecnt']);
    if(!parsed.accepted)throw Error('AMOUNT_API_SAVE_REJECTED');
    const counter=parsed.updates['61-TKSBCODECNT'];
    if(typeof counter!=='string'||!/^\d+$/.test(counter)||!Number.isSafeInteger(Number(counter)))throw Error('AMOUNT_API_CURRENT_SAVE_BRANCH_UNKNOWN');
    const count=Number(counter);let refresh=null,allocation=null;
    if(count>0&&Array.isArray(c.budget_allocations)&&c.budget_allocations.length)allocation=await reimbursementAmountAllocate(c);
    if(count===0||allocation){
      const post=await reimbursementAmountInspect({...c,initialization_verified:false});
      if(ReimbursementHttpValues.moneyCents(post.bill['t.total_amt'])!==billTotal)throw Error('AMOUNT_API_PERSISTED_BILL_TOTAL');
      reimbursementAmountVerifyGroups(prepared,post);
      for(const key of Object.keys(prepared.bill))if(key!=='t.total_amt'&&post.bill[key]!==prepared.bill[key])throw Error('AMOUNT_API_OTHER_BILL_FIELD_CHANGED');
      if(JSON.stringify(post.basic_sections)!==JSON.stringify(prepared.basic_sections))throw Error('AMOUNT_API_OTHER_BASIC_FIELD_CHANGED');
      // 按官方重新打开过程刷新F390，下一独立读取核持久值，不能用原编辑临时表冒充保存回读。
      refresh=await reimbursementAmountInitialize({...c,initialization_verified:false,expected_plan:post});
      if(!refresh.ok||refresh.initialized!==true)throw Error('AMOUNT_API_PERSISTENCE_REFRESH_UNKNOWN');
    }
    return{...report,ok:true,system_write:true,save_dispatched:true,financial_amount_save_requested:true,persistence_refresh:refresh,allocation,
      branch:{source:'current_native_save_response',count,allocation_required:count>0&&!allocation},
      state:count&&!allocation?'saved_native_invoice_allocation_required':'saved_independent_readback_required',requests:[...prepared.requests,...requests,...(allocation?.requests||[])]};
  }catch(error){return{ok:false,action:'amount-save',case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,business_transport:'native_http',
    save_dispatched:dispatched,system_write:dispatched,financial_amount_save_requested:dispatched,prepared,
    state:dispatched?'unknown_amount_save_recover_original':'rejected_before_dispatch',error:String(error.message),requests,requires_independent_readback:true};}
}

// Recovery for an already dispatched save whose old validator assumed that
// one clicked category owned the whole invoice. Only refresh the editor's
// staging rows from persisted storage; never issue the monetary save again.
async function reimbursementAmountRecoverPersisted(c){
  if(!c.persistence_recovery||c.action!=='recover'||c.execute!==false||c.budget_allocations?.length)throw Error('AMOUNT_API_PERSISTENCE_RECOVERY_SCOPE');
  const before=c.expected_plan,post=await reimbursementAmountInspect({...c,initialization_verified:false});
  if(before.editor_scope!=='whole_bill')throw Error('AMOUNT_API_PERSISTENCE_RECOVERY_NOT_WHOLE_BILL');
  const target=ReimbursementHttpValues.moneyCents(before.bill['t.total_amt'])-ReimbursementHttpValues.moneyCents(c.reduction.change_total);
  if(ReimbursementHttpValues.moneyCents(post.bill['t.total_amt'])!==target)throw Error('AMOUNT_API_PERSISTENCE_RECOVERY_TOTAL');
  reimbursementAmountVerifyGroups(before,post);
  for(const k of Object.keys(before.bill))if(k!=='t.total_amt'&&post.bill[k]!==before.bill[k])throw Error('AMOUNT_API_PERSISTENCE_RECOVERY_BILL_DRIFT');
  if(JSON.stringify(post.basic_sections)!==JSON.stringify(before.basic_sections))throw Error('AMOUNT_API_PERSISTENCE_RECOVERY_BASIC_DRIFT');
  const refresh=await reimbursementAmountInitialize({...c,execute:true,pending_registered:true,initialization_verified:false,expected_plan:post});
  if(!refresh.ok||refresh.initialized!==true||refresh.financial_amount_save_requested!==false)throw Error('AMOUNT_API_PERSISTENCE_RECOVERY_REFRESH');
  const after=await reimbursementAmountInspect({...c,initialization_verified:true});
  reimbursementAmountVerifyGroups(before,after);
  return {...after,persistence_recovery:{original:c.persistence_recovery,refresh,monetary_save_replayed:false,temporary_editor_refreshed:true}};
}
