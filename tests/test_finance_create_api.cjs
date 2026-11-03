const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {webcrypto,createHash}=require('node:crypto');
const source=['finance_http.js','finance_native_protocol.js','finance_native_form.js','finance_create_api.js'].map(n=>fs.readFileSync(path.join(__dirname,'../tools',n),'utf8')).join('\n');
const hash=s=>createHash('sha256').update(s).digest('hex');
const canonical=v=>Array.isArray(v)?v.map(canonical):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;
async function fixture(mode='ok'){
  const row={'t.tk_guid':'a'.repeat(32),'t.tk_code_abst':'普/99000000000000001234','t.tk_total_amt':'1.00','t.tk_mod_flag':'NORMAL','t.auth_rst_desc':'正常(已认证)','t.serial_no':'','t.PC_Has_chosen':'N','t.tk_type':'DZ'};
  const defaultRow={REQ_SNO:'TEST001',BX_TYPE:mode.startsWith('domestic')?'2':'1',TKS_GUID:''};
  const fields=['ym.req_sno','ym.bx_type','ytp.tks_guid','ytp.tks_remark'].map(fieldname=>({fieldname,initdata:'',nullable:true,readonly:true}));
  const init='`$'+'1'.repeat(32)+':#0-userinfo.userid#;#0-OUTER-UNIKEY#;#0-USERINFO.ALL_USERROLE#';
  const guard={btnguid:'guard',check_func:'`$'+'2'.repeat(32)+':#53-MultiRows#;#53-MultiCount#',procs:['`$'+'3'.repeat(32)+':#53-MultiRows#']};
  const create={btnguid:'create',check_func:'',procs:['`$'+'4'.repeat(32)+':#53-MultiRows#;#45-ym.req_sno#']};
  const bind='$'+'5'.repeat(32)+'|#0-userinfo.userid#,#0-userinfo.username#,#61-bxxginfo_serialno#,#61-BX_JBXX_BX_TYPE#';
  const definition={wins:[{winno:33,beforeLoadProcs:[init]},{winno:39,func_btns:[guard,create]}],funcs:[{funcno:45,bindSql:bind,formhtml:'@FFFF/jbxx_45.html',formFields:fields}],funcMap:{},actMap:{}};
  const template='<table><tr style="display:none"><td>#ym.req_sno#</td></tr></table>';
  const userData={'0-USERINFO.USERID':'TEST001','0-USERINFO.USERNAME':'Synthetic Person','0-OUTER-UNIKEY':'','0-USERINFO.ALL_USERROLE':'role',
      '0-ZHEJIANGLAB_ISOPEN':'N','0-YGCG_VUE_TYPE':'','0-YGCG_BXORD':'17','0-CUSTOMER_NAME':'Synthetic University','0-G0SERIALNO':'old-stale-serial'};
  const before=JSON.stringify(userData);let finalCalls=0,buttonCalls=0;
  const $={getProjID:()=> 'WF_YBX',UC:{userData,bindData:key=>userData[key.slice(1,-1).toUpperCase()]??''},ajax:o=>({done(fn){this.fn=fn;return this;},fail(fn){
    if(o.url.startsWith('loadDefinition'))this.fn(definition);
    else if(o.url==='common_getCustomFormFileContent.action')this.fn(mode==='template'?template+'drift':template);
    else if(o.url==='common_bindSQLDataBackend.action'){assert(o.data.cvstr.includes('#61-bxxginfo_serialno#::'));this.fn(JSON.stringify([defaultRow]));}
    else{
      buttonCalls++;const data=JSON.parse(o.data.userContext);
      assert.equal(data['0-G0SERIALNO'],'');assert.equal(data['WF_YBX$53-MULTIROWS'],row['t.tk_guid']);
      if(o.data.cmd.includes('"`$'+'1'.repeat(32)))this.fn('pass');
      else if(o.data.cmd.includes('"`$'+'2'.repeat(32))){
        if(mode==='judge')this.fn('judge:学校要求确认');
        else if(mode==='missing')this.fn('pass');
        else this.fn('passlocal(61-BXSX_COUNT:'+(mode==='branch'?'1':'0')+';61-BXCG_COUNT:0;61-BXYYF_MZ_COUNT:0;61-BXYYF_ZY_COUNT:0)');
      }else{finalCalls++;if(mode==='unknown')fn({status:0},'timeout');else this.fn(mode==='serial'?'pass':mode==='domestic_preview'?'passlocal(0-G0SERIALNO:123456789;0-bx_type:2;0-g0Display:jbxx,btxx;pgWrite_item24-cj_Display:N;61-serialno:123456789)':mode==='identity_response'?'passlocal(0-G0SERIALNO:123456789;0-userinfo.userid:OTHER)':mode==='wrong_type'?'passlocal(0-G0SERIALNO:123456789;0-bx_type:2)':'passlocal(0-G0SERIALNO:123456789)');}
    }return this;}})};
  const callback=function nativeFixedCallback(){};
  const contract={definition_sha256:hash(JSON.stringify(canonical(definition))),view_init:[init],bind_sql:bind,template_filename:'FFFF/jbxx_45.html',template_sha256:hash(template),
    guard_button:guard,create_button:create,callbacks:{ybx_jbxx_next_before:hash(String(callback))},
    guard_update_keys:['61-BXSX_COUNT','61-BXCG_COUNT','61-BXYYF_MZ_COUNT','61-BXYYF_ZY_COUNT','0-G0SERIALNO'],create_update_keys:['0-G0SERIALNO']};
  const ctx=vm.createContext({window:{jQuery:$,ybx_jbxx_next_before:callback},performance,Date,TextEncoder,crypto:webcrypto,Uint8Array,
    location:{origin:'https://cwzh.ustc.edu.cn',pathname:'/YBX/main.jsp'}});
  vm.runInContext(source+`;async function reimbursementBusinessApi(){return{ok:true,complete:${mode!=='incomplete_pool'},rows:[${JSON.stringify(row)}]};};this.prepare=reimbursementCreatePrepare;this.execute=reimbursementCreateExecute;`,ctx);
  const c={case_id:'fixture',expected_userid:'TEST001',expected_amount:'1.00',creation_eligible:true,create_contract:contract,selected:[{guid:row['t.tk_guid'],amount:'1.00',native_row:row}],pool_contracts:[],execute:true,pending_registered:true,token:'original'};
  if(mode.startsWith('domestic'))c.bill_type='2';
  return {ctx,c,prepare:()=>ctx.prepare(c),execute:()=>ctx.execute(c),finalCalls:()=>finalCalls,buttonCalls:()=>buttonCalls,unchanged:()=>assert.equal(JSON.stringify(userData),before)};
}
(async()=>{
  let f=await fixture();f.c.prepared=await f.prepare();assert.equal(f.buttonCalls(),0);const result=await f.execute();assert.equal(result.serial,'123456789');assert.equal(f.finalCalls(),1);f.unchanged();
  await assert.rejects(f.execute(),/ORIGINAL_INTENT/);assert.equal(f.finalCalls(),1);
  f=await fixture('domestic');f.c.prepared=await f.prepare();assert.equal(f.c.prepared.original['ym.bx_type'],'2');assert.equal((await f.execute()).ok,true);f.unchanged();
  f=await fixture('domestic_preview');f.c.prepared=await f.prepare();assert.equal((await f.execute()).ok,true);assert.equal(f.finalCalls(),1);f.unchanged();
  for(const mode of ['branch','judge','missing']){f=await fixture(mode);f.c.prepared=await f.prepare();const r=await f.execute();assert.equal(r.ok,false);assert.equal(r.create_dispatched,false);assert.equal(f.finalCalls(),0);f.unchanged();}
  for(const mode of ['unknown','serial','identity_response','wrong_type']){f=await fixture(mode);f.c.prepared=await f.prepare();const r=await f.execute();assert.equal(r.ok,false);assert.equal(r.state,'create_result_unknown');assert.equal(f.finalCalls(),1);await assert.rejects(f.execute(),/ORIGINAL_INTENT/);assert.equal(f.finalCalls(),1);}
  f=await fixture('template');await assert.rejects(f.prepare(),/TEMPLATE_CHANGED/);assert.equal(f.buttonCalls(),0);
  f=await fixture('incomplete_pool');await assert.rejects(f.prepare(),/POOL_INCOMPLETE/);assert.equal(f.buttonCalls(),0);
  f=await fixture();f.c.prepared=await f.prepare();f.c.prepared.targets[0]['t.tk_total_amt']='2.00';await assert.rejects(f.execute(),/PREPARE_CHANGED/);assert.equal(f.buttonCalls(),0);
  f=await fixture();f.c.pending_registered=false;await assert.rejects(f.execute(),/PENDING_REQUIRED/);assert.equal(f.buttonCalls(),0);
  console.log('native create fixed protocol, private context, branch holds and single dispatch passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
