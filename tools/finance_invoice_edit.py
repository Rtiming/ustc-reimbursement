#!/usr/bin/env python3
"""受控修改已认证票的行程信息；原生入口、原件和单次意图分别核对。"""
import argparse
import json
import re
from datetime import date
from pathlib import Path
from finance_invoice_retry import digest, load, sha, write, run_browser

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'ustc-invoice-travel-edit-v1'
MEDICAL_SCHEMA = 'ustc-invoice-medical-edit-v1'


def validate(request):
    if not isinstance(request, dict) or request.get('schema') not in [SCHEMA, MEDICAL_SCHEMA]:
        raise ValueError('需要正式行程修改请求')
    medical=request['schema']==MEDICAL_SCHEMA
    for key, pattern in [('case_id', r'[A-Za-z0-9_-]+'), ('tab_id', r'[0-9]+'),
                         ('expected_userid', r'[A-Za-z0-9_.@-]{1,80}'),
                         ('invoice_number', r'[0-9]{10}' if medical else r'[0-9]{20}'), ('native_guid', r'[A-Fa-f0-9]{32}'),
                         ('source_sha256', r'[a-f0-9]{64}'), ('amount', r'[0-9]+\.[0-9]{2}')]:
        if not isinstance(request.get(key), str) or not re.fullmatch(pattern, request[key]):
            raise ValueError('行程请求字段不符：' + key)
    source = Path(request.get('source_path', ''))
    if not source.is_absolute() or sha(source) != request['source_sha256']:
        raise ValueError('原件路径或SHA不符')
    changes = request.get('changes')
    if request.get('native_status') not in ['NORMAL','ERROR']:
        raise ValueError('须冻结当前原生状态；红字已认证的出行人缺项也可能为ERROR')
    if medical:
        if not re.fullmatch(r'[0-9]{8}',str(request.get('invoice_code',''))) or not isinstance(changes,dict) or set(changes)-{'insurance_type'} or changes.get('insurance_type','自费')!='自费':raise ValueError('医疗检查须有8位代码，只支持已确认的自费类型')
    elif not isinstance(changes, dict) or 'traveler' not in changes or set(changes)-{'traveler','person_id','departure_date','arrival_date','seat_class','arrival_city','arrival_airport'} or not isinstance(changes['traveler'], str) or not 1 <= len(changes['traveler']) <= 30:
        raise ValueError('仅支持有来源依据的姓名、学工号、出发/到达日期、舱位')
    if 'person_id' in changes and not re.fullmatch(r'[A-Za-z0-9]{3,30}',str(changes['person_id'])):raise ValueError('学工号格式不符')
    for key in ['departure_date','arrival_date']:
        if key in changes:
            if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',str(changes[key])):raise ValueError('行程日期须为ISO日期')
            date.fromisoformat(changes[key])
    if 'seat_class' in changes and changes['seat_class'] not in ['经济舱','公务舱','头等舱']:raise ValueError('须有实际舱位依据')
    if 'arrival_city' in changes and not re.fullmatch(r'[\u4e00-\u9fff]{2,20}',str(changes['arrival_city'])):raise ValueError('到达城市须取原件文字')
    if 'arrival_airport' in changes and (not changes.get('arrival_city') or not re.fullmatch(r'[\u4e00-\u9fffA-Za-z0-9（）() ]{2,50}',str(changes['arrival_airport']))):raise ValueError('机场须有城市及独立来源依据')
    evidence = request.get('evidence')
    if not isinstance(evidence, list) or not evidence:
        raise ValueError('须有出行人及行程的来源证据')
    for item in evidence:
        if not isinstance(item, dict) or not isinstance(item.get('basis'), str) or not item['basis'].strip() or sha(item['path']) != item.get('sha256'):
            raise ValueError('行程依据或证据SHA不符')
    return request


def guard_script(request):
    return r'''
 const req=REQUEST,vis=e=>!!e&&e.getClientRects().length>0;
 if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX'||String($.UC.bindData('#0-userinfo.userid#'))!==req.expected_userid)throw Error('账号或项目改变');
 if(String($.UC.bindData('#61-serialno#')||'').trim())throw Error('当前有报销单上下文，不能修改票池');
'''.replace('REQUEST', json.dumps(request, ensure_ascii=False))


def open_script(request, request_id):
    validate(request)
    return r'''(()=>{GUARD
 const requestId=REQUEST_ID,registry=window.__ustcInvoiceEdit||(window.__ustcInvoiceEdit={});
 if(registry[requestId])throw Error('原请求已发起；只读恢复');
 if([...document.querySelectorAll('.ui-dialog')].some(vis))throw Error('存在未处理弹框');
 const element=document.getElementById('gridWF_YBX_53');
 if(!element?.grid||!vis(element)||element.grid.hDiv.loading)throw Error('票池尚未就绪');
 const q=$(element),ids=q.jqGrid('getDataIDs'),selected=q.jqGrid('getGridParam','selarrrow');
 if(!Array.isArray(selected)||selected.length)throw Error('保留已有选票，停止修改');
 const rows=ids.map(id=>({id,row:q.jqGrid('getRowData',id)})).filter(x=>x.row['t.tk_guid']===req.native_guid);
 if(rows.length!==1)throw Error('本页没有唯一目标');
 const {id,row}=rows[0],numbers=[...new Set(String(row['t.tk_code_abst']||'').match(/[0-9]{20}/g)||[])];
 const medical=req.schema==='ustc-invoice-medical-edit-v1',identity=medical?row['t.tk_type']==='MEDICAL'&&row['t.tk_code']===req.invoice_code&&row['t.tk_code_abst']==='医/'+req.invoice_code+'/'+req.invoice_number:numbers.length===1&&numbers[0]===req.invoice_number;
 if(!identity||row['t.tk_mod_flag']!==req.native_status||row['t.canedit']!=='Y'||String(row['t.serial_no']||'').trim()||!['','N','0'].includes(String(row['t.PC_Has_chosen']||''))||Number(row['t.tk_total_amt'])!==Number(req.amount))throw Error('票号、金额、认证或保护状态不符');
 if(!String(row['t.auth_rst_desc']).startsWith('正常(已认证)')||!medical&&!String(row['t.auth_rst_desc']).includes('出行人不能为空'))throw Error('当前不属于已适配的出行人缺失提示');
 const defs=q.jqGrid('getGridParam','colModel').filter(c=>c.name==='t.tk_code_abst').flatMap(c=>c.attachbtn||[]).filter(b=>b.btnguid==='BBACD62A945A41FBBC663E484ACECBE2');
 if(defs.length!==1||defs[0].caption!=='修 改'||defs[0].call_proc!==true||defs[0].procs?.length!==1||defs[0].procs[0]!=='`$a53c0687c9a9ded12adcabee1540caed:#53-t.tk_mainguid#;#53-t.inner_ord#'||defs[0].acts!=="YBX_JSONDATA['发票']['修改发票_入口']=='申请报销单1':1230@YBX_JSONDATA['发票']['修改发票_入口']=='申请报销单':288")throw Error('原生修改定义改变，须重新适配');
 const buttons=[...document.getElementById(id).querySelectorAll('[btnname]')].filter(e=>vis(e)&&!e.disabled&&e.getAttribute('btnname')==='修 改');
 if(buttons.length!==1)throw Error('目标行修改入口不唯一');
 registry[requestId]={case_id:req.case_id,native_guid:req.native_guid,invoice_number:req.invoice_number,request_digest:REQUEST_DIGEST,phase:'open_requested',before_row:row};
 buttons[0].click();registry[requestId].phase='open_button_returned';
 return{ok:true,action:'open',request_id:requestId,marker:registry[requestId],system_write:true,claim_created:false,requires_independent_readback:true};
})()'''.replace('GUARD', guard_script(request)).replace('REQUEST_ID', json.dumps(request_id)).replace('REQUEST_DIGEST', json.dumps(digest(request)))


def inspect_script(request, recover_id=None, original_marker=None):
    return r'''(async()=>{GUARD
 const forms=[],definitions=[],omissions=[];
 const serial=v=>{if(typeof v==='function')return String(v);if(v===undefined)return null;const s=JSON.stringify(v);if(s.length>30000){omissions.push('definition_limit');return null;}return v;};
 for(const funcno of Object.keys($.F?.list||{})){const f=$.F.list[funcno];
  const id=$.F.idFunc.getFormID(funcno),element=document.getElementById(id);if(!vis(element))continue;
  const inputs=[...element.querySelectorAll('input,select,textarea')].filter(e=>e.type!=='password').map(e=>({id:e.id,name:e.name||'',type:e.type,value:e.value,visible:vis(e),disabled:!!e.disabled,readonly:!!e.readOnly,options:e.options?[...e.options].map(x=>({value:x.value,text:x.text,selected:x.selected})):null}));
  forms.push({funcno,id,title:f.title,multiCount:f.multiCount,fields:(f.formFields||[]).map(x=>Object.fromEntries(Object.entries(x).filter(([k,v])=>v===null||['string','number','boolean','function'].includes(typeof v)).map(([k,v])=>[k,serial(v)]))),inputs});
 }
 const buttons=[...document.querySelectorAll('button[btnname]')].filter(vis).map(e=>({id:e.id,btnname:e.getAttribute('btnname'),text:e.innerText,disabled:!!e.disabled,owner:e.parentElement?.className}));
 for(const pageKey of Object.keys($.page?.list||{})){const page=$.page?.list?.[pageKey];for(const win of page?.wins||[]){
  const controls=[...document.querySelectorAll('.foot_winWF_YBX_'+win.winno+' button[btnname]')].filter(vis);if(!controls.length)continue;
  for(const b of win.func_btns||[])if(controls.some(e=>e.getAttribute('btnname')===b.caption)){
   const d={pageKey,winno:win.winno};for(const k of ['caption','btnguid','call_proc','pre_compute','pre_check','check_func','procs','post_check','acts','affects','jscontent','clickquery','clickqueryhint','beforeclick','afterclick'])d[k]=serial(b[k]);definitions.push(d);
  }
 }}
 const grids=[];for(const [funcno,def] of Object.entries($.Q?.list||{})){const id=$.Q.idFunc.getGridID(funcno),e=document.getElementById(id);if(!e?.grid||!vis(e)||funcno==='53')continue;const q=$(e),ids=q.jqGrid('getDataIDs');grids.push({funcno,id,columns:def.colModel,nativeQuery:def.url,records:q.jqGrid('getGridParam','records'),rows:ids.slice(0,100).map(id=>({id,row:q.jqGrid('getRowData',id)})),loading:!!e.grid.hDiv.loading,selected:q.jqGrid('getGridParam','selarrrow'),ondblClickRow:serial(q.jqGrid('getGridParam','ondblClickRow')),onSelectRow:serial(q.jqGrid('getGridParam','onSelectRow'))});}
 return{ok:omissions.length===0,schema:'ustc-invoice-edit-inspect-v1',observedAt:new Date().toISOString(),identity:{userid:req.expected_userid},projid:'WF_YBX',currentPage:$.page.currPage,
  request_digest:REQUEST_DIGEST,forms,grids,buttons,buttonDefinitions:definitions,omissions,
  targetPool:(()=>{const e=document.getElementById('gridWF_YBX_53');if(!e?.grid||!vis(e))return null;const q=$(e);return{loading:!!e.grid.hDiv.loading,rows:q.jqGrid('getDataIDs').map(id=>q.jqGrid('getRowData',id)).filter(r=>r['t.tk_guid']===req.native_guid)};})(),
  context:{selected_guid:String($.UC.bindData('#53-t.tk_guid#')||''),selected_mainguid:String($.UC.bindData('#53-t.tk_mainguid#')||''),selected_inner_ord:String($.UC.bindData('#53-t.inner_ord#')||''),edit_guid:String($.UC.bindData('#61-tks_guid#')||''),edit_mainguid:String($.UC.bindData('#61-tk_mainguid#')||''),currentFunc:String($.UC.bindData('#0-CURRFUNC#')||''),nativeEditEntry:typeof YBX_JSONDATA==='object'?YBX_JSONDATA['发票']?.['修改发票_入口']:null,documentHidden:document.hidden},
  visibleDialogs:[...document.querySelectorAll('.ui-dialog')].filter(vis).map(e=>({text:e.innerText})),
  airportBinding:['WF_YBX_324','WF_YBX_325'].map(k=>({key:k,...Object.fromEntries(['did','dstr','backwinno','backprojid','mainWinno','pageName'].map(f=>[f,serial($.page.list[k]?.[f])]))})),
  airportDialog:[...document.querySelectorAll('.ui-dialog-content')].filter(e=>vis(e)&&e.closest('.ui-dialog')?.innerText.includes('机场/城市选择')).map(e=>({id:e.id,buttons:Object.fromEntries(Object.entries($(e).dialog('option','buttons')||{}).map(([k,v])=>[k,serial(v)])),dataKeys:Object.keys($(e).data())})),
  airportControls:[...document.querySelectorAll('input,button,a,[onclick]')].filter(e=>vis(e)&&e.closest('.ui-dialog')?.innerText.includes('机场/城市选择')).slice(0,120).map(e=>({id:e.id,tag:e.tagName,type:e.type,value:e.value,text:(e.innerText||'').slice(0,100),onclick:e.getAttribute('onclick')})),
  visibleText:document.body.innerText.slice(0,12000),
  fieldRuntime:{passenger:String($.UC.bindData('#1150-t.passenger_sname#')||''),passenger_id:String($.UC.bindData('#1150-t.passenger_sno#')||''),getDataToContext:serial($.F.getDataToContext),getInputData:serial($.F.getInputData),draftAfterSource:serial(window.ybx_jbxx_next_sx_after),draftAfterV1Source:serial(window.ybx_jbxx_next_sx_after_v1)},
  medicalRuntime:{paytype:String($.UC.bindData('#1360-t.medical_paytype#')||''),activeRequests:Number($.active||0),
   validationErrors:($.F.validators?.['1360']?.errorList||[]).map(e=>({id:e.element?.id,message:e.message})),
   recentNativeResources:performance.getEntriesByType('resource').filter(e=>new URL(e.name).origin===location.origin).slice(-35).map(e=>({path:new URL(e.name).pathname,status:e.responseStatus,started_at_utc:new Date(performance.timeOrigin+e.startTime).toISOString(),duration:e.duration,initiator:e.initiatorType}))},
  original_operation_result:ORIGINAL_MARKER?(()=>{const text=document.getElementById(ORIGINAL_MARKER)?.textContent;if(!text||text==='pending')return {state:text||'missing'};return JSON.parse(text)})():null,
  edit_marker:RECOVER_ID?(window.__ustcInvoiceEdit||{})[RECOVER_ID]||null:null,related_edit_markers:RECOVER_ID?Object.entries(window.__ustcInvoiceEdit||{}).filter(([k,v])=>(k===RECOVER_ID||k.startsWith(RECOVER_ID+'_'))&&v.case_id===req.case_id&&v.native_guid===req.native_guid&&v.request_digest===REQUEST_DIGEST).map(([request_id,marker])=>({request_id,marker})):[],system_write:false};
})()'''.replace('GUARD', guard_script(request)).replace('REQUEST_DIGEST', json.dumps(digest(request))).replace('RECOVER_ID', json.dumps(recover_id)).replace('ORIGINAL_MARKER',json.dumps(original_marker))


def airport_script(request, request_id):
    validate(request)
    if not request['changes'].get('arrival_city'):raise ValueError('须有原票到达城市依据')
    return r'''(()=>{GUARD
 if(String($.UC.bindData('#61-tks_guid#')||'').trim()!==req.native_guid||!vis(document.getElementById('formWF_YBX_1150')))throw Error('不是本票行程表单');
 const field=($.F.list['1150']?.formFields||[]).filter(f=>f.id==='t-tour_endcity');
 if(field.length!==1||field[0].inputtype!=='func'||field[0].binding_func!=='W@325@@#61-invoice_tour_endinfo#')throw Error('原生机场选择定义改变');
 const button=document.getElementById('formWF_YBX_1150_t-tour_endcity_0_btn');if(!vis(button)||button.disabled)throw Error('原生到达机场选择按钮不可用');
 button.click();return{ok:true,action:'airport-open',system_write:false};
})()'''.replace('GUARD',guard_script(request))


def airport_query_script(request, request_id):
    validate(request)
    if not request['changes'].get('arrival_city'):raise ValueError('须有原票到达城市依据')
    return r'''(()=>{GUARD
 if(String($.UC.bindData('#61-tks_guid#')||'').trim()!==req.native_guid)throw Error('机场选择上下文不是本票');
 const grid=document.getElementById('gridWF_YBX_444'),input=document.getElementById('gridWF_YBX_444_qspi'),button=document.getElementById('gridWF_YBX_444_query');
 if(!grid?.grid||!vis(grid)||grid.grid.hDiv.loading||!vis(input)||!vis(button)||button.disabled||button.getAttribute('btnname')!=='快速查询')throw Error('原生机场快速查询未就绪');
 const columns=$.Q.list['444'].colModel.map(c=>c.name);if(JSON.stringify(columns)!==JSON.stringify(['t.citycode','t.cityname','t.airportname','t.shortname']))throw Error('机场字段定义改变');
 $(input).val(req.changes.arrival_city).trigger('input').trigger('change');button.click();
 return{ok:true,action:'airport-query',city:req.changes.arrival_city,system_write:false,requires_independent_readback:true};
})()'''.replace('GUARD',guard_script(request))


def airport_select_script(request, inspection, request_id):
    """只选登记的唯一城市/机场候选，经原生选择及确定回填，不改只读机场值。"""
    validate(request)
    observed=inspection.get('result',inspection)
    if observed.get('schema')!='ustc-invoice-edit-inspect-v1' or observed.get('ok') is not True or observed.get('request_digest')!=digest(request):
        raise ValueError('机场选择须引用同案同票的独立检查')
    grids=[g for g in observed.get('grids',[]) if g.get('funcno')=='444']
    dialogs=observed.get('airportDialog',[])
    bindings=[b for b in observed.get('airportBinding',[]) if b.get('key')=='WF_YBX_324']
    if len(grids)!=1 or grids[0].get('loading') or len(grids[0]['rows'])!=grids[0]['records'] or len(dialogs)!=1 or len(bindings)!=1:
        raise ValueError('机场候选未完整加载或原生弹框不唯一')
    binding=bindings[0]
    if binding.get('did')!='formWF_YBX_1150_t-tour_endcity_0' or binding.get('dstr')!='#61-airportinfo#' or binding.get('pageName')!='机场/城市选择':
        raise ValueError('原生机场回填目标改变')
    airport=request['changes'].get('arrival_airport')
    hits=[r for r in grids[0]['rows'] if r['row'].get('t.cityname','').removesuffix('市')==request['changes'].get('arrival_city','').removesuffix('市') and r['row'].get('t.airportname')==airport]
    if not airport or len(hits)!=1:raise ValueError('须有实际机场依据且候选唯一；不能只按城市选第一行')
    confirm=dialogs[0].get('buttons',{}).get('确定','')
    if 'wfFuncDialogOKCheck' not in confirm or 'targetInputID=pageDefUpToDate.did' not in confirm or 'dstr=pageDefUpToDate.dstr' not in confirm:
        raise ValueError('原生机场确定处理器未适配')
    return r'''(()=>{GUARD
 const candidate=CANDIDATE,definition=BINDING,confirmSource=CONFIRM,dialogId=DIALOG_ID;
 if(String($.UC.bindData('#61-tks_guid#')||'').trim()!==req.native_guid)throw Error('机场上下文不是本票');
 const element=document.getElementById('gridWF_YBX_444'),dialog=document.getElementById(dialogId),input=document.getElementById('gridWF_YBX_444_qspi');
 if(!element?.grid||!vis(element)||element.grid.hDiv.loading||!vis(dialog)||input?.value!==req.changes.arrival_city)throw Error('机场查询已改变');
 const q=$(element),row=q.jqGrid('getRowData',candidate.id);
 for(const [k,v]of Object.entries(candidate.row))if(row[k]!==v)throw Error('机场候选改变');
 if(q.jqGrid('getGridParam','records')!==RECORDS)throw Error('机场候选范围改变');
 const live=$.page.list.WF_YBX_324;for(const [k,v]of Object.entries(definition))if(k!=='key'&&live[k]!==v)throw Error('机场回填定义改变');
 if(String($(dialog).dialog('option','buttons')?.['确定'])!==confirmSource)throw Error('机场确定处理器改变');
 const cell=document.getElementById(candidate.id)?.querySelector('td[aria-describedby="gridWF_YBX_444_t.cityname"]');
 const buttons=[...dialog.closest('.ui-dialog').querySelectorAll('button')].filter(e=>vis(e)&&!e.disabled&&e.innerText.trim()==='确定');
 if(!vis(cell)||buttons.length!==1)throw Error('原生机场选择控件不唯一');
 cell.click();if(q.jqGrid('getGridParam','selrow')!==candidate.id)throw Error('原生机场行未选择');
 buttons[0].click();
 return{ok:true,action:'airport-select',selected:candidate.row,system_write:false,requires_independent_readback:true};
})()'''.replace('GUARD',guard_script(request)).replace('CANDIDATE',json.dumps(hits[0],ensure_ascii=False)).replace('BINDING',json.dumps(binding,ensure_ascii=False)).replace('CONFIRM',json.dumps(confirm,ensure_ascii=False)).replace('DIALOG_ID',json.dumps(dialogs[0]['id'])).replace('RECORDS',json.dumps(grids[0]['records']))


def journey_script(request, request_id):
    validate(request)
    return r'''(()=>{GUARD
 const requestId=REQUEST_ID,registry=window.__ustcInvoiceEdit||(window.__ustcInvoiceEdit={});
 if(registry[requestId])throw Error('原请求已发起；只读恢复');
 const no=document.getElementById('formWF_YBX_404_t-invoice_no'),amount=document.getElementById('formWF_YBX_404_t-invoice_total');
 if(!vis(no)||no.value!==req.invoice_number||!vis(amount)||Number(amount.value)!==Number(req.amount)||String($.UC.bindData('#53-t.tk_guid#'))!==req.native_guid)throw Error('修改窗口票号、金额或GUID不符');
 const page=$.page.list.WF_YBX_288,defs=(page?.wins||[]).filter(w=>String(w.winno)==='296').flatMap(w=>w.func_btns||[]).filter(b=>b.caption==='修改行程');
 if(defs.length!==1||defs[0].btnguid!=='696587F6D64D4E1985E1A2FE132D8E4E'||defs[0].call_proc!==false||defs[0].acts!=='1==1:938'||defs[0].procs?.length)throw Error('原生修改行程定义改变');
 const buttons=[...document.querySelectorAll('.foot_winWF_YBX_296 button[btnname]')].filter(e=>vis(e)&&!e.disabled&&e.getAttribute('btnname')==='修改行程');
 if(buttons.length!==1)throw Error('修改行程按钮不唯一');
 registry[requestId]={case_id:req.case_id,native_guid:req.native_guid,invoice_number:req.invoice_number,request_digest:REQUEST_DIGEST,phase:'journey_requested'};
 buttons[0].click();registry[requestId].phase='journey_button_returned';
 return{ok:true,action:'journey',request_id:requestId,marker:registry[requestId],system_write:false,requires_independent_readback:true};
})()'''.replace('GUARD',guard_script(request)).replace('REQUEST_ID',json.dumps(request_id)).replace('REQUEST_DIGEST',json.dumps(digest(request)))


def save_script(request, inspection, request_id):
    validate(request)
    observed = inspection.get('result', inspection)
    if observed.get('schema') != 'ustc-invoice-edit-inspect-v1' or observed.get('ok') is not True or observed.get('request_digest') != digest(request):
        raise ValueError('保存须引用同案同票请求的独立表单检查')
    forms = [f for f in observed.get('forms', []) if f.get('funcno') == '1150']
    if len(forms) != 1:
        raise ValueError('须已打开唯一行程维护表单')
    passengers = [v for v in forms[0]['inputs'] if re.fullmatch(r'formWF_YBX_1150_t-passenger_sname_[0-9]+',v['id'])]
    if forms[0].get('multiCount',1)!=1 or len(passengers)!=1:
        raise ValueError('当前只适配一个原生行程行；多行行程须另行明确分段，不复制整票数据')
    if not 1 <= len(passengers) <= 10 or any(v['disabled'] or v['readonly'] or not v['visible'] or v['value'] not in ['',request['changes']['traveler']] for v in passengers):
        raise ValueError('乘坐人控件不完整、不可改或已有其他姓名，停止覆盖')
    definitions = [b for b in observed.get('buttonDefinitions', []) if str(b['winno']) == '938' and b['caption'] == '保存']
    if len(definitions) != 1 or definitions[0]['call_proc'] is not True or definitions[0]['check_func'] != '`$e34e194182c92f9a07b159e1c0f9bbec:#1150-t.passenger_sname#' or definitions[0]['procs'] != ['`$94b021dc268add7e4ac343a3c9548771:#1150-t.tripmodel#;#1150-t.tour_starttime#;#1150-t.tour_startdate#;#1150-t.tour_startcity_tx#;#1150-t.tour_startcity#;#1150-t.tour_seatclass#;#1150-t.tour_endtime#;#1150-t.tour_enddate#;#1150-t.tour_endcity_tx#;#1150-t.tour_endcity#;#1150-t.passenger_sno#;#1150-t.passenger_sname#;#1150-t.passenger_scard#;#61-tks_guid#;#61-tk_mainguid#;#395-t.inv_type#;#0-userinfo.username#;#0-userinfo.userid#']:
        raise ValueError('原生行程保存定义改变')
    baseline = [v for f in observed['forms'] if f['funcno'] in ['404','395','1150'] for v in f['inputs']]
    names={'traveler':'passenger_sname','person_id':'passenger_sno','departure_date':'tour_startdate','arrival_date':'tour_enddate','seat_class':'tour_seatclass'}
    patches={f'formWF_YBX_1150_t-{names[k]}_0':v for k,v in request['changes'].items() if k in names}
    inputs={v['id']:v for v in forms[0]['inputs']}
    if request['changes'].get('arrival_city'):
        city_id='formWF_YBX_1150_t-tour_endcity_tx_0'
        actual=inputs.get(city_id,{}).get('value','')
        city=request['changes']['arrival_city'].removesuffix('市')+'市'
        airport=request['changes'].get('arrival_airport')
        if airport:
            chosen=inputs.get('formWF_YBX_1150_t-tour_endcity_0',{}).get('value','')
            if chosen!=city+'-'+airport:raise ValueError('须先通过原生机场候选选择并独立核对实际回填值')
            # 原生机场选择仅回填显示字段；保存过程另取隐藏城市文本。
            # 只按已选择的完整原生值派生该固定字段，禁止沿用识别错误的旧城市。
            if city_id not in inputs or inputs[city_id].get('visible') is not False:raise ValueError('原生隐藏城市字段未适配')
            patches[city_id]=city
        elif actual.removesuffix('市')!=request['changes']['arrival_city'].removesuffix('市'):
            raise ValueError('系统到达城市与原件不符，须先通过原生机场候选选择更正')
    for field in forms[0].get('fields',[]):
        if field.get('nullable') is not False or field.get('hidden') is True:continue
        control=inputs.get('formWF_YBX_1150_'+field['id']+'_0')
        if control and control['visible'] and not str(patches.get(control['id'],control['value'])).strip():
            raise ValueError('原生必填字段缺来源值：'+str(field.get('label') or field['id']))
    if set(patches)-set(inputs):raise ValueError('拟修改字段未实际加载')
    return r'''(()=>{GUARD
 const requestId=REQUEST_ID,registry=window.__ustcInvoiceEdit||(window.__ustcInvoiceEdit={}),baseline=BASELINE,definition=DEFINITION,patches=PATCHES;
 const stable=v=>JSON.stringify(v&&typeof v==='object'?Array.isArray(v)?v.map(x=>JSON.parse(stable(x))):Object.fromEntries(Object.keys(v).sort().map(k=>[k,JSON.parse(stable(v[k]))])):v);
 if(registry[requestId])throw Error('原保存已发起；只读恢复');
 if(String($.UC.bindData('#61-tks_guid#')||'').trim()!==req.native_guid||String($.UC.bindData('#53-t.tk_guid#'))!==req.native_guid)throw Error('保存上下文不是本票');
 for(const b of baseline){const e=document.getElementById(b.id);if(!e||e.value!==b.value)throw Error('表单原值改变：'+b.id);}
 const defs=($.page.list.WF_YBX_938?.wins||[]).filter(w=>String(w.winno)==='938').flatMap(w=>w.func_btns||[]).filter(b=>b.caption==='保存');
 if(defs.length!==1)throw Error('行程保存定义不唯一');
 const live={pageKey:'WF_YBX_938',winno:938};for(const k of Object.keys(definition))if(!['pageKey','winno'].includes(k)){const v=defs[0][k];live[k]=v===undefined?null:typeof v==='function'?String(v):v;}
 if(stable(live)!==stable(definition))throw Error('行程保存定义与独立检查不同');
 const buttons=[...document.querySelectorAll('.foot_winWF_YBX_938 button[btnname]')].filter(e=>vis(e)&&!e.disabled&&e.getAttribute('btnname')==='保存');
 if(buttons.length!==1||!vis(document.getElementById('formWF_YBX_1150')))throw Error('行程保存窗口未就绪');
 registry[requestId]={case_id:req.case_id,native_guid:req.native_guid,invoice_number:req.invoice_number,request_digest:REQUEST_DIGEST,phase:'fill_requested',before_inputs:baseline};
 for(const [id,value] of Object.entries(patches)){const e=document.getElementById(id),derivedCity=id==='formWF_YBX_1150_t-tour_endcity_tx_0'&&!!req.changes.arrival_airport;if(!e||!vis(e)&&!derivedCity||e.disabled||e.readOnly&&!derivedCity&&!/tour_(start|end)date_0$/.test(id))throw Error('行程字段不可改');$(e).val(value).trigger('input').trigger('change');}
 $.F.getDataToContext('1150');
 for(const b of baseline){const expected=Object.hasOwn(patches,b.id)?patches[b.id]:b.value;if(document.getElementById(b.id).value!==expected)throw Error('出现未授权字段变化：'+b.id);}
 registry[requestId].phase='save_requested';buttons[0].click();registry[requestId].phase='save_button_returned';
 return{ok:true,action:'save',request_id:requestId,marker:registry[requestId],changed_fields:Object.keys(patches),system_write:true,requires_independent_readback:true};
})()'''.replace('GUARD',guard_script(request)).replace('REQUEST_ID',json.dumps(request_id)).replace('REQUEST_DIGEST',json.dumps(digest(request))).replace('BASELINE',json.dumps(baseline,ensure_ascii=False)).replace('DEFINITION',json.dumps(definitions[0],ensure_ascii=False)).replace('PATCHES',json.dumps(patches,ensure_ascii=False))


def close_script(request, request_id):
    return r'''(async()=>{GUARD
 const deadline=Date.now()+10000;
 while(vis(document.getElementById('formWF_YBX_1150'))&&Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,100));
 const no=document.getElementById('formWF_YBX_404_t-invoice_no');
 if(!vis(no)&&!vis(document.getElementById('formWF_YBX_1150'))&&vis(document.getElementById('gridWF_YBX_53')))return{ok:true,action:'close',already_returned_to_pool:true,system_write:false,requires_independent_readback:true};
 if(!vis(no)||no.value!==req.invoice_number||vis(document.getElementById('formWF_YBX_1150')))throw Error('须先完成行程保存或返回；不丢弃打开的行程');
 const defs=($.page.list.WF_YBX_288?.wins||[]).filter(w=>String(w.winno)==='296').flatMap(w=>w.func_btns||[]).filter(b=>b.caption==='返回');
 if(defs.length!==1||defs[0].btnguid!=='B85BEA5772564A59AC1474E3D03B992E'||defs[0].call_proc!==false||defs[0].procs?.length)throw Error('原生返回定义改变');
 const buttons=[...document.querySelectorAll('.foot_winWF_YBX_296 button[btnname]')].filter(e=>vis(e)&&!e.disabled&&e.getAttribute('btnname')==='返回');if(buttons.length!==1)throw Error('返回按钮不唯一');
 buttons[0].click();return{ok:true,action:'close',system_write:false};
})()'''.replace('GUARD',guard_script(request))


def resume_save_script(request, inspection, request_id):
    observed=inspection.get('result',inspection)
    marker=observed.get('edit_marker') or {}
    original=observed.get('original_operation_result') or {}
    if marker.get('request_digest')!=digest(request) or marker.get('phase')!='fill_requested' or original.get('ok') is not False or original.get('error')!='行程字段不可改':
        raise ValueError('仅接续已确认在保存按钮前报字段错误的原任务；未知/已发起保存不可重放')
    return save_script(request,inspection,request_id)


def medical_save_script(request, inspection, request_id):
    from decimal import Decimal
    validate(request)
    if request['schema']!=MEDICAL_SCHEMA or request['changes']!={'insurance_type':'自费'}:raise ValueError('只补已确认自费类型，不改原票及支付金额')
    observed=inspection.get('result',inspection)
    if observed.get('ok')is not True or observed.get('schema')!='ustc-invoice-edit-inspect-v1' or observed.get('request_digest')!=digest(request):raise ValueError('医疗保存须同案同票的独立字段检查')
    forms=[f for f in observed['forms']if f['funcno']=='1360']
    if len(forms)!=1:raise ValueError('医疗表单不唯一')
    inputs={x['id']:x for x in forms[0]['inputs']}
    prefix='formWF_YBX_1360_t-medical_'
    def field(k):return inputs[prefix+k]['value']
    if field('invcode')!=request['invoice_code'] or field('invno')!=request['invoice_number'] or Decimal(field('invamount'))!=Decimal(request['amount']):raise ValueError('医疗票原生代码/号码/金额不符')
    if field('paytype')not in ['', '自费'] or any(Decimal(field(k))!=0 for k in ['payoverall','payother','payaccount','paygrcash']) or Decimal(field('paycash'))!=Decimal(request['amount']):raise ValueError('仅处理医保类型空白、全额个人现金支付票，不覆盖其他医保/支付信息')
    target=inputs[prefix+'paytype']
    if not target['visible'] or target['readonly'] or target['disabled']:raise ValueError('医保类型字段不可编辑')
    definitions=[b for b in observed['buttonDefinitions']if str(b['winno'])=='1159' and b['caption']=='保存']
    expected_proc='`$a774675c472bb055e1fdbf4c8da1a24d:#1360-t.medical_paytype#;#1360-t.medical_payoverall#;#1360-t.medical_payother#;#1360-t.medical_paygrcash#;#1360-t.medical_paycash#;#1360-t.medical_payaccount#;#1360-t.medical_invno#;#1360-t.medical_invdate#;#1360-t.medical_invcode#;#1360-t.medical_invcheck#;#1360-t.medical_invamount#;#1360-t.medical_hospital#;#0-is_skip#;#61-tks_guid#;#61-tk_mainguid#;#61-is_sg#;#395-t.inv_type#;#0-userinfo.username#;#0-userinfo.userid#'
    if len(definitions)!=1 or definitions[0]['btnguid']!='9EBDF997C5B84DBFA9D759C37D6391A5' or definitions[0]['call_proc']is not True or definitions[0]['check_func']!='`$22c050234a162229a62c14220fab379d:#1360-t.medical_paytype#;#1360-t.medical_payoverall#;#1360-t.medical_payother#;#1360-t.medical_paycash#;#1360-t.medical_payaccount#;#1360-t.medical_invno#;#1360-t.medical_invdate#;#1360-t.medical_invcode#;#1360-t.medical_invamount#;#1360-t.medical_hospital#' or len(definitions[0]['procs'])!=1 or definitions[0]['procs'][0]!=expected_proc:raise ValueError('原生医疗保存定义改变')
    baseline=[v for f in observed['forms']if f['funcno']in ['395','1360']for v in f['inputs']]
    return r'''(async()=>{GUARD
 const registry=window.__ustcInvoiceEdit||(window.__ustcInvoiceEdit={}),requestId=REQUEST_ID,baseline=BASELINE,definition=DEFINITION;
 if(registry[requestId])throw Error('原医疗保存已发起，只读恢复');
 if(String($.UC.bindData('#61-tks_guid#')||'')!==req.native_guid)throw Error('医疗保存上下文不是本票');
 for(const b of baseline){const e=document.getElementById(b.id);if(!e||e.value!==b.value)throw Error('医疗字段原值改变：'+b.id);}
 const defs=($.page.list.WF_YBX_288?.wins||[]).filter(w=>String(w.winno)==='1159').flatMap(w=>w.func_btns||[]).filter(b=>b.caption==='保存');
 if(defs.length!==1)throw Error('医疗保存按钮定义不唯一');
 for(const k of ['btnguid','call_proc','check_func','procs','acts'])if(JSON.stringify(defs[0][k])!==JSON.stringify(definition[k]))throw Error('医疗保存定义与独立检查不同');
 const buttons=[...document.querySelectorAll('.foot_winWF_YBX_1159 button[btnname]')].filter(e=>vis(e)&&!e.disabled&&e.getAttribute('btnname')==='保存');
 if(buttons.length!==1)throw Error('原生医疗保存控件不唯一');
 const input=document.getElementById('formWF_YBX_1360_t-medical_paytype');if(!vis(input)||input.disabled||input.readOnly)throw Error('医保类型控件改变');
 registry[requestId]={case_id:req.case_id,native_guid:req.native_guid,invoice_number:req.invoice_number,request_digest:REQUEST_DIGEST,phase:'medical_fill_requested'};
 $(input).val('自费').trigger('input').trigger('change');$.F.getDataToContext('1360');
 for(const b of baseline)if(document.getElementById(b.id).value!==(b.id===input.id?'自费':b.value))throw Error('医疗票出现非授权字段变化');
 if(Number($.active||0)!==0)throw Error('原生请求仍在进行，不能保存');
 const responses=[],nativeErrors=[],namespace='.ustcMedical_'+requestId;
 const receive=(event,xhr,settings)=>{const url=new URL(settings.url,location.href);
  if(url.origin===location.origin&&url.pathname==='/YBX/commonUpdate_responseButtonEvent.action')responses.push({status:xhr.status,response_text:String(xhr.responseText||'').slice(0,4000)});};
 const onError=e=>nativeErrors.push(String(e.message||''));
 $(document).on('ajaxComplete'+namespace,receive);window.addEventListener('error',onError);
 try{registry[requestId].phase='medical_save_requested';buttons[0].click();registry[requestId].phase='medical_save_button_returned';
  const deadline=Date.now()+12000;while(!responses.length&&!nativeErrors.length&&Date.now()<deadline)await new Promise(r=>setTimeout(r,100));
 }finally{$(document).off('ajaxComplete'+namespace,receive);window.removeEventListener('error',onError);}
 registry[requestId].nativeResponses=responses;registry[requestId].nativeErrors=nativeErrors;
 if(responses.length!==1||responses[0].status!==200||nativeErrors.length){registry[requestId].phase=responses.length===1&&responses[0].status===404?'medical_http_404':'medical_save_requires_recovery';return{ok:false,error:'医疗保存未取得唯一正常原生回执',marker:registry[requestId],system_write:true,requires_independent_readback:true};}
 registry[requestId].phase='medical_native_response_received';
 return{ok:true,action:'medical-save',marker:registry[requestId],system_write:true,save_verified:false,requires_independent_readback:true};
})()'''.replace('GUARD',guard_script(request)).replace('REQUEST_ID',json.dumps(request_id)).replace('REQUEST_DIGEST',json.dumps(digest(request))).replace('BASELINE',json.dumps(baseline,ensure_ascii=False)).replace('DEFINITION',json.dumps(definitions[0],ensure_ascii=False))


def medical_close_script(request, request_id):
    return r'''(async()=>{GUARD
 const deadline=Date.now()+10000;
 while(vis(document.getElementById('formWF_YBX_1360'))&&Date.now()<deadline)await new Promise(r=>setTimeout(r,100));
 if(!vis(document.getElementById('formWF_YBX_1360'))&&vis(document.getElementById('gridWF_YBX_53')))return{ok:true,action:'medical-close',already_returned_to_pool:true,system_write:false};
 throw Error('医疗保存尚未返回票池，保留原页核查，不重发');
})()'''.replace('GUARD',guard_script(request))


def medical_return_script(request, request_id):
    """只返回本票维护页供重新读取；不保存、不宣称之前的保存成功。"""
    return r'''(()=>{GUARD
 if(String($.UC.bindData('#61-tks_guid#')||'')!==req.native_guid||!vis(document.getElementById('formWF_YBX_1360')))throw Error('不是本票医疗表单');
 const value=k=>document.getElementById('formWF_YBX_1360_t-medical_'+k)?.value;
 if(value('invcode')!==req.invoice_code||value('invno')!==req.invoice_number||Number(value('invamount'))!==Number(req.amount))throw Error('医疗票身份或金额变化');
 if(Number($.active||0)!==0)throw Error('原生请求仍在进行，不能离开维护页');
 const defs=($.page.list.WF_YBX_288?.wins||[]).filter(w=>String(w.winno)==='1159').flatMap(w=>w.func_btns||[]).filter(b=>b.caption==='返回');
 if(defs.length!==1||defs[0].btnguid!=='79D43C91442C42749ED5D759ACCA864C'||defs[0].call_proc!==false||defs[0].procs?.length)throw Error('医疗原生返回定义改变');
 const buttons=[...document.querySelectorAll('.foot_winWF_YBX_1159 button[btnname="返回"]')].filter(e=>vis(e)&&!e.disabled);if(buttons.length!==1)throw Error('医疗返回入口不唯一');
 const nativeErrors=[],onError=e=>nativeErrors.push({message:String(e.message||''),filename:String(e.filename||'').split('?')[0],line:e.lineno||null});window.addEventListener('error',onError);try{buttons[0].click();}finally{window.removeEventListener('error',onError);}return{ok:nativeErrors.length===0,action:'medical-return',nativeErrors,system_write:false,prior_save_verified:false,requires_independent_reopen:true};
})()'''.replace('GUARD',guard_script(request))


def run_workflow(request, request_id, output):
    """单票一条龙；任何一步未取得成功回执即停在原步骤，不重放。"""
    from finance_upload_batch_run import full_grid
    from invoice_identity import source_matches
    validate(request)
    folder=output.parent/'steps';folder.mkdir(exist_ok=False)
    receipts=[]
    before=full_grid(request['tab_id'],'53',request['expected_userid'],folder/'before-pool.json')
    inspection=None
    try:
        actions=['open','journey']
        if request['changes'].get('arrival_airport'):actions += ['airport-open','airport-query','inspect','airport-select']
        actions += ['inspect','save','close']
        for index,action in enumerate(actions,1):
            step=folder/(str(index)+'-'+action);step.mkdir()
            marker=request_id+'_'+action
            if action=='inspect':source=inspect_script(request)
            else:
                source={'save':save_script,'airport-select':airport_select_script}[action](request,inspection,marker) if action in ['save','airport-select'] else {'open':open_script,'journey':journey_script,'airport-open':airport_script,'airport-query':airport_query_script,'close':close_script}[action](request,marker)
                intent=ROOT/'.local/invoice_edit_intents'/request['case_id']/(digest({'request':request,'action':action,**({'request_id':marker} if action in ['journey','close'] else {})})+'.json')
                write(intent,{'request':request,'action':action,'request_id':marker,'output':str((step/'native.json').resolve())})
            result=run_browser(source,request,step/'native.json','read' if action=='inspect' else 'write')
            receipts.append({'action':action,'path':str((step/'native.json').resolve()),'sha256':sha(step/'native.json')})
            if result.get('phase')!='script_completed' or result.get('result',{}).get('ok') is not True:
                raise ValueError('步骤'+action+'未取得成功回执；只读恢复原请求，不重放')
            if action=='inspect':inspection=result
        after=full_grid(request['tab_id'],'53',request['expected_userid'],folder/'after-pool.json')
        target={'invoice_number_candidates':[request['invoice_number']]}
        hits=[r for r in after['grids'][0]['rows'] if source_matches(r,target)]
        if len(hits)!=1 or hits[0]['t.tk_guid']!=request['native_guid'] or hits[0]['t.tk_mod_flag']!='NORMAL' or hits[0]['t.auth_rst_desc']!='正常(已认证)' or float(hits[0]['t.tk_total_amt'])!=float(request['amount']):
            raise ValueError('保存后原票状态、号码、GUID或金额未核对通过')
        protected={r['t.tk_guid']:r for r in before['grids'][0]['rows'] if r['t.tk_guid']!=request['native_guid']}
        remaining={r['t.tk_guid']:r for r in after['grids'][0]['rows'] if r['t.tk_guid']!=request['native_guid']}
        changes=[k for k,v in protected.items() if remaining.get(k)!=v]
        result={'ok':not changes,'schema':'ustc-invoice-edit-workflow-v1','case_id':request['case_id'],'invoice_number':request['invoice_number'],
                'traveler':request['changes']['traveler'],'native_row':hits[0],'protected_record_changes':changes,'steps':receipts,
                'observedAt':after['observedAt'],'system_write':True,'independent_pool_readback':True,'claim_created':False}
    except (ValueError,RuntimeError,OSError) as error:
        result={'ok':False,'schema':'ustc-invoice-edit-workflow-v1','case_id':request['case_id'],'invoice_number':request['invoice_number'],
                'error':str(error),'steps':receipts,'retry_automatically':False,'claim_created':False}
    write(output,result)
    return result


def medical_fields(observation):
    value=observation.get('result',observation)
    forms=[f for f in value.get('forms',[]) if str(f.get('funcno'))=='1360']
    if value.get('ok') is not True or len(forms)!=1:raise ValueError('未取得唯一医疗字段回读')
    return {x['id']:str(x.get('value','')) for x in forms[0].get('inputs',[]) if x.get('id','').startswith('formWF_YBX_1360_t-medical_')}


def run_medical_workflow(request, request_id, output):
    """单票补录、重新打开核对并返回；失败保留具体步骤，不重放保存。"""
    validate(request)
    if request['schema']!=MEDICAL_SCHEMA or request['changes']!={'insurance_type':'自费'}:raise ValueError('医疗流程只接受已确认自费的医疗清单')
    save_intent=ROOT/'.local/invoice_edit_intents'/request['case_id']/(digest({'request':request,'action':'medical-save'})+'.json')
    if save_intent.exists():raise ValueError('本票已有保存意图，须恢复原任务及独立回读，不能重开流程')
    folder=output.parent/'steps';folder.mkdir(exist_ok=False)
    journal={'schema':'ustc-medical-edit-workflow-v1','case_id':request['case_id'],
        'invoice_number':request['invoice_number'],'steps':[],'ok':False,
        'save_verified':False,'retry_automatically':False,'claim_created':False,
        'quiet_policy':'existing_tab_no_activation_no_selection'}
    inspection=None;baseline=None
    actions=['medical-open','inspect-before','medical-save','medical-close','reopen','inspect-saved','medical-return','return-complete']
    try:
        for index,action in enumerate(actions,1):
            step=folder/(str(index)+'-'+action);step.mkdir()
            marker=request_id+'_'+str(index)
            native_action={'reopen':'medical-open','return-complete':'medical-close'}.get(action,action)
            if action.startswith('inspect-'):source=inspect_script(request)
            elif action=='medical-save':source=medical_save_script(request,inspection,marker)
            else:source={'medical-open':open_script,'medical-close':medical_close_script,'medical-return':medical_return_script}[native_action](request,marker)
            if not action.startswith('inspect-'):
                key={'request':request,'action':native_action}
                if native_action in ['medical-open','medical-return','medical-close']:key['request_id']=marker
                intent=ROOT/'.local/invoice_edit_intents'/request['case_id']/(digest(key)+'.json')
                write(intent,{'request':request,'action':native_action,'request_id':marker,'output':str((step/'native.json').resolve())})
            journal['current_step']=action
            journal['current_receipt']=str((step/'native.json').resolve())
            output.write_text(json.dumps(journal,ensure_ascii=False,indent=2)+'\n')
            result=run_browser(source,request,step/'native.json','read' if action.startswith('inspect-') else 'write')
            journal['steps'].append({'action':action,'path':str((step/'native.json').resolve()),'sha256':sha(step/'native.json')})
            if result.get('phase')!='script_completed' or result.get('result',{}).get('ok') is not True:
                raise ValueError('步骤'+action+'结果未确认；恢复该步骤原回执，不重新运行补录')
            if action=='inspect-before':
                inspection=result;baseline=medical_fields(result)
            if action=='inspect-saved':
                expected={**baseline,'formWF_YBX_1360_t-medical_paytype':'自费'}
                if medical_fields(result)!=expected:raise ValueError('重开后字段与原值及确认修改不一致')
                journal['save_verified']=True
        journal.update(ok=True,current_step='completed',independent_reopen_readback=True)
    except (ValueError,RuntimeError,OSError) as error:
        journal['error']=str(error)
    output.write_text(json.dumps(journal,ensure_ascii=False,indent=2)+'\n')
    return journal


def medical_workflow_recovery_source(folder):
    """仅定位原流程最后一步；不执行或接续任何业务动作。"""
    journal=load(folder/'output.json')
    actions=['medical-open','inspect-before','medical-save','medical-close','reopen','inspect-saved','medical-return','return-complete']
    action=journal.get('current_step')
    if journal.get('schema')!='ustc-medical-edit-workflow-v1' or action not in actions:raise ValueError('原医疗流程没有待恢复步骤')
    expected=(folder/'steps'/(str(actions.index(action)+1)+'-'+action)/'native.json').resolve()
    if str(expected)!=journal.get('current_receipt'):raise ValueError('原流程回执位置不匹配')
    for step in journal.get('steps',[]):
        if step['action']==action and (step['path']!=str(expected) or step['sha256']!=sha(expected)):raise ValueError('原步骤回执SHA改变')
    return load(expected)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['run','open','medical-run','medical-open','medical-save','medical-close','medical-return','journey','airport-open','airport-query','airport-select','save','resume-save','close','inspect','recover'])
    p.add_argument('--request',type=Path,required=True)
    p.add_argument('--request-id',required=True)
    p.add_argument('--execute',action='store_true')
    p.add_argument('--inspection',type=Path)
    p.add_argument('--original-marker')
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();request=validate(load(a.request))
    if request['schema']==MEDICAL_SCHEMA and a.action not in ['medical-run','medical-open','medical-save','medical-close','medical-return','inspect','recover']:raise ValueError('医疗票当前仅支持原生窗口检查')
    if a.action.startswith('medical-') and request['schema']!=MEDICAL_SCHEMA:raise ValueError('医疗入口须同案医疗原件')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', a.request_id):raise ValueError('须有稳定ASCII请求号')
    if a.original_marker and (a.action!='recover' or not re.fullmatch(r'reimbursement-result-[a-f0-9]{32}',a.original_marker)):raise ValueError('只读恢复须正式原生结果标记')
    if a.execute != (a.action in ['run','open','medical-run','medical-open','medical-save','medical-close','medical-return','journey','airport-open','airport-query','airport-select','save','resume-save','close']):raise ValueError('写入及原生导航须execute=true')
    if a.output.exists():raise ValueError('输出已存在，不能覆盖')
    if a.action in ['run','medical-run']:
        result=(run_medical_workflow if a.action=='medical-run' else run_workflow)(request,a.request_id,a.output)
        print(json.dumps(result,ensure_ascii=False));return 0 if result['ok'] else 1
    if a.action in ['open','medical-run','medical-open','medical-save','medical-close','medical-return','journey','airport-open','airport-query','airport-select','save','resume-save','close']:
        if a.action in ['save','resume-save','airport-select','medical-save']:
            if not a.inspection:raise ValueError('保存须引用独立表单检查')
            source={'medical-save':medical_save_script,'save':save_script,'resume-save':resume_save_script,'airport-select':airport_select_script}[a.action](request,load(a.inspection),a.request_id)
        else:source={'open':open_script,'medical-open':open_script,'medical-close':medical_close_script,'medical-return':medical_return_script,'journey':journey_script,'airport-open':airport_script,'airport-query':airport_query_script,'close':close_script}[a.action](request,a.request_id)
        intent=ROOT/'.local/invoice_edit_intents'/request['case_id']/(digest({'request':request,'action':a.action,**({'request_id':a.request_id} if a.action in ['journey','close','medical-return','medical-open'] else {})})+'.json')
        write(intent,{'request':request,'action':a.action,'request_id':a.request_id,'output':str(a.output.resolve())})
    else:source=inspect_script(request,a.request_id if a.action=='recover' else None,a.original_marker)
    result=run_browser(source,request,a.output.parent/'native.json','write' if a.execute else 'read',keep_internal_tab=False)
    write(a.output,result)
    print(json.dumps({'ok':result.get('result',{}).get('ok') is True,'action':a.action,'output':str(a.output)},ensure_ascii=False))
    return 0 if result.get('result',{}).get('ok') is True else 1


if __name__=='__main__':raise SystemExit(main())
