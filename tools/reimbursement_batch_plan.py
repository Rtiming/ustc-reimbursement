#!/usr/bin/env python3
"""归一化材料的离线拆单、金额及PDF导出计划；不访问财务、不生成财务单。"""
from __future__ import annotations

import argparse
import copy
import json
import math
import re
import sys
import unicodedata
from collections import defaultdict
from decimal import Decimal
from pathlib import Path, PurePosixPath, PureWindowsPath

from reimbursement_readiness import amount, money
from reimbursement_standards import encoded, iso_date, issue, read_json, safe_path, sha, text

SCHEMA = 'ustc-reimbursement-batch-input-v1'


def _refs(value):
    return isinstance(value, list) and bool(value) and all(text(x) for x in value)


def _confirmed(row):
    return isinstance(row, dict) and row.get('status') == 'confirmed' and _refs(row.get('source_refs'))


def _amount_confirmed(row):
    return (_confirmed(row) and row.get('amount_status', 'confirmed') == 'confirmed'
            and (row.get('amount_origin') is None or
                 isinstance(row.get('amount_origin'), str) and row['amount_origin'] in {'document_content', 'verified_extract'}))


def _filename_claim_problem(record, expense, invoices, aliases, folder, face, value):
    """核对既有来源声明，不读取文件；只支持唯一原票的明确实报候选。"""
    if (record.get('kind') != 'request' or not _confirmed(record)
            or record.get('amount_status', 'confirmed') != 'confirmed'):
        return '文件名实报仅可作为已确认的request申请依据，不是票面或清单来源'
    ids = expense['invoice_ids']
    if (len(ids) != 1 or not isinstance(record.get('invoice_id'), str)
            or aliases.get(record['invoice_id']) != ids[0] or expense.get('invoice_allocations') is not None):
        return '文件名实报须明确绑定本费用唯一完整发票，不推定共票或整组分摊'
    invoice = invoices[ids[0]]
    if record.get('invoice_number') != invoice.get('invoice_number'):
        return '文件名实报依据的完整票号与本费用原票不符'
    common = set(record.get('source_refs', [])) & set(invoice.get('source_refs', []))
    pages = [ref for ref in common if re.fullmatch(r'.+#sha256=[a-f0-9]{64}&page=[1-9][0-9]*', ref)]
    if len(pages) != 1:
        return '须以唯一原件SHA及页码引用同时绑定本票与申请依据'
    absolute = lambda path: isinstance(path, str) and (PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute())
    source_path = pages[0].split('#sha256=', 1)[0]
    archive, archive_sha, chain = (record.get(k) for k in ['source_archive', 'source_archive_sha256', 'source_chain'])
    if (not absolute(source_path) or not absolute(archive) or not isinstance(archive_sha, str)
            or not re.fullmatch(r'[a-f0-9]{64}', archive_sha) or not isinstance(chain, str)
            or not chain.startswith(archive + '!/') or not archive.lower().endswith('.zip')):
        return '须保留原ZIP绝对路径、SHA及精确source_chain成员名'
    if any(record.get(key) != invoice.get(key) for key in ['source_archive', 'source_archive_sha256', 'source_chain']):
        return '申请依据与本票登记的原ZIP路径、SHA或成员链不一致'
    member = chain[len(archive) + 2:]
    parts = member.split('/')
    if (len(parts) < 2 or any(part in ['', '.', '..'] for part in parts) or '\\' in member
            or '!/' in member or re.search(r'[\r\n\x00]', member) or folder not in parts[:-1]):
        return '原包成员须精确属于本活动目录，不能使用越界、模糊或另组成员'
    filename = PurePosixPath(member)
    if filename.suffix.lower() not in ['.pdf', '.ofd', '.png', '.jpg', '.jpeg'] or filename.suffix.lower() != Path(source_path).suffix.lower():
        return '原ZIP成员与本票原件格式不一致'
    number = r'(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?'
    match = re.fullmatch(r'[^\r\n]+?(?<![-+±−－0-9.])(' + number + r')元[ \t]*实报[ \t:：]*(' + number + r')元', filename.stem)
    if (not match or filename.stem.count('实报') != 1 or face is None or value is None
            or Decimal(match[1]) != face or Decimal(match[2]) != value or not Decimal(0) < value <= face):
        return '成员名须唯一明确“原票面金额元 实报申请金额元”，与原票及申请一致且不超过票面'
    basis = record.get('claim_basis_ref')
    match = re.fullmatch(r'(.+\.json)#sha256=[a-f0-9]{64}/rows/[0-9]+', basis) if isinstance(basis, str) else None
    if not match or not absolute(match[1]) or basis not in record['source_refs']:
        return '须引用已有逐票实报依据JSON的绝对路径、SHA及精确/rows/行号'
    return None


def _issues_result(issues, method='batch_plan'):
    return {'status': 'blocked' if issues else 'ready', 'issues': issues, 'needed_inputs': copy.deepcopy(issues),
            'next_action': {'method': method, 'action': 'resolve_fields' if issues else 'review_plan',
                            'fields': sorted({x['field'] for x in issues}),
                            'instruction': '按来源补齐或确认所列字段后重新规划；不要补零或猜分摊。' if issues else
                            '核对本计划并由现有财务入口另行检查授权和当前状态。'}}


def _amount(value, field, issues):
    try:
        result = amount(value, field)
    except (ValueError, ArithmeticError) as exc:
        issues.append(issue(field, 'INVALID_AMOUNT', str(exc)))
        return None
    if result is None:
        issues.append(issue(field, 'AMOUNT_UNKNOWN', '金额未识别或未确认，须核对原件；未知不按0处理'))
    return result


def _sum(values):
    values = list(values)
    return sum(values, Decimal(0)) if values and all(x is not None for x in values) else None


def chinese_number(number):
    if number >= 10000:
        high, low = divmod(number, 10000)
        return chinese_number(high) + '万' + (('零' if low < 1000 else '') + chinese_number(low) if low else '')
    digits = '零一二三四五六七八九'
    result, zero = '', False
    for divisor, unit in [(1000, '千'), (100, '百'), (10, '十'), (1, '')]:
        digit, number = divmod(number, divisor)
        if digit:
            if zero:
                result += '零'
            result += ('' if digit == 1 and divisor == 10 and not result else digits[digit]) + unit
            zero = False
        elif result and number:
            zero = True
    return result or '零'


def safe_filename(value):
    """摘要保留原文；文件名另做Mac/Windows共用的保守清理。"""
    value = unicodedata.normalize('NFC', value)
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value).strip().rstrip('. ')
    value = value[:180].rstrip('. ')
    if not value:
        raise ValueError('清理后文件名为空')
    if re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', value):
        value = '_' + value
    return value


def _pack(groups, limit):
    """只接受最少单数；不可拆组先装箱，余下单票可直接补齐容量。"""
    count = sum(len(x['invoice_ids']) for x in groups)
    if not count:
        return [], 'no_invoices'
    bins_count = math.ceil(count / limit)
    if any(len(x['invoice_ids']) > limit for x in groups):
        return [], 'indivisible_group_over_limit'
    ordered = sorted(groups, key=lambda x: (-len(x['invoice_ids']), x['order']))
    bins, sizes = [[] for _ in range(bins_count)], [0] * bins_count
    for group in ordered:
        candidates = [i for i in range(bins_count) if sizes[i] + len(group['invoice_ids']) <= limit]
        if not candidates:
            break
        index = min(candidates, key=lambda i: (sizes[i], i))
        bins[index].append(group)
        sizes[index] += len(group['invoice_ids'])
    else:
        return bins, 'balanced_greedy'
    # 贪心失败不等于不可拆；有界回溯找最少单数，耗尽预算则明确待决策。
    multi = [x for x in ordered if len(x['invoice_ids']) > 1]
    singles = [x for x in ordered if len(x['invoice_ids']) == 1]
    if len(multi) > 500:
        return [], 'packing_search_limit'
    bins, sizes, visits = [[] for _ in range(bins_count)], [0] * bins_count, 0

    def visit(offset):
        nonlocal visits
        visits += 1
        if visits > 100000:
            return False
        if offset == len(multi):
            for group in singles:
                index = min(range(bins_count), key=lambda i: (sizes[i], i))
                if sizes[index] >= limit:
                    return False
                bins[index].append(group)
                sizes[index] += 1
            return True
        group, tried = multi[offset], set()
        weight = len(group['invoice_ids'])
        for index in sorted(range(bins_count), key=lambda i: (sizes[i], i)):
            if sizes[index] in tried or sizes[index] + weight > limit:
                continue
            tried.add(sizes[index])
            bins[index].append(group)
            sizes[index] += weight
            if visit(offset + 1):
                return True
            bins[index].pop()
            sizes[index] -= weight
        return False

    if visit(0):
        return bins, 'bounded_search'
    return [], 'packing_search_limit' if visits > 100000 else 'indivisible_groups_cannot_fit_minimum'


def _policy(data):
    issues = []
    policy = data.get('policy', {})
    if not isinstance(policy, dict):
        policy = {}
    if not _confirmed(policy):
        issues.append(issue('policy', 'POLICY_CONFIRMATION_REQUIRED', '须引用本案明确采用的已确认规则'))
    if type(policy.get('max_invoices_per_form')) is not int or policy.get('max_invoices_per_form') != 50:
        issues.append(issue('policy.max_invoices_per_form', 'UNSUPPORTED_LIMIT', '当前工具仅实现明确确认的每单最多50张规则'))
    for field, expected in [('split_strategy', 'minimal_balanced'), ('amount_strategy', 'minimum_comparable')]:
        if policy.get(field) != expected or (isinstance(expected, bool) and type(policy.get(field)) is not bool):
            issues.append(issue('policy.' + field, 'UNSUPPORTED_POLICY', '须明确选择当前已实现的规则：' + str(expected)))
    if type(policy.get('contact_in_summary')) is not bool:
        issues.append(issue('policy.contact_in_summary', 'UNSUPPORTED_POLICY', '须明确联系人写入摘要或系统备注，不接受未知位置'))
    priority = policy.get('contact_priority')
    if priority is not None and (not isinstance(priority, list) or not priority
            or any(x not in {'responsible_person', 'form_filler'} for x in priority) or len(priority) != len(set(priority))):
        issues.append(issue('policy.contact_priority', 'INVALID_CONTACT_PRIORITY', '联系人顺序仅支持responsible_person/form_filler且不可重复'))
    return policy, issues


def _select_contact(clarification, policy, field, issues):
    priority = policy.get('contact_priority')
    previous = clarification.get('contact')
    if priority is None:
        if not _confirmed(previous) or not text(previous.get('name')) or previous.get('position') != 'bottom':
            issues.append(issue(field + '.contact', 'BOTTOM_CONTACT_UNKNOWN', '须识别并确认明白纸底部的问题联系人'))
            return None
        return {**copy.deepcopy(previous), 'basis': previous.get('basis', {'selected_field': 'confirmed_bottom_contact'})}
    if (not isinstance(priority, list) or not priority or any(x not in {'responsible_person', 'form_filler'} for x in priority)
            or len(priority) != len(set(priority))):
        issues.append(issue('policy.contact_priority', 'INVALID_CONTACT_PRIORITY', '先明确支持的联系人优先级'))
        return None
    blanks, sources = [], []
    for key in priority:
        row = clarification.get(key)
        if not _confirmed(row):
            issues.append(issue(field + '.' + key, 'CONTACT_FIELD_UNCONFIRMED', '须核对该原字段；缺失或识别不清不能视为空白后选其他人'))
            return None
        sources.extend(row['source_refs'])
        if row.get('blank') is True:
            if text(row.get('name')):
                issues.append(issue(field + '.' + key, 'CONTACT_BLANK_CONFLICT', '该字段同时标为空白并给出姓名，须核对原件'))
                return None
            blanks.append(key)
            continue
        if not text(row.get('name')):
            issues.append(issue(field + '.' + key, 'CONTACT_VALUE_UNKNOWN', '姓名未知；只有原件确认空白且blank:true才可选择下一字段'))
            return None
        contact = {**copy.deepcopy(row), 'position': 'bottom',
                   'basis': {'selected_field': key, 'priority': list(priority), 'preceding_blank_fields': blanks,
                             'source_refs': list(dict.fromkeys(sources))}}
        if _confirmed(previous) and previous.get('name') != contact['name']:
            issues.append(issue(field + '.contact', 'CONTACT_SELECTION_CONFLICT', '已有联系人与本次优先级选择不同，须修正选定记录并保留原字段'))
        return contact
    issues.append(issue(field + '.contact', 'NO_CONTACT_AVAILABLE', '所有允许的联系人字段均确认空白，须询问本案联系人'))
    return None


def _contact_text(contact, field, issues):
    """只消费已选角色本身的字段；缺省不表示原纸空白，不合并其他人的信息。"""
    if contact is None:
        return None
    basis = contact.get('basis')
    selected = basis.get('selected_field') if isinstance(basis, dict) else None
    owner = selected if selected in {'responsible_person', 'form_filler'} else 'contact'
    parts = ['报销有问题请联系' + contact['name']]
    for key, label in [('student_id', '学号'), ('employee_id', '工号'), ('phone', '电话'),
                       ('wechat', '微信'), ('qq', 'QQ'), ('email', '邮箱')]:
        value, status = contact.get(key), contact.get(key + '_status')
        place = field + '.' + owner + '.' + key
        empty = value is None or isinstance(value, str) and not value.strip()
        if status == 'deferred_by_user':
            deferred = contact.get(key + '_deferral', {})
            refs = deferred.get('source_refs') if isinstance(deferred, dict) else None
            if (not empty or not isinstance(deferred, dict) or deferred.get('confirmed_by_user') is not True
                    or not isinstance(deferred.get('reason'), str) or not deferred['reason'].strip()
                    or not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or not ref.strip() for ref in refs)):
                issues.append(issue(place, 'CONTACT_DETAIL_DEFERRAL_INVALID', '暂不填写须保留用户明确决定、理由和来源，当前值须空；原纸候选另外保留'))
            # 这是用户允许暂缺的可选联系字段，不表示原纸空白或候选已确认。
            continue
        if status in ['blank', 'confirmed_blank']:
            if not empty:
                issues.append(issue(place, 'CONTACT_DETAIL_BLANK_CONFLICT', '本字段同时标为明确空白并给出值，须核对所选联系人原件'))
            continue
        if status is not None and status != 'confirmed' or status is None and contact.get(key + '_candidates'):
            issues.append(issue(place, 'CONTACT_DETAIL_UNCONFIRMED', '本字段未确认或仍为候选，不写入系统备注；须核对所选联系人同角色来源'))
            continue
        if empty and status is None:
            # 兼容既有只确认姓名的记录，不把未提供字段当作“纸上未写”。
            continue
        if (not isinstance(value, str) or not value.strip()
                or re.search(r'[\x00-\x1f\x7f-\x9f\u2028\u2029]', value)):
            issues.append(issue(place, 'CONTACT_DETAIL_INVALID', '本字段须为已确认的非空单行文本，号码不转数字、不补位'))
            continue
        # 所选行已由_select_contact核验confirmed/source_refs；显式字段状态优先。
        parts.append(label + '：' + value)
    return '；'.join(parts)


def _finance_readiness(context, policy):
    problems, fields = [], {}
    if not isinstance(context, dict):
        context = {}
    roles = policy.get('required_roles', [])
    if not isinstance(roles, list) or any(x not in {'witness', 'final_funding_approver', 'pre_reviewer', 'acceptance_person'} for x in roles):
        problems.append(issue('policy.required_roles', 'INVALID_ROLE_FIELDS', '角色字段不受支持；不得以一个姓名代填其他角色'))
        roles = []
    for key in roles:
        row = context.get(key)
        fields[key] = copy.deepcopy(row) if _confirmed(row) else None
        if not _confirmed(row) or not text(row.get('name')):
            problems.append(issue('case_context.' + key, 'ROLE_UNKNOWN', '须确认该角色真实姓名及本案依据'))
        elif row.get('native_verified') is not True or not text(row.get('person_id')) or not _refs(row.get('native_source_refs')):
            problems.append(issue('case_context.' + key, 'NATIVE_IDENTITY_NOT_VERIFIED', '须由正式财务读取入口核对当前原生姓名、工号及角色；历史提示不能绑定'))
    prohibited = policy.get('prohibited_roles', [])
    if not isinstance(prohibited, list) or any(x not in {'witness', 'final_funding_approver', 'pre_reviewer', 'acceptance_person'} for x in prohibited):
        problems.append(issue('policy.prohibited_roles', 'INVALID_ROLE_FIELDS', '禁止填入的角色须为字段名数组'))
        prohibited = []
    for key in prohibited:
        if context.get(key):
            fields[key] = None
            problems.append(issue('case_context.' + key, 'ROLE_NOT_AUTHORIZED', '本案规则未采用该角色，须留空'))
    funding = context.get('funding')
    fields['funding'] = copy.deepcopy(funding) if _confirmed(funding) else None
    if not _confirmed(funding) or not text(funding.get('code')) or funding.get('complete') is not True:
        problems.append(issue('case_context.funding', 'FULL_FUNDING_REQUIRED', '从本案或当前系统确认完整经费卡号；尾号提示不可代填'))
    elif funding.get('native_verified') is not True or not _refs(funding.get('native_source_refs')):
        problems.append(issue('case_context.funding', 'NATIVE_FUNDING_NOT_VERIFIED', '须回读当前可选完整经费及最终批准流程'))
    return {**_issues_result(problems), 'fields': fields, 'write_authorized': False}


def _invoices(rows, field):
    issues, warnings, invoices, aliases, numbers = [], [], {}, {}, {}
    if not isinstance(rows, list):
        return {}, {}, [issue(field, 'INVALID_INVOICES', '发票须为数组')], []
    for index, source in enumerate(rows):
        place = f'{field}[{index}]'
        if not isinstance(source, dict) or not text(source.get('invoice_id')):
            issues.append(issue(place, 'INVOICE_ID_REQUIRED', '须有稳定发票ID'))
            continue
        row = copy.deepcopy(source)
        identifier = row['invoice_id']
        number = row.get('invoice_number')
        if not _confirmed(row) or not text(number):
            issues.append(issue(place, 'INVOICE_IDENTITY_UNKNOWN', '须从票面确认发票身份和完整票号，不能按金额或文件名判重'))
        if not _refs(row.get('source_refs')):
            row['source_refs'] = []
        face = _amount(row.get('face_amount'), place + '.face_amount', issues)
        row['face_amount'] = money(face)
        canonical = aliases.get(identifier) or (numbers.get(number) if text(number) else None) or identifier
        aliases[identifier] = canonical
        if text(number):
            numbers[number] = canonical
        if canonical in invoices:
            old = invoices[canonical]
            if (old.get('invoice_number') != number or old.get('face_amount') != row.get('face_amount')
                    or old.get('status') != row.get('status')):
                issues.append(issue(place, 'DUPLICATE_INVOICE_CONFLICT', '相同发票身份的票面/确认状态冲突，须核原件'))
            old['source_refs'] = sorted(set(old.get('source_refs', []) + row.get('source_refs', [])))
            warnings.append(issue(place, 'DUPLICATE_COLLAPSED', '同票多个载体只计一次，保留全部来源'))
        else:
            row['invoice_id'] = canonical
            invoices[canonical] = row
    return invoices, aliases, issues, warnings


def _document(document, policy, prefix, export_root, used_paths):
    structure, money_issues, export_issues, warnings = [], [], [], []
    identifier = document.get('document_id')
    if not text(identifier):
        structure.append(issue(prefix + '.document_id', 'DOCUMENT_ID_REQUIRED', '须给单据材料组稳定ID'))
    folder = document.get('folder_name')
    if not text(folder) or not text(document.get('folder_source_ref')):
        export_issues.append(issue(prefix + '.folder_name', 'SUMMARY_SOURCE_REQUIRED', '须核对电子材料的原文件夹名及来源作为摘要'))
    clarification = document.get('clarification', {})
    if not isinstance(clarification, dict):
        clarification = {}
    business_date = clarification.get('business_date', {})
    date_value = None
    if _confirmed(business_date):
        try:
            date_value = iso_date(business_date.get('value')).isoformat()
        except ValueError:
            pass
    if date_value is None:
        export_issues.append(issue(prefix + '.clarification.business_date', 'BUSINESS_DATE_UNKNOWN', '须确认明白纸真实业务日期，不能用下载日期或猜年份'))
    contact = _select_contact(clarification, policy, prefix + '.clarification', export_issues)
    contact_text = _contact_text(contact, prefix + '.clarification', export_issues)
    invoices, aliases, invoice_issues, duplicate_warnings = _invoices(document.get('invoices', []), prefix + '.invoices')
    structure.extend(invoice_issues)
    warnings.extend(duplicate_warnings)
    rows = document.get('expenses', [])
    if not isinstance(rows, list):
        rows = []
        structure.append(issue(prefix + '.expenses', 'INVALID_EXPENSES', '费用须为数组'))
    expenses, used, excluded, expense_ids = [], defaultdict(list), set(), set()
    for n, original in enumerate(rows):
        field = f'{prefix}.expenses[{n}]'
        if not isinstance(original, dict) or not text(original.get('expense_id')) or original['expense_id'] in expense_ids:
            structure.append(issue(field, 'EXPENSE_ID_INVALID', '每项费用须有唯一稳定ID；票版不可另算一项费用'))
            continue
        row = copy.deepcopy(original)
        expense_ids.add(row['expense_id'])
        refs = row.get('invoice_ids', [])
        if not isinstance(refs, list) or not refs or any(not isinstance(x, str) or x not in aliases for x in refs):
            structure.append(issue(field + '.invoice_ids', 'INVOICE_LINK_REQUIRED', '费用须关联已登记且明确选用的发票'))
            refs = [x for x in refs if isinstance(x, str) and x in aliases] if isinstance(refs, list) else []
        row['invoice_ids'] = list(dict.fromkeys(aliases[x] for x in refs))
        old = row.get('excluded_invoice_ids', [])
        if not isinstance(old, list) or any(not isinstance(x, str) or x not in aliases for x in old):
            structure.append(issue(field + '.excluded_invoice_ids', 'INVALID_EXCLUSION', '排除票版须指向已登记发票'))
            old = []
        for x in old:
            excluded.add(aliases[x])
        row['excluded_invoice_ids'] = list(dict.fromkeys(aliases[x] for x in old))
        row['_field'], row['_order'] = field, len(expenses)
        for iid in row['invoice_ids']:
            used[iid].append(len(expenses))
            if invoices[iid].get('version_role') in {'superseded', 'excluded'}:
                structure.append(issue(field + '.invoice_ids', 'OLD_VERSION_SELECTED', '明确旧版/排除票不得作为当前申请票'))
        expenses.append(row)
    for iid in set(invoices) - set(used) - excluded:
        structure.append(issue(prefix + '.invoices.' + iid, 'UNASSIGNED_INVOICE', '该发票未分配费用或明确排除，不能遗漏'))
    for iid in set(used) & excluded:
        structure.append(issue(prefix + '.invoices.' + iid, 'SELECTED_AND_EXCLUDED', '同票既选用又排除，须核对票版决定'))
    if not used:
        structure.append(issue(prefix + '.expenses', 'NO_SELECTED_INVOICES', '尚未确认任何待报销发票/费用'))

    # 共票与明确不可拆组构成原子组；不能为满足张数把同票放入两张单。
    parent = list(range(len(expenses)))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def join(a, b):
        parent[find(b)] = find(a)
    for indexes in used.values():
        for n in indexes[1:]:
            join(indexes[0], n)
    grouping = {}
    for n, row in enumerate(expenses):
        group = row.get('keep_together')
        if text(group):
            if group in grouping:
                join(grouping[group], n)
            grouping[group] = n

    invoice_parts = defaultdict(list)
    evaluated = []
    for row in expenses:
        field, local = row['_field'], []
        if not _confirmed(row) or not text(row.get('scope_id')):
            local.append(issue(field + '.scope_id', 'EXPENSE_SCOPE_UNCONFIRMED', '须确认此次申请费用的同一比较范围及来源'))
        for iid in row['invoice_ids']:
            if not _amount_confirmed(invoices[iid]):
                local.append(issue(field + '.invoices.' + iid, 'INVOICE_AMOUNT_UNCONFIRMED',
                                   '该票金额尚未从原件正文核定，或仅来自文件名候选；不进入取小运算'))
        allocation = row.get('invoice_allocations')
        parts = []
        if allocation is not None:
            if not isinstance(allocation, list):
                local.append(issue(field + '.invoice_allocations', 'INVALID_ALLOCATION', '共票拆分须为逐票分配数组'))
                allocation = []
            seen = set()
            for n, part in enumerate(allocation):
                place = field + f'.invoice_allocations[{n}]'
                if not isinstance(part, dict) or part.get('invoice_id') not in aliases:
                    local.append(issue(place, 'UNKNOWN_INVOICE', '分配须指向已登记发票'))
                    continue
                iid = aliases[part['invoice_id']]
                if iid in seen or iid not in row['invoice_ids'] or not _refs(part.get('source_refs')):
                    local.append(issue(place, 'ALLOCATION_BASIS_REQUIRED', '分配范围须唯一、属于该费用并有真实依据'))
                    continue
                seen.add(iid)
                value = _amount(part.get('amount'), place + '.amount', local)
                parts.append(value)
                invoice_parts[iid].append((row['expense_id'], value))
            if seen != set(row['invoice_ids']):
                local.append(issue(field + '.invoice_allocations', 'INCOMPLETE_ALLOCATION', '须覆盖该费用全部选用票据'))
        elif any(len(used[x]) > 1 for x in row['invoice_ids']):
            local.append(issue(field + '.invoice_allocations', 'SHARED_INVOICE_ALLOCATION_REQUIRED', '同票关联多费用，须有真实范围分配；不能重复票面额'))
        else:
            for iid in row['invoice_ids']:
                value = _amount(invoices[iid].get('face_amount'), field + '.invoices.' + iid, local)
                parts.append(value)
                invoice_parts[iid].append((row['expense_id'], value))
        face = _sum(parts)
        values = [{'kind': 'invoice', 'amount': money(face), 'scope_id': row.get('scope_id'),
                   'source_refs': sorted({s for iid in row['invoice_ids'] for s in invoices[iid].get('source_refs', [])})}]
        evidence = row.get('amount_evidence', [])
        filename_claim = False
        if not isinstance(evidence, list):
            evidence = []
            local.append(issue(field + '.amount_evidence', 'INVALID_EVIDENCE', '金额依据须为数组'))
        for n, record in enumerate(evidence):
            place = field + f'.amount_evidence[{n}]'
            if not isinstance(record, dict) or record.get('kind') not in {'invoice', 'list', 'request'}:
                local.append(issue(place, 'INVALID_EVIDENCE_KIND', '仅比较票面、清单及申请/明白纸金额'))
                continue
            value = _amount(record.get('amount'), place + '.amount', local)
            if record.get('amount_origin') == 'filename_explicit_claim':
                problem = _filename_claim_problem(record, row, invoices, aliases, folder, face, value)
                if problem:
                    local.append(issue(place, 'FILENAME_CLAIM_BASIS_INVALID', problem))
                else:
                    filename_claim = True
                amount_confirmed = problem is None
            else:
                amount_confirmed = _amount_confirmed(record)
            if not amount_confirmed or not text(row.get('scope_id')) or record.get('scope_id') != row.get('scope_id'):
                local.append(issue(place, 'AMOUNTS_NOT_COMPARABLE', '金额未确认或不属于同一费用范围，不可直接取小'))
            if record.get('kind') == 'invoice' and value != face:
                local.append(issue(place, 'INVOICE_FACE_CONFLICT', '票面依据与逐票原金额/明确共票分配不一致'))
            values.append({**copy.deepcopy(record), 'amount': money(value)})
        claim = min(Decimal(x['amount']) for x in values) if not local and face is not None else None
        if claim is not None and claim <= 0:
            local.append(issue(field + '.claim_amount', 'NONPOSITIVE_CLAIM', '取小结果不大于0，须明确该费用是否退出本次申请'))
        payments = row.get('payments', [])
        payment_values, payees = [], []
        if not isinstance(payments, list) or not payments:
            local.append(issue(field + '.payments', 'PAYMENT_MAPPING_REQUIRED', '须据明白纸确认打给谁以及该费用的实际申请分配'))
            payments = []
        seen_payees = set()
        for n, payee in enumerate(payments):
            place = field + f'.payments[{n}]'
            if (not _confirmed(payee) or not text(payee.get('payee_id')) or not text(payee.get('name'))
                    or not text(payee.get('mode'))):
                local.append(issue(place, 'PAYEE_UNCONFIRMED', '须有已确认的收款身份、方式及来源'))
                continue
            key = (payee['payee_id'], payee['mode'])
            if key in seen_payees:
                local.append(issue(place, 'DUPLICATE_PAYMENT', '同费用同收款人同支付方式重复，须合并并保留依据'))
            seen_payees.add(key)
            value = _amount(payee.get('amount'), place + '.amount', local)
            payment_values.append(value)
            payees.append({**copy.deepcopy(payee), 'amount': money(value), 'expense_id': row['expense_id']})
        payment_total = _sum(payment_values)
        if claim is not None and payment_total is not None and claim != payment_total:
            local.append(issue(field + '.payments', 'PAYMENT_TOTAL_MISMATCH', '收款分配合计不等于该费用取小后的申请额，不能按比例改写'))
        if filename_claim and claim is not None:
            local.append(issue(field + '.amount_evidence', 'FILENAME_CLAIM_NATIVE_REVIEW_REQUIRED',
                               '文件名明确实报仅核定候选；仍须真实原生逐票映射、金额保存及独立持久回读，不能以手填native_expense_id升为执行就绪'))
        native = row.get('native_expense_allocations', [])
        native_values = []
        reduced = claim is not None and face is not None and claim < face
        if reduced or native or not isinstance(native, list):
            if not isinstance(native, list) or not native:
                local.append(issue(field + '.native_expense_allocations', 'REDUCTION_NATIVE_MAPPING_REQUIRED',
                                   '少报金额须确认落到哪项原生费用并与支付合计一致；保留原票面不修改'))
            else:
                native_ids = set()
                for n, part in enumerate(native):
                    place = field + f'.native_expense_allocations[{n}]'
                    if not _confirmed(part) or not text(part.get('native_expense_id')) or part.get('native_expense_id') in native_ids:
                        local.append(issue(place, 'NATIVE_MAPPING_UNCONFIRMED', '原生费用分配须唯一且有已确认依据'))
                        continue
                    native_ids.add(part['native_expense_id'])
                    native_values.append(_amount(part.get('amount'), place + '.amount', local))
                if claim is not None and _sum(native_values) != claim:
                    local.append(issue(field + '.native_expense_allocations', 'NATIVE_TOTAL_MISMATCH', '原生费用分配须等于取小后的申请额'))
        evaluated.append({'expense_id': row['expense_id'], 'scope_id': row.get('scope_id'),
                          'invoice_ids': row['invoice_ids'], 'excluded_invoice_ids': row['excluded_invoice_ids'],
                          'invoice_face_scope_amount': money(face), 'amount_evidence': values,
                          'claim_amount': money(claim), 'payments': payees,
                          'payment_total': money(payment_total), 'native_expense_allocations': copy.deepcopy(native),
                          'source_refs': row.get('source_refs', []), **_issues_result(local)})
        money_issues.extend(local)
    for iid in used:
        actual = _sum(x[1] for x in invoice_parts[iid])
        expected = amount(invoices[iid].get('face_amount'), iid)
        if actual != expected or actual is None:
            problem = issue(prefix + '.invoices.' + iid, 'INVOICE_ALLOCATION_NOT_CLOSED', '该票各费用范围分配未闭合到原票面额，须核对共票关系')
            money_issues.append(problem)
            for n in used[iid]:
                evaluated[n]['claim_amount'] = None
                evaluated[n]['status'] = 'blocked'
                evaluated[n]['issues'].append(problem)
                evaluated[n]['needed_inputs'].append(problem)
    components = {}
    for n, row in enumerate(expenses):
        key = find(n)
        group = components.setdefault(key, {'expense_indexes': [], 'invoice_ids': [], 'order': n})
        group['expense_indexes'].append(n)
        group['invoice_ids'] = list(dict.fromkeys(group['invoice_ids'] + row['invoice_ids']))
    groups = list(components.values())
    bins, packing = _pack(groups, 50)
    if not bins:
        structure.append(issue(prefix + '.expenses', 'SPLIT_REQUIRES_DECISION',
                               '不可拆组无法在最少单数内排妥或尚未证明可行：' + packing + '；须核对分组，不能突破50张'))
    sequences = document.get('export_sequences', [])
    if not isinstance(sequences, list):
        sequences = []
    if len(sequences) != len(bins):
        export_issues.append(issue(prefix + '.export_sequences', 'EXPORT_SEQUENCES_REQUIRED', '须按实际拆单数给明确序号；不会自行编造或延续旧编号'))
    forms = []
    for n, groups_in_bin in enumerate(bins):
        expense_indexes = sorted(x for group in groups_in_bin for x in group['expense_indexes'])
        selected = [evaluated[x] for x in expense_indexes]
        invoice_ids = list(dict.fromkeys(iid for x in selected for iid in x['invoice_ids']))
        base = (folder + ('（' + chinese_number(n + 1) + '）' if len(bins) > 1 else '')) if text(folder) else None
        summary = (base + '；' + contact_text if policy.get('contact_in_summary') else base) if base and contact else None
        remarks = contact_text if policy.get('contact_in_summary') is False else None
        sequence = sequences[n] if n < len(sequences) else None
        valid_sequence = ((isinstance(sequence, int) and not isinstance(sequence, bool) and sequence > 0)
                          or (isinstance(sequence, str) and bool(re.fullmatch(r'\d+', sequence)) and int(sequence) > 0))
        local_export = []
        if not valid_sequence:
            local_export.append(issue(prefix + f'.export_sequences[{n}]', 'SEQUENCE_UNKNOWN', '须确认该报销单导出文件的序号'))
        path, filename, absolute = None, None, None
        if valid_sequence and summary and date_value:
            filename = safe_filename(str(sequence) + '_' + summary) + '.pdf'
            path = date_value + '/' + filename
            collision_key = unicodedata.normalize('NFC', path).casefold()
            if collision_key in used_paths:
                local_export.append(issue(prefix + f'.forms[{n}].export', 'EXPORT_NAME_COLLISION', '本批导出计划重名，须调整明确序号；禁止覆盖'))
            else:
                used_paths.add(collision_key)
            if export_root is not None:
                target = safe_path(export_root, path)
                absolute = str(target)
                parent_dir = target.parent
                if target.exists() or (parent_dir.exists() and any(x.name.casefold() == filename.casefold() for x in parent_dir.iterdir())):
                    local_export.append(issue(prefix + f'.forms[{n}].export', 'EXPORT_ALREADY_EXISTS', '目标已有同名文件，禁止覆盖；须明确新版本/序号'))
        export_issues.extend(local_export)
        amount_ready = all(x['status'] == 'ready' for x in selected)
        claims = _sum(amount(x['claim_amount'], x['expense_id']) for x in selected)
        payment_rows = [copy.deepcopy(p) for x in selected for p in x['payments']]
        form_issues = [x for expense in selected for x in expense['issues']] + local_export
        forms.append({'form_index': n + 1, 'expense_ids': [x['expense_id'] for x in selected],
                      'invoice_ids': invoice_ids, 'invoice_count': len(invoice_ids),
                      'invoice_face_total': money(_sum(amount(invoices[iid]['face_amount'], iid) for iid in invoice_ids)),
                      'claim_amount': money(claims) if amount_ready else None, 'candidate_claim_total': money(claims),
                      'summary_base': base, 'summary': summary, 'remarks': remarks,
                      'contact_source_refs': contact['source_refs'] if contact else [],
                      'payments': payment_rows, 'amount_status': 'ready' if amount_ready else 'blocked',
                      'export': {'business_date': date_value, 'sequence': sequence if valid_sequence else None,
                                 'filename': filename, 'relative_path': path, 'absolute_path': absolute,
                                 'overwrite_allowed': False, 'pdf_generated': False}, **_issues_result(form_issues)})
    flattened = [iid for form in forms for iid in form['invoice_ids']]
    if forms and (len(flattened) != len(set(flattened)) or set(flattened) != set(used)):
        raise AssertionError('拆单不变量失败：选用票重复或遗漏')
    all_issues = structure + money_issues + export_issues
    for form in forms:
        if structure or export_issues:
            form.update(_issues_result(form['issues'] + structure + export_issues))
        if structure:
            form['claim_amount'] = None
            form['amount_status'] = 'blocked'
    return {'document_id': identifier, 'original_folder_summary': folder, 'business_date': date_value,
            'contact_fields': {key: copy.deepcopy(clarification.get(key)) for key in ('responsible_person', 'form_filler')},
            'selected_contact': contact,
            'unique_invoice_count': len(used), 'excluded_invoice_ids': sorted(excluded),
            'minimum_form_count': math.ceil(len(used) / 50) if used else 0,
            'packing_method': packing, 'balance_optimal_proven': bool(bins) and all(len(x['invoice_ids']) == 1 for x in groups),
            'structure_readiness': _issues_result(structure), 'amount_readiness': _issues_result(money_issues),
            'export_readiness': _issues_result(export_issues), 'invoices': list(invoices.values()),
            'expenses': evaluated, 'forms': forms, 'warnings': warnings,
            'candidate_claim_total': money(_sum(amount(x['claim_amount'], x['expense_id']) for x in evaluated)),
            'claim_amount': money(_sum(amount(x['claim_amount'], x['expense_id']) for x in evaluated)) if not money_issues and not structure else None,
            **_issues_result(all_issues)}


def build_plan(data, export_root=None):
    """直接接受归一化对象，保留字段级阻塞；ready仅指离线计划资料齐备。"""
    if not isinstance(data, dict) or data.get('schema') != SCHEMA:
        raise ValueError('须提供schema=' + SCHEMA + '的归一化对象')
    if not text(data.get('case_id')):
        raise ValueError('case_id必须明确，不采用默认旧案')
    if export_root is not None:
        export_root = Path(export_root).resolve(strict=True)
        if not export_root.is_dir():
            raise ValueError('export_root须为已有授权资料目录；规划不创建目录')
    policy, policy_issues = _policy(data)
    rows = data.get('documents', [])
    if not isinstance(rows, list):
        raise ValueError('documents须为材料组数组，一份明白纸可引用多张照片')
    documents, used_paths, ids, input_issues = [], set(), set(), list(policy_issues)
    if not rows:
        input_issues.append(issue('documents', 'DOCUMENTS_REQUIRED', '尚未建立明白纸与电子材料组对应关系'))
    for n, document in enumerate(rows):
        if not isinstance(document, dict):
            input_issues.append(issue(f'documents[{n}]', 'INVALID_DOCUMENT', '材料组须为对象'))
            continue
        if text(document.get('document_id')) and document['document_id'] in ids:
            input_issues.append(issue(f'documents[{n}].document_id', 'DUPLICATE_DOCUMENT', '材料组编号重复，不能重复申请'))
            continue
        if text(document.get('document_id')):
            ids.add(document['document_id'])
        result = _document(document, policy, f'documents[{n}]', export_root, used_paths)
        documents.append(result)
    issues = input_issues + [x for d in documents for x in d['issues']]
    # 同批跨文件夹的同票也只可申请一次；不按文件夹不同而默许重复。
    selected_numbers = {}
    for n, document in enumerate(documents):
        active = {iid for expense in document['expenses'] for iid in expense['invoice_ids']}
        for invoice in document['invoices']:
            number = invoice.get('invoice_number')
            if invoice['invoice_id'] in active and text(number):
                if number in selected_numbers:
                    problem = issue(f'documents[{n}].invoices', 'CROSS_DOCUMENT_DUPLICATE', '同票在不同材料组重复选用，须明确唯一费用归属')
                    issues.append(problem)
                    for index in (selected_numbers[number], n):
                        affected = documents[index]
                        affected.update(_issues_result(affected['issues'] + [problem]))
                        affected['claim_amount'] = None
                        affected['structure_readiness'].update(_issues_result(affected['structure_readiness']['issues'] + [problem]))
                        for form in affected['forms']:
                            form.update(_issues_result(form['issues'] + [problem]))
                            form['claim_amount'] = None
                            form['amount_status'] = 'blocked'
                selected_numbers[number] = n
    finance = _finance_readiness(data.get('case_context', {}), policy)
    issues.extend(finance['issues'])
    return {'schema': 'ustc-reimbursement-batch-plan-v1', 'case_id': data['case_id'],
            'input_sha256': sha(encoded(data)), 'policy_source_refs': policy.get('source_refs', []),
            'documents': documents, 'finance_readiness': finance,
            'structure_readiness': _issues_result(input_issues + [x for d in documents for x in d['structure_readiness']['issues']]),
            'amount_readiness': _issues_result([x for d in documents for x in d['amount_readiness']['issues']]),
            'export_readiness': _issues_result([x for d in documents for x in d['export_readiness']['issues']]),
            'system_write': False, 'finance_write_authorized': False, 'pdf_generated': False,
            **_issues_result(issues)}


def write_plan(plan, output):
    """派生计划拒绝覆盖；不写任何财务PDF或原材料。"""
    output = Path(output)
    with output.open('xb') as handle:
        handle.write(encoded(plan))
    return {'path': str(output.resolve()), 'sha256': sha(output.read_bytes()), 'system_write': False}


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--export-root', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = build_plan(read_json(args.input), args.export_root)
    if args.output:
        result['plan_file'] = write_plan(result, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
