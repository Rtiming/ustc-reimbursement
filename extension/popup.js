const status = document.getElementById('status'), result = document.getElementById('result');
let config;
document.getElementById('caseFile').addEventListener('change',async e=>{try{config=JSON.parse(await e.target.files[0].text());status.textContent='参数已载入：'+config.case_id+' / '+config.serial;}catch(err){config=undefined;status.textContent=String(err.message);}});
async function target(){const [tab]=await chrome.tabs.query({active:true,currentWindow:true});const url=new URL(tab.url);if(url.origin!=='https://cwzh.ustc.edu.cn'||url.pathname!=='/YBX/main.jsp')throw Error('请打开已登录的科大智能报销主页面');return tab;}
async function run(file,action){const buttons=[...document.querySelectorAll('button')];buttons.forEach(x=>x.disabled=true);try{const tab=await target();let reply;if(file){reply=await chrome.scripting.executeScript({target:{tabId:tab.id},world:'MAIN',files:[file]});}else{if(!config)throw Error('先导入案件参数');const c=config,n=Number(document.getElementById('index').value),section=document.getElementById('section').value,save=document.getElementById('save').checked;
 await chrome.scripting.executeScript({target:{tabId:tab.id},world:'MAIN',files:['browser_automation.js']});
 reply=await chrome.scripting.executeScript({target:{tabId:tab.id},world:'MAIN',func:async(c,action,n,section,save)=>{
  const allow=['status','navigate','fillBasic','fillFunding','fillExpenses','fillTransfers','cardQuery','cardSelect','savePayments','fillExplanation','preReview','noContract','audit'];if(!allow.includes(action))throw Error('动作未开放');
  const A=globalThis.ReimbursementAutomation;A.guard(c,false);
  for(const group of ['payments','funding','expense_allocations'])if(c[group].reduce((sum,x)=>sum+A.cents(x.amount),0)!==A.cents(c.amount))throw Error('参数金额未闭合：'+group);
  if(c.payments.some(x=>!x.name||!x.person_id||!['bank_transfer','corporate_card'].includes(x.mode)))throw Error('人员或支付方式未接通');
  if(c.payments.filter(x=>x.mode==='corporate_card').length>1)throw Error('多条公务卡尚未实测接通');
  const keys=c.payments.map(x=>x.mode+'/'+x.person_id);if(new Set(keys).size!==keys.length)throw Error('同一人员同一支付方式须合并');
  for(const f of c.funding)if(c.expense_allocations.filter(x=>x.project===f.code).reduce((s,x)=>s+A.cents(x.amount),0)!==A.cents(f.amount))throw Error('经费卡分摊合计不符');
  const arg=action==='navigate'?section:action==='fillTransfers'?{start:n,limit:5}:['cardQuery','cardSelect','preReview'].includes(action)?n:save;
  return await A[action](c,arg);
 },args:[c,action,n,section,save]});}
 const data=reply[0]?.result;if(!data)throw Error('页面未返回结果');result.textContent=JSON.stringify(data,null,2);status.textContent=data.ok?'动作完成；保存请独立回读。':'动作停止，请检查结果。';
 }catch(e){status.textContent=String(e.message);}finally{buttons.forEach(x=>x.disabled=false);}}
document.getElementById('inspect').addEventListener('click',()=>run('finance_read_probe.js'));
document.getElementById('definitions').addEventListener('click',()=>run('finance_definition_probe.js'));
document.getElementById('execute').addEventListener('click',()=>run(null,document.getElementById('action').value));
