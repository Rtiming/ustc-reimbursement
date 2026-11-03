/* 私人库路径、版本和来源由正式规范owner取得；用户只核对业务内容。 */
window.WorkbenchStandards=async function(ctx){
  const {content,button,table,api,perform,tools,caseId,notice,isCurrent}=ctx;
  const node=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
  const tool=name=>tools.find(t=>t.name==='reimbursement_'+name);
  const read=args=>api('/api/read',{tool:'reimbursement_standards_read',arguments:args||{}});
  const stateNames={confirmed:'已确认',proposed:'待确认',disputed:'存在争议',superseded:'已被新版本替代',retired:'已停用'};
  let library,current;
  function field(parent,label,value='',options=null){const wrap=node('label',label),input=node(options?'select':'input');
    if(options)for(const[v,t]of options)input.append(new Option(t,v));input.value=value;wrap.append(input);parent.append(wrap);return input;}
  function textField(parent,label,value=''){const wrap=node('label',label),input=node('textarea');input.value=value;wrap.append(input);parent.append(wrap);return input;}
  async function home(){
    const listing=await read();if(!isCurrent())return;library=listing.library_root;content.replaceChildren();
    content.append(node('p','使用安装时绑定的私人规范库。修改保留旧版本；专项选择不授予财务执行权限。'),
      table(['已登记专项','适用场景',''],listing.profiles.map(p=>[p.title||p.profile_id,p.mode||'按专项范围核对',button('查看规则与选用',()=>profile(p.profile_id))])));
    content.append(button('增加专项',()=>create(listing),true));
    const imported=node('input');imported.type='file';imported.accept='.json';imported.setAttribute('aria-label','选择私人规范包');
    content.append(node('h3','导入已有私人规范'),node('p','仅导入你明确选择的专项与材料规则。已有非空专项保持原样；来源和历史版本会保存到私人资料目录。'),imported,
      button('查看规范包范围',async()=>{const file=imported.files[0];if(!file)throw Error('请选择私人规范包');if(file.size>2*1024*1024)throw Error('规范包不能超过2MiB');const bundle=JSON.parse(await file.text());
        const preview=await perform(tool('standards_import'),{bundle},false,{show:false});if(!preview)return;
        const area=node('div');content.append(area);const scope=[...preview.profiles.map(p=>[p.title,p.revision+'，'+p.rule_count+'条规则；'+p.change]),['材料检查规则',preview.material_rule_count+'条'],['来源',preview.source?.source]];
        area.append(table(['导入范围','内容'],scope),button('确认导入这份规范包',async()=>{const saved=await perform(tool('standards_import'),{bundle,execute:true,expected_snapshot:preview.expected_snapshot},true,{show:false,summary:scope});if(saved?.executed){notice('私人规范已导入并回读。');await home();}},true));
      }));
  }
  async function create(listing){
    content.replaceChildren(node('h2','增加一个私人专项'));const form=node('form');form.onsubmit=e=>e.preventDefault();
    const title=field(form,'专项名称');form.append(node('p','选择要继承的已有规则；新增专项只存放差异。'));
    const parents=[];for(const p of listing.profiles){const wrap=node('label'),box=node('input');box.type='checkbox';box.checked=p.profile_id==='common';wrap.append(box,document.createTextNode(p.title||p.profile_id));form.append(wrap);parents.push([p.profile_id,box]);}
    const id='profile_'+crypto.randomUUID().replaceAll('-','').slice(0,20);
    form.append(button('核对并建立',async()=>{if(!title.value.trim())throw Error('请填写专项名称');
      const args={library_root:library,profile_id:id,title:title.value.trim(),parent_ids:parents.filter(([,b])=>b.checked).map(([id])=>id)};
      const preview=await perform(tool('standards_add_profile'),args,false,{show:false});if(!preview)return;
      const saved=await perform(tool('standards_add_profile'),{...args,execute:true,expected_registry_sha256:preview.before_registry_sha256},true,{show:false,summary:[['专项名称',args.title],['继承规则',listing.profiles.filter(p=>args.parent_ids.includes(p.profile_id)).map(p=>p.title||p.profile_id).join('、')||'不继承'],['初始内容','空专项，尚未确认任何新规则']]});
      if(saved?.executed)await profile(id);
    },true),button('返回专项列表',home));content.append(form);
  }
  async function profile(id){
    const value=await read({profile_id:id});if(!isCurrent())return;current=value;const p=value.profile;
    content.replaceChildren(node('h2',p.title),node('p','版本'+p.revision+'；继承'+(p.extends?.join('、')||'无')));
    content.append(table(['规则原文','当前状态','适用范围','来源',''],p.rules.map(r=>[r.statement,stateNames[r.status]||r.status,
      Object.entries(r.scope).map(([k,v])=>({all:'本专项所有场景',business:'业务',organization:'组织',responsible_person:'负责人',case_id:'活动',expense_type:'费用',funding_code:'经费'}[k]||k)+(k==='all'?'':'：'+String(v))).join('；'),
      r.source_refs.map(id=>p.sources.find(s=>s.source_id===id)?.location||id).join('\n'),button('修订或停用',()=>editRule(r))])));
    if(!p.rules.length)content.append(node('p','本专项还没有登记规则。空模板不表示已满足财务要求。'));
    content.append(button('登记一条新规则',()=>editRule()),button('按本活动核对适用规则',select,true),button('返回专项列表',home));
  }
  async function select(){
    if(!caseId)throw Error('请先在顶部选择本次活动，再选择适用专项');
    const p=current.profile;content.replaceChildren(node('h2','本活动采用“'+p.title+'”'));const form=node('form');form.onsubmit=e=>e.preventDefault();
    const date=field(form,'业务日期（材料对应日期，不是默认今天）');date.type='date';const basis=textField(form,'本次采用此专项的依据');
    const scopes={};const source=await api('/api/read',{tool:'reimbursement_overview',arguments:{case_id:caseId,include_parameters:true}});
    const keys=['institution','organization','responsible_person','business','expense_type','funding_code'];
    for(const k of keys)scopes[k]=field(form,{institution:'学校',organization:'所属组织',responsible_person:'负责人',business:'业务类别',expense_type:'费用类型',funding_code:'经费代码'}[k]);
    form.append(button('核对本次适用范围',async()=>{
      if(!date.value||!basis.value.trim())throw Error('请填写业务日期和采用依据');
      const context={case_id:caseId,selected_profile_ids:[p.profile_id],selection_basis:basis.value.trim()};for(const[k,e]of Object.entries(scopes))if(e.value.trim())context[k]=e.value.trim();
      const result=await api('/api/read',{tool:'reimbursement_standards_select',arguments:{library_root:library,profile_id:p.profile_id,context,as_of:date.value}});
      const area=node('div');content.append(area);area.append(node('h3',result.status==='ready'?'适用规则已核对':'仍有范围或冲突需要核对'),table(['适用规则'],(result.applied_rules||[]).map(r=>[r.statement])),table(['待解决事项'],(result.issues||[]).map(r=>[r.detail])));
      if(result.status==='ready')area.append(button('将本次规则选择登记到活动',async()=>{
        const changes={material_context:{standards_selection:{library_root:library,profile_id:p.profile_id,context,as_of:date.value,profile_sha256:result.profile_sha256,profile_sources:result.profile_sources||[],selection:result}}};
        const saved=await perform(tool('update_case'),{case_id:caseId,document:'case',changes,expected_revision:source.revision},true,{show:false,summary:[['专项',p.title],['业务日期',date.value],['采用依据',basis.value],['已匹配规则',result.applied_rules?.length||0]]});
        if(saved){notice('本活动已登记本次规则选择，来源与版本已保留。');await profile(p.profile_id);}
      },true));
    },true),button('返回规则',()=>profile(p.profile_id)));content.append(form);
  }
  function editRule(original){
    const p=current.profile;content.replaceChildren(node('h2',original?'修订或停用规则':'登记新规则'));const form=node('form');form.onsubmit=e=>e.preventDefault();
    const statement=textField(form,'规则原文',original?.statement||'');
    const kind=field(form,'规则来源类别',original?.kind||'user_workflow',[['official_requirement','学校正式要求'],['reviewer_requirement','审核人员要求'],['user_workflow','使用者整理规则'],['case_decision','本案决定'],['historical_reference','历史参考'],['unverified_extract','待核对提取']]);
    const status=field(form,'当前确认状态',original?.status||'proposed',[['proposed','尚待确认'],['confirmed','已有明确确认'],['disputed','存在争议']]);
    const actor=field(form,'本次登记者或确认者'),location=textField(form,'本次依据原文位置（通知链接、文档或明确反馈记录）'),reason=textField(form,'新增、修订或停用的原因');
    const scopeKind=field(form,'适用范围',original?Object.keys(original.scope)[0]:'business',[['business','指定业务'],['organization','指定组织'],['responsible_person','指定负责人'],['case_id','仅本活动'],['all','本专项全部场景']]);
    const scopeValue=field(form,'范围内容',original?String(Object.values(original.scope)[0]):'');
    const effectAction=field(form,'规则性质',original?.effect.action||'required',[['required','必须满足'],['recommended','建议'],['excluded','不适用'],['ordering','排列顺序'],['default','已确认默认安排']]);
    const target=field(form,'规则约束的业务项目',original?.effect.target||'manual.review_requirement');
    const effectValue=textField(form,'具体安排',original?typeof original.effect.value==='string'?original.effect.value:JSON.stringify(original.effect.value):'');
    form.append(node('p','工具已支持的规则会参与规划。尚未适配的业务效果保留为人工核对项，不会自动扩大到其他活动。'));
    async function save(retire=false){
      if(!actor.value.trim()||!location.value.trim()||!reason.value.trim())throw Error('请填写登记者、原始依据和变更原因');
      const stamp=new Date().toISOString(),sid='source_'+crypto.randomUUID().replaceAll('-',''),source={source_id:sid,kind:'user_provided_reference',location:location.value.trim()};
      const change={action:retire?'retire':original?'revise':'add',actor:actor.value.trim(),at:stamp,reason:reason.value.trim(),sources:[source],source_refs:[sid]};
      if(retire)change.rule_id=original.rule_id;
      else{
        if(!statement.value.trim()||!target.value.trim()||!effectValue.value.trim())throw Error('请填写规则原文、业务项目和具体安排');
        let v=effectValue.value.trim();try{v=JSON.parse(v);}catch{}
        const revision=original?original.revision.split('.').map((n,i)=>i===2?Number(n)+1:n).join('.'):'0.1.0';
        change.rule={...(original||{}),rule_id:original?.rule_id||'rule_'+crypto.randomUUID().replaceAll('-',''),revision,statement:statement.value.trim(),kind:kind.value,status:status.value,
          scope:scopeKind.value==='all'?{all:true}:{[scopeKind.value]:scopeKind.value==='case_id'?caseId:scopeValue.value.trim()},effect:{action:effectAction.value,target:target.value.trim(),value:v},source_refs:[sid]};
        if(status.value==='confirmed')change.rule.confirmation={by:actor.value.trim(),at:stamp,source_refs:[sid]};else delete change.rule.confirmation;
        if(original)change.rule.supersedes=[{rule_id:original.rule_id,revision:original.revision,reason:reason.value.trim(),source_refs:[sid]}];
      }
      const args={library_root:library,profile_id:p.profile_id,change,expected_sha256:current.sha256};
      const preview=await perform(tool('standards_change'),args,false,{show:false});if(!preview)return;
      const saved=await perform(tool('standards_change'),{...args,execute:true},true,{show:false,summary:[['专项',p.title],['操作',retire?'停用':original?'修订':'新增'],['规则',statement.value],['确认状态',stateNames[status.value]],['依据',location.value],['原因',reason.value]]});
      if(saved?.executed)await profile(p.profile_id);
    }
    form.append(button('预览并确认保存',()=>save(),true));if(original)form.append(button('按本次依据停用规则',()=>save(true)));form.append(button('返回规则',()=>profile(p.profile_id)));content.append(form);
  }
  await home();
};
