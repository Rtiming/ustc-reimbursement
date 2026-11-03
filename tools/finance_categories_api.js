/* 整组科目改类；来源和完整GUID由本案正式表单冻结。 */
function reimbursementCategoriesInventory(doc,serial,allowProjectRows=false){
  const rows=[];
  for(const e of doc.querySelectorAll('[onclick]')){
    const handler=e.getAttribute('onclick')||'';if(!handler.startsWith("pageOpen_v1('发票信息',"))continue;
    const match=/^pageOpen_v1\('发票信息','([^']+)'\)$/.exec(handler),parts=match?.[1].split('&'),cells=e.closest('tr')?.cells;
    if(!parts||parts.length!==4||!cells||cells.length!==5||!/^.+!P!\w{1,40}$/.test(parts[0])||!/^\d{5,12}$/.test(parts[1].trim())||!parts[2]||!parts[3])throw Error('CATEGORIES_API_PREVIEW_CHANGED');
    rows.push({group:parts[0].split('!P!')[0],group_key:parts[0],subject:parts[1].trim(),subject_name:e.textContent.trim(),project:parts[2],budget:parts[3],amount:cells[4].textContent.trim(),parameters:match[1]});
  }
  if(!rows.length||new Set(rows.map(r=>allowProjectRows?r.parameters:r.group_key)).size!==rows.length)throw Error('CATEGORIES_API_PREVIEW_GROUPS');return rows;
}

async function reimbursementCategoriesQuery(c,http,context){
  const q=c.categories_contract.query;
  const reply=await http.post('commonQuery_doQuery.action?funcno=305&projid=WF_YBX',{_search:false,nd:Date.now(),page:1,rows:'10000',sidx:'',sord:'',userData:JSON.stringify(context),itemArr:'',sectionArr:''});
  const items=Array.isArray(reply)?reply:reply?.rows;
  if(!Array.isArray(items)||!items.length||items.length>=10000||!Array.isArray(reply)&&(![0,1].includes(Number(reply.page))||![0,1].includes(Number(reply.total))))throw Error('CATEGORIES_API_INCOMPLETE');
  return items.map(item=>{if(!Array.isArray(item.cell)||item.cell.length!==q.columns.length)throw Error('CATEGORIES_API_COLUMNS');return Object.fromEntries(q.columns.map((key,i)=>[key,ReimbursementHttpValues.decode(item.cell[i])]));});
}

async function reimbursementCategoriesPrepare(c){
  if(c.funding?.length!==1||c.funding_policy?.invoice_level_required||!c.expense_categories?.length)throw Error('CATEGORIES_API_REQUIRES_SOURCE_BOUND_SINGLE_FUND');
  const meta=await reimbursementAmountInspect({...c,group:'all',subject:'00',initialization_verified:false,include_categories:true});
  const invoices=await reimbursementBusinessApi({...c,action:'bill-read',domain:'used_invoices',contracts:c.used_contracts,expected_amount:c.expected_amount});
  const sections=invoices.sections.filter(s=>s.funcno==='533'&&s.complete===true);if(sections.length!==1)throw Error('CATEGORIES_API_INVOICES_INCOMPLETE');
  const ids=new Set(sections[0].rows.map(r=>r['t.tk_guid'])),changes={};
  for(const p of c.expense_categories){
    const hits=meta.categories.filter(r=>r.group===p.group&&(!p.group_key||r.group_key===p.group_key));
    if(hits.length!==1||hits[0].project!==c.funding[0].code||ReimbursementHttpValues.moneyCents(hits[0].amount)!==ReimbursementHttpValues.moneyCents(p.amount)||p.invoice_guids.some(g=>!ids.has(g)))throw Error('CATEGORIES_API_GROUP_SOURCE_CHANGED');
    changes[hits[0].group_key]={subject:p.subject,subject_name:p.subject_name};
  }
  const requests=[],http=createReimbursementHttp(c.expected_userid,{prefix:'CATEGORIES_API',requests,timeout:15000,allowed:['definition']});
  await reimbursementAmountDefinition(http,'213',c.categories_contract.definition_sha256);
  return{ok:true,complete:true,action:'categories-prepare',case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,business_transport:'native_http',system_write:false,save_dispatched:false,bill:meta.bill,
    original:{bill:meta.bill,basic:meta.basic_sections,categories:meta.categories,invoices:sections[0].rows},changes,definition_sha256:c.categories_contract.definition_sha256,requests:[...meta.requests,...invoices.requests,...requests]};
}

function reimbursementCategoriesApplyResponse(raw,context,c,phase,target){
  // W213初始化返回四个显示/分组字段；逐个接纳，不执行响应脚本，
  // 不放宽共享协议，账号、单号及流程字段不能由分类窗口改写。
  if(typeof raw!=='string'||raw.length>32768)throw Error('CATEGORIES_API_RESPONSE_SIZE');
  const allowed=phase==='initialize'?['BXX_WEB','0-SPLIT_JJSUBJ_FLAG','0-TKS_INFO_MAIN_TITLE','0-TKS_INFO_TITLE_PC_TITLE']:['61-XTYPE'],keys=[];
  for(const part of raw.split('local(').slice(1))for(const pair of part.replace(/\)$/,'').split(';').filter(x=>x.trim())){
    const fieldName=pair.slice(0,pair.indexOf(':')).toUpperCase();
    if(!allowed.includes(fieldName))throw Error('CATEGORIES_API_LOCAL_SCOPE');keys.push(fieldName);
  }
  const parsed=ReimbursementNativeProtocol.response(raw,keys);
  if(!parsed.accepted)throw Error('CATEGORIES_API_NATIVE_'+parsed.status.toUpperCase());
  for(const [fieldName,value]of Object.entries(parsed.updates)){
    if(fieldName==='61-XTYPE'){
      const parts=value.split('&');
      if(!target||parts.length!==4||parts[0]!==target.group_key||parts[1].trim()!==target.subject||parts[2]!==target.project||!['',target.budget].includes(parts[3]))throw Error('CATEGORIES_API_LOCAL_IDENTITY');
    }
    if(fieldName==='0-SPLIT_JJSUBJ_FLAG'&&!['Y','N'].includes(value))throw Error('CATEGORIES_API_LOCAL_VALUE');
    if(fieldName==='0-TKS_INFO_MAIN_TITLE'&&value!=='发票信息'||fieldName==='0-TKS_INFO_TITLE_PC_TITLE'&&value!=='发票内容')throw Error('CATEGORIES_API_LOCAL_VALUE');
    if(fieldName==='BXX_WEB'){
      const rows=JSON.parse(value.replace(/：/g,':'));
      if(!Array.isArray(rows)||rows.length!==1||!rows[0]||typeof rows[0]!=='object'||Array.isArray(rows[0])||
        Object.entries(rows[0]).some(([k,v])=>!/^bxx_web_\d{5,12}$/.test(k)||typeof v!=='string'||v.length>2048))throw Error('CATEGORIES_API_LOCAL_VALUE');
    }
  }
  ReimbursementNativeProtocol.applyResponse(raw,context,keys);return parsed;
}

async function reimbursementCategoriesSave(c){
  let dispatched=false,phase='prepare',responseRaw=null;const requests=[],completed=[],native_responses=[];
  try{
    const p=await reimbursementCategoriesPrepare(c);
    for(const k of ['original','changes','definition_sha256'])if(JSON.stringify(ReimbursementNativeForm.canonical(p[k]))!==JSON.stringify(ReimbursementNativeForm.canonical(c.expected_plan?.[k])))throw Error('CATEGORIES_API_PREPARE_DRIFT');
    const http=createReimbursementHttp(c.expected_userid,{prefix:'CATEGORIES_API',requests,timeout:15000,allowed:['definition','query','button']});
    const d=await reimbursementAmountDefinition(http,'213',c.categories_contract.definition_sha256),win=d.wins.find(w=>String(w.winno)==='213');
    const execute=async(button,context,target)=>{if(button.wf_enabled||button.pre_compute||button.customFunc||button.jscontent)throw Error('CATEGORIES_API_UNSUPPORTED_NATIVE_BRANCH');const cmd=ReimbursementNativeProtocol.command(button.check_func,button.procs,context,{});dispatched=true;responseRaw=null;const raw=await http.post('commonUpdate_responseButtonEvent.action',{cmd,projid:'WF_YBX',userContext:JSON.stringify(context),async:false},'text');responseRaw=typeof raw==='string'?raw.slice(0,32768):null;const parsed=reimbursementCategoriesApplyResponse(raw,context,c,phase,target);native_responses.push({phase,status:parsed.status,updated_keys:Object.keys(parsed.updates)});responseRaw=null;};
    for(const plan of c.expense_categories){
      const entry=p.original.categories.find(r=>r.group===plan.group&&(!plan.group_key||r.group_key===plan.group_key));
      if(entry.subject===plan.subject&&entry.subject_name===plan.subject_name){completed.push({group_key:entry.group_key,no_change:true});continue;}
      const{data:context,set}=http.contextCopy();set('61-serialno',c.serial);set('61-xtype',entry.parameters);set('61-xtype_order',entry.parameters);set('305-COND','');set('305-CONDOBJ',[]);set('305-CURRPAGE',1);
      phase='initialize';await execute({check_func:'',procs:win.beforeLoadProcs},context);
      const rows=await reimbursementCategoriesQuery(c,http,context),sameIds=s=>s.split(',').sort().join(',')===[...plan.invoice_guids].sort().join(',');
      if(rows.some(r=>r['t.xtype'].split('&')[0]!==entry.group_key||r['t.xtype'].split('&')[2]!==c.funding[0].code||ReimbursementHttpValues.moneyCents(r['t.amt'])!==ReimbursementHttpValues.moneyCents(plan.amount)||!sameIds(r['t.tk_guids']))||rows.filter(r=>r['t.select_flag']==='已选').length!==1)throw Error('CATEGORIES_API_COMPLETE_GROUP_CHANGED');
      const target=rows.filter(r=>r['t.jj_subj']===plan.subject&&r['t.jj_subjname']===plan.subject_name);if(target.length!==1)throw Error('CATEGORIES_API_TARGET_NOT_UNIQUE');
      for(const[key,value]of Object.entries(target[0]))set('305-'+key,value);
      const responseTarget={group_key:entry.group_key,subject:plan.subject,project:entry.project,budget:entry.budget};
      phase='select';await execute(win.grid_btns.find(b=>b.btnguid===c.categories_contract.select_button),context,responseTarget);
      const selected=(await reimbursementCategoriesQuery(c,http,context)).filter(r=>r['t.select_flag']==='已选');if(selected.length!==1||selected[0]['t.jj_subj']!==plan.subject)throw Error('CATEGORIES_API_SELECTION_NOT_READ_BACK');
      phase='save';await execute(win.func_btns.find(b=>b.btnguid===c.categories_contract.save_button),context,responseTarget);completed.push({group_key:entry.group_key,subject:plan.subject});
    }
    return{ok:true,action:'categories-save',case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,business_transport:'native_http',system_write:dispatched,save_dispatched:dispatched,state:'saved_independent_readback_required',completed,requests,native_responses};
  }catch(error){return{ok:false,action:'categories-save',case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,business_transport:'native_http',system_write:dispatched,save_dispatched:dispatched,state:dispatched?'unknown_save_readback_required':'rejected_before_save',completed,error:String(error.message),requests,native_responses,failed_phase:phase,native_response:responseRaw};}
}
