const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),{webcrypto}=require('node:crypto');
const source=['finance_http.js','finance_native_protocol.js','finance_native_form.js','finance_preview_api.js','finance_pre_review_api.js'].map(n=>fs.readFileSync(path.join(__dirname,'../tools',n),'utf8')).join('\n');
const canonical=v=>Array.isArray(v)?v.map(canonical):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;
async function run(mode='ok') {
  const names=['param_show_name','param_desc','param_key','param_key2','param_key3','param_key4','param_show_rule'];
  const form={funcno:'1297',formFields:names.map(n=>({fieldname:'t.'+n,nullable:true,readonly:n==='param_show_name'||n==='param_desc'})),typeMap:{}};
  const def={wins:[{winno:1099,func_btns:[{btnguid:'SAVE',check_func:'',procs:['`$'+'A'.repeat(32).toLowerCase()+':#1297-t.param_key3#;#61-serialno#;#61-lc_params_name#;#61-lc_params_uuid#;#61-param_list_src_table#;#0-userinfo.userid#']}]}],funcs:[form],funcMap:{},actMap:{}};
  const hash=Buffer.from(await webcrypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(canonical(def))))).toString('hex');
  const slots=Array.from({length:4},(_,i)=>({index:i,name:'ustc_xmysr'+(i||''),count:'10',uuid:String(i).repeat(32),source_table:'QT',multi_choice:'N',select_or_and:'A',project_code:''}));
  const original=slots.map(s=>Object.fromEntries([...names.map(n=>[n.toUpperCase(),'']),['SERIAL_NO','10001'],['LC_PARAMS_NAME',s.name],['PARAM_SHOW_NAME','[项目预审人'+(s.index+1)+']'],['LC_APPROVER',''],['LC_APPROVER_NAME','']]));
  const read={ok:true,complete:true,bill:{'t.status':mode==='approved'?'10':'-99','t.lc_finish':mode==='approved'?'T':'F'},workflow_parameters:slots,requests:[],sections:slots.map((slot,i)=>({slot,funcno:'1297',kind:'form',rows:[original[i]]}))};
  let reads=0,saves=0;const sent=[],$={getProjID:()=> 'WF_YBX',UC:{userData:{'0-USERINFO.USERID':'TEST001'},bindData:()=> 'TEST001'},ajax:o=>{
    sent.push(o);let result=def;
    if(o.url==='commonUpdate_responseButtonEvent.action'){
      saves++;const ctx=JSON.parse(o.data.userContext);assert.equal(ctx['WF_YBX$61-LC_PARAMS_NAME'],slots[saves-1].name);
      assert.equal(ctx['WF_YBX$1297-T.PARAM_KEY3'],saves===1?'P001/Person One':'P002/Person Two');result='pass';
    }
    return{done(fn){this.fn=fn;return this;},fail(fn){if(mode==='timeout'&&saves===2)fn({status:0},'timeout');else this.fn(result);return this;}};
  }};
  const ctx=vm.createContext({window:{jQuery:$},performance,Date,TextEncoder,crypto:webcrypto,location:{origin:'https://cwzh.ustc.edu.cn',pathname:'/YBX/main.jsp'},
    reimbursementBusinessApi:async()=>{reads++;const r=structuredClone(read);if(mode==='drift'&&reads===2)r.sections[0].rows[0].PARAM_KEY3='Changed';if(mode==='hidden')r.workflow_parameters[0].count='0';return r;}});vm.runInContext(source,ctx);
  const c={case_id:'fixture',serial:'10001',expected_userid:'TEST001',pre_review:mode==='empty'?[]:[{id:'P001',name:'Person One'},{id:'P002',name:'Person Two'}],
    pre_review_contract:{winno:'1099',funcno:'1297',save_button:'SAVE',definition_sha256:hash}};
  const p=await vm.runInContext('reimbursementPreReviewPrepare('+JSON.stringify(c)+')',ctx);
  const r=await vm.runInContext('reimbursementPreReviewSave('+JSON.stringify({...c,execute:true,expected_plan:Object.fromEntries(['original','changes','slots','definition_sha256'].map(k=>[k,p[k]]))})+')',ctx);
  return{p,r,saves};
}
(async()=>{
  const a=await run();assert.equal(a.saves,2);assert.equal(a.r.system_write,true);assert.equal(Object.keys(a.p.original).length,4);
  const empty=await run('empty');assert.equal(empty.saves,0);assert(empty.r.requires_readback);
  for(const mode of ['approved','drift']){const a=await run(mode);assert.equal(a.saves,0);assert.equal(a.r.state,'rejected_before_save');}
  const unknown=await run('timeout');assert.equal(unknown.saves,2);assert.equal(unknown.r.state,'unknown_save_readback_required');
  await assert.rejects(()=>run('hidden'));
  console.log('pre-review API: native slots, original parameters, exact declared people, draft/visibility/drift guards, partial unknown without replay passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
