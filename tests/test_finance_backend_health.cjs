const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=['finance_http.js','finance_backend_health.js'].map(n=>fs.readFileSync(__dirname+'/../tools/'+n,'utf8')).join('\n');
async function check(mode){
  let calls=0,user=mode==='other'?'OTHER':'TEST001',captured;
  const context={performance,location:{origin:'https://cwzh.ustc.edu.cn',pathname:mode.startsWith('portal')?'/WFManager/home2.jsp':'/YBX/main.jsp',href:'https://cwzh.ustc.edu.cn/WFManager/home2.jsp'},
    window:{jQuery:{getProjID:()=> 'WF_YBX',UC:{bindData:key=>key==='#61-serialno#'?'123':user}}},
    document:{getElementById:id=>id==='YBX'&&mode==='portal-ready'?{getAttribute:()=> 'WINOPEN/YBX/main2.jsp?context=SECRET_TICKET&pId=YBX'}:null},
    URL,AbortController,setTimeout,clearTimeout,
    };
  context.window.jQuery.ajax=options=>{
      calls++;captured=options;
      let value={wins:[{winno:mode==='definition'?999:70}],funcs:[{funcno:101,multifields:'t.serial_no',colModel:[{name:'t.REQ_INFO'}]}]};
      if(mode==='identity')user='OTHER';
      if(mode==='multiple')value=[value,value];
      return {done(fn){this.fn=fn;return this;},fail(fn){
        if(['404','network','html'].includes(mode))fn({status:mode==='404'?404:0},mode==='html'?'parsererror':'error');
        else this.fn(value);return this;}};
    };
  const result=await vm.runInNewContext(source+'\nreimbursementBackendHealth("TEST001")',context);
  assert.equal(result.system_write,false);assert.equal(result.navigation_requested,false);assert.equal(result.retry_requested,false);
  assert(!JSON.stringify(result).includes('SECRET'));assert(!JSON.stringify(result).includes('secret'));
  assert.equal(calls,mode.startsWith('portal')||mode==='other'?0:1);
  if(captured){assert.equal(captured.url,'loadDefinition.action?type=W');assert.equal(captured.data.winno,'70');assert.equal(captured.data.projid,'WF_YBX');assert.equal(captured.timeout,5000);}
  return result;
}
(async()=>{
  assert.equal((await check('ok')).state,'native_backend_ready');
  assert.equal((await check('ok')).identity_verified,true);
  assert.equal((await check('wrapped')).state,'native_backend_ready');
  assert.equal((await check('multiple')).state,'backend_contract_changed');
  for(const [mode,state]of Object.entries({'404':'native_backend_unavailable',html:'backend_unconfirmed',network:'backend_unconfirmed',other:'identity_mismatch',identity:'identity_changed',definition:'backend_contract_changed','portal-ready':'entry_available_not_opened','portal-missing':'entry_contract_unavailable'})){
    const r=await check(mode);assert.equal(r.state,state);assert.equal(r.ok,false);
  }
  assert(!/\.click\s*\(|\.trigger\s*\(|location\.(assign|replace)|eval\s*\(/.test(source));
  console.log('backend health: cached identity != live backend, fixed single read, entry drift, no ticket disclosure or navigation passed');
})().catch(e=>{console.error(e);process.exitCode=1});
