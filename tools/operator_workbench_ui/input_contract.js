/* 同一输入可重连，检查与执行是不同意图；只计算标识，不调用业务。 */
((root) => {
  function canonical(value) {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
    return value;
  }
  async function requestId(base, argumentsValue) {
    if (!/^[a-f0-9]{32}$/.test(base)) throw Error('本次操作标识无效');
    const value = {...argumentsValue};
    delete value.request_id;
    const hash = await root.crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(canonical(value))));
    const fingerprint = [...new Uint8Array(hash)].map(v => v.toString(16).padStart(2, '0')).join('');
    return 'workbench-' + base + '-' + fingerprint.slice(0, 24);
  }
  const api = {canonical, requestId};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.WorkbenchInputs = api;
})(globalThis);
