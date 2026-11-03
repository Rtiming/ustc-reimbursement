window.WorkbenchCaseDetails = async function (context) {
  const {api,perform,tool,caseId,content,editor,button,notice,onNext,isCurrent}=context;
  const make=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
  let current=await api('/api/read',{tool:'reimbursement_case_details',arguments:{case_id:caseId}});
  if(!isCurrent())return;
  let values=current.draft&&!current.draft_stale?current.draft.values:current.values;
  content.replaceChildren();
  content.append(make('p',current.note+' 金额单位均为元。','muted'));
  if(current.draft)content.append(make('p',current.draft_stale?'存在较早的未确认草稿，当前展示已保存参数，未用旧草稿覆盖。':'正在继续未确认的填写草稿，尚未写入正式填报参数。','status-tag'));
  const editors={};
  function paymentsEditor(rows) {
    const wrapper=make('div'),lines=[];
    const schema=current.form_schema.properties.payments.items.properties;
    function add(row={mode:'bank_transfer'}) {
      const line=make('fieldset','','payment-details-line'),fields={};
      line.append(make('legend','收款安排'));
      for(const[key,s]of Object.entries(schema)){const field=editor(key,s,row[key],current.form_schema,['mode','name','amount'].includes(key));fields[key]=field;line.append(field.node);}
      function allowed(key){const mode=fields.mode.get();if(['bank_name','bank_account','source_refs'].includes(key))return mode==='corporate_transfer';if(key==='transaction')return mode==='corporate_card';if(key==='person_id')return mode!=='corporate_transfer';return true;}
      function modeChanged(){for(const[key,field]of Object.entries(fields))field.node.hidden=!allowed(key);}
      fields.mode.node.querySelector('select').onchange=modeChanged;modeChanged();
      const entry={get:()=>{const value={};for(const[key,field]of Object.entries(fields)){const v=field.get();if(allowed(key)&&v!==undefined)value[key]=v;}return value;}};
      lines.push(entry);line.append(button('移除此收款安排',()=>{line.remove();lines.splice(lines.indexOf(entry),1);}));wrapper.append(line);
    }
    rows.forEach(add);wrapper.append(button('增加收款人或单位',()=>add()));
    return {node:wrapper,get:()=>lines.map(row=>row.get())};
  }
  const groups=[['报销事项',['summary','amount','phone','landline','remarks','leader_expenses','return_point']],['证明人',['teacher_name','teacher_id']],
    ['经费与实际收款',['funding','payments']],['预算与材料交付',['allocation_mode','allocations','contract','delegate_print','paper_originals']]];
  for(const[title,keys]of groups){content.append(make('h2',title));if(title==='经费与实际收款')content.append(make('p','按实际垫付或收款安排填写。个人银行卡由学校已有账户匹配；证明人与收款人分别核对。','help'));const section=make('div','','fields');for(const key of keys){const schema=current.form_schema.properties[key];const e=key==='payments'?paymentsEditor(values[key]||[]):editor(key,schema,values[key],current.form_schema,false);editors[key]=e;if(key==='payments')e.node.classList.add('wide');section.append(e.node);}content.append(section);}
  const basis=make('textarea');basis.placeholder='可选：记录金额、经费或收款安排的确认依据，便于以后追溯';basis.setAttribute('aria-label','填报信息确认依据');content.append(make('h2','保存本次填写'),basis);
  const summary=make('div');content.append(summary);
  function showNeeds(data){summary.replaceChildren();if(data.needed_inputs?.length)summary.append(make('p','仍待核对：'+data.needed_inputs.map(row=>row.label).join('、'),'muted'));if(data.input_validation?.error)summary.append(make('p','后续办理检查：'+data.input_validation.error,'help'));}
  showNeeds(current);
  function collect(){const value={};for(const[key,e]of Object.entries(editors)){const v=e.get();if(v!==undefined)value[key]=v;}return value;}
  const actions=make('div','','actions');content.append(actions);
  actions.append(button('暂存填写',async()=>{
    const result=await perform(tool,{case_id:caseId,action:'draft',values:collect(),expected_revision:current.revision,execute:true},false,{show:false});
    if(result?.saved_draft&&isCurrent()){current.revision=result.revision;notice('未确认的填写已暂存，可以稍后继续；正式填报参数未改变。');}
    else if(result?.error)throw Error(result.error);
  }));
  actions.append(button('核对并保存本地信息',async()=>{
    const v=collect();const result=await perform(tool,{case_id:caseId,action:'save',values:v,expected_revision:current.revision,confirmation_note:basis.value.trim()||'用户核对当前表单并点击保存本地信息',execute:true},false,{show:false});
    if(!result||!isCurrent())return;
    if(result.saved){current=result;notice('填报信息已保存到本机。'+(result.needed_inputs?.length?'下面列出了仍需补充的信息。':result.input_validation?.ok?'填写内容检查通过，可以继续核对本案办理条件。':'请按下面提示完成办理条件检查。'));showNeeds(result);if(result.next_action?.tool)actions.append(button('查看本案下一步',()=>onNext(result.next_action)));}
    else throw Error(result.error||'未取得保存回执，请先核对原操作。');
  },true));
};
