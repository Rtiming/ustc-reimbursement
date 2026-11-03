/* Closed native diagnostics for manual receipts and travel allowances. */
async function reimbursementSupplementInspect(c) {
  const requests=[],http=createReimbursementHttp(c.expected_userid,{prefix:'SUPPLEMENT_API',requests,timeout:15000,
    allowed:['definition','gridDefinition','binding','button','procedure','query','formTemplate','printDocument']});
  const hash=async value=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value)))].map(n=>n.toString(16).padStart(2,'0')).join('');
  const definitions=[];
  for(const no of c.contracts[c.kind].windows){
    const d=await http.post('loadDefinition.action?type=W',{winno:no,projid:'WF_YBX'});
    const sha=await hash(JSON.stringify(ReimbursementNativeForm.canonical(Object.fromEntries(['wins','funcs','funcMap','actMap'].map(k=>[k,d[k]])))));
    if(sha!==c.contracts.definitions[no])throw Error('SUPPLEMENT_API_DEFINITION_CHANGED:'+no);
    definitions.push({winno:no,definition:d,sha256:sha});
  }
  const {data:context,set,get}=http.contextCopy();
  const report={ok:true,complete:true,action:c.kind+'-inspect',case_id:c.case_id,expected_userid:c.expected_userid,
    serial:c.serial,business_transport:'native_http',system_write:false,save_dispatched:false,definitions,requests};
  if(c.kind==='nonvat'){
    const d=definitions.find(x=>x.winno==='61').definition,form=d.funcs.find(f=>String(f.funcno)==='86');
    if(form.formhtml!=='@FFFF/sglufp_86.html')throw Error('SUPPLEMENT_API_MANUAL_TEMPLATE');
    report.manual_template=await http.post('common_getCustomFormFileContent.action',{filename:'FFFF/sglufp_86.html'},'text');
    report.manual_template_sha256=await hash(report.manual_template);
    report.types=ReimbursementHttpValues.formRows(await http.post('common_bindSQLDataBackend.action',{sqlid:'af9dbd21084129048c30825811dfe916',cvstr:'',projid:'WF_YBX'},'text'));
    const entry=definitions.find(x=>x.winno==='33').definition.wins.find(w=>String(w.winno)==='39').func_btns.filter(b=>b.caption==='批量票录入');
    if(entry.length!==1||entry[0].procs.length)throw Error('SUPPLEMENT_API_ENTRY');
    const cmd=ReimbursementNativeProtocol.command(entry[0].check_func,[],context);
    report.entry_guard_raw=await http.post('commonUpdate_responseButtonEvent.action',{cmd,projid:'WF_YBX',userContext:JSON.stringify(context),async:false},'text');
    const keys=[...report.entry_guard_raw.matchAll(/(?:local\(|;)([^():;]+):/g)].map(m=>m[1]);
    if(keys.some(k=>!/^0-(?:tk_type|tk_mod|tk_guid|MULTITKITEM[A-Za-z0-9_]*)$/i.test(k)))throw Error('SUPPLEMENT_API_ENTRY_CONTEXT:'+JSON.stringify(keys));
    ReimbursementNativeProtocol.applyResponse(report.entry_guard_raw,context,keys);
    const batch=definitions.find(x=>x.winno==='551').definition.wins.find(w=>String(w.winno)==='551');
    report.batch_init_raw=await http.post('commonUpdate_responseButtonEvent.action',{cmd:ReimbursementNativeProtocol.command('',batch.beforeLoadProcs,context),projid:'WF_YBX',userContext:JSON.stringify(context),async:false},'text');
    const initKeys=[...report.batch_init_raw.matchAll(/(?:local\(|;)([^():;]+):/g)].map(m=>m[1]);
    if(initKeys.some(k=>!/^0-(?:tk_type|tk_mod|tk_guid|MULTITKITEM[A-Za-z0-9_]*)$/i.test(k)))throw Error('SUPPLEMENT_API_INIT_CONTEXT:'+JSON.stringify(initKeys));
    ReimbursementNativeProtocol.applyResponse(report.batch_init_raw,context,initKeys);
    report.batch_defaults=Object.fromEntries(['tk_type','tk_mod','tk_guid'].map(k=>[k,get('0-'+k)]));
    report.batch_catalog=ReimbursementHttpValues.formRows(await http.post('common_bindSQLDataBackend.action',{sqlid:'1b04a53e315a2cd15ce3c7887f92f3b7',cvstr:'#0-tk_type#::'+get('0-tk_type'),projid:'WF_YBX'},'text'));
  }else{
    report.bound_to_bill=Boolean(c.serial);
    if(c.serial){
      report.bill=await reimbursementBusinessApi({...c,action:'bill-read',domain:'basic',contracts:c.basic_contracts});
      set('61-serialno',c.serial);
      report.preview_html=await http.post('ybxPrint_yb4PrintHtml.action',{printModel:'P',printType:'报销确认',serialno:c.serial,byear:'',dbserver:'',bxId:'',appName:'YBX',bxOrderid:'',sign:'',jsonData:encodeURI('YB!P!NEW!P!'+c.serial+'!P!报销确认!P!'+c.expected_userid+'!P!undefined')},'text');
    }else set('61-serialno','');
    report.travel_types=[];
    for(const type of ['1','2','4'])report.travel_types.push({bill_type:type,rows:ReimbursementHttpValues.formRows(await http.post('common_bindSQLDataBackend.action',{sqlid:'46b449e9e184a1a1408fac7b557eb519',cvstr:'#61-BX_JBXX_BX_TYPE#::'+type,projid:'WF_YBX'},'text'))});
    if(c.allowance_source){
      report.policy=Object.fromEntries(['0-WPCL_SHOW_MEMBERS','0-WPCLTSGZ','0-IS_CHANGE_WPCL_BT','61-BT_GROUPNAME','61-MAX_ORD'].map(k=>[k,get(k)??'']));
      report.native_helpers={handleUC:String(window.handleUC),strToJson:String(window.strToJson)};
      const win=definitions.find(x=>x.winno==='867').definition.wins.find(w=>String(w.winno)==='867');
      report.allowance_init_raw=await http.post('commonUpdate_responseButtonEvent.action',{cmd:ReimbursementNativeProtocol.command('',win.beforeLoadProcs,context),projid:'WF_YBX',userContext:JSON.stringify(context),async:false},'text');
      report.person_states=ReimbursementHttpValues.formRows(await http.post('common_bindSQLDataBackend.action',{sqlid:'e629f1d4f5f9a014f9af22cd3b9ed584',cvstr:'',projid:'WF_YBX'},'text'));
      const personForm=definitions.find(x=>x.winno==='1525').definition.funcs.find(f=>String(f.funcno)==='1739'),field=personForm.formFields.find(f=>f.fieldname==='t.sno');
      if(!field.Events[0].proc.startsWith('$45de0f7b76501427658d320750e49e16|'))throw Error('ALLOWANCE_API_PERSON_LOOKUP_CHANGED');
      report.native_people=[];
      for(const p of c.allowance_source.people){
        for(const f of personForm.formFields)set('1739-'+f.fieldname,'');set('1739-t.sno',p.id);
        const raw=await http.post('common_callProc.action',{procName:field.Events[0].proc.split('|')[0],userData:JSON.stringify(context),projid:'WF_YBX'},'text');
        report.native_people.push({id:p.id,name:p.name,native_response:raw});
      }
      const grid=definitions.find(x=>x.winno==='352').definition.funcs.find(f=>String(f.funcno)==='488'),cols=grid.colModel.map(x=>x.name);
      if(cols.join(',')!=='t.select_key,t.select_value'||grid.needcount!==true)throw Error('ALLOWANCE_API_CITY_COLUMNS_CHANGED');
      set('488-COND','');set('488-CONDOBJ',[]);set('488-CURRPAGE',1);
      const cities=[];let count=null,pages=1;
      for(let page=1;page<=pages;page++){
        const r=await http.post('commonQuery_doQuery.action?funcno=488&projid=WF_YBX&needcount=true',{_search:false,nd:Date.now(),page,rows:'100',sidx:'',sord:'',userData:JSON.stringify(context),itemArr:'',sectionArr:''});
        const n=Number(r.records),total=Number(r.total);
        if(!Number.isSafeInteger(n)||n<0||n>5000||total!==Math.ceil(n/100)||!Array.isArray(r.rows)||r.rows.length!==Math.min(100,Math.max(0,n-(page-1)*100))||count!==null&&count!==n)throw Error('ALLOWANCE_API_CITY_INCOMPLETE');
        count=n;pages=total;
        for(const row of r.rows){if(row.cell.length!==cols.length)throw Error('ALLOWANCE_API_CITY_ROW');cities.push(Object.fromEntries(cols.map((k,i)=>[k,ReimbursementHttpValues.decode(row.cell[i])])));}
      }
      report.city_catalog_records=count;report.cities=cities.filter(x=>/北京|上海|合肥/.test(x['t.select_value']));
    }
  }
  return report;
}

async function reimbursementNonvatPrepare(c){
  if(c.entry.evidence_kind!=='other_receipt'&&c.entry.evidence_kind!=='fiscal_receipt'||!c.entry.amount_basis_confirmed||
    !/^\d+\.\d{2}$/.test(c.entry.amount)||!Array.isArray(c.entry.source_files)||!c.entry.source_files.length)throw Error('NONVAT_API_SOURCE');
  const inspected=await reimbursementSupplementInspect(c);
  if(!inspected.batch_catalog.some(x=>x.SELECT_KEY==='其他发票'))throw Error('NONVAT_API_RECEIPT_CATALOG');
  const raw=inspected.batch_defaults.tk_guid,guid=raw.trim();
  if(!/^[A-Fa-f0-9]{32}$/.test(guid)||inspected.batch_defaults.tk_type!=='MULTITK'||inspected.batch_defaults.tk_mod!=='MULTITKITEM')throw Error('NONVAT_API_NATIVE_TYPE');
  const pool=await reimbursementBusinessApi({...c,action:'invoices-list',contracts:c.pool_contracts});
  if(!pool.complete||pool.rows.some(r=>r['t.tk_guid']===guid))throw Error('NONVAT_API_GUID_ALREADY_EXISTS');
  return {...inspected,action:'nonvat-prepare',entry:c.entry,guid,defaults:inspected.batch_defaults,
    original_guids:pool.rows.map(r=>r['t.tk_guid']),requests:[...inspected.requests,...pool.requests]};
}

async function reimbursementNonvatWrite(c){
  const key='__ustcNativeNonvatEntriesV1',hash=async bytes=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(n=>n.toString(16).padStart(2,'0')).join('');
  const requests=[],http=createReimbursementHttp(c.expected_userid,{prefix:'NONVAT_API',requests,timeout:30000,allowed:['definition','button']});
  if(!c.execute||!c.pending_registered||!/^[a-f0-9]{64}$/.test(c.token))throw Error('NONVAT_API_PENDING_REQUIRED');
  let registry=Object.getOwnPropertyDescriptor(window,key)?.value;
  if(!registry){registry=Object.create(null);Object.defineProperty(window,key,{value:registry});}
  function report(s){return{ok:true,action:'nonvat-save',case_id:c.case_id,expected_userid:c.expected_userid,entry:c.entry,guid:s.guid,
    phase:s.phase,files:s.files.map(f=>({meta:f.meta,filecode:f.filecode||null,received:f.received,phase:f.phase})),save_dispatched:s.save_dispatched,
    business_transport:'native_http',system_write:s.files.some(f=>f.phase.startsWith('upload'))||s.save_dispatched,native_response:s.native_response||null,requests:s.requests};}
  if(c.step==='start'){
    if(registry[c.token]||c.prepared.case_id!==c.case_id||JSON.stringify(c.prepared.entry)!==JSON.stringify(c.entry))throw Error('NONVAT_API_ORIGINAL_INTENT');
    const s={guid:c.prepared.guid,defaults:c.prepared.defaults,userid:c.expected_userid,case_id:c.case_id,entry:c.entry,phase:'staging',save_dispatched:false,
      files:c.entry.source_files.map(meta=>({meta,bytes:new Uint8Array(meta.size),received:0,phase:'staging'})),requests:[]};
    registry[c.token]=s;return report(s);
  }
  const s=registry[c.token];
  if(!s||s.userid!==c.expected_userid||s.case_id!==c.case_id||JSON.stringify(s.entry)!==JSON.stringify(c.entry))throw Error('NONVAT_API_ORIGINAL_SESSION');
  if(c.step==='save'){
    if(s.save_dispatched||s.files.some(f=>f.phase!=='upload_received'))throw Error('NONVAT_API_SAVE_ONCE');
    const native=await ReimbursementNativeForm.definition(http,{winno:'551',funcno:'752',save_caption:'保存',definition_sha256:c.contracts.definitions['551']});
    const field=native.form.formFields.find(f=>f.fieldname==='t.mattachfile');
    if(field.needTransPic!=='true'||field.includeMD5Code!=='true'||field.includebase64code!=='false')throw Error('NONVAT_API_ATTACHMENT_DEFINITION');
    const {data:context,set}=http.contextCopy();
    for(const f of native.form.formFields)set('752-'+f.fieldname,'');
    for(const [k,v]of Object.entries(s.defaults))set('0-'+k,v);
    set('752-t.mxtype','其他发票');set('752-t.mtkitemprice',c.entry.amount);set('752-t.mtkitemnum','1');
    set('752-t.mattachfile',s.files.map(f=>f.filecode+'&'+f.meta.name+'&.'+f.meta.name.split('.').at(-1)+'&WF_YBX').join('|'));
    const cmd=ReimbursementNativeProtocol.command(native.save.check_func,native.save.procs,context,native.form.typeMap);
    s.save_dispatched=true;s.phase='save_requested';
    s.native_response=await http.post('commonUpdate_responseButtonEvent.action',{cmd,projid:'WF_YBX',userContext:JSON.stringify(context),async:false},'text');
    s.requests.push(...requests);
    const status=/^(pass|success)(?=[:;]|local\(|$)/.exec(s.native_response)?.[1];
    s.phase=status?'save_accepted':'save_response_unaccepted';
    return{...report(s),ok:Boolean(status),requires_independent_readback:true};
  }
  if(!Number.isSafeInteger(c.index)||c.index<0||c.index>=s.files.length)throw Error('NONVAT_API_FILE_INDEX');
  const f=s.files[c.index];
  if(c.step==='append'){
    if(f.phase!=='staging'||c.offset!==f.received||typeof c.base64!=='string'||c.base64.length>174764)throw Error('NONVAT_API_CHUNK_SEQUENCE');
    const raw=Uint8Array.from(atob(c.base64),x=>x.charCodeAt(0));
    if(!raw.length||raw.length>131072||f.received+raw.length>f.meta.size||await hash(raw)!==c.chunk_sha256)throw Error('NONVAT_API_CHUNK_SHA');
    f.bytes.set(raw,f.received);f.received+=raw.length;
    if(f.received===f.meta.size){if(await hash(f.bytes)!==f.meta.sha256)throw Error('NONVAT_API_SOURCE_SHA');f.phase='staged';}
    return report(s);
  }
  if(c.step!=='upload'||f.phase!=='staged')throw Error('NONVAT_API_UPLOAD_ONCE');
  const data=new FormData();data.append('doc',new File([f.bytes],f.meta.name));f.phase='upload_requested';
  const response=await fetch('fileSystem_saveAction.action?projid=WF_YBX&needBase64=false&needMd5=true&needTransPic=true',
    {method:'POST',credentials:'same-origin',redirect:'error',body:data,signal:AbortSignal.timeout(30000)});
  if(!response.ok)throw Error('NONVAT_API_UPLOAD_HTTP:'+response.status);
  f.filecode=await response.text();http.guard();
  if(!/^[A-Fa-f0-9]{32}$/.test(f.filecode))throw Error('NONVAT_API_FILECODE');
  f.phase='upload_received';delete f.bytes;return report(s);
}

async function reimbursementNonvatReadback(c){
  const saved=c.original_save;
  if(saved.case_id!==c.case_id||saved.expected_userid!==c.expected_userid||saved.save_dispatched!==true||!Array.isArray(saved.files)||saved.files.length!==c.entry.source_files.length)throw Error('NONVAT_API_SAVE_BINDING');
  const pool=await reimbursementBusinessApi({...c,action:'invoices-list',contracts:c.pool_contracts}),rows=pool.rows.filter(r=>r['t.tk_guid']===saved.guid);
  if(!pool.complete||rows.length!==1||rows[0]['t.serial_no']||rows[0]['t.tk_type']!=='MULTITK'||ReimbursementHttpValues.moneyCents(rows[0]['t.tk_total_amt'])!==ReimbursementHttpValues.moneyCents(c.entry.amount))throw Error('NONVAT_API_POOL_READBACK:'+JSON.stringify(rows));
  const source_readbacks=[];
  for(let i=0;i<saved.files.length;i++){
    const f=saved.files[i],source=c.entry.source_files[i];
    if(JSON.stringify(f.meta)!==JSON.stringify(source)||!/^[A-Fa-f0-9]{32}$/.test(f.filecode))throw Error('NONVAT_API_SOURCE_BINDING');
    const u=new URL('fileSystem_getAction.action',location.href);u.search=new URLSearchParams({seq:f.filecode,minFile:'0',type:'.'+source.name.split('.').at(-1),projid:'WF_YBX'});
    const response=await fetch(u.href,{credentials:'same-origin',redirect:'error',signal:AbortSignal.timeout(15000)}),bytes=new Uint8Array(await response.arrayBuffer());
    const sha=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(n=>n.toString(16).padStart(2,'0')).join('');
    source_readbacks.push({filecode:f.filecode,sha256:sha,verified:response.ok&&sha===source.sha256&&bytes.length===source.size});
  }
  return{ok:true,complete:true,action:'nonvat-readback',case_id:c.case_id,expected_userid:c.expected_userid,entry:c.entry,guid:saved.guid,
    native_row:rows[0],source_readbacks,business_transport:'native_http',system_write:false,requests:pool.requests};
}

async function reimbursementNonvatSelection(c){
  const pool=await reimbursementBusinessApi({...c,action:'invoices-list',contracts:c.pool_contracts});
  if(!pool.complete||!Array.isArray(c.entries)||!c.entries.length)throw Error('NONVAT_API_SELECTION_INCOMPLETE');
  const selected_rows=c.entries.map(entry=>{
    const found=pool.rows.filter(row=>row['t.tk_guid']===entry.guid);
    if(found.length!==1)throw Error('NONVAT_API_SELECTION_GUID');
    const row=found[0];
    if(row['t.serial_no']||!['','N','0'].includes(row['t.PC_Has_chosen'])||row['t.tk_type']!=='MULTITK'||row['t.tk_code_abst']!=='批/其他发票'||
       row['t.tk_mod_flag']!=='NORMAL'||row['t.auth_rst_desc']!=='正常'||ReimbursementHttpValues.moneyCents(row['t.tk_total_amt'])!==ReimbursementHttpValues.moneyCents(entry.amount))throw Error('NONVAT_API_SELECTION_PROTECTED');
    return row;
  });
  if(c.entries.reduce((s,e)=>s+ReimbursementHttpValues.moneyCents(e.amount),0n)!==ReimbursementHttpValues.moneyCents(c.expected_amount))throw Error('NONVAT_API_SELECTION_AMOUNT');
  return{ok:true,complete:true,action:'nonvat-selection',case_id:c.case_id,expected_userid:c.expected_userid,selected_rows,
    business_transport:'native_http',system_write:false,selection_source:'source_receipt_and_native_guid',requests:pool.requests};
}

async function reimbursementAllowancePrepare(c){
  const inspected=c.inspected,source=c.allowance_source,requests=[],http=createReimbursementHttp(c.expected_userid,{prefix:'ALLOWANCE_API',requests,timeout:15000,allowed:['definition','binding','procedure']});
  if(inspected.case_id!==c.case_id||inspected.expected_userid!==c.expected_userid||!inspected.complete||inspected.native_people.length!==source.people.length)throw Error('ALLOWANCE_API_SOURCE_INSPECTION');
  const people=source.people.map(p=>{
    const hits=inspected.native_people.filter(x=>x.id===p.id&&x.name===p.name);
    if(hits.length!==1)throw Error('ALLOWANCE_API_PERSON_SOURCE');
    const response=ReimbursementNativeProtocol.response(hits[0].native_response,['61-travelmember_trigger_info','61-travelmember_info']);
    const data=JSON.parse(response.updates['61-TRAVELMEMBER_TRIGGER_INFO'].replaceAll('：',':').replaceAll('；',';').replaceAll('左括号wingsoft','(').replaceAll('右括号wingsoft',')').replace(/[\n\r\t]/g,''));
    if(!response.accepted||data.SCOUNT.value!=='1'||data.SNO.value!==p.id||data.SNAME.value!==p.name||data.SSTATE.value!=='校内'||data.STECHPOST.value!=='三类'||data.SRANKPOST.value!=='三类')throw Error('ALLOWANCE_API_PERSON_NOT_UNIQUE_OR_CATEGORY');
    return{id:p.id,name:p.name,state:data.SSTATE.value,level:data.STECHPOST.value,rank:data.SRANKPOST.value};
  });
  const {data:context,set,get}=http.contextCopy();set('61-serialno',c.serial||'');set('61-BX_JBXX_BX_TYPE','2');
  const native=await http.post('loadDefinition.action?type=W',{winno:'867',projid:'WF_YBX'}),form=native.funcs.find(f=>String(f.funcno)==='1076');
  if(form.bindSql!=='$703262101ed0f451aaffbc24d0df8153|#61-serialno#,#0-userinfo.userid#,#61-BX_JBXX_BX_TYPE#')throw Error('ALLOWANCE_API_BINDING_CHANGED');
  const defaults=ReimbursementHttpValues.formRows(await http.post('common_bindSQLDataBackend.action',{sqlid:'703262101ed0f451aaffbc24d0df8153',cvstr:'#61-serialno#::'+(c.serial||'')+'$$#0-userinfo.userid#::'+c.expected_userid+'$$#61-BX_JBXX_BX_TYPE#::2',projid:'WF_YBX'},'text'));
  const cities={};for(const name of new Set(source.entries.flatMap(e=>[e.departure_city,e.arrival_city]))){
    const hits=inspected.cities.filter(x=>x['t.select_key']===name+'市');if(hits.length!==1)throw Error('ALLOWANCE_API_CITY_NOT_UNIQUE');cities[name]=hits[0]['t.select_key'];
  }
  const field=form.formFields.find(f=>f.fieldname==='t.btts');if(!field.Events[0].proc.startsWith('$9c15ec6eef0f12e76fd2d53d4c1f5b48|'))throw Error('ALLOWANCE_API_CALCULATION_CHANGED');
  const comparisons=[];
  for(const type of inspected.travel_types.find(x=>x.bill_type==='2').rows){
    const e=source.entries[0];
    const values={members:'',ylry:'0',elry:'0',slry:'1',cllx:type.SELECT_KEY,jtgj:'其他',cfxc_s:cities[e.departure_city],cfxc_e:cities[e.arrival_city],clbz:'',is_wcsy:'N',sdate:e.date,edate:e.date,jtfy:'0',hsbt:'0',jtbt:'0',feeamount:'0',btts:'1'};
    for(const[k,v]of Object.entries(values))set('1076-t.'+k,v);set('61-hs_amt','0');set('61-jt_amt','0');
    const raw=await http.post('common_callProc.action',{procName:field.Events[0].proc.split('|')[0],userData:JSON.stringify(context),projid:'WF_YBX'},'text');
    comparisons.push({type,native_response:raw});
  }
  // Counts mode is a server configuration, never an excuse to fabricate member IDs.
  if(inspected.policy['0-WPCL_SHOW_MEMBERS']!=='N'||inspected.policy['0-WPCLTSGZ']!=='N')throw Error('ALLOWANCE_API_MEMBER_MODE_UNSUPPORTED');
  const types=inspected.travel_types.find(x=>x.bill_type==='2').rows;
  if(!types.some(t=>t.SELECT_KEY==='1'&&t.SELECT_VALUE==='不包干'))throw Error('ALLOWANCE_API_SEPARATE_TICKET_TYPE');
  const grouped=new Map(),personDays=new Set();
  for(const e of source.entries){
    if(!people.some(p=>p.id===e.person_id&&p.name===e.name)||!/^\d{4}-\d{2}-\d{2}$/.test(e.date)||personDays.has(e.person_id+':'+e.date))throw Error('ALLOWANCE_API_PERSON_DAY');
    personDays.add(e.person_id+':'+e.date);
    const k=JSON.stringify([e.date,e.departure_city,e.arrival_city]);
    if(!grouped.has(k))grouped.set(k,[]);grouped.get(k).push(e);
  }
  const groups=[];
  for(const entries of grouped.values()){
    const e=entries[0],values={members:'',ylry:'0',elry:'0',slry:String(entries.length),cllx:'1',jtgj:'其他',cfxc_s:cities[e.departure_city],cfxc_e:cities[e.arrival_city],clbz:'',is_wcsy:'N',sdate:e.date,edate:e.date,jtfy:'0',hsbt:'0',jtbt:'0',feeamount:'0',btts:'1'};
    for(const[k,v]of Object.entries(values))set('1076-t.'+k,v);set('61-hs_amt','0');set('61-jt_amt','0');
    const destination=form.formFields.find(f=>f.fieldname==='t.cfxc_e').Events[0].proc;
    if(!destination.startsWith('$0d1308ca1a9075ba17882caa79df9835|'))throw Error('ALLOWANCE_API_CITY_CALCULATION_CHANGED');
    const city_raw=await http.post('common_callProc.action',{procName:destination.split('|')[0],userData:JSON.stringify(context),projid:'WF_YBX'},'text');
    const raw=await http.post('common_callProc.action',{procName:field.Events[0].proc.split('|')[0],userData:JSON.stringify(context),projid:'WF_YBX'},'text');
    const parsed=ReimbursementNativeProtocol.response(raw,['61-hs_amt','61-jt_amt']);
    if(!parsed.accepted)throw Error('ALLOWANCE_API_CALCULATION_REJECTED');
    const hs=parsed.updates['61-HS_AMT'],jt=parsed.updates['61-JT_AMT'];
    const cents=entries.reduce((s,e)=>s+ReimbursementHttpValues.moneyCents(e.amount),0n);
    if(ReimbursementHttpValues.moneyCents(hs)+ReimbursementHttpValues.moneyCents(jt)!==cents)throw Error('ALLOWANCE_API_SOURCE_AMOUNT_EXCEEDS_NATIVE');
    Object.assign(values,{hsbt:hs,jtbt:jt,feeamount:(Number(cents)/100).toFixed(2)});
    groups.push({entries,values,city_native_response:city_raw,native_response:raw});
  }
  if(groups.length>99||groups.reduce((s,g)=>s+ReimbursementHttpValues.moneyCents(g.values.feeamount),0n)!==ReimbursementHttpValues.moneyCents(source.amount))throw Error('ALLOWANCE_API_TOTAL');
  return{ok:true,complete:true,action:'allowance-prepare',case_id:c.case_id,expected_userid:c.expected_userid,people,cities,defaults,comparisons,
    policy:inspected.policy,groups,source_amount:source.amount,business_transport:'native_http',system_write:false,save_dispatched:false,requests};
}
