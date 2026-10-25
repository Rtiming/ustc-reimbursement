(function(root) {
  const visible=e=>e && e.getClientRects().length;
  const money=v=>{const s=String(v).replaceAll(',','').trim();if(!/^\d+(?:\.\d{1,2})?$/.test(s))throw new Error('金额格式异常');const [a,b='']=s.split('.');const n=Number(a)*100+Number(b.padEnd(2,'0'));if(!Number.isSafeInteger(n))throw new Error('金额越界');return n;};
  let serial,caseAmount;
  function configure(c){serial=c.serial;caseAmount=money(c.amount);if(!/^\d+$/.test(serial))throw new Error('单号未配置');}
  const decimal=n=>(n/100).toFixed(2);
  const wait=async(test,message)=>{const deadline=Date.now()+20000;while(Date.now()<deadline){if(test())return;await new Promise(r=>setTimeout(r,100));}throw new Error(message);};
  function guard(){if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX'||$.UC.bindData('#61-serialno#')!==serial)throw new Error('本案身份变化');if(visible(document.querySelector('.foot_winWF_YBX_76 button[btnname="同意"]')))throw new Error('当前为本人审核，须退回原生修改入口，不按草稿写入');}
  function button(scope,caption){const b=[...scope.querySelectorAll('button')].filter(e=>visible(e)&&e.getAttribute('btnname')===caption);if(b.length!==1)throw new Error('原生按钮不唯一：'+caption);return b[0];}
  async function allRows(){
    const g=$('#gridWF_YBX_1669');if(!visible(g[0]))throw new Error('逐票拆分未打开');
    const count=g.jqGrid('getGridParam','records');if(count<1||count>100)throw new Error('逐票记录数异常');
    if(g.jqGrid('getDataIDs').length!==count){g.jqGrid('setGridParam',{rowNum:100,page:1}).trigger('reloadGrid');await wait(()=>g.jqGrid('getDataIDs').length===count&&!g[0].grid.hDiv.loading,'完整逐票列表未就绪');}
    return g.jqGrid('getDataIDs').map(id=>{const row=g.jqGrid('getRowData',id);const cell=document.getElementById(id)?.querySelector('[aria-describedby="gridWF_YBX_1669_t.sj_bf"]');if(cell)row['t.sj_bf']=cell.innerText;return {id,row};});
  }
  async function select(key){
    guard();const g=$('#gridWF_YBX_1666');if(!visible(g[0]))throw new Error('经费列表未加载');
    const rows=g.jqGrid('getDataIDs').map(id=>({id,row:g.jqGrid('getRowData',id)})).filter(x=>x.row['t.select_key']===key);
    if(rows.length!==1)throw new Error('目标经费和科目不唯一');
    if($.UC.bindData('#1666-t.select_key#')!==key)document.getElementById(rows[0].id).click();
    await wait(()=>$.UC.bindData('#1666-t.select_key#')===key,'目标经费未绑定');
    await wait(()=>!$('#gridWF_YBX_1669')[0].grid.hDiv.loading&&$('#gridWF_YBX_1669').jqGrid('getDataIDs').every(id=>{
      const r=$('#gridWF_YBX_1669').jqGrid('getRowData',id);
      // 原生页面默认填“总额减其他报销项已分担”，不是当前项已保存金额。
      const amount=money(r['t.amt'])-money(r['t.sj_amt']||'0');
      const e=document.getElementById('input_t_split_amt_'+id);
      return e&&money(e.value||0)===amount;
    }),'目标经费金额尚未独立加载');
    return {ok:true,action:'select_split_funding',serial,key};
  }
  async function update(key,group,kind,plan){
    const policy=plan.policy;const selectedProject=key.split('!P!')[1];if(!['clear','wc','ww','life-ww'].includes(kind))throw new Error('未知修正模式');if((kind==='wc'||kind==='clear')&&selectedProject!==policy.project)throw new Error('团委模式经费不符');if((kind==='ww'||kind==='life-ww')&&selectedProject===policy.project)throw new Error('其他经费模式不符');if(kind==='wc'&&key!==[policy.subject,policy.project,policy.budget].join('!P!'))throw new Error('团委仅允许交通科目');
    guard();if($.UC.bindData('#1666-t.select_key#')!==key)throw new Error('选中经费不是预期目标');
    const rows=await allRows();
    if($.UC.bindData('#1666-t.select_key#')!==key)throw new Error('加载期间经费选择变化');
    const targets=plan.people.filter(x=>x.group===group);
    const found=[];const changes=[];let total=0;
    for(const {id,row:r} of rows){
      if(r['t.serial_no']!==serial)throw new Error('拆分明细混入其他单据');
      const source=money(r['t.amt']);if(!Number.isSafeInteger(source)||source<=0)throw new Error('票据金额异常');
      const p=targets.find(x=>x.invoice_guid===r['t.tk_guid']);
      const qualifies=p&&(group!=='交通运输服务'||r['t.invoice_type'].includes('国内机票款'));
      const wc=qualifies?money(p.wc_amount):0;
      if(wc>source||wc>money(policy.person_cap))throw new Error('单票额度不足或超过个人上限');
      let next;
      if(kind==='clear')next=0;
      else if(kind==='wc')next=wc;
      else if(kind==='ww')next=source-wc;
      else if(kind==='life-ww'){
        const start=String(r['t.sj_bf']).indexOf(key.replaceAll('!P!','-')+'-');
        next=start<0?0:money(String(r['t.sj_bf']).slice(start+(key.replaceAll('!P!','-')+'-').length).match(/^\d+(?:\.\d+)?/)[0]);
        if(qualifies)next=source-wc;
      } else throw new Error('未知修正模式');
      const e=document.getElementById('input_t_split_amt_'+id);if(!visible(e)||e.readOnly||e.disabled)throw new Error('拆分金额不可编辑');
      changes.push({e,next});
      total+=next;if(qualifies)found.push({name:p.name,guid:p.invoice_guid,amount:decimal(wc),invoiceDetail:r['t.invoice_type']});
    }
    if(kind!=='clear'&&new Set(found.map(x=>x.guid)).size!==targets.length)throw new Error('本类去程机票未全部唯一匹配');
    guard();if($.UC.bindData('#1666-t.select_key#')!==key)throw new Error('写入前经费选择变化');
    for(const {e,next} of changes)$(e).val(decimal(next)).trigger('change').trigger('blur');
    $.Q.fn.getTableDataToContext('1669');
    button(document.querySelector('.foot_winWF_YBX_1435'),'三、更新绑定报销项').click();
    return {ok:true,action:'update_invoice_split_bindings',serial,key,group,kind,total:decimal(total),eligible:found,requiresReadback:true};
  }
  function confirm(){guard();button(document.querySelector('.foot_winWF_YBX_1436'),'四、确认拆分结果并保存').click();return {ok:true,action:'request_confirm_invoice_split',serial,requiresReadback:true};}
  function acceptConfirmation(){
    guard();const ds=[...document.querySelectorAll('.ui-dialog')].filter(e=>visible(e)&&e.innerText.includes('确认保存拆分结果？'));
    if(ds.length!==1)throw new Error('确认保存拆分窗口不唯一');
    const bs=[...ds[0].querySelectorAll('button')].filter(e=>visible(e)&&e.textContent.replace(/\s/g,'')==='继续');
    if(bs.length!==1)throw new Error('确认按钮不唯一');bs[0].click();return{ok:true,action:'save_invoice_split',serial,requiresReadback:true};
  }
  async function save(){if(![...document.querySelectorAll('.ui-dialog')].some(e=>visible(e)&&e.innerText.includes('确认保存拆分结果？')))confirm();await wait(()=>[...document.querySelectorAll('.ui-dialog')].some(e=>visible(e)&&e.innerText.includes('确认保存拆分结果？')),'原生保存确认未出现');const result=acceptConfirmation();await wait(()=>!visible(document.getElementById('gridWF_YBX_1669'))&&!visible(document.getElementById('gridWF_YBX_305'))&&document.getElementById('iframepage_print')?.contentDocument.body.innerText.includes('报销单号：'+serial),'保存后的编辑预览尚未恢复');return result;}
  async function open(group){
    guard();const g=$('#gridWF_YBX_305');
    if(visible(g[0])){
      const rows=g.jqGrid('getDataIDs').map(id=>g.jqGrid('getRowData',id));
      if(!rows.length||rows.some(r=>!r['t.xtype'].startsWith(group+'!P!')))throw new Error('当前发票信息属于其他分组');
      if(visible(document.getElementById('gridWF_YBX_1669'))){await allRows();return {ok:true,action:'split_already_open',serial,group};}
    }else{
      const f=document.getElementById('iframepage_print');
      if(!f?.contentDocument.body.innerText.includes('报销单号：'+serial))throw new Error('本案编辑预览未就绪');
      const nodes=[...f.contentDocument.querySelectorAll('[onclick]')].filter(e=>e.getAttribute('onclick').startsWith("pageOpen_v1('发票信息','"+group+'!P!'));
      if(!nodes.length)throw new Error('本类发票信息原生入口缺失');
      nodes[0].click();await wait(()=>visible(document.getElementById('gridWF_YBX_305')),'发票信息未打开');
    }
    await wait(()=>visible(document.querySelector('.foot_winWF_YBX_213')),'发票信息按钮未就绪');
    button(document.querySelector('.foot_winWF_YBX_213'),'拆分多报销项报销').click();
    await wait(()=>visible(document.getElementById('gridWF_YBX_1669')),'逐票拆分未打开');
    await allRows();return {ok:true,action:'open_invoice_split',serial,group};
  }
  async function returnToPreview(){
    guard();button(document.querySelector('.foot_winWF_YBX_1436'),'返回').click();
    await wait(()=>!visible(document.getElementById('gridWF_YBX_1669')),'逐票拆分未关闭');
    button(document.querySelector('.foot_winWF_YBX_213'),'返回').click();
    await wait(()=>!visible(document.getElementById('gridWF_YBX_305'))&&visible(document.getElementById('iframepage_print')),'编辑预览未恢复');
    return {ok:true,action:'return_to_edit_preview',serial};
  }
  async function begin(){
    if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX')throw new Error('财务系统不符');
    const g=$('#gridWF_YBX_101');if(!visible(g[0]))throw new Error('退回单列表未打开');
    const matches=g.jqGrid('getDataIDs').map(id=>({id,row:g.jqGrid('getRowData',id)})).filter(x=>x.row['t.serial_no']===serial);
    if(matches.length!==1||matches[0].row['t.status']!=='-3'||money(matches[0].row['t.total_amt'])!==caseAmount)throw new Error('本单未证实处于退回状态或总额变化');
    const target=matches[0];if(g.jqGrid('getGridParam','selrow')!==target.id)g.jqGrid('setSelection',target.id,true);
    if(g.jqGrid('getGridParam','selrow')!==target.id)throw new Error('目标单据未唯一选中');
    button(document.querySelector('.foot_winWF_YBX_71'),'修改').click();
    const text='如您需要修改，将会使报销单进入草稿状态，需重新提交后进入流程审批';
    await wait(()=>[...document.querySelectorAll('.ui-dialog')].some(e=>visible(e)&&e.innerText.includes(text)),'退回修改确认未出现');
    const ds=[...document.querySelectorAll('.ui-dialog')].filter(e=>visible(e)&&e.innerText.includes(text));
    if(ds.length!==1)throw new Error('退回修改确认不唯一');
    const bs=[...ds[0].querySelectorAll('button')].filter(e=>visible(e)&&e.textContent.replace(/\s/g,'')==='继续');
    if(bs.length!==1)throw new Error('原生继续按钮不唯一');bs[0].click();
    return {ok:true,action:'begin_returned_correction',serial,requiresReadback:true};
  }
  async function remark(text){
    guard();const f=document.getElementById('iframepage_print');
    if(!f?.contentDocument.body.innerText.includes('报销单号：'+serial))throw new Error('本案编辑预览未就绪');
    const id='formWF_YBX_60_t-cl_abst';if(!visible(document.getElementById(id)))f.contentWindow.pageOpen('XG基本');
    await wait(()=>visible(document.getElementById(id))&&visible(document.querySelector('.foot_winWF_YBX_43')),'经费备注和保存按钮未加载');
    const e=document.getElementById(id);if(e.readOnly||e.disabled)throw new Error('经费备注不可修改');
    const originalAbstract=document.getElementById('formWF_YBX_60_t-abst').value;
    $(e).val(text).trigger('change').trigger('blur');$.F.getDataToContext('60');
    if(document.getElementById('formWF_YBX_60_t-abst').value!==originalAbstract)throw new Error('摘要发生非预期变化');
    button(document.querySelector('.foot_winWF_YBX_43'),'保存').click();
    await wait(()=>!visible(document.getElementById(id))&&document.getElementById('iframepage_print')?.contentDocument.body.innerText.replaceAll('，',',').includes(text.replaceAll('，',',')),'备注保存后的预览尚未更新');
    return {ok:true,action:'update_funding_remark',serial,remark:text,requiresReadback:true};
  }
  async function basicWindow(){
    guard();const f=document.getElementById('iframepage_print');
    if(!f?.contentDocument.body.innerText.includes('报销单号：'+serial))throw new Error('本案编辑预览未就绪');
    const id='formWF_YBX_60_t-abst';if(!visible(document.getElementById(id)))f.contentWindow.pageOpen('XG基本');
    await wait(()=>visible(document.getElementById(id))&&visible(document.querySelector('.foot_winWF_YBX_43')),'基本信息窗口未就绪');
    await wait(()=>['t-abst','t-lc_accepter','t-lc_accepter_name'].every(k=>document.getElementById('formWF_YBX_60_'+k)?.value?.trim()),'原单摘要和教师证明人尚未加载');
    let previous='',stableSince=Date.now();
    await wait(()=>{const current=JSON.stringify(basicValues());if(current!==previous){previous=current;stableSince=Date.now();return false;}return Date.now()-stableSince>=300;},'基本信息仍在异步加载');
  }
  async function drafts(){
    if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX')throw new Error('财务系统不符');
    if(!visible(document.getElementById('gridWF_YBX_101'))){
      const entries=[...document.querySelectorAll('.pageTitle[title="草稿"]')].filter(visible);
      if(entries.length!==1)throw new Error('草稿原生入口不唯一');entries[0].click();
      await wait(()=>visible(document.getElementById('gridWF_YBX_101')),'草稿列表未打开');
    }
    return {ok:true,action:'open_drafts_readonly',projid:$.getProjID(),system_write:false};
  }
  async function resume(){
    if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX')throw new Error('财务系统不符');
    const g=$('#gridWF_YBX_101');if(!visible(g[0]))throw new Error('草稿列表未打开');
    const rows=g.jqGrid('getDataIDs').map(id=>({id,row:g.jqGrid('getRowData',id)})).filter(x=>x.row['t.serial_no']===serial);
    if(rows.length!==1||rows[0].row['t.status']!=='-99'||rows[0].row['t.bx_typename']!=='日常报销'||money(rows[0].row['t.total_amt'])!==caseAmount)throw new Error('本单草稿身份、状态或金额不符');
    g.jqGrid('setSelection',rows[0].id,true);
    if(g.jqGrid('getGridParam','selrow')!==rows[0].id)throw new Error('本单未唯一选中');
    button(document.querySelector('.foot_winWF_YBX_71'),'修改').click();
    await wait(()=>document.getElementById('iframepage_print')?.contentDocument.body.innerText.includes('报销单号：'+serial),'本单草稿编辑预览未恢复');
    guard();return {ok:true,action:'resume_existing_draft',serial,requiresReadback:true};
  }
  function basicValues(){return Object.fromEntries([...document.querySelectorAll('[id^="formWF_YBX_60_"]')].filter(e=>e.matches('input,select,textarea')).map(e=>[e.id,e.value]));}
  async function basic(fields){
    const allowed=['t-abst','t-req_mobile','t-req_tele'];
    if(!Object.keys(fields).length||Object.keys(fields).some(k=>!allowed.includes(k)||typeof fields[k]!=='string'||!fields[k].trim()))throw new Error('只允许明确的摘要和联系电话');
    await basicWindow();const before=basicValues();
    const changes=Object.entries(fields).map(([k,v])=>{const e=document.getElementById('formWF_YBX_60_'+k);if(!visible(e)||e.readOnly||e.disabled)throw new Error('字段不可编辑：'+k);return {e,v};});
    guard();for(const {e,v}of changes)$(e).val(v).trigger('change').trigger('blur');
    const after=basicValues();for(const [id,value]of Object.entries(before))if(!Object.hasOwn(fields,id.replace('formWF_YBX_60_',''))&&after[id]!==value)throw new Error('其他基本信息非预期变化：'+id);
    $.F.getDataToContext('60');button(document.querySelector('.foot_winWF_YBX_43'),'保存').click();
    await wait(()=>!visible(document.getElementById('formWF_YBX_60_t-abst'))&&document.getElementById('iframepage_print')?.contentDocument.body.innerText.includes('报销单号：'+serial),'基本信息保存后预览未恢复');
    return {ok:true,action:'save_returned_basic',serial,fields,requiresReadback:true};
  }
  async function basicRead(){
    const id='formWF_YBX_60_t-abst';if(visible(document.getElementById(id)))throw new Error('须先回到保存后的预览，再重开核对');
    await basicWindow();const all=basicValues();const fields=Object.fromEntries(Object.entries(all).map(([k,v])=>[k.replace('formWF_YBX_60_',''),v]));
    await basicClose();
    await wait(()=>!visible(document.getElementById(id)),'只读基本信息窗口未关闭');
    return {ok:true,action:'read_persisted_basic',serial,projid:$.getProjID(),fields,system_write:false};
  }
  async function basicClose(){
    guard();const id='formWF_YBX_60_t-abst';let dialog,closes=[];
    await wait(()=>{const e=document.getElementById(id);dialog=e?.closest('.ui-dialog');
      closes=dialog?[...dialog.querySelectorAll('.ui-dialog-titlebar-close')].filter(b=>visible(b)&&b.closest('.ui-dialog')===dialog):[];
      return visible(e)&&visible(dialog)&&closes.length===1;},'基本信息原生弹窗尚未就绪');
    if(closes.length!==1)throw new Error('基本信息原生关闭入口不唯一');closes[0].click();
    await wait(()=>!visible(document.getElementById(id)),'基本信息原生关闭未完成');
    return {ok:true,action:'close_basic_readonly',serial,system_write:false};
  }
  root.ReimbursementSplit={configure,select,update,save,open,returnToPreview,begin,remark,basic,basicRead,basicClose,drafts,resume};
})(globalThis);
