'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../tools/system_print.js'),'utf8');
function fixture(options={}){
  const state={selected:[],opened:0,chosen:0,prompt:false};
  const vis=x=>({getClientRects:()=>[{}],...x});
  const button=vis({disabled:false,getAttribute:k=>k==='btnname'?'打印报销单':null,click(){state.opened++;state.prompt=true;}});
  const no=vis({disabled:false,innerText:'否',click(){state.chosen++;state.prompt=false;}});
  const dialog={getClientRects:()=>state.prompt?[{}]:[],innerText:'提示信息 是否打印发票等附件',querySelectorAll:()=>[no]};
  const content={...dialog,innerText:'是否打印发票等附件',closest:()=>dialog};
  const grid=vis({grid:{hDiv:{loading:false}}});
  function $(arg){return{jqGrid:(method,...a)=>{if(method==='resetSelection')state.selected=[];if(method==='setSelection')state.selected=[a[0]];if(method==='getDataIDs')return['row1'];if(method==='getRowData')return{'t.serial_no':'123'};if(method==='getGridParam')return a[0]==='records'?1:state.selected;}};}
  $.getProjID=()=> 'WF_YBX';$.active=0;$.UC={bindData:key=>key==='#0-userinfo.userid#'?(options.wrongUser?'OTHER':'SYNTH001'):'123'};
  const document={getElementById:id=>id==='gridWF_YBX_101'?grid:id==='printBxdWarnMsgBox101'?content:null,
    querySelectorAll:selector=>selector==='.ui-dialog'?(state.prompt?[dialog]:[]):selector.startsWith('button')?(options.hidden?[]:[button]):[]};
  const definition={caption:'打印报销单',pre_check:true,call_proc:true,post_check:true,wf_enabled:null,wf_trans:null,acts:'',
    check_func:'`$94b256ac92dda44bf3c48aefdac300bc:#101-t.serial_no#;#101-MultiRows#;#101-MultiCount#;#0-userinfo.userid#',
    procs:['`$d264de40319173e29c4db20af0188ea8:#101-t.serial_no#;#101-MultiRows#;#101-MultiCount#;#0-userinfo.userid#']};
  if(options.changedDefinition)definition.wf_trans='APPROVAL';
  const crypto={subtle:{digest:async(_,bytes)=>Buffer.from(options.changedCallback?'0'.repeat(64):new TextDecoder().decode(bytes).includes('print_bxd_prompt_after_101')?'a432fbbfb8d523175a40555682620921790e48da74cf21a3b101858220efbe20':'c8a8c68a95c0fad4f09b2e88ccc8655933963288733132f96f16cf2df9ab65d9','hex')}};
  const context=vm.createContext({document,$,crypto,TextEncoder,Uint8Array,URL,location:{origin:'https://cwzh.ustc.edu.cn',pathname:'/YBX/main.jsp'},
    print_bxd_prompt_after_101:function print_bxd_prompt_after_101(){},handlePrintAction:function handlePrintAction(){},
    ReimbursementSubmission:{printInspect:()=>({definition,row:{id:'row1'}})}});
  vm.runInContext(source,context);context.ReimbursementSystemPrint.configure({case_id:'fixture'},{serial:'123',expected_userid:'SYNTH001',print_export_authorized:options.authorized!==false,include_invoice_attachments:false});
  return{state,api:context.ReimbursementSystemPrint};
}
(async()=>{
  const f=fixture();await f.api.open();assert.equal(f.state.opened,1);assert.deepEqual(f.state.selected,['row1']);
  assert.equal(f.api.read().phase,'print_open_requested');await f.api.choose();assert.equal(f.state.chosen,1);
  await assert.rejects(f.api.open(),/ORIGINAL_INTENT_EXISTS/);await assert.rejects(f.api.choose(),/NOT_AUTHORIZED/);
  for(const o of [{authorized:false},{wrongUser:true},{hidden:true},{changedDefinition:true},{changedCallback:true}]){
    const x=fixture(o);await assert.rejects(x.api.open());assert.equal(x.state.opened,0);assert.equal(x.state.chosen,0);
  }
  const x=fixture();await assert.rejects(x.api.choose(),/ORIGINAL_INTENT_REQUIRED/);
  console.log('system print: exact selection, visible button, no attachments, no duplicate dispatch, identity and definition guards passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
