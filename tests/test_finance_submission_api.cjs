const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),{webcrypto,createHash}=require('node:crypto');
const source=['finance_http.js','finance_native_protocol.js','finance_native_form.js','finance_submission_api.js'].map(n=>fs.readFileSync(__dirname+'/../tools/'+n,'utf8')).join('\n');
const canonical=v=>Array.isArray(v)?v.map(canonical):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;
const hash=v=>createHash('sha256').update(v).digest('hex');
function doc(mode='ok'){
  const inputs=[];
  function group(name,selected,handler,cells){
    const container={getAttribute:k=>k===(name==='form_grantcwprint'?'onclick':'onchange')?handler:null};
    const row={cells:cells?.map(textContent=>({textContent}))};
    for(const value of ['Y','N'])inputs.push({checked:value===selected,disabled:mode==='disabled',
      getAttribute:k=>({name,value,type:'radio'}[k]??null),closest:k=>k==='ul'?container:row});
  }
  group('form_grantcwprint',mode==='noop'?'N':'Y',"pageOpen('委托财务处打印')");
  group('form_proof_choose_1',mode==='noop'?'N':'Y',"pageOpen_v1('辅助证据必选','1&合同号&合同号')",['合同号[报销项：材料费]',mode==='contract_present'?'共1笔':'共0笔','原生合同说明','是否选择']);
  group('form_proof_choose_2','Y',"pageOpen_v1('辅助证据必选','2&材料申请&original-guid')",['材料申请','共1笔','原生材料说明','是否选择']);
  if(mode==='duplicate')inputs.push(inputs[0]);
  return {body:{textContent:(mode==='identity'?'报销单号：999':'报销单号：123')+' 基本信息 报销项信息 支付信息 财务审批意见 会计审核'},querySelectorAll:()=>inputs};
}
async function fixture(mode='ok'){
  const print={check_func:'',procs:['`$'+'a'.repeat(32)+':#61-serialno#;#61-bx_btinfo_params#;#0-userinfo.userid#']};
  const proof={check_func:'',procs:['`$'+'b'.repeat(32)+':#61-serialno#;#61-proof_type#;#61-proof_is_choose#;#61-proof_guid#;#0-userinfo.userid#']};
  const definition={wins:[],funcs:[],funcMap:{},actMap:{}},shell='fixed shell',inner='fixed inner',sent=[];
  const uc={'0-USERINFO.USERID':'TEST001','0-USERINFO.USERNAME':'Synthetic Operator','WF_YBX$61-SERIALNO':'OTHER'},before=JSON.stringify(uc);
  const $={getProjID:()=> 'WF_YBX',UC:{userData:uc,bindData:()=> 'TEST001'},ajax:o=>({done(fn){this.fn=fn;return this;},fail(fn){
    sent.push(o);let result;
    if(o.url.startsWith('loadDefinition'))result=definition;
    else if(o.url==='redirectCustomPage.action')result=o.data.url.endsWith('bx_dtl.jsp')?(mode==='handler'?inner+'drift':inner):shell;
    else if(o.url==='ybxPrint_yb4PrintHtml.action')result='synthetic confirmation HTML';
    else{
      assert.equal(o.url,'commonUpdate_responseButtonEvent.action');const ctx=JSON.parse(o.data.userContext);assert.equal(ctx['WF_YBX$61-SERIALNO'],'123');
      if(o.data.cmd.includes('b'.repeat(32))){assert.equal(ctx['WF_YBX$61-PROOF_TYPE'],'合同号');assert.equal(ctx['WF_YBX$61-PROOF_IS_CHOOSE'],'N');assert.equal(ctx['WF_YBX$61-PROOF_GUID'],'合同号');}
      else assert.equal(ctx['WF_YBX$61-BX_BTINFO_PARAMS'],'N');
      if(mode==='unknown'){fn({status:0},'timeout');return this;}result=mode==='judge'?'judge:学校提示':'pass';
    }
    this.fn(result);return this;}})};
  const ctx=vm.createContext({performance,crypto:webcrypto,TextEncoder,window:{jQuery:$},location:{origin:'https://cwzh.ustc.edu.cn',pathname:'/YBX/main.jsp'},DOMParser:class{parseFromString(){return doc(mode);}},
    reimbursementBusinessApi:async()=>({bill:{'t.status':'-99','t.lc_finish':'F'},requests:[],sections:[{funcno:'60',kind:'form',rows:[{ABST:'Synthetic purpose',REQ_MOBILE:'13000000000'}]}]})});
  vm.runInContext(source+';this.prepare=reimbursementSubmissionPrepare;this.save=reimbursementSubmissionOptions;',ctx);
  const c={case_id:'fixture',serial:'123',expected_userid:'TEST001',expected_amount:'1.00',plan:{summary:'Synthetic purpose',phone:'13000000000',contract_exists:false,delegate_finance_print:false,submit_authorized:true},
    basic_contracts:[],submission_contract:{definition_sha256:hash(JSON.stringify(canonical(definition))),shell_sha256:hash(shell),inner_sha256:hash(inner),buttons:{'委托财务处打印':print,'辅助证据必选':proof}}};
  try{
    if(mode==='readonly'){
      c.action='inspect';c.plan.submit_authorized=false;
      const p=await ctx.prepare(c);assert.equal(p.system_write,false);
      const denied=await ctx.save({...c,execute:true,pending_registered:true,expected_plan:p});
      assert.equal(denied.state,'rejected_before_save');assert.equal(denied.error,'SUBMISSION_API_PLAN_SCOPE');
      assert(!sent.some(o=>o.url==='commonUpdate_responseButtonEvent.action'));return denied;
    }
    const p=await ctx.prepare(c);assert(!sent.some(o=>o.url==='commonUpdate_responseButtonEvent.action'));assert.equal(p.target.proofs[1].selected,'Y');
    if(mode==='drift')p.original.proofs[1].selected='N';
    const result=await ctx.save({...c,execute:true,pending_registered:mode!=='pending',expected_plan:p});
    const count=sent.filter(o=>o.url==='commonUpdate_responseButtonEvent.action').length;
    assert.equal(count,['drift','pending','noop'].includes(mode)?0:['unknown','judge'].includes(mode)?1:2);return result;
  }finally{assert.equal(JSON.stringify(uc),before);}
}
(async()=>{
  const inventoryContext=vm.createContext({});vm.runInContext(source,inventoryContext);
  const cell=(xtype,cells)=>({getAttribute:()=>"pageOpen_v1('特殊事项','"+xtype+"')",closest:()=>({cells:cells.map(textContent=>({textContent}))})});
  const rows=[cell('33&33',['33','其他附件','','可填写']),cell('维修费&维修费',['维修费','维修明细','','请填写并上传'])];
  const notes=inventoryContext.reimbursementSupplementaryInventory({querySelectorAll:()=>rows});
  assert.equal(notes.length,2);assert.equal(notes[1].xtype,'维修费&维修费');
  assert.throws(()=>inventoryContext.reimbursementSupplementaryInventory({querySelectorAll:()=>[rows[0],rows[0]]}));
  await fixture('readonly');
  assert.equal((await fixture()).state,'saved_independent_readback_required');assert.equal((await fixture('noop')).save_dispatched,false);
  for(const m of ['drift','pending'])assert.equal((await fixture(m)).state,'rejected_before_save');
  for(const m of ['unknown','judge'])assert.equal((await fixture(m)).state,'unknown_save_readback_required');
  for(const m of ['disabled','duplicate','identity','contract_present','handler'])await assert.rejects(()=>fixture(m));
  assert(!/\.click\s*\(|\.trigger\s*\(|dispatchEvent|eval\s*\(/.test(source));
  console.log('native submission options: exact source, complete groups, preserve other proofs, no-op and unknown hold passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
