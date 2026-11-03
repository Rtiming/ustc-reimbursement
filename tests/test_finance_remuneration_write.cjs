const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const code=['finance_http.js','finance_native_protocol.js','finance_native_form.js','finance_remuneration_api.js'].map(f=>fs.readFileSync(path.join(__dirname,'../tools',f),'utf8')).join('\n');
async function fixture(action,mode='ok'){
 const calls=[],store={'0-BANKCHECK_URL':'F','WF_YBX$61-CJ_CHECJSYS':'N'},key=k=>(k.startsWith('0-')?'':'WF_YBX$')+k.toUpperCase(),get=k=>store[key(k)],set=(k,v)=>store[key(k)]=v;
 const native=caption=>({caption,check_func:'',procs:['`$'+'a'.repeat(32)+':#61-serialno#;#0-userinfo.userid#']});store['0-USERINFO.USERID']='TEST001';
 const original={'dd.state_ybx':'学生人员一般薪酬','dd.state':'学生','dd.abst':'学生其他劳务','dd.ff_startdate':'2026-05-31','dd.ff_enddate':'2026-05-31','dd.hastax':'T','dd.ffcycle':'1'};
 const events=['dd.state_ybx','dd.abst','dd.ff_startdate','dd.ff_enddate'].map(fieldname=>({fieldname,Events:[{event:'change',proc:'$'+'b'.repeat(32)+'|#61-serialno#'}]}));
 const personFields=['dd.unino','dd.amt','dd.premark'].map(fieldname=>({fieldname,nullable:false})),person={funcno:'282',groupnamefrom:mode==='groups_missing'?'native':'',typeMap:{},formFields:personFields,formhtml:'<tr><td>#dd.unino# #dd.amt# #dd.premark#</td></tr>'};
 const add=native('添加');add.btnguid='289FA79F8B1D4C6F8E427D84000E92D9';
 const buttons=[['394EF3609EDB471E812179D639C5DD95','前往报销'],['85DE9208A69E45AA8A37DC7947F503FA','前往报销cj'],['7FDC2123B80D4499AE9D186842DB040E','保存']].map(([btnguid,caption])=>({...native(caption),btnguid}));
 const row={'t.unino':'SA00000001','t.name':'合成人员','t.AMT':'10.00','t.premark':'合成劳务','t.serial_no':''},rows=action==='add-person'&&mode!=='duplicate'?[]:[row];
 const card={name:'合成人员',bankno:'TESTBANK',bankname:'合成银行',card_no:'000000****0000',branchcode:'','other_information':'native synthetic binding'},p={original,rows,bill:action==='save'?{'t.status':'-99','t.lc_finish':'F'}:null,person:{unino:'SA00000001',name:'合成人员',state:'学生',allowedFlag:'T',cardInfo:[card]},default_bank:card,requests:[]};
 const c={execute:true,pending_registered:true,action,case_id:'fixture',serial:action==='save'?'123':'',student_id:action==='add-person'?'SA00000001':null,expected_userid:'TEST001',expected_plan:p,activity:{approved_people:{SA00000001:{name:'合成人员',amount:'10.00'}},person_name:'合成人员',amount:'10.00',reason:'合成劳务',period_start:'2026-05-31',period_end:'2026-05-31',claim_amount:'10.00'}};
 set('61-serialno',c.serial);if(mode!=='groups_missing')set('282-groupname','');
 set('61-std_version','V2');set('61-otherinfo_json','[]');set('61-gznr_show','T');
 if(mode==='workflow')p.bill={'t.status':'1','t.lc_finish':'F'};
 const http={post:async(url,data)=>{calls.push({url,data});if(action==='create-claim'&&['judge','wrong_judge'].includes(mode)&&calls.length===1)return 'judge:正确发放记录共1条，合计'+(mode==='wrong_judge'?'11.00':'10.00')+'元，是否继续？';if(mode==='judge'&&calls.length===2)assert(data.cmd.includes('check_func:"escape"'));if(mode==='unknown'&&url==='commonUpdate_responseButtonEvent.action')throw Error('timeout');if(url==='commonUpdate_responseButtonEvent.action'){if(action==='add-person'&&calls.filter(x=>x.url===url).length===1)return'passlocal(61-select_std:;0-CJ_SELECT_OTH_STD:;61-cj_sel_standard:;61-premark_show:说明:事由;0-cardno_is_readonly:;0-dblrorderstrs:)';return action==='create-claim'?'passlocal(0-g0Serialno:123;0-need_turor:N;0-zip_seq:)' :'pass';}return'pass';}};
 const b={http,context:store,set,get,rows,original,bill:p.bill,requests:[],form:{typeMap:{},formFields:events},person,def:{wins:[{winno:198,func_btns:buttons}]},personDef:{wins:[{winno:199,beforeLoadProcs:native('init').procs,func_btns:[add]}]}};
 const ctx=vm.createContext({performance});vm.runInContext(code,ctx);
 if(mode!=='groups_missing')p.person_rules=ctx.reimbursementRemunerationPersonRules(person,get);c.expected_plan=p;
 ctx.reimbursementRemunerationInspect=async()=>p;ctx.reimbursementRemunerationBuild=async()=>b;
 const r=await vm.runInContext('reimbursementRemunerationSave('+JSON.stringify(c)+')',ctx);return{r,calls,store};
}
(async()=>{
 const add=await fixture('add-person');assert.equal(add.r.ok,true,JSON.stringify(add.r));assert.equal(add.calls.filter(c=>c.url==='commonUpdate_responseButtonEvent.action').length,2);assert.equal(add.store['WF_YBX$282-DD.AMT'],'10.00');assert.equal(add.store['WF_YBX$282-DD.BANKNO'],'TESTBANK!P!000000****0000!P!合成银行');
 const missing=await fixture('add-person','groups_missing');assert.equal(missing.r.error,'REMUNERATION_API_PERSON_VISIBLE_GROUPS_MISSING');assert.deepEqual(Array.from(missing.r.steps),[]);assert.equal(missing.calls.filter(c=>c.url==='commonUpdate_responseButtonEvent.action').length,1);
 const create=await fixture('create-claim');assert.equal(create.r.new_serial,'123');assert.equal(create.calls.length,2);
 const confirmed=await fixture('create-claim','judge');assert.equal(confirmed.r.new_serial,'123');assert.equal(confirmed.calls.length,3);
 const refused=await fixture('create-claim','wrong_judge');assert.equal(refused.r.error,'REMUNERATION_API_CREATION_CONFIRMATION_SCOPE');assert.equal(refused.calls.length,1);
 const save=await fixture('save');assert.equal(save.r.ok,true);assert.equal(save.calls.length,1);
 const unknown=await fixture('add-person','unknown');assert.equal(unknown.r.ok,false);assert.equal(unknown.calls.filter(c=>c.url==='commonUpdate_responseButtonEvent.action').length,1);assert.equal(unknown.r.save_dispatched,false);
 const duplicate=await fixture('add-person','duplicate');assert.equal(duplicate.r.ok,false);assert.equal(duplicate.calls.length,0);
 const workflow=await fixture('save','workflow');assert.equal(workflow.r.ok,false);assert.equal(workflow.calls.length,0);
 console.log('remuneration HTTP write: exact source, field events, bank binding, native add/create/save, workflow lock and timeout no-retry passed');
})().catch(e=>{console.error(e);process.exitCode=1});
