const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const root=path.join(__dirname,'..');
const source=['finance_http.js','finance_business_api.js'].map(n=>fs.readFileSync(path.join(root,'tools',n),'utf8')).join('\n');
const domains=JSON.parse(fs.readFileSync(path.join(root,'data/finance_business_contracts.json'),'utf8')).domains;
async function run(mode='ok',history=false,serial=null){
 const contracts=history?domains.approval_history:domains.approval,filterNo=history?'719':'514';
 const sent=[],contract=contracts[0],context={performance,Date,location:{origin:'https://cwzh.ustc.edu.cn',pathname:'/YBX/main.jsp'}};
 const data={'0-USERINFO.USERID':'USER01'};
 const $={getProjID:()=> 'WF_YBX',UC:{userData:data,bindData:()=> 'USER01'},ajax:options=>{
  sent.push(options);let value;
  if(options.url.startsWith('loadDefinition'))value={wins:[],funcs:[{...contract,colModel:contract.columns.map(name=>({name})),multifields:mode==='drift'?'changed':contract.multifields},
   {funcno:filterNo,formFields:[...contract.tablenames.matchAll(new RegExp('#'+filterNo+'-([^#]+)#','g'))].map(m=>({fieldname:m[1],initdata:''}))}]};
  else{const fields={'t.serial_no':'1234','t.uni_key':'KEY01','t.node_id':'449','t.amount':'10.00','t.req_name':'Someone else'};
   value={records:1,total:1,page:1,rows:[{cell:contract.columns.map(n=>fields[n]||'')}]};
   if(mode==='empty')value={records:0,total:0,page:0,rows:[]};
   if(mode==='truncated')value.rows=[];
   if(mode==='duplicate'){value.records=2;value.rows.push(value.rows[0]);}
  }
  return{done(fn){this.fn=fn;return this;},fail(){this.fn(value);return this;}};
 }};context.window={jQuery:$};vm.createContext(context);
 const result=await vm.runInContext(source+'\nreimbursementBusinessApi('+JSON.stringify({case_id:'fixture',expected_userid:'USER01',action:history?'approval-history':'approvals-list',contracts,...(serial?{approval_serial:serial}:{})})+')',context);
 return{result,sent,data};
}
(async()=>{
 const {result,sent,data}=await run();assert.equal(result.total_records,1);assert.equal(result.account_scope,'current_approver');
 assert.equal(result.rows[0]['t.node_id'],'449');assert.equal(result.approval_permission_not_inferred,true);
 assert(sent.every(r=>r.url.startsWith('loadDefinition')||r.url.startsWith('commonQuery_doQuery')));
 const query=JSON.parse(sent.at(-1).data.userData);assert.equal(query['WF_YBX$514-T.APPROVAL_TYPE'],'');assert.equal(Object.keys(data).length,1);
 assert.equal((await run('empty')).result.total_records,0);
 for(const mode of ['drift','truncated','duplicate'])await assert.rejects(()=>run(mode));
 const history=await run('ok',true,'1234');assert.equal(history.result.account_scope,'current_approver_history');assert.equal(history.result.query_scope,'native_approved_list_exact_serial');
 assert.equal(JSON.parse(history.sent.at(-1).data.userData)['WF_YBX$719-T.SERIAL_NO'],'1234');assert.equal(Object.keys(history.data).length,1);
 for(const mode of ['drift','truncated'])await assert.rejects(()=>run(mode,true));
 console.log('approval queue: current approver scope, roles, empty list, drift and completeness passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
