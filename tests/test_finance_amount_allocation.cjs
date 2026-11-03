const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),{webcrypto}=require('node:crypto');
const source=['finance_http.js','finance_native_protocol.js','finance_native_form.js','finance_amount_api.js'].map(f=>fs.readFileSync(require('node:path').join(__dirname,'../tools',f),'utf8')).join('\n');
const ctx=vm.createContext({performance,crypto:webcrypto,TextEncoder});vm.runInContext(source,ctx);
const guid='A'.repeat(32),fields=['t.t_desc','t.invoice_amt','t.t_amt','t.old_amt','t.amt_sum','t.tk_guid','t.xtype','t.uni_prj_code','t.bu_code','t.t_name','t.ord','t.tlevel','t.tparent','t.tleaf','t.texpanded','t.tloaded'];
const row=(id,parent,leaf,project,amount)=>({...Object.fromEntries(fields.map(k=>[k,''])),'t.tk_guid':guid,'t.ord':id,'t.tparent':parent,'t.tleaf':leaf?'true':'false','t.t_amt':amount,'t.invoice_amt':'12.00','t.uni_prj_code':project,'t.bu_code':project?'B1':'','t.old_amt':'12.00','t.amt_sum':'12.00'});
const rows=[row('R','0',false,'','10.00'),row('A','R',true,'F1','7.00'),row('B','R',true,'F2','5.00')];
const c={funding:[{code:'F1',amount:'6.00'},{code:'F2',amount:'4.00'}],funding_policy:{},reduction:{rows:[{invoice_guid:guid,claim_amount:'10.00'}]},budget_allocations:[
 {invoice_guid:guid,project:'F1',budget:'B1',amount:'6.00'},{invoice_guid:guid,project:'F2',budget:'B1',amount:'4.00'}]};
const run=(config,data,owned=[guid])=>vm.runInContext('reimbursementAmountAllocationTarget('+[config,data,owned].map(JSON.stringify).join(',')+')',ctx);
const original=JSON.stringify(rows),target=run(c,rows);assert.equal(target[0]['t.t_amt'],'10.00');assert.equal(target[1]['t.t_amt'],'6.00');assert.equal(target[2]['t.t_amt'],'4.00');
for(let i=0;i<rows.length;i++)for(const key of fields)if(key!=='t.t_amt')assert.equal(target[i][key],rows[i][key]);assert.equal(JSON.stringify(rows),original);
for(const mode of ['missing','duplicate','extra','wrong_total','unapproved','foreign_guid','orphan','cycle','changed_budget','policy','over_cap']){
 const config=JSON.parse(JSON.stringify(c)),data=JSON.parse(JSON.stringify(rows));let owned=[guid];
 if(mode==='missing')config.budget_allocations.pop();if(mode==='duplicate')config.budget_allocations[1]=config.budget_allocations[0];
 if(mode==='extra')config.budget_allocations.push(config.budget_allocations[0]);if(mode==='wrong_total')config.budget_allocations[0].amount='5.00';
 if(mode==='unapproved')config.funding.pop();if(mode==='foreign_guid')owned=['B'.repeat(32)];
 if(mode==='orphan')data[1]['t.tparent']='missing';if(mode==='cycle')data[0]['t.tparent']='A';
 if(mode==='changed_budget')data[1]['t.bu_code']='OTHER';if(mode==='policy')config.funding_policy.invoice_level_required=true;
 if(mode==='over_cap'){config.budget_allocations[0].amount='8.00';config.budget_allocations[1].amount='2.00';}
 assert.throws(()=>run(config,data,owned),undefined,mode);
}
console.log('invoice allocation: owned GUIDs, full exact leaf targets, invoice/fund totals, tree integrity, protected fields and special-policy isolation passed');
