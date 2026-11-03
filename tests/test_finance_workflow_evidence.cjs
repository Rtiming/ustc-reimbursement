const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const code=fs.readFileSync(process.env.REIMBURSEMENT_TEST_WORKFLOW_SOURCE||path.join(__dirname,'../tools/finance_workflow_api.js'),'utf8');
async function fixture(mode='ok'){
 const c={case_id:'fixture',serial:'123',expected_userid:'TEST001',system_mode:mode==='salary'?'remuneration':'daily',funding_policy:mode==='policy'?{invoice_level_required:true}:{},payment_contracts:['payment'],budget_contracts:['budget']};
 const calls=[],read=domain=>({ok:true,complete:true,case_id:mode==='wrong_case'?'other':c.case_id,serial:c.serial,expected_userid:c.expected_userid,domain,system_write:false,sections:[{funcno:domain,rows:[{original:'kept'}]}],requests:[{endpoint:domain,status:'received'}]});
 const ctx=vm.createContext({reimbursementPaymentsBuild:async p=>{calls.push('payment-build');assert.equal(p.contracts,c.payment_contracts);return{read:read('payments'),report:{changes:mode==='payment_change'?{amount:'1'}:{},card_bindings:mode==='card'?[{selection_required:true}]:[],corporate_bindings:mode==='corporate'?[{resolve_required:true}]:[],requests:[{endpoint:'payment-owner',status:'received'}]}};},reimbursementExpensesBuild:async p=>{calls.push('expense-build');assert.equal(p.contracts,c.budget_contracts);return{read:read('categories'),report:{changes:mode==='budget_change'?{node:'1'}:{},requests:[{endpoint:'expense-owner',status:'received'}]}};},reimbursementBusinessApi:async p=>{calls.push(p.domain);return read(p.domain);}});
 vm.runInContext(code,ctx);return{result:await ctx.reimbursementWorkflowEvidence(c,{'t.status':mode==='submitted'?'30':'-99'}),calls};
}
(async()=>{
 const f=await fixture();assert.deepEqual(f.calls,['payment-build','expense-build']);assert.equal(JSON.stringify(f.result.required_changes),'{}');assert.equal(f.result.payments.sections[0].rows[0].original,'kept');assert.equal(f.result.budget.sections[0].rows[0].original,'kept');assert.equal(f.result.requests.length,2);
 for(const mode of ['payment_change','card','corporate'])assert.equal((await fixture(mode)).result.required_changes.payments,true);
 assert.equal((await fixture('budget_change')).result.required_changes.budget,true);
 const policy=await fixture('policy');assert.deepEqual(policy.calls,['payment-build','categories']);assert.equal(policy.result.required_changes.invoice_level_policy_current_proof_required,true);
 assert.deepEqual((await fixture('submitted')).calls,['payments','categories']);
 assert.deepEqual((await fixture('salary')).calls,['payments','expense-build']);
 await assert.rejects(()=>fixture('wrong_case'),/DOMAIN_READ_BINDING/);
 console.log('workflow evidence: one validated domain read, complete original sections, change/card/policy gates and legacy routes preserved');
})().catch(e=>{console.error(e);process.exitCode=1});
