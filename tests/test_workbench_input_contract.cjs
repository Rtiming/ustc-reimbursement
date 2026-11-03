const assert = require('node:assert/strict');
const input = require('../tools/operator_workbench_ui/input_contract.js');
(async () => {
  const base = 'a'.repeat(32);
  const plan = {case_id:'fixture',request:{sources:[{id:'a',path:'/synthetic/file.pdf'}]},execute:false};
  const first = await input.requestId(base, plan);
  assert.equal(first, await input.requestId(base, {...plan,request_id:'ignored'}));
  assert.equal(first, await input.requestId(base, {execute:false,request:plan.request,case_id:'fixture'}));
  assert.notEqual(first, await input.requestId(base, {...plan,execute:true}));
  assert.notEqual(first, await input.requestId(base, {...plan,case_id:'different'}));
  assert.ok(first.length <= 96);
  await assert.rejects(input.requestId('../invalid', plan));
  console.log('workbench intent contract: passed');
})();
