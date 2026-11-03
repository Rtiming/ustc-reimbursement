/* 固定验收人查询；只用W43中已核教师字段的lookup过程，不依赖历史单号。 */
async function reimbursementStaffLookup(c){
  const requests=[],http=createReimbursementHttp(c.expected_userid,{prefix:'STAFF_API',requests,timeout:15000,allowed:['definition','procedure']});
  const q=c.staff_query;
  if(typeof q!=='string'||!/^[A-Za-z0-9]{2,40}$/.test(q)||!/[0-9]/.test(q)||/^(PB|SA|SB|BA)[0-9]/i.test(q))throw Error('STAFF_API_QUERY');
  const definition=await http.post('loadDefinition.action?type=W',{winno:'43',projid:'WF_YBX'});
  const canonical=ReimbursementNativeForm.canonical;
  const projected=Object.fromEntries(['wins','funcs','funcMap','actMap'].map(k=>[k,definition[k]]));
  const hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(canonical(projected)))))].map(n=>n.toString(16).padStart(2,'0')).join('');
  if(hash!==c.basic_contract.definition_sha256)throw Error('STAFF_API_DEFINITION_CHANGED');
  const form=definition.funcs.filter(f=>String(f.funcno)==='60');
  const fields=form.length===1?form[0].formFields.filter(f=>f.id==='t-lc_accepter'):[];
  if(fields.length!==1||fields[0].Events?.length!==1||typeof fields[0].Events[0].proc!=='string')throw Error('STAFF_API_LOOKUP_FIELD');
  const proc=fields[0].Events[0].proc.split('|')[0];
  const {data:context,set,get}=http.contextCopy();
  for(const k of Object.keys(context))if(/^WF_YBX\$(60|61)-/.test(k))delete context[k];
  set('60-t.lc_accepter',q);set('60-lc_value','');set('60-lc_field','');
  const raw=await http.post('common_callProc.action',{procName:proc,userData:JSON.stringify(context),projid:'WF_YBX'},'text');
  ReimbursementNativeProtocol.applyResponse(raw,context,['60-lc_value','60-lc_field']);
  if(get('60-lc_field')!=='lc_accepter_name')throw Error('STAFF_API_TARGET');
  const value=get('60-lc_value');
  if(typeof value!=='string'||value.length>8000)throw Error('STAFF_API_VALUE_LIMIT');
  const name=value.trim();
  if(/[\/\r\n,]/.test(name))throw Error('STAFF_API_NAME_FORMAT');
  const person=name?{id:q,name}:null,candidates=[];
  return{ok:true,complete:true,action:'staff-lookup',case_id:c.case_id,expected_userid:c.expected_userid,
    staff_query:q,person,candidates,found:!!person||candidates.length>0,raw_value:value,definition_sha256:hash,
    business_transport:'native_http',system_write:false,page_context_modified:false,requests};
}
