/* 固定表单适配器的内部共用校验；不持有公开URL、字段或过程入口。 */
const ReimbursementNativeForm = (() => {
  function canonical(value) {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(k => [k, canonical(value[k])]));
    return value;
  }
  async function definition(http, contract) {
    const value = await http.post('loadDefinition.action?type=W', {winno: contract.winno, projid: 'WF_YBX'});
    const projected = Object.fromEntries(['wins','funcs','funcMap','actMap'].map(k => [k, value[k]]));
    const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(canonical(projected)))))].map(n => n.toString(16).padStart(2,'0')).join('');
    if (hash !== contract.definition_sha256) throw Error('NATIVE_FORM_DEFINITION_CHANGED');
    const form = value.funcs.find(f => String(f.funcno) === contract.funcno);
    const win = value.wins.find(w => String(w.winno) === (contract.save_winno || contract.winno));
    const saves = win?.func_btns.filter(b => contract.save_button ? b.btnguid === contract.save_button :
      typeof contract.save_caption==='string' && b.caption===contract.save_caption) || [];
    const save=saves[0];
    if (!form?.formFields || saves.length!==1 || save.wf_enabled || save.customFunc || save.jscontent || save.pre_compute) throw Error('NATIVE_FORM_SAVE_CONTRACT');
    return {value, form, win, save, hash};
  }
  function bind(form, row, set) {
    const original = {};
    for (const field of form.formFields) {
      const key = field.fieldname.replace(/^[A-Za-z][A-Za-z0-9_]*\./, '').toUpperCase();
      if (!Object.hasOwn(row, key) || row[key] !== null && typeof row[key] !== 'string') throw Error('NATIVE_FORM_BOUND_FIELD:' + key);
      original[field.fieldname] = row[key] ?? '';
      set(form.funcno + '-' + field.fieldname, original[field.fieldname]);
    }
    return original;
  }
  function visible(form, groups) {
    if (typeof groups !== 'string') throw Error('NATIVE_FORM_GROUPS_MISSING');
    const fields = new Set();
    for (const match of form.formhtml.matchAll(/<tr\b([^>]*)>([\s\S]*?)<\/tr>/g)) {
      const group = match[1].match(/groupname="([^"]+)"/)?.[1];
      if (/display\s*:\s*none/i.test(match[1]) || groups && group && group !== '0' && !groups.split(',').includes(group)) continue;
      for (const field of match[2].matchAll(/#([A-Za-z][A-Za-z0-9_]*\.[A-Za-z0-9_]+)#/g)) fields.add(field[1]);
    }
    return fields;
  }
  function validate(form, visible, get, changed) {
    for (const name of changed) {
      const field = form.formFields.find(f => f.fieldname === name);
      if (!field || field.hidden || field.readonly || !visible.has(name)) throw Error('NATIVE_FORM_CHANGED_FIELD_HIDDEN:' + name);
    }
    for (const field of form.formFields) {
      if (!visible.has(field.fieldname) || field.hidden || field.readonly) continue;
      const value = get(form.funcno + '-' + field.fieldname);
      if (typeof value !== 'string' || !field.nullable && !value.trim()) throw Error('NATIVE_FORM_REQUIRED:' + field.fieldname);
      if (field.maxlen && value.length > Number(field.maxlen) || field.minlen && value.length < Number(field.minlen)) throw Error('NATIVE_FORM_LENGTH:' + field.fieldname);
      if (value && field.format === 'number' && !/^\d+(?:\.\d+)?$/.test(value)) throw Error('NATIVE_FORM_NUMBER:' + field.fieldname);
    }
  }
  return Object.freeze({canonical, definition, bind, visible, validate});
})();
