#!/usr/bin/env python3
"""读取当前权限内已加载的财务字段，保留原值/显示值，生成可审核操作计划。

snapshot 只读已有 Q/F/DOM，不查询隐藏后台、不翻页；plan 永远不执行操作。
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'ustc-finance-surface-v1'


def recognition_navigation_capture_script():
    """只读固定文字的已加载节点，含隐藏候选；不选择候选、不猜路由。"""
    return r'''(()=>{
 const selector='a,button,[onclick],h3,span',labels=['【点击查看明细】','处理识别中的图片'],limit=20,parentLimit=4;
 const matches=[...document.querySelectorAll(selector)].filter(e=>labels.includes((e.textContent||'').trim()));
 let omittedFields=0;
 const bounded=v=>{if(typeof v==='string'&&v.length>4096){omittedFields++;return{capture_omitted:true,reason:'string_limit',originalLength:v.length};}return v;};
 const extra=e=>{
  const attrs=[...(e.attributes||[])],attributes=attrs.slice(0,40).map(a=>({name:a.name,value:bounded(a.value)}));
  let nativeClickHandlers=null,clickHandlerCount=null,clickHandlersAvailable=false,clickHandlersError=null;
  try{if(typeof $!=='undefined'&&typeof $._data==='function'){
   const handlers=$._data(e,'events')?.click||[];clickHandlersAvailable=Array.isArray(handlers);
   if(clickHandlersAvailable){clickHandlerCount=handlers.length;nativeClickHandlers=handlers.slice(0,20).map(h=>({
    type:h.type??null,origType:h.origType??null,namespace:h.namespace??null,selector:h.selector??null,
    handler:typeof h.handler==='function'?bounded(String(h.handler)):null}));}
  }}catch(error){clickHandlersError=error.name;}
  return{attributes,attributeCount:attrs.length,attributesComplete:attrs.length<=40,
   nativeClickHandlers,clickHandlerCount,clickHandlersAvailable,clickHandlersComplete:clickHandlersAvailable&&clickHandlerCount<=20,clickHandlersError};
 };
 const describe=e=>({tag:e.tagName,id:e.id||null,text:labels.includes((e.textContent||'').trim())?(e.textContent||'').trim():null,
  visible:!!e.getClientRects().length,disabled:typeof e.disabled==='boolean'?e.disabled:null,
  onclick:bounded(e.getAttribute('onclick')),href:bounded(e.getAttribute('href')),
  onclickProperty:typeof e.onclick==='function'?bounded(String(e.onclick)):null});
 const candidates=matches.slice(0,limit).map((e,index)=>{
  const parents=[];let p=e.parentElement;
  while(p&&parents.length<parentLimit){parents.push(describe(p));p=p.parentElement;}
  return{candidateIndex:index,...describe(e),...extra(e),parents,parentChainComplete:!p};
 });
 const definitions=[];let definitionCount=0;
 const pages=typeof $==='undefined'?null:$.page?.list;
 if(pages&&typeof pages==='object')for(const [pageKey,page]of Object.entries(pages)){
  if(!Array.isArray(page.wins))continue;
  for(const win of page.wins){if(!Array.isArray(win.func_btns))continue;
   for(const button of win.func_btns){if(button?.caption!=='处理识别中的图片')continue;
    definitionCount++;
    if(definitions.length<limit){
     const definition=typeof native==='function'?native(button,'','metadata','recognitionNavigationDefinitions.'+pageKey+'.'+win.winno):null;
     definitions.push({pageKey,winno:String(win.winno),caption:button.caption,definition,
      definitionCaptureAvailable:typeof native==='function',
      definitionCaptureComplete:typeof native==='function'&&!JSON.stringify(definition).includes('"nativeType":"capture_omitted"')});
    }
   }
  }
 }
 const exactGuid='99F8745496EB419B803BF408A1A3B97A',exactCaption='处理识别中的图片';
 const search={scope:{root:'$.page.list',exactGuid,exactCaption},limits:{depth:10,nodes:12000,keysPerObject:400,matches:20},
  visitedNodes:0,matchedCount:0,matches:[],omissionCount:0,omissions:[],searchComplete:!!pages};
 const searchSeen=new WeakSet(),omit=(path,reason)=>{search.searchComplete=false;search.omissionCount++;if(search.omissions.length<40)search.omissions.push({path,reason});};
 const record=(value,path,matchedFields)=>{
  search.matchedCount++;if(search.matches.length>=search.limits.matches){omit(path,'match_limit');return;}
  const definition=typeof native==='function'?native(value,'','metadata','recognitionNavigationExactDefinition.'+path):null;
  search.matches.push({path,matchedFields,definition,definitionCaptureAvailable:typeof native==='function',
   definitionCaptureComplete:typeof native==='function'&&!JSON.stringify(definition).includes('"nativeType":"capture_omitted"')});
 };
 const walk=(value,path,depth)=>{
  if(!value||typeof value!=='object'||searchSeen.has(value))return;
  if(search.visitedNodes>=search.limits.nodes){omit(path,'node_limit');return;}
  if(depth>search.limits.depth){omit(path,'depth_limit');return;}
  if(typeof Node==='function'&&value instanceof Node){omit(path,'DOM_not_traversed');return;}
  searchSeen.add(value);search.visitedNodes++;
  let keys;try{keys=Object.keys(value);}catch(error){omit(path,'keys_unavailable');return;}
  if(keys.length>search.limits.keysPerObject)omit(path,'key_limit');
  const children=[],matchedFields=[];
  for(const key of keys.slice(0,search.limits.keysPerObject)){
   let desc;try{desc=Object.getOwnPropertyDescriptor(value,key);}catch(error){omit(path+'['+JSON.stringify(key)+']','property_unavailable');continue;}
   if(!desc||!('value'in desc)){omit(path+'['+JSON.stringify(key)+']','accessor_not_read');continue;}
   const child=desc.value,childPath=path+'['+JSON.stringify(key)+']';
   if(child===exactGuid||child===exactCaption)matchedFields.push({field:key,value:child});
   if(key===exactGuid)record(child,childPath,[{field:'object_key',value:key}]);
   if(child&&typeof child==='object')children.push([child,childPath]);
  }
  if(matchedFields.length)record(value,path,matchedFields);
  for(const [child,childPath]of children)walk(child,childPath,depth+1);
 };
 if(pages&&typeof pages==='object')walk(pages,'$.page.list',0);
 let fpDescriptor=null;try{if(typeof window!=='undefined')fpDescriptor=Object.getOwnPropertyDescriptor(window,'fp_clz');}catch(error){}
 const fpSource=fpDescriptor&&'value'in fpDescriptor&&typeof fpDescriptor.value==='function'?bounded(Function.prototype.toString.call(fpDescriptor.value)):null;
 const observedFunctions={fp_clz:{scope:'window own data property',available:!!fpSource,
  accessorNotRead:!!fpDescriptor&&!('value'in fpDescriptor),source:fpSource,sourceCaptureComplete:typeof fpSource==='string'}};
 return{schema:'ustc-finance-recognition-navigation-observation-v1',locator:{selector,texts:labels},
  candidateCount:matches.length,captureComplete:matches.length<=limit&&omittedFields===0&&candidates.every(x=>x.attributesComplete&&(!x.clickHandlersAvailable||x.clickHandlersComplete)),omittedCandidates:Math.max(0,matches.length-limit),omittedFields,
  parentLimit,candidates,selectedCandidate:null,queriedBackend:false,executed:false,
  nativeDefinitions:{scope:{windows:'all_loaded_windows',caption:'处理识别中的图片'},loadedPageDefinitionsAvailable:!!pages,
   definitionCount,captureComplete:!!pages&&definitionCount<=limit&&definitions.every(x=>x.definitionCaptureComplete),
   omittedDefinitions:Math.max(0,definitionCount-limit),definitions},nativeDefinitionSearch:search,observedFunctions};
})()'''


def loaded_buttons_capture_script():
    """有界盘点已加载 DOM 与窗口按钮定义；读取函数源码，不调用按钮或业务函数。"""
    return r'''(()=>{
 const limits={controls:600,pages:80,windows:240,definitions:600,handlers:10,string:4096};
 const omissions=[];let omissionCount=0;
 const omit=(path,reason)=>{omissionCount++;if(omissions.length<40)omissions.push({path,reason});};
 const own=(o,k,path)=>{if(!o||!['object','function'].includes(typeof o))return undefined;const d=Object.getOwnPropertyDescriptor(o,k);
  if(d&&!('value'in d)){omit(path,'accessor_not_read');return undefined;}return d?.value;};
 const bounded=(v,path)=>{if(typeof v==='string'&&v.length>limits.string){omit(path,'string_limit');return{nativeType:'capture_omitted',reason:'string_limit',path};}return v;};
 const captured=(v,path)=>typeof native==='function'?native(v,'','metadata',path):null;
 const incomplete=v=>!!v&&typeof v==='object'&&(v.nativeType==='capture_omitted'||v.nativeType==='shared_or_circular_reference'||Object.values(v).some(incomplete));
 const array=(o,k,path)=>{const v=own(o,k,path);if(v!==undefined&&!Array.isArray(v))omit(path,'array_unavailable');return Array.isArray(v)?v:[];};
 const scalar=(v,path)=>{if(v===undefined||v===null)return null;if(typeof v==='string'||typeof v==='number'&&Number.isFinite(v))return bounded(String(v),path);omit(path,'non_scalar_not_coerced');return null;};
 const definitions=[],jquery=typeof $==='undefined'?null:$;
 const pages=own(own(jquery,'page','$.page'),'list','$.page.list');
 if(!pages||typeof pages!=='object')omit('$.page.list','definitions_unavailable');
 let windowCount=0,definitionCount=0;
 const pageKeys=pages&&typeof pages==='object'?Object.keys(pages):[];
 if(pageKeys.length>limits.pages)omit('$.page.list','page_limit');
 for(const key of pageKeys.slice(0,limits.pages)){
  const path='$.page.list['+JSON.stringify(key)+']',page=own(pages,key,path),wins=array(page,'wins',path+'.wins');
  for(let i=0;i<wins.length;i++){
   if(windowCount++>=limits.windows){omit(path+'.wins','window_limit');break;}
   const win=own(wins,String(i),path+'.wins['+i+']'),winno=scalar(own(win,'winno',path+'.winno'),path+'.winno');
   for(const group of ['title_btns','func_btns']){
    const list=array(win,group,path+'.wins['+i+'].'+group);
    for(let j=0;j<list.length;j++){
     if(definitionCount++>=limits.definitions){omit(path+'.'+group,'definition_limit');break;}
     const p=path+'.wins['+i+'].'+group+'['+j+']',button=own(list,String(j),p);
     if(!button||typeof button!=='object'){omit(p,'definition_unavailable');continue;}
     const definition=captured(button,p);
     definitions.push({path:p,page_key:key,owner_winno:winno,group,
      guid:scalar(own(button,'btnguid',p+'.btnguid'),p+'.btnguid'),
      caption:scalar(own(button,'caption',p+'.caption'),p+'.caption'),
      definition,definition_capture_complete:definition!==null&&!incomplete(definition)});
    }
   }
  }
 }
 const definitionScopeComplete=omissionCount===0&&definitions.every(d=>d.definition_capture_complete);
 const nodes=document.querySelectorAll('a,button,[onclick],[role="button"],.iconBtn'),controls=[];
 if(nodes.length>limits.controls)omit('DOM.buttons','control_limit');
 for(let i=0;i<Math.min(nodes.length,limits.controls);i++){
  const e=nodes[i],p='DOM.buttons['+i+']',controlOmissionStart=omissionCount,attr=k=>bounded(e.getAttribute(k),p+'.'+k);
  let owner=null,grid=null,parent=e;
  for(let depth=0;parent&&depth<10;depth++,parent=parent.parentElement){
   const ids=String(parent.id||'')+' '+String(parent.className||'');
   owner=owner||ids.match(/(?:^|\s)(?:foot_)?winWF_YBX_([0-9]+)(?:_|\s|$)/)?.[1]||null;
   grid=grid||ids.match(/(?:^|\s)(?:t_)?gridWF_YBX_([0-9]+)(?:_|\s|$)/)?.[1]||null;
  }
  const guid=attr('guid'),btnname=attr('btnname'),text=bounded((e.textContent||'').trim(),p+'.text');
  const exact=typeof guid==='string'&&guid?definitions.filter(d=>d.guid===guid):[];
  const matched=exact.length?exact:definitions.filter(d=>owner&&d.owner_winno===owner&&typeof btnname==='string'&&btnname&&d.caption===btnname);
  let handlers=null,handlerCount=null;
  try{const data=own(jquery,'_data',p+'.$._data');if(typeof data==='function'){
   const events=Reflect.apply(data,jquery,[e,'events']),clicks=own(events,'click',p+'.handlers.click');
   if(Array.isArray(clicks)){handlerCount=clicks.length;if(clicks.length>limits.handlers)omit(p+'.handlers','handler_limit');
    handlers=[];for(let j=0;j<Math.min(clicks.length,limits.handlers);j++)handlers.push(captured(own(clicks,String(j),p+'.handlers['+j+']'),p+'.handlers['+j+']'));}
  }else omit(p+'.handlers','handlers_unavailable');}catch(error){omit(p+'.handlers','handlers_unavailable');}
  controls.push({tag:e.tagName,id:e.id||'',text,btnname,guid,title:attr('title'),onclick:attr('onclick'),href:attr('href'),
   visible:!!e.getClientRects().length,disabled:typeof e.disabled==='boolean'?e.disabled:null,
   enabled:typeof e.disabled==='boolean'?!e.disabled:null,aria_disabled:attr('aria-disabled'),
   owner_winno:owner,grid_funcno:grid,native_click_handlers:handlers,click_handler_count:handlerCount,
   native_definition_paths:matched.map(d=>d.path),definition_binding:matched.length===1?(exact.length?'exact_guid':'owner_caption_unique'):matched.length?'ambiguous':'unavailable',
   capture_complete:definitionScopeComplete&&omissionCount===controlOmissionStart&&!incomplete(handlers)&&![text,btnname,guid].some(incomplete)});
 }
 return{schema:'ustc-loaded-native-buttons-v1',scope:'current_loaded_DOM_and_page_window_definitions',loaded_only:true,
  queried_backend:false,executed:false,limits,control_count:nodes.length,controls,definitions,
  loaded_definitions_available:!!pages,capture_complete:omissionCount===0&&definitions.every(d=>d.definition_capture_complete)&&controls.every(c=>c.capture_complete),omission_count:omissionCount,omissions};
})()'''


def print_shell_capture_script():
    """只读已加载打印壳的源码证据；不读取外部脚本、不点击或执行打印。"""
    return r'''(()=>{
 const limits={scripts:100,scriptUnits:512000,snippets:28,snippetUnits:6000,globalKeys:5000,functions:32,functionUnits:16000};
 const result={schema:'ustc-finance-print-shell-observation-v1',scope:'current main document and fixed same-origin iframepage_print; loaded inline sources and own print functions only',budget_scope:'per_document',
  queried_backend:false,external_sources_read:false,functions_called:false,buttons_clicked:false,window_print_called:false,
  native_call_chain_verified:false,limits,documents:[],omissions:[],capture_complete:true};
 const omit=(path,reason)=>{result.capture_complete=false;if(result.omissions.length<100)result.omissions.push({path,reason});};
 const own=(o,k,path)=>{try{const d=Object.getOwnPropertyDescriptor(o,k);if(d&&!('value'in d)){omit(path,'accessor_not_read');return;}return d?.value;}catch{omit(path,'property_unavailable');}};
 const pathOnly=(value,base=location.href)=>{try{const u=new URL(value,base);return u.protocol==='https:'?u.origin+u.pathname:u.protocol==='about:'?'about:blank':null;}catch{return null;}};
 const anchors=/DY打印|0DEE7AADF0CB4F8F8E3F4173D877F71B|bx_frame_print\.jsp|bx_dtl\.jsp|PDFViewerApplication|\b[A-Za-z_$][\w$]*(?:print|pdf)[\w$]*\s*\(|\b(?:print|pdf)[\w$]*\s*\(/gi;
 // 取代码邻域时遇到认证或会话初始化片段则整段省略，不把页面凭据带进证据。
 const protectedSource=/password|passwd|WFServiceToken|SSNID|(?:access|refresh|session)[_-]?token|authorization|document\s*\.\s*cookie|userContext\s*=/i;
 let scriptCount=0,snippetCount=0,functionCount=0;
 function observe(view,doc,label){
  let documentScriptCount=0,documentSnippetCount=0,documentFunctionCount=0;
  const row={label,url:pathOnly(view.location.href),scripts:[],functions:[],viewer_application_present:false};result.documents.push(row);
  row.viewer_application_present=!!own(view,'PDFViewerApplication',label+'.PDFViewerApplication');
  const scripts=Array.from(doc.scripts||[]);
  for(let i=0;i<scripts.length;i++){
   const path=label+'.scripts['+i+']';if(documentScriptCount>=limits.scripts){omit(path,'script_count_limit');break;}documentScriptCount++;scriptCount++;
   const script=scripts[i],src=script.getAttribute('src');
   if(src){row.scripts.push({index:i,kind:'external',url:pathOnly(src,view.location.href),source_available:false});continue;}
   const text=script.textContent||'',entry={index:i,kind:'inline',source_units:text.length,scan_complete:text.length<=limits.scriptUnits,snippets:[]};
   if(!entry.scan_complete)omit(path,'inline_source_scan_limit');
   const scanned=text.slice(0,limits.scriptUnits);anchors.lastIndex=0;let match,lastEnd=-1;
   while((match=anchors.exec(scanned))!==null){
    if(match.index<lastEnd)continue;
    if(documentSnippetCount>=limits.snippets){omit(path,'snippet_count_limit');break;}
    const start=Math.max(0,match.index-2000),end=Math.min(scanned.length,start+limits.snippetUnits),source=scanned.slice(start,end);lastEnd=end;
    if(protectedSource.test(source)){omit(path,'protected_source_window_not_read');continue;}
    entry.snippets.push({matched:match[0],start,end,source,complete_script:start===0&&end===text.length});documentSnippetCount++;snippetCount++;
   }
   if(entry.snippets.length||!entry.scan_complete)row.scripts.push(entry);
  }
  let keys;try{keys=Object.getOwnPropertyNames(view);}catch{omit(label,'global_names_unavailable');return;}
  if(keys.length>limits.globalKeys)omit(label,'global_name_limit');
  // init_bx_qrbxxx_56为已观察主壳的直接调用；优先保留它与原生开页函数，避免历史打印辅助函数挤占证据。
  const priorityFunctions=['init_bx_qrbxxx_56','pageOpen','pageOpen_v1','print'];
  const names=keys.slice(0,limits.globalKeys).filter(key=>/print|pdf|(?:^|_)dy(?:_|$)/i.test(key)||priorityFunctions.includes(key));
  const rank=name=>priorityFunctions.includes(name)?priorityFunctions.indexOf(name):priorityFunctions.length;
  names.sort((a,b)=>rank(a)-rank(b));
  for(const key of names){
   const path=label+'.'+key,value=own(view,key,path);if(typeof value!=='function')continue;
   if(documentFunctionCount>=limits.functions){omit(path,'function_count_limit');break;}documentFunctionCount++;functionCount++;
   let source;try{source=Function.prototype.toString.call(value);}catch{omit(path,'function_source_unavailable');continue;}
   if(protectedSource.test(source)){omit(path,'protected_function_source_not_read');continue;}
   const complete=source.length<=limits.functionUnits;if(!complete)omit(path,'function_source_limit');
   row.functions.push({name:key,source:source.slice(0,limits.functionUnits),source_units:source.length,capture_complete:complete});
  }
 }
 // 两个固定文档各自限额，主页面脚本/全局较多时仍能读取当前单据的已加载打印壳。
 const frame=document.getElementById('iframepage_print');
 if(frame){try{const view=frame.contentWindow,doc=frame.contentDocument;
   if(!view||!doc||view.location.origin!==location.origin)omit('iframepage_print','same_origin_document_unavailable');
   else observe(view,doc,'iframepage_print');
  }catch{omit('iframepage_print','frame_unavailable');}}
 observe(window,document,'main');
 result.script_count_observed=scriptCount;result.snippet_count=snippetCount;result.function_count=functionCount;
 return result;
})()'''


def snapshot_script():
    """固定读取代码；调用者不能提供 JS/URL/SQL，沿用静默 runtime 和身份边界。"""
    from finance_invoice_verification import NATIVE_SERIALIZER_JS, SNAPSHOT
    return r'''(async()=>{
 const base=await BASE_SNAPSHOT;
 if(base.ok!==true)return base;
 const baseSerialization=base.serialization;
 const vis=e=>!!(e&&e.getClientRects().length);
 const protect=(key,value)=>/password|passwd|token|session|captcha|\byzm\b|\botp\b|csrf|authorization|cookie|formWF_YBX_1362_t-code/i.test(key)?'[protected]':
  /account|bankacnt|cnt1|cardno|acnt/i.test(key)&&typeof value==='string'?value.replace(/\b\d{12,19}\b/g,m=>m.slice(0,4)+'****'+m.slice(-4)):value;
 const mask=protect;
 __BOUNDED_NATIVE_SERIALIZER__
 const native=nativeValue;
 const control=e=>({tag:e.tagName,id:e.id,name:e.name||'',type:e.type||'',text:(e.textContent||'').trim(),btnname:e.getAttribute('btnname'),
  value:protect(e.id+' '+e.name,e.value),checked:typeof e.checked==='boolean'?e.checked:null,
  disabled:!!e.disabled,readOnly:!!e.readOnly,visible:vis(e),onclick:e.getAttribute('onclick'),href:e.getAttribute('href'),
  title:e.getAttribute('title'),options:e.options?[...e.options].map(o=>({text:o.text,value:o.value,selected:o.selected,disabled:o.disabled})):null});
 const grids=[];
 for(const[funcno,def]of Object.entries($.Q.list||{})){
  const id=$.Q.idFunc.getGridID(funcno),e=document.getElementById(id),initialized=!!e?.grid;
  let g=base.grids.find(x=>String(x.funcno)===funcno);
  if(!g){
   g={funcno,id,columns:(def.colModel||[]).map((c,i)=>({name:c.name,label:def.colNames?.[i]||''})),
    columnDefinitions:native(def.colModel||[],'','metadata','grids.'+funcno+'.columnDefinitions'),columnNames:native(def.colNames||[],'','core','grids.'+funcno+'.columnNames'),rows:[],rawRows:[],rowActionControls:[]};
   if(initialized){
    const ids=$(e).jqGrid('getDataIDs');
    g.rows=ids.map(rowId=>native($(e).jqGrid('getRowData',rowId),'','core','grids.'+funcno+'.rows.'+rowId));
    g.rawRows=ids.map(rowId=>({rowId,nativeData:native($(e).jqGrid('getLocalRow',rowId)||null,'','core','grids.'+funcno+'.rawRows.'+rowId)}));
    g.rowActionControls=ids.map(rowId=>({rowId,controls:[...(document.getElementById(rowId)?.querySelectorAll('a,button,[onclick]')||[])].map(control)}));
   }
  }
  g.initialized=initialized;g.visible=vis(e);g.title=def.title||'';
  g.loadedDefinition=native(def,'','metadata','grids.'+funcno+'.loadedDefinition'); // 当前页面已加载定义，不查询接口。
  if(initialized){
   const p=k=>$(e).jqGrid('getGridParam',k),ids=$(e).jqGrid('getDataIDs');
   g.records=p('records');g.rowCount=g.rows.length;g.loading=!!e.grid.hDiv.loading;
   g.currentPage=p('page');g.lastPage=p('lastpage');g.allRecordsVisible=!g.loading&&g.rowCount===g.records;
   g.runtimeColumnDefinitions=native(p('colModel'),'','metadata','grids.'+funcno+'.runtimeColumnDefinitions');
   g.nativeRowEvents={onSelectRow:native(p('onSelectRow'),'','metadata','grids.'+funcno+'.onSelectRow'),onCellSelect:native(p('onCellSelect'),'','metadata','grids.'+funcno+'.onCellSelect'),ondblClickRow:native(p('ondblClickRow'),'','metadata','grids.'+funcno+'.ondblClickRow')};
   g.nativeQuery={url:def.url,datatype:p('datatype'),prmNames:native(p('prmNames'),'','metadata','grids.'+funcno+'.prmNames')};
   g.selection={multiselect:!!p('multiselect'),selectedRowId:p('selrow'),selectedRowIds:native(p('selarrrow')||[],'','core','grids.'+funcno+'.selectedRowIds'),
    rowCheckboxes:ids.map(rowId=>({rowId,controls:[...(document.getElementById(rowId)?.querySelectorAll('input[type="checkbox"]')||[])].map(control)})),
    headerCheckboxes:[...(e.grid.hDiv?.querySelectorAll('input[type="checkbox"]')||[])].map(control)};
  }else{g.records=null;g.rowCount=0;g.loading=null;g.allRecordsVisible=false;g.selection=null;}
  grids.push(g);
 }
 base.grids=grids;
 base.forms=Object.entries($.F?.list||{}).map(([funcno,f])=>{
  const id=$.F.idFunc.getFormID(funcno),element=document.getElementById(id);
  return{funcno,id,title:f.title||'',visible:vis(element),loadedDefinition:native(f,'','metadata','forms.'+funcno+'.loadedDefinition'),fields:(f.formFields||[]).map(field=>{
   const e=document.getElementById(id+'_'+field.id);
   return{...native(field,'','metadata','forms.'+funcno+'.fields.'+field.id),id:field.id,fieldname:field.fieldname,label:field.label,control:e&&!['password'].includes(e.type)?control(e):null};
  })};
 });
 base.pageControls=[...document.querySelectorAll('a,button,[onclick],h3,span,input,select,textarea')]
  .filter(e=>e.type!=='password'&&((e.matches('input,select,textarea'))||vis(e)))
  .map(control).filter(x=>x.type||x.id||x.onclick||x.text&&x.text.length<100);
 base.recognitionNavigationCandidates=RECOGNITION_NAVIGATION_CAPTURE;
 base.loadedButtons=LOADED_BUTTONS_CAPTURE;
 base.printShellObservation=PRINT_SHELL_CAPTURE;
 base.frames=[...document.querySelectorAll('iframe')].map((frame,index)=>{
  try{const d=frame.contentDocument;if(!d)return{index,id:frame.id,inaccessible:true};
   return{index,id:frame.id,visible:vis(frame),location:frame.contentWindow.location.origin+frame.contentWindow.location.pathname,
    controls:[...d.querySelectorAll('input,select,textarea,a,button,[onclick]')].filter(e=>e.type!=='password').map(control)};
  }catch(error){return{index,id:frame.id,inaccessible:true,error:error.name};}
 });
 base.previewDiagnostics={visibilityState:document.visibilityState,
  iframe:[...document.querySelectorAll('iframe')].map(e=>({id:e.id,src:e.getAttribute('src'),readyState:e.contentDocument?.readyState,outerHTML:e.outerHTML.slice(0,2000),contentHTML:e.contentDocument?.documentElement?.outerHTML.slice(0,3000)})),
  pageDefinition:native(Object.fromEntries(Object.entries($.page.list['WF_YBX_41']||{}).filter(([k])=>['mainWinno','funcMap','actMap','pageName','funcs','projID','target','onload','afterload'].includes(k))),'','metadata','preview.pageDefinition'),
  updateHandler:native(window.after_apply_list_update||null,'','metadata','preview.updateHandler'),
  resources:performance.getEntriesByType('resource').filter(e=>e.name.startsWith(location.origin+'/YBX/')).slice(-40).map(e=>({path:new URL(e.name).pathname,type:e.initiatorType,duration:e.duration,responseStatus:e.responseStatus||null}))};
 base.serial=$.UC.bindData('#61-serialno#');base.node=$.UC.bindData('#61-node_id#');
 base.uploadTracking=native(window.__reimbursementUploadTracking||null,'','core','uploadTracking');
 base.mode='read_only_loaded_finance_surface';base.system_write=false;
 base.scope={loadedDefinitionsOnly:true,queriedBackend:false,paginationReloaded:false,includesUninitializedDefinitions:true};
 base.baseSerialization=baseSerialization;
 return finalizeCapture(base);
})()'''.replace('BASE_SNAPSHOT', SNAPSHOT.rstrip().rstrip(';')).replace('__BOUNDED_NATIVE_SERIALIZER__', NATIVE_SERIALIZER_JS).replace('RECOGNITION_NAVIGATION_CAPTURE', recognition_navigation_capture_script()).replace('LOADED_BUTTONS_CAPTURE', loaded_buttons_capture_script()).replace('PRINT_SHELL_CAPTURE', print_shell_capture_script())


def display_text(value):
    return html.unescape(re.sub(r'<[^>]*>', '', str(value))).strip() if value is not None else ''


def receipt_body(receipt):
    body = receipt
    # runtime envelope and read-only recovery envelope may each wrap result.
    for _ in range(3):
        if isinstance(body, dict) and isinstance(body.get('grids'), list):
            break
        body = body.get('result') if isinstance(body, dict) else None
    if not isinstance(body, dict) or body.get('ok') is not True or not isinstance(body.get('grids'), list):
        raise ValueError('须为成功且含已观察 grids 的只读回执；pending/unknown 不证明页面内容')
    if body.get('projid') not in [None, 'WF_YBX']:
        raise ValueError('不是已核对的 WF_YBX 财务回执')
    return body


def parse_recognition_error(message):
    """保留完整原文；masked 身份不在工具中补成某个人的姓名。"""
    text = display_text(message)
    reserved = '票据重复性检查失败' in text and ('已预约' in text or '预约单号' in text)
    found = []
    for match in re.finditer(r'预约单号[：:]\s*([0-9]+)\s*[,，]\s*操作人[：:]\s*(.*?)\s*[,，]\s*操作时间[：:]\s*([0-9]{4}-[0-9]{2}-[0-9]{2}(?:[ T][0-9:]+)?)', text):
        operator = match.group(2).strip()
        identity = re.fullmatch(r'(.*?)\(([A-Za-z0-9]+)\)', operator)
        found.append({'reservation_number': match.group(1),
                      'operator_masked': identity.group(1).strip() if identity else operator,
                      'operator_id': identity.group(2) if identity else None,
                      'operation_date': match.group(3)})
    file_codes = re.findall(r'文件编号\(([A-Za-z0-9]+)\)', text)
    return {'full_message': message, 'display_text': text,
            'error_type': 'duplicate_reserved_invoice' if reserved else 'reported_error' if text else None,
            'reservations': found, 'file_codes': file_codes,
            'reimbursement_status': 'unknown', 'payment_status': 'unknown'}


ALIASES = {
    'invoice_number': ['t.tk_code_abst', 'tk_code_abst', 't.tk_no', 'tk_no'],
    'invoice_content': ['t.tk_display_abst', 'tk_display_abst'],
    'remarks': ['t.paper_notes', 'paper_notes'], 'total_amount': ['t.tk_total_amt', 'tk_total_amt'],
    'school_status': ['t.auth_rst_desc', 'auth_rst_desc'], 'image_number': ['t.show_id', 't.show_seq', 'show_id', 'show_seq'],
    'seller': ['t.tk_salename', 'tk_salename'], 'sharer': ['t.share_sname', 'share_sname'],
    'share_information': ['t.share_remark', 'share_remark'], 'invoice_date': ['t.tk_date', 'tk_date'],
    'certification_date': ['t.auth_date', 'auth_date'], 'native_guid': ['t.tk_guid', 'tk_guid'],
    'native_main_guid': ['t.tk_mainguid', 'tk_mainguid'], 'invoice_type': ['t.tk_type', 'tk_type'],
    'bound_serial': ['t.serial_no', 'serial_no'], 'native_mod_flag': ['t.tk_mod_flag', 'tk_mod_flag'],
    'queue_status': ['t.show_status', 'show_status'], 'recognition_error': ['t.show_err_msg', 'show_err_msg'],
    'image_filename': ['t.show_filename', 'show_filename'], 'file_code': ['t.filesequcode', 'filesequcode'],
    'native_file_reference': ['t.tk_filecode', 'tk_filecode'], 'native_edit_flag': ['t.canedit', 'canedit'],
}


def capture_incomplete(value):
    if isinstance(value, dict):
        return value.get('nativeType') in ['capture_omitted', 'shared_or_circular_reference'] or any(capture_incomplete(v) for v in value.values())
    return isinstance(value, list) and any(capture_incomplete(v) for v in value)


def captured_list(value):
    return value if isinstance(value, list) else []


def normalize_grid(grid):
    definitions = {x['name']: x for x in captured_list(grid.get('columnDefinitions')) if isinstance(x, dict) and 'name' in x}
    runtime = {x['name']: x for x in captured_list(grid.get('runtimeColumnDefinitions')) if isinstance(x, dict) and 'name' in x}
    labels = {x['name']: x.get('label', '') for x in grid.get('columns', []) if isinstance(x, dict) and 'name' in x}
    raw_rows = grid.get('rawRows') or []
    rows = grid.get('rows') or []
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError('grid.rows 须保持原生行对象')
    names = list(dict.fromkeys([*labels, *definitions, *runtime,
                                *(key for row in rows for key in row),
                                *(key for row in raw_rows if isinstance(row.get('nativeData'), dict) for key in row['nativeData'])]))
    columns = []
    for name in names:
        definition, current = definitions.get(name), runtime.get(name)
        hidden = current.get('hidden') if current and isinstance(current.get('hidden'), bool) else (
            not definition['isshow'] if definition and isinstance(definition.get('isshow'), bool) else None)
        columns.append({'name': name, 'label': labels.get(name, ''), 'hidden': hidden,
                        'visibility_basis': 'jqGrid.colModel.hidden' if current and isinstance(current.get('hidden'), bool) else
                        'Q.colModel.isshow' if definition and isinstance(definition.get('isshow'), bool) else 'unavailable',
                        'definition': definition, 'runtime_definition': current})
    normalized_rows = []
    action_rows = grid.get('rowActionControls') or []
    for index, row in enumerate(rows):
        raw_entry = raw_rows[index] if index < len(raw_rows) else {}
        raw = raw_entry.get('nativeData') if isinstance(raw_entry.get('nativeData'), dict) else None
        row_id = raw_entry.get('rowId') or (action_rows[index].get('rowId') if index < len(action_rows) else None)
        fields = {name: {'display_available': name in row, 'display_value': row.get(name),
                         'display_text': display_text(row[name]) if name in row else None,
                         'raw_available': raw is not None and name in raw, 'raw_value': raw.get(name) if raw else None,
                         'display_capture_complete': name in row and not capture_incomplete(row.get(name)),
                         'raw_capture_complete': raw is not None and name in raw and not capture_incomplete(raw.get(name))}
                  for name in names}
        semantic = {key: [{'column': name, **fields[name]} for name in aliases if name in fields]
                    for key, aliases in ALIASES.items()}
        def text(key):
            return next((item['display_text'] for item in semantic[key] if item['display_available']), '')
        candidates = sorted({n for item in semantic['invoice_number'] for representation in [item['display_text'] if item['display_capture_complete'] else None, item['raw_value'] if item['raw_capture_complete'] else None]
                             for n in re.findall(r'(?<![0-9])[0-9]{20}(?![0-9])', display_text(representation))})
        error = parse_recognition_error(next((item['display_value'] for item in semantic['recognition_error'] if item['display_available']), ''))
        filename = text('image_filename')
        filename_sha = re.fullmatch(r'([a-fA-F0-9]{64})\.[A-Za-z0-9]+', filename)
        school_status = text('school_status')
        normalized_rows.append({'row_index': index, 'row_id': row_id, 'fields': fields, 'semantic': semantic,
            'invoice_number_candidates': candidates, 'recognition_error': error,
            'source_link': {'filename_sha256_candidate': filename_sha.group(1).lower() if filename_sha else None,
                            'byte_hash_verified': False, 'file_code': text('file_code'),
                            'native_file_reference': text('native_file_reference'), 'native_main_guid': text('native_main_guid')},
            'state_dimensions': {'queue_status_reported': text('queue_status'), 'school_status_reported': school_status,
                'school_certification': 'reported_certified' if '已认证' in school_status else 'unknown',
                'duplicate_reservation': 'reported_reserved' if error['error_type'] == 'duplicate_reserved_invoice' else 'unknown',
                'bound_serial_reported': text('bound_serial'), 'native_mod_flag_reported': text('native_mod_flag'),
                'national_tax_verification': 'unknown', 'red_void_status': 'unknown',
                'reimbursement_status': 'unknown', 'payment_status': 'unknown'},
            'actions': action_rows[index].get('controls', []) if index < len(action_rows) else [],
            'raw_row_available': raw is not None,
            'raw_unavailable_reason': None if raw is not None else raw_entry.get('unavailableReason', 'native_local_row_cache_unavailable_or_not_captured')})
    records, loading = grid.get('records'), grid.get('loading')
    initialized = grid.get('initialized', True)
    complete = initialized is True and loading is False and type(records) is int and records == len(rows)
    amounts = []
    for row in normalized_rows:
        value = next((x['display_text'] for x in row['semantic']['total_amount'] if x['display_available']), '')
        if value:
            try:
                amount = Decimal(value.replace(',', ''))
                if amount.is_finite(): amounts.append(amount)
            except InvalidOperation:
                pass
    return {'funcno': str(grid.get('funcno', '')), 'id': grid.get('id'), 'title': grid.get('title'),
            'visible': grid.get('visible'), 'initialized': initialized, 'columns': columns, 'rows': normalized_rows,
            'native_query_definition': grid.get('nativeQuery'), 'loaded_definition': grid.get('loadedDefinition'),
            'native_row_events': grid.get('nativeRowEvents', grid.get('rowEventHandlers')),
            'selection': grid.get('selection'),
            'completeness': {'scope': '当前登录人、当前原生筛选条件的已加载 grid；不代表全校',
                'records_reported': records, 'rows_observed': len(rows), 'row_count_reported': grid.get('rowCount'),
                'loading': loading, 'current_page': grid.get('currentPage'), 'last_page': grid.get('lastPage'),
                'complete_for_current_filter': complete,
                'reason': 'all_current_filter_records_loaded' if complete else 'loading' if loading else
                          'not_initialized' if not initialized else 'pagination_or_count_unavailable'},
            'loaded_rows_amount': {'sum': str(sum(amounts, Decimal('0'))) if amounts else None,
                                  'amount_rows': len(amounts), 'scope': '仅已加载行的显示金额，非本案可报总额'}}


def capability_for(control, has_recognition=False):
    text = display_text(control.get('btnname') or control.get('text', '')).replace(' ', '')
    if control.get('type') == 'checkbox': return 'select_rows'
    if control.get('id') == 'gridWF_YBX_113_refresh': return 'refresh_recognition'
    names = {'返回首页': 'view_home', '已使用发票': 'view_used', '【点击查看明细】': 'view_recognition',
             '【上传发票】': 'open_upload', '上传发票': 'open_upload', '前往报销': 'create_draft',
             '批量删除': 'delete_images', '删除': 'delete_images', '重新识别': 'rerecognize',
             '手工标注': 'manual_label', '发票备注信息': 'invoice_remarks', '修改': 'edit_invoice',
             '保存发票': 'save_upload', '打开原图': 'open_original_image',
             '向左转': 'image_rotate', '向右转': 'image_rotate', '适合': 'image_zoom', '放大': 'image_zoom', '缩小': 'image_zoom'}
    if text == '返回' and has_recognition: return 'view_pool'
    return names.get(text, 'unmapped_native_control')


CAPABILITIES = {
    'view_home': {'system_write': False, 'support': 'existing_typed_tool', 'route': {'method': 'invoice_verification', 'action': 'view', 'view': 'home'}},
    'view_used': {'system_write': False, 'support': 'existing_typed_tool', 'route': {'method': 'invoice_verification', 'action': 'view', 'view': 'used'}},
    'view_recognition': {'system_write': False, 'support': 'existing_typed_tool', 'route': {'method': 'invoice_verification', 'action': 'view', 'view': 'recognition'}},
    'view_pool': {'system_write': False, 'support': 'existing_typed_tool', 'route': {'method': 'invoice_verification', 'action': 'view', 'view': 'pool'}},
    'open_upload': {'system_write': False, 'support': 'existing_typed_tool', 'route': {'operation': 'invoice', 'action': 'open-upload'}},
    'save_upload': {'system_write': True, 'support': 'existing_case_guarded_tool', 'route': {'operation': 'invoice', 'action': 'save-upload'}},
    'create_draft': {'system_write': True, 'support': 'existing_case_guarded_tool', 'route': {'operation': 'invoice', 'action': 'create-draft'}, 'requires': ['已核对的选择清单/GUID/金额', '本案建单授权', '确认无旧单或旧创建请求']},
    'select_rows': {'system_write': False, 'support': 'existing_tool_requires_case_sources',
                    'route': {'method': 'invoice_selection', 'action': 'preview', 'grid': '53'},
                    'requires': ['同案正式source_job_id/source_artifact_id及audit_refs', '完整票号/GUID唯一匹配', '真实完整selarrrow及身份/上下文', 'apply另需明确本批授权及批准preview；只勾选不建单']},
    'invoice_remarks': {'system_write': True, 'support': 'native_unimplemented', 'route': None},
    'edit_invoice': {'system_write': True, 'support': 'native_unimplemented', 'route': None},
    'edit_travel': {'system_write': True, 'support': 'existing_case_guarded_tool',
        'route': {'method':'invoice_edit','action':'inspect'},
        'requires': ['同案invoice_edit_manifest及SHA、原件与人员/行程依据',
                     '已认证但提示出行人不能为空，未选且未绑定报销单',
                     '原生必填姓名、学工号、出发/到达日期及舱位逐项有据；当前限一个原生行程行',
                     'run串行打开、行程维护、字段检查、保存、返回及独立完整票池核对；未知不重放']},
    'delete_images': {'system_write': True, 'support': 'existing_tool_requires_case_sources',
        'route': {'method': 'invoice_retry', 'action': 'inspect'}, 'workflow_action': 'cleanup',
        'requires': ['53仅处理已核对的本案异常池记录；113仅清理本账号预约失败上传条目',
                     '同案原件SHA、完整53/113回执及精确目标；保留完整错误/预约归档',
                     'inspect→preview→cleanup→独立inspect→confirm→新完整回读→readback；不释放他人预约']},
    'rerecognize': {'system_write': True, 'support': 'existing_tool_requires_case_sources',
        'route': {'method': 'invoice_retry', 'action': 'inspect'}, 'workflow_action': 'reidentify',
        'requires': ['113单张无错待识别记录；完整53不存在同票号；原件SHA与filecode精确对应',
                     '预约错误不得作为无错待识别重试；已有异目标勾选不得清除',
                     'inspect→preview→reidentify→独立inspect→confirm→新完整回读→readback']},
    'refresh_recognition': {'system_write': False, 'support': 'existing_tool_requires_case_sources',
        'route': {'method': 'invoice_retry', 'action': 'inspect'}, 'workflow_action': 'refresh',
        'requires': ['113已观察的原生刷新图标及绑定handler；明确账号',
                     'inspect→preview→refresh→新完整回读→readback；刷新不等于重新识别']},
    'manual_label': {'system_write': True, 'support': 'native_unimplemented', 'route': None},
    'open_original_image': {'system_write': False, 'support': 'native_unimplemented', 'route': None},
    'image_rotate': {'system_write': False, 'support': 'native_unimplemented', 'route': None},
    'image_zoom': {'system_write': False, 'support': 'native_unimplemented', 'route': None},
    'unmapped_native_control': {'system_write': None, 'support': 'observed_only', 'route': None},
}


def observed_operations(body, grids):
    inventory = body.get('loadedButtons') if isinstance(body.get('loadedButtons'), dict) else {}
    buttons = captured_list(inventory.get('controls'))
    definitions = captured_list(inventory.get('definitions'))
    controls = [(c.get('grid_funcno'), None, c) for c in buttons if isinstance(c, dict)]
    captured_ids = {c.get('id') for c in buttons if isinstance(c, dict) and c.get('id')}
    controls.extend((None, None, c) for c in body.get('pageControls', body.get('controls', []))
                    if not c.get('id') or c.get('id') not in captured_ids)
    for entry in definitions:
        if not isinstance(entry, dict): continue
        controls.append((None, None, {'tag': 'NATIVE_WINDOW_DEFINITION', 'id': entry.get('guid'),
            'text': entry.get('caption'), 'owner_winno': entry.get('owner_winno'), 'visible': None,
            'enabled': None, 'definition_path': entry.get('path'), 'definition': entry.get('definition'),
            'capture_complete': entry.get('definition_capture_complete') is True}))
    for grid in grids:
        for row in grid['rows']:
            controls.extend((grid['funcno'], row['row_id'], c) for c in row['actions'])
        selection = grid.get('selection') or {}
        for row in selection.get('rowCheckboxes', []):
            controls.extend((grid['funcno'], row.get('rowId'), c) for c in row.get('controls', []))
        controls.extend((grid['funcno'], None, c) for c in selection.get('headerCheckboxes', []))
        for column in grid['columns']:
            for button in captured_list((column.get('definition') or {}).get('attachbtn')):
                if not isinstance(button, dict) or 'caption' not in button: continue
                controls.append((grid['funcno'], None, {'tag': 'NATIVE_COLUMN_DEFINITION', 'id': button.get('btnguid'),
                    'text': button.get('caption'), 'visible': None, 'column': column['name'], 'definition': button}))
    operations, seen = [], set()
    for grid_id, row_id, control in controls:
        if not isinstance(control, dict): continue
        capability = capability_for(control, any(g['funcno'] == '113' and g['initialized'] and g['visible'] is not False for g in grids))
        if capability == 'edit_invoice' and str(grid_id) == '53' and row_id:
            row = next((r for g in grids if g['funcno']=='53' for r in g['rows'] if r['row_id']==row_id),None)
            status = row.get('state_dimensions',{}).get('school_status_reported','') if row else ''
            if isinstance(status,str) and status.startswith('正常(已认证)') and '出行人不能为空' in status:
                capability = 'edit_travel'
        # Static text is retained as pageControls; it is not offered as a clickable operation.
        if capability == 'unmapped_native_control' and not (control.get('onclick') is not None or control.get('href') or control.get('tag') in ['BUTTON', 'A', 'IMG', 'NATIVE_COLUMN_DEFINITION', 'NATIVE_WINDOW_DEFINITION']):
            continue
        locator = {'grid_funcno': grid_id, 'row_id': row_id, 'tag': control.get('tag'),
                   'id': control.get('id'), 'text': control.get('text'), 'column': control.get('column')}
        if control.get('owner_winno') is not None: locator['owner_winno'] = control['owner_winno']
        if control.get('definition_path'): locator['definition_path'] = control['definition_path']
        fingerprint = json.dumps(locator, sort_keys=True, ensure_ascii=False)
        if fingerprint in seen: continue
        seen.add(fingerprint)
        contract = CAPABILITIES[capability].copy()
        retry_grid = str(grid_id) if str(grid_id) in ['53', '113'] else {'39': '53', '79': '113'}.get(control.get('owner_winno'))
        if capability == 'refresh_recognition': retry_grid = '113'
        if isinstance(contract.get('route'), dict) and contract['route'].get('method') == 'invoice_retry':
            # 标题文本相同不足以证明是同一个原生业务按钮；缺归属仍只显示能力，不给可调用路由。
            if retry_grid and (capability == 'delete_images' or retry_grid == '113'):
                contract['route'] = {**contract['route'], 'grid': retry_grid}
            else:
                contract['route'] = None
                contract['requires'] = [*contract.get('requires', []), '当前控件缺少已核对53/113归属，先重新取得surface/inspect']
        paths = control.get('native_definition_paths', [])
        native_conditions = [{'definition_path': d.get('path'), 'conditions': {
            k: d['definition'][k] for k in ['pre_check', 'check_func', 'clickquery', 'clickqueryhint', 'call_proc', 'acts', 'procs']
            if k in d['definition']}} for d in definitions if isinstance(d, dict) and d.get('path') in paths and isinstance(d.get('definition'), dict)]
        operations.append({'operation_key': 'op_' + hashlib.sha256(fingerprint.encode()).hexdigest()[:20],
            'capability': capability, **contract, 'observed_control': control, 'locator': locator,
            'implementation_status': 'implemented' if contract['support'].startswith('existing_') else 'discovered_only',
            'ready_to_execute': False, 'loaded_only': True, 'native_preconditions': native_conditions,
            'capture_complete': control.get('capture_complete') is not False and not capture_incomplete(control),
            'availability': 'disabled' if control.get('disabled') or control.get('aria_disabled') == 'true' else 'hidden' if control.get('visible') is False else
                            'definition_only' if control.get('tag') in ['NATIVE_COLUMN_DEFINITION', 'NATIVE_WINDOW_DEFINITION'] else 'observed',
            'executed': False})
    return operations


def normalize(receipt, source=None):
    body = receipt_body(receipt)
    grids = [normalize_grid(g) for g in body['grids']]
    operations = observed_operations(body, grids)
    return {'schema': SCHEMA, 'ok': True, 'system_write': False, 'executed_operations': [],
            'source': source, 'identity': body.get('identity'), 'current_page': body.get('currentPage'),
            'serial': body.get('serial'), 'node': body.get('node'), 'observation_time': body.get('observedAt'),
            'scope': {'permission': '当前登录人已加载原生页面与定义', 'backend_enumerated': False,
                      'pagination_fetched': False, 'full_school_claim': False},
            'serialization': {'base': body.get('baseSerialization'), 'current': body.get('serialization'),
                              'truncations_are_explicit': True},
            'grids': grids, 'forms': body.get('forms', []), 'page_controls': body.get('pageControls', body.get('controls', [])),
            'recognition_navigation_candidates': body.get('recognitionNavigationCandidates'),
            'loaded_buttons': body.get('loadedButtons'),
            'print_shell': body.get('printShellObservation'),
            'frames': body.get('frames', []), 'upload_tracking': body.get('uploadTracking'),
            'operations': operations,
            'page_overview': {'grids': [{'funcno': g['funcno'], 'visible': g['visible'], 'initialized': g['initialized'],
                'field_count': len(g['columns']), 'row_count': len(g['rows'])} for g in grids],
                'operation_groups': {key: [o['operation_key'] for o in operations if o['implementation_status'] == key]
                                     for key in ['implemented', 'discovered_only']},
                'availability_groups': {key: [o['operation_key'] for o in operations if o['availability'] == key]
                                        for key in ['observed', 'hidden', 'disabled', 'definition_only']},
                'discovery_is_execution_authority': False, 'queried_backend': False},
            'case_tool_routes': {'upload': {'operation': 'invoice', 'action': 'upload'},
                'edit_basic': {'operation': 'stage', 'stage': 'basic'}, 'edit_funding': {'operation': 'stage', 'stage': 'funding'},
                'edit_expenses': {'operation': 'stage', 'stage': 'expenses'}, 'edit_payments': {'operation': 'stage', 'stage': 'payments'},
                'edit_explanation': {'operation': 'stage', 'stage': 'notes'},
                'correct_returned_invoice': {'operation': 'correction', 'action': 'check'},
                'submit': {'operation': 'submission', 'action': 'inspect'}},
            'state_rule_reference': 'docs/发票识别认证与预约占用逻辑.md'}


def make_plan(receipt, operation_key, case_id, tab_id, source=None):
    if not isinstance(case_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', case_id):
        raise ValueError('明确合法 case_id')
    if not isinstance(tab_id, str) or not re.fullmatch(r'[0-9]+', tab_id):
        raise ValueError('tab_id 须为当前标准工具取得的数字字符串')
    surface = normalize(receipt, source)  # 不信任调用者塞入的 operations/route；只从原回执重新归一化。
    matches = [op for op in surface['operations'] if op['operation_key'] == operation_key]
    if len(matches) != 1:
        raise ValueError('operation_key 必须来自同案已观察回执，不能猜按钮或提供 JS/URL')
    operation = matches[0]
    locator = operation['locator']
    target_grid = next((g for g in surface['grids'] if g['funcno'] == locator['grid_funcno']), None)
    target_row = next((r for r in target_grid['rows'] if r['row_id'] == locator['row_id']), None) if target_grid and locator['row_id'] else None
    target = {'grid_funcno': locator['grid_funcno'], 'row_id': locator['row_id'],
              'selection_observed': target_grid['selection'] if target_grid else None,
              'invoice_number_candidates': target_row['invoice_number_candidates'] if target_row else [],
              'native_guid': target_row['semantic']['native_guid'] if target_row else [],
              'native_main_guid': target_row['semantic']['native_main_guid'] if target_row else [],
              'file_code': target_row['source_link']['file_code'] if target_row else None,
              'image_number': target_row['semantic']['image_number'] if target_row else [],
              'state_dimensions': target_row['state_dimensions'] if target_row else None,
              'current_grid_complete': target_grid['completeness']['complete_for_current_filter'] if target_grid else None}
    route = operation['route']
    request = None
    required_inputs = []
    if route and operation['availability'] == 'observed' and operation['capture_complete'] and not capture_incomplete(target):
        if route.get('method'):
            request = {'method': route['method'], 'params': {'case_id': case_id, 'tab_id': tab_id,
                **{k: v for k, v in route.items() if k != 'method'}, 'request_id': 'review-before-new-request'}}
        else:
            request = {'method': 'enqueue', 'params': {'case_id': case_id, 'operation': route['operation'],
                'parameters': {k: v for k, v in route.items() if k != 'operation'} | {'tab_id': tab_id},
                'request_id': 'review-before-new-request', 'execute': False}}
    if route and route.get('method') == 'invoice_retry':
        required_inputs = ['expected_userid', 'source_job_id', 'source_artifact_id', 'pool_job_id', 'pool_artifact_id',
                           'recognition_job_id', 'recognition_artifact_id', 'inspection_job_id', 'inspection_artifact_id', 'targets']
        who = (surface.get('identity') or {}).get('userid')
        if request and isinstance(who, str) and re.fullmatch(r'[A-Za-z0-9_.@-]{1,80}', who):
            request['params'].update(expected_userid=who, execute=False)
        else:
            request = None
    if route and route.get('method') == 'invoice_edit':
        required_inputs = ['本案invoice_edit_manifest及来源SHA','姓名/学工号/出发和到达日期/舱位的逐项依据']
        who=(surface.get('identity') or {}).get('userid')
        if request and len(target['invoice_number_candidates'])==1 and isinstance(who,str) and re.fullmatch(r'[A-Za-z0-9_.@-]{1,80}',who):
            request['params'].update(invoice_number=target['invoice_number_candidates'][0],expected_userid=who,execute=False)
        else:request=None
    if operation['capability'] == 'select_rows':
        required_inputs = ['source_job_id', 'source_artifact_id', 'audit_refs']
        selectors = None
        if target_row and not capture_incomplete(target):
            if len(target['invoice_number_candidates']) == 1:
                selectors = [{'invoice_number': target['invoice_number_candidates'][0]}]
            else:
                guid = next((item['display_text'] for item in target['native_guid'] if item['display_capture_complete'] and item['display_text']), None)
                if guid: selectors = [{'native_guid': guid}]
        if selectors and operation['capture_complete'] and operation['availability'] == 'observed':
            request = {'method': 'invoice_selection', 'params': {'case_id': case_id, 'action': 'preview',
                'tab_id': tab_id, 'request_id': 'review-before-new-request', 'grid': locator['grid_funcno'],
                'mode': 'remove' if operation['observed_control'].get('checked') is True else 'append',
                'selectors': selectors, 'execute': False}}
            if source and source.get('job_id') and source.get('artifact_id'):
                request['params'].update(snapshot_job_id=source['job_id'], snapshot_artifact_id=source['artifact_id'])
        else:
            request = None;required_inputs.append('selectors')
    return {'schema': SCHEMA + '-plan', 'ok': True, 'case_id': case_id, 'tab_id': tab_id, 'source': source,
            'operation': operation, 'target': target, 'execute': False, 'system_write': False, 'browser_action_performed': False,
            'proposed_request': request, 'required_case_inputs': required_inputs, 'ready_to_execute': False,
            'next': '审核本案来源/当前视图/授权/原生入口及工具参数后，单独调用既有工具；本计划不点击、不保存、不建单、不提交'}


def detail_script(receipt, operation_key, case_id, tab_id):
    """只允许已确认的只读原生入口；修改/存储过程绝不被视为详情。"""
    from finance_invoice_verification import navigation
    plan = make_plan(receipt, operation_key, case_id, tab_id)
    operation = plan['operation']
    if not operation['capture_complete'] or operation['availability'] != 'observed' or operation['capability'] != 'view_recognition':
        raise ValueError('本次 read-detail 仅适配已观察到的原生识别明细入口；修改/删除/未知行事件不可作只读详情')
    return navigation('recognition')


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as file:
        path.chmod(0o600)
        json.dump(value, file, ensure_ascii=False, indent=2)
        file.write('\n')


def read_source(path):
    path = Path(path).resolve()
    content = path.read_bytes()
    return json.loads(content), {'path': str(path), 'sha256': hashlib.sha256(content).hexdigest()}


def snapshot(tab_id, output):
    if not isinstance(tab_id, str) or not re.fullmatch(r'[0-9]+', tab_id):
        raise ValueError('tab-id 须来自本次标准 tabs/access 回执')
    output = Path(output)
    native_output = output.with_name(output.stem + '.native.json')
    script = output.with_suffix('.js')
    if any(p.exists() for p in [output, native_output, script]):
        raise ValueError('保留旧字段回执，使用新的 output/request_id')
    output.parent.mkdir(parents=True, exist_ok=True)
    with script.open('x', encoding='utf-8') as file:
        script.chmod(0o600)
        file.write(snapshot_script())
    process = subprocess.run([sys.executable, str(ROOT/'tools/edge_runtime.py'), str(script),
        '--tab-id', tab_id, '--output', str(native_output), '--quiet'])
    if native_output.exists():
        native_output.chmod(0o600)
        receipt, source = read_source(native_output)
        result = {**receipt, 'surface_source': source}
        try:
            result['surface'] = normalize(receipt, source)
        except ValueError as error:
            result['surface_unavailable'] = str(error)
        write_new(output, result)  # 保留原始 result_marker；超时只 recover，不重发采集。
        return process.returncode or (1 if 'surface_unavailable' in result else 0)
    return process.returncode or 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    cmd = commands.add_parser('snapshot')
    cmd.add_argument('--tab-id', required=True)
    cmd.add_argument('--output', type=Path, required=True)
    for action in ['normalize', 'plan', 'detail']:
        cmd = commands.add_parser(action)
        cmd.add_argument('--receipt', type=Path, required=True)
        cmd.add_argument('--output', type=Path, required=True)
        if action in ['plan', 'detail']:
            cmd.add_argument('--operation-key', required=True)
            cmd.add_argument('--case-id', required=True)
            cmd.add_argument('--tab-id', required=True)
            cmd.add_argument('--source-job-id')
            cmd.add_argument('--source-artifact-id')
    args = parser.parse_args()
    if args.command == 'snapshot': return snapshot(args.tab_id, args.output)
    receipt, source = read_source(args.receipt)
    if args.command in ['plan', 'detail']:
        source.update(job_id=args.source_job_id, artifact_id=args.source_artifact_id)
    if args.command == 'detail':
        script = args.output.with_suffix('.js')
        if args.output.exists() or script.exists(): raise ValueError('保留旧详情回执，使用新的 output/request_id')
        code = detail_script(receipt, args.operation_key, args.case_id, args.tab_id)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with script.open('x', encoding='utf-8') as file:
            script.chmod(0o600)
            file.write(code)
        return subprocess.run([sys.executable, str(ROOT/'tools/edge_runtime.py'), str(script),
            '--tab-id', args.tab_id, '--output', str(args.output), '--quiet']).returncode
    result = normalize(receipt, source) if args.command == 'normalize' else make_plan(receipt, args.operation_key, args.case_id, args.tab_id, source)
    write_new(args.output, result)
    print(json.dumps({'ok': True, 'system_write': False, 'output': str(args.output.resolve())}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
