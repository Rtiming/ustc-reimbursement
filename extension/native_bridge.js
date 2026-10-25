/* Native Messaging只接本机工具；普通网页不能发送消息或任意指定URL。 */
'use strict';
const HOST = 'cn.edu.ustc.reimbursement';
const ENTRY = 'https://cwzh.ustc.edu.cn/YBX/main.jsp';
const SYNTHETIC = false; // 仅独立测试包生成时切换，正式包始终false。
const SYNTHETIC_URL = null;
const BUILD_SHA = null; // 安装包注入源码身份；未构建的目录不能冒认已安装版本。
let port = null;
let queue = Promise.resolve();
// 队列可能被一个卡死的页面执行永久堵住，救援操作因此绕过队列。
const RESCUE_OPERATIONS = new Set(['ping', 'tab-close', 'reload']);
// 单个操作的上限：超时只解除队列继续办后续请求，既不重发也不假装成功。
const OPERATION_DEADLINE_MS = 25000;
const KNOWN_ERRORS = ['invalid_tab', 'finance_tab_not_unique_or_wrong_origin', 'tab_window_mismatch',
  'tab_namespace_mismatch', 'no_existing_browser_window', 'invalid_evaluate', 'wrong_origin',
  'invalid_native_response', 'invalid_create', 'wrong_window_type', 'user_selection_changed',
  'synthetic_operation_disabled', 'invalid_packet', 'request_expired', 'unsupported_operation',
  'operation_timeout','tab_discarded','frozen_tab_not_woken','relocation_target_not_isolated_frozen','relocation_result_unknown','verification_page_not_bound'];

function errorCode(error) {
  return KNOWN_ERRORS.includes(error.message) ? error.message : 'native_error';
}

function withDeadline(promise, ms) {
  let timer;
  return Promise.race([
    promise,
    new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('operation_timeout')), ms); })
  ]).finally(() => clearTimeout(timer));
}

// 只关闭本项目财务标签：窗口须与同次inventory一致，其他站点标签一律不碰。
async function rescueDispatch(message) {
  if (!message || message.schema !== 'ustc-native-edge-v1' || !/^[a-f0-9]{32}$/.test(message.id)) throw new Error('invalid_packet');
  if (Date.now() / 1000 > message.expires_at) throw new Error('request_expired');
  const args = message.arguments || {};
  if (message.operation === 'ping') return {pong: true, observed_at: Date.now()};
  if (message.operation !== 'tab-close') throw new Error('unsupported_operation');
  const tab = await target(args.tab_id, 'finance', args.expected_window_id ?? null);
  const origin = new URL(tab.url).origin;
  await chrome.tabs.remove(tab.id);
  return {tab_id: String(tab.id), window_id: String(tab.windowId), closed: true, origin};
}

function allowed(raw, scope = 'access') {
  try {
    const u = new URL(raw);
    if (scope === 'finance') return u.origin === 'https://cwzh.ustc.edu.cn' &&
      ['/YBX/main.jsp', '/WBX/main.jsp', '/WFManager/home2.jsp'].includes(u.pathname.split(';')[0]);
    if (['https://cwzh.ustc.edu.cn', 'http://cwzh.ustc.edu.cn'].includes(u.origin)) return true;
    if (u.origin === 'https://id.ustc.edu.cn' && u.pathname.replace(/;jsessionid=[^/]+/ig, '') === '/cas/login') {
      const service = new URL(u.searchParams.get('service'));
      return ['https://cwzh.ustc.edu.cn', 'http://cwzh.ustc.edu.cn'].includes(service.origin) &&
        ['/WFManager/home2.jsp', '/YBX/main.jsp'].includes(service.pathname.replace(/;jsessionid=[^/]+/ig, ''));
    }
    return false;
  } catch (_) { return false; }
}

async function target(id, scope = 'access', expectedWindowId = null) {
  let tabs;
  if (id == null) tabs = (await chrome.tabs.query({})).filter(t => allowed(t.url, scope));
  else {
    if (!/^[0-9]+$/.test(String(id))) throw new Error('invalid_tab');
    tabs = [await chrome.tabs.get(Number(id))];
  }
  if (tabs.length !== 1 || !allowed(tabs[0].url, scope)) throw new Error('finance_tab_not_unique_or_wrong_origin');
  if (expectedWindowId !== null && (!/^[0-9]+$/.test(String(expectedWindowId)) || String(tabs[0].windowId) !== String(expectedWindowId))) throw new Error('tab_window_mismatch');
  return tabs[0];
}

async function inventory() {
  const wins = await chrome.windows.getAll({populate: true, windowTypes: ['normal']});
  const allTabs = await chrome.tabs.query({});
  if (!wins.length) throw new Error('no_existing_browser_window');
  return {
    all_tab_ids: allTabs.map(t => String(t.id)), all_tab_count: allTabs.length,
    tab_inventory_complete: true, tab_inventory_scope: 'extension_visible_profile_tabs', window_inventory_scope: 'normal',
    edge_frontmost: wins.some(w => w.focused),
    windows: wins.map(w => ({window_id: String(w.id), visible: w.state !== 'minimized', minimized: w.state === 'minimized', active_tab_id: String((w.tabs.find(t => t.active) || {}).id || '')})),
    tabs: wins.flatMap(w => w.tabs.filter(t => allowed(t.url)).map(t => {
      const u = new URL(t.url);
      return {window_id: String(w.id), tab_id: String(t.id), loading: t.status === 'loading', path: u.origin + u.pathname.split(';')[0],
        active:!!t.active,discarded:!!t.discarded,frozen:typeof t.frozen==='boolean'?t.frozen:null,
        auto_discardable:typeof t.autoDiscardable==='boolean'?t.autoDiscardable:null};
    }))
  };
}

async function evaluate(args) {
  if (!['finance', 'access'].includes(args.scope) || typeof args.source !== 'string') throw new Error('invalid_evaluate');
  let tab = await target(args.tab_id, args.scope, args.expected_window_id ?? null);
  if(args.scope==='finance'&&tab.frozen===true){await wakeFinanceTab(args);tab=await target(args.tab_id,args.scope,args.expected_window_id??null);}
  if(args.scope==='finance'&&tab.active&&tab.autoDiscardable!==false)await chrome.tabs.update(tab.id,{autoDiscardable:false});
  const values = await chrome.scripting.executeScript({target: {tabId: tab.id}, world: 'MAIN', injectImmediately: true,
    func: (source, scope) => {
      // 导航发生在tabs.get之后也必须再核对；不放宽站点CSP或认证限制。
      const u = new URL(location.href);
      let valid = ['https://cwzh.ustc.edu.cn', 'http://cwzh.ustc.edu.cn'].includes(u.origin);
      if (scope === 'finance') valid = u.origin === 'https://cwzh.ustc.edu.cn' && u.pathname.split(';')[0] === '/YBX/main.jsp';
      else if (u.origin === 'https://id.ustc.edu.cn' && u.pathname === '/cas/login') {
        try { valid = ['https://cwzh.ustc.edu.cn', 'http://cwzh.ustc.edu.cn'].includes(new URL(u.searchParams.get('service')).origin); } catch (_) { valid = false; }
      }
      if (!valid) throw new Error('wrong_origin');
      return (0, eval)(source);
    }, args: [args.source, args.scope]});
  if (values.length !== 1 || typeof values[0].result !== 'string') throw new Error('invalid_native_response');
  return values[0].result;
}

async function wakeFinanceTab(args){
  if(Object.keys(args).some(k=>!['tab_id','expected_window_id','expected_epoch','scope','source'].includes(k)))throw Error('invalid_evaluate');
  const tab=await target(args.tab_id,'finance',args.expected_window_id??null);
  if(new URL(tab.url).pathname!=='/YBX/main.jsp')throw Error('wrong_origin');
  if(tab.discarded)throw Error('tab_discarded');
  const win=await chrome.windows.get(tab.windowId,{populate:true});
  const active=(win.tabs||await chrome.tabs.query({windowId:tab.windowId})).find(t=>t.active);
  if(!active||active.id!==tab.id)throw Error('user_selection_changed');
  const before={frozen:tab.frozen??null,auto_discardable:tab.autoDiscardable??null};
  await chrome.tabs.update(tab.id,{autoDiscardable:false,active:true});
  let after=await chrome.tabs.get(tab.id),helper=null,helperClosed=false;
  if(after.frozen===true){
    // 固定空白辅助页只在本窗口短暂切换；不导航原财务页、不把窗口置前。
    helper=await chrome.tabs.create({windowId:tab.windowId,url:'about:blank',active:true});
    const selected=(await chrome.tabs.query({windowId:tab.windowId,active:true}))[0];
    if(!selected||selected.id!==helper.id)throw Error('user_selection_changed');
    await chrome.tabs.update(tab.id,{active:true,autoDiscardable:false});
    const check=await chrome.tabs.get(helper.id),current=(await chrome.tabs.query({windowId:tab.windowId,active:true}))[0];
    if(current?.id===tab.id&&check.windowId===tab.windowId&&check.url==='about:blank'){await chrome.tabs.remove(helper.id);helperClosed=true;}
    for(let i=0;i<20;i++){after=await chrome.tabs.get(tab.id);if(after.frozen!==true)break;await new Promise(r=>setTimeout(r,50));}
  }
  if(after.frozen===true||after.discarded)throw Error('frozen_tab_not_woken');
  return{tab_id:String(tab.id),window_id:String(tab.windowId),before,after:{frozen:after.frozen??null,auto_discardable:after.autoDiscardable??null},
    woken:after.frozen===false,helper_tab_id:helper?String(helper.id):null,helper_closed:helperClosed,foreground_requested:false,
    navigation_requested:false,page_script_executed:false,business_replayed:false,system_write:false};
}

async function relocateFinanceTab(args){
  if(SYNTHETIC)throw Error('synthetic_operation_disabled');
  if(Object.keys(args).some(k=>!['tab_id','expected_window_id','expected_epoch'].includes(k))||!args.expected_window_id)throw Error('invalid_create');
  const tab=await target(args.tab_id,'finance',args.expected_window_id);
  const old=await chrome.windows.get(tab.windowId,{populate:true});
  // 仅接续独立工作窗；不把用户混合窗口中的标签搬走，也不刷新原文档。
  if(old.type!=='normal'||old.tabs?.length!==1||old.tabs[0].id!==tab.id||!tab.active||tab.discarded||tab.frozen!==true)throw Error('relocation_target_not_isolated_frozen');
  const created=await chrome.windows.create({tabId:tab.id,type:'normal',focused:false});
  const after=await chrome.tabs.get(tab.id);
  if(created.focused===true||after.windowId!==created.id||after.url!==tab.url)throw Error('relocation_result_unknown');
  await chrome.tabs.update(tab.id,{autoDiscardable:false,active:true});
  let observed=await chrome.tabs.get(tab.id);
  for(let i=0;i<20&&observed.frozen===true;i++){await new Promise(r=>setTimeout(r,50));observed=await chrome.tabs.get(tab.id);}
  return{tab_id:String(tab.id),old_window_id:String(tab.windowId),window_id:String(created.id),relocated:true,
    woken:observed.frozen===false,foreground_requested:false,navigation_requested:false,business_replayed:false,system_write:false};
}

async function showVerification(args){
  if(Object.keys(args).some(k=>!['tab_id','expected_window_id','expected_epoch','serial','expected_userid'].includes(k))||!/^\d+$/.test(args.serial||'')||!(/^[A-Za-z0-9]{2,40}$/).test(args.expected_userid||''))throw Error('invalid_evaluate');
  const tab=await target(args.tab_id,'finance',args.expected_window_id);
  const values=await chrome.scripting.executeScript({target:{tabId:tab.id},world:'MAIN',injectImmediately:true,
    func:(serial,userid)=>{const visible=e=>!!e?.getClientRects().length;
      return location.origin==='https://cwzh.ustc.edu.cn'&&location.pathname==='/YBX/main.jsp'&&window.$?.getProjID()==='WF_YBX'&&
        $.UC.bindData('#0-userinfo.userid#')===userid&&$.UC.bindData('#61-serialno#')===serial&&String($.UC.bindData('#61-node_id#'))==='508'&&visible(document.getElementById('formWF_YBX_1362_t-code'));},
    args:[args.serial,args.expected_userid]});
  if(values.length!==1||values[0].result!==true)throw Error('verification_page_not_bound');
  await chrome.tabs.update(tab.id,{active:true,autoDiscardable:false});
  await chrome.windows.update(tab.windowId,{focused:true,state:'normal'});
  return{shown:true,tab_id:String(tab.id),window_id:String(tab.windowId),serial:args.serial,verification_only:true,code_read:false,code_entered:false,approval_requested:false};
}

async function createPage(args, entry = ENTRY) {
  if (typeof args.silent !== 'boolean' || !/^[0-9]+$/.test(String(args.window_id))) throw new Error('invalid_create');
  const before = await chrome.windows.get(Number(args.window_id), {populate: true});
  if (before.type !== 'normal') throw new Error('wrong_window_type');
  const original = before.tabs.find(t => t.active);
  if (!original || String(original.id) !== args.active_tab_id) throw new Error('user_selection_changed');
  const created = await chrome.tabs.create({windowId: before.id, url: entry, active: !args.silent});
  let restored = args.silent;
  if (!args.silent) {
    const now = (await chrome.tabs.query({windowId: before.id, active: true}))[0];
    if (now && now.id === created.id) {
      await chrome.tabs.update(original.id, {active: true});
      restored = true;
    }
  }
  return {tab_id: String(created.id), old_active_tab_restored: restored, foreground_requested: false,
          active_tab_requested: !args.silent, silent: args.silent};
}

async function casLogin(args) {
  if (SYNTHETIC) throw new Error('synthetic_operation_disabled');
  if (typeof args.username !== 'string' || args.username !== args.expected_username || typeof args.password !== 'string' || !args.password || args.password.length > 8192) throw new Error('invalid_auth_request');
  const tab = await target(args.tab_id, 'access', args.expected_window_id ?? null);
  const u = new URL(tab.url);
  if (u.origin !== 'https://id.ustc.edu.cn' || u.pathname.replace(/;jsessionid=[^/]+/ig, '') !== '/cas/login') return {state: 'wrong_login_page', login_submitted: false};
  const values = await chrome.scripting.executeScript({target: {tabId: tab.id}, world: 'MAIN',
    func: async (username, password, expectedUsername) => {
      const stopped = state => ({state, login_submitted: false});
      const url = new URL(location.href);
      const normalPath = value => value.replace(/;jsessionid=[^/]+/ig, '');
      if (url.origin !== 'https://id.ustc.edu.cn' || normalPath(url.pathname) !== '/cas/login') return stopped('wrong_login_page');
      let service;
      try { service = new URL(url.searchParams.get('service')); } catch (_) { return stopped('wrong_login_service'); }
      if (!['https://cwzh.ustc.edu.cn', 'http://cwzh.ustc.edu.cn'].includes(service.origin) ||
          !['/WFManager/home2.jsp', '/YBX/main.jsp'].includes(normalPath(service.pathname)) || service.username || service.password) return stopped('wrong_login_service');
      const visible = element => !!element?.getClientRects().length;
      const originalDocument = document;
      const guard = () => {
        if (document !== originalDocument || location.href !== url.href) return 'wrong_login_page';
        let current;
        try { current = new URL(new URL(location.href).searchParams.get('service')); } catch (_) { return 'wrong_login_service'; }
        if (current.href !== service.href) return 'wrong_login_service';
        const challenge = [...document.querySelectorAll('input')].some(e => visible(e) && /captcha|otp|sms|verification|验证码/i.test(e.name+' '+e.id+' '+e.placeholder));
        if (challenge || [...document.querySelectorAll('.captcha,.geetest_panel,[id*=captcha],[id*=Captcha]')].some(visible)) return 'challenge_required';
        if ([...document.querySelectorAll('[role=alert],.error,.error-message,.alert-danger')].some(e => visible(e) && e.textContent.trim())) return 'login_error';
        return null;
      };
      const initialGuard = guard();
      if (initialGuard) return stopped(initialGuard);
      const user = document.getElementById('nameInput');
      const passwords = [...document.querySelectorAll('input[type=password]')].filter(visible);
      const button = document.getElementById('submitBtn');
      if (!visible(user) || passwords.length !== 1 || !visible(button)) return stopped('login_controls_not_ready');
      if (username !== expectedUsername || (user.value && user.value !== username)) return stopped('identity_mismatch');
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
      if (!setter) return stopped('login_controls_not_ready');
      const sameControls = () => {
        const current = [...document.querySelectorAll('input[type=password]')].filter(visible);
        return document.getElementById('nameInput') === user && document.getElementById('submitBtn') === button &&
          current.length === 1 && current[0] === passwords[0] && [user, passwords[0], button].every(e => e.ownerDocument === originalDocument && e.isConnected && visible(e));
      };
      for (const [element, value] of [[user, username], [passwords[0], password]]) {
        const issue = guard();
        if (issue) return stopped(issue);
        if (!sameControls()) return stopped('login_controls_not_ready');
        if (element === passwords[0] && user.value !== expectedUsername) return stopped('identity_mismatch');
        setter.call(element, value);
        element.dispatchEvent(new Event('input', {bubbles: true}));
        element.dispatchEvent(new Event('change', {bubbles: true}));
      }
      for (let i = 0; button.disabled && i < 10; i++) await new Promise(resolve => setTimeout(resolve, 50));
      const finalGuard = guard();
      if (finalGuard) return stopped(finalGuard);
      if (!sameControls() || button.disabled || passwords[0].value !== password) return stopped('login_controls_not_ready');
      if (user.value !== expectedUsername || user.value !== username) return stopped('identity_mismatch');
      button.click();
      return {state: 'login_submitted', login_submitted: true};
    }, args: [args.username, args.password, args.expected_username]});
  if (values.length !== 1 || typeof values[0].result?.state !== 'string') throw new Error('invalid_native_response');
  return values[0].result;
}

async function syntheticSmoke() {
  if (!SYNTHETIC) throw new Error('synthetic_operation_disabled');
  const before = await inventory();
  const modes = [];
  for (const silent of [true, false]) {
    const window = (await inventory()).windows[0];
    const created = await createPage({window_id: window.window_id, active_tab_id: window.active_tab_id, silent}, SYNTHETIC_URL);
    try {
      // 只执行已打包的固定无业务副作用函数，不接收任意地址或脚本。
      let read;
      for (let count = 0; count < 30; count++) {
        read = await chrome.scripting.executeScript({target: {tabId: Number(created.tab_id)}, world: 'MAIN',
          func: source => JSON.parse((0, eval)(source)),
          args: ["JSON.stringify({title:document.title,marker:document.getElementById('fixture')?.textContent})"]});
        if (read[0].result.marker) break;
        await new Promise(resolve => setTimeout(resolve, 50));
      }
      modes.push({silent, read: read[0].result, creation: created, after: await inventory()});
    } finally { await chrome.tabs.remove(Number(created.tab_id)); }
  }
  return {modes, before, after: await inventory(), real_finance_accessed: false};
}

async function dispatch(message) {
  if (!message || message.schema !== 'ustc-native-edge-v1' || !/^[a-f0-9]{32}$/.test(message.id)) throw new Error('invalid_packet');
  if (Date.now() / 1000 > message.expires_at) throw new Error('request_expired');
  const args = message.arguments || {};
  if (args.expected_epoch !== undefined && args.expected_epoch !== message.epoch) throw new Error('tab_namespace_mismatch');
  if (message.operation === 'inventory') return inventory();
  if (message.operation === 'create-finance-window') {
    if(SYNTHETIC)throw Error('synthetic_operation_disabled');
    if(Object.keys(args).some(k=>k!=='expected_epoch'))throw Error('invalid_create');
    const before=await chrome.windows.getAll({windowTypes:['normal']});
    if(!before.length)throw Error('no_existing_browser_window');
    const created=await chrome.windows.create({url:ENTRY,type:'normal',focused:false});
    if(created.focused===true)throw Error('background_window_focus_unexpected');
    const tabs=created.tabs||await chrome.tabs.query({windowId:created.id});
    if(tabs.length!==1)throw Error('invalid_native_response');
    return{window_id:String(created.id),tab_id:String(tabs[0].id),window_created:true,window_focus_requested:false,existing_windows:before.map(w=>String(w.id))};
  }
  if (message.operation === 'evaluate') return evaluate(args);
  if (message.operation === 'wake-finance-tab') return wakeFinanceTab(args);
  if (message.operation === 'relocate-finance-tab') return relocateFinanceTab(args);
  if (message.operation === 'show-verification') return showVerification(args);
  if (message.operation === 'page-read') {
    // Legacy envelope only: one top-level injection, no activation, reload, or retry.
    if (args.activate === true || args.all_frames === true) throw new Error('invalid_evaluate');
    return JSON.stringify({value: await evaluate(args), frame_id: 0, steps: [{step: 'single_background_injection', ok: true}]});
  }
  if (message.operation === 'validate-tab') { const t = await target(args.tab_id, 'access', args.expected_window_id ?? null); return {tab_id: String(t.id), window_id: String(t.windowId), verified: true}; }
  if (message.operation === 'tab-origin') {
    const t = await target(args.tab_id, 'access', args.expected_window_id ?? null); const u = new URL(t.url);
    return {origin: u.origin, pathname: u.pathname.split(';')[0]};
  }
  if (message.operation === 'create-page') return createPage(args);
  if (message.operation === 'cas-login') return casLogin(args);
  if (message.operation === 'smoke') return syntheticSmoke();
  throw new Error('unsupported_operation');
}

function connect() {
  if (port) return;
  const connection = chrome.runtime.connectNative(HOST);
  port = connection;
  connection.onMessage.addListener(message => {
    if (message && RESCUE_OPERATIONS.has(message.operation)) {
      if (message.operation === 'reload') {
        // 自更新：先回包，再让浏览器用磁盘上的新代码重载本扩展；不需要用户点击。
        if (port === connection) connection.postMessage({id: message.id, ok: true, result: {reloading: true}});
        setTimeout(() => { try { chrome.runtime.reload(); } catch (_) {} }, 250);
        return;
      }
      (async () => {
        let reply;
        try { reply = {id: message.id, ok: true, result: await rescueDispatch(message)}; }
        catch (error) { reply = {id: message.id, ok: false, error_code: errorCode(error)}; }
        if (port === connection) connection.postMessage(reply);
      })().catch(() => {});
      return;
    }
    queue = queue.then(async () => {
      let reply;
      try { reply = {id: message.id, ok: true, result: await withDeadline(dispatch(message), OPERATION_DEADLINE_MS)}; }
      catch (error) {
        reply = {id: message.id, ok: false, error_code: errorCode(error)};
        if (SYNTHETIC) reply.synthetic_diagnostic = String(error.message).slice(0, 300);
      }
      if (port === connection) connection.postMessage(reply);
    }).catch(() => {});
  });
  connection.onDisconnect.addListener(() => {
    const reason = chrome.runtime.lastError?.message || 'native host disconnected';
    console.warn('USTC native bridge:', reason);
    if (port === connection) port = null;
  });
  connection.postMessage({type: 'hello', protocol: 1, extension_id: chrome.runtime.id, synthetic_profile: SYNTHETIC, build_sha256: BUILD_SHA});
}
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name !== 'native-heartbeat') return;
  if (!port) connect();
  else port.postMessage({type: 'heartbeat'});
});
chrome.alarms.create('native-heartbeat', {periodInMinutes: 0.5});
connect();
