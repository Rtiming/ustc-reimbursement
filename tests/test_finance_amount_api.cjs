const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),{webcrypto,createHash}=require('node:crypto');
const root=require('node:path').resolve(__dirname,'..');
const source=['finance_http.js','finance_native_protocol.js','finance_native_form.js','finance_amount_api.js'].map(f=>fs.readFileSync(root+'/tools/'+f,'utf8')).join('\n');
const canonical=v=>Array.isArray(v)?v.map(canonical):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;
const digest=v=>createHash('sha256').update(typeof v==='string'?v:JSON.stringify(canonical(v))).digest('hex');
async function fixture(mode='ok'){
 const state={persisted:'12.00',editor:'12.00',initializations:0,saves:0},guid='A'.repeat(32),userid='TEST001',serial='123';
 const open={btnguid:'OPEN',caption:'报销项详情',check_func:'',procs:['`$'+'a'.repeat(32)+':#61-xtype#;#61-serialno#;#0-userinfo.userid#']};
 const main={wins:[{winno:41,func_btns:[open]}],funcs:[],funcMap:{},actMap:{}};
 const fields=['t.t_desc','t.invoice_amt_xs','t.zf_zmt','t.t_amt','t.invoice_amt','t.old_amt','t.serial_no','t.tk_guid','t.ord'];
 const bindSql='$'+'b'.repeat(32)+'|#61-serialno#';
 const form={funcno:'390',multiRec:true,bindSql,formFields:fields.map(fieldname=>({fieldname})),typeMap:{}};
 const save={btnguid:'SAVE',check_func:'',procs:['`$'+'c'.repeat(32)+':#390-t.tk_guid#;#390-t.t_amt#;#390-t.ord#;#390-t.invoice_amt#;#61-serialno#;#0-userinfo.userid#']};
 const amount={wins:[{winno:282,func_btns:[save]}],funcs:[form],funcMap:{},actMap:{}};
 const data=()=>({T_DESC:'发票 '+ '9'.repeat(20),INVOICE_AMT_XS:'12.00',ZF_ZMT:'0.00',T_AMT:state.editor,INVOICE_AMT:'12.00',OLD_AMT:state.editor,SERIAL_NO:mode==='wrong_serial'?'999':serial,TK_GUID:guid,ORD:'1'});
 const entry=()=>({textContent:mode==='whole_bill'?String(Number(state.persisted)-4):state.persisted,getAttribute:name=>name==='title'?'可点击修改报销金额，修改的金额不能大于发票金额':mode==='handler'?"unknown('报销项详情')":"pageOpen_v1('报销项详情','测试组&30201&测试组!P!x')"});
 const doc={querySelectorAll:()=>mode==='whole_bill'?[entry(),{textContent:'4.00',getAttribute:name=>name==='title'?'可点击修改报销金额，修改的金额不能大于发票金额':"pageOpen_v1('报销项详情','另一组&3021801&另一组!P!x')"}]:[entry()]};
 const uc={'0-USERINFO.USERID':userid,'0-SHOW_ZFJE':mode==='formula'?'Y':'N'},original=JSON.stringify(uc);
 const $={getProjID:()=> 'WF_YBX',UC:{userData:uc,bindData:()=>userid},ajax:o=>{
  let result;
  if(o.url==='loadDefinition.action?type=W')result=o.data.winno==='41'?main:amount;
  else if(o.url==='redirectCustomPage.action')result=o.data.url.endsWith('bx_dtl.jsp')?'inner':'shell';
  else if(o.url==='ybxPrint_yb4PrintHtml.action')result='complete document';
  else if(o.url==='common_bindSQLDataBackend.action'){assert.equal(o.data.cvstr,'#61-serialno#::123');result=JSON.stringify(mode==='duplicate'?[data(),data()]:[data()]);}
  else if(o.url==='commonUpdate_responseButtonEvent.action'){
   const context=JSON.parse(o.data.userContext);assert.equal(context['WF_YBX$61-SERIALNO'],serial);
   if(o.data.cmd.includes('`$'+'a'.repeat(32))){state.initializations++;state.editor=state.persisted;result='pass';}
   else{
    state.saves++;assert.equal(context['WF_YBX$390-T.TK_GUID'],guid);assert.equal(context['WF_YBX$390-T.T_AMT'],'10.00');assert.equal(context['WF_YBX$390-T.INVOICE_AMT'],'12.00');
    state.persisted='10.00';
    if(mode==='unknown')return{done(){return this},fail(f){f({},'timeout');return this}};
    result=mode==='missing_counter'?'pass':'passlocal(61-tksbcodecnt:'+(mode==='allocation'?'2':'0')+')';
   }
  }else throw Error('unexpected endpoint:'+o.url);
  return{done(f){this.f=f;return this},fail(){this.f(result);return this}};
 }};
 const c={case_id:'fixture',serial,expected_userid:userid,group:'测试组',subject:'30201',expected_final_amount:'10.00',execute:true,pending_registered:true,
  basic_contracts:[],submission_contract:{shell_sha256:digest('shell'),inner_sha256:digest('inner')},
  amount_contract:{winno:'282',funcno:'390',save_button:'SAVE',bindSql,definition_sha256:digest(amount),main_definition_sha256:digest(main),open_button:open}};
 const ctx=vm.createContext({performance,crypto:webcrypto,TextEncoder,window:{jQuery:$},location:{origin:'https://cwzh.ustc.edu.cn',pathname:'/YBX/main.jsp'},DOMParser:class{parseFromString(){return doc}},
  reimbursementSubmissionInventory:()=>({print:'N',proofs:[]}),reimbursementBusinessApi:async config=>config.action==='bills-list'?{complete:true,rows:[{'t.serial_no':serial,'t.status':mode==='workflow'?'0':'-99','t.lc_finish':'F','t.total_amt':state.persisted}],requests:[]}:config.domain==='used_invoices'?{sections:[{funcno:'533',complete:true,rows:[{'t.serial_no':serial,'t.tk_guid':guid,'t.tk_total_amt':'12.00','t.tk_code_abst':'9'.repeat(20)}]}],requests:[]}:{sections:[{funcno:'60',rows:[{ABST:'unchanged'}]}],requests:[]}});
 vm.runInContext(source,ctx);
 const call=(method,config)=>vm.runInContext(method+'('+JSON.stringify(config)+')',ctx);
 const inspect=extra=>call('reimbursementAmountInspect',{...c,...extra});
 const start=async()=>{const p=await inspect();const opened=await call('reimbursementAmountInitialize',{...c,expected_plan:p});assert(opened.ok);return inspect({initialization_verified:true});};
 const reduce=async()=>{
  const p=await start(),r=p.rows[0],reduction={schema:'ustc-native-invoice-reduction-preparation-v1',current_total:'12.00',claim_total:'10.00',rows:[{invoice_guid:guid,ord:'1',native_fields:r,current_amount:'12.00',face_amount:'12.00',claim_amount:'10.00'}]};
  if(mode==='drift')reduction.rows[0].ord='2';
  const result=await call('reimbursementAmountSave',{...c,initialization_verified:true,expected_plan:p,reduction});assert.equal(JSON.stringify(uc),original);return result;
 };
 return{state,inspect,start,reduce,call,c,guid};
}
(async()=>{
 const good=await fixture(),read=await good.inspect();assert.equal(read.amount_rows_complete,false);const result=await good.reduce();
 assert.equal(result.state,'saved_independent_readback_required');assert.equal(good.state.saves,1);assert.equal(good.state.initializations,2);
 assert.equal((await good.inspect({initialization_verified:true})).rows[0]['t.t_amt'],'10.00');
 const whole=await fixture('whole_bill');const wholeSaved=await whole.reduce();assert.equal(wholeSaved.ok,true);assert.equal(whole.state.persisted,'10.00');assert.equal(whole.state.saves,1);assert.equal(whole.state.initializations,2);
 const recovery=await fixture('whole_bill'),before=await recovery.start();recovery.state.persisted='10.00';
 const recovered=await recovery.call('reimbursementAmountRecoverPersisted',{...recovery.c,action:'recover',execute:false,persistence_recovery:{fixture:true},expected_plan:before,reduction:{change_total:'2.00'}});
 assert.equal(recovered.rows[0]['t.t_amt'],'10.00');assert.equal(recovery.state.saves,0);assert.equal(recovery.state.initializations,2);assert.equal(recovered.persistence_recovery.monetary_save_replayed,false);
 const branch=await fixture('allocation');assert.equal((await branch.reduce()).branch.count,2);assert.equal(branch.state.initializations,1);
 for(const mode of ['missing_counter','unknown']){const f=await fixture(mode),r=await f.reduce();assert.equal(r.ok,false);assert(r.save_dispatched);assert.equal(f.state.saves,1);}
 const drift=await fixture('drift');assert.equal((await drift.reduce()).save_dispatched,false);assert.equal(drift.state.saves,0);
 for(const mode of ['formula','workflow','handler','wrong_serial','duplicate']){const f=await fixture(mode);await assert.rejects(()=>f.start());assert.equal(f.state.saves,0);}
 assert(!/\.click\s*\(|\.trigger\s*\(|dispatchEvent|eval\s*\(/.test(source));
 console.log('amount HTTP: exact group, native init, complete rows, one reduction, dynamic branch, persistence refresh and unknown stop passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
