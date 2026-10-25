/* 固定报销单打印入口。只选择同案一行；不调用隐藏按钮或猜测下载地址。 */
(function(root){
  let c,p;
  const visible=e=>!!e?.getClientRects().length;
  const sha=async s=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s)))].map(x=>x.toString(16).padStart(2,'0')).join('');
  function configure(config,plan){c=config;p=plan;}
  function identity(){
    if(location.origin!=='https://cwzh.ustc.edu.cn'||location.pathname!=='/YBX/main.jsp'||$.getProjID()!=='WF_YBX'||$.UC.bindData('#0-userinfo.userid#')!==p.expected_userid)throw Error('SYSTEM_PRINT_IDENTITY_CHANGED');
  }
  async function callbacks(){
    for(const [name,expected]of [['print_bxd_prompt_after_101','a432fbbfb8d523175a40555682620921790e48da74cf21a3b101858220efbe20'],['handlePrintAction','c8a8c68a95c0fad4f09b2e88ccc8655933963288733132f96f16cf2df9ab65d9']]){
      const fn=Object.getOwnPropertyDescriptor(root,name)?.value;
      if(typeof fn!=='function'||await sha(String(fn))!==expected)throw Error('SYSTEM_PRINT_NATIVE_CALLBACK_CHANGED');
    }
  }
  function binding(){
    identity();const value=Object.getOwnPropertyDescriptor(root,'__ustcSystemPrintIntentV1')?.value;
    if(!value||value.case_id!==c.case_id||value.serial!==p.serial||value.userid!==p.expected_userid)throw Error('SYSTEM_PRINT_ORIGINAL_INTENT_REQUIRED');
    return value;
  }
  async function details(){
    identity();
    if(Object.prototype.hasOwnProperty.call(root,'__ustcSystemPrintIntentV1'))throw Error('SYSTEM_PRINT_ORIGINAL_INTENT_EXISTS');
    if([...document.querySelectorAll('.ui-dialog')].some(visible))throw Error('SYSTEM_PRINT_EDITOR_OR_LOADING');
    const deadline=Date.now()+15000;let grid;
    while(true){identity();grid=document.getElementById('gridWF_YBX_101');if(visible(grid)&&grid.grid?.hDiv?.loading===false)break;if(Date.now()>deadline)throw Error('DETAIL_LIST_NOT_READY');await new Promise(r=>setTimeout(r,100));}
    const matches=$(grid).jqGrid('getDataIDs').map(id=>({id,row:$(grid).jqGrid('getRowData',id)})).filter(x=>x.row['t.serial_no']===p.serial);
    if(matches.length!==1||Number(String(matches[0].row['t.total_amt']).replaceAll(',',''))!==Number(p.amount)||!String(matches[0].row['t.REQ_INFO']||'').startsWith(p.expected_userid+'-'))throw Error('DETAIL_CASE_BINDING_CHANGED');
    if(String(matches[0].row['t.status'])==='-99')throw Error('DETAIL_DRAFT_REQUIRES_EXISTING_OPEN_DRAFT');
    const defs=Object.values($.page.list||{}).flatMap(page=>(page.wins||[]).filter(w=>String(w.winno)==='71').flatMap(w=>w.func_btns||[])).filter(d=>d.btnguid==='CA0541BD7A3D46ECA2078DD7B5FA8FDC'&&d.caption==='查看明细');
    if(defs.length!==1||defs[0].pre_check!==true||defs[0].call_proc!==true||defs[0].post_check!==true||(defs[0].procs||[]).length||defs[0].acts!=='1==1:69'||defs[0].check_func!=='`$befac77e186c7e6ab77274901c30f0dc:#101-MultiRows#;#101-MultiCount#'||defs[0].wf_trans)throw Error('DETAIL_NATIVE_DEFINITION_CHANGED');
    for(const [name,expected]of [['before_apply_list_dtl','a39d8062f22eac6da123374a8e136b2247fcf9edd2f2e7b0a3f1617eebf80869'],['after_apply_list_dtl','89f4476b0fdbe242e53dbc38bec8255d5b36756c59266a413d1d6421da996821']]){
      const fn=Object.getOwnPropertyDescriptor(root,name)?.value;if(typeof fn!=='function'||await sha(String(fn))!==expected)throw Error('DETAIL_NATIVE_CALLBACK_CHANGED');
    }
    const buttons=[...document.querySelectorAll('button[guid="CA0541BD7A3D46ECA2078DD7B5FA8FDC"]')].filter(b=>visible(b)&&!b.disabled&&b.getAttribute('btnname')==='查看明细');
    if(buttons.length!==1)throw Error('DETAIL_VISIBLE_BUTTON_REQUIRED');
    $(grid).jqGrid('resetSelection');$(grid).jqGrid('setSelection',matches[0].id,true);
    const selected=$(grid).jqGrid('getGridParam','selarrrow');if(selected.length!==1||String(selected[0])!==String(matches[0].id))throw Error('DETAIL_SELECTION_CHANGED');
    Object.defineProperty(root,'__ustcSystemPrintIntentV1',{value:{case_id:c.case_id,serial:p.serial,userid:p.expected_userid,row_id:matches[0].id,phase:'detail_open_requested'},configurable:true});
    buttons[0].click();return{ok:true,action:'detail_open_requested',serial:p.serial,system_write:false,requiresReadback:true};
  }
  function detailContinue(){
    const intent=binding(),dialogs=[...document.querySelectorAll('.ui-dialog')].filter(visible);
    const expected='【查看报销单】按钮仅供查看报销明细使用,待审批流程完成后请使用【打印报销单】按钮进行打印预约单并连同报销发票等附件装袋一起投递';
    if(intent.phase!=='detail_open_requested'||dialogs.length!==1||dialogs[0].innerText.replace(/\s/g,'')!==('提示框Close'+expected+'继续取消').replace(/\s/g,''))throw Error('DETAIL_KNOWN_INFORMATION_PROMPT_REQUIRED');
    const buttons=[...dialogs[0].querySelectorAll('button')].filter(b=>visible(b)&&!b.disabled&&b.innerText.trim()==='继续');
    if(buttons.length!==1)throw Error('DETAIL_CONTINUE_BUTTON_REQUIRED');
    intent.phase='detail_continue_requested';buttons[0].click();
    return{ok:true,action:'detail_continue_requested',serial:p.serial,system_write:false,requiresReadback:true};
  }
  async function detailReturn(){
    const intent=binding();
    if(Object.prototype.hasOwnProperty.call(root,'__ustcReimbursementPdfExportV1'))throw Error('DETAIL_EXPORT_UNFINISHED_KEEP_VIEWER');
    if($.UC.bindData('#61-serialno#')!==p.serial||[...document.querySelectorAll('.ui-dialog')].some(visible)||root.YBX_JSONDATA?.['确认报销信息']?.['入口']!=='报销单列表')throw Error('DETAIL_RETURN_CONTEXT_CHANGED');
    const defs=Object.values($.page.list||{}).flatMap(page=>(page.wins||[]).filter(w=>String(w.winno)==='69').flatMap(w=>w.func_btns||[])).filter(d=>d.btnguid==='090FE1957D6E4BF7840C53C593EF06D3'&&d.caption==='返回');
    if(defs.length!==1||defs[0].call_proc!==false||(defs[0].procs||[]).length||defs[0].wf_trans||!String(defs[0].acts).startsWith("YBX_JSONDATA['确认报销信息']['入口']=='报销单列表'||YBX_JSONDATA['确认报销信息']['入口']=='PDF打印报销单列表':70@")||String(defs[0].afterclick).replace(/\s/g,'')!=='function(op){after_sqwc_return();}')throw Error('DETAIL_RETURN_DEFINITION_CHANGED');
    const buttons=[...document.querySelectorAll('button[guid="090FE1957D6E4BF7840C53C593EF06D3"]')].filter(b=>visible(b)&&!b.disabled&&b.getAttribute('btnname')==='返回');
    if(buttons.length!==1)throw Error('DETAIL_RETURN_BUTTON_REQUIRED');
    intent.phase='detail_return_requested';buttons[0].click();
    const deadline=Date.now()+15000;
    while(true){identity();const grid=document.getElementById('gridWF_YBX_101');if(visible(grid)&&grid.grid?.hDiv?.loading===false)break;if(Date.now()>deadline)throw Error('DETAIL_RETURN_PENDING_READ_ORIGINAL');await new Promise(r=>setTimeout(r,100));}
    delete root.__ustcSystemPrintIntentV1;
    return{ok:true,action:'detail_returned',serial:p.serial,system_write:false};
  }
  function exportScope(){
    identity();const config=p.grid_export,grid=document.getElementById('gridWF_YBX_101');
    if(!config||!['EXCEL','PDF','DBF'].includes(config.format)||!(/^[A-Za-z0-9_\-\u4e00-\u9fff]{1,50}$/).test(config.title||'')||!Array.isArray(config.serials)||!config.serials.length||config.serials.length>500||new Set(config.serials).size!==config.serials.length)throw Error('GRID_EXPORT_PLAN_REQUIRED');
    if(!visible(grid)||grid.grid?.hDiv?.loading)throw Error('GRID_EXPORT_LIST_NOT_READY');
    const ids=$(grid).jqGrid('getDataIDs'),rows=ids.map(id=>$(grid).jqGrid('getRowData',id)),serials=rows.map(r=>r['t.serial_no']);
    if(Number($(grid).jqGrid('getGridParam','records'))!==config.serials.length||rows.length!==config.serials.length||new Set(serials).size!==serials.length||serials.some(s=>!config.serials.includes(s))||rows.some(r=>!String(r['t.REQ_INFO']||'').startsWith(p.expected_userid+'-')))throw Error('GRID_EXPORT_SCOPE_CHANGED');
    return config;
  }
  async function exportMenu(){
    identity();const config=p.grid_export,grid=document.getElementById('gridWF_YBX_101');
    if(!config||!Array.isArray(config.serials)||config.serials.length>500||!visible(grid)||grid.grid?.hDiv?.loading||[...document.querySelectorAll('.ui-dialog')].some(visible))throw Error('GRID_EXPORT_SCOPE_NOT_READY');
    if($(grid).jqGrid('getDataIDs').length!==Number($(grid).jqGrid('getGridParam','records'))){
      if(Number($(grid).jqGrid('getGridParam','records'))!==config.serials.length)throw Error('GRID_EXPORT_SCOPE_CHANGED');
      $(grid).jqGrid('setGridParam',{rowNum:config.serials.length,page:1}).trigger('reloadGrid');
      const deadline=Date.now()+15000;
      while(grid.grid.hDiv.loading||$(grid).jqGrid('getDataIDs').length!==config.serials.length){if(Date.now()>deadline)throw Error('GRID_EXPORT_LIST_LOADING');await new Promise(r=>setTimeout(r,100));}
    }
    exportScope();
    for(const [fn,expected]of [[Object.getOwnPropertyDescriptor($.grid||{},'printGrid')?.value,'c6759857b390b03044b5873b359779345dc54f88cb924c3828e813acc2684c50'],[Object.getOwnPropertyDescriptor(root,'printGrid')?.value,'daa4da048d0aa8aebf2b544c0e3092d9e2fc5a36f5c6d4fdc4389f5ec41db980']]){
      if(typeof fn!=='function'||await sha(String(fn))!==expected)throw Error('GRID_EXPORT_NATIVE_FUNCTION_CHANGED');
    }
    const button=document.getElementById('gridWF_YBX_101_print');
    if(!visible(button)||button.innerText.trim()!=='导出')throw Error('GRID_EXPORT_BUTTON_REQUIRED');
    if(Object.prototype.hasOwnProperty.call(root,'__ustcGridExportIntentV1'))throw Error('GRID_EXPORT_ORIGINAL_INTENT_EXISTS');
    Object.defineProperty(root,'__ustcGridExportIntentV1',{value:{userid:p.expected_userid,title:config.title,format:config.format,serials:config.serials.slice(),phase:'menu_requested'},configurable:true});
    button.click();return{ok:true,action:'grid_export_menu_requested',format:config.format,rows:config.serials.length,system_write:false};
  }
  function exportExecute(){
    const config=exportScope(),intent=Object.getOwnPropertyDescriptor(root,'__ustcGridExportIntentV1')?.value;
    if(!intent||intent.phase!=='menu_requested'||intent.userid!==p.expected_userid||intent.title!==config.title||intent.format!==config.format||JSON.stringify(intent.serials)!==JSON.stringify(config.serials))throw Error('GRID_EXPORT_ORIGINAL_PLAN_REQUIRED');
    const content=document.getElementById('dialog'),dialog=content?.closest('.ui-dialog'),all=[...document.querySelectorAll('.ui-dialog')].filter(visible);
    if(!visible(content)||all.length!==1||all[0]!==dialog||!dialog.innerText.includes('导出文件')||!content.innerText.includes('导出范围'))throw Error('GRID_EXPORT_DIALOG_REQUIRED');
    const format=content.querySelector('#p_type'),title=content.querySelector('#name'),whole=content.querySelector('#whole');
    if(!visible(format)||!visible(title)||!visible(whole)||!whole.checked||![...format.options].some(o=>o.value===config.format))throw Error('GRID_EXPORT_OPTIONS_CHANGED');
    $(format).val(config.format).trigger('change');$(title).val(config.title);
    if(format.value!==config.format||title.value!==config.title)throw Error('GRID_EXPORT_FIELDS_NOT_BOUND');
    const buttons=[...dialog.querySelectorAll('button')].filter(b=>visible(b)&&!b.disabled&&b.innerText.trim()==='执行');
    if(buttons.length!==1)throw Error('GRID_EXPORT_EXECUTE_REQUIRED');
    intent.phase='download_requested';buttons[0].click();
    return{ok:true,action:'grid_export_download_requested',title:config.title,format:config.format,rows:config.serials.length,financial_write:false};
  }
  function read(){
    const intent=binding();const dialogs=[...document.querySelectorAll('.ui-dialog')].filter(visible).map(d=>({text:d.innerText.slice(0,2000),buttons:[...d.querySelectorAll('button')].filter(visible).map(b=>({text:b.innerText,disabled:!!b.disabled}))}));
    const frames=[];
    function scan(doc,depth){if(depth>2)return;for(const f of doc.querySelectorAll('iframe')){try{if(!visible(f))continue;const w=f.contentWindow,u=new URL(w.location.href);if(u.origin!==location.origin)continue;
      frames.push({id:f.id,path:u.origin+u.pathname,pdf_ready:!!w.PDFViewerApplication?.pdfDocument});if(f.contentDocument)scan(f.contentDocument,depth+1);
    }catch{}}}
    scan(document,0);
    return{ok:true,action:'system_print_read',case_id:c.case_id,serial:p.serial,identity:{userid:p.expected_userid},projid:$.getProjID(),
      native_serial:$.UC.bindData('#61-serialno#'),phase:intent.phase,dialogs,frames,pdf_ready:frames.some(f=>f.pdf_ready),system_write:false};
  }
  async function open(){
    identity();if(p.print_export_authorized!==true||p.include_invoice_attachments!==false)throw Error('SYSTEM_PRINT_AUTHORIZATION_REQUIRED');
    if(Object.prototype.hasOwnProperty.call(root,'__ustcSystemPrintIntentV1'))throw Error('SYSTEM_PRINT_ORIGINAL_INTENT_EXISTS');
    if($.active!==0||[...document.querySelectorAll('.ui-dialog')].some(visible))throw Error('SYSTEM_PRINT_EDITOR_OR_LOADING');
    const sourceGrid=document.getElementById('gridWF_YBX_101');
    if(!visible(sourceGrid)||sourceGrid.grid?.hDiv?.loading)throw Error('SYSTEM_PRINT_LIST_NOT_READY');
    const count=Number($(sourceGrid).jqGrid('getGridParam','records'));
    if(!$(sourceGrid).jqGrid('getDataIDs').some(id=>$(sourceGrid).jqGrid('getRowData',id)['t.serial_no']===p.serial)){
      if(!Number.isInteger(count)||count<1||count>500)throw Error('SYSTEM_PRINT_BOUNDED_LIST_REQUIRED');
      $(sourceGrid).jqGrid('setGridParam',{rowNum:count,page:1}).trigger('reloadGrid');
      const deadline=Date.now()+15000;
      while(sourceGrid.grid.hDiv.loading||$(sourceGrid).jqGrid('getDataIDs').length!==count){if(Date.now()>deadline)throw Error('SYSTEM_PRINT_LIST_LOADING');await new Promise(r=>setTimeout(r,100));}
    }
    const observed=ReimbursementSubmission.printInspect(),d=observed.definition;
    if(d.caption!=='打印报销单'||d.pre_check!==true||d.call_proc!==true||d.post_check!==true||d.wf_enabled||d.wf_trans||d.acts!==''||
       d.check_func!=='`$94b256ac92dda44bf3c48aefdac300bc:#101-t.serial_no#;#101-MultiRows#;#101-MultiCount#;#0-userinfo.userid#'||
       JSON.stringify(d.procs)!==JSON.stringify(['`$d264de40319173e29c4db20af0188ea8:#101-t.serial_no#;#101-MultiRows#;#101-MultiCount#;#0-userinfo.userid#']))throw Error('SYSTEM_PRINT_NATIVE_DEFINITION_CHANGED');
    await callbacks();identity();
    const grid=document.getElementById('gridWF_YBX_101'),row=observed.row;
    const buttons=[...document.querySelectorAll('button[guid="179CD38964434F7E89CEEFE72D6D3E35"]')].filter(b=>visible(b)&&!b.disabled&&b.getAttribute('btnname')==='打印报销单');
    if(buttons.length!==1)throw Error('SYSTEM_PRINT_VISIBLE_BUTTON_REQUIRED');
    $(grid).jqGrid('resetSelection');$(grid).jqGrid('setSelection',row.id,true);
    const selected=$(grid).jqGrid('getGridParam','selarrrow');
    if(selected.length!==1||String(selected[0])!==String(row.id))throw Error('SYSTEM_PRINT_SELECTION_CHANGED');
    Object.defineProperty(root,'__ustcSystemPrintIntentV1',{value:{case_id:c.case_id,serial:p.serial,userid:p.expected_userid,row_id:row.id,phase:'print_open_requested'},configurable:true});
    buttons[0].click();return{ok:true,action:'system_print_open_requested',serial:p.serial,requiresReadback:true,approval_requested:false};
  }
  async function choose(){
    const intent=binding();if(intent.phase!=='print_open_requested'||p.print_export_authorized!==true||p.include_invoice_attachments!==false)throw Error('SYSTEM_PRINT_CHOICE_NOT_AUTHORIZED');
    await callbacks();
    const content=document.getElementById('printBxdWarnMsgBox101'),dialog=content?.closest('.ui-dialog');
    if(!visible(content)||!visible(dialog)||![...document.querySelectorAll('.ui-dialog')].filter(visible).every(x=>x===dialog)||
       !/是否打印发票等附件|为响应节约型校园要求，如您已有纸质附件，本步骤可不打印附件/.test(content.innerText))throw Error('SYSTEM_PRINT_ATTACHMENT_PROMPT_REQUIRED');
    const selected=$('#gridWF_YBX_101').jqGrid('getGridParam','selarrrow');
    if(selected.length!==1||String(selected[0])!==String(intent.row_id))throw Error('SYSTEM_PRINT_SELECTION_CHANGED');
    const buttons=[...dialog.querySelectorAll('button')].filter(b=>visible(b)&&!b.disabled&&b.innerText.trim()==='否');
    if(buttons.length!==1)throw Error('SYSTEM_PRINT_NO_ATTACHMENTS_BUTTON_REQUIRED');
    intent.phase='print_no_attachments_requested';buttons[0].click();
    return{ok:true,action:'system_print_choice_requested',serial:p.serial,include_invoice_attachments:false,requiresReadback:true,approval_requested:false};
  }
  function dismiss(){
    const intent=binding(),dialogs=[...document.querySelectorAll('.ui-dialog')].filter(visible);
    if(intent.phase!=='print_open_requested'||dialogs.length!==1||!/^提示信息\s*Close\s*该报销单正在审核中，请审核完成后打印\s*确认$/.test(dialogs[0].innerText.trim()))throw Error('SYSTEM_PRINT_KNOWN_REJECTION_REQUIRED');
    const buttons=[...dialogs[0].querySelectorAll('button')].filter(b=>visible(b)&&!b.disabled&&b.innerText.trim()==='确认');
    if(buttons.length!==1)throw Error('SYSTEM_PRINT_REJECTION_BUTTON_REQUIRED');
    intent.phase='rejected_wait_approval';buttons[0].click();
    return{ok:true,action:'system_print_waiting_school_approval',serial:p.serial,print_requested:false,system_write:false};
  }
  root.ReimbursementSystemPrint={configure,open,choose,read,dismiss,details,detailContinue,detailReturn,exportMenu,exportExecute};
})(globalThis);
