/* 选择项目的临时状态也属于写入：仅在宿主冻结pending后初始化、选择并保存。 */
async function reimbursementFundingBuild(c) {
  'use strict';
  const requests=[],http=createReimbursementHttp(c.expected_userid,{prefix:'FUNDING_API',requests,timeout:15000,allowed:['definition','button','procedure']});
  const {moneyCents}=ReimbursementHttpValues;
  if (!Array.isArray(c.funding) || !c.funding.length || new Set(c.funding.map(f=>f.code)).size!==c.funding.length ||
      c.funding.some(f=>!/^\w+$/.test(f.code) || moneyCents(f.amount)<=0n) ||
      c.funding.reduce((sum,f)=>sum+moneyCents(f.amount),0n)!==moneyCents(c.expected_amount)) throw Error('FUNDING_API_PARAMETERS');
  const read=await reimbursementBusinessApi({...c,action:'bill-read',domain:'funding'});
  if(read.bill['t.status']!=='-99' || read.bill['t.lc_finish']!=='F') throw Error('FUNDING_API_NOT_DRAFT');
  const budget=await reimbursementBusinessApi({...c,action:'bill-read',domain:'categories',contracts:c.budget_contracts});
  const original=budget.sections.filter(s=>s.funcno==='307' && s.kind==='grid' && s.complete===true);
  if(original.length!==1 || original[0].rows.some(r=>r['t.serial_no']!==c.serial)) throw Error('FUNDING_API_BUDGET_BINDING');
  const definition=await http.post('loadDefinition.action?type=W',{winno:'170',projid:'WF_YBX'});
  const canonical=v=>Array.isArray(v)?v.map(canonical):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;
  const projected=Object.fromEntries(['wins','funcs','funcMap','actMap'].map(k=>[k,definition[k]]));
  const hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(canonical(projected)))))].map(v=>v.toString(16).padStart(2,'0')).join('');
  if(hash!==c.funding_contract.definition_sha256) throw Error('FUNDING_API_DEFINITION_CHANGED');
  const win=definition.wins.find(w=>String(w.winno)==='170'),form=definition.wins.find(w=>String(w.winno)==='171');
  const choose=win?.grid_btns.find(b=>b.btnguid===c.funding_contract.select_button);
  const save=form?.func_btns.find(b=>b.btnguid===c.funding_contract.save_button);
  if(!choose || !save || !Array.isArray(win.beforeLoadProcs) || win.beforeLoadProcs.length!==1 ||
      [choose,save].some(b=>b.pre_check || b.pre_compute || b.jscontent || b.wf_enabled || b.hasIntf_beforeclick || b.hasIntf_afterclick))
    throw Error('FUNDING_API_BUTTON_CONTRACT');
  const sections=read.sections.filter(s=>s.funcno==='242' && s.kind==='grid' && s.complete===true);
  if(sections.length!==1) throw Error('FUNDING_API_CANDIDATES_INCOMPLETE');
  const rows=sections[0].rows,codes=c.funding.map(f=>f.code);
  if(rows.some(r=>r['t.select_flag']==='已选' && !codes.includes(r['t.uni_prj_code']))) throw Error('FUNDING_API_EXTRA_SELECTION');
  const candidates=[];
  for(const f of c.funding){
    const byCode=rows.filter(r=>r['t.uni_prj_code']===f.code);
    const matches=f.owner_name ? byCode.filter(r=>typeof r['t.CHARGE_NAME']==='string' &&
      r['t.CHARGE_NAME'].split(/[,，]/).map(s=>s.trim()).includes(f.owner_name)) : byCode;
    if(byCode.some(r=>r['t.select_flag']==='已选' && !matches.includes(r)))
      throw Error('FUNDING_API_EXTRA_SELECTION');
    if(byCode.length && !matches.length) throw Error('FUNDING_API_OWNER_BINDING');
    if(!matches.length){
      const other=definition.funcs.find(q=>String(q.funcno)==='243'),field=other?.formFields.find(q=>q.fieldname==='t.proj_code');
      if(!field?.Events?.[0]?.proc?.startsWith('$d572d69025d7eda5495af21191ca9f30|'))throw Error('FUNDING_API_OTHER_QUERY_CHANGED');
      const {data:lookup,set,get}=http.contextCopy();set('61-serialno',c.serial);
      for(const q of other.formFields)set('243-'+q.fieldname,'');set('243-t.proj_code',f.code);
      const raw=await http.post('common_callProc.action',{procName:field.Events[0].proc.split('|')[0],userData:JSON.stringify(lookup),projid:'WF_YBX'},'text');
      const keys=[...raw.matchAll(/(?:local\(|;)([^():;]+):/g)].map(m=>m[1]);
      if(keys.some(k=>!/^243-[A-Za-z0-9_.]+$/i.test(k)&&k.toLowerCase()!=='61-proj_changename'))throw Error('FUNDING_API_OTHER_QUERY_CONTEXT:'+raw);
      ReimbursementNativeProtocol.applyResponse(raw,lookup,keys);
      const target=Object.fromEntries(other.formFields.map(q=>[q.fieldname,get('243-'+q.fieldname)]));
      target['t.charge_sname']=get('61-proj_changename');
      if(!target['t.charge_sname']?.trim()||target['t.proj_code']!==f.code||
        f.owner_name&&!target['t.charge_sname'].split(/[,，]/).map(x=>x.trim()).includes(f.owner_name))throw Error('FUNDING_API_OTHER_NOT_UNIQUE:'+JSON.stringify(target));
      target['t.bx_amt']=f.amount;candidates.push({code:f.code,external:true,target});continue;
    }
    if(matches.length!==1) throw Error('FUNDING_API_CANDIDATE_NOT_UNIQUE');
    const row=matches[0];
    // 普通项目的grant_id可以为空；仍冻结并逐字核对该字段，不生成替代值。
    if(typeof row['t.grant_id']!=='string' ||
        f.owner_name && !row['t.CHARGE_NAME'].split(/[,，]/).map(s=>s.trim()).includes(f.owner_name)) throw Error('FUNDING_API_OWNER_BINDING');
    candidates.push({code:f.code,grant_id:row['t.grant_id'],owners:row['t.CHARGE_NAME']});
  }
  const leaves=original[0].rows.filter(r=>r['t.tlevel']==='2');
  const current=[...new Set(leaves.map(r=>r['t.uni_prj_code']))].sort();
  if(current.some(code=>!codes.includes(code))) throw Error('FUNDING_API_EXISTING_PROJECT_SCOPE');
  let implicit=null;
  const selectedProjects=rows.filter(r=>r['t.select_flag']==='已选').map(r=>r['t.uni_prj_code']).sort();
  if(!original[0].rows.length&&c.budget_allocation_mode==='system_default_single_funding'&&
      JSON.stringify(selectedProjects)===JSON.stringify([...codes].sort()))implicit=await reimbursementDefaultFunding(c);
  const matches=!!implicit||JSON.stringify(current)===JSON.stringify([...codes].sort()) &&
    leaves.reduce((sum,r)=>sum+moneyCents(r['t.amt']),0n)===moneyCents(c.expected_amount);
  const report={ok:true,complete:true,action:'funding-prepare',case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,
    business_transport:'native_http',system_write:false,save_dispatched:false,page_context_modified:false,
    definition_sha256:hash,bill:read.bill,original:{budget_rows:original[0].rows,...(implicit?{implicit_default:implicit.proof}:{})},changes:matches?{}:{projects:[...codes].sort()},
    candidates,requests:[...read.requests,...budget.requests,...requests,...(implicit?.requests||[])],observed_at:new Date().toISOString()};
  return {report,http,requests,win,choose,save,other_save:form.func_btns.find(b=>b.caption==='YC_保存他人项目')};
}

async function reimbursementFundingPrepare(c) {return (await reimbursementFundingBuild(c)).report;}

async function reimbursementFundingSave(c) {
  let dispatched=false,prepared=null;
  const steps=[];
  try {
    if(c.execute!==true || !c.expected_plan) throw Error('FUNDING_API_WRITE_INTENT');
    const built=await reimbursementFundingBuild(c);prepared=built.report;
    for(const key of ['original','changes','candidates','definition_sha256'])
      if(JSON.stringify(prepared[key])!==JSON.stringify(c.expected_plan[key])) throw Error('FUNDING_API_PLAN_DRIFT:'+key);
    if(!Object.keys(prepared.changes).length) return {...prepared,action:'funding-save',state:'already_matches_readback_required',requires_readback:true};
    const {data:context,set}=built.http.contextCopy();
    for(const key of Object.keys(context)) if(/^WF_YBX\$(?:242|243)-/.test(key)) delete context[key];
    set('61-serialno',c.serial);set('61-ismulti_proj',c.funding.length>1?'Y':'N');
    async function dispatch(name,check,procs) {
      built.http.guard();dispatched=true;steps.push({name,state:'dispatched'});
      const raw=await built.http.post('commonUpdate_responseButtonEvent.action',{
        cmd:ReimbursementNativeProtocol.command(check,procs,context,{}),projid:'WF_YBX',userContext:JSON.stringify(context),async:false},'text');
      // 只解析本域临时上下文，绝不运行服务端返回的JS或接受账号/单号覆盖。
      const keys=[...raw.matchAll(/(?:local\(|;)([^():;]+):/g)].map(m=>m[1]);
      if(keys.some(k=>!(/^(?:242|243)-[A-Za-z0-9_.]+$/i.test(k) || /^61-(?!serialno$)[A-Za-z0-9_.]+$/i.test(k))))
        throw Error('FUNDING_API_RESPONSE_CONTEXT');
      ReimbursementNativeProtocol.applyResponse(raw,context,keys);steps.at(-1).state='accepted';
      set('61-serialno',c.serial);set('61-ismulti_proj',c.funding.length>1?'Y':'N');
    }
    await dispatch('initialize','',built.win.beforeLoadProcs);
    const fresh=await reimbursementBusinessApi({...c,action:'bill-read',domain:'funding'});
    const rows=fresh.sections.find(s=>s.funcno==='242' && s.complete===true)?.rows;
    if(!rows || rows.some(r=>r['t.select_flag']==='已选' && !c.funding.some(f=>f.code===r['t.uni_prj_code'])))
      throw Error('FUNDING_API_SELECTION_DRIFT');
    for(const candidate of prepared.candidates) {
      if(candidate.external){
        const save=built.other_save;
        if(!save||save.procs.length!==1||!save.procs[0].startsWith('`$9687e941831f71c3a9579a55528e40a1:'))throw Error('FUNDING_API_OTHER_SAVE_CHANGED');
        for(const[k,v]of Object.entries(candidate.target))set('243-'+k,v);
        await dispatch('select-other:'+candidate.code,save.check_func,save.procs);continue;
      }
      const matches=rows.filter(r=>r['t.uni_prj_code']===candidate.code && r['t.grant_id']===candidate.grant_id && r['t.CHARGE_NAME']===candidate.owners);
      if(matches.length!==1) throw Error('FUNDING_API_CANDIDATE_DRIFT');
      if(matches[0]['t.select_flag']==='已选') continue;
      set('242-t.uni_prj_code',candidate.code);set('242-t.grant_id',candidate.grant_id);
      await dispatch('select:'+candidate.code,built.choose.check_func,built.choose.procs);
    }
    await dispatch('save',built.save.check_func,built.save.procs);
    return {...prepared,action:'funding-save',state:'saved_independent_readback_required',system_write:true,save_dispatched:true,
      requires_readback:true,steps,requests:[...prepared.requests,...built.requests.slice(1),...fresh.requests]};
  }catch(error){
    return {ok:false,complete:false,action:'funding-save',case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,
      business_transport:'native_http',system_write:dispatched,save_dispatched:dispatched,steps,prepared,
      state:dispatched?'unknown_save_readback_required':'rejected_before_save',requires_readback:true,error:String(error.message)};
  }
}
