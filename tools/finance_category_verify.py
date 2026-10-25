"""核对原生报销项；经费和金额相同并不表示科目正确。"""
from __future__ import annotations

import re
from decimal import Decimal


_CONTROL = re.compile(r"^pageOpen_v1\('发票信息','(.+?)!P![^&']*&([^&']+)&([^&']+)&([^']+)'\)$")


def category_rows(frame):
    result = []
    rows = [row for table in frame.get('tables', []) for row in table
            if isinstance(row, list) and len(row) == 5 and all(isinstance(v, str) for v in row)]
    for control in frame.get('controls', []):
        match = _CONTROL.fullmatch(control.get('onclick', ''))
        if not match:
            continue
        group, subject, project, budget = (v.strip() for v in match.groups())
        matches = [row for row in rows if row[1] == control.get('text', '').strip()
                   and re.match(re.escape(group) + r'(?:\s|,|，|$)', row[0]) and row[2].startswith(project + '/')
                   and re.fullmatch(r'[\d,]+\.\d{2}', row[4])]
        if len(matches) != 1:
            raise ValueError('报销项不能唯一对应原生费用行：' + group)
        row = matches[0]
        result.append({'group': group, 'subject': subject, 'subject_name': row[1],
                       'project': project, 'budget': budget,
                       'amount': str(Decimal(row[4].replace(',', ''))), 'invoice_content': row[0]})
    if len({r['group'] for r in result}) != len(result):
        raise ValueError('原生报销项分组重复，不能按整组核对')
    return result


def verify_categories(form, frame):
    """只核本案有来源的明确分类，不推断所有学生活动都用同一个科目。"""
    plans = form.get('expense_categories', [])
    if not plans:
        raise ValueError('本案尚无已冻结的报销项目分类，不能登记分类核对通过')
    actual = category_rows(frame)
    funding = {f['code'] for f in form['funding']}
    for plan in plans:
        hits = [r for r in actual if r['group'] == plan['group']]
        if len(hits) != 1:
            raise ValueError('分类保存后缺少唯一原生分组：' + plan['group'])
        row = hits[0]
        if (row['subject'] != plan['subject'] or row['subject_name'] != plan['subject_name']
                or row['project'] not in funding or Decimal(row['amount']) != Decimal(plan['amount'])):
            raise ValueError('分类保存后科目代码、名称、经费或金额不符：' + plan['group'])
    return actual


def review_material_prompt(form, frame, gaps):
    """先把分类待核和真实补件分开；提示消失不作为分类正确的依据。"""
    if not gaps:
        return {'state': 'no_material_prompt', 'review_required': False}
    rows = category_rows(frame)
    planned = {p['group'] for p in form.get('expense_categories', [])}
    if planned:
        verify_categories(form, frame)
    unchecked = [r for r in rows if r['group'] not in planned]
    required = bool(unchecked) or not rows
    return {'state': 'category_review_required' if required else 'category_verified_materials_still_required',
            'review_required': required, 'categories': rows, 'unchecked_groups': unchecked,
            'next_action': '核对原票用途及原生科目候选，冻结分类后回读材料要求' if required else '处理仍存在的材料要求'}
