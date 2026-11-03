const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),{webcrypto}=require('node:crypto');
const code=['finance_http.js','finance_business_api.js','finance_native_protocol.js','finance_native_form.js','finance_notes_api.js'].map(n=>fs.readFileSync(path.join(__dirname,'../tools',n),'utf8')).join('\n');
const canonical=v=>Array.isArray(v)?v.map(canonical):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;
async function fixture(mode='ok') {
  const fields=[{fieldname:'t.spec_response',readonly:false,nullable:true,maxlen:'200'},
    {fieldname:'t.SPEC_FILE',readonly:false,nullable:true},{fieldname:'t.hidden',hidden:true,readonly:true,nullable:true}];
  const form={funcno:'76',bindSql:'$'+'3'.repeat(32)+'|#61-serialno#,#61-XTYPE#,#0-userinfo.userid#',formFields:fields,typeMap:{},formhtml:'<tr groupname="0"><td>#t.spec_response#</td><td>#t.SPEC_FILE#</td></tr>'};
  const def={wins:[{winno:54,beforeLoadProcs:['`$'+'1'.repeat(32)+':#61-XTYPE#;#61-serialno#;#0-userinfo.userid#'],
    func_btns:[{btnguid:'SAVE',procs:['`$'+'2'.repeat(32)+':#76-t.spec_response#;#76-t.SPEC_FILE#;#76-t.hidden#;#61-serialno#'],check_func:''}]}],funcs:[form],funcMap:{},actMap:{}};
  const hash=Buffer.from(await webcrypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(canonical(def))))).toString('hex');
  const billcols=['t.serial_no','t.REQ_INFO','t.status','t.status_name','t.total_amt','t.abst','t.lc_finish'];
  const data={'0-USERINFO.USERID':'TEST001','WF_YBX$61-SERIALNO':'OTHER'},sent=[];let saveCalls=0;
  const $={getProjID:()=> 'WF_YBX',UC:{bindData:()=> 'TEST001',userData:data},ajax:o=>{
    sent.push(o);let result;
    if(o.url.startsWith('loadDefinition'))result=o.data.winno==='70'?{wins:[],funcs:[{funcno:101,tablenames:'(@{ybx_query_pc_package.wf_query_main_list_v1:#99-year#})',multifields:'t.serial_no',colModel:billcols.map(name=>({name}))}]}:def;
    else if(o.url.startsWith('commonQuery'))result={records:1,page:1,total:1,rows:[{cell:['10001','TEST001-Test',mode==='approved'?'10':'-99','Draft','120.00','Test',mode==='approved'?'T':'F']}]};
    else if(o.url==='common_bindSQLDataBackend.action') {
      assert(o.data.cvstr.includes('#61-XTYPE#::'+(mode==='scoped'?'维修费&维修费':'33&33')));
      result=JSON.stringify([{SPEC_RESPONSE:mode==='drift'&&sent.length>5?'Changed':'Old',SPEC_FILE:'KEEP-ATTACHMENT',HIDDEN:'KEEP-HIDDEN',SPEC_TYPE:mode==='scoped'?'维修费':'33',SPEC_GUID:mode==='scoped'?'维修费':'33'}]);
    } else if(o.data.cmd.includes('2'.repeat(32))){saveCalls++;const ctx=JSON.parse(o.data.userContext);
      assert.equal(ctx['WF_YBX$76-T.SPEC_FILE'],'KEEP-ATTACHMENT');assert.equal(ctx['WF_YBX$76-T.HIDDEN'],'KEEP-HIDDEN');result='pass';
    }else result=mode==='bad_init'?'judge:requires user':'passlocal(61-SPEC_DES:0)';
    return {done(fn){this.fn=fn;return this;},fail(fn){if(mode==='timeout'&&saveCalls)fn({status:0},'timeout');else this.fn(result);return this;}};
  }};
  const ctx=vm.createContext({window:{jQuery:$},performance,Date,TextEncoder,crypto:webcrypto,location:{origin:'https://cwzh.ustc.edu.cn',pathname:'/YBX/main.jsp'}});vm.runInContext(code,ctx);
  const c={case_id:'fixture',serial:'10001',expected_userid:'TEST001',expected_amount:'120.00',
    contracts:[{winno:'54',funcno:'76',bindSql:'$'+'3'.repeat(32)+'|#61-serialno#,#61-XTYPE#,#0-userinfo.userid#'}],
    notes_contract:{winno:'54',funcno:'76',xtype:'33&33',initialization_keys:['61-SPEC_DES','61-SPEC_OPTIONS','61-write_isnull','61-upload_isnull','61-option_isnotnull'],save_button:'SAVE',definition_sha256:hash},
    explanations:{general:mode==='long'?'x'.repeat(201):'New',card_notes:[],record_only:mode==='record_only'}};
  if(mode==='scoped'){
    const read=await vm.runInContext('reimbursementBusinessApi('+JSON.stringify({...c,action:'bill-read',domain:'notes',notes_xtype:'维修费&维修费'})+')',ctx);
    assert.equal(read.notes_xtype,'维修费&维修费');assert.equal(read.sections[0].rows[0].SPEC_TYPE,'维修费');assert.equal(saveCalls,0);return read;
  }
  const prepare=await vm.runInContext('reimbursementNotesPrepare('+JSON.stringify(c)+')',ctx);
  const save=await vm.runInContext('reimbursementNotesSave('+JSON.stringify({...c,execute:true,expected_plan:Object.fromEntries(['original','changes','scope','definition_sha256'].map(k=>[k,prepare[k]]))})+')',ctx);
  assert.equal(data['WF_YBX$61-SERIALNO'],'OTHER');return{prepare,save,sent,saveCalls};
}
(async()=>{
  await fixture('scoped');
  const good=await fixture();assert.equal(good.saveCalls,1);assert(good.save.save_dispatched);assert.deepEqual(Object.keys(good.prepare.changes),['t.spec_response']);
  const skipped=await fixture('record_only');assert.equal(skipped.saveCalls,0);assert(skipped.save.requires_readback);
  for(const mode of ['approved','drift']){const r=await fixture(mode);assert.equal(r.saveCalls,0);assert.equal(r.save.state,'rejected_before_save');}
  const unknown=await fixture('timeout');assert.equal(unknown.saveCalls,1);assert.equal(unknown.save.state,'unknown_save_readback_required');
  for(const mode of ['long','bad_init'])await assert.rejects(()=>fixture(mode));
  assert(!/\.click\s*\(|\.trigger\s*\(|dispatchEvent|eval\s*\(/.test(code));
  console.log('notes API: pinned definition, original attachments, record-only, draft guard, drift, single save and unknown without retry passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
