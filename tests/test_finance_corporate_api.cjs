'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),{createHash,webcrypto}=require('node:crypto');
const root=require('node:path').resolve(__dirname,'..');
const source=['finance_http.js','finance_native_protocol.js','finance_corporate_api.js','finance_payments_api.js'].map(n=>fs.readFileSync(root+'/tools/'+n,'utf8')).join('\n');
const canon=v=>Array.isArray(v)?v.map(canon):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canon(v[k])])):v;
const hash=v=>createHash('sha256').update(JSON.stringify(canon(v))).digest('hex');
async function fixture(mode='ok'){
 const names={19:['bx_amt','yt_amt','wt_amt','clr_amt'],22:['sno','sname','cnt1','bankacnt','bankname','key','amount','remark'],24:['banksname','bankacnt','bankname','amount','remark','taxpayer_ord','is_entrust_bj_laboratory']};
 const event='$c7ec1501d96a56ef126b1d23a4e20661|#24-t.banksname#,#61-serialno#,#0-userinfo.userid#';
 const d={wins:[{winno:'13',func_btns:[{btnguid:'SAVE',check_func:'',procs:[]}]}],funcs:Object.entries(names).map(([n,list])=>({funcno:n,multiRec:n!=='19',formFields:list.map(name=>({fieldname:'t.'+name,readonly:false,hidden:false,
   binding_data:n==='22'&&name==='sno'?'@$df52e671fa8782a8f989c8f7c934542e|#22-t.sno#,#0-userinfo.userid#,#61-serialno#':'',
   Events:n==='24'&&name==='banksname'?[{event:'change',proc:event,callBack:'after_transfer_taxord(idx);'}]:[]}))})),funcMap:{},actMap:{}};
 const bank={wins:[],funcs:[{funcno:80,tablenames:'fixed-bank-directory'}],funcMap:{},actMap:{}},q={funcno:80,needcount:true,colModel:[{name:'t.branchcode'},{name:'t.branchname'}]};
 const original={19:[{BX_AMT:'10.00',YT_AMT:'0.00',WT_AMT:'10.00',CLR_AMT:'0.00'}],22:[Object.fromEntries(names[22].map(k=>[k.toUpperCase(),k==='amount'?'0.00':'']))],24:[Object.fromEntries(names[24].map(k=>[k.toUpperCase(),k==='amount'?'0.00':'']))]};
 const p={mode:'corporate_transfer',expected_userid:'TEST001',name:'Synthetic Company',bank_account:'123456789012',bank_name:mode==='slash'?'/Synthetic Branch':'Synthetic Branch',amount:'10.00',source_refs:['synthetic approved invoice']};
 const c={case_id:'fixture',serial:'123',expected_userid:'TEST001',expected_amount:'10.00',payments:[p],contracts:Object.keys(names).map(funcno=>({funcno})),payments_contract:{definition_sha256:hash(d),save_button:'SAVE'},
  corporate_contract:{window:'56',definition_sha256:hash(bank),query_sha256:hash(q),bank_query:{tablenames:'fixed-bank-directory'},taxpayer_proc:event.split('|')[0],taxpayer_event:event}};
 const uc={'0-SHOW_BRANCHNAME':mode==='up'?'up':'','0-USERINFO.USERID':'TEST001','0-USERINFO.USERNAME':'Synthetic Operator','0-CARDCHECK_BXDLR':'F'},before=JSON.stringify(uc),sent=[];
 const $={getProjID:()=> 'WF_YBX',UC:{userData:uc,bindData:r=>uc[r.slice(1,-1).toUpperCase()]},ajax:o=>({done(f){this.f=f;return this},fail(f){
  sent.push(o);let result;
  if(o.url==='loadDefinition.action?type=W')result=o.data.winno==='13'?d:bank;
  else if(o.url==='loadDefinition.action?type=Q')result=[q];
  else if(o.url.startsWith('commonQuery_doQuery.action?funcno=80')){
    const data=JSON.parse(o.data.userData);assert.equal(data[mode==='up'?'WF_YBX$82-T.BRANCHNAME1':'WF_YBX$82-T.BRANCHNAME'],'Synthetic Branch');
    result={records:mode==='duplicate'?2:1,total:1,page:1,rows:[{cell:['123456789012','Synthetic Branch']}]};if(mode==='duplicate')result.rows.push({cell:['987654321098','Synthetic Branch']});
    if(mode==='incomplete')result.records=2;if(mode==='empty')result=[];if(mode==='empty_object')result={records:0,total:0,page:0,rows:[]};
  }else if(o.url==='common_callProc.action'){
    const data=JSON.parse(o.data.userData);assert.equal(o.data.idx,'0');assert.equal(data['0-MULTI_FORM_INDEX'],'0');assert.equal(data['WF_YBX$24-T.BANKSNAME'],p.name);assert.equal(data['WF_YBX$24-T.BANKACNT'],p.bank_account);assert.equal(o.data.procName,event.split('|')[0]);
    if(mode==='unknown'){f({},'timeout');return this;}result=mode==='no_identity'?'pass':'passlocal(0-taxpayer_ord:server-taxpayer-id)';
  }else if(o.url==='commonUpdate_responseButtonEvent.action'){
    const data=JSON.parse(o.data.userContext);assert.equal(data['WF_YBX$24-T.TAXPAYER_ORD'],'server-taxpayer-id');assert.equal(data['WF_YBX$24-T.BANKNAME'],'123456789012/Synthetic Branch');assert.equal(data['WF_YBX$24-T.AMOUNT'],'10.00');result='pass';
  }else throw Error('unexpected endpoint');
  this.f(result);return this;
 }})};
 const ctx=vm.createContext({performance,crypto:webcrypto,TextEncoder,window:{jQuery:$},location:{origin:'https://cwzh.ustc.edu.cn',pathname:'/YBX/main.jsp'},
  reimbursementBusinessApi:async()=>({bill:{'t.status':'-99','t.lc_finish':'F'},requests:[],sections:Object.entries(original).map(([funcno,rows])=>({funcno,kind:'form',rows}))})});
 vm.runInContext(source+';this.prepare=reimbursementPaymentsPrepare;this.save=reimbursementPaymentsSave;',ctx);
 try{
  const plan=await ctx.prepare(c);assert.equal(plan.corporate_bindings.length,1);assert(!sent.some(r=>r.url==='common_callProc.action'));
  const saved=await ctx.save({...c,execute:true,expected_plan:plan});assert.equal(sent.filter(r=>r.url==='common_callProc.action').length,1);
  assert.equal(sent.filter(r=>r.url==='commonUpdate_responseButtonEvent.action').length,['unknown','no_identity'].includes(mode)?0:1);return saved;
 }finally{assert.equal(JSON.stringify(uc),before)}
}
(async()=>{
 for(const mode of ['ok','up','slash'])assert.equal((await fixture(mode)).corporate_resolution[0].taxpayer_ord,'server-taxpayer-id');
 for(const mode of ['duplicate','incomplete','empty','empty_object'])await assert.rejects(()=>fixture(mode));
 for(const mode of ['unknown','no_identity'])assert.equal((await fixture(mode)).state,'unknown_save_readback_required');
 assert(!/\.click\s*\(|\.trigger\s*\(|dispatchEvent|eval\s*\(/.test(source));
 console.log('new corporate identity: exact complete bank directory, native zero-based field procedure, preserved fields and unknown stop passed');
})().catch(e=>{console.error(e);process.exitCode=1});
