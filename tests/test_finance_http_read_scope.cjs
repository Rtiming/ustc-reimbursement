/* 跨owner的同次只读复用；写入/身份/新任务边界不能复用旧结果。 */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(process.env.REIMBURSEMENT_TEST_HTTP_SOURCE||path.join(__dirname,'../tools/finance_http.js'),'utf8');
function fixture(){
 let userid='TEST001',fail=false,sequence=0;
 const calls=[],requests=[],$={getProjID:()=> 'WF_YBX',UC:{userData:{'0-USERINFO.USERID':userid},bindData:()=>userid},ajax:o=>{
  calls.push(o);return{done(fn){this.fn=fn;return this;},fail(fn){if(fail){fail=false;fn({status:0},'timeout');}else this.fn(o.dataType==='text'?'template-'+(++sequence):{sequence:++sequence,nested:{untouched:'yes'}});return this;}};
 }};
 const box=vm.createContext({window:{jQuery:$},location:{origin:'https://cwzh.ustc.edu.cn',pathname:'/YBX/main.jsp'},performance});vm.runInContext(source,box);
 const scope=vm.runInContext('ReimbursementHttpReadScope',box),api=()=>box.createReimbursementHttp('TEST001',{prefix:'TEST',requests,timeout:15000,allowed:['definition','gridDefinition','query','binding','button','procedure','detailsTemplate','printDocument']});
 return{scope,api,calls,requests,changeIdentity:()=>{userid='OTHER';},failNext:()=>{fail=true;}};
}
const definition='loadDefinition.action?type=W',query='commonQuery_doQuery.action?funcno=101&projid=WF_YBX&needcount=true';
const params={_search:false,nd:1,page:1,rows:'100',sidx:'',sord:'',userData:'{"same":"context"}',itemArr:'',sectionArr:''};
(async()=>{
 const f=fixture();
 await f.scope.run('TEST001',async()=>{
  const a=await f.api().post(definition,{winno:'70',projid:'WF_YBX'});a.nested.untouched='changed';
  const b=await f.api().post(definition,{winno:'70',projid:'WF_YBX'});assert.equal(b.nested.untouched,'yes');assert.equal(f.calls.length,1);assert.equal(f.requests.at(-1).status,'reused');assert.equal(f.requests.at(-1).reused_from,f.requests[0].scope_request_id);
  const q=await f.api().post(query,params);assert.equal((await f.api().post(query,{...params,nd:999})).sequence,q.sequence);
  assert.equal(f.calls.length,2);
  await f.api().post(query,{...params,page:2});await f.api().post(query,{...params,userData:'{"other":"case"}'});assert.equal(f.calls.length,4);
  await f.api().post('common_bindSQLDataBackend.action',{sqlid:'fixed'},'text');await f.api().post('common_bindSQLDataBackend.action',{sqlid:'fixed'},'text');assert.equal(f.calls.length,6);
  await f.api().post('commonUpdate_responseButtonEvent.action',{cmd:'fixed'});
  assert.notEqual((await f.api().post(definition,{winno:'70',projid:'WF_YBX'})).sequence,b.sequence);assert.equal(f.calls.length,8);
  const n=f.calls.length;await f.api().post(definition,{winno:'70',projid:'WF_YBX'});assert.equal(f.calls.length,n);
  await f.api().post('common_callProc.action',{procName:'fixed'});await f.api().post(definition,{winno:'70',projid:'WF_YBX'});assert.equal(f.calls.length,n+2);
  await assert.rejects(()=>f.scope.run('TEST001',async()=>{}),/SCOPE_OWNER/);
 });
 const before=f.calls.length;await f.scope.run('TEST001',()=>f.api().post(definition,{winno:'70',projid:'WF_YBX'}));assert.equal(f.calls.length,before+1);
 await f.api().post(definition,{winno:'70',projid:'WF_YBX'});await f.api().post(definition,{winno:'70',projid:'WF_YBX'});assert.equal(f.calls.length,before+3);
 const g=fixture();await g.scope.run('TEST001',async()=>{await g.api().post(definition,{winno:'70'});g.changeIdentity();assert.throws(()=>g.api(),/IDENTITY/);});assert.equal(g.calls.length,1);
 const h=fixture();await h.scope.run('TEST001',async()=>{h.failNext();await assert.rejects(()=>h.api().post(definition,{winno:'70'}),/REQUEST_UNKNOWN/);assert.equal(h.calls.length,1);await h.api().post(definition,{winno:'70'});assert.equal(h.calls.length,2);});
 const limit=fixture();await limit.scope.run('TEST001',async()=>{for(let i=0;i<33;i++)await limit.api().post(definition,{winno:String(i)});await limit.api().post(definition,{winno:'32'});assert.equal(limit.calls.length,34);await limit.api().post(definition,{winno:'0'});assert.equal(limit.calls.length,34);});
 const aborted=fixture();await assert.rejects(()=>aborted.scope.run('TEST001',async()=>{await aborted.api().post(definition,{winno:'70'});throw Error('abort');}),/abort/);await aborted.scope.run('TEST001',()=>aborted.api().post(definition,{winno:'70'}));assert.equal(aborted.calls.length,2);
 const preview=fixture();await preview.scope.run('TEST001',async()=>{const url='ybxPrint_yb4PrintHtml.action',data={printModel:'P',printType:'报销确认',serialno:'123'};const first=await preview.api().post(url,data,'text');assert.equal(await preview.api().post(url,data,'text'),first);assert.equal(preview.calls.length,1);await preview.api().post(url,{...data,serialno:'456'},'text');assert.equal(preview.calls.length,2);await preview.api().post(url,{...data,printType:'签章预生成'},'text');await preview.api().post(url,data,'text');assert.equal(preview.calls.length,4);});
 console.log('scoped read reuse: fresh tasks, isolated values, exact query context, mutation invalidation, identity, failure and bounded lifetime passed');
})().catch(e=>{console.error(e);process.exitCode=1});
