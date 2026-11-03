"""从完整本人清单提取财务反馈；保留原文，不改变学校状态。"""
from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

from finance_bill_state import index_bills

SCHEMA = 'ustc-finance-feedback-v1'
LABELS = {'finance_returned': '财务挂单', 'approval_returned': '审批退回',
          'history': '历史意见', 'state_unverified': '状态待核'}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {'br', 'p', 'div', 'li', 'tr'}:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag.lower() in {'p', 'div', 'li', 'tr'}:
            self.parts.append('\n')

    def handle_data(self, data):
        self.parts.append(data)


def text(value):
    """普通文字原样保留；只将真实展示标签转成换行，不执行原HTML。"""
    if not isinstance(value, str):
        raise ValueError('财务反馈字段必须为文本，不能将未知对象冒作原文')
    if re.search(r'</?(?:br|p|div|span|li|tr|td|b|strong|font)\b', value, re.I):
        parser = _Text()
        parser.feed(value)
        return ''.join(parser.parts).replace('\r\n', '\n').strip('\n')
    return html.unescape(value).replace('\r\n', '\n')


def classify(row):
    status, finish = row.get('t.status'), row.get('t.lc_finish')
    if status == '-15' and finish == 'T':
        return 'finance_returned'
    if status == '-3' and finish == 'F':
        return 'approval_returned'
    if ((status in {'10', '30'} and finish == 'T') or
            (status in {'-99', '-2'} and finish == 'F')):
        return 'history'
    return 'state_unverified'


def project(snapshot, userid, source, serials=None, include_history=False):
    if type(include_history) is not bool:
        raise ValueError('include_history须为布尔值')
    indexed = index_bills(snapshot, userid)
    observed = snapshot['observed_at']
    try:
        dt = datetime.fromisoformat(observed.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError('清单必须有带时区的采集时间') from None
    if (not isinstance(source, dict) or not re.fullmatch(r'[a-f0-9]{64}', source.get('sha256', ''))
            or not source.get('job_id') or not source.get('artifact_id')):
        raise ValueError('反馈须保留正式源任务、artifact及SHA')
    if serials is not None:
        if (not isinstance(serials, list) or not serials or
                any(not isinstance(s, str) or not re.fullmatch(r'[0-9]{1,20}', s) for s in serials)
                or len(serials) != len(set(serials)) or not set(serials) <= set(indexed)):
            raise ValueError('所选单号须唯一且完整存在于本次本人清单')
        scope = set(serials)
    else:
        scope = set(indexed)
    rows, counts = [], dict.fromkeys(LABELS, 0)
    missing, no_feedback, omitted_history = [], 0, 0
    for serial, native in indexed.items():
        if serial not in scope:
            continue
        reason = native.get('t.process_opinion')
        field_present = 't.process_opinion' in native
        if reason is not None and not isinstance(reason, str):
            raise ValueError('学校返回的意见字段不是文本')
        kind = classify(native)
        active = kind in {'finance_returned', 'approval_returned'}
        if not reason and not active:
            # 未提供意见列和明确的空意见是不同情况。
            if not field_present:
                missing.append(serial)
            else:
                no_feedback += 1
            continue
        counts[kind] += 1
        if kind == 'history' and not include_history:
            omitted_history += 1
            continue
        if not reason:
            missing.append(serial)
        rows.append({'serial': serial, 'summary': text(native.get('t.abst', '')),
                     'amount_yuan': native.get('t.total_amt'),
                     'status': text(native.get('t.status_name', '')),
                     'status_code': native.get('t.status'), 'category': kind,
                     'category_label': LABELS[kind], 'reason_original': reason,
                     'reason_text': text(reason) if reason else '',
                     'reason_available': bool(reason), 'reason_field_present': field_present,
                     'observed_at': observed})
    result = {'schema': SCHEMA, 'ok': True, 'system_write': False,
              'account_scope': 'current_applicant', 'expected_userid': userid,
              'observed_at': observed, 'source': dict(source), 'total_bills': len(indexed),
              'selected_bills': len(scope), 'serials': serials, 'include_history': include_history,
              'counts': counts, 'omitted_history_count': omitted_history,
              'no_feedback_count': no_feedback, 'missing_reason_serials': missing,
              'feedback_complete': not missing, 'rows': rows}
    result['copy_text'] = copy_text(result)
    return result


def copy_text(report):
    parts = []
    for row in report['rows']:
        reason = row['reason_text'] if row['reason_available'] else '系统未返回理由，待核实'
        parts.append(f"报销单：{row['serial']}\n事项：{row['summary']}\n"
                     f"金额：{row['amount_yuan']}元\n状态：{row['status']}（{row['category_label']}）\n"
                     f"财务反馈原文：\n{reason}\n采集时间：{row['observed_at']}")
    return '\n\n'.join(parts) if parts else '本次所选范围未发现待处理反馈。'


def checklist_plan(report, report_path):
    """长原文分段写入Excel，防止单元格长度上限静默丢字。"""
    detail = []
    for row in report['rows']:
        original = row['reason_text'] or '系统未返回理由，待核实'
        chunks = [original[i:i+30000] for i in range(0, len(original), 30000)]
        for i, chunk in enumerate(chunks):
            detail.append({'单号': row['serial'], '事项': row['summary'],
                           '申请金额': str(row['amount_yuan']).replace(',', ''), '当前状态': row['status'],
                           '分类': row['category_label'], '原文分段': f'{i+1}/{len(chunks)}',
                           '反馈原文': chunk, '采集时间': row['observed_at']})
    summary = [
        {'项目': '本次完整本人清单', '数量': report['total_bills'], '说明': '数量来自完整分页读取'},
        {'项目': '财务挂单', '数量': report['counts']['finance_returned'], '说明': '与历史意见分开'},
        {'项目': '审批退回', '数量': report['counts']['approval_returned'], '说明': '不与财务挂单混计'},
        {'项目': '理由未取得', '数量': len(report['missing_reason_serials']), '说明': '、'.join(report['missing_reason_serials']) or '无'},
        {'项目': '未列入的历史意见', '数量': report['omitted_history_count'], '说明': '按本次选择保留在来源记录'},
    ]
    return {'schema': 'ustc-checklist-delivery-v1', 'case_id': report['source']['case_id'],
            'system_write': False, 'note': '反馈原文与学校状态分别保留，长原文按分段顺序阅读。',
            'sources': [{'path': str(Path(report_path).resolve()), 'sha256': hashlib.sha256(Path(report_path).read_bytes()).hexdigest()}],
            'sheets': [
                {'name': '问题总结', 'columns': [{'key': k, 'type': 'number' if k == '数量' else 'text'} for k in ['项目', '数量', '说明']], 'rows': summary},
                {'name': '财务反馈原文', 'rowHeight': 220, 'columns': [{'key': k, 'type': 'money' if k == '申请金额' else 'text', 'width': 65 if k == '反馈原文' else 26}
                    for k in ['单号', '事项', '申请金额', '当前状态', '分类', '原文分段', '反馈原文', '采集时间']], 'rows': detail},
            ]}


def export(report, output_dir):
    from material_intake import reject_links
    from assistant_api import atomic_json
    output = Path(output_dir)
    reject_links(output)
    # 输出目录独占创建；未知/中断导出保留现场，不覆写已有证据。
    output.mkdir(parents=False, exist_ok=False)
    atomic_json(output/'导出状态.json', {'status': 'preparing', 'schema': SCHEMA})
    target = output/'财务反馈汇总.json'
    atomic_json(target, report)
    (output/'财务反馈原文.txt').write_text(report['copy_text'], encoding='utf-8')
    template = Path(__file__).with_name('finance_feedback_ui.html').read_text(encoding='utf-8')
    payload = json.dumps(report, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    (output/'财务反馈汇总.html').write_text(template.replace('/*REPORT_DATA*/{}', payload), encoding='utf-8')
    (output/'财务反馈汇总.js').write_bytes(Path(__file__).with_name('finance_feedback_ui.js').read_bytes())
    atomic_json(output/'Excel交付计划.json', checklist_plan(report, target))
    from checklist_native import render
    excel = render(output/'Excel交付计划.json', output/'财务挂单理由汇总.xlsx')
    files = [{'name': p.name, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'bytes': p.stat().st_size}
             for p in sorted(output.iterdir()) if p.name != '导出状态.json']
    atomic_json(output/'导出状态.json', {'status': 'completed', 'schema': SCHEMA, 'files': files,
                'system_write': False, 'excel_status': 'exported_and_readback_verified'})
    return {'directory': str(output), 'files': files, 'excel_status': 'exported_and_readback_verified','excel':excel}


def run(service, case_id, source_job_id, serials=None, include_history=False, output_dir=None, execute=False):
    from assistant_contract import APIError
    if type(execute) is not bool:
        raise APIError('feedback_execute', 'execute须为布尔值')
    result = service.result(case_id, source_job_id)
    if result.get('status') != 'completed' or result.get('exit_code') != 0:
        raise APIError('feedback_source', '须引用已完成的完整本人清单任务；先查原result')
    artifacts = [a for a in result.get('artifacts', []) if a.get('name') == 'output.json']
    if len(artifacts) != 1:
        raise APIError('feedback_source', '完整清单须有唯一正式JSON回执')
    artifact = artifacts[0]
    path, envelope, request = service._typed_case_artifact(case_id, source_job_id, artifact['artifact_id'], 'business', ['bills-list'])
    native = envelope.get('result', {})
    if (envelope.get('phase') != 'script_completed' or request.get('execute') is not False
            or native.get('case_id') != case_id or native.get('system_write') is not False):
        raise APIError('feedback_source', '须同案、只读、已完成的正式本人清单回执')
    report = project(native, request['parameters']['expected_userid'],
        {'case_id': case_id, 'job_id': source_job_id, 'artifact_id': artifact['artifact_id'],
         'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}, serials, include_history)
    if output_dir is not None:
        from material_intake import reject_links
        if not isinstance(output_dir, str) or not Path(output_dir).is_absolute():
            raise APIError('feedback_output', '导出使用授权资料目录内的绝对新目录')
        reject_links(Path(output_dir))
        service.material_path(str(Path(output_dir).parent))
        if Path(output_dir).exists():
            raise APIError('feedback_output_exists', '导出目录已存在；保留原结果，不覆盖')
        report['export_plan'] = {'output_dir': output_dir, 'local_write': execute, 'system_write': False}
        if execute:
            report['export'] = export(report, output_dir)
    elif execute:
        raise APIError('feedback_output', 'execute只用于明确的新目录导出；读取汇总无需执行标志')
    return report
