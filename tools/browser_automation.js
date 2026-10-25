/* USTC WF_YBX 原生控件驱动。参数来自案件配置，宿主可为 Edge 或扩展 MAIN world。 */
(function(root){
 'use strict';
 const $=root.$,delay=ms=>new Promise(r=>setTimeout(r,ms));
 const visible=e=>!!e&&e.getClientRects().length>0;
 const one=(es,message)=>{if(es.length!==1)throw new Error(message);return es[0];};
 const cents=s=>{s=String(s).replaceAll(',','').trim();if(!/^\d+(?:\.\d{1,2})?$/.test(s))throw new Error('金额格式错误');const[a,b='']=s.split('.');return Number(a)*100+Number(b.padEnd(2,'0'));};
 function guard(c,write=true){if(c.system_mode&&c.system_mode!=='daily')throw new Error('系统模式尚未实测接通：'+c.system_mode);if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX'||!/^\d+$/.test(c.serial)||$.UC.bindData('#61-serialno#')!==c.serial)throw new Error('业务系统或单据身份不符');let user=c.expected_userid;if(c.payments?.some(p=>p.mode==='corporate_transfer')){const users=new Set(c.payments.map(p=>p.expected_userid));if(users.size!==1||!c.payments.every(p=>p.mode==='corporate_transfer')||typeof c.payments[0].expected_userid!=='string'||!/^[A-Za-z0-9]+$/.test(c.payments[0].expected_userid))throw new Error('对公支付须有本案一致的明确登录人且不得混合支付');const corporateUser=c.payments[0].expected_userid;if(user&&user!==corporateUser)throw new Error('对公支付登录人配置冲突');user=corporateUser;}if(user&&$.UC.bindData('#0-userinfo.userid#')!==user)throw new Error('当前登录人不符');if(write&&visible(document.querySelector('.foot_winWF_YBX_76 button[btnname="同意"]')))throw new Error('当前为申请人审核页，不能继续按草稿填写');}
 async function wait(test,message){for(let i=0;i<80;i++){if(test())return;await delay(100);}throw new Error(message);}
 function click(scope,caption){one([...scope.querySelectorAll('button')].filter(e=>visible(e)&&(e.getAttribute('btnname')===caption||e.innerText.trim()===caption)),'原生按钮不唯一：'+caption).click();}
 function dialog(fieldId){return one([...document.querySelectorAll('.ui-dialog')].filter(e=>visible(e)&&e.querySelector('#'+fieldId)),'原生窗口不唯一：'+fieldId);}
 function set(id,value){const e=document.getElementById(id);if(!visible(e))throw new Error('字段未加载：'+id);if(e.readOnly||e.disabled)throw new Error('字段不可编辑：'+id);if(e.options&&!Array.from(e.options).some(o=>o.value===value))throw new Error('字段选项变化：'+id);$(e).val(value).trigger('change').trigger('blur');return e;}
 function inspectHome(c){
  const serial=$.UC.bindData('#61-serialno#'),emptySerial=[undefined,null,''].includes(serial);
  if(!emptySerial)guard(c,false);
  else if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX'||!/^[0-9]+$/.test(c.serial)||c.system_mode&&c.system_mode!=='daily')throw Error('首页入口诊断业务身份不符');
  if(!/^[A-Za-z0-9]{2,40}$/.test(c.expected_userid||'')||$.UC.bindData('#0-userinfo.userid#')!==c.expected_userid)throw Error('首页入口诊断须本次明确登录人');
  const own=(object,key,limit=2048)=>{
   const d=object&&Object.getOwnPropertyDescriptor(object,key);
   if(!d)return{present:false};
   if(!Object.hasOwn(d,'value'))return{present:true,kind:'accessor',value_type:'unread',value:null};
   const value=d.value,type=value===null?'null':typeof value;
   return{present:true,kind:'data',value_type:type,
    value:type==='string'?value.slice(0,limit):['boolean','null'].includes(type)||type==='number'&&Number.isFinite(value)?value:null,
    truncated:type==='string'&&value.length>limit};
  };
  const heads=[...document.querySelectorAll('h2')],inputs=[...document.querySelectorAll('input,select,textarea')],
   dialogs=[...document.querySelectorAll('.ui-dialog')].filter(visible),frames=[...document.querySelectorAll('iframe')];
  if(heads.length>100||inputs.length>3000||dialogs.length>50||frames.length>32)throw Error('首页诊断页面规模超出有界清点范围');
  const headers=heads.filter(e=>e.textContent.trim()==='中国科学技术大学').map(e=>{
   const handler=e.getAttribute('onclick');return{caption:'中国科学技术大学',visible:visible(e),onclick:typeof handler==='string'?handler.slice(0,4096):null,
    onclick_truncated:typeof handler==='string'&&handler.length>4096};
  });
  const visibleInputs=inputs.filter(e=>visible(e)&&!['hidden','password','button','submit','reset'].includes(e.type));
  const inputMetadata=e=>{
   const node=x=>({tag:String(x.tagName||'').slice(0,40),id:String(x.id||'').slice(0,200),class:String(x.getAttribute?.('class')||'').slice(0,300)}),ancestors=[];
   let parent=e.parentElement;for(let depth=0;parent&&depth<8;depth++,parent=parent.parentElement)ancestors.push(node(parent));
   return{...node(e),name:String(e.name||'').slice(0,200),type:e.type||'',disabled:!!e.disabled,read_only:!!e.readOnly,
    form:e.form?{tag:String(e.form.tagName||'').slice(0,40),id:String(e.form.id||'').slice(0,200),name:String(e.form.name||'').slice(0,200)}:null,
    ancestors,ancestors_truncated:!!parent};
  };
  const previews=frames.filter(e=>e.id==='iframepage_print').map(f=>{
   try{
    const doc=f.contentDocument,text=doc?.body?.innerText||'',bounded=text.slice(0,262144),serials=[...bounded.matchAll(/报销单号[:：]\s*([0-9]+)/g)].map(x=>x[1]);
    return{id:f.id,visible:visible(f),accessible:true,url:f.contentWindow.location.origin+f.contentWindow.location.pathname,
     document_ready_state:doc?.readyState||null,serials:serials.slice(0,8),serial_scan_truncated:text.length>262144||serials.length>8};
   }catch(error){return{id:f.id,visible:visible(f),accessible:false,error:String(error.name)};}
  });
  const descriptor=Object.getOwnPropertyDescriptor(root,'__reimbursementUploadTracking'),track=descriptor&&Object.hasOwn(descriptor,'value')?descriptor.value:null;
  const tracking=Object.fromEntries(['dispatchRequested','saveRequested','done'].map(key=>[key,own(track,key)]));
  const uploader=$('#formWF_YBX_84_t-img').data('uploadifive');
  const dataObject=(object,key)=>{const d=object&&Object.getOwnPropertyDescriptor(object,key);return d&&Object.hasOwn(d,'value')&&typeof d.value==='object'?d.value:null;};
  return{ok:true,action:'inspect_native_home_entry',serial:c.serial,current_serial:serial??null,serial_state:emptySerial?'empty':'matches_case',identity:{userid:c.expected_userid},system_write:false,client_state_changed:false,context_modified:false,
   default_win:own(root,'defaultWin'),current_page:typeof $.page?.currPage==='string'?$.page.currPage.slice(0,64):Number.isSafeInteger($.page?.currPage)?$.page.currPage:null,
   header_count:headers.length,headers,previews,
   editors:{visible_input_count:visibleInputs.length,editable_count:visibleInputs.filter(e=>!e.disabled&&!e.readOnly).length,
    fields:visibleInputs.slice(0,100).map(inputMetadata),truncated:visibleInputs.length>100},
   dialogs:{visible_count:dialogs.length},upload:{tracking_present:!!descriptor,tracking,control_present:!!uploader,
    queue_count:own(dataObject(uploader,'queue'),'count'),current_uploads:own(dataObject(uploader,'uploads'),'current'),errors:own(dataObject(uploader,'uploads'),'errors')},
   active_requests:Number.isSafeInteger($.active)?$.active:null};
 }
 async function closePayments(c){
  const guid='3BB7925A6F164D0AA0451CE177A7E1CF',forms=['19','22','24','245','285','456','597','994','1508','1510','1791','2082','2212','2225'];
  const snapshot=()=>{
   guard(c,false);if(!/^[A-Za-z0-9]{2,40}$/.test(c.expected_userid||'')||$.UC.bindData('#0-userinfo.userid#')!==c.expected_userid||!['6','70'].includes(String($.page.currPage)))throw Error('支付返回须本案同账号同号原生支付上下文6/70');
   if([...document.querySelectorAll('.ui-dialog')].some(visible))throw Error('存在其他对话框，不能丢弃原现场');
   const tracker=root.__reimbursementUploadTracking;
   if(tracker&&(tracker.done!==true||typeof tracker.dispatchRequested!=='boolean'||typeof tracker.saveRequested!=='boolean'))throw Error('原上传状态未明确结束，不能返回');
   const upload=$('#formWF_YBX_84_t-img').data('uploadifive');
   if(upload&&[upload.queue?.count,upload.uploads?.current,upload.uploads?.errors].some(n=>!Number.isSafeInteger(n)||n!==0))throw Error('原生上传队列未明确为空，不能返回');
   const native=[...document.querySelectorAll('input,select,textarea')],all=native.filter(e=>/^formWF_YBX_/.test(e.id));
   if(all.length>2000||new Set(all.map(e=>e.id)).size!==all.length||all.some(e=>typeof e.value!=='string'))throw Error('支付字段全量快照不完整或重复');
   if(native.some(e=>visible(e)&&!['hidden','password','button','submit','reset'].includes(e.type)&&!all.includes(e)))throw Error('有未核定的可见输入，不返回');
   const expected=new Set(),counts=[],rows=[];
   for(const no of forms){
    const def=$.F.list[no],keys=def?.formFields?.map(f=>f.id);
    if(!Array.isArray(keys)||!keys.length||keys.length>50||new Set(keys).size!==keys.length||keys.some(k=>typeof k!=='string'||!/^t-[a-zA-Z0-9_]+$/.test(k))||typeof def.multiRec!=='boolean')throw Error('原生支付类型字段定义不完整');
    const count=def.multiRec?def.multiCount:1;
    if(!Number.isSafeInteger(count)||count<0||count>100)throw Error('原生支付类型完整行数不符');
    counts.push({form:no,count,multi:def.multiRec,keys});
    for(let i=0;i<count;i++){
     const fields=keys.map(key=>{const id='formWF_YBX_'+no+'_'+key+(def.multiRec?'_'+i:'');expected.add(id);return one(all.filter(e=>e.id===id),'原生支付行字段缺失');});
     if(no==='19')continue;
     const amounts=fields.filter(e=>/_t-(?:amount|amt|cashamount|hkamount|gwk_pay_amt|gwk_amt)(?:_\d+)?$/.test(e.id));
     if(!amounts.length)throw Error('原生支付行金额定义未知，不返回');
     const empty=fields.every(e=>e.value===''&&!e.checked);
     if(!empty&&amounts.some(e=>e.value!=='0.00'))throw Error('存在已填支付金额或非整行空白的空金额，不能返回');
     rows.push({form:no,index:i,empty});
    }
   }
   if(all.length!==expected.size||all.some(e=>!expected.has(e.id)))throw Error('存在未核定支付字段，不能返回');
   const field=id=>one(all.filter(e=>e.id===id),'支付总额字段不唯一');
   if(!visible(field('formWF_YBX_19_t-bx_amt'))||cents(field('formWF_YBX_19_t-bx_amt').value)!==cents(c.amount)||cents(field('formWF_YBX_19_t-wt_amt').value)!==cents(c.amount)||field('formWF_YBX_19_t-yt_amt').value!=='0.00'||field('formWF_YBX_19_t-clr_amt').value!=='0.00')throw Error('待报销、未填或已填支付总额状态不符');
   const footer=one([...document.querySelectorAll('.foot_winWF_YBX_13')].filter(visible),'支付编辑器不唯一');
   const buttons=[...document.querySelectorAll('button')].filter(e=>visible(e)&&e.getAttribute('btnname')==='返回');
   const button=one(buttons,'支付原生返回不唯一');
   if(![...footer.querySelectorAll('button')].includes(button)||button.disabled||button.getAttribute('aria-disabled')==='true'||button.getAttribute('guid')!==guid)throw Error('支付返回GUID、归属或启用状态不符');
   const win=one(($.page.list?.WF_YBX_13?.wins||[]).filter(w=>w.winno===13),'原生支付窗口定义不唯一');
   const def=one((win.func_btns||[]).filter(b=>b.btnguid===guid&&b.caption==='返回'),'原生返回定义不唯一');
   const emptyFunction=(fn,arg)=>typeof fn==='function'&&Function.prototype.toString.call(fn).replace(/\s+/g,'')==='function('+arg+'){}';
   if(['pre_compute','pre_check','call_proc','post_check','clickquery','autojump','hasIntf_beforeclick','hasIntf_afterclick'].some(k=>def[k]!==false)||!Array.isArray(def.procs)||def.procs.length||['check_func','affects','jscontent','customFunc','numSql'].some(k=>def[k]!=='')||def.acts!=='1==1:41'||!emptyFunction(def.beforeclick,'op')||!emptyFunction(def.afterclick,'op')||!emptyFunction(def.interfaceFailCallback,'result'))throw Error('原生返回定义不再是已核无保存链');
   const fields=all.map(e=>({id:e.id,value:e.value,checked:!!e.checked,disabled:!!e.disabled,readOnly:!!e.readOnly})).sort((a,b)=>a.id.localeCompare(b.id));
   return{button,rows,fingerprint:JSON.stringify({serial:c.serial,amount:c.amount,counts,fields})};
  };
  const original=snapshot(),last=snapshot();if(original.fingerprint!==last.fingerprint||original.button!==last.button)throw Error('支付字段在返回前变化，保留原现场');
  last.button.click();
  try{
   await wait(()=>{guard(c,false);if($.UC.bindData('#0-userinfo.userid#')!==c.expected_userid)throw Error('返回期间账号变化');const frame=document.getElementById('iframepage_print');return!visible(document.getElementById('formWF_YBX_19_t-bx_amt'))&&![...document.querySelectorAll('.foot_winWF_YBX_13')].some(visible)&&![...document.querySelectorAll('.ui-dialog')].some(visible)&&visible(frame)&&frame.contentDocument?.body.innerText.includes('报销单号：'+c.serial);},'原生返回结果未知，必须独立回读');
  }catch(error){return{ok:false,action:'close_payments_without_save',serial:c.serial,system_write:false,fields_modified:false,saveRequested:false,navigationRequested:true,execution_status:'unknown_verify_before_retry',requiresIndependentReadback:true,requiresReadback:true};}
  return{ok:true,action:'close_payments_without_save',serial:c.serial,system_write:false,fields_modified:false,saveRequested:false,returnedToPreview:true,zero_payment_rows:original.rows.filter(r=>!r.empty).length,empty_payment_rows:original.rows.filter(r=>r.empty).length,requiresIndependentReadback:true,requiresReadback:true};
 }
 async function inspectDraft(c){
  if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX'||!/^\d+$/.test(c.serial)||!c.expected_userid||$.UC.bindData('#0-userinfo.userid#')!==c.expected_userid)throw new Error('草稿系统、单号或登录人不符');
  const g=$('#gridWF_YBX_101');if(!visible(g[0])||g[0].grid.hDiv.loading)throw new Error('草稿列表尚未就绪');
  const x=one(g.jqGrid('getDataIDs').map(id=>({id,r:g.jqGrid('getRowData',id)})).filter(x=>x.r['t.serial_no']===c.serial),'草稿单号不唯一');
  if(x.r['t.status']!=='-99'||x.r['t.bx_typename']!=='日常报销'||cents(x.r['t.total_amt'])!==cents(c.amount)||!x.r['t.REQ_INFO'].startsWith(c.expected_userid+'-'))throw new Error('草稿状态、类型、金额或申请人不符');
  const footer=one([...document.querySelectorAll('.foot_winWF_YBX_71')].filter(visible),'草稿操作区不唯一');
  g.jqGrid('setSelection',x.id,true);if(g.jqGrid('getGridParam','selrow')!==x.id)throw new Error('草稿未唯一选中');
  click(footer,'修改');await wait(()=>document.getElementById('iframepage_print')?.contentDocument.body.innerText.includes('报销单号：'+c.serial),'原草稿预览未恢复');
  guard(c,false);return{ok:true,action:'inspect_existing_draft',serial:c.serial,system_write:false,requiresReadback:true};
 }
 async function recoverPreview(c){
  guard(c,false);if(!c.expected_userid||$.UC.bindData('#0-userinfo.userid#')!==c.expected_userid)throw new Error('预览登录人不符');
  const f=document.getElementById('iframepage_print'),expected='redirectCustomPage.action?url=Customize/customPrint/bx_dtl.jsp';
  if(!visible(f)||f.getAttribute('src')!==expected||f.contentWindow.location.href!=='about:blank'||f.contentDocument.body.innerText.trim())throw new Error('仅恢复已观察到的空白原生预览，不刷新已有内容');
  f.src=expected;await wait(()=>f.contentDocument?.body.innerText.includes('报销单号：'+c.serial),'原生预览重新加载未完成');
  return{ok:true,action:'reload_blank_native_preview',serial:c.serial,system_write:false,requiresReadback:true};
 }
 async function diagnosePreview(c){
  guard(c,false);if(!c.expected_userid||$.UC.bindData('#0-userinfo.userid#')!==c.expected_userid)throw new Error('预览登录人不符');
  const f=document.getElementById('iframepage_print'),expected='redirectCustomPage.action?url=Customize/customPrint/bx_dtl.jsp';
  if(!visible(f)||f.getAttribute('src')!==expected)throw new Error('仅诊断当前原生预览地址');
  const response=await fetch(expected,{method:'GET',credentials:'same-origin',redirect:'error',signal:AbortSignal.timeout(8000)}),text=await response.text();
  const d=new DOMParser().parseFromString(text,'text/html');d.querySelectorAll('script,style').forEach(e=>e.remove());const previewText=d.body.innerText||d.body.textContent||'';
  return{ok:response.ok,action:'diagnose_native_preview_response',serial:c.serial,system_write:false,status:response.status,bodyLength:text.length,contentType:response.headers.get('content-type'),previewText:previewText.slice(0,20000),previewTextTruncated:previewText.length>20000,sourceHead:text.slice(0,500),frameURL:f.contentWindow.location.href,
   responsePolicy:{frameOptions:response.headers.get('x-frame-options'),contentSecurityPolicy:response.headers.get('content-security-policy')},
   frameReady:f.contentDocument?.body.innerText.includes('报销单号：'+c.serial)||false,
   documentState:{readyState:document.readyState,visibility:document.visibilityState,baseURI:document.baseURI},
   frameSource:f.src};
 }
 function recoverMain(c){
  guard(c,false);if(!c.expected_userid||$.UC.bindData('#0-userinfo.userid#')!==c.expected_userid)throw new Error('预览登录人不符');
  const f=document.getElementById('iframepage_print');
  if(!visible(f)||f.getAttribute('src')!=='redirectCustomPage.action?url=Customize/customPrint/bx_dtl.jsp'||f.contentWindow.location.href!=='about:blank'||f.contentDocument.body.innerText.trim())throw new Error('只重载已核对的空白预览页');
  if([...document.querySelectorAll('input,select,textarea')].some(e=>visible(e)&&!e.disabled&&!e.readOnly))throw new Error('页面仍有可编辑字段，不能丢弃填写现场');
  location.replace('https://cwzh.ustc.edu.cn/YBX/main.jsp');
  return{ok:true,action:'reload_blank_preview_main',serial:c.serial,system_write:false,navigationRequested:true,requiresReadback:true};
 }
 async function navigate(c,section){guard(c);const routes={basic:'XG基本',funding:'选择项目',expenses:'项目明细',payments:'XG支付',explanation:'33&33'};if(!routes[section])throw new Error('未知填报环节');const readyTargets={basic:'formWF_YBX_60_t-abst',funding:'gridWF_YBX_242',expenses:'gridWF_YBX_307',payments:'formWF_YBX_19_t-bx_amt',explanation:'formWF_YBX_76_t-spec_response'};if(visible(document.getElementById(readyTargets[section])))return{ok:true,action:'navigate',serial:c.serial,section,alreadyOpen:true};const f=document.getElementById('iframepage_print');await wait(()=>f?.contentWindow.pageOpen&&f.contentDocument.body.innerText.includes('报销单号：'+c.serial),'报销单预览未就绪');if(section==='explanation'){if(!f.contentDocument.querySelector('[onclick="pageOpen_v1(\'特殊事项\',\'33&33\')"]'))throw new Error('总体说明入口变化');f.contentWindow.pageOpen_v1('特殊事项',routes[section]);}else f.contentWindow.pageOpen(routes[section]);const targets={basic:'formWF_YBX_60_t-abst',funding:'gridWF_YBX_242',expenses:'gridWF_YBX_307',payments:'formWF_YBX_19_t-bx_amt',explanation:'formWF_YBX_76_t-spec_response'};await wait(()=>visible(document.getElementById(targets[section])),'目标填报页面未就绪');return{ok:true,action:'navigate',serial:c.serial,section};}
 function inspectBasic(c){
  guard(c,false);const prefix='formWF_YBX_60_',keys=['t-lc_accepter','t-lc_accepter_name'];
  const fields=keys.map(k=>{const e=document.getElementById(prefix+k);if(!visible(e))throw new Error('基本信息证明人字段未打开');return{id:e.id,value:e.value,readOnly:!!e.readOnly,disabled:!!e.disabled,html:e.outerHTML,containerHTML:e.parentElement?.outerHTML,handlers:$._data?Object.entries($._data(e,'events')||{}).map(([event,handlers])=>({event,handlers:handlers.map(h=>String(h.handler).slice(0,8000))})):[]};});
  return{ok:true,action:'inspect_basic_teacher',serial:c.serial,system_write:false,fields,
   definitions:($.F.list['60'].formFields||[]).filter(x=>keys.includes(x.id)),
   candidates:[...document.querySelectorAll('.ac_results li')].filter(visible).map(e=>e.innerText),
   nativeTriggerSource:typeof $.page?.triggerBy==='function'?String($.page.triggerBy).slice(0,16000):null,
   validationErrors:($.F.validators?.['60']?.errorList||[]).map(e=>({id:e.element?.id,message:e.message})),
   activeRequests:Number($.active||0),nativeButtons:$.page?.list?.WF_YBX_43?.wins?.find(w=>w.winno===43)?.func_btns,
   recentNativeResources:typeof performance==='undefined'?[]:performance.getEntriesByType('resource').filter(e=>new URL(e.name).origin===location.origin).slice(-12).map(e=>({path:new URL(e.name).pathname,status:e.responseStatus,startedAt:new Date(performance.timeOrigin+e.startTime).toISOString(),duration:e.duration}))};
 }
 function teacherCandidate(text,name){
  const match=/^([A-Za-z0-9]{2,40})\/([^/\r\n]+)\/([^/\r\n]+)$/.exec(String(text).trim());
  if(!match||match[2].trim()!==name)throw new Error('证明人姓名未唯一匹配原生工号/姓名/单位候选');
  return{id:match[1],name:match[2].trim(),department:match[3].trim()};
 }
 async function closeBasic(c){
  guard(c);const id='formWF_YBX_60_t-lc_accepter',scope=dialog(id);
  const close=one([...scope.querySelectorAll('.ui-dialog-titlebar-close')].filter(e=>visible(e)&&e.closest('.ui-dialog')===scope),'基本信息原生关闭入口不唯一');
  close.click();await wait(()=>!visible(document.getElementById(id)),'基本信息窗口未关闭');
  return{ok:true,action:'close_basic_without_save',serial:c.serial,system_write:false};
 }
 async function openInvoiceGroup(c,group){
  guard(c);if(typeof group!=='string'||!group.trim()||group.length>80||/[\r\n'"<>!&]/.test(group))throw new Error('须明确原生发票内容分组');
  if(visible(document.getElementById('gridWF_YBX_305')))throw new Error('已有发票信息窗口，先核对原窗口');
  const f=document.getElementById('iframepage_print');if(!f?.contentDocument.body.innerText.includes('报销单号：'+c.serial))throw new Error('本案编辑预览未就绪');
  const e=one([...f.contentDocument.querySelectorAll('[onclick]')].filter(x=>x.getAttribute('onclick').startsWith("pageOpen_v1('发票信息','"+group+'!P!')),'本类发票信息入口不唯一');
  e.click();await wait(()=>visible(document.getElementById('gridWF_YBX_305')),'发票信息未打开');
  return{ok:true,action:'open_invoice_group',serial:c.serial,group,system_write:false};
 }
 function inspectInvoiceGroup(c){
  guard(c,false);const g=$('#gridWF_YBX_305');if(!visible(g[0])||g[0].grid.hDiv.loading)throw new Error('发票分组未就绪');
  return{ok:true,action:'inspect_invoice_group_controls',serial:c.serial,system_write:false,
   rows:g.jqGrid('getDataIDs').map(id=>({id,row:g.jqGrid('getRowData',id),html:document.getElementById(id).outerHTML})),
   callbacks:Object.fromEntries(['onSelectRow','onCellSelect','beforeSelectRow'].map(k=>[k,String(g.jqGrid('getGridParam',k))])),
   nativeButtons:$.page?.list?.WF_YBX_213?.wins?.find(w=>w.winno===213)?.func_btns};
 }
 async function fillInvoiceCategory(c,index){
  guard(c);const p=c.expense_categories?.[index];if(!p||!p.source||!p.invoice_guids?.length)throw new Error('须有冻结的费用分类及依据');
  if(c.funding.length!==1||c.funding_policy?.invoice_level_required)throw new Error('整组分类仅用于单经费且无逐票限制');
  await openInvoiceGroup(c,p.group);const g=$('#gridWF_YBX_305');await wait(()=>!g[0].grid.hDiv.loading,'分类候选尚未加载');
  const rows=()=>g.jqGrid('getDataIDs').map(id=>({id,r:g.jqGrid('getRowData',id)})),sameIds=s=>String(s).split(',').sort().join(',')===[...p.invoice_guids].sort().join(',');
  const all=rows();if(!all.length||all.some(x=>!x.r['t.xtype'].startsWith(p.group+'!P!')||x.r['t.xtype'].split('&')[2]!==c.funding[0].code||cents(x.r['t.amt'])!==cents(p.amount)||!sameIds(x.r['t.tk_guids'])))throw new Error('分类分组、经费、金额或逐票范围变化');
  const target=one(all.filter(x=>x.r['t.jj_subj']===p.subject&&x.r['t.jj_subjname']===p.subject_name),'目标科目不唯一或不是原生候选');
  if(all.filter(x=>x.r['t.select_flag']==='已选').length!==1)throw new Error('当前分类并非唯一整组科目');
  if(target.r['t.select_flag']==='已选'){await closeInvoiceGroup(c);return{ok:true,action:'verify_invoice_category',serial:c.serial,group:p.group,subject:p.subject,system_write:false,requiresReadback:true};}
  const choose=one([...document.getElementById(target.id).querySelectorAll('[btnname="选择"]')].filter(visible),'分类原生选择按钮不唯一');choose.click();
  await wait(()=>{const chosen=rows().filter(x=>x.r['t.select_flag']==='已选');return chosen.length===1&&chosen[0].r['t.jj_subj']===p.subject;},'分类选择尚未回读，不重发');
  click(one([...document.querySelectorAll('.foot_winWF_YBX_213')].filter(visible),'分类确认窗口不唯一'),'确认');
  // 每组保存后核新预览的代码和金额，再继续下一组；独立任务仍须再次回读。
  await wait(()=>{
   if(visible(g[0]))return false;
   const doc=document.getElementById('iframepage_print')?.contentDocument;
   if(!doc?.body?.innerText?.includes('报销单号：'+c.serial))return false;
   const prefix="pageOpen_v1('发票信息','"+p.group+'!P!';
   const found=[...doc.querySelectorAll('[onclick]')].filter(e=>e.getAttribute('onclick').startsWith(prefix));
   if(found.length!==1)return false;
   const parts=found[0].getAttribute('onclick').slice(prefix.length).split('&'),cells=found[0].closest('tr')?.cells;
   return parts[1]?.trim()===p.subject&&parts[2]===c.funding[0].code&&found[0].innerText.trim()===p.subject_name&&cells?.length===5&&cents(cells[4].innerText)===cents(p.amount);
  },'分类已请求确认但原生科目或金额尚未匹配；停止后续分组并独立回读，不重发');
  return{ok:true,action:'save_invoice_category',serial:c.serial,group:p.group,subject:p.subject,subjectName:p.subject_name,amount:p.amount,invoiceCount:p.invoice_guids.length,requiresReadback:true};
 }
 async function closeInvoiceGroup(c){
  guard(c);if(!visible(document.getElementById('gridWF_YBX_305'))||visible(document.getElementById('gridWF_YBX_1669')))throw new Error('只关闭原生发票信息窗口，不丢弃逐票拆分');
  click(one([...document.querySelectorAll('.foot_winWF_YBX_213')].filter(visible),'发票信息窗口不唯一'),'返回');
  await wait(()=>!visible(document.getElementById('gridWF_YBX_305')),'发票信息未关闭');
  return{ok:true,action:'close_invoice_group_without_save',serial:c.serial,system_write:false};
 }
 async function lookupTeacher(c){
  guard(c);const name=c.basic?.teacher_name;if(typeof name!=='string'||!name.trim()||/[\r\n/]/.test(name))throw new Error('须有本案确认的证明人姓名');
  const id='formWF_YBX_60_t-lc_accepter',nameId='formWF_YBX_60_t-lc_accepter_name';
  set(id,name);let candidate;await wait(()=>{try{candidate=teacherCandidate($('#'+nameId).val(),name);return true;}catch{return false;}},'证明人姓名未返回唯一原生候选');
  return{ok:true,action:'lookup_teacher_by_name',serial:c.serial,candidate,saveRequested:false,requiresReadback:true};
 }
 async function fillBasic(c,save){
  guard(c);const b=c.basic,lookup=await lookupTeacher(c),candidate=lookup.candidate;
  if(candidate.id!==b.teacher_id)throw new Error('证明人工号与原生姓名候选不符：'+candidate.id+'/'+candidate.name+'/'+candidate.department);
  const id='formWF_YBX_60_t-lc_accepter';set(id,candidate.id);await wait(()=>$('#formWF_YBX_60_t-lc_accepter_name').val()===b.teacher_name,'证明人工号未解析成确认的教师姓名');
  for(const[k,v]of Object.entries(b.fields))set('formWF_YBX_60_'+k,v);$.F.getDataToContext('60');
  if(save){click(dialog(id),'保存');await wait(()=>{guard(c,false);const frame=document.getElementById('iframepage_print'),doc=frame?.contentDocument,text=doc?.body?.innerText;
   const rawSummary=doc?.body?[...doc.querySelectorAll('tr')].filter(row=>row.cells.length===2&&row.cells[0].textContent.trim()==='摘要').map(row=>row.cells[1].textContent.trim()):[];
   return!visible(document.getElementById(id))&&visible(frame)&&typeof text==='string'&&text.includes('报销单号：'+c.serial)&&rawSummary.length===1&&rawSummary[0]===b.fields['t-abst'];},'基本信息已请求保存但预览未更新，须独立回读原结果');}
  return{ok:true,action:'fill_basic',serial:c.serial,teacher:candidate.id,name:candidate.name,department:candidate.department,saveRequested:!!save,requiresReadback:!!save};
 }
 function fillExpenses(c,save){
  if(c.funding_policy?.invoice_level_required)throw new Error('本案经费有限定票据用途/个人额度，必须逐票拆分，不能只修改费用分摊总额');
  guard(c);const grid=$('#gridWF_YBX_307');if(!visible(grid[0]))throw new Error('费用分摊未打开');
  const rows=grid.jqGrid('getDataIDs').map(id=>({id,r:grid.jqGrid('getRowData',id)}));if(rows.some(x=>x.r['t.serial_no']!==c.serial))throw new Error('分摊表混入其他单据');
  const leaves=rows.filter(x=>x.r.TLEVEL==='2'),plans=c.expense_allocations;
  if(c.budget_allocation_mode==='system_default_single_funding'){
   if(c.funding.length!==1||plans.length||!leaves.length)throw new Error('自动预算须单经费、无手工分摊且原生预算已加载');
   const amounts=leaves.map(x=>{if(x.r['t.uni_prj_code']!==c.funding[0].code)throw new Error('系统预算包含其他项目');const input=one([...document.getElementById(x.id).querySelectorAll('input[cname="t.amt"]')],'金额控件不唯一');if(cents(input.value)!==cents(input.defaultValue))throw new Error('存在未保存预算修改，不能当作系统自动分配');return{subject:x.r['t.jj_subj'],amount:cents(input.value)};});
   if(amounts.reduce((n,x)=>n+x.amount,0)!==cents(c.amount))throw new Error('系统自动预算合计不符');
   for(const parent of rows.filter(x=>x.r.TLEVEL==='0')){const sum=amounts.filter(x=>x.subject===parent.r['t.jj_subj']).reduce((n,x)=>n+x.amount,0);const input=document.getElementById(parent.id).querySelector('input[cname="t.amt"]');if(sum!==cents(input.value))throw new Error('系统自动预算存在未分配金额');}
   if(save)click(one([...document.querySelectorAll('.foot_winWF_YBX_216')].filter(visible),'预算返回窗口不唯一'),'返回');
   return{ok:true,action:'verify_system_default_budget',serial:c.serial,project:c.funding[0].code,total:c.amount,system_write:false,saveRequested:false,requiresReadback:true};
  }
  if(plans.length!==leaves.length)throw new Error('必须显式配置所有预算叶节点，包括0元节点');
  const changes=plans.map(p=>{const x=one(leaves.filter(x=>x.r['t.uni_prj_code']===p.project&&x.r['t.jj_subj']===p.subject&&x.r['t.bu_code']===p.budget),'预算节点不唯一');const input=one([...document.getElementById(x.id).querySelectorAll('input[cname="t.amt"]')],'金额控件不唯一');if(input.readOnly)throw new Error('叶节点金额不可编辑');if(cents(p.amount)>cents(x.r['t.bu_code_balance']))throw new Error('预算余额不足');return{...p,input,current:cents(input.value),next:cents(p.amount)};});
  if(changes.reduce((n,x)=>n+x.next,0)!==cents(c.amount))throw new Error('分摊总额不符');
  for(const parent of rows.filter(x=>x.r.TLEVEL==='0')){const sum=changes.filter(x=>x.subject===parent.r['t.jj_subj']).reduce((n,x)=>n+x.next,0);const input=document.getElementById(parent.id).querySelector('input[cname="t.amt"]');if(sum!==cents(input.value))throw new Error('经济科目分摊总额不符：'+parent.r['t.jj_subjname']);}
  for(const fund of c.funding){if(changes.filter(x=>x.project===fund.code).reduce((n,x)=>n+x.next,0)!==cents(fund.amount))throw new Error('项目分摊总额不符：'+fund.code);}
  changes.sort((a,b)=>(a.next-a.current)-(b.next-b.current)).forEach(x=>$(x.input).val(x.amount).trigger('change').trigger('blur'));
  if(save)click(one([...document.querySelectorAll('.foot_winWF_YBX_216')].filter(visible),'费用分摊保存窗口不唯一'),'保存');
  return{ok:true,action:'fill_expenses',serial:c.serial,allocations:changes.map(({input,current,next,...x})=>x),saveRequested:!!save,requiresReadback:!!save};
 }
 const transferRemarkUnits=value=>Array.from(value).reduce((n,ch)=>n+(ch.codePointAt(0)>255?2:1),0);
 function validateTransferRemark(value){if(typeof value!=='string'||transferRemarkUnits(value)>25)throw new Error('转卡附言须为不超过25字符单位的字符串，汉字按2单位计');return value;}
 async function fillTransfers(c,range){
  if(c.payments?.some(p=>p.mode==='corporate_transfer'))return fillCorporateTransfers(c,range);
  guard(c);const plans=c.payments.filter(p=>p.mode==='bank_transfer');
  for(const p of plans)if(Object.prototype.hasOwnProperty.call(p,'remark'))validateTransferRemark(p.remark);
  if(!visible(document.getElementById('formWF_YBX_19_t-bx_amt'))||cents($('#formWF_YBX_19_t-bx_amt').val())!==cents(c.amount))throw new Error('结算页面或金额不符');
  if($.F.list['22'].multiCount>plans.length)throw new Error('现有转卡行数超过配置，须检查');
  while($.F.list['22'].multiCount<plans.length){const count=$.F.list['22'].multiCount;const plus=[...document.querySelectorAll('.addoneformWF_YBX_22')].filter(visible);if(!plus.length)throw new Error('转卡添加入口缺失');plus[0].click();await wait(()=>$.F.list['22'].multiCount===count+1,'原生新增转卡行未完成');}
  const start=range.start||0,limit=range.limit||5;if(start<0||limit<1||limit>5)throw new Error('每次只处理1至5个已配置收款人');const results=[];
  for(let i=start;i<Math.min(start+limit,plans.length);i++){
   const p=plans[i],prefix='formWF_YBX_22_t-',input=document.getElementById(prefix+'sno_'+i),name=document.getElementById(prefix+'sname_'+i),bank=document.getElementById(prefix+'cnt1_'+i),amount=document.getElementById(prefix+'amount_'+i);
   if(cents(amount.value||'0')>0&&(input.value!==p.person_id||name.value!==p.name||cents(amount.value)!==cents(p.amount)))throw new Error('现有非零支付与配置冲突，停止：'+i);
   if(input.value!==p.person_id||name.value!==p.name||!bank.value){
    input.focus();input.value=p.person_id;$(input).trigger('focus').trigger('keyup').trigger($.Event('keydown',{keyCode:40,which:40}));
    const match=e=>{const text=e.innerText.replace(/^\d+\/\d+:\s*/,'');return visible(e)&&text.startsWith(p.name+'>'+p.person_id+'>');};
    await wait(()=>[...document.querySelectorAll('.ac_results li')].some(match),'工号无匹配候选：'+p.person_id);
    const li=one([...document.querySelectorAll('.ac_results li')].filter(match),'姓名工号匹配候选不唯一：'+p.person_id);
    $(li).trigger('mouseover').trigger('mousedown');
    // 后台标签页 HTMLElement.blur() 不保证触发 onblur；jQuery 原生事件同时调用页面 inline 解析。
    $(input).trigger('blur');
    if(name.value!==p.name){
     if(input.getAttribute('onblur')!=='_3splitCardUnit(this,22);'||typeof root._3splitCardUnit!=='function')throw new Error('系统人员解析回调变化');
     root._3splitCardUnit(input,22);
    }
    $(li).trigger('mouseup').trigger('click');
    await wait(()=>input.value===p.person_id&&name.value===p.name&&!!bank.value,'人员或银行卡未完成原生联动：'+p.person_id);
   }
   set(prefix+'amount_'+i,p.amount);
   // 附言是独立字段；缺省保留原生值，不能把摘要或联系人说明挤入短字段。
   if(Object.prototype.hasOwnProperty.call(p,'remark'))set(prefix+'remark_'+i,p.remark);
   results.push({index:i,person_id:p.person_id,name:p.name,amount:p.amount,bankBound:!!bank.value});
  }
  $.F.getDataToContext('22');$.F.getDataToContext('19');return{ok:true,action:'fill_transfers',serial:c.serial,results,transferRows:plans.length,total:$('#formWF_YBX_19_t-yt_amt').val(),remaining:$('#formWF_YBX_19_t-wt_amt').val(),saveRequested:false};
 }
 function fillExplanation(c,save){guard(c);const notes=c.explanations;if(!notes?.general)throw new Error('补充说明未配置');if(notes.record_only===true)throw new Error('当前说明按用户最新要求仅留档，不自动改写现有单据');const text=[notes.general,...(notes.card_notes||[]).map(x=>x.text)].join('\n\n');set('formWF_YBX_76_t-spec_response',text);$.F.getDataToContext('76');if(save)click(dialog('formWF_YBX_76_t-spec_response'),'保存');return{ok:true,action:'fill_explanation',serial:c.serial,text,cardNoteCount:(notes.card_notes||[]).length,saveRequested:!!save,requiresReadback:!!save};}
 function noContract(c){guard(c);if(c.contract?.exists!==false)throw new Error('未确认无合同；有合同案件须走关联和附件流程');const f=document.getElementById('iframepage_print');if(!f?.contentDocument.body.innerText.includes('报销单号：'+c.serial))throw new Error('预览不符');const es=[...f.contentDocument.querySelectorAll('input[name^="form_proof_choose"][value="N"]')];if(es.length!==1||!es[0].closest('tr')?.innerText.includes('合同号'))throw new Error('合同选择结构变化');if(!es[0].checked)es[0].click();return{ok:true,action:'choose_no_contract',serial:c.serial,requiresReadback:true};}
 function queryMoney(value){const n=cents(value),whole=String(Math.floor(n/100)),fraction=String(n%100).padStart(2,'0').replace(/0+$/,'');return fraction?whole+'.'+fraction:whole;}
 function fundingPreviewMatches(c){
  const frame=document.getElementById('iframepage_print'),doc=frame?.contentDocument,text=doc?.body?.innerText;
  if(!visible(frame)||typeof text!=='string'||!text.includes('报销单号：'+c.serial))return false;
  const totals=[...text.matchAll(/金额总计[：:]\s*([\d,]+(?:\.\d{1,2})?)(?![\d.])/g)];
  if(totals.length!==1||cents(totals[0][1])!==cents(c.amount))return false;
  const projects=[...doc.querySelectorAll('[onclick]')].filter(e=>visible(e)&&e.getAttribute('onclick')==="pageOpen('选择项目')")
   .flatMap(e=>(e.innerText||'').split(/\r?\n/).filter(line=>line.trim()).map(line=>line.split('/').map(s=>s.trim())));
  return projects.length===c.funding.length&&projects.every(parts=>parts.length>=3)&&c.funding.every(p=>projects.filter(parts=>parts[0]===p.code&&parts[1]===p.name).length===1);
 }
 function fundingValidationErrors(){
  const errors=paymentPrompts().filter(x=>x.title==='提示信息'&&/错误|失败|未选择|未选|至少|不能为空|不正确|不合法|不能大于|请填写|请选择|不一致|余额不足|不允许|禁止/.test(x.message)).map(x=>x.message);
  for(const item of $.F.validators?.['243']?.errorList||[])if(item.message)errors.push(String(item.message));
  return[...new Set(errors)];
 }
 async function fillFunding(c,save){
  guard(c);const grid=$('#gridWF_YBX_242');if(!visible(grid[0]))throw new Error('经费选择窗口未打开');
  const multiple=c.funding.length>1?'Y':'N',radio=one([...document.querySelectorAll('input[name="rdIsmulti_Proj"]')].filter(x=>visible(x)&&x.value===multiple),'项目模式不唯一');
  if(!radio.checked)radio.click();await wait(()=>$.UC.bindData('#61-ismulti_proj#')===multiple,'项目模式尚未绑定');
  const rows=grid.jqGrid('getDataIDs').map(id=>({id,r:grid.jqGrid('getRowData',id)}));
  if(rows.some(x=>x.r['t.select_flag']==='已选'&&!c.funding.some(p=>p.code===x.r['t.uni_prj_code'])))throw new Error('存在配置以外的已选项目');
  for(const p of c.funding){const x=one(rows.filter(x=>x.r['t.uni_prj_code']===p.code),'经费项目不唯一：'+p.code);if(x.r['t.select_flag']!=='已选'){const b=document.getElementById(x.id).querySelector('[btnname="选择"]');if(!b)throw new Error('原生项目选择缺失');b.click();await wait(()=>grid.jqGrid('getRowData',x.id)['t.select_flag']==='已选','项目选择未完成');}}
  if(save){
   click(dialog('gridWF_YBX_242'),'保存');
   for(let i=0;i<80;i++){
    guard(c,false);const errors=fundingValidationErrors();
    if(errors.length)return{ok:false,action:'fill_funding',serial:c.serial,not_saved:true,saveRequested:true,error_code:'funding_native_validation_rejected',validation_errors:errors,requiresReadback:true};
    if(!visible(document.getElementById('gridWF_YBX_242'))&&fundingPreviewMatches(c))return{ok:true,action:'fill_funding',serial:c.serial,projects:c.funding.map(x=>x.code),total:c.amount,saveRequested:true,fundingWindowExited:true,previewVerified:true,requiresReadback:true};
    await delay(100);
   }
   return{ok:false,action:'fill_funding',serial:c.serial,saveRequested:true,execution_status:'unknown_verify_before_retry',error_code:'funding_save_readiness_timeout',requiresReadback:true};
  }
  return{ok:true,action:'fill_funding',serial:c.serial,projects:c.funding.map(x=>x.code),saveRequested:false,requiresReadback:false};
 }
 async function preReview(c,index){
  guard(c);const p=c.pre_review[index];if(!p||index<0||index>3)throw new Error('预审人员配置不存在');
  const frame=document.getElementById('iframepage_print');if(!frame?.contentDocument.body.innerText.includes('报销单号：'+c.serial))throw new Error('预览身份不符');
  const token='ustc_xmysr'+(index||'');const e=one([...frame.contentDocument.querySelectorAll('[onclick]')].filter(x=>x.getAttribute('onclick').startsWith("pageOpen_v1('选择流程参数','WORKFLOW_V1&"+token+'&')),'预审入口不唯一');
  e.click();await wait(()=>visible(document.getElementById('formWF_YBX_1297_t-param_key3'))&&$('#formWF_YBX_1297_t-param_show_name').val()==='[项目预审人'+(index+1)+']','预审窗口未就绪');
  // 该原生窗口明确接受“工号/姓名”格式，保存过程校验并解析；不凭蓝框判完成。
  set('formWF_YBX_1297_t-param_key3',p.id+'/'+p.name);$.F.getDataToContext('1297');click(dialog('formWF_YBX_1297_t-param_key3'),'保存');
  return{ok:true,action:'save_pre_review',serial:c.serial,index,...p,requiresReadback:true};
 }
 async function cardQuery(c,index){
  guard(c);const p=c.payments.filter(x=>x.mode==='corporate_card')[index];if(!p?.transaction)throw new Error('公务卡交易条件未配置');
  if(!visible(document.getElementById('formWF_YBX_792_t-no'))){click(document,'选择消费记录');await wait(()=>visible(document.getElementById('formWF_YBX_792_t-no')),'消费查询未打开');}
  const search=queryMoney(p.transaction.search_amount||p.amount);set('formWF_YBX_792_t-no',p.person_id);set('formWF_YBX_792_t-pay_amt',search);set('formWF_YBX_792_t-pay_time',p.transaction.search_period||'A');$.F.getDataToContext('792');
  if($.UC.bindData('#792-t.no#')!==p.person_id||$.UC.bindData('#792-t.pay_amt#')!==search)throw new Error('消费查询条件未绑定');
  let done=false;const handler=(_event,_xhr,settings)=>{if(settings.url.includes('commonQuery_doQuery.action?funcno=793&'))done=true;};
  $(document).on('ajaxComplete.reimbursementCard',handler);
  try{click(dialog('formWF_YBX_792_t-no'),'查询');await wait(()=>done,'消费查询未完成');}
  finally{$(document).off('ajaxComplete.reimbursementCard',handler);}
  const g=$('#gridWF_YBX_793'),rows=g.jqGrid('getRowData');return{ok:true,action:'query_card',serial:c.serial,person_id:p.person_id,searchAmount:search,rowCount:rows.length,candidates:rows.map(r=>({name:r['t.accname'],date:r['t.pay_time'],merchant:r['t.pay_addr'],amount:r['t.pay_amt'],cardSuffix:r['t.account'].slice(-4)}))};
 }
 function cardSelect(c,index){
  guard(c);const p=c.payments.filter(x=>x.mode==='corporate_card')[index],t=p?.transaction;if(!t?.date||!t.merchant||!/^\d{4}$/.test(t.card_suffix))throw new Error('交易匹配条件不完整');
  const g=$('#gridWF_YBX_793');if(!visible(g[0]))throw new Error('消费候选未打开');
  const id=one(g.jqGrid('getDataIDs').filter(id=>{const r=g.jqGrid('getRowData',id);return r['t.accname']===p.name&&r['t.pay_time'].startsWith(t.date)&&r['t.pay_addr']===t.merchant&&cents(r['t.pay_amt'])===cents(t.search_amount||p.amount)&&r['t.account'].endsWith(t.card_suffix);}), '公务卡候选不唯一或不存在');
  const selected=g.jqGrid('getGridParam','selarrrow')||[];if(selected.some(x=>x!==id))throw new Error('存在其他已选公务卡记录');if(!selected.includes(id))g.jqGrid('setSelection',id,true);
  const input=one([...document.getElementById(id).querySelectorAll('input[cname="t.real_amount"]')],'实报金额控件不唯一');$(input).val(p.amount).trigger('change').trigger('blur');click(one([...document.querySelectorAll('.foot_winWF_YBX_585')].filter(visible),'消费保存窗口不唯一'),'保存');return{ok:true,action:'select_card',serial:c.serial,person_id:p.person_id,amount:p.amount,requiresReadback:true};
 }
 async function audit(c){
  guard(c);const frame=document.getElementById('iframepage_print'),text=frame?.contentDocument?.body?.innerText||'',previewLoaded=text.includes('报销单号：'+c.serial);const tables=previewLoaded?[...frame.contentDocument.querySelectorAll('table')].map(t=>[...t.rows].map(r=>[...r.cells].map(x=>x.innerText.trim()))):[];
  const issues=[];if(!previewLoaded)issues.push('当前不是已保存单据预览；编辑器值不能当保存回执');
  const corporate=c.payments.some(p=>p.mode==='corporate_transfer');if(corporate)return{ok:false,action:'audit_saved_preview',serial:c.serial,previewLoaded,paymentTotal:false,issues:['对公汇款预览行结构及账号SHA尚待原生独立证据核定，不能仅凭姓名金额确认保存'],requiresReadback:true,materialsComplete:false};
  for(const p of c.payments){const amount=cents(p.amount)/100,formatted=amount.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});const matched=tables.flat().some(row=>row.includes(p.name)&&row.includes(formatted)&&(p.mode!=='bank_transfer'||row.includes(p.person_id)));if(!matched)issues.push('支付回读缺失或金额不同：'+p.person_id+'/'+p.mode);}
  const paymentTotal=previewLoaded&&text.includes('共填写'+c.payments.length+'条支付信息,小计 '+(cents(c.amount)/100).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})+'元');if(!paymentTotal)issues.push('支付总额/条数尚未获得保存回读');
  for(const p of c.pre_review||[]){if(!text.includes(p.id+'('+p.name+')'))issues.push('预审人未回读：'+p.id);}
  let explanationVerified=false;
  if(c.explanations&&previewLoaded&&c.explanations.record_only!==true&&
     (c.explanations.general||(c.explanations.card_notes||[]).some(x=>x.text))){
   const expected=[c.explanations.general,...(c.explanations.card_notes||[]).map(x=>x.text)].join('\n\n');
   if(!visible(document.getElementById('formWF_YBX_76_t-spec_response')))await navigate(c,'explanation');
   await wait(()=>visible(document.getElementById('formWF_YBX_76_t-spec_response'))&&[...document.querySelectorAll('.ui-dialog')].filter(e=>visible(e)&&e.querySelector('#formWF_YBX_76_t-spec_response')).length===1,'说明回读窗口未打开');
   explanationVerified=$('#formWF_YBX_76_t-spec_response').val()===expected;
   if(!explanationVerified)issues.push('已保存说明内容与配置不同');
   click(dialog('formWF_YBX_76_t-spec_response'),'返回');
  }

  return{ok:issues.length===0,action:'audit_saved_preview',serial:c.serial,previewLoaded,paymentTotal,explanationVerified,issues,materialsComplete:false,materialsStatus:'附件与原件关联另按材料计划核对'};
 }
 async function closeEmptyExplanation(c){
  guard(c,false);
  if(!/^[A-Za-z0-9]{2,40}$/.test(c.expected_userid||'')||$.UC.bindData('#0-userinfo.userid#')!==c.expected_userid)throw Error('空说明返回账号不符');
  const notes=c.explanations;
  if(notes?.record_only!==true||notes.general!==''||!Array.isArray(notes.card_notes)||notes.card_notes.length)throw Error('仅返回只留档且无说明内容的原审读窗口');
  const dialogs=[...document.querySelectorAll('.ui-dialog')].filter(visible),field=document.getElementById('formWF_YBX_76_t-spec_response');
  if(dialogs.length!==1||!visible(field)||!dialogs[0].querySelector('#formWF_YBX_76_t-spec_response')||field.value!=='')throw Error('原说明窗口不唯一或已有内容，不返回');
  const inputs=[...dialogs[0].querySelectorAll('input,select,textarea')].filter(e=>visible(e)&&!['hidden','button','submit','reset'].includes(e.type));
  if(inputs.some(e=>e!==field&&(e.value||e.files?.length)))throw Error('说明窗口存在其他输入或上传，不返回');
  const files=$.UC.bindData('#76-t.SPEC_FILE#');if(![undefined,null,'','[]'].includes(files))throw Error('说明附件不为空，不自动返回');
  click(dialogs[0],'返回');
  await wait(()=>{guard(c,false);if($.UC.bindData('#0-userinfo.userid#')!==c.expected_userid)throw Error('返回期间账号变化');return!visible(field)&&!visible(dialogs[0])&&visible(document.getElementById('iframepage_print'));},'说明返回结果未知，仅回读原请求');
  return{ok:true,action:'close_empty_explanation',serial:c.serial,system_write:false,fields_modified:false,saveRequested:false,requiresReadback:true};
 }
 async function prepareReview(c){const checked=await audit(c);if(!checked.ok)throw new Error('保存回读未通过：'+checked.issues.join('；'));await wait(()=>visible(document.querySelector('.foot_winWF_YBX_41 button[btnname="下一步"]')),'下一步未就绪');click(one([...document.querySelectorAll('.foot_winWF_YBX_41')].filter(visible),'单据窗口不唯一'),'下一步');return{ok:true,action:'prepare_review_requested',serial:c.serial,requiresReadback:true,finalApproval:false};}
 function acceptPromise(c){guard(c);if(c.review?.workflow_started===true)throw new Error('本案流程已创建，不能再次确认承诺生成流程');if(c.review?.ordinary_promise_authorized!==true)throw new Error('普通承诺未获本案授权');click(one([...document.querySelectorAll('.foot_winWF_YBX_512')].filter(visible),'承诺页未就绪'),'已阅读遵守且提交');return{ok:true,action:'accept_ordinary_promise',serial:c.serial,requiresReadback:true,finalApproval:false};}
 async function departmentApprovers(c){
  guard(c);const results=[];
  for(let i=0;i<c.funding.length;i++){const p=c.funding[i],a=p.department_approver,id='formWF_YBX_1369_t-drive_condition_'+i,label=$('#formWF_YBX_1369_t-condition_name_'+i).val()||'';if(!label.includes(p.code)||!label.includes('单位负责人（院级）'))throw new Error('审批节点身份变化');const control=$('#'+id).data('xmSelect');one((control?.options?.data||[]).filter(x=>x.value===a.id&&x.name===a.id+'('+a.name+')'),'审批人候选不唯一：'+a.id);$.F.setInputData(id,'xmselect',a.id,'');await wait(()=>control.getValue().map(x=>x.value).join(',')===a.id&&$.F.getInputData(id,'xmselect')===a.id,'审批人未完成业务绑定');$('#'+id).find('xm-select').trigger('change');results.push({project:p.code,...a});}
  $.F.getDataToContext('1369');if($.UC.bindData('#1369-t.drive_condition#')!==results.map(x=>x.id).join('$|'))throw new Error('审批人上下文不同');click(dialog('formWF_YBX_1369_t-drive_condition_0'),'保存');return{ok:true,action:'save_department_approvers',serial:c.serial,results,requiresReadback:true,finalApproval:false};
 }
 function status(c){guard(c,false);return{ok:true,action:'status',serial:c.serial,node:$.UC.bindData('#61-node_id#'),captchaRequired:$.UC.bindData('#0-NEED_SP_YZM#'),applicantAgreementVisible:visible(document.querySelector('.foot_winWF_YBX_76 button[btnname="同意"]')),editablePaymentVisible:visible(document.getElementById('formWF_YBX_19_t-bx_amt')),previewVisible:visible(document.getElementById('iframepage_print')),finalApprovalExecuted:false};}
 async function waitSection(c,section){guard(c,false);const tests={promise:()=>visible(document.querySelector('.foot_winWF_YBX_512 button[btnname="已阅读遵守且提交"]')),department:()=>visible(document.getElementById('formWF_YBX_1369_t-condition_name_0')),applicant:()=>visible(document.querySelector('.foot_winWF_YBX_76 button[btnname="同意"]'))};if(!tests[section])throw new Error('未知等待环节');await wait(tests[section],'目标环节未完成加载：'+section);return{...status(c),section};}
 const remarkLengthMessage='转卡附言最大长度大于设定长度25字';
 const bankDigitsMessage='汇款-银行账号必须为纯数字：';
 function paymentPrompts(){return [...document.querySelectorAll('.ui-dialog')].filter(visible).map(element=>({element,title:(element.querySelector('.ui-dialog-title')?.textContent||'').trim(),message:(element.querySelector('.ui-dialog-content')?.textContent||'').trim()}));}
 function paymentValidationErrors(){
  const errors=paymentPrompts().filter(x=>x.title==='提示信息'&&/最大长度|不能为空|不正确|不合法|不能大于|请填写|未完成|不一致|长度|必须为纯数字/.test(x.message)).map(x=>x.message);
  for(const key of ['19','22','24','285'])for(const x of $.F.validators?.[key]?.errorList||[])if(typeof x.message==='string')errors.push(x.message);
  return [...new Set(errors.map(message=>message.replace(/\b\d(?:[ \u00a0]?\d){11,31}\b/g,value=>{const digits=value.replace(/[ \u00a0]/g,'');return digits.slice(0,4)+'****'+digits.slice(-4);})) )];
 }
 function personalPaymentSnapshot(c){
  guard(c);if(!c.payments?.length||c.payments.some(p=>p.mode!=='bank_transfer')||c.payments.reduce((n,p)=>n+cents(p.amount),0)!==cents(c.amount))throw new Error('仅允许金额闭合的全个人转卡配置');
  if(!visible(document.getElementById('formWF_YBX_19_t-bx_amt'))||cents($('#formWF_YBX_19_t-bx_amt').val())!==cents(c.amount)||cents($('#formWF_YBX_19_t-yt_amt').val())!==cents(c.amount)||cents($('#formWF_YBX_19_t-wt_amt').val())!==0)throw new Error('个人支付原生合计尚未闭合');
  if($.F.list['22']?.multiCount!==c.payments.length)throw new Error('原生个人转卡行数与配置不符');
  return JSON.stringify(c.payments.map((p,i)=>{
   const row=['sno','sname','amount','cnt1','remark'].map(k=>$('#formWF_YBX_22_t-'+k+'_'+i).val());
   if(row[0]!==p.person_id||row[1]!==p.name||cents(row[2])!==cents(p.amount)||typeof row[3]!=='string'||!row[3].trim())throw new Error('个人转卡身份、金额或银行卡不符：'+i);
   validateTransferRemark(row[4]);return row;
  }));
 }
 function zeroRemittanceSnapshot(strict=false){
  const elements=[...document.querySelectorAll('input,select,textarea')].filter(e=>/^formWF_YBX_24_.*_[0-9]+$/.test(e.id));
  const count=$.F.list['24']?.multiCount??0;
  if(!Number.isSafeInteger(count)||count<0||count>100||elements.some(e=>Number(e.id.match(/_([0-9]+)$/)[1])>=count))throw new Error('原生汇款完整行数不符');
  const rows=[];
  for(let i=0;i<count;i++){
   const own=elements.filter(e=>e.id.endsWith('_'+i));
   if(new Set(own.map(e=>e.id)).size!==own.length)throw new Error('原生汇款字段重复：'+i);
   const field=k=>one(own.filter(e=>e.id==='formWF_YBX_24_t-'+k+'_'+i),'原生汇款字段缺失：'+i);
   const name=field('banksname'),account=field('bankacnt'),bank=field('bankname'),amount=field('amount');field('remark');
   const fields=own.map(e=>{if(typeof e.value!=='string')throw new Error('汇款字段值类型不符：'+i);return{id:e.id,value:e.value,checked:!!e.checked,readOnly:!!e.readOnly,disabled:!!e.disabled};}).sort((a,b)=>a.id.localeCompare(b.id));
   if(cents(amount.value)!==0)throw new Error('存在非零未计划汇款：'+i);
   const normalized=account.value.replaceAll(' ','');
   if(strict){
    if(![name,account,bank,amount].every(visible)||!name.value.trim()||!bank.value.trim())throw new Error('严格汇款校验要求完整原生身份：'+i);
    if(!/^[0-9 ]+$/.test(account.value))throw new Error('汇款账号不是纯数字及ASCII格式空格：'+i);
    if(!/^[0-9]{12,30}$/.test(normalized))throw new Error('汇款账号位数不符：'+i);
   }
   rows.push({index:i,fields,normalized,digits_length:normalized.length,needsNormalization:/^[0-9 ]+$/.test(account.value)&&/[0-9]/.test(account.value)&&account.value.includes(' ')});
  }
  return{rows,fingerprint:JSON.stringify(rows.map(r=>r.fields))};
 }
 async function normalizeZeroRemittanceAccounts(c,original,checkBindings){
  const targets=original.rows.filter(r=>r.needsNormalization);if(!targets.length)return{snapshot:original,changes:[]};
  if(zeroRemittanceSnapshot(true).fingerprint!==original.fingerprint)throw new Error('严格规范化前原汇款行变化，停止保存');
  const bindings=personalPaymentSnapshot(c),expected=JSON.parse(JSON.stringify(original.rows));
  const digest=async text=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text))),v=>v.toString(16).padStart(2,'0')).join('');
  const changes=[];
  for(const row of targets){const id='formWF_YBX_24_t-bankacnt_'+row.index,before=row.fields.find(f=>f.id===id).value;
   changes.push({row:row.index,zero:true,digits_length:row.digits_length,format_only:true,before_sha256:await digest(before),after_sha256:await digest(row.normalized)});
  }
  const verify=()=>{guard(c);checkBindings();if(personalPaymentSnapshot(c)!==bindings||zeroRemittanceSnapshot(true).fingerprint!==JSON.stringify(expected.map(r=>r.fields)))throw new Error('汇款规范化期间身份或原字段变化，停止保存');};
  verify();if(paymentValidationErrors().length)throw new Error('仍有支付校验错误，不规范化汇款账号');
  for(const row of targets){
   verify();const id='formWF_YBX_24_t-bankacnt_'+row.index;set(id,row.normalized);
   expected.find(r=>r.index===row.index).fields.find(f=>f.id===id).value=row.normalized;verify();
  }
  $.F.getDataToContext('24');verify();return{snapshot:zeroRemittanceSnapshot(),changes};
 }
 function corporatePlans(c){
  guard(c);const plans=c.payments;
  if(!Array.isArray(plans)||!plans.length||plans.some(p=>p.mode!=='corporate_transfer'||!Number.isSafeInteger(cents(p.amount)))||!Number.isSafeInteger(cents(c.amount))||!Number.isSafeInteger(plans.reduce((n,p)=>n+cents(p.amount),0))||plans.reduce((n,p)=>n+cents(p.amount),0)!==cents(c.amount))throw new Error('仅允许金额闭合的全对公转账配置');
  const accounts=new Set();
  for(const p of plans){
   if(typeof p.name!=='string'||!p.name.trim()||p.name!==p.name.trim()||typeof p.bank_name!=='string'||!p.bank_name.trim()||p.bank_name!==p.bank_name.trim()||/[\x00-\x1f\x7f]/.test(p.name+p.bank_name)||Object.hasOwn(p,'person_id'))throw new Error('对公名称、开户行或身份配置不完整');
   if(typeof p.bank_account!=='string'||!new RegExp('^[0-9 ]+$').test(p.bank_account)||!/^\d{12,30}$/.test(p.bank_account.replaceAll(' ','')))throw new Error('对公账号仅允许数字及ASCII格式空格');
   const account=p.bank_account.replaceAll(' ','');if(accounts.has(account))throw new Error('对公收款账户重复');accounts.add(account);
   if(cents(p.amount)<=0||!Array.isArray(p.source_refs)||!p.source_refs.length||p.source_refs.some(x=>typeof x!=='string'||!x.trim())||new Set(p.source_refs).size!==p.source_refs.length)throw new Error('对公付款金额或本案来源不完整');
   if(Object.hasOwn(p,'remark')){validateTransferRemark(p.remark);if(/[\x00-\x1f\x7f]/.test(p.remark))throw new Error('对公附言须为单行短文本');}
  }
  return plans;
 }
 function corporatePaymentSnapshot(c){
  const plans=corporatePlans(c);
  if(!visible(document.getElementById('formWF_YBX_19_t-bx_amt'))||cents($('#formWF_YBX_19_t-bx_amt').val())!==cents(c.amount))throw new Error('对公结算页面或待报销金额不符');
  const all=[...document.querySelectorAll('input,select,textarea')].filter(e=>/^formWF_YBX_(19|22|24|285)_/.test(e.id));
  if(new Set(all.map(e=>e.id)).size!==all.length||all.some(e=>typeof e.value!=='string'))throw new Error('原生支付全字段快照不完整或重复');
  const rows={};
  for(const form of ['22','24','285']){
   const count=$.F.list[form]?.multiCount??0,own=all.filter(e=>e.id.startsWith('formWF_YBX_'+form+'_'));
   if(!Number.isSafeInteger(count)||count<0||count>100||own.some(e=>!/_\d+$/.test(e.id)||Number(e.id.match(/_(\d+)$/)[1])>=count))throw new Error('原生支付完整行数不符');
   rows[form]=Array.from({length:count},(_,index)=>{
    const fields=own.filter(e=>e.id.endsWith('_'+index));
    const field=key=>one(fields.filter(e=>e.id==='formWF_YBX_'+form+'_t-'+key+'_'+index),'原生支付行缺少必需字段');
    const amount=field('amount');
    if(form!=='24'){const emptyCard=form==='285'&&fields.every(e=>e.value==='');if(!emptyCard&&cents(amount.value)!==0)throw new Error('存在未计划个人或公务卡正额');return{index,fields,amount};}
    const name=field('banksname'),bank=field('bankname'),account=field('bankacnt'),remark=field('remark');
    return{index,fields,amount,name,bank,account,remark};
   });
  }
  const targets=plans.map(p=>{
   const bankMatches=value=>value===p.bank_name||(/^\d{12}\//.test(value)&&value.slice(13)===p.bank_name);
   const row=one(rows['24'].filter(r=>r.name.value===p.name&&bankMatches(r.bank.value)&&/^[0-9 ]+$/.test(r.account.value)&&r.account.value.replaceAll(' ','')===p.bank_account.replaceAll(' ','')),'本案对公收款原生行不完整或不唯一');
   if(![row.name,row.bank,row.account,row.amount,row.remark].every(visible)||row.amount.disabled||row.amount.readOnly||row.remark.disabled||row.remark.readOnly)throw new Error('本案对公行未加载或金额附言不可编辑');
   if(cents(row.amount.value)!==0&&cents(row.amount.value)!==cents(p.amount))throw new Error('本案对公行已有不同的非零金额');
   return{plan:p,row};
  });
  if(new Set(targets.map(t=>t.row.index)).size!==targets.length)throw new Error('多个对公配置命中同一原生行');
  for(const row of rows['24'])if(!targets.some(t=>t.row.index===row.index)&&cents(row.amount.value)!==0)throw new Error('存在非零未计划对公行');
  const fields=all.map(e=>({id:e.id,value:e.value,checked:!!e.checked,readOnly:!!e.readOnly,disabled:!!e.disabled})).sort((a,b)=>a.id.localeCompare(b.id));
  const stable=fields.filter(e=>!['formWF_YBX_19_t-yt_amt','formWF_YBX_19_t-wt_amt'].includes(e.id));
  return{targets,rows,fields,stable,fingerprint:JSON.stringify({serial:c.serial,amount:c.amount,payments:c.payments,fields:stable})};
 }
 async function corporateDigest(value){return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value))),v=>v.toString(16).padStart(2,'0')).join('');}
 async function inspectCorporates(c){
  const original=corporatePaymentSnapshot(c),same=()=>{const now=corporatePaymentSnapshot(c);if(now.fingerprint!==original.fingerprint)throw new Error('对公只读核对期间身份或原字段变化');return now;},rows=[];
  for(const row of original.rows['24']){
   const digits=/^[0-9 ]+$/.test(row.account.value)?row.account.value.replaceAll(' ',''):null;
   const identityHash=await corporateDigest(JSON.stringify([row.name.value,row.bank.value,digits]));same();
   const accountHash=digits===null?null:await corporateDigest(digits);same();
   rows.push({index:row.index,planned:original.targets.some(t=>t.row.index===row.index),amount:row.amount.value,identity_sha256:identityHash,account_sha256:accountHash,digits_length:digits?.length??0,needsNormalization:digits!==null&&row.account.value.includes(' ')});
  }
  const snapshotHash=await corporateDigest(original.fingerprint);same();
  return{ok:true,action:'inspect_corporate_payments',serial:c.serial,system_write:false,expectedAmount:c.amount,counts:{corporate:original.rows['24'].length,personal:original.rows['22'].length,card:original.rows['285'].length},rows,snapshot_sha256:snapshotHash};
 }
 async function fillCorporateTransfers(c,range={}){
  if(range.start!==undefined&&range.start!==0)throw new Error('全对公仅允许一次完整填入，不分批');
  let original=corporatePaymentSnapshot(c),expected=JSON.parse(JSON.stringify(original.stable));
  const binding=JSON.stringify({serial:c.serial,amount:c.amount,payments:c.payments});
  const verify=()=>{const now=corporatePaymentSnapshot(c);if(JSON.stringify({serial:c.serial,amount:c.amount,payments:c.payments})!==binding||JSON.stringify(now.stable)!==JSON.stringify(expected))throw new Error('对公填写期间身份或原字段变化，停止保存');return now;};
  const initialErrors=paymentValidationErrors();if(initialErrors.length)return{ok:false,action:'fill_corporate_transfers',serial:c.serial,not_saved:true,saveRequested:false,error_code:'corporate_native_validation_rejected',validation_error_count:initialErrors.length,requiresReadback:true};
  const normalizations=[];
  // Hash every permitted format correction before making any field changes.
  for(const {plan:p,row} of original.targets)if(row.account.value.includes(' ')){
   if(row.account.readOnly||row.account.disabled)throw new Error('本案账号格式需要规范化但字段不可编辑');
   const before=await corporateDigest(row.account.value);verify();const after=await corporateDigest(p.bank_account.replaceAll(' ',''));verify();
   normalizations.push({row:row.index,before_sha256:before,after_sha256:after,format_only:true,digits_length:p.bank_account.replaceAll(' ','').length});
  }
  const change=(field,value)=>{verify();if(field.value===value)return;const item=one(expected.filter(x=>x.id===field.id),'对公目标快照缺失');item.value=value;set(field.id,value);verify();};
  for(const {plan:p,row} of original.targets){
   if(row.account.value.includes(' '))change(row.account,p.bank_account.replaceAll(' ',''));
   change(row.amount,p.amount);if(Object.hasOwn(p,'remark'))change(row.remark,p.remark);
  }
  for(const key of ['24','19']){verify();$.F.getDataToContext(key);verify();}
  for(let i=0;i<80;i++){
   verify();const errors=paymentValidationErrors();if(errors.length)return{ok:false,action:'fill_corporate_transfers',serial:c.serial,not_saved:true,saveRequested:false,error_code:'corporate_native_validation_rejected',validation_error_count:errors.length,requiresReadback:true};
   if(cents($('#formWF_YBX_19_t-yt_amt').val())===cents(c.amount)&&cents($('#formWF_YBX_19_t-wt_amt').val())===0)return{ok:true,action:'fill_corporate_transfers',serial:c.serial,rows:original.targets.map(t=>({index:t.row.index,amount:t.plan.amount})),accountNormalization:normalizations,saveRequested:false,requiresReadback:true};
   await delay(100);
  }
  verify();return{ok:false,action:'fill_corporate_transfers',serial:c.serial,not_saved:true,saveRequested:false,error_code:'corporate_payment_totals_readiness_timeout',requiresReadback:true};
 }
 async function saveCorporatePayments(c){
  const original=corporatePaymentSnapshot(c),verify=()=>{const now=corporatePaymentSnapshot(c);if(now.fingerprint!==original.fingerprint)throw new Error('对公保存期间身份或原字段变化，停止保存');for(const {plan:p,row} of now.targets)if(cents(row.amount.value)!==cents(p.amount)||row.account.value!==p.bank_account.replaceAll(' ','')||(Object.hasOwn(p,'remark')&&row.remark.value!==p.remark))throw new Error('对公原生金额、账号格式或附言与配置不符');return now;};
  verify();
  const initialErrors=paymentValidationErrors();if(initialErrors.length)return{ok:false,action:'save_corporate_payments',serial:c.serial,not_saved:true,saveRequested:false,error_code:'corporate_native_validation_rejected',validation_error_count:initialErrors.length,requiresReadback:true};
  for(const key of ['24','19']){verify();$.F.getDataToContext(key);verify();}
  let closed=false;
  for(let i=0;i<80;i++){
   verify();const errors=paymentValidationErrors();if(errors.length)return{ok:false,action:'save_corporate_payments',serial:c.serial,not_saved:true,saveRequested:false,error_code:'corporate_native_validation_rejected',validation_error_count:errors.length,requiresReadback:true};
   if(cents($('#formWF_YBX_19_t-yt_amt').val())===cents(c.amount)&&cents($('#formWF_YBX_19_t-wt_amt').val())===0){closed=true;break;}
   await delay(100);
  }
  if(!closed)return{ok:false,action:'save_corporate_payments',serial:c.serial,not_saved:true,saveRequested:false,error_code:'corporate_payment_totals_readiness_timeout',requiresReadback:true};
  verify();click(one([...document.querySelectorAll('.foot_winWF_YBX_13')].filter(visible),'结算保存窗口不唯一'),'保存');
  for(let i=0;i<80;i++){
   guard(c,false);const errors=paymentValidationErrors();if(errors.length)return{ok:false,action:'save_corporate_payments',serial:c.serial,saveRequested:true,execution_status:'unknown_verify_before_retry',error_code:'corporate_native_validation_rejected',validation_error_count:errors.length,requiresReadback:true};
   const f=document.getElementById('iframepage_print'),editor=visible(document.getElementById('formWF_YBX_19_t-bx_amt')),text=f?.contentDocument?.body?.innerText||'';
   if(!editor&&visible(f)&&text.includes('报销单号：'+c.serial))return{ok:true,action:'save_corporate_payments',serial:c.serial,expectedAmount:c.amount,saveRequested:true,paymentWindowExited:true,requiresReadback:true};
   if(!editor&&/报销单号[：:]\s*\d+/.test(text)&&!text.includes('报销单号：'+c.serial))throw new Error('保存后原生预览单号变化，保留原意图');
   if(editor)verify();await delay(100);
  }
  guard(c,false);return{ok:false,action:'save_corporate_payments',serial:c.serial,saveRequested:true,execution_status:'unknown_verify_before_retry',error_code:'corporate_payment_save_readiness_timeout',requiresReadback:true};
 }
 async function savePayments(c){
  if(c.payments?.some(p=>p.mode==='corporate_transfer'))return saveCorporatePayments(c);
  guard(c);const plans=c.payments.filter(p=>p.mode==='bank_transfer'),cards=c.payments.filter(p=>p.mode==='corporate_card');
  function checkedInputs(){
   guard(c);if(!visible(document.getElementById('formWF_YBX_19_t-bx_amt'))||cents($('#formWF_YBX_19_t-bx_amt').val())!==cents(c.amount))throw new Error('结算页面或待报销金额不符');
   const values=[];let invalidRemark;
   for(let i=0;i<plans.length;i++){
    const p=plans[i],row=['sno','sname','amount','cnt1','remark'].map(k=>$('#formWF_YBX_22_t-'+k+'_'+i).val());
    if(row[0]!==p.person_id||row[1]!==p.name||cents(row[2])!==cents(p.amount)||!row[3])throw new Error('转卡身份、金额或银行卡不符：'+i);
    if(typeof row[4]!=='string'||transferRemarkUnits(row[4])>25)invalidRemark=i;
    values.push(row);
   }
   for(let i=0;i<cards.length;i++){
    const p=cards[i],row=['gwk_banksname','amount','gwk_bankacnt','gwk_seq_no'].map(k=>$('#formWF_YBX_285_t-'+k+'_'+i).val());
    if(row[0]!==p.name||cents(row[1])!==cents(p.amount)||!row[2]||!row[3])throw new Error('公务卡记录未完成系统绑定：'+i);
    values.push(row);
   }
   return{values:JSON.stringify(values),invalidRemark};
  }
  const original=checkedInputs();
  if(original.invalidRemark!==undefined)return{ok:false,action:'save_payments',serial:c.serial,not_saved:true,saveRequested:false,error_code:'payment_remark_too_long',row:original.invalidRemark,max_units:25,requiresReadback:true};
  const initialErrors=paymentValidationErrors();if(initialErrors.length)return{ok:false,action:'save_payments',serial:c.serial,not_saved:true,saveRequested:false,error_code:'payment_native_validation_rejected',validation_errors:initialErrors,requiresReadback:true};
  const remittances=zeroRemittanceSnapshot();
  // 原生合计可能晚于金额字段更新；仅等待，期间不重填、不更新上下文、不请求保存。
  let totalsClosed=false;
  for(let i=0;i<80;i++){
   if(checkedInputs().values!==original.values)throw new Error('等待支付合计期间原支付输入变化，停止保存');
   if(zeroRemittanceSnapshot().fingerprint!==remittances.fingerprint)throw new Error('等待支付合计期间原汇款行变化，停止保存');
   const errors=paymentValidationErrors();
   if(errors.length)return{ok:false,action:'save_payments',serial:c.serial,not_saved:true,saveRequested:false,error_code:'payment_native_validation_rejected',validation_errors:errors,requiresReadback:true};
   if(cents($('#formWF_YBX_19_t-yt_amt').val())===cents(c.amount)&&cents($('#formWF_YBX_19_t-wt_amt').val())===0){totalsClosed=true;break;}
   await delay(100);
  }
  if(!totalsClosed)return{ok:false,action:'save_payments',serial:c.serial,not_saved:true,saveRequested:false,error_code:'payment_totals_readiness_timeout',requiresReadback:true};
  const checkBindings=()=>{if(checkedInputs().values!==original.values)throw new Error('规范化前个人支付输入变化，停止保存');};
  const normalized=await normalizeZeroRemittanceAccounts(c,remittances,checkBindings),remittanceNormalization=normalized.changes;
  checkBindings();if(zeroRemittanceSnapshot().fingerprint!==normalized.snapshot.fingerprint)throw new Error('保存前汇款行变化，停止保存');
  const preSaveErrors=paymentValidationErrors();if(preSaveErrors.length)return{ok:false,action:'save_payments',serial:c.serial,not_saved:true,saveRequested:false,error_code:'payment_native_validation_rejected',validation_errors:preSaveErrors,remittanceNormalization,requiresReadback:true};
  $.F.getDataToContext('22');$.F.getDataToContext('285');$.F.getDataToContext('19');
  checkBindings();if(zeroRemittanceSnapshot().fingerprint!==normalized.snapshot.fingerprint)throw new Error('原生支付上下文同步后汇款行变化，停止保存');
  if(cents($('#formWF_YBX_19_t-bx_amt').val())!==cents(c.amount)||cents($('#formWF_YBX_19_t-yt_amt').val())!==cents(c.amount)||cents($('#formWF_YBX_19_t-wt_amt').val())!==0)throw new Error('原生支付上下文同步后合计变化，停止保存');
  const contextErrors=paymentValidationErrors();if(contextErrors.length)return{ok:false,action:'save_payments',serial:c.serial,not_saved:true,saveRequested:false,error_code:'payment_native_validation_rejected',validation_errors:contextErrors,remittanceNormalization,requiresReadback:true};
  click(one([...document.querySelectorAll('.foot_winWF_YBX_13')].filter(visible),'结算保存窗口不唯一'),'保存');
  for(let i=0;i<80;i++){
   guard(c,false);const errors=paymentValidationErrors();
   if(errors.length)return{ok:false,action:'save_payments',serial:c.serial,not_saved:true,saveRequested:true,error_code:'payment_native_validation_rejected',validation_errors:errors,remittanceNormalization,requiresReadback:true};
   const f=document.getElementById('iframepage_print');
   if(!visible(document.getElementById('formWF_YBX_19_t-bx_amt'))&&visible(f)&&f.contentDocument?.body.innerText.includes('报销单号：'+c.serial))return{ok:true,action:'save_payments',serial:c.serial,expectedAmount:c.amount,saveRequested:true,paymentWindowExited:true,remittanceNormalization,requiresReadback:true};
   await delay(100);
  }
  return{ok:false,action:'save_payments',serial:c.serial,saveRequested:true,execution_status:'unknown_verify_before_retry',error_code:'payment_save_readiness_timeout',remittanceNormalization,requiresReadback:true};
 }
 async function dismissPaymentValidationError(c){
  guard(c,false);if(!visible(document.getElementById('formWF_YBX_19_t-bx_amt'))||cents($('#formWF_YBX_19_t-bx_amt').val())!==cents(c.amount))throw new Error('仅处理本单金额一致的已打开支付页');
  const prompts=paymentPrompts().filter(x=>x.title==='提示信息');
  const prompt=one(prompts,'须有唯一支付校验提示'),isRemark=prompt.message.replace(/\s+/g,'')===remarkLengthMessage,isBank=prompt.message===bankDigitsMessage;
  if(!isRemark&&!isBank)throw new Error('只关闭已知支付校验，不处理其他确认');
  if(paymentValidationErrors().some(m=>isBank?m!==bankDigitsMessage:m.replace(/\s+/g,'')!==remarkLengthMessage))throw new Error('存在其他支付校验错误，不能关闭并返回');
  const personal=isBank?personalPaymentSnapshot(c):null,remittances=isBank?zeroRemittanceSnapshot(true):null;
  if(isBank&&!remittances.rows.length)throw new Error('缺少本次原生零金额汇款行，不关闭提示');
  const verify=()=>{guard(c,false);if(isBank&&(personalPaymentSnapshot(c)!==personal||zeroRemittanceSnapshot(true).fingerprint!==remittances.fingerprint))throw new Error('支付校验恢复期间原输入变化，不继续返回');};
  verify();const button=one([...prompt.element.querySelectorAll('button')].filter(e=>visible(e)&&!e.disabled&&(e.innerText||'').trim()==='确认'),'支付校验确认按钮不唯一');button.click();
  await wait(()=>!visible(prompt.element),'支付校验提示尚未关闭');verify();
  click(one([...document.querySelectorAll('.foot_winWF_YBX_13')].filter(visible),'原支付页返回入口不唯一'),'返回');
  await wait(()=>{guard(c,false);const f=document.getElementById('iframepage_print');return!visible(document.getElementById('formWF_YBX_19_t-bx_amt'))&&visible(f)&&f.contentDocument?.body.innerText.includes('报销单号：'+c.serial);},'原单据预览尚未恢复；保留原支付意图，不重放');
  return{ok:true,action:'dismiss_payment_validation_and_return',serial:c.serial,system_write:false,saveRequested:false,validationKind:isBank?'zero_remittance_account_format':'transfer_remark_length',validationDismissed:true,returnedToPreview:true,requiresReadback:true};
 }
 async function dismissPaymentRemarkError(c){return{...await dismissPaymentValidationError(c),action:'dismiss_payment_remark_validation_and_return'};}
 root.ReimbursementAutomation={version:'0.3.1',guard,wait,click,dialog,set,cents,inspectHome,closePayments,inspectDraft,recoverPreview,diagnosePreview,recoverMain,navigate,inspectBasic,closeBasic,closeEmptyExplanation,openInvoiceGroup,inspectInvoiceGroup,closeInvoiceGroup,fillInvoiceCategory,teacherCandidate,lookupTeacher,fillBasic,fillExpenses,fillTransfers,inspectCorporates,fillCorporateTransfers,saveCorporatePayments,transferRemarkUnits,validateTransferRemark,fillExplanation,noContract,queryMoney,fillFunding,preReview,cardQuery,cardSelect,audit,prepareReview,acceptPromise,departmentApprovers,status,waitSection,savePayments,dismissPaymentValidationError,dismissPaymentRemarkError};
})(globalThis);
