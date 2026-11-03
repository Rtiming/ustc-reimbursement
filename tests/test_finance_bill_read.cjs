const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const runtime = fs.readFileSync(require('node:path').join(__dirname, '../tools/finance_http.js'), 'utf8');
const source = runtime + '\n' + fs.readFileSync(path.join(__dirname, '../tools/finance_preview_api.js'), 'utf8') + '\n' + fs.readFileSync(path.join(__dirname, '../tools/finance_business_api.js'), 'utf8');
const contracts = JSON.parse(fs.readFileSync(path.join(__dirname, '../data/finance_business_contracts.json'))).domains;
const columns = ['t.serial_no','t.REQ_INFO','t.status','t.status_name','t.total_amt','t.abst','t.node_id','t.wf_name'];
async function run(domain, mode) {
  const sent=[], data={'0-USERINFO.USERID':'TEST001','0-USERINFO.USERNAME':'Test','WF_YBX$61-SERIALNO':'OTHER_BILL','WF_YBX$61-NODE_ID':'999','WF_YBX$61-WF_NAME':'OTHER_WORKFLOW'};
  const $={getProjID:()=> 'WF_YBX',UC:{bindData:()=> 'TEST001',userData:data},ajax:o=>{
    sent.push(o);let result;
    if(o.url==='loadDefinition.action?type=Q') {
      assert.equal(o.data.funcNo,contracts[domain][0].funcno);
      const names=contracts[domain][0].queryColumns.slice();
      if(mode==='query_drift')names.splice(5,1);
      result=[{funcno:contracts[domain][0].funcno,colModel:names.map(name=>({name}))}];
    } else if(o.url.startsWith('loadDefinition')) {
      result=o.data.winno==='41'?{funcMap:{'41':{type:'C',url:'Customize/customPrint/bx_frame_print.jsp'}}}:o.data.winno==='70'?{wins:[],funcs:[{funcno:101,tablenames:'(@{ybx_query_pc_package.wf_query_main_list_v1:#99-year#})',multifields:'t.serial_no',colModel:columns.map(name=>({name}))}]}:
        {wins:[],funcs:contracts[domain].map(c=>({...c,bindSql:mode==='definition_drift'?'changed':c.bindSql,prjfields:c.wireColumns?.join(','),formFields:c.emptyDefaults?.map(name=>({fieldname:'t.'+name,initdata:''})),colModel:c.columns?.map(name=>({name}))}))};
    } else if(o.url.includes('funcno=101&')) {
      const q=JSON.parse(o.data.userData);assert.equal(q['WF_YBX$99-T.SERIAL_NO'],'10001');
      const noWorkflow=['draft_without_workflow','submitted_without_workflow'].includes(mode);
      const legacy=mode==='legacy_paid_without_workflow';
      result={records:1,total:1,page:1,rows:[{cell:['10001','TEST001-Test',legacy?'30':mode==='submitted_without_workflow'?'10':'-99',legacy?'审批完成-支付完成':'draft',mode==='amount'?'121':'120','Test',noWorkflow?'':'508',legacy||noWorkflow||mode==='workflow_missing'?'':'WF_TEST']}]};
    } else if(o.url==='ybxPrint_yb4PrintHtml.action')result='Preview';
    else if(o.url==='common_bindSQLDataBackend.action') {
      assert(o.data.cvstr.includes('#61-serialno#::10001'));assert(!o.data.cvstr.includes('OTHER_BILL'));
      assert(!o.data.cvstr.includes('OTHER_WORKFLOW'));
      result=mode==='executable'?'[(()=>{throw Error("must not execute")})()]':JSON.stringify([{'t.serial_no':'10001','t.abst':'Test','t.amount':'120'}]);
    } else {
      if(['invoices','recognition'].includes(domain)){if(domain==='invoices')assert.equal(JSON.parse(o.data.userData)['WF_YBX$61-BX_JBXX_BX_TYPE'],'1');else assert.equal(JSON.parse(o.data.userData)['WF_YBX$113-T.TK_FILECODE'],'');}else assert.equal(JSON.parse(o.data.userData)['WF_YBX$61-SERIALNO'],'10001');
      const contract=contracts[domain].find(c=>o.url.includes('funcno='+c.funcno+'&'));
      if(domain==='invoices'){assert.equal(o.data.sidx,'t.tk_guid');assert.equal(o.data.sord,'asc');}
      if(domain==='funding')assert.equal(JSON.parse(o.data.userData)['WF_YBX$61-IGNORE_ZERO'],'N');
      if(domain==='submission')assert.equal(JSON.parse(o.data.userData)['WF_YBX$61-WF_NAME'],mode==='legacy_paid_without_workflow'?'':'WF_TEST');
      const nonempty=['nonempty_tree','nonempty_remuneration','short_remuneration','nonempty_used','wrong_used_serial','duplicate_used'].includes(mode);
      if(domain==='used_invoices'){const q=JSON.parse(o.data.userData);assert.equal(q['WF_YBX$535-T.SERIAL_NO'],'10001');assert.equal(q['WF_YBX$535-T.BX_TYPE'],'-999');assert.equal(q['WF_YBX$535-T.TK_NO'],'');}
      const row=(contract.queryColumns||contract.wireColumns)?.map(c=>c==='t.serial_no'?(mode==='wrong_used_serial'?'99999':'10001'):c==='t.tk_guid'?'A'.repeat(32):c==='t.AMT'?'120.00':c==='t.after_income'?'110.00':'value');
      if(mode==='short_remuneration')row.pop();
      result={records:nonempty?1:0,total:mode==='truncated'?2:nonempty||!contract.needcount?1:0,page:1,rows:nonempty?[{cell:row}]:[]};
      if(mode==='duplicate_used')result={records:2,total:1,page:1,rows:[{cell:row},{cell:row}]};
      if(domain==='invoices'&&['paginated_pool','duplicate_pool','pool_drift'].includes(mode)){
        const page=Number(o.data.page),begin=(page-1)*100,length=Math.min(100,201-begin);
        result={records:mode==='pool_drift'&&page===2?202:201,total:3,page,rows:Array.from({length},(_,i)=>({cell:contract.columns.map(c=>c==='t.tk_guid'?(mode==='duplicate_pool'&&page===3?'0':String(begin+i)).padStart(32,'0'):'value')}))};
      }
    }
    return{done(fn){this.fn=fn;return this;},fail(fn){if(mode==='network'&&sent.length===4)fn(null,'timeout');else this.fn(result);return this;}};
  }};
  class Parser {parseFromString(){return{body:{textContent:'报销单号：10001'},querySelectorAll:()=>[{getAttribute:()=>"pageOpen_v1('选择流程参数','WORKFLOW_V1&ustc_xmysr&1&"+'A'.repeat(32)+"&QT&N&A&')"}]};}}
  const ctx={performance,Date,TextEncoder,crypto:require('node:crypto').webcrypto,DOMParser:Parser,location:{origin:'https://cwzh.ustc.edu.cn',pathname:'/YBX/main.jsp'},window:{jQuery:$}};
  vm.createContext(ctx);
  const c={case_id:'synthetic',expected_userid:'TEST001',serial:'10001',expected_amount:'120.00',action:domain==='invoices'?'invoices-list':domain==='recognition'?'recognition-list':'bill-read',domain,contracts:contracts[domain]};
  const result=await vm.runInContext(source+'\nreimbursementBusinessApi('+JSON.stringify(c)+')',ctx);
  assert.equal(data['WF_YBX$61-SERIALNO'],'OTHER_BILL');
  assert.equal(data['WF_YBX$61-WF_NAME'],'OTHER_WORKFLOW');
  return{result,sent};
}
(async()=>{
  for(const domain of Object.keys(contracts)) {
    const {result,sent}=await run(domain);assert(result.complete);if(['invoices','recognition'].includes(domain))assert.equal(result.rows.length,0);else assert.equal(result.sections.length,contracts[domain].length);
    assert.equal(result.system_write,false);assert.equal(result.page_context_modified,false);
    assert(sent.every(o=>o.timeout===15000));
    assert(sent.every(o=>!o.url.includes('Update')&&!o.url.includes('callJsBlock')));
  }
  for(const mode of ['definition_drift','amount','executable','network'])await assert.rejects(()=>run('basic',mode));
  await assert.rejects(()=>run('categories','truncated'));
  const tree=await run('categories','nonempty_tree');assert.equal(tree.result.sections[0].rows.length,1);assert.equal(tree.result.sections[0].rows[0]['t.serial_no'],'10001');
  await assert.rejects(()=>run('submission','workflow_missing'));
  await assert.rejects(()=>run('submission','submitted_without_workflow'));
  const draft=await run('submission','draft_without_workflow');
  assert.equal(draft.result.workflow_state,'not_started');
  assert.deepEqual(JSON.parse(JSON.stringify(draft.result.sections.map(s=>s.funcno))),['2040']);
  assert.deepEqual(JSON.parse(JSON.stringify(draft.result.not_applicable_sections.map(s=>s.funcno))),['109','495','1816']);
  assert(draft.sent.filter(o=>o.url==='common_bindSQLDataBackend.action').every(o=>!o.data.cvstr.includes('node_id')&&!o.data.cvstr.includes('wf_name')));
  const legacy=await run('submission','legacy_paid_without_workflow');
  assert.equal(legacy.result.complete,false);assert.equal(legacy.result.partial,true);
  assert.equal(legacy.result.workflow_state,'closed_missing_workflow_name');
  assert.deepEqual(JSON.parse(JSON.stringify(legacy.result.sections.map(s=>s.funcno))),['109','1816','2040']);
  assert.equal(legacy.result.unavailable_sections[0].funcno,'495');
  assert(legacy.sent.filter(o=>o.url==='common_bindSQLDataBackend.action').every(o=>!o.data.cvstr.includes('wf_name')));
  const reward=await run('remuneration','nonempty_remuneration');
  assert.equal(reward.result.sections[0].rows[0]['t.AMT'],'120.00');
  assert.equal(reward.result.sections[0].rows[0]['t.after_income'],'110.00');
  assert.equal(reward.result.sections[0].rows[0]['t.serial_no'],'10001');
  await assert.rejects(()=>run('remuneration','query_drift'),/QUERY_COLUMNS_CHANGED/);
  await assert.rejects(()=>run('remuneration','short_remuneration'),/ROW_COLUMNS/);
  const used=await run('used_invoices','nonempty_used');assert.equal(used.result.sections[0].rows[0]['t.tk_guid'],'A'.repeat(32));
  await assert.rejects(()=>run('used_invoices','wrong_used_serial'),/USED_INVOICE_BINDING/);
  await assert.rejects(()=>run('used_invoices','duplicate_used'),/USED_INVOICE_BINDING/);
  await assert.rejects(()=>run('used_invoices','query_drift'),/QUERY_COLUMNS_CHANGED/);
  const pool=await run('invoices','paginated_pool');assert.equal(pool.result.total_records,201);assert.equal(pool.result.rows.length,201);
  await assert.rejects(()=>run('invoices','duplicate_pool'),/POOL_DUPLICATE/);
  await assert.rejects(()=>run('invoices','pool_drift'),/SECTION_INCOMPLETE/);
  console.log('bill-read: fixed form/grid contracts, amount, drift, executable-response rejection, context isolation passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
