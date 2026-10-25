#!/usr/bin/env python3
"""独立静默选票：默认预览；明确执行后只变更原生勾选，不建单或提交。"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
import sys
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'ustc-invoice-selection-v1'
ALIASES = {
    'native_guid': ['t.tk_guid', 'tk_guid'],
    'image_number': ['t.show_id', 't.show_seq', 'show_id', 'show_seq'],
    'amount': ['t.tk_total_amt', 'tk_total_amt', 't.amt', 'amt'],
    'seller': ['t.tk_salename', 'tk_salename'],
    'invoice_type': ['t.tk_type', 'tk_type', 't.invoice_type'],
    'content': ['t.tk_display_abst', 'tk_display_abst'],
    'invoice_date': ['t.tk_date', 'tk_date'],
    'invoice_code': ['t.tk_code', 'tk_code'],
}
IDENTIFIERS = {'invoice_number', 'native_guid', 'image_number'}
PROTECTED_FIELDS = ['t.tk_guid', 't.tk_code_abst', 't.tk_total_amt', 't.tk_type', 't.tk_date',
                    't.tk_salename', 't.tk_buysno', 't.serial_no', 't.tk_mod_flag', 't.auth_rst_desc']


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def text(value):
    return html.unescape(re.sub(r'<[^>]*>', '', str(value))).strip() if value is not None else ''


def value(row, name):
    return next((text(row[k]) for k in ALIASES[name] if k in row and text(row[k])), '')


def money(value_):
    s = text(value_).replace(',', '')
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,2})?', s):
        raise ValueError('金额须为非负十进制，最多两位小数')
    return Decimal(s).quantize(Decimal('.01'))


def numbers(row):
    found = {n for k in ['t.tk_code_abst', 'tk_code_abst', 't.tk_no', 'tk_no']
            for n in re.findall(r'(?<![0-9])[0-9]{20}(?![0-9])', text(row.get(k)))}
    medical = re.fullmatch(r'医/([0-9]{8})/([0-9]{10})', text(row.get('t.tk_code_abst')))
    if value(row, 'invoice_type') == 'MEDICAL' and medical and medical[1] == value(row, 'invoice_code'):
        found.add(medical[2])
    return found


def validate_request(request):
    if not isinstance(request, dict) or not isinstance(request.get('case_id'), str) or not request['case_id'].strip():
        raise ValueError('需要明确case_id')
    if not isinstance(request.get('tab_id'), str) or not re.fullmatch(r'[0-9]+', request['tab_id']):
        raise ValueError('需要本次实际ASCII tab_id字符串')
    if str(request.get('grid', '53')) not in ['53', '1669']:
        raise ValueError('仅支持已核对的53或1669发票表格')
    mode = request.get('mode', 'append')
    if mode not in ['append', 'replace', 'remove']:
        raise ValueError('mode须为append/replace/remove')
    if str(request.get('grid', '53')) == '1669' and not re.fullmatch(r'[0-9]+', str(request.get('serial', ''))):
        raise ValueError('1669须明确本案已有单号')
    selectors = request.get('selectors')
    if not isinstance(selectors, list) or (not selectors and mode != 'replace'):
        raise ValueError('selectors须为非空列表；replace可明确清空本案已选集')
    for selector in selectors:
        if not isinstance(selector, dict) or not IDENTIFIERS.intersection(selector) or set(selector) - (IDENTIFIERS | set(ALIASES)):
            raise ValueError('每个条件需要票号、原生GUID或图片编号；其余仅允许规定辅助参数')
        if any(not isinstance(v, str) or not v.strip() for v in selector.values()):
            raise ValueError('所有选择条件均须为非空字符串')
        if 'invoice_number' in selector and not re.fullmatch(r'[0-9]{20}', selector['invoice_number']):
            if not re.fullmatch(r'[0-9]{10}', selector['invoice_number']) or not re.fullmatch(r'[0-9]{8}', selector.get('invoice_code', '')):
                raise ValueError('票号只接受20位ASCII数字字符串；医疗财政票另须10位号码及8位代码')
        if 'amount' in selector:
            money(selector['amount'])
    expected = request.get('expected_before_guids')
    if expected is not None and (not isinstance(expected, list) or any(not isinstance(x, str) or not x for x in expected) or len(set(expected)) != len(expected)):
        raise ValueError('expected_before_guids须为唯一原生GUID字符串列表')
    return {**request, 'grid': str(request.get('grid', '53')), 'mode': mode}


def body(receipt):
    for _ in range(3):
        if isinstance(receipt, dict) and isinstance(receipt.get('grids'), list):
            break
        receipt = receipt.get('result') if isinstance(receipt, dict) else None
    if not isinstance(receipt, dict) or receipt.get('ok') is not True or receipt.get('projid') != 'WF_YBX':
        raise ValueError('必须是成功的原生WF_YBX完整回执，unknown不可放行')
    return receipt


def observed_tabs(receipt):
    result = []
    for _ in range(4):
        if not isinstance(receipt, dict):
            break
        if receipt.get('tab_id') is not None:
            result.append(str(receipt['tab_id']))
        receipt = receipt.get('result')
    return result


def grid_state(receipt, grid, allow_missing_selection=False):
    b = body(receipt)
    gs = [g for g in b['grids'] if str(g.get('funcno')) == grid and g.get('visible', True)]
    if len(gs) != 1:
        raise ValueError('本次可见目标表格必须唯一')
    g = gs[0]
    rows = g.get('rows', [])
    if g.get('loading') or g.get('records') != len(rows) or g.get('allRecordsVisible') is False:
        raise ValueError('需要完整分页收集或全部记录已加载，首屏不代表全量')
    if len({value(r, 'native_guid') for r in rows}) != len(rows) or any(not value(r, 'native_guid') for r in rows):
        raise ValueError('完整列表GUID缺失或重复')
    selection = g.get('selection')
    missing_selection = not isinstance(selection, dict) or not isinstance(selection.get('selectedRowIds'), list)
    if missing_selection and not allow_missing_selection:
        raise ValueError('回执缺少实际selarrrow，不能用PC_Has_chosen猜当前勾选')
    ids = g.get('rowIds') or [x.get('rowId') for x in g.get('rawRows', [])]
    if len(ids) != len(rows) or any(not x for x in ids) or (len(set(ids)) != len(ids) and not (g.get('paginationReloaded') and not missing_selection and selection.get('selectedGuids') == [])):
        raise ValueError('回执缺少可追溯原生行ID')
    selected = None if missing_selection else selection.get('selectedGuids')
    if selected is None and not missing_selection:
        mapping = dict(zip(ids, rows))
        if any(x not in mapping for x in selection['selectedRowIds']):
            raise ValueError('已选行不在完整范围，不能隐式丢弃')
        selected = [value(mapping[x], 'native_guid') for x in selection['selectedRowIds']]
    if selected is not None and (len(set(selected)) != len(selected) or any(x not in {value(r, 'native_guid') for r in rows} for x in selected)):
        raise ValueError('已选GUID不完整或重复')
    identity = b.get('identity', {})
    if not isinstance(identity.get('userid'), str) or not identity['userid']:
        raise ValueError('回执缺少实际登录身份')
    return b, g, list(zip(ids, rows)), None if selected is None else sorted(selected)


def flatten_audits(receipts):
    audits = []
    for receipt in receipts:
        reports = receipt.get('reports', [receipt])
        for report in reports:
            pages = report.get('pages', [report.get('page')])
            for page in pages:
                if isinstance(page, dict):
                    audits.append({'path': report.get('path'), 'sha256': report.get('sha256'), 'page': page})
    return audits


def binding(row, manifest, audits, require_eligible=True):
    guid, native_numbers = value(row, 'native_guid'), numbers(row)
    candidates = []
    for source in manifest.get('original_files', []):
        ns = set(source.get('invoice_number_candidates', []))
        verified = source.get('verified_native_binding', {})
        direct = native_numbers & ns
        if value(row, 'invoice_type') == 'MEDICAL' and source.get('invoice_code') != value(row, 'invoice_code'):
            direct = set()
        explicit = (verified.get('confirmed') is True and verified.get('source_sha256') == source.get('sha256')
                    and verified.get('guid') == guid and verified.get('number') in ns
                    and verified.get('type') == value(row, 'invoice_type')
                    and verified.get('display') == value(row, 'content')
                    and verified.get('amount') == str(money(value(row, 'amount')))
                    and verified.get('person_id') == text(row.get('t.tk_buysno'))
                    and verified.get('receipt') and Path(verified['receipt']).is_file())
        if len(direct) == 1 or explicit:
            candidates.append((source, next(iter(direct)) if direct else verified['number']))
    if len(candidates) != 1:
        raise ValueError('原生行无法唯一绑定本案已核原件；非本案/重复/NaN不得猜测：' + guid)
    source, number = candidates[0]
    path = Path(source['path'])
    if not path.is_file() or sha(path) != source.get('sha256'):
        raise ValueError('本案原件SHA变化：' + str(path))
    checks = [a for a in audits if a['sha256'] == source['sha256']
              and number in a['page'].get('invoice_number_candidates', [])]
    if len(checks) != 1:
        raise ValueError('缺少该原件完整且唯一的票面审核：' + number)
    check = checks[0]['page'];validation = check.get('validation', {})
    medical = value(row, 'invoice_type') == 'MEDICAL'
    if require_eligible:
        if medical:
            owner = source.get('material_owner')
            code = check.get('verification', {}).get('fields', {}).get('invoice_code')
            if (manifest.get('personal_medical_fiscal_allowed') is not True or not owner or
                owner not in manifest.get('allowed_medical_payers', []) or
                check.get('kind') != 'medical_fiscal' or check.get('payer_name') != owner or
                validation.get('tax_requirement') != 'not_applicable_to_personal_medical_fiscal_ticket' or
                not re.fullmatch(r'[0-9]{10}', number) or code != source.get('invoice_code') or
                code != value(row, 'invoice_code')):
                raise ValueError('医疗财政票的本案授权、交款人或代码审核未通过：' + number)
        elif validation.get('title_matches_expected') is not True or validation.get('tax_id_matches_expected') is not True:
            raise ValueError('购方名称/税号审核未通过，系统NORMAL不能替代：' + number)
        if money(check.get('total_amount')) != money(value(row, 'amount')):
            raise ValueError('原件审核金额与原生行不同：' + number)
    result = {'guid': guid, 'invoice_number': number, 'source_path': str(path.resolve()), 'source_sha256': source['sha256'],
              'native_row': deepcopy(row), 'buyer_fields_passed': validation.get('title_matches_expected') is True and validation.get('tax_id_matches_expected') is True}
    if medical:
        result.update(invoice_code=source.get('invoice_code'), medical_fiscal_fields_passed=require_eligible,
                      buyer_tax_requirement='not_applicable_to_personal_medical_fiscal_ticket')
    return result


def matches(row, selector, manifest=None):
    for key, expected in selector.items():
        if key == 'invoice_number':
            if expected not in numbers(row):
                bindings = [s.get('verified_native_binding', {}) for s in (manifest or {}).get('original_files', [])]
                if not any(b.get('confirmed') is True and b.get('number') == expected and b.get('guid') == value(row, 'native_guid') for b in bindings):
                    return False
        elif key == 'amount':
            if money(value(row, key)) != money(expected):
                return False
        elif key == 'image_number':
            if re.sub(r'\s+', '', value(row, key)) != re.sub(r'\s+', '', expected):
                return False
        elif value(row, key) != expected:
            return False
    return True


def preview(request, manifest, receipt, audit_receipts):
    request = validate_request(request)
    if manifest.get('case_id') != request['case_id']:
        raise ValueError('清单与请求不是同案')
    b, g, rows, actual_before = grid_state(receipt, request['grid'], allow_missing_selection=True)
    tabs = observed_tabs(receipt)
    if any(tab != request['tab_id'] for tab in tabs):
        raise ValueError('原生回执不是本次请求的tab_id')
    selection_available = actual_before is not None
    before = actual_before or []
    if request.get('expected_userid') and request['expected_userid'] != b['identity']['userid']:
        raise ValueError('登录身份与请求不同')
    if manifest.get('expected_userid') and manifest['expected_userid'] != b['identity']['userid']:
        raise ValueError('登录身份与本案材料清单不同')
    if selection_available and request.get('expected_before_guids') is not None and sorted(request['expected_before_guids']) != before:
        raise ValueError('实际已有勾选与expected_before_guids不符')
    if request['grid'] == '1669' and text(b.get('serial')) != request['serial']:
        raise ValueError('1669不是本案明确已有单号')
    audits = flatten_audits(audit_receipts)
    targets = []
    for selector in request['selectors']:
        found = [(rid, row) for rid, row in rows if matches(row, selector, manifest)]
        if len(found) != 1:
            raise ValueError('选择条件缺失或重复，不按金额/销售方猜票：' + json.dumps(selector, ensure_ascii=False))
        targets.append(found[0])
    guids = [value(row, 'native_guid') for _, row in targets]
    if len(set(guids)) != len(guids):
        raise ValueError('多个选择条件指向同一票')
    if selection_available and request['mode'] == 'remove' and not set(guids).issubset(before):
        raise ValueError('remove只能取消当前已选票，不能当作新增选择')
    final = sorted(set(before) | set(guids)) if request['mode'] == 'append' else sorted(guids) if request['mode'] == 'replace' else sorted(set(before) - set(guids))
    adds, removes = sorted(set(final)-set(before)), sorted(set(before)-set(final))
    if not selection_available and request['mode'] == 'remove':
        removes = sorted(guids)
    by_guid = {value(row, 'native_guid'): row for _, row in rows}
    bindings = {}
    # 被移除票只核同案原件与审核来源，允许取消购方错误/占用/未认证票。
    for guid in set(final) | set(removes):
        row = by_guid[guid]
        bindings[guid] = binding(row, manifest, audits, require_eligible=guid in adds)
        if guid in adds:
            bound = text(row.get('t.serial_no', row.get('serial_no')))
            if bound and not (request['grid'] == '1669' and bound == request['serial']):
                raise ValueError('票据已绑定其他单据：' + guid)
            if request['grid'] == '53' and (text(row.get('t.tk_mod_flag', row.get('tk_mod_flag'))) != 'NORMAL' or text(row.get('t.auth_rst_desc', row.get('auth_rst_desc'))) != '正常(已认证)'):
                raise ValueError('新增票必须实际正常认证，不接受未认证/预约占用：' + guid)
            if text(row.get('t.show_err_msg')) or '预约' in text(row.get('t.auth_rst_desc')) or any(text(row.get(k)) for k in ['t.reserved_serial_no', 't.reservation_serial', 't.occupied_by']):
                raise ValueError('新增票存在预约占用或识别错误：' + guid)
            if request['grid'] == '53':
                # 复用既有原件→认证池绑定合同（含航空显式verified_native_binding），
                # 额外的购方审核和取消选择语义由本工具承担。
                try:
                    from select_case_invoices import select
                except ModuleNotFoundError:
                    from tools.select_case_invoices import select
                source = next(s for s in manifest['original_files'] if s['sha256'] == bindings[guid]['source_sha256'])
                select({'case_id': request['case_id'], 'original_files': [source], 'expected_count': 1},
                       {'result': {'grids': [{'funcno': '53', 'rows': [row]}]}}, str(money(value(row, 'amount'))))
    ready = selection_available and request['grid'] == '53' and (g.get('selection') or {}).get('multiselect') is True
    reason = None if ready else '1669纯勾选回调未独立证实，拆分写入交correction；或当前原生表非multiselect'
    rendered = set(g.get('renderedGuids', [value(row, 'native_guid') for _, row in rows]))
    if len(rendered) != g['records'] or not set(before + final).issubset(rendered):
        ready = False;reason = '完整分页已核但最终/已有票不在同一原生加载页；需用原生查询把目标定位到同一可选范围，禁止跨页漏选'
    if not selection_available:
        reason = '缺少实际selarrrow/全已选GUID，只输出真实候选匹配；不从已选0张或PC_Has_chosen猜选择状态'
    if any(key not in b for key in ['serial', 'node']):
        ready = False;reason = (reason+'；' if reason else '')+'单据/节点上下文未采集，不能用于apply'
    if not tabs:
        ready = False;reason = (reason+'；' if reason else '')+'原生标签ID未留证，不能用于apply'
    try:
        candidate_amount = str(sum((money(value(row, 'amount')) for _, row in targets), Decimal('0')))
    except ValueError:
        candidate_amount = None  # 取消错误票不因其显示金额格式错误而被卡住。
    return {'schema': SCHEMA, 'case_id': request['case_id'], 'request': request, 'identity': b['identity'],
            'current_page': str(b.get('currentPage')), 'serial_context': text(b.get('serial')) if 'serial' in b else None, 'node_context': text(b.get('node')) if 'node' in b else None,
            'context_available': all(key in b for key in ['serial', 'node']),
            'observed_tab_id': tabs[0] if tabs else None,
            'grid': request['grid'], 'scope': {'complete': True, 'records': g['records'], 'rendered_guids': sorted(rendered)},
            'selection_available': selection_available,
            'before_guids': actual_before, 'target_guids': sorted(guids), 'after_guids': final if selection_available else None,
            'added_guids': adds if selection_available else None, 'removed_guids': removes if selection_available else None, 'preserved_guids': sorted(set(before) & set(final)) if selection_available else None,
            'bindings': bindings, 'apply_ready': ready, 'unsupported_reason': reason,
            'candidate_amount': candidate_amount,
            'executed': False, 'selection_write': False, 'draft_created': False, 'submitted': False,
            'note': '移除仅取消勾选，不删除票据；新增门槛独立于财务系统NORMAL。'}


def snapshot_script(request):
    request = validate_request(request)
    return r'''(async()=>{
 const request=REQUEST,gridId='gridWF_YBX_'+request.grid,e=document.getElementById(gridId),g=$(e);
 if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX'||!e?.grid||!e.getClientRects().length)throw Error('可见财务目标表未就绪');
 const userid=$.UC.bindData('#0-userinfo.userid#');if(request.expected_userid&&userid!==request.expected_userid)throw Error('登录身份不同');
 const vis=()=>!!e.getClientRects().length,selected=()=>g.jqGrid('getGridParam','selarrrow')||[];
 const capture=()=>{if(!vis()||e.grid.hDiv.loading)throw Error('目标表加载中或页面改变');const ids=g.jqGrid('getDataIDs');return ids.map(id=>({id,queryPage:g.jqGrid('getGridParam','page'),row:g.jqGrid('getRowData',id)}));};
 const start=capture(),before=selected().slice(),original={page:g.jqGrid('getGridParam','page'),rowNum:g.jqGrid('getGridParam','rowNum')},count=Number(g.jqGrid('getGridParam','records'));
 if(!Number.isSafeInteger(count)||count<0||count>5000)throw Error('完整范围记录数异常或超过有界分页上限');
 const initialGuids=before.map(id=>{const row=start.find(x=>x.id===id)?.row;if(!row)throw Error('已选行不在当前范围');return row['t.tk_guid']||row.tk_guid;});
 let all=start,paginationReloaded=false;
 const reload=async(page,rowNum)=>{if(selected().length)throw Error('分页会丢失已有勾选，保持原状态并停止');g.jqGrid('setGridParam',{page,rowNum}).trigger('reloadGrid');await new Promise(r=>setTimeout(r,100));const end=Date.now()+8000;while(e.grid.hDiv.loading){if(Date.now()>end)throw Error('原生分页查询超时');await new Promise(r=>setTimeout(r,100));}if(!vis()||Number(g.jqGrid('getGridParam','records'))!==count||$.UC.bindData('#0-userinfo.userid#')!==userid)throw Error('分页期间范围或身份改变');};
 if(start.length!==count){
  if(before.length||start.some(x=>!['','0','false','False','N','否'].includes(String(x.row['t.PC_Has_chosen']||'').trim())))throw Error('当前已有勾选，不能以预览翻页重置；请提供完整已选范围回执');
  const allowed=(g.jqGrid('getGridParam','rowList')||[]).map(Number).filter(x=>Number.isSafeInteger(x)&&x>0&&x<=500),size=Math.max(Number(original.rowNum),...allowed);
  await reload(1,size);paginationReloaded=true;all=capture();
  if(all.length!==count){
   const last=Number(g.jqGrid('getGridParam','lastpage'));if(!Number.isSafeInteger(last)||last<1||last>50)throw Error('分页范围超过有界上限');
   try{for(let page=2;page<=last;page++){await reload(page,size);all.push(...capture());}}
   finally{await reload(original.page,original.rowNum);}
  }
 }
 const rendered=capture(),guid=x=>x.row['t.tk_guid']||x.row.tk_guid;
 if(all.length!==count||new Set(all.map(guid)).size!==count||all.some(x=>!guid(x)))throw Error('分页记录重复/缺失，未证明完整');
 const selection={multiselect:!!g.jqGrid('getGridParam','multiselect'),selectedRowIds:selected().slice(),selectedGuids:initialGuids};
 const boundedFunction=f=>typeof f==='function'?{nativeType:'function',source:String(f).slice(0,16000),truncated:String(f).length>16000}:null;
 return{ok:true,projid:'WF_YBX',identity:{userid,username:$.UC.bindData('#0-userinfo.username#')},currentPage:String($.page.currPage),serial:$.UC.bindData('#61-serialno#')??null,node:$.UC.bindData('#61-node_id#')??null,system_write:false,selection_write:false,
 grids:[{funcno:request.grid,id:gridId,visible:true,records:count,rowCount:count,loading:false,allRecordsVisible:true,rowIds:all.map(x=>x.id),rawRows:all.map(x=>({rowId:x.id,queryPage:x.queryPage})),rows:all.map(x=>x.row),renderedGuids:rendered.map(guid),selection,paginationReloaded,nativeQuery:$.Q.list[request.grid].url,
 columnDefinitions:(g.jqGrid('getGridParam','colModel')||[]).map(c=>({name:c.name,hidden:!!c.hidden,formatter:boundedFunction(c.formatter)})),
 rowEventHandlers:Object.fromEntries(['onSelectRow','onCellSelect','ondblClickRow'].map(k=>[k,boundedFunction(g.jqGrid('getGridParam',k))]))}]};
})()'''.replace('REQUEST', json.dumps(request, ensure_ascii=False))


def apply_script(plan, request_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', request_id):
        raise ValueError('request_id须为稳定ASCII编号')
    if not plan.get('apply_ready') or plan['grid'] != '53':
        raise ValueError(plan.get('unsupported_reason') or '预览未放行')
    core = (ROOT/'tools/finance_dom_core.js').read_text()
    return r'''(()=>{
 CORE
 const p=PLAN,requestId=REQUEST_ID;
 if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX'||$.UC.bindData('#0-userinfo.userid#')!==p.identity.userid||$.UC.bindData('#0-userinfo.username#')!==p.identity.username||String($.page.currPage)!==p.current_page||String($.UC.bindData('#61-serialno#')??'')!==p.serial_context||String($.UC.bindData('#61-node_id#')??'')!==p.node_context)throw Error('身份/页面/单据上下文已改变');
 const e=document.getElementById('gridWF_YBX_53'),g=$(e);if(!e?.grid||!e.getClientRects().length||e.grid.hDiv.loading||!g.jqGrid('getGridParam','multiselect'))throw Error('原生勾选表未就绪');
 const rows=()=>g.jqGrid('getDataIDs').map(id=>({id,row:g.jqGrid('getRowData',id)})),guid=x=>x.row['t.tk_guid'],selected=()=>{const rs=rows();return(g.jqGrid('getGridParam','selarrrow')||[]).map(id=>{const x=rs.find(x=>x.id===id);if(!x)throw Error('已选行不在当前范围');return guid(x);}).sort();};
 const equals=(a,b)=>JSON.stringify(a.slice().sort())===JSON.stringify(b.slice().sort());
 if(Number(g.jqGrid('getGridParam','records'))!==p.scope.records||!equals(selected(),p.before_guids))throw Error('完整范围/已有勾选与已批准预览不同');
 const all=rows();if(all.length!==p.scope.records)throw Error('执行时并未加载完整原生查询范围');for(const id of [...p.before_guids,...p.after_guids]){if(all.filter(x=>guid(x)===id).length!==1)throw Error('目标GUID不在同一加载页或重复');}
 const protectedKeys=PROTECTED_KEYS;
 const immutable=rs=>rs.map(x=>Object.fromEntries(protectedKeys.map(k=>[k,x.row[k]]))).sort((a,b)=>String(a['t.tk_guid']).localeCompare(String(b['t.tk_guid'])));
 const originalFields=JSON.stringify(immutable(all));
 for(const[guid,b]of Object.entries(p.bindings)){
  const row=all.find(x=>x.row['t.tk_guid']===guid)?.row;if(!row||JSON.stringify(row)!==JSON.stringify(b.native_row))throw Error('预览后的原生票据字段改变');
  if(p.added_guids.includes(guid))ReimbursementDOM.cents(row['t.tk_total_amt']);
 }
 const used=window.__reimbursementInvoiceSelectionRequests||(window.__reimbursementInvoiceSelectionRequests={});
 if(Object.hasOwn(used,requestId))throw Error('该选票请求已启动，不重发；只读原标记');used[requestId]={caseId:p.case_id,before:p.before_guids,after:p.after_guids};
 // 复用现有create_daily_draft的原生setSelection机制，完全不点击前往报销。
 for(const id of p.removed_guids){const x=all.find(x=>guid(x)===id);g.jqGrid('setSelection',x.id,true);}
 for(const id of p.added_guids){const x=all.find(x=>guid(x)===id);g.jqGrid('setSelection',x.id,true);}
 if(JSON.stringify(immutable(rows()))!==originalFields)throw Error('勾选回调改变票面/认证/单号字段，停止并独立读回');
 if(!equals(selected(),p.after_guids))throw Error('原生勾选不符合预期，停止并独立读回；不自动重做');
 return{ok:true,action:'native_invoice_selection_changed',case_id:p.case_id,request_id:requestId,before_guids:p.before_guids,after_guids:selected(),added_guids:p.added_guids,removed_guids:p.removed_guids,selection_write:true,draft_created:false,submitted:false,invoice_deleted:false,requiresIndependentReadback:true};
})()'''.replace('CORE', core).replace('PLAN', json.dumps(plan, ensure_ascii=False)).replace('REQUEST_ID', json.dumps(request_id)).replace('PROTECTED_KEYS', json.dumps(PROTECTED_FIELDS))


def readback(plan, receipt):
    if plan.get('after_guids') is None:
        raise ValueError('预览缺少实际选择状态，无法用于执行后核对')
    b, g, rows, selected = grid_state(receipt, plan['grid'])
    same_identity = b['identity'] == plan['identity']
    tabs = observed_tabs(receipt)
    same_tab = bool(tabs) and all(tab == plan['request']['tab_id'] for tab in tabs)
    same_context = str(b.get('currentPage')) == plan['current_page'] and text(b.get('serial')) == plan['serial_context'] and text(b.get('node')) == plan['node_context']
    same_scope = g['records'] == plan['scope']['records']
    observed = {value(row, 'native_guid'): row for _, row in rows}
    field_differences = []
    # 取消勾选不会删除票据；被取消/被replace移除的票仍须保持原字段。
    # bindings覆盖保留、新增及取消的before/after联合，不只核最终已选集。
    for guid in sorted(plan['bindings']):
        expected = plan['bindings'][guid]['native_row'];actual = observed.get(guid, {})
        differences = {key: {'expected': expected.get(key), 'actual': actual.get(key)} for key in PROTECTED_FIELDS if expected.get(key) != actual.get(key)}
        if differences:
            field_differences.append({'guid': guid, 'differences': differences})
    return {'schema': SCHEMA, 'case_id': plan['case_id'], 'ok': same_identity and same_tab and same_context and same_scope and not field_differences and selected == plan['after_guids'],
            'identity_matches': same_identity, 'tab_matches': same_tab, 'context_matches': same_context, 'scope_matches': same_scope, 'actual_selected_guids': selected,
            'invoice_fields_match': not field_differences, 'invoice_field_differences': field_differences,
            'invoice_fields_checked_guids': sorted(plan['bindings']),
            'expected_selected_guids': plan['after_guids'], 'missing_guids': sorted(set(plan['after_guids'])-set(selected)),
            'unexpected_guids': sorted(set(selected)-set(plan['after_guids'])), 'records': g['records'],
            'system_write': False, 'selection_write': False, 'draft_created': False, 'submitted': False}


def run_browser(source, request, output):
    output = Path(output)
    script = output.with_suffix('.js')
    if output.exists() or script.exists():
        raise ValueError('保留旧请求回执；输出已存在，不重放')
    script.parent.mkdir(parents=True, exist_ok=True);script.write_text(source)
    subprocess.run([sys.executable, str(ROOT/'tools/edge_runtime.py'), str(script), '--tab-id', request['tab_id'], '--output', str(output), '--quiet', '--timeout', '60'], check=False)
    if not output.exists():
        raise RuntimeError('桥接未留下结果；不得重发写请求')
    return json.loads(output.read_text())


def load(path):
    return json.loads(Path(path).read_text())


def write(path, data):
    path = Path(path)
    if path.exists():
        raise ValueError('不覆盖既有选票/读回回执')
    path.parent.mkdir(parents=True, exist_ok=True);path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command', nargs='?', default='preview', choices=['preview', 'apply', 'readback'])
    ap.add_argument('--request', type=Path);ap.add_argument('--case-manifest', type=Path);ap.add_argument('--audit-receipts', type=Path, nargs='+')
    ap.add_argument('--snapshot', type=Path);ap.add_argument('--preview', type=Path);ap.add_argument('--preview-sha256')
    ap.add_argument('--execute', action='store_true');ap.add_argument('--request-id');ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    if args.output.exists():
        raise ValueError('回执存在；只恢复原请求，不能重发')
    if args.command == 'preview':
        if not args.request or not args.case_manifest or not args.audit_receipts or args.execute:
            ap.error('preview需要request/case-manifest/audit-receipts，不能带execute')
        request = validate_request(load(args.request))
        snapshot = args.snapshot or args.output.with_name(args.output.stem+'-snapshot.json')
        receipt = load(snapshot) if args.snapshot else run_browser(snapshot_script(request), request, snapshot)
        plan = preview(request, load(args.case_manifest), receipt, [load(p) for p in args.audit_receipts])
        plan['evidence'] = {name: {'path': str(path.resolve()), 'sha256': sha(path)} for name, path in [('request', args.request), ('case_manifest', args.case_manifest), ('snapshot', snapshot)]}
        plan['evidence']['audit_receipts'] = [{'path': str(p.resolve()), 'sha256': sha(p)} for p in args.audit_receipts]
        plan['prepared_at_utc'] = datetime.now(timezone.utc).isoformat();write(args.output, plan)
        print(json.dumps({'apply_ready': plan['apply_ready'], 'added': plan['added_guids'], 'removed': plan['removed_guids'], 'executed': False, 'output': str(args.output)}, ensure_ascii=False))
        return
    if not args.preview or not args.preview_sha256 or sha(args.preview) != args.preview_sha256:
        ap.error('需要完整preview与准确preview-sha256')
    plan = load(args.preview)
    if args.command == 'apply' and plan.get('apply_ready') is not True:
        raise ValueError(plan.get('unsupported_reason') or '预览未放行')
    request = {**plan['request'], 'expected_before_guids': plan['before_guids']}
    if args.command == 'readback':
        if plan.get('after_guids') is None:
            raise ValueError('候选预览没有已确认的预期已选集；先补真实选择状态重新preview')
        # 恢复时原材料/审核清单可能已更新；只读核对仍以批准preview的SHA
        # 和已存身份/预期GUID为依据，不因旧审核文件更新而阻断状态查看。
        receipt = load(args.snapshot) if args.snapshot else run_browser(snapshot_script(request), request, args.output.with_name(args.output.stem+'-snapshot.json'))
        write(args.output, readback(plan, receipt));return
    for name in ['request', 'case_manifest', 'snapshot']:
        ref = plan['evidence'][name]
        if sha(ref['path']) != ref['sha256']:
            raise ValueError('预览证据改变：'+name)
    audit_refs = plan['evidence']['audit_receipts']
    if any(sha(r['path']) != r['sha256'] for r in audit_refs):
        raise ValueError('票面审核来源改变，重做preview')
    if not args.execute or not args.request_id:
        ap.error('apply必须明确execute与稳定request-id；不从工具开发推定具体选票授权')
    fresh_path = args.output.with_name(args.output.stem+'-before.json')
    fresh = run_browser(snapshot_script(request), request, fresh_path)
    current = preview(request, load(plan['evidence']['case_manifest']['path']), fresh, [load(r['path']) for r in audit_refs])
    for key in ['identity', 'current_page', 'serial_context', 'node_context', 'scope', 'before_guids', 'target_guids', 'after_guids', 'bindings']:
        if current[key] != plan[key]:
            raise ValueError('执行前独立读回与批准预览改变：'+key)
    native_path = args.output.with_name(args.output.stem+'-native.json')
    native = run_browser(apply_script(current, args.request_id), request, native_path)
    if native.get('result', {}).get('ok') is not True:
        unknown = native.get('result', {}).get('execution_status') == 'unknown_verify_before_retry'
        failed = {'ok': False, 'case_id': plan['case_id'], 'execution_status': 'transport_unknown_recover_original_marker' if unknown else 'native_completed_error', 'native_receipt': str(native_path), 'preview_sha256': args.preview_sha256, 'no_replay': True}
        if not unknown:
            after_path = args.output.with_name(args.output.stem+'-after-error.json')
            after = run_browser(snapshot_script(request), request, after_path)
            failed['readback_receipt'] = str(after_path)
            try:
                failed['readback'] = readback(plan, after)
            except ValueError as exc:
                failed['readback'] = {'ok': False, 'readback_status': 'unknown', 'error': str(exc)}
        write(args.output, failed);return
    after_path = args.output.with_name(args.output.stem+'-after.json')
    after = run_browser(snapshot_script(request), request, after_path)
    try:
        verified = readback(plan, after)
    except ValueError as exc:
        verified = {'ok': False, 'readback_status': 'unknown', 'error': str(exc)}
    write(args.output, {'case_id': plan['case_id'], 'preview_sha256': args.preview_sha256, 'native_receipt': str(native_path), 'readback_receipt': str(after_path), 'native': native['result'], 'readback': verified, 'ok': verified.get('ok') is True, 'no_replay': True})
    print(json.dumps({'ok': verified.get('ok'), 'selection_write': True, 'draft_created': False, 'submitted': False, 'output': str(args.output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
