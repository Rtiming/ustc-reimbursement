/* Data-only school command protocol. Domain owners supply pinned definitions;
 * this module has no transport, UI, workflow approval, retry, or public entry. */
const ReimbursementNativeProtocol = (() => {
  'use strict';
  const refPattern = /^#(\d+)-([A-Za-z0-9_.-]+)#$/;
  function parameters(refs, context, types = {}) {
    if (!refs) return '[]';
    return '[' + refs.split(';').map(ref => {
      const match = ref.match(refPattern);
      if (!match) throw Error('NATIVE_PROTOCOL_REFERENCE');
      const key = (match[1] === '0' ? '' : 'WF_YBX$') + ref.slice(1, -1).toUpperCase();
      // Missing data must not silently become an empty save parameter.
      if (!Object.hasOwn(context, key) || typeof context[key] !== 'string')
        throw Error('NATIVE_PROTOCOL_MISSING_VALUE:' + ref);
      let value = context[key];
      if ((value.startsWith('[') && value.endsWith(']')) || (value.startsWith('{') && value.endsWith('}'))) value += ' ';
      const type = types[match[2]] || 'CHAR';
      if (!['CHAR', 'NUMBER', 'DATE', 'VARCHAR2'].includes(type)) throw Error('NATIVE_PROTOCOL_TYPE');
      return '{name:' + JSON.stringify(match[2]) + ',value:' + JSON.stringify(value) + ',type:' + JSON.stringify(type) + '}';
    }).join(',') + ']';
  }
  function command(check, procs, context, types = {}, workflow = null) {
    if (!Array.isArray(procs) || procs.length > 8) throw Error('NATIVE_PROTOCOL_PROCEDURES');
    function proc(spec, name, data) {
      if (typeof spec !== 'string') throw Error('NATIVE_PROTOCOL_SPEC');
      const parts = spec.match(/^(`\$[a-f0-9]{32}):(.*)$/);
      if (!parts) throw Error('NATIVE_PROTOCOL_SPEC');
      return name + ':' + JSON.stringify(parts[1]) + ',' + data + ':' + parameters(parts[2], context, types);
    }
    const values = [check === '' || check === 'escape' ? 'check_func:"escape"' : proc(check, 'check_func', 'check_data')];
    procs.forEach((spec, i) => values.push(proc(spec, 'proc' + i, 'data' + i)));
    const fields=['wfName','wfAct','wfNodeId','wfState','wfUniKey','wfVars','wfKeyVal','wfComment','wfCollab','wfPdf'];
    if(workflow){
      if(Object.keys(workflow).sort().join(',')!==[...fields,'wfSecretaryRoleGuid'].sort().join(',')||Object.values(workflow).some(v=>typeof v!=='string'))throw Error('NATIVE_PROTOCOL_WORKFLOW_FIELDS');
      fields.splice(fields.length-1,0,'wfSecretaryRoleGuid');
    }
    for(const name of fields)values.push(name+':'+JSON.stringify(workflow?workflow[name]:''));
    return '{' + values.join(',') + '}';
  }
  function response(value, allowedKeys) {
    if (typeof value !== 'string' || !Array.isArray(allowedKeys)) throw Error('NATIVE_PROTOCOL_RESPONSE');
    // A button with several procedures returns concatenated successful chunks.
    // Merge only explicit pass/success boundaries. Errors, judges, trailing
    // commands and duplicate keys still fail atomically below.
    const normalized = value.replace(/\)(?:pass|success)(?=local\()/g, ')');
    const parts = normalized.split('local('), status = parts.shift();
    const match = status.match(/^(pass|success|error|judge)(?:[:;]([\s\S]*))?$/);
    if (!match) throw Error('NATIVE_PROTOCOL_RESPONSE_UNKNOWN');
    const updates = Object.create(null), allowed = new Set(allowedKeys.map(k => k.toUpperCase()));
    for (const part of parts) {
      if (!part.endsWith(')')) throw Error('NATIVE_PROTOCOL_LOCAL_SYNTAX');
      for (const pair of part.slice(0, -1).split(';').filter(pair => pair.trim() !== '')) {
        const i = pair.indexOf(':'), key = pair.slice(0, i).toUpperCase(), v = pair.slice(i + 1);
        // Native handleMsg also treats ':' / ';' as delimiters. Reject ambiguous
        // values rather than accepting a different value from the school UI.
        if (i <= 0 || v.includes(':') || !allowed.has(key) || Object.hasOwn(updates, key))
          throw Error('NATIVE_PROTOCOL_LOCAL_KEY');
        updates[key] = v;
      }
    }
    return {status: match[1], message: match[2] || '', accepted: ['pass', 'success'].includes(match[1]),
      confirmation_required: match[1] === 'judge', updates};
  }
  function applyResponse(value, context, allowedKeys) {
    const parsed = response(value, allowedKeys);
    if (!parsed.accepted) throw Error('NATIVE_PROTOCOL_' + parsed.status.toUpperCase());
    // Atomic parsing above: invalid responses never partly update context.
    for (const [key, v] of Object.entries(parsed.updates)) context[(key.startsWith('0-') ? '' : 'WF_YBX$') + key] = v;
    return parsed;
  }
  return Object.freeze({parameters, command, response, applyResponse});
})();
