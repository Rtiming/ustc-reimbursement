const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {webcrypto,createHash}=require('node:crypto'),{File}=require('node:buffer');
const source=['finance_http.js','finance_native_protocol.js','finance_native_form.js','finance_upload_api.js'].map(n=>fs.readFileSync(path.join(__dirname,'../tools',n),'utf8')).join('\n');
const digest=b=>createHash('sha256').update(b).digest('hex');
function canonical(v){return Array.isArray(v)?v.map(canonical):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;}
async function fixture(mode='ok') {
  const bytes=Buffer.from('verified original invoice'),plugin=Buffer.from('fixed official upload plugin');let uploads=0,saves=0,reads=0;
  const save={caption:'保存发票',check_func:'',procs:['`$'+'a'.repeat(32)+':#84-t.img#;#61-tksFilesSrc#;#61-TRIP_ID#;#0-userinfo.userid#']};
  const fields=[{fieldname:'t.img',inputtype:'uploadfile',includebase64code:'false',includeMD5Code:'true',needTransPic:'true'},{fieldname:'t.docs'}];
  const definition={wins:[{winno:60,beforeLoadProcs:[],func_btns:[save]}],funcs:[{funcno:84,formFields:fields,typeMap:{}}],funcMap:{},actMap:{}};
  const contract={winno:'60',funcno:'84',save_caption:'保存发票',definition_sha256:digest(JSON.stringify(canonical(definition))),
    source:'YBX',trip_id:'',max_file_bytes:5242880,upload_plugin_sha256:digest(plugin)};
  const $={getProjID:()=> 'WF_YBX',UC:{bindData:()=> 'TEST001',userData:{'0-USERINFO.USERID':'TEST001',
    '0-FPSC_FILETYPES':'*.pdf;*.ofd;*.png','0-FPSC_FILESIZE':mode==='size'?'0KB':'1000MB'}},ajax:o=>({done(fn){this.fn=fn;return this;},fail(fn){
      if(o.url.startsWith('loadDefinition'))this.fn(definition);
      else {saves++;assert.equal(o.url,'commonUpdate_responseButtonEvent.action');const data=JSON.parse(o.data.userContext);
        assert.equal(data['WF_YBX$61-TKSFILESSRC'],'YBX');assert.equal(data['WF_YBX$61-TRIP_ID'],'');
        assert(data['WF_YBX$84-T.IMG'].includes('&invoice.pdf&.pdf&WF_YBX'));
        if(mode==='save_unknown')fn(null,'timeout');else this.fn('pass:');}return this;}})};
  const ctx=vm.createContext({window:{jQuery:$},performance,Date,TextEncoder,crypto:webcrypto,Uint8Array,File,FormData,AbortSignal,
    atob:s=>Buffer.from(s,'base64').toString('binary'),location:{origin:'https://cwzh.ustc.edu.cn',pathname:'/YBX/main.jsp'},
    reimbursementBusinessApi:async()=>{reads++;return {rows:mode==='duplicate'?[{'t.tk_no':'9'.repeat(20),'t.tk_guid':'old'}]:mode==='drift'&&reads>1?[{'t.tk_guid':'new'}]:[],requests:[]};},
    fetch:async(url,options)=>{
      if(url==='uploadFile/jquery.uploadifive.js')return{ok:true,arrayBuffer:async()=>mode==='plugin_drift'?Buffer.from('changed'):plugin};
      uploads++;assert.equal(url,'fileSystem_saveAction.action?projid=WF_YBX&needBase64=false&needMd5=true&needTransPic=true');
      const file=options.body.get('doc');assert.equal(file.name,'invoice.pdf');assert.deepEqual(Buffer.from(await file.arrayBuffer()),bytes);
      if(mode==='upload_unknown')throw Error('unknown network response');return{ok:true,text:async()=> 'c'.repeat(32)};
    }});vm.runInContext(source,ctx);
  const f={name:'invoice.pdf',size:bytes.length,type:'application/pdf',sha256:digest(bytes)},batch={caseId:'fixture',expectedUserid:'TEST001',offset:0,count:1,files:[f]};
  const c={case_id:'fixture',expected_userid:'TEST001',token:'a'.repeat(32),offset:0,batch,execute:true,upload_execution_eligible:true,
    sources:[{name:f.name,sha256:f.sha256,invoice_numbers:['9'.repeat(20)]}],upload_contract:contract,pool_contracts:[]};
  if(mode==='medical'||mode==='multi'){
    const ids=[{kind:'medical',code:'12345678',number:'1234567890'}];
    if(mode==='multi')ids.push({kind:'vat',code:'',number:'8'.repeat(20)});
    c.sources[0].invoice_numbers=ids.map(x=>x.number);c.sources[0].invoice_identities=ids;
  }
  if(mode==='medical_code_missing')c.sources[0].invoice_identities=[{kind:'medical',code:'',number:'9'.repeat(20)}];
  const call=(action,extra={})=>vm.runInContext('reimbursementUploadApi('+JSON.stringify({...c,action,...extra})+')',ctx);
  const append=()=>call('append',{index:0,offset:0,base64:bytes.toString('base64'),chunk_sha256:digest(bytes)});
  const ready=async()=>{const p=await call('prepare');await call('start',{expected_plan:p});await append();};
  return {call,append,ready,bytes,get uploads(){return uploads;},get saves(){return saves;}};
}
(async()=>{
  const f=await fixture();await f.ready();
  await assert.rejects(()=>f.append(),/CHUNK_SEQUENCE/);
  const u=await f.call('upload',{index:0});assert.equal(u.files[0].filecode,'c'.repeat(32));
  await assert.rejects(()=>f.call('upload',{index:0}),/UPLOAD_ONCE/);assert.equal(f.uploads,1);
  const s=await f.call('save');assert(s.save_dispatched);assert.equal(s.phase,'save_accepted');
  await assert.rejects(()=>f.call('save'),/SAVE_ONCE/);assert.equal(f.saves,1);
  for(const mode of ['duplicate','size','plugin_drift','drift']){
    const bad=await fixture(mode);await assert.rejects(()=>bad.ready());assert.equal(bad.uploads,0);assert.equal(bad.saves,0);
  }
  const unknown=await fixture('upload_unknown');await unknown.ready();await assert.rejects(()=>unknown.call('upload',{index:0}));
  await assert.rejects(()=>unknown.call('upload',{index:0}),/UPLOAD_ONCE/);assert.equal(unknown.uploads,1);assert.equal(unknown.saves,0);
  const lost=await fixture('save_unknown');await lost.ready();await lost.call('upload',{index:0});await assert.rejects(()=>lost.call('save'));
  assert.equal((await lost.call('status')).phase,'save_requested');await assert.rejects(()=>lost.call('save'),/SAVE_ONCE/);assert.equal(lost.saves,1);
  const corrupt=await fixture();const p=await corrupt.call('prepare');await corrupt.call('start',{expected_plan:p});
  await assert.rejects(()=>corrupt.call('append',{index:0,offset:0,base64:corrupt.bytes.toString('base64'),chunk_sha256:'0'.repeat(64)}),/CHUNK_SHA/);
  assert.equal((await corrupt.call('status')).files[0].received,0);assert.equal(corrupt.uploads,0);
  for(const mode of ['medical','multi']){const rich=await fixture(mode);await rich.ready();await rich.call('upload',{index:0});await rich.call('save');assert.equal(rich.uploads,1);assert.equal(rich.saves,1);}
  const short=await fixture('medical_code_missing');await assert.rejects(()=>short.ready(),/IDENTITIES/);assert.equal(short.uploads,0);
  assert(!/\.click\s*\(|\.trigger\s*\(|dispatchEvent|DataTransfer|eval\s*\(/.test(source));
  console.log('HTTP upload: fixed multipart, SHA chunks, native limits/plugin drift, duplicate rejection, single save and unknown no replay passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
