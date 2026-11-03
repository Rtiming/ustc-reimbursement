/* 人的办理步骤与agent使用同一case_finance计划和原任务。 */
window.WorkbenchCaseFinance = async function(ctx) {
  const {value,content,button,table,perform,api,tool,onResult,notice,isCurrent,openConnection,onNext}=ctx;
  const make=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
  const labels={funding:'经费项目',basic:'摘要与证明人',categories:'费用类别',expenses:'预算分配',payments:'实际收款',notes:'事项说明','pre-review':'预审核人'};
  const actions={source_audit:'原票购方审核',pool:'学校完整票池',selection_preview:'本案选票预览',selection_apply:'保存本案选票',upload:'上传原票',create:'学校建单',bind_created:'绑定学校单号'};
  async function request(args,confirm=false,summary){
    const result=await perform(tool,{case_id:value.case_id,...args,request_id:'case-finance-'+crypto.randomUUID().replaceAll('-','')},confirm,{show:false,summary});
    if(result&&isCurrent())onResult(result);return result;
  }
  if(value.schema==='ustc-case-fill-v1'){
    const states={planned:'核对本次填写范围',running:'正在逐项填写和核验',paused:'已暂停后续阶段',needs_review:'原任务需要核对',source_changed:'资料或连接已变化',completed:'所列填写阶段均已回读'};
    content.append(make('h2',states[value.state]||value.state),make('p','单号'+value.serial+'；本人账号'+value.expected_userid));
    const f=value.form;const scope=[['学校单号',value.serial],['本人账号',value.expected_userid],['摘要',f.basic?.fields?.['t-abst']],['申请金额',f.amount],['证明人',(f.basic?.teacher_name||'')+' '+(f.basic?.teacher_id||'')],
      ['经费',(f.funding||[]).map(r=>r.code+'：'+r.amount).join('；')],['实际收款',(f.payments||[]).map(r=>r.name+'：'+r.amount).join('；')],['阶段',value.stages.map(s=>labels[s]).join(' → ')]];
    content.append(table(['填写范围','本次内容'],scope),make('p',value.error||value.note));
    if(value.rows.length)content.append(table(['阶段','实际进度'],value.rows.map(r=>[labels[r.stage],{prepared:'已核对保存前差异',saved_pending_readback:'已保存，等待独立回读',verified:'保存已独立核验',verified_unchanged:'学校当前内容已一致'}[r.state]||r.state])));
    const live=make('p');content.append(live);let advancing=false;
    const pause=button('暂停后续阶段',async()=>{const r=await perform(tool,{case_id:value.case_id,action:'fill_pause',plan_id:value.plan_id},false,{show:false});notice('已请求暂停，原任务核验后不再开始下一阶段。');if(!advancing&&r&&isCurrent())onResult(r);});
    pause.hidden=value.state!=='running';content.append(pause);
    async function proceed(){
      if(advancing)return;advancing=true;
      try{
      let current=value,first=true;
      // 页面关闭仅停止继续派发；原后台任务仍保留，重新打开按同一计划接续。
      while(isCurrent()&&['planned','paused','running'].includes(current.state)){
        const args={case_id:value.case_id,action:'fill_continue',plan_id:value.plan_id,execute:true,request_id:'case-fill-'+crypto.randomUUID().replaceAll('-','')};
        if(!current.authorized)args.authorization_note='本人核对本计划所列单号、完整填报信息、经费和实际收款，确认按此范围填写草稿并逐域回读；本人提交另行确认。';
        current=await perform(tool,args,first&&!current.authorized,{show:false,summary:scope});first=false;
        if(!current)return;
        pause.hidden=false;
        if(current.schema!=='ustc-case-fill-v1'){if(isCurrent())onResult(current);return;}
        live.textContent='当前：'+(labels[current.stages[current.index]]||'阶段完成')+'，'+({prepare:'核对学校现状',save:'保存本次信息',readback:'独立回读',ack:'登记核验结果'}[current.phase]||current.phase);
        if(!['planned','running'].includes(current.state)){if(isCurrent())onResult(current);return;}
        await new Promise(resolve=>setTimeout(resolve,700));
      }
      }finally{advancing=false;}
    }
    if(['planned','paused','running'].includes(value.state))content.append(button(value.authorized?'接续原填写计划':'确认范围并依次填写',proceed,true));
    if(value.state==='needs_review')content.append(button('只恢复原保存结果',()=>request({action:'fill_recover',plan_id:value.plan_id})));
    content.append(button('更新填写进度',async()=>onResult(await api('/api/read',{tool:tool.name,arguments:{case_id:value.case_id,action:'fill_status',plan_id:value.plan_id}}))));
    content.append(button('返回本活动办理步骤',async()=>onResult(await api('/api/read',{tool:tool.name,arguments:{case_id:value.case_id}}))));return;
  }
  if(value.schema==='ustc-case-ready-v1'){
    const titles={planned:'核对本单提交范围与学校承诺',running:'正在准备学校本人确认',paused:'已暂停后续提交步骤',needs_review:'原提交任务需要核对',source_changed:'原提交范围已变化',completed:'已核到学校本人确认节点'};
    const f=value.form,scope=[['学校单号',f.serial],['本人账号',value.expected_userid],['摘要',f.basic?.fields?.['t-abst']],['申请金额',f.amount],
      ['经费与单位审批人',(f.funding||[]).map(r=>r.code+'：'+r.amount+'；'+(r.department_approver?.name||'未指定')).join('\n')],
      ['实际收款',(f.payments||[]).map(r=>r.name+'：'+r.amount).join('\n')],['财务打印',f.delivery?.delegate_finance_print?'委托财务打印':'自行打印'],['纸质原件',f.delivery?.originals_required?'需要提交':'不需要提交'],['学校承诺全文',value.promise_text]];
    content.append(make('h2',titles[value.state]||value.state),table(['本次准备范围','确认内容'],scope),make('p',value.error||value.note));
    const live=make('p');content.append(live);let advancing=false;
    const pause=button('暂停后续步骤',async()=>{const r=await perform(tool,{case_id:value.case_id,action:'ready_pause',plan_id:value.plan_id},false,{show:false});notice('已请求暂停，原任务核验后不再开始下一步。');if(!advancing&&r&&isCurrent())onResult(r);});pause.hidden=value.state!=='running';content.append(pause);
    async function proceed(){
      if(advancing)return;advancing=true;
      try{let current=value,first=true;
        while(isCurrent()&&['planned','paused','running'].includes(current.state)){
          const args={case_id:value.case_id,action:'ready_continue',plan_id:value.plan_id,execute:true};
          if(!current.authorized)args.authorization_note='本人已阅读并同意此计划显示的学校承诺全文，确认所列学校单号、账号、金额、经费、收款和打印安排，授权准备到本人确认节点；最终本人同意另行办理。';
          current=await perform(tool,args,first&&!current.authorized,{show:false,summary:scope});first=false;if(!current)return;
          pause.hidden=false;live.textContent='已完成'+current.step+'个准备或独立核验步骤；原任务持续保留。';
          if(current.schema!=='ustc-case-ready-v1'||!['planned','running'].includes(current.state)){if(isCurrent())onResult(current);return;}
          await new Promise(resolve=>setTimeout(resolve,700));
        }
      }finally{advancing=false;}
    }
    if(['planned','paused','running'].includes(value.state))content.append(button(value.authorized?'接续原准备计划':'阅读承诺并确认准备',proceed,true));
    if(value.state==='needs_review')content.append(button('只核查原提交结果',()=>request({action:'ready_recover',plan_id:value.plan_id})));
    if(value.state==='completed'&&value.next_action)content.append(button('查看本人待办并确认',()=>onNext(value.next_action),true));
    content.append(button('更新准备进度',async()=>onResult(await api('/api/read',{tool:tool.name,arguments:{case_id:value.case_id,action:'ready_status',plan_id:value.plan_id}}))),button('返回本活动',()=>request({action:'inspect'})));return;
  }
  content.append(make('p',value.note));
  content.append(make('h2','1. 本人账号与本次活动'));
  if(value.connection)content.append(make('p','本案填报账号：'+value.connection.expected_userid+'；核验时间：'+new Date(value.connection.verified_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})));
  const accounts=make('select');accounts.setAttribute('aria-label','本案填报账号');accounts.append(new Option('请选择已核实的本人账号',''));
  value.connections.forEach(r=>accounts.append(new Option(r.expected_userid,r.connection_id)));if(value.connection)accounts.value=value.connection.connection_id;
  content.append(accounts,button('使用这个本人账号',()=>{if(!accounts.value)throw Error('请先选择本人账号');return request({action:'bind',connection_id:accounts.value,execute:true},false);}),button('登录或重新核对账号',openConnection));
  content.append(make('h2','2. 本案原票与学校票池'),make('p','先勾选本次原票，核对购方和学校票池。每次可处理20张选票，继续追加仍属于本案；金额不会由文件名推定。'));
  const selected=new Set();
  content.append(table(['选择','本案原件','票号','票面核对','上传'],value.originals.map(r=>{
    const box=make('input');box.type='checkbox';box.disabled=!r.invoice_numbers.length;box.setAttribute('aria-label','选择原件'+r.name);
    box.onchange=()=>r.invoice_numbers.forEach(n=>box.checked?selected.add(n):selected.delete(n));
    return [box,r.name,r.invoice_numbers.join('、'),r.reviewed?'已核对':'待核对',button('核对并上传此原件',()=>request({action:'upload',start:r.index,count:1,execute:true},true,[['原文件',r.name],['已核票号',r.invoice_numbers.join('、')],['来源校验',r.sha256],['范围','只处理这一份原件；原生接口将先核对本人票池，已有票据不会盲目重传。']]))];
  })));
  const numbers=()=>{if(!selected.size||selected.size>20)throw Error('请选择1至20张票据');return [...selected];};
  content.append(button('审核所选原票购方',()=>request({action:'source_audit',invoice_numbers:numbers()})),button('读取学校完整票池',()=>request({action:'pool'})),button('预览将选入本案的票',()=>request({action:'selection_preview',invoice_numbers:numbers()})));
  if(value.selection_plan){const p=value.selection_plan;content.append(button('核对并保存原选票计划',()=>request({action:'selection_apply',job_id:p.source.job_id,execute:true},true,[['本人账号',value.connection?.expected_userid],['本案摘要',value.summary],['逐票范围',p.rows.map(r=>r.number+' / '+r.amount+'元 / '+r.seller).join('\n')],['选择前后',p.before_count+'张 → '+p.after_count+'张'],['核对','执行前重新核对本次原票和学校完整票池。']])));}
  if(value.selected.length)content.append(table(['本案已选票号','票面金额'],value.selected.map(r=>[r.invoice_number||r.number||r.guid,r.amount])));
  content.append(make('h2','3. 学校建单与填写'));
  if(value.serial){content.append(make('p','本案学校单号：'+value.serial),button('核对本单全部填写范围',()=>request({action:'fill_plan'}),true));}
  else {content.append(make('p','学校尚未返回并绑定本案单号。选票核实后再建单。'));
    const create=button('核对范围并创建学校草稿',()=>request({action:'create',execute:true},true,[['本案摘要',value.summary],['申请金额',value.amount],['已选票数',value.selected.length],['操作','由学校创建一张草稿，取得真实单号后继续填写。']]),true);create.disabled=!value.selected.length||!!value.pending_write;create.dataset.keepDisabled=String(create.disabled);content.append(create);
    if(value.jobs.some(r=>r.action==='create'&&r.status==='completed'))content.append(button('登记原任务返回的学校单号',()=>request({action:'bind_created',execute:true})));
  }
  value.fill_plans.forEach(plan_id=>content.append(button('继续已保存的填写计划',async()=>onResult(await api('/api/read',{tool:tool.name,arguments:{case_id:value.case_id,action:'fill_status',plan_id}})))));
  if(value.serial){content.append(make('h2','4. 准备提交与本人确认'),make('p','从学校取得本单校验结果和承诺全文，阅读后确认准备范围。本人审批与学校验证码在下一步分别办理。'),button('读取学校提交条件和承诺',()=>request({action:'ready_inspect'}),true));}
  (value.ready_plans||[]).forEach(plan_id=>content.append(button('继续原提交准备计划',async()=>onResult(await api('/api/read',{tool:tool.name,arguments:{case_id:value.case_id,action:'ready_status',plan_id}})))));
  if(value.pending_write)content.append(make('p','本案有原操作等待核验。请查看下面原任务；不能重新上传、建单或覆盖原结果。'));
  if(value.jobs.length){content.append(make('h2','本案原任务'),table(['步骤','程序状态',''],value.jobs.map(r=>[actions[r.action]||r.action,{completed:'程序已返回，业务以回读为准',running:'处理中',queued:'已排队',failed:'需要核对',unknown:'结果待核对'}[r.status]||r.status,button('查看结果与下一步',async()=>onResult(await api('/api/read',{tool:'reimbursement_task_view',arguments:{case_id:value.case_id,job_id:r.job_id}})))])));}
};
