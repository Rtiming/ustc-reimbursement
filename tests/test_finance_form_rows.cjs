const assert=require('node:assert/strict'),vm=require('vm'),fs=require('fs');
const ctx=vm.createContext({});vm.runInContext(fs.readFileSync('tools/finance_business_api.js','utf8'),ctx);
const form={multiRec:true,formFields:[{fieldname:'t.sno'},{fieldname:'t.amount'},{fieldname:'t.remark'}]};
const r=ctx.reimbursementExpandFormRows(form,[{SNO:'S1$|S2',AMOUNT:'4.00$|6.00',REMARK:''}]);
assert.equal(r.length,2);assert.equal(r[1].SNO,'S2');assert.equal(r[1].AMOUNT,'6.00');assert.equal(r[1].REMARK,'');
assert.throws(()=>ctx.reimbursementExpandFormRows(form,[{SNO:'S1$|S2',AMOUNT:'4.00',REMARK:''}]));
assert.throws(()=>ctx.reimbursementExpandFormRows(form,[{SNO:'S1$|S2',AMOUNT:'4$|5$|6'}]));
assert.equal(ctx.reimbursementExpandFormRows({...form,multiRec:false},[{SNO:'a$|b'}]).length,1);
console.log('native multi-record form: aligned columns, empty defaults, mismatched row counts rejected');
