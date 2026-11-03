'use strict';
// 原窗口有用户页时，恢复只移动原文档，不注入业务或切换用户选择。
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../extension/native_bridge.js'),'utf8');
function fixture(options={}){
  const tabs=[{id:10,windowId:1,url:'https://example.invalid/',active:true},
    {id:11,windowId:1,url:options.url||'https://cwzh.ustc.edu.cn/YBX/main.jsp',active:false,frozen:true,discarded:!!options.discarded}];
  const state={created:[],updated:[],scripts:[],removed:[]};
  const win=id=>({id,type:'normal',focused:false,tabs:tabs.filter(t=>t.windowId===id).map(t=>({...t}))});
  const event=()=>({addListener(){}});
  const chrome={runtime:{id:'fixture',onStartup:event(),onInstalled:event(),connectNative:()=>({onMessage:event(),onDisconnect:event(),postMessage(){}})},
    alarms:{onAlarm:event(),create(){}},
    windows:{get:async id=>win(id),create:async args=>{state.created.push(args);const t=tabs.find(t=>t.id===args.tabId);t.windowId=2;t.active=true;t.frozen=!!options.remainFrozen;if(options.selectionRace)tabs[0].active=false;return win(2);}},
    tabs:{get:async id=>({...tabs.find(t=>t.id===id)}),update:async(id,args)=>{state.updated.push({id,...args});Object.assign(tabs.find(t=>t.id===id),args);},remove:async id=>state.removed.push(id)},
    scripting:{executeScript:async arg=>{state.scripts.push(arg);throw Error('must not execute');}}};
  const context=vm.createContext({chrome,URL,console,setTimeout:(fn)=>setTimeout(fn,0),clearTimeout});vm.runInContext(source,context);
  return{state,tabs,call:args=>vm.runInContext('relocateFinanceTab',context)(args)};
}
(async()=>{
  const args={tab_id:'11',expected_window_id:'1',expected_active_tab_id:'10'};
  const a=fixture(),r=await a.call(args);
  assert.equal(r.woken,true);assert.equal(r.tab_id,'11');assert.equal(r.source_active_tab_preserved,true);assert.equal(r.source_active_tab_id,'10');
  assert.equal(a.tabs[0].active,true);assert.equal(a.tabs[0].windowId,1);assert.deepEqual(JSON.parse(JSON.stringify(a.state.created)),[{tabId:11,type:'normal',focused:false}]);
  assert.equal(a.state.scripts.length,0);assert.equal(a.state.removed.length,0);assert(a.state.updated.every(x=>x.id===11));
  for(const [change,opts,pattern] of [[{expected_active_tab_id:'99'},{},/user_selection_changed/],[{expected_active_tab_id:'11'},{},/user_selection_changed/],[{expected_window_id:'3'},{},/tab_window_mismatch/],[{},{discarded:true},/not_isolated_frozen/],[{},{url:'https://evil.invalid/YBX/main.jsp'},/wrong_origin/]]){
    const f=fixture(opts);await assert.rejects(f.call({...args,...change}),pattern);assert.equal(f.state.created.length,0);assert.equal(f.state.scripts.length,0);
  }
  const absent=fixture();await assert.rejects(absent.call({tab_id:'11',expected_window_id:'1'}),/not_isolated_frozen/);assert.equal(absent.state.created.length,0);
  const race=fixture({selectionRace:true});await assert.rejects(race.call(args),/relocation_result_unknown/);assert.equal(race.state.created.length,1);assert.equal(race.state.updated.length,0);
  const still=fixture({remainFrozen:true});const retained=await still.call(args);assert.equal(retained.woken,false);assert.equal(retained.window_id,'2');assert.equal(still.state.created.length,1);
  console.log('mixed-window frozen recovery contracts passed');
})().catch(e=>{console.error(e);process.exit(1);});
