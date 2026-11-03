'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),{webcrypto}=require('node:crypto');
const runtime=fs.readFileSync(path.join(__dirname,'../tools/finance_http.js'),'utf8');
const source=runtime+'\n'+fs.readFileSync(path.join(__dirname,'../tools/finance_print_api.js'),'utf8');
const fields=['t.serial_no','t.REQ_INFO','t.total_amt','t.abst','t.status','t.status_name','t.node_id'];
function fixture(options={}){
 const calls=[],data={'0-USERINFO.USERID':'TEST01'};
 const buttons=[
 {btnguid:'179CD38964434F7E89CEEFE72D6D3E35',check_func:'`$94b256ac92dda44bf3c48aefdac300bc:#101-t.serial_no#;#101-MultiRows#;#101-MultiCount#;#0-userinfo.userid#',procs:['`$d264de40319173e29c4db20af0188ea8:#101-t.serial_no#;#101-MultiRows#;#101-MultiCount#;#0-userinfo.userid#'],acts:''},
 {btnguid:'59372DA65E17470B96019B939A19CA88',check_func:'`$52fe7915fe470195e821dab7add0fd35:#101-t.serial_no#;#101-MultiCount#;#0-userinfo.userid#',procs:['`$5ea22eedd011091c5bd082bb1713e428:#101-t.serial_no#;#101-MultiRows#;#101-MultiCount#'],acts:'1==1:351'}
 ].map(b=>({...b,pre_check:true,post_check:true,call_proc:true}));
 if(options.drift)buttons[0].check_func='unverified';
 const list={wins:[{beforeLoadProcs:['`$bd8a1cd165467591385b30ac59ad3f99:#0-userinfo.userid#;'],buttons},{beforeLoadProcs:['`$1a2afdf516c239ff7602263a9a005c3a:#0-USERINFO.USERID#']}],funcs:[{funcno:'101',tablenames:'(@{ybx_query_pc_package.wf_query_main_list_v1:#99-t.serial_no#}',multifields:'t.serial_no',colModel:fields.map(name=>({name})),typeMap:{}}]};
 const print={wins:[{beforeLoadProcs:['`$ae59a69ccf76351707cc931841b7f33a:#61-serialno#;#0-userinfo.userid#;#0-dy_init_flag#']}],funcMap:{351:{url:'Customize/customPrint/bx_bxddy_frame.jsp'}}};
 const $={getProjID:()=> 'WF_YBX',UC:{userData:data,bindData:k=>data[k.slice(1,-1).toUpperCase()]||'',setData:(k,v)=>{data[k.toUpperCase()]=v;},appendDataType:()=>{}},page:{btn:{generateCmd:(check,procs)=>JSON.stringify({check,procs})}}};
 $.ajax=o=>{
  calls.push(o);let done,fail;
  queueMicrotask(()=>{
   if(options.unknown&&o.url==='ybxPrint_yb4PrintHtml.action'){fail({},'timeout');return;}
   let result;
   if(o.url.includes('loadDefinition'))result=o.data.winno==='70'?list:print;
   else if(o.url.startsWith('commonQuery')){
    assert.equal(o.data.queryJson,undefined);result={records:1,rows:[{cell:['123','TEST01-Synthetic',options.amount||'10.00','Fixture','10','待投递','164']}]};
   }else if(o.url==='commonUpdate_responseButtonEvent.action'){
    result=options.reject&&o.data.cmd.includes('94b256')?'error:该报销单正在审核中，请审核完成后打印':'pass;';
   }else if(o.url==='MainOperateAction_updateYbxMain.action')result={status:'success',msg:''};
   else if(o.url==='ybxPrint_yb4PrintHtml.action')result='fixture123&Fixture&.pdf';
   else throw Error('Unexpected endpoint '+o.url);
   done(result);
  });
  return{done:f=>{done=f;return{fail:f=>{fail=f;}};}};
 };
 const raw=Buffer.from('%PDF-synthetic-native-http-fixture');
 const context=vm.createContext({window:{jQuery:$},location:{origin:'https://cwzh.ustc.edu.cn',pathname:'/YBX/main.jsp',href:'https://cwzh.ustc.edu.cn/YBX/main.jsp'},performance,crypto:webcrypto,Uint8Array,URL,URLSearchParams,AbortSignal,btoa:s=>Buffer.from(s,'binary').toString('base64'),fetch:async url=>{assert.equal(url.pathname,'/YBX/fileSystem_getAction.action');assert.equal(url.searchParams.get('seq'),'fixture123');return{ok:true,arrayBuffer:async()=>raw};}});
 vm.runInContext(source,context);
 const config={case_id:'fixture',serial:'123',expected_userid:'TEST01',expected_amount:'10.00',expected_summary:'Fixture',include_invoice_attachments:false,session_token:'a'.repeat(32),max_bytes:100000,max_chunk:524288};
 return{calls,config,api:arg=>context.reimbursementPrintApi({...config,...arg}),context};
}
(async()=>{
 for(const include of [true,false]){
  const f=fixture();f.config.include_invoice_attachments=include;const first=await f.api({action:'begin'});
  assert.equal(first.final_print_verified,true);assert.equal(first.pages,null);assert.equal(first.business_transport,'native_http');
  assert.equal(f.calls.filter(c=>c.url==='MainOperateAction_updateYbxMain.action')[0].data.isPrint,include?'Y':'N');
  assert.equal(f.calls.filter(c=>c.url==='commonUpdate_responseButtonEvent.action').length,5);
  await assert.rejects(f.api({action:'begin'}),/PENDING/);
  const chunk=await f.api({action:'chunk',offset:0,length:first.size});assert.equal(chunk.sha256,first.sha256);
  await assert.rejects(f.api({action:'chunk',offset:0,length:first.size,include_invoice_attachments:!include}),/ORIGINAL_SESSION/);
  assert.equal((await f.api({action:'finish'})).transport_finished,true);
 }
 for(const options of [{drift:true}]){
  const f=fixture(options);await assert.rejects(f.api({action:'begin'}));assert.equal(f.calls.filter(c=>c.url==='MainOperateAction_updateYbxMain.action').length,0);
 }
 const grouped=fixture({amount:'7,683.78'});grouped.config.expected_amount='7683.78';assert.equal((await grouped.api({action:'begin'})).final_print_verified,true);
 const malformed=fixture({amount:'1,0.00'});assert.equal((await malformed.api({action:'begin'})).school_rejected,true);
 const mismatch=fixture({amount:'99.00'});assert.equal((await mismatch.api({action:'begin'})).rejection_code,'input_binding_mismatch');assert.equal(mismatch.calls.some(c=>c.url==='MainOperateAction_updateYbxMain.action'),false);
 const r=fixture({reject:true});assert.equal((await r.api({action:'begin'})).school_rejected,true);assert.equal(r.calls.some(c=>c.url==='ybxPrint_yb4PrintHtml.action'),false);
 const u=fixture({unknown:true});await assert.rejects(u.api({action:'begin'}),/UNKNOWN/);await assert.rejects(u.api({action:'begin'}),/PENDING/);assert.equal(u.calls.filter(c=>c.url==='ybxPrint_yb4PrintHtml.action').length,1);
 assert.equal(/\.click\(|dispatchEvent|\.runInstance\(|\.refresh\(|btnClickFunc|clickButton/.test(source),false);
 console.log('print API: fixed checks, independent content option, byte transport, drift/rejection/unknown without replay passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
