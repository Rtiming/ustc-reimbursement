const assert=require('node:assert/strict'),fs=require('fs'),vm=require('vm'),{webcrypto,createHash}=require('crypto');
const source=fs.readFileSync('tools/finance_upload_recognition_api.js','utf8');
async function fixture(change={},responses){
 const f={name:'source.pdf',filecode:'a'.repeat(32),sha256:'b'.repeat(64),invoice_numbers:['9'.repeat(20)]};
 const row={'t.filesequcode':f.filecode,'t.show_filename':f.name,'t.show_err_msg':'','t.show_type':'PDF','t.show_status':'识别中','t.tk_filecode':f.filecode+'&source.pdf&.pdf&WF_YBX',...change.row};
 const callbacks={invoiceHandle:function invoiceHandle(){},pdf_v2:function pdf_v2(){}};
 const calls=[];const contract={callbacks:Object.fromEntries(Object.entries(callbacks).map(([k,v])=>[k,createHash('sha256').update(String(v)).digest('hex')]))};
 const c={file:f,case_id:'fixture',expected_userid:'TEST001',execute:true,pending_registered:true,original_save_accepted:true,contract,...change.config};
 const context={crypto:webcrypto,TextEncoder,window:{...callbacks,jQuery:{}},createReimbursementHttp:()=>({post:async(url,data)=>{calls.push({url,data});return JSON.stringify(responses?.shift()||{stateCode:'SUCCESS',data:{status:'pass',seq:f.filecode}})}}),
 reimbursementBusinessApi:async()=>({complete:true,requests:[],grids:[{funcno:'53',complete:true,rows:change.pool||[]},{funcno:'113',complete:true,rows:[row]}]})};
 vm.createContext(context);vm.runInContext(source,context);
 return {run:()=>context.reimbursementUploadRecognition(c),calls,context,c,row};
}
(async()=>{
 let t=await fixture();assert.equal((await t.run()).ok,true);assert.equal(t.calls.length,1);assert.equal(t.calls[0].data.interfaceName,'pdfsign2');assert.equal(decodeURIComponent(t.calls[0].data.arguments),'jsonData|'+'a'.repeat(32)+'!S!TEST001');
 t=await fixture({},[{stateCode:'SUCCESS',data:{status:'error',seq:'a'.repeat(32)}},{stateCode:'SUCCESS',data:{status:'pass',seq:'a'.repeat(32)}}]);await t.run();assert.deepEqual(t.calls.map(x=>x.data.interfaceName),['pdfsign2','OCR_cutRecognition_HH']);
 for(const change of [{row:{'t.show_filename':'other.pdf'}},{row:{'t.show_err_msg':'already reserved'}},{row:{'t.show_status':'done'}},{pool:[{'t.tk_no':'9'.repeat(20)}]},{config:{pending_registered:false}}]){
  t=await fixture(change);await assert.rejects(t.run());assert.equal(t.calls.length,0);
 }
 t=await fixture({},[{stateCode:'SUCCESS',data:{status:'pass',seq:'c'.repeat(32)}}]);await assert.rejects(t.run());assert.equal(t.calls.length,1);
 for(const rejectGuard of [false,true]){
  t=await fixture();const steps=[];
  t.context.ReimbursementNativeForm={canonical:x=>x};
  t.context.reimbursementRetryApi=async p=>{steps.push(p.action);
   if(p.action==='inspect')return{complete:true,native_ocr_contract_verified:true,grid:{rows:[t.row]},grids:[{funcno:'53',complete:true,rows:[]}]};
   if(p.action==='reidentify'){if(rejectGuard)throw Error('native guard rejected');return{requires_confirmation:true,marker:{native_guard:'judge'}};}
   return{ok:true,server_accepted:true};};
  const c={...t.c,recovery:true,failed_receipts:[{filecode:t.c.file.filecode}],retry_contract:{grids:{'113':{reidentify_button:{caption:'重新识别'}}}}};
  if(rejectGuard){await assert.rejects(()=>t.context.reimbursementRecoverUploadRecognition(c));assert.deepEqual(steps,['inspect','reidentify']);}
  else{const r=await t.context.reimbursementRecoverUploadRecognition(c);assert.equal(r.original_pdf_request_replayed,false);assert.deepEqual(steps,['inspect','reidentify','confirm']);}
 }
 console.log('upload recognition: exact original queue, PDF protocol, native fallback, protected/error rows and wrong-target response passed');
})().catch(e=>{console.error(e);process.exit(1)});
