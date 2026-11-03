const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {createHash,webcrypto}=require('node:crypto');
const source=['finance_http.js','finance_native_protocol.js','finance_native_form.js','finance_retry_api.js'].map(n=>fs.readFileSync(path.join(__dirname,'../tools',n),'utf8')).join('\n');
const sha=v=>createHash('sha256').update(v).digest('hex');
function canonical(v){return Array.isArray(v)?v.map(canonical):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;}
function fixture(grid='113',mode='ok'){
 let writes=0,ocrWrites=0,guards=0;
 const reidentify=mode.startsWith('ocr');
 const ids=['a'.repeat(32),'b'.repeat(32)],sourceSha=['1'.repeat(64),'2'.repeat(64)];
 const rows=ids.map((id,i)=>grid==='113'?{'t.filesequcode':id,'t.show_filename':sourceSha[i]+'.pdf','t.show_err_msg':'图像无法识别'}:
   {'t.tk_guid':id,'t.tk_mod_flag':'ERROR','t.serial_no':'','t.PC_Has_chosen':'N'});
 const button={caption:'批量删除',clickquery:true,clickqueryhint:'确认删除',acts:'',customFunc:'',jscontent:'',check_func:'',
  procs:['`$'+'1'.repeat(32)+':#'+grid+'-MultiRows#;#'+grid+'-MultiCount#;#0-userinfo.username#;#0-userinfo.userid#']};
 const definition={wins:[{winno:grid==='53'?'39':'79',func_btns:[button]}],funcs:[],funcMap:{},actMap:{}};
 const decoder=function(){return 'native semicolon decoder';};
 const contract={multirows_decoder_sha256:sha(String(decoder)),grids:{[grid]:{winno:grid==='53'?'33':'79',host_winno:grid==='53'?'39':'79',
    definition_sha256:sha(JSON.stringify(canonical(definition))),cleanup_button:button}}};
 const plan={business_transport:'native_http',case_id:'fixture',ready:true,action:'cleanup',request:{expected_userid:'TEST001',grid},button_definition:button,
  targets:rows.map((r,i)=>({native_key:grid==='53'?'tk_guid':'filesequcode',native_value:ids[i],native_row:structuredClone(r),source_sha256:sourceSha[i]})),protected_native_ids:[]};
 const callbacks={reidentification:function(){},invoiceHandle:function(){},ocr_hh:function(){},ocr_hh_succCallBack:function(){},callInterface:function(){}};
 if(reidentify){
   const reid={caption:'重新识别',pre_check:true,clickquery:false,check_func:'`$'+'2'.repeat(32)+':#113-MultiRows#;#113-MultiCount#;#0-userinfo.userid#',procs:[]};
   definition.wins[0].func_btns.push(reid);contract.grids[grid].reidentify_button=reid;
   contract.grids[grid].definition_sha256=sha(JSON.stringify(canonical(definition)));
   contract.ocr_callbacks=Object.fromEntries(Object.entries(callbacks).map(([n,f])=>[n,sha(String(f))]));
   plan.action='reidentify';plan.button_definition=reid;plan.targets=plan.targets.slice(0,1);plan.protected_native_ids=[ids[1]];
   if(mode==='ocr-name'){rows[0]['t.show_filename']='original.pdf';plan.targets[0].native_row=structuredClone(rows[0]);plan.targets[0].native_filename='original.pdf';}
 }
 const data={'0-USERINFO.USERID':'TEST001','0-USERINFO.USERNAME':'合成人','WF_YBX$53-MULTIROWS':'original-selection'};
 const $={getProjID:()=> 'WF_YBX',Q:{loadMultiRows:decoder},UC:{userData:data,bindData:()=> 'TEST001'},ajax:o=>({done(fn){this.fn=fn;return this;},fail(fn){
   if(o.url.startsWith('loadDefinition')){const d=structuredClone(definition);if(mode==='definition')d.changed='not-projected';if(mode==='definition')d.wins[0].changed=true;this.fn(d);}
   else if(o.url==='thirdPartyInterface.action'){
     ocrWrites++;assert.equal(o.data.interfaceName,'OCR_cutRecognition_HH');assert.equal(o.data.projId,'WF_YBX');
     assert.equal(decodeURIComponent(o.data.arguments),'jsonData|'+ids[0]+'!P!WF_YBX');
     if(mode==='ocr-lost')fn({status:0},'timeout');else this.fn(JSON.stringify({stateCode:'SUCCESS',data:{status:'pass',seq:ids[0]}}));
   }
   else if(reidentify){
     guards++;assert.equal(o.url,'commonUpdate_responseButtonEvent.action');
     const ctx=JSON.parse(o.data.userContext);assert.equal(ctx['WF_YBX$113-MULTIROWS'],ids[0]);
     this.fn(mode==='ocr-name'?'judge:您确定要重新识别序号为[1],图片名称为[original.pdf ]的未识别图片吗？':'judge:您确定要重新识别['+(mode==='ocr-other'?'f'.repeat(64):sourceSha[0])+'.pdf]吗？');
   }
   else {assert.equal(o.url,'commonUpdate_responseButtonEvent.action');writes++;
     const context=JSON.parse(o.data.userContext);assert.equal(context['WF_YBX$'+grid+'-MULTIROWS'],ids.join(';'));assert.equal(context['WF_YBX$'+grid+'-MULTICOUNT'],'2');
     assert(o.data.cmd.includes('value:"'+ids.join(';')+'"'));assert(o.data.cmd.includes('wfAct:""'));
     if(mode==='lost')fn({status:0},'timeout');else this.fn('pass');
   }return this;}})};
 $.I={callInterface:callbacks.callInterface};
 const ctx=vm.createContext({window:{jQuery:$,$,...callbacks},crypto:webcrypto,TextEncoder,Date,performance,location:{origin:'https://cwzh.ustc.edu.cn',pathname:'/YBX/main.jsp'},
  reimbursementBusinessApi:async()=>({ok:true,complete:true,requests:[],grids:[{funcno:'53',complete:true,records:grid==='53'?rows.length:0,rows:grid==='53'?rows:[]},
    {funcno:'113',complete:true,records:grid==='113'?rows.length:0,rows:grid==='113'?rows:[]}]}),document:new Proxy({},{get(){throw Error('no DOM access');}})});
 vm.runInContext(source,ctx);
 const base={expected_userid:'TEST001',case_id:'fixture',grid,retry_contract:contract,inventory_contracts:{},request_id:'request',plan_digest:'c'.repeat(64),plan};
 return {call:(action,extra={})=>vm.runInContext('reimbursementRetryApi('+JSON.stringify({...base,action,...extra})+')',ctx),rows,data,get writes(){return writes;},get ocrWrites(){return ocrWrites;},get guards(){return guards;}};
}
(async()=>{
 for(const grid of ['53','113']){
  const f=fixture(grid),inspect=await f.call('inspect');assert.equal(inspect.grid.allRecordsVisible,false);assert.equal(inspect.grid.complete,true);assert.equal(inspect.display_evidence,false);
  const armed=await f.call('cleanup');assert.equal(armed.system_write,false);assert.equal(f.writes,0);
  await assert.rejects(()=>f.call('confirm'),/PENDING_REQUIRED/);assert.equal(f.writes,0);
  const done=await f.call('confirm',{execute:true,pending_registered:true});assert.equal(done.marker.phase,'confirm_http_received');assert.equal(f.writes,1);
  await assert.rejects(()=>f.call('confirm',{execute:true,pending_registered:true}),/ORIGINAL_CONFIRMATION/);assert.equal(f.writes,1);
  assert.equal(f.data['WF_YBX$53-MULTIROWS'],'original-selection');
 }
 const lost=fixture('113','lost');await lost.call('cleanup');await assert.rejects(()=>lost.call('confirm',{execute:true,pending_registered:true}),/REQUEST_UNKNOWN/);
 await assert.rejects(()=>lost.call('confirm',{execute:true,pending_registered:true}),/ORIGINAL_CONFIRMATION/);assert.equal(lost.writes,1);
 const changed=fixture();await changed.call('cleanup');changed.rows[0]['t.show_err_msg']='changed';await assert.rejects(()=>changed.call('confirm',{execute:true,pending_registered:true}),/TARGET_CHANGED/);assert.equal(changed.writes,0);
 const protectedRow=fixture('53');protectedRow.rows[0]['t.serial_no']='12345';await assert.rejects(()=>protectedRow.call('cleanup'));assert.equal(protectedRow.writes,0);
 const drift=fixture('113','definition');await assert.rejects(()=>drift.call('inspect'),/DEFINITION_CHANGED/);assert.equal(drift.writes,0);
 const ocr=fixture('113','ocr-ok');assert((await ocr.call('inspect')).native_ocr_contract_verified);
 await ocr.call('reidentify');assert.equal(ocr.guards,1);assert.equal(ocr.ocrWrites,0);
 const result=await ocr.call('confirm',{execute:true,pending_registered:true});assert.equal(result.server_accepted,true);assert.equal(result.ocr_status,'pass');assert.equal(result.recognized,false);assert.equal(ocr.ocrWrites,1);
 await assert.rejects(()=>ocr.call('confirm',{execute:true,pending_registered:true}),/ORIGINAL_CONFIRMATION/);assert.equal(ocr.ocrWrites,1);
 const originalName=fixture('113','ocr-name');await originalName.call('reidentify');await originalName.call('confirm',{execute:true,pending_registered:true});assert.equal(originalName.ocrWrites,1);
 const wrongSource=fixture('113','ocr-other');await assert.rejects(()=>wrongSource.call('reidentify'),/CONFIRMATION_SOURCE_MISMATCH/);assert.equal(wrongSource.ocrWrites,0);
 const lostOcr=fixture('113','ocr-lost');await lostOcr.call('reidentify');await assert.rejects(()=>lostOcr.call('confirm',{execute:true,pending_registered:true}),/REQUEST_UNKNOWN/);
 await assert.rejects(()=>lostOcr.call('confirm',{execute:true,pending_registered:true}),/ORIGINAL_CONFIRMATION/);assert.equal(lostOcr.ocrWrites,1);assert.equal(lostOcr.guards,1);
 assert(!/\.click\s*\(|\.trigger\s*\(|dispatchEvent/.test(source));
 console.log('retry HTTP: full inventory, no DOM/selection changes, native semicolon binding, protected records, confirm once and unknown no-replay passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
