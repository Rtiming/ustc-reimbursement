const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const {webcrypto, createHash} = require('node:crypto');
const path = require('node:path');
const source = ['finance_http.js','finance_native_protocol.js','finance_basic_api.js']
  .map(p => fs.readFileSync(path.join(__dirname, '../tools', p), 'utf8')).join('\n');
const canonical = x => Array.isArray(x) ? x.map(canonical) : x && typeof x === 'object' ?
  Object.fromEntries(Object.keys(x).sort().map(k=>[k,canonical(x[k])])) : x;
async function run(mode, write = false) {
  const fields = ['abst','req_mobile','req_tele','is_have_changefee','td_point','lc_accepter','lc_accepter_name','cl_abst','untouched'];
  const form = {funcno:'60', typeMap:{}, formFields:fields.map(n=>({id:'t-'+n, fieldname:'t.'+n, nullable:!['abst'].includes(n),
    format:n==='req_mobile'?'cellphone':'', Events:n==='lc_accepter'?[{proc:'$'+'b'.repeat(32)+'|#60-t.lc_accepter#,#61-serialno#'}]:[]})),
    formhtml:fields.map(n=>'<tr groupname="'+(mode==='allowance'&&n.startsWith('lc_accepter')?'9':n==='cl_abst'?'8':'0')+'"><td>#t.'+n+'#</td></tr>').join('')};
  const save={btnguid:'SAVE',check_func:'',procs:['`$'+'c'.repeat(32)+':#60-t.abst#;#61-serialno#;#0-userinfo.userid#']};
  const definition={wins:[{winno:43,beforeLoadProcs:['`$'+'a'.repeat(32)+':#61-serialno#;#0-userinfo.userid#'],func_btns:[save]}],funcs:[form],funcMap:{},actMap:{}};
  const hash=createHash('sha256').update(JSON.stringify(canonical(definition))).digest('hex');
  if(mode==='drift')save.pre_compute=true;
  const bound=Object.fromEntries(fields.map(n=>[n.toUpperCase(),n==='abst'?'Old':n==='untouched'?'Preserve me':'']));
  if(mode==='missing')delete bound.UNTOUCHED;
  const sent=[],original={'0-USERINFO.USERID':'TEST001','WF_YBX$61-SERIALNO':'OTHER','WF_YBX$60-T.ABST':'UNSAVED'};
  const before=JSON.stringify(original);
  const $={getProjID:()=> 'WF_YBX',UC:{userData:original,bindData:()=> 'TEST001'},ajax:o=>{
    sent.push(o); let result;
    if(o.url.startsWith('loadDefinition'))result=definition;
    else if(o.url==='commonUpdate_responseButtonEvent.action'){
      if (o.data.cmd.includes('`$'+'c'.repeat(32))) {
        const ctx = JSON.parse(o.data.userContext);
        assert.equal(ctx['WF_YBX$60-T.UNTOUCHED'],'Preserve me');
        assert.equal(ctx['WF_YBX$61-SERIALNO'],'123');
        assert.equal(ctx['WF_YBX$60-T.ABST'],'New');
        assert.equal(sent.filter(x=>x.data.cmd?.includes('`$'+'c'.repeat(32))).length,1);
        if (mode==='timeout') return {done(){return this;},fail(fn){fn({},'timeout','timeout');return this;}};
        result=mode==='savejudge'?'judge:confirm':mode==='saveerror'?'error:rejected':'pass';
      } else {
        assert(o.data.cmd.includes('`$'+'a'.repeat(32)));
        result=mode==='judge'?'judge:confirm':mode==='context'?'pass;local(0-userinfo.userid:OTHER)':'pass;local(60-groupname:'+(mode==='hidden'?'0':'8')+')';
      }
    } else {
      assert.equal(o.url,'common_callProc.action');
      const c=JSON.parse(o.data.userData);assert.equal(c['WF_YBX$61-SERIALNO'],'123');
      assert.equal(c['WF_YBX$60-T.LC_ACCEPTER'],'100');
      const value=mode==='wrongid'?'Other':mode==='ambiguous'?'100/Teacher/Unit,200/Teacher/Unit':mode==='descriptor'?'100/Teacher/Unit':mode==='descriptor_wrongid'?'200/Teacher/Unit':mode==='empty_id'?'':'Teacher';
      result='passlocal(60-lc_value:'+value+';60-lc_field:lc_accepter_name)';
    }
    return{done(fn){this.fn=fn;return this;},fail(){this.fn(result);return this;}};
  }};
  const context=vm.createContext({performance,crypto:webcrypto,TextEncoder,window:{jQuery:$},location:{origin:'https://cwzh.ustc.edu.cn',pathname:'/YBX/main.jsp'},
    reimbursementBusinessApi:async()=>({sections:[{funcno:'60',rows:[bound]}],requests:[],bill:{'t.serial_no':'123','t.bx_type':mode==='allowance'?'2':'1','t.status':mode==='approved'?'10':'-99','t.lc_finish':mode==='approved'?'T':'F'}})});
  const c={case_id:'fixture',serial:'123',expected_userid:'TEST001',basic_contract:{definition_sha256:hash,save_button:'SAVE'},
    basic:{teacher_id:'100',teacher_name:'Teacher',fields:{'t-abst':'New','t-req_mobile':mode==='phone'?'123':'13800000000','t-is_have_changefee':'N','t-td_point':'退单地点','t-cl_abst':'Notes'}}};
  try {
    const plan=await vm.runInContext(source+'\nreimbursementBasicPrepare('+JSON.stringify(c)+')',context);
    if (!write) return plan;
    if(mode==='plan_drift')plan.original['t.untouched']='Changed';
    if(mode==='noop')for(const [k,v] of Object.entries(plan.changes))bound[k.slice(2).toUpperCase()]=v;
    const expected=mode==='noop'?await vm.runInContext('reimbursementBasicPrepare('+JSON.stringify(c)+')',context):plan;
    const result=await vm.runInContext('reimbursementBasicSave('+JSON.stringify({...c,execute:mode!=='no_intent',expected_plan:expected})+')',context);
    assert.equal(sent.filter(x=>x.data.cmd?.includes('`$'+'c'.repeat(32))).length,
      ['approved','plan_drift','no_intent','noop'].includes(mode)?0:1);
    return result;
  } finally {assert.equal(JSON.stringify(original),before);}
}
(async()=>{
  const r=await run(); assert.equal(r.complete,true);assert.equal(r.system_write,false);assert.equal(r.save_dispatched,false);
  assert.equal(r.original['t.untouched'],'Preserve me');assert(!Object.hasOwn(r.changes,'t.untouched'));assert.equal(r.changes['t.abst'],'New');
  for(const mode of ['drift','missing','judge','context','hidden','ambiguous','wrongid','descriptor_wrongid','empty_id','phone'])await assert.rejects(()=>run(mode));
  const saved=await run('ok',true);assert.equal(saved.save_dispatched,true);assert.equal(saved.requires_readback,true);
  assert.equal((await run('blankname',true)).teacher.id,'100');
  assert.equal((await run('descriptor',true)).teacher.department,'Unit');
  const allowance=await run('allowance',true);assert.equal(allowance.teacher.applicable,false);assert(!Object.hasOwn(allowance.prepared?.changes||allowance.changes,'t.lc_accepter'));
  for(const mode of ['timeout','savejudge','saveerror']){
    const failed=await run(mode,true);assert.equal(failed.state,'unknown_save_readback_required');assert.equal(failed.save_dispatched,true);
  }
  for(const mode of ['approved','plan_drift','no_intent'])assert.equal((await run(mode,true)).state,'rejected_before_save');
  assert.equal((await run('noop',true)).state,'already_matches_readback_required');
  assert(!/\.click\s*\(|\.trigger\s*\(|dispatchEvent|eval\s*\(/.test(source));
  console.log('basic HTTP: frozen intent, single save, no-op, no replay on timeout/rejection, preserved fields and isolated context passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
