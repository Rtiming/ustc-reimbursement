"""把本机已核案件与逐案规划登记为整批入口；不创建财务单、不制造准备或批准回执。"""

from __future__ import annotations

import hashlib
import copy
import json
from pathlib import Path
import re
from contextlib import ExitStack

from assistant_api import APIError, atomic_json, read_json
from batch_workflow import _json_path
from platform_runtime import file_lock
from reimbursement_standards import safe_path
from submission import view_plan, validate_plan
from submission_batch import business_fingerprint
from workspace_config import standards_library
from validate_config import money

SCHEMA = 'ustc-workflow-registration-v1'


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def destination(service, value):
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise APIError('workflow_path', '登记和导出目录须为资料根内绝对路径')
    path = Path(value)
    root = next((r for r in service.material_roots if path.is_relative_to(r) and path != r), None)
    if root is None:
        raise APIError('workflow_path', '登记和导出不能覆盖资料根或越出授权范围')
    return safe_path(root, str(path.relative_to(root)))


def build_registration(service, case_id, request, output_dir):
    _, root_paths = service.resolve(case_id)
    if read_json(root_paths['form']).get('serial'):
        raise APIError('workflow_root', '批次导航案件不能同时是一张财务单')
    allowed = {'schema', 'scenario', 'expected_userid', 'source', 'documents',
               'authorize_prepare', 'authorize_approve', 'department_approvers', 'delivery', 'show_verification_interface'}
    if not isinstance(request, dict) or set(request) - allowed or request.get('schema') != SCHEMA:
        raise APIError('workflow_schema', '须使用ustc-workflow-registration-v1且不含未知字段')
    userid, scenario, source = (request.get(k) for k in ('expected_userid', 'scenario', 'source'))
    if not re.fullmatch(r'[A-Za-z0-9]{2,40}', userid or '') or not re.fullmatch(r'[a-zA-Z0-9_-]+', scenario or '') or not isinstance(source, str) or not source.strip():
        raise APIError('workflow_identity', '须明确本机登录人、专项标识和本次授权/配置来源')
    for key in ('authorize_prepare', 'authorize_approve', 'show_verification_interface'):
        if type(request.get(key, False)) is not bool:
            raise APIError('workflow_authorization', '准备和本人批准授权须分别为布尔值')
    rows = request.get('documents')
    if not isinstance(rows, list) or not rows or len(rows) > 200:
        raise APIError('workflow_documents', '登记1至200个明确案件，不自动发现其他案件')
    out = destination(service, output_dir)
    if out.exists():
        raise APIError('workflow_output_exists', '登记目录已存在，先查原登记结果，不覆盖原版本')
    current_profile = service.standards_read(str(standards_library(service.root)), scenario)
    index_rows, bindings, pdf_rows, approval_rows, plans = [], [], [], [], []
    seen, serials = set(), set()
    delivery = request.get('delivery')
    if delivery is not None:
        if not isinstance(delivery, dict) or set(delivery) - {'export_root', 'download_directory', 'format', 'filename_separator'}:
            raise APIError('workflow_delivery', '导出配置包含未知字段')
        export_root = destination(service, delivery.get('export_root'))
        if export_root.exists() and not export_root.is_dir():
            raise APIError('workflow_delivery', '导出根已存在且不是目录')
        downloads = Path(delivery.get('download_directory', ''))
        if not downloads.is_absolute() or not downloads.is_dir() or downloads.is_symlink():
            raise APIError('workflow_downloads', '须配置当前Edge实际下载目录，不猜作者设备路径')
        if delivery.get('format', 'EXCEL') not in ('EXCEL', 'PDF', 'DBF') or delivery.get('filename_separator', '_') not in ('_', ' '):
            raise APIError('workflow_delivery', '列表格式或文件名分隔符不支持')
    for number, row in enumerate(rows, 1):
        if not isinstance(row, dict) or set(row) - {'case_id', 'plan_path', 'preparation_receipt'}:
            raise APIError('workflow_document', '逐案输入只接受case_id、plan_path和真实preparation_receipt')
        case = row.get('case_id')
        service.case_id(case)
        if case == case_id or case in seen:
            raise APIError('workflow_duplicate', '批次导航与单据分开，单据不能重复')
        seen.add(case)
        _, paths = service.resolve(case)
        form = read_json(paths['form'])
        if not isinstance(form.get('serial'), str) or not re.fullmatch(r'\d+', form['serial']) or form['serial'] in serials:
            raise APIError('workflow_serial', '每案须有已经绑定且不重复的真实单号；登记工具不代为建单')
        serials.add(form['serial'])
        overview = service.overview(case)
        if overview.get('unverified_write') or overview.get('active_jobs'):
            raise APIError('workflow_original_job', '该案有未结束任务或未知写入，先查原任务：' + case)
        plan_path = _json_path(service, row.get('plan_path'))
        plan = read_json(plan_path)
        if plan.get('ok') is not True or plan.get('status') != 'ready':
            raise APIError('workflow_plan_not_ready', '正式规划仍有未决项，不能登记为可办理输入')
        selection = plan.get('rule_selection', {})
        if (selection.get('status') != 'ready' or selection.get('case_id') != case
                or selection.get('profile_id') != scenario
                or selection.get('profile_sha256') != current_profile['sha256']
                or selection.get('profile_revision') != current_profile['profile']['revision']):
            raise APIError('workflow_plan_rules', '逐案计划须绑定当前案件和当前专项版本')
        evidence = plan.get('input_evidence', {})
        normalized_path = _json_path(service, evidence.get('path'))
        normalized = read_json(normalized_path)
        if (sha(normalized_path.read_bytes()) != evidence.get('sha256') or normalized.get('case_id') != case
                or normalized.get('schema') != 'ustc-reimbursement-batch-input-v1'):
            raise APIError('workflow_plan_source', '逐案归一化输入的案件或SHA不符')
        forms = [f for d in plan.get('plan', {}).get('documents', []) for f in d.get('forms', [])]
        if len(forms) != 1:
            raise APIError('workflow_split_required', '一个案件须对应一张明确拆分form；先逐案规划，不把多单绑为一单')
        planned = forms[0]
        source_docs = normalized.get('documents', [])
        if (planned.get('status') != 'ready' or len(source_docs) != 1
                or planned.get('summary_base') != source_docs[0].get('folder_name')
                or type(plan.get('effective_policy', {}).get('contact_in_summary')) is not bool):
            raise APIError('workflow_plan_scope', '单据规划须明确来源材料组、联系人规则及就绪状态')
        fields = form.get('basic', {}).get('fields', {})
        if (planned.get('summary') != fields.get('t-abst') or (planned.get('remarks') or '') != fields.get('t-cl_abst', '')
                or money(planned.get('claim_amount')) != money(form.get('amount'))):
            raise APIError('workflow_form_changed', '本机已核表单的摘要、备注或金额与正式计划不符')
        binding = {'case_id': case, 'serial': form['serial'], 'amount': form['amount'],
                   'business_fingerprint': business_fingerprint(form),
                   'invoice_manifest_sha256': sha(paths['invoices'].read_bytes())}
        bindings.append(binding)
        index_rows.append({'index': number, 'case_id': case, 'official_plan': str(plan_path),
                           'official_plan_sha256': sha(plan_path.read_bytes()),
                           'normalized_input': str(normalized_path), 'normalized_input_sha256': evidence['sha256'],
                           'summary': planned['summary'], 'remarks': planned.get('remarks') or '', 'claim_amount': planned['claim_amount']})
        if form.get('contract', {}).get('exists') is not False or form.get('delivery', {}).get('delegate_finance_print') is not False:
            raise APIError('workflow_options_unconfirmed', '本流程须明确已核无合同及自行打印；不把新用户的未知值默认为否')
        native_plan = view_plan(form, userid, source)
        native_plan['applicant_agreement_authorized'] = request.get('authorize_approve', False)
        choices = request.get('department_approvers', [])
        by_project = {x.get('project'): x for x in choices if isinstance(x, dict)}
        if request.get('authorize_prepare'):
            if not overview.get('input_validation', {}).get('ok'):
                raise APIError('workflow_form_incomplete', '准备授权前补齐本案字段：' + case)
            if len(by_project) != len(choices) or any(f['code'] not in by_project for f in form.get('funding', [])):
                raise APIError('workflow_approver', '每项经费须有已确认的单位审批人，不从旧案继承')
            native_plan['department_approvers'] = [by_project[f['code']] for f in form['funding']]
        validate_plan(native_plan, form)
        plans.append({'case_id': case, 'plan': native_plan})
        if row.get('preparation_receipt'):
            approval_rows.append({**binding, 'plan': native_plan, 'preparation_receipt': row['preparation_receipt']})
        if delivery is not None:
            export = planned.get('export', {})
            from reimbursement_batch_plan import iso_date
            iso_date(export.get('business_date'))
            sequence = export.get('sequence')
            if not ((type(sequence) is int and sequence > 0) or (isinstance(sequence, str) and sequence.isdigit() and int(sequence) > 0)):
                raise APIError('workflow_export_sequence', '正式规划缺少已确认导出序号')
            from reimbursement_batch_plan import safe_filename
            from finance_pdf_export import _no_existing
            separator = delivery.get('filename_separator', '_')
            predicted = export_root / export['business_date'] / (safe_filename(str(sequence) + separator + planned['summary']) + '.pdf')
            destination(service, str(predicted))
            _no_existing(predicted)
            pdf_rows.append({'case_id': case, 'amount': form['amount'], 'export_root': str(export_root),
                             'export_plan': {'schema': 'ustc-finance-pdf-export-plan-v1', 'status': 'confirmed',
                                             'case_id': case, 'serial': form['serial'], 'summary': planned['summary'],
                                             'business_date': export['business_date'], 'sequence': sequence,
                                             'source_refs': [str(plan_path) + '#sha256=' + sha(plan_path.read_bytes()), source],
                                             'view_kind': 'details', 'delivery_mode': 'rename_only',
                                             'filename_separator': delivery.get('filename_separator', '_')}})
    payloads = {'batch-index.json': {'schema': 'ustc-reviewed-batch-plan-index-v1', 'documents': index_rows}}
    index_sha = sha(encoded(payloads['batch-index.json']))
    authorization = {'schema': 'ustc-batch-submission-authorization-v1', 'batch_index_sha256': index_sha,
                     'expected_userid': userid, 'submit_authorized': request.get('authorize_prepare', False),
                     'applicant_agreement_authorized': request.get('authorize_approve', False), 'approval_opinion': '同意',
                     'source': source, 'cases': bindings, 'department_approvers': request.get('department_approvers', [])}
    payloads['preparation.json'] = authorization
    payloads['approval-plans.json'] = {'source': source, 'plans': plans}
    def reference(name):
        return {'path': str(out / name), 'sha256': sha(encoded(payloads[name]))}
    definition = {'schema': 'ustc-registered-workflow-v1', 'case_id': case_id, 'scenario': scenario,
                  'expected_userid': userid, 'login_mode': 'session_only', 'source': source,
                  'batch_index': reference('batch-index.json'), 'preparation_authorization': reference('preparation.json'),
                  'approval_plans': reference('approval-plans.json'), 'approval_authorized': request.get('authorize_approve', False),
                  'show_verification_interface': request.get('show_verification_interface', False), 'registration_directory': str(out)}
    if approval_rows:
        if len(approval_rows) != len(bindings):
            raise APIError('workflow_preparation_partial', '真实准备回执须覆盖全部批准案件，不能混入没有回执的单据')
        payloads['approval.json'] = {'schema': 'ustc-applicant-batch-v1', 'expected_userid': userid, 'source': source,
                                    'approval_opinion': '同意', 'applicant_agreement_authorized': request.get('authorize_approve', False),
                                    'show_verification_interface': request.get('show_verification_interface', False), 'cases': approval_rows}
        from applicant_batch import validate_manifest_value
        validate_manifest_value(service, payloads['approval.json'], userid)
        definition['approval_manifest'] = reference('approval.json')
    if delivery is not None:
        payloads['system-pdfs.json'] = {'schema': 'ustc-system-pdf-batch-v1', 'expected_userid': userid, 'source': source, 'cases': pdf_rows}
        payloads['list-export.json'] = {'schema': 'ustc-grid-export-v1', 'expected_userid': userid, 'source': source,
                                       'format': delivery.get('format', 'EXCEL'), 'cases': [{'case_id': x['case_id'], 'serial': x['serial']} for x in bindings],
                                       'output_root': str(export_root), 'download_directory': str(downloads.resolve())}
        definition.update(system_pdf_manifest=reference('system-pdfs.json'), list_export_manifest=reference('list-export.json'))
    payloads['workflow.json'] = definition
    preview = {'case_id': case_id, 'root_revision': service.revision(case_id), 'output_dir': str(out),
               'definition': reference('workflow.json'), 'files': {k: sha(encoded(v)) for k, v in payloads.items()}}
    return out, payloads, preview, sha(encoded(preview))


def register(service, case_id, request, output_dir, execute=False, expected_preview_sha256=None):
    if type(execute) is not bool:
        raise APIError('workflow_execute', 'execute须为布尔值')
    request = copy.deepcopy(request)
    out, payloads, preview, digest = build_registration(service, case_id, request, output_dir)
    result = {'ok': True, 'executed': False, 'system_write': False, 'preview_sha256': digest, **preview,
              'filesystem_write': False, 'financial_write_executed': False,
              'gates': ([] if request.get('authorize_prepare') else ['准备未授权']) +
                       ([] if request.get('authorize_approve') else ['本人批准未授权']) +
                       ([] if 'approval.json' in payloads else ['准备完成后从真实回读生成批准清单']),
              'next_action': '核对预览后携expected_preview_sha256执行；登记不等于财务已办理'}
    if not execute:
        return result
    with file_lock(service.root / '.local/workflow-orchestrator.lock'), ExitStack() as locks:
        for case in sorted([case_id] + [r['case_id'] for r in request['documents']]):
            locks.enter_context(service.mutex(case))
        out, payloads, current, current_digest = build_registration(service, case_id, request, output_dir)
        if current_digest != expected_preview_sha256 or current_digest != digest:
            raise APIError('workflow_preview_changed', '来源、规则、案件或登记预览已变化，重新预览后再执行')
        out.mkdir(parents=True, exist_ok=False)
        for name, value in payloads.items():
            with (out / name).open('xb') as stream:
                stream.write(encoded(value))
        if request.get('delivery') is not None:
            destination(service, request['delivery']['export_root']).mkdir(parents=True, exist_ok=True)
        # 只有真实原生准备回执校验通过，才可写入批准清单的登记引用。
        if 'approval.json' in payloads:
            from applicant_batch import validate_manifest
            validate_manifest(service, out / 'approval.json', request['expected_userid'])
        _, paths = service.resolve(case_id)
        case = read_json(paths['case'])
        case.setdefault('material_context', {})['workflow_entry'] = current['definition']
        atomic_json(paths['case'], case)
        result.update(executed=True, filesystem_write=True, revision=service.revision(case_id), next_action='workflow status，然后按明确授权选择fill/ready/approve及两类导出')
    return result
