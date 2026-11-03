/* 学生其他劳务：固定原生人员/银行卡查询，发放数值交学校计算。 */
function reimbursementRemunerationApplyFieldEvent(raw,context,personSetup=false,creationSetup=false){
  // School $.page.handleMsg takes each local block up to its last ')', and
  // applies only pairs with exactly one ASCII colon. Keep that data grammar
  // local to salary setup; save responses and the shared protocol stay strict.
  const globalKeys=new Set(['0-ABST_ISSTD','0-ABST_MEMATTR','0-ABST_UNIT','0-UNIT_DESP','0-STD_CTYPE','0-STD_CTYPEHINT','0-CTRL_TYPE','0-CJ_FF_MONTHS_DATE_MAX','0-CJ_FF_MONTHS_DATE_MIN','925-GROUPSHOWVALUE','0-ZKD_CJXZ_HINT','0-EXCEL_TEMPLATEI','0-CJFIX_CHANGE_REMINDER','0-CJFIX_CHANGE_SINGLE_REMARK']);
  const ignoredKeys=new Set(['61-REMARK_INIT','61-CJFZEXMENTOR_TYPE_LIST','61-CJFZEXDEGREE_TYPE_LIST','61-CJFZEXIS_ACTIVITIES_LIST','61-CJFZEXIS_TEACHER_LIST','61-CJFZEXWORK_HOURS_LIST','61-CJFZEXREVIEW_KIND_LIST','61-CJFZEXREVIEW_CONTENT_LIST']);
  if(creationSetup)['0-BXSX_JSON','0-G0SERIALNO','0-LC_FINISH','0-NEED_TUROR','0-ZIP_SEQ'].forEach(key=>globalKeys.add(key));
  if(personSetup){['0-CJ_SELECT_OTH_STD','0-CARDNO_IS_READONLY','0-DBLRORDERSTRS'].forEach(key=>globalKeys.add(key));ignoredKeys.add('61-PREMARK_SHOW');}
  if(typeof raw!=='string'||raw.length>65536)throw Error('REMUNERATION_API_EVENT_CONTEXT_CHANGED');
  const parts=raw.split('local('),updates=Object.create(null),ignored=[];
  if(parts.shift()!=='pass')throw Error('REMUNERATION_API_FIELD_EVENT_STATUS');
  for(const part of parts){
    const end=part.lastIndexOf(')');
    if(end<0||! /^(?:pass)*$/.test(part.slice(end+1)))throw Error('REMUNERATION_API_FIELD_EVENT_SYNTAX');
    for(const pair of part.slice(0,end).split(';').filter(Boolean)){
      if(pair==='61-cj_fcnt_controlN'){ignored.push(pair);continue;}
      const values=pair.split(':'),key=values[0].toUpperCase();
      if(values.length>2&&ignoredKeys.has(key)){ignored.push(key);continue;}
      if(values.length!==2||(!/^(?:61|277)-[A-Z0-9_.]+$/.test(key)&&!globalKeys.has(key))||Object.hasOwn(updates,key))throw Error('REMUNERATION_API_EVENT_CONTEXT_CHANGED');
      updates[key]=values[1];
    }
  }
  // Parse every block before applying anything: no partial context on refusal.
  for(const[key,value]of Object.entries(updates))context[(key.startsWith('0-')?'':'WF_YBX$')+key]=value;
  return{updates,ignored_native_pairs:ignored};
}

function reimbursementRemunerationPreview(doc,salary,c){
  const money=ReimbursementHttpValues.moneyCents,header=['序号','工号','姓名','人员类型','支付方式','卡号','户名','发放事由','金额'];
  const tables=[...doc.querySelectorAll('table')].map(t=>[...t.rows].map(r=>[...r.cells].map(cell=>cell.textContent.trim()))).filter(table=>table.some(row=>JSON.stringify(row)===JSON.stringify(header)));
  if(tables.length!==1||!salary.complete||salary.bill?.['t.bx_type']!=='8')throw Error('REMUNERATION_API_PREVIEW_SCOPE');
  const expected=Object.entries(c.activity.approved_people).map(([id,p])=>({id,name:p.name,amount:money(p.amount).toString()})).sort((a,b)=>a.id.localeCompare(b.id));
  const actual=salary.rows.map(r=>({id:r['t.unino'],name:r['t.name'],amount:money(r['t.AMT']).toString()})).sort((a,b)=>a.id.localeCompare(b.id));
  const observed=tables[0].filter(r=>r.length===9&&/^\d+$/.test(r[0])).map(r=>({id:r[1],name:r[2],amount:money(r[8]).toString()})).sort((a,b)=>a.id.localeCompare(b.id));
  if(JSON.stringify(expected)!==JSON.stringify(actual)||JSON.stringify(expected)!==JSON.stringify(observed)||actual.reduce((s,r)=>s+BigInt(r.amount),0n)!==money(c.expected_amount))throw Error('REMUNERATION_API_PREVIEW_PEOPLE_CHANGED');
  for(const row of tables[0].filter(r=>r.length===9&&/^\d+$/.test(r[0]))){const source=salary.rows.find(r=>r['t.unino']===row[1]);if(row[3]!=='学生'||row[7]!==source['t.premark']||row[6]!==source['t.name'])throw Error('REMUNERATION_API_PREVIEW_PERSON_CHANGED');}
  for(const[k,v]of Object.entries({'dd.state_ybx':'学生人员一般薪酬','dd.state':'学生','dd.abst':'学生其他劳务','dd.ff_startdate':c.activity.period_start,'dd.ff_enddate':c.activity.period_end,'dd.hastax':'T'}))if(salary.original[k].trim()!==v)throw Error('REMUNERATION_API_PREVIEW_PERIOD_CHANGED');
  const mode=[...doc.querySelectorAll('input[name="form_cj_paytype"]')];if(mode.length!==2||mode.filter(e=>e.checked).length!==1||!mode.some(e=>e.checked&&e.getAttribute('value')==='N'))throw Error('REMUNERATION_API_PREVIEW_NORMAL_TRANSFER_REQUIRED');
  return{original:salary.original,rows:salary.rows,preview_people:observed};
}

function reimbursementRemunerationNoExtraPayments(payments){
  if(!payments.ok||!payments.complete||payments.domain!=='payments'||!payments.sections?.length)throw Error('REMUNERATION_API_PAYMENT_READ_INCOMPLETE');
  const fields={'22':['AMOUNT'],'24':['AMOUNT'],'285':['AMOUNT'],'456':['AMOUNT'],'994':['AMOUNT'],'1791':['AMOUNT'],'2212':['CASHAMOUNT','HKAMOUNT']};
  for(const[no,keys]of Object.entries(fields)){
    const sections=payments.sections.filter(s=>s.funcno===no&&s.kind==='form');
    if(sections.length!==1||sections[0].rows.length!==1)throw Error('REMUNERATION_API_PAYMENT_DOMAIN_MISSING');
    for(const key of keys){const value=sections[0].rows[0][key];if(typeof value!=='string'||ReimbursementHttpValues.moneyCents(value||'0')!==0n)throw Error('REMUNERATION_API_EXTRA_PAYMENT_NOT_ZERO');}
  }
  const split=payments.sections.filter(s=>s.funcno==='2225'&&s.kind==='form');if(split.length!==1||split[0].rows.length)throw Error('REMUNERATION_API_EXTRA_PAYMENT_SPLIT');
}

async function reimbursementRemunerationBuild(c){
  const requests=[],http=createReimbursementHttp(c.expected_userid,{prefix:'REMUNERATION_API',requests,timeout:15000,allowed:['definition','binding','procedure','button','query','gridDefinition','remunerationBankCheck']});
  const def=await reimbursementAmountDefinition(http,'198',c.remuneration_contract.definition_sha256),personDef=await reimbursementAmountDefinition(http,'199',c.remuneration_contract.person_definition_sha256);
  const form=def.funcs.find(f=>String(f.funcno)==='277'),person=personDef.funcs.find(f=>String(f.funcno)==='282');
  let bill=null;
  if(c.serial){const owned=await reimbursementBusinessApi({...c,action:'bills-list',serials:[c.serial]});if(!owned.complete||owned.rows.length!==1||owned.rows[0]['t.bx_type']!=='8'||ReimbursementHttpValues.moneyCents(owned.rows[0]['t.total_amt'])!==ReimbursementHttpValues.moneyCents(c.activity.claim_amount))throw Error('REMUNERATION_API_OWNED_BILL_REQUIRED');bill=owned.rows[0];requests.push(...owned.requests);}
  const{data:context,set,get}=http.contextCopy();set('61-serialno',c.serial||'');set('61-cj_baseinfo','');
  // 表单和人员行必须从同一原生单号完整查询，拒绝继承另一个页面的277/282字段。
  for(const key of Object.keys(context))if(/^WF_YBX\$(277|282|280)-/.test(key))delete context[key];
  const[sql,refs]=form.bindSql.split('|'),cvstr=refs.split(',').map(ref=>ref+'::'+get(ref.slice(1,-1))).join('$$');
  const bound=ReimbursementHttpValues.formRows(await http.post('common_bindSQLDataBackend.action',{sqlid:sql.slice(1),cvstr,projid:'WF_YBX'},'text'));
  if(bound.length!==1)throw Error('REMUNERATION_API_BASE_FORM_REQUIRED');
  const defaulted=[];
  for(const field of form.formFields){const key=field.fieldname.replace(/^dd\./,'').toUpperCase();if(!Object.hasOwn(bound[0],key)&&field.nullable===true&&field.initdata===''){bound[0][key]='';defaulted.push(field.fieldname);}}
  const original=ReimbursementNativeForm.bind(form,bound[0],set);
  const rows=await reimbursementRemunerationRows(c,http,context);
  const approved=c.activity.approved_people,seen=new Set();
  for(const row of rows){
    const id=row['t.unino'];if(seen.has(id)||!approved[id]||row['t.name']!==approved[id].name||ReimbursementHttpValues.moneyCents(row['t.AMT'])!==ReimbursementHttpValues.moneyCents(approved[id].amount)||row['t.serial_no']!==(c.serial||'')||row['t.tips'].trim())throw Error('REMUNERATION_API_FOREIGN_OR_INVALID_PERSON');seen.add(id);
  }
  return{http,def,personDef,form,person,context,set,get,rows,original,defaulted,bill,requests};
}

async function reimbursementRemunerationRows(c,http,context){
  const contract=c.remuneration_contract.query,actual=await http.post('loadDefinition.action?type=Q',{funcNo:'280',projid:'WF_YBX'}),q=Array.isArray(actual)?actual[0]:actual;
  if(!q||String(q.funcno)!=='280'||JSON.stringify(q.colModel.map(v=>v.name))!==JSON.stringify(contract.queryColumns))throw Error('REMUNERATION_API_QUERY_DEFINITION_CHANGED');
  for(const key of Object.keys(context))if(key.startsWith('WF_YBX$280-'))delete context[key];
  Object.assign(context,{'WF_YBX$280-COND':'','WF_YBX$280-CONDOBJ':[],'WF_YBX$280-CURRPAGE':1});
  const rows=[];let count=null,pages=1;
  for(let page=1;page<=pages;page++){
    const r=await http.post('commonQuery_doQuery.action?funcno=280&projid=WF_YBX&needcount=true',{_search:false,nd:Date.now(),page,rows:'100',sidx:'',sord:'',userData:JSON.stringify(context),itemArr:'',sectionArr:''}),n=Number(r?.records),p=Number(r?.total);
    // Native F280 returns [] for a new, empty claim; never accept this shorthand for a bound claim or later page.
    if(!c.serial&&page===1&&count===null&&Array.isArray(r)&&r.length===0)return [];
    if(!Number.isSafeInteger(n)||n<0||n>1000||p!==Math.ceil(n/100)||![page,...(n===0?[0]:[])].includes(Number(r.page))||!Array.isArray(r.rows)||r.rows.length!==Math.min(100,Math.max(0,n-(page-1)*100))||count!==null&&(n!==count||p!==pages))throw Error('REMUNERATION_API_INCOMPLETE_ROWS:'+JSON.stringify({response_type:Array.isArray(r)?'array':typeof r,array_length:Array.isArray(r)?r.length:null,keys:r&&typeof r==='object'&&!Array.isArray(r)?Object.keys(r):[],records:r?.records,total:r?.total,page:r?.page,rows_array:Array.isArray(r?.rows),row_count:Array.isArray(r?.rows)?r.rows.length:null}));
    count=n;pages=p;for(const item of r.rows){if(!Array.isArray(item.cell)||item.cell.length!==contract.queryColumns.length)throw Error('REMUNERATION_API_ROW_COLUMNS');rows.push(Object.fromEntries(contract.queryColumns.map((key,i)=>[key,ReimbursementHttpValues.decode(item.cell[i])])));}
  }
  return rows;
}

function reimbursementRemunerationSelectPerson(c,data){
  if(!Array.isArray(data)||data.length!==1)throw Error('REMUNERATION_API_PERSON_NOT_UNIQUE');
  const p=data[0],approved=c.activity.approved_people[c.student_id];
  if(!approved||String(p.unino).trim()!==c.student_id||String(p.name).trim()!==approved.name||String(p.state).trim()!=='学生'||p.allowedFlag!=='T'||!Array.isArray(p.cardInfo)||!p.cardInfo.length)throw Error('REMUNERATION_API_PERSON_NOT_ELIGIBLE');
  // 学校默认优先第一张非空卡；只接受普通校内学生银行转卡，现金和多人卡均不猜选。
  const card=p.cardInfo.find(k=>typeof k.card_no==='string'&&k.card_no.length>0);
  if(!card||card.bankno==='0'||!(/^[0-9]{10,30}$/.test(card.card_no)||/^[0-9]{6}\*{4}[0-9]{4}$/.test(card.card_no))||typeof card.bankno!=='string'||!card.bankno||typeof card.bankname!=='string'||!card.bankname||String(card.name).trim()!==approved.name||typeof card.other_information!=='string')throw Error('REMUNERATION_API_DEFAULT_BANK_NOT_VERIFIED');
  return{person:p,card};
}

function reimbursementRemunerationPersonRules(person,get){
  const form=JSON.parse(JSON.stringify(person)),groups=person.groupnamefrom===''?'':get('282-groupname');
  if(typeof groups!=='string')throw Error('REMUNERATION_API_PERSON_VISIBLE_GROUPS_MISSING');
  const visible=ReimbursementNativeForm.visible(form,groups),hidden=new Set(['dd.is_qsgx','dd.after_income','dd.rank_post','dd.inv_no']);
  if(get('61-taxable')==='Y')throw Error('REMUNERATION_API_WITHHOLDING_DATE_REQUIRES_CONFIRMED_SOURCE');
  const version=get('61-std_version'),raw=get('61-otherinfo_json');
  if(version!=='V2'||typeof raw!=='string')throw Error('REMUNERATION_API_PERSON_RULE_VERSION');
  const rules=JSON.parse(raw.replaceAll('：',':')),seen=new Set();
  if(!Array.isArray(rules)||rules.length>100)throw Error('REMUNERATION_API_PERSON_RULES');
  for(const rule of rules){
    if(!rule||!/^\w+$/.test(rule.col_name)||seen.has(rule.col_name)||!['Y','N'].includes(rule.netin)||!['Y','N'].includes(rule.nullFlag)||!['N','D','S','E'].includes(rule.format)||typeof rule.enum_list!=='string')throw Error('REMUNERATION_API_PERSON_RULES');
    seen.add(rule.col_name);const name='dd.'+rule.col_name,field=form.formFields.find(f=>f.fieldname===name);if(!field)continue;
    field.nullable=rule.nullFlag==='Y';if(rule.format==='N')field.format='number';if(rule.format==='D')field.format='digits';
    if(rule.netin!=='Y'&&!['unino','premark'].includes(rule.col_name))hidden.add(name);
    if(rule.netin==='Y'&&rule.enum_list)throw Error('REMUNERATION_API_ENUM_SELECTION_REQUIRES_SOURCE:'+name);
  }
  if(get('0-CJ_YHZZ_ISSHOWBANK')!=='Y')hidden.add('dd.branchinfo');
  if(get('61-isunit_other')!=='T')hidden.add('dd.funit');
  if(get('61-abst_isstd')!=='T'&&get('61-CJFZEXFCNT_NETIN')!=='Y')hidden.add('dd.fcnt');
  if(get('61-gznr_show')!=='T')hidden.add('dd.premark');
  if(get('0-cjfix_change_single_remark')==='')hidden.add('dd.single_entry_remark');
  if(visible.has('dd.work_hours')&&!hidden.has('dd.work_hours')&&get('61-UPD_WORK_HOURS')==='Y')hidden.add('dd.work_hours');else hidden.add('dd.work_hours2');
  if(get('61-CJFZEXSTANDARD_NETIN')==='Y'&&get('61-CJFZEXSTANDARD_NULL')==='N'&&get('61-CJFZEXFCNT_NETIN')==='Y'&&get('61-CJFZEXFCNT_NULL')==='N')throw Error('REMUNERATION_API_CALCULATED_AMOUNT_REQUIRES_SOURCE');
  if(get('0-CJ_SELECT_OTH_STD')||get('61-cj_sel_standard'))throw Error('REMUNERATION_API_STANDARD_SELECTION_REQUIRES_SOURCE');
  for(const name of hidden)visible.delete(name);
  return{version,form,visible:[...visible].sort(),rules};
}

async function reimbursementRemunerationPersonSetup(c,b){
  const {http,set,get,context}=b;
  for(const[key,value]of Object.entries({'dd.state_ybx':'学生人员一般薪酬','dd.state':'学生','dd.abst':'学生其他劳务','dd.ff_startdate':c.activity.period_start,'dd.ff_enddate':c.activity.period_end,'dd.hastax':'T'}))set('277-'+key,value);
  set('61-BX_JBXX_BX_TYPE','8');set('61-jzxjff','');set('61-BX_JKINFO','');
  for(const field of ['dd.state_ybx','dd.abst','dd.ff_startdate','dd.ff_enddate']){
    const event=b.form.formFields.find(f=>f.fieldname===field)?.Events.find(e=>e.event==='change'&&e.proc);
    if(!event)throw Error('REMUNERATION_API_FIELD_EVENT_MISSING');
    const raw=await http.post('common_callProc.action',{procName:event.proc.split('|')[0],userData:JSON.stringify(context),projid:'WF_YBX'},'text');
    reimbursementRemunerationApplyFieldEvent(raw,context);
  }
  for(const field of b.person.formFields)set('282-'+field.fieldname,'');
  const procs=b.personDef.wins.find(w=>String(w.winno)==='199').beforeLoadProcs;
  const cmd=ReimbursementNativeProtocol.command('',procs,context,{...b.form.typeMap,...b.person.typeMap});
  const raw=await http.post('commonUpdate_responseButtonEvent.action',{cmd,projid:'WF_YBX',userContext:JSON.stringify(context),async:false},'text');
  reimbursementRemunerationApplyFieldEvent(raw,context,true);
  // These native setup procedures return local form rules only; no add/save button is dispatched.
  return reimbursementRemunerationPersonRules(b.person,get);
}

async function reimbursementRemunerationInspect(c){
  const b=await reimbursementRemunerationBuild(c),report={ok:true,complete:true,action:'remuneration-inspect',case_id:c.case_id,serial:c.serial||'',expected_userid:c.expected_userid,business_transport:'native_http',system_write:false,save_dispatched:false,original:b.original,native_empty_defaults:b.defaulted,rows:b.rows,bill:b.bill,definition_sha256:c.remuneration_contract.definition_sha256,requests:b.requests};
  if(c.student_id){
    report.person_rules=await reimbursementRemunerationPersonSetup(c,b);
    for(const[key,value]of Object.entries({'dd.state_ybx':'学生人员一般薪酬','dd.state':'学生','dd.abst':'学生其他劳务','dd.ff_startdate':c.activity.period_start,'dd.ff_enddate':c.activity.period_end,'dd.hastax':'T'}))b.set('277-'+key,value);
    for(const field of b.person.formFields)b.set('282-'+field.fieldname,'');
    b.set('282-dd.unino',c.student_id);b.set('61-jzxjff','');b.set('280-t.personid_jm','');b.set('280-t.name','');
    const event=b.person.formFields.find(f=>f.fieldname==='dd.unino').Events.find(e=>e.event==='blur');
    if(event.proc!==c.remuneration_contract.lookup_proc||event.callBack!=='cj_get_unino_dtl_v1();')throw Error('REMUNERATION_API_LOOKUP_CHANGED');
    const raw=await b.http.post('common_callProc.action',{procName:event.proc.split('|')[0],userData:JSON.stringify(b.context),projid:'WF_YBX'},'text');
    const keys=[...raw.matchAll(/(?:local\(|;)([^():;]+):/g)].map(m=>m[1]);
    if(keys.some(k=>!['0-BX_CJDBINFO','BX_CJDBINFO'].includes(k.toUpperCase())&&!/^282-[A-Za-z0-9_.]+$/.test(k)))return{...report,ok:false,complete:false,state:'native_lookup_contract_changed',lookup_context_keys:keys,native_lookup_response:raw};
    ReimbursementNativeProtocol.applyResponse(raw,b.context,keys);
    const text=b.get('0-BX_CJDBINFO')||b.get('BX_CJDBINFO');if(typeof text!=='string')throw Error('REMUNERATION_API_PERSON_DATA_MISSING');
    const selected=reimbursementRemunerationSelectPerson(c,JSON.parse(text.replaceAll('：',':')));report.person=selected.person;report.default_bank=selected.card;
    const v={'dd.unino':c.student_id,'dd.sname':selected.card.name.trim(),'dd.state':selected.person.state.trim(),'dd.bankno':selected.card.bankno+'!P!'+selected.card.card_no+'!P!'+selected.card.bankname,'dd.cardno':selected.card.card_no,'dd.branchinfo':selected.card.branchcode||'','dd.tele':selected.person.tele||'','dd.jdata':selected.card.other_information,'dd.company':selected.person.company||'','dd.tech_post':selected.person.tech_post||'','dd.amt':c.activity.amount,'dd.premark':c.activity.reason,'dd.inv_no':''};
    for(const[k,value]of Object.entries(v))b.set('282-'+k,value);
    ReimbursementNativeForm.validate(report.person_rules.form,new Set(report.person_rules.visible),b.get,['dd.unino','dd.amt','dd.premark']);
  }
  return report;
}

async function reimbursementRemunerationSave(c){
  let dispatched=false,creationDispatched=false;const requests=[],steps=[];let eventDiagnostic=null;
  try{
    if(c.execute!==true||!c.pending_registered||!c.expected_plan||!['add-person','create-claim','save'].includes(c.action))throw Error('REMUNERATION_API_PENDING_REQUIRED');
    const p=await reimbursementRemunerationInspect(c);
    for(const key of ['original','rows','person','default_bank','bill','person_rules'])if(JSON.stringify(ReimbursementNativeForm.canonical(p[key]))!==JSON.stringify(ReimbursementNativeForm.canonical(c.expected_plan[key])))throw Error('REMUNERATION_API_PREPARE_DRIFT');
    const b=await reimbursementRemunerationBuild(c),{http,context,set,get}=b;
    if(c.serial&&(b.bill['t.status']!=='-99'||b.bill['t.lc_finish']!=='F'))throw Error('REMUNERATION_API_EXISTING_WORKFLOW_MUST_NOT_CHANGE');
    const native=async(button,confirmed=false)=>{
      if(!button||button.wf_enabled||button.customFunc||button.pre_compute||button.jscontent)throw Error('REMUNERATION_API_BUTTON_CHANGED');
      const cmd=ReimbursementNativeProtocol.command(button.check_func,button.procs,context,{...b.form.typeMap,...b.person.typeMap});dispatched=true;
      if(c.action==='create-claim'&&button.btnguid==='85DE9208A69E45AA8A37DC7947F503FA')creationDispatched=true;
      const raw=await http.post('commonUpdate_responseButtonEvent.action',{cmd,projid:'WF_YBX',userContext:JSON.stringify(context),async:false},'text');
      if(!button.caption&&button.procs===b.personDef.wins.find(w=>String(w.winno)==='199').beforeLoadProcs){
        reimbursementRemunerationApplyFieldEvent(raw,context,true);steps.push('native_setup');return;
      }
      const keys=[...raw.matchAll(/(?:local\(|;)([^():;]+):/g)].map(m=>m[1]);
      if(keys.some(k=>!/^61-[A-Za-z0-9_.]+$/i.test(k)&&!/^277-[A-Za-z0-9_.]+$/.test(k)&&!/^282-[A-Za-z0-9_.]+$/.test(k)&&!['0-G0SERIALNO','0-LC_FINISH',...(c.action==='create-claim'?['0-BXSX_JSON','0-NEED_TUROR','0-ZIP_SEQ']:[])].includes(k.toUpperCase()))){eventDiagnostic={button:button.caption||'native_setup',keys,response:raw};throw Error('REMUNERATION_API_SAVE_CONTEXT_CHANGED');}
      eventDiagnostic={button:button.caption,keys,response:raw};
      if(c.action==='create-claim'&&raw.startsWith('pass')){reimbursementRemunerationApplyFieldEvent(raw,context,false,true);steps.push(button.caption);return;}
      const response=ReimbursementNativeProtocol.response(raw,keys);
      if(response.confirmation_required){
        eventDiagnostic={button:button.caption,keys,response:raw};
        const message=response.message.replace(/<[^>]*>/g,'').replace(/&nbsp;/g,' '),counts=[...message.matchAll(/正确发放记录共\s*(\d+)\s*条[，,]\s*合计\s*([\d,.]+)\s*元/g)];
        if(confirmed||c.action!=='create-claim'||button.btnguid!=='394EF3609EDB471E812179D639C5DD95'||counts.length!==1||/错误|异常|不允许|失败|超/.test(message)||Number(counts[0][1])!==b.rows.length||ReimbursementHttpValues.moneyCents(counts[0][2])!==ReimbursementHttpValues.moneyCents(c.activity.claim_amount))throw Error('REMUNERATION_API_CREATION_CONFIRMATION_SCOPE');
        for(const[key,value]of Object.entries(response.updates))set(key,value);
        steps.push('native_people_total_confirmed');
        // Native handleMsg's continue callback runs the same fixed procedures
        // with check_func=escape after the successful count/amount check.
        await native({...button,check_func:'escape'},true);return;
      }
      ReimbursementNativeProtocol.applyResponse(raw,context,keys);steps.push(button.caption||'native_setup');
    };
    const event=async(field,event)=>{
      const definition=b.form.formFields.find(f=>f.fieldname===field)?.Events.find(e=>e.event===event&&e.proc);if(!definition)throw Error('REMUNERATION_API_FIELD_EVENT_MISSING');
      const raw=await http.post('common_callProc.action',{procName:definition.proc.split('|')[0],userData:JSON.stringify(context),projid:'WF_YBX'},'text'),keys=[...raw.matchAll(/(?:local\(|;)([^():;]+):/g)].map(m=>m[1]);
      try{reimbursementRemunerationApplyFieldEvent(raw,context);}catch(error){eventDiagnostic={field,event,keys,response:raw};throw error;}
    };
    const target={'dd.state_ybx':'学生人员一般薪酬','dd.state':'学生','dd.abst':'学生其他劳务','dd.ff_startdate':c.activity.period_start,'dd.ff_enddate':c.activity.period_end,'dd.hastax':'T'};
    if(b.rows.length&&Object.entries(target).some(([k,v])=>String(b.original[k]).trim()!==v))throw Error('REMUNERATION_API_EXISTING_ACTIVITY_CHANGED');
    if(!b.rows.length&&c.serial)throw Error('REMUNERATION_API_BOUND_PERSON_SET_MISSING');
    if(c.serial&&c.action==='create-claim'||!c.serial&&c.action==='save')throw Error('REMUNERATION_API_CREATE_OR_SAVE_SCOPE');
    set('61-BX_JBXX_BX_TYPE','8');set('61-cj_tks_sum','');set('61-jzxjff','');set('61-BX_JKINFO','');
    for(const[key,value]of Object.entries(target))set('277-'+key,value);
    if(!['','1'].includes(String(b.original['dd.ffcycle']).trim()))throw Error('REMUNERATION_API_BATCH_MONTHS_NOT_IN_SCOPE');set('277-dd.ffcycle','1');
    if(!b.rows.length&&c.action!=='add-person'){
      dispatched=true;await event('dd.state_ybx','change');await event('dd.abst','change');await event('dd.ff_startdate','change');await event('dd.ff_enddate','change');
    }
    if(get('61-taxable')==='Y')throw Error('REMUNERATION_API_WITHHOLDING_DATE_REQUIRES_CONFIRMED_SOURCE');set('277-dd.withholding_obligation_date','');
    if(c.action==='add-person'){
      if(!c.student_id||b.rows.some(r=>r['t.unino']===c.student_id))throw Error('REMUNERATION_API_DUPLICATE_PERSON');
      const selected=reimbursementRemunerationSelectPerson(c,[p.person]),card=selected.card,person=selected.person;
      for(const field of b.person.formFields)set('282-'+field.fieldname,'');
      const rules=await reimbursementRemunerationPersonSetup(c,b);
      if(JSON.stringify(ReimbursementNativeForm.canonical(rules))!==JSON.stringify(ReimbursementNativeForm.canonical(p.person_rules)))throw Error('REMUNERATION_API_PERSON_RULES_CHANGED');
      const values={'dd.unino':c.student_id,'dd.sname':card.name.trim(),'dd.state':person.state.trim(),'dd.bankno':card.bankno+'!P!'+card.card_no+'!P!'+card.bankname,
        'dd.cardno':card.card_no,'dd.branchinfo':card.branchcode||'','dd.tele':person.tele||'','dd.jdata':card.other_information,'dd.company':person.company||'','dd.tech_post':person.tech_post||'',
        'dd.amt':c.activity.amount,'dd.premark':c.activity.reason,'dd.inv_no':''};
      for(const[key,value]of Object.entries(values))set('282-'+key,value);
      if(c.activity.reason.length>20||!c.activity.reason||ReimbursementHttpValues.moneyCents(c.activity.amount)<=0n)throw Error('REMUNERATION_API_REASON_OR_AMOUNT');
      ReimbursementNativeForm.validate(rules.form,new Set(rules.visible),get,['dd.unino','dd.amt','dd.premark']);
      await native(b.personDef.wins.find(w=>String(w.winno)==='199').func_btns.find(btn=>btn.btnguid==='289FA79F8B1D4C6F8E427D84000E92D9'));
    }else{
      if(b.rows.length!==Object.keys(c.activity.approved_people).length)throw Error('REMUNERATION_API_COMPLETE_APPROVED_PERSON_SET_REQUIRED');
      set('61-bankcheck_id','');set('61-bankolpaycheck_id','');set('0-zip_seq','');
      // 银行三要素前置检查不可因为是新HTTP入口而省略。
      for(const[url,flag,key]of [['bankOlpayCheck_BankCheckDtlExcel.action',get('0-BANKCHECK_URL')!=='F','61-bankolpaycheck_id'],['bankCheck_BankCheckDtlExcel.action',get('61-CJ_CHECJSYS')==='Y','61-bankcheck_id']]){
        if(!flag)continue;dispatched=true;const request=await http.post(url,{user_id:c.expected_userid,user_name:get('0-userinfo.username'),serial_no:c.serial||'',type:'酬金申报'},'text');
        if(typeof request!=='string'||!request||!/^[A-Za-z0-9_-]{1,100}$/.test(request))throw Error('REMUNERATION_API_BANK_BATCH_CHECK_UNKNOWN');set(key,request);
      }
      const win=b.def.wins.find(w=>String(w.winno)==='198');
      if(c.action==='save')await native(win.func_btns.find(btn=>btn.btnguid==='7FDC2123B80D4499AE9D186842DB040E'));
      else{
        await native(win.func_btns.find(btn=>btn.btnguid==='394EF3609EDB471E812179D639C5DD95'));
        if(Number(get('61-BXSX_COUNT')||'0')>0||get('61-cj_stdinfo'))throw Error('REMUNERATION_API_CREATION_EXTRA_NATIVE_REVIEW');
        await native(win.func_btns.find(btn=>btn.btnguid==='85DE9208A69E45AA8A37DC7947F503FA'));
      }
    }
    const serial=c.serial||get('0-g0Serialno');if(c.action==='create-claim'&&!/^\d+$/.test(serial))throw Error('REMUNERATION_API_CREATED_SERIAL_UNKNOWN');
    return{ok:true,action:'remuneration-save',operation:c.action,case_id:c.case_id,serial:c.serial||'',expected_userid:c.expected_userid,new_serial:c.action==='create-claim'?serial:null,business_transport:'native_http',system_write:dispatched,save_dispatched:dispatched,creation_procedure_dispatched:creationDispatched,steps,state:'saved_independent_readback_required',requests:[...p.requests,...b.requests,...requests]};
  }catch(error){return{ok:false,action:'remuneration-save',operation:c.action,case_id:c.case_id,serial:c.serial||'',expected_userid:c.expected_userid,business_transport:'native_http',system_write:dispatched,save_dispatched:dispatched,creation_procedure_dispatched:creationDispatched,steps,state:dispatched?'unknown_original_remuneration_recovery_required':'rejected_before_save',error:String(error.message),event_diagnostic:eventDiagnostic,requests};}
}
