const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),crypto=require('node:crypto'),path=require('node:path');
const root=path.join(__dirname,'../tools'),source=['finance_native_protocol.js','finance_native_form.js','finance_applicant_api.js'].map(f=>fs.readFileSync(path.join(root,f),'utf8')).join('\n');
const hash=s=>crypto.createHash('sha256').update(s).digest('hex');
const canon=v=>Array.isArray(v)?v.map(canon):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canon(v[k])])):v;
async function run(mode='ok'){
 const requests=[],sent=[],state={node:'508'};function handler(){}
 const row={'t.serial_no':'123','t.basetable_key':'123','t.node_id':'508','t.wfname':'YBX_LC','t.wf_name':'YBX_LC','t.uni_key':'789','t.nodecnt':'1','t.collab_ord':'none','t.isNeedCollab':'F','t.signtype':'no','t.curr_mans':'TEST001 - Test','t.node_name1':'申请人/审批(TEST001 - Test)','t.remark':'Purpose','t.amount':'10.00'};
 const d={wins:[],funcs:[{funcno:'105',tablenames:'@{ybx_query_pc_package.wfplc_query_approval_v1:#514-t.serial_no#}',colModel:Object.keys(row).map(name=>({name}))}],funcMap:{},actMap:{}};
 const contract={button:{check_func:'`$'+'a'.repeat(32)+':#109-t.remark#',procs:['`$'+'b'.repeat(32)+':#61-serialno#']},applicant_definition_sha256:hash(JSON.stringify(canon(d))),list_definition_sha256:hash(JSON.stringify(canon(d)))};
 for(const k of ['verification_guard','agreement_guard','generate_command','open_sign','pdf_compensation','button_engine','after_lc_agree','after_lc_agree_bxpdf'])contract[k+'_sha256']=hash(String(handler));
 if(mode==='definition_drift')contract.list_definition_sha256='bad';
 if(mode==='other_owner')row['t.curr_mans']='OTHER - Person';if(mode==='signature')row['t.signtype']='force';if(mode==='amount')row['t.amount']='11.00';if(mode==='collaborator')row['t.collab_ord']='other';
 const config={case_id:'fixture',serial:'123',expected_amount:'10.00',expected_userid:'TEST001',plan:{summary:'Purpose',approval_opinion:'同意',applicant_agreement_authorized:mode!=='unauthorized'},applicant_contract:contract,pending_registered:true};
 const http={guard(){},contextCopy(){const data={'0-USERINFO.USERNAME':'Test'};return{data,set:(k,v)=>{data[(k.startsWith('0-')?'':'WF_YBX$')+k.toUpperCase()]=v;},get:k=>data[(k.startsWith('0-')?'':'WF_YBX$')+k.toUpperCase()]};},async post(url,data){requests.push(url);
  if(url.startsWith('loadDefinition'))return d;
  if(url.startsWith('commonQuery_'))return{records:1,total:1,page:1,rows:[{cell:Object.values(row)}]};
  if(url==='ybxPrint_yb4PrintHtml.action')return mode==='document_not_ready'?'error:pending next approver':'OK';
  if(url==='MainOperateAction_updateLcRedis.action')return'OK';
  if(url==='activation_ifsend.action')return{status:mode==='sms'?'wyz':'yyz'};
  if(url==='MainOperateAction_approveCheck.action')return{status:mode==='check_reject'?'error':'success'};
  if(url==='MainOperateAction_approveSealReq.action')return{status:mode==='seal'?'sealSuccess':'sealPass'};
  if(url==='workflow_getSecretaryDef.action')return{code:'success',data:[]};
  if(url==='wfNew_getCollabStatus.action')return mode==='collab_reject'?'no':'ok';
  if(url==='commonUpdate_responseButtonEvent.action'){sent.push(data);if(mode==='unknown')throw Error('TIMEOUT');state.node='510';return'pass';}
  throw Error('Unexpected endpoint:'+url);
 }};
 const history=()=>[{'t.node_id':'508','t.node_name':'预约流程-申请人','t.appinfo':state.node==='508'?'TEST001-Test':'-','t.operator':state.node==='508'?'':'TEST001','t.op_comment':state.node==='508'?'':'同意'},...(state.node==='508'?[]:[{'t.node_id':'510','t.node_name':'Next approver','t.appinfo':'OTHER-Person','t.operator':''}])];
 const context={crypto:crypto.webcrypto,TextEncoder,performance,Date,window:{jQuery:{page:{btn:{generateCmd:handler}},openSign:handler,button:{btnClickFunc:handler}},before_sp_yzm:handler,before_lc_agree:handler,lcpdf_blnew:handler,after_lc_agree:handler,after_lc_agree_bxpdf:handler},createReimbursementHttp:()=>http,ReimbursementHttpReadScope:{run:(_,f)=>f()},ReimbursementHttpValues:{decode:String,moneyCents:v=>Math.round(Number(v)*100)},reimbursementBusinessApi:async()=>({complete:true,bill:{'t.serial_no':'123','t.basetable_key':'123','t.node_id':mode==='stale_list'?'508':state.node,'t.wf_name':'YBX_LC','t.uni_key':'789','t.abst':'Purpose','t.total_amt':'10.00'},sections:[{funcno:'1816',complete:true,rows:history()}],requests:[]}),config};
 vm.createContext(context);vm.runInContext(source,context);
 try{config.prepared=await vm.runInContext('reimbursementApplicantInspect(config)',context);}catch(error){return{error,sent};}
 const result=await vm.runInContext('reimbursementApplicantAgree(config)',context);
 const readback=state.node!=='508'?await vm.runInContext('reimbursementApplicantInspect({...config,readback:true})',context):null;
 return{result,sent,requests,readback};
}
(async()=>{
 const ok=await run();assert.equal(ok.result.approval_dispatched,true);assert.equal(ok.sent.length,1);assert(ok.sent[0].cmd.includes('wfAct:"APPROVAL"'));assert(ok.sent[0].cmd.includes('wfNodeId:"508"'));assert(ok.sent[0].cmd.includes('wfKeyVal:"123"'));assert(!ok.sent[0].cmd.includes('APPLY'));
 for(const mode of ['definition_drift','other_owner','signature','amount','collaborator','sms','unauthorized','check_reject','seal','collab_reject']){const r=await run(mode);assert.equal(r.sent.length,0,mode);}
 const unknown=await run('unknown');assert.equal(unknown.sent.length,1);assert.equal(unknown.result.state,'unknown_original_recovery_required');assert.equal(unknown.result.approval_dispatched,true);
 const stale=await run('stale_list');assert.equal(stale.result.ok,true);assert.equal(stale.readback.completed,true);assert.equal(stale.readback.original.workflow_node_id,'510');assert.equal(stale.readback.original.main_list_node_stale,true);assert.equal(stale.sent.length,1);
 const docs=await run('document_not_ready');assert.equal(docs.result.ok,true);assert.equal(docs.result.documents.documents.length,4);assert(docs.result.documents.documents.every(d=>d.status==='not_ready'));assert.equal(docs.readback.completed,true);
 assert(!/\.click\s*\(|\.trigger\s*\(|openExternal|activation_(?:updateUser|huoQuCode)/.test(source));
 console.log('Applicant HTTP: fixed original 508, scope/signature/SMS/native guards, no replay on unknown passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
