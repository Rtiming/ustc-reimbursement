/* Counts-mode travel allowance. Fixed W867 definition and source-backed groups. */
async function reimbursementAllowanceBuild(c){
  const prepared=c.prepared,requests=[],http=createReimbursementHttp(c.expected_userid,{prefix:'ALLOWANCE_CREATE_API',requests,timeout:30000,allowed:['definition','binding','button']});
  if(!prepared?.complete||prepared.case_id!==c.case_id||prepared.expected_userid!==c.expected_userid||prepared.policy['0-WPCL_SHOW_MEMBERS']!=='N'||prepared.policy['0-WPCLTSGZ']!=='N'||!prepared.groups?.length||prepared.groups.length>99)throw Error('ALLOWANCE_CREATE_SOURCE');
  const native=await ReimbursementNativeForm.definition(http,{winno:'867',funcno:'1076',save_caption:'下一步',definition_sha256:c.contracts.definitions['867']});
  const {data:context,set,get}=http.contextCopy();
  if(get('0-WPCL_SHOW_MEMBERS')!=='N'||get('0-WPCLTSGZ')!=='N')throw Error('ALLOWANCE_CREATE_POLICY_CHANGED');
  set('61-serialno','');set('61-BX_JBXX_BX_TYPE','2');set('61-max_ord','');set('0-g0Serialno','');
  for(const field of native.form.formFields){
    const name=field.fieldname.slice(2),values=prepared.groups.map(g=>g.values[name]);
    if(values.some(v=>typeof v!=='string'||/[\r\n]|\$\|/.test(v)))throw Error('ALLOWANCE_CREATE_FIELD:'+name);
    set('1076-'+field.fieldname,values.join('$|'));
  }
  if(prepared.groups.some(g=>g.values.members!==''||g.values.ylry!=='0'||g.values.elry!=='0'||g.values.cllx!=='1'||g.values.jtfy!=='0'||g.values.is_wcsy!=='N'||g.values.sdate!==g.values.edate||g.values.btts!=='1')||prepared.groups.reduce((s,g)=>s+ReimbursementHttpValues.moneyCents(g.values.feeamount),0n)!==ReimbursementHttpValues.moneyCents(c.expected_amount))throw Error('ALLOWANCE_CREATE_SCOPE');
  return{http,native,context,requests};
}

async function reimbursementAllowanceCreatePrepare(c){
  const b=await reimbursementAllowanceBuild(c),before=await reimbursementBusinessApi({...c,action:'bills-list'});
  if(!before.complete||before.account_scope!=='current_applicant')throw Error('ALLOWANCE_CREATE_BEFORE_INCOMPLETE');
  const cmd=ReimbursementNativeProtocol.command(b.native.save.check_func,[],b.context,b.native.form.typeMap);
  const raw=await b.http.post('commonUpdate_responseButtonEvent.action',{cmd,projid:'WF_YBX',userContext:JSON.stringify(b.context),async:false},'text');
  const accepted=/^(pass|success)(?=[:;]|local\(|$)/.test(raw);
  return{ok:accepted,complete:true,action:'allowance-create-prepare',case_id:c.case_id,expected_userid:c.expected_userid,
    definition_sha256:b.native.hash,guard_response:raw,before_serials:before.rows.map(r=>r['t.serial_no']),groups:c.prepared.groups,
    business_transport:'native_http',system_write:false,save_dispatched:false,requests:[...before.requests,...b.requests]};
}

async function reimbursementAllowanceCreate(c){
  if(!c.execute||!c.pending_registered||!c.creation_prepare?.ok||!/^[a-f0-9]{64}$/.test(c.token))throw Error('ALLOWANCE_CREATE_PENDING_REQUIRED');
  const key='__ustcNativeAllowanceCreatesV1';let registry=Object.getOwnPropertyDescriptor(window,key)?.value;
  if(!registry){registry=Object.create(null);Object.defineProperty(window,key,{value:registry});}
  if(registry[c.token])throw Error('ALLOWANCE_CREATE_ORIGINAL_ONLY');
  const b=await reimbursementAllowanceBuild(c);
  if(JSON.stringify(c.creation_prepare.groups)!==JSON.stringify(c.prepared.groups)||b.native.hash!==c.creation_prepare.definition_sha256)throw Error('ALLOWANCE_CREATE_PREPARE_CHANGED');
  const marker={case_id:c.case_id,userid:c.expected_userid,token:c.token,save_dispatched:true,phase:'save_requested'};registry[c.token]=marker;
  const result={ok:false,action:'allowance-create',case_id:c.case_id,expected_userid:c.expected_userid,token:c.token,
    save_dispatched:true,business_transport:'native_http',system_write:true,requests:b.requests,marker};
  try{
    const cmd=ReimbursementNativeProtocol.command(b.native.save.check_func,b.native.save.procs,b.context,b.native.form.typeMap);
    result.native_response=await b.http.post('commonUpdate_responseButtonEvent.action',{cmd,projid:'WF_YBX',userContext:JSON.stringify(b.context),async:false},'text');
    const keys=[...result.native_response.matchAll(/(?:local\(|;)([^():;]+):/g)].map(m=>m[1]);
    // Native creation also returns preview flags. Decode them as inert data;
    // never apply them to UC or run the returned view/workflow instructions.
    if(keys.some(k=>!(/^(?:0|61)-[A-Za-z0-9_.]+$/i.test(k)||k==='pgWrite_item24-cj_Display')||/^0-userinfo\./i.test(k)))throw Error('ALLOWANCE_CREATE_RESPONSE_KEYS');
    const parsed=ReimbursementNativeProtocol.response(result.native_response,keys),serial=parsed.updates['0-G0SERIALNO'];
    if(!parsed.accepted||!/^\d{1,20}$/.test(serial||'')||c.creation_prepare.before_serials.includes(serial))throw Error('ALLOWANCE_CREATE_RESPONSE_UNCONFIRMED');
    Object.assign(result,{ok:true,serial,state:'created_pending_readback',server_accepted:true});Object.assign(marker,{serial,phase:'save_accepted'});
  }catch(error){result.error=error.message;result.state='create_result_unknown';}
  return result;
}

async function reimbursementAllowanceReadback(c){
  if(!/^\d{1,20}$/.test(c.serial||'')||c.creation_prepare.before_serials.includes(c.serial))throw Error('ALLOWANCE_READBACK_SERIAL');
  const bill=await reimbursementBusinessApi({...c,action:'bill-read',domain:'basic',contracts:c.basic_contracts});
  const requests=[],http=createReimbursementHttp(c.expected_userid,{prefix:'ALLOWANCE_READBACK_API',requests,timeout:15000,allowed:['definition','binding']});
  await ReimbursementNativeForm.definition(http,{winno:'867',funcno:'1076',save_caption:'下一步',definition_sha256:c.contracts.definitions['867']});
  const raw=await http.post('common_bindSQLDataBackend.action',{sqlid:'703262101ed0f451aaffbc24d0df8153',cvstr:'#61-serialno#::'+c.serial+'$$#0-userinfo.userid#::'+c.expected_userid+'$$#61-BX_JBXX_BX_TYPE#::2',projid:'WF_YBX'},'text');
  // The binding uses semantic aliases, which differ from the editable field names.
  const aliases={ylry:'YLRY',elry:'ELRY',slry:'SLRY',cllx:'CLLX',cfxc_s:'CFXC_S',cfxc_e:'CFXC_E',clbz:'CLBZ',sdate:'SDATE',edate:'EDATE',hsbt:'HSBT',jtbt:'JTBT',jtfy:'JTFY',feeamount:'FEEAMOUNT',is_wcsy:'IS_WCSY',jtgj:'JTGJ'};
  const rows=reimbursementExpandFormRows({multiRec:true,formFields:Object.values(aliases).map(fieldname=>({fieldname:'t.'+fieldname}))},ReimbursementHttpValues.formRows(raw));
  if(!bill.complete||rows.length!==c.prepared.groups.length||bill.bill['t.status']!=='-99'||bill.bill['t.lc_finish']!=='F')throw Error('ALLOWANCE_READBACK_DRAFT_OR_COUNT');
  const expected=c.prepared.groups.map(g=>g.values),remaining=[...rows];
  for(const values of expected){
    const index=remaining.findIndex(row=>Object.entries(aliases).every(([k,a])=>['hsbt','jtbt','jtfy','feeamount'].includes(k)?ReimbursementHttpValues.moneyCents(row[a])===ReimbursementHttpValues.moneyCents(values[k]):String(row[a]??'').trim()===values[k]));
    if(index<0)throw Error('ALLOWANCE_READBACK_GROUP_MISMATCH:'+JSON.stringify(rows));remaining.splice(index,1);
  }
  return{ok:true,complete:true,action:'allowance-readback',case_id:c.case_id,expected_userid:c.expected_userid,serial:c.serial,
    expected_amount:c.expected_amount,bill:bill.bill,rows,verified_groups:expected.length,member_mode:'counts',claim_type:'2',
    business_transport:'native_http',system_write:false,page_context_modified:false,requests:[...bill.requests,...requests]};
}
