/* 分节点提交合同；最终本人同意及短信仅作准备，绝不调用审批/SMS发送。 */
function reimbursementWorkflowSubmitted(raw,c){
  if(!['promise','department'].includes(c?.action)||typeof raw!=='string'||!raw.startsWith('passlocal(')||!raw.includes('109-wf_inst.BASETABLE_KEY:'))return null;
  if(/afterjs:|<script\b/i.test(raw))throw Error('WORKFLOW_API_SUBMIT_RESPONSE_COMMAND');
  const chunks=raw.split('local(');if(chunks.shift()!=='pass')throw Error('WORKFLOW_API_SUBMIT_RESPONSE_STATUS');
  const presentation=new Set(['BXWC_TIPS','0-BXWC_TIPS','61-INDIV_SCH_TIPS','0-AFTER_LC_TIPS']),updates={};
  for(const chunk of chunks){
    const end=chunk.lastIndexOf(')'),tail=chunk.slice(end+1);
    if(end<0||!['','pass','success','['+c.serial+']pass','['+c.serial+']success'].includes(tail))throw Error('WORKFLOW_API_SUBMIT_RESPONSE_TAIL');
    const body=chunk.slice(0,end),keys=[...body.matchAll(/(?:^|;)([A-Za-z0-9_.-]+):/g)];
    for(let i=0;i<keys.length;i++){
      const key=keys[i][1].toUpperCase(),start=keys[i].index+keys[i][0].length,value=body.slice(start,i+1<keys.length?keys[i+1].index:body.length).replace(/;$/,'');
      if(presentation.has(key))continue; // Display-only HTML is never executed or inserted.
      if(!(/^(?:0|61|56)-[A-Z0-9_.*.-]+$/.test(key)||/^109-WF_INST\.(NODE_ID|UNI_KEY|BASETABLE_KEY)$/.test(key))||/^0-USERINFO\./.test(key)||/[;:\r\n]/.test(value)||Object.hasOwn(updates,key)&&updates[key]!==value)throw Error('WORKFLOW_API_SUBMIT_RESPONSE_FIELD');
      updates[key]=value;
    }
  }
  const node=updates['61-NODE_ID'],instance=updates['61-UNI_KEY'];
  if(node!=='508'||!/^\d+$/.test(instance||'')||updates['61-WF_NAME']!=='YBX_LC'||updates['61-SPINFO_SERIALNO']!==c.serial||updates['109-WF_INST.NODE_ID']!==node||updates['109-WF_INST.UNI_KEY']!==instance||updates['109-WF_INST.BASETABLE_KEY']!==c.serial)throw Error('WORKFLOW_API_SUBMIT_INSTANCE_BINDING');
  return{accepted:true,updates,workflow_instance:{node_id:node,uni_key:instance,base_key:c.serial,wf_name:'YBX_LC'}};
}
function reimbursementWorkflowApply(raw,context,c=null){
  const submitted=reimbursementWorkflowSubmitted(raw,c);
  if(submitted){for(const[key,value]of Object.entries(submitted.updates))context[(key.startsWith('0-')?'':'WF_YBX$')+key]=value;return submitted;}
  const keys=[...raw.matchAll(/(?:local\(|;)([^():;]+):/g)].map(m=>m[1]);
  const refused=keys.filter(k=>!(/^(?:0|61|56)-[A-Za-z0-9_.*.-]+$/i.test(k)||['LC_NODE_COUNT','WEB_INFO'].includes(k.toUpperCase()))||/^0-USERINFO\./i.test(k));
  if(refused.length)throw Error('WORKFLOW_API_CONTEXT_CHANGED:'+refused.filter(k=>/^[A-Za-z0-9_.*-]{1,80}$/.test(k)).slice(0,100).join(','));
  return ReimbursementNativeProtocol.applyResponse(raw,context,keys);
}
async function reimbursementWorkflowEvidence(c,bill){
  const requests=[],required_changes={};let payments,budget;
  const verify=(read,domain)=>{
    if(!read?.ok||!read.complete||read.case_id!==c.case_id||read.serial!==c.serial||read.expected_userid!==c.expected_userid||read.domain!==domain||read.system_write!==false)throw Error('WORKFLOW_API_DOMAIN_READ_BINDING');
    return read;
  };
  if(bill['t.status']==='-99'&&c.system_mode==='daily'){
    // 域owner的本次完整读取已被其计划校验，直接用于汇总，避免再查一次相同数据。
    const payment=await reimbursementPaymentsBuild({...c,contracts:c.payment_contracts}),paymentPlan=payment.report;
    payments=verify(payment.read,'payments');requests.push(...paymentPlan.requests);
    if(Object.keys(paymentPlan.changes).length||paymentPlan.card_bindings?.some(p=>p.selection_required)||paymentPlan.corporate_bindings?.some(p=>p.resolve_required))required_changes.payments=true;
    if(c.funding_policy?.invoice_level_required){
      required_changes.invoice_level_policy_current_proof_required=true;
      budget=await reimbursementBusinessApi({...c,action:'bill-read',domain:'categories',contracts:c.budget_contracts});requests.push(...budget.requests);
    }else{
      const allocation=await reimbursementExpensesBuild({...c,contracts:c.budget_contracts});budget=verify(allocation.read,'categories');requests.push(...allocation.report.requests);
      if(Object.keys(allocation.report.changes).length)required_changes.budget=true;
    }
  }else{
    payments=await reimbursementBusinessApi({...c,action:'bill-read',domain:'payments',contracts:c.payment_contracts});
    if(bill['t.status']==='-99'&&c.system_mode==='remuneration'){
      const allocation=await reimbursementExpensesBuild({...c,contracts:c.budget_contracts});budget=verify(allocation.read,'categories');requests.push(...payments.requests,...allocation.report.requests);
      if(Object.keys(allocation.report.changes).length)required_changes.budget=true;
    }else{budget=await reimbursementBusinessApi({...c,action:'bill-read',domain:'categories',contracts:c.budget_contracts});requests.push(...payments.requests,...budget.requests);}
  }
  return{payments,budget,requests,required_changes};
}
async function reimbursementWorkflowBuild(c){
  return ReimbursementHttpReadScope.run(c.expected_userid,()=>reimbursementWorkflowBuildScoped(c));
}
async function reimbursementWorkflowBuildScoped(c){
  const requests=[],http=createReimbursementHttp(c.expected_userid,{prefix:'WORKFLOW_API',requests,timeout:15000,allowed:['definition','binding','button','detailsTemplate','printDocument','workflowSecretary','workflowCollab','workflowSign','workflowAssets']});
  const{data:context,set,get}=http.contextCopy();
  for(const key of Object.keys(context))if(/^WF_YBX\$(56|61|109|1369)-/.test(key))delete context[key];
  set('61-serialno',c.serial);set('0-buss_var_list','');
  const basic=await reimbursementBusinessApi({...c,action:'bill-read',domain:'basic',contracts:c.basic_contracts}),bill=basic.bill;
  const main=await reimbursementAmountDefinition(http,'41',c.workflow_contract.definition_sha256);
  const init=main.wins.find(w=>String(w.winno)==='41').beforeLoadProcs;
  const raw=await http.post('commonUpdate_responseButtonEvent.action',{cmd:ReimbursementNativeProtocol.command('',init,context,{}),projid:'WF_YBX',userContext:JSON.stringify(context),async:false},'text');reimbursementWorkflowApply(raw,context);
  if(get('61-serialno')!==c.serial)throw Error('WORKFLOW_API_PREPARED_SERIAL_CHANGED');
  if(c.next_context)for(const[k,v]of Object.entries(c.next_context)){
    if(!(/^(?:0|61|56)-[A-Za-z0-9_.*.-]+$/i.test(k)||k.toUpperCase()==='LC_NODE_COUNT')||/^0-USERINFO\./i.test(k)||k.toUpperCase()==='61-SERIALNO'&&v!==c.serial)throw Error('WORKFLOW_API_NEXT_CONTEXT_NOT_SCOPED');set(k,v);
  }
  const forms=basic.sections.filter(s=>s.funcno==='60'&&s.rows.length===1);
  if(forms.length!==1||forms[0].rows[0].ABST!==c.plan.summary||![forms[0].rows[0].REQ_MOBILE,forms[0].rows[0].REQ_TELE].includes(c.plan.phone))throw Error('WORKFLOW_API_BASIC_SOURCE_CHANGED');
  const hash=async s=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s)))].map(n=>n.toString(16).padStart(2,'0')).join('');
  for(const[name,sha]of [['bx_frame_print.jsp',c.submission_contract.shell_sha256],['bx_dtl.jsp',c.submission_contract.inner_sha256]])if(await hash(await http.post('redirectCustomPage.action',{url:'Customize/customPrint/'+name},'text'))!==sha)throw Error('WORKFLOW_API_TEMPLATE_CHANGED');
  const html=await http.post('ybxPrint_yb4PrintHtml.action',{printModel:'P',printType:'报销确认',serialno:c.serial,byear:'',dbserver:'',bxId:'',appName:'YBX',bxOrderid:'',sign:'',jsonData:encodeURI('YB!P!NEW!P!'+c.serial+'!P!报销确认!P!'+c.expected_userid+'!P!undefined')},'text');
  const doc=new DOMParser().parseFromString(html,'text/html'),options=reimbursementSubmissionInventory(doc,c.serial,c.system_mode),nativeError=doc.body.textContent.includes('*错误*');
  const evidence=await reimbursementWorkflowEvidence(c,bill),{payments,budget}=evidence;requests.push(...evidence.requests);
  let invoiceRows=[],salary=null,preReview=[],requiredChanges={...evidence.required_changes};
  if(c.pre_review?.length){const pr=await reimbursementBusinessApi({...c,action:'bill-read',domain:'pre_review',contracts:c.pre_review_contracts});preReview=pr.sections;requests.push(...pr.requests);}
  if(bill['t.status']==='-99'&&c.system_mode==='daily'){
    if(c.pre_review?.length){const pr=await reimbursementPreReviewPrepare({...c,contracts:c.pre_review_contracts});requests.push(...pr.requests);if(Object.keys(pr.changes).length)requiredChanges.pre_review=true;}
  }
  if(c.system_mode==='remuneration'){
    salary=await reimbursementRemunerationInspect(c);
    reimbursementRemunerationPreview(doc,salary,c);
    if(options.proofs.length)throw Error('WORKFLOW_API_PAYROLL_PREVIEW_SCOPE_CHANGED');
    reimbursementRemunerationNoExtraPayments(payments);
  }else{
    const used=await reimbursementBusinessApi({...c,action:'bill-read',domain:'used_invoices',contracts:c.used_contracts});requests.push(...used.requests);
    if(used.sections.length!==1||used.sections[0].complete!==true)throw Error('WORKFLOW_API_INVOICES_INCOMPLETE');invoiceRows=used.sections[0].rows;
    const ids=invoiceRows.map(r=>r['t.tk_guid']).sort();if(JSON.stringify(ids)!==JSON.stringify([...c.invoice_guids].sort()))throw Error('WORKFLOW_API_INVOICE_SET_CHANGED');
  }
  const environment=Object.fromEntries(['0-LC_SIGNTYPE','0-M_YBXLC','0-YBX_WF_NAME','0-Is_ChengNuoShu','0-TJBXD_OPEN_SP','0-Lc_IsPrepdf','0-NEED_SP_YZM','0-GET_SIGN_DEF_URL','0-buss_var_list','61-lc_abst','61-Lc_IsSignCa','61-proof_gdzcfile'].map(k=>[k,get(k)]));
  const promiseDef=await reimbursementAmountDefinition(http,'512',c.workflow_contract.promise_definition_sha256),promiseForm=promiseDef.funcs.find(f=>String(f.funcno)==='700');
  if(promiseForm.bindSql!==c.workflow_contract.promise_bind)throw Error('WORKFLOW_API_PROMISE_CHANGED');
  const promise=ReimbursementHttpValues.formRows(await http.post('common_bindSQLDataBackend.action',{sqlid:promiseForm.bindSql.split('|')[0].slice(1),cvstr:'',projid:'WF_YBX'},'text'));
  if(promise.length!==1||typeof promise[0].CNS!=='string'||!promise[0].CNS)throw Error('WORKFLOW_API_PROMISE_MISSING');
  const report={ok:true,complete:true,action:'workflow-inspect',case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,business_transport:'native_http',system_write:false,save_dispatched:false,
    original:{bill,basic:basic.sections,options,invoices:invoiceRows,salary:salary?{original:salary.original,rows:salary.rows}:null,payments:payments.sections,budget:budget.sections,pre_review:preReview},required_changes:requiredChanges,native_error_present:nativeError,native_error_messages:(doc.body.textContent.match(/[^\n]{0,100}\*错误\*[^\n]{0,400}/g)||[]).slice(0,30),environment,
    promise_sha256:await hash(promise[0].CNS),promise_html:promise[0].CNS,promise_text:new DOMParser().parseFromString(promise[0].CNS,'text/html').body.textContent,
    definition_sha256:c.workflow_contract.definition_sha256,requests:[...basic.requests,...(salary?.requests||[]),...requests],
    final_applicant_approval_executed:false,sms_sent:false,applicant_action_required:bill['t.status']!=='-99'};
  if(get('0-buss_var_list')&&['workflow-inspect','applicant-prepare'].includes(c.action))report.department_candidates=await reimbursementWorkflowDepartment(c,{report,http,context,set,get,main},true);
  return{report,http,context,set,get,main};
}
async function reimbursementWorkflowInspect(c){return(await reimbursementWorkflowBuild(c)).report;}

async function reimbursementWorkflowDepartment(c,b,inspect=false){
  const d=await reimbursementAmountDefinition(b.http,'1166',c.workflow_contract.department_definition_sha256),f=d.funcs.find(f=>String(f.funcno)==='1369');
  const[sql,refs]=f.bindSql.split('|'),cvstr=refs.split(',').map(ref=>ref+'::'+b.get(ref.slice(1,-1))).join('$$');
  const data=ReimbursementHttpValues.formRows(await b.http.post('common_bindSQLDataBackend.action',{sqlid:sql.slice(1),cvstr,projid:'WF_YBX'},'text'));
  const plans=c.plan.department_approvers;
  if(data.length!==c.funding.length||!data.length||!inspect&&(!Array.isArray(plans)||plans.length!==data.length))throw Error('WORKFLOW_API_COMPLETE_DEPARTMENT_PLAN_REQUIRED');
  const rows=[],inspected=[];
  for(let i=0;i<data.length;i++){
    const row=ReimbursementNativeForm.bind(f,data[i],b.set),plan=plans?.[i],fund=c.funding[i];
    if(!row['t.condition_name'].includes(fund.code)||!row['t.condition_name'].includes('单位负责人（院级）')||!inspect&&(plan.project!==fund.code||!plan.source))throw Error('WORKFLOW_API_DEPARTMENT_OWNER');
    const spec=f.formFields.find(x=>x.fieldname==='t.drive_condition').binding_data,[query,binds]=spec.slice(1).split('|');
    const binding=binds.split(',').map(ref=>ref+'::'+b.get(ref.slice(1,-1))).join('$$');
    const options=ReimbursementHttpValues.formRows(await b.http.post('common_bindSQLDataBackend.action',{sqlid:query.slice(1),cvstr:binding,projid:'WF_YBX'},'text'));
    inspected.push({project:fund.code,condition:row['t.condition_name'],options});
    if(inspect)continue;
    if(options.filter(p=>p.VALUE===plan.id&&p.NAME===plan.id+'('+plan.name+')').length!==1)throw Error('WORKFLOW_API_DEPARTMENT_CANDIDATE_NOT_UNIQUE');
    rows.push({...row,'t.drive_condition':plan.id});
  }
  if(inspect)return inspected;
  for(const field of f.formFields){const values=rows.map(r=>r[field.fieldname]);if(values.some(v=>typeof v!=='string'||v.includes('$|')||/[\r\n]/.test(v)))throw Error('WORKFLOW_API_DEPARTMENT_WIRE');b.set('1369-'+field.fieldname,values.join('$|'));}
  return{definition:d,form:f,original:data,target:rows};
}

async function reimbursementWorkflowSave(c){
  let dispatched=false;const steps=[],requests=[];let b,nativeResponse=null;
  try{
    if(c.execute!==true||!c.pending_registered||!c.expected_plan||!['next','promise','department'].includes(c.action)||c.plan.submit_authorized!==true)throw Error('WORKFLOW_API_PENDING_OR_AUTHORIZATION');
    b=await reimbursementWorkflowBuild(c);
    for(const key of ['original','native_error_present','environment','promise_sha256','definition_sha256'])if(JSON.stringify(ReimbursementNativeForm.canonical(b.report[key]))!==JSON.stringify(ReimbursementNativeForm.canonical(c.expected_plan[key])))throw Error('WORKFLOW_API_PREPARE_DRIFT');
    if(b.report.original.bill['t.status']!=='-99'||b.report.original.bill['t.lc_finish']!=='F'||b.report.native_error_present)throw Error('WORKFLOW_API_DRAFT_WITHOUT_NATIVE_ERRORS_REQUIRED');
    if(Object.keys(b.report.required_changes||{}).length)throw Error('WORKFLOW_API_REQUIRED_FIELDS_NOT_SAVED_OR_SPECIAL_PROOF_REQUIRED');
    if(b.report.original.options.print!==(c.plan.delegate_finance_print===true?'Y':'N')||b.report.original.options.proofs.some(p=>p.contract&&p.selected!=='N'))throw Error('WORKFLOW_API_OPTIONS_NOT_SAVED');
    const execute=async(button,workflow=null)=>{
      const cmd=ReimbursementNativeProtocol.command(button.check_func,button.procs,b.context,{},workflow);dispatched=true;
      const raw=await b.http.post('commonUpdate_responseButtonEvent.action',{cmd,projid:'WF_YBX',userContext:JSON.stringify(b.context),async:false},'text');
      nativeResponse=typeof raw==='string'?raw.slice(0,32768):null;
      const missingNotes=c.action==='next'&&typeof raw==='string'&&raw.match(/^#left#font class="cont_tipword_NORMAL"#right#(还有[1-9][0-9]*个补充说明未填写！)#left#\/font#right#$/);
      if(missingNotes){b.set('61-tipinfo',missingNotes[1]);return{accepted:false,validation_required:true,updates:{}};}
      const departmentOnly=c.action==='promise'&&typeof raw==='string'&&raw.match(/^afterjs:local\(0-buss_var_list:([A-Z][A-Z0-9_]{0,79})\)$/);
      if(departmentOnly){b.set('0-buss_var_list',departmentOnly[1]);const updates={'0-BUSS_VAR_LIST':departmentOnly[1]};steps.push({caption:button.caption,updates});return{accepted:true,requires_department:true,updates};}
      const parsed=reimbursementWorkflowApply(raw,b.context,c);steps.push({caption:button.caption,updates:parsed.updates});return parsed;
    };
    if(c.action==='next'){
      for(const key of ['61-tipinfo','61-jhinfo','61-tipinfo_with_tick'])b.set(key,'');b.set('0-buss_var_list','');
      const parsed=await execute(c.workflow_contract.next),messages=Object.fromEntries(['61-tipinfo','61-jhinfo','61-tipinfo_with_tick'].map(key=>[key,b.get(key)]));
      const prompts=Object.values(messages).some(v=>typeof v==='string'&&v.trim());
      return{ok:true,action:'workflow-save',operation:'next',case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,business_transport:'native_http',save_dispatched:true,system_write:true,steps,next_context:parsed.updates,
        state:prompts?'native_information_requires_user_review':'next_staged_independent_readback_required',messages,workflow_started_by_action:false,final_applicant_approval_executed:false,sms_sent:false};
    }
    if(!c.next_verified||Object.values(c.next_messages||{}).some(v=>typeof v==='string'&&v.trim()))throw Error('WORKFLOW_API_ORIGINAL_NEXT_AND_PROMPTS_REQUIRED');
    if(b.get('0-LC_SIGNTYPE')!=='no')throw Error('WORKFLOW_API_EXTERNAL_SIGNATURE_REQUIRES_USER');
    let department=null;
    if(b.get('0-buss_var_list')){
      if(c.action!=='department')return{ok:true,action:'workflow-save',operation:c.action,case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,business_transport:'native_http',save_dispatched:false,system_write:false,state:'department_selection_required',workflow_started_by_action:false,final_applicant_approval_executed:false,sms_sent:false};
      department=await reimbursementWorkflowDepartment(c,b);await execute(c.workflow_contract.department);b.set('0-buss_var_list','');
    }else if(c.action==='department')throw Error('WORKFLOW_API_NO_PENDING_DEPARTMENT');
    // 清理原生C通配符的旧流程引用；新APPLY按官方空实例参数生成，签章定位另用U+单号。
    for(const no of ['56','101','1654'])for(const field of ['UNI_KEY','WF_NAME','NODE_ID','BASETABLE_KEY'])b.set(no+'-WF_INST.'+field,'');
    const name=b.get('0-YBX_WF_NAME');if(name!=='YBX_LC')throw Error('WORKFLOW_API_FLOW_NAME_CHANGED');
    let pdf='';
    if(b.get('61-Lc_IsSignCa')==='T'||b.get('0-Lc_IsPrepdf')==='Y'){
      dispatched=true;pdf=await b.http.post('ybxPrint_yb4PrintHtml.action',{printModel:'P',printType:'签章预生成',serialno:c.serial,byear:'',dbserver:'',bxId:'',appName:'YBX',bxOrderid:'',sign:'',jsonData:'YB!P!NEW!P!'+c.serial+'!P!签章预生成!P!'+b.get('61-Lc_IsSignCa')},'text');
      if(typeof pdf!=='string'||!/^[A-Za-z0-9_-]{1,200}$/.test(pdf))throw Error('WORKFLOW_API_SIGN_PDF_UNKNOWN');
    }
    // School UC.bindData returns an empty string for an absent optional comment.
    // This is not an approval opinion; preserve any actual native comment.
    const wf={wfName:name,wfAct:'APPLY',wfNodeId:'',wfState:'agree',wfUniKey:'',wfVars:':[]',wfKeyVal:'',wfComment:b.get('61-lc_abst')??'',wfCollab:'none',wfSecretaryRoleGuid:'',wfPdf:pdf};
    if(typeof wf.wfComment!=='string')throw Error('WORKFLOW_API_NATIVE_WORKFLOW_COMMENT_MISSING');
    const secretary=await b.http.post('workflow_getSecretaryDef.action',{projId:'WF_YBX',wfName:name,unikey:'',nodeId:''});
    if(secretary?.code!=='success'||!Array.isArray(secretary.data)||secretary.data.length)throw Error('WORKFLOW_API_SECRETARY_CHOICE_REQUIRES_USER');
    if(await b.http.post('wfNew_getCollabStatus.action',{wfName:name,wfAct:'APPLY',wfNodeId:'',wfUniKey:'',projid:'WF_YBX'},'text')!=='ok')throw Error('WORKFLOW_API_COLLAB_NOT_READY');
    const promise=b.get('0-Is_ChengNuoShu')==='Y';
    if(promise&&c.plan.promise_sha256!==b.report.promise_sha256)throw Error('WORKFLOW_API_EXACT_PROMISE_ACKNOWLEDGEMENT_REQUIRED');
    const submitted=await execute(promise?c.workflow_contract.promise:c.workflow_contract.submit,wf);
    if(submitted.requires_department)return{ok:true,action:'workflow-save',operation:c.action,case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,business_transport:'native_http',system_write:true,save_dispatched:true,steps,next_context:submitted.updates,state:'department_selection_required',workflow_started_by_action:false,final_applicant_approval_executed:false,sms_sent:false};
    if(b.get('0-GET_SIGN_DEF_URL'))await b.http.post('MainOperateAction_getSignByUrl.action',{serial_no:c.serial});
    const assets=b.get('61-proof_gdzcfile');if(assets)await b.http.post('ModDataApiAction_getZcFileList.action',{zccode:assets},'text');
    // 原生提交后的PDF生成与工作流提交分开报告；下载交付仍走正式打印工具。
    await b.http.post('ybxPrint_yb4PrintHtml.action',{printModel:'P',printType:'报销单列表',serialno:c.serial,byear:'',dbserver:'',bxId:'',appName:'YBX',bxOrderid:'',sign:'',jsonData:'YB!P!NEW!P!'+c.serial+'!P!报销单列表!P!'+c.expected_userid},'text');
    return{ok:true,action:'workflow-save',operation:c.action,case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,business_transport:'native_http',system_write:true,save_dispatched:true,steps,department,workflow_instance:submitted.workflow_instance||null,state:'submitted_independent_readback_required',workflow_started_by_action:true,final_applicant_approval_executed:false,sms_sent:false,requests:[...b.report.requests,...requests]};
  }catch(error){return{ok:false,action:'workflow-save',operation:c.action,case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,business_transport:'native_http',system_write:dispatched,save_dispatched:dispatched,state:dispatched?'unknown_original_workflow_recovery_required':'rejected_before_dispatch',error:String(error.message),native_response:nativeResponse,steps,requests,final_applicant_approval_executed:false,sms_sent:false};}
}
