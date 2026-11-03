const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../tools/finance_split_api.js'),'utf8');
const ctx=vm.createContext({ReimbursementHttpValues:{moneyCents:v=>BigInt(v.replace('.',''))}});vm.runInContext(source,ctx);
const guid='A'.repeat(32),keys=['30211!P!WC0000000001!P!A00001','30211!P!WW0000000002!P!A00001'];
const c={serial:'123',group:'交通运输服务',plan:{policy:{subject:'30211',project:'WC0000000001',budget:'A00001',person_cap:'450.00'},funding:[{code:'WC0000000001'},{code:'WW0000000002'}],people:[{group:'交通运输服务',invoice_guid:guid,invoice_amount:'500.00',wc_amount:'450.00'}]}};
const row={'t.serial_no':'123','t.inner_ord':'1','t.tk_guid':guid,'t.amt':'500.00','t.invoice_type':'国内机票款','t.sj_bf':'30211-WW0000000002-A00001-500.00'};
const call=(p,rows,k=keys,o=[guid],g=[guid])=>vm.runInContext('reimbursementSplitAmounts('+[p,rows,k,o,g].map(x=>JSON.stringify(x)).join(',')+')',ctx);
assert.deepEqual(JSON.parse(JSON.stringify(call(c,[row])[0].amounts)),['450.00','50.00']);
for(const mode of ['guid','serial','face','ineligible','unknown_binding','cap','missing','duplicate','fund']){
 const p=JSON.parse(JSON.stringify(c)),r={...row};let rows=[r],owned=[guid],k=keys;
 if(mode==='guid')owned=['B'.repeat(32)];if(mode==='serial')r['t.serial_no']='999';if(mode==='face')r['t.amt']='600.00';
 if(mode==='ineligible')r['t.invoice_type']='行李费';if(mode==='unknown_binding')r['t.sj_bf']='30211-WX0000000003-A00001-500.00';
 if(mode==='cap')p.plan.policy.person_cap='400.00';if(mode==='missing')rows=[];if(mode==='duplicate')rows=[r,r];if(mode==='fund')k=[keys[0],keys[0]];
 assert.throws(()=>call(p,rows,k,owned),undefined,mode);
}
assert(!/\.click\s*\(|\.trigger\s*\(|dispatchEvent|eval\s*\(/.test(source));
console.log('split HTTP: exact owned group, source amounts, eligible travel, explicit two-fund keys, cap and third-binding protection passed');
