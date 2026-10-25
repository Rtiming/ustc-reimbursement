"""静默读取学校当前发票池并逐票留证；失败不切前台，未命中不推定无效或未报过。"""
from __future__ import annotations
import argparse
import json
import re
import subprocess
import sys
from datetime import datetime,timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE_SERIALIZER_JS = r'''
 const serialization={schema:'bounded-native-v1',limits:{
  metadata:{depth:8,nodes:25000,units:768000,stringUnits:16384,entries:2000,keys:400},
  core:{depth:12,nodes:150000,units:4000000,stringUnits:4000000,entries:10000,keys:1000},
  outputUnits:6000000,omissionDetails:250},stats:{metadata:{nodes:0,units:0,omissions:0},core:{nodes:0,units:0,omissions:0}},
  omissions:[],omissionCount:0,omissionDetailsDropped:0,byReason:{},definitionCaptureComplete:true,coreCaptureComplete:true};
 const captureOmission=(scope,path,reason,extra={})=>{
  serialization.omissionCount++;serialization.stats[scope].omissions++;
  serialization.byReason[reason]=(serialization.byReason[reason]||0)+1;
  if(scope==='core')serialization.coreCaptureComplete=false;else serialization.definitionCaptureComplete=false;
  const item={scope,path:String(path).slice(0,256),reason,...extra};
  if(serialization.omissions.length<serialization.limits.omissionDetails)serialization.omissions.push(item);else serialization.omissionDetailsDropped++;
  return{nativeType:'capture_omitted',...item};
 };
 const nativeValue=(value,key='',scope='metadata',path='$',depth=0,seen=new WeakMap())=>{
  const limits=serialization.limits[scope],stats=serialization.stats[scope];
  if(stats.nodes>=limits.nodes)return captureOmission(scope,path,'node_budget');
  stats.nodes++;stats.units+=12;
  if(stats.units>=limits.units)return captureOmission(scope,path,'output_budget');
  if(depth>limits.depth)return captureOmission(scope,path,'depth_limit');
  if(typeof value==='undefined')return{nativeType:'undefined'};
  if(typeof value==='bigint'||typeof value==='symbol')return{nativeType:typeof value,value:String(value)};
  if(typeof value==='function')return{nativeType:'function',source:nativeValue(String(value),key,scope,path+'.source',depth+1,seen)};
  if(typeof value==='string'){
   const protectedValue=mask(key,value);
   if(protectedValue.length>limits.stringUnits)return captureOmission(scope,path,'string_limit',{originalUnits:protectedValue.length});
   if(stats.units+protectedValue.length>limits.units)return captureOmission(scope,path,'output_budget',{originalUnits:protectedValue.length});
   stats.units+=protectedValue.length;return protectedValue;
  }
  if(value instanceof Date){try{return{nativeType:'Date',value:value.toISOString()};}catch(error){return captureOmission(scope,path,'invalid_date');}}
  if(value&&typeof value==='object'){
   if(typeof Node==='function'&&value instanceof Node){
    captureOmission(scope,path,'opaque_DOM');return{nativeType:'DOM',tag:value.nodeName,id:value.id||null};
   }
   if(typeof window!=='undefined'&&value===window){captureOmission(scope,path,'opaque_Window');return{nativeType:'Window'};}
   if(typeof jQuery==='function'&&value instanceof jQuery){
    captureOmission(scope,path,'opaque_jQuery');return{nativeType:'jQuery',length:value.length,ids:[...Array(Math.min(value.length,20)).keys()].map(i=>value[i]?.id||null)};
   }
   if(seen.has(value))return{nativeType:'shared_or_circular_reference',sourcePath:seen.get(value)};
   seen.set(value,String(path).slice(0,256));
   if(Array.isArray(value)){
    const result=[],count=Math.min(value.length,limits.entries);
    for(let i=0;i<count;i++){
     if(stats.nodes>=limits.nodes||stats.units>=limits.units){result.push(captureOmission(scope,path,'array_budget',{omittedItems:value.length-i}));return result;}
     const d=Object.getOwnPropertyDescriptor(value,String(i));
     result.push(d&&'value'in d?nativeValue(d.value,key,scope,path+'['+i+']',depth+1,seen):d?captureOmission(scope,path+'['+i+']','accessor_skipped'):null);
    }
    if(count<value.length)result.push(captureOmission(scope,path,'array_limit',{omittedItems:value.length-count}));return result;
   }
   let keys;try{keys=Object.keys(value);}catch(error){return captureOmission(scope,path,'object_keys_unavailable',{error:error.name});}
   const result={},count=Math.min(keys.length,limits.keys);
   for(let i=0;i<count;i++){
    if(stats.nodes>=limits.nodes||stats.units>=limits.units){result.__captureOmitted=captureOmission(scope,path,'object_budget',{omittedKeys:keys.length-i});return result;}
    const k=keys[i];if(k.length>limits.stringUnits){result.__captureOmitted=captureOmission(scope,path,'key_limit',{omittedKeys:keys.length-i});return result;}
    stats.units+=k.length;
    let d;try{d=Object.getOwnPropertyDescriptor(value,k);}catch(error){result[k]=captureOmission(scope,path+'.'+k,'property_unavailable',{error:error.name});continue;}
    result[k]=d&&'value'in d?nativeValue(d.value,k,scope,path+'.'+k,depth+1,seen):captureOmission(scope,path+'.'+k,'accessor_skipped');
   }
   if(count<keys.length)result.__captureOmitted=captureOmission(scope,path,'object_key_limit',{omittedKeys:keys.length-count});return result;
  }return mask(key,value);
 };
 const finalizeCapture=result=>{
  result.serialization=serialization;
  if(!serialization.coreCaptureComplete){result.ok=false;result.error='核心原生值存在明确缺项，不能用于执行计划';}
  const units=JSON.stringify(result).length;serialization.outputUnits=units;
  if(units>serialization.limits.outputUnits)return{ok:false,system_write:false,error:'字段回执超过有界输出预算，未宣称完整',
   serialization:{...serialization,outputLimitExceeded:true},currentPage:result.currentPage,projid:result.projid};
  return result;
 };
'''
REFRESH_POOL = r'''(()=>{
 if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX')throw Error('财务系统不符');
 const grid=document.getElementById('gridWF_YBX_53');
 if(!grid?.grid||!grid.getClientRects().length)throw Error('当前待报销发票池未加载');
 if(!$.Q.list['53'].url?.startsWith('commonQuery_doQuery.action?funcno=53'))throw Error('原生只读池查询入口改变');
 $(grid).jqGrid('setGridParam',{page:1}).trigger('reloadGrid');
 return{ok:true,action:'native_visible_pool_refresh',system_write:false,requiresIndependentReadback:true};
})()'''
SNAPSHOT = r'''(async()=>{
 if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX')throw Error('财务身份或主机不符');
 const vis=e=>e&&e.getClientRects().length;
 const mask=(k,v)=>/account|bankacnt|cnt1|cardno|acnt/i.test(k)&&typeof v==='string'?v.replace(/\b\d{12,19}\b/g,m=>m.slice(0,4)+'****'+m.slice(-4)):v;
 __BOUNDED_NATIVE_SERIALIZER__
 const grids=[];
 for(const [funcno,def] of Object.entries($.Q.list)){
  const id=$.Q.idFunc.getGridID(funcno),e=document.getElementById(id);
  if(!e?.grid||!vis(e))continue;
  const records=$(e).jqGrid('getGridParam','records');
  const rowIds=$(e).jqGrid('getDataIDs');
  const rows=rowIds.map(rowId=>Object.fromEntries(Object.entries($(e).jqGrid('getRowData',rowId)).map(([k,v])=>[k,mask(k,v)])));
  const rawRows=rowIds.map(rowId=>{const data=$(e).jqGrid('getLocalRow',rowId);return{rowId,nativeDataAvailable:!!data,nativeData:nativeValue(data||null,'','core','grids.'+funcno+'.rawRows.'+rowId),unavailableReason:data?null:'当前原生表格未保留getLocalRow缓存；显示行与原生定义仍单独保留，不推测原始值类型'};});
  const rowActionControls=rowIds.map(rowId=>({rowId,controls:[...(document.getElementById(rowId)?.querySelectorAll('a,button,[onclick]')||[])].map(x=>({tag:x.tagName,id:x.id,text:x.textContent.trim(),onclick:x.getAttribute('onclick'),href:x.getAttribute('href'),title:x.getAttribute('title'),disabled:!!x.disabled,visible:!!vis(x)}))}));
  grids.push({funcno,id,records,rowCount:rows.length,loading:!!e.grid.hDiv.loading,
   allRecordsVisible:!e.grid.hDiv.loading&&rows.length===records,
   currentPage:$(e).jqGrid('getGridParam','page'),lastPage:$(e).jqGrid('getGridParam','lastpage'),
   columns:def.colModel.map((c,i)=>({name:c.name,label:def.colNames[i]})),
   columnDefinitions:nativeValue(def.colModel,'','metadata','grids.'+funcno+'.columnDefinitions'),columnNames:nativeValue(def.colNames,'','core','grids.'+funcno+'.columnNames'),runtimeColumnDefinitions:nativeValue($(e).jqGrid('getGridParam','colModel'),'','metadata','grids.'+funcno+'.runtimeColumnDefinitions'),
   nativeQuery:{url:def.url,datatype:$(e).jqGrid('getGridParam','datatype'),prmNames:nativeValue($(e).jqGrid('getGridParam','prmNames'),'','metadata','grids.'+funcno+'.prmNames')},
   rowEventHandlers:Object.fromEntries(['ondblClickRow','onSelectRow','onCellSelect','onRightClickRow'].map(key=>[key,nativeValue($(e).jqGrid('getGridParam',key),'','metadata','grids.'+funcno+'.rowEventHandlers.'+key)])),
   rows,rawRows,rowActionControls});
 }
 const controls=[...document.querySelectorAll('a,button,[onclick],h3,span')].filter(vis).map(e=>({tag:e.tagName,id:e.id,text:e.textContent.trim(),onclick:e.getAttribute('onclick')})).filter(x=>x.text&&x.text.length<100);
 return finalizeCapture({ok:true,mode:'read_only_visible_invoice_view',system_write:false,observedAt:new Date().toISOString(),
  projid:$.getProjID(),currentPage:String($.page.currPage),
  identity:{userid:$.UC.bindData('#0-userinfo.userid#'),username:$.UC.bindData('#0-userinfo.username#')},
  visibleText:document.body.innerText.slice(0,20000),grids,controls});
})()'''.replace('__BOUNDED_NATIVE_SERIALIZER__', NATIVE_SERIALIZER_JS)


def full_grid_script(grid, expected_userid):
    """完整读取已加载的53/113/101查询范围；翻页仅查询，始终尝试恢复原视图。"""
    if grid not in ('53', '113', '101'):
        raise ValueError('完整分页只支持53发票池、113识别明细或101本人报销单')
    if not isinstance(expected_userid, str) or not re.fullmatch(r'[A-Za-z0-9_.@-]{1,80}', expected_userid):
        raise ValueError('expected-userid须来自本次身份回执的ASCII标识')
    return r'''(async()=>{
 const target=TARGET,expectedUserid=EXPECTED_USERID,id='gridWF_YBX_'+target;
 if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX')throw Error('财务主机或项目不符');
 const e=document.getElementById(id),g=$(e),vis=x=>!!x?.getClientRects().length;
 if(!e?.grid||!vis(e))throw Error('指定原生表未加载，不能导航或猜测范围');
 const userid=$.UC.bindData('#0-userinfo.userid#'),pageContext=String($.page.currPage),def=$.Q.list[target];
 if(userid!==expectedUserid)throw Error('登录身份与本次预期不同');
 if(!def?.url?.match(new RegExp('^commonQuery_doQuery\\.action\\?funcno='+target+'(?:&|$)')))throw Error('不是已核对的原生只读查询入口');
 const initialUrl=def.url;
 for(let poll=0;e.grid.hDiv.loading===true&&poll<30;poll++){
  if(document.getElementById(id)!==e||!vis(e)||String($.page.currPage)!==pageContext||$.UC.bindData('#0-userinfo.userid#')!==userid||$.Q.list[target]?.url!==initialUrl)throw Error('等待原表期间页面或身份变化');
  await new Promise(resolve=>setTimeout(resolve,100));
 }
 if(e.grid.hDiv.loading!==false||document.getElementById(id)!==e||!vis(e)||String($.page.currPage)!==pageContext||$.UC.bindData('#0-userinfo.userid#')!==userid||$.Q.list[target]?.url!==initialUrl)throw Error('原生表尚未就绪或页面身份改变；仅重新读取，不重导航');
 const mask=(k,v)=>/account|bankacnt|cnt1|cardno|acnt/i.test(k)&&typeof v==='string'?v.replace(/\b\d{12,19}\b/g,m=>m.slice(0,4)+'****'+m.slice(-4)):v;
 __BOUNDED_NATIVE_SERIALIZER__
 const param=k=>g.jqGrid('getGridParam',k),getSelected=()=>{
  const values=param('selarrrow');
  if(!Array.isArray(values)&&param('multiselect'))throw Error('缺少真实已选行数组，不能安全翻页');
  return Array.isArray(values)?values.slice():[];
 };
 const integer=v=>(typeof v==='number'||typeof v==='string'&&/^[0-9]+$/.test(v))&&Number.isSafeInteger(Number(v))?Number(v):null;
 const original={page:integer(param('page')),rowNum:integer(param('rowNum')),lastPage:integer(param('lastpage'))},count=integer(param('records'));
 if(!Number.isSafeInteger(count)||count<0||count>5000)throw Error('记录数异常或超过5000条上限');
 const validPage=count===0?[0,1].includes(original.page)&&original.lastPage===0:Number.isSafeInteger(original.page)&&original.page>=1&&Number.isSafeInteger(original.lastPage)&&original.lastPage>=original.page;
 if(!validPage||!Number.isSafeInteger(original.rowNum)||original.rowNum<1)throw Error('原分页参数异常');
 const initialSelection=getSelected(),seen=new Set(),colModel=param('colModel')||def.colModel||[],columns=colModel.map((c,i)=>({name:c.name,label:(param('colNames')||def.colNames||[])[i]||c.label||c.name,hidden:!!c.hidden}));
 const nativeUrl=def.url,currentIdentity=()=>$.UC.bindData('#0-userinfo.userid#');
 const sameContext=()=>{try{return document.getElementById(id)===e&&vis(e)&&String($.page.currPage)===pageContext&&currentIdentity()===userid&&$.Q.list[target]?.url===nativeUrl;}catch(error){return false;}};
 const rowIdentity=row=>{
  const keys=target==='53'?['t.tk_guid','tk_guid']:target==='101'?['t.serial_no']:['t.show_seq','show_seq','t.filesequcode','filesequcode'];
  for(const key of keys){const value=row[key];if(typeof value==='string'&&value.trim()&&value!=='NaN')return{field:key,value,key:key+':'+value};if(typeof value==='number'&&Number.isSafeInteger(value))return{field:key,value,key:key+':'+value};}
  throw Error('缺少可核对原生行标识，不用分页行号冒充唯一标识');
 };
 const capture=()=>{
  if(!sameContext()||e.grid.hDiv.loading)throw Error('目标表加载中或页面/身份改变');
  if(integer(param('records'))!==count)throw Error('读取期间记录总数变化');
  const ids=g.jqGrid('getDataIDs');if(!Array.isArray(ids)||new Set(ids).size!==ids.length)throw Error('当前页原生行号重复/异常');
  return ids.map(rowId=>{
   const display=g.jqGrid('getRowData',rowId),raw=g.jqGrid('getLocalRow',rowId),nativeIdentifier=rowIdentity(display),queryPage=Number(param('page'));
   const controls=[...(document.getElementById(rowId)?.querySelectorAll('a,button,[onclick]')||[])].map(x=>({tag:x.tagName,id:x.id,text:x.textContent.trim(),onclick:x.getAttribute('onclick'),href:x.getAttribute('href'),title:x.getAttribute('title'),disabled:!!x.disabled,visible:vis(x)}));
   return{rowId,queryPage,nativeIdentifier,row:nativeValue(display,'','core','rows.'+queryPage+'.'+rowId),
    raw:{rowId,queryPage,nativeIdentifier,nativeDataAvailable:!!raw,nativeData:nativeValue(raw||null,'','core','rawRows.'+queryPage+'.'+rowId),unavailableReason:raw?null:'原生远程表未保留getLocalRow缓存；不推测显示值的原始类型'},
    actions:{rowId,queryPage,nativeIdentifier,controls:nativeValue(controls,'','core','actions.'+queryPage+'.'+rowId)}};
  });
 };
 const hasPersistentSelection=rows=>rows.some(x=>!['','0','false','False','N','否'].includes(String(x.row['t.PC_Has_chosen']??x.row.PC_Has_chosen??'').trim()));
 const initial=capture(),all=[],pages=[],start=Date.now(),deadline=start+35000;
 let touched=false,readError=null,restorationError=null,complete=false;
 const addPage=rows=>{for(const x of rows){if(seen.has(x.nativeIdentifier.key))throw Error('分页记录原生标识重复，停止完整性结论');seen.add(x.nativeIdentifier.key);all.push(x);}pages.push({page:Number(param('page')),rowNum:Number(param('rowNum')),rowCount:rows.length,records:Number(param('records'))});};
 const reload=async(page,rowNum,restoring=false)=>{
  if(!sameContext())throw Error('页面或登录身份改变，不能继续操作原表');
  if(getSelected().length)throw Error('出现已有勾选，禁止翻页重置选择');
  if(!restoring&&Date.now()>deadline)throw Error('完整分页超过35秒读取预算');
  touched=true;g.jqGrid('setGridParam',{page,rowNum}).trigger('reloadGrid');
  const end=Date.now()+8000;
  await new Promise(r=>setTimeout(r,100));
  while(e.grid.hDiv.loading){if(Date.now()>end||(!restoring&&Date.now()>deadline))throw Error('原生分页查询超时');await new Promise(r=>setTimeout(r,100));}
  if(!sameContext()||Number(param('page'))!==page||Number(param('rowNum'))!==rowNum)throw Error('原生分页未应用或范围/身份改变');
  if(!restoring&&Number(param('records'))!==count)throw Error('分页期间记录总数变化');
 };
 try{
  if(count===0){
   if(e.grid.hDiv.loading!==false||initial.length!==0||initialSelection.length!==0)throw Error('空表记录、加载状态或实际行/勾选不一致');
   addPage(initial);complete=true;
  }
  else if(initial.length===count){addPage(initial);complete=true;}
  else{
   if(initialSelection.length||hasPersistentSelection(initial))throw Error('已有勾选，保持原状态并停止完整分页');
   const choices=[original.rowNum,...(param('rowList')||[]).map(Number)].filter(n=>Number.isSafeInteger(n)&&n>0&&n<=500);
   if(!choices.length)throw Error('没有可核对的有界原生页容量');
   const size=Math.max(...choices),last=Math.ceil(count/size);
   if(last<1||last>50)throw Error('查询超过50页上限');
   for(let page=1;page<=last;page++){
    await reload(page,size);const rows=capture();
    if(hasPersistentSelection(rows))throw Error('分页发现已选票，停止且不清除选择');
    if(Number(param('lastpage'))!==last||rows.length!==Math.min(size,count-(page-1)*size))throw Error('原生分页总页数或每页条数不一致');
    addPage(rows);
   }
   complete=all.length===count;
  }
 }catch(error){readError=String(error.message||error);complete=false;}
 finally{
  if(touched){try{await reload(original.page,original.rowNum,true);}catch(error){restorationError=String(error.message||error);complete=false;}}
 }
 let restoredSelection=null;try{restoredSelection=getSelected();}catch(error){restorationError=restorationError||String(error.message||error);}
 const finalEmptyIds=count===0?g.jqGrid('getDataIDs'):null;
 const emptyStateMatches=count!==0||e.grid.hDiv.loading===false&&Array.isArray(finalEmptyIds)&&finalEmptyIds.length===0&&integer(param('lastpage'))===original.lastPage;
 const viewRestored=sameContext()&&!e.grid.hDiv.loading&&emptyStateMatches&&integer(param('page'))===original.page&&integer(param('rowNum'))===original.rowNum&&JSON.stringify(restoredSelection)===JSON.stringify(initialSelection);
 if(!viewRestored){restorationError=restorationError||'原页/页容量/选择或身份未能独立回读为原值';complete=false;}
 if(integer(param('records'))!==count){readError=readError||'恢复后记录总数变化';complete=false;}
 const data=all.length?all:initial,result={ok:complete&&!readError&&!restorationError,mode:'read_only_full_grid',system_write:false,selection_write:false,observedAt:new Date().toISOString(),projid:'WF_YBX',currentPage:pageContext,
  serial:String($.UC.bindData('#61-serialno#')||''),
  visibleDialogs:[...(document.querySelectorAll?.('.ui-dialog')||[])].filter(vis).map(e=>({text:(e.innerText||'').slice(0,2000)})),
  identity:{userid,username:$.UC.bindData('#0-userinfo.username#')},scope:'当前登录人、指定原生查询条件下的完整表格；不含其他账号或全校查重证明',
  scopeComplete:complete&&!readError&&!restorationError,error:readError||restorationError,readError,restorationError,
  pagination:{limits:{maxRecords:5000,maxPages:50,maxPageSize:500,readBudgetMs:35000},original,visitedPages:pages,reloaded:touched,viewRestored,
   current:{page:Number(param('page')),rowNum:Number(param('rowNum')),records:Number(param('records'))},initialSelectedRowIds:initialSelection,restoredSelectedRowIds:restoredSelection},
  grids:[{funcno:target,id,records:count,rowCount:data.length,loading:!!e.grid.hDiv.loading,allRecordsVisible:complete&&!readError&&!restorationError,
   currentPage:Number(param('page')),lastPage:Number(param('lastpage')),columns,columnDefinitions:nativeValue(def.colModel,'','metadata','columnDefinitions'),
   runtimeColumnDefinitions:nativeValue(colModel,'','metadata','runtimeColumnDefinitions'),columnNames:nativeValue(param('colNames')||def.colNames||[],'','core','columnNames'),
   nativeQuery:{url:def.url,datatype:param('datatype'),prmNames:nativeValue(param('prmNames'),'','metadata','prmNames')},
   rowIds:data.map(x=>x.rowId),nativeIdentifiers:data.map(x=>({rowId:x.rowId,queryPage:x.queryPage,...x.nativeIdentifier})),
   rows:data.map(x=>x.row),rawRows:data.map(x=>x.raw),rowActionControls:data.map(x=>x.actions)}]};
 const finalized=finalizeCapture(result);
 if(!finalized.ok){finalized.scopeComplete=false;for(const grid of finalized.grids||[])grid.allRecordsVisible=false;}
 return finalized;
})()'''.replace('TARGET', json.dumps(grid)).replace('EXPECTED_USERID', json.dumps(expected_userid)).replace('__BOUNDED_NATIVE_SERIALIZER__', NATIVE_SERIALIZER_JS)


def navigation(view, expected_userid=None):
    label = {'home': '返回首页', 'used': '已使用发票', 'recognition': '【点击查看明细】', 'pool': '返回', 'drafts':'草稿', 'in-progress':'审批中', 'awaiting-approval':'待我审批','all-bills':'全部报销单'}[view]
    if expected_userid is not None and (not isinstance(expected_userid, str) or not re.fullmatch(r'[A-Za-z0-9_.@-]{1,80}', expected_userid)):
        raise ValueError('expected-userid须为本次已验证的ASCII账号')
    return r'''(()=>{
 if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX')throw Error('财务身份或主机不符');
 const label=LABEL,expectedUserid=EXPECTED_USERID,vis=e=>!!e?.getClientRects().length;
 const userid=$.UC.bindData('#0-userinfo.userid#');
 if(expectedUserid!==null&&userid!==expectedUserid)throw Error('导航账号与本次预期不同');
 if(label==='待我审批'){
  const target=document.getElementById('result206_title');
  if(!expectedUserid||String($.page.currPage)!=='6'||String($.UC.bindData('#61-serialno#')||'').trim()||$.active!==0||[...document.querySelectorAll('.ui-dialog')].some(vis)||!vis(target)||!/^待我审批\s*您有[0-9]+张报销单需要审核/.test(target.textContent.trim()))throw Error('待我审批入口须为本账号无编辑状态的原生首页');
  target.click();return{ok:true,action:'open_my_pending_approval_readonly',identity:{userid},system_write:false,approval_executed:false};
 }
 if(label==='草稿'||label==='审批中'||label==='全部报销单'){
  if(label!=='草稿'&&!expectedUserid)throw Error('报销单导航须明确当前账号');
  const entries=[...document.querySelectorAll('.pageTitle[title="'+label+'"]')].filter(vis);
  if(entries.length!==1)throw Error(label+'原生入口不唯一');entries[0].click();
  return{ok:true,action:label==='草稿'?'open_drafts_readonly':label==='全部报销单'?'open_all_bills_readonly':'open_in_progress_readonly',projid:$.getProjID(),system_write:false};
 }
 const candidates=[...document.querySelectorAll('a,button,[onclick],h3,span')].filter(e=>vis(e)&&e.textContent.trim()===label);
 const leaves=candidates.filter(e=>!candidates.some(x=>x!==e&&e.contains(x)));
 if(label==='返回首页'&&leaves.length===0){
  const node=String($.UC.bindData('#61-node_id#')||''),serial=String($.UC.bindData('#61-serialno#')||'');
  const lists=[...document.querySelectorAll('#gridWF_YBX_105,#gridWF_YBX_101')].filter(vis);
  const forms=[...document.querySelectorAll('[id^="formWF_YBX_"]')].filter(vis);
  const header=[...document.querySelectorAll('h2')].filter(e=>vis(e)&&e.textContent.trim()==='中国科学技术大学');
  const def=Object.getOwnPropertyDescriptor(window,'defaultWin');
  const onclick="if(defaultWin){var defWin=defaultWin.split(':');$.page.openExternal(defWin[0],defWin[1],defWin[2]);}else{window.location.reload();}";
  if(!expectedUserid||!['','508','535'].includes(node)||(serial&&!/^[0-9]+$/.test(serial))||lists.length!==1||$.active!==0||forms.some(e=>!/^formWF_YBX_(?:99|514)(?:_|$)/.test(e.id))||[...document.querySelectorAll('.ui-dialog')].some(vis)||def?.value!=='WF_YBX:6:SY-个人首页'||header.length!==1||header[0].getAttribute('onclick')!==onclick)throw Error('提交后列表不符合已核原生首页返回合同');
  header[0].click();return{ok:true,action:'native_read_only_view',label,navigation_route:'verified_post_applicant_list_header',system_write:false};
 }
 if(label==='【点击查看明细】'&&leaves.length===0){
  // 2026-10-13正式surface核对的零队列标题按钮；只点原生控件，不调用路由/回调或改display。
  if(!expectedUserid||String($.page.currPage)!=='6'||String($.UC.bindData('#61-serialno#')||'').trim())throw Error('零队列导航须明确账号且位于未建单的发票池');
  if([...document.querySelectorAll('.ui-dialog')].some(vis))throw Error('存在未处理弹框');
  const pool=document.getElementById('gridWF_YBX_53'),header=document.getElementById('winWF_YBX_33_head');
  if(!pool?.grid||!vis(pool)||pool.grid.hDiv.loading!==false||!vis(header)||!header.textContent.includes('您上传的发票已经全部识别完成。'))throw Error('不是已核对的零队列发票池状态');
  const selected=$(pool).jqGrid('getGridParam','selarrrow');
  if(!Array.isArray(selected)||selected.length)throw Error('已有勾选或选择未知，不执行零队列导航');
  const guid='99F8745496EB419B803BF408A1A3B97A',caption='处理识别中的图片';
  const buttons=[...document.querySelectorAll('button[guid="'+guid+'"]')];
  if(buttons.length!==1)throw Error('零队列原生按钮不唯一');
  const button=buttons[0];
  if(button.disabled||vis(button)||!header.contains(button)||button.getAttribute('btnname')!==caption||button.textContent.trim()!==caption||button.getAttribute('onclick')!==''||button.getAttribute('type')!=='button')throw Error('零队列原生按钮属性变化');
  const defs=$.page.list?.WF_YBX_33?.wins?.[0]?.title_btns;
  if(!Array.isArray(defs))throw Error('零队列标题定义未加载');
  const found=defs.filter(b=>b.btnguid===guid||b.caption===caption);
  if(found.length!==1)throw Error('零队列标题定义不唯一');
  const d=found[0],norm=s=>String(s).replace(/\s+/g,'');
  if(d.btnguid!==guid||d.caption!==caption||d.acts!=='1==1:79'||!Array.isArray(d.procs)||d.procs.length||
   ['pre_compute','pre_check','call_proc','post_check','clickquery','autojump','hasIntf_beforeclick','hasIntf_afterclick','sign_enabled','wfsignenabled'].some(k=>d[k]!==false)||
   ['check_func','affects','jscontent','customFunc','numSql','clickqueryhint'].some(k=>d[k]!=='')||
   typeof d.beforeclick!=='function'||norm(Function.prototype.toString.call(d.beforeclick))!=='function(op){}'||
   typeof d.afterclick!=='function'||norm(Function.prototype.toString.call(d.afterclick))!=='function(op){fp_clz();}'||
   typeof d.interfaceFailCallback!=='function'||norm(Function.prototype.toString.call(d.interfaceFailCallback))!=='function(result){}')throw Error('零队列导航定义出现未核对过程或回调');
  const descriptor=Object.getOwnPropertyDescriptor(window,'fp_clz');
  const source=descriptor&&'value'in descriptor&&typeof descriptor.value==='function'?Function.prototype.toString.call(descriptor.value):null;
  if(norm(source)!==norm('function fp_clz() { $.C.setValue("113", "t.tk_filecode", ""); $.C.setValue("113", "t.show_filename", ""); }'))throw Error('零队列详情清空回调源码变化');
  button.click();
  return{ok:true,action:'native_read_only_view',label,clickedGuid:guid,navigation_route:'zero_queue_title_button',
   identity:{userid},system_write:false,selection_write:false,client_state_changed:true,
   detail_context_cleared:['113.t.tk_filecode','113.t.show_filename'],requiresIndependentReadback:true};
 }
 if(leaves.length!==1)throw Error('原生只读导航入口不唯一：'+label+'，数量'+leaves.length);
 // 首页的H3本身没有事件，实际原生入口是其标题span。
 let target=leaves[0].tagName==='H3'?leaves[0].querySelector('span[id$="_title"]'):leaves[0];
 if(label==='【点击查看明细】'&&target?.parentElement?.id==='45_span_desc'){
  const owner=target.parentElement;
  if(!expectedUserid||!vis(owner)||owner.getAttribute('onclick')!=="ViewInvoiceInRecognition('','','');"||target.getAttribute('onclick'))throw Error('识别明细标题原生点击归属变化');
  target=owner; // The observed label span has no handler; its fixed parent owns the native action.
 }
 if(!target)throw Error('原生标题控件未加载');
 target.click();return{ok:true,action:'native_read_only_view',label,clickedId:target.id,system_write:false};
})()'''.replace('LABEL', json.dumps(label, ensure_ascii=False)).replace('EXPECTED_USERID', json.dumps(expected_userid))


def match_pool(numbers, receipts):
    if any(not isinstance(number, str) or not re.fullmatch(r'[0-9]{20}', number) for number in numbers):
        raise ValueError('票号必须是20位字符串，不接受数字类型')
    records = {number: [] for number in numbers}
    scopes = []
    for path in receipts:
        receipt = json.loads(Path(path).read_text(encoding='utf-8'))
        data = receipt.get('result', receipt)
        if not data.get('ok'):
            raise ValueError('失败回执不能证明发票池范围：' + str(path))
        for grid in data.get('grids', []):
            complete = bool(grid.get('allRecordsVisible', not grid.get('loading') and grid.get('rowCount') == grid.get('records')))
            scopes.append(dict(receipt=str(Path(path).resolve()), grid=grid.get('funcno'),
                records=grid.get('records'), row_count=grid.get('rowCount'), visible_grid_complete=complete))
            for row in grid.get('rows', []):
                values = [re.sub(r'<[^>]*>', '', str(value)).strip() for key, value in row.items()
                    if key in ['t.tk_no', 'tk_no', 't.tk_code_abst', 'tk_code_abst']]
                row_numbers={n for value in values for n in re.findall(r'(?<![0-9])[0-9]{20}(?![0-9])',value)}
                for number in numbers:
                    if number in row_numbers:
                        records[number].append(dict(receipt=str(Path(path).resolve()),grid=grid.get('funcno'),row=row))
    return dict(scope='当前登录人可见池，不含其他账号或全校查重证明',scopes=scopes,
        invoices=[dict(invoice_number=number, matches=rows,
            visibility_status='exact_invoice_number_found' if rows else 'not_found_in_observed_view',
            authenticity_status='unknown',red_void_status='unknown',reimbursement_status='unknown',
            note='只按完整票号匹配。原生状态列按实际回执另核；未命中不表示无效、未红冲或未报过。') for number, rows in records.items()])


def used_query_script(numbers):
    if not numbers or len(numbers)>6 or any(not isinstance(n,str) or not re.fullmatch(r'[0-9]{20}',n) for n in numbers):
        raise ValueError('已使用池静默查询每批1至6个20位字符串票号')
    return r'''(async()=>{
 if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX'||String($.page.currPage)!=='386')throw Error('不是已核对的已使用发票查询页');
 const grid=document.getElementById('gridWF_YBX_533'),field=document.getElementById('formWF_YBX_535_t-tk_no');
 if(!grid?.grid||!field||!field.getClientRects().length)throw Error('原生票号查询控件未就绪');
 if(!$.Q.list['533'].url?.startsWith('commonQuery_doQuery.action?funcno=533'))throw Error('原生只读查询入口改变');
 const other=['serial_no','start_money','end_money'].map(k=>document.getElementById('formWF_YBX_535_t-'+k)?.value||'');
 if(other.some(Boolean)||document.getElementById('formWF_YBX_535_t-tk_typename')?.value||document.getElementById('formWF_YBX_535_t-bx_type')?.value!=='-999')throw Error('其他筛选条件非空，不能宣称完整本账号查询');
 const original=field.value,results=[],numbers=NUMBERS;
 const reload=async()=>{if(String($.page.currPage)!=='386')throw Error('查询期间用户已切换页面');$(grid).jqGrid('setGridParam',{page:1}).trigger('reloadGrid');await new Promise(r=>setTimeout(r,100));const deadline=Date.now()+5000;while(grid.grid.hDiv.loading){if(Date.now()>deadline)throw Error('原生查询未完成');await new Promise(r=>setTimeout(r,100));}};
 try{
  for(const number of numbers){
   $(field).val(number).trigger('change').trigger('blur');$.F.getDataToContext('535');if(field.value!==number)throw Error('票号条件未正确写入原生查询框');
   await reload();
   const rows=$(grid).jqGrid('getDataIDs').map(id=>$(grid).jqGrid('getRowData',id));
   if(rows.some(row=>![...String(row['t.tk_code_abst']||'').matchAll(/(?<!\d)\d{20}(?!\d)/g)].some(m=>m[0]===number)))throw Error('查询未应用本票号条件');
   const count=$(grid).jqGrid('getGridParam','records');
   if(rows.length!==count)throw Error('本票查询仍有分页，不能声明完整结果');
   results.push({invoice_number:number,records:count,rows,complete:true,observedAt:new Date().toISOString()});
  }
 }finally{$(field).val(original).trigger('change').trigger('blur');$.F.getDataToContext('535');}
 return{ok:true,system_write:false,mode:'native_used_invoice_number_query',scope:'当前登录人的已使用票据查询',
  identity:{userid:$.UC.bindData('#0-userinfo.userid#'),username:$.UC.bindData('#0-userinfo.username#')},results,conditionRestored:field.value===original};
})()'''.replace('NUMBERS',json.dumps(numbers,ensure_ascii=False))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ['snapshot','full-grid','view','query-used','recover','refresh-pool']:
        cmd=sub.add_parser(name);cmd.add_argument('--tab-id',required=True);cmd.add_argument('--output',type=Path,required=True)
        if name=='full-grid':
            cmd.add_argument('--grid',required=True,choices=['53','113','101'])
            cmd.add_argument('--expected-userid',required=True)
        if name=='view':
            cmd.add_argument('view',choices=['home','used','recognition','pool','drafts','in-progress','awaiting-approval','all-bills'])
            cmd.add_argument('--expected-userid')
        if name=='query-used':cmd.add_argument('--numbers',type=Path,required=True)
        if name=='recover':cmd.add_argument('--receipt',type=Path,required=True)
    cmd=sub.add_parser('match-pool');cmd.add_argument('--numbers',type=Path,required=True)
    cmd.add_argument('--receipts',type=Path,nargs='+',required=True);cmd.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise ValueError('不覆盖旧查验回执，使用新输出文件')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    if args.command=='match-pool':
        numbers=json.loads(args.numbers.read_text(encoding='utf-8'))
        if not isinstance(numbers,list) or not numbers or any(not isinstance(n,str) or not re.fullmatch(r'[0-9]{20}',n) for n in numbers):raise ValueError('逐票查询清单须为20位ASCII票号字符串数组')
        args.output.write_text(json.dumps(match_pool(numbers,args.receipts),ensure_ascii=False,indent=2)+'\n')
        return
    if not re.fullmatch(r'[0-9]+',args.tab_id):raise ValueError('tab-id须来自本次标准tabs/access回执')
    if args.command=='recover':
        prior=json.loads(args.receipt.read_text(encoding='utf-8'))
        marker=prior.get('result',{}).get('result_marker')
        if prior.get('tab_id')!=args.tab_id or not isinstance(marker,str) or not re.fullmatch(r'reimbursement-result-[a-f0-9]{32}',marker):
            raise ValueError('原回执不含同标签有效结果标记，不能猜标记或重发查询')
        from edge_read_probe import evaluate
        from edge_runtime import browser_lock
        result=dict(receipt=str(args.receipt.resolve()),tab_id=args.tab_id,
            observed_at_utc=datetime.now(timezone.utc).isoformat(),system_write=False,reinjected=False)
        try:
            with browser_lock():
                raw=evaluate('document.getElementById('+json.dumps(marker)+')?.textContent || "missing"',args.tab_id)
            result['marker_state']=raw if raw in ['pending','missing',''] else 'completed'
            if result['marker_state']=='completed':result['result']=json.loads(raw)
        except (RuntimeError,subprocess.TimeoutExpired) as exc:
            result.update(marker_state='unknown',error=str(exc),note='静默只读原标记失败；未重发、未切前台')
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:result[k] for k in ['marker_state','system_write','reinjected']},ensure_ascii=False))
        return
    script=args.output.with_suffix('.js')
    if script.exists():raise ValueError('查验脚本已存在，先检查旧操作回执')
    source=SNAPSHOT if args.command=='snapshot' else full_grid_script(args.grid,args.expected_userid) if args.command=='full-grid' else REFRESH_POOL if args.command=='refresh-pool' else navigation(args.view,args.expected_userid) if args.command=='view' else used_query_script(json.loads(args.numbers.read_text(encoding='utf-8')))
    script.write_text(source,encoding='utf-8')
    raise SystemExit(subprocess.run([sys.executable,str(ROOT/'tools/edge_runtime.py'),str(script),
        '--tab-id',args.tab_id,'--output',str(args.output),'--quiet']).returncode)


if __name__=='__main__':main()
