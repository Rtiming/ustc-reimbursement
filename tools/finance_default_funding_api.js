/* 学校单经费默认分配可能没有Q307手工树；必须从完整确认正文逐组核对。 */
async function reimbursementDefaultFunding(c){
  if(c.budget_allocation_mode!=='system_default_single_funding'||c.funding?.length!==1||c.expense_allocations?.length||c.funding_policy?.invoice_level_required)throw Error('DEFAULT_FUNDING_API_EXPLICIT_SINGLE_DEFAULT_REQUIRED');
  const meta=await reimbursementAmountInspect({...c,group:'all',subject:'00',initialization_verified:false,include_categories:true}),money=ReimbursementHttpValues.moneyCents;
  if(!meta.categories.length||meta.categories.some(r=>r.project!==c.funding[0].code||!r.budget)||meta.categories.reduce((sum,r)=>sum+money(r.amount),0n)!==money(c.expected_amount)||money(c.funding[0].amount)!==money(c.expected_amount))throw Error('DEFAULT_FUNDING_API_PREVIEW_NOT_CLOSED');
  if(c.remuneration_native_source){
    if(c.system_mode!=='remuneration'||meta.bill['t.bx_type']!=='8'||meta.entries.length||meta.categories.length!==1||meta.categories[0].group!=='学生-学生其他劳务'||!meta.salary?.rows?.length)throw Error('DEFAULT_FUNDING_API_PAYROLL_SCOPE');
    return{proof:{kind:'system_default_remuneration_funding',bill:meta.bill,basic:meta.basic_sections,categories:meta.categories,amounts:[],salary:meta.salary,source_binding:c.remuneration_native_source},requests:meta.requests};
  }
  if(c.domestic_allowance_source){
    const s=c.domestic_allowance_source,readback=await reimbursementDomesticAllowanceFinalReadback({...c,...s});
    const components=readback.components,allowanceCategories=meta.categories.filter(r=>Object.hasOwn(components,r.group)),invoiceCategories=meta.categories.filter(r=>!Object.hasOwn(components,r.group));
    if(meta.bill['t.bx_type']!=='2'||!readback.complete||JSON.stringify(components)!==JSON.stringify(s.components)||allowanceCategories.length!==2||new Set(allowanceCategories.map(r=>r.group)).size!==2||allowanceCategories.some(r=>money(r.amount)!==money(components[r.group]))||invoiceCategories.length!==meta.entries.length||invoiceCategories.reduce((v,r)=>v+money(r.amount),0n)!==money(s.invoice_amount)||invoiceCategories.some(row=>{const hits=meta.entries.filter(e=>e.group===row.group&&e.subject===row.subject&&e.parameters.split('&')[2]===row.group_key);return hits.length!==1||money(hits[0].amount)!==money(row.amount);}))throw Error('DEFAULT_FUNDING_API_DOMESTIC_COMPONENTS');
    return{proof:{kind:'system_default_domestic_funding',bill:meta.bill,basic:meta.basic_sections,categories:meta.categories,amounts:meta.entries,source_binding:s.binding,allowance_rows:readback.allowances,components},requests:[...meta.requests,...readback.requests]};
  }
  if(c.allowance_native_source){
    const s=c.allowance_native_source,allowance=await reimbursementAllowanceReadback({...c,...s});
    if(meta.bill['t.bx_type']!=='2'||meta.entries.length||!allowance.complete||meta.categories.length!==2)throw Error('DEFAULT_FUNDING_API_ALLOWANCE_SCOPE');
    const totals={'国内出差伙食补贴':allowance.rows.reduce((v,r)=>v+money(r.HSBT),0n),'国内出差交通补贴':allowance.rows.reduce((v,r)=>v+money(r.JTBT),0n)};
    if(new Set(meta.categories.map(r=>r.group)).size!==2||meta.categories.some(r=>!Object.hasOwn(totals,r.group)||money(r.amount)!==totals[r.group]))throw Error('DEFAULT_FUNDING_API_ALLOWANCE_COMPONENTS');
    return{proof:{kind:'system_default_allowance_funding',bill:meta.bill,basic:meta.basic_sections,categories:meta.categories,amounts:[],source_binding:s.binding,allowance_rows:allowance.rows},requests:[...meta.requests,...allowance.requests]};
  }
  if(meta.entries.length!==meta.categories.length||meta.categories.some(row=>{const hits=meta.entries.filter(e=>e.group===row.group&&e.subject===row.subject&&e.parameters.split('&')[2]===row.group_key);return hits.length!==1||money(hits[0].amount)!==money(row.amount);}))throw Error('DEFAULT_FUNDING_API_GROUP_AMOUNT_CHANGED');
  return{proof:{kind:'system_default_single_funding',bill:meta.bill,basic:meta.basic_sections,categories:meta.categories,amounts:meta.entries},requests:meta.requests};
}
