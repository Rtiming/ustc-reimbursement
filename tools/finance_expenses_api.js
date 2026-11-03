/* 预算分配使用W216固定保存合同；金额以分计算，整树回读防止漏改其他节点。 */
async function reimbursementExpensesBuild(c) {
  'use strict';
  const requests = [], started = performance.now();
  const http = createReimbursementHttp(c.expected_userid, {prefix:'EXPENSES_API', requests,
    timeout:15000, allowed:['definition','button']});
  const {moneyCents} = ReimbursementHttpValues;
  if (c.funding_policy?.invoice_level_required) throw Error('EXPENSES_API_INVOICE_LEVEL_REQUIRED');
  if (!Array.isArray(c.funding) || !c.funding.length || !Array.isArray(c.expense_allocations) ||
      !['manual','system_default_single_funding'].includes(c.budget_allocation_mode))
    throw Error('EXPENSES_API_PARAMETERS');
  const read = await reimbursementBusinessApi({...c,action:'bill-read',domain:'categories'});
  if (read.bill['t.status'] !== '-99' || read.bill['t.lc_finish'] !== 'F') throw Error('EXPENSES_API_NOT_DRAFT');
  const sections = read.sections.filter(s=>s.funcno==='307' && s.kind==='grid' && s.complete===true);
  if (sections.length!==1) throw Error('EXPENSES_API_TREE_MISSING');
  if(!sections[0].rows.length){
    await reimbursementAmountDefinition(http,'216',c.expenses_contract.definition_sha256);const implicit=await reimbursementDefaultFunding(c);
    return{report:{ok:true,complete:true,action:'expenses-prepare',case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,business_transport:'native_http',system_write:false,save_dispatched:false,
      page_context_modified:false,bill:read.bill,definition_sha256:c.expenses_contract.definition_sha256,original:{implicit_default:implicit.proof},changes:{},requests:[...read.requests,...requests,...implicit.requests]},http,requests,read};
  }
  const definition = await http.post('loadDefinition.action?type=W',{winno:'216',projid:'WF_YBX'});
  const canonical = v => Array.isArray(v) ? v.map(canonical) : v && typeof v==='object' ?
    Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])) : v;
  const projected = Object.fromEntries(['wins','funcs','funcMap','actMap'].map(k=>[k,definition[k]]));
  const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(canonical(projected)))))].map(v=>v.toString(16).padStart(2,'0')).join('');
  if (hash!==c.expenses_contract.definition_sha256) throw Error('EXPENSES_API_DEFINITION_CHANGED');
  const win = definition.wins.find(w=>String(w.winno)==='216');
  const save = win?.func_btns.find(b=>b.btnguid===c.expenses_contract.save_button);
  if (!save || save.pre_check || save.pre_compute || save.wf_enabled || save.jscontent || win.beforeLoadProcs?.length)
    throw Error('EXPENSES_API_SAVE_CONTRACT');
  const columns = c.contracts[0].wireColumns;
  const original = Object.create(null), target = Object.create(null), order = [];
  for (const row of sections[0].rows) {
    if (Object.keys(row).length!==columns.length || columns.some(k=>typeof row[k]!=='string') ||
        row['t.serial_no']!==c.serial || !row['t.ord'] || Object.hasOwn(original,row['t.ord']) ||
        !['0','1','2'].includes(row['t.tlevel']) || /[<>&;\r\n]/.test(row['t.ord']))
      throw Error('EXPENSES_API_TREE_BINDING');
    original[row['t.ord']]={...row}; target[row['t.ord']]={...row}; order.push(row['t.ord']);
  }
  const rows = Object.values(target), leaves = rows.filter(r=>r['t.tlevel']==='2');
  if (!leaves.length || rows.some(r=>r['t.tlevel']==='2' ? r['t.tleaf']!=='true' : r['t.tleaf']!=='false'))
    throw Error('EXPENSES_API_TREE_LEVEL');
  const fundCodes = c.funding.map(f=>f.code);
  if (new Set(fundCodes).size!==fundCodes.length || leaves.some(r=>!fundCodes.includes(r['t.uni_prj_code'])))
    throw Error('EXPENSES_API_PROJECT_SCOPE');
  const sum = values => values.reduce((a,b)=>a+moneyCents(b),0n);
  const decimal = cents => String(cents/100n)+'.'+String(cents%100n).padStart(2,'0');
  if (c.budget_allocation_mode==='system_default_single_funding') {
    if (c.funding.length!==1 || c.expense_allocations.length) throw Error('EXPENSES_API_DEFAULT_PARAMETERS');
  } else {
    if (c.expense_allocations.length!==leaves.length) throw Error('EXPENSES_API_ALL_LEAVES_REQUIRED');
    const used = new Set();
    for (const plan of c.expense_allocations) {
      const matches = leaves.filter(r=>r['t.uni_prj_code']===plan.project && r['t.jj_subj']===plan.subject && r['t.bu_code']===plan.budget);
      if (matches.length!==1 || used.has(matches[0]['t.ord'])) throw Error('EXPENSES_API_LEAF_NOT_UNIQUE');
      const row=matches[0], value=moneyCents(plan.amount);
      if (value>moneyCents(row['t.bu_code_balance'])) throw Error('EXPENSES_API_BALANCE');
      row['t.amt']=decimal(value); used.add(row['t.ord']);
    }
  }
  for (const row of rows.filter(r=>r['t.tlevel']==='1')) {
    const children=leaves.filter(r=>r['t.tparent']===row['t.ord']);
    if (!children.length || children.some(r=>r['t.jj_subj']!==row['t.jj_subj'])) throw Error('EXPENSES_API_PARENT');
    row['t.amt']=decimal(sum(children.map(r=>r['t.amt'])));
  }
  for (const row of leaves) {
    const parent=target[row['t.tparent']];
    if (!parent || parent['t.tlevel']!=='1') throw Error('EXPENSES_API_ORPHAN');
  }
  const roots=rows.filter(r=>r['t.tlevel']==='0');
  for (const row of rows.filter(r=>r['t.tlevel']==='1')) {
    const root=target[row['t.tparent']];
    if (!root || root['t.tlevel']!=='0' || root['t.jj_subj']!==row['t.jj_subj']) throw Error('EXPENSES_API_ROOT');
  }
  for (const root of roots) {
    const children=rows.filter(r=>r['t.tparent']===root['t.ord'] && r['t.tlevel']==='1');
    if (!children.length || sum(children.map(r=>r['t.amt']))!==moneyCents(root['t.amt'])) throw Error('EXPENSES_API_SUBJECT_TOTAL');
  }
  if (sum(roots.map(r=>r['t.amt']))!==moneyCents(c.expected_amount) || sum(leaves.map(r=>r['t.amt']))!==moneyCents(c.expected_amount) ||
      sum(c.funding.map(f=>f.amount))!==moneyCents(c.expected_amount)) throw Error('EXPENSES_API_TOTAL');
  for (const fund of c.funding) if (sum(leaves.filter(r=>r['t.uni_prj_code']===fund.code).map(r=>r['t.amt']))!==moneyCents(fund.amount))
    throw Error('EXPENSES_API_FUND_TOTAL');
  const changes = {};
  for (const id of order) if (moneyCents(target[id]['t.amt'])!==moneyCents(original[id]['t.amt'])) changes[id]={'t.amt':target[id]['t.amt']};
  // 学校grid.fn.getDataByColumns按列顺序用&连接、按行用;连接，并替换值内的分隔符。
  // 拒绝需要HTML控件解析的值；本适配器只序列化已读取的数据。
  const wire = order.map(id=>columns.map(k=>{
    const value=target[id][k];
    if (/^\s*<(?:input|select|ul)\b/i.test(value)) throw Error('EXPENSES_API_EXECUTABLE_CELL');
    return value.replaceAll('&','＆').replaceAll(';','；');
  }).join('&')).join(';');
  const {data:context,set}=http.contextCopy();
  for(const key of Object.keys(context)) if(key.startsWith('WF_YBX$307-')) delete context[key];
  set('61-serialno',c.serial);set('307-ALLTABLEDATA',wire);set('307-dfamt','0');
  const command=ReimbursementNativeProtocol.command(save.check_func,save.procs,context,{});
  const report={ok:true,complete:true,action:'expenses-prepare',case_id:c.case_id,serial:c.serial,
    expected_userid:c.expected_userid,business_transport:'native_http',system_write:false,save_dispatched:false,
    page_context_modified:false,bill:read.bill,definition_sha256:hash,original,changes,
    requests:[...read.requests,...requests],elapsed_ms:Math.round(performance.now()-started),observed_at:new Date().toISOString()};
  return {report,context,command,http,requests,read};
}

async function reimbursementExpensesPrepare(c) { return (await reimbursementExpensesBuild(c)).report; }

async function reimbursementExpensesSave(c) {
  let dispatched=false,prepared=null;
  try {
    if (c.execute!==true || !c.expected_plan) throw Error('EXPENSES_API_WRITE_INTENT');
    const built=await reimbursementExpensesBuild(c);prepared=built.report;
    for (const key of ['original','changes','definition_sha256'])
      if(JSON.stringify(prepared[key])!==JSON.stringify(c.expected_plan[key])) throw Error('EXPENSES_API_PLAN_DRIFT:'+key);
    if(!Object.keys(prepared.changes).length) return {...prepared,action:'expenses-save',state:'already_matches_readback_required',requires_readback:true};
    built.http.guard();dispatched=true;
    const raw=await built.http.post('commonUpdate_responseButtonEvent.action',{cmd:built.command,projid:'WF_YBX',userContext:JSON.stringify(built.context),async:false},'text');
    if(!ReimbursementNativeProtocol.response(raw,[]).accepted) throw Error('EXPENSES_API_SAVE_REJECTED');
    return {...prepared,action:'expenses-save',state:'saved_independent_readback_required',system_write:true,
      save_dispatched:true,requires_readback:true,requests:[...prepared.requests,built.requests.at(-1)]};
  } catch(error) {
    return {ok:false,complete:false,action:'expenses-save',case_id:c.case_id,serial:c.serial,
      expected_userid:c.expected_userid,business_transport:'native_http',system_write:dispatched,save_dispatched:dispatched,
      state:dispatched?'unknown_save_readback_required':'rejected_before_save',requires_readback:true,error:String(error.message),prepared};
  }
}
