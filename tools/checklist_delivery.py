"""将既有报销清单输出交付为Excel表格；不重新识别、计算报销额或操作财务。"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

SCHEMA = 'ustc-checklist-delivery-v1'
PROFILES = {
    'workspace': [
        ('费用与材料', '02_台账/费用与材料.csv'),
        ('纸质材料', '02_台账/纸质材料定位.csv'),
        ('问题事项', '02_台账/待核事项.csv'),
        ('票版明细', '02_台账/全部票版.csv'),
        ('材料来源', '02_台账/全部材料页.csv'),
    ],
    'formal': [
        ('打款人', '01_打款人清单.csv'),
        ('报销材料', '02_报销材料明细.csv'),
        ('纸质材料', '03_纸质材料清单.csv'),
        ('付款对应', '05_付款凭证逐票核对.csv'),
        ('来源覆盖', '04_原始材料覆盖核对.csv'),
    ],
    'readiness': [
        ('人员核对', '按材料归属人员汇总.csv'),
        ('问题费用', '问题费用.csv'),
    ],
}
MONEY_HEADERS = {
    '票面金额', '票面价税合计', '实际付款金额', '本轮金额核算基数',
    '原清单小计', '原清单全部明细', '去重后按目录有票核算基数',
    '原小计差额', '无票单列登记额', '原表明细金额', '该行原表小计',
    'invoice_face_amount', 'candidate_amount', 'receivable_amount',
    'invoice_face_amount_yuan', 'candidate_amount_yuan', 'receivable_amount_yuan',
    'group_face_amount_subtotal_not_claim', 'group_face_amount_not_claim',
    'auxiliary_material_amount_not_invoice', 'declared_claim_amount', '涉及金额',
}

ISSUE_COLUMNS = ['问题编号', '状态', '人员/费用', '涉及金额', '问题', '已确认依据', '下一步']
ISSUE_NOTE = '问题金额可能相互重叠、表示差额或无票登记额，不得相加作为申请额。未登记问题不表示材料齐全；事实、推测和待核事项分别说明。'


def ensure_issue_summary(plan, extra_issues=()):
    """提取已有问题记录作为首表，不推定解决状态、金额或处理决定。"""
    if any(s['name'] == '问题总结' for s in plan['sheets']):
        summary = next(s for s in plan['sheets'] if s['name'] == '问题总结')
        plan['sheets'] = [summary] + [s for s in plan['sheets'] if s is not summary]
        return plan
    rows = []
    for sheet in plan['sheets']:
        if sheet['name'] not in {'问题与处理', '问题事项', '问题费用'}:
            continue
        for r in sheet['rows']:
            def pick(*keys, default=''):
                return next((r[k] for k in keys if r.get(k) not in (None, '')), default)
            rows.append(dict(zip(ISSUE_COLUMNS, [
                pick('问题编号', '编号', 'expense_id'),
                pick('状态', 'readiness_label', '优先级', default='待核'),
                pick('对象', '人员', 'material_owner'),
                pick('涉及金额', 'group_face_amount_not_claim', default=None),
                pick('问题', '主题', 'pending_review_reasons'),
                pick('已证实依据', '已确认依据', '已知', 'material_notes'),
                pick('处理方式', '下一步', default='处理动作未登记，需沿原问题依据核定。'),
            ])))
    rows.extend(extra_issues)
    summary = dict(name='问题总结', title='问题总结：先看差异和处理动作', note=ISSUE_NOTE,
                   columns=[column_spec(c) for c in ISSUE_COLUMNS], rows=rows)
    plan['sheets'].insert(0, summary)
    return plan


def write_issue_summary(plan, output):
    """交付独立可读总结，已有同内容可复用，差异不覆盖。"""
    summary = next(s for s in plan['sheets'] if s['name'] == '问题总结')
    cols = summary['columns']
    def cell(value):
        return '' if value is None else str(value)
    def md(value):
        return html.escape(cell(value)).replace('|', '&#124;').replace('\r', '').replace('\n', '<br>')
    context = f"案件：{plan['case_id']}。{summary.get('note', ISSUE_NOTE)}"
    empty = '当前来源未登记问题明细；需核对原材料和办理差项，不能据此认定没有问题。'
    lines = [f"# {plan.get('title', '报销清单')}：问题总结", '', context, '']
    if summary['rows']:
        lines += ['| ' + ' | '.join(md(c.get('label', c['key'])) for c in cols) + ' |',
                  '|' + '|'.join('---' for _ in cols) + '|']
        lines += ['| ' + ' | '.join(md(r[c['key']]) for c in cols) + ' |' for r in summary['rows']]
    else:
        lines += [empty]
    lines += ['', '原记录与完整票号、费用明细保留在清单Excel和来源文件中；本总结不表示已建单、审批或付款。', '']
    markdown = '\n'.join(lines)
    headers = ''.join('<th>' + html.escape(c.get('label', c['key'])) + '</th>' for c in cols)
    body = ''.join('<tr>' + ''.join('<td>' + html.escape(cell(r[c['key']])).replace('\n', '<br>') + '</td>' for c in cols) + '</tr>' for r in summary['rows'])
    content = '<table><thead><tr>' + headers + '</tr></thead><tbody>' + body + '</tbody></table>' if body else '<p>' + empty + '</p>'
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>报销问题总结</title><style>body{font:16px/1.6 system-ui,sans-serif;margin:28px;color:#203044}table{border-collapse:collapse;width:100%}th,td{border:1px solid #d8e1e8;padding:12px;vertical-align:top}th{background:#294a68;color:white}tbody tr:nth-child(even){background:#f3f6f8}td:nth-child(2){font-weight:600}main{overflow:auto}h1{font-size:26px}</style><h1>' + html.escape(plan.get('title', '报销清单')) + '：问题总结</h1><p>' + html.escape(context) + '</p><main>' + content + '</main><p>完整票号及逐项材料沿清单Excel追溯。本总结不表示已建单、审批或付款。</p></html>'
    output = Path(output)
    paths = [(output / '问题总结.md', markdown), (output / '问题总结.html', page)]
    for path, text in paths:
        if path.exists() and path.read_text(encoding='utf-8') != text:
            raise ValueError('问题总结已有不同内容；使用新的交付目录，不覆盖原总结')
    for path, text in paths:
        if not path.exists():
            with path.open('x', encoding='utf-8') as stream:
                stream.write(text)
    return dict(markdown='问题总结.md', page='问题总结.html', rows=len(summary['rows']))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def column_spec(header):
    is_money = header in MONEY_HEADERS or header.endswith('/元')
    is_count = header in {'费用项数', '票版数', '票数', '序号', '原表行号'} or header.endswith('_count')
    width = 45 if any(s in header for s in ('说明', '问题', '依据', '备注', '状态', '行程', '内容', '限制', '方式')) else 30 if any(s in header for s in ('号', 'SHA', '路径', '来源')) else 22
    return dict(key=header, label=header, type='money' if is_money else 'number' if is_count else 'text', width=width)


def source_table(path, name, *, note=''):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        columns = reader.fieldnames
        if not columns or len(set(columns)) != len(columns):
            raise ValueError(f'清单列名为空或重复: {path}')
        rows = list(reader)
    if any(None in row or any(v is None for v in row.values()) for row in rows):
        raise ValueError(f'清单行的列数与表头不符: {path}')
    return dict(name=name, title=name, note=note, columns=[column_spec(c) for c in columns], rows=rows), dict(path=str(path.resolve()), sha256=digest(path))


def validate_manifest(plan):
    if plan.get('schema') != SCHEMA or not isinstance(plan.get('case_id'), str) or not plan['case_id'].strip():
        raise ValueError('须给明确case_id和清单交付schema')
    if plan.get('system_write') is not False:
        raise ValueError('表格交付不操作财务，system_write须为false')
    sheets = plan.get('sheets')
    if not isinstance(sheets, list) or not sheets:
        raise ValueError('须有非空表格清单')
    names = set()
    for table in sheets:
        name = table.get('name', '')
        if not name or len(name) > 31 or re.search(r'[\\/:*?\[\]]', name) or name.casefold() in names:
            raise ValueError('工作表名称非法或重复')
        names.add(name.casefold())
        cols = table.get('columns', [])
        keys = [c.get('key') for c in cols]
        if not keys or len(set(keys)) != len(keys) or any(not isinstance(k, str) or not k for k in keys):
            raise ValueError('表格字段为空或重复')
        if any(c.get('type') not in {'text', 'money', 'number'} for c in cols):
            raise ValueError('仅支持text/money/number字段')
        for row in table.get('rows', []):
            if not isinstance(row, dict) or set(row) != set(keys):
                raise ValueError('行字段与列定义不符；不得静默丢列')
            if any(v is not None and not isinstance(v, (str, int, float, bool)) for v in row.values()):
                raise ValueError('表格单元格须为标量或null')
            if any(isinstance(v, float) and not math.isfinite(v) for v in row.values()):
                raise ValueError('数值须有限；未知不能用NaN代替null')
    sources = plan.get('sources')
    if not isinstance(sources, list) or not sources:
        raise ValueError('须登记来源路径及SHA')
    for source in sources:
        path = Path(source['path'])
        if not path.is_absolute() or not path.is_file() or digest(path) != source['sha256']:
            raise ValueError('来源不存在或SHA变化；先重建清单交付计划')
    return plan


def prepare(source, profile, case_id, title, extra_issues=()):
    source = Path(source).resolve()
    metadata_name = {'workspace':'材料工作区输入.json', 'formal':'清单核对记录.json', 'readiness':'就绪度.json'}[profile]
    metadata_path = source / metadata_name
    metadata = json.loads(metadata_path.read_text(encoding='utf-8')) if metadata_path.exists() else {}
    if metadata_path.exists() and metadata.get('case_id') != case_id:
        raise ValueError('来源清单case_id不符；不能将其他案件表格标为本案')
    sources, sheets = [], []
    for name, filename in PROFILES[profile]:
        table, receipt = source_table(source / filename, name)
        sheets.append(table); sources.append(receipt)
    if profile == 'workspace':
        person_rows = defaultdict(lambda: {'费用项数': 0, '票版数': 0})
        for row in sheets[0]['rows']:
            person_rows[row['人员']]['费用项数'] += 1
            person_rows[row['人员']]['票版数'] += int(row['票版数'])
        rows = [dict(材料归属人员=p, **counts, 正式收款人='未核定', 正式申请额='未核定') for p, counts in person_rows.items()]
        sheets.insert(0, dict(name='人员核对', title='人员与收款信息核对', note='按材料归属汇总，不把材料姓名自动当作正式收款人。', columns=[column_spec(c) for c in ['材料归属人员', '费用项数', '票版数', '正式收款人', '正式申请额']], rows=rows))
    if profile == 'readiness':
        sheets.append(dict(name='纸质材料', title='纸质材料定位', note='此办理差项报告不含纸质逐页定位；沿本案材料工作区或正式材料清单查询，不等于没有纸质材料。', columns=[column_spec(c) for c in ['纸质材料', '定位状态']], rows=[]))
    plan = dict(schema=SCHEMA, case_id=case_id, title=title, profile=profile,
                note='从已有核对清单导出的快照；未知金额、实际收款人和审批状态保持原记录。',
                sources=sources, sheets=sheets, system_write=False)
    gaps = []
    for i, item in enumerate(metadata.get('form_readiness', {}).get('gaps', []), 1):
        gaps.append(dict(zip(ISSUE_COLUMNS, [f'办理差项{i}', item.get('state', '待核'), '整案', None,
            item.get('detail', ''), '来源：就绪度整案差项', '沿本案已确认信息补齐或核定，不以票面金额代填。'])))
    for i, item in enumerate(metadata.get('open_items', []), 1):
        gaps.append(dict(zip(ISSUE_COLUMNS, [f'待办{i}', '待核', '整案', None, item, '来源：正式清单待办', '沿原待办核实并记录处理结果。'])))
    return validate_manifest(ensure_issue_summary(plan, [*gaps, *extra_issues]))


def register_delivery(source, profile, case_id, title, *, published_source=None, extra_issues=()):
    """生成工具在发布时写出明确下一步；没有.xlsx就不标记已交付。"""
    source = Path(source).resolve()
    plan = prepare(source, profile, case_id, title, extra_issues)
    if published_source:
        published_source = Path(published_source).resolve()
        for item in plan['sources']:
            item['path'] = str(published_source / Path(item['path']).relative_to(source))
    with (source / '清单表格交付计划.json').open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(plan, ensure_ascii=False, indent=2) + '\n')
    issue_summary = write_issue_summary(plan, source)
    return dict(plan='清单表格交付计划.json', required_output='报销清单.xlsx', issue_summary=issue_summary,
                tool='checklist_delivery.py', workbook_created=False,
                instruction='先展示问题总结，再继续导出Excel并在回复展示汇总表；计划生成不等于表格已交付。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--source', type=Path, help='已有工具的输出目录')
    group.add_argument('--input', type=Path, help='已准备的ustc-checklist-delivery-v1 JSON')
    parser.add_argument('--profile', choices=PROFILES)
    parser.add_argument('--case-id')
    parser.add_argument('--title', default='报销清单')
    parser.add_argument('--output', required=True, type=Path, help='新的.xlsx文件；prepare-only时为新的JSON文件')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--node', default='node', help='现有Node运行时，不自动安装')
    parser.add_argument('--module-root', type=Path, help='已配置的node_modules根，或在当前任务目录准备node_modules链接')
    parser.add_argument('--qa-dir', type=Path, help='全新视觉核验目录；为每张表保存可见列的预览')
    args = parser.parse_args()
    try:
        if args.output.exists(): raise ValueError('输出已存在，不覆盖')
        if args.source:
            if not args.profile or not args.case_id: raise ValueError('来源目录模式须给profile和case-id')
            plan = prepare(args.source, args.profile, args.case_id, args.title)
        else:
            plan = validate_manifest(ensure_issue_summary(json.loads(args.input.read_text(encoding='utf-8'))))
        if args.prepare_only:
            if args.output.suffix != '.json': raise ValueError('准备计划输出须为.json')
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open('x', encoding='utf-8') as stream:
                stream.write(json.dumps(plan, ensure_ascii=False, indent=2) + '\n')
            print(json.dumps(dict(ok=True, manifest=str(args.output.resolve()), sheets=len(plan['sheets']), system_write=False), ensure_ascii=False))
            return
        if args.output.suffix != '.xlsx': raise ValueError('表格交付输出须为.xlsx')
        manifest = args.output.with_suffix('.delivery.json')
        if manifest.exists(): raise ValueError('交付计划回执已存在，不覆盖')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with manifest.open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(plan, ensure_ascii=False, indent=2) + '\n')
        write_issue_summary(plan, args.output.parent)
        command = [args.node, str(Path(__file__).with_suffix('.mjs')), '--input', str(manifest.resolve()), '--output', str(args.output.resolve()), '--python', sys.executable]
        if args.module_root: command += ['--module-root', str(args.module_root.resolve())]
        if args.qa_dir: command += ['--qa-dir', str(args.qa_dir.resolve())]
        result = subprocess.run(command, check=False)
        if result.returncode: parser.exit(result.returncode, 'Excel未生成；交付计划已保留，修复已配置运行时后沿原计划继续。\n')
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f'清单交付失败: {exc}\n')


if __name__ == '__main__':
    main()
