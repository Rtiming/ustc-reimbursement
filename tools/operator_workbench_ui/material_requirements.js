window.WorkbenchMaterialRequirements=function(context){
  const {value,content,button,table,onNext,perform,tools,notice}=context;
  const make=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
  content.append(make('h2','材料要求与具体差项'),make('p',value.note,'muted'));
  if(value.configuration_error)content.append(make('p',value.configuration_error,'status-tag'),button('查看私人规范与专项',()=>onNext({ui_group:'rules'})));
  if(value.rows.length)content.append(table(['材料','当前情况','核对要求','依据'],value.rows.map(row=>[row.label,row.state,row.instruction,row.source||'待核定来源'])));
  const changes={},section=make('div','','fields');
  if(value.conditions.length){content.append(make('h2','本案适用条件'),make('p','只填写已有依据的条件；不确定时保留未核定。条件决定哪些材料适用于本案。','help'));
    for(const row of value.conditions){const label=make('label',row.label),select=make('select');select.add(new Option(row.value===undefined||row.value===null?'尚未核定':'保持已登记值',''));select.add(new Option('是','true'));select.add(new Option('否','false'));label.append(select);if(typeof row.value==='boolean')label.append(make('small','当前已登记：'+(row.value?'是':'否')));select.onchange=()=>{if(select.value==='')delete changes[row.key];else changes[row.key]=select.value==='true';};section.append(label);}
    content.append(section);
    const basis=make('textarea');basis.placeholder='记录本次适用条件的依据，例如本案批复、实际费用或专项条款';basis.setAttribute('aria-label','材料适用条件依据');content.append(basis,button('保存已核定条件',async()=>{
      if(!Object.keys(changes).length)throw Error('请选择本次已经核定的条件。');if(!basis.value.trim())throw Error('请填写核定依据。');
      const result=await perform(tools.find(t=>t.name==='reimbursement_material_requirements'),{case_id:value.case_id,action:'context',values:changes,note:basis.value.trim(),expected_revision:value.revision,execute:true},false,{show:false});
      if(!result?.saved)throw Error(result?.error||'未取得保存回执');await onNext({tool:'reimbursement_material_requirements',arguments:{case_id:value.case_id},label:'更新材料差项'});
    }));
  }
  const actions=make('div','','actions');content.append(actions);
  actions.append(button('补充或分类原始材料',()=>onNext({ui_group:'materials'})),button('已查看差项，继续其他准备',async()=>{
    const result=await perform(tools.find(t=>t.name==='reimbursement_material_requirements'),{case_id:value.case_id,action:'acknowledge',expected_revision:value.revision,execute:true},false,{show:false});
    if(!result?.saved)throw Error(result?.error||'未取得查看记录');notice('当前差项已保留，材料仍须按要求补齐。');await onNext(result.next_action);
  },true));
};
