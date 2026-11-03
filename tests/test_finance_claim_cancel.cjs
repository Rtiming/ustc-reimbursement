const assert=require('node:assert/strict'),fs=require('fs'),vm=require('vm'),crypto=require('crypto');
const source=['finance_http.js','finance_native_form.js','finance_native_protocol.js','finance_claim_cancel_api.js'].map(n=>fs.readFileSync('tools/'+n,'utf8')).join('\n');
const canonical=v=>Array.isArray(v)?v.map(canonical):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;
async function fixture(mode='ok'){
 const button={btnguid:'cancel',check_func:'`$'+'a'.repeat(32)+':#101-MultiRows#;#101-MultiCount#;#0-userinfo.userid#',procs:['`$'+'b'.repeat(32)+':#101-MultiRows#;#0-userinfo.userid#']},d={wins:[{winno:71,func_btns:[button]}],funcs:[],funcMap:{},actMap:{}};
 const bill={'t.serial_no':'123','t.REQ_INFO':(mode==='other'?'OTHER':'TEST001')+'-合成','t.total_amt':'10.00','t.status':mode==='submitted'?'1':'-99','t.lc_finish':'F','t.wf_name':'','t.uni_key':'','t.node_id':''};
 const nonvat=['nonvat','nonvat_wrong_auth','nonvat_wrong_type'].includes(mode);
 const row={'t.tk_guid':'a'.repeat(32),'t.serial_no':'123','t.tk_total_amt':'10.00','t.tk_code_abst':nonvat?'批/其他发票':'普/99000000000000000001','t.tk_type':nonvat?'MULTITK':'DZ','t.tk_mod_flag':'NORMAL','t.auth_rst_desc':nonvat?'正常':'正常(已认证)'};
 const user={'0-USERINFO.USERID':'TEST001','0-USERINFO.USERNAME':'合成','WF_YBX$61-SERIALNO':'OTHER'};let cancelled=false,writes=0;
 const before=JSON.stringify(user),$={getProjID:()=> 'WF_YBX',UC:{userData:user,bindData:()=> 'TEST001'},ajax:o=>({done(fn){this.fn=fn;return this;},fail(){
  if(o.url.startsWith('loadDefinition'))this.fn(d);
  else if(o.data.cmd.includes('"proc0"')||o.data.cmd.includes('proc0:')){writes++;cancelled=true;this.fn('pass');}
  else this.fn('judge:您确定要撤销报销单吗？');return this;}})};
 const ctx=vm.createContext({performance,crypto:crypto.webcrypto,TextEncoder,window:{jQuery:$},location:{origin:'https://cwzh.ustc.edu.cn',pathname:'/YBX/main.jsp'},reimbursementBusinessApi:async c=>c.action==='bills-list'?{complete:true,account_scope:'current_applicant',rows:cancelled?[]:[bill],requests:[]}:c.action==='invoices-list'?{complete:true,rows:[{...row,'t.serial_no':cancelled?'':'123',...(mode==='nonvat_wrong_auth'?{'t.auth_rst_desc':'正常(已认证)'}:{}),...(mode==='nonvat_wrong_type'?{'t.tk_type':'DZ'}:{})}],requests:[]}:{complete:true,bill,sections:[{funcno:'533',complete:true,rows:[row]}],requests:[]}});
 vm.runInContext(source,ctx);const c={case_id:'fixture',serial:'123',expected_userid:'TEST001',expected_amount:'10.00',expected_guids:[row['t.tk_guid']],cancel_contract:{button,definition_sha256:crypto.createHash('sha256').update(JSON.stringify(canonical(d))).digest('hex')},token:'original',execute:true,pending_registered:true};
 if(nonvat)c.expected_nonvat_guids=[row['t.tk_guid']];
 return{ctx,c,writes:()=>writes,unchanged:()=>assert.equal(JSON.stringify(user),before)};
}
(async()=>{
 for(const mode of ['submitted','other']){const f=await fixture(mode);await assert.rejects(()=>f.ctx.reimbursementClaimCancelPrepare(f.c));assert.equal(f.writes(),0);}
 const f=await fixture();f.c.prepared=await f.ctx.reimbursementClaimCancelPrepare(f.c);assert.equal(f.writes(),0);assert.equal((await f.ctx.reimbursementClaimCancel(f.c)).ok,true);assert.equal(f.writes(),1);
 const r=await f.ctx.reimbursementClaimCancelReadback(f.c);assert.equal(r.released.length,1);await assert.rejects(()=>f.ctx.reimbursementClaimCancel(f.c));assert.equal(f.writes(),1);f.unchanged();
 for(const mode of ['nonvat','nonvat_wrong_auth','nonvat_wrong_type']){const n=await fixture(mode);n.c.prepared=await n.ctx.reimbursementClaimCancelPrepare(n.c);await n.ctx.reimbursementClaimCancel(n.c);if(mode==='nonvat')assert.equal((await n.ctx.reimbursementClaimCancelReadback(n.c)).released.length,1);else await assert.rejects(()=>n.ctx.reimbursementClaimCancelReadback(n.c),/SOURCE_TYPE/);assert.equal(n.writes(),1);n.unchanged();}
 console.log('draft cancel: exact own draft, confirmation, single dispatch, independent absence and released-invoice readback passed');
})().catch(e=>{console.error(e);process.exitCode=1});
