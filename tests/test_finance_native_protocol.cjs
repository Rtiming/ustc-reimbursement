const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../tools/finance_native_protocol.js'), 'utf8');
const protocol = vm.runInNewContext(source + ';ReimbursementNativeProtocol;', {});
const context = {'WF_YBX$60-T.ABST': '中文"\\\n内容', 'WF_YBX$61-SERIALNO': '123', '0-USERINFO.USERID': 'TEST001'};
const spec = '`$' + 'a'.repeat(32) + ':#60-t.abst#;#61-serialno#;#0-userinfo.userid#';
const text = protocol.command('', [spec], context);
// Evaluate only our generated data in an isolated test to check wire escaping;
// production response handling never evaluates school text.
const data = vm.runInNewContext('(' + text + ')');
assert.equal(data.data0[0].value, context['WF_YBX$60-T.ABST']);
assert.equal(data.data0[1].name, 'serialno');
assert.equal(data.data0[2].type, 'CHAR');
assert.equal(data.wfAct, '');
assert.equal(data.check_func, 'escape');
assert.equal(data.proc0, '`$' + 'a'.repeat(32));
const outer=protocol.parameters('#0-OUTER-UNIKEY#',{'0-OUTER-UNIKEY':''});
assert.equal(vm.runInNewContext('('+outer+')')[0].name,'OUTER-UNIKEY');
assert.throws(()=>protocol.parameters('#0-OUTER;code#',{}),/REFERENCE/);
assert.throws(() => protocol.command('', [spec], {}), /MISSING_VALUE/);
assert.throws(() => protocol.command('', ['@arbitrary|#0-userinfo.userid#'], context), /SPEC/);
assert.throws(() => protocol.parameters('#60-x#', {'WF_YBX$60-X': 1}), /MISSING_VALUE/);
assert.equal(vm.runInNewContext('(' + protocol.parameters('#60-x#', {'WF_YBX$60-X': '[1]'}) + ')')[0].value, '[1] ');
let result = protocol.applyResponse('pass;local(60-lc_value:Teacher;60-lc_field:t.lc_accepter_name)', context, ['60-lc_value','60-lc_field']);
assert.equal(result.accepted, true); assert.equal(context['WF_YBX$60-LC_VALUE'], 'Teacher');
const before = JSON.stringify(context);
for (const bad of ['pass;local(60-lc_value:new;0-userinfo.userid:OTHER)', 'pass;local(60-lc_value:new;60-lc_value:duplicate)',
 'pass;local(60-lc_value:a:b)', 'pass;local(60-lc_value:new', 'afterjs:alert(1)', 'success_callback()',
 'judge:Confirm?', 'error:rejected']) {
  assert.throws(() => protocol.applyResponse(bad, context, ['60-lc_value']));
  assert.equal(JSON.stringify(context), before);
}
assert.equal(protocol.response('judge:Confirm?', []).confirmation_required, true);
assert.equal(protocol.response('error:no', []).accepted, false);
assert.equal(protocol.response('passlocal(60-field:value)', ['60-field']).updates['60-FIELD'], 'value');
assert.equal(protocol.response('passlocal(61-jhinfo:)passlocal(61-Lc_IsSignCa:F)successlocal(61-tipinfo:)', ['61-jhinfo','61-Lc_IsSignCa','61-tipinfo']).updates['61-LC_ISSIGNCA'], 'F');
for(const bad of ['passlocal(60-field:x)error:rejected','passlocal(60-field:x)judgelocal(60-other:y)',
 'passlocal(60-field:x)passlocal(60-field:y)','passlocal(60-field:x)passlocal(0-userinfo.userid:OTHER)',
 'passlocal(60-field:x)passlocal(60-other:y)afterjs:run()']) {
 const frozen=JSON.stringify(context);
 assert.throws(()=>protocol.applyResponse(bad,context,['60-field','60-other']));
 assert.equal(JSON.stringify(context),frozen);
}
assert.equal(protocol.response('passlocal(60-field:value; )', ['60-field']).updates['60-FIELD'], 'value');
assert.throws(()=>protocol.response('passlocal(60-field:value; ;0-userinfo.userid:OTHER)', ['60-field']),/LOCAL_KEY/);
console.log('native protocol: data-only serialization, escaping, identity and ambiguous-response rejection passed');

const varchar=vm.runInNewContext('('+protocol.parameters('#282-dd.review_kind#',{'WF_YBX$282-DD.REVIEW_KIND':''},{'dd.review_kind':'VARCHAR2'})+')');
assert.equal(varchar[0].type,'VARCHAR2');assert.equal(varchar[0].value,'');
assert.throws(()=>protocol.parameters('#282-dd.review_kind#',{'WF_YBX$282-DD.REVIEW_KIND':''},{'dd.review_kind':'UNKNOWN'}),/TYPE/);
