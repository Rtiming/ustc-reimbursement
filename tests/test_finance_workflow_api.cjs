const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const code=['finance_native_protocol.js','finance_native_form.js','finance_workflow_api.js'].map(f=>fs.readFileSync(path.join(__dirname,'../tools',f),'utf8')).join('\n');
async function fixture(action,mode='ok'){
 const values={'0-LC_SIGNTYPE':'no','0-YBX_WF_NAME':'YBX_LC','0-Is_ChengNuoShu':'Y','61-LC_ABST':'本案工作流','0-buss_var_list':'','0-USERINFO.USERID':'TEST001'},store={},calls=[];
 const key=k=>(k.startsWith('0-')?'':'WF_YBX$')+k.toUpperCase(),set=(k,v)=>store[key(k)]=v,get=k=>store[key(k)];Object.entries(values).forEach(([k,v])=>set(k,v));set('61-serialno','123');
 if(mode==='missing-comment')delete store[key('61-LC_ABST')];if(mode==='invalid-comment')set('61-LC_ABST',42);
 const button=caption=>({caption,check_func:'',procs:['`$'+'a'.repeat(32)+':#61-serialno#;#0-userinfo.userid#']});
 const report={original:{bill:{'t.status':'-99','t.lc_finish':'F'},options:{print:'N',proofs:[]}},native_error_present:mode==='material',environment:{stable:true},promise_sha256:'promise-hash',definition_sha256:'fixed',requests:[]};
 const c={execute:true,pending_registered:true,action,case_id:'fixture',serial:'123',expected_userid:'TEST001',plan:{submit_authorized:true,promise_sha256:mode==='unacknowledged'?'other':'promise-hash'},expected_plan:report,next_verified:true,next_messages:{},workflow_contract:{next:button('下一步'),promise:button('已阅读遵守并提交'),submit:button('REAL_提交'),department:button('保存')}};
 if(mode==='signature')set('0-LC_SIGNTYPE','external');if(mode==='department')set('0-buss_var_list','selection');if(mode==='prompt')c.next_messages={'61-tipinfo':'需本人阅读'};
 const http={post:async(url,data)=>{calls.push({url,data});if(url==='workflow_getSecretaryDef.action')return{code:'success',data:mode==='secretary'?[{}]:[]};if(url==='wfNew_getCollabStatus.action')return mode==='collab'?'wait':'ok';if(url==='commonUpdate_responseButtonEvent.action'){if(mode==='unknown')throw Error('timeout');if(mode==='department-callback')return 'afterjs:local(0-buss_var_list:TEST_ROLE)';if(mode==='unsafe-callback')return 'afterjs:run()';return action==='next'?'passlocal(61-lc_abst:本案工作流;0-buss_var_list:)':'pass';}if(url==='ybxPrint_yb4PrintHtml.action')return'created';throw Error('unexpected:'+url);}};
 const ctx=vm.createContext({}),b={report,http,context:store,set,get};vm.runInContext(code,ctx);ctx.reimbursementWorkflowBuild=async()=>b;
 const result=await vm.runInContext('reimbursementWorkflowSave('+JSON.stringify(c)+')',ctx);return{result,calls};
}
(async()=>{
 const next=await fixture('next');assert.equal(next.result.ok,true);assert.equal(next.result.workflow_started_by_action,false);assert.equal(next.calls.length,1);
 const promise=await fixture('promise');assert.equal(promise.result.ok,true,JSON.stringify(promise.result));const save=promise.calls.find(c=>c.url==='commonUpdate_responseButtonEvent.action');
 assert(save.data.cmd.includes('wfVars:\":[]\"'));assert(save.data.cmd.includes('wfUniKey:\"\"'));assert(save.data.cmd.includes('wfNodeId:\"\"'));assert.equal(promise.result.final_applicant_approval_executed,false);assert.equal(promise.result.sms_sent,false);
 const emptyComment=await fixture('promise','missing-comment');assert.equal(emptyComment.result.ok,true);assert(emptyComment.calls.find(c=>c.url==='commonUpdate_responseButtonEvent.action').data.cmd.includes('wfComment:""'));
 for(const mode of ['material','signature','prompt','secretary','collab','unacknowledged','invalid-comment']){const f=await fixture('promise',mode);assert.equal(f.result.ok,false,mode);assert.equal(f.calls.filter(c=>c.url==='commonUpdate_responseButtonEvent.action').length,0,mode);}
 const callback=await fixture('promise','department-callback');assert.equal(callback.result.ok,true);assert.equal(callback.result.state,'department_selection_required');assert.equal(callback.result.workflow_started_by_action,false);assert.equal(callback.result.next_context['0-BUSS_VAR_LIST'],'TEST_ROLE');const unsafe=await fixture('promise','unsafe-callback');assert.equal(unsafe.result.ok,false);
 const dept=await fixture('promise','department');assert.equal(dept.result.state,'department_selection_required');assert.equal(dept.result.workflow_started_by_action,false);assert.equal(dept.calls.length,0);
 const unknown=await fixture('promise','unknown');assert.equal(unknown.result.ok,false);assert.equal(unknown.result.save_dispatched,true);assert.equal(unknown.calls.filter(c=>c.url==='commonUpdate_responseButtonEvent.action').length,1);
 const forbidden=await fixture('applicant-agree');assert.equal(forbidden.result.ok,false);assert.equal(forbidden.calls.length,0);
 assert(!/\.click\s*\(|\.trigger\s*\(|dispatchEvent|eval\s*\(|activation_ifsend|approveCheck/.test(code));
 console.log('workflow HTTP: native empty APPLY instance/wfVars, original next, promise SHA, secretary/collab/material/signature gates, no SMS/final approval and timeout no retry passed');
})().catch(e=>{console.error(e);process.exitCode=1});

{
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const code=fs.readFileSync(require('node:path').join(__dirname,'../tools/finance_workflow_api.js'),'utf8');
(async()=>{
for(const mode of ['ok','wrong-name','duplicate','inspect']){
 const f={bindSql:'`sql|#0-buss_var_list#',formFields:[{fieldname:'t.drive_condition',binding_data:'`options|#0-buss_var_list#'}]},store={'0-buss_var_list':'ROLE'},options=[{VALUE:'X123',NAME:mode==='wrong-name'?'X123(Other)':'X123(Example)'}];if(mode==='duplicate')options.push({...options[0]});
 const c={workflow_contract:{},funding:[{code:'FUND'}],plan:{department_approvers:[{project:'FUND',id:'X123',name:'Example',source:'verified'}]}};
 const ctx=vm.createContext({reimbursementAmountDefinition:async()=>({funcs:[{...f,funcno:'1369'}]}),ReimbursementHttpValues:{formRows:x=>x},ReimbursementNativeForm:{bind:(_f,row)=>row}});vm.runInContext(code,ctx);
 const b={get:k=>store[k],set:(k,v)=>store[k]=v,http:{post:async(_u,d)=>d.sqlid==='sql'?[{'t.condition_name':'[单位负责人（院级）]FUND','t.drive_condition':''}]:options}};
 let result,error;try{result=await ctx.reimbursementWorkflowDepartment(c,b,mode==='inspect')}catch(e){error=e}
 if(['wrong-name','duplicate'].includes(mode))assert(error);else{assert(!error);if(mode==='ok')assert.equal(store['1369-t.drive_condition'],'X123');else assert.equal(result[0].options[0].VALUE,'X123')}
}console.log('department: native uppercase candidates, exact identity, duplicates and read-only inspection passed');})().catch(e=>{console.error(e);process.exitCode=1});

}

{
 const ctx=vm.createContext({});vm.runInContext(fs.readFileSync(path.join(__dirname,'../tools/finance_workflow_api.js'),'utf8'),ctx);
 const config={action:'department',serial:'123'};
 const response='passlocal(0-YBX_WF_NAME:YBX_LC;61-lc_abst:;bxwc_tips:<span>材料提示</span>;61-proof_gdzcfile:;)[123]passlocal(0-lc_node_count:0;0-lc_finish:F;61-node_id:508;61-uni_key:4567;61-wf_name:YBX_LC;61-spinfo_serialno:123;109-wf_inst.NODE_ID:508;109-wf_inst.UNI_KEY:4567;109-wf_inst.BASETABLE_KEY:123;0-after_lc_tips:<span style="font-size：24px;font-weight：bold;">申请提交</span>)';
 assert.equal(ctx.reimbursementWorkflowSubmitted(response,config).workflow_instance.uni_key,'4567');
 for(const bad of [response.replace('[123]pass','[999]pass'),response.replace('61-spinfo_serialno:123','61-spinfo_serialno:999'),response.replace('109-wf_inst.UNI_KEY:4567','109-wf_inst.UNI_KEY:999'),response+'afterjs:run()',response.replace('61-lc_abst:','0-userinfo.userid:OTHER;61-lc_abst:')])assert.throws(()=>ctx.reimbursementWorkflowSubmitted(bad,config));
 assert.equal(ctx.reimbursementWorkflowSubmitted(response,{...config,action:'next'}),null);
 console.log('workflow submission: display HTML ignored, native numeric instance and original serial bound, no scripts executed');
}
