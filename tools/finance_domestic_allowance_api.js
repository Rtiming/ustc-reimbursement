/* 有票国内差旅补助的固定原生合同；不改变页面UC，也不执行保存。 */
async function reimbursementDomesticAllowanceInspect(c){
  const travel=await reimbursementBusinessApi({...c,action:'bill-read',domain:'travel',contracts:c.travel_contracts});
  const used=await reimbursementBusinessApi({...c,action:'bill-read',domain:'used_invoices',contracts:c.used_contracts});
  const bill=travel.bill;
  if(!travel.complete||!used.complete||bill['t.status']!=='-99'||bill['t.lc_finish']!=='F'||bill['t.bx_type']!=='2'||bill['t.wf_name']||bill['t.uni_key']||bill['t.node_id']||JSON.stringify(used.sections[0].rows.map(r=>r['t.tk_guid']).sort())!==JSON.stringify(c.expected_guids))throw Error('DOMESTIC_ALLOWANCE_OWN_DRAFT');
  const requests=[],http=createReimbursementHttp(c.expected_userid,{prefix:'DOMESTIC_ALLOWANCE',requests,timeout:15000,allowed:['definition','binding','button','procedure']});
  const definitions={};
  for(const winno of ['128','353']){
    const value=await http.post('loadDefinition.action?type=W',{winno,projid:'WF_YBX'});
    const digest=[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(ReimbursementNativeForm.canonical(Object.fromEntries(['wins','funcs','funcMap','actMap'].map(k=>[k,value[k]])))))))].map(n=>n.toString(16).padStart(2,'0')).join('');
    if(digest!==c.definition_hashes[winno])throw Error('DOMESTIC_ALLOWANCE_DEFINITION_CHANGED');definitions[winno]=value;
  }
  const {data:context,set,get}=http.contextCopy();set('61-serialno',c.serial);set('61-xgbt_src','报销单列表');
  async function init(win){
    const raw=await http.post('commonUpdate_responseButtonEvent.action',{cmd:ReimbursementNativeProtocol.command('',win.beforeLoadProcs,context,{}),projid:'WF_YBX',userContext:JSON.stringify(context),async:false},'text');
    const keys=[...raw.matchAll(/(?:local\(|;)([^():;]+):/g)].map(m=>m[1]);
    if(keys.some(k=>!/^61-[A-Za-z0-9_.]+$/i.test(k)&&k!=='0-is_refund_field_name'))throw Error('DOMESTIC_ALLOWANCE_INIT_CONTEXT:'+raw);
    try{ReimbursementNativeProtocol.applyResponse(raw,context,keys);}catch(e){throw Error(e.message+':'+raw);}return raw;
  }
  async function bind(form){
    const [sql,refs]=form.bindSql.split('|'),cvstr=refs.split(',').map(r=>r+'::'+get(r.slice(1,-1))).join('$$');
    const raw=await http.post('common_bindSQLDataBackend.action',{sqlid:sql.slice(1),cvstr,projid:'WF_YBX'},'text');
    return{raw,rows:ReimbursementHttpValues.formRows(raw)};
  }
  const win=definitions['128'].wins.find(w=>String(w.winno)==='128'),form=definitions['128'].funcs.find(f=>f.funcno==='192');
  const allowances=[];
  for(const row of travel.sections[0].rows){
    set('61-sno',row['t.sno']);set('61-cityname',row['t.city']);set('61-startdate',row['t.startdate']);set('61-STAYDAYS_JT',row['t.STAYDAYS_JT']);set('61-STAYDAYS_HS',row['t.STAYDAYS_HS']);
    const init_raw=await init(win),bound=await bind(form);
    allowances.push({row,init_raw,bound,policy:Object.fromEntries(['61-bt_groupname_xgbt','61-cl_subsidy_canupdate','61-bt_days_change','61-bt_hs_change','61-bt_jt_change','0-FORM_DEFINITION'].map(k=>[k,get(k)]))});
  }
  set('490-MultiRows','');set('490-MultiCount','0');set('61-xcrid','');set('0-rid','');set('1545-userid','');
  const trip_definition=definitions['353'],initial=[];
  for(const no of ['355','354'])initial.push({winno:no,raw:await init(trip_definition.wins.find(w=>String(w.winno)===no))});
  const templates=[];
  for(const no of ['492','493'])templates.push({funcno:no,...await bind(trip_definition.funcs.find(f=>f.funcno===no))});
  return{ok:true,complete:true,action:'domestic-allowance-inspect',case_id:c.case_id,expected_userid:c.expected_userid,serial:c.serial,bill,used,allowances,initial,templates,definitions,
    policy:Object.fromEntries(['61-bt_groupname','61-bt_groupname_xgbt','61-cl_subsidy_canupdate','61-xcrid','0-FORM_DEFINITION'].map(k=>[k,get(k)])),
    native_helpers:Object.fromEntries(['split_memberinfo_v1','init_xgbt','xclr_change_choose_492','xcxx_ggjt','add_citybyend_city'].map(k=>[k,String(window[k])])),
    requests:[...travel.requests,...used.requests,...requests],business_transport:'native_http',system_write:false,save_dispatched:false};
}

async function reimbursementDomesticTripBuild(c){
  const travel=await reimbursementBusinessApi({...c,action:'bill-read',domain:'travel',contracts:c.travel_contracts}),used=await reimbursementBusinessApi({...c,action:'bill-read',domain:'used_invoices',contracts:c.used_contracts});
  const bill=travel.bill;
  if(!travel.complete||!used.complete||bill['t.status']!=='-99'||bill['t.lc_finish']!=='F'||bill['t.bx_type']!=='2'||!bill['t.REQ_INFO']?.startsWith(c.expected_userid+'-')||bill['t.wf_name']||bill['t.uni_key']||bill['t.node_id']||ReimbursementHttpValues.moneyCents(bill['t.total_amt'])!==ReimbursementHttpValues.moneyCents(c.expected_amount)||JSON.stringify(used.sections[0].rows.map(r=>r['t.tk_guid']).sort())!==JSON.stringify(c.expected_guids))throw Error('DOMESTIC_TRIP_OWN_DRAFT');
  if(travel.sections[0].rows.some(r=>r['t.sno']===c.person.id)||!c.entries?.length||c.entries.length>20||c.entries.some(e=>e.person_id!==c.person.id||e.name!==c.person.name)||new Set(c.entries.map(e=>e.date)).size!==c.entries.length)throw Error('DOMESTIC_TRIP_NEW_SOURCE_PERSON_ONLY');
  const requests=[],http=createReimbursementHttp(c.expected_userid,{prefix:'DOMESTIC_TRIP',requests,timeout:15000,allowed:['definition','binding','button','procedure']});
  const native=await ReimbursementNativeForm.definition(http,{winno:'353',funcno:'492',save_winno:'354',save_caption:'保存',definition_sha256:c.definition_hashes['353']});
  const pf=native.value.funcs.find(f=>f.funcno==='493');
  const {data:context,set,get}=http.contextCopy();set('61-serialno',c.serial);set('61-xcrid','');set('490-MultiRows','');set('490-MultiCount','0');set('0-rid','');set('1545-userid','');
  for(const no of ['355','354']){
    const win=native.value.wins.find(w=>String(w.winno)===no),raw=await http.post('commonUpdate_responseButtonEvent.action',{cmd:ReimbursementNativeProtocol.command('',win.beforeLoadProcs,context,{}),projid:'WF_YBX',userContext:JSON.stringify(context),async:false},'text');
    const keys=[...raw.matchAll(/(?:local\(|;)([^():;]+):/g)].map(m=>m[1]);
    if(keys.some(k=>!/^61-[A-Za-z0-9_.]+$/i.test(k)&&k!=='0-is_refund_field_name'))throw Error('DOMESTIC_TRIP_INIT_CONTEXT');ReimbursementNativeProtocol.applyResponse(raw,context,keys);
  }
  async function bind(form){
    const [sql,refs]=form.bindSql.split('|'),cvstr=refs.split(',').map(r=>r+'::'+get(r.slice(1,-1))).join('$$');
    const rows=ReimbursementHttpValues.formRows(await http.post('common_bindSQLDataBackend.action',{sqlid:sql.slice(1),cvstr,projid:'WF_YBX'},'text'));
    if(rows.length!==1)throw Error('DOMESTIC_TRIP_BLANK_TEMPLATE');return rows[0];
  }
  const originalTrip=await bind(native.form),originalPerson=await bind(pf);
  if(originalPerson.SNO||originalPerson.SNAME||originalPerson.SKEY||originalTrip.TK_GUID||originalTrip.TOURSKEY!=='!P!')throw Error('DOMESTIC_TRIP_BLANK_TEMPLATE');
  for(const f of pf.formFields)set('493-'+f.fieldname,originalPerson[f.fieldname.slice(2).toUpperCase()]??'');
  set('493-t.state','校内');set('493-t.sno',c.person.id);
  const event=pf.formFields.find(f=>f.fieldname==='t.sno').Events.find(e=>e.event==='blur');
  if(event.proc!=='$7690c02cf8bc179476855dcb77ef6278|#493-t.state#,#493-t.srankpost#,#493-t.sno#,#493-t.sname#,#493-t.slevel#,#493-t.scardid#')throw Error('DOMESTIC_TRIP_PERSON_LOOKUP_CHANGED');
  const person_raw=await http.post('common_callProc.action',{procName:event.proc.split('|')[0],userData:JSON.stringify(context),projid:'WF_YBX'},'text');
  let parsed;try{parsed=ReimbursementNativeProtocol.response(person_raw,['61-travelmember_trigger_info','61-travelmember_info','61-is_xw','61-travelmember_flag']);}catch(e){throw Error(e.message+':'+person_raw);}
  if(!parsed.accepted)throw Error('DOMESTIC_TRIP_PERSON_LOOKUP_REJECTED');
  const info=JSON.parse(parsed.updates['61-TRAVELMEMBER_TRIGGER_INFO'].replaceAll('：',':'));
  const v=k=>info[k]?.value;
  if(v('SNO')!==c.person.id||v('SNAME')!==c.person.name||v('SSTATE')!=='校内'||v('SCOUNT')!=='1'||!v('STECHPOST')||!v('SRANKPOST')||!v('SCARDID'))throw Error('DOMESTIC_TRIP_PERSON_NOT_UNIQUE');
  const person={...originalPerson,SNO:v('SNO'),SNAME:v('SNAME'),STATE:v('SSTATE'),SLEVEL:v('STECHPOST'),SRANKPOST:v('SRANKPOST'),SCARDID:v('SCARDID'),SKEY:v('SNO')+'.'+v('SNAME')+'.'+v('SCARDID').slice(-1)};
  const trips=c.entries.map(e=>({...originalTrip,STARTCITY:e.departure_city.replace(/市$/,'')+'市',ENDCITY:e.arrival_city.replace(/市$/,'')+'市',STARTDATE:e.date,ENDDATE:e.date}));
  if(trips.some(t=>!/^\d{4}-\d{2}-\d{2}$/.test(t.STARTDATE)||t.STARTCITY===t.ENDCITY))throw Error('DOMESTIC_TRIP_ROUTE_SOURCE');
  for(const f of pf.formFields)set('493-'+f.fieldname,person[f.fieldname.slice(2).toUpperCase()]??'');
  for(const f of native.form.formFields)set('492-'+f.fieldname,trips.map(t=>t[f.fieldname.slice(2).toUpperCase()]??'').join('$|'));
  return{http,native,context,report:{ok:true,complete:true,action:'domestic-travel-add-prepare',case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,bill,before_allowances:travel.sections[0].rows,used_rows:used.sections[0].rows,originalTrip,originalPerson,person,trips,person_raw,definition_sha256:native.hash,
    business_transport:'native_http',system_write:false,save_dispatched:false,requests:[...travel.requests,...used.requests,...requests]}};
}
async function reimbursementDomesticTripPrepare(c){return(await reimbursementDomesticTripBuild(c)).report;}

async function reimbursementDomesticTripSave(c){
  if(!c.execute||!c.pending_registered||!c.prepared?.ok)throw Error('DOMESTIC_TRIP_PENDING_REQUIRED');
  const built=await reimbursementDomesticTripBuild(c),fresh=built.report;
  for(const k of ['bill','before_allowances','used_rows','person','trips','originalTrip','originalPerson','definition_sha256'])if(JSON.stringify(ReimbursementNativeForm.canonical(fresh[k]))!==JSON.stringify(ReimbursementNativeForm.canonical(c.prepared[k])))throw Error('DOMESTIC_TRIP_PREPARE_CHANGED');
  const registry='__ustcDomesticTravelAddsV1';if(!window[registry])Object.defineProperty(window,registry,{value:Object.create(null)});
  if(window[registry][c.token])throw Error('DOMESTIC_TRIP_ORIGINAL_ONLY');window[registry][c.token]={serial:c.serial,person_id:c.person.id,dispatched:true};
  const r={ok:false,case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,token:c.token,save_dispatched:true,system_write:true,business_transport:'native_http'};
  try{
    const types={...built.native.form.typeMap,...built.native.value.funcs.find(f=>f.funcno==='493').typeMap};
    r.native_response=await built.http.post('commonUpdate_responseButtonEvent.action',{cmd:ReimbursementNativeProtocol.command(built.native.save.check_func,built.native.save.procs,built.context,types),projid:'WF_YBX',userContext:JSON.stringify(built.context),async:false},'text');
    r.ok=/^(pass|success)(?=[:;]|local\(|$)/.test(r.native_response);r.state=r.ok?'saved_pending_readback':'save_response_unconfirmed';
  }catch(e){r.error=e.message;r.state='save_result_unknown';}
  return r;
}

async function reimbursementDomesticTripReadback(c){
  const listing=await reimbursementBusinessApi({...c,action:'bills-list'}),hits=listing.rows.filter(r=>r['t.serial_no']===c.serial);
  if(!listing.complete||listing.account_scope!=='current_applicant'||hits.length!==1)throw Error('DOMESTIC_TRIP_READBACK_OWN_BILL');
  const amount=hits[0]['t.total_amt'].replaceAll(',',''),actual={...c,expected_amount:amount.includes('.')?amount:amount+'.00'};
  const travel=await reimbursementBusinessApi({...actual,action:'bill-read',domain:'travel',contracts:c.travel_contracts}),used=await reimbursementBusinessApi({...actual,action:'bill-read',domain:'used_invoices',contracts:c.used_contracts});
  if(!travel.complete||!used.complete||travel.bill['t.status']!=='-99'||travel.bill['t.lc_finish']!=='F'||travel.bill['t.bx_type']!=='2'||travel.bill['t.wf_name']||travel.bill['t.uni_key']||travel.bill['t.node_id']||JSON.stringify(used.sections[0].rows.map(r=>r['t.tk_guid']).sort())!==JSON.stringify(c.expected_guids))throw Error('DOMESTIC_TRIP_READBACK_SCOPE');
  const rows=travel.sections[0].rows,added=rows.filter(r=>r['t.sno']===c.person.id),retained=rows.filter(r=>r['t.sno']!==c.person.id);
  const sort=a=>[...a].sort((a,b)=>JSON.stringify(a).localeCompare(JSON.stringify(b)));
  if(!added.length||added.some(r=>r['t.sname']!==c.person.name||r['t.serial_no']!==c.serial)||JSON.stringify(sort(retained))!==JSON.stringify(sort(c.prepared.before_allowances))||JSON.stringify(sort(used.sections[0].rows))!==JSON.stringify(sort(c.prepared.used_rows)))throw Error('DOMESTIC_TRIP_READBACK_PROTECTED_ROWS');
  for(const e of c.entries){const date=Date.parse(e.date);if(!added.some(r=>{const start=Date.parse(r['t.startdate'].slice(0,10));return date>=start&&date<start+Number(r['t.staydays'])*86400000;}))throw Error('DOMESTIC_TRIP_SOURCE_DAY_MISSING');}
  return{ok:true,complete:true,case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,person_id:c.person.id,bill:travel.bill,allowances:rows,used_rows:used.sections[0].rows,added,
    business_transport:'native_http',system_write:false,requests:[...listing.requests,...travel.requests,...used.requests]};
}

function reimbursementDomesticAllowanceAssignments(rows,entries){
  const assigned=rows.map(()=>[]);
  for(const e of entries){
    const date=Date.parse(e.date),hits=rows.map((r,i)=>({r,i})).filter(({r})=>r['t.sno']===e.person_id&&r['t.sname']===e.name&&date>=Date.parse(r['t.startdate'].slice(0,10))&&date<Date.parse(r['t.startdate'].slice(0,10))+Number(r['t.staydays'])*86400000);
    if(hits.length!==1)throw Error('DOMESTIC_ALLOWANCE_SOURCE_DAY_NOT_UNIQUE');assigned[hits[0].i].push(e);
  }
  return assigned;
}

async function reimbursementDomesticAllowanceEditBuild(c){
  const travel=await reimbursementBusinessApi({...c,action:'bill-read',domain:'travel',contracts:c.travel_contracts}),used=await reimbursementBusinessApi({...c,action:'bill-read',domain:'used_invoices',contracts:c.used_contracts});
  const bill=travel.bill,rows=travel.sections[0].rows;
  if(!travel.complete||!used.complete||bill['t.status']!=='-99'||bill['t.lc_finish']!=='F'||bill['t.bx_type']!=='2'||!bill['t.REQ_INFO']?.startsWith(c.expected_userid+'-')||bill['t.wf_name']||bill['t.uni_key']||bill['t.node_id']||JSON.stringify(used.sections[0].rows.map(r=>r['t.tk_guid']).sort())!==JSON.stringify(c.expected_guids))throw Error('DOMESTIC_ALLOWANCE_EDIT_OWN_DRAFT');
  if(!Number.isSafeInteger(c.row_index)||c.row_index<0||c.row_index>=rows.length)throw Error('DOMESTIC_ALLOWANCE_ROW_INDEX');
  const row=rows[c.row_index],entries=reimbursementDomesticAllowanceAssignments(rows,c.source_plan.entries)[c.row_index];
  const requests=[],http=createReimbursementHttp(c.expected_userid,{prefix:'DOMESTIC_ALLOWANCE_EDIT',requests,timeout:15000,allowed:['definition','binding','button']});
  const native=await ReimbursementNativeForm.definition(http,{winno:'128',funcno:'192',save_caption:'保存',definition_sha256:c.definition_hashes['128']});
  const {data:context,set,get}=http.contextCopy();
  set('61-serialno',c.serial);set('61-xgbt_src','报销单列表');set('61-sno',row['t.sno']);set('61-cityname',row['t.city']);set('61-startdate',row['t.startdate']);set('61-STAYDAYS_HS',row['t.STAYDAYS_HS']);set('61-STAYDAYS_JT',row['t.STAYDAYS_JT']);
  const init_raw=await http.post('commonUpdate_responseButtonEvent.action',{cmd:ReimbursementNativeProtocol.command('',native.win.beforeLoadProcs,context,{}),projid:'WF_YBX',userContext:JSON.stringify(context),async:false},'text');
  const keys=[...init_raw.matchAll(/(?:local\(|;)([^():;]+):/g)].map(m=>m[1]);if(keys.some(k=>!/^61-[A-Za-z0-9_.]+$/i.test(k)))throw Error('DOMESTIC_ALLOWANCE_EDIT_INIT_KEYS');ReimbursementNativeProtocol.applyResponse(init_raw,context,keys);
  if(get('61-cl_subsidy_canupdate')!=='Y'||get('61-bt_hs_change')!=='T'||get('61-bt_jt_change')!=='T'||get('61-bt_groupname_xgbt')!=='99')throw Error('DOMESTIC_ALLOWANCE_EDIT_NOT_PERMITTED');
  const fieldDefs=JSON.parse(get('0-FORM_DEFINITION').replaceAll('：',':')).YBX_GNCL_USER_BTDTL;
  if(!Array.isArray(fieldDefs)||fieldDefs.some(f=>['hsbt_amt','jtbt_amt','STAYDAYS_HS','STAYDAYS_JT'].includes(f.name)&&f.showFlag===false))throw Error('DOMESTIC_ALLOWANCE_EDIT_FIELD_HIDDEN');
  const [sql,refs]=native.form.bindSql.split('|'),cvstr=refs.split(',').map(r=>r+'::'+get(r.slice(1,-1))).join('$$');
  const bound=ReimbursementHttpValues.formRows(await http.post('common_bindSQLDataBackend.action',{sqlid:sql.slice(1),cvstr,projid:'WF_YBX'},'text'));
  if(bound.length!==1||bound[0].SNO!==row['t.sno']||bound[0].SNAME!==row['t.sname']||bound[0].CITY!==row['t.city']||bound[0].STARTDATE!==row['t.startdate']||bound[0].STAYDAYS!==row['t.staydays'])throw Error('DOMESTIC_ALLOWANCE_EDIT_BOUND_ROW');
  const original={...bound[0],STAYDAYS_HS:row['t.STAYDAYS_HS'],STAYDAYS_JT:row['t.STAYDAYS_JT']};
  const cents=ReimbursementHttpValues.moneyCents,fmt=n=>(n/100n).toString()+'.'+(n%100n).toString().padStart(2,'0');
  // 此原生绑定的STD是原行程总标准；九天行程返回900/720，而非每天100/80。
  const oldDays=BigInt(original.STAYDAYS_OLD);
  if(oldDays<=0n||cents(original.HSBT_STD)%oldDays||cents(original.JTBT_STD)%oldDays)throw Error('DOMESTIC_ALLOWANCE_EDIT_DAILY_STANDARD_NOT_EXACT');
  const hs=cents(original.HSBT_STD)/oldDays*BigInt(entries.length),jt=cents(original.JTBT_STD)/oldDays*BigInt(entries.length);
  if(entries.reduce((s,e)=>s+cents(e.amount),0n)!==hs+jt||cents(original.BG_HOTEL_AMT)!==0n||!['','0','0.00'].includes(original.CG_CQ_AMT))throw Error('DOMESTIC_ALLOWANCE_EDIT_SOURCE_RATE_OR_OTHER_COMPONENT:'+JSON.stringify({HSBT_STD:original.HSBT_STD,JTBT_STD:original.JTBT_STD,BG_HOTEL_AMT:original.BG_HOTEL_AMT,CG_CQ_AMT:original.CG_CQ_AMT,source_entries:entries.length,source_amount:entries.reduce((s,e)=>s+cents(e.amount),0n).toString(),native:original.TOTAL_AMT}));
  const target={...original,HSBT_AMT:fmt(hs),JTBT_AMT:fmt(jt),TOTAL_AMT:fmt(hs+jt),STAYDAYS_HS:String(entries.length),STAYDAYS_JT:String(entries.length)};
  for(const f of native.form.formFields){const key=f.fieldname.slice(2).toUpperCase();if(typeof target[key]!=='string')throw Error('DOMESTIC_ALLOWANCE_EDIT_MISSING_FIELD:'+key);set('192-'+f.fieldname,target[key]);}
  set('61-STAYDAYS_HS',target.STAYDAYS_HS);set('61-STAYDAYS_JT',target.STAYDAYS_JT);
  const expected_after=fmt(cents(c.expected_amount)-cents(original.TOTAL_AMT)+hs+jt);
  return{http,native,context,report:{ok:true,complete:true,action:'domestic-allowance-edit-prepare',case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,rows,used_rows:used.sections[0].rows,row_index:c.row_index,original,target,entries,expected_after,init_raw,definition_sha256:native.hash,business_transport:'native_http',system_write:false,save_dispatched:false,requests:[...travel.requests,...used.requests,...requests]}};
}
async function reimbursementDomesticAllowanceEditPrepare(c){return(await reimbursementDomesticAllowanceEditBuild(c)).report;}

async function reimbursementDomesticAllowanceEditSave(c){
  if(!c.execute||!c.pending_registered||!c.prepared?.ok)throw Error('DOMESTIC_ALLOWANCE_EDIT_PENDING_REQUIRED');
  const built=await reimbursementDomesticAllowanceEditBuild(c),fresh=built.report;
  for(const k of ['rows','used_rows','row_index','original','target','entries','expected_after','init_raw','definition_sha256'])if(JSON.stringify(ReimbursementNativeForm.canonical(fresh[k]))!==JSON.stringify(ReimbursementNativeForm.canonical(c.prepared[k])))throw Error('DOMESTIC_ALLOWANCE_EDIT_PREPARE_CHANGED');
  const registry='__ustcDomesticAllowanceEditsV1';if(!window[registry])Object.defineProperty(window,registry,{value:Object.create(null)});
  if(window[registry][c.token])throw Error('DOMESTIC_ALLOWANCE_EDIT_ORIGINAL_ONLY');window[registry][c.token]={serial:c.serial,row_index:c.row_index,dispatched:true};
  const r={ok:false,case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,token:c.token,save_dispatched:true,system_write:true,business_transport:'native_http'};
  try{
    r.native_response=await built.http.post('commonUpdate_responseButtonEvent.action',{cmd:ReimbursementNativeProtocol.command(built.native.save.check_func,built.native.save.procs,built.context,built.native.form.typeMap),projid:'WF_YBX',userContext:JSON.stringify(built.context),async:false},'text');
    r.ok=/^(pass|success)(?=[:;]|local\(|$)/.test(r.native_response);r.state=r.ok?'saved_pending_readback':'save_response_unconfirmed';
  }catch(e){r.error=e.message;r.state='save_result_unknown';}
  return r;
}

async function reimbursementDomesticAllowanceEditReadback(c){
  const actual={...c,expected_amount:c.prepared.expected_after};
  const travel=await reimbursementBusinessApi({...actual,action:'bill-read',domain:'travel',contracts:c.travel_contracts}),used=await reimbursementBusinessApi({...actual,action:'bill-read',domain:'used_invoices',contracts:c.used_contracts});
  const rows=travel.sections[0].rows,target=c.prepared.target,before=c.prepared.rows[c.row_index];
  const key=r=>JSON.stringify([r['t.sno'],r['t.city'],r['t.startdate']]),hits=rows.filter(r=>key(r)===key(before));
  const cents=ReimbursementHttpValues.moneyCents,sort=a=>[...a].sort((a,b)=>key(a).localeCompare(key(b)));
  if(!travel.complete||!used.complete||travel.bill['t.status']!=='-99'||travel.bill['t.lc_finish']!=='F'||travel.bill['t.bx_type']!=='2'||travel.bill['t.wf_name']||travel.bill['t.uni_key']||travel.bill['t.node_id']||rows.length!==c.prepared.rows.length||hits.length!==1||JSON.stringify(sort(rows.filter(r=>key(r)!==key(before))))!==JSON.stringify(sort(c.prepared.rows.filter(r=>key(r)!==key(before))))||JSON.stringify(used.sections[0].rows)!==JSON.stringify(c.prepared.used_rows))throw Error('DOMESTIC_ALLOWANCE_EDIT_READBACK_PROTECTED');
  const row=hits[0];for(const [a,b]of [['t.hsbt_amt','HSBT_AMT'],['t.jtbt_amt','JTBT_AMT'],['t.total_amt','TOTAL_AMT']])if(cents(row[a])!==cents(target[b]))throw Error('DOMESTIC_ALLOWANCE_EDIT_READBACK_AMOUNT');
  for(const [a,b]of [['t.STAYDAYS_HS','STAYDAYS_HS'],['t.STAYDAYS_JT','STAYDAYS_JT']])if(Number(row[a])!==Number(target[b]))throw Error('DOMESTIC_ALLOWANCE_EDIT_READBACK_DAYS');
  const protectedRow={...before,'t.hsbt_amt':row['t.hsbt_amt'],'t.jtbt_amt':row['t.jtbt_amt'],'t.total_amt':row['t.total_amt'],'t.STAYDAYS_HS':row['t.STAYDAYS_HS'],'t.STAYDAYS_JT':row['t.STAYDAYS_JT']};
  // 国内补贴首次保存由学校将空币种/汇率补成此固定默认，并将空长期补贴补成零。
  // 只接受这个完整组合；其他币种、比例或非零补贴仍属于未授权变化。
  const native_normalizations={};
  if(before['t.cg_wh_bz']===''&&before['t.cg_wh_rate']===''&&row['t.cg_wh_bz']==='人民币'&&row['t.cg_wh_rate']==='100')for(const k of ['t.cg_wh_bz','t.cg_wh_rate']){protectedRow[k]=row[k];native_normalizations[k]=row[k];}
  if(before['t.cg_cq_amt']===''&&row['t.cg_cq_amt']==='0'){protectedRow['t.cg_cq_amt']='0';native_normalizations['t.cg_cq_amt']='0';}
  if(JSON.stringify(ReimbursementNativeForm.canonical(row))!==JSON.stringify(ReimbursementNativeForm.canonical(protectedRow)))throw Error('DOMESTIC_ALLOWANCE_EDIT_READBACK_ROW_DRIFT:'+JSON.stringify(Object.fromEntries(Object.keys(row).filter(k=>row[k]!==protectedRow[k]).map(k=>[k,{before:protectedRow[k],after:row[k]}]))));
  return{ok:true,complete:true,case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,row_index:c.row_index,bill:travel.bill,allowances:rows,used_rows:used.sections[0].rows,native_normalizations,business_transport:'native_http',system_write:false,requests:[...travel.requests,...used.requests]};
}

async function reimbursementDomesticAllowanceFinalReadback(c){
  const travel=await reimbursementBusinessApi({...c,action:'bill-read',domain:'travel',contracts:c.travel_contracts}),used=await reimbursementBusinessApi({...c,action:'bill-read',domain:'used_invoices',contracts:c.used_contracts});
  const bill=travel.bill,rows=travel.sections[0].rows,invoices=used.sections[0].rows,cents=ReimbursementHttpValues.moneyCents;
  if(!travel.complete||!used.complete||bill['t.status']!=='-99'||bill['t.lc_finish']!=='F'||bill['t.bx_type']!=='2'||!bill['t.REQ_INFO']?.startsWith(c.expected_userid+'-')||bill['t.wf_name']||bill['t.uni_key']||bill['t.node_id']||JSON.stringify(invoices.map(r=>r['t.tk_guid']).sort())!==JSON.stringify(c.expected_guids))throw Error('DOMESTIC_ALLOWANCE_FINAL_OWN_DRAFT');
  const assignments=reimbursementDomesticAllowanceAssignments(rows,c.source_plan.entries),people=new Map(c.source_plan.people.map(p=>[p.id,p.name]));
  let food=0n,traffic=0n;
  for(let i=0;i<rows.length;i++){
    const row=rows[i],entries=assignments[i],hs=cents(row['t.hsbt_amt']),jt=cents(row['t.jtbt_amt']);
    if(people.get(row['t.sno'])!==row['t.sname']||row['t.serial_no']!==c.serial||!['','0','0.00'].includes(row['t.cg_cq_amt'])||cents(row['t.bg_hotel_amt'])!==0n||Number(row['t.STAYDAYS_HS'])!==entries.length||Number(row['t.STAYDAYS_JT'])!==entries.length||hs+jt!==cents(row['t.total_amt'])||entries.reduce((s,e)=>s+cents(e.amount),0n)!==hs+jt)throw Error('DOMESTIC_ALLOWANCE_FINAL_PERSON_DATES_COMPONENTS');
    food+=hs;traffic+=jt;
  }
  const invoice_amount=invoices.reduce((s,r)=>s+cents(r['t.tk_total_amt']),0n);
  if(invoice_amount!==cents(c.invoice_amount)||food+traffic!==cents(c.source_plan.amount)||invoice_amount+food+traffic!==cents(c.expected_amount))throw Error('DOMESTIC_ALLOWANCE_FINAL_TOTAL');
  return{ok:true,complete:true,case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,bill,allowances:rows,used_rows:invoices,invoice_amount:c.invoice_amount,allowance_amount:c.source_plan.amount,
    components:{'国内出差伙食补贴':(food/100n).toString()+'.'+(food%100n).toString().padStart(2,'0'),'国内出差交通补贴':(traffic/100n).toString()+'.'+(traffic%100n).toString().padStart(2,'0')},people_count:people.size,source_person_dates:c.source_plan.entries.length,
    business_transport:'native_http',system_write:false,requests:[...travel.requests,...used.requests]};
}
