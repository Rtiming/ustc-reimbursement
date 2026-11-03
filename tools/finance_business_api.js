/* Native multi-record form bindings may serialize all rows in one object. */
function reimbursementExpandFormRows(form,rows){
  if(form.multiRec!==true||rows.length!==1)return rows;
  const row=rows[0],names=form.formFields.map(f=>f.fieldname.replace(/^[A-Za-z][A-Za-z0-9_]*\./,'').toUpperCase());
  const lengths=names.filter(k=>typeof row[k]==='string'&&row[k].includes('$|')).map(k=>row[k].split('$|').length);
  if(!lengths.length)return rows;
  const count=lengths[0];if(count>1000||lengths.some(n=>n!==count))throw Error('BUSINESS_API_FORM_MULTIROW_LENGTH');
  const result=Array.from({length:count},()=>({}));
  for(const[key,value]of Object.entries(row)){
    if(value===null||value===''){for(const r of result)r[key]=value;continue;}
    if(typeof value!=='string'||value.split('$|').length!==count)throw Error('BUSINESS_API_FORM_MULTIROW_FIELD:'+key);
    value.split('$|').forEach((v,i)=>result[i][key]=v);
  }
  return result;
}
/* 固定业务HTTP入口；所有查询都绑定当前账号，分页完整性失败不返回完整清单。 */
async function reimbursementBusinessApi(c) {
  'use strict';
  const requests = [], started = performance.now();
  const http = createReimbursementHttp(c.expected_userid, {prefix: 'BUSINESS_API', requests,
    // 完整清单包含总数聚合和分页；与单号查询使用不同的固定预算。
    timeout: ['bills-list','approvals-list','approval-history'].includes(c.action)?30000:15000, allowed: ['definition', 'gridDefinition', 'query', 'binding']});
  const {post} = http;
  if (!c.case_id || !/^[A-Za-z0-9]{2,40}$/.test(c.expected_userid)) throw Error('BUSINESS_API_BINDING');
  // 查询上下文独立副本；不修改用户正在编辑的页面字段或全局UC。
  const {data: context, set} = http.contextCopy();
  const {decode, moneyCents, formRows} = ReimbursementHttpValues;
  async function readSections(contracts) {
    const definitions = new Map(), sections = [];
    for (const contract of contracts) {
      if (!definitions.has(contract.winno)) definitions.set(contract.winno, await definition(contract.winno));
      const found = definitions.get(contract.winno).funcs.filter(f => String(f.funcno) === contract.funcno);
      if (found.length !== 1) throw Error('BUSINESS_API_FORM_DEFINITION');
      const native = found[0];
      if (contract.bindSql) {
        if (native.bindSql !== contract.bindSql) throw Error('BUSINESS_API_BINDING_DEFINITION_CHANGED');
        const [sql, fields] = contract.bindSql.split('|');
        if (!/^\$[a-f0-9]{32}$/.test(sql) || !fields || fields.split(',').some(f => !/^#(?:0-userinfo\.(?:userid|username)|61-(?:serialno|node_id|wf_name|XTYPE|LC_PARAMS_NAME|LC_PARAMS_UUID))#$/i.test(f)))
          throw Error('BUSINESS_API_BINDING_CONTRACT');
        const cvstr = fields.split(',').map(field => {
          const key = field.slice(1, -1).toUpperCase();
          const value = context[(key.startsWith('0-') ? '' : 'WF_YBX$') + key];
          if (typeof value !== 'string' || !value || value.includes('$$') || value.includes('::')) throw Error('BUSINESS_API_BINDING_VALUE');
          return field + '::' + value;
        }).join('$$');
        const data = reimbursementExpandFormRows(native,formRows(await post('common_bindSQLDataBackend.action', {sqlid: sql.slice(1), cvstr, projid: 'WF_YBX'}, 'text')));
        const defaulted = new Set();
        if (contract.emptyDefaults) {
          if (contract.emptyDefaults.some(key=>!native.formFields?.some(f=>f.fieldname.replace(/^[A-Za-z][A-Za-z0-9_]*\./,'').toUpperCase()===key && f.initdata===''))) throw Error('BUSINESS_API_BOUND_DEFAULT_CHANGED');
          for (const row of data) for (const key of contract.emptyDefaults) if (!Object.hasOwn(row,key)) {row[key]='';defaulted.add(key);}
        }
        sections.push({funcno: contract.funcno, kind: 'form', rows: data, ...(defaulted.size?{native_empty_defaults:[...defaulted]}:{})});
      } else {
        if (native.tablenames !== contract.tablenames || native.needcount !== contract.needcount || !!native.treeGrid !== contract.treeGrid || JSON.stringify(native.colModel?.map(v => v.name)) !== JSON.stringify(contract.columns))
          throw Error('BUSINESS_API_GRID_DEFINITION_CHANGED');
        const no = contract.funcno, data = []; let records = null, pages = 1;
        let wireColumns = contract.wireColumns || contract.columns;
        if (contract.wireColumns && (native.prjfields !== wireColumns.join(',') || JSON.stringify(wireColumns.slice(0, contract.columns.length)) !== JSON.stringify(contract.columns)))
          throw Error('BUSINESS_API_TREE_COLUMNS_CHANGED');
        if (contract.queryColumns) {
          // W198内嵌的旧列少于实际Q280；官方grid从type=Q取列，不能按W列错位解读人员和金额。
          const raw = await post('loadDefinition.action?type=Q', {funcNo: no, projid: 'WF_YBX'});
          const queries = Array.isArray(raw) ? raw : [raw];
          if (queries.length !== 1 || String(queries[0]?.funcno) !== no ||
              JSON.stringify(queries[0]?.colModel?.map(v => v.name)) !== JSON.stringify(contract.queryColumns))
            throw Error('BUSINESS_API_QUERY_COLUMNS_CHANGED');
          wireColumns = contract.queryColumns;
        }
        // 53原生默认排序为空；按唯一GUID排序，避免跨页遇到同日期票据时重复或漏行。
        const sortField = no === '53' ? 't.tk_guid' : '';
        if (sortField && !wireColumns.includes(sortField)) throw Error('BUSINESS_API_POOL_SORT_CONTRACT');
        set(no + '-COND', ''); set(no + '-CONDOBJ', []); set(no + '-CURRPAGE', 1);
        for (let page = 1; page <= pages; page++) {
          let reply = await post('commonQuery_doQuery.action?funcno=' + no + '&projid=WF_YBX' + (contract.needcount ? '&needcount=true' : ''), {
            _search: false, nd: Date.now(), page, rows: contract.needcount ? '100' : '10000', sidx: sortField, sord: sortField ? 'asc' : '', userData: JSON.stringify(context), itemArr: '', sectionArr: ''});
          if(!contract.needcount && Array.isArray(reply)) reply={page:1,total:1,rows:reply};
          const n = Number(reply?.records), p = Number(reply?.total);
          if (contract.needcount && (!Number.isSafeInteger(n) || n < 0 || n > 5000 || !Number.isSafeInteger(p) || p !== Math.ceil(n / 100) ||
              !(Number(reply.page) === page || n === 0 && Number(reply.page) === 0) || !Array.isArray(reply.rows) ||
              reply.rows.length !== Math.min(100, Math.max(0, n - (page - 1) * 100)) || records !== null && (n !== records || p !== pages)))
            throw Error('BUSINESS_API_SECTION_INCOMPLETE:' + JSON.stringify({funcno: no, records: reply?.records, total: reply?.total, page: reply?.page, rows: Array.isArray(reply?.rows) ? reply.rows.length : null}));
          if (!contract.needcount && (!Array.isArray(reply?.rows) || reply.rows.length >= 10000 || ![0, 1].includes(Number(reply.page)) || ![0, 1].includes(p)))
            throw Error('BUSINESS_API_UNPAGED_LIMIT');
          records = contract.needcount ? n : reply.rows.length; pages = contract.needcount ? p : 1;
          for (const row of reply.rows) {
            if (!Array.isArray(row.cell) || row.cell.length !== wireColumns.length) throw Error('BUSINESS_API_ROW_COLUMNS:' + JSON.stringify({funcno: no, expected: wireColumns.length, actual: Array.isArray(row.cell) ? row.cell.length : null}));
            data.push(Object.fromEntries(wireColumns.map((col, i) => [col, decode(row.cell[i])])));
          }
        }
        sections.push({funcno: no, kind: 'grid', rows: data, complete: data.length === records});
      }
    }
    return sections;
  }
  async function definition(winno) {
    const value = await post('loadDefinition.action?type=W', {winno, projid: 'WF_YBX'});
    if (!value || !Array.isArray(value.wins) || !Array.isArray(value.funcs)) throw Error('BUSINESS_API_DEFINITION');
    return value;
  }
  function result(value) {
    return {ok: true, action: c.action, case_id: c.case_id, expected_userid: c.expected_userid,
      business_transport: 'native_http', system_write: false, requests,
      observed_at: new Date().toISOString(), elapsed_ms: Math.round(performance.now() - started), ...value};
  }
  // 开发检查只读取现有实现明确引用的窗口定义，不接受URL、过程或任意窗口号。
  const domains = {bills: ['70', '351', '1421'], details: ['69'], basic: ['43'], funding: ['170','272'],
    payments: ['13', '56', '584'], categories: ['213', '216', '282', '276', '1437'], submission: ['41', '76', '512', '1166', '1160', '74', '373'], approval: ['74', '75', '76', '373', '2075'],
    invoices: ['33', '60'], recognition: ['79'], invoice_edit: ['1230', '938', '288', '325', '122', '1492', '944'], remuneration: ['198', '199'],
    notes: ['54'], pre_review: ['1099'], used_invoices: ['386'], invoice_allocations:['276'], travel:['122','1492','128','944','353']};
  if(c.action==='approval-filters'){
    const d=await definition('74'),f=d.funcs.find(f=>String(f.funcno)==='514');
    const field=f?.formFields?.find(f=>f.fieldname==='t.approval_type');
    if(field?.binding_data!=='@$400337949da16163fd1bd63576ca50d6|')throw Error('APPROVAL_FILTER_BINDING_CHANGED');
    const raw=await post('common_bindSQLDataBackend.action',{sqlid:'400337949da16163fd1bd63576ca50d6',cvstr:'',projid:'WF_YBX'},'text');
    return result({complete:true,native_options_raw:raw,binding:field.binding_data,system_write:false});
  }
  if (c.action === 'definitions') {
    if (!Object.hasOwn(domains, c.domain)) throw Error('BUSINESS_API_DOMAIN');
    const definitions = [];
    for (const winno of domains[c.domain]) {
      try {definitions.push({winno, definition: await definition(winno)});}
      catch (error) {definitions.push({winno, error: String(error.message)}); break;}
    }
    return result({domain: c.domain, complete: definitions.length === domains[c.domain].length && definitions.every(d => d.definition),
      ...(c.domain==='approval'?{native_approval_policy:Object.fromEntries(['0-is_argee_default','0-LC_AGREE_BTN_NAME','0-LC_REJECT_BTN_NAME','0-MANUAL_NODE','0-is_jbrfzrbmfzr','0-NEED_SP_YZM'].map(key=>[key,window.jQuery.UC.bindData('#'+key+'#')??null])),native_approval_entry_sources:Object.fromEntries(['before_dsp_approval','after_dsp_approval','after_lc_agree','after_lc_reject','getLcSignModel'].map(name=>[name,typeof window[name]==='function'?String(window[name]):null])),native_batch_serializer:String(window.jQuery.grid?.fn?.getRowsDataEx||''),native_approval_button_hooks:definitions.flatMap(d=>(d.definition?.wins||[]).flatMap(w=>(w.func_btns||[]).map(b=>({winno:w.winno,guid:b.btnguid,before_type:typeof b.beforeclick,before:String(b.beforeclick||''),after_type:typeof b.afterclick,after:String(b.afterclick||'')})))),native_approval_scripts:[...document.scripts].map(s=>s.src).filter(u=>u&&new URL(u).origin===location.origin).map(u=>new URL(u).pathname),native_engine_members:Object.fromEntries(['page','button','grid','W'].map(k=>[k,Object.getOwnPropertyNames(window.jQuery[k]||{}).map(n=>({name:n,type:typeof window.jQuery[k][n]}))])),native_approval_sources:Object.fromEntries([['query',window.jQuery.Q],['query_fn',window.jQuery.Q?.fn],['grid_fn',window.jQuery.grid?.fn],['button',window.jQuery.button],['page',window.jQuery.page],['page_button',window.jQuery.page?.btn],['context',window.jQuery.UC],['window',window]].filter(([,o])=>o).flatMap(([owner,o])=>Object.getOwnPropertyNames(o).flatMap(name=>{const fn=Object.getOwnPropertyDescriptor(o,name)?.value;if(typeof fn!=='function')return[];const source=String(fn);return /MultiRows|MultiCount|MULTIROWS|MULTICOUNT|yzm_|before_sp_yzm|generateCmd|beforeclick|afterclick|after_dsp_batchAudit|loadDefinition|loadFunc|loadScript|2319|373/.test(name+' '+source)?[[owner+'.'+name,source]]:[];})))}:{}),
      ...(c.domain==='submission'?{native_handoff_surface:{page:String(window.jQuery.page?.currPage||''),editable_controls:[...document.querySelectorAll('input,select,textarea')].filter(e=>e.getClientRects().length&&!e.disabled&&!e.readOnly&&e.type!=='hidden').map(e=>({id:e.id,tag:e.tagName,type:e.type,form:e.closest('form')?.id||'',pager:e.closest('.ui-pg-table')?.className||''}))}}:{}),
      ...(c.domain==='submission'?{native_workflow_sources:Object.fromEntries([['jquery',window.jQuery],...['page','UC','C','F','Q','B','WF','wf','button'].map(k=>[k,window.jQuery[k]]),['window',window]].filter(([,o])=>o).flatMap(([owner,o])=>Object.getOwnPropertyNames(o).flatMap(name=>{const fn=Object.getOwnPropertyDescriptor(o,name)?.value;if(typeof fn!=='function')return[];const s=String(fn);return /wfSecretaryRoleGuid|commonUpdate_responseButtonEvent|wfAct|wfvarstr|batch.*approval|approval.*batch/i.test(s)?[[owner+'.'+name,s]]:[];})))}:{}),
      ...(c.domain==='submission'?{native_applicant_protocol:{generate_command:String(window.jQuery.page?.btn?.generateCmd||''),open_sign:String(window.jQuery.openSign||''),signature_model:String(window.getLcSignModel||''),pdf_compensation:String(window.lcpdf_blnew||''),hooks:Object.fromEntries(Object.getOwnPropertyNames(window).filter(name=>/^before_.*(?:agree|pl)|^after_(?:bx_lcfinsh|lc_agree|lc_agree_bxpdf)$/.test(name)&&typeof Object.getOwnPropertyDescriptor(window,name)?.value==='function').map(name=>[name,String(window[name])]))}}:{}),
      ...(c.domain==='submission'?{native_active_applicant_buttons:Object.values(window.jQuery.page?.list||{}).flatMap(p=>(p.wins||[]).filter(w=>String(w.winno)==='76').flatMap(w=>w.func_btns||[])).filter(b=>b.caption==='同意').map(b=>({beforeclick:String(b.beforeclick||''),afterclick:String(b.afterclick||'')}))}:{}),
      ...(c.domain==='submission'?{native_applicant_checks:Object.fromEntries(['before_lc_agree','before_sp_yzm'].map(name=>[name,typeof window[name]==='function'?String(window[name]):null])),native_verification_sources:Object.fromEntries(['yzm_init_1362','yzm_jy_1362','before_sp_yzm'].filter(name=>typeof Object.getOwnPropertyDescriptor(window,name)?.value==='function').map(name=>[name,String(Object.getOwnPropertyDescriptor(window,name).value)])),native_page_open_external:String(window.jQuery.page?.openExternal||''),native_context_set_data:String(window.jQuery.UC?.setData||'')}:{}),
      ...(c.domain==='remuneration'?{native_form_sources:Object.fromEntries(['appendXmSelectItembySql','appendSelectItembySql','createSelectAfterGetData','parseParamsToData','getSelectCheckOptions'].map(name=>[name,String(window.jQuery.F?.[name]||'')]))}:{}),
      definitions, business_actions_executed: false, ...(c.domain==='payments'?{supporting_queries:{bank:await post('loadDefinition.action?type=Q',{funcNo:'80',projid:'WF_YBX'})},native_engine_sources:{form_getDataToContext:String(window.jQuery.F?.getDataToContext||''),form_getFormData:String(window.jQuery.F?.getFormData||''),form_wrapBody:String(window.jQuery.F?.wrapFormBody||''),field_call_proc:String(window.jQuery.callProc||''),bind_event:String(window.jQuery.bindEvent||''),form_input_url:window.jQuery.form?.options?.inputDataURL||'',context_callFunc:String(window.jQuery.C?.callFunc||''),event_functions:Object.fromEntries(Object.entries(window.jQuery.F?.eventFunc||{}).filter(([k,v])=>typeof v==='function').map(([k,v])=>[k,String(v)])),query_loadMultiRows:String(window.jQuery.Q?.loadMultiRows||''),query_functions:Object.fromEntries(Object.entries(window.jQuery.Q?.fn||{}).filter(([k,v])=>typeof v==='function').map(([k,v])=>[k,String(v)])),context_bindData:String(window.jQuery.UC?.bindData||'')},native_engine_inventory:Object.fromEntries(['Q','F','UC','C'].map(key=>[key,window.jQuery[key]?Object.keys(window.jQuery[key]).map(name=>({name,type:typeof window.jQuery[key][name]})):[]]))}:{})});
  }
  if(['approval-log','approval-context'].includes(c.action)){
    const target=c.approval_target;
    if(!target||!/^\d+$/.test(target.serial)||!/^\d+$/.test(target.instance)||!/^\w+$/.test(target.workflow)||
       !Array.isArray(target.source_rows)||!target.source_rows.length||
       JSON.stringify(c.contracts?.map(c=>c.funcno).sort())!==JSON.stringify(c.action==='approval-context'?['109','1816']:['1816']))throw Error('APPROVAL_LOG_SCOPE');
    for(const row of target.source_rows)if(row['t.serial_no']!==target.serial||row['t.uni_key']!==target.instance||row['t.wfname']!==target.workflow)throw Error('APPROVAL_LOG_SOURCE_CHANGED');
    set('61-serialno',target.serial);set('61-wf_name',target.workflow);set('61-uni_key',target.instance);
    if(c.action==='approval-context'){
      const nodes=[...new Set(target.source_rows.map(r=>r['t.node_id']))];
      if(nodes.length!==1||!/^\d+$/.test(nodes[0]))throw Error('APPROVAL_NODE_AMBIGUOUS');
      set('61-node_id',nodes[0]);
    }
    const sections=await readSections(c.contracts);
    const log=sections.find(s=>s.funcno==='1816');
    if(log?.complete!==true)throw Error('APPROVAL_LOG_INCOMPLETE');
    const approved=await reimbursementBusinessApi({...c,action:'approval-history',approval_serial:target.serial,contracts:c.approval_history_contracts});
    if(approved.complete!==true||approved.rows.some(row=>row['t.serial_no']!==target.serial))throw Error('APPROVAL_LOG_APPROVED_SCOPE');
    return result({complete:true,account_scope:'current_approver_source',target,history:log.rows,
      ...(c.action==='approval-context'?{approval_fields:sections.find(s=>s.funcno==='109').rows}:{}),
      approved_rows:approved.rows,source_reference:target.source,page_context_modified:false,selection_write:false,requests:requests.concat(approved.requests)});
  }
  if (['approvals-list','approval-history'].includes(c.action)) {
    const history=c.action==='approval-history',windowNo=history?'75':'74',queryNo=history?'107':'105',filterNo=history?'719':'514';
    const expected=c.contracts?.[0];
    if(c.contracts?.length!==1||expected?.winno!==windowNo||expected.funcno!==queryNo)throw Error('APPROVAL_QUEUE_CONTRACT');
    const def=await definition(windowNo),grid=def.funcs.find(f=>String(f.funcno)===queryNo),filter=def.funcs.find(f=>String(f.funcno)===filterNo);
    if(grid?.multifields!==expected.multifields||!Array.isArray(filter?.formFields)||
       filter.formFields.some(f=>f.initdata!==''))throw Error('APPROVAL_QUEUE_FILTER_CHANGED');
    for(const token of expected.tablenames.matchAll(new RegExp('#'+filterNo+'-([^#]+)#','g'))) {
      if(!filter.formFields.some(f=>f.fieldname===token[1]))throw Error('APPROVAL_QUEUE_FILTER_MISSING');
      set(filterNo+'-'+token[1],'');
    }
    if(c.approval_serial!==undefined){
      if(!/^\d+$/.test(c.approval_serial))throw Error('APPROVAL_HISTORY_SERIAL');
      set(filterNo+'-t.serial_no',c.approval_serial);
    }
    set(queryNo+'-COND','');set(queryNo+'-CONDOBJ',[]);set(queryNo+'-CURRPAGE',1);
    const sections=await readSections(c.contracts),rows=sections[0].rows,keys=new Set();
    if(sections[0].complete!==true)throw Error('APPROVAL_QUEUE_INCOMPLETE');
    for(const row of rows){
      const key=JSON.stringify([row['t.serial_no'],row['t.uni_key'],row['t.node_id'],row['t.collab_ord']]);
      if(!/^\d+$/.test(row['t.serial_no'])||(!history&&keys.has(key)))throw Error('APPROVAL_QUEUE_ROW_IDENTITY');
      moneyCents(row['t.amount']);keys.add(key);
    }
    return result({complete:true,account_scope:history?'current_approver_history':'current_approver',identity:{userid:c.expected_userid},
      query_scope:(history?'native_approved_list':'native_approval_list')+(c.approval_serial?'_exact_serial':'_empty_filters'),rows,total_records:rows.length,
      role_labels_from_native:true,approval_permission_not_inferred:true,
      page_context_modified:false,selection_write:false});
  }
  if (c.action==='invoice-inventory') {
    const pool=await reimbursementBusinessApi({...c,action:'invoices-list',contracts:c.contracts.invoices});
    const recognition=await reimbursementBusinessApi({...c,action:'recognition-list',contracts:c.contracts.recognition});
    requests.push(...pool.requests,...recognition.requests);
    return result({complete:true,projid:'WF_YBX',identity:{userid:c.expected_userid},account_scope:'current_user_daily_invoice_inventory',
      grids:[{funcno:'53',rows:pool.rows,records:pool.total_records,complete:true},
        {funcno:'113',rows:recognition.rows,records:recognition.total_records,complete:true}],
      page_context_modified:false,selection_write:false});
  }
  if (c.action === 'recognition-list') {
    if (!Array.isArray(c.contracts) || c.contracts.length!==1 || c.contracts[0].winno!=='79' || c.contracts[0].funcno!=='113') throw Error('BUSINESS_API_RECOGNITION_CONTRACT');
    // 原生fp_clz只清两个本地条件；这里清独立上下文，不打开窗口或改变用户页面。
    set('113-t.tk_filecode','');set('113-t.show_filename','');
    const sections=await readSections(c.contracts),rows=sections[0].rows,ids=rows.map(r=>r['t.tk_filecode']);
    if (ids.some(id=>!id) || new Set(ids).size!==ids.length) throw Error('BUSINESS_API_RECOGNITION_DUPLICATE');
    return result({complete:true,account_scope:'current_user_invoice_recognition',rows,total_records:rows.length,
      page_context_modified:false,selection_write:false});
  }
  if (c.action === 'invoices-list') {
    if (!Array.isArray(c.contracts) || c.contracts.length !== 1 || c.contracts[0].funcno !== '53') throw Error('BUSINESS_API_POOL_CONTRACT');
    // 对应官方日常入口BX_01_1_；清空可选筛选，不改变页面全局上下文或勾选状态。
    for (const field of ['61-has_chosen','61-trip_tkguids','0-ty_trip_id','0-tk_sx_id']) set(field, '');
    set('61-bx_jbxx_bx_type', '1');
    const sections = await readSections(c.contracts), rows = sections[0].rows;
    const ids = rows.map(r => r['t.tk_guid']);
    if (ids.some(id => !id) || new Set(ids).size !== ids.length) throw Error('BUSINESS_API_POOL_DUPLICATE:' + JSON.stringify({records:ids.length,unique:new Set(ids).size,missing:ids.filter(id=>!id).length,duplicate_guids:[...new Set(ids.filter((id,i)=>ids.indexOf(id)!==i))].slice(0,10)}));
    return result({complete: true, account_scope: 'current_user_daily_invoice_pool', rows,
      total_records: rows.length, page_context_modified: false, selection_write: false});
  }
  if (!['bills-list', 'bill-read'].includes(c.action)) throw Error('BUSINESS_API_ACTION');
  if (c.action === 'bill-read' && (!/^\d+$/.test(c.serial) || !/^\d+\.\d{2}$/.test(c.expected_amount) || !Array.isArray(c.contracts)))
    throw Error('BUSINESS_API_CASE_BINDING');
  const list = await definition('70'), grid = list.funcs.find(f => String(f.funcno) === '101');
  if (!grid?.tablenames?.startsWith('(@{ybx_query_pc_package.wf_query_main_list_v1:') ||
      grid.multifields !== 't.serial_no' || !Array.isArray(grid.colModel)) throw Error('BUSINESS_API_BILL_DEFINITION');
  const required = ['t.serial_no', 't.REQ_INFO', 't.status', 't.status_name', 't.total_amt', 't.abst'];
  if (required.some(name => !grid.colModel.some(col => col.name === name))) throw Error('BUSINESS_API_BILL_COLUMNS');
  for (const token of grid.tablenames.matchAll(/#99-([^#]+)#/g)) set('99-' + token[1], '');
  // serials只由已核清单/导出owner注入，不接受公开任意条件或SQL。
  if (c.serials && (!Array.isArray(c.serials) || !c.serials.length || c.serials.length>25 ||
      c.serials.some(s=>typeof s!=='string' || !/^\d+$/.test(s)) || new Set(c.serials).size!==c.serials.length)) throw Error('BUSINESS_API_SERIAL_SCOPE');
  set('99-t.serial_no', c.action === 'bill-read' ? c.serial : c.serials?.join(',') || ''); set('99-t.cx_type', 'USER');
  if (c.list_view) {
    if (c.list_view!=='ready_for_delivery' || c.serials || c.action!=='bills-list' ||
        !String(context['0-YBSTATUS']||'').split(',').includes('BX_YBXWC|审批完成')) throw Error('BUSINESS_API_LIST_VIEW');
    set('99-t.status','BX_YBXWC');
  }
  set('61-mainlist_params', 'BX_YBXALL'); set('101-COND', '');
  set('101-CONDOBJ', []); set('101-CURRPAGE', 1);
  const rows = [], serials = new Set(); let count = null, pages = null;
  for (let page = 1; page <= (pages ?? 1); page++) {
    const reply = await post('commonQuery_doQuery.action?funcno=101&projid=WF_YBX&needcount=true', {
      _search: false, nd: Date.now(), page, rows: '100', sidx: '', sord: '',
      userData: JSON.stringify(context), itemArr: '', sectionArr: ''});
    const records = Number(reply?.records), total = Number(reply?.total);
    if (!Number.isSafeInteger(records) || records < 0 || records > 5000 || !Number.isSafeInteger(total) ||
        total < 0 || total > 50 || !(Number(reply.page) === page || records === 0 && Number(reply.page) === 0) || !Array.isArray(reply.rows) ||
        count !== null && (count !== records || pages !== total)) throw Error('BUSINESS_API_INCOMPLETE_PAGES');
    if (page === 1) {count = records; pages = total;}
    if (total !== Math.ceil(records / 100) || reply.rows.length !== Math.min(100, Math.max(0, records - (page - 1) * 100)))
      throw Error('BUSINESS_API_PAGE_SIZE');
    for (const item of reply.rows) {
      if (!Array.isArray(item.cell) || item.cell.length !== grid.colModel.length) throw Error('BUSINESS_API_ROW_COLUMNS');
      const row = Object.fromEntries(grid.colModel.map((col, i) => [col.name, decode(item.cell[i])]));
      if (!/^\d+$/.test(row['t.serial_no']) || !row['t.REQ_INFO'].startsWith(c.expected_userid + '-') || serials.has(row['t.serial_no']))
        throw Error('BUSINESS_API_ROW_BINDING');
      serials.add(row['t.serial_no']); rows.push(row);
    }
  }
  if (rows.length !== count) throw Error('BUSINESS_API_INCOMPLETE_LIST');
  if (c.serials && (serials.size!==c.serials.length || c.serials.some(s=>!serials.has(s)))) throw Error('BUSINESS_API_SERIAL_SCOPE_CHANGED');
  if (c.action === 'bill-read') {
    if (rows.length !== 1 || rows[0]['t.serial_no'] !== c.serial ||
        moneyCents(rows[0]['t.total_amt']) !== moneyCents(c.expected_amount))
      throw Error('BUSINESS_API_CASE_MISMATCH');
    set('61-serialno', c.serial);
    if (c.domain === 'funding') set('61-ignore_zero', 'N');
    if (c.domain === 'notes') {
      const xtype=c.notes_xtype || '33&33', parts=xtype.split('&');
      if(parts.length!==2 || !parts.every(p=>/^[A-Za-z0-9_\u4e00-\u9fff|]{1,100}$/.test(p)) ||
        !(parts[0]===parts[1]&&!parts[0].includes('|') || parts[1]==='CHECK_DW'&&parts[0]==='CHECK_DW|'+c.serial)) throw Error('BUSINESS_API_NOTES_SCOPE');
      set('61-XTYPE',xtype);
    }
    if (c.domain === 'travel') set('61-xgbt_src','报销单列表');
    let draftSubmission = false, legacySubmission = false;
    if (c.domain === 'submission') {
      // 流程身份来自本次同号、同金额、同申请人的清单行，不继承当前页其他单据。
      const node = rows[0]['t.node_id'], workflow = rows[0]['t.wf_name'];
      draftSubmission = rows[0]['t.status'] === '-99' && node === '' && workflow === '';
      legacySubmission = rows[0]['t.status'] === '30' && rows[0]['t.status_name'] === '审批完成-支付完成' && /^\d+$/.test(node) && workflow === '';
      if (!draftSubmission && !legacySubmission && (!/^\d+$/.test(node) || !/^[A-Za-z0-9_]{1,80}$/.test(workflow)))
        throw Error('BUSINESS_API_WORKFLOW_BINDING');
      set('61-node_id', node); set('61-wf_name', workflow);
    }
    if (c.domain==='used_invoices') {
      for (const key of ['serial_no','start_money','end_money','tk_typename','tk_no'])set('535-t.'+key,'');
      set('535-t.bx_type','-999');set('535-t.serial_no',c.serial);
    }
    let sections, workflowParameters;
    if (c.domain==='pre_review') {
      const preview=await reimbursementPreviewData(c,rows[0]);requests.push(...preview.requests);
      workflowParameters=preview.pre_review_slots;sections=[];
      for (const slot of workflowParameters) {
        reimbursementPreReviewContext(set,slot);
        const current=await readSections(c.contracts);
        if (current.length!==1 || current[0].rows.length!==1) throw Error('BUSINESS_API_WORKFLOW_PARAMETER_BINDING');
        sections.push({...current[0],slot});
      }
    } else if (draftSubmission || legacySubmission) {
      // 未发起流程的草稿仍可有附件；只读不依赖流程节点的2040附件汇总。
      if (JSON.stringify(c.contracts.map(x=>x.funcno))!==JSON.stringify(['109','495','1816','2040']))
        throw Error('BUSINESS_API_DRAFT_SUBMISSION_CONTRACT');
      sections = await readSections(c.contracts.filter(x=>draftSubmission ? x.funcno==='2040' : x.funcno!=='495'));
    } else sections = await readSections(c.contracts);
    if (c.domain==='used_invoices') {
      if(sections.length!==1||sections[0].funcno!=='533')throw Error('BUSINESS_API_USED_INVOICE_SECTION');
      const ids=sections[0].rows.map(r=>r['t.tk_guid']);
      if(ids.some(id=>!/^[A-Fa-f0-9]{32}$/.test(id))||new Set(ids.map(s=>s.toLowerCase())).size!==ids.length||
          sections[0].rows.some(r=>r['t.serial_no']!==c.serial))throw Error('BUSINESS_API_USED_INVOICE_BINDING');
    }
    return result({serial: c.serial, domain: c.domain, complete: !legacySubmission, bill: rows[0], sections, ...(c.domain==='notes'?{notes_xtype:c.notes_xtype||'33&33'}:{}),
      ...(draftSubmission ? {workflow_state:'not_started',not_applicable_sections:['109','495','1816'].map(funcno=>({funcno,reason:'draft_without_workflow'}))} : {}),
      ...(legacySubmission ? {partial:true,workflow_state:'closed_missing_workflow_name',unavailable_sections:[{funcno:'495',reason:'source_workflow_name_empty'}]} : {}),
      ...(workflowParameters ? {workflow_parameters:workflowParameters} : {}), page_context_modified: false});
  }
  return result({complete: true, account_scope: 'current_applicant', total_records: count, total_pages: pages, rows});
}
