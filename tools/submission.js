(function(root){
  const visible=e=>!!e&&e.getClientRects().length>0;
  let c,p;
  function configure(config,plan){c=config;p=plan;}
  function guard(){
    if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX'||$.UC.bindData('#61-serialno#')!==p.serial)throw Error('提交单据身份变化');
    if(p.expected_userid&&$.UC.bindData('#0-userinfo.userid#')!==p.expected_userid)throw Error('提交登录账号变化');
  }
  const wait=async(test,message)=>{
    if(test())return;
    if(typeof MutationObserver==='undefined'){const deadline=Date.now()+15000;while(Date.now()<deadline){if(test())return;await new Promise(r=>setTimeout(r,100));}throw Error(message);}
    // 后台计时器会被浏览器节流；优先在原生DOM变化时检查，不额外等一轮定时器。
    await new Promise((resolve,reject)=>{
      let done=false,timer,deadline,observer;
      const finish=error=>{if(done)return;done=true;clearTimeout(timer);clearTimeout(deadline);observer?.disconnect();error?reject(error):resolve();};
      const check=()=>{try{if(test())finish();}catch(error){finish(error);}};
      const fallback=()=>{check();if(!done)timer=setTimeout(fallback,1000);};
      observer=new MutationObserver(check);observer.observe(document.documentElement,{subtree:true,childList:true,attributes:true,characterData:true});
      deadline=setTimeout(()=>finish(Error(message)),15000);check();if(!done)timer=setTimeout(fallback,1000);
    });
  };
  function preview(){guard();const f=document.getElementById('iframepage_print');if(!f?.contentDocument.body.innerText.includes('报销单号：'+p.serial))throw Error('本单预览未就绪');return f;}
  // 与read_current_state.js共享proof_row字段合同；只按原生首列识别业务类型。
  function proofRow(e){
    if(!e.name?.startsWith('form_proof_choose'))return undefined;
    const tr=e.closest('tr'),cells=tr?[...tr.cells].map(cell=>cell.innerText.trim()):[];
    return{type:cells[0]??null,description:cells[1]??null,note:cells[2]??null,cells,text:tr?.innerText.trim()??null,
      complete:cells.length===4&&!!cells[0]&&!!cells[1]};
  }
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
  const contractType=type=>typeof type==='string'&&/^合同号(?:\s*\[[\s\S]*\])?$/.test(type);
  function proofGroups(fields){
    const grouped=new Map();
    for(const e of fields.filter(e=>e.name.startsWith('form_proof_choose'))){
      const row=e.proof_row;
      if(e.type!=='radio'||row?.complete!==true||row.cells?.length!==4||row.type!==row.cells[0]||row.description!==row.cells[1]||row.note!==row.cells[2])throw Error('辅助证据选项缺少完整原生行类型，不能默认作合同');
      const previous=grouped.get(e.name);
      if(previous&&JSON.stringify(previous.row)!==JSON.stringify(row))throw Error('同组辅助证据原生行不一致');
      const group=previous||{name:e.name,row,fields:[]};group.fields.push(e);grouped.set(e.name,group);
    }
    for(const group of grouped.values())if(group.fields.length!==2||group.fields.filter(e=>e.value==='Y').length!==1||group.fields.filter(e=>e.value==='N').length!==1||group.fields.filter(e=>e.checked).length!==1)throw Error('辅助证据原生Y/N组不完整或选择不唯一');
    return [...grouped.values()];
  }
  function contractGroups(fields,inventory){
    const all=proofGroups(fields),groups=all.filter(group=>contractType(group.row.type)),proofFields=fields.filter(e=>e.name.startsWith('form_proof_choose'));
    const sectionNames=['title','serial','basic','expenses','payments','invoices','promise','footer','ticket_summary'];
    const counts=['table_count','row_count','input_count','captured_input_count','proof_control_count','proof_radio_count','proof_group_count','contract_row_count','contract_text_count','contract_bound_text_count','contract_unbound_text_count'];
    if(inventory?.schema!=='ustc-native-proof-inventory-v1'||inventory.preview_complete!==true||inventory.scan_complete!==true||inventory.truncated!==false||
       inventory.serial!==p.serial||inventory.preview_serials?.length!==1||inventory.preview_serials[0]!==p.serial||inventory.frame_id!=='iframepage_print'||
       inventory.frame_url!=='https://cwzh.ustc.edu.cn/YBX/redirectCustomPage.action'||inventory.document_ready_state!=='complete'||inventory.frame_visible!==true||
       !inventory.sections||Object.keys(inventory.sections).length!==9||!sectionNames.every(key=>inventory.sections[key]===true)||
       !counts.every(key=>Number.isInteger(inventory[key])&&inventory[key]>=0)||inventory.table_count<1||inventory.row_count<1||
       inventory.input_count<inventory.captured_input_count||inventory.captured_input_count<fields.length||
       inventory.contract_unbound_text_count!==0||inventory.contract_text_count!==inventory.contract_bound_text_count||
       inventory.proof_control_count!==proofFields.length||inventory.proof_radio_count!==proofFields.length||inventory.proof_group_count!==all.length||
       inventory.contract_row_count!==groups.length||inventory.contract_rows?.length!==groups.length)throw Error('缺少完整同号原生预览辅助证据清点，不能推定无合同');
    const covered=new Set();
    for(const row of inventory.contract_rows){
      const group=groups.find(group=>row.control_names?.length===1&&row.control_names[0]===group.name);
      if(!group||covered.has(group.name)||row.type!==group.row.type||row.cell_count!==4||row.control_count!==2||row.radio_count!==2)throw Error('合同类型行未完整提取原生radio，保留现场核对');
      covered.add(group.name);
    }
    if(!groups.length&&inventory.contract_text_count!==0)throw Error('预览仍有合同号文本但无完整合同控件，不能推定不适用');
    if(groups.some(group=>!/^共\s*0\s*笔$/.test(group.row.description)))throw Error('合同已有关联，须核对实际合同');
    return groups;
  }
  function optionsReady(){
    const observed=inspect(),fields=observed.fields,prints=fields.filter(e=>e.name==='form_grantcwprint'&&e.checked);
    if(observed.proof_inventory?.preview_complete!==true)return false;
    return prints.length===1&&prints[0].value==='N'&&contractGroups(fields,observed.proof_inventory).every(group=>group.fields.some(e=>e.value==='N'&&e.checked));
  }
  function nativeButton(scope,caption){const buttons=[...scope.querySelectorAll('button')].filter(e=>visible(e)&&e.getAttribute('btnname')===caption);if(buttons.length!==1)throw Error('原生按钮不唯一：'+caption);return buttons[0];}
  function inspect(){
    guard();const f=document.getElementById('iframepage_print'),d=f?.contentDocument;
    const fields=d?[...d.querySelectorAll('input[name="form_grantcwprint"],input[name^="form_proof_choose"]')].map(e=>({name:e.name,type:e.type,value:e.value,checked:e.checked,disabled:e.disabled,visible:visible(e),onclick:e.getAttribute('onclick'),onchange:e.getAttribute('onchange'),row:e.closest('tr')?.innerText.trim(),proof_row:proofRow(e)})):[];
    const windows=[];for(const page of Object.values($.page.list||{}))for(const w of page.wins||[]){
      if(!visible(document.getElementById('winWF_YBX_'+w.winno)))continue;
      windows.push({winno:w.winno,title:w.title,funcno:w.funcno,buttons:(w.func_btns||[]).map(b=>({caption:b.caption,hasProcedure:!!b.call_proc,workflowTransition:!!b.wf_trans}))});
    }
    const departmentCandidates=c.funding.map((fund,i)=>{const id='formWF_YBX_1369_t-drive_condition_'+i,label=document.getElementById('formWF_YBX_1369_t-condition_name_'+i);if(!visible(label))return null;const control=$('#'+id).data('xmSelect');return{project:fund.code,label:label.value,options:(control?.options?.data||[]).map(x=>({id:x.value,label:x.name})),selected:(control?.getValue()||[]).map(x=>({id:x.value,label:x.name}))};}).filter(Boolean);
    return {ok:true,action:'submission_inspect',serial:p.serial,projid:$.getProjID(),node:$.UC.bindData('#61-node_id#'),nodeName:$.UC.bindData('#61-node_name#'),captchaRequired:$.UC.bindData('#0-NEED_SP_YZM#'),applicantAgreementCheck:typeof root.before_lc_agree==='function'?String(root.before_lc_agree).slice(0,16000):null,verificationCheck:typeof root.before_sp_yzm==='function'?String(root.before_sp_yzm).slice(0,12000):null,fields,proof_inventory:d?proofInventory(f,p.serial):null,windows,departmentCandidates,
      visibleButtons:[...document.querySelectorAll('button')].filter(visible).map(e=>({caption:e.getAttribute('btnname')||e.innerText.trim(),onclick:e.getAttribute('onclick')})),
      visibleInputs:[...document.querySelectorAll('input,select,textarea')].filter(e=>visible(e)&&e.type!=='hidden'&&e.type!=='password').map(e=>({id:e.id,name:e.name,type:e.type,value:/yzm|captcha|sms|otp/i.test(e.id+' '+e.name)||e.id==='formWF_YBX_1362_t-code'?'[protected]':e.value,checked:e.checked,options:e.options?[...e.options].map(o=>({text:o.text,value:o.value})):undefined})),system_write:false};
  }
  async function deliveryOpen(){
    guard();const f=document.getElementById('iframepage_print');
    if(!f?.contentDocument.body.innerText.includes('报销单号：'+p.serial))throw Error('本单预览未就绪');
    const links=[...f.contentDocument.querySelectorAll('[onclick]')].filter(e=>visible(e)&&e.getAttribute('onclick')==="pageOpen('委托财务处打印')");
    if(links.length!==1)throw Error('委托打印原生入口不唯一');links[0].click();
    return {ok:true,action:'open_delivery_options',serial:p.serial,requiresReadback:true};
  }
  async function options(){
    const f=preview(),d=f.contentDocument;
    if(p.contract_exists!==false||p.delegate_finance_print!==false)throw Error('本动作仅处理无合同、不委托打印');
    const observed=inspect(),fields=observed.fields,groups=proofGroups(fields),contractsSelected=contractGroups(fields,observed.proof_inventory);
    const names=new Set(contractsSelected.map(group=>group.name));
    const contracts=[...d.querySelectorAll('input[name^="form_proof_choose"][value="N"]')].filter(e=>names.has(e.name));
    const prints=[...d.querySelectorAll('input[name="form_grantcwprint"][value="N"]')];
    const printFields=fields.filter(e=>e.name==='form_grantcwprint');
    if(contracts.length!==names.size||prints.length!==1||printFields.length!==2||printFields.some(e=>e.type!=='radio')||printFields.filter(e=>e.value==='Y').length!==1||printFields.filter(e=>e.checked).length!==1)throw Error('合同或打印选择入口变化');
    for(const e of contracts)if(!visible(e)||e.disabled)throw Error('合同选择不可编辑，须核对实际合同');
    if(!visible(prints[0])||prints[0].disabled)throw Error('打印选项不可编辑');
    const otherBefore=groups.filter(group=>!names.has(group.name)).map(group=>({name:group.name,row:group.row,checked_value:group.fields.find(e=>e.checked).value}));
    for(const e of contracts)if(!e.checked)e.click();
    if(!prints[0].checked)prints[0].click();
    await wait(optionsReady,'合同/打印选项尚未完成更新');
    const after=proofGroups(inspect().fields);
    for(const previous of otherBefore){const current=after.find(group=>group.name===previous.name);if(!current||JSON.stringify(current.row)!==JSON.stringify(previous.row)||current.fields.find(e=>e.checked).value!==previous.checked_value)throw Error('非合同辅助证据选项发生变化，保留现场并独立核对');}
    return {ok:true,action:'select_submission_options',serial:p.serial,contract_exists:false,delegate_finance_print:false,
      contract_control_present:contractsSelected.length>0,contract_control_status:contractsSelected.length?'present_confirmed_no_contract':'not_applicable_in_complete_preview',
      proof_inventory:inspect().proof_inventory,verified_scope:'contract_and_finance_print_only',other_auxiliary_rows:otherBefore,selection_staged:true,requiresReadback:true};
  }
  async function next(){
    const f=preview(),text=f.contentDocument.body.innerText;
    if(!optionsReady())throw Error('须先确认所有合同无、委托打印否');
    const tight=text.replace(/\s+/g,''),tickets=[...tight.matchAll(/共选择([0-9]+)张发票[,，、]?合计([0-9,.]+)元/g)],totals=[...tight.matchAll(/金额总计[:：]?([0-9,.]+)元?/g)];
    const cents=value=>{const clean=String(value??'').replace(/,/g,'');if(!/^\d+(?:\.\d{1,2})?$/.test(clean))throw Error('金额格式不完整');return Math.round(Number(clean)*100);};
    const summaries=[...f.contentDocument.querySelectorAll('tr')].filter(row=>row.cells.length===2&&row.cells[0].textContent?.trim()==='摘要').map(row=>row.cells[1].textContent.trim());
    if((summaries.length? summaries.length!==1||summaries[0]!==p.summary:!text.includes(p.summary))||!text.includes(p.phone)||tickets.length!==1||totals.length!==1||Number(tickets[0][1])!==c.expected_invoice_count||cents(tickets[0][2])!==cents(c.submission_invoice_face_total)||cents(totals[0][1])!==cents(p.amount))throw Error('摘要、电话、申请总额或原票面合计不符');
    if(/\*错误\*/.test(text))throw Error('本单仍有原生材料错误，不能提交');
    for(const pay of c.payments){const amount=(Number(pay.amount)).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});if(![...f.contentDocument.querySelectorAll('table')].flatMap(t=>[...t.rows]).some(row=>{const cells=[...row.cells].map(x=>x.innerText.trim());return cells.includes(pay.name)&&cells.includes(amount)&&(pay.mode!=='bank_transfer'||cells.includes(pay.person_id));}))throw Error('支付身份或金额变化：'+pay.person_id);}
    nativeButton(document.querySelector('.foot_winWF_YBX_41'),'下一步').click();
    return {ok:true,action:'submission_next',serial:p.serial,requiresReadback:true};
  }
  async function promise(){
    guard();await wait(()=>{guard();return [...document.querySelectorAll('.winDiv button[btnname="已阅读遵守且提交"]')].filter(visible).length===1;},'本人承诺提交入口不唯一或尚未到达');
    const buttons=[...document.querySelectorAll('.winDiv button[btnname="已阅读遵守且提交"]')].filter(visible);
    if(buttons.length!==1)throw Error('本人承诺提交入口不唯一或尚未到达');
    const approve=[...document.querySelectorAll('.foot_winWF_YBX_76 button[btnname="同意"]')].filter(visible);
    if(approve.length)throw Error('已进入最终本人同意页，验证码和本人同意保留给用户');
    buttons[0].click();return {ok:true,action:'promise_submitted',serial:p.serial,requiresReadback:true,final_agreement:false};
  }
  async function department(){guard();return await ReimbursementAutomation.departmentApprovers(c);}
  async function verificationCancel(){
    guard();if(!p.expected_userid||String($.UC.bindData('#61-node_id#'))!=='508')throw Error('只暂缓本人的申请人短信验证');
    const code=document.getElementById('formWF_YBX_1362_t-code');if(!visible(code)||String(code.value||'').trim())throw Error('验证码输入不为空或窗口已改变，保留现场');
    const dialogs=[...document.querySelectorAll('.ui-dialog')].filter(visible);
    if(dialogs.length!==1||!dialogs[0].contains(code)||!dialogs[0].innerText.includes('短信验证码'))throw Error('不是唯一原生短信验证窗口');
    const defs=Object.values($.page.list||{}).flatMap(page=>(page.wins||[]).filter(w=>String(w.winno)==='1160'&&String(w.funcno)==='1362').flatMap(w=>w.func_btns||[])).filter(b=>b.caption==='取消');
    if(defs.length!==1||defs[0].call_proc!==false||defs[0].wf_trans||(defs[0].procs||[]).length)throw Error('验证码取消入口出现后台或流程动作');
    const closes=[...dialogs[0].querySelectorAll('button.ui-dialog-titlebar-close')].filter(e=>visible(e)&&!e.disabled);
    if(closes.length!==1)throw Error('原生验证码窗口关闭按钮不唯一');closes[0].click();
    await wait(()=>!visible(dialogs[0])||!visible(code),'原生验证取消尚未完成，仅回读');
    return{ok:true,action:'verification_deferred',serial:p.serial,system_write:false,agreement_executed:false};
  }
  async function applicantReturn(){
    guard();if(!p.expected_userid||String($.UC.bindData('#61-node_id#'))!=='508'||[...document.querySelectorAll('.ui-dialog')].some(visible))throw Error('只返回本人申请人节点且无验证窗口');
    const field=document.getElementById('formWF_YBX_109_t-remark');
    if(!visible(field)||!['','同意'].includes(field.value))throw Error('当前有非默认意见，保留现场');
    const defs=Object.values($.page.list||{}).flatMap(page=>(page.wins||[]).filter(w=>String(w.winno)==='76').flatMap(w=>w.func_btns||[])).filter(b=>b.btnguid==='84D260E3256A4CE6ABEA1B09CA4EBF10'&&b.caption==='返回');
    if(defs.length!==1||defs[0].call_proc!==false||defs[0].wf_trans||(defs[0].procs||[]).length||String(defs[0].afterclick).replace(/\s+/g,'')!=='function(op){init_flag_printwhere();returnwf_returnHome_sp();}')throw Error('本人审核返回定义变化');
    const button=nativeButton(document.querySelector('.foot_winWF_YBX_76'),'返回');if(button.getAttribute('guid')!==defs[0].btnguid)throw Error('本人审核返回按钮身份变化');
    button.click();return{ok:true,action:'applicant_return_requested',serial:p.serial,system_write:false,agreement_executed:false,requiresReadback:true};
  }
  function templateInspect(){
    if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX'||!p.expected_userid||$.UC.bindData('#0-userinfo.userid#')!==p.expected_userid)throw Error('模板观察账号变化');
    const functions=['before_applycheck','ybxmain_filesize','jbxxNext_v1','checkCommit_V1','print_bxd_prompt_after_101','handlePrintAction','before_dsp_approval','after_dsp_approval','before_apply_list_dtl','after_apply_list_dtl','printGrid'].map(name=>{const d=Object.getOwnPropertyDescriptor(root,name),source=d&&'value'in d&&typeof d.value==='function'?String(d.value):null;return{name,source:source?.slice(0,24000),capture_complete:source!==null&&source.length<=24000};});
    const gridExport=Object.getOwnPropertyDescriptor($.grid||{},'printGrid')?.value;
    functions.push({name:'$.grid.printGrid',source:typeof gridExport==='function'?String(gridExport).slice(0,24000):null,capture_complete:typeof gridExport==='function'&&String(gridExport).length<=24000});
    return{ok:true,action:'inspect_fixed_native_template_functions',identity:{userid:p.expected_userid},functions,serial:$.UC.bindData('#61-serialno#'),node:$.UC.bindData('#61-node_id#'),pendingNativeRequests:$.active,system_write:false,functions_called:false};
  }
  async function dismissValidation(){
    guard();if(!p.expected_userid||!['','535'].includes(String($.UC.bindData('#61-node_id#')||'')))throw Error('不是本单已拒绝的下一步提示');
    const dialogs=[...document.querySelectorAll('.ui-dialog')].filter(visible);
    if(dialogs.length!==1||!/^提示信息\s*Close\s*还有(?:[1-9][0-9]*个补充说明未填写|资产购置清单\s*等[1-9][0-9]*个辅助证据未关联明细)！\s*确认$/.test(dialogs[0].innerText.trim()))throw Error('仅关闭明确缺说明或资产清单的通知，不确认未知问题');
    const buttons=[...dialogs[0].querySelectorAll('button')].filter(e=>visible(e)&&e.innerText.trim()==='确认'&&!e.disabled);
    if(buttons.length!==1)throw Error('缺说明通知确认入口不唯一');buttons[0].click();
    await wait(()=>!visible(dialogs[0]),'原提示关闭尚未完成，仅回读');
    return{ok:true,action:'dismissed_missing_supplement_notice',serial:p.serial,system_write:false,approval_requested:false};
  }
  function printInspect(){
    if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX'||!p.expected_userid||$.UC.bindData('#0-userinfo.userid#')!==p.expected_userid)throw Error('打印入口观察账号变化');
    const grid=document.getElementById('gridWF_YBX_101');if(!visible(grid)||grid.grid?.hDiv?.loading)throw Error('原生报销单列表未就绪');
    const ids=$(grid).jqGrid('getDataIDs'),matches=ids.map(id=>({id,row:$(grid).jqGrid('getRowData',id)})).filter(v=>v.row['t.serial_no']===p.serial);
    if(matches.length!==1||Number(String(matches[0].row['t.total_amt']).replaceAll(',',''))!==Number(p.amount)||!String(matches[0].row['t.REQ_INFO']||'').startsWith(p.expected_userid+'-'))throw Error('打印观察须唯一同号、同金额、同申请人的原生行');
    const defs=Object.values($.page.list||{}).flatMap(page=>(page.wins||[]).filter(w=>String(w.winno)==='71').flatMap(w=>w.func_btns||[])).filter(b=>b.btnguid==='179CD38964434F7E89CEEFE72D6D3E35');
    if(defs.length!==1)throw Error('打印报销单原生定义不唯一');
    const def=defs[0],keys=['btnguid','caption','pre_check','call_proc','check_func','procs','post_check','acts','wf_enabled','wf_trans'];
    const definition=Object.fromEntries(keys.map(k=>[k,def[k]??null]));definition.beforeclick=String(def.beforeclick);definition.afterclick=String(def.afterclick);
    const descriptor=Object.getOwnPropertyDescriptor(root,'print_bxd_prompt_after_101');
    return{ok:true,action:'print_entry_inspect',serial:p.serial,row:matches[0],selected:$(grid).jqGrid('getGridParam','selarrrow'),definition,
      callback_source:descriptor&&'value'in descriptor&&typeof descriptor.value==='function'?String(descriptor.value).slice(0,24000):null,
      source_capture_complete:!!descriptor&&typeof descriptor.value==='function'&&String(descriptor.value).length<=24000,system_write:false,buttons_clicked:false};
  }
  async function applicantOpen(){
    if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX'||!p.expected_userid||$.UC.bindData('#0-userinfo.userid#')!==p.expected_userid||c.review?.workflow_status!=='applicant_review_pending')throw Error('本人审批入口账号或登记状态变化');
    // 在同一工作页接续下一单，不为每单重开窗口或重复读取整份列表。
    if(!visible(document.getElementById('gridWF_YBX_105'))){
      if([...document.querySelectorAll('.ui-dialog')].some(visible)||$.active!==0)throw Error('仍有未处理窗口或请求');
      if(String($.page.currPage)!=='6'){
        const list=document.getElementById('gridWF_YBX_101'),heads=[...document.querySelectorAll('h2')].filter(e=>visible(e)&&e.textContent.trim()==='中国科学技术大学');
        const onclick="if(defaultWin){var defWin=defaultWin.split(':');$.page.openExternal(defWin[0],defWin[1],defWin[2]);}else{window.location.reload();}";
        const forms=[...document.querySelectorAll('[id^="formWF_YBX_"]')].filter(visible);
        if(!visible(list)||list.grid?.hDiv?.loading||forms.some(e=>!/^formWF_YBX_(?:99|514)(?:_|$)/.test(e.id))||Object.getOwnPropertyDescriptor(root,'defaultWin')?.value!=='WF_YBX:6:SY-个人首页'||heads.length!==1||heads[0].getAttribute('onclick')!==onclick)throw Error('申请人列表返回入口变化');
        heads[0].click();
      }
      await wait(()=>String($.page.currPage)==='6'&&$.active===0&&visible(document.getElementById('result206_title')),'首页尚未就绪，保留原打开任务');
      const title=document.getElementById('result206_title');
      if(String($.UC.bindData('#61-serialno#')||'').trim()||!/^待我审批\s*您有[0-9]+张报销单需要审核/.test(title.textContent.trim()))throw Error('待我审批首页范围变化');
      title.click();
      await wait(()=>{const g=document.getElementById('gridWF_YBX_105');return visible(g)&&g.grid?.hDiv?.loading===false&&$.active===0;},'待本人审批列表尚未就绪，仅恢复原打开');
    }
    const grid=document.getElementById('gridWF_YBX_105');
    if(!visible(grid)||grid.grid?.hDiv?.loading||$.active!==0||[...document.querySelectorAll('.ui-dialog')].some(visible))throw Error('本人待审批列表尚未就绪');
    const matches=$(grid).jqGrid('getDataIDs').map(id=>({id,row:$(grid).jqGrid('getRowData',id)})).filter(x=>x.row['t.serial_no']===p.serial);
    if(matches.length!==1)throw Error('本单待审批行不唯一');
    const hit=matches[0],row=hit.row;
    if(row['t.basetable_key']!==p.serial||String(row['t.node_id'])!=='508'||row['t.status_name']!=='申请人'||String(row['t.nodecnt'])!=='1'||row['t.wfname']!=='YBX_LC'||row['t.wf_name']!=='YBX_LC'||row['t.collab_ord']!=='none'||row['t.isNeedCollab']!=='F'||!String(row['t.curr_mans']).startsWith(p.expected_userid+' - ')||!String(row['t.node_name1']).startsWith('申请人/审批('+p.expected_userid+' - ')||Number(row['t.amount'])!==Number(p.amount)||row['t.remark']!==p.summary)throw Error('本单角色、办理人、金额或摘要不符，不打开审批');
    const guid='1DBA02EC959A452F82CF7ABB18E62B95',defs=($.Q.list['105']?.grid_btns||[]).filter(d=>d.btnguid===guid&&d.caption==='审批');
    if(defs.length!==1)throw Error('原生申请人入口定义不唯一');
    const d=defs[0],norm=v=>String(v).replace(/\s+/g,'');
    if(d.pre_check!==false||d.call_proc!==true||d.post_check!==true||d.wf_trans||d.wf_state||d.sign_enabled!==false||d.wfsignenabled!==false||JSON.stringify(d.procs)!==JSON.stringify(['`$6b37df0ed70c35116868a16c5af5f5f1:#105-t.basetable_key#'])||d.acts!=='1==1:WF(#C-wf_inst.wf_name#, #C-wf_inst.node_id#, #C-wf_inst.project_id#, #C-wf_inst_user.collab_ord#)'||norm(d.beforeclick)!=='function(op){before_dsp_approval();}'||norm(d.afterclick)!=='function(op){after_dsp_approval();}')throw Error('申请人打开流程的原生定义变化');
    for(const [name,expected] of [['before_dsp_approval','eb1da995b0b7ff3ebb24316eda9c7c16cea3ed4307efd197596c8674e1c817a0'],['after_dsp_approval','a470bd3491cdd50944752dfadcfdfb413cd8ecc4a61e01ea567e4caa758472d3']]){
      const value=Object.getOwnPropertyDescriptor(root,name)?.value;if(typeof value!=='function')throw Error('原生打开回调缺失');
      const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(String(value))),sha=[...new Uint8Array(bytes)].map(x=>x.toString(16).padStart(2,'0')).join('');
      if(sha!==expected)throw Error('原生打开回调变化，先核对新定义');
    }
    const buttons=[...document.getElementById(hit.id).querySelectorAll('button')].filter(e=>visible(e)&&!e.disabled&&e.getAttribute('guid')===guid&&e.getAttribute('btnname')==='审批');
    if(buttons.length!==1)throw Error('本行审批入口不唯一');buttons[0].click();
    await wait(()=>$.UC.bindData('#61-serialno#')===p.serial&&String($.UC.bindData('#61-node_id#'))==='508'&&visible(document.getElementById('formWF_YBX_109_t-remark')),'本人审批页尚未就绪，仅回读原打开结果');
    return{ok:true,action:'applicant_page_opened',serial:p.serial,node:'508',system_write:false,agreement_executed:false,requiresReadback:true};
  }
  async function submissionStatus(){
    const identity=()=>{if(location.origin!=='https://cwzh.ustc.edu.cn'||location.pathname!=='/YBX/main.jsp'||$.getProjID()!=='WF_YBX'||!p.expected_userid||$.UC.bindData('#0-userinfo.userid#')!==p.expected_userid)throw Error('提交状态导航身份变化');};
    identity();
    const activeAgreement=()=>[...document.querySelectorAll('.foot_winWF_YBX_76 button[btnname="同意"]')].some(visible);
    if(activeAgreement()&&p.applicant_agreement_authorized===false&&c.review?.workflow_status==='applicant_review_pending'){
      if([...document.querySelectorAll('.ui-dialog')].some(visible))throw Error('仍有本人验证码窗口，先沿暂缓动作关闭');
      const opinion=document.getElementById('formWF_YBX_109_t-remark');
      if(!visible(opinion)||!['','同意'].includes(opinion.value)||[...document.querySelectorAll('input[type="file"]')].some(e=>e.files?.length))throw Error('原审批页存在非默认意见或未保存文件，保留现场');
      const serial=String($.UC.bindData('#61-serialno#')||'');if(serial&&serial!==p.serial)throw Error('暂缓返回时原单号变化');
      const def=Object.getOwnPropertyDescriptor(root,'defaultWin'),heads=[...document.querySelectorAll('h2')].filter(e=>visible(e)&&e.textContent.trim()==='中国科学技术大学');
      const onclick="if(defaultWin){var defWin=defaultWin.split(':');$.page.openExternal(defWin[0],defWin[1],defWin[2]);}else{window.location.reload();}";
      if(def?.value!=='WF_YBX:6:SY-个人首页'||heads.length!==1||heads[0].getAttribute('onclick')!==onclick)throw Error('暂缓后的原生首页入口变化');
      heads[0].click();await wait(()=>{identity();return!activeAgreement()&&[...document.querySelectorAll('.pageTitle[title="审批中"]')].some(visible);},'原生暂缓返回首页尚未完成，仅回读');
    }
    if([...document.querySelectorAll('.ui-dialog')].some(visible)||[...document.querySelectorAll('.foot_winWF_YBX_76 button[btnname="同意"]')].some(visible))throw Error('仍有本人校验或审核现场，仅回读不切换');
    const forms=[...document.querySelectorAll('[id^="formWF_YBX_"]')].filter(visible);
    if(forms.some(e=>!/^formWF_YBX_(?:99|514)(?:_|$)/.test(e.id)))throw Error('仍有业务编辑表单，不切换');
    const ready=()=>[...document.querySelectorAll('.pageTitle[title="审批中"]')].filter(visible);
    if(ready().length!==1){
      const list=[...document.querySelectorAll('#gridWF_YBX_105,#gridWF_YBX_101')].filter(visible);
      const serial=String($.UC.bindData('#61-serialno#')||'');
      if(list.length!==1||(serial&&serial!==p.serial))throw Error('提交后原生列表未就绪，保留现场');
      await wait(()=>{identity();return $.active===0&&list.every(g=>g.grid?.hDiv?.loading===false);},'提交后原生列表仍在加载，仅回读');
      const def=Object.getOwnPropertyDescriptor(root,'defaultWin'),heads=[...document.querySelectorAll('h2')].filter(e=>visible(e)&&e.textContent.trim()==='中国科学技术大学');
      const onclick="if(defaultWin){var defWin=defaultWin.split(':');$.page.openExternal(defWin[0],defWin[1],defWin[2]);}else{window.location.reload();}";
      if(def?.value!=='WF_YBX:6:SY-个人首页'||heads.length!==1||heads[0].getAttribute('onclick')!==onclick)throw Error('提交后原生首页入口变化');
      heads[0].click();await wait(()=>{identity();return ready().length===1&&$.active===0;},'原生首页尚未就绪，仅回读');
    }
    identity();if(ready().length!==1)throw Error('审批中原生入口不唯一');ready()[0].click();
    await wait(()=>{identity();const grid=document.getElementById('gridWF_YBX_101');return visible(grid)&&grid.grid?.hDiv?.loading===false&&$.active===0;},'审批中列表尚未就绪，仅回读');
    return{ok:true,action:'open_in_progress_readonly',expected_serial:p.serial,system_write:false,requiresReadback:true};
  }
  async function applicantAgree(){
    guard();
    if(p.applicant_agreement_authorized!==true||p.approval_opinion!=='同意'||!p.expected_userid||String($.UC.bindData('#61-node_id#'))!=='508')throw Error('本人同意须明确授权并处于申请人508节点');
    const captcha=$.UC.bindData('#0-NEED_SP_YZM#');
    // Y表示启用校验；原生before_sp_yzm还会检查已验证会话。不能把配置Y误判为实际待输验证码，也不改该配置。
    if([...document.querySelectorAll('input,select,textarea')].some(e=>visible(e)&&(/yzm|captcha|sms|otp/i.test(e.id+' '+e.name)||e.id==='formWF_YBX_1362_t-code')))throw Error('本单已出现本人验证码输入，保留现场');
    if([...document.querySelectorAll('.ui-dialog')].some(visible))throw Error('本单仍有未处理对话框');
    const grid=document.getElementById('gridWF_YBX_1816');if(!visible(grid)||grid.grid?.hDiv?.loading)throw Error('申请人日志未就绪');
    const ids=$(grid).jqGrid('getDataIDs'),count=$(grid).jqGrid('getGridParam','records');
    if(!Number.isInteger(Number(count))||Number(count)!==ids.length||ids.length<1||ids.length>100)throw Error('申请人日志分页未完整');
    const pending=ids.map(id=>$(grid).jqGrid('getRowData',id)).filter(row=>String(row['t.node_id'])==='508'&&row['t.node_name']==='预约流程-申请人'&&String(row['t.appinfo']||'').startsWith(p.expected_userid+'-')&&!row['t.operator']);
    if(pending.length!==1)throw Error('待审批人并非当前申请人，不能代其他角色审批');
    const defs=Object.values($.page.list||{}).flatMap(page=>(page.wins||[]).filter(w=>String(w.winno)==='76').flatMap(w=>w.func_btns||[])).filter(b=>b.btnguid==='EF5365A90D3947A8B0272D9339EA8076'&&b.caption==='同意');
    if(defs.length!==1||defs[0].wf_enabled!==true||defs[0].wf_state!=='agree'||defs[0].wf_trans!=='APPROVAL'||defs[0].wf_comment!=='#109-t.remark#'||defs[0].pre_check!==true||defs[0].post_check!==true||defs[0].call_proc!==true)throw Error('申请人同意原生流程定义变化');
    const field=document.getElementById('formWF_YBX_109_t-remark');if(!visible(field)||field.disabled)throw Error('审批意见原生输入未就绪');
    const scope=document.querySelector('.foot_winWF_YBX_76'),button=scope&&nativeButton(scope,'同意');
    if(!button||button.disabled||button.getAttribute('guid')!=='EF5365A90D3947A8B0272D9339EA8076')throw Error('申请人同意原生按钮身份变化');
    $.F.setInputData('formWF_YBX_109_t-remark','textarea','同意','');$.F.getDataToContext('109');
    if(field.value!=='同意'||$.UC.bindData('#109-t.remark#')!=='同意')throw Error('审批意见未完成原生绑定');
    guard();button.click();
    await wait(()=>visible(document.getElementById('formWF_YBX_1362_t-code'))||!visible(field),'本人同意尚未返回，保留原请求');
    return{ok:true,action:'applicant_agree_requested',serial:p.serial,applicant_userid:p.expected_userid,opinion:'同意',node:'508',native_verification_enabled:captcha==='Y',verification_required:visible(document.getElementById('formWF_YBX_1362_t-code')),requiresReadback:true,leadership_approval_completed:false,payment_completed:false};
  }
  root.ReimbursementSubmission={configure,inspect,deliveryOpen,options,next,promise,department,applicantAgree,submissionStatus,printInspect,templateInspect,applicantOpen,dismissValidation,verificationCancel,applicantReturn};
})(globalThis);
