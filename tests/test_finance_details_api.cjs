const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {webcrypto,createHash}=require('node:crypto');
const source=['finance_http.js','finance_native_protocol.js','finance_native_form.js','finance_details_api.js'].map(n=>fs.readFileSync(path.join(__dirname,'../tools',n),'utf8')).join('\n');
const digest=b=>createHash('sha256').update(b).digest('hex');
function canonical(v){return Array.isArray(v)?v.map(canonical):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;}
async function fixture(mode='ok') {
  let generated=0,downloaded=0,checks=0;
  const check='`$befac77e186c7e6ab77274901c30f0dc:#101-MultiRows#;#101-MultiCount#';
  const init='`$663f836de1606b5bebc5b97c75ca1c6e:#61-serialno#;#0-status_name_dtl#;#0-userinfo.userid#;#0-dy_init_flag#;#61-node_id#';
  const list={wins:[{winno:71,func_btns:[{btnguid:'button',check_func:check,procs:[],acts:'1==1:69'}]}],funcs:[],funcMap:{},actMap:{}};
  const details={wins:[{winno:69,beforeLoadProcs:[init]}],funcs:[],funcMap:{'69':{url:'Customize/customPrint/bx_pdf_mx.jsp'}},actMap:{}};
  const outer='<script>var printType="打印历史";</script>',shellCode='const fixedNativePdfShell=true;';
  const callbacks={before_apply_list_dtl:'function before_apply_list_dtl() { return true; }',after_apply_list_dtl:'function after_apply_list_dtl() { return true; }'};
  const callbackSource=Object.values(callbacks).join('\n');
  const keys=['0-wfname','0-uni_key','0-basetable_key','0-project_id','0-node_id','0-shcj_hide_return','0-is_show_ysppdf','0-ysp_special_html','0-init_ckmx_btn_info'];
  const contract={list_definition_sha256:digest(JSON.stringify(canonical(list))),details_definition_sha256:digest(JSON.stringify(canonical(details))),
    outer_template_sha256:digest(outer),shell_inline_scripts_sha256:digest(shellCode),callbacks:Object.fromEntries(Object.entries(callbacks).map(([k,v])=>[k,digest(v)])),
    initialization_keys:keys,list_button:'button'};
  const bytes=Buffer.concat([Buffer.from('%PDF-'),Buffer.alloc(507)]);
  const data={'0-USERINFO.USERID':'TEST001','0-PDFPRINT_CONTROL':'N','0-PDFPRINT_CONTROL_CKMX':mode==='disabled'?'Y':'N','WF_YBX$61-SERIALNO':'OTHER'};
  const $={getProjID:()=> 'WF_YBX',UC:{bindData:()=> 'TEST001',userData:data},ajax:o=>({done(fn){this.fn=fn;return this;},fail(fn){
    let result;
    if(o.url.startsWith('loadDefinition'))result=structuredClone(o.data.winno==='70'?list:details);
    else if(o.url==='redirectCustomPage.action')result=o.data.url.includes('bx_pdf_mx')?(mode==='template_drift'?outer+'changed':outer):
      '<div id="sys_abc">'+(mode==='server_identity'?'OTHER':'TEST001')+'</div><script>'+shellCode+'</script>';
    else if(o.url==='commonUpdate_responseButtonEvent.action'){
      checks++;const context=JSON.parse(o.data.userContext);assert.equal(context['WF_YBX$61-SERIALNO'],'10001');
      if(checks%2===1)result='pass';else result='passlocal('+keys.map(k=>k+':'+(k==='0-is_show_ysppdf'?'Y':'')).join(';')+(mode==='extra_init'?';0-UNREVIEWED:value':'')+')';
    } else if(o.url==='ybxPrint_yb4PrintHtml.action'){
      generated++;assert.equal(o.data.serialno,'10001');assert.equal(o.data.printType,'打印历史');
      assert.equal(decodeURI(o.data.jsonData),'YB!P!HIS!P!10001!P!打印历史');result='a'.repeat(32)+'&native.pdf&.pdf';
    } else throw Error('unexpected endpoint');this.fn(result);return this;}})};
  const bill={'t.serial_no':'10001','t.REQ_INFO':'TEST001-Person','t.status':mode==='draft'?'-99':'10','t.status_name':'Ready',
    't.total_amt':mode==='amount'?'9.99':'10.00','t.abst':mode==='summary'?'Other':'Test','t.node_id':'','t.bx_type':'1'};
  const ctx=vm.createContext({window:{jQuery:$},performance,Date,TextEncoder,crypto:webcrypto,Uint8Array,URL,URLSearchParams,AbortSignal,
    btoa:s=>Buffer.from(s,'binary').toString('base64'),location:{origin:'https://cwzh.ustc.edu.cn',pathname:'/YBX/main.jsp',href:'https://cwzh.ustc.edu.cn/YBX/main.jsp'},
    reimbursementBusinessApi:async()=>({ok:true,complete:true,bill,requests:[]}),fetch:async(url)=>{
      if(url==='customjs/wf_ybx.js')return{ok:true,text:async()=>mode==='callback_drift'?callbackSource.replace('true','false'):callbackSource};
      downloaded++;assert.equal(url.pathname,'/YBX/fileSystem_getAction.action');assert.equal(url.searchParams.get('projid'),'YBX');
      if(mode==='download_unknown'&&downloaded===1)throw Error('network response lost');return{ok:true,arrayBuffer:async()=>bytes};
    }});vm.runInContext(source,ctx);
  const c={case_id:'fixture',serial:'10001',expected_userid:'TEST001',expected_amount:'10.00',expected_summary:'Test',
    session_token:'b'.repeat(32),details_contract:contract,basic_contracts:[],max_bytes:64000000,max_chunk:131072};
  const call=(action,extra={})=>vm.runInContext('reimbursementDetailsApi('+JSON.stringify({...c,action,...extra})+')',ctx);
  const seedLegacy=(phase='preparing')=>Object.defineProperty(ctx.window,'__ustcDetailsPdfApiV1',{configurable:true,value:{
    case_id:c.case_id,serial:c.serial,userid:c.expected_userid,token:c.session_token,expected_amount:c.expected_amount,
    expected_summary:c.expected_summary,contract,phase,requests:[{endpoint:'commonQuery_doQuery.action',status:'received'},{endpoint:'common_bindSQLDataBackend.action',status:'received'}]}});
  return {call,seedLegacy,get generated(){return generated;},get downloaded(){return downloaded;},data};
}
(async()=>{
  const f=await fixture(),m=await f.call('begin');assert.equal(m.final_print_verified,false);assert.equal(m.approval_verified,false);
  assert.equal(m.kind,'system_view_preview_not_final_print');assert.equal(m.details_transport,'native_http');assert.equal(m.financial_write,false);
  assert.equal(f.data['WF_YBX$61-SERIALNO'],'OTHER');assert.equal(f.generated,1);
  const chunk=await f.call('chunk',{offset:0,length:512});assert.equal(Buffer.from(chunk.base64,'base64').length,512);
  await assert.rejects(()=>f.call('begin'),/PENDING_ORIGINAL/);assert.equal(f.generated,1);
  assert((await f.call('finish')).transport_finished);
  const draft=await fixture('draft');const rejected=await draft.call('begin');
  assert.equal(rejected.rejection_code,'draft_not_downloadable');assert.equal(rejected.pdf_generation_requested,false);
  assert.equal(draft.generated,0);assert.equal(draft.downloaded,0);
  const oldDraft=await fixture('draft');oldDraft.seedLegacy();
  const resolved=await oldDraft.call('begin',{recovery:{kind:'native_details_read_rejection',source_sha256:'c'.repeat(64)}});
  assert.equal(resolved.original_rejection_recovered,true);assert.equal(oldDraft.generated,0);assert.equal(oldDraft.downloaded,0);
  for(const mode of ['ok','amount','summary']){
    const changed=await fixture(mode);changed.seedLegacy();
    await assert.rejects(()=>changed.call('begin',{recovery:{kind:'native_details_read_rejection',source_sha256:'c'.repeat(64)}}),/REJECTION_CHANGED/);
    assert.equal(changed.generated,0);
  }
  const wrongPhase=await fixture('draft');wrongPhase.seedLegacy('pdf_generate');
  await assert.rejects(()=>wrongPhase.call('begin',{recovery:{kind:'native_details_read_rejection',source_sha256:'c'.repeat(64)}}),/REJECTION_PHASE/);
  for(const mode of ['disabled','server_identity','template_drift','callback_drift','extra_init','amount','summary']){
    const bad=await fixture(mode);await assert.rejects(()=>bad.call('begin'));assert.equal(bad.generated,0);assert.equal(bad.downloaded,0);
  }
  const lost=await fixture('download_unknown');await assert.rejects(()=>lost.call('begin'),/DOWNLOAD_UNKNOWN/);
  const resumed=await lost.call('begin',{recovery:{kind:'native_details_original',source_sha256:'c'.repeat(64)}});
  assert(resumed.reused_generated_file);assert.equal(lost.generated,1);assert.equal(lost.downloaded,2);
  assert(!/\.click\s*\(|\.trigger\s*\(|dispatchEvent|eval\s*\(/.test(source));
  console.log('details HTTP: history/preview identity, server actor, native permissions, definition/template guards, chunks and original-only recovery passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
