/* 单段行程补录：原始票池GUID、固定行程绑定及全部未改字段分别保护。 */
async function reimbursementTravelAirport(c,http){
  const contract=c.travel_contract,requests=http.requests;
  const d=await http.post('loadDefinition.action?type=W',{winno:'325',projid:'WF_YBX'});
  const hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(ReimbursementNativeForm.canonical(Object.fromEntries(['wins','funcs','funcMap','actMap'].map(k=>[k,d[k]])))))))].map(n=>n.toString(16).padStart(2,'0')).join('');
  const q=d.funcs.find(f=>String(f.funcno)==='444'),cols=q?.colModel?.map(f=>f.name);
  if(hash!==contract.airport_definition_sha256||q?.tablenames!==contract.airport_query.tablenames||q.needcount!==true||JSON.stringify(cols)!==JSON.stringify(contract.airport_columns))throw Error('TRAVEL_API_AIRPORT_DEFINITION_CHANGED');
  const {data:context,set}=http.contextCopy();
  for(const key of Object.keys(context))if(key.startsWith('WF_YBX$444-'))delete context[key];
  set('444-COND','');set('444-CONDOBJ',[]);set('444-CURRPAGE',1);
  const rows=[];let count=null,pages=1;
  for(let page=1;page<=pages;page++){
    const r=await http.post('commonQuery_doQuery.action?funcno=444&projid=WF_YBX&needcount=true',{_search:false,nd:Date.now(),page,rows:'100',sidx:'',sord:'',userData:JSON.stringify(context),itemArr:'',sectionArr:''});
    const n=Number(r?.records),total=Number(r?.total);
    if(!Number.isSafeInteger(n)||n<0||n>5000||!Number.isSafeInteger(total)||total!==Math.ceil(n/100)||!(Number(r.page)===page||!n&&Number(r.page)===0)||!Array.isArray(r.rows)||r.rows.length!==Math.min(100,Math.max(0,n-(page-1)*100))||count!==null&&count!==n)throw Error('TRAVEL_API_AIRPORT_INCOMPLETE');
    count=n;pages=total;
    for(const row of r.rows){if(!Array.isArray(row.cell)||row.cell.length!==cols.length)throw Error('TRAVEL_API_AIRPORT_ROW');rows.push(Object.fromEntries(cols.map((k,i)=>[k,ReimbursementHttpValues.decode(row.cell[i])])));}
  }
  const change=c.edit.changes,city=change.arrival_city.replace(/市$/,'');
  const hits=rows.filter(r=>r['t.cityname'].replace(/市$/,'')===city&&r['t.airportname']===change.arrival_airport);
  if(hits.length!==1||!hits[0]['t.citycode'])throw Error('TRAVEL_API_AIRPORT_NOT_UNIQUE');
  return hits[0];
}

async function reimbursementTravelBuild(c){
  const requests=[],http=createReimbursementHttp(c.expected_userid,{prefix:'TRAVEL_API',requests,timeout:15000,allowed:['definition','binding','query','button']});
  const req=c.edit,{moneyCents,formRows}=ReimbursementHttpValues;
  if(req.schema!=='ustc-invoice-travel-edit-v1'||typeof req.changes.traveler!=='string'||!req.changes.traveler.trim())throw Error('TRAVEL_API_SOURCE_REQUIRED');
  const pool=await reimbursementBusinessApi({...c,action:'invoices-list',contracts:c.pool_contracts}),rows=pool.sections?.flatMap(s=>s.rows||[])||pool.rows;
  if(pool.complete!==true||!Array.isArray(rows))throw Error('TRAVEL_API_POOL_INCOMPLETE');
  const hits=rows.filter(r=>r['t.tk_guid']===req.native_guid);
  if(hits.length!==1)throw Error('TRAVEL_API_UNIQUE_GUID');
  const row=hits[0],numbers=[...new Set(String(row['t.tk_code_abst']||row['t.tk_no']||'').match(/(?<!\d)\d{20}(?!\d)/g)||[])];
  if(numbers.length!==1||numbers[0]!==req.invoice_number||row['t.tk_no']&&row['t.tk_no']!==req.invoice_number||row['t.tk_type']!=='INVOICE'||
    row['t.tk_mod_flag']!==req.native_status||row['t.canedit']!=='Y'||row['t.serial_no']||!['','N','0'].includes(row['t.PC_Has_chosen']||'')||
    !row['t.auth_rst_desc']?.startsWith('正常(已认证)')||moneyCents(row['t.tk_total_amt'])!==moneyCents(req.amount)||!/^[a-f0-9]{32}$/i.test(row['t.tk_mainguid']||''))throw Error('TRAVEL_API_PROTECTED_INVOICE');
  const native=await ReimbursementNativeForm.definition(http,c.travel_contract);
  if(native.form.bindSql!==c.travel_contract.bindSql)throw Error('TRAVEL_API_BINDING_CHANGED');
  const {data:context,set,get}=http.contextCopy();set('61-tks_guid',req.native_guid);set('61-tk_mainguid',row['t.tk_mainguid']);set('395-t.inv_type','INVOICE');
  const [sql,refs]=native.form.bindSql.split('|'),cvstr=refs.split(',').map(ref=>ref+'::'+get(ref.slice(1,-1))).join('$$');
  const data=formRows(await http.post('common_bindSQLDataBackend.action',{sqlid:sql.slice(1),cvstr,projid:'WF_YBX'},'text'));
  if(data.length!==1)throw Error('TRAVEL_API_ONE_JOURNEY_REQUIRED');
  const original=ReimbursementNativeForm.bind(native.form,data[0],set),target={...original},change=req.changes;
  if(!['',change.traveler].includes(original['t.passenger_sname']))throw Error('TRAVEL_API_EXISTING_OTHER_TRAVELER');
  const map={traveler:'passenger_sname',person_id:'passenger_sno',departure_date:'tour_startdate',arrival_date:'tour_enddate',seat_class:'tour_seatclass'};
  for(const [key,name]of Object.entries(map))if(Object.hasOwn(change,key)){
    if(/[\x00-\x1f\x7f]/.test(change[key])||change[key].includes('$|'))throw Error('TRAVEL_API_FIELD_SEPARATOR');
    // 舱位目录动态下发；此次仅保留已在本票出现的原生值，不能用显示文字猜代码。
    if(key==='seat_class'&&change[key]!==original['t.tour_seatclass'])throw Error('TRAVEL_API_SEAT_CATALOG_REQUIRED');
    target['t.'+name]=change[key];
  }
  let airport=null;
  if(change.arrival_airport){
    airport=await reimbursementTravelAirport(c,http);target['t.tour_endcity_tx']=airport['t.cityname'];target['t.tour_endcity']=airport['t.cityname']+'-'+airport['t.airportname'];
  }else if(change.arrival_city&&original['t.tour_endcity_tx'].replace(/市$/,'')!==change.arrival_city.replace(/市$/,''))throw Error('TRAVEL_API_AIRPORT_SOURCE_REQUIRED');
  for(const name of ['tour_startdate','tour_enddate']){
    const value=target['t.'+name];
    if(!/^\d{4}-\d{2}-\d{2}$/.test(value)||!Number.isFinite(Date.parse(value))||new Date(value).toISOString().slice(0,10)!==value)throw Error('TRAVEL_API_DATE');
  }
  if(target['t.tour_enddate']<target['t.tour_startdate'])throw Error('TRAVEL_API_DATE_ORDER');
  for(const name of ['tour_startcity','tour_endcity','tour_startdate','tour_enddate','passenger_sname','passenger_sno','tour_seatclass'])if(!target['t.'+name]?.trim())throw Error('TRAVEL_API_REQUIRED:'+name);
  const changes={};for(const [name,value]of Object.entries(target)){set('1150-'+name,value);if(value!==original[name])changes[name]=value;}
  return {http,native,context,report:{ok:true,complete:true,action:'travel-prepare',case_id:c.case_id,expected_userid:c.expected_userid,native_guid:req.native_guid,
    business_transport:'native_http',original,target,changes,airport,pool_row:row,definition_sha256:native.hash,system_write:false,save_dispatched:false,requests:[...pool.requests,...requests]}};
}
async function reimbursementTravelPrepare(c){return(await reimbursementTravelBuild(c)).report;}
async function reimbursementTravelSave(c){
  let dispatched=false,prepared=null;
  try{
    if(c.execute!==true||!c.pending_registered||!c.expected_plan)throw Error('TRAVEL_API_PENDING_REQUIRED');
    const built=await reimbursementTravelBuild(c);prepared=built.report;
    for(const key of ['original','target','changes','airport','pool_row','definition_sha256'])if(JSON.stringify(ReimbursementNativeForm.canonical(prepared[key]))!==JSON.stringify(ReimbursementNativeForm.canonical(c.expected_plan[key])))throw Error('TRAVEL_API_PLAN_DRIFT');
    if(Object.keys(prepared.changes).length){
      const save=built.native.save,cmd=ReimbursementNativeProtocol.command(save.check_func,save.procs,built.context,built.native.form.typeMap);
      dispatched=true;const raw=await built.http.post('commonUpdate_responseButtonEvent.action',{cmd,projid:'WF_YBX',userContext:JSON.stringify(built.context),async:false},'text');
      if(!ReimbursementNativeProtocol.response(raw,[]).accepted)throw Error('TRAVEL_API_SAVE_REJECTED');
    }
    return {...prepared,action:'travel-save',save_dispatched:dispatched,system_write:dispatched,state:dispatched?'saved_independent_readback_required':'already_matches_readback_required',requires_readback:true};
  }catch(error){return{ok:false,action:'travel-save',case_id:c.case_id,expected_userid:c.expected_userid,native_guid:c.edit.native_guid,business_transport:'native_http',
    save_dispatched:dispatched,system_write:dispatched,state:dispatched?'unknown_save_readback_required':'rejected_before_save',prepared,error:String(error.message),requires_readback:true};}
}
