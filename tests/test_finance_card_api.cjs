const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),{webcrypto,createHash}=require('node:crypto');
const source=['finance_http.js','finance_native_protocol.js','finance_payments_api.js'].map(n=>fs.readFileSync(__dirname+'/../tools/'+n,'utf8')).join('\n');
const canonical=v=>Array.isArray(v)?v.map(canonical):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;
const digest=v=>createHash('sha256').update(JSON.stringify(canonical(v))).digest('hex');
async function fixture(mode='ok'){
  const names={19:['bx_amt','wt_amt','yt_amt','clr_amt'],22:['sno','sname','bankacnt','key','bankname','amount'],285:['gwk_banksname','gwk_bankacnt','gwk_date','gwk_pay_amt','amount','gwk_remark','gwk_seq_no','gwk_ord']};
  const save={btnguid:'save',check_func:'',procs:['`$'+'a'.repeat(32)+':#285-t.gwk_seq_no#;#285-t.amount#;#22-t.sno#;#19-t.yt_amt#;#61-serialno#']};
  const definition={wins:[{winno:13,func_btns:[save]}],funcs:Object.entries(names).map(([no,fields])=>({funcno:no,multiRec:no!=='19',formFields:fields.map(n=>({fieldname:'t.'+n,binding_data:no==='22'&&n==='sno'?'@$df52e671fa8782a8f989c8f7c934542e|#22-t.sno#,#0-userinfo.userid#,#61-serialno#':''}))})),funcMap:{},actMap:{}};
  const columns=['t.accname','t.account','t.pay_time','t.pay_addr','t.pay_amt','t.real_amount','t.auth_no','t.req_no','t.seq_no','t.selected'];
  const q={funcno:'793',colModel:columns.map(name=>({name}))};
  const grid={funcno:'793',multifields:'t.real_amount,t.seq_no',needcount:false,tablenames:'@{fixed-native-query}@ t'};
  const cardDef={wins:[{winno:'585',func_btns:[{caption:'保存',check_func:'',procs:['`$eec573bd273e86a1f3c7946333d8e84a:#793-MultiRows#;#793-MultiCount#;#19-t.wt_amt#;#61-serialno#;#0-userinfo.userid#']}]}],funcs:[grid],funcMap:{},actMap:{}};
  const transaction={'t.accname':'Synthetic Cardholder','t.account':'9900000000001234','t.pay_time':'2026-10-10','t.pay_addr':'Synthetic Merchant','t.pay_amt':'20.00','t.real_amount':'10.00','t.auth_no':'synthetic-auth','t.req_no':'123','t.seq_no':'synthetic-sequence','t.selected':'T'};
  const original={19:[{BX_AMT:'10.00',YT_AMT:'0.00',WT_AMT:'10.00',CLR_AMT:'0.00'}],22:[Object.fromEntries(names[22].map(n=>[n.toUpperCase(),'']))],
    285:[{GWK_BANKSNAME:'Synthetic Cardholder',GWK_BANKACNT:'9900000000001234',GWK_DATE:'2026-10-10',GWK_PAY_AMT:'20.00',AMOUNT:'0.00',GWK_REMARK:'original note',GWK_SEQ_NO:'synthetic-sequence',GWK_ORD:'original-order'}]};
  const p={mode:'corporate_card',person_id:'S001',name:'Synthetic Cardholder',amount:'10.00',transaction:{date:'2026-10-10',merchant:'Synthetic Merchant',search_amount:'20.00',card_suffix:'1234'}};
  const uc={'0-USERINFO.USERID':'TEST001','0-USERINFO.USERNAME':'Synthetic Operator','0-CARDCHECK_BXDLR':'T'},before=JSON.stringify(uc),sent=[];
  const $={getProjID:()=> 'WF_YBX',UC:{userData:uc,bindData:key=>key==='#0-GWKSELECT#'?(mode==='route'?'Y':'N'):'TEST001'},ajax:o=>({done(fn){this.fn=fn;return this;},fail(fn){
    sent.push(o);let reply;
    if(o.url==='loadDefinition.action?type=W')reply=o.data.winno==='584'?cardDef:definition;
    else if(o.url==='loadDefinition.action?type=Q')reply=[q];
    else if(o.url.startsWith('commonQuery_doQuery')){
      const ctx=JSON.parse(o.data.userData);assert.equal(ctx['WF_YBX$61-SERIALNO'],'123');assert.equal(ctx['WF_YBX$792-T.NO'],'S001');assert.equal(ctx['WF_YBX$792-T.PAY_AMT'],'20');
      const rows=[transaction];if(mode==='ambiguous')rows.push({...transaction,'t.seq_no':'other-sequence'});
      reply={records:rows.length,total:1,page:1,rows:rows.map(row=>({cell:columns.map(k=>row[k])}))};
      if(mode==='truncated')reply.records=2;
    }else{
      assert.equal(o.url,'commonUpdate_responseButtonEvent.action');const ctx=JSON.parse(o.data.userContext);
      if(mode.startsWith('new_')&&ctx['WF_YBX$793-MULTIROWS']){assert.equal(ctx['WF_YBX$793-MULTIROWS'],'10.00&synthetic-sequence');assert.equal(ctx['WF_YBX$793-MULTICOUNT'],'1');if(mode==='new_unknown'){fn({status:0},'timeout');return this;}original[285]=[{GWK_BANKSNAME:'Synthetic Cardholder',GWK_BANKACNT:'9900000000001234',GWK_DATE:'2026-10-10',GWK_PAY_AMT:'20.00',AMOUNT:'10.00',GWK_REMARK:'',GWK_SEQ_NO:'synthetic-sequence',GWK_ORD:'server-generated-order'}];original[19][0].YT_AMT='10.00';original[19][0].WT_AMT='0.00';this.fn('pass');return this;}
      assert.equal(ctx['WF_YBX$285-T.GWK_SEQ_NO'],'synthetic-sequence');assert.equal(ctx['WF_YBX$285-T.AMOUNT'],'10.00');assert.equal(ctx['WF_YBX$285-T.GWK_ORD'],'original-order');assert.equal(ctx['WF_YBX$285-T.GWK_REMARK'],'original note');assert.equal(ctx['WF_YBX$22-T.SNO'],'');
      if(mode==='unknown'){fn({status:0},'timeout');return this;}reply='pass';
    }
    this.fn(reply);return this;}})};
  const contract={winno:'584',definition_sha256:digest(cardDef),query_definition_sha256:digest(q),grid};
  const c={case_id:'fixture',serial:'123',expected_userid:'TEST001',expected_amount:'10.00',payments:[p],card_contract:contract,
    contracts:Object.keys(names).map(funcno=>({funcno})),payments_contract:{definition_sha256:digest(definition),save_button:'save'}};
  if(mode.startsWith('new_')){original[285]=[Object.fromEntries(names[285].map(n=>[n.toUpperCase(),n==='amount'?'0.00':'']))];transaction['t.req_no']='';transaction['t.selected']='F';if(mode==='new_foreign')transaction['t.req_no']='999';}
  if(mode==='missing_selection')original[285][0].GWK_SEQ_NO='other-sequence';
  if(mode==='wrong_bound_account')original[285][0].GWK_BANKACNT='9999999999991234';
  if(mode==='wrong_merchant')transaction['t.pay_addr']='Other Merchant';
  if(mode==='readonly')definition.funcs.find(f=>f.funcno==='285').formFields.find(f=>f.fieldname==='t.amount').readonly=true;
  const ctx=vm.createContext({performance,crypto:webcrypto,TextEncoder,window:{jQuery:$},location:{origin:'https://cwzh.ustc.edu.cn',pathname:'/YBX/main.jsp'},
    reimbursementBusinessApi:async()=>({bill:{'t.status':'-99','t.lc_finish':'F'},requests:[],sections:Object.entries(original).map(([funcno,rows])=>({funcno,kind:'form',rows}))})});
  vm.runInContext(source+';this.prepare=reimbursementPaymentsPrepare;this.save=reimbursementPaymentsSave;',ctx);
  try{
    const plan=await ctx.prepare(c);assert.equal(plan.card_bindings.length,1);assert(!sent.some(o=>o.url==='commonUpdate_responseButtonEvent.action'));
    const result=await ctx.save({...c,execute:true,expected_plan:plan});assert.equal(sent.filter(o=>o.url==='commonUpdate_responseButtonEvent.action').length,1);
    assert(!sent.some(o=>o.url==='bankCheck_BankCheckDtl.action'));return result;
  }finally{assert.equal(JSON.stringify(uc),before);}
}
(async()=>{
  assert.equal((await fixture()).state,'saved_independent_readback_required');assert.equal((await fixture('unknown')).state,'unknown_save_readback_required');
  assert.equal((await fixture('new_ok')).card_selection.state,'accepted');assert.equal((await fixture('new_unknown')).state,'unknown_save_readback_required');
  for(const mode of ['new_foreign','route','ambiguous','truncated','missing_selection','wrong_bound_account','wrong_merchant','readonly'])await assert.rejects(()=>fixture(mode));
  console.log('native card: exact complete transaction query, bound identity, partial amount, no UI and unknown write passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
