const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {webcrypto,createHash}=require('node:crypto');
const source=['finance_http.js','finance_native_protocol.js','finance_payments_api.js'].map(n=>fs.readFileSync(__dirname+'/../tools/'+n,'utf8')).join('\n');
const canonical=v=>Array.isArray(v)?v.map(canonical):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;
async function run(mode='ok',write=false){
  const fields={19:['bx_amt','wt_amt','yt_amt','clr_amt'],22:['sno','sname','depart','cnt1','bankacnt','bankname','key','amount','remark'],24:['banksname','bankacnt','bankname','amount','remark','taxpayer_ord','is_entrust_bj_laboratory']};
  const definition={wins:[{winno:13,func_btns:[{btnguid:'SAVE',pre_check:true,check_func:'',
    procs:['`$'+'a'.repeat(32)+':#19-t.bx_amt#;#19-t.yt_amt#;#19-t.wt_amt#;#22-t.sno#;#22-t.bankacnt#;#22-t.key#;#22-t.amount#;#24-t.banksname#;#61-bankcheck_id#;#61-serialno#;#0-userinfo.userid#']}]}],
    funcs:Object.entries(fields).map(([no,names])=>({funcno:no,multiRec:no!=='19',formFields:names.map(n=>({fieldname:'t.'+n,
      binding_data:no==='22'&&n==='sno'?'@$df52e671fa8782a8f989c8f7c934542e|#22-t.sno#,#0-userinfo.userid#,#61-serialno#':''}))})),funcMap:{},actMap:{}};
  const hash=createHash('sha256').update(JSON.stringify(canonical(definition))).digest('hex');
  const original={19:[{BX_AMT:'10.00',WT_AMT:'10.00',YT_AMT:'0.00',CLR_AMT:'0.00'}],22:[Object.fromEntries(fields[22].map(n=>[n.toUpperCase(),'']))],24:[Object.fromEntries(fields[24].map(n=>[n.toUpperCase(),'']))]};
  const pay=[{mode:'bank_transfer',person_id:'S001',name:'Student A',amount:'4.00',remark:'备注'},{mode:'bank_transfer',person_id:'S002',name:'Student B',amount:'6.00'}];
  const corporate=mode.startsWith('corporate'),mixed=mode.startsWith('mixed');
  if(mixed){pay.splice(1,1,...['A','B'].map((n,i)=>({mode:'corporate_transfer',name:'Company '+n,bank_name:'Branch '+n,bank_account:'12345678901'+i,amount:'3.00',expected_userid:'TEST001',source_refs:['synthetic-'+n]})));}
  if(mode==='mixed_bad_total')pay[1].amount='2.00';
  if(mode==='mixed_duplicate_account')pay[2].bank_account=pay[1].bank_account;
  if(mode==='mixed_foreign')Object.assign(original[24][0],{BANKSNAME:'Foreign',BANKACNT:'999999999999',AMOUNT:'1.00'});
  if(corporate){pay.splice(0,pay.length,{mode:'corporate_transfer',name:'Synthetic Company',bank_name:'Synthetic Branch',bank_account:'1234 5678 9012',amount:'10.00',expected_userid:'TEST001',source_refs:['synthetic-account-letter'],remark:'用途'});Object.assign(original[24][0],{BANKSNAME:'Synthetic Company',BANKACNT:'1234 5678 9012',BANKNAME:'123456789012/Synthetic Branch',TAXPAYER_ORD:'native-original-id'});}
  const uc={'0-USERINFO.USERID':'TEST001','0-USERINFO.USERNAME':'Operator','0-CARDCHECK_BXDLR':mode==='no_check'?'F':'T','WF_YBX$61-SERIALNO':'OTHER','WF_YBX$22-T.BANKACNT':'UNSAVED'};
  const before=JSON.stringify(uc),sent=[];
  const $={getProjID:()=> 'WF_YBX',UC:{userData:uc,bindData:()=> 'TEST001'},ajax:o=>{
    sent.push(o);let result=definition;
    if(o.url.startsWith('commonQuery_autoComplete_f')){
      assert.equal(o.data['#61-serialno#'],'123');assert.equal(o.data['#0-userinfo.userid#'],'TEST001');
      const p=pay.find(p=>p.person_id===o.data.q);
      result=p.person_id+'|$|'+p.name+'>'+p.person_id+'>12345678>Bank>Branch/Code>KEY'+p.person_id;
      if(mode==='empty_bank_label')result=result.replace('>Bank>','>>');
      if(mode==='ambiguous')result+='$|$'+result;
      if(mode==='wrong_name')result=result.replace(p.name,'Other');
      if(mode==='protocol')result='invalid';
    }else if(o.url==='bankCheck_BankCheckDtl.action'){
      const data=JSON.parse(o.data.checkinfo);assert.equal(data.length,mixed?1:2);assert.equal(data[0].check_name,'Student A');assert.equal(data[0].check_opt_unino,'TEST001');
      assert.equal(data[0].check_bankno_md5,'KEYS001');assert.equal(data[0].check_branchcode,'Branch');
      result=mode==='bank_rejected'?'rejected':'12345';
    }else if(o.url==='commonUpdate_responseButtonEvent.action'){
      const ctx=JSON.parse(o.data.userContext);assert.equal(ctx['WF_YBX$61-SERIALNO'],'123');
      if(mixed){assert.equal(ctx['WF_YBX$22-T.SNO'],'S001');assert.equal(ctx['WF_YBX$22-T.AMOUNT'],'4.00');assert.equal(ctx['WF_YBX$24-T.BANKSNAME'],'Company A$|Company B');assert.equal(ctx['WF_YBX$24-T.AMOUNT'],'3.00$|3.00');assert.equal(ctx['WF_YBX$24-T.TAXPAYER_ORD'],'resolved-0$|resolved-1');assert.equal(ctx['WF_YBX$24-MULTICOUNT'],'2');}
      else if(corporate){assert.equal(ctx['WF_YBX$24-T.BANKSNAME'],'Synthetic Company');assert.equal(ctx['WF_YBX$24-T.BANKACNT'],'123456789012');assert.equal(ctx['WF_YBX$24-T.AMOUNT'],'10.00');assert.equal(ctx['WF_YBX$24-T.TAXPAYER_ORD'],'native-original-id');assert.equal(ctx['WF_YBX$22-T.SNO'],'');assert.equal(ctx['WF_YBX$61-BANKCHECK_ID'],'');}
      else{assert.equal(ctx['WF_YBX$22-T.SNO'],'S001$|S002');assert.equal(ctx['WF_YBX$22-T.AMOUNT'],'4.00$|6.00');
      assert.equal(ctx['WF_YBX$22-T.KEY'],'KEYS001$|KEYS002');assert.equal(ctx['WF_YBX$19-T.YT_AMT'],'10.00');assert.equal(ctx['WF_YBX$19-T.WT_AMT'],'0.00');
      assert.equal(ctx['WF_YBX$61-BANKCHECK_ID'],mode==='no_check'?'':'12345');
      assert.equal(ctx['WF_YBX$24-T.BANKSNAME'],mode==='inactive_defaults'?'Invoice issuer':'');
      if(mode==='inactive_defaults')assert.equal(ctx['WF_YBX$24-T.AMOUNT'],'0.00');}
      if(mode==='timeout'||mode==='corporate_timeout')return{done(){return this;},fail(fn){fn({},'timeout');return this;}};
      result=mode==='judge'?'judge:confirm':mode==='error'?'error:reject':'pass';
    }
    return{done(fn){this.fn=fn;return this;},fail(){this.fn(result);return this;}};
  }};
  const c={case_id:'fixture',serial:'123',expected_userid:'TEST001',expected_amount:'10.00',payments:pay,
    payments_contract:{definition_sha256:hash,save_button:'SAVE'},contracts:Object.keys(fields).map(funcno=>({funcno}))};
  if(mode==='corporate_wrong_account')original[24][0].BANKACNT='999999999999';
  if(mode==='corporate_wrong_branch')original[24][0].BANKNAME='Other';
  if(mode==='corporate_changed_account_nonzero'){original[24][0].BANKACNT='999999999999';original[24][0].AMOUNT='1.00';}
  if(mode==='corporate_changed_account_no_taxpayer'){original[24][0].BANKACNT='999999999999';original[24][0].TAXPAYER_ORD='';}
  if(mode==='corporate_duplicate_name'){original[24][0].BANKACNT='999999999999';original[24].push({...original[24][0]});}
  if(mode==='corporate_existing_amount')original[24][0].AMOUNT='2.00';
  if(mode==='corporate_personal_present')original[22][0].SNO='OTHER';
  if(mode==='corporate_readonly')definition.funcs.find(f=>f.funcno==='24').formFields.find(f=>f.fieldname==='t.amount').readonly=true;
  if(mode==='definition')definition.wins[0].func_btns[0].pre_compute=true;
  if(mode==='other_payment')original[24][0].AMOUNT='1.00';
  if(mode==='foreign_recipient'){original[22][0].SNO='OTHER';original[22][0].AMOUNT='1.00';}
  if(mode==='inactive_defaults'){original[22][0].SNO='OTHER';original[22][0].SNAME='Other';original[22][0].AMOUNT='0.00';original[24][0].BANKSNAME='Invoice issuer';original[24][0].AMOUNT='0.00';}
  if(mode==='missing_field')delete original[22][0].KEY;
  if(mode==='remark')pay[0].remark='中'.repeat(13);
  if(mode==='wrong_mode')pay[0].mode='corporate_transfer';
  if(mode==='amount')pay[0].amount='5.00';
  if(mode==='clr')original[19][0].CLR_AMT='1.00';
  if(['noop','noop_native_blank'].includes(mode)){
    original[19][0].WT_AMT='0';original[19][0].YT_AMT='10';
    original[22]=pay.map(p=>({SNO:p.person_id,SNAME:p.name,DEPART:'',CNT1:'12345678',BANKACNT:'12345678',BANKNAME:'Branch/Code',KEY:'KEY'+p.person_id,AMOUNT:String(Number(p.amount)),REMARK:p.remark||''}));
    if(mode==='noop_native_blank'){pay[0].remark='';original[22][0].REMARK=' ';}
  }
  const context=vm.createContext({performance,crypto:webcrypto,TextEncoder,window:{jQuery:$},location:{origin:'https://cwzh.ustc.edu.cn',pathname:'/YBX/main.jsp'},
    reimbursementCorporateBank:async(c,p)=>({branchcode:'123456789012',branchname:p.bank_name,requests:[]}),
    reimbursementCorporateResolve:async(c,b,binding,before)=>{before();b.target['24'][binding.row_index].TAXPAYER_ORD='resolved-'+binding.row_index;return {...binding,state:'accepted',taxpayer_ord:'resolved-'+binding.row_index}},
    reimbursementBusinessApi:async()=>({bill:{'t.status':mode==='approved'?'10':'-99','t.lc_finish':mode==='approved'?'T':'F'},requests:[],
      sections:Object.entries(original).map(([funcno,rows])=>({funcno,kind:'form',rows}))})});
  try{
    const plan=await vm.runInContext(source+'\nreimbursementPaymentsPrepare('+JSON.stringify(c)+')',context);
    assert(!sent.some(x=>['bankCheck_BankCheckDtl.action','commonUpdate_responseButtonEvent.action'].includes(x.url)));
    if(!write)return plan;
    if(mode==='drift')plan.bank_bindings[0].key='WRONG';
    const result=await vm.runInContext('reimbursementPaymentsSave('+JSON.stringify({...c,execute:mode!=='no_intent',expected_plan:plan})+')',context);
    const saves=sent.filter(x=>x.url==='commonUpdate_responseButtonEvent.action');
    assert.equal(saves.length,['drift','no_intent','noop','noop_native_blank','bank_rejected'].includes(mode)?0:1);
    if(corporate)assert(!sent.some(x=>x.url==='bankCheck_BankCheckDtl.action'||x.url.startsWith('commonQuery_autoComplete')));
    if(mode==='no_check')assert(!sent.some(x=>x.url==='bankCheck_BankCheckDtl.action'));
    return result;
  }finally{assert.equal(JSON.stringify(uc),before);}
}
(async()=>{
  assert.equal((await run()).save_dispatched,false);
  const mixed=await run('mixed',true);assert.equal(mixed.state,'saved_independent_readback_required');assert.equal(mixed.corporate_resolution.length,2);
  for(const m of ['mixed_bad_total','mixed_duplicate_account','mixed_foreign'])await assert.rejects(()=>run(m));
  for(const mode of ['definition','other_payment','foreign_recipient','missing_field','remark','wrong_mode','amount','clr','ambiguous','wrong_name','protocol','approved'])await assert.rejects(()=>run(mode),mode);
  for(const mode of ['corporate_changed_account_nonzero','corporate_changed_account_no_taxpayer','corporate_duplicate_name','corporate_existing_amount','corporate_personal_present','corporate_readonly'])await assert.rejects(()=>run(mode));
  for(const mode of ['corporate_wrong_account','corporate_wrong_branch']){
    const result=await run(mode,true);assert.equal(result.state,'saved_independent_readback_required');
    assert.equal(result.corporate_bindings[0].resolve_required,false);assert.equal(result.corporate_resolution.length,0);
    assert.equal(result.corporate_bindings[0].taxpayer_ord,'native-original-id');
  }
  assert.equal((await run('corporate_timeout',true)).state,'unknown_save_readback_required');
  for(const mode of ['ok','no_check','corporate','inactive_defaults','empty_bank_label'])assert.equal((await run(mode,true)).state,'saved_independent_readback_required');
  for(const mode of ['timeout','judge','error','bank_rejected'])assert.equal((await run(mode,true)).state,'unknown_save_readback_required');
  for(const mode of ['drift','no_intent'])assert.equal((await run(mode,true)).state,'rejected_before_save');
  for(const mode of ['noop','noop_native_blank'])assert.equal((await run(mode,true)).state,'already_matches_readback_required');
  assert(!/\.click\s*\(|\.trigger\s*\(|dispatchEvent|eval\s*\(/.test(source));
  console.log('payments HTTP: unique native bank binding, multi-row wire, bank check, preserved other sections and unknown result passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
