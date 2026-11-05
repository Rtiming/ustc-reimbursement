const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {webcrypto,createHash}=require('node:crypto');
const source=['finance_http.js','finance_native_protocol.js','finance_funding_api.js'].map(n=>fs.readFileSync(__dirname+'/../tools/'+n,'utf8')).join('\n');
const canonical=v=>Array.isArray(v)?v.map(canonical):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;
async function run(mode='ok',write=false){
  const init='`$'+'a'.repeat(32),choose='`$'+'b'.repeat(32),save='`$'+'c'.repeat(32);
  const definition={wins:[{winno:170,beforeLoadProcs:[init+':#61-serialno#;#0-userinfo.userid#'],grid_btns:[{btnguid:'SELECT',check_func:'',procs:[choose+':#242-t.uni_prj_code#;#242-t.grant_id#;#61-serialno#;#61-ismulti_proj#;#0-userinfo.userid#']}]},
    {winno:171,func_btns:[{btnguid:'SAVE',check_func:'',procs:[save+':#61-ismulti_proj#;#61-serialno#;#0-userinfo.userid#']}]}],funcs:[],funcMap:{},actMap:{}};
  const hash=createHash('sha256').update(JSON.stringify(canonical(definition))).digest('hex');
  const candidates=['F1','F2'].map((code,i)=>({'t.uni_prj_code':code,'t.grant_id':'G'+i,'t.CHARGE_NAME':'Owner','t.select_flag':''}));
  const budgets=mode==='noop'?candidates.map((r,i)=>({'t.serial_no':'123','t.tlevel':'2','t.uni_prj_code':r['t.uni_prj_code'],'t.ord':String(i),'t.amt':'5.00'})):[];
  const uc={'0-USERINFO.USERID':'TEST001','WF_YBX$61-SERIALNO':'OTHER'},before=JSON.stringify(uc),sent=[];
  const $={getProjID:()=> 'WF_YBX',UC:{userData:uc,bindData:()=> 'TEST001'},ajax:o=>{
    sent.push(o);let result=definition;
    if(o.url==='commonUpdate_responseButtonEvent.action'){
      const data=JSON.parse(o.data.userContext);assert.equal(data['WF_YBX$61-SERIALNO'],'123');assert.equal(data['WF_YBX$61-ISMULTI_PROJ'],'Y');
      if(mode==='timeout')return{done(){return this;},fail(fn){fn({},'timeout');return this;}};
      if(o.data.cmd.includes(choose)){
        const row=candidates.find(r=>r['t.uni_prj_code']===data['WF_YBX$242-T.UNI_PRJ_CODE']);
        assert(row);assert.equal(row['t.grant_id'],data['WF_YBX$242-T.GRANT_ID']);row['t.select_flag']='已选';
      }
      result=mode==='error'?'error:reject':mode==='judge'?'judge:confirm':mode==='context'?'passlocal(0-userinfo.userid:OTHER)':'pass';
    }
    return{done(fn){this.fn=fn;return this;},fail(){this.fn(result);return this;}};
  }};
  const c={case_id:'fixture',serial:'123',expected_userid:'TEST001',expected_amount:'10.00',funding:[{code:'F1',amount:'5.00',owner_name:'Owner'},{code:'F2',amount:'5.00',owner_name:'Owner'}],
    funding_contract:{definition_sha256:hash,select_button:'SELECT',save_button:'SAVE'},budget_contracts:[]};
  if(mode==='definition')definition.wins[0].grid_btns[0].pre_check=true;
  if(mode==='duplicate')candidates.push({...candidates[0]});
  if(mode==='owner_duplicate')candidates.push({...candidates[0],'t.grant_id':'G_OTHER','t.CHARGE_NAME':'Other'});
  if(mode==='selected_other_owner')candidates.push({...candidates[0],'t.grant_id':'G_OTHER','t.CHARGE_NAME':'Other','t.select_flag':'已选'});
  if(mode==='missing_owner'){candidates.push({...candidates[0],'t.CHARGE_NAME':'Other'});delete c.funding[0].owner_name;}
  if(mode==='extra')candidates.push({'t.uni_prj_code':'OTHER','t.select_flag':'已选'});
  if(mode==='owner')candidates[0]['t.CHARGE_NAME']='Other';
  if(mode==='grant')candidates[0]['t.grant_id']=null;
  if(mode==='empty_grant')candidates[0]['t.grant_id']='';
  if(mode==='amount')c.funding[0].amount='6.00';
  if(mode==='empty_default'){c.budget_allocation_mode='system_default_single_funding';c.funding=[{code:'F1',amount:'10.00',owner_name:'Owner'}];}
  if(mode==='existing')budgets.push({'t.serial_no':'123','t.uni_prj_code':'OTHER','t.tlevel':'2'});
  const context=vm.createContext({performance,crypto:webcrypto,TextEncoder,window:{jQuery:$},location:{origin:'https://cwzh.ustc.edu.cn',pathname:'/YBX/main.jsp'},
    reimbursementBusinessApi:async conf=>({bill:{'t.status':mode==='approved'?'10':'-99','t.lc_finish':mode==='approved'?'T':'F'},requests:[],
      sections:[{funcno:conf.domain==='funding'?'242':'307',kind:'grid',complete:mode!=='incomplete',rows:conf.domain==='funding'?candidates:budgets}]})});
  try{
    const plan=await vm.runInContext(source+'\nreimbursementFundingPrepare('+JSON.stringify(c)+')',context);
    assert.equal(sent.filter(x=>x.url==='commonUpdate_responseButtonEvent.action').length,0);
    if(!write)return plan;
    if(mode==='drift')plan.candidates[0].grant_id='WRONG';
    const result=await vm.runInContext('reimbursementFundingSave('+JSON.stringify({...c,execute:mode!=='no_intent',expected_plan:plan})+')',context);
    const writes=sent.filter(x=>x.url==='commonUpdate_responseButtonEvent.action');
    if(['ok','empty_grant'].includes(mode)){assert.equal(writes.length,4);assert(writes[0].data.cmd.includes(init));assert(writes[3].data.cmd.includes(save));}
    if(['timeout','error','judge','context'].includes(mode))assert.equal(writes.length,1);
    if(['noop','drift','no_intent'].includes(mode))assert.equal(writes.length,0);
    return result;
  }finally{assert.equal(JSON.stringify(uc),before);}
}
(async()=>{
  assert.equal((await run()).save_dispatched,false);
  assert.deepEqual(JSON.parse(JSON.stringify((await run('empty_default')).changes)),{projects:['F1']});
  for(const mode of ['definition','duplicate','extra','owner','grant','amount','existing','approved','incomplete','missing_owner','selected_other_owner'])await assert.rejects(()=>run(mode));
  assert.equal((await run('owner_duplicate')).candidates[0].grant_id,'G0');
  assert.equal((await run('owner_duplicate',true)).state,'saved_independent_readback_required');
  assert.equal((await run('ok',true)).state,'saved_independent_readback_required');
  assert.equal((await run('empty_grant',true)).state,'saved_independent_readback_required');
  for(const mode of ['timeout','error','judge','context'])assert.equal((await run(mode,true)).state,'unknown_save_readback_required');
  for(const mode of ['drift','no_intent'])assert.equal((await run(mode,true)).state,'rejected_before_save');
  assert.equal((await run('noop',true)).state,'already_matches_readback_required');
  assert(!/\.click\s*\(|\.trigger\s*\(|dispatchEvent|eval\s*\(/.test(source));
  console.log('funding HTTP: pinned candidate identity, no preview writes, bounded mutation sequence, unknown outcome and no UI passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
