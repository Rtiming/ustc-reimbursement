/* 打开日常报销发票池；实际票据及占用状态须独立回读。 */
(()=>{
 const request=__POOL_CONTEXT__,vis=e=>!!e?.getClientRects().length;
 if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX')throw new Error('财务系统不符');
 if(request.mode==='diagnose-pool'){
  if(!/^[A-Za-z0-9]{2,40}$/.test(request.expected_userid)||$.UC.bindData('#0-userinfo.userid#')!==request.expected_userid||
   !['6','33'].includes(String($.page.currPage))||String($.UC.bindData('#61-serialno#')||'').trim())throw Error('票池诊断仅限同账号、空单号的已加载page6/33');
  const capture={complete:true,nodes:0,units:0,omissionCount:0,omissions:[],limits:{nodes:12000,units:600000,depth:10,entries:150,keys:200,string:32000}},seen=new WeakMap();
  const omitted=(path,reason)=>{capture.complete=false;capture.omissionCount++;if(capture.omissions.length<60)capture.omissions.push({path,reason});return{nativeType:'capture_omitted',path,reason};};
  const native=(value,path,depth=0)=>{
   if(++capture.nodes>capture.limits.nodes||capture.units>capture.limits.units)return omitted(path,'budget');
   if(depth>capture.limits.depth)return omitted(path,'depth');
   if(typeof value==='undefined')return{nativeType:'undefined'};
   if(typeof value==='function')return{nativeType:'function',source:native(Function.prototype.toString.call(value),path+'.source',depth+1)};
   if(typeof value==='string'){
    if(value.length>capture.limits.string)return omitted(path,'string_limit');
    capture.units+=value.length;return value;
   }
   if(value===null||typeof value==='number'||typeof value==='boolean')return value;
   if(typeof value!=='object')return{nativeType:typeof value};
   if(typeof Node==='function'&&value instanceof Node)return{nativeType:'DOM',id:value.id||null,tag:value.nodeName};
   if(value===window)return{nativeType:'Window'};
   if(seen.has(value))return{nativeType:'shared_reference',sourcePath:seen.get(value)};seen.set(value,path);
   const keys=Object.keys(value),limit=Array.isArray(value)?capture.limits.entries:capture.limits.keys,result=Array.isArray(value)?[]:{};
   if(keys.length>limit)omitted(path,'entries');
   for(const key of keys.slice(0,limit)){
    capture.units+=key.length+12;
    if(capture.nodes>=capture.limits.nodes||capture.units>=capture.limits.units){omitted(path,'budget');break;}
    const d=Object.getOwnPropertyDescriptor(value,key),child=path+'['+JSON.stringify(key)+']';
    result[key]=/password|cookie|authorization|secret|token/i.test(key)?'[protected]':d&&'value'in d?native(d.value,child,depth+1):omitted(child,'accessor_not_read');
   }return result;
  };
  const own=(object,key,path)=>{const d=object&&Object.getOwnPropertyDescriptor(object,key);return!d?{nativeType:'not_loaded'}:'value'in d?native(d.value,path):omitted(path,'accessor_not_read');};
  const functions={func_sy_jump:own(window,'func_sy_jump','window.func_sy_jump'),page_open:own($.page,'open','$.page.open')};
  const pages=['WF_YBX_6','WF_YBX_33'].map(key=>({key,definition:own($.page.list,key,'$.page.list.'+key)}));
  const visibleButtons=[...document.querySelectorAll('button')].filter(vis),buttonLimit=50;
  if(visibleButtons.length>buttonLimit)omitted('visibleButtons','entry_limit');
  const buttons=visibleButtons.slice(0,buttonLimit).map((e,index)=>({index,id:e.id||null,guid:e.getAttribute('guid'),btnname:e.getAttribute('btnname'),
   text:native((e.textContent||'').trim(),'buttons.'+index+'.text'),disabled:!!e.disabled,onclick:native(e.getAttribute('onclick'),'buttons.'+index+'.onclickAttribute'),onclickProperty:own(e,'onclick','buttons.'+index+'.onclick')}));
  const grids=['53','113'].map(funcno=>{const e=document.getElementById('gridWF_YBX_'+funcno);return{funcno,loaded:!!e?.grid,visible:vis(e),
   loading:e?.grid?e.grid.hDiv?.loading??null:null,definition:own($.Q?.list,funcno,'$.Q.list.'+funcno)};});
  const elements=['winWF_YBX_6','winWF_YBX_33','winWF_YBX_33_head','formWF_YBX_45','formWF_YBX_84'].map(id=>{const e=document.getElementById(id);return{id,loaded:!!e,visible:vis(e)};});
  const dialogs=[...document.querySelectorAll('.ui-dialog')].filter(vis).map(e=>({title:e.querySelector('.ui-dialog-title')?.textContent?.trim()||null,
   buttons:[...e.querySelectorAll('button')].filter(vis).map(b=>(b.textContent||'').trim())}));
  const editableInputs=[...document.querySelectorAll('input,select,textarea')].filter(e=>vis(e)&&!e.disabled&&!e.readOnly&&!['hidden','password','button','submit','reset'].includes(e.type));
  // Layout rectangles alone do not prove that an iframe is painted. Only inspect
  // the outer DOM/CSS; never read its URL, document, contents, or event handlers.
  const iframeLimits={frames:20,ancestors:24,rects:8,domString:512};
  const domString=(value,path)=>typeof value==='string'&&value.length>iframeLimits.domString?omitted(path,'dom_string_limit'):native(value,path);
  const rectangle=r=>Object.fromEntries(['x','y','top','left','right','bottom','width','height'].map(key=>[key,Number.isFinite(r[key])?r[key]:null]));
  const nodeEvidence=(e,path)=>{
   let computedStyle=null,clientRects=[],boundingRect=null,rectsComplete=true;
   try{const s=getComputedStyle(e);computedStyle=Object.fromEntries(['display','visibility','opacity','contentVisibility','overflowX','overflowY','position','clip','clipPath'].map(key=>[key,domString(s[key]??null,path+'.computedStyle.'+key)]));}
   catch{omitted(path+'.computedStyle','computed_style_unavailable');}
   try{const rects=[...e.getClientRects()];if(rects.length>iframeLimits.rects){rectsComplete=false;omitted(path+'.clientRects','rect_limit');}
    clientRects=rects.slice(0,iframeLimits.rects).map(rectangle);boundingRect=rectangle(e.getBoundingClientRect());}
   catch{rectsComplete=false;omitted(path+'.clientRects','rects_unavailable');}
   return{id:domString(e.id||null,path+'.id'),tag:domString(e.nodeName||null,path+'.tag'),
    class:domString(e.getAttribute('class'),path+'.class'),hiddenAttribute:e.hasAttribute('hidden'),ariaHidden:domString(e.getAttribute('aria-hidden'),path+'.ariaHidden'),
    computedStyle,clientRects,boundingRect,rectsComplete};
  };
  const allIframes=[...document.querySelectorAll('iframe')];
  if(allIframes.length>iframeLimits.frames)omitted('iframes','iframe_limit');
  const iframes=allIframes.slice(0,iframeLimits.frames).map((frame,index)=>{
   const path='iframes.'+index,element=nodeEvidence(frame,path+'.element'),ancestors=[];
   let parent=frame.parentElement;
   while(parent&&ancestors.length<iframeLimits.ancestors){ancestors.push(nodeEvidence(parent,path+'.ancestors.'+ancestors.length));parent=parent.parentElement;}
   const ancestorChainComplete=!parent;if(parent)omitted(path+'.ancestors','ancestor_depth');
   const nodes=[element,...ancestors],paintSuppressionEvidence=[];
   nodes.forEach((node,level)=>{const s=node.computedStyle;if(!s)return;
    // visibility may be overridden by descendants; the iframe's computed value
    // is authoritative for that property. Ancestor opacity/display affect it.
    for(const property of ['display','opacity',...(level===0?['visibility']:[]),...(level>0?['contentVisibility']:[])]){
     const value=s[property];if(property==='display'&&value==='none'||property==='opacity'&&typeof value==='string'&&value.trim()!==''&&Number(value)===0||
      property==='visibility'&&['hidden','collapse'].includes(value)||property==='contentVisibility'&&value==='hidden')paintSuppressionEvidence.push({level,id:node.id,property,value});
    }
   });
   return{index,element,ancestors,ancestorChainComplete,layoutRectsPresent:element.clientRects.length>0,
    positiveAreaRect:element.clientRects.some(r=>r.width>0&&r.height>0),paintSuppressionEvidence,
    evidenceComplete:ancestorChainComplete&&nodes.every(node=>node.computedStyle&&node.rectsComplete)};
  });
  return{ok:true,action:'diagnose_daily_invoice_entry',case_id:request.case_id,projid:$.getProjID(),identity:{userid:request.expected_userid},
   currentPage:String($.page.currPage),serial:null,documentReadyState:document.readyState,functions,pages,grids,elements,buttons,dialogs,iframes,iframeCount:allIframes.length,iframeLimits,
   editableInputCount:editableInputs.length,uploadTrackingActive:!!(window.__reimbursementUploadTracking&&!window.__reimbursementUploadTracking.done),
   system_write:false,client_state_changed:false,navigationRequested:false,clickRequested:false,saveRequested:false,capture};
 }
 if(request.mode==='recover-empty-entry'){
  const main='https://cwzh.ustc.edu.cn/YBX/main.jsp';
  if(!/^[A-Za-z0-9]{2,40}$/.test(request.expected_userid)||$.UC.bindData('#0-userinfo.userid#')!==request.expected_userid||
   location.href!==main||String($.page.currPage)!=='6'||String($.UC.bindData('#61-serialno#')||'').trim())throw Error('空入口恢复须同账号、固定main.jsp、page6和空单号');
  if([...document.querySelectorAll('.ui-dialog')].some(vis)||[...document.querySelectorAll('iframe')].some(vis)||
   [...document.querySelectorAll('input,select,textarea')].some(e=>vis(e)&&!e.disabled&&!e.readOnly&&!['hidden','password','button','submit','reset'].includes(e.type)))throw Error('仍有窗口、预览或可编辑现场，不能重载');
  if([...document.querySelectorAll('[id^="gridWF_YBX_"]')].some(e=>e.grid||vis(e))||
   ['formWF_YBX_45','formWF_YBX_84','formWF_YBX_84_t-img','gridWF_YBX_53','gridWF_YBX_113'].some(id=>document.getElementById(id)))throw Error('原生表格、填写或上传控件仍已加载，不能重载');
  if(window.__reimbursementUploadTracking)throw Error('仍有上传追踪，不能按空入口重载');
  const buttons=[...document.querySelectorAll('button')].filter(vis),expected={
   'E2FA7F84957945D19CCEB50A371E3DD0':'下一步','6B8865442D0A4BFC845554195686735B':'返回'};
  if(buttons.length!==2||new Set(buttons.map(e=>e.getAttribute('guid'))).size!==2||buttons.some(e=>
   expected[e.getAttribute('guid')]!==(e.textContent||'').trim()||e.disabled||e.getAttribute('aria-disabled')==='true'))throw Error('并非已核的空入口两按钮现场');
  const result={ok:true,action:'recover_empty_invoice_entry',case_id:request.case_id,identity:{userid:request.expected_userid},
   currentPage:'6',serial:null,preflightPassed:true,system_write:false,saveRequested:false,abandonRequested:false,
   execute:request.execute===true,navigationRequested:request.execute===true,client_state_changed:request.execute===true,
   requiresIndependentReadback:request.execute===true,no_replay:true};
  if(request.execute===true)location.reload();
  return result;
 }
 if(request.mode==='cancel-leave'){
  if(!/^[A-Za-z0-9]{2,40}$/.test(request.expected_userid)||$.UC.bindData('#0-userinfo.userid#')!==request.expected_userid||
   String($.page.currPage)!=='6'||String($.UC.bindData('#61-serialno#')||'').trim())throw Error('仅取消本次同账号、未建单page6离开提示');
  const dialogs=[...document.querySelectorAll('.ui-dialog')].filter(vis);
  if(dialogs.length!==1)throw Error('离开填写报销单窗口不唯一');
  const dialog=dialogs[0],titles=[...dialog.querySelectorAll('.ui-dialog-title')].filter(vis);
  const buttons=[...dialog.querySelectorAll('button')].filter(vis),caption=e=>(e.textContent||'').trim();
  const labels=buttons.map(caption).sort(),expected=['确定','取消','保存草稿','放弃报销'].sort();
  if(titles.length!==1||caption(titles[0])!=='离开填写报销单'||
   !(dialog.textContent||'').includes('该笔报销存入草稿箱，可继续修改报销')||
   labels.length!==expected.length||labels.some((value,index)=>value!==expected[index]))throw Error('离开提示标题、内容或完整按钮集合变化');
  const cancel=buttons.filter(e=>caption(e)==='取消');
  if(cancel.length!==1||cancel[0].disabled||cancel[0].getAttribute('aria-disabled')==='true')throw Error('取消按钮不可唯一使用');
  cancel[0].click();
  return{ok:true,action:'cancel_leave_invoice_entry',case_id:request.case_id,identity:{userid:request.expected_userid},
   currentPage:'6',serial:null,system_write:false,client_state_changed:true,cancelRequested:true,
   saveRequested:false,abandonRequested:false,requiresIndependentReadback:true};
 }
 if(request.mode!=='open-pool')throw Error('未支持的固定票池入口动作');
 $.C.setValue('0','FUNC_CODE','BX_01_1_');
 if(func_sy_jump()===false)throw new Error('日常报销导航拒绝');
 $.page.open('33');return{ok:true,action:'open_daily_invoice_pool'};
})()
