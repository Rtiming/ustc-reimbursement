/* 专项逐票拆分：只接受原计划的两项已核经费，不提供任意过程调用。 */
async function reimbursementSplitRead(c,metaOnly=false){
  const m=await reimbursementAmountInspect({...c,subject:'00',initialization_verified:false,include_categories:true,allow_category_project_rows:true});
  const entries=m.categories.filter(r=>r.group===c.group&&(!c.plan.group_keys?.[c.group]||r.group_key===c.plan.group_keys[c.group]));
  if(!entries.length||new Set(entries.map(r=>r.group_key)).size!==1)throw Error('SPLIT_API_GROUP_NOT_UNIQUE');
  const used=await reimbursementBusinessApi({...c,action:'bill-read',domain:'used_invoices',contracts:c.used_contracts,expected_amount:c.expected_amount});
  const sections=used.sections.filter(s=>s.funcno==='533'&&s.complete===true);if(sections.length!==1)throw Error('SPLIT_API_INVOICE_SCOPE');
  const requests=[],http=createReimbursementHttp(c.expected_userid,{prefix:'SPLIT_API',requests,timeout:15000,allowed:['definition','query']});
  await reimbursementAmountDefinition(http,'213',c.split_contract.category_sha256);await reimbursementAmountDefinition(http,'1437',c.split_contract.definition_sha256);
  const report={ok:true,complete:true,action:'split-inspect',case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,business_transport:'native_http',system_write:false,save_dispatched:false,
    original:{bill:m.bill,basic:m.basic_sections,categories:m.categories,invoices:sections[0].rows,entry:entries[0]},definition_sha256:c.split_contract.definition_sha256,requests:[...m.requests,...used.requests,...requests]};
  if(!metaOnly){
    if(c.initialization_verified!==true)throw Error('SPLIT_API_ORIGINAL_INITIALIZATION_REQUIRED');
    const{data:context,set}=http.contextCopy();set('61-serialno',c.serial);set('61-xtype',entries[0].parameters);set('1666-t.select_key','');
    report.keys=await reimbursementSplitQuery(c,http,context,'1666');
    const keys=c.plan.native_keys?.[c.group];if(!Array.isArray(keys)||keys.length!==2)throw Error('SPLIT_API_TWO_EXPLICIT_KEYS_REQUIRED');
    const selected=report.keys.filter(r=>r['t.select_key']===keys[0]);if(selected.length!==1)throw Error('SPLIT_API_KEY_NOT_UNIQUE');set('1666-t.select_key',keys[0]);
    report.rows=await reimbursementSplitQuery(c,http,context,'1669');report.rows_complete=true;
  }
  return report;
}

async function reimbursementSplitMetadata(c){return reimbursementSplitRead(c,true);}
async function reimbursementSplitInspect(c){return reimbursementSplitRead(c,false);}

async function reimbursementSplitQuery(c,http,context,no){
  if(!['1666','1669','305'].includes(no))throw Error('SPLIT_API_QUERY_SCOPE');
  for(const k of Object.keys(context))if(k.startsWith('WF_YBX$'+no+'-')&&!['WF_YBX$1666-T.SELECT_KEY','WF_YBX$1666-T.SELECT_VALUE'].includes(k))delete context[k];
  context['WF_YBX$'+no+'-COND']='';context['WF_YBX$'+no+'-CONDOBJ']=[];context['WF_YBX$'+no+'-CURRPAGE']=1;
  const q=c.split_contract.queries[no],reply=await http.post('commonQuery_doQuery.action?funcno='+no+'&projid=WF_YBX',{_search:false,nd:Date.now(),page:1,rows:'10000',sidx:'',sord:'',userData:JSON.stringify(context),itemArr:'',sectionArr:''});
  const items=Array.isArray(reply)?reply:reply?.rows;
  if(!Array.isArray(items)||!items.length||items.length>=10000||!Array.isArray(reply)&&(![0,1].includes(Number(reply.page))||![0,1].includes(Number(reply.total))))throw Error('SPLIT_API_INCOMPLETE_QUERY');
  return items.map(item=>{if(!Array.isArray(item.cell)||item.cell.length!==q.columns.length)throw Error('SPLIT_API_QUERY_COLUMNS');return Object.fromEntries(q.columns.map((k,i)=>[k,ReimbursementHttpValues.decode(item.cell[i])]));});
}

function reimbursementSplitAmounts(c,rows,keys,owned,groupGuids){
  const money=ReimbursementHttpValues.moneyCents,policy=c.plan.policy,people=c.plan.people.filter(p=>p.group===c.group),target=new Map(people.map(p=>[p.invoice_guid,p])),seen=new Set();
  if(new Set(rows.map(r=>r['t.tk_guid'])).size!==rows.length||rows.length!==groupGuids.length||groupGuids.some(g=>!rows.some(r=>r['t.tk_guid']===g))||rows.some(r=>!owned.includes(r['t.tk_guid'])))throw Error('SPLIT_API_GROUP_GUID_SET');
  const wc=[policy.subject,policy.project,policy.budget].join('!P!');if(keys[0]!==wc||keys[1].split('!P!')[1]===policy.project||keys.some(k=>!/^\d+!P![A-Z]+\d+!P![A-Z]+\d+$/.test(k))||c.plan.funding.length!==2||!c.plan.funding.some(f=>f.code===keys[1].split('!P!')[1]))throw Error('SPLIT_API_TARGET_FUNDING');
  const decimal=n=>String(n/100n)+'.'+String(n%100n).padStart(2,'0');
  return rows.map(row=>{
    if(row['t.serial_no']!==c.serial||!/^\d+$/.test(row['t.inner_ord']))throw Error('SPLIT_API_ROW_IDENTITY');
    const face=money(row['t.amt']),person=target.get(row['t.tk_guid']),eligible=person&&(c.group!=='交通运输服务'||row['t.invoice_type'].includes('国内机票款'));
    if(person&&(money(person.invoice_amount)!==face||!eligible))throw Error('SPLIT_API_ELIGIBILITY_OR_FACE_CHANGED');
    const first=person?money(person.wc_amount):0n;if(first>face||first>money(policy.person_cap))throw Error('SPLIT_API_PERSON_CAP');if(person)seen.add(person.invoice_guid);
    // 原有第三项绑定不能被两项计划静默替换。
    const original=[...row['t.sj_bf'].matchAll(/(\d+)-([A-Z]+\d+)-([A-Z]+\d+)-(\d+(?:\.\d{1,2})?)(?!\d)/g)];
    if(row['t.sj_bf'].trim()&&!original.length||original.some(m=>!keys.includes([m[1],m[2],m[3]].join('!P!'))))throw Error('SPLIT_API_UNPLANNED_ORIGINAL_BINDING');
    return{...row,amounts:[decimal(first),decimal(face-first)]};
  }).map((row,i,all)=>{if(i===all.length-1&&seen.size!==people.length)throw Error('SPLIT_API_MISSING_ELIGIBLE_INVOICE');return row;});
}

async function reimbursementSplitApply(c){
  let dispatched=false;const requests=[],steps=[];
  try{
    if(!c.pending_registered||!c.expected_plan)throw Error('SPLIT_API_PENDING_REQUIRED');
    const p=await reimbursementSplitRead(c,true);for(const k of ['original','definition_sha256'])if(JSON.stringify(ReimbursementNativeForm.canonical(p[k]))!==JSON.stringify(ReimbursementNativeForm.canonical(c.expected_plan[k])))throw Error('SPLIT_API_PREPARE_DRIFT');
    const http=createReimbursementHttp(c.expected_userid,{prefix:'SPLIT_API',requests,timeout:15000,allowed:['definition','query','button']});
    const d=await reimbursementAmountDefinition(http,'213',c.split_contract.category_sha256),s=await reimbursementAmountDefinition(http,'1437',c.split_contract.definition_sha256),w=d.wins.find(w=>String(w.winno)==='213');
    if(http.contextCopy().get('0-SPLIT_JJSUBJ_FLAG')!=='Y')throw Error('SPLIT_API_NATIVE_FEATURE_DISABLED');
    const{data:context,set}=http.contextCopy();set('61-serialno',c.serial);set('61-xtype',p.original.entry.parameters);set('61-xtype_order',p.original.entry.parameters);
    const execute=async b=>{if(!b||b.wf_enabled||b.pre_compute||b.customFunc||b.jscontent)throw Error('SPLIT_API_BUTTON_CONTRACT');const cmd=ReimbursementNativeProtocol.command(b.check_func,b.procs,context,{});dispatched=true;const raw=await http.post('commonUpdate_responseButtonEvent.action',{cmd,projid:'WF_YBX',userContext:JSON.stringify(context),async:false},'text');if(!ReimbursementNativeProtocol.response(raw,[]).accepted)throw Error('SPLIT_API_REJECTED_OR_CONFIRMATION_REQUIRED');steps.push(b.caption||'initialize');};
    const initialize=async()=>{await execute({check_func:'',procs:w.beforeLoadProcs});await execute(w.func_btns.find(b=>b.btnguid===c.split_contract.open_button));};
    await initialize();
    const category=await reimbursementSplitQuery(c,http,context,'305'),guids=[...new Set(category.flatMap(r=>r['t.tk_guids'].split(',')))];
    if(!guids.length||category.some(r=>r['t.xtype'].split('&')[0]!==p.original.entry.group_key))throw Error('SPLIT_API_CATEGORY_GROUP_CHANGED');
    const keyrows=await reimbursementSplitQuery(c,http,context,'1666'),keys=c.plan.native_keys?.[c.group];
    if(!Array.isArray(keys)||keys.length!==2||keys.some(k=>keyrows.filter(r=>r['t.select_key']===k).length!==1))throw Error('SPLIT_API_EXPLICIT_NATIVE_KEYS_REQUIRED');
    set('1666-t.select_key',keys[0]);let rows=await reimbursementSplitQuery(c,http,context,'1669');
    const original=rows,target=reimbursementSplitAmounts(c,rows,keys,p.original.invoices.map(r=>r['t.tk_guid']),guids),update=s.wins.find(w=>String(w.winno)==='1435').func_btns.find(b=>b.caption==='三、更新绑定报销项');
    for(let i=0;i<keys.length;i++){
      const key=keyrows.find(r=>r['t.select_key']===keys[i]);set('1666-t.select_key',keys[i]);set('1666-t.select_value',key['t.select_value']);
      const current=await reimbursementSplitQuery(c,http,context,'1669');if(current.length!==original.length||current.some(r=>!original.some(o=>o['t.tk_guid']===r['t.tk_guid']&&o['t.serial_no']===r['t.serial_no']&&o['t.inner_ord']===r['t.inner_ord']&&o['t.amt']===r['t.amt']&&o['t.invoice_type']===r['t.invoice_type'])))throw Error('SPLIT_API_INTERMEDIATE_SCOPE_CHANGED');
      const fields=['t.split_amt','t.serial_no','t.tk_guid','t.inner_ord','t.invoice_type'];
      const wire=current.map(r=>fields.map(k=>k==='t.split_amt'?target.find(t=>t['t.tk_guid']===r['t.tk_guid']).amounts[i]:r[k]).map(v=>v.replaceAll('&','＆').replaceAll(';','；')).join('&')).join(';');
      set('61-16669_datas',wire);await execute(update);
    }
    await execute(s.wins.find(w=>String(w.winno)==='1436').func_btns.find(b=>b.caption==='四、确认拆分结果并保存'));
    // 重新执行官方打开过程，再由下一独立任务核持久分配。
    await initialize();
    return{ok:true,action:'split-apply',case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,business_transport:'native_http',system_write:true,save_dispatched:true,state:'saved_independent_readback_required',initialization_verified:true,original_rows:original,target_rows:target,keys,steps,requests};
  }catch(error){return{ok:false,action:'split-apply',case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,business_transport:'native_http',system_write:dispatched,save_dispatched:dispatched,state:dispatched?'unknown_original_split_recovery_required':'rejected_before_dispatch',error:String(error.message),steps,requests};}
}
