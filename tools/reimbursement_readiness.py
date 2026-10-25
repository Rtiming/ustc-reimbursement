#!/usr/bin/env python3
"""从已留档材料/财务分类和同案表单生成只读差项清单；不访问财务。"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

CLASSIFICATION_SCHEMA = 'legacy-final-material-finance-classification-v1'
SCHEMA = 'ustc-reimbursement-readiness-v1'
EVIDENCE_SCHEMAS = {'materials': 'ustc-material-workspace-v1',
                    'reissues': 'legacy-reissued-material-review-v1',
                    'finance': 'ustc-finance-invoice-reconcile-v1'}
BUCKETS = {'material_candidate_system_normal': '购方匹配且系统正常的材料候选',
           'reservation_blocked': '预约仍受阻', 'buyer_problem': '购方问题',
           'no_formal_invoice': '未见正式发票', 'pending_review': '版本、金额或系统状态待核'}
STABLE_FIELDS = ['material_owner', 'material_category', 'invoice_numbers',
                 'material_preferred_invoice_numbers', 'not_for_selection_invoice_numbers',
                 'material_amount_display_not_claim', 'material_flags']


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'JSON存在重复键：' + key)
        result[key] = value
    return result


def read_json(path):
    path = Path(path).resolve(strict=True)
    data = path.read_bytes()
    value = json.loads(data.decode('utf-8-sig'), parse_float=Decimal,
                       parse_constant=lambda x: (_ for _ in ()).throw(ValueError('非法JSON数值：' + x)),
                       object_pairs_hook=unique_pairs)
    require(isinstance(value, dict), '输入必须为JSON对象：' + str(path))
    return value, {'path': str(path), 'sha256': hashlib.sha256(data).hexdigest()}


def amount(value, label):
    """未知仅接受null/空串；精确到分，不静默舍入、不使用浮点累计。"""
    if value is None or value == '':
        return None
    require(not isinstance(value, bool) and isinstance(value, (str, int, Decimal)), label + '须为金额或null')
    if isinstance(value, str):
        require(bool(re.fullmatch(r'[0-9]+(?:\.[0-9]{1,2})?', value)), label + '不是合法非负金额')
    parsed = Decimal(value)
    require(parsed.is_finite() and 0 <= parsed <= Decimal('9999999999999999.99'), label + '金额超出支持范围')
    require(parsed == parsed.quantize(Decimal('.01')), label + '最多两位小数')
    return parsed


def money(value):
    return format(value, '.2f') if value is not None else None


def index_rows(rows, field, label):
    require(isinstance(rows, list), label + '必须为数组')
    result = {}
    for row in rows:
        require(isinstance(row, dict), label + '行必须为对象')
        key = row.get(field)
        require(isinstance(key, str) and bool(key.strip()) and key == key.strip(), label + '缺少合法' + field)
        require(key not in result, label + '重复' + field + '：' + key)
        result[key] = row
    return result


def numbers(value, label):
    require(isinstance(value, list) and all(isinstance(x, str) and re.fullmatch(r'[0-9]{20}', x) for x in value),
            label + '必须为20位票号字符串数组')
    require(len(value) == len(set(value)), label + '不能重复票号')
    return value


def timestamp(value, label):
    require(isinstance(value, str), label + '须有采集时间')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError(label + '时间格式不合法') from exc
    require(parsed.tzinfo is not None, label + '须带时区')
    return value


def unresolved_material_flag(text):
    """仅识别明确未解决文字；肯定性备注保留原文，不制造材料缺口。"""
    for clause in re.split(r'[；;。]', text):
        if re.search(r'未见|缺少|待确定|待补|待核|缺失|不完整|无法确认', clause):
            if re.match(r'\s*(已补齐|已解决|无需|不缺少)', clause) and not re.search(r'但|仍', clause):
                continue
            return True
    return False


def load_inputs(classification_path, form_path):
    report, report_ref = read_json(classification_path)
    form, form_ref = read_json(form_path)
    require(report.get('schema') == CLASSIFICATION_SCHEMA, '仅支持' + CLASSIFICATION_SCHEMA)
    case = report.get('case_id')
    require(isinstance(case, str) and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', case)), '须明确case_id')
    require(form.get('case_id') == case, 'form与classification跨案或缺少case_id')
    evidence = report.get('input_evidence')
    require(isinstance(evidence, dict) and set(evidence) == set(EVIDENCE_SCHEMAS), 'input_evidence须为materials/reissues/finance三份明确输入')
    sources, refs = {}, {}
    for kind, schema in EVIDENCE_SCHEMAS.items():
        ref = evidence[kind]
        require(isinstance(ref, dict) and isinstance(ref.get('path'), str) and ref['path'] and
                isinstance(ref.get('sha256'), str) and bool(re.fullmatch(r'[a-f0-9]{64}', ref['sha256'])),
                kind + '缺少文件路径及SHA256')
        path = Path(ref['path'])
        if not path.is_absolute():
            path = Path(report_ref['path']).parent / path
        source, actual = read_json(path)
        require(actual['sha256'] == ref['sha256'], kind + '来源SHA256变化')
        require(source.get('case_id') == case, kind + '输入跨案或缺少case_id')
        require(source.get('schema') == schema, kind + '输入版本暂不支持')
        sources[kind], refs[kind] = source, actual
    return report, form, sources, {'classification': report_ref, 'form': form_ref, **refs}


def form_gaps(form):
    total = amount(form.get('amount'), 'form.amount')
    basic = form.get('basic', {})
    require(isinstance(basic, dict) and isinstance(basic.get('fields', {}), dict), 'form.basic/fields须为对象')
    fields = basic.get('fields', {})
    configs, additional = {}, []
    for key in ['funding', 'expense_allocations', 'payments']:
        rows = form.get(key, [])
        require(isinstance(rows, list) and all(isinstance(x, dict) for x in rows), 'form.' + key + '须为对象数组')
        values = [amount(x.get('amount'), 'form.' + key + '.amount') for x in rows]
        subtotal = sum(values, Decimal(0)) if rows and all(v is not None for v in values) else None
        configs[key] = {'configured_count': len(rows), 'configured_amount': money(subtotal),
                        'unknown_amount_count': sum(v is None for v in values)}
        if subtotal is not None and total is not None and subtotal != total:
            additional.append({'item': key + '_amount', 'state': 'mismatch', 'detail': '表单配置金额与声明申请额不同，须核对'})
    checks = [
        ('scope_and_amount', total is not None and bool(form.get('expense_allocations')), '本批费用范围和申请额；票面金额不能直接替代申请额'),
        ('funding', bool(form.get('funding')), '本案经费卡与分配'),
        ('witness', bool(basic.get('teacher_id')) and bool(basic.get('teacher_name')), '本案证明人及工号'),
        ('contact', bool(fields.get('t-req_mobile') or fields.get('t-req_tele')), '本案联系人及联系方式'),
        ('payments', bool(form.get('payments')), '实际收款人、代付关系及打款分配；材料归属人不自动成为收款人')]
    gaps = [{'item': key, 'state': 'configured_unverified' if present else 'missing', 'detail': detail}
            for key, present, detail in checks]
    gaps.extend(additional)
    gaps.extend([
        {'item': 'formal_invoice_selection', 'state': 'independent_receipt_required', 'detail': '本工具没有正式选票回执，需按本批核定版本选票并独立回读'},
        {'item': 'draft_creation', 'state': 'configured_serial_unverified' if form.get('serial') else 'not_recorded_in_form',
         'detail': '本工具不建单；有配置单号也须核对当前真实报销单'}])
    return {'declared_application_amount': money(total), 'configured_serial': form.get('serial'),
            'configurations': configs, 'gaps': gaps, 'ready_to_file': False,
            'reason': '配置、正式选票与建单回执分别核定；本分类汇总不能证明可办理'}


def build_readiness(classification_path, form_path):
    report, form, sources, refs = load_inputs(classification_path, form_path)
    expenses = index_rows(report.get('expense_records'), 'expense_id', '分类费用')
    materials = index_rows(sources['materials'].get('expenses'), 'id', '材料费用')
    reissues = index_rows(sources['reissues'].get('expense_classification'), 'expense_id', '材料分类依据')
    finance = index_rows(sources['finance'].get('invoices'), 'invoice_number', '财务票版')
    require(set(expenses) == set(materials) == set(reissues), '费用组范围与来源不一致')
    states = report.get('invoice_version_states')
    require(isinstance(states, dict), '缺少invoice_version_states')
    generated = timestamp(report.get('generated_at_utc'), '分类生成')
    observed = report.get('current_finance_observed_at_utc')
    require(isinstance(observed, list) and bool(observed), '缺少财务采集时间')
    for value in observed: timestamp(value, '财务采集')
    scopes = sources['finance'].get('query_scopes')
    require(isinstance(scopes, list) and len(scopes) == 2 and all(isinstance(x, dict) for x in scopes) and
            {x.get('grid') for x in scopes} == {'53', '113'}, '财务范围须同时有53/113')
    require(set(observed) == {x.get('observed_at') for x in scopes}, '分类采集时间与财务回执不一致')
    scope_complete = all(x.get('complete') is True for x in scopes)
    seen_numbers, rows = set(), []
    for eid, record in expenses.items():
        origin, audited = materials[eid], reissues[eid]
        for field in STABLE_FIELDS:
            require(field in record and record[field] == audited.get(field), eid + '分类与来源字段不一致：' + field)
        owner = record.get('material_owner')
        require(isinstance(owner, str) and owner.strip() and owner == origin.get('person'), eid + '材料归属人与来源不一致')
        category = record.get('material_category')
        require(category in ['A', 'B', 'C', 'D'], eid + '材料分类暂不支持')
        ns = numbers(record.get('invoice_numbers'), eid + '.invoice_numbers')
        preferred = numbers(record.get('material_preferred_invoice_numbers'), eid + '.preferred')
        excluded = numbers(record.get('not_for_selection_invoice_numbers'), eid + '.excluded')
        require(not (set(ns) & seen_numbers), '同票号出现在多个费用组：' + eid)
        seen_numbers.update(ns)
        require(set(preferred).isdisjoint(excluded) and set(preferred) | set(excluded) == set(ns), eid + '票版候选/排除分组不完整或重叠')
        versions = index_rows(origin.get('versions'), 'invoice_number', eid + '材料票版')
        require(set(versions) == set(ns), eid + '票号分组与材料来源不一致')
        require((category == 'D') == (not ns), eid + '无票D类与正式票号不一致')
        flags = record.get('material_flags')
        require(isinstance(flags, list) and all(isinstance(x, str) and x.strip() for x in flags), eid + 'material_flags必须保留文字数组')
        unresolved_flags = [x for x in flags if unresolved_material_flag(x)]
        notes = [x for x in flags if x not in unresolved_flags]
        pending = ['材料待补或待核：' + x for x in unresolved_flags]
        declared = amount(record.get('material_amount_display_not_claim'), eid + '分类票面金额')
        version_amounts, buyer_matches = {}, {}
        current = record.get('current_version_states')
        require(isinstance(current, dict) and set(current) == set(ns), eid + '当前票版状态不完整')
        for n, version in versions.items():
            require(n in finance and n in states and current[n] == states[n], eid + '票版当前状态与总表不一致')
            state = states[n]
            require(isinstance(state, dict) and state.get('current_state') == finance[n].get('verification_state'), n + '财务状态与依据不一致')
            require(state.get('local_reservation_blocked') in [True, False] and isinstance(state.get('local_reservation_blocked'), bool), n + '预约阻塞状态须明确布尔值')
            material_amount = amount(version.get('amount_display'), n + '材料票面金额')
            finance_amount = amount(finance[n].get('original_total_amount_display_only'), n + '财务来源票面金额')
            version_amounts[n] = money(material_amount)
            if material_amount != finance_amount: pending.append('票面金额与财务来源记录不一致：' + n)
            buyer_matches[n] = version.get('buyer_status') == 'reported_fields_match' and finance[n].get('buyer_audit', {}).get('status') == 'reported_fields_match'
        amounts = {Decimal(v) for v in version_amounts.values() if v is not None}
        group_amount = None
        if ns:
            if any(v is None for v in version_amounts.values()) or declared is None:
                pending.append('票面金额未知，未计入已知票面小计')
            elif len(amounts) != 1 or declared not in amounts:
                pending.append('同费用票版金额不唯一或分类金额不一致，未计入已知票面小计')
            elif not any(x.startswith('票面金额与财务来源记录不一致') for x in pending):
                group_amount = declared
            if category in ['A', 'B'] and len(preferred) != 1:
                pending.append('材料候选票版不唯一，须核定一个费用对应的采用版本')
            if category == 'A' and len(ns) != 1 or category == 'B' and len(ns) < 2:
                pending.append('单票/重开票分类与票版数量不一致')
        blocked = any(current[n]['local_reservation_blocked'] for n in preferred or ns)
        buyer_ok = bool(preferred) and all(buyer_matches[n] for n in preferred)
        normal = scope_complete and bool(preferred) and all(current[n]['current_state'] == 'normal_certified' for n in preferred)
        unambiguous = category in ['A', 'B'] and len(preferred) == 1 and (category != 'A' or len(ns) == 1) and (category != 'B' or len(ns) >= 2)
        if category == 'D': bucket = 'no_formal_invoice'
        elif group_amount is None: bucket = 'pending_review'
        elif blocked: bucket = 'reservation_blocked'
        elif category == 'C' or preferred and not buyer_ok: bucket = 'buyer_problem'
        elif buyer_ok and normal and unambiguous: bucket = 'material_candidate_system_normal'
        else: bucket = 'pending_review'
        if not scope_complete: pending.append('财务完整范围未获证明')
        if bucket == 'pending_review': pending.append('版本、购方或系统状态仍需独立核对')
        claim = amount(record.get('claim_amount'), eid + '.claim_amount')
        payee = record.get('actual_payee')
        require(payee is None or isinstance(payee, str), eid + '.actual_payee仅支持姓名字符串或null')
        rows.append({'expense_id': eid, 'material_owner': owner, 'expense_kind': record.get('expense_kind'),
            'material_category': category, 'readiness_category': bucket, 'readiness_label': BUCKETS[bucket],
            'invoice_numbers': ns, 'preferred_invoice_numbers': preferred, 'excluded_invoice_numbers': excluded,
            'version_amounts_not_claim': version_amounts, 'group_face_amount_not_claim': money(group_amount),
            'auxiliary_material_amount_not_invoice': money(declared) if category == 'D' else None,
            'material_flags': flags, 'material_notes': notes, 'unresolved_material_flags': unresolved_flags,
            'pending_review_reasons': list(dict.fromkeys(pending)),
            'needs_review': bool(pending) or bucket != 'material_candidate_system_normal',
            'declared_claim_amount': money(claim), 'actual_payee': payee,
            'current_version_states': current, 'selection_verified': False,
            'reimbursement_status': 'unknown', 'payment_status': 'unknown', 'ready_to_file': False})
    require(seen_numbers == set(states) == set(finance), '分类/财务来源票号总范围不一致')
    people = []
    for owner in sorted({r['material_owner'] for r in rows}):
        group = [r for r in rows if r['material_owner'] == owner]
        people.append(summarize(group) | {'material_owner': owner, 'actual_payee_inferred_from_owner': False})
    return {'schema': SCHEMA, 'case_id': report['case_id'], 'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'input_evidence': refs, 'classification_generated_at_utc': generated,
        'finance_observed_at_utc': observed, 'finance_scope_complete': scope_complete,
        'boundaries': {'financial_system_accessed': False, 'selected_invoices': False, 'materials_modified': False,
            'application_amount_calculated': False, 'raw_material_files_rehashed': False,
            'generated_at_is_finance_verification_time': False, 'finance_status_basis': 'captured_receipts_only',
            'input_evidence_files_rehashed': True, 'ready_to_file': False,
            'note': '票面按费用组去重统计，不是申请额；采集后状态未知，不能推定预约释放、已报或付款。'},
        'summary': summarize(rows), 'form_readiness': form_gaps(form), 'people': people, 'expenses': rows}


def summarize(rows):
    invoiced = [r for r in rows if r['invoice_numbers']]
    known = [r for r in invoiced if r['group_face_amount_not_claim'] is not None]
    counts = Counter(r['readiness_category'] for r in rows)
    subtotals = {}
    for key in BUCKETS:
        group = [r for r in known if r['readiness_category'] == key]
        subtotals[key] = money(sum((Decimal(r['group_face_amount_not_claim']) for r in group), Decimal(0))) if group else None
    return {'expense_group_count': len(rows), 'invoice_version_count': sum(len(r['invoice_numbers']) for r in rows),
        'invoiced_expense_group_count': len(invoiced), 'known_face_amount_group_count': len(known),
        'no_formal_invoice_group_count': counts['no_formal_invoice'], 'face_amount_applicable': bool(invoiced),
        'unknown_or_conflicting_face_amount_group_count': len(invoiced) - len(known),
        'group_face_amount_subtotal_not_claim': money(sum((Decimal(r['group_face_amount_not_claim']) for r in known), Decimal(0))) if known else None,
        'face_amount_complete': len(known) == len(invoiced),
        'material_candidate_group_count_before_finance': sum(r['material_category'] in ['A', 'B'] for r in rows),
        'pending_review_group_count': sum(r['needs_review'] for r in rows),
        'material_flag_group_count': sum(bool(r['material_flags']) for r in rows),
        'unresolved_material_flag_group_count': sum(bool(r['unresolved_material_flags']) for r in rows),
        'category_counts': {key: counts[key] for key in BUCKETS},
        'category_face_amount_subtotals_not_claim': subtotals}


def csv_cell(value):
    if value is None: return ''
    if isinstance(value, (list, dict)): value = json.dumps(value, ensure_ascii=False)
    value = str(value)
    return "'" + value if value.startswith(('=', '+', '-', '@', '\t', '\r')) else value


def write_csv(path, fields, rows):
    with path.open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for row in rows: writer.writerow({k: csv_cell(row.get(k)) for k in fields})


def markdown(report):
    summary = report['summary']
    face_total = summary['group_face_amount_subtotal_not_claim']
    face_display = face_total + ' 元' if face_total is not None else ('不适用（无正式票）' if not summary['face_amount_applicable'] else '未知')
    lines = ['# 报销就绪度清单', '', '[先看问题总结与处理动作](问题总结.html) · [文字版](问题总结.md)', '', '案件：' + report['case_id'],
        '清单生成：' + report['generated_at_utc'],
        '分类生成：' + report['classification_generated_at_utc'],
        '财务采集：' + '、'.join(report['finance_observed_at_utc']), '',
        '本清单可基于历史回执；清单生成时间不是财务实时核验时间，系统状态仅截至上述采集时间。', '',
        '材料候选不等于可报。当前仅汇总已采集证据；范围、经费、证明人、联系人、收款、正式选票及建单须分别核定。', '',
        f"共 {summary['expense_group_count']} 个费用组、{summary['invoice_version_count']} 个票版；新旧同费用仅计一次。",
        f"已知票面金额小计 {face_display}（不是申请额）；未知或冲突金额 {summary['unknown_or_conflicting_face_amount_group_count']} 组。无正式票 {summary['no_formal_invoice_group_count']} 组，不计入票面合计。", '',
        '| 分类 | 费用组 | 已知票面小计（非申请额） |', '|---|---:|---:|']
    for key, label in BUCKETS.items():
        subtotal = summary['category_face_amount_subtotals_not_claim'][key]
        display = subtotal if subtotal is not None else ('不适用' if key == 'no_formal_invoice' else '未知' if summary['category_counts'][key] else '—')
        lines.append(f"| {label} | {summary['category_counts'][key]} | {display} |")
    lines += ['', f"保留材料备注/标记 {summary['material_flag_group_count']} 组，其中明确未解决材料问题 {summary['unresolved_material_flag_group_count']} 组；所有问题费用共 {summary['pending_review_group_count']} 组，详见问题费用.csv。", '', '## 整案差项', '']
    for gap in report['form_readiness']['gaps']:
        lines.append('- ' + gap['detail'] + '：' + gap['state'])
    lines += ['', '申请额：' + (report['form_readiness']['declared_application_amount'] or '未知，未用票面金额代填'),
        '材料归属人不自动视为实际收款人。已配置字段仍需核对，不推定已选票、已建单、已报销或已付款。', '',
        '输入JSON及三份input_evidence已按SHA核验；本工具未重新扫描原始PDF、未访问财务、未修改材料。', '']
    return '\n'.join(lines)


def generate(classification_path, form_path, output_dir):
    output = Path(output_dir)
    require(not output.exists(), '输出目录已存在，禁止覆盖')
    report = build_readiness(classification_path, form_path)
    output.mkdir(parents=True, exist_ok=False)
    with (output / '就绪度.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2); stream.write('\n')
    people = []
    for row in report['people']:
        people.append({**row, **row['category_counts']})
    write_csv(output / '按材料归属人员汇总.csv', ['material_owner', 'expense_group_count', 'invoice_version_count',
        'invoiced_expense_group_count', 'no_formal_invoice_group_count', 'face_amount_applicable',
        'group_face_amount_subtotal_not_claim', 'unknown_or_conflicting_face_amount_group_count',
        *BUCKETS, 'material_flag_group_count', 'unresolved_material_flag_group_count', 'pending_review_group_count', 'actual_payee_inferred_from_owner'], people)
    write_csv(output / '问题费用.csv', ['expense_id', 'material_owner', 'expense_kind', 'readiness_label',
        'invoice_numbers', 'preferred_invoice_numbers', 'excluded_invoice_numbers', 'group_face_amount_not_claim',
        'auxiliary_material_amount_not_invoice', 'material_notes', 'unresolved_material_flags', 'pending_review_reasons', 'declared_claim_amount', 'actual_payee'],
        [r for r in report['expenses'] if r['needs_review']])
    (output / '报销就绪度.md').write_text(markdown(report), encoding='utf-8')
    from checklist_delivery import register_delivery
    report['table_delivery'] = register_delivery(output, 'readiness', report['case_id'], '报销办理差项清单')
    (output / '就绪度.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--classification', required=True, type=Path)
    parser.add_argument('--form', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    try:
        report = generate(args.classification, args.form, args.output_dir)
    except (ValueError, OSError) as error:
        parser.exit(2, str(error) + '\n')
    print(json.dumps({'ok': True, 'case_id': report['case_id'], 'output_dir': str(args.output_dir.resolve()),
                      'summary': report['summary'], 'ready_to_file': False,
                      'table_delivery': report['table_delivery']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
