'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../tools/submission.js'),'utf8');
function fixture(change={}){
  let clicks=0;const ctx={'#61-serialno#':'123','#61-node_id#':change.node||'508','#0-userinfo.userid#':change.user||'SYNTH001','#0-NEED_SP_YZM#':change.captcha};
  const visible=extra=>({getClientRects:()=>[{}],...extra});
  const field=visible({id:'formWF_YBX_109_t-remark',name:'',value:'',disabled:false});
  const button=visible({disabled:false,getAttribute:k=>k==='btnname'?'同意':k==='guid'?(change.guid||'EF5365A90D3947A8B0272D9339EA8076'):null,click:()=>{clicks++;field.getClientRects=()=>[];}});
  const grid=visible({grid:{hDiv:{loading:false}}});
  const def={btnguid:'EF5365A90D3947A8B0272D9339EA8076',caption:'同意',wf_enabled:true,wf_state:'agree',wf_trans:change.transition||'APPROVAL',wf_comment:'#109-t.remark#',pre_check:true,post_check:true,call_proc:true};
  const document={getElementById:id=>id==='gridWF_YBX_1816'?grid:id===field.id?field:null,
    querySelector:()=>({querySelectorAll:()=>[button]}),querySelectorAll:selector=>selector==='.ui-dialog'?[]:[field,...(change.visibleCaptcha?[visible({id:'form_sms_yzm',name:'',value:''})]:[])]};
  const dollar=()=>({jqGrid:(method)=>method==='getDataIDs'?['r1']:method==='getGridParam'?1:{'t.node_id':'508','t.node_name':'预约流程-申请人','t.appinfo':(change.assignee||'SYNTH001')+'-合成人','t.operator':''}});
  Object.assign(dollar,{getProjID:()=> 'WF_YBX',UC:{bindData:k=>ctx[k]},page:{list:{WF_YBX_76:{wins:[{winno:76,func_btns:[def]}]}}},F:{setInputData:(id,type,value)=>{field.value=value;},getDataToContext:()=>{ctx['#109-t.remark#']=field.value;}}});
  const sandbox=vm.createContext({document,$:dollar,location:{origin:'https://cwzh.ustc.edu.cn'},setTimeout,clearTimeout});vm.runInContext(source,sandbox);
  sandbox.ReimbursementSubmission.configure({}, {serial:'123',expected_userid:'SYNTH001',applicant_agreement_authorized:change.authorized!==false,approval_opinion:change.opinion||'同意'});
  return{sandbox,clicks:()=>clicks};
}
(async()=>{
  const good=fixture();assert.equal((await good.sandbox.ReimbursementSubmission.applicantAgree()).action,'applicant_agree_requested');assert.equal(good.clicks(),1);
  const verifiedSession=fixture({captcha:'Y'});await verifiedSession.sandbox.ReimbursementSubmission.applicantAgree();assert.equal(verifiedSession.clicks(),1);
  const bad=[{node:'509'},{user:'SYNTH002'},{assignee:'SYNTH002'},{visibleCaptcha:true},{authorized:false},{opinion:'拒绝'},{guid:'CHANGED'},{transition:'REJECT'}];
  for(const config of bad){const x=fixture(config);await assert.rejects(()=>x.sandbox.ReimbursementSubmission.applicantAgree());assert.equal(x.clicks(),0);}
  process.stdout.write(JSON.stringify({ok:true,passed:bad.length+1,browser_access:false,system_write:false})+'\n');
})().catch(e=>{process.stderr.write(e.stack+'\n');process.exitCode=1;});
