/* School HTTP transport shared by fixed domain adapters; never a public URL API. */
const ReimbursementHttpReadScope = (() => {
  let active = null;
  const clone = (wire, type) => type === 'text' ? wire : JSON.parse(wire);
  function requestKey(url, data, type) {
    // 只复用定义、固定模板、确认预览及同条件101清单；绑定SQL、人员候选、票池仍逐次查询。
    if (/^loadDefinition\.action\?type=[WQ]$/.test(url) ||
        url === 'redirectCustomPage.action' && /^Customize\/customPrint\/bx_(?:frame_print|dtl)\.jsp$/.test(data?.url || ''))
      return JSON.stringify([url, type, data]);
    if (url === 'ybxPrint_yb4PrintHtml.action' && data?.printType === '报销确认' && data?.printModel === 'P')
      return JSON.stringify([url, type, data]);
    if (url === 'commonQuery_doQuery.action?funcno=101&projid=WF_YBX&needcount=true') {
      const {nd, ...query} = data;
      return JSON.stringify([url, type, query]);
    }
    return null;
  }
  function readOnly(url, data) {
    return /^loadDefinition\.action\?type=[WQ]$/.test(url) ||
      /^commonQuery_doQuery\.action\?funcno=\d+&projid=WF_YBX(?:&needcount=true)?$/.test(url) ||
      ['common_bindSQLDataBackend.action', 'common_getCustomFormFileContent.action',
       'commonQuery_autoComplete_f.action?funcno=22&fieldname=t.sno&id=22-t.sno&sid=df52e671fa8782a8f989c8f7c934542e&&projid=WF_YBX',
       'redirectCustomPage.action'].includes(url) ||
      url === 'ybxPrint_yb4PrintHtml.action' && data?.printType === '报销确认';
  }
  function invalidate() {
    if (active) {active.entries.clear();active.characters = 0;}
  }
  async function read(userid, url, data, type, trace, load) {
    const scope = active;
    if (!scope) return load();
    if (scope.userid !== userid) throw Error('HTTP_READ_SCOPE_IDENTITY');
    if (!readOnly(url, data)) invalidate();
    const key = requestKey(url, data, type);
    if (key === null) return load();
    const found = scope.entries.get(key);
    if (found) {
      const wire = await found.promise;
      trace.status = 'reused';trace.elapsed_ms = 0;trace.reused_from = found.id;
      return clone(wire, type);
    }
    if (scope.entries.size >= 32 || key.length > 4 * 1024 * 1024) return load();
    const entry = {id: ++scope.sequence};trace.scope_request_id = entry.id;
    entry.promise = (async () => {
      const value = await load(), wire = type === 'text' ? value : JSON.stringify(value);
      if (typeof wire !== 'string') throw Error('HTTP_READ_SCOPE_DATA_ONLY');
      if (scope.entries.get(key) === entry) {
        const characters = key.length + wire.length;
        if (scope.characters + characters > 4 * 1024 * 1024) scope.entries.delete(key);
        else scope.characters += characters;
      }
      return wire;
    })();
    scope.entries.set(key, entry);
    try {return clone(await entry.promise, type);}
    catch (error) {invalidate();throw error;}
  }
  async function run(userid, operation) {
    if (active || !/^[A-Za-z0-9]{2,40}$/.test(userid) || typeof operation !== 'function')
      throw Error('HTTP_READ_SCOPE_OWNER');
    active = {userid, entries: new Map(), sequence: 0, characters: 0};
    try {return await operation();}
    finally {active = null;}
  }
  return Object.freeze({run, read});
})();

function createReimbursementHttp(userid, options) {
  'use strict';
  const $ = window.jQuery, requests = options.requests;
  const routes = {
    definition: /^loadDefinition\.action\?type=W$/,
    gridDefinition: /^loadDefinition\.action\?type=Q$/,
    query: /^commonQuery_doQuery\.action\?funcno=\d+&projid=WF_YBX(?:&needcount=true)?$/,
    binding: /^common_bindSQLDataBackend\.action$/,
    formTemplate: /^common_getCustomFormFileContent\.action$/,
    button: /^commonUpdate_responseButtonEvent\.action$/,
    procedure: /^common_callProc\.action$/,
    paymentCandidate: /^commonQuery_autoComplete_f\.action\?funcno=22&fieldname=t\.sno&id=22-t\.sno&sid=df52e671fa8782a8f989c8f7c934542e&&projid=WF_YBX$/,
    bankCheck: /^bankCheck_BankCheckDtl\.action$/,
    remunerationBankCheck: /^(?:bankOlpayCheck_BankCheckDtlExcel|bankCheck_BankCheckDtlExcel)\.action$/,
    printOption: /^MainOperateAction_updateYbxMain\.action$/,
    printDocument: /^ybxPrint_yb4PrintHtml\.action$/,
    workflowSecretary: /^workflow_getSecretaryDef\.action$/,
    workflowCollab: /^wfNew_getCollabStatus\.action$/,
    workflowSign: /^MainOperateAction_getSignByUrl\.action$/,
    workflowAssets: /^ModDataApiAction_getZcFileList\.action$/,
    applicantVerificationStatus: /^activation_ifsend\.action$/,
    applicantCheck: /^MainOperateAction_approveCheck\.action$/,
    applicantSeal: /^MainOperateAction_approveSealReq\.action$/,
    workflowRefresh: /^MainOperateAction_updateLcRedis\.action$/,
    invoiceOcr: /^thirdPartyInterface\.action$/,
    detailsTemplate: /^redirectCustomPage\.action$/,
  };
  function guard() {
    if (location.origin !== 'https://cwzh.ustc.edu.cn' || location.pathname !== '/YBX/main.jsp' ||
        $?.getProjID?.() !== 'WF_YBX' || $.UC.bindData('#0-userinfo.userid#') !== userid)
      throw Error(options.prefix + '_IDENTITY_CHANGED');
  }
  if (!/^[A-Za-z0-9]{2,40}$/.test(userid) || !Array.isArray(requests) ||
      !([5000, 15000, 30000].includes(options.timeout) || options.timeout===120000 &&
        options.allowed.length===1 && options.allowed[0]==='invoiceOcr') ||
      !options.allowed.every(name => Object.hasOwn(routes, name))) throw Error('HTTP_ADAPTER_CONFIGURATION');
  guard();
  async function post(url, data, type = 'json') {
    if (!options.allowed.some(name => routes[name].test(url)) || !['json', 'text'].includes(type))
      throw Error(options.prefix + '_ENDPOINT_NOT_ALLOWED');
    guard();
    const trace = {endpoint: url.split('?')[0], status: 'dispatched',
      ...(options.phase ? {phase: options.phase()} : {})};
    const at = performance.now(); requests.push(trace);
    const value = await ReimbursementHttpReadScope.read(userid, url, data, type, trace, async () => {
      const result = await new Promise((resolve, reject) => {
      // The school wraps success/error callbacks. Deferred callbacks preserve
      // errors without invoking its prompt UI. No transport-level retries.
      $.ajax({type: 'POST', url, data, dataType: type, async: true, timeout: options.timeout})
        .done(resolve).fail((xhr, status) => {
          trace.status = 'unknown';
          trace.http_status = Number.isInteger(xhr?.status) ? xhr.status : null;
          reject(Error(options.prefix + '_REQUEST_UNKNOWN:' + trace.endpoint + ':' + status + ':http=' + trace.http_status));
        });
      });
      trace.elapsed_ms = Math.round(performance.now() - at);
      trace.status = 'received';guard();
      if (typeof result === 'string' && (result.startsWith('$$$SYS_ERROR$$$') || result.includes('login.jsp')))
        throw Error(options.prefix + '_NATIVE_RESPONSE_INVALID:' + trace.endpoint);
      return result;
    });
    guard();
    return value;
  }
  function contextCopy() {
    const data = JSON.parse(JSON.stringify($.UC.userData));
    const key = name => (name.startsWith('0-') ? '' : 'WF_YBX$') + name.toUpperCase();
    return {data, set: (name, value) => {data[key(name)] = value;}, get: name => data[key(name)]};
  }
  return {guard, post, contextCopy};
}

const ReimbursementHttpValues = Object.freeze({
  decode(value) {
    return String(value ?? '').replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(Number(n)))
      .replace(/&quot;/g, '"').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
  },
  moneyCents(value) {
    if (typeof value !== 'string' || !/^(?:\d+|[1-9]\d{0,2}(?:,\d{3})+)(?:\.\d{1,2})?$/.test(value))
      throw Error('HTTP_MONEY_INVALID');
    const [whole, fraction = ''] = value.replaceAll(',', '').split('.');
    return BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0'));
  },
  formRows(value) {
    if (typeof value !== 'string') throw Error('HTTP_FORM_RESPONSE');
    // Historical native bindings wrap arrays in parentheses; accept data only.
    const data = JSON.parse(value.trim().replace(/^\((\[[\s\S]*\])\);?$/, '$1'));
    if (!Array.isArray(data) || data.some(row => !row || typeof row !== 'object' || Array.isArray(row)))
      throw Error('HTTP_FORM_ROWS');
    return data;
  },
});
