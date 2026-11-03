const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {webcrypto,createHash}=require('node:crypto');
const source=['finance_http.js','finance_native_protocol.js','finance_expenses_api.js'].map(n=>fs.readFileSync(__dirname+'/../tools/'+n,'utf8')).join('\n');
const canonical=v=>Array.isArray(v)?v.map(canonical):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;
const columns=['t.t_desc','t.amt','t.bu_code','t.t_id','t.uni_prj_code','t.serial_no','t.activity_name','t.bu_code_balance','t.jj_subj','t.jj_subjname','t.ord','t.s_ord','t.tlevel','t.tparent','t.tleaf','t.texpanded','t.tloaded'];
async function run(mode='ok',write=false) {
  const save={btnguid:'SAVE',check_func:'',procs:['`$'+'a'.repeat(32)+':#307-ALLTABLEDATA#;#307-dfamt#;#61-serialno#;#0-userinfo.userid#']};
  const definition={wins:[{winno:216,beforeLoadProcs:[],func_btns:[save]}],funcs:[],funcMap:{},actMap:{}};
  const hash=createHash('sha256').update(JSON.stringify(canonical(definition))).digest('hex');
  const make=(id,level,parent,amount,project='',budget='')=>Object.fromEntries(columns.map(k=>[k,({
    't.t_desc':'Label','t.amt':amount,'t.bu_code':budget,'t.uni_prj_code':project,'t.serial_no':'123','t.bu_code_balance':'20.00',
    't.jj_subj':'30201','t.ord':id,'t.s_ord':'R','t.tlevel':level,'t.tparent':parent,'t.tleaf':String(level==='2'),
    't.texpanded':'true','t.tloaded':'true'})[k]??'']));
  const rows=[make('R','0','','10.00'),make('P','1','R','10.00'),make('A','2','P','10.00','F1','B1'),make('B','2','P','0.00','F2','B2')];
  const funds=[{code:'F1',amount:'4.00'},{code:'F2',amount:'6.00'}];
  const allocations=[{project:'F1',subject:'30201',budget:'B1',amount:'4.00'},{project:'F2',subject:'30201',budget:'B2',amount:'6.00'}];
  const sent=[],uc={'0-USERINFO.USERID':'TEST001','WF_YBX$61-SERIALNO':'OTHER','WF_YBX$307-ALLTABLEDATA':'UNSAVED'};
  const original=JSON.stringify(uc);
  const $={getProjID:()=> 'WF_YBX',UC:{userData:uc,bindData:()=> 'TEST001'},ajax:o=>{
    sent.push(o);let reply=definition;
    if(o.url==='commonUpdate_responseButtonEvent.action') {
      const data=JSON.parse(o.data.userContext);assert.equal(data['WF_YBX$61-SERIALNO'],'123');assert.equal(data['WF_YBX$307-DFAMT'],'0');
      // 独立手写的学校wire格式，避免用被测序列化器生成期望。
      assert.equal(data['WF_YBX$307-ALLTABLEDATA'],
        'Label&10.00&&&&123&&20.00&30201&&R&R&0&&false&true&true;'+
        'Label&10.00&&&&123&&20.00&30201&&P&R&1&R&false&true&true;'+
        'Label&4.00&B1&&F1&123&&20.00&30201&&A&R&2&P&true&true&true;'+
        'Label&6.00&B2&&F2&123&&20.00&30201&&B&R&2&P&true&true&true');
      if(mode==='timeout')return {done(){return this;},fail(fn){fn({status:0},'timeout');return this;}};
      reply=mode==='judge'?'judge:confirm':mode==='error'?'error:reject':'pass';
    }
    return {done(fn){this.fn=fn;return this;},fail(){this.fn(reply);return this;}};
  }};
  const c={case_id:'fixture',serial:'123',expected_amount:'10.00',expected_userid:'TEST001',contracts:[{wireColumns:columns}],
    expenses_contract:{definition_sha256:hash,save_button:'SAVE'},funding:funds,expense_allocations:allocations,budget_allocation_mode:'manual'};
  if(mode==='definition')save.pre_check=true;
  if(mode==='duplicate')rows.push({...rows[2]});
  if(mode==='foreign')rows[2]['t.serial_no']='999';
  if(mode==='orphan')rows[2]['t.tparent']='MISSING';
  if(mode==='unknown_project')rows[2]['t.uni_prj_code']='OTHER';
  if(mode==='balance')rows[3]['t.bu_code_balance']='1.00';
  if(mode==='partial')allocations.pop();
  if(mode==='duplicate_plan')allocations[1]={...allocations[0]};
  if(mode==='subject')rows[0]['t.amt']='11.00';
  if(mode==='fund_total')funds[0].amount='5.00';
  if(mode==='invoice_level')c.funding_policy={invoice_level_required:true};
  if(mode==='markup')rows[1]['t.activity_name']='<input value="bad">';
  if(mode==='missing')delete rows[1]['t.s_ord'];
  if(mode==='noop'){rows[2]['t.amt']='4.00';rows[3]['t.amt']='6.00';}
  if(mode==='default'){c.budget_allocation_mode='system_default_single_funding';c.funding=[{code:'F1',amount:'10.00'}];c.expense_allocations=[];rows.pop();}
  const context=vm.createContext({performance,TextEncoder,crypto:webcrypto,window:{jQuery:$},location:{origin:'https://cwzh.ustc.edu.cn',pathname:'/YBX/main.jsp'},
    reimbursementBusinessApi:async()=>({bill:{'t.status':mode==='approved'?'10':'-99','t.lc_finish':mode==='approved'?'T':'F'},
      sections:[{funcno:'307',kind:'grid',complete:mode!=='incomplete',rows}],requests:[]})});
  try {
    const plan=await vm.runInContext(source+'\nreimbursementExpensesPrepare('+JSON.stringify(c)+')',context);
    if(!write)return plan;
    if(mode==='drift')plan.original.A['t.amt']='9.00';
    const result=await vm.runInContext('reimbursementExpensesSave('+JSON.stringify({...c,execute:mode!=='no_intent',expected_plan:plan})+')',context);
    assert.equal(sent.filter(o=>o.url==='commonUpdate_responseButtonEvent.action').length,['drift','no_intent','noop','default'].includes(mode)?0:1);
    return result;
  }finally{assert.equal(JSON.stringify(uc),original);}
}
(async()=>{
  const plan=await run();assert.equal(plan.changes.A['t.amt'],'4.00');assert.equal(plan.changes.B['t.amt'],'6.00');assert.equal(plan.system_write,false);
  for(const mode of ['definition','duplicate','foreign','orphan','unknown_project','balance','partial','duplicate_plan','subject','fund_total','invoice_level','markup','missing','incomplete','approved'])await assert.rejects(()=>run(mode));
  assert.equal((await run('ok',true)).state,'saved_independent_readback_required');
  for(const mode of ['timeout','judge','error'])assert.equal((await run(mode,true)).state,'unknown_save_readback_required');
  for(const mode of ['drift','no_intent'])assert.equal((await run(mode,true)).state,'rejected_before_save');
  for(const mode of ['noop','default'])assert.equal((await run(mode,true)).state,'already_matches_readback_required');
  assert(!/\.click\s*\(|\.trigger\s*\(|dispatchEvent|eval\s*\(/.test(source));
  console.log('expenses HTTP: complete tree, exact wire, totals, balance, single save and unknown recovery passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
