const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),{webcrypto,createHash}=require('node:crypto');
const root=require('node:path').resolve(__dirname,'..');
const source=['finance_http.js','finance_native_protocol.js','finance_native_form.js','finance_travel_api.js'].map(f=>fs.readFileSync(root+'/tools/'+f,'utf8')).join('\n');
const canonical=v=>Array.isArray(v)?v.map(canonical):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;
const digest=v=>createHash('sha256').update(JSON.stringify(canonical(v))).digest('hex');
async function fixture(mode='ok',save=true){
 const guid='a'.repeat(32),userid='TEST001',calls=[];
 const row={'t.tk_guid':guid,'t.tk_mainguid':'b'.repeat(32),'t.tk_type':'INVOICE','t.tk_code_abst':'9'.repeat(20),'t.tk_no':'9'.repeat(20),
  't.tk_mod_flag':'ERROR','t.canedit':'Y','t.serial_no':'','t.PC_Has_chosen':'N','t.auth_rst_desc':'正常(已认证) 出行人不能为空','t.tk_total_amt':'10.00'};
 const data={TOUR_STARTCITY:'合肥市-新桥机场',TOUR_ENDCITY:'北京市-首都机场',TOUR_STARTCITY_TX:'合肥市',TOUR_ENDCITY_TX:'北京市',
  TOUR_STARTDATE:'2026-10-01',TOUR_STARTTIME:'09:00',TOUR_ENDDATE:'2026-10-01',TOUR_ENDTIME:'11:00',PASSENGER_SNAME:'',PASSENGER_SNO:'',PASSENGER_SCARD:'original-id',TOUR_SEATCLASS:'经济舱',TRIPMODEL:'飞机',TOUR_STARTPORT:'original-port'};
 const bindSql='$'+'c'.repeat(32)+'|#61-tks_guid#,#0-userinfo.userid#';
 const def={wins:[{winno:938,func_btns:[{caption:'保存',check_func:'',procs:['`$'+'e'.repeat(32)+':#1150-t.passenger_sname#;#1150-t.passenger_sno#;#61-tks_guid#;#395-t.inv_type#']}]}],
  funcs:[{funcno:'1150',bindSql,typeMap:{},formFields:Object.keys(data).map(k=>({fieldname:'t.'+k.toLowerCase()}))}],funcMap:{},actMap:{}};
 const cols=['t.citycode','t.cityname','t.airportname','t.shortname'],airportDef={wins:[],funcs:[{funcno:'444',tablenames:'fixed',needcount:true,colModel:cols.map(name=>({name}))}],funcMap:{},actMap:{}};
 const c={case_id:'fixture',expected_userid:userid,pool_contracts:[],travel_contract:{winno:'938',funcno:'1150',save_caption:'保存',bindSql,definition_sha256:digest(def),
  airport_definition_sha256:digest(airportDef),airport_query:{tablenames:'fixed'},airport_columns:cols},
  edit:{schema:'ustc-invoice-travel-edit-v1',native_guid:guid,native_status:'ERROR',invoice_number:'9'.repeat(20),amount:'10.00',changes:{traveler:'合成人',person_id:'TEST002'}}};
 if(mode.startsWith('airport'))Object.assign(c.edit.changes,{arrival_city:'上海',arrival_airport:'虹桥机场'});
 if(mode==='other')data.PASSENGER_SNAME='其他人';if(mode==='bound')row['t.serial_no']='123';if(mode==='selected')row['t.PC_Has_chosen']='Y';
 if(mode==='incomplete')row['t.auth_rst_desc']='未认证';if(mode==='multi')data.MULTI=true;
 if(mode==='seat')c.edit.changes.seat_class='公务舱';if(mode==='date')Object.assign(c.edit.changes,{departure_date:'2026-10-02',arrival_date:'2026-10-02'});if(mode==='date_order')c.edit.changes.departure_date='2026-10-02';
 const uc={'0-USERINFO.USERID':userid},before=JSON.stringify(uc);
 const $={getProjID:()=> 'WF_YBX',UC:{userData:uc,bindData:()=>userid},ajax:o=>{
  calls.push(o);let result;
  if(o.url==='loadDefinition.action?type=W')result=o.data.winno==='325'?airportDef:def;
  else if(o.url==='common_bindSQLDataBackend.action')result=JSON.stringify(mode==='multi'?[data,data]:[data]);
  else if(o.url.startsWith('commonQuery_doQuery'))result={records:mode==='airport_duplicate'?2:1,total:1,page:1,rows:Array(mode==='airport_duplicate'?2:1).fill({cell:['SHA','上海市','虹桥机场','SHA']})};
  else if(o.url==='commonUpdate_responseButtonEvent.action'){
   const ctx=JSON.parse(o.data.userContext);assert.equal(ctx['WF_YBX$1150-T.PASSENGER_SNAME'],'合成人');assert.equal(ctx['WF_YBX$1150-T.PASSENGER_SNO'],'TEST002');
   assert.equal(ctx['WF_YBX$395-T.INV_TYPE'],'INVOICE');assert.equal(ctx['WF_YBX$1150-T.PASSENGER_SCARD'],'original-id');
   if(mode==='airport')assert.equal(ctx['WF_YBX$1150-T.TOUR_ENDCITY'],'上海市-虹桥机场');
   if(mode==='timeout')return{done(){return this},fail(f){f({},'timeout');return this}};
   result='pass';
  }else throw Error('unexpected endpoint');
  return{done(f){this.f=f;return this},fail(){this.f(result);return this}};
 }};
 const ctx=vm.createContext({performance,crypto:webcrypto,TextEncoder,window:{jQuery:$},location:{origin:'https://cwzh.ustc.edu.cn',pathname:'/YBX/main.jsp'},
  reimbursementBusinessApi:async()=>({ok:true,complete:true,rows:[row],requests:[]})});
 try{
  const p=await vm.runInContext(source+'\nreimbursementTravelPrepare('+JSON.stringify(c)+')',ctx);
  assert.equal(calls.filter(x=>x.url==='commonUpdate_responseButtonEvent.action').length,0);
  if(!save)return p;
  if(mode==='drift')p.original['t.passenger_scard']='changed';
  const r=await vm.runInContext('reimbursementTravelSave('+JSON.stringify({...c,execute:true,pending_registered:true,expected_plan:p})+')',ctx);
  assert.equal(calls.filter(x=>x.url==='commonUpdate_responseButtonEvent.action').length,mode==='drift'?0:1);return r;
 }finally{assert.equal(JSON.stringify(uc),before);}
}
(async()=>{
 for(const m of ['other','bound','selected','incomplete','multi','seat','airport_duplicate','date_order'])await assert.rejects(()=>fixture(m));
 for(const m of ['ok','date','airport'])assert.equal((await fixture(m)).state,'saved_independent_readback_required');
 assert.equal((await fixture('drift')).state,'rejected_before_save');assert.equal((await fixture('timeout')).state,'unknown_save_readback_required');
 assert(!/\.click\s*\(|\.trigger\s*\(|dispatchEvent|eval\s*\(/.test(source));
 console.log('travel HTTP: single exact ticket/journey, protected fields, complete airport candidates, original intent and unknown-save gates passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
