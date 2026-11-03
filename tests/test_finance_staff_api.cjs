const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const{webcrypto,createHash}=require('node:crypto');
const source=['finance_http.js','finance_native_protocol.js','finance_native_form.js','finance_staff_api.js'].map(n=>fs.readFileSync(path.join(__dirname,'../tools',n),'utf8')).join('\n');
function canonical(v){return Array.isArray(v)?v.map(canonical):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;}
function fixture(mode='ok'){
 const definition={wins:[],funcs:[{funcno:60,formFields:[{id:'t-lc_accepter',Events:[{proc:'@fixture_lookup|fixture'}]}]}],funcMap:{},actMap:{}};
 const hash=createHash('sha256').update(JSON.stringify(canonical(definition))).digest('hex');
 const data={'0-USERINFO.USERID':'TEST001','WF_YBX$60-T.LC_ACCEPTER':'OTHER','WF_YBX$61-SERIALNO':'999'},before=structuredClone(data),calls=[];
 const $={getProjID:()=> 'WF_YBX',UC:{bindData:()=>mode==='identity'?'OTHER':'TEST001',userData:data},ajax:o=>({done(fn){this.fn=fn;return this;},fail(){
  calls.push(o);let result;
  if(o.url.startsWith('loadDefinition'))result=mode==='drift'?{...definition,wins:[{}]}:definition;
  else{assert.equal(o.url,'common_callProc.action');assert.equal(o.data.procName,'@fixture_lookup');
   const c=JSON.parse(o.data.userData);assert.equal(c['WF_YBX$61-SERIALNO'],undefined);
   const q=c['WF_YBX$60-T.LC_ACCEPTER'];const value=mode==='extra_format'?'T100/Fixture/Fixture Department':q==='T100'?'Fixture':' ';
   result='passlocal(60-lc_value:'+value+';60-lc_field:lc_accepter_name'+(mode==='extra'?';60-UNREVIEWED:bad':'')+')';
  }this.fn(result);return this;}})};
 const ctx=vm.createContext({window:{jQuery:$},crypto:webcrypto,TextEncoder,performance,Date,Uint8Array,AbortSignal,location:{origin:'https://cwzh.ustc.edu.cn',pathname:'/YBX/main.jsp'}});vm.runInContext(source,ctx);
 const call=q=>vm.runInContext('reimbursementStaffLookup('+JSON.stringify({case_id:'fixture',expected_userid:'TEST001',staff_query:q,basic_contract:{definition_sha256:hash}})+')',ctx);
 return{call,calls,data,before};
}
(async()=>{
 const f=fixture(),byId=await f.call('T100');assert.deepEqual(JSON.parse(JSON.stringify(byId.person)),{id:'T100',name:'Fixture'});assert.equal(byId.system_write,false);
 assert.equal((await f.call('T999')).found,false);assert.deepEqual(f.data,f.before);
 for(const mode of ['identity','drift','extra','extra_format'])await assert.rejects(()=>fixture(mode).call('T100'));
 for(const q of ['x;save()','*','PB00','SA00','','Fixture','杨小鹏']){const bad=fixture();await assert.rejects(()=>bad.call(q));assert.equal(bad.calls.length,0);}
 console.log('staff lookup: pinned definition, fixed lookup, immutable context, unknown identity/response rejection passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
