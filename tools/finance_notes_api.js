/* 总体说明只修改定稿正文；其他事项、附件、签字人与隐藏原字段全部保留。 */
async function reimbursementNotesBuild(c) {
  const requests = [], http = createReimbursementHttp(c.expected_userid, {prefix:'NOTES_API', requests,
    timeout:15000, allowed:['definition','button']});
  const notes = c.explanations;
  if (!notes || typeof notes.general !== 'string' || !Array.isArray(notes.card_notes || []) ||
      (notes.card_notes || []).some(row => typeof row.text !== 'string')) throw Error('NOTES_API_PARAMETERS');
  const text = [notes.general, ...(notes.card_notes || []).map(row => row.text)].join('\n\n');
  if (!text.trim() && notes.record_only !== true) throw Error('NOTES_API_EMPTY_TEXT');
  const contract = c.notes_contract;
  if (contract.xtype !== '33&33' || contract.winno !== '54' || contract.funcno !== '76') throw Error('NOTES_API_SCOPE');
  const read = await reimbursementBusinessApi({...c, action:'bill-read', domain:'notes'});
  const rows = read.sections.find(s => s.funcno === '76')?.rows;
  if (rows?.length !== 1) throw Error('NOTES_API_BINDING_COUNT');
  if (rows[0].SPEC_TYPE!=='33' || rows[0].SPEC_GUID!=='33') throw Error('NOTES_API_BOUND_SCOPE');
  const native = await ReimbursementNativeForm.definition(http, contract);
  const {data:context,set,get} = http.contextCopy();
  set('61-serialno',c.serial);set('61-XTYPE','33&33');
  for (const key of contract.initialization_keys) set(key,'');
  const command = ReimbursementNativeProtocol.command('',native.win.beforeLoadProcs,context,native.form.typeMap);
  const raw = await http.post('commonUpdate_responseButtonEvent.action', {cmd:command,projid:'WF_YBX',userContext:JSON.stringify(context),async:false},'text');
  const keys = [...raw.matchAll(/(?:local\(|;)([^():;]+):/g)].map(m=>m[1]);
  const allowed = new Set(contract.initialization_keys.map(k=>k.toUpperCase()));
  if (keys.some(k=>!allowed.has(k.toUpperCase()))) throw Error('NOTES_API_INITIALIZATION_CONTEXT:'+JSON.stringify(keys));
  ReimbursementNativeProtocol.applyResponse(raw,context,keys);
  // 学校先执行窗口初始化，再用绑定值填表；不让初始化默认值覆盖原说明或附件。
  const original = ReimbursementNativeForm.bind(native.form,rows[0],set);
  const changes = notes.record_only === true || original['t.spec_response'] === text ? {} : {'t.spec_response':text};
  for (const [key,value] of Object.entries(changes)) set('76-'+key,value);
  const visible = ReimbursementNativeForm.visible(native.form,get('61-spec_des'));
  const optionMode=get('61-SPEC_OPTIONS');
  if (['Y','A','X'].includes(optionMode)) visible.delete('t.spec_response');
  if (optionMode!=='Y') visible.delete('t.spec_response_select');
  if (optionMode!=='X') visible.delete('t.spec_response_xmselect');
  if (!get('61-spec_des').split(',').includes('0')) visible.delete('t.SPEC_FILE');
  ReimbursementNativeForm.validate(native.form,visible,get,Object.keys(changes));
  if (get('61-write_isnull')==='Y' && !get('76-t.spec_response').trim() ||
      get('61-upload_isnull')==='Y' && !get('76-t.SPEC_FILE').trim() ||
      get('61-option_isnotnull')==='Y' && !get('76-t.spec_response_select').trim() ||
      get('61-option_isnotnull')==='X' && !get('76-t.spec_response_xmselect').trim()) throw Error('NOTES_API_NATIVE_REQUIRED');
  const saveCommand = ReimbursementNativeProtocol.command(native.save.check_func,native.save.procs,context,native.form.typeMap);
  const report = {ok:true,complete:true,action:'notes-prepare',case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,
    business_transport:'native_http',system_write:false,save_dispatched:false,page_context_modified:false,bill:read.bill,
    definition_sha256:native.hash,original,changes,visible_fields:[...visible],scope:{xtype:'33&33',groups:get('61-spec_des'),option_mode:optionMode,record_only:notes.record_only===true},
    requests:[...read.requests,...requests],observed_at:new Date().toISOString()};
  return {report,http,requests,context,saveCommand};
}
async function reimbursementNotesPrepare(c) {return (await reimbursementNotesBuild(c)).report;}
async function reimbursementNotesSave(c) {
  let dispatched=false,prepared=null;
  try {
    if (c.execute!==true || !c.expected_plan) throw Error('NOTES_API_WRITE_INTENT');
    const built=await reimbursementNotesBuild(c);prepared=built.report;
    if (prepared.bill['t.status']!=='-99' || prepared.bill['t.lc_finish']!=='F') throw Error('NOTES_API_DRAFT_REQUIRED');
    for (const key of ['original','changes','scope','definition_sha256'])
      if (JSON.stringify(prepared[key])!==JSON.stringify(c.expected_plan[key])) throw Error('NOTES_API_PLAN_DRIFT:'+key);
    if (!Object.keys(prepared.changes).length) return {...prepared,action:'notes-save',state:'already_matches_readback_required',requires_readback:true};
    built.http.guard();dispatched=true;
    const raw=await built.http.post('commonUpdate_responseButtonEvent.action',{cmd:built.saveCommand,projid:'WF_YBX',userContext:JSON.stringify(built.context),async:false},'text');
    if (!ReimbursementNativeProtocol.response(raw,[]).accepted) throw Error('NOTES_API_SAVE_REJECTED');
    return {...prepared,action:'notes-save',state:'saved_independent_readback_required',system_write:true,save_dispatched:true,requires_readback:true};
  } catch (error) {
    return {ok:false,complete:false,action:'notes-save',case_id:c.case_id,serial:c.serial,expected_userid:c.expected_userid,
      business_transport:'native_http',system_write:dispatched,save_dispatched:dispatched,prepared,requires_readback:true,
      state:dispatched?'unknown_save_readback_required':'rejected_before_save',error:String(error.message)};
  }
}
