'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../tools/finance_http.js'), 'utf8');
function fixture(mode) {
  let user = 'TEST001';
  const calls = [], requests = [], data = {'0-USERINFO.USERID': user, 'WF_YBX$61-SERIALNO': 'original'};
  const $ = {getProjID: () => 'WF_YBX', UC: {userData: data, bindData: () => user}, ajax: o => {
    calls.push(o);
    return {done(fn) {this.fn = fn; return this;}, fail(fn) {
      if (mode === 'timeout') fn(null, 'timeout');
      else {if (mode === 'identity') user = 'OTHER'; this.fn(mode === 'login' ? '<html>login.jsp</html>' : {ok: true});}
      return this;
    }};
  }};
  const context = vm.createContext({window: {jQuery: $}, location: {origin: 'https://cwzh.ustc.edu.cn', pathname: '/YBX/main.jsp'}, performance});
  vm.runInContext(source, context);
  const api = context.createReimbursementHttp('TEST001', {prefix: 'TEST', requests, timeout: 15000, allowed: ['definition', 'query', 'binding']});
  return {api, requests, calls, data, create:options=>context.createReimbursementHttp('TEST001',options),values: vm.runInContext('ReimbursementHttpValues', context)};
}
(async () => {
  const f = fixture();
  const copy = f.api.contextCopy(); copy.set('61-serialno', 'new');
  assert.equal(copy.get('61-serialno'), 'new'); assert.equal(f.data['WF_YBX$61-SERIALNO'], 'original');
  await f.api.post('loadDefinition.action?type=W', {winno: '70'});
  assert.equal(f.requests[0].status, 'received'); assert.equal(f.calls.length, 1);
  for (const url of ['https://cwzh.ustc.edu.cn/YBX/loadDefinition.action?type=W', '//evil', 'loadDefinition.action?type=W&extra=1', 'commonUpdate_responseButtonEvent.action', 'commonQuery_doQuery.action?funcno=1&projid=OTHER'])
    await assert.rejects(() => f.api.post(url, {}), /ENDPOINT_NOT_ALLOWED/);
  assert.equal(f.calls.length, 1);
  const long=f.create({prefix:'LONG',requests:[],timeout:120000,allowed:['invoiceOcr']});
  await long.post('thirdPartyInterface.action',{interfaceName:'pdfsign2'});
  assert.equal(f.calls.at(-1).timeout,120000);
  for(const allowed of [['definition'],['invoiceOcr','query']])assert.throws(()=>f.create({prefix:'BAD',requests:[],timeout:120000,allowed}));
  for (const mode of ['timeout', 'identity', 'login']) {
    const g = fixture(mode);
    await assert.rejects(() => g.api.post('loadDefinition.action?type=W', {}));
    assert.equal(g.calls.length, 1); // No retry after an unknown request.
    if (mode === 'timeout') assert.equal(g.requests[0].status, 'unknown');
  }
  assert.equal(f.values.moneyCents('12,345.67'), 1234567n);
  assert.equal(f.values.moneyCents('9007199254740993.01'), 900719925474099301n);
  for (const amount of ['1,2,3', 'NaN', '1e4', '-1', '1.001', '', 1]) assert.throws(() => f.values.moneyCents(amount));
  assert.equal(f.values.formRows('([{ "value": "ok" }]);')[0].value, 'ok');
  for (const raw of ['[(()=>42)()]', '{}', '[null]', 'undefined']) assert.throws(() => f.values.formRows(raw));
  assert(!/\.click\s*\(|\.trigger\s*\(|dispatchEvent|eval\s*\(/.test(source));
  console.log('shared HTTP: scoped endpoints, isolated context, identity drift, unknown without retry, exact cents and data-only parsing passed');
})().catch(e => {console.error(e); process.exitCode = 1;});
