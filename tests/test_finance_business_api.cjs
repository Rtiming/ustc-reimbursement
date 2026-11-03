const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const runtime = fs.readFileSync(require('node:path').join(__dirname, '../tools/finance_http.js'), 'utf8');
const source = runtime + '\n' + fs.readFileSync(require('node:path').join(__dirname, '../tools/finance_business_api.js'), 'utf8');
const columns = ['t.serial_no','t.REQ_INFO','t.status','t.status_name','t.total_amt','t.abst'];
async function run(mode = 'ok') {
  const sent = [], data = {'0-USERINFO.USERID':'USER01','0-USERINFO.USERNAME':'Test'};
  const context = {performance, Date, location: {origin:'https://cwzh.ustc.edu.cn', pathname:'/YBX/main.jsp'}};
  const $ = {getProjID:()=> 'WF_YBX',UC:{bindData:()=>mode==='identity'?'OTHER':'USER01',setData:(k,v)=>{data[k]=v;},userData:data},ajax:options=>{
    sent.push(options); let value;
    if(options.url.startsWith('loadDefinition')) value={wins:[],funcs:[{funcno:101,tablenames:'(@{ybx_query_pc_package.wf_query_main_list_v1:#99-year#})',multifields:'t.serial_no',colModel:columns.map(name=>({name}))}]};
    else {
      const page=options.data.page, begin=(page-1)*100, length=page===1?100:1;
      value={page,total:2,records:101,rows:Array.from({length},(_,i)=>({cell:[String(1000+begin+i),'USER01-TEST','10','已预约待投递','1,200.00','TEST']}))};
      if(mode==='empty')value={page:0,total:0,records:0,rows:[]};
      if(mode==='duplicate'&&page===2)value.rows[0].cell[0]='1000';
      if(mode==='truncated'&&page===2)value.rows=[];
      if(mode==='drift'&&page===2)value.records=102;
      if(mode==='other_owner'&&page===2)value.rows[0].cell[1]='OTHER-TEST';
    }
    return {done(fn){this.fn=fn;return this;},fail(fn){if(mode==='network'&&sent.length===3)fn(null,'timeout');else this.fn(value);return this;}};
  }};
  context.window={jQuery:$}; vm.createContext(context);
  const result=await vm.runInContext(source+'\nreimbursementBusinessApi({case_id:"synthetic",expected_userid:"USER01",action:"bills-list"})',context);
  return {result,sent,data};
}
(async()=>{
  const {result,sent,data}=await run();assert.equal(result.complete,true);assert.equal(result.rows.length,101);assert.equal(sent.length,3);
  assert(sent.every(o=>o.timeout===30000));
  const query=JSON.parse(sent[1].data.userData);
  assert.equal(query['WF_YBX$99-YEAR'],'');assert.equal(query['WF_YBX$99-T.CX_TYPE'],'USER');assert.equal(result.system_write,false);
  assert.equal(Object.keys(data).length,2);assert.equal((await run('empty')).result.total_records,0);
  for(const mode of ['identity','duplicate','truncated','drift','other_owner','network'])await assert.rejects(()=>run(mode));
  assert(!/\.click\s*\(|\.trigger\s*\(|dispatchEvent|runInstance|\.page\.open/.test(source));
  console.log('business API: complete pagination, account binding, drift, truncation and unknown request checks passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
