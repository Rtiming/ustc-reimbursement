/* 已认证、未绑定医疗票只补本人确认的自费类型；原生票面和每项支付金额完整保护。 */
async function reimbursementMedicalBuild(c){
  const requests=[],http=createReimbursementHttp(c.expected_userid,{prefix:'MEDICAL_API',requests,timeout:15000,allowed:['definition','binding','button']});
  const req=c.edit,{moneyCents,formRows}=ReimbursementHttpValues;
  if(req.schema!=='ustc-invoice-medical-edit-v1'||JSON.stringify(req.changes)!==JSON.stringify({insurance_type:'自费'}))throw Error('MEDICAL_API_CONFIRMED_SELF_PAY_ONLY');
  const pool=await reimbursementBusinessApi({...c,action:'invoices-list',contracts:c.pool_contracts});
  const rows=pool.sections?.flatMap(s=>s.rows||[])||pool.rows;
  if(pool.complete!==true||!Array.isArray(rows))throw Error('MEDICAL_API_POOL_INCOMPLETE');
  const matches=rows.filter(r=>r['t.tk_guid']===req.native_guid);
  if(matches.length!==1)throw Error('MEDICAL_API_UNIQUE_GUID');
  const row=matches[0];
  if(row['t.tk_type']!=='MEDICAL'||row['t.tk_code']!==req.invoice_code||row['t.tk_code_abst']!=='医/'+req.invoice_code+'/'+req.invoice_number||
    row['t.tk_mod_flag']!==req.native_status||row['t.canedit']!=='Y'||row['t.serial_no']||!['','N','0'].includes(row['t.PC_Has_chosen']||'')||
    !row['t.auth_rst_desc']?.startsWith('正常(已认证)')||moneyCents(row['t.tk_total_amt'])!==moneyCents(req.amount)||!/^[a-f0-9]{32}$/i.test(row['t.tk_mainguid']||''))throw Error('MEDICAL_API_PROTECTED_OR_DIFFERENT_INVOICE');
  const native=await ReimbursementNativeForm.definition(http,c.medical_contract);
  if(native.form.bindSql!==c.medical_contract.bindSql)throw Error('MEDICAL_API_BINDING_CHANGED');
  const {data:context,set,get}=http.contextCopy();
  set('61-tks_guid',req.native_guid);set('61-tk_mainguid',row['t.tk_mainguid']);set('61-picture_seq','');set('395-T.INV_TYPE','MEDICAL');set('61-is_sg','N');
  if(!['','N','F'].includes(get('0-is_skip')||''))throw Error('MEDICAL_API_SKIP_VALIDATION_FORBIDDEN');set('0-is_skip',get('0-is_skip')||'');
  const [sql,refs]=native.form.bindSql.split('|');
  const cvstr=refs.split(',').map(ref=>ref+'::'+get(ref.slice(1,-1))).join('$$');
  const data=formRows(await http.post('common_bindSQLDataBackend.action',{sqlid:sql.slice(1),cvstr,projid:'WF_YBX'},'text'));
  if(data.length!==1)throw Error('MEDICAL_API_FORM_NOT_UNIQUE');
  const original=ReimbursementNativeForm.bind(native.form,data[0],set);
  if(original['t.medical_invcode']!==req.invoice_code||original['t.medical_invno']!==req.invoice_number||moneyCents(original['t.medical_invamount'])!==moneyCents(req.amount)||
     moneyCents(original['t.medical_paycash'])!==moneyCents(req.amount)||['overall','other','account','grcash'].some(n=>moneyCents(original['t.medical_pay'+n]||'0')!==0n)||
     !['','自费'].includes(original['t.medical_paytype']))throw Error('MEDICAL_API_PAYMENT_FACTS_CHANGED');
  const target={...original,'t.medical_paytype':'自费'},changes=original['t.medical_paytype']==='自费'?{}:{'t.medical_paytype':'自费'};
  for(const [key,value]of Object.entries(target))set('1360-'+key,value);
  return {http,native,context,report:{ok:true,complete:true,action:'medical-prepare',case_id:c.case_id,expected_userid:c.expected_userid,native_guid:req.native_guid,
    business_transport:'native_http',original,target,changes,pool_row:row,definition_sha256:native.hash,system_write:false,save_dispatched:false,requests:[...pool.requests,...requests]}};
}
async function reimbursementMedicalPrepare(c){return(await reimbursementMedicalBuild(c)).report;}
async function reimbursementMedicalSave(c){
  let dispatched=false,prepared=null;
  try{
    if(c.execute!==true||!c.pending_registered||!c.expected_plan)throw Error('MEDICAL_API_PENDING_REQUIRED');
    const built=await reimbursementMedicalBuild(c);prepared=built.report;
    for(const key of ['original','target','changes','pool_row','definition_sha256'])if(JSON.stringify(ReimbursementNativeForm.canonical(prepared[key]))!==JSON.stringify(ReimbursementNativeForm.canonical(c.expected_plan[key])))throw Error('MEDICAL_API_PLAN_DRIFT');
    if(Object.keys(prepared.changes).length){
      const save=built.native.save,cmd=ReimbursementNativeProtocol.command(save.check_func,save.procs,built.context,built.native.form.typeMap);
      dispatched=true;
      const raw=await built.http.post('commonUpdate_responseButtonEvent.action',{cmd,projid:'WF_YBX',userContext:JSON.stringify(built.context),async:false},'text');
      const result=ReimbursementNativeProtocol.response(raw,['61-tksbcodecnt']);if(!result.accepted)throw Error('MEDICAL_API_NATIVE_SAVE_REJECTED');
    }
    return {...prepared,action:'medical-save',save_dispatched:dispatched,system_write:dispatched,state:dispatched?'saved_independent_readback_required':'already_matches_readback_required',requires_readback:true};
  }catch(error){return{ok:false,action:'medical-save',case_id:c.case_id,expected_userid:c.expected_userid,native_guid:c.edit.native_guid,business_transport:'native_http',
    save_dispatched:dispatched,system_write:dispatched,state:dispatched?'unknown_save_readback_required':'rejected_before_save',prepared,error:String(error.message),requires_readback:true};}
}
