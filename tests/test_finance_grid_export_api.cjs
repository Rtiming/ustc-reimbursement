const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),{webcrypto}=require('node:crypto');
const source=['finance_http.js','finance_grid_export_api.js'].map(n=>fs.readFileSync(path.join(__dirname,'../tools',n),'utf8')).join('\n');
async function fixture(mode='ok') {
  const fields=['t.serial_no','t.req_date','t.uni_prj_code','t.abst','t.status_name','t.total_amt','t.real_info','t.req_mobile'];
  const rows=[Object.fromEntries(fields.map((f,i)=>[f,i===0?'10001':f==='t.total_amt'?'10.00':'Text']))];
  const bytes=new Uint8Array(512);bytes.set([208,207,17,224,161,177,26,225]);let downloads=0;
  const $={getProjID:()=> 'WF_YBX',UC:{userData:{},bindData:()=> 'TEST001'},ajax:o=>({done(fn){this.fn=fn;return this;},fail(){this.fn({funcs:[{funcno:'101',colModel:fields.map(name=>({name,isshow:true,hidecond:'',datatype:'CHAR'}))}]});return this;}})};
  const ctx=vm.createContext({window:{jQuery:$},performance,Date,TextEncoder,crypto:webcrypto,URLSearchParams,AbortSignal,
    Uint8Array,btoa:s=>Buffer.from(s,'binary').toString('base64'),location:{origin:'https://cwzh.ustc.edu.cn',pathname:'/YBX/main.jsp'},
    reimbursementBusinessApi:async c=>{assert.equal(c.serials.join(','),'10001');const r=structuredClone(rows);if(mode==='drift')r[0]['t.total_amt']='9.99';return{ok:true,complete:true,rows:r,total_records:1,requests:[]};},
    fetch:async(url,options)=>{downloads++;assert.equal(url,'printer.action');const setting=JSON.parse(decodeURIComponent(options.body.get('set')));assert.equal(setting.scope,'whole');assert.equal(setting.cols_en.length,8);
      if(mode==='timeout')throw Error('network unknown');return{ok:true,headers:{get:()=> 'application/msexcel'},arrayBuffer:async()=>bytes.buffer};}});vm.runInContext(source,ctx);
  const c={case_id:'fixture',expected_userid:'TEST001',session_token:'a'.repeat(32),scope:rows,scope_query:'serials',export_format:'EXCEL'};
  const call=async(action,extra={})=>vm.runInContext('reimbursementGridExportApi('+JSON.stringify({...c,action,...extra})+')',ctx);
  return{call,get downloads(){return downloads;}};
}
(async()=>{
  const f=await fixture(),m=await f.call('begin');assert.equal(m.records,1);assert.equal(m.system_write,false);
  const chunk=await f.call('chunk',{offset:0,length:512});assert.equal(Buffer.from(chunk.base64,'base64').length,512);
  await assert.rejects(()=>f.call('begin'),/PENDING_ORIGINAL/);assert.equal(f.downloads,1);
  await assert.rejects(()=>f.call('chunk',{offset:510,length:10}),/CHUNK_BOUNDS/);
  assert((await f.call('finish')).transport_finished);
  const changed=await fixture('drift');await assert.rejects(()=>changed.call('begin'),/SNAPSHOT_CHANGED/);assert.equal(changed.downloads,0);
  const unknown=await fixture('timeout');await assert.rejects(()=>unknown.call('begin'));await assert.rejects(()=>unknown.call('begin'),/PENDING_ORIGINAL/);assert.equal(unknown.downloads,1);
  assert(!/\.click\s*\(|\.trigger\s*\(|dispatchEvent|eval\s*\(/.test(source));
  console.log('native grid export: fixed source/columns, drift rejection, original intent, bounded chunks, SHA and no dialog or replay passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
