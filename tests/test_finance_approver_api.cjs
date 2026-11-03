const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),crypto=require('node:crypto'),path=require('node:path');
const root=path.join(__dirname,'..');
const source=['finance_http.js','finance_native_protocol.js','finance_native_form.js','finance_approver_api.js'].map(name=>fs.readFileSync(path.join(root,'tools',name),'utf8')).join('\n');
const sha=s=>crypto.createHash('sha256').update(s).digest('hex');
function canonical(v){if(Array.isArray(v))return v.map(canonical);if(v&&typeof v==='object')return Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])]));return v;}
function fixture(mode='normal',decision='agree'){
 const sent=[],state={decided:false,checks:0};
 const target={'t.serial_no':'1234','t.req_name':'APPLICANT01 - Other person','t.remark':'Synthetic reimbursement','t.uni_prj':'SYNTHETIC',
  't.wfname':'YBX_LC','t.wf_name':'YBX_LC','t.uni_key':'5678','t.node_id':'449','t.collab_ord':'none','t.isNeedCollab':'F','t.signtype':'no','t.nodecnt':'1','t.curr_mans':'USER01 - Teacher','t.amount':'10.00'};
 const hook=function(op){},handler=function(){};
 const buttons=['agree','reject'].map(d=>({btnguid:d,check_func:'',procs:[],beforeclick:hook,afterclick:hook}));
 const defs={'74':{wins:[],funcs:[],funcMap:{},actMap:{}},'76':{wins:[{winno:76,func_btns:buttons}],funcs:[],funcMap:{},actMap:{}}};
 const contract={buttons:Object.fromEntries(buttons.map(b=>[b.btnguid,{btnguid:b.btnguid,before_sha256:sha(String(hook)),after_sha256:sha(String(hook))}]))};
 for(const key of ['verification_guard','agreement_guard','rejection_guard','generate_command','button_engine','signature_model','pdf_compensation','after_lc_agree','after_lc_agree_bxpdf'])contract[key+'_sha256']=sha(String(handler));
 contract.list_definition_sha256=sha(JSON.stringify(canonical(defs['74'])));contract.approval_definition_sha256=sha(JSON.stringify(canonical(defs['76'])));
 const data={'0-USERINFO.USERID':'USER01','0-USERINFO.USERNAME':'Teacher','0-OTHER_GLOBAL':'kept','WF_YBX$61-SERIALNO':'OTHER_CASE','WF_YBX$1362-T.CODE':'MUST_NOT_REUSE'};
 if(mode==='manual')data['0-MANUAL_NODE']='449';
 const $={getProjID:()=> 'WF_YBX',UC:{userData:data,bindData:()=> 'USER01'},page:{btn:{generateCmd:handler}},button:{btnClickFunc:handler},ajax:options=>{
  sent.push(options);let answer;
  if(options.url.startsWith('loadDefinition'))answer=defs[options.data.winno];
  else if(options.url==='activation_ifsend.action')answer={status:mode==='sms'||mode==='expires'&&state.checks++>0?'wyz':'yyz'};
  else if(options.url==='MainOperateAction_approveCheck.action')answer={status:mode==='judge'?'judge':'success'};
  else if(options.url==='MainOperateAction_approveSealReq.action')answer={status:'sealPass'};
  else if(options.url==='workflow_getSecretaryDef.action')answer={code:'success',data:mode==='secretary'?[{}]:[]};
  else if(options.url==='wfNew_getCollabStatus.action')answer='ok';
  else if(options.url==='commonUpdate_responseButtonEvent.action'){state.decided=true;answer='pass';}
  else if(options.url==='ybxPrint_yb4PrintHtml.action')answer=mode==='document_failure'?'error: synthetic render failure':'synthetic rendered document';
  else if(options.url==='MainOperateAction_updateLcRedis.action')answer='ok';
  else throw Error('unexpected endpoint '+options.url);
  return{done(fn){this.fn=fn;return this;},fail(fn){if(mode==='unknown'&&options.url==='commonUpdate_responseButtonEvent.action')fn({status:0},'timeout');else this.fn(answer);return this;}};
 }};
 const business=async c=>{
  if(c.action==='approvals-list'){
   const row={...target};if(mode==='changed_amount')row['t.amount']='11.00';if(mode==='other_actor')row['t.curr_mans']='OTHER01 - Not this actor';
   return{complete:true,rows:state.decided?[]:[row],requests:[]};
  }
  if(['approval-log','approval-context'].includes(c.action)){
   const history=state.decided&&mode!=='disappear_only'?[{'t.node_id':'449','t.operator':'USER01','t.op_comment':mode==='wrong_comment'?'different':'情况属实'}]:[{'t.node_id':'449','t.operator':'','t.op_comment':''}];
   const approved={...target};if(mode==='header_drift')approved['t.amount']='12.00';
   return{complete:true,history,approved_rows:state.decided?[approved]:[],approval_fields:[{REMARK:'',FJ:null}],requests:[]};
  }
  throw Error('unexpected business action '+c.action);
 };
 const context={performance,Date,TextEncoder,crypto:crypto.webcrypto,location:{origin:'https://cwzh.ustc.edu.cn',pathname:'/YBX/main.jsp'},
  window:{jQuery:$,before_sp_yzm:handler,before_lc_agree:handler,before_lc_reject:handler,getLcSignModel:handler,lcpdf_blnew:handler,after_lc_agree:handler,after_lc_agree_bxpdf:handler},reimbursementBusinessApi:business};
 vm.createContext(context);vm.runInContext(source,context);
 const config={case_id:'account_fixture',expected_userid:'USER01',target,decision,opinion:'情况属实',contract,
  source:{job_id:'synthetic'},queue_contracts:[],log_contracts:[],history_contracts:[],context_contracts:[],submission_contracts:[]};
 function call(method,extra={}){context.config={...config,...extra};return vm.runInContext(method+'(config)',context);}
 return{call,sent,state,config,data,context,defs};
}
module.exports={fixture};
if(require.main===module)(async()=>{
 const f=fixture();const preview=await f.call('reimbursementApproverInspect');assert.equal(preview.state,'ready');assert.equal(preview.node_id,'449');assert.equal(preview.completed,false);
 const result=await f.call('reimbursementApproverDecide',{authorized:true,pending_registered:true,prepared:preview});assert.equal(result.approval_dispatched,true);assert.equal(result.state,'submitted_independent_readback_required');
 const writes=f.sent.filter(s=>s.url==='commonUpdate_responseButtonEvent.action');assert.equal(writes.length,1);
 const command=vm.runInNewContext('('+writes[0].data.cmd+')');assert.equal(command.wfNodeId,'449');assert.equal(command.wfComment,'情况属实');assert.equal(command.wfState,'agree');
 const sentContext=JSON.parse(writes[0].data.userContext);assert.equal(sentContext['WF_YBX$61-SERIALNO'],'1234');assert(!Object.hasOwn(sentContext,'WF_YBX$1362-T.CODE'));assert.equal(f.data['WF_YBX$61-SERIALNO'],'OTHER_CASE');
 const readback=await f.call('reimbursementApproverInspect',{readback:true,baseline:preview});assert.equal(readback.completed,true);
 const finalized=await f.call('reimbursementApproverFinalize',{authorized:true,baseline:preview});assert.equal(finalized.cache_refreshed,true);assert.equal(finalized.documents.length,4);assert.equal(finalized.approval_dispatched,false);assert.equal(f.sent.filter(s=>s.url==='commonUpdate_responseButtonEvent.action').length,1);
 const unapproved=fixture();await assert.rejects(()=>unapproved.call('reimbursementApproverFinalize',{authorized:true,baseline:preview}));assert.equal(unapproved.sent.filter(s=>s.url==='ybxPrint_yb4PrintHtml.action').length,0);
 const docFail=fixture('document_failure');docFail.state.decided=true;await assert.rejects(()=>docFail.call('reimbursementApproverFinalize',{authorized:true,baseline:preview}));assert.equal(docFail.sent.filter(s=>s.url==='commonUpdate_responseButtonEvent.action').length,0);
 for(const mode of ['changed_amount','other_actor','manual']){const bad=fixture(mode);await assert.rejects(()=>bad.call('reimbursementApproverInspect'));assert.equal(bad.sent.filter(s=>s.url==='commonUpdate_responseButtonEvent.action').length,0);}
 for(const mode of ['sms','expires','judge','secretary']){const blocked=fixture(mode),p=await blocked.call('reimbursementApproverInspect');const r=await blocked.call('reimbursementApproverDecide',{authorized:true,pending_registered:true,prepared:p});assert.equal(r.approval_dispatched,false,mode);}
 for(const mode of ['disappear_only','wrong_comment','header_drift']){const bad=fixture(mode),p=await bad.call('reimbursementApproverInspect');bad.state.decided=true;const after=await bad.call('reimbursementApproverInspect',{readback:true,baseline:p});assert.equal(after.completed,false,mode);}
 const lost=fixture('unknown'),p=await lost.call('reimbursementApproverInspect');const uncertain=await lost.call('reimbursementApproverDecide',{authorized:true,pending_registered:true,prepared:p});assert.equal(uncertain.state,'unknown_original_recovery_required');assert.equal(uncertain.approval_dispatched,true);assert.equal(lost.sent.filter(s=>s.url==='commonUpdate_responseButtonEvent.action').length,1);
 const reject=fixture('normal','reject'),rp=await reject.call('reimbursementApproverInspect');await reject.call('reimbursementApproverDecide',{authorized:true,pending_registered:true,prepared:rp});const rw=reject.sent.find(s=>s.url==='commonUpdate_responseButtonEvent.action');assert.equal(vm.runInNewContext('('+rw.data.cmd+')').wfState,'reject');assert.equal(JSON.parse(rw.data.userContext)['WF_YBX$109-T.FJ'],'');
 console.log('approver API: bound teacher role, unchanged amount, original opinion, SMS expiry, no replay and independent log/header proof passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
