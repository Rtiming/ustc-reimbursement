window.WorkbenchIntakeGroups=function(context){
  const {value,content,button,table,perform,tool,onCase,notice}=context;
  const make=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
  content.append(make('h2','按活动分配本批材料'),make('p',value.note,'muted'));
  if(value.saved||value.existing_plan){const rows=value.created_cases||value.existing_plan.plan.groups.map(g=>({case_id:g.case_id,title:g.title,material_count:g.source.material_ids.length}));content.append(table(['活动','材料份数',''],rows.map(r=>[r.title,r.material_count,button('进入此活动',()=>onCase(r.case_id),true)])));content.append(make('p','未分配的材料继续保留在原收件批次中。','help'));return;}
  const groups=value.directory_candidates.map(g=>({title:g.original_summary,material_ids:g.material_ids,profile:'student_activity'}));
  const groupList=make('div','','fields'),selectors=[];content.append(groupList);
  function addGroup(title='',profile='student_activity'){
    const index=groups.length;groups.push({title,material_ids:[],profile});drawGroup(index);for(const s of selectors)s.select.add(new Option(title||'新活动'+(index+1),String(index)));
  }
  function drawGroup(index){const group=groups[index],name=make('input'),mode=make('select');name.value=group.title;name.placeholder='活动名称';name.setAttribute('aria-label','活动'+(index+1)+'名称');for(const[k,label]of [['student_activity','学生活动'],['research_purchase','科研采购'],['travel','差旅']])mode.add(new Option(label,k));mode.value=group.profile;name.oninput=()=>{group.title=name.value;for(const row of selectors){const option=[...row.select.options].find(o=>o.value===String(index));if(option)option.textContent=name.value||'新活动'+(index+1);}};mode.onchange=()=>group.profile=mode.value;const label=make('label','活动'+(index+1));label.append(name,mode);groupList.append(label);}
  groups.forEach((_,index)=>drawGroup(index));
  content.append(button('增加一个活动',()=>addGroup()));
  const materialRows=value.materials.map(row=>{const select=make('select');select.setAttribute('aria-label',row.name+'所属活动');select.add(new Option('暂不分配，保留原批次',''));groups.forEach((g,i)=>select.add(new Option(g.title||'活动'+(i+1),String(i))));const proposed=groups.findIndex(g=>g.material_ids.includes(row.material_id));select.value=proposed<0?'':String(proposed);selectors.push({select,material_id:row.material_id});return[row.name,row.kind,select];});
  content.append(table(['材料来源','格式','所属活动'],materialRows));
  content.append(button('核对分组并建立独立活动',async()=>{
    const chosen=groups.map(g=>({...g,material_ids:[]}));for(const row of selectors)if(row.select.value!=='')chosen[Number(row.select.value)].material_ids.push(row.material_id);
    const filled=chosen.filter(g=>g.material_ids.length);if(!filled.length)throw Error('请至少分配一份材料。');if(filled.some(g=>!g.title.trim()))throw Error('请填写每个有材料的活动名称。');
    const args={case_id:value.case_id,intake_job_id:value.intake_job_id,groups:filled,expected_revision:value.revision};
    const preview=await perform(tool,{...args,execute:false},false,{show:false});if(!preview?.ok)throw Error(preview?.error||'分组检查未完成');
    const result=await perform(tool,{...args,execute:true},true,{show:false,summary:[...filled.map(g=>[g.title,g.material_ids.length+'份材料']),['暂不分配',preview.unassigned_count+'份，保留在原批次'],['建立范围','仅创建独立活动及来源关联；不核定金额，不创建学校单据']]});
    if(result?.saved){content.replaceChildren();WorkbenchIntakeGroups({...context,value:result});notice('各活动已分别建立，下一步进入各自材料页核对。');}else if(result?.error)throw Error(result.error);
  },true));
};
