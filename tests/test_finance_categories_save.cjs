const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=['finance_native_form.js','finance_native_protocol.js','finance_categories_api.js'].map(f=>fs.readFileSync(path.join(__dirname,'../tools',f),'utf8')).join('\n');
const guid='A'.repeat(32),row={group:'纸制品',group_key:'纸制品!P!1',subject:'30201',subject_name:'办公费',project:'F1',budget:'B1',amount:'91.00',parameters:'纸制品!P!1&30201&F1&B1'};
const plan={original:{categories:[row]},changes:{'纸制品!P!1':{subject:'3029901',subject_name:'学生活动费'}},definition_sha256:'pinned'};
const c={serial:'123',case_id:'fixture',expected_userid:'TEST001',funding:[{code:'F1'}],expense_categories:[{group:'纸制品',subject:'3029901',subject_name:'学生活动费',amount:'91.00',invoice_guids:[guid]}],categories_contract:{select_button:'select',save_button:'save',definition_sha256:'pinned'},expected_plan:plan};
async function run(options={}){
 const state={calls:0,queries:0,contexts:[]};
 const context={'0-USERINFO.USERID':'TEST001','WF_YBX$61-SERIALNO':'123'};
 const proc='`$'+'a'.repeat(32)+':#61-serialno#';
 const ctx=vm.createContext({ReimbursementHttpValues:{moneyCents:v=>Math.round(Number(v)*100)},
   createReimbursementHttp:()=>({contextCopy:()=>({data:context,set:(k,v)=>{context['WF_YBX$'+k.toUpperCase()]=v;}}),post:async()=>{state.calls++;state.contexts.push({...context});return state.calls===1?(options.raw||'passlocal(BXX_WEB:[{"bxx_web_30201"：""}];0-SPLIT_JJSUBJ_FLAG:Y;0-tks_info_main_title:发票信息;0-tks_info_title_pc_title:发票内容;)'):state.calls===2?(options.selected||'passlocal(61-xtype:纸制品!P!1&3029901 &F1&)'):'pass';}}),
   reimbursementAmountDefinition:async()=>({wins:[{winno:'213',beforeLoadProcs:[proc],grid_btns:[{btnguid:'select',check_func:'',procs:[proc]}],func_btns:[{btnguid:'save',check_func:'',procs:[proc]}]}]})});
 vm.runInContext(source,ctx);ctx.prepared=plan;
 vm.runInContext('reimbursementCategoriesPrepare=async()=>prepared',ctx);
 ctx.query=async()=>{state.queries++;return ['30201','3029901'].map((subject,i)=>({'t.xtype':row.parameters,'t.amt':options.amount||'91.00','t.tk_guids':guid,'t.jj_subj':subject,'t.jj_subjname':i?'学生活动费':'办公费','t.select_flag':(state.calls>1?i===1:i===0)?'已选':''}));};
 vm.runInContext('reimbursementCategoriesQuery=query',ctx);ctx.config=c;
 const result=await vm.runInContext('reimbursementCategoriesSave(config)',ctx);return{result,state,context};
}
(async()=>{
 const good=await run();assert.equal(good.result.ok,true);assert.equal(good.state.calls,3);assert.equal(good.state.queries,2);
 assert.equal(good.state.contexts[1]['0-SPLIT_JJSUBJ_FLAG'],'Y');assert.equal(good.result.native_responses[0].updated_keys.length,4);
 assert.equal(good.state.contexts[2]['WF_YBX$61-XTYPE'],'纸制品!P!1&3029901 &F1&');
 for(const selected of ['passlocal(61-xtype:别组!P!1&3029901&F1&)','passlocal(61-xtype:纸制品!P!1&30201&F1&)','passlocal(61-xtype:纸制品!P!1&3029901&OTHER&)']){
   const bad=await run({selected});assert.equal(bad.result.ok,false);assert.equal(bad.result.failed_phase,'select');assert.equal(bad.state.calls,2);
 }
 for(const raw of ['passlocal(0-userinfo.userid:OTHER)','passlocal(61-serialno:OTHER)','judge:confirm','passlocal(0-SPLIT_JJSUBJ_FLAG:Y;0-SPLIT_JJSUBJ_FLAG:N)','passlocal(0-SPLIT_JJSUBJ_FLAG:Y:N)','passlocal(BXX_WEB:[{"__proto__"：""}])']){
   const bad=await run({raw});assert.equal(bad.result.ok,false);assert.equal(bad.result.failed_phase,'initialize');assert.equal(bad.state.calls,1);assert.equal(bad.state.queries,0);
   assert.equal(bad.context['WF_YBX$61-SERIALNO'],'123');assert.equal(bad.context['WF_YBX$61-XTYPE'],row.parameters);
 }
 const drift=await run({amount:'92.00'});assert.equal(drift.result.ok,false);assert.equal(drift.state.calls,1);assert.equal(drift.result.error,'CATEGORIES_API_COMPLETE_GROUP_CHANGED');
 console.log('categories save: native local initialization, unchanged identity, exact group and select/readback/save sequence passed');
})().catch(e=>{console.error(e);process.exit(1);});
