"""登记清单驱动原生明细PDF导出，正式打印按学校审批状态单列。"""

from __future__ import annotations
import hashlib
import time
from pathlib import Path

from assistant_api import APIError, atomic_json, read_json
from registered_session import RegisteredSession
from registered_inputs import checked_reference
from submission import view_plan


def validate_entries(service, manifest, userid):
    value = read_json(manifest)
    if (
        value.get('schema') != 'ustc-system-pdf-batch-v1'
        or value.get('expected_userid') != userid
        or not value.get('source')
    ):
        raise APIError('system_pdf_manifest', '系统PDF批次须绑定账号及来源')
    entries = value.get('cases')
    if (
        not isinstance(entries, list)
        or not entries
        or len({r.get('case_id') for r in entries}) != len(entries)
    ):
        raise APIError('system_pdf_scope', '系统PDF案件清单为空或重复')
    for row in entries:
        _, paths = service.resolve(row['case_id'])
        form = read_json(paths['form'])
        plan = row['export_plan']
        if (
            any(plan.get(k) != form.get(k) for k in ('case_id', 'serial'))
            or row.get('amount') != form.get('amount')
            or plan.get('summary') != form['basic']['fields']['t-abst']
        ):
            raise APIError('system_pdf_binding', '导出清单与本案单号、摘要或金额不同')
        if (
            plan.get('view_kind') != 'details'
            or plan.get('delivery_mode') != 'rename_only'
            or plan.get('filename_separator') not in (' ', '_')
        ):
            raise APIError('system_pdf_mode', '明细原文件只归档；命名分隔须明确为空格或下划线')
        output = Path(row['export_root']).resolve()
        if not any(output.is_relative_to(root) for root in service.material_roots):
            raise APIError('system_pdf_root', '导出路径超出授权材料根')
    return value


def settle_view_preflight(service, case, state, state_path):
    """只接续已明确在选择/点击前拒绝的查看动作，原回执原样保留。"""
    if state.get('phase') != 'detail_open_requested' or not state.get('request_id'):
        return False
    hits = []
    for p in (service.jobs / case).glob('*/request.json'):
        request = read_json(p)
        if request.get('request_id') == state['request_id']:
            hits.append((p, request))
    if len(hits) != 1:
        return False
    path, request = hits[0]
    result = service.result(case, path.parent.name)
    if (
        result.get('status') != 'failed'
        or request.get('operation') != 'submission'
        or request.get('parameters', {}).get('action') != 'detail-open'
    ):
        return False
    artifact = next((a for a in result.get('artifacts', []) if a['name'] == 'output.json'), None)
    if not artifact:
        return False
    evidence = service.artifact(case, result['job_id'], artifact['artifact_id'])
    envelope = read_json(evidence)
    body = envelope.get('result', {})
    if (
        envelope.get('phase') != 'script_completed'
        or body.get('ok') is not False
        or body.get('error') not in ('SYSTEM_PRINT_EDITOR_OR_LOADING', 'DETAIL_LIST_NOT_READY')
    ):
        return False
    source = Path(envelope.get('script', ''))
    if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != envelope.get(
        'source_sha256'
    ):
        return False
    text = source.read_text()
    segment = text.split('async function details(){', 1)[-1].split('function detailContinue', 1)[0]
    if segment.find("throw Error('" + body['error'] + "')") < 0 or segment.find(
        "throw Error('" + body['error'] + "')"
    ) > segment.find("jqGrid('resetSelection')"):
        return False
    proof = {
        'original_state': state,
        'original_job_id': result['job_id'],
        'source_sha256': envelope['source_sha256'],
        'view_action_not_dispatched': True,
        'financial_write': False,
    }
    atomic_json(state_path.parent / (case + '-preflight-' + result['job_id'] + '.json'), proof)
    state.update(
        phase='retryable_view_preflight',
        attempt=state.get('attempt', 1) + 1,
        preflight_rejection_job=result['job_id'],
    )
    atomic_json(state_path, state)
    return True


def reusable_export(service, entry, state):
    """复用原导出任务的文件检查，绑定原命名及输出目录，不解析PDF。"""
    case = entry['case_id']
    prior = state.get('export_job_id') or entry.get('existing_export_job_id')
    if not prior:
        return None
    checked = service.pdf_export_inspect(case, prior)
    if checked.get('status') != 'ready' or checked.get('ok') is not True:
        raise APIError('system_pdf_original_export', '原导出未完成；保留原任务：' + prior)
    folder = service.job_folder(case, prior)
    parameters = read_json(folder / 'request.json')['parameters']
    if (
        parameters.get('export_plan') != entry['export_plan']
        or not parameters.get('export_root')
        or Path(parameters['export_root']).resolve() != Path(entry['export_root']).resolve()
    ):
        raise APIError('system_pdf_existing_plan', '既有PDF与当前命名或归档目录不同，不覆盖')
    result = read_json(folder / 'pdf_export/export_receipt.json')
    return {
        'case_id': case,
        'serial': entry['export_plan']['serial'],
        'status': 'reused',
        'pdf': result['pdf'],
        'pdf_sha256': result['pdf_sha256'],
        'export_job_id': prior,
    }


def run_system_pdfs(service, case_id, cfg, index, auth, *, execute=False, progress=None):
    manifest = checked_reference(service, cfg.get('system_pdf_manifest'))
    userid = cfg['expected_userid']
    value = validate_entries(service, manifest, userid)
    fingerprint = hashlib.sha256(manifest.read_bytes()).hexdigest()
    if not execute:
        return {
            'ok': True,
            'state': 'system_pdf_preview',
            'phase': 'export-system',
            'case_id': case_id,
            'documents': len(value['cases']),
            'system_write_requested': False,
        }
    session = RegisteredSession(service, case_id + '-pdf', cfg, index, auth, progress)
    session.ids = list(dict.fromkeys(session.ids + [r['case_id'] for r in value['cases']]))
    folder = session.folder / ('exports-' + fingerprint[:16])
    folder.mkdir(parents=True, exist_ok=True)
    report_path = folder / ('report-' + str(time.time_ns()) + '.json')
    report = {
        'ok': True,
        'case_id': case_id,
        'phase': 'export-system',
        'state': 'running',
        'report_path': str(report_path),
        'results': [],
        'source_kind': 'system_view_preview_not_final_print',
        'system_write_requested': False,
        'approval_completed': False,
    }

    def checkpoint():
        report['pdf_exported'] = sum(
            x.get('status') in ('exported', 'reused') for x in report['results']
        )
        report['held_count'] = sum(
            x.get('status', '').startswith('held_') for x in report['results']
        )
        atomic_json(report_path, report)
        atomic_json(session.folder / 'current-pdf-result.json', report)

    def job(case, operation, parameters, label, execute=False, stable=None):
        return session.job(
            case,
            operation,
            {**parameters, 'tab_id': session.state['tab_id']},
            label,
            execute,
            request_id=stable,
        )

    def action(case, plan, name, stable=None):
        return job(case, 'submission', {'action': name, 'plan': plan}, name, True, stable)

    checkpoint()
    try:
        reused = {}
        for entry in value['cases']:
            state_path = folder / (entry['case_id'] + '.json')
            state = read_json(state_path) if state_path.exists() else {}
            cached = reusable_export(service, entry, state)
            if cached:
                reused[entry['case_id']] = cached
                state.update(
                    phase='exported', export_job_id=cached['export_job_id'], pdf=cached['pdf']
                )
                atomic_json(state_path, state)
                continue
            if state_path.exists():
                if state.get('phase') not in ('exported', 'retryable_view_preflight'):
                    if settle_view_preflight(service, entry['case_id'], state, state_path):
                        continue
                    raise APIError(
                        'system_pdf_original_task',
                        '原查看或导出尚未确认，保留原工作页：' + str(state_path),
                    )
        if len(reused) == len(value['cases']):
            report.update(
                state='completed',
                results=list(reused.values()),
                reused_completed_run=True,
                live_rechecked=False,
            )
            checkpoint()
            return report
        # 一个工作页处理整批，和验证码页分别持有，不影响本人输入。
        first = value['cases'][0]['case_id']
        session.begin_case(first)
        job(
            first,
            'invoice_verification',
            {'action': 'view', 'view': 'all-bills', 'expected_userid': userid},
            'pdf-all-bills',
        )
        source, native = job(
            first,
            'invoice_verification',
            {'action': 'full-grid', 'grid': '101', 'expected_userid': userid},
            'pdf-all-bills-read',
        )
        if native.get('scopeComplete') is not True:
            raise APIError('system_pdf_list_incomplete', '本人单据列表尚未完整取得')
        native_rows = {
            r['t.serial_no']: r for g in native.get('grids', []) for r in g.get('rows', [])
        }
        list_art = next(a for a in source['artifacts'] if a['name'] == 'output.json')
        report['source_list'] = {
            'case_id': first,
            'job_id': source['job_id'],
            'artifact_id': list_art['artifact_id'],
            'sha256': list_art['sha256'],
        }
        for entry in value['cases']:
            case = entry['case_id']
            _, paths = service.resolve(case)
            form = read_json(paths['form'])
            plan = entry['export_plan']
            serial = plan['serial']
            row = {'case_id': case, 'serial': serial, 'status': 'running'}
            report['results'].append(row)
            checkpoint()
            state_path = folder / (case + '.json')
            saved = read_json(state_path) if state_path.exists() else {}
            if case in reused:
                row.update(reused[case])
                checkpoint()
                continue
            if serial not in native_rows:
                row.update(status='held_not_found', issue='当前本人列表未找到原单')
                checkpoint()
                continue
            if str(native_rows[serial].get('t.status')) == '-99':
                row.update(status='held_draft', issue='仍为草稿，先按材料差项完成准备')
                checkpoint()
                continue
            if saved and saved.get('phase') != 'retryable_view_preflight':
                if session.state.get('last_case_id') != case:
                    raise APIError(
                        'system_pdf_original_view', '存在原查看任务，未自动重开；保留原job和工作页'
                    )
                raise APIError(
                    'system_pdf_original_view',
                    '原查看或下载任务未完整结束，需按保存阶段恢复；不换请求号重发',
                )
            if hashlib.sha256(manifest.read_bytes()).hexdigest() != fingerprint:
                raise APIError('system_pdf_input_drift', '运行中导出清单变化')
            session.state['last_case_id'] = case
            atomic_json(session.state_path, session.state)
            submission_plan = view_plan(form, userid, value['source'])
            attempt = saved.get('attempt', 1)
            prefix = (
                'system-pdf-'
                + fingerprint[:12]
                + '-'
                + case
                + ('-r' + str(attempt) if attempt > 1 else '')
            )
            atomic_json(
                state_path,
                {
                    'phase': 'detail_open_requested',
                    'request_id': prefix + '-open',
                    'attempt': attempt,
                },
            )
            action(case, submission_plan, 'detail-open', prefix + '-open')
            for _ in range(15):
                _, observed = action(case, submission_plan, 'system-print-read')
                if observed.get('dialogs') or observed.get('pdf_ready'):
                    break
                time.sleep(0.2)
            if observed.get('dialogs'):
                atomic_json(
                    state_path,
                    {'phase': 'detail_continue_requested', 'request_id': prefix + '-continue'},
                )
                action(case, submission_plan, 'detail-continue', prefix + '-continue')
            for _ in range(20):
                _, observed = action(case, submission_plan, 'system-print-read')
                if observed.get('pdf_ready'):
                    break
                if observed.get('dialogs'):
                    raise APIError('system_pdf_native_dialog', '查看明细出现需处理的原生提示')
                time.sleep(0.3)
            else:
                raise APIError('system_pdf_loading', '原生明细PDF未就绪，保留原页')
            Path(entry['export_root']).mkdir(parents=True, exist_ok=True)
            request_id = prefix + '-export'
            job_result, result = session.job(
                case,
                'pdf_export',
                {
                    'export_plan': plan,
                    'export_root': entry['export_root'],
                    'tab_id': session.state['tab_id'],
                    'expected_userid': userid,
                    'silent': True,
                },
                'export-system-pdf',
                True,
                request_id=request_id,
                on_enqueued=lambda queued: atomic_json(
                    state_path,
                    {
                        'phase': 'export_requested',
                        'export_job_id': queued['job_id'],
                        'request_id': request_id,
                    },
                ),
            )
            if result.get('ok') is not True or result.get('status') != 'ready':
                raise APIError('system_pdf_export_pending', '原导出结果尚未完整，保留原job')
            atomic_json(
                state_path,
                {'phase': 'exported', 'export_job_id': job_result['job_id'], 'pdf': result['pdf']},
            )
            row.update(
                status='exported',
                pdf=result['pdf'],
                pdf_sha256=result['pdf_sha256'],
                export_job_id=job_result['job_id'],
            )
            checkpoint()
            if progress:
                progress(
                    {
                        'event': 'system_pdf_exported',
                        'case_id': case,
                        'serial': serial,
                        'pdf': result['pdf'],
                    }
                )
            try:
                action(case, submission_plan, 'detail-return', prefix + '-return')
            except APIError as error:
                if 'DETAIL_RETURN_PENDING_READ_ORIGINAL' not in str(error) or service.overview(
                    case
                ).get('unverified_write'):
                    raise
                # 文件已完整落盘。只恢复这个工具的只读工作页，不重发返回或下载。
                session.begin_case(case)
                job(
                    case,
                    'invoice_verification',
                    {'action': 'view', 'view': 'all-bills', 'expected_userid': userid},
                    'pdf-return-recovery',
                )
                _, refreshed = job(
                    case,
                    'invoice_verification',
                    {'action': 'full-grid', 'grid': '101', 'expected_userid': userid},
                    'pdf-return-recovery-list',
                )
                if refreshed.get('scopeComplete') is not True:
                    raise APIError('system_pdf_return_list', '返回恢复后的列表未完整取得')
                native_rows = {
                    r['t.serial_no']: r
                    for g in refreshed.get('grids', [])
                    for r in g.get('rows', [])
                }
                row['read_only_page_recovered'] = True
                checkpoint()
        report['state'] = 'completed_with_holds' if report['held_count'] else 'completed'
        checkpoint()
        return report
    except (APIError, ValueError, OSError, KeyError) as error:
        if report['results'] and report['results'][-1]['status'] == 'running':
            report['results'][-1].update(status='held_original_task', issue=str(error))
        report.update(
            ok=False,
            state='original_task_needs_readback',
            error=str(error),
            error_code=getattr(error, 'code', 'system_pdf_error'),
            handoff={
                'session_file': str(session.state_path),
                'report_path': str(report_path),
                'resume_command': 'workflow --case-id '
                + case_id
                + ' --phase export-system --execute',
            },
        )
        checkpoint()
        return report
