'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),{webcrypto,createHash}=require('node:crypto');
const root=require('node:path').resolve(__dirname,'..');
const source=['finance_http.js','finance_native_protocol.js','finance_native_form.js','finance_medical_api.js'].map(n=>fs.readFileSync(root+'/tools/'+n,'utf8')).join('\n');
async function run(mode='ok',write=false){
 const guid='a'.repeat(32),main='b'.repeat(32),userid='TEST001',calls=[];
 const row={'t.tk_guid':guid,'t.tk_mainguid':main,'t.tk_type':'MEDICAL','t.tk_code':'12345678','t.tk_code_abst':'医/12345678/1234567890',
  't.tk_mod_flag':'NORMAL','t.canedit':'Y','t.serial_no':'','t.PC_Has_chosen':'N','t.auth_rst_desc':'正常(已认证)','t.tk_total_amt':'10.00'};
 const data={MEDICAL_INVCODE:'12345678',MEDICAL_INVNO:'1234567890',MEDICAL_INVCHECK:'original-check',MEDICAL_INVDATE:'2026-10-01',MEDICAL_HOSPITAL:'Synthetic Hospital',
  MEDICAL_PAYTYPE:'',MEDICAL_PAYOVERALL:'0.00',MEDICAL_PAYOTHER:'0',MEDICAL_PAYACCOUNT:'0',MEDICAL_PAYGRCASH:'0',MEDICAL_PAYCASH:'10.00',MEDICAL_INVAMOUNT:'10.00'};
 const bindSql='$'+'c'.repeat(32)+'|#61-tks_guid#,#0-userinfo.userid#,#61-picture_seq#,#395-T.INV_TYPE#';
 const definition={wins:[{winno:288,func_btns:[]},{winno:1159,func_btns:[{btnguid:'SAVE',check_func:'',procs:[]}]}],
  funcs:[{funcno:'1360',bindSql,typeMap:{},formFields:Object.keys(data).map(n=>({fieldname:'t.'+n.toLowerCase(),readonly:false,hidden:false}))}],funcMap:{},actMap:{}};
 const canon=v=>Array.isArray(v)?v.map(canon):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canon(v[k])])):v;
 const hash=createHash('sha256').update(JSON.stringify(canon(definition))).digest('hex');
 const uc={'0-USERINFO.USERID':userid,'0-IS_SKIP':'N'};const before=JSON.stringify(uc);
 const $={getProjID:()=> 'WF_YBX',UC:{userData:uc,bindData:r=>uc[r.slice(1,-1).toUpperCase()]},ajax:o=>{
  calls.push(o);let result;
  if(o.url==='loadDefinition.action?type=W')result=definition;
  else if(o.url==='common_bindSQLDataBackend.action'){assert.equal(o.data.cvstr,'#61-tks_guid#::'+guid+'$$#0-userinfo.userid#::'+userid+'$$#61-picture_seq#::$$#395-T.INV_TYPE#::MEDICAL');result=JSON.stringify([data]);}
  else if(o.url==='commonUpdate_responseButtonEvent.action'){
   const context=JSON.parse(o.data.userContext);assert.equal(context['WF_YBX$1360-T.MEDICAL_PAYTYPE'],'自费');
   for(const [k,v]of Object.entries(data))if(k!=='MEDICAL_PAYTYPE')assert.equal(context['WF_YBX$1360-T.'+k],v);
   if(mode==='timeout')return{done(){return this},fail(f){f({},'timeout');return this}};
   result=mode==='reject'?'error:rejected':'pass';
  }else throw Error('unexpected endpoint');
  return{done(f){this.f=f;return this},fail(){this.f(result);return this}};
 }};
 const c={case_id:'fixture',expected_userid:userid,pool_contracts:[],medical_contract:{winno:'288',save_winno:'1159',funcno:'1360',save_button:'SAVE',definition_sha256:hash,bindSql},
  edit:{schema:'ustc-invoice-medical-edit-v1',native_guid:guid,native_status:'NORMAL',invoice_code:'12345678',invoice_number:'1234567890',amount:'10.00',changes:{insurance_type:'自费'}}};
 if(mode==='bound')row['t.serial_no']='123';if(mode==='selected')row['t.PC_Has_chosen']='Y';if(mode==='wrong_invoice')data.MEDICAL_INVNO='9999999999';
 if(mode==='not_cash')data.MEDICAL_PAYCASH='8.00';if(mode==='insurance')data.MEDICAL_PAYTYPE='其他';if(mode==='other_payment')data.MEDICAL_PAYACCOUNT='1';
 if(mode==='skip')uc['0-IS_SKIP']='Y';if(mode==='definition')definition.wins[1].func_btns[0].pre_compute=true;
 if(mode==='noop')data.MEDICAL_PAYTYPE='自费';
 const frozen=JSON.stringify(uc),ctx=vm.createContext({performance,crypto:webcrypto,TextEncoder,window:{jQuery:$},location:{origin:'https://cwzh.ustc.edu.cn',pathname:'/YBX/main.jsp'},
  reimbursementBusinessApi:async()=>({ok:true,complete:mode!=='incomplete',rows:[row],requests:[]})});
 try{
  const prepared=await vm.runInContext(source+'\nreimbursementMedicalPrepare('+JSON.stringify(c)+')',ctx);
  assert.equal(calls.filter(x=>x.url==='commonUpdate_responseButtonEvent.action').length,0);
  if(!write)return prepared;
  if(mode==='drift')prepared.original['t.medical_invcheck']='changed';
  return await vm.runInContext('reimbursementMedicalSave('+JSON.stringify({...c,execute:true,pending_registered:true,expected_plan:prepared})+')',ctx);
 }finally{assert.equal(JSON.stringify(uc),frozen)}
}
(async()=>{
 for(const mode of ['bound','selected','wrong_invoice','not_cash','insurance','other_payment','skip','definition','incomplete'])await assert.rejects(()=>run(mode));
 assert.equal((await run('ok',true)).state,'saved_independent_readback_required');
 assert.equal((await run('noop',true)).save_dispatched,false);
 assert.equal((await run('drift',true)).state,'rejected_before_save');
 for(const mode of ['timeout','reject'])assert.equal((await run(mode,true)).state,'unknown_save_readback_required');
 assert(!/\.click\s*\(|\.trigger\s*\(|dispatchEvent|eval\s*\(/.test(source));
 console.log('medical HTTP: exact source identity, payment facts, one-field save, unchanged global context, and unknown recovery gate passed');
})().catch(e=>{console.error(e);process.exitCode=1});
