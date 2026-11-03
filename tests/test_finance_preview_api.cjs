const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),{webcrypto}=require('node:crypto');
const source=['finance_http.js','finance_preview_api.js'].map(n=>fs.readFileSync(path.join(__dirname,'../tools',n),'utf8')).join('\n');
async function run(mode='ok') {
  const calls=[],data={'0-USERINFO.USERID':'TEST001'},attrs=["pageOpen_v1('特殊事项','33&33')"];
  for(let i=0;i<4;i++)attrs.push("pageOpen_v1('选择流程参数','WORKFLOW_V1&ustc_xmysr"+(i||'')+"&10&"+'A'.repeat(32)+"&QT&N&A&')");
  if(mode==='duplicate')attrs.push(attrs[1]);if(mode==='missing')attrs.splice(2,1);if(mode==='table')attrs[1]=attrs[1].replace('&QT&','&OTHER&');
  const $={getProjID:()=> 'WF_YBX',UC:{userData:data,bindData:()=> 'TEST001'},ajax:o=>{
    calls.push(o);let value=o.url.startsWith('loadDefinition')?{funcMap:{'41':{type:'C',url:mode==='template'?'other':'Customize/customPrint/bx_frame_print.jsp'}}}:'<safe html>';
    if(o.url==='ybxPrint_yb4PrintHtml.action'){assert.equal(o.data.printType,'报销确认');assert.equal(o.data.serialno,'10001');}
    return{done(fn){this.fn=fn;return this;},fail(){this.fn(value);return this;}};
  }};
  class Parser {parseFromString(){return{body:{textContent:'报销单号：'+(mode==='identity'?'OTHER':'10001')},querySelectorAll:()=>attrs.map(a=>({getAttribute:()=>a}))};}}
  const context=vm.createContext({window:{jQuery:$},performance,Date,crypto:webcrypto,TextEncoder,encodeURI,DOMParser:Parser,
    location:{origin:'https://cwzh.ustc.edu.cn',pathname:'/YBX/main.jsp'}});vm.runInContext(source,context);
  const c={serial:'10001',expected_userid:'TEST001',expected_amount:'120.00'},bill={'t.serial_no':'10001','t.REQ_INFO':'TEST001-Test','t.total_amt':'120.00'};
  if(mode==='owner')bill['t.REQ_INFO']='OTHER';
  const result=await vm.runInContext('reimbursementPreviewData('+JSON.stringify(c)+','+JSON.stringify(bill)+')',context);
  return{result,calls};
}
(async()=>{
  const {result,calls}=await run();assert(result.notes_available);assert.equal(result.pre_review_slots.length,4);
  assert.equal(calls.length,2);assert.equal(result.system_write,false);assert.equal(result.navigation_requested,false);
  for(const mode of ['owner','identity','template','duplicate','missing','table'])await assert.rejects(()=>run(mode));
  assert(!/\.click\s*\(|\.trigger\s*\(|dispatchEvent|eval\s*\(/.test(source));
  console.log('native preview: same-bill binding, fixed template, data-only workflow parameters, duplicate/gap/foreign scope rejection passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
