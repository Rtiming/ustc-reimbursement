/* 人工入口只提交明确工具合同；案件与执行状态由后端原工具持有。 */
(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const state = {csrf:'',tools:[],groups:[],cases:[],roots:[],group:'activities',caseId:localStorage.getItem('reimbursement-case') || '',lastResult:null};
  const labels = {case_id:'当前活动',title:'活动名称',profile:'材料场景',action:'办理事项',domain:'读取内容',execute:'实际执行',request_id:'本次操作标识',expected_userid:'学校登录学工号',tab_id:'已核实的财务页面',job_id:'原任务',source_job_id:'完整清单来源任务',artifact_id:'来源结果',serials:'所选单号',serial:'原报销单号',include_history:'同时查看历史意见',output_dir:'新输出目录',export_root:'导出目录',output_root:'交付目录',workspace:'已有材料核对工作台',port:'本机端口',document:'要维护的资料',changes:'本次修改',expected_revision:'当前资料版本',paths:'原件路径',query:'查找内容',exact:'精确匹配',person_lookup:'查找姓名与学工号',limit:'最多显示条数',mode:'办理方式',stage:'填写环节',plan:'办理计划',request:'材料整理请求',context:'本次适用范围',data:'已核材料数据',policy:'已确认规则',library_root:'私人规范库目录',profile_id:'专项编号',selected_profile_ids:'适用专项',selection_basis:'采用依据',as_of:'业务日期',input_path:'已核材料文件',output_path:'新计划文件',amount:'核定申请金额',name:'姓名',person_id:'学工号',phone:'联系电话',remark:'备注',summary:'事项摘要',number:'发票号码',date:'日期',source:'依据',note:'核对说明',reason:'原因',text:'已确定正文',filename:'文件名称',file:'文件',sha256:'原件校验信息',path:'资料路径',sources:'原始材料',id:'材料标识',group_strip_dirs:'跳过的外层目录数',schema:'资料格式',field_review:'人工复核记录',merge_pdf:'合并为一个PDF',finance_only:'仅导出财务单',sequences:'本批序号',hashes:'核对原件完整性',include_parameters:'查看业务输入',topic:'帮助主题',state:'状态',status:'状态',amount_yuan:'申请金额',reason_text:'反馈原文',observed_at:'采集时间'};
  const names = {bank_transfer:'个人转卡',corporate_transfer:'对公转账',corporate_card:'公务卡',system_default_single_funding:'单经费，使用学校自动分配',manual:'按已核明细分配',unconfirmed:'尚未核定',unknown:'尚未核对',yes:'是',no:'否',student_activity:'学生活动',research_purchase:'科研采购',travel:'差旅',basic:'基本信息',funding:'经费',expenses:'费用与分摊',payments:'收款信息',notes:'补充说明','pre-review':'预审人',audit:'回读核验',form:'报销信息',case:'活动材料',readback:'独立回读',recover:'核查原任务',status:'查看进度',inspect:'检查当前条件','bills-list':'本人全部单据','bill-read':'读取当前单据','invoices-list':'本人发票池','recognition-list':'票据识别明细',definitions:'业务适配诊断',list:'查看登记',open:'打开',summary:'汇总',register:'登记已有工作台',doctor:'检查环境',start:'启动',stop:'结束服务'};
  const statuses = {queued:'等待处理',running:'处理中',completed:'程序已结束，查看业务结果',failed:'需要处理',unknown:'结果待核实',prepared:'等待确认',dispatching:'正在发起',returned:'已取得工具结果',needs_review:'需要核对原任务',draft:'草稿',submitted_to_approval:'已提交，待后续审批',returned_editing:'退回修正中'};
  Object.assign(names,{bank_transfer:'个人转卡',corporate_transfer:'对公转账',corporate_card:'公务卡',system_default_single_funding:'单经费沿用学校自动分配',manual:'按学校预算项目手工分配',unconfirmed:'尚未核定',unknown:'尚未核对',yes:'是',no:'否'});
  const node = (tag,text,cls) => {const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e};
  const message = text => {byId('notice').textContent=text || '';};
  const uid = () => crypto.randomUUID().replaceAll('-','');
  const current = () => state.cases.find(row=>row.case_id===state.caseId);

  async function api(path, value) {
    const response = await fetch(path, value===undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-Workbench-CSRF':state.csrf},body:JSON.stringify(value)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '工作台请求未完成');
    return result;
  }
  async function boot() {
    const launch = new URLSearchParams(location.hash.slice(1)).get('launch');
    if (launch) {await api('/api/session',{token:launch});history.replaceState(null,'',location.pathname);}
    const result = await api('/api/bootstrap');
    Object.assign(state,{csrf:result.csrf,tools:result.catalog.tools,groups:result.catalog.groups,cases:result.cases.filter(row=>row.role!=='account_inbox'),roots:result.material_roots});
    if (!state.cases.some(row=>row.case_id===state.caseId)) state.caseId='';
    const select=byId('case-select');select.replaceChildren(new Option('请选择活动',''));
    for(const row of state.cases)select.add(new Option(row.title || row.case_id,row.case_id));
    select.value=state.caseId;
    const nav=byId('nav');nav.replaceChildren();
    for(const group of state.groups){const button=node('button',group.title,'nav-item'+(group.id===state.group?' active':''));button.onclick=()=>showGroup(group.id);nav.append(button);}
    showGroup(state.group);
  }
  function heading(title,description='') {state.view=uid();byId('page-title').textContent=title;byId('page-description').textContent=description;byId('content').replaceChildren();message('');}
  function button(text,fn,primary=false){const b=node('button',text,primary?'primary':'');b.type='button';b.onclick=async()=>{if(b.disabled)return;b.disabled=true;try{await fn();}catch(e){message(e.message);}finally{b.disabled=b.dataset.keepDisabled==='true';}};return b;}
  function table(headers,values){const t=node('table'),head=node('thead'),tr=node('tr');for(const h of headers)tr.append(node('th',h));head.append(tr);t.append(head);const body=node('tbody');for(const row of values){const r=node('tr');for(const v of row){const c=node('td');v instanceof Node?c.append(v):c.textContent=v===null||v===undefined?'待核定':String(v);r.append(c);}body.append(r);}t.append(body);return t;}
  function showGroup(group) {
    state.group=group;
    const info=state.groups.find(row=>row.id===group);
    heading(info?.title || '活动与批次',group==='activities'?'先选择或新建一个活动，再导入材料。':'使用同一活动资料和任务记录办理；本人验证由本人完成。');
    [...byId('nav').children].forEach((b,i)=>b.classList.toggle('active',state.groups[i]?.id===group));
    const content=byId('content');
    if(['settings','feedback','approval'].includes(group))content.append(button('本人账号与财务收件箱',showConnection,true));
    if(group==='activities'){
      if(state.cases.length)content.append(table(['活动','原单号','申请金额','当前记录',''],state.cases.map(row=>[row.title||row.case_id,row.serial||'尚未建单',row.amount,statuses[row.status]||row.status||'待准备',button('进入',()=>{state.caseId=row.case_id;byId('case-select').value=row.case_id;localStorage.setItem('reimbursement-case',row.case_id);showGroup('materials');})])));
      else content.append(node('p','还没有活动。点击“新建活动”，填写名称后即可开始。','empty'));
      if(state.caseId)content.append(button('查看本案下一步',()=>followNext({tool:'reimbursement_case_journey',arguments:{case_id:state.caseId},label:'本案下一步'}),true));
      content.append(node('h2','批次准备'));
    }
    if(group==='materials'){content.append(button('选择文件并导入',importFiles,true));const saved=sessionStorage.getItem('intake-'+state.caseId);if(saved)content.append(button('继续已上传的材料',()=>showIntake(JSON.parse(saved))));const batches=node('section');content.append(batches);const caseId=state.caseId,view=state.view;if(caseId)api('/api/read',{tool:'reimbursement_intake_review',arguments:{case_id:caseId,action:'batches'}}).then(value=>{if(state.view!==view)return;if(value.batches.length){batches.append(node('h2','已有收件批次'),table(['原始材料','来源数量','整理时间',''],value.batches.map(row=>[row.file_names.join('、')||'材料整理任务',row.file_count, row.created_at?new Date(row.created_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'待记录',button(row.next_action.label,()=>followNext(row.next_action))])));}}).catch(e=>message(e.message));content.append(node('h2','其他核对操作'));}
    if(group==='feedback')content.append(node('p','先同步学校完整单据清单，再从相同来源取得挂单理由。当前挂单和历史意见分开显示。','intro'));
    const list=node('div','','task-list');
    for(const tool of state.tools.filter(row=>row.group===group&&!['reimbursement_new_case','reimbursement_material_intake','reimbursement_intake_review','reimbursement_finance_connection'].includes(row.name))){const b=button(tool.title,()=>showTool(tool));b.className='task-button';list.append(b);}
    content.append(list);
    if(group==='settings')content.append(button('退出工作台服务',async()=>{await api('/api/stop',{});message('工作台服务已退出。资料和原任务保留，需要时从固定入口重新打开。');}));
  }

  function resolveSchema(schema,root,value){
    if(schema.$ref){const parts=schema.$ref.replace(/^#\//,'').split('/');return parts.reduce((a,k)=>a[k],root);}
    if(schema.anyOf){return schema.anyOf.find(s=>s.type!=='null' && (value===undefined || (s.type==='array'?Array.isArray(value):s.type===typeof value))) || schema.anyOf.find(s=>s.type!=='null') || {};}
    return schema;
  }
  function editor(key,schema,value,root,required=false) {
    schema=resolveSchema(schema,root,value);
    const title=schema.title&&/[^\x00-\x7F]/.test(schema.title)?schema.title:(labels[key]||schema.title||key);
    const wrap=node('div','','field'),label=node('label',title+(required?' *':''));wrap.append(label);
    let get;
    if(schema.enum){const e=node('select');const i=schema.enum.indexOf(value ?? schema.default);if(!required||i<0)e.add(new Option(required?'请选择':'未指定',''));schema.enum.forEach((v,i)=>e.add(new Option(names[v]||String(v),String(i))));if(i>=0)e.value=String(i);wrap.append(e);get=()=>e.value===''?undefined:schema.enum[Number(e.value)];}
    else if(schema.type==='boolean'){const e=node('input');e.type='checkbox';e.checked=value ?? schema.default ?? false;wrap.append(e);get=()=>e.checked;}
    else if(schema.type==='object' || value && typeof value==='object'&&!Array.isArray(value)){
      wrap.classList.add('wide');const body=node('div','','object-fields');wrap.append(body);let fields={};let existing=value && typeof value==='object'?value:{};
      function rebuild(data){existing=data;body.replaceChildren();fields={};const properties=schema.properties || Object.fromEntries(Object.entries(data).map(([k,v])=>[k,{type:Array.isArray(v)?'array':v===null?'string':typeof v,items:{type:'string'}}]));for(const [k,s] of Object.entries(properties)){const child=editor(k,s,data[k],root,(schema.required||[]).includes(k));fields[k]=child;body.append(child.node);}if(!Object.keys(properties).length)body.append(node('p','请选择前一步生成的资料文件，载入后可逐项查看和填写。','help'));}
      rebuild(existing);
      const file=node('input');file.type='file';file.accept='.json';file.setAttribute('aria-label',(labels[key]||key)+'资料文件');file.onchange=async()=>{try{const v=JSON.parse(await file.files[0].text());if(!v||typeof v!=='object'||Array.isArray(v))throw Error('请选择此步骤对应的资料文件');rebuild(v);}catch(e){message(e.message);}};wrap.append(file);
      get=()=>{const out={...existing};for(const[k,e]of Object.entries(fields)){const v=e.get();if(v===undefined)delete out[k];else out[k]=v;}return Object.keys(out).length?out:required?{}:undefined;};
    } else if(schema.type==='array'){
      wrap.classList.add('wide');const item=resolveSchema(schema.items||{},root);let values=Array.isArray(value)?value:[];
      if(item.type==='object'||values.some(v=>v&&typeof v==='object')){const body=node('div','','object-fields'),fields=[];wrap.append(body);const add=v=>{const e=editor('条目',item,v,root,true),row=node('div','','array-row');row.append(e.node,button('移除此项',()=>{row.remove();fields.splice(fields.indexOf(e),1);}));fields.push(e);body.append(row);};values.forEach(add);wrap.append(button('增加一项',()=>add({})));get=()=>fields.map(f=>f.get());}
      else {const e=node('textarea');e.placeholder='每行一项';e.value=values.join('\n');wrap.append(e);get=()=>e.value.trim()?e.value.split(/\r?\n/).filter(v=>v.trim()).map(v=>item.type==='integer'||item.type==='number'?Number(v):v.trim()):required?[]:undefined;}
    } else {
      const e=node(schema.type==='string'&&['text','note','reason','summary'].includes(key)?'textarea':'input');
      if(e.tagName==='INPUT')e.type=['integer','number'].includes(schema.type)?'number':'text';
      if(schema.type==='number')e.step='any';e.value=value ?? schema.default ?? '';if(schema.description)e.title=schema.description;
      wrap.append(e);get=()=>e.value===''?undefined:['integer','number'].includes(schema.type)?Number(e.value):e.value;
    }
    return {node:wrap,get};
  }

  async function showTool(tool,defaults={}) {
    if(['reimbursement_case_materials','reimbursement_material_review','reimbursement_prepare_batch','reimbursement_prepare_batch_file','reimbursement_batch_plan'].includes(tool.name))return showCaseMaterials();
    if(['reimbursement_case_delivery','reimbursement_pdf_export'].includes(tool.name))return showDelivery();
    if(tool.name.startsWith('reimbursement_standards_'))return showStandards();
    if(tool.name==='reimbursement_case_finance')return showCaseFinance(defaults);
    if(tool.name==='reimbursement_approver_batch')return showApprover(defaults);
    if(tool.name==='reimbursement_case_details'&&(!defaults.action||defaults.action==='inspect'))return showCaseDetails();
    heading(tool.title,current()?.title || '请先选择适用活动');
    const schema=tool.inputSchema,properties=schema.properties||{},required=schema.required||[];
    const data={...defaults};if(properties.case_id&&!data.case_id)data.case_id=state.caseId;
    if(required.includes('case_id')&&!state.caseId&&tool.name!=='reimbursement_new_case'){message('请先选择或新建一个活动。');return;}
    const intentBase=uid();
    if(properties.expected_revision&&state.caseId){const o=await api('/api/read',{tool:'reimbursement_overview',arguments:{case_id:state.caseId,include_parameters:true}});data.expected_revision=o.revision;if(tool.name==='reimbursement_update_case'&&!data.changes)data.changes={amount:o.parameters?.form?.amount,basic:o.parameters?.form?.basic};}
    const form=node('form'),fields=node('div','','fields'),editors={};
    for(const[key,s]of Object.entries(properties)){if(['case_id','request_id','expected_revision','execute'].includes(key))continue;const e=editor(key,s,data[key],schema,required.includes(key));editors[key]=e;fields.append(e.node);}
    form.append(fields);const actions=node('div','','actions');
    async function submit(execute){const args={};for(const k of ['case_id','expected_revision'])if(data[k])args[k]=data[k];for(const[k,e]of Object.entries(editors)){const v=e.get();if(v!==undefined)args[k]=v;}if(properties.execute)args.execute=execute;if(properties.request_id)args.request_id=await WorkbenchInputs.requestId(intentBase,args);await perform(tool,args,execute);}
    actions.append(button(properties.execute?'先检查条件':'开始',()=>submit(false),!properties.execute));
    if(properties.execute)actions.append(button('核对并执行',()=>submit(true),true));
    actions.append(button('返回',()=>showGroup(tool.group)));form.append(actions);form.onsubmit=e=>e.preventDefault();byId('content').append(form);
    const help=node('details');help.append(node('summary','操作说明'),node('p',tool.description,'muted'));byId('content').append(help,node('div','','result'));byId('content').lastChild.id='result';
  }

  function describe(value) {
    if(value===null||value===undefined)return '尚未指定';
    if(typeof value==='boolean')return value?'是':'否';
    if(Array.isArray(value))return value.map(describe).join('\n');
    if(typeof value==='object')return Object.entries(value).filter(([k])=>!['schema','case_id','id','sha256'].includes(k)).map(([k,v])=>(labels[k]||k)+'：'+describe(v)).join('\n');
    return names[value] || String(value);
  }
  async function perform(tool,args,confirm=false,options={}) {
    const view=state.view;
    message('正在检查本次操作…');
    if(tool.annotations?.readOnlyHint===true&&args.execute!==true){const value=await api('/api/read',{tool:tool.name,arguments:args});if(state.view===view){message('');if(options.show!==false)showResult(value,{tool:tool.name,arguments:args});}return value;}
    const prepared=await api('/api/prepare',{tool:tool.name,arguments:args});
    if(confirm){byId('confirm-title').textContent=tool.title;byId('confirm-body').replaceChildren(table(['项目','本次内容'],options.summary || Object.entries(args).filter(([k])=>!['request_id','expected_revision','case_id','execute'].includes(k)).map(([k,v])=>[labels[k]||k,describe(v)])));const answer=await new Promise(resolve=>{byId('confirm-back').onclick=()=>{byId('confirm').close();resolve(false);};byId('confirm-go').onclick=()=>{byId('confirm').close();resolve(true);};byId('confirm').oncancel=()=>resolve(false);byId('confirm').showModal();});if(!answer){message('已返回修改，尚未执行。');return;}}
    message('正在办理，请保留本次任务记录。');const value=await api('/api/execute',{intent_id:prepared.intent_id});if(state.view===view){message(value.ok?'':value.error);if(options.show!==false)showResult(value.result || value,{tool:tool.name,arguments:args});}return value.result || value;
  }
  function showResult(value,context={}) {
    if(['ustc-case-delivery-v1','ustc-case-delivery-list-v1'].includes(value.schema))return showDelivery(value);
    if(['ustc-case-finance-v1','ustc-case-fill-v1','ustc-case-ready-v1'].includes(value.schema))return showCaseFinance({},value);
    clearTimeout(state.pollHandle);const pollVersion=uid();state.pollVersion=pollVersion;const view=state.view;
    state.lastResult=value;let target=byId('result');if(!target){target=node('div','','result');target.id='result';byId('content').append(target);}target.replaceChildren(node('h2','本次结果'));
    if(value.job_id&&value.schema!=='ustc-task-view-v1'&&!context.showRaw){
      target.append(node('p','正在读取本步业务结果…','progress'));
      api('/api/read',{tool:'reimbursement_task_view',arguments:{case_id:value.case_id||context.arguments?.case_id||state.caseId,job_id:value.job_id}}).then(next=>{if(state.view===view&&state.pollVersion===pollVersion)showResult(next,context);}).catch(e=>message(e.message));return;
    }
    if(value.schema==='ustc-task-view-v1'){
      target.replaceChildren(node('h2',value.title),node('p',value.summary,'intro'));
      if(value.approval_batch)WorkbenchApprover({value:value.approval_batch,content:target,button,table,perform,api,tool:state.tools.find(t=>t.name==='reimbursement_approver_batch'),onResult:showResult,notice:message});
      else if(value.rows?.length){const keys=Object.keys(value.rows[0]);target.append(table(keys,value.rows.map(row=>keys.map(key=>row[key]))));}
      if(value.rows_truncated)target.append(node('p',`此处展示前${value.rows.length}条，共${value.row_count}条；完整依据保留在原任务中。`,'help'));
      if(value.issues?.length)target.append(table(['需要核对的问题'],value.issues.map(row=>[typeof row==='string'?row:describe(row)])));
      if(value.next_action?.tool&&!value.approval_batch&&!['queued','running'].includes(value.status))target.append(button(value.next_action.label||'继续下一步',()=>followNext(value.next_action),true));
      if(value.details_call){const details=node('details');details.append(node('summary','查看技术回执'));details.ontoggle=async()=>{if(details.open&&!details.dataset.loaded){try{const r=await api('/api/read',{tool:value.details_call.tool,arguments:value.details_call.arguments});details.append(node('pre',JSON.stringify(r.artifact||r,null,2)));details.dataset.loaded='true';}catch(e){message(e.message);}}};target.append(details);}
      if(['queued','running'].includes(value.status))state.pollHandle=setTimeout(async()=>{try{const next=await api('/api/read',{tool:'reimbursement_task_view',arguments:{case_id:value.case_id,job_id:value.job_id}});if(state.view===view&&state.pollVersion===pollVersion)showResult(next,context);}catch(e){if(state.view===view)message(e.message);}},1200);
      return;
    }
    if(['ustc-approver-batch-v1','ustc-approver-selection-v1'].includes(value.schema)||value.approver_plans){WorkbenchApprover({value,content:target,button,table,perform,api,tool:state.tools.find(t=>t.name==='reimbursement_approver_batch'),onResult:showResult,notice:message});return;}
    if(value.schema==='ustc-intake-groups-v1'){WorkbenchIntakeGroups({value,content:target,button,table,perform,tool:state.tools.find(t=>t.name==='reimbursement_intake_groups'),notice:message,onCase:async caseId=>{state.caseId=caseId;localStorage.setItem('reimbursement-case',caseId);await boot();showGroup('materials');}});return;}
    if(value.schema==='ustc-material-requirements-v1'){WorkbenchMaterialRequirements({value,content:target,button,table,onNext:followNext,perform,tools:state.tools,notice:message});return;}
    if(['ustc-case-journey-v1','ustc-invoice-face-view-v1'].includes(value.schema)){WorkbenchJourney({value,content:target,button,table,onNext:followNext,api,perform,tools:state.tools,notice:message});return;}
    if(value.job_id){target.append(node('p',statuses[value.status]||value.status||'任务已登记','status-tag'),button('更新此任务进度',async()=>showResult(await api('/api/read',{tool:'reimbursement_result',arguments:{case_id:value.case_id||context.arguments?.case_id||state.caseId,job_id:value.job_id}}),context)));}
    if(value.copy_text){const area=node('textarea');area.value=value.copy_text;area.readOnly=true;target.append(area,button('复制汇总原文',async()=>{try{await navigator.clipboard.writeText(value.copy_text);message('已复制。');}catch{area.focus();area.select();message('内容已选中，请按Ctrl+C复制。');}}));}
    if(value.rows?.length){const keys=Object.keys(value.rows[0]).slice(0,8);target.append(table(keys.map(k=>labels[k]||k),value.rows.slice(0,100).map(row=>keys.map(k=>typeof row[k]==='object'?JSON.stringify(row[k]):row[k]))));if(value.rows.length>100)target.append(node('p','页面先展示100条；完整结果保留在原任务中。','muted'));}
    if(value.error||value.message||value.next)target.append(node('p',value.error||value.message||value.next,'muted'));
    if(value.errors?.length)target.append(table(['待办项','说明'],value.errors.map(row=>[row.field==='form.serial'?'学校单号（创建后取得）':row.field,row.error])));
    const details=node('details');details.append(node('summary','查看完整结果与依据'),node('pre',JSON.stringify(value,null,2)));target.append(details);
    if(value.job_id&&value.status==='completed'&&context.tool==='reimbursement_business'&&context.arguments?.action==='bills-list')target.append(button('从本次完整清单取得挂单理由',()=>showTool(state.tools.find(t=>t.name==='reimbursement_finance_feedback'),{source_job_id:value.job_id})));
    if(value.job_id&&['queued','running'].includes(value.status))state.pollHandle=setTimeout(async()=>{try{const next=await api('/api/read',{tool:'reimbursement_result',arguments:{case_id:value.case_id||context.arguments?.case_id,job_id:value.job_id}});if(state.view===view&&state.pollVersion===pollVersion)showResult(next,context);}catch(e){if(state.view===view)message(e.message);}},1200);
  }
  async function followNext(action){
    const target=action.arguments?.case_id;
    if(target&&target!==state.caseId){
      const all=await api('/api/read',{tool:'reimbursement_cases',arguments:{}});
      state.cases=all.cases.filter(row=>row.role!=='account_inbox');
      if(!state.cases.some(row=>row.case_id===target))throw Error('原分单尚未登记，请刷新后核对');
      state.caseId=target;localStorage.setItem('reimbursement-case',target);
      const select=byId('case-select');select.replaceChildren(new Option('请选择活动',''));
      for(const row of state.cases)select.add(new Option(row.title||row.case_id,row.case_id));
      select.value=target;
    }
    if(action.ui_group)return showGroup(action.ui_group);
    if(action.tool==='reimbursement_case_materials')return showCaseMaterials();
    const tool=state.tools.find(row=>row.name===action.tool);if(!tool)throw Error('下一步工具尚未接入');
    if(tool.name==='reimbursement_case_finance'&&['inspect','fill_status','ready_status'].includes(action.arguments?.action||'inspect'))return showCaseFinance(action.arguments);
    if(tool.name==='reimbursement_approver_batch')return showApprover(action.arguments);
    if(action.ui_form==='case_details'||tool.name==='reimbursement_case_details')return showCaseDetails();
    if(tool.name==='reimbursement_intake_review'&&action.arguments.intake_job_id)return showIntakeFiles({caseId:action.arguments.case_id,executeJob:action.arguments.intake_job_id});
    heading(action.label||tool.title,current()?.title||state.caseId);return perform(tool,action.arguments,Boolean(action.requires_user_confirmation));
  }
  async function showCaseFinance(args={},supplied){
    const caseId=args.case_id||supplied?.case_id||state.caseId;
    if(!caseId){message('请先选择或新建活动。');return;}
    heading('本活动财务办理向导','从本案原件继续：核对票池、选票建单、填写与独立回读。');const view=state.view;
    const value=supplied||await api('/api/read',{tool:'reimbursement_case_finance',arguments:{case_id:caseId,...(args.plan_id?{action:args.action||'fill_status',plan_id:args.plan_id}:{})}});
    if(state.view!==view)return;
    await WorkbenchCaseFinance({value,api,perform,tool:state.tools.find(t=>t.name==='reimbursement_case_finance'),content:byId('content'),button,table,notice:message,onResult:showResult,isCurrent:()=>state.view===view,openConnection:showConnection,onNext:followNext});
  }
  async function showStandards(){
    heading('私人规范与专项','选择已有专项、核对来源，或登记本次明确的新要求。');const view=state.view;
    await WorkbenchStandards({content:byId('content'),button,table,api,perform,tools:state.tools,caseId:state.caseId,notice:message,isCurrent:()=>state.view===view});
  }
  async function showDelivery(supplied){
    if(!state.caseId&&!supplied?.case_id){message('请先选择本次活动');return;}
    heading('本活动交付与系统PDF','从已核本单生成文件，接续原导出并核验文件完整性。');const view=state.view;
    const tool=state.tools.find(t=>t.name==='reimbursement_case_delivery');
    const value=supplied||await api('/api/read',{tool:tool.name,arguments:{case_id:state.caseId}});if(state.view!==view)return;
    await WorkbenchDelivery({value,content:byId('content'),button,table,api,perform,tool,onResult:showResult,isCurrent:()=>state.view===view});
  }
  async function showApprover(args={}){
    heading('本人确认与审批','先核对本人的待办、角色和意见；验证与每项结果分别保留。');
    const view=state.view;
    byId('content').append(button('本人账号与财务收件箱',showConnection));
    const argumentsValue=args.action==='inspect'?args:args.plan_id?{action:'status',plan_id:args.plan_id}:{action:'list'};
    const value=await api('/api/read',{tool:'reimbursement_approver_batch',arguments:argumentsValue});
    if(state.view!==view)return;
    showResult({...value,approver_plans:argumentsValue.action==='list'});
  }
  async function showConnection(){
    heading('本人账号与财务收件箱','无需新建活动即可读取本人单据、挂单理由和审批清单。');const view=state.view;
    await WorkbenchConnection({api,perform,tool:state.tools.find(t=>t.name==='reimbursement_finance_connection'),content:byId('content'),button,table,notice:message,onResult:showResult,isCurrent:()=>state.view===view});
  }
  async function showCaseMaterials(){
    if(!state.caseId){message('请先选择或新建活动。');return;}
    heading('本活动材料、费用与补件','导入的材料直接核对，保存费用关联后继续分单或补件。');const view=state.view;
    await WorkbenchCaseMaterials({api,perform,tool:state.tools.find(t=>t.name==='reimbursement_case_materials'),caseId:state.caseId,content:byId('content'),button,table,notice:message,onNext:followNext,isCurrent:()=>state.view===view});
  }
  async function showCaseDetails(){
    if(!state.caseId){message('请先选择一个活动。');return;}
    heading('填写报销信息',current()?.title||state.caseId);const view=state.view;
    await WorkbenchCaseDetails({api,perform,tool:state.tools.find(t=>t.name==='reimbursement_case_details'),caseId:state.caseId,content:byId('content'),editor,button,notice:message,onNext:followNext,isCurrent:()=>state.view===view});
  }
  function newActivity() {
    heading('新建活动','活动名称用于查找；不会创建学校财务单据。');
    const title=editor('title',{type:'string'},'',{},true),profile=editor('profile',{type:'string',enum:['student_activity','research_purchase','travel']},'student_activity',{},true);
    const form=node('div','','fields');form.append(title.node,profile.node);byId('content').append(form,button('创建活动',async()=>{const name=title.get();if(!name?.trim())throw Error('请填写活动名称');const caseId='activity_'+new Date().toISOString().slice(0,10).replaceAll('-','')+'_'+uid().slice(0,8);const t=state.tools.find(t=>t.name==='reimbursement_new_case');const result=await perform(t,{case_id:caseId,profile:profile.get(),title:name});if(result?.ok){state.caseId=caseId;localStorage.setItem('reimbursement-case',caseId);await boot();showGroup('materials');}},true));
  }
  function rememberIntake(intake) {sessionStorage.setItem('intake-'+intake.caseId,JSON.stringify(intake));}
  async function trackIntake(job,intake,statusNode,view) {
    let result=job;
    let delay=700;
    const deadline=Date.now()+180000;
    while(result?.job_id&&['queued','running'].includes(result.status)&&Date.now()<deadline) {
      if(state.view!==view)return result;
      statusNode.textContent=result.status==='queued'?'等待整理工具开始…':'正在核对材料和保存结果…';
      await new Promise(resolve=>setTimeout(resolve,delay));delay=Math.min(2500,delay*1.4);
      result=await api('/api/read',{tool:'reimbursement_result',arguments:{case_id:intake.caseId,job_id:result.job_id}});
    }
    return result;
  }
  async function showIntake(intake) {
    if(intake.caseId!==state.caseId)throw Error('这批材料属于另一活动，请先切换活动。');
    heading('核对本批材料',current()?.title || state.caseId);
    const view=state.view,content=byId('content');
    content.append(table(['收到的材料','文件大小'],intake.files.map(file=>[file.name,(file.size/1024).toFixed(1)+'KB'])));
    const statusNode=node('p',intake.completed?'这批材料已归档，点击下方查看清单。':'原文件已保存。先检查内容，再开始整理。','progress');
    content.append(statusNode);
    const group=editor('group_strip_dirs',{type:'string',enum:['none','0','1']},intake.request.sources[0].group_strip_dirs===null?'none':String(intake.request.sources[0].group_strip_dirs),{},true);
    const select=group.node.querySelector('select');
    [...select.options].forEach((option,i)=>option.textContent=['先整体核对，不按目录分组','按第一层活动目录分组','略过外层，再按活动目录分组'][i]);
    group.node.querySelector('label').textContent='压缩包内的活动分组';
    if(intake.completed)select.disabled=true;
    content.append(group.node);
    const actions=node('div','','actions');content.append(actions);
    const tool=state.tools.find(t=>t.name==='reimbursement_material_intake');
    async function start(execute) {
      select.disabled=true;
      try {
      const picked=group.get();intake.request.sources[0].group_strip_dirs=picked==='none'?null:Number(picked);
      const args={case_id:intake.caseId,request:intake.request,execute};args.request_id=await WorkbenchInputs.requestId(intake.base,args);
      const originalJob=execute?intake.executeJob:intake.previewJob;
      let result;
      if(originalJob)result=await api('/api/read',{tool:'reimbursement_result',arguments:{case_id:intake.caseId,job_id:originalJob}});
      else result=await perform(tool,args,execute,{show:false,summary:[['活动',current()?.title||intake.caseId],['原始文件',intake.files.map(f=>f.name).join('\n')],['整理内容','保留原件、展开ZIP、提取已有文字、列出待核事项'],['分组方式',select.selectedOptions[0].textContent],['财务影响','只整理本地材料，不建单、不提交、不审批']]});
      if(!result)return;
      if(result.job_id){if(execute)intake.executeJob=result.job_id;else intake.previewJob=result.job_id;rememberIntake(intake);}
      result=await trackIntake(result,intake,statusNode,view);
      if(state.view!==view)return;
      const body=result.messages?.find(row=>row&&typeof row==='object'&&typeof row.ok==='boolean') || result;
      if(result.status==='completed'&&result.exit_code===0&&body.ok===true) {
        if(execute){intake.completed=true;intake.result=result;rememberIntake(intake);statusNode.textContent=`已归档${body.original_count??intake.files.length}份来源材料，${body.ocr_candidates??0}项需要进一步识别。核定金额和财务办理尚未开始。`;executeButton.disabled=true;executeButton.dataset.keepDisabled='true';checkButton.disabled=true;select.disabled=true;actions.append(button('查看材料清单',()=>showIntakeFiles(intake),true));}
        else {intake.previewOk=true;rememberIntake(intake);statusNode.textContent=`检查通过：本批${body.file_count??intake.files.length}份来源文件，可以开始整理。`;executeButton.disabled=false;}
        message('');
      } else if(['queued','running'].includes(result.status))statusNode.textContent='任务仍在运行。可以稍后继续此批材料，程序不会重复发起。';
      else {statusNode.textContent='本次未完成，请先处理下面的问题。';message(body.error?.message||body.error||result.error||result.next||'请在任务与进度中查看原任务结果。');}
      } finally {if(state.view===view)select.disabled=Boolean(intake.completed);}
    }
    const checkButton=button('检查材料',()=>start(false));
    const executeButton=button('开始整理',()=>start(true),true);executeButton.disabled=!intake.previewOk||intake.completed;
    if(intake.completed){checkButton.disabled=true;actions.append(button('查看材料清单',()=>showIntakeFiles(intake),true));}
    else actions.append(checkButton,executeButton);
    actions.append(button('返回材料页',()=>showGroup('materials')));
    select.onchange=()=>{intake.previewOk=false;delete intake.previewJob;executeButton.disabled=true;rememberIntake(intake);statusNode.textContent='分组方式已变，请重新检查这批材料。';};
  }
  async function showIntakeFiles(intake) {
    heading('本批材料清单','材料按收到的原件保存；识别出的金额和票号仍需对照原件确认。');
    const view=state.view;
    await WorkbenchMaterialReview({api,perform,tool:state.tools.find(t=>t.name==='reimbursement_intake_review'),caseId:intake.caseId,jobId:intake.executeJob,heading,content:byId('content'),button,table,notice:message,isCurrent:()=>state.view===view,
      onNext:async action=>{const tool=state.tools.find(t=>t.name===action.tool);if(!tool)throw Error('下一步工具尚未接入');heading(tool.title,current()?.title || intake.caseId);await perform(tool,action.arguments,false);}});
  }
  function importFiles() {
    if(!state.caseId){message('请先选择或新建一个活动。');return;}
    heading('导入材料',current()?.title || state.caseId);const area=node('div','','upload-area');area.append(node('p','选择本活动的PDF、照片、表格或ZIP材料包。原件按收到的字节保存。'));
    const input=node('input');input.type='file';input.multiple=true;input.accept='.pdf,.ofd,.png,.jpg,.jpeg,.webp,.doc,.docx,.xls,.xlsx,.csv,.txt,.zip';input.setAttribute('aria-label','选择本活动材料');area.append(input);const progress=node('p','','progress');area.append(progress);const batchId=uid();
    area.append(button('上传并准备整理',async()=>{if(!input.files.length)throw Error('请先选择材料');const caseId=state.caseId;let sourceDir;const files=[...input.files];for(const [i,file]of files.entries()){if(file.size>100*1024*1024)throw Error(file.name+'超过100MiB');progress.textContent=`正在保存${i+1}/${files.length}：${file.name}`;const bytes=await file.arrayBuffer(),hash=await crypto.subtle.digest('SHA-256',bytes),sha=[...new Uint8Array(hash)].map(v=>v.toString(16).padStart(2,'0')).join('');const response=await fetch('/api/upload?'+new URLSearchParams({case_id:caseId,batch_id:batchId,name:file.name}),{method:'POST',headers:{'X-Workbench-CSRF':state.csrf,'X-File-SHA256':sha},body:bytes});const r=await response.json();if(!response.ok)throw Error(r.error);sourceDir=r.source_dir;}const intake={caseId,base:uid(),files:files.map(f=>({name:f.name,size:f.size})),request:{schema:'reimbursement.material-intake.v1',case_id:caseId,sources:[{id:'received',path:sourceDir,group_strip_dirs:null}],output_dir:state.roots[0]+'/整理批次/'+caseId+'/'+batchId}};rememberIntake(intake);await showIntake(intake);},true));byId('content').append(area);
  }
  async function showTasks(){heading('任务与进度','任务程序结束和财务业务完成分别记录；结果未知时先核查原任务。');const r=await api('/api/tasks'+(state.caseId?'?case_id='+encodeURIComponent(state.caseId):''));byId('content').append(table(['操作','当前进度',''],r.tasks.map(t=>[t.title,statuses[t.status]||t.status,button('查看',()=>showResult(t.result||t))])));}
  byId('new-case').onclick=newActivity;byId('case-select').onchange=()=>{state.caseId=byId('case-select').value;localStorage.setItem('reimbursement-case',state.caseId);showGroup(state.group);};byId('refresh').onclick=()=>boot().catch(e=>message(e.message));byId('tasks-nav').onclick=()=>showTasks().catch(e=>message(e.message));byId('agent-nav').onclick=()=>{heading('接入AI助手','人工与agent使用同一份活动资料、规则和任务记录。');byId('content').append(node('p','安装报告中提供MCP配置和独立技能。将报销服务加入你的模型客户端后，从能力检查开始，按返回的下一步办理。本人验证码在学校界面完成。','intro'),node('p','当前工具目录共'+state.tools.length+'项；客户端接通和真实业务办理需要分别验收。','muted'));};
  boot().catch(e=>message(e.message));
})();
