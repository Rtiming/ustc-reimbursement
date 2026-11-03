/* 基本信息只由本案参数及固定原生定义生成；预检和保存共用字段规则。 */
async function reimbursementBasicBuild(c) {
  'use strict';
  const started = performance.now(), requests = [];
  const http = createReimbursementHttp(c.expected_userid, {prefix: 'BASIC_API', requests,
    timeout: 15000, allowed: ['definition', 'button', 'procedure']});
  const basic = c.basic, protocol = ReimbursementNativeProtocol;
  const permitted = ['t-abst','t-req_mobile','t-req_tele','t-is_have_changefee','t-td_point','t-cl_abst'];
  if (!basic || Object.keys(basic.fields || {}).some(k => !permitted.includes(k)) ||
      !/^[A-Za-z0-9]{2,40}$/.test(basic.teacher_id) || /^(PB|SA|SB|BA)/i.test(basic.teacher_id) ||
      typeof basic.teacher_name !== 'string' || !basic.teacher_name.trim() || /[\r\n/,]/.test(basic.teacher_name) ||
      Object.values(basic.fields).some(v => typeof v !== 'string')) throw Error('BASIC_API_PARAMETERS');
  const read = await reimbursementBusinessApi({...c, action: 'bill-read', domain: 'basic'});
  const rows = read.sections.find(s => s.funcno === '60')?.rows;
  if (!Array.isArray(rows) || rows.length !== 1) throw Error('BASIC_API_BINDING_COUNT');
  const definition = await http.post('loadDefinition.action?type=W', {winno: '43', projid: 'WF_YBX'});
  function canonical(value) {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(k => [k, canonical(value[k])]));
    return value;
  }
  const projected = Object.fromEntries(['wins','funcs','funcMap','actMap'].map(k => [k, definition[k]]));
  const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(canonical(projected)))))].map(n => n.toString(16).padStart(2,'0')).join('');
  if (hash !== c.basic_contract.definition_sha256) throw Error('BASIC_API_DEFINITION_CHANGED');
  const form = definition.funcs.find(f => String(f.funcno) === '60');
  const win = definition.wins.find(w => String(w.winno) === '43');
  const save = win.func_btns.find(b => b.btnguid === c.basic_contract.save_button);
  const {data: context, set, get} = http.contextCopy();
  for (const key of Object.keys(context)) if (key.startsWith('WF_YBX$60-')) delete context[key];
  set('61-serialno', c.serial);
  const original = {};
  for (const field of form.formFields) {
    const key = field.fieldname.replace(/^t\./, '').toUpperCase();
    if (!Object.hasOwn(rows[0], key)) throw Error('BASIC_API_MISSING_BOUND_FIELD:' + key);
    const value = rows[0][key];
    if (value !== null && typeof value !== 'string') throw Error('BASIC_API_BOUND_TYPE:' + key);
    original[field.fieldname] = value ?? '';
    set('60-' + field.fieldname, value ?? '');
  }
  // Never inherit another open bill's name/teacher values.
  // These auxiliary fields carry a selected "id/name" choice, not a display
  // name. Passing a plain name makes the native procedure overwrite the id.
  for (const field of ['real_sname','lc_approver_name','lc_accepter_name']) set('61-' + field, '');
  const cmd = protocol.command('', win.beforeLoadProcs, context, form.typeMap);
  const prepared = await http.post('commonUpdate_responseButtonEvent.action', {
    cmd, projid: 'WF_YBX', userContext: JSON.stringify(context), async: false}, 'text');
  const keys = [...prepared.matchAll(/(?:local\(|;)([^():;]+):/g)].map(m => m[1]);
  const permittedContext = k => /^60-[A-Za-z0-9_.]+$/.test(k) || k === '61-CJ_IS_KYBSZYF';
  if (keys.some(k => !permittedContext(k))) throw Error('BASIC_API_PREPARE_CONTEXT_KEYS:' + keys.filter(k => !permittedContext(k)).join(','));
  try { protocol.applyResponse(prepared, context, keys); }
  catch (error) { throw Error('BASIC_API_PREPARE_PROTOCOL:' + error.message); }
  const groupnames = get('60-groupname');
  if (typeof groupnames !== 'string') throw Error('BASIC_API_GROUPS_MISSING');
  const visible = new Set();
  for (const match of form.formhtml.matchAll(/<tr\b([^>]*)>([\s\S]*?)<\/tr>/g)) {
    const group = match[1].match(/groupname="([^"]+)"/)?.[1];
    if (/display\s*:\s*none/i.test(match[1]) || groupnames && group && group !== '0' && !groupnames.split(',').includes(group)) continue;
    for (const field of match[2].matchAll(/#(t\.[A-Za-z0-9_]+)#/g)) visible.add(field[1]);
  }
  const teacherVisible=visible.has('t.lc_accepter')&&visible.has('t.lc_accepter_name');
  const teacherNotApplicable=read.bill['t.bx_type']==='2'&&!visible.has('t.lc_accepter')&&!visible.has('t.lc_accepter_name');
  if(!teacherVisible&&!teacherNotApplicable)throw Error('BASIC_API_TEACHER_VISIBILITY');
  for (const id of [...Object.keys(basic.fields), ...(teacherVisible?['t-lc_accepter','t-lc_accepter_name']:[])]) {
    const field=id.replace('t-', 't.');
    if (!visible.has(field) && (!Object.hasOwn(basic.fields,id) || basic.fields[id]!==original[field]))
      throw Error('BASIC_API_CHANGED_FIELD_HIDDEN:' + id);
  }
  const teacherField = form.formFields.find(f => f.id === 't-lc_accepter');
  const teacherProc = teacherField.Events[0].proc.split('|')[0];
  async function teacherLookup(value) {
    set('60-t.lc_accepter', value); set('60-lc_value', ''); set('60-lc_field', '');
    const response = await http.post('common_callProc.action', {procName: teacherProc,
      userData: JSON.stringify(context), projid: 'WF_YBX'}, 'text');
    try { protocol.applyResponse(response, context, ['60-lc_value','60-lc_field']); }
    catch (error) { throw Error('BASIC_API_TEACHER_PROTOCOL:' + error.message); }
    if (get('60-lc_field') !== 'lc_accepter_name') throw Error('BASIC_API_TEACHER_TARGET');
    return get('60-lc_value');
  }
  const candidateText = await teacherLookup(basic.teacher_name);
  const candidate = candidateText.trim() ? /^([A-Za-z0-9]{2,40})\/([^/\r\n,]+)\/([^/\r\n,]+)$/.exec(candidateText) : null;
  if (candidateText.trim() && (!candidate || candidate[1] !== basic.teacher_id || candidate[2].trim() !== basic.teacher_name))
    throw Error('BASIC_API_TEACHER_NOT_UNIQUE:' + candidateText);
  const name = await teacherLookup(basic.teacher_id);
  if (name !== basic.teacher_name) throw Error('BASIC_API_TEACHER_ID_MISMATCH');
  if(teacherVisible)set('60-t.lc_accepter_name', name);
  else for(const field of ['t.lc_accepter','t.lc_accepter_name'])set('60-'+field,original[field]);
  set('61-lc_accepter_name', '');
  for (const [id, value] of Object.entries(basic.fields)) set('60-' + id.replace('t-', 't.'), value);
  if(c.protected_identity_repair){
    const repair=c.protected_identity_repair;
    if(repair.original_id!==c.expected_userid||original['t.real_sno']!==repair.observed_id||
      original['t.real_sname']!==repair.original_name||!read.bill['t.REQ_INFO']?.startsWith(c.expected_userid+'-')||
      repair.observed_id!==repair.original_name)throw Error('BASIC_API_IDENTITY_REPAIR_BINDING');
    const personField=form.formFields.find(f=>f.fieldname==='t.real_sno');
    set('60-t.real_sno',repair.original_id);set('60-lc_value','');set('60-lc_field','');
    const raw=await http.post('common_callProc.action',{procName:personField.Events[0].proc.split('|')[0],userData:JSON.stringify(context),projid:'WF_YBX'},'text');
    protocol.applyResponse(raw,context,['60-lc_value','60-lc_field']);
    if(get('60-lc_field')!=='real_sname'||get('60-lc_value')!==repair.original_name)throw Error('BASIC_API_IDENTITY_REPAIR_LOOKUP');
    set('61-real_sname','');
  }
  for (const field of form.formFields) {
    if (!visible.has(field.fieldname)) continue;
    const value = get('60-' + field.fieldname);
    if (!field.nullable && !value.trim()) throw Error('BASIC_API_REQUIRED:' + field.id);
    if (field.maxlen && value.length > Number(field.maxlen) || field.minlen && value.length < Number(field.minlen))
      throw Error('BASIC_API_LENGTH:' + field.id);
    if (value && field.format === 'cellphone' && !/^1[3-9]\d{9}$/.test(value)) throw Error('BASIC_API_PHONE');
    if (value && field.format === 'number' && !/^\d+(?:\.\d+)?$/.test(value)) throw Error('BASIC_API_NUMBER');
  }
  // Serialize the exact approved definition but do not dispatch its save.
  const saveCommand = protocol.command(save.check_func, save.procs, context, form.typeMap);
  const changes = Object.fromEntries(form.formFields.map(f => [f.fieldname, get('60-' + f.fieldname)])
    .filter(([key,value]) => value !== original[key]));
  const writable = new Set([...Object.keys(basic.fields).map(k => k.replace('t-', 't.')), 't.lc_accepter','t.lc_accepter_name']);
  if(c.protected_identity_repair)writable.add('t.real_sno');
  if (Object.keys(changes).some(k => !writable.has(k))) throw Error('BASIC_API_UNDECLARED_CHANGE');
  const report = {ok: true, complete: true, action: 'basic-prepare', case_id: c.case_id, serial: c.serial,
    expected_userid: c.expected_userid, business_transport: 'native_http', system_write: false,
    financial_save_enabled: false, save_dispatched: false, page_context_modified: false,
    bill: read.bill, definition_sha256: hash, original, changes,
    visible_fields: [...visible], teacher: {id: basic.teacher_id, name, department: candidate?.[3] || '', lookup_by:'confirmed_native_id',applicable:teacherVisible},
    save_command_prepared: Boolean(saveCommand),
    requests: [...read.requests, ...requests], observed_at: new Date().toISOString(),
    elapsed_ms: Math.round(performance.now() - started)};
  return {report, command: saveCommand, context, http, requests};
}

async function reimbursementBasicPrepare(c) {
  return (await reimbursementBasicBuild(c)).report;
}

async function reimbursementBasicSave(c) {
  let dispatched = false, prepared = null, native_response = null;
  try {
    if (c.execute !== true || !c.expected_plan) throw Error('BASIC_API_WRITE_INTENT');
    const built = await reimbursementBasicBuild(c); prepared = built.report;
    if (prepared.bill['t.status'] !== '-99' || prepared.bill['t.lc_finish'] !== 'F')
      throw Error('BASIC_API_NOT_DRAFT');
    for (const field of ['original','changes','teacher','definition_sha256']) {
      if (JSON.stringify(prepared[field]) !== JSON.stringify(c.expected_plan[field]))
        throw Error('BASIC_API_PLAN_DRIFT:' + field);
    }
    if (!Object.keys(prepared.changes).length) return {...prepared, action:'basic-save',
      state:'already_matches_readback_required', requires_readback:true, save_dispatched:false};
    built.http.guard();
    dispatched = true;
    const raw = await built.http.post('commonUpdate_responseButtonEvent.action', {
      cmd:built.command, projid:'WF_YBX', userContext:JSON.stringify(built.context), async:false}, 'text');
    native_response=raw;
    const response = ReimbursementNativeProtocol.response(raw, []);
    if (!response.accepted) throw Error('BASIC_API_SAVE_' + response.status.toUpperCase());
    return {...prepared, action:'basic-save', state:'saved_independent_readback_required',
      requests:[...prepared.requests, built.requests.at(-1)],
      system_write:true, financial_save_enabled:true, save_dispatched:true, requires_readback:true};
  } catch (error) {
    return {ok:false, complete:false, action:'basic-save', case_id:c.case_id, serial:c.serial,
      expected_userid:c.expected_userid, business_transport:'native_http',
      state:dispatched?'unknown_save_readback_required':'rejected_before_save',
      save_dispatched:dispatched, system_write:dispatched, requires_readback:true,
      error:String(error.message), prepared, native_response};
  }
}
