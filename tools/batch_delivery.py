"""Read-only delivery snapshots from registered native receipts and an explicit batch index.

No method in this module enqueues finance work, reads a live browser, acknowledges
a write, or changes a case. A separately supplied progress file may be replaced
only after its original bytes have been backed up and its SHA checked again.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
import zipfile
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import safe_xml as ET

from assistant_api import APIError, atomic_json, read_json
from batch_workflow import DraftBatch, _body, _json_path, _material_gaps, _partial_payment_gaps
from checklist_delivery import validate_manifest, write_issue_summary
from submission import verify_options
from reimbursement_batch_plan import safe_filename

STATUS_TEXT = {
    'draft_created': '已建立草稿；保存字段待核',
    'draft_filled_native_audit_passed': '已填草稿；原生字段与合同/打印选项已核',
    'draft_filled_system_material_gaps': '已填待补材料',
    'draft_filled_fields_verified_options_metadata_pending': '已填草稿；合同和打印选项待复核',
    'draft_partially_filled_missing_payment': '仅基本信息与经费已填；待补收款',
    'historical_fields_verified_current_task_pending': '历史保存字段已核；当前任务待回读',
}
OPTIONS_RECHECK_NOTE = '需读取当前原单核对合同和打印选项；已保存字段保留'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _verified_json(service, path, sha=None):
    path = _json_path(service, str(path))
    if sha and digest(path) != sha:
        raise APIError('delivery_source_sha', '交付来源SHA不符')
    return path, read_json(path)


def _index(service, path, scenario):
    path, index = _verified_json(service, path)
    if index.get('schema') not in {'ustc-reviewed-equal-amount-plan-index-v1',
            'ustc-reviewed-equal-amount-plan-index-v2', 'ustc-reviewed-batch-plan-index-v1'}:
        raise APIError('delivery_index', '须使用正式批次计划索引')
    rows, sequences = {}, set()
    for source in index.get('documents', []):
        row = copy.deepcopy(source)
        case = row.get('case_id') or row.get('proposed_case_id')
        service.case_id(case)
        sequence = row.get('index', row.get('sequence'))
        if type(sequence) is not int or sequence <= 0 or sequence in sequences or case in rows:
            raise APIError('delivery_mapping', '索引须有唯一明确的case及正整数序号')
        sequences.add(sequence)
        _, plan = _verified_json(service, row['official_plan'], row['official_plan_sha256'])
        _, normalized = _verified_json(service, row['normalized_input'], row['normalized_input_sha256'])
        selection = plan.get('rule_selection', {})
        if selection.get('case_id') != case or selection.get('status') != 'ready' or (scenario is not None and selection.get('profile_id') != scenario):
            raise APIError('delivery_scenario', '索引案件或指定专项与正式计划不同')
        if (normalized.get('case_id') != case or plan.get('input_evidence', {}).get('sha256') != row['normalized_input_sha256']):
            raise APIError('delivery_input', '正式计划与归一化材料SHA/案件未闭合')
        forms = [form for document in plan.get('plan', {}).get('documents', []) for form in document.get('forms', [])]
        if len(forms) != 1 or any(forms[0].get(key) != row.get(key) for key in ['summary', 'remarks', 'claim_amount']):
            raise APIError('delivery_plan', '索引与唯一正式表单的摘要、备注、金额不符')
        documents = normalized.get('documents', [])
        if (len(documents) != 1 or forms[0].get('summary_base') != documents[0].get('folder_name')
                or type(plan.get('effective_policy', {}).get('contact_in_summary')) is not bool
                or (plan['effective_policy']['contact_in_summary'] is False and forms[0]['summary'] != documents[0]['folder_name'])):
            raise APIError('delivery_folder', '正式摘要与本批归一化原目录或联系人分栏规则不符')
        if 'expected_pdf_filename_user_format' in row:
            expected = safe_filename(str(sequence) + ' ' + row['summary']) + '.pdf'
            if row['expected_pdf_filename_user_format'] != expected:
                raise APIError('delivery_pdf_filename', '明确PDF文件名须为本序号、空格及原摘要的安全文件名，不能含任意路径或改写摘要空格')
        if not row.get('invoice_numbers') or len(set(row['invoice_numbers'])) != len(row['invoice_numbers']):
            raise APIError('delivery_invoices', '索引须有唯一明确票号集合')
        invoices = {invoice['invoice_id']: invoice for document in plan['plan']['documents'] for invoice in document['invoices']}
        numbers = [invoices[invoice_id]['invoice_number'] for invoice_id in forms[0]['invoice_ids']]
        if set(numbers) != set(row['invoice_numbers']) or len(numbers) != len(row['invoice_numbers']):
            raise APIError('delivery_invoices', '索引完整票号集合与正式计划不同')
        row.update(case_id=case, sequence=sequence, planned=forms[0], profile_id=selection.get('profile_id'))
        rows[case] = row
    return path, rows


def _native(service, case, ref):
    folder = service.job_folder(case, ref['job_id'])
    status, request = read_json(folder/'status.json'), read_json(folder/'request.json')
    if (status.get('status') != 'completed' or status.get('exit_code') != 0
            or request.get('case_id') != case or request.get('operation') != 'read_system'
            or request.get('execute') is not False):
        raise APIError('delivery_native_job', '须为本案正式完成的独立只读read_system回执')
    path = service.artifact(case, ref['job_id'], ref['artifact_id'])
    sha = digest(path)
    if ref.get('sha256') and ref['sha256'] != sha:
        raise APIError('delivery_native_sha', '引用的native_evidence与正式artifact SHA不同')
    native = _body(read_json(path))
    if native.get('ok') is not True:
        raise APIError('delivery_native_failed', '独立原生回读未成功')
    return native, {**ref, 'path': str(path), 'sha256': sha,
                    'finished_at_utc': status.get('finished_at_utc') or ''}


def _frame(native, form, row, userid, *, saved=True):
    serial = form.get('serial')
    if (not isinstance(serial, str) or not re.fullmatch(r'\d+', serial)
            or native.get('serial') != serial or native.get('projid') != 'WF_YBX'
            or native.get('identity', {}).get('userid') != userid or native.get('visibleDialogs')):
        raise APIError('delivery_identity', '原生同号、系统、当前身份或dialog不符')
    frames = [frame for frame in native.get('frames', []) if frame.get('id') == 'iframepage_print'
              and '报销单号：'+serial in frame.get('visibleText', '')]
    if len(frames) != 1:
        raise APIError('delivery_frame', '原生同号预览不唯一')
    frame = frames[0]
    if saved and frame.get('proof_inventory', {}).get('document_ready_state') in ['loading', 'interactive']:
        raise APIError('delivery_loading', '原生预览尚未完成加载')
    text = frame['visibleText']; tight = re.sub(r'\s+', '', text).replace(',', '')
    amount = Decimal(form['amount'])
    total = re.search(r'金额总计[：:]\s*([\d,]+\.\d{2})', text)
    face_total = Decimal(row['planned'].get('invoice_face_total', row['claim_amount']))
    expected = '共选择'+str(len(row['invoice_numbers']))+'张发票合计'+format(face_total, '.2f')+'元'
    if (total is None or Decimal(total.group(1).replace(',', '')) != amount or expected not in tight
            or any(number not in text for number in row['invoice_numbers'])):
        raise APIError('delivery_amount', '原生金额、选票数或本单完整票号未闭合')
    return frame


def _pending(service, case, paths):
    path = paths['state'].parent/'pending_write.json'
    return (read_json(path) if path.exists() else None), service.active_jobs(case)


def _saved(service, record, row, userid, source):
    case = row['case_id']; _, paths = service.resolve(case)
    form_sha = digest(paths['form']); form, state = read_json(paths['form']), read_json(paths['state'])
    if record.get('case_id') != case or record.get('serial') != form.get('serial') or Decimal(record['amount']) != Decimal(form['amount']):
        raise APIError('delivery_case', '保存记录与当前案件、单号或金额不符')
    if record.get('form_sha256') and record['form_sha256'] != form_sha:
        raise APIError('delivery_form_sha', '保存完成记录后表单SHA已改变')
    if Decimal(form['amount']) != Decimal(row['claim_amount']):
        raise APIError('delivery_claim', '本案金额与正式计划不同')
    fields = form.get('basic', {}).get('fields', {})
    if fields.get('t-abst') != row['summary'] or fields.get('t-cl_abst', '') != (row.get('remarks') or ''):
        raise APIError('delivery_summary', '当前摘要或备注与正式计划不同')
    native, ref = _native(service, case, record['native_evidence'])
    frame = _frame(native, form, row, userid)
    partial = record.get('status') == 'draft_partially_filled_missing_payment'
    groups = ['basic', 'funding', 'payments_empty'] if partial else ['basic', 'funding', 'payments']
    required = {'basic', 'funding'} if partial else {'basic', 'funding', 'payments', 'audit'}
    fresh_route = False
    if not partial and record.get('verification_method') == 'read_only_existing_draft':
        request = read_json(service.job_folder(case, ref['job_id'])/'request.json')
        params = request.get('parameters', {})
        if (params.get('mode') != 'open-draft' or params.get('serial') != form['serial']
                or params.get('expected_userid') != userid or record.get('form_sha256') != form_sha
                or record.get('verified_field_groups') != groups or record.get('options_verified') is not True):
            raise APIError('delivery_fresh_route', '完整草稿核验记录未绑定正式同号开单回读')
        # This route independently checks the whole preview without creating a
        # legacy audit-stage acknowledgement. Recheck the native evidence here.
        verify_options(native, form['serial'])
        if any(p['id'] + '(' + p['name'] + ')' not in frame['visibleText'] for p in form.get('pre_review', [])):
            raise APIError('delivery_pre_review', '完整草稿预审人未独立核实')
        fresh_route = True
    if not fresh_route and not required.issubset(set(state.get('operator_verified_stages', []))):
        raise APIError('delivery_stages', '本案缺少已独立核验的字段阶段登记')
    if partial and (not _partial_payment_gaps(form, row['planned'])
                    or record.get('verified_stages') != ['funding', 'basic'] or not record.get('deferred_inputs')):
        raise APIError('delivery_partial', 'partial须为正式全对公、金额闭合、支付明确空白且仅经费/基本信息')
    for group in groups:
        DraftBatch.verify_stage(SimpleNamespace(service=service), case, group, frame)
    options, option_gap = None, None
    if not partial:
        try: options = verify_options(native, form['serial'])
        except ValueError as error: option_gap = str(error)
    gaps = _material_gaps(frame)
    pending, active = _pending(service, case, paths)
    if digest(paths['form']) != form_sha:
        raise APIError('delivery_changed', '本轮核验期间表单变化，须下一快照重查')
    status = ('draft_partially_filled_missing_payment' if partial else
              'draft_filled_system_material_gaps' if gaps else
              'draft_filled_native_audit_passed' if options else
              'draft_filled_fields_verified_options_metadata_pending')
    if pending or active: status = 'historical_fields_verified_current_task_pending'
    return {'case_id': case, 'sequence': row['sequence'], 'document_id': row.get('document_id'),
            'serial': form['serial'], 'amount': form['amount'], 'status': status,
            'source_reported_status': record.get('status'), 'record_source': source, 'summary': row['summary'],
            'form_sha256': form_sha, 'verified_field_groups': groups, 'partial': partial,
            'deferred_inputs': record.get('deferred_inputs', []), 'system_evidence_gaps': gaps,
            'options_verified': options is not None, 'options_verification_gap': option_gap,
            'native_evidence': ref, 'pending': pending, 'active_jobs': active,
            'currently_processing': bool(pending or active), 'workflow_started': None,
            'approval_completed': None, 'payment_completed': None, 'pdf_exported': None}


def _created(service, row, userid, form, state):
    target = state.get('creation_readback'); matches = []
    if not target: raise APIError('delivery_creation', '本地单号缺正式原建单回读')
    for path in (service.jobs/row['case_id']).glob('*/status.json'):
        for ref in read_json(path).get('artifacts', []):
            if Path(ref['path']).resolve() == Path(target).resolve():
                matches.append({**ref, 'job_id': path.parent.name})
    if len(matches) != 1: raise APIError('delivery_creation', '建单回读未唯一注册到本案artifact')
    native, ref = _native(service, row['case_id'], matches[0])
    frame = _frame(native, form, row, userid, saved=False)
    return {'case_id': row['case_id'], 'sequence': row['sequence'], 'serial': form['serial'],
            'amount': form['amount'], 'status': 'draft_created', 'native_evidence': ref,
            'source_preview_ready_state': frame.get('proof_inventory', {}).get('document_ready_state')}


def collect_delivery(service, *, batch_index, expected_userid, scenario=None, legacy_evidence=()):
    """Return a pure local snapshot. Legacy records must be explicitly supplied."""
    if not isinstance(expected_userid, str) or not expected_userid.strip():
        raise APIError('delivery_user', '须显式给本轮已确认expected_userid')
    index_path, rows = _index(service, batch_index, scenario)
    started = stamp(); documents = {}; rejected = []; candidates = []
    for case, row in rows.items():
        for name in ['completed.json', 'partial.json']:
            for path in sorted((service.root/'.local/batch_workflows').glob('*/'+case+'/'+name)):
                candidates.append((read_json(path), str(path)))
    for record in legacy_evidence:
        record = copy.deepcopy(record)
        if record.get('case_id') not in rows:
            raise APIError('delivery_legacy_scope', '显式legacy证据必须属于当前索引映射')
        if not record.get('native_evidence') and record.get('job_id') and record.get('artifact_id'):
            case = record['case_id']; _, paths = service.resolve(case); form = read_json(paths['form'])
            gaps = _partial_payment_gaps(form, rows[case]['planned'])
            record.update(serial=form.get('serial'), amount=form.get('amount'),
                          native_evidence={key: record[key] for key in ['job_id', 'artifact_id', 'sha256'] if key in record},
                          status='draft_partially_filled_missing_payment' if gaps else 'explicit_legacy_readback')
            if gaps: record.update(verified_stages=['funding', 'basic'], deferred_inputs=gaps)
        if not record.get('native_evidence'):
            raise APIError('delivery_legacy_ref', 'legacy须显式给正式job/artifact引用，不能推测')
        candidates.append((copy.deepcopy(record), 'explicit_legacy_evidence'))
    for record, source in candidates:
        case = record.get('case_id')
        try:
            if case not in rows: raise APIError('delivery_case', '完成记录case不属于所在索引')
            item = _saved(service, record, rows[case], expected_userid, source)
            prior = documents.get(case)
            if prior and prior['serial'] != item['serial']:
                raise APIError('delivery_serial_conflict', '同案有多个真实单号，拒绝合并')
            if not prior or item['native_evidence']['finished_at_utc'] > prior['native_evidence']['finished_at_utc']:
                documents[case] = item
        except (ValueError, KeyError, OSError, RuntimeError) as error:
            rejected.append({'case_id': case, 'source': source, 'error': str(error)})
    created = {case: {k: value for k, value in item.items() if k in
               ['case_id', 'sequence', 'serial', 'amount', 'native_evidence']} for case, item in documents.items()}
    pending = []
    for case, row in rows.items():
        try:
            _, paths = service.resolve(case); form, state = read_json(paths['form']), read_json(paths['state'])
            unknown, active = _pending(service, case, paths)
            if unknown or active:
                pending.append({'case_id': case, 'sequence': row['sequence'], 'serial': form.get('serial'),
                                'pending': unknown, 'active_jobs': active})
            if case not in created and form.get('serial'):
                created[case] = _created(service, row, expected_userid, form, state)
        except (ValueError, KeyError, OSError, RuntimeError) as error:
            rejected.append({'case_id': case, 'scope': 'current_case_or_creation', 'error': str(error)})
    documents = sorted(documents.values(), key=lambda item: item['sequence'])
    counts = {'created': len(created), 'filled_drafts': sum(not x['partial'] and not x['currently_processing'] for x in documents),
              'audit_passed_count': sum(x['status'] == 'draft_filled_native_audit_passed' for x in documents),
              'material_gap_count': sum(bool(x['system_evidence_gaps']) and not x['partial'] for x in documents),
              'partial_count': sum(x['partial'] and not x['currently_processing'] for x in documents),
              'options_metadata_pending_count': sum(x['status'] == 'draft_filled_fields_verified_options_metadata_pending' for x in documents),
              'pending_or_in_progress_count': len(pending)}
    valid_cases = {item['case_id'] for item in documents}
    for issue in rejected:
        issue['severity'] = 'historical_record_rejected' if issue.get('case_id') in valid_cases else 'error'
    return {'ok': not any(issue['severity'] == 'error' for issue in rejected), 'scan_started_at_utc': started, 'as_of_utc': stamp(), 'counts': counts,
            'documents': documents, 'created_serials': sorted(created.values(), key=lambda item: item['sequence']),
            'pending': pending, 'issues': rejected, 'batch_index': str(index_path),
            'batch_index_sha256': digest(index_path), 'system_write': False, 'browser_accessed': False,
            'pdf_filename_overrides': [{'sequence':row['sequence'], 'filename':row['expected_pdf_filename_user_format']}
                                       for row in rows.values() if 'expected_pdf_filename_user_format' in row]}


def _output_safe(service, path):
    path = Path(path).resolve()
    for protected in [service.root/'cases', service.root/'.local/assistant_jobs', service.root/'规范库']:
        if path.is_relative_to(protected.resolve()):
            raise APIError('delivery_output_scope', '交付不能写入案件、任务或规范目录')
    return path


def _apply(plan, snapshot, sequence_key, presentation):
    filenames = {row['sequence']:row['filename'] for row in snapshot.get('pdf_filename_overrides', [])}
    plan['raw_pdf_statuses'] = []
    for sheet in plan['sheets']:
        for row in sheet['rows']:
            filename = filenames.get(row.get(sequence_key))
            if filename:
                if '期望文件名' in row: row['期望文件名'] = filename
                if isinstance(row.get('PDF期望路径'), str):
                    original = row['PDF期望路径']
                    separator = max(original.rfind('/'), original.rfind('\\'))
                    row['PDF期望路径'] = original[:separator + 1] + filename
            for key in ['PDF状态', '导出状态']:
                if key in row:
                    plan['raw_pdf_statuses'].append({'sheet':sheet['name'], 'sequence':row.get(sequence_key),
                                                     'field':key, 'value':row[key]})
                    if row[key] == 'pending_native_serial_and_preview': row[key] = '待建单后导出'
    for item in snapshot['documents'] + [x for x in snapshot['created_serials'] if x['case_id'] not in {d['case_id'] for d in snapshot['documents']}]:
        label = STATUS_TEXT[item.get('status', 'draft_created')]
        for sheet in plan['sheets']:
            for row in sheet['rows']:
                if row.get(sequence_key) != item['sequence']: continue
                for key in ['真实财务单号', '实际财务单号']:
                    if key in row: row[key] = item['serial']
                if '可办范围状态' in row: row['可办范围状态'] = label
                if '保存回读' in row and item.get('verified_field_groups'):
                    row['保存回读'] = '已独立回读：'+','.join(item['verified_field_groups'])
                if '原生身份绑定' in row and 'payments' in item.get('verified_field_groups', []):
                    row['原生身份绑定'] = '本案收款身份及金额已独立回读'
                for key in ['回执来源', '实际回执']:
                    if key in row: row[key] = item['native_evidence']['path']
                for key in ['提交状态', '实际PDF_SHA256', '真实PDF路径']:
                    if key in row: row[key] = None
                if 'PDF状态' in row: row['PDF状态'] = '待导出'
                if sheet['name'] == 'PDF导出':
                    if '导出状态' in row: row['导出状态'] = '待导出'
                    if 'SHA256' in row: row['SHA256'] = None
                    if '阻塞说明' in row:
                        row['阻塞说明'] = presentation.get('pdf_gap', '已取得真实单号；尚未登记经字节核验的PDF回执')
        summary = next((sheet for sheet in plan['sheets'] if sheet['name'] == '问题总结'), None)
        if summary and (item.get('system_evidence_gaps') or item.get('partial') or item.get('options_verification_gap') or item.get('currently_processing')):
            target = next((row for row in summary['rows'] if row.get(sequence_key) == item['sequence']), None)
            if target is None and all(key in [c['key'] for c in summary['columns']] for key in [sequence_key, '活动或事项', '当前状态']):
                target = {c['key']: None for c in summary['columns']}
                target.update({sequence_key: item['sequence'], '活动或事项': item.get('summary', item['case_id'])}); summary['rows'].append(target)
            if target is not None:
                if '当前状态' in target: target['当前状态'] = label
                if '核定申请额' in target: target['核定申请额'] = item['amount']
                details = [' / '.join(row) for row in item.get('system_evidence_gaps', [])]+item.get('deferred_inputs', [])
                if item.get('options_verification_gap'): details.append(OPTIONS_RECHECK_NOTE)
                if item.get('currently_processing'): details.append('当前任务执行中或原写入待独立回读。')
                if '需补材料或确认' in target:
                    old_detail = target['需补材料或确认'] or ''
                    if item.get('options_verification_gap'):
                        for legacy in ['旧回执未含本轮严格合同清点元数据；不补造、不据此重复写选项。',
                                       '历史回执元数据不足，仅登记已核字段；不补造合同清点元数据。']:
                            old_detail = old_detail.replace(legacy, OPTIONS_RECHECK_NOTE)
                    fresh = [detail for detail in details if detail and detail not in old_detail]
                    target['需补材料或确认'] = '；'.join(filter(None, [old_detail]+fresh))
                if '处理动作' in target: target['处理动作'] = '按所列缺项接续；保留已核保存字段'
    edition = presentation.get('edition', '办理中快照' if snapshot['in_progress'] else '本轮快照')
    plan['title'] = presentation.get('title', plan.get('title', '报销办理结果'))
    plan['note'] = edition+'；截至UTC '+snapshot['as_of_utc']+'；提交、审批、付款和未核PDF字节字段留空。'
    for sheet in plan['sheets']:
        note = presentation.get('sheet_notes', {}).get(sheet['name'], sheet.get('note', ''))
        sheet['note'] = edition+'，截至UTC '+snapshot['as_of_utc']+'。'+note
    return plan


def _readable(plan, snapshot, folder):
    def text(value): return html.escape('' if value is None else str(value)).replace('|', '&#124;').replace('\n', '<br>')
    counts = snapshot['counts']
    lines = ['# '+plan['title'], '', plan['note'], '',
             '已建'+str(counts['created'])+'单；已填草稿'+str(counts['filled_drafts'])+'单；待补系统材料'+str(counts['material_gap_count'])+'单；仅基本信息与经费已填'+str(counts['partial_count'])+'单；当前办理或待回读'+str(counts['pending_or_in_progress_count'])+'单。', '']
    summary = next((sheet for sheet in plan['sheets'] if sheet['name'] == '问题总结'), None)
    if summary:
        lines += ['| '+' | '.join(text(c.get('label', c['key'])) for c in summary['columns'])+' |',
                  '|'+'|'.join('---' for _ in summary['columns'])+'|']
        lines += ['| '+' | '.join(text(row[c['key']]) for c in summary['columns'])+' |' for row in summary['rows']]
    path = folder/'办理情况与缺项.md'; path.write_text('\n'.join(lines)+'\n', encoding='utf-8')
    page = folder/'办理情况与缺项.html'
    body = '<h1>'+text(plan['title'])+'</h1><p>'+text(plan['note'])+'</p><p>'+text(lines[4])+'</p>'
    if summary:
        body += '<table><thead><tr>'+''.join('<th>'+text(c.get('label', c['key']))+'</th>' for c in summary['columns'])+'</tr></thead><tbody>'
        body += ''.join('<tr>'+''.join('<td>'+text(row[c['key']])+'</td>' for c in summary['columns'])+'</tr>' for row in summary['rows'])+'</tbody></table>'
    page.write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{font:16px/1.7 system-ui;margin:28px;color:#203044}table{border-collapse:collapse;width:100%}th,td{padding:12px;border:1px solid #d8e1e8;vertical-align:top}th{background:#294a68;color:white}</style>'+body+'</html>', encoding='utf-8')
    return {'markdown': str(path), 'markdown_sha256': digest(path), 'html': str(page), 'html_sha256': digest(page)}


def verify_workbook(path, plan):
    ns = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}; checked = 0
    with zipfile.ZipFile(path) as archive:
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            strings = [''.join(t.text or '' for t in node.iter('{'+ns['x']+'}t')) for node in ET.from_zip(archive, 'xl/sharedStrings.xml')]
        sheets = ET.from_zip(archive, 'xl/workbook.xml').find('x:sheets', ns)
        if [s.attrib['name'] for s in sheets] != [s['name'] for s in plan['sheets']]:
            raise APIError('delivery_excel_sheets', 'XLSX工作表名称、顺序或数量不同')
        for index, spec in enumerate(plan['sheets'], 1):
            cells = {cell.attrib['r']: cell for cell in ET.from_zip(archive, 'xl/worksheets/sheet'+str(index)+'.xml').findall('.//x:sheetData/x:row/x:c', ns)}
            for row_index, row in enumerate(spec['rows'], 6):
                for column_index, column in enumerate(spec['columns'], 1):
                    n, letters = column_index, ''
                    while n: n, r = divmod(n-1, 26); letters = chr(65+r)+letters
                    cell = cells.get(letters+str(row_index)); actual = None
                    if cell is not None:
                        value = cell.find('x:v', ns)
                        if cell.attrib.get('t') == 'e': raise APIError('delivery_excel_cell', 'XLSX存在错误单元格')
                        if cell.attrib.get('t') == 's': actual = strings[int(value.text)]
                        elif cell.attrib.get('t') == 'inlineStr': actual = ''.join(t.text or '' for t in cell.findall('.//x:t', ns))
                        elif value is not None: actual = value.text
                    expected = row[column['key']]
                    if expected in (None, ''): equal = actual in (None, '')
                    elif column['type'] in ['money', 'number']: equal = actual is not None and Decimal(actual) == Decimal(str(expected))
                    else: equal = str(actual) == str(expected) or (str(actual) == "'"+str(expected) and bool(re.match(r'\s*[=+@]', str(expected))))
                    if not equal: raise APIError('delivery_excel_cell', 'XLSX与交付计划不符：'+spec['name']+' '+letters+str(row_index))
                    checked += 1
    return {'sheets': len(plan['sheets']), 'data_cells_checked': checked, 'all_plan_cells_match': True}


def render_workbook(service, plan_path, output, runtime=None):
    """Use checklist_delivery's existing node/module-root interface; never install."""
    runtime = runtime or {}
    node = runtime.get('node') or shutil.which('node')
    modules = runtime.get('module_root') or os.environ.get('NODE_PATH')
    python = runtime.get('python') or service.python
    if not node or not modules or not Path(node).is_file() or not Path(modules).is_dir():
        return {'status': 'unavailable', 'needed_inputs': ['已配置的node及module_root；不自动安装依赖']}
    folder = output/'Excel'; folder.mkdir()
    path = folder/'报销办理结果.xlsx'; qa = folder/'视觉核验'
    command = [str(python), str(Path(__file__).with_name('checklist_delivery.py')), '--input', str(plan_path),
               '--output', str(path), '--node', str(node), '--module-root', str(modules), '--qa-dir', str(qa)]
    marker = runtime.get('operation_marker')
    if marker:
        marked = subprocess.run([str(node), str(marker), '--operation-kind', 'create', '--expected-output-count', '1', '--output-format', 'xlsx'], cwd=output, capture_output=True, text=True)
        (output/'表格操作登记.log').write_text(marked.stdout+marked.stderr, encoding='utf-8')
        if marked.returncode: return {'status': 'failed', 'error': '表格操作登记未成功，未创建工作簿'}
    result = subprocess.run(command, cwd=output, capture_output=True, text=True)
    log = output/'Excel生成.log'; log.write_text(result.stdout+result.stderr, encoding='utf-8')
    if result.returncode: return {'status': 'failed', 'log': str(log), 'error': '正式Excel入口未完成'}
    receipt_path = Path(str(path)+'.receipt.json'); receipt = read_json(receipt_path)
    if receipt.get('ok') is not True or receipt.get('sha256') != digest(path):
        raise APIError('delivery_excel_sha', 'Excel正式回执SHA不符')
    checked = verify_workbook(path, read_json(plan_path))
    evidence = {'status': 'created', 'output': str(path), 'sha256': digest(path),
                'receipt': str(receipt_path), 'receipt_sha256': digest(receipt_path),
                'qa_directory': str(qa), 'cell_verification': checked, 'visual_review_pending': True}
    atomic_json(folder/'逐格核验.json', evidence)
    return evidence


def refresh_delivery(service, *, batch_index, base_delivery_plan, output_root, expected_userid,
                     scenario=None, legacy_evidence=(), progress_path=None, in_progress=True,
                     render_excel=False, runtime=None, check_only=False, presentation=None,
                     sequence_key='序号'):
    """Public local-only entry. check_only never creates files or discovers runtimes."""
    root = _output_safe(service, output_root)
    if progress_path:
        progress_path = _output_safe(service, progress_path)
    base_path, base = _verified_json(service, base_delivery_plan)
    plan = validate_manifest(copy.deepcopy(base))
    if progress_path:
        inputs = {base_path.resolve(), Path(batch_index).resolve()} | {Path(ref['path']).resolve() for ref in plan['sources']}
        if progress_path in inputs:
            raise APIError('delivery_progress_scope', '进度输出不能覆盖计划、索引或来源文件')
    snapshot = collect_delivery(service, batch_index=batch_index, expected_userid=expected_userid,
                                scenario=scenario, legacy_evidence=legacy_evidence)
    snapshot['in_progress'] = bool(in_progress)
    if not in_progress and snapshot['pending']:
        raise APIError('delivery_in_progress', '仍有原生任务或待回读写入，只能生成办理中快照')
    if check_only: return snapshot
    folder = root/('snapshot_'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:8])
    folder.mkdir(parents=True)
    previous = None; old = None; old_sha = None
    if progress_path:
        if progress_path.exists():
            old = progress_path.read_bytes(); old_sha = hashlib.sha256(old).hexdigest()
            previous = folder/'previous_progress.json'; previous.write_bytes(old)
    native_path = folder/'verified_native_records.json'; atomic_json(native_path, snapshot)
    plan = _apply(plan, snapshot, sequence_key, presentation or {})
    plan['sources'] += [{'path': str(base_path), 'sha256': digest(base_path)},
                        {'path': str(native_path), 'sha256': digest(native_path)}]
    if previous: plan['sources'].append({'path': str(previous), 'sha256': old_sha})
    validate_manifest(plan)
    plan_path = folder/'清单表格交付计划.json'; atomic_json(plan_path, plan)
    if any(sheet['name'] == '问题总结' for sheet in plan['sheets']): write_issue_summary(plan, folder)
    readable = _readable(plan, snapshot, folder)
    excel = render_workbook(service, plan_path, folder, runtime) if render_excel else {'status': 'not_requested'}
    progress = {**snapshot, 'schema': (json.loads(old).get('schema') if old else None) or 'ustc-batch-delivery-progress-v1',
                'updated_at_utc': snapshot['as_of_utc'], 'batch_still_in_progress': bool(in_progress),
                'filled_and_audited': snapshot['counts']['filled_drafts'], **snapshot['counts'],
                'in_progress': snapshot['pending'], 'claims_finalized': False, 'pdf_exported': None,
                'approval_completed': None, 'payment_completed': None, 'delivery_snapshot': str(folder),
                'delivery_plan': str(plan_path), 'readable_summary': readable, 'excel_delivery': excel,
                'canonical_before': {'path': str(previous), 'sha256': old_sha} if previous else None,
                'count_notes': {'filled_and_audited': '兼容字段：已填且audit阶段有登记；材料gap与旧选项元数据限制另列，不代表审批通过。'}}
    progress_file = folder/'progress.json'; atomic_json(progress_file, progress)
    if progress_path:
        if (progress_path.exists() and (old is None or digest(progress_path) != old_sha)) or (old is not None and not progress_path.exists()):
            raise APIError('delivery_progress_changed', '聚合期间进度原件变化；已保留快照，未覆盖')
        atomic_json(progress_path, progress)
    result = {**snapshot, 'ok': snapshot['ok'] and excel['status'] not in ['failed', 'unavailable'],
              'output_directory': str(folder), 'delivery_plan': str(plan_path),
              'progress_path': str(progress_path or progress_file), 'progress_sha256': digest(progress_path or progress_file),
              'readable_summary': readable, 'excel': excel}
    atomic_json(folder/'refresh_receipt.json', result)
    return result
