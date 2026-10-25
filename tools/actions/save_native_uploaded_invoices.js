((expected)=>{
 const id='formWF_YBX_84_t-img',uploader=$('#'+id).data('uploadifive'),t=window.__reimbursementUploadTracking;
 if($.UC.bindData('#0-userinfo.userid#')!==expected.expectedUserid)throw new Error('保存登录身份不同');
 if(!t||t.schema!==expected.schema||t.caseId!==expected.caseId||t.expectedUserid!==expected.expectedUserid||t.manifestSha256!==expected.manifestSha256||t.batchSha256!==expected.batchSha256||t.offset!==expected.offset||t.count!==expected.count)throw new Error('保存与预期案件、身份或批次manifest不符');
 if(t.saveRequested)throw new Error('本批已经请求保存；只读回查，不重复保存');
 const fileKey=x=>JSON.stringify([x.name,x.sha256,x.size,x.type]);
 if(JSON.stringify(t.files.map(fileKey).sort())!==JSON.stringify(expected.files.map(fileKey).sort()))throw new Error('保存文件SHA集合与本批manifest不符');
 if(!t.done||t.errors.length||t.receipts.length!==t.files.length||!t.files.length||t.files.length>10)throw new Error('上传尚未完整成功');
 if(!uploader||uploader.queue.count||uploader.uploads.current||uploader.uploads.errors)throw new Error('上传队列未完成');
 const raw=$.F.getInputData(id,'uploadfile');
 const pairs=[...String(raw).matchAll(/([a-fA-F0-9]{32})&([^&|]+)&/g)].map(m=>({response:m[1],name:m[2]}));
 const receiptKey=r=>JSON.stringify([r.name,r.response]);
 if(new Set(t.receipts.map(r=>r.name)).size!==t.files.length||new Set(t.receipts.map(r=>r.response)).size!==t.files.length||pairs.length!==t.files.length||JSON.stringify(pairs.map(receiptKey).sort())!==JSON.stringify(t.receipts.map(receiptKey).sort()))throw new Error('上传回执重复、缺失或原生文件列表不符');
 for(const r of t.receipts){if(!/^[a-fA-F0-9]{32}$/.test(r.response)||!t.files.some(f=>f.name===r.name))throw new Error('回执文件不属于本批');}
 $.F.getDataToContext('84');
 if($.UC.bindData('#84-t.img#')!==raw)throw new Error('原生表单上下文不同步');
 const buttons=[...document.querySelectorAll('.foot_winWF_YBX_60 button[btnname="保存发票"]')].filter(e=>e.getClientRects().length);
 if(buttons.length!==1||buttons[0].disabled)throw new Error('保存发票按钮不唯一或不可用');
 // 在原生按钮前记为已请求；按钮异常、服务端失败、传输未知均不回退此标记。
 t.saveRequested=true;t.saveRequestedAt=new Date().toISOString();t.saveRequestBatchSha256=expected.batchSha256;
 buttons[0].click();
 return{ok:true,action:'save_uploaded_invoices_native',batch:expected,count:t.files.length,receipts:t.receipts,saveRequested:true,requiresAuthenticationReadback:true};
})(__EXPECTED_UPLOAD_BATCH__)
