const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const {fixture}=require('./test_finance_approver_api.cjs');
const sha=s=>crypto.createHash('sha256').update(s).digest('hex');
function setup(){
 const f=fixture('sms'),$=f.context.window.jQuery,els={},opens=[],editors=[];
 $.active=0;$.page.currPage='6';
 const element=(id,value='')=>({id,value,getClientRects:()=>[{}],disabled:false,readOnly:false,type:'textarea',closest:()=>null});
 $.UC.bindData=expression=>{const key=expression.replaceAll('#','').toUpperCase();return f.data[key]??f.data['WF_YBX$'+key];};
 $.UC.setData=function(key,value){f.data[key]=value;};
 $.page.openExternal=function(project,page,title){
  opens.push(page);$.page.currPage=page;
  if(page==='76')els['formWF_YBX_109_t-remark']=element('formWF_YBX_109_t-remark','native default');
  if(page==='1160'){
   const code=element('formWF_YBX_1362_t-code');Object.defineProperty(code,'value',{get(){throw Error('SMS CODE MUST NEVER BE READ');}});els[code.id]=code;
  }
 };
 f.context.window.yzm_jy_1362=function(){throw Error('Must not invoke human callback');};
 f.context.document={getElementById:id=>els[id],querySelectorAll:selector=>selector==='input,select,textarea'?editors:[]};
 for(const [fn,key]of [[f.context.window.yzm_jy_1362,'verification_callback_sha256'],[$.page.openExternal,'page_open_external_sha256'],[$.UC.setData,'context_set_data_sha256']])f.config.contract[key]=sha(String(fn));
 f.defs['1160']={wins:[],funcs:[],funcMap:{},actMap:{}};
 // Native form canonical sorts keys but JSON still preserves integer-key ordering.
 f.config.contract.verification_definition_sha256=sha(JSON.stringify({actMap:{},funcMap:{},funcs:[],wins:[]}));
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../tools/finance_approver_verification.js'),'utf8'),f.context);
 return{...f,$,els,opens,editors,element};
}
(async()=>{
 const f=setup(),r=await f.call('reimbursementApproverVerification',{authorized:true});
 assert.equal(r.node_id,'449');assert.equal(r.code_read,false);assert.equal(r.approval_dispatched,false);assert.equal(r.sms_sent,false);assert.deepEqual(f.opens,['76','1160']);
 assert.equal(f.els['formWF_YBX_109_t-remark'].value,'情况属实');assert.equal(f.data['0-YZM_CAPTION'],'同意');
 assert.equal(f.sent.filter(x=>x.url==='commonUpdate_responseButtonEvent.action').length,0);
 await f.call('reimbursementApproverVerification',{authorized:true});assert.deepEqual(f.opens,['76','1160']);
 f.els['formWF_YBX_109_t-remark'].value='different human draft';await assert.rejects(()=>f.call('reimbursementApproverVerification',{authorized:true}),/DIFFERENT_VERIFICATION/);assert.equal(f.els['formWF_YBX_109_t-remark'].value,'different human draft');
 const editing=setup();editing.editors.push(editing.element('unrelated-editor','must preserve'));await assert.rejects(()=>editing.call('reimbursementApproverVerification',{authorized:true}),/EXISTING_EDITOR/);assert.deepEqual(editing.opens,[]);
 const drift=setup();drift.config.contract.verification_callback_sha256='wrong';await assert.rejects(()=>drift.call('reimbursementApproverVerification',{authorized:true}),/HANDLER_CHANGED/);assert.deepEqual(drift.opens,[]);
 const unauthorized=setup();await assert.rejects(()=>unauthorized.call('reimbursementApproverVerification'),/AUTHORITY/);assert.deepEqual(unauthorized.opens,[]);
 console.log('approver verification: visible exact-role handoff, original opinion, no code read/send or approval, preserved other edits passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
