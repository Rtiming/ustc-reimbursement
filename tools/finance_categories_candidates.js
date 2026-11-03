/* 分类排查只读当前学校候选，不初始化、选择或保存类别。 */
async function reimbursementCategoriesCandidates(c){
  const meta=await reimbursementAmountInspect({...c,group:'all',subject:'00',initialization_verified:false,include_categories:true});
  const used=await reimbursementBusinessApi({...c,action:'bill-read',domain:'used_invoices',contracts:c.used_contracts,expected_amount:c.expected_amount});
  if(used.sections.length!==1||used.sections[0].complete!==true)throw Error('CATEGORIES_API_INVOICES_INCOMPLETE');
  const owned=new Set(used.sections[0].rows.map(r=>r['t.tk_guid'])),requests=[],http=createReimbursementHttp(c.expected_userid,{prefix:'CATEGORIES_API',requests,timeout:15000,allowed:['definition','query']});
  await reimbursementAmountDefinition(http,'213',c.categories_contract.definition_sha256);const groups=[];
  for(const current of meta.categories){
    const{data:context,set}=http.contextCopy();for(const k of Object.keys(context))if(k.startsWith('WF_YBX$305-'))delete context[k];
    set('61-serialno',c.serial);set('61-xtype',current.parameters);set('61-xtype_order',current.parameters);set('305-COND','');set('305-CONDOBJ',[]);set('305-CURRPAGE',1);
    const rows=await reimbursementCategoriesQuery(c,http,context);
    if(rows.some(r=>r['t.xtype'].split('&')[0]!==current.group_key||r['t.tk_guids'].split(',').some(g=>!owned.has(g))||ReimbursementHttpValues.moneyCents(r['t.amt'])!==ReimbursementHttpValues.moneyCents(current.amount))||rows.filter(r=>r['t.select_flag']==='已选').length!==1)throw Error('CATEGORIES_API_CANDIDATE_GROUP_OR_SELECTED_SET_CHANGED');
    groups.push({current,candidates:rows});
  }
  return{ok:true,complete:true,action:'category-candidates',case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,business_transport:'native_http',system_write:false,save_dispatched:false,groups,requests:[...meta.requests,...used.requests,...requests]};
}
