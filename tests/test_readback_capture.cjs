const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
(async()=>{
 const rawBank='9900 1234 5678 9012',fields=[
  {id:'formWF_YBX_24_t-bankacnt_0',name:'account',type:'text',value:rawBank},
  {id:'formWF_YBX_24_t-bankacnt_1',name:'account',type:'text',value:'9900123456789012345678'},
  {id:'formWF_YBX_60_t-req_mobile',name:'phone',type:'text',value:'00000000000'}
 ].map(field=>({...field,getClientRects:()=>[1]}));
 const rows=[{cells:[{textContent:'摘要'},{textContent:'合成  文件夹'}]},
  {cells:[{textContent:'备注'},{textContent:'合成联系说明'}]},
  {cells:[{textContent:'序号'},{textContent:'票据号'},{textContent:'票据内容'},{textContent:'备注'},{textContent:'金额'}]}];
 const doc={body:{innerText:'合成 文件夹'},querySelectorAll:selector=>selector==='tr'?rows:[]};
 const frame={id:'unrelated-test-frame',contentDocument:doc,contentWindow:{location:{origin:'https://cwzh.ustc.edu.cn',pathname:'/YBX/redirectCustomPage.action'}}};
 const jq=()=>({data:()=>undefined});jq.Q={list:{}};jq.F={list:{}};jq.UC={bindData:()=> 'synthetic'};jq.page={currPage:6};jq.getProjID=()=> 'WF_YBX';
 let applicantVisible=false;
 const context={$:jq,window:{},document:{body:{innerText:''},querySelectorAll:selector=>selector==='iframe'?[frame]:selector==='input,select,textarea'?fields:selector==='.foot_winWF_YBX_76 button[btnname="同意"]'?[{getClientRects:()=>applicantVisible?[1]:[]}]:[]}};
 const result=await vm.runInNewContext(fs.readFileSync('tools/actions/read_current_state.js','utf8'),context);
 assert.equal(result.applicantAgreementVisible,false);
 applicantVisible=true;
 const applicant=await vm.runInNewContext(fs.readFileSync('tools/actions/read_current_state.js','utf8'),context);
 assert.equal(applicant.applicantAgreementVisible,true);
 assert.equal(result.visibleInputs[0].value,'9900****9012');
 assert.equal(result.visibleInputs[1].value,'9900****5678');
 assert.equal(result.visibleInputs[2].value,'00000000000');
 assert(!JSON.stringify(result).includes(rawBank));
 assert.deepEqual(JSON.parse(JSON.stringify(result.frames[0].raw_basic)),{schema:'ustc-native-basic-text-v1',fields:[
  {label:'摘要',value:'合成  文件夹'},{label:'备注',value:'合成联系说明'}]});
 const nativeCells=[{innerText:'合成公司'},{innerText:rawBank},{innerText:'100.00'},{innerText:'合成银行'}];
 const nativeRow={cells:nativeCells,innerText:nativeCells.map(c=>c.innerText).join(' ')};
 const nativeTable={rows:[nativeRow]};
 frame.id='iframepage_print';frame.getClientRects=()=>[1];doc.readyState='complete';
 doc.querySelectorAll=selector=>selector==='table'?[nativeTable]:selector==='tr'?[nativeRow]:[];
 context.crypto=require('node:crypto').webcrypto;context.TextEncoder=TextEncoder;
 const native=await vm.runInNewContext(fs.readFileSync('tools/actions/read_current_state.js','utf8'),context);
 const hashes=native.frames[0].numeric_cell_hashes;
 assert.equal(hashes.scan_complete,true);assert.equal(hashes.candidates.length,1);
 assert.deepEqual(JSON.parse(JSON.stringify(hashes.candidates[0])),{table:0,row:0,cell:1,digits_length:16,
  sha256:require('node:crypto').createHash('sha256').update('9900123456789012').digest('hex')});
 assert(!JSON.stringify(hashes).includes('9900123456789012'));
 let ticks=0;context.Date={now:()=>ticks};jq.active=1;
 context.setTimeout=fn=>{ticks+=100;jq.active=0;fn();};
 const settled=await vm.runInNewContext(fs.readFileSync('tools/actions/read_current_state.js','utf8'),context);
 assert.equal(settled.nativeRequests,0);assert.equal(settled.readinessWaitMs,100);assert.equal(settled.readinessWaitTimedOut,false);
 jq.active=1;context.setTimeout=fn=>{ticks+=100;fn();};
 const pending=await vm.runInNewContext(fs.readFileSync('tools/actions/read_current_state.js','utf8'),context);
 assert.equal(pending.nativeRequests,1);assert.equal(pending.readinessWaitMs,5000);assert.equal(pending.readinessWaitTimedOut,true);
 console.log('raw summary keeps exact spaces, invoice header is not a remark, and formatted accounts stay masked');
})().catch(error=>{console.error(error);process.exitCode=1;});
