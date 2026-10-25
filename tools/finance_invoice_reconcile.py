#!/usr/bin/env python3
"""按原件与完整53/113回执生成只读逐票报告；认证、合规、预约和付款分别留证。"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from finance_surface import display_text, parse_recognition_error

NUMBER = re.compile(r'[0-9]{20}')
SHA = re.compile(r'[a-f0-9]{64}')
FILE_CODE = re.compile(r'[a-fA-F0-9]{32}')
PLANE_BINDING_FIELDS = ('tk_type', 'tk_code_abst', 'tk_display_abst', 'tk_total_amt', 'tk_date', 'tk_buysno')


def read_json(path):
    path = Path(path).resolve()
    raw = path.read_bytes()
    return json.loads(raw), {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}


def unwrap(receipt):
    body = receipt
    for _ in range(4):
        if not isinstance(body, dict):
            break
        if 'grids' in body or 'uploadTracking' in body or 'upload_tracking' in body:
            return body
        if not isinstance(body.get('result'), dict):
            return body
        body = body['result']
    raise ValueError('回执没有可读取的原生结果对象')


def field(row, name):
    return row.get('t.' + name, row.get(name))


def grid_scope(receipt, number, expected_userid):
    body = unwrap(receipt)
    if body.get('identity', {}).get('userid') != expected_userid:
        raise ValueError('财务回执身份不同或缺失，不能混入本案范围')
    if body.get('projid') != 'WF_YBX':
        raise ValueError('回执不是WF_YBX财务项目')
    grids = [g for g in body.get('grids', []) if str(g.get('funcno')) == number]
    if len(grids) != 1:
        raise ValueError('须明确且唯一的' + number + '原生表回执')
    grid = grids[0]
    rows = grid.get('rows')
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ValueError('原生行须为对象数组')
    reasons = []
    checks = {
        'receipt_ok': body.get('ok') is True,
        'scope_complete': body.get('scopeComplete') is True,
        'core_capture_complete': body.get('serialization', {}).get('coreCaptureComplete') is True,
        'view_restored': body.get('pagination', {}).get('viewRestored') is True,
        'grid_complete': grid.get('allRecordsVisible') is True,
        'not_loading': grid.get('loading') is False,
        'consistent_count': type(grid.get('records')) is int and type(grid.get('rowCount')) is int and grid.get('records') == len(rows) == grid.get('rowCount'),
        'bounded_count': type(grid.get('records')) is int and 0 <= grid.get('records') <= 5000,
    }
    reasons.extend(key for key, passed in checks.items() if not passed)
    return body, grid, {'grid': number, 'complete': not reasons, 'checks': checks,
        'incomplete_reasons': reasons, 'identity': body['identity'], 'observed_at': body.get('observedAt'),
        'records': grid.get('records'), 'observed_rows': len(rows),
        'scope': '当前登录人、原回执查询条件下可见的指定表；不含其他账号或全校查重证明'}


def validate_sources(manifest, verify_bytes=False, allow_medical=False):
    from invoice_identity import source_identity
    if not isinstance(manifest.get('case_id'), str) or not manifest['case_id'].strip():
        raise ValueError('manifest须有明确case_id')
    userid = manifest.get('expected_userid')
    if not isinstance(userid, str) or not re.fullmatch(r'[A-Za-z0-9_.@-]{1,80}', userid):
        raise ValueError('manifest须有明确本案expected_userid')
    sources = manifest.get('original_files')
    if not isinstance(sources, list) or not sources:
        raise ValueError('manifest须有原件列表')
    if manifest.get('expected_count', len(sources)) != len(sources):
        raise ValueError('manifest声明票数与来源列表不同')
    numbers, shas = set(), set()
    for source in sources:
        candidates = source.get('invoice_number_candidates')
        identity = source_identity(source, allow_medical=allow_medical)
        sha = source.get('sha256')
        if not isinstance(sha, str) or not SHA.fullmatch(sha) or not isinstance(source.get('path'), str):
            raise ValueError('原件须有路径与完整SHA256')
        if identity in numbers or sha in shas:
            raise ValueError('manifest票号或原件SHA重复，不能逐票唯一关联')
        numbers.add(identity); shas.add(sha)
        if verify_bytes and hashlib.sha256(Path(source['path']).read_bytes()).hexdigest() != sha:
            raise ValueError('原件字节SHA变化：' + source['path'])
    return sources


def person_mapping(manifest, mapping, verify_evidence=False):
    if not mapping:
        return {}
    if mapping.get('case_id') != manifest['case_id'] or not isinstance(mapping.get('entries'), list):
        raise ValueError('人员展示映射须为同案case_id与entries，不接受全局姓名猜测')
    result = {}
    for entry in mapping['entries']:
        if not all(isinstance(entry.get(k), str) and entry[k].strip() for k in ['from', 'to', 'basis']):
            raise ValueError('姓名映射须有from/to/basis')
        if entry['from'] in result:
            raise ValueError('人员展示映射的from重复')
        if not isinstance(entry.get('evidence'), list) or not entry['evidence']:
            raise ValueError('姓名映射须有补证路径及SHA')
        for evidence in entry['evidence']:
            if not isinstance(evidence.get('path'), str) or not isinstance(evidence.get('sha256'), str) or not SHA.fullmatch(evidence['sha256']):
                raise ValueError('人员补证须有真实路径及完整SHA256')
            if verify_evidence and hashlib.sha256(Path(evidence['path']).read_bytes()).hexdigest() != evidence['sha256']:
                raise ValueError('人员补证SHA变化')
        if 'expected_count' in entry and sum(s.get('person') == entry['from'] for s in manifest['original_files']) != entry['expected_count']:
            raise ValueError('姓名映射预期数量与本案来源不同')
        result[entry['from']] = entry
    return result


def invoice_number(row):
    """只读实际票号列；优先复合票号，NaN/整数/Unicode数码不修造。"""
    def candidates(value):
        if not isinstance(value, str):
            return []
        return re.findall(r'(?<![0-9])[0-9]{20}(?![0-9])', display_text(value))
    preferred = candidates(field(row, 'tk_code_abst'))
    fallback = candidates(field(row, 'tk_no'))
    if len(set(preferred)) > 1 or len(set(fallback)) > 1:
        return None, '票号列有多个不同完整号码'
    if preferred:
        if fallback and fallback[0] != preferred[0]:
            return None, '复合票号与原生票号列冲突'
        return preferred[0], None
    if fallback:
        return fallback[0], None
    return None, '实际票号列无可用20位ASCII字符串号码'


def native_pool_bindings(manifest, pool_receipt, pool, pool_scope, bindings, pool_reference):
    """显式补证缺号航空行；精确冻结原生回执，不伪造服务器文件关联。"""
    if bindings is None:
        return {}
    if not isinstance(bindings, dict) or bindings.get('case_id') != manifest['case_id'] or bindings.get('expected_userid') != manifest['expected_userid']:
        raise ValueError('原生航空补证必须绑定同案和同一登录人')
    if not pool_scope['complete']:
        raise ValueError('原生航空补证只接受完整票池回执')
    if not isinstance(pool_reference, dict) or not isinstance(pool_reference.get('path'), str):
        raise ValueError('原生航空补证须提供票池回执原文件路径与SHA')
    actual_pool, actual_reference = read_json(pool_reference['path'])
    if (actual_pool != pool_receipt or pool_reference.get('sha256') != actual_reference['sha256']
            or bindings.get('pool_receipt_sha256') != actual_reference['sha256']):
        raise ValueError('原生航空补证票池回执内容或SHA变化，须重新补证')
    entries = bindings.get('entries')
    if not isinstance(entries, list) or not entries:
        raise ValueError('原生航空补证须有非空entries')
    sources = {s['invoice_number_candidates'][0]: s for s in manifest['original_files']}
    used_numbers, used_shas, used_guids, result = set(), set(), set(), {}

    def amount(value):
        if isinstance(value, bool) or not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,2})?', str(value)):
            raise ValueError('航空补证金额须为明确有限的票面数值')
        try:
            return Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError('航空补证金额无效') from exc

    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('航空补证entry须为对象')
        number, sha, guid = (entry.get(k) for k in ('invoice_number', 'source_sha256', 'native_guid'))
        if number not in sources or sources[number]['sha256'] != sha:
            raise ValueError('航空补证票号或来源SHA不属于本manifest')
        if not isinstance(guid, str) or not guid.strip() or number in used_numbers or sha in used_shas or guid in used_guids:
            raise ValueError('航空补证票号、来源SHA或GUID重复/缺失')
        source = sources[number]
        if hashlib.sha256(Path(source['path']).read_bytes()).hexdigest() != sha:
            raise ValueError('航空补证原件SHA变化')
        expected = entry.get('expected_native_fields')
        if not isinstance(expected, dict) or set(expected) != set(PLANE_BINDING_FIELDS) or expected.get('tk_type') != 'PLANE':
            raise ValueError('航空补证须明确PLANE及全部六项原生字段')
        rows = [(index, row) for index, row in enumerate(pool['rows']) if field(row, 'tk_guid') == guid]
        if len(rows) != 1:
            raise ValueError('航空补证GUID未唯一匹配票池行')
        index, row = rows[0]
        if any(field(row, key) != expected[key] or type(field(row, key)) is not type(expected[key]) for key in PLANE_BINDING_FIELDS):
            raise ValueError('航空补证原生字段与冻结值不一致')
        matching_rows = [r for r in pool['rows'] if all(field(r, key) == expected[key] and type(field(r, key)) is type(expected[key]) for key in PLANE_BINDING_FIELDS)]
        if len(matching_rows) != 1:
            raise ValueError('航空补证同字段存在多个原生候选')
        parsed, error = invoice_number(row)
        has_other_number = any(isinstance(field(row, key), str) and re.search(r'(?<![0-9])[0-9]{8,20}(?![0-9])', display_text(field(row, key)))
                               for key in ('tk_code_abst', 'tk_no'))
        if parsed is not None or error != '实际票号列无可用20位ASCII字符串号码' or has_other_number:
            raise ValueError('航空补证只适用缺票号行，原生已有号码或号码冲突不能覆盖')
        if any(invoice_number(r)[0] == number for r in pool['rows']):
            raise ValueError('航空补证票号另有原生票池记录，不能强行绑定')
        native_person = re.split(r'[;；]', display_text(expected['tk_display_abst']), maxsplit=1)[0].strip()
        if not source.get('person') or native_person != source['person']:
            raise ValueError('航空补证原生旅客姓名与原件manifest不一致')
        if not isinstance(source.get('issue_date'), str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', source['issue_date']) or expected['tk_date'] != source['issue_date']:
            raise ValueError('航空补证原生开票日与原件manifest不一致')
        if amount(expected['tk_total_amt']) != amount(source.get('total_amount')):
            raise ValueError('航空补证原生金额与原件manifest不一致')
        candidates = [s for s in sources.values() if s.get('person') == native_person and s.get('issue_date') == source['issue_date']
                      and s.get('total_amount') is not None and amount(s['total_amount']) == amount(expected['tk_total_amt'])]
        if len(candidates) != 1:
            raise ValueError('航空补证同姓名金额开票日存在多个来源候选')
        if not isinstance(entry.get('basis'), str) or not entry['basis'].strip() or not isinstance(entry.get('evidence'), list) or not entry['evidence']:
            raise ValueError('航空补证须有对应依据和非空证据路径/SHA')
        for evidence in entry['evidence']:
            if not isinstance(evidence, dict) or not isinstance(evidence.get('path'), str) or not Path(evidence['path']).is_absolute() or not isinstance(evidence.get('sha256'), str) or not SHA.fullmatch(evidence['sha256']):
                raise ValueError('航空补证证据须为绝对路径及完整SHA')
            if hashlib.sha256(Path(evidence['path']).read_bytes()).hexdigest() != evidence['sha256']:
                raise ValueError('航空补证证据SHA变化')
        used_numbers.add(number); used_shas.add(sha); used_guids.add(guid)
        result[index] = {'invoice_number': number, 'binding_type': 'explicit_native_binding',
            'binding': entry, 'pool_receipt': actual_reference, 'native_invoice_number_issue': error,
            'binding_scope': '按票面原件及原生旅客/金额/开票日显式对应；不是服务器filecode绑定，原生缺号问题仍保留',
            'native_buysno_scope': '航空行原生tk_buysno按原值保存；可能为学工号，不能当作票面购方税号审核结果'}
    return result


def upload_bindings(manifest, uploads):
    """复用真实tracking的files.name/sha256及原生onUploadComplete响应，不搜任意文本。"""
    known = {s['sha256'] for s in manifest['original_files']}
    code_to_sources, name_to_sources = defaultdict(set), defaultdict(set)
    evidence, warnings = [], []
    for receipt_index, receipt in enumerate(uploads):
        body = unwrap(receipt)
        tracking = body.get('uploadTracking', body.get('upload_tracking'))
        if not isinstance(tracking, dict):
            warnings.append('上传回执无原生tracking，仅保留输入来源，未猜文件编号'); continue
        if tracking.get('caseId') != manifest['case_id']:
            raise ValueError('上传tracking不是同案')
        identity = body.get('identity', {}).get('userid') or tracking.get('expectedUserid')
        if identity and identity != manifest['expected_userid']:
            raise ValueError('上传tracking登录身份不同')
        file_shas = {}
        for item in tracking.get('files', []):
            name, sha = item.get('name'), item.get('sha256')
            if not isinstance(name, str) or not isinstance(sha, str) or not SHA.fullmatch(sha):
                raise ValueError('上传tracking文件绑定缺失或格式异常')
            if sha not in known:
                raise ValueError('上传tracking含非本manifest原件SHA')
            if name in file_shas and file_shas[name] != sha:
                raise ValueError('同一tracking文件名对应不同SHA')
            file_shas[name] = sha
        for item in tracking.get('receipts', []):
            name, response = item.get('name'), item.get('response')
            if name not in file_shas or not isinstance(response, str) or not FILE_CODE.fullmatch(response.strip()):
                warnings.append('上传响应未形成明确文件名/SHA/32位原生文件编号绑定'); continue
            sha, code = file_shas[name], response.strip().lower()
            code_to_sources[code].add(sha); name_to_sources[name].add(sha)
            evidence.append({'upload_receipt_index': receipt_index, 'source_sha256': sha, 'filename': name, 'file_code': code,
                'response': response, 'tracking_identity_reported': identity or None})
    if any(len(shas) > 1 for shas in code_to_sources.values()):
        raise ValueError('原生文件编号映射到多个源SHA，不能唯一关联')
    return code_to_sources, name_to_sources, evidence, warnings


def recognition_sources(row, known, code_map, name_map):
    associations = []
    filename = field(row, 'show_filename')
    ref = field(row, 'tk_filecode')
    names = [filename] if isinstance(filename, str) else []
    codes = [field(row, 'filesequcode'), *parse_recognition_error(field(row, 'show_err_msg'))['file_codes']]
    if isinstance(ref, str) and len(ref.split('&')) == 4:
        parts = ref.split('&'); codes.append(parts[0]); names.append(parts[1])
    for name in names:
        match = re.fullmatch(r'([a-fA-F0-9]{64})\.[A-Za-z0-9]+', name)
        if match and match.group(1).lower() in known:
            associations.append((match.group(1).lower(), 'sha_filename'))
        for sha in name_map.get(name, []):
            associations.append((sha, 'upload_receipt_filename_sha'))
    for code in codes:
        if isinstance(code, str):
            for sha in code_map.get(code.lower(), []):
                associations.append((sha, 'upload_receipt_filecode_sha'))
    shas = {sha for sha, _ in associations}
    return shas, [{'source_sha256': sha, 'basis': basis} for sha, basis in associations]


def verification_state(pool_matches, recognition_matches, capture_complete, binding_complete):
    """结果层独立于来源覆盖与票面审核；原生记录冲突不选一个版本当结果。"""
    reasons = []
    if len(pool_matches) > 1:
        reasons.append('同源票号匹配多条发票池记录')
    if len(recognition_matches) > 1:
        reasons.append('同一源SHA匹配多条识别记录')
    if pool_matches and recognition_matches:
        reasons.append('同一来源同时出现在池与识别明细，两份回执可能处于不同转换时点')
    if reasons:
        return 'conflict', False, reasons
    if pool_matches:
        row = pool_matches[0]['row']
        flag = display_text(field(row, 'tk_mod_flag'))
        description = display_text(field(row, 'auth_rst_desc')).replace('（', '(').replace('）', ')')
        if flag == 'NORMAL' and description == '正常(已认证)':
            return 'normal_certified', True, ['原生NORMAL且认证说明为正常(已认证)，不覆盖独立票面问题']
        if flag in {'ABNORMAL', 'ERROR', 'FAILED', 'INVALID', 'VOID', 'EXCEPTION'} or re.search(r'异常|错误|失败|作废|红冲|无效', description):
            return 'abnormal_pool', True, ['原生池状态有明确异常标记或异常说明；原始flag/说明保留，不推断真伪、已报或付款']
        return 'unknown', False, ['池记录存在，但缺少一致明确的正常认证或异常结果']
    if recognition_matches:
        item = recognition_matches[0]
        error = item['recognition_error']
        if error['error_type'] == 'duplicate_reserved_invoice':
            return 'recognition_reserved', True, ['完整识别错误报告预约占用，是已知阻碍，不证明已报或付款']
        if error['display_text']:
            return 'recognition_error', True, ['原生识别明细有完整错误，未自动重传或重识别']
        status = display_text(field(item['row'], 'show_status'))
        if status:
            return 'recognition_pending', False, ['无错误识别记录尚无池结果，原生状态：' + status]
        return 'unknown', False, ['识别记录存在但状态和错误均空，不能判断处理结果']
    if capture_complete and binding_complete:
        return 'not_observed', False, ['在两份完整可关联范围未观察到，不表示临时文件没上传或其他账号未用']
    return 'unknown', False, ['原生范围或来源关联不完整，不能用未见证明没有记录']


def reconcile(manifest, pool_receipt, recognition_receipt, mapping=None, upload_receipts=(), verify_bytes=False,
              native_bindings=None, pool_reference=None):
    sources = validate_sources(manifest, verify_bytes)
    mapping = person_mapping(manifest, mapping, verify_bytes)
    pool_body, pool, pool_scope = grid_scope(pool_receipt, '53', manifest['expected_userid'])
    recognition_body, recognition, recognition_scope = grid_scope(recognition_receipt, '113', manifest['expected_userid'])
    explicit_bindings = native_pool_bindings(manifest, pool_receipt, pool, pool_scope, native_bindings, pool_reference)
    code_map, name_map, upload_evidence, upload_warnings = upload_bindings(manifest, upload_receipts)
    by_number = {s['invoice_number_candidates'][0]: s for s in sources}
    by_sha = {s['sha256']: s for s in sources}
    pool_matches, recognition_matches = defaultdict(list), defaultdict(list)
    unresolved_pool, unresolved_recognition, foreign_pool = [], [], []
    for index, row in enumerate(pool['rows']):
        number, error = invoice_number(row)
        item = {'row_index': index, 'native_guid': field(row, 'tk_guid'), 'row': row}
        if index in explicit_bindings:
            bound = explicit_bindings[index]
            pool_matches[bound['invoice_number']].append({**item, **bound})
        elif error:
            unresolved_pool.append({**item, 'reason': error})
        elif number in by_number:
            pool_matches[number].append(item)
        else:
            foreign_pool.append({**item, 'invoice_number': number})
    for index, row in enumerate(recognition['rows']):
        shas, basis = recognition_sources(row, by_sha, code_map, name_map)
        item = {'row_index': index, 'match_evidence': basis, 'row': row,
            'recognition_error': parse_recognition_error(field(row, 'show_err_msg'))}
        if len(shas) != 1:
            unresolved_recognition.append({**item, 'reason': '源SHA关联冲突' if shas else '未有可验证的源SHA文件名或上传响应文件编号绑定'})
        else:
            recognition_matches[next(iter(shas))].append(item)
    duplicate_numbers = [number for number, matches in pool_matches.items() if len(matches) > 1]
    conflicting_sources = [s['invoice_number_candidates'][0] for s in sources
        if len(pool_matches[s['invoice_number_candidates'][0]]) > 1 or len(recognition_matches[s['sha256']]) > 1
        or pool_matches[s['invoice_number_candidates'][0]] and recognition_matches[s['sha256']]]
    capture_complete = pool_scope['complete'] and recognition_scope['complete']
    binding_complete = not unresolved_pool and not unresolved_recognition and not conflicting_sources
    complete = capture_complete and binding_complete
    invoices = []
    version_groups = defaultdict(list)
    for source in sources:
        number, sha = source['invoice_number_candidates'][0], source['sha256']
        pm, rm = pool_matches[number], recognition_matches[sha]
        display_mapping = mapping.get(source.get('person'))
        buyer_passed = source.get('buyer_fields_passed')
        buyer = {'buyer_title': source.get('buyer_title'), 'buyer_tax_id': source.get('buyer_tax_id'),
            'status': 'reported_fields_match' if buyer_passed is True else 'reported_buyer_problem' if buyer_passed is False else 'unknown',
            'independent_of_school_certification': True}
        found = bool(pm or rm)
        visibility = 'matched_observed_rows' if found else 'not_observed_in_complete_views' if complete else 'unknown_in_incomplete_or_unresolved_views'
        reservations = [r for item in rm for r in item['recognition_error']['reservations']]
        reserved = any(item['recognition_error']['error_type'] == 'duplicate_reserved_invoice' for item in rm)
        state, terminal, reasons = verification_state(pm, rm, capture_complete, binding_complete)
        invoice = {'invoice_number': number, 'source_sha256': sha, 'source_path': source['path'],
            'source_chain': source.get('source_chain'), 'source_kind': source.get('source_kind'), 'source_page': source.get('source_page'),
            'person_source_label': source.get('person'), 'person_display': display_mapping['to'] if display_mapping else source.get('person'),
            'person_mapping': display_mapping, 'issue_date': source.get('issue_date'), 'original_total_amount_display_only': source.get('total_amount'),
            'version_group': source.get('version_group'), 'version_role': source.get('version_role'), 'paired_invoice_number': source.get('paired_invoice_number'),
            'same_expense_rule': '同费用版本仅核验，不双计；未据此核定报销费用' if source.get('version_group') else '费用组仍按本案事实核定，不凭票数累计',
            'buyer_audit': buyer, 'visibility_status': visibility, 'pool_match_ambiguous': len(pm) > 1,
            'verification_state': state, 'verification_terminal': terminal,
            'verification_reasons': reasons, 'source_accounted_unambiguously': bool(pm or rm) and state != 'conflict',
            'pool_matches': pm, 'recognition_matches': rm,
            'school_statuses': [{'reported_description': field(item['row'], 'auth_rst_desc'),
                'reported_flag': field(item['row'], 'tk_mod_flag'), 'reported_auth_date': field(item['row'], 'auth_date'),
                'native_guid': field(item['row'], 'tk_guid'), 'image_number': field(item['row'], 'show_id'),
                'reported_bound_serial': field(item['row'], 'serial_no'), 'reported_selected_flag': field(item['row'], 'PC_Has_chosen'),
                'buyer_tax_column_reported': field(item['row'], 'tk_buysno')} for item in pm],
            'recognition_statuses': [field(item['row'], 'show_status') for item in rm],
            'reservation_status': 'reported_reserved' if reserved else 'unknown', 'reservations': reservations,
            'full_recognition_errors': [item['recognition_error']['full_message'] for item in rm if item['recognition_error']['full_message']],
            'upload_transport_status': 'not_inferred_from_pool_or_recognition',
            'save_action_status': 'not_inferred_from_pool_or_recognition',
            'red_void_status': 'unknown', 'reimbursement_status': 'unknown', 'payment_status': 'unknown',
            'selected_by_this_tool': False, 'submitted_by_this_tool': False}
        invoices.append(invoice)
        if source.get('version_group'):
            version_groups[source['version_group']].append({'invoice_number': number, 'source_sha256': sha,
                'version_role': source.get('version_role'), 'paired_invoice_number': source.get('paired_invoice_number')})
    all_sources_accounted = complete and all(i['source_accounted_unambiguously'] for i in invoices)
    verification_complete = all_sources_accounted and all(i['verification_terminal'] for i in invoices)
    state_counts = Counter(i['verification_state'] for i in invoices)
    report = {'schema': 'ustc-finance-invoice-reconcile-v1', 'case_id': manifest['case_id'],
        'generated_at_utc': datetime.now(timezone.utc).isoformat(), 'expected_userid': manifest['expected_userid'],
        'complete': complete, 'complete_meaning': '两份明确原生查询回执完整且关联无缺项；不表示全部来源票据已上传、认证或可报',
        'capture_complete': capture_complete, 'binding_complete': binding_complete, 'source_bytes_rechecked': verify_bytes,
        'all_sources_accounted': all_sources_accounted,
        'all_sources_accounted_meaning': '两份原生范围与关联完整，全部来源票据均有唯一且无冲突的可见记录，不等于认证成功',
        'verification_complete': verification_complete,
        'verification_complete_meaning': '全部来源唯一定位且达到正常认证、异常池或明确识别错误/预约等可报告结果；不是全部可报、已报或已付款',
        'system_write': False, 'selection_write': False, 'submission': False, 'application_amount_calculated': False,
        'query_scopes': [pool_scope, recognition_scope], 'upload_binding_evidence': upload_evidence, 'upload_binding_warnings': upload_warnings,
        'explicit_native_pool_bindings': list(explicit_bindings.values()),
        'unresolved_pool_rows': unresolved_pool, 'unresolved_recognition_rows': unresolved_recognition,
        'foreign_pool_rows': foreign_pool, 'duplicate_pool_invoice_numbers': duplicate_numbers,
        'conflicting_source_invoice_numbers': conflicting_sources,
        'person_display_mappings': list(mapping.values()),
        'expense_version_groups': [{'group': key, 'invoice_versions': items, 'count_towards_requested_amount': False} for key, items in version_groups.items()],
        'summary': {'source_invoice_versions': len(invoices), 'source_sha_unique': len(by_sha),
            'buyer_fields_match': sum(i['buyer_audit']['status'] == 'reported_fields_match' for i in invoices),
            'buyer_problem': sum(i['buyer_audit']['status'] == 'reported_buyer_problem' for i in invoices),
            'pool_matched_invoice_versions': sum(bool(i['pool_matches']) for i in invoices),
            'reported_school_certified_invoice_versions': sum(any('已认证' in str(s.get('reported_description') or '') for s in i['school_statuses']) for i in invoices),
            'recognition_matched_invoice_versions': sum(bool(i['recognition_matches']) for i in invoices),
            'reported_reserved_invoice_versions': sum(i['reservation_status'] == 'reported_reserved' for i in invoices),
            'not_observed_invoice_versions': sum(i['visibility_status'] == 'not_observed_in_complete_views' for i in invoices),
            'unknown_visibility_invoice_versions': sum(i['visibility_status'] == 'unknown_in_incomplete_or_unresolved_views' for i in invoices),
            'normal_certified_invoice_versions': state_counts['normal_certified'],
            'abnormal_pool_invoice_versions': state_counts['abnormal_pool'],
            'recognition_error_invoice_versions': state_counts['recognition_reserved'] + state_counts['recognition_error'],
            'recognition_reserved_state_invoice_versions': state_counts['recognition_reserved'],
            'recognition_other_error_invoice_versions': state_counts['recognition_error'],
            'pending_recognition_invoice_versions': state_counts['recognition_pending'],
            'not_observed_state_invoice_versions': state_counts['not_observed'],
            'unknown_state_invoice_versions': state_counts['unknown'],
            'conflicted_source_invoice_versions': state_counts['conflict'],
            'accounted_unambiguously_invoice_versions': sum(i['source_accounted_unambiguously'] for i in invoices),
            'verification_terminal_invoice_versions': sum(i['verification_terminal'] for i in invoices),
            'person_display_counts': dict(Counter(i['person_display'] for i in invoices)), 'expense_version_group_count': len(version_groups)},
        'invoices': invoices}
    return report


CSV_COLUMNS = ['invoice_number', 'source_sha256', 'person_display', 'person_source_label', 'source_path',
    'source_chain', 'version_group', 'version_role', 'paired_invoice_number', 'original_total_amount_display_only',
    'buyer_audit_status', 'buyer_title', 'buyer_tax_id', 'visibility_status', 'pool_match_ambiguous',
    'verification_state', 'verification_terminal', 'verification_reasons', 'source_accounted_unambiguously',
    'school_statuses', 'recognition_statuses', 'reservation_status', 'reservations', 'full_recognition_errors',
    'red_void_status', 'reimbursement_status', 'payment_status', 'report_id']


def write_reports(report, prefix):
    prefix = Path(prefix).resolve()
    paths = {extension: Path(str(prefix) + '.' + extension) for extension in ['json', 'csv', 'md']}
    if any(path.exists() for path in paths.values()):
        raise ValueError('不覆盖旧汇总，换新output-prefix')
    prefix.parent.mkdir(parents=True, exist_ok=True)
    report = {**report, 'report_id': hashlib.sha256(json.dumps(report, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
    with paths['json'].open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    with paths['csv'].open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS); writer.writeheader()
        for invoice in report['invoices']:
            row = {key: invoice.get(key) for key in CSV_COLUMNS}
            row.update(buyer_audit_status=invoice['buyer_audit']['status'], buyer_title=invoice['buyer_audit']['buyer_title'],
                buyer_tax_id=invoice['buyer_audit']['buyer_tax_id'], report_id=report['report_id'])
            for key, value in row.items():
                if isinstance(value, (list, dict)):
                    row[key] = json.dumps(value, ensure_ascii=False)
            writer.writerow(row)
    def cell(value):
        return display_text(value).replace('\\', '\\\\').replace('|', '\\|').replace('\n', '<br>')
    s = report['summary']
    lines = ['# 发票原件与财务回执逐票汇总\n\n',
        f"案件：{report['case_id']}；范围：当前登录人{report['expected_userid']}。报告编号：`{report['report_id']}`。\n\n",
        f"完整读取与关联：{'是' if report['complete'] else '否'}；全部来源都有无冲突记录：{'是' if report['all_sources_accounted'] else '否'}；全部查验都有明确结果：{'是' if report['verification_complete'] else '否'}。来源票据版本{s['source_invoice_versions']}张，票面名称税号匹配{s['buyer_fields_match']}张、购方问题{s['buyer_problem']}张。\n\n",
        f"处理结果分别为：正常认证{s['normal_certified_invoice_versions']}张、异常池票{s['abnormal_pool_invoice_versions']}张、有错误识别{s['recognition_error_invoice_versions']}张（其中预约占用{s['recognition_reserved_state_invoice_versions']}张）、无错误待识别{s['pending_recognition_invoice_versions']}张、未观察到{s['not_observed_state_invoice_versions']}张、不明{s['unknown_state_invoice_versions']}张、关联冲突{s['conflicted_source_invoice_versions']}张。\n\n",
        'complete只描述输入回执与绑定范围。全部来源都有记录仍可能有待识别；查验有明确结果包括已知异常或预约阻碍，不能称全部通过或可报。一个来源同时出现于池和识别明细、多条池记录或多条识别记录均为冲突，不算完成。NORMAL不替代票面合规；预约不等于已报或付款；未见不证明临时文件没有上传、其他账号未用或发票无效。原票金额仅展示，不计算申请总额；同费用新旧版本不双计。\n\n',
        f"缺号航空行显式补证{len(report.get('explicit_native_pool_bindings', []))}张：仅按原件及冻结原生字段对应，保留系统缺票号问题；不是服务器filecode绑定，也未修改学校记录。完整补证和证据SHA在JSON中。\n\n",
        '| 票号 | 人员 | 原票金额仅展示 | 票面名称税号 | 明确处理状态 | 当前认证列 | 识别状态 | 预约及完整错误 | 可见性 | 源SHA |\n',
        '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n']
    for invoice in report['invoices']:
        errors = '\n'.join(invoice['full_recognition_errors'])
        states = '; '.join(str(x.get('reported_description') or x.get('reported_flag') or '') for x in invoice['school_statuses'])
        audit = {'reported_fields_match': '名称税号匹配', 'reported_buyer_problem': '购方字段问题', 'unknown': '未知'}[invoice['buyer_audit']['status']]
        visibility = {'matched_observed_rows': '已匹配原生记录', 'not_observed_in_complete_views': '完整范围未观察到',
                      'unknown_in_incomplete_or_unresolved_views': '不完整/关联缺项，未见结论不明'}[invoice['visibility_status']]
        if any(item.get('binding_type') == 'explicit_native_binding' for item in invoice['pool_matches']):
            visibility += '（缺号航空显式补证）'
        processing = {'normal_certified': '正常认证', 'abnormal_pool': '异常池票', 'recognition_reserved': '识别错误：预约占用',
            'recognition_error': '有错误识别', 'recognition_pending': '无错误待识别/入池', 'not_observed': '未观察到',
            'unknown': '不明', 'conflict': '关联冲突'}[invoice['verification_state']]
        values = [invoice['invoice_number'], invoice['person_display'], invoice['original_total_amount_display_only'], audit, processing, states,
            '; '.join(str(x) for x in invoice['recognition_statuses']), errors, visibility, invoice['source_sha256']]
        lines.append('| ' + ' | '.join(cell(x) for x in values) + ' |\n')
    lines.append('\nJSON保留原生显示行、完整原文、关联依据、补证映射及全部输入路径/SHA；CSV保留全文错误。三份报告使用同一报告编号。\n')
    with paths['md'].open('x', encoding='utf-8') as stream:
        stream.write(''.join(lines))
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--pool', type=Path, required=True)
    parser.add_argument('--recognition', type=Path, required=True)
    parser.add_argument('--person-mapping', type=Path)
    parser.add_argument('--native-pool-bindings', type=Path, help='只为缺号PLANE行提供同案、原件SHA和完整票池SHA冻结的显式补证')
    parser.add_argument('--upload-receipts', type=Path, nargs='*', default=[])
    parser.add_argument('--output-prefix', type=Path, required=True)
    args = parser.parse_args()
    manifest, manifest_ref = read_json(args.manifest)
    pool, pool_ref = read_json(args.pool)
    recognition, recognition_ref = read_json(args.recognition)
    mapping, mapping_ref = read_json(args.person_mapping) if args.person_mapping else (None, None)
    bindings, bindings_ref = read_json(args.native_pool_bindings) if args.native_pool_bindings else (None, None)
    uploads, upload_refs = [], []
    for path in args.upload_receipts:
        data, reference = read_json(path); uploads.append(data); upload_refs.append(reference)
    report = reconcile(manifest, pool, recognition, mapping, uploads, verify_bytes=True,
        native_bindings=bindings, pool_reference=pool_ref)
    report['input_evidence'] = {'manifest': manifest_ref, 'pool': pool_ref, 'recognition': recognition_ref,
        'person_mapping': mapping_ref, 'upload_receipts': upload_refs, 'native_pool_bindings': bindings_ref}
    paths = write_reports(report, args.output_prefix)
    print(json.dumps({'complete': report['complete'], 'all_sources_accounted': report['all_sources_accounted'],
        'verification_complete': report['verification_complete'], 'summary': report['summary'],
        'outputs': {key: str(value) for key, value in paths.items()}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
