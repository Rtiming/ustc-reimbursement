const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),{webcrypto}=require('node:crypto');
const code=['finance_native_form.js','finance_categories_api.js'].map(f=>fs.readFileSync(require('node:path').join(__dirname,'../tools',f),'utf8')).join('\n');
const guid='A'.repeat(32),row={group:'纸制品',group_key:'纸制品!P!1',subject:'30201',subject_name:'办公费',project:'F1',budget:'B1',amount:'10.00',parameters:'纸制品!P!1&30201&F1&B1'};
const c={case_id:'fixture',serial:'123',expected_userid:'TEST001',expected_amount:'10.00',funding:[{code:'F1',amount:'10.00'}],funding_policy:{},categories_contract:{definition_sha256:'hash'},used_contracts:[],expense_categories:[{group:'纸制品',subject:'30202',subject_name:'印刷费',amount:'10.00',invoice_guids:[guid],source:'synthetic'}]};
async function fixture(config,mode){
 const ctx=vm.createContext({crypto:webcrypto,TextEncoder,performance,ReimbursementHttpValues:{moneyCents:v=>BigInt(v.replace('.','')),decode:v=>v},createReimbursementHttp:()=>({}),
 reimbursementAmountInspect:async()=>({bill:{'t.serial_no':'123'},basic_sections:[],categories:[{...row,...(mode==='project'?{project:'OTHER'}:{})}],requests:[]}),
 reimbursementBusinessApi:async()=>({sections:[{funcno:'533',complete:mode!=='incomplete',rows:[{'t.tk_guid':mode==='guid'?'B'.repeat(32):guid}]}],requests:[]}),
 reimbursementAmountDefinition:async()=>({})});
 vm.runInContext(code,ctx);return vm.runInContext('reimbursementCategoriesPrepare('+JSON.stringify(config)+')',ctx);
}
(async()=>{
 const p=await fixture(c);assert.equal(p.system_write,false);assert.equal(p.changes['纸制品!P!1'].subject,'30202');
 for(const mode of ['project','guid','incomplete'])await assert.rejects(fixture(c,mode));
 await assert.rejects(fixture({...c,funding:[...c.funding,{code:'F2'}]}));await assert.rejects(fixture({...c,funding_policy:{invoice_level_required:true}}));
 const ctx=vm.createContext({});vm.runInContext(code,ctx);
 const cell=(handler,name,amount)=>({getAttribute:()=>handler,textContent:name,closest:()=>({cells:[{},{},{},{},{textContent:amount}]})});
 const entry=cell("pageOpen_v1('发票信息','纸制品!P!1&30201&F1&B1')",'办公费','10.00');ctx.document={querySelectorAll:()=>[entry]};
 const rows=vm.runInContext("reimbursementCategoriesInventory(document,'123')",ctx);assert.equal(rows[0].group_key,row.group_key);
 ctx.document={querySelectorAll:()=>[entry,entry]};assert.throws(()=>vm.runInContext("reimbursementCategoriesInventory(document,'123')",ctx));
 console.log('categories HTTP: original group/GUID/project/amount source binding, complete reads, policy isolation and unique preview passed');
})().catch(e=>{console.error(e);process.exitCode=1});
