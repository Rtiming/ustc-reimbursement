'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../extension/native_bridge.js'), 'utf8');
const ENTRY = 'https://cwzh.ustc.edu.cn/YBX/main.jsp';

function harness(options={}) {
  const state = {active: 10, created: [], updated: [], scripts: [], next: 20};
  const tabs = [
    {id: 10, windowId: 1, url: 'https://private.example/path?secret=do-not-collect', active: true},
    {id: 11, windowId: 1, url: ENTRY+'?ticket=do-not-collect', active: false},
    {id: 12, windowId: 1, url: 'https://id.ustc.edu.cn/cas/login?service=https%3A%2F%2Fcwzh.ustc.edu.cn%2FWFManager%2Fhome2.jsp', active: false},
    {id: 13, windowId: 1, url: 'https://cwzh.ustc.edu.cn.evil.invalid/YBX/main.jsp', active: false},
  ];
  const event = () => ({addListener() {}});
  const window = () => ({id: 1, type: 'normal', state: 'normal', focused: !!state.focused, tabs: tabs.map(t => ({...t, active: t.id === state.active}))});
  const chrome = {
    runtime: {id: 'fixture', onStartup: event(), onInstalled: event(), connectNative: () => ({onMessage: event(), onDisconnect: event(), postMessage() {}})},
    alarms: {onAlarm: event(), create() {}},
    windows: {getAll: async () => [window()], get: async () => options.isolated?{...window(),tabs:window().tabs.filter(t=>t.id===11)}:window(),update:async(id,args)=>{state.focused=args.focused;state.windowUpdates=[...(state.windowUpdates||[]),{id,...args}];return window();},create:async args=>{state.created.push(args);if(args.tabId){const t=tabs.find(t=>t.id===args.tabId);t.windowId=2;state.frozen=!!options.remainFrozen;return{id:2,focused:false,tabs:[t]};}return{id:2,focused:false,tabs:[{id:30,windowId:2,url:args.url}]};}},
    tabs: {
      query: async filter => filter.active ? window().tabs.filter(t => t.active) : window().tabs,
      get: async id => ({...tabs.find(t => t.id === id),active:id===state.active,...(id===11?{frozen:!!state.frozen,autoDiscardable:state.autoDiscardable!==false}: {})}),
      create: async args => {state.created.push(args); const t = {id: state.next++, windowId: args.windowId, url: args.url}; tabs.push(t); if (args.active) state.active = t.id; return t;},
      update: async (id, args) => {state.updated.push({id, ...args}); if (args.active) state.active = id;if(id===11&&args.active&&!options.hardFrozen)state.frozen=false;if(id===11&&'autoDiscardable'in args)state.autoDiscardable=args.autoDiscardable;},
      remove:async id=>{const at=tabs.findIndex(t=>t.id===id);if(at>=0)tabs.splice(at,1);},
    },
    scripting: {executeScript: async args => {state.scripts.push(args); return [{result: String(args.func).includes('formWF_YBX_1362_t-code')?options.verificationBound===true:'fixture-result'}];}},
  };
  const context = vm.createContext({chrome, URL, console, setTimeout, Date});
  if(options.frozen){state.frozen=true;state.active=11;}
  vm.runInContext(source, context);
  const dispatch = vm.runInContext('dispatch', context);
  const call = (operation, args = {}, expires = Date.now()/1000+10, epoch) => dispatch({schema: 'ustc-native-edge-v1', id: 'a'.repeat(32), operation, arguments: args, expires_at: expires, epoch});
  return {call, state, context};
}

(async () => {
  const a = harness();
  const inventory = await a.call('inventory');
  assert.deepEqual(Array.from(inventory.tabs, x => x.tab_id), ['11', '12']);
  assert.deepEqual(Array.from(inventory.all_tab_ids), ['10','11','12','13']);
  assert.equal(inventory.all_tab_count,4);assert.equal(inventory.tab_inventory_complete,true);
  assert.equal(inventory.tab_inventory_scope,'extension_visible_profile_tabs');assert.equal(inventory.window_inventory_scope,'normal');
  assert(!JSON.stringify(inventory).includes('do-not-collect'));
  assert(!JSON.stringify(inventory).includes('private.example'));
  await a.call('validate-tab', {tab_id: '11'});
  assert.equal(a.state.created.length, 0); assert.equal(a.state.updated.length, 0);
  await assert.rejects(a.call('validate-tab', {tab_id: '13'}), /wrong_origin/);
  await assert.rejects(a.call('evaluate', {tab_id: '13', scope: 'finance', source: 'evil'}), /wrong_origin/);
  assert.equal(a.state.scripts.length, 0);
  assert.equal(await a.call('evaluate', {tab_id: '11', scope: 'finance', source: 'fixture'}), 'fixture-result');
  assert.equal(a.state.scripts[0].target.tabId, 11); assert.equal(a.state.scripts[0].world, 'MAIN');
  assert.equal(a.state.scripts[0].injectImmediately,true);
  const frozen=harness({frozen:true});const woke=await frozen.call('wake-finance-tab',{tab_id:'11',expected_window_id:'1'});
  assert.equal(woke.woken,true);assert.equal(woke.foreground_requested,false);assert.equal(woke.business_replayed,false);assert.equal(frozen.state.scripts.length,0);
  assert.equal(frozen.state.active,11);assert.equal(frozen.state.autoDiscardable,false);
  const inactive=harness();inactive.state.frozen=true;await assert.rejects(inactive.call('wake-finance-tab',{tab_id:'11',expected_window_id:'1'}),/user_selection_changed/);assert.equal(inactive.state.updated.length,0);
  const hard=harness({frozen:true,hardFrozen:true});await assert.rejects(hard.call('wake-finance-tab',{tab_id:'11',expected_window_id:'1'}),/frozen_tab_not_woken/);
  assert.equal(hard.state.scripts.length,0);
  const isolated=harness({frozen:true,hardFrozen:true,isolated:true});
  const moved=await isolated.call('relocate-finance-tab',{tab_id:'11',expected_window_id:'1'});
  assert.equal(moved.tab_id,'11');assert.equal(moved.window_id,'2');assert.equal(moved.woken,true);
  assert.equal(isolated.state.created[0].tabId,11);assert.equal(isolated.state.created[0].focused,false);
  assert.equal(isolated.state.created[0].url,undefined);assert.equal(isolated.state.scripts.length,0);
  const stuck=harness({frozen:true,hardFrozen:true,isolated:true,remainFrozen:true});
  const retained=await stuck.call('relocate-finance-tab',{tab_id:'11',expected_window_id:'1'});
  assert.equal(retained.relocated,true);assert.equal(retained.woken,false);assert.equal(retained.window_id,'2');
  await assert.rejects(hard.call('relocate-finance-tab',{tab_id:'11',expected_window_id:'1'}),/not_isolated_frozen/);
  await assert.rejects(isolated.call('relocate-finance-tab',{tab_id:'11',expected_window_id:'1'}),/tab_window_mismatch/);
  const verification=harness({verificationBound:true});const shown=await verification.call('show-verification',{tab_id:'11',expected_window_id:'1',serial:'123',expected_userid:'SYNTH001'});
  assert.equal(shown.shown,true);assert.equal(shown.code_read,false);assert.equal(verification.state.windowUpdates[0].id,1);
  const notVerification=harness();await assert.rejects(notVerification.call('show-verification',{tab_id:'11',expected_window_id:'1',serial:'123',expected_userid:'SYNTH001'}),/verification_page_not_bound/);assert.equal(notVerification.state.windowUpdates,undefined);
  assert.equal(a.state.updated.length, 0);
  const scriptCount=a.state.scripts.length;
  await assert.rejects(a.call('evaluate',{tab_id:'11',scope:'finance',source:'fixture',expected_window_id:'2'}),/tab_window_mismatch/);
  await assert.rejects(a.call('evaluate',{tab_id:'11',scope:'finance',source:'fixture',expected_epoch:'old'},undefined,'new'),/tab_namespace_mismatch/);
  assert.equal(a.state.scripts.length,scriptCount);
  const bound=await a.call('validate-tab',{tab_id:'11',expected_window_id:'1',expected_epoch:'current'},undefined,'current');
  assert.equal(bound.window_id,'1');assert.equal(bound.tab_id,'11');
  const quiet = await a.call('create-page', {window_id: '1', active_tab_id: '10', silent: true, url: 'https://ignored.invalid'});
  assert.equal(a.state.created[0].url, ENTRY); assert.equal(a.state.created[0].active, false);
  assert.equal(quiet.foreground_requested, false); assert.equal(a.state.active, 10);
  const visible = await a.call('create-page', {window_id: '1', active_tab_id: '10', silent: false});
  assert.equal(a.state.created[1].active, true); assert.equal(a.state.active, 10); assert.equal(visible.old_active_tab_restored, true);
  await assert.rejects(a.call('create-page', {window_id: '1', active_tab_id: '10', silent: 'false'}), /invalid_create/);
  await assert.rejects(a.call('create-page', {window_id: '1', active_tab_id: '99', silent: true}), /user_selection_changed/);
  const before = a.state.created.length;
  await assert.rejects(a.call('create-page', {}, 0), /expired/); assert.equal(a.state.created.length, before);
  await assert.rejects(a.call('smoke'), /synthetic_operation_disabled/);
  await assert.rejects(a.call('raw-click'), /unsupported_operation/);
  const separate=harness();const separateWindow=await separate.call('create-finance-window');assert.equal(separateWindow.window_created,true);assert.equal(separate.state.created[0].url,ENTRY);assert.equal(separate.state.created[0].focused,false);assert.equal(separate.state.active,10);
  await assert.rejects(separate.call('create-finance-window',{url:'https://elsewhere.invalid'}),/invalid_create/);
  const oldReadCount=a.state.scripts.length;
  const legacy=JSON.parse(await a.call('page-read',{tab_id:'11',scope:'finance',source:'fixture'}));
  assert.equal(legacy.value,'fixture-result');assert.equal(a.state.scripts.length,oldReadCount+1);
  assert.equal(a.state.scripts.at(-1).target.allFrames,undefined);
  await assert.rejects(a.call('page-read',{tab_id:'11',scope:'finance',source:'fixture',activate:true}),/invalid_evaluate/);
  await assert.rejects(a.call('page-read',{tab_id:'11',scope:'finance',source:'fixture',all_frames:true}),/invalid_evaluate/);
  assert.equal(a.state.scripts.length,oldReadCount+1);
  assert.equal((source.match(/chrome\.windows\.update/g)||[]).length,1);
  assert(source.split('async function showVerification(args){')[1].split('async function createPage')[0].includes('chrome.windows.update'));
  assert(!source.includes('document.cookie'));
  assert(!source.includes('onMessageExternal'));
  console.log('native bridge contract: fixed origins/tabs, silent switch, no URL override, expired request and production smoke guards passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
