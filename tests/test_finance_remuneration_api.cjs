const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),code=fs.readFileSync(require('node:path').join(__dirname,'../tools/finance_remuneration_api.js'),'utf8');
const ctx=vm.createContext({});vm.runInContext(code,ctx);
const c={student_id:'SA00000001',activity:{approved_people:{SA00000001:{name:'合成人员',amount:'100.00'}}}};
const card={name:'合成人员',bankno:'TESTBANK',bankname:'合成银行',card_no:'000000****0000',other_information:'native-private-bank-binding'};
const person={unino:'SA00000001 ',name:'合成人员 ',state:'学生  ',allowedFlag:'T',cardInfo:[card]};
const call=data=>vm.runInContext('reimbursementRemunerationSelectPerson('+JSON.stringify(c)+','+JSON.stringify(data)+')',ctx);
assert.equal(call([person]).card.card_no,card.card_no);
for(const mode of ['duplicate','name','state','ineligible','cash','card_owner','bad_mask','empty']){
 const p=JSON.parse(JSON.stringify(person));let rows=[p];
 if(mode==='duplicate')rows.push(p);if(mode==='name')p.name='另一个人';if(mode==='state')p.state='校外';if(mode==='ineligible')p.allowedFlag='F';
 if(mode==='cash')p.cardInfo[0].bankno='0';if(mode==='card_owner')p.cardInfo[0].name='他人';if(mode==='bad_mask')p.cardInfo[0].card_no='arbitrary';if(mode==='empty')p.cardInfo=[];
 assert.throws(()=>call(rows),undefined,mode);
}
assert(!/\.click\s*\(|\.trigger\s*\(|dispatchEvent|eval\s*\(/.test(code));
console.log('remuneration HTTP: padded native identity, student eligibility, exact bank owner, native masked bank binding and cash isolation passed');

const event=(raw,context={})=>vm.runInContext('reimbursementRemunerationApplyFieldEvent('+JSON.stringify(raw)+',target)',Object.assign(ctx,{target:context}));
const target={};event('passlocal(61-taxable:N;61-cj_fcnt_controlN;61-REMARK_INIT:说明:事由;0-abst_isstd:T)passlocal(277-pj_state:)passpasslocal(0-cjfix_change_reminder:说明。)local(0-cjfix_change_single_remark:)',target);
assert.equal(target['WF_YBX$61-TAXABLE'],'N');assert.equal(target['0-ABST_ISSTD'],'T');assert.equal(target['WF_YBX$277-PJ_STATE'],'');assert(!Object.hasOwn(target,'WF_YBX$61-CJ_FCNT_CONTROLN'));
for(const raw of ['error:fail','passlocal(61-taxable:N)judge:confirm','passlocal(61-taxable:N;0-USERID:OTHER)','passlocal(61-taxable:N;277-other:bad:syntax)','passlocal(61-taxable:N;61-taxable:Y)','passlocal(61-unknownNoColon)']){
 const untouched={};assert.throws(()=>event(raw,untouched));assert.deepEqual(untouched,{});
}
console.log('salary setup event: observed concatenated pass blocks, explicit native-ignored hints, unknown keys/status and atomic rejection passed');

const setupRaw='passlocal(61-select_std:;0-CJ_SELECT_OTH_STD:;61-cj_sel_standard:;61-premark_show:说明:事由;0-cardno_is_readonly:;0-dblrorderstrs:)';
assert.throws(()=>event(setupRaw));
ctx.target={};vm.runInContext('reimbursementRemunerationApplyFieldEvent('+JSON.stringify(setupRaw)+',target,true)',ctx);
assert.equal(ctx.target['0-CARDNO_IS_READONLY'],'');assert(!Object.hasOwn(ctx.target,'WF_YBX$61-PREMARK_SHOW'));

vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../tools/finance_native_form.js'),'utf8'),ctx);
const fields=['unino','amt','premark','fcnt','work_hours','work_hours2'].map(n=>({fieldname:'dd.'+n,nullable:false}));
const form={funcno:'282',groupnamefrom:'',formFields:fields,formhtml:'<tr groupname="0"><td>'+fields.map(f=>'#'+f.fieldname+'#').join(' ')+'</td></tr>'};
const config={'61-std_version':'V2','61-otherinfo_json':JSON.stringify([{col_name:'fcnt',netin:'N',nullFlag:'Y',enum_list:'',format:'S'},{col_name:'work_hours',netin:'N',nullFlag:'Y',enum_list:'',format:'E'}]),'61-gznr_show':'T','61-abst_isstd':'F','61-CJFZEXFCNT_NETIN':'N'};
ctx.ruleForm=form;ctx.ruleConfig=config;ctx.ruleGet=k=>config[k];
const visibility=()=>vm.runInContext('reimbursementRemunerationPersonRules(ruleForm,ruleGet)',ctx);
let profile=visibility();assert.deepEqual(Array.from(profile.visible),['dd.amt','dd.premark','dd.unino']);
ctx.profile=profile;ctx.values={'282-dd.unino':'SA00000001','282-dd.amt':'10.00','282-dd.premark':'合成劳务'};
vm.runInContext('ReimbursementNativeForm.validate(profile.form,new Set(profile.visible),k=>values[k],["dd.unino","dd.amt","dd.premark"])',ctx);
config['61-UPD_WORK_HOURS']='Y';assert(!visibility().visible.includes('dd.work_hours2'));
config['61-std_version']='unknown';assert.throws(visibility);config['61-std_version']='V2';
config['61-otherinfo_json']=JSON.stringify([{col_name:'work_hours',netin:'Y',nullFlag:'N',enum_list:'',format:'E'}]);profile=visibility();assert(profile.visible.includes('dd.work_hours2'));assert(!profile.visible.includes('dd.work_hours'));
config['61-otherinfo_json']=JSON.stringify([{col_name:'fcnt',netin:'Y',nullFlag:'N',enum_list:'一!P!二',format:'E'}]);assert.throws(visibility);
console.log('student dynamic form: native V2 visibility, required hours only when shown, unknown rules and enum source stop passed');

ctx.ReimbursementHttpValues={moneyCents:v=>{const s=String(v).replaceAll(',','');if(!/^\d+(?:\.\d{1,2})?$/.test(s))throw Error('money');const [a,b='']=s.split('.');return BigInt(a)*100n+BigInt(b.padEnd(2,'0'));}};
const payrollConfig={expected_amount:'100.00',activity:{period_start:'2026-05-01',period_end:'2026-05-31',approved_people:{SA00000001:{name:'合成人员',amount:'100.00'}}}};
const salary={complete:true,bill:{'t.bx_type':'8'},original:{'dd.state_ybx':'学生人员一般薪酬','dd.state':'学生','dd.abst':'学生其他劳务','dd.ff_startdate':'2026-05-01','dd.ff_enddate':'2026-05-31','dd.hastax':'T'},rows:[{'t.unino':'SA00000001','t.name':'合成人员','t.AMT':'100.00','t.premark':'合成劳务'}]};
function preview(mode){
 const values=[['序号','工号','姓名','人员类型','支付方式','卡号','户名','发放事由','金额'],['1','SA00000001','合成人员','学生','转卡','0000****0000','合成人员','合成劳务','100.00']];
 const copy=JSON.parse(JSON.stringify(salary));if(mode==='amount')values[1][8]='101.00';if(mode==='identity')values[1][1]='SA00000002';if(mode==='duplicate')values.push([...values[1]]);if(mode==='reason')values[1][7]='其他';if(mode==='period')copy.original['dd.ff_enddate']='2026-06-01';
 const doc={querySelectorAll:q=>q==='table'?[{rows:values.map(r=>({cells:r.map(textContent=>({textContent}))}))}]:['N','Y'].map(v=>({checked:v===(mode==='extra'?'Y':'N'),getAttribute:()=>v}))};
 return ctx.reimbursementRemunerationPreview(doc,copy,payrollConfig);
}
assert.equal(preview().rows.length,1);for(const mode of ['amount','identity','duplicate','reason','period','extra'])assert.throws(()=>preview(mode),undefined,mode);
const amounts={'22':['AMOUNT'],'24':['AMOUNT'],'285':['AMOUNT'],'456':['AMOUNT'],'994':['AMOUNT'],'1791':['AMOUNT'],'2212':['CASHAMOUNT','HKAMOUNT']};
const payment={ok:true,complete:true,domain:'payments',sections:[...Object.entries(amounts).map(([funcno,keys])=>({funcno,kind:'form',rows:[Object.fromEntries(keys.map(k=>[k,'']))]})),{funcno:'2225',kind:'form',rows:[]}]};
ctx.reimbursementRemunerationNoExtraPayments(payment);
for(const[no,keys]of Object.entries(amounts))for(const key of keys){const bad=JSON.parse(JSON.stringify(payment));bad.sections.find(s=>s.funcno===no).rows[0][key]='1.00';assert.throws(()=>ctx.reimbursementRemunerationNoExtraPayments(bad));}
for(const change of [p=>p.sections.shift(),p=>delete p.sections[0].rows[0].AMOUNT,p=>p.sections.at(-1).rows.push({})]){const bad=JSON.parse(JSON.stringify(payment));change(bad);assert.throws(()=>ctx.reimbursementRemunerationNoExtraPayments(bad));}
console.log('payroll preview and additional payments: exact people, period, transfer mode, native uppercase amounts and missing domains fail closed');
