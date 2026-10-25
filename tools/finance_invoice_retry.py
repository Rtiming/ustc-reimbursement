#!/usr/bin/env python3
"""异常票据受控重试：先只读检查和预览，原生删除、确认及回读分别留证。"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from invoice_identity import source_identity, source_matches

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'ustc-finance-invoice-retry-v1'
PLAN_SCHEMA = 'ustc-finance-invoice-retry-plan-v1'
BUTTON_KEYS = ['caption','captionTag','pre_compute','pre_check','call_proc','check_func','procs','post_check','acts','affects','jscontent','clickquery','clickqueryhint','autojump','btn_type','customFunc','hint']


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def text(value):
    return html.unescape(re.sub(r'<[^>]*>', '', str(value))).strip() if value is not None else ''


def validate_request(request):
    if not isinstance(request, dict) or not re.fullmatch(r'[A-Za-z0-9_-]+', str(request.get('case_id', ''))):
        raise ValueError('需要明确ASCII case_id')
    if not isinstance(request.get('tab_id'), str) or not re.fullmatch(r'[0-9]+', request['tab_id']):
        raise ValueError('需要实际ASCII tab_id字符串')
    if not isinstance(request.get('expected_userid'), str) or not request['expected_userid'].strip():
        raise ValueError('需要明确当前账号expected_userid')
    if str(request.get('grid', '')) not in ['53', '113']:
        raise ValueError('仅支持已观察原生53/113表格')
    for key in ('target_guids', 'target_filecodes'):
        values = request.get(key, [])
        if not isinstance(values, list) or len(values) > 20 or any(not isinstance(v, str) or not re.fullmatch(r'[A-Fa-f0-9]{32}', v) for v in values):
            raise ValueError('可选目标原生GUID/filecode须为最多20个32位十六进制字符串')
    return {**request, 'grid': str(request['grid'])}


def inspect_script(request, request_id=None):
    request = validate_request(request)
    if request_id is not None and not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', request_id):
        raise ValueError('request_id须为稳定ASCII标识')
    return r'''(()=>{
 const req=REQUEST,requestId=REQUEST_ID,vis=e=>!!e&&e.getClientRects().length>0;
 if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX')throw Error('财务主机或项目不符');
 const identity={userid:String($.UC.bindData('#0-userinfo.userid#')||''),username:String($.UC.bindData('#0-userinfo.username#')||'')};
 if(identity.userid!==req.expected_userid)throw Error('财务账号与本案不同');
 const grid=document.getElementById('gridWF_YBX_'+req.grid);
 if(!grid?.grid||!vis(grid))throw Error('指定原生表未加载或不可见');
 const q=$(grid),ids=q.jqGrid('getDataIDs'),rows=ids.map(id=>q.jqGrid('getRowData',id));
 const records=q.jqGrid('getGridParam','records'),loading=!!grid.grid.hDiv.loading;
 const selected=q.jqGrid('getGridParam','selarrrow'),selectedIds=Array.isArray(selected)?selected.map(String):null;
 const winno=req.grid==='113'?'79':'39',footSelector='.foot_winWF_YBX_'+winno+' button[btnname]',headSelector='#winWF_YBX_'+winno+'_head button[btnname]';
 const relevant=caption=>/删除|识别|认证|标注|处理|刷新/.test(String(caption||''));
 const captureErrors=[];
 const bounded=(value,path,max=12000)=>{if(value===undefined)return null;const s=typeof value==='function'?String(value):value;const encoded=JSON.stringify(s);if(encoded&&encoded.length>max){captureErrors.push({path,reason:'definition_string_budget'});return null;}return s;};
 const control=(e,selector)=>({tag:e.tagName,id:e.id,btnname:e.getAttribute('btnname'),text:e.innerText.trim(),visible:vis(e),disabled:!!e.disabled,selector,winno,onclick:bounded(e.getAttribute('onclick'),'control.onclick'),title:e.getAttribute('title'),href:e.getAttribute('href'),className:e.className||''});
 const pagerSelector='#gbox_gridWF_YBX_'+req.grid+' .ui-pg-button';
 const pagerNodes=[...document.querySelectorAll(pagerSelector)].filter(e=>relevant(e.getAttribute('title')||e.innerText));
 const controlNodes=[...new Set([...document.querySelectorAll(footSelector),...document.querySelectorAll(headSelector)])].filter(e=>relevant(e.getAttribute('btnname')||e.getAttribute('title')||e.innerText));
 if(controlNodes.length>50)captureErrors.push({path:'controls',reason:'control_count_budget'});
 const controls=controlNodes.slice(0,50).map(e=>control(e,e.closest?.('#winWF_YBX_'+winno+'_head')?headSelector:footSelector));
 if(pagerNodes.length>10)captureErrors.push({path:'pagerControls',reason:'control_count_budget'});
 const pagerControls=pagerNodes.slice(0,10).map(e=>{const item=control(e,pagerSelector),events=typeof $._data==='function'?$._data(e,'events'):null;
  const clicks=events?.click||[];if(clicks.length>5)captureErrors.push({path:'pagerControls.click','reason':'event_count_budget'});
  item.nativeClickHandlers=clicks.slice(0,5).map(x=>typeof x.handler==='function'?bounded(x.handler,'pagerControls.click'):null);return item;});
 const refreshNodes=[...document.querySelectorAll('[title],[btnname]')].filter(e=>vis(e)&&/刷新/.test(e.getAttribute('title')||e.getAttribute('btnname')||''));
 if(refreshNodes.length>20)captureErrors.push({path:'refreshControls',reason:'control_count_budget'});
 const refreshControls=refreshNodes.slice(0,20).map(e=>{const item=control(e,null);item.ancestorIds=[];let parent=e.parentElement;
  for(let n=0;parent&&n<6;n++,parent=parent.parentElement)if(parent.id)item.ancestorIds.push(parent.id);
  const events=typeof $._data==='function'?$._data(e,'events'):null;item.nativeClickHandlers=(events?.click||[]).slice(0,5).map(x=>typeof x.handler==='function'?bounded(x.handler,'refreshControls.click'):null);return item;});
 const windowNode=document.getElementById('winWF_YBX_'+winno)||grid.closest?.('.win')||grid.parentElement;
 const labelNodes=[...(windowNode?.querySelectorAll('a,button,span,div')||[])].filter(e=>vis(e)&&String(e.innerText||e.textContent||'').trim()==='刷新'&&![...e.children].some(c=>String(c.innerText||c.textContent||'').trim()==='刷新'));
 if(labelNodes.length>20)captureErrors.push({path:'refreshTextControls',reason:'control_count_budget'});
 const refreshTextControls=labelNodes.slice(0,20).map(e=>{const item=control(e,null);item.ancestorIds=[];let parent=e.parentElement;
  for(let n=0;parent&&n<8;n++,parent=parent.parentElement)if(parent.id)item.ancestorIds.push(parent.id);
  item.nativeNodes=[e,e.parentElement,e.parentElement?.parentElement].filter(Boolean).map((node,index)=>{const events=typeof $._data==='function'?$._data(node,'events'):null;
   return{level:index,tag:node.tagName,id:node.id||'',className:node.className||'',onclick:bounded(node.getAttribute('onclick'),'refreshText.onclick'),outerHTML:bounded(node.outerHTML,'refreshText.outerHTML',6000),nativeClickHandlers:(events?.click||[]).slice(0,5).map(x=>typeof x.handler==='function'?bounded(x.handler,'refreshText.click'):null)};});return item;});
 const defs=[],keys=['caption','captionTag','pre_compute','pre_check','call_proc','check_func','procs','post_check','acts','affects','jscontent','clickquery','clickqueryhint','autojump','btn_type','customFunc','hint'];
 for(const [key,page] of Object.entries($.page?.list||{}).slice(0,50)){
  for(const win of (Array.isArray(page?.wins)?page.wins:[]).slice(0,50)){
   if(String(win.winno)!==winno)continue;
   for(const button of (Array.isArray(win.func_btns)?win.func_btns:[]).slice(0,50)){
    if(!relevant(button.caption))continue;
    const value={pageKey:key,winno:String(win.winno)};
    for(const field of keys){
     const native=button[field];if(native===undefined)continue;
     const serial=typeof native==='function'?String(native):native;
     const encoded=JSON.stringify(serial);
     if(encoded&&encoded.length>12000){captureErrors.push({path:key+'.'+winno+'.'+field,reason:'definition_string_budget'});continue;}
     if(serial===null||['string','number','boolean'].includes(typeof serial)||Array.isArray(serial)&&serial.length<=30&&serial.every(x=>typeof x==='string'))value[field]=serial;
     else captureErrors.push({path:key+'.'+winno+'.'+field,reason:'unsupported_definition_type'});
    }
    defs.push(value);
   }
  }
 }
 const field=(row,key)=>row['t.'+key]??row[key]??'';
 const explicitTargets=(req.target_guids||[]).length+(req.target_filecodes||[]).length>0;
 const targetRows=ids.map((id,index)=>({id:String(id),row:rows[index]})).filter(x=>explicitTargets?
   (req.target_guids||[]).includes(String(field(x.row,'tk_guid')))||(req.target_filecodes||[]).includes(String(field(x.row,'filesequcode'))):
   req.grid==='113'||String(field(x.row,'tk_mod_flag'))!=='NORMAL');
 if(targetRows.length>20)captureErrors.push({path:'rowActionControls',reason:'target_row_count_budget'});
 const rowActionControls=targetRows.slice(0,20).map(x=>{
  const native=document.getElementById(x.id),nodes=[...(native?.querySelectorAll('a,button,[onclick]')||[])];
  if(nodes.length>20)captureErrors.push({path:'rowActionControls.'+x.id,reason:'row_control_count_budget'});
  return{rowId:x.id,native_guid:field(x.row,'tk_guid'),filecode:field(x.row,'filesequcode'),controls:nodes.slice(0,20).map(e=>control(e,null))};
 });
 const publicFunctions={};
 for(const name of ['reidentification','invoiceHandle','invoiceHandle_before','init_invoice_processing','ybx_jbxx_next_before']){
  const fn=window[name];publicFunctions[name]={available:typeof fn==='function',source:typeof fn==='function'?bounded(fn,'publicFunctions.'+name):null};
 }
 const selectionCallbacks={};
 for(const name of ['onSelectRow','onSelectAll']){const fn=q.jqGrid('getGridParam',name);selectionCallbacks[name]=typeof fn==='function'?bounded(fn,'selectionCallbacks.'+name):null;}
 const dialogs=[...document.querySelectorAll('.ui-dialog')].filter(vis).map((e,index)=>({index,id:e.id,text:e.innerText.trim(),buttons:[...e.querySelectorAll('button')].filter(vis).map(b=>({text:b.innerText.trim(),disabled:!!b.disabled,id:b.id}))}));
 const marker=requestId?(window.__ustcInvoiceRetry||{})[requestId]||null:null;
 const context={serial:textContext('#61-serialno#'),selected:String($.UC.bindData('#'+req.grid+'-multirows#')||''),currentFunc:textContext('#0-CURRFUNC#'),
  selected_filecode:textContext('#'+req.grid+'-t.filesequcode#'),selected_guid:textContext('#'+req.grid+'-t.tk_guid#'),selected_file_reference:textContext('#'+req.grid+'-t.tk_filecode#')};
 function textContext(k){return String($.UC.bindData(k)||'').trim();}
 return{ok:true,schema:'ustc-finance-invoice-retry-inspect-v1',observedAt:new Date().toISOString(),projid:'WF_YBX',identity,currentPage:String($.page.currPage),context,
   grid:{funcno:req.grid,records,rows,rowIds:ids.map(String),selectedRowIds:selectedIds,selectedRowId:q.jqGrid('getGridParam','selrow'),loading,complete:!loading&&records===rows.length,
    currentPage:q.jqGrid('getGridParam','page'),lastPage:q.jqGrid('getGridParam','lastpage'),pageSize:q.jqGrid('getGridParam','rowNum'),scope:'当前渲染页；未刷新或翻页，不推定覆盖全部记录'},
   controls,pagerControls,refreshControls,refreshTextControls,buttonDefinitions:defs,rowActionControls,publicFunctions,selectionCallbacks,definitionCaptureComplete:captureErrors.length===0,captureErrors,dialogs,marker,request_id:requestId,
   system_write:false,selection_write:false,deleted:false};
})()'''.replace('REQUEST_ID', json.dumps(request_id)).replace('REQUEST', json.dumps(request, ensure_ascii=False))


def body(receipt):
    for _ in range(4):
        if isinstance(receipt, dict) and receipt.get('ok') is True and ('grid' in receipt or 'action' in receipt):
            return receipt
        receipt = receipt.get('result') if isinstance(receipt, dict) else None
    raise ValueError('需要成功的原生检查/动作回执，未知结果不能放行')


def native_field(row, key):
    return row.get('t.' + key, row.get(key))


def preview(request, manifest, pool_receipt, recognition_receipt, inspection):
    from finance_invoice_reconcile import grid_scope, validate_sources, invoice_number
    from finance_surface import parse_recognition_error
    request = validate_request(request)
    action, grid = request.get('action'), request['grid']
    if action not in ('refresh', 'reidentify', 'cleanup'):
        raise ValueError('预览action只支持refresh/reidentify/cleanup')
    if manifest.get('case_id') != request['case_id'] or manifest.get('expected_userid') != request['expected_userid']:
        raise ValueError('候选manifest必须同案同账号')
    sources = {source_identity(s, True): s for s in validate_sources(manifest, True, allow_medical=True)}
    _, pool, ps = grid_scope(pool_receipt, '53', request['expected_userid'])
    _, recognition, rs = grid_scope(recognition_receipt, '113', request['expected_userid'])
    if not ps['complete'] or not rs['complete']:
        raise ValueError('预览须有完整票池和识别明细基线')
    observed = body(inspection)
    if observed.get('schema') != 'ustc-finance-invoice-retry-inspect-v1' or observed.get('identity', {}).get('userid') != request['expected_userid'] or observed.get('projid') != 'WF_YBX':
        raise ValueError('检查回执账号或原生范围不符')
    if str(observed.get('grid', {}).get('funcno')) != grid or observed['grid'].get('loading') is not False:
        raise ValueError('指定表检查未完成加载')
    if observed.get('definitionCaptureComplete') is not True or observed.get('dialogs') or observed.get('context', {}).get('serial'):
        raise ValueError('定义不完整、有未处理弹框或当前有报销单上下文')
    supplied = request.get('targets', [])
    if not isinstance(supplied, list) or len(supplied) > 20 or (action == 'refresh') != (len(supplied) == 0):
        raise ValueError('刷新不带票据目标，其他动作须明确1至20个目标')
    if action == 'reidentify' and (grid != '113' or len(supplied) != 1):
        raise ValueError('原生重识别仅支持113单票操作')
    full_rows = pool['rows'] if grid == '53' else recognition['rows']
    if observed['grid'].get('records') != len(full_rows):
        raise ValueError('当前记录数与完整基线不同，须更新完整回执')
    targets, used = [], set()
    for item in supplied:
        number, source_sha = item.get('invoice_number'), item.get('source_sha256')
        identity = source_identity({'invoice_number_candidates': [number], 'invoice_code': item.get('invoice_code')}, True)
        source = sources.get(identity)
        if source is None or source['sha256'] != source_sha or source_sha in used:
            raise ValueError('目标票号/SHA不是本案唯一原件')
        used.add(source_sha)
        key = 'tk_guid' if grid == '53' else 'filesequcode'
        value = item.get('native_guid' if grid == '53' else 'filecode')
        if not isinstance(value, str) or not re.fullmatch(r'[A-Fa-f0-9]{32}', value):
            raise ValueError('目标须有准确原生GUID/filecode')
        matches = [r for r in full_rows if native_field(r, key) == value]
        current = [r for r in observed['grid']['rows'] if native_field(r, key) == value]
        if len(matches) != 1 or len(current) != 1 or matches[0] != current[0]:
            raise ValueError('目标未唯一出现在当前页或与完整基线不同')
        row = matches[0]
        if grid == '53':
            if action != 'cleanup' or not source_matches(row, source):
                raise ValueError('53仅支持明确票号异常记录清理')
            flag = text(native_field(row, 'tk_mod_flag'))
            if flag not in ('ERROR', 'ABNORMAL', 'FAILED', 'INVALID', 'EXCEPTION') or text(native_field(row, 'serial_no')) or text(native_field(row, 'PC_Has_chosen')) not in ('', 'N', '0'):
                raise ValueError('正常、已选、已绑定单据或非明确异常票不得清理')
            error = None
            scope = '仅删除当前账号该异常票池记录；不删除原件，不自动重传'
        else:
            if action == 'reidentify' and any(source_matches(r, source) for r in pool['rows']):
                raise ValueError('同票号已存在完整票池，应先归账核对，不能再次重识别')
            filename = native_field(row, 'show_filename')
            if filename != source_sha + '.pdf':
                raise ValueError('113目标必须有与本案源SHA完全相符的原生文件名')
            error = parse_recognition_error(native_field(row, 'show_err_msg'))
            if action == 'reidentify' and error['error_type'] == 'duplicate_reserved_invoice':
                raise ValueError('预约占用票不能按普通重识别反复尝试')
            if action == 'cleanup' and not error['display_text']:
                raise ValueError('无错待识别先刷新或单票重识别，不直接当失败记录清理')
            scope = ('仅删除本账号失败上传队列记录；预约仍受阻，不释放他人预约'
                     if error['error_type'] == 'duplicate_reserved_invoice' else '仅处理本账号识别记录，不修改原始票据或他人预约')
        targets.append({'invoice_number': number, **({'invoice_code': identity[1]} if identity[0] == 'medical' else {}),
            'source_sha256': source_sha, 'source_path': source['path'],
            'native_key': key, 'native_value': value, 'native_row': row, 'recognition_error': error, 'cleanup_scope': scope})
    selected = observed['grid'].get('selectedRowIds')
    if not isinstance(selected, list):
        raise ValueError('未取得真实原生勾选集合')
    if action != 'refresh':
        visible_ids = observed['grid'].get('rowIds')
        if not isinstance(visible_ids, list) or len(visible_ids) != len(observed['grid']['rows']):
            raise ValueError('未取得与当前行一一对应的原生行ID')
        target_ids = {str(row_id) for row_id, row in zip(visible_ids, observed['grid']['rows'])
                      if any(native_field(row, target['native_key']) == target['native_value'] for target in targets)}
        if set(map(str, selected)) - target_ids:
            raise ValueError('存在本次目标以外的原生勾选，保留用户现场并停止')
    caption = '重新识别' if action == 'reidentify' else '批量删除'
    definitions = [d for d in observed.get('buttonDefinitions', []) if d.get('caption') == caption]
    controls = [c for c in observed.get('controls', []) if c.get('btnname') == caption and c.get('visible') is True and c.get('disabled') is False]
    reasons, definition, refresh_control = [], None, None
    if action == 'refresh':
        refreshes = [n for c in observed.get('refreshTextControls', []) if c.get('visible') is True and c.get('text') == '刷新'
                     for n in c.get('nativeNodes', []) if n.get('id') == 'gridWF_YBX_113_refresh' and n.get('tag') == 'DIV'
                     and len(n.get('nativeClickHandlers', [])) == 1 and 'reloadGrid' in n['nativeClickHandlers'][0]]
        if grid != '113' or len(refreshes) != 1:
            reasons.append('未捕获唯一113原生刷新控件及reloadGrid监听器')
        else:
            refresh_control = refreshes[0]
    elif len(definitions) != 1 or len(controls) != 1:
        reasons.append('原生动作定义或可见按钮不唯一')
    else:
        definition = definitions[0]
        if action == 'cleanup' and (definition.get('clickquery') is not True or not definition.get('clickqueryhint')):
            reasons.append('删除入口没有明确原生确认步骤')
        if action == 'reidentify' and (definition.get('pre_check') is not True or not definition.get('check_func') or definition.get('clickquery') is not False):
            reasons.append('重识别原生守卫或确认合同改变')
    return {'schema': PLAN_SCHEMA, 'case_id': request['case_id'], 'action': action, 'request': request,
        'ready': not reasons, 'readiness_reasons': reasons, 'targets': targets, 'before_selected_row_ids': selected,
        'button_definition': definition, 'refresh_control': refresh_control,
        'selection_callbacks': observed.get('selectionCallbacks'),
        'scope': {'complete_pool': ps, 'complete_recognition': rs, 'visible_page': observed['grid'].get('currentPage'),
            'visible_row_count': len(observed['grid']['rows']), 'grid_records': len(full_rows)},
        'executed': False, 'system_write': False, 'selection_write': False,
        'protected_native_ids': [native_field(row, 'tk_guid' if grid == '53' else 'filesequcode') for row in full_rows
                                 if native_field(row, 'tk_guid' if grid == '53' else 'filesequcode') not in {t['native_value'] for t in targets}]}


def validate_plan(plan, expected_action=None):
    if not isinstance(plan, dict) or plan.get('schema') != PLAN_SCHEMA or plan.get('case_id') != plan.get('request', {}).get('case_id'):
        raise ValueError('须为同案正式异常处理预览计划')
    validate_request(plan['request'])
    if plan.get('action') not in ('refresh', 'reidentify', 'cleanup') or plan['action'] != plan['request'].get('action'):
        raise ValueError('计划动作与请求不同')
    if expected_action and plan['action'] != expected_action:
        raise ValueError('执行动作与已预览计划不同')
    if plan.get('ready') is not True:
        raise ValueError('预览尚未就绪：' + '; '.join(plan.get('readiness_reasons', [])))
    return plan


def verify_plan_evidence(plan):
    evidence = plan.get('inputs_evidence')
    if not isinstance(evidence, dict) or not {'request', 'manifest', 'pool', 'recognition', 'inspection'}.issubset(evidence):
        raise ValueError('计划须绑定全部输入文件SHA')
    for item in evidence.values():
        if not isinstance(item, dict) or sha(item['path']) != item.get('sha256'):
            raise ValueError('预览输入证据已变化，须重新预览')
    for target in plan['targets']:
        if sha(target['source_path']) != target['source_sha256']:
            raise ValueError('目标原件SHA已变化')
    rebuilt = preview(*(load(evidence[k]['path']) for k in ('request', 'manifest', 'pool', 'recognition', 'inspection')))
    if {k: v for k, v in plan.items() if k != 'inputs_evidence'} != rebuilt:
        raise ValueError('计划内容与冻结输入重建结果不同')


def validate_confirm_evidence(plan, receipt, inspection):
    validate_plan(plan)
    if plan['action'] not in ('cleanup', 'reidentify'):
        raise ValueError('只有清理和重识别有已适配的确认步骤')
    action = body(receipt)
    if action.get('case_id') != plan['case_id'] or action.get('action') != plan['action'] or action.get('marker', {}).get('plan_digest') != digest(plan) or action.get('marker', {}).get('phase') != 'button_returned':
        raise ValueError('确认须绑定本计划已返回的原生动作发起回执')
    observed = body(inspection)
    if observed.get('identity', {}).get('userid') != plan['request']['expected_userid'] or str(observed.get('grid', {}).get('funcno')) != plan['request']['grid']:
        raise ValueError('确认检查账号或表格不符')
    if observed.get('projid') != 'WF_YBX' or observed.get('definitionCaptureComplete') is not True or observed.get('context', {}).get('serial') or observed['grid'].get('loading') is not False:
        raise ValueError('确认检查未就绪或当前有报销单上下文')
    definitions = [d for d in observed.get('buttonDefinitions', []) if d.get('caption') == plan['button_definition']['caption']]
    if definitions != [plan['button_definition']] or observed['grid'].get('records') != plan['scope']['grid_records']:
        raise ValueError('确认检查定义或记录数变化')
    row_ids, rows = observed['grid'].get('rowIds', []), observed['grid'].get('rows', [])
    if len(row_ids) != len(rows):
        raise ValueError('确认检查未取得原生行ID')
    selected = []
    for target in plan['targets']:
        matches = [(rid, row) for rid, row in zip(row_ids, rows) if native_field(row, target['native_key']) == target['native_value']]
        if len(matches) != 1 or matches[0][1] != target['native_row']:
            raise ValueError('确认检查目标行已变化')
        selected.append(str(matches[0][0]))
    if sorted(selected) != sorted(map(str, observed['grid'].get('selectedRowIds', []))):
        raise ValueError('确认检查实际勾选不是本次确切目标')
    dialogs = observed.get('dialogs', [])
    if len(dialogs) != 1 or not confirmation_text_matches(plan, dialogs[0].get('text', '')):
        raise ValueError('独立检查没有本次确切原生确认框')
    if plan['action'] == 'reidentify':
        target, context = plan['targets'][0], observed.get('context', {})
        expected = '&'.join([target['native_value'], target['source_sha256']+'.pdf', '.pdf', 'WF_YBX'])
        if context.get('currentFunc') not in ('113', '115') or context.get('selected') != target['native_value'] or context.get('selected_filecode') != target['native_value'] or str(context.get('selected_file_reference', '')).replace('＆','&') != expected:
            raise ValueError('重识别确认的113单票上下文不匹配')
    buttons = [b for b in dialogs[0].get('buttons', []) if re.sub(r'\s+', '', b.get('text', '')) == '继续' and b.get('disabled') is False]
    if len(buttons) != 1:
        raise ValueError('独立检查没有唯一可用的原生继续按钮')
    return True


def confirmation_text_matches(plan, value):
    if not isinstance(value, str):
        return False
    if plan['action'] == 'cleanup':
        return bool(plan['button_definition'].get('clickqueryhint')) and plan['button_definition']['clickqueryhint'] in value
    if plan['action'] != 'reidentify' or len(plan['targets']) != 1:
        return False
    filename = plan['targets'][0]['source_sha256']+'.pdf'
    pattern = r'您确定要重新识别序号为\[[^\]\r\n]{1,100}\],图片名称为\[('+re.escape(filename)+r')\s*\]的未识别图片吗？'
    # 展示序号可能截断；身份只用唯一完整源SHA文件名及独立113上下文。
    return len(re.findall(pattern, value)) == 1 and re.findall(r'[a-f0-9]{64}\.pdf', value) == [filename]


def action_script(plan, request_id, confirm=False):
    validate_plan(plan)
    if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', request_id):
        raise ValueError('request_id须为稳定ASCII标识')
    if plan['action'] == 'refresh':
        if confirm:
            raise ValueError('刷新没有删除确认步骤')
        return refresh_script(plan, request_id)
    if confirm and plan['action'] not in ('cleanup', 'reidentify'):
        raise ValueError('确认步骤仅用于删除清理或原生重识别')
    return r'''(()=>{
 const p=PLAN,requestId=REQUEST_ID,confirm=CONFIRM,req=p.request,vis=e=>!!e&&e.getClientRects().length>0;
 const stable=v=>JSON.stringify(v&&typeof v==='object'?Array.isArray(v)?v.map(x=>JSON.parse(stable(x))):Object.fromEntries(Object.keys(v).sort().map(k=>[k,JSON.parse(stable(v[k]))])):v);
 if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX')throw Error('财务主机或项目不同');
 if(String($.UC.bindData('#0-userinfo.userid#')||'')!==req.expected_userid||String($.UC.bindData('#61-serialno#')||'').trim())throw Error('登录身份或报销单上下文改变');
 const element=document.getElementById('gridWF_YBX_'+req.grid);if(!element?.grid||!vis(element)||element.grid.hDiv.loading)throw Error('目标表未就绪');
 const grid=$(element),ids=grid.jqGrid('getDataIDs').map(String),all=ids.map(id=>({id,row:grid.jqGrid('getRowData',id)}));
 for(const name of ['onSelectRow','onSelectAll']){const fn=grid.jqGrid('getGridParam',name);if((typeof fn==='function'?String(fn):null)!==(p.selection_callbacks?.[name]??null))throw Error('原生选择回调变化');}
 const field=(r,k)=>r['t.'+k]??r[k]??'';
 const selected=()=>{const a=grid.jqGrid('getGridParam','selarrrow');if(!Array.isArray(a))throw Error('没有可靠勾选集合');return a.map(String).sort();};
 const chosen=p.targets.map(t=>{const matches=all.filter(x=>field(x.row,t.native_key)===t.native_value);if(matches.length!==1||stable(matches[0].row)!==stable(t.native_row))throw Error('目标行改变或不在本页');return matches[0];});
 const assertRecognitionContext=()=>{
  const target=p.targets[0],currentFunc=String($.UC.bindData('#0-CURRFUNC#')||''),multi=String($.UC.bindData('#113-multirows#')||'');
  const expectedRef=[target.native_value,target.source_sha256+'.pdf','.pdf','WF_YBX'].join('&'),normalizeRef=value=>String(value||'').replaceAll('＆','&');
  if(chosen.length!==1||!['113','115'].includes(currentFunc)||multi!==target.native_value||String($.UC.bindData('#113-t.filesequcode#')||'')!==target.native_value||normalizeRef($.UC.bindData('#113-t.tk_filecode#'))!==expectedRef||normalizeRef(field(chosen[0].row,'tk_filecode'))!==expectedRef)throw Error('原生单票上下文未准确绑定');
 };
 if(grid.jqGrid('getGridParam','records')!==p.scope.grid_records)throw Error('原生记录数变化，须独立回读');
 const winno=req.grid==='113'?'79':'39',defs=[];
 for(const [pageKey,page]of Object.entries($.page?.list||{}))for(const win of page.wins||[])if(String(win.winno)===winno)for(const b of win.func_btns||[])if(b.caption===p.button_definition.caption){const d={pageKey,winno};for(const k of BUTTON_KEYS)if(b[k]!==undefined)d[k]=typeof b[k]==='function'?String(b[k]):b[k];defs.push(d);}
 if(defs.length!==1||stable(defs[0])!==stable(p.button_definition))throw Error('原生动作定义变化');
 const dialogs=[...document.querySelectorAll('.ui-dialog')].filter(vis),registry=window.__ustcInvoiceRetry||(window.__ustcInvoiceRetry={});
 if(Object.hasOwn(registry,requestId))throw Error('原请求已经启动，只读恢复，不重发');
 if(confirm){
  if(stable(selected())!==stable(chosen.map(x=>x.id).sort()))throw Error('确认前已选目标变化');
  if(dialogs.length!==1)throw Error('原生确认框不唯一');
  if(p.action==='reidentify'){
   assertRecognitionContext();const message=dialogs[0].innerText,filename=p.targets[0].source_sha256+'.pdf';
   const match=[...message.matchAll(/您确定要重新识别序号为\[[^\]\r\n]{1,100}\],图片名称为\[([a-f0-9]{64}\.pdf)\s*\]的未识别图片吗？/g)];
   if(match.length!==1||match[0][1]!==filename||stable(message.match(/[a-f0-9]{64}\.pdf/g))!==stable([filename]))throw Error('重识别确认框源SHA与本票不同');
  }else if(!dialogs[0].innerText.includes(p.button_definition.clickqueryhint))throw Error('删除确认框与本次意图不同');
  // “继续”来自已留证的原生确认契约；出现其他按钮时停止，不猜测替代按钮。
  const buttons=[...dialogs[0].querySelectorAll('button')].filter(b=>vis(b)&&!b.disabled&&b.innerText.replace(/\s+/g,'')==='继续');if(buttons.length!==1)throw Error('真实继续按钮不唯一');
  registry[requestId]={case_id:p.case_id,action:'confirm',confirmation_for:p.action,plan_digest:PLAN_DIGEST,phase:'confirm_requested',targets:p.targets.map(t=>t.native_value)};
  buttons[0].click();registry[requestId].phase='confirm_button_returned';
 }else{
  if(dialogs.length)throw Error('存在未处理弹框');
  if(stable(selected())!==stable(p.before_selected_row_ids.map(String).sort()))throw Error('已有选择变化，不覆盖用户现场');
  const buttons=[...document.querySelectorAll('.foot_winWF_YBX_'+winno+' button[btnname]')].filter(b=>vis(b)&&!b.disabled&&b.getAttribute('btnname')===p.button_definition.caption);if(buttons.length!==1)throw Error('原生按钮不唯一');
  registry[requestId]={case_id:p.case_id,action:p.action,plan_digest:PLAN_DIGEST,phase:'selection_requested',targets:p.targets.map(t=>t.native_value)};
  grid.jqGrid('resetSelection');for(const c of chosen)grid.jqGrid('setSelection',c.id,true);
  if(stable(selected())!==stable(chosen.map(x=>x.id).sort()))throw Error('实际勾选与单票/清理目标不同');
  if(p.action==='reidentify'){
   // 原生113选行触发115详情后，CURRFUNC会变115；79按钮守卫及识别函数仍读取113。
   assertRecognitionContext();
  }
  registry[requestId].phase='button_requested';buttons[0].click();registry[requestId].phase='button_returned';
 }
 return{ok:true,case_id:p.case_id,action:confirm?'confirm':p.action,request_id:requestId,marker:registry[requestId],system_write:true,selection_write:!confirm,
  requires_independent_readback:true,requires_confirmation:!confirm&&['cleanup','reidentify'].includes(p.action),confirmation_for:confirm?p.action:null,deleted:false,reidentified:false,source_targets:p.targets};
})()'''.replace('PLAN_DIGEST', json.dumps(digest(plan))).replace('BUTTON_KEYS', json.dumps(BUTTON_KEYS)).replace('REQUEST_ID', json.dumps(request_id)).replace('CONFIRM', json.dumps(confirm)).replace('PLAN', json.dumps(plan, ensure_ascii=False))


def refresh_script(plan, request_id):
    return r'''(()=>{
 const p=PLAN,requestId=REQUEST_ID,vis=e=>!!e&&e.getClientRects().length>0;
 if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX'||String($.UC.bindData('#0-userinfo.userid#')||'')!==p.request.expected_userid)throw Error('财务项目或账号不同');
 if(String($.UC.bindData('#61-serialno#')||'').trim()||[...document.querySelectorAll('.ui-dialog')].some(vis))throw Error('存在报销单或未处理弹框');
 const grid=document.getElementById('gridWF_YBX_113');if(!vis(grid)||!grid.grid||grid.grid.hDiv.loading)throw Error('113表未就绪');
 const selected=$(grid).jqGrid('getGridParam','selarrrow');if(!Array.isArray(selected)||JSON.stringify([...selected].map(String).sort())!==JSON.stringify(p.before_selected_row_ids.map(String).sort()))throw Error('当前勾选已改变');
 const button=document.getElementById('gridWF_YBX_113_refresh'),expected=p.refresh_control;
 if(!vis(button)||button.tagName!==expected.tag||button.parentElement?.id!=='t_gridWF_YBX_113'||button.innerText.trim()!=='刷新'||button.getAttribute('onclick')!==expected.onclick)throw Error('原生刷新控件变化');
 const handlers=(typeof $._data==='function'?$._data(button,'events')?.click:[])||[];
 if(JSON.stringify(handlers.map(x=>String(x.handler)))!==JSON.stringify(expected.nativeClickHandlers))throw Error('原生刷新监听器变化');
 const registry=window.__ustcInvoiceRetry||(window.__ustcInvoiceRetry={});if(Object.hasOwn(registry,requestId))throw Error('原请求已启动，只读恢复，不重发');
 registry[requestId]={case_id:p.case_id,action:'refresh',plan_digest:PLAN_DIGEST,phase:'refresh_requested',targets:[]};button.click();registry[requestId].phase='refresh_button_returned';
 return{ok:true,case_id:p.case_id,action:'refresh',request_id:requestId,marker:registry[requestId],system_write:false,selection_write:false,query_refresh_requested:true,reidentified:false,requires_independent_readback:true};
})()'''.replace('PLAN_DIGEST', json.dumps(digest(plan))).replace('REQUEST_ID', json.dumps(request_id)).replace('PLAN', json.dumps(plan, ensure_ascii=False))


def parse_observed_time(value):
    if not isinstance(value, str):
        raise ValueError('回执必须有带时区的实际采集/执行时间')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('回执采集/执行时间不是ISO格式') from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('回执采集/执行时间缺少时区')
    return result


def validate_readback_evidence(plan, pool_receipt, recognition_receipt, receipt):
    from finance_invoice_reconcile import grid_scope
    validate_plan(plan)
    req = plan['request']
    _, pool, ps = grid_scope(pool_receipt, '53', req['expected_userid'])
    _, recognition, rs = grid_scope(recognition_receipt, '113', req['expected_userid'])
    if not ps['complete'] or not rs['complete']:
        raise ValueError('处理后回读必须为完整票池和识别明细')
    expected_action = 'confirm' if plan['action'] in ('cleanup', 'reidentify') else plan['action']
    expected_phase = {'confirm': 'confirm_button_returned', 'refresh': 'refresh_button_returned', 'reidentify': 'button_returned'}[expected_action]
    if not isinstance(receipt, dict) or receipt.get('schema') != 'ustc-edge-operation-v2' or receipt.get('phase') != 'script_completed' or receipt.get('tab_id') != req['tab_id']:
        raise ValueError('处理后回读须引用本次成功动作的原始运行时回执')
    action = body(receipt)
    if action.get('case_id') != plan['case_id'] or action.get('action') != expected_action or action.get('marker', {}).get('plan_digest') != digest(plan) or action.get('marker', {}).get('phase') != expected_phase:
        raise ValueError('处理后回读动作、计划或执行阶段不符；cleanup/reidentify须引用confirm')
    executed = parse_observed_time(receipt.get('executed_at_utc'))
    for label, scope in [('pool', ps), ('recognition', rs)]:
        observed = parse_observed_time(scope.get('observed_at'))
        baseline = parse_observed_time(plan['scope']['complete_'+label].get('observed_at'))
        if observed <= baseline or observed <= executed:
            raise ValueError('处理后'+label+'采集时间须晚于计划基线及本次动作完成时间')
    return {'pool': pool, 'recognition': recognition, 'pool_scope': ps, 'recognition_scope': rs,
            'action_evidence': {'action': expected_action, 'request_id': action.get('request_id'), 'executed_at_utc': receipt['executed_at_utc'],
                                'plan_digest': digest(plan), 'marker_phase': expected_phase}}


def readback(plan, pool_receipt, recognition_receipt, receipt):
    from finance_invoice_reconcile import invoice_number
    from finance_surface import parse_recognition_error
    verified = validate_readback_evidence(plan, pool_receipt, recognition_receipt, receipt)
    req, pool, recognition = plan['request'], verified['pool'], verified['recognition']
    rows = pool['rows'] if req['grid'] == '53' else recognition['rows']
    result = []
    for target in plan['targets']:
        exact = [r for r in rows if native_field(r, target['native_key']) == target['native_value']]
        source = {'invoice_number_candidates': [target['invoice_number']], 'invoice_code': target.get('invoice_code')}
        pool_matches = [r for r in pool['rows'] if source_matches(r, source)]
        recognition_matches = [r for r in recognition['rows'] if native_field(r, 'show_filename') == target['source_sha256']+'.pdf']
        reserved = target.get('recognition_error', {}).get('error_type') == 'duplicate_reserved_invoice' if target.get('recognition_error') else False
        result.append({**target, 'native_record_absent': not exact, 'pool_rows': pool_matches, 'recognition_rows': recognition_matches,
            'recognition_errors': [parse_recognition_error(native_field(r,'show_err_msg')) for r in recognition_matches],
            'local_state': ('预约仍受阻，仅本账号失败上传记录已清理' if reserved and not exact else
                            '原异常池记录已清理，尚未据此确认重传或认证' if plan['action']=='cleanup' and not exact else
                            '仍有识别记录，结果按完整回执判断' if recognition_matches else '已观察到同票号池记录' if pool_matches else '未观察到可归账记录，不能认定成功'),
            'reservation_released': False, 'reimbursement_or_payment_confirmed': False})
    current_keys = {native_field(r, 'tk_guid' if req['grid']=='53' else 'filesequcode') for r in rows}
    missing_protected = [key for key in plan['protected_native_ids'] if key not in current_keys]
    return {'schema': 'ustc-finance-invoice-retry-readback-v1', 'case_id': plan['case_id'], 'action': plan['action'],
        'action_evidence': verified['action_evidence'],
        'complete': True, 'targets': result, 'all_target_records_absent': bool(result) and all(r['native_record_absent'] for r in result),
        'protected_record_changes': missing_protected, 'protected_records_unchanged': not missing_protected,
        'system_write': False, 'reservation_released': False, 'retry_automatically': False}


def run_browser(source, request, output, operation_kind='read', keep_internal_tab=False):
    output = Path(output)
    script = output.with_suffix('.js')
    if output.exists() or script.exists():
        raise ValueError('回执或脚本已存在，保留原标记而不重放')
    script.parent.mkdir(parents=True, exist_ok=True)
    with script.open('x', encoding='utf-8') as stream:
        stream.write(source)
    subprocess.run([sys.executable, str(ROOT/'tools/edge_runtime.py'), str(script), '--tab-id', request['tab_id'],
                    '--output', str(output), '--quiet', '--timeout', '60', '--operation-kind', operation_kind]+(['--keep-internal-tab'] if keep_internal_tab else []), check=False)
    if not output.exists():
        raise RuntimeError('桥接没有回执；不重发')
    return load(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['inspect', 'preview', 'reidentify', 'refresh', 'cleanup', 'confirm', 'readback', 'recover'])
    for name in ('request', 'manifest', 'pool', 'recognition', 'inspection', 'plan', 'receipt'):
        parser.add_argument('--'+name, type=Path)
    parser.add_argument('--plan-sha256')
    parser.add_argument('--receipt-sha256')
    parser.add_argument('--request-id')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.execute and args.command not in ('refresh', 'reidentify', 'cleanup', 'confirm'):
        raise ValueError('只读/预览动作不接受execute')
    if args.output.exists():
        raise ValueError('输出已存在；不覆盖或重放')
    def require(*names):
        if any(getattr(args, name) is None for name in names):
            raise ValueError('缺少参数：' + ', '.join('--'+n.replace('_','-') for n in names))
    def reference(path):
        return {'path': str(path.resolve()), 'sha256': sha(path)}
    if args.command == 'inspect':
        require('request')
        request = validate_request(load(args.request))
        result = run_browser(inspect_script(request, args.request_id), request, args.output.parent/'native.json')
        write(args.output, result)
    elif args.command == 'preview':
        require('request', 'manifest', 'pool', 'recognition', 'inspection')
        names = ('request', 'manifest', 'pool', 'recognition', 'inspection')
        result = preview(*(load(getattr(args, n)) for n in names))
        result['inputs_evidence'] = {n: reference(getattr(args, n)) for n in names}
        write(args.output, result)
    else:
        require('plan', 'plan_sha256')
        if sha(args.plan) != args.plan_sha256:
            raise ValueError('计划文件SHA不符')
        plan = validate_plan(load(args.plan))
        verify_plan_evidence(plan)
        if args.command == 'readback':
            require('pool', 'recognition', 'receipt', 'receipt_sha256')
            if sha(args.receipt) != args.receipt_sha256:
                raise ValueError('本次动作回执SHA不符')
            for name in ('pool', 'recognition'):
                if sha(getattr(args, name)) == plan['inputs_evidence'][name]['sha256']:
                    raise ValueError('不能把操作前原回执当作处理后独立回读')
            result = readback(plan, load(args.pool), load(args.recognition), load(args.receipt))
            result['inputs_evidence'] = {n: reference(getattr(args, n)) for n in ('plan', 'pool', 'recognition', 'receipt')}
            write(args.output, result)
        elif args.command == 'recover':
            require('request_id')
            result = run_browser(inspect_script(plan['request'], args.request_id), plan['request'], args.output.parent/'native.json')
            write(args.output, result)
        else:
            require('request_id')
            if args.command != 'confirm':
                validate_plan(plan, args.command)
            else:
                require('receipt', 'receipt_sha256', 'inspection')
                if sha(args.receipt) != args.receipt_sha256:
                    raise ValueError('删除发起回执SHA不符')
                validate_confirm_evidence(plan, load(args.receipt), load(args.inspection))
            source = action_script(plan, args.request_id, args.command == 'confirm')
            if not args.execute:
                result = {'ok': True, 'case_id': plan['case_id'], 'action': args.command, 'planned_only': True, 'executed': False, 'system_write': False, 'plan_digest': digest(plan)}
                write(args.output, result)
            else:
                # A frozen plan/action is single-use even if a caller changes request_id or output.
                intent_key = digest({'case_id': plan['case_id'], 'plan_digest': digest(plan), 'action': args.command})
                intent_path = ROOT/'.local/invoice_retry_intents'/plan['case_id']/(intent_key+'.json')
                if intent_path.exists():
                    raise ValueError('该计划动作已有持久意图；只读恢复或独立回读，不重发')
                intent = {'case_id': plan['case_id'], 'action': args.command, 'request_id': args.request_id, 'plan': reference(args.plan), 'plan_digest': digest(plan), 'created_at': datetime.now(timezone.utc).isoformat(), 'output': str(args.output.resolve())}
                write(intent_path, intent)
                result = run_browser(source, plan['request'], args.output.parent/'native.json', 'browser-local' if args.command == 'refresh' else 'write')
                write(args.output, result)
    try:
        success = body(result).get('ok') is True
    except ValueError:
        success = result.get('ready', result.get('complete', False)) is True
    print(json.dumps({'ok': success, 'output': str(args.output), 'command': args.command}, ensure_ascii=False))
    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
