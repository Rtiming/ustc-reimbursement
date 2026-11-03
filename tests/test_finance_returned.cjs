const assert=require('node:assert/strict'),fs=require('fs'),vm=require('vm'),crypto=require('crypto');
const source=['finance_http.js','finance_native_form.js','finance_native_protocol.js','finance_returned_api.js'].map(n=>fs.readFileSync('tools/'+n,'utf8')).join('\n');
const canonical=v=>Array.isArray(v)?v.map(canonical):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;
function fixture(mode='ok'){
 const button={btnguid:'modify',caption:'修改',check_func:'`$'+'a'.repeat(32)+':#101-MultiRows#;#101-MultiCount#;#0-userinfo.userid#',procs:['`$'+'b'.repeat(32)+':#101-MultiRows#;#0-userinfo.userid#']};
 const d={wins:[{winno:71,func_btns:[button]}],funcs:[],funcMap:{},actMap:{}};
 let bill={'t.serial_no':'123','t.REQ_INFO':(mode==='other'?'OTHER':'TEST001')+'-测试','t.total_amt':'3700.00','t.status':mode==='submitted'?'1':'-3','t.node_id':'578','t.wf_name':'YBX_LC','t.uni_key':'123456','t.lc_finish':'F','t.bx_type':'1'},writes=0;
 let row={'t.tk_guid':'a'.repeat(32),'t.serial_no':'123','t.tk_total_amt':'3700.00','t.status_name':'流程已退回'};
 const user={'0-USERINFO.USERID':'TEST001','0-USERINFO.USERNAME':'合成','WF_YBX$61-SERIALNO':'OTHER'},before=JSON.stringify(user);
 const $={getProjID:()=> 'WF_YBX',UC:{userData:user,bindData:()=> 'TEST001'},ajax:o=>({done(fn){this.fn=fn;return this},fail(){
  if(o.url.startsWith('loadDefinition'))this.fn(d);else {writes++;if(mode==='timeout')return this;if(mode==='judge'&&!o.data.cmd.includes('check_func:"escape"')){this.fn('judge:如您需要修改，将会使报销单进入草稿状态，需重新提交后进入流程审批');return this;}bill={...bill,'t.status':'-99','t.node_id':'','t.wf_name':'','t.uni_key':''};this.fn('passlocal(61-serialno:123)');}return this;}})};
 const ctx=vm.createContext({performance,crypto:crypto.webcrypto,TextEncoder,window:{jQuery:$},location:{origin:'https://cwzh.ustc.edu.cn',pathname:'/YBX/main.jsp'},reimbursementBusinessApi:async c=>({ok:true,complete:true,serial:c.serial,expected_userid:c.expected_userid,bill:{...bill},sections:[{funcno:'533',complete:true,rows:[{...row}]}],requests:[]})});
 vm.runInContext(source,ctx);
 const c={case_id:'fixture',serial:'123',expected_userid:'TEST001',expected_amount:'3700.00',expected_guids:[row['t.tk_guid']],returned_contract:{button,definition_sha256:crypto.createHash('sha256').update(JSON.stringify(canonical(d))).digest('hex')},execute:true,pending_registered:true,token:'one-intent'};
 return {ctx,c,writes:()=>writes,bill:()=>bill,mutateBill:v=>{bill={...bill,...v}},mutateRow:v=>{row={...row,...v}},unchanged:()=>assert.equal(JSON.stringify(user),before)};
}
(async()=>{
 for(const mode of ['other','submitted']){const f=fixture(mode);await assert.rejects(()=>f.ctx.reimbursementReturnedPrepare(f.c));assert.equal(f.writes(),0);}
 const f=fixture();f.c.prepared=await f.ctx.reimbursementReturnedPrepare(f.c);assert.equal(f.writes(),0);
 assert.equal((await f.ctx.reimbursementReturnedOpen(f.c)).ok,true);assert.equal(f.writes(),1);
 f.mutateRow({'t.status_name':'已保存草稿'});assert.equal((await f.ctx.reimbursementReturnedReadback(f.c)).original_serial_preserved,true);
 await assert.rejects(()=>f.ctx.reimbursementReturnedOpen(f.c));assert.equal(f.writes(),1);f.unchanged();
 f.mutateRow({'t.tk_total_amt':'1500.00'});await assert.rejects(()=>f.ctx.reimbursementReturnedReadback(f.c),/INVOICES_CHANGED/);
 for(const change of [{'t.status':'-99'},{'t.node_id':'449'},{'t.total_amt':'1500.00'},{'t.bx_type':'2'}]){const x=fixture();x.mutateBill(change);await assert.rejects(()=>x.ctx.reimbursementReturnedPrepare(x.c));assert.equal(x.writes(),0);}
 const x=fixture();x.c.prepared=await x.ctx.reimbursementReturnedPrepare(x.c);x.mutateRow({'t.tk_total_amt':'4000.00'});await assert.rejects(()=>x.ctx.reimbursementReturnedOpen(x.c),/SOURCE_CHANGED/);assert.equal(x.writes(),0);
 const j=fixture('judge');j.c.prepared=await j.ctx.reimbursementReturnedPrepare(j.c);const first=await j.ctx.reimbursementReturnedOpen(j.c);assert.equal(first.ok,false);assert.equal(j.bill()['t.status'],'-3');
 const confirm={...j.c,action:'returned-confirm',token:'confirm',confirmation:{native_response:first.native_response}};
 assert.equal((await j.ctx.reimbursementReturnedOpen(confirm)).ok,true);assert.equal(j.writes(),2);assert.equal((await j.ctx.reimbursementReturnedReadback(confirm)).original_serial_preserved,true);
 console.log('returned original: own rejected daily, same amount/invoices, source drift, single dispatch and independent readback passed');
})().catch(e=>{console.error(e);process.exitCode=1});
