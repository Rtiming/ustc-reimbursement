/* 回读当前页面的单据/表格/字段，输出只含必要业务事实。 */
(async () => {
  // 与submission.js的proof_row字段合同一致，保留原生行而不猜业务归属。
  const proofRow=e=>{
    if(!e.name?.startsWith('form_proof_choose'))return undefined;
    const tr=e.closest('tr'),cells=tr?[...tr.cells].map(cell=>cell.innerText.trim()):[];
    return{type:cells[0]??null,description:cells[1]??null,note:cells[2]??null,cells,text:tr?.innerText.trim()??null,
      complete:cells.length===4&&!!cells[0]&&!!cells[1]};
  };
  // 全量清点只证明当前同号原生预览；空字段本身不是“不适用”的依据。
  function proofInventory(f,serial){
    const d=f.contentDocument,text=d.body.innerText,tight=text.replace(/\s+/g,''),tables=[...d.querySelectorAll('table')],rows=[...d.querySelectorAll('tr')];
    const inputs=[...d.querySelectorAll('input,select,textarea')],controls=inputs.filter(e=>e.name?.startsWith('form_proof_choose'));
    const serials=[...tight.matchAll(/报销单号[:：]([0-9]+)/g)].map(x=>x[1]);
    const sections={title:tight.includes('中国科学技术大学报销单'),serial:serials.length===1&&serials[0]===serial,
      basic:tight.includes('基本信息'),expenses:tight.includes('报销项信息'),payments:tight.includes('支付信息'),
      invoices:tight.includes('票据一览'),promise:tight.includes('报销承诺'),footer:tight.includes('财务审批意见')&&tight.includes('会计审核'),
      ticket_summary:/共选择[0-9]+张发票[,，、]?合计[0-9,.]+元/.test(tight)};
    const contractRows=rows.filter(row=>/^合同号(?:\s*\[[\s\S]*\])?$/.test(row.cells[0]?.innerText.trim()||'')).map(row=>{
      const own=controls.filter(e=>e.closest('tr')===row);return{type:row.cells[0].innerText.trim(),cell_count:row.cells.length,
        control_names:[...new Set(own.map(e=>e.name))],control_count:own.length,radio_count:own.filter(e=>e.type==='radio').length};});
    const boundText=rows.filter(row=>/^合同号(?:\s*\[[\s\S]*\])?$/.test(row.cells[0]?.innerText.trim()||'')).reduce((sum,row)=>sum+(row.innerText.match(/合同号/g)||[]).length,0);
    const url=f.contentWindow.location.origin+f.contentWindow.location.pathname,frameVisible=typeof f.getClientRects==='function'&&f.getClientRects().length>0;
    return{schema:'ustc-native-proof-inventory-v1',frame_id:f.id,serial,preview_serials:serials,frame_url:url,document_ready_state:d.readyState,
      frame_visible:frameVisible,preview_complete:f.id==='iframepage_print'&&url==='https://cwzh.ustc.edu.cn/YBX/redirectCustomPage.action'&&d.readyState==='complete'&&frameVisible&&Object.values(sections).every(Boolean),
      sections,auxiliary_section_present:tight.includes('辅助证据'),scan_complete:true,truncated:false,
      table_count:tables.length,row_count:rows.length,input_count:inputs.length,captured_input_count:inputs.filter(e=>e.type!=='password').length,
      proof_control_count:controls.length,proof_radio_count:controls.filter(e=>e.type==='radio').length,proof_group_count:new Set(controls.map(e=>e.name)).size,
      contract_row_count:contractRows.length,contract_rows:contractRows,contract_text_count:(tight.match(/合同号/g)||[]).length,
      contract_bound_text_count:boundText,contract_unbound_text_count:(tight.match(/合同号/g)||[]).length-boundText};
  }
  const maskAccount=v=>typeof v==='string'?v.replace(/\b\d(?:[ \u00a0]?\d){11,31}\b/g,m=>{const digits=m.replace(/[ \u00a0]/g,'');return digits.slice(0,4)+'****'+digits.slice(-4);}):v;
  const sanitizeRow=row=>Object.fromEntries(Object.entries(row).map(([k,v])=>[k,/account|bankacnt|cnt1|cardno|acnt/i.test(k)?maskAccount(v):v]));
  const grids = [];
  for (const [funcno, def] of Object.entries($.Q.list)) {
    const id = $.Q.idFunc.getGridID(funcno);
    const element = document.getElementById(id);
    if (!element || !element.grid) continue;
    if(String(funcno)==='1669'&&element.getClientRects().length){
      const count=$(element).jqGrid('getGridParam','records');
      if(count<1||count>100)throw new Error('逐票拆分记录数超出已验证范围');
      if($(element).jqGrid('getDataIDs').length!==count){
        $(element).jqGrid('setGridParam',{rowNum:100,page:1}).trigger('reloadGrid');
      }
      const deadline=Date.now()+15000;
      while(element.grid.hDiv.loading||$(element).jqGrid('getDataIDs').length!==count){
        if(Date.now()>deadline)throw new Error('完整逐票分页未就绪');
        await new Promise(r=>setTimeout(r,100));
      }
    }
    const data = $(element).jqGrid("getDataIDs").map(rowId => {
      const row=$(element).jqGrid('getRowData',rowId);
      // jqGrid去HTML时会把<br>两侧金额和下一科目拼接成一个数字。
      const cell=document.getElementById(rowId)?.querySelector('[aria-describedby="'+id+'_t.sj_bf"]');
      if(cell)row['t.sj_bf']=cell.innerText;
      return row;
    });
    grids.push({funcno, id, visible:!!element.getClientRects().length, title: def.title, columns: def.colModel.map((c, i) => ({name: c.name, label: def.colNames[i]})),
      rowCount: data.length, records: $(element).jqGrid('getGridParam','records'),
      loading: !!element.grid.hDiv.loading, rows: data.map(sanitizeRow)});
  }
  const forms = Object.entries($.F?.list || {}).map(([funcno, f]) => ({funcno, title: f.title,multiCount:Number.isSafeInteger(f.multiCount)?f.multiCount:null,
    fields: (f.formFields || []).map(x => ({id: x.id, fieldname: x.fieldname, label: x.label, nullable: x.nullable}))}));
  const frames = [...document.querySelectorAll("iframe")].map((f, i) => {
    try {
      const d=f.contentDocument;
      return {index:i,id:f.id,url:f.contentWindow.location.origin+f.contentWindow.location.pathname,
        visibleText:d.body.innerText,
        raw_basic:{schema:'ustc-native-basic-text-v1',fields:[...d.querySelectorAll('tr')].filter(row=>row.cells.length===2&&['摘要','备注'].includes(row.cells[0].textContent.trim())).map(row=>({label:row.cells[0].textContent.trim(),value:row.cells[1].textContent.trim()}))},
        proof_inventory:f.id==='iframepage_print'?proofInventory(f,String($.UC.bindData('#61-serialno#')||'')):undefined,
        tables:[...d.querySelectorAll("table")].map(t=>[...t.rows].map(r=>[...r.cells].map(c=>c.innerText.trim()))),
        fields:[...d.querySelectorAll("input,select,textarea")].filter(e=>e.type!=="password").map(e=>({id:e.id,name:e.name,type:e.type,checked:e.checked,disabled:e.disabled,value:/yzm|captcha|sms|otp/i.test(e.id+' '+e.name)||e.id==='formWF_YBX_1362_t-code'?'[protected]':/account|bankacnt|cnt1|cardno|acnt/i.test(e.id)?maskAccount(e.value):e.value,proof_row:proofRow(e)})),
        controls:[...d.querySelectorAll("[onclick]")].map(e=>({tag:e.tagName,text:e.textContent.trim(),onclick:e.getAttribute("onclick")}))};
    }catch(e){return {index:i,inaccessible:true,error:String(e.name)};}
  });
  // Hash exact numeric table cells from the captured preview. This is a
  // candidate inventory, not a claim that an invoice number is a bank account.
  // Consumers must bind table/row/cell and the complete native payment row.
  for(const frame of frames){
    if(frame.id!=='iframepage_print'||frame.url!=='https://cwzh.ustc.edu.cn/YBX/redirectCustomPage.action')continue;
    const candidates=[];let truncated=false;
    for(let ti=0;ti<frame.tables.length;ti++)for(let ri=0;ri<frame.tables[ti].length;ri++)for(let ci=0;ci<frame.tables[ti][ri].length;ci++){
      const digits=frame.tables[ti][ri][ci].replace(/[ \u00a0]/g,'');
      if(!/^[0-9]{12,30}$/.test(digits))continue;
      if(candidates.length>=256){truncated=true;continue;}
      const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(digits));
      candidates.push({table:ti,row:ri,cell:ci,digits_length:digits.length,sha256:Array.from(new Uint8Array(digest),v=>v.toString(16).padStart(2,'0')).join('')});
    }
    frame.numeric_cell_hashes={schema:'ustc-native-numeric-table-cell-hashes-v1',source:'captured_same_frame_table_cells',scan_complete:!truncated,truncated,candidates};
  }
  const track=window.__reimbursementUploadTracking;
  const uploadTracking=track?{schema:track.schema,caseId:track.caseId,expectedUserid:track.expectedUserid,manifestSha256:track.manifestSha256,batchSha256:track.batchSha256,offset:track.offset,count:track.count,dispatchRequested:track.dispatchRequested,dispatchAt:track.dispatchAt,saveRequested:track.saveRequested,saveRequestedAt:track.saveRequestedAt,saveRequestBatchSha256:track.saveRequestBatchSha256,done:track.done,files:track.files,receipts:track.receipts,errors:track.errors}:null;
  const uploadControl=$('#formWF_YBX_84_t-img').data('uploadifive');
  const uploadNativeState=uploadControl?{queueCount:uploadControl.queue?.count,currentUploads:uploadControl.uploads?.current,uploadErrors:uploadControl.uploads?.errors,nativeFileList:$.F.getInputData('formWF_YBX_84_t-img','uploadfile')}:null;
  const draftContext={businessType:$.UC.bindData('#61-bx_jbxx_bx_type#'),currentFunc:$.UC.bindData('#0-CURRFUNC#'),
    fields:[...document.querySelectorAll('#formWF_YBX_45 input,#formWF_YBX_45 select,#formWF_YBX_45 textarea')].filter(e=>e.type!=='password').map(e=>({id:e.id,value:e.value,visible:!!e.getClientRects().length,disabled:!!e.disabled})),
    nextBeforeSource:typeof window.ybx_jbxx_next_before==='function'?String(window.ybx_jbxx_next_before).slice(0,16000):null};
  const visibleDialogs=[...document.querySelectorAll('.ui-dialog')].filter(e=>e.getClientRects().length).map(e=>({text:e.innerText.slice(0,6000),buttons:[...e.querySelectorAll('button')].filter(b=>b.getClientRects().length).map(b=>({text:b.innerText,btnname:b.getAttribute('btnname'),disabled:!!b.disabled}))}));
  return {ok: true, currentPage: String($.page.currPage), projid: $.getProjID(), nativeRequests:$.active, identity:{userid:$.UC.bindData('#0-userinfo.userid#'),username:$.UC.bindData('#0-userinfo.username#')}, uploadNativeState, serial:$.UC.bindData('#61-serialno#'), node:$.UC.bindData('#61-node_id#'), uploadTracking, grids, forms,
    frames,draftContext,visibleDialogs,
    visibleText: document.body.innerText.slice(0, 16000),
    visibleInputs: [...document.querySelectorAll("input,select,textarea")].filter(e => e.getClientRects().length && !["password","hidden"].includes(e.type))
      .map(e => ({id:e.id, name:e.name, type:e.type, checked:e.checked, disabled:e.disabled, value:/yzm|captcha|sms|otp/i.test(e.id+' '+e.name)||e.id==='formWF_YBX_1362_t-code'?'[protected]':/account|bankacnt|cnt1|cardno|acnt/i.test(e.id)?maskAccount(e.value):e.value, options:e.options?[...e.options].map(o=>({text:o.text,value:o.value})):undefined}))};
})()
