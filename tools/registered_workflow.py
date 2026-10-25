"""登记入口只编排各业务阶段，资料与浏览器生命周期由专用模块持有。"""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
import time

from assistant_api import APIError, atomic_json, read_json
from edge_runtime import file_lock

# 保留既有导入入口；实现与内部消费者均指向唯一owner。
from registered_inputs import checked_reference, load_definition
from registered_session import RegisteredSession
from assistant_contract import REGISTERED_PHASES


def _collect_existing(session):
    from submission import verify_applicant_agreement, verify_applicant_prepared_list
    from applicant_batch import record_existing_submission

    case = session.ids[0]
    tab = session.begin_case(case, force_new=True)
    session.job(
        case,
        'invoice_verification',
        {
            'action': 'view',
            'view': 'in-progress',
            'tab_id': tab,
            'expected_userid': session.cfg['expected_userid'],
        },
        'initial-in-progress-view',
    )
    for attempt in range(12):
        result, native = session.job(
            case, 'read_system', {'mode': 'readback', 'tab_id': tab}, 'initial-workflow-list'
        )
        if any(
            str(g.get('funcno')) == '101' and g.get('visible') and not g.get('loading')
            for g in native.get('grids', [])
        ):
            break
        time.sleep(0.25)
    else:
        raise APIError('registered_initial_list', '原生审批中列表尚未就绪，仅回读')
    art = next(a for a in result['artifacts'] if a['name'] == 'output.json')
    ref = {
        'case_id': case,
        'job_id': result['job_id'],
        'artifact_id': art['artifact_id'],
        'sha256': art['sha256'],
        'scope': '本账号原生列表，逐单按真实单号匹配；不跨案ack金融写入',
    }
    for current in session.ids:
        f = read_json(session.service.resolve(current)[1]['form'])
        try:
            verify_applicant_agreement(
                native,
                f['serial'],
                session.cfg['expected_userid'],
                f['amount'],
                f['basic'].get('teacher_name'),
            )
            record_existing_submission(
                session.service, current, session.cfg['expected_userid'], ref
            )
            session.initial_states[current] = {
                'status': 'already_in_approval_verified',
                'native_evidence': ref,
            }
            continue
        except ValueError:
            pass
        try:
            verify_applicant_prepared_list(
                native, f['serial'], session.cfg['expected_userid'], f['amount']
            )
            session.initial_states[current] = {
                'status': 'already_applicant_ready_verified',
                'native_evidence': ref,
            }
        except ValueError:
            pass
    atomic_json(
        session.folder / 'initial-workflow-states.json',
        {'states': session.initial_states, 'native_evidence': ref},
    )


def run_registered_workflow(service, *, case_id, phase='ready', execute=False, progress=None):
    # One lease covers bootstrap, window retirement, finance and delivery. A
    # second invocation must not touch the first invocation's browser scene.
    try:
        with (
            file_lock(service.root / '.local/workflow-orchestrator.lock')
            if execute and phase != 'materials'
            else nullcontext()
        ):
            return _run_registered_workflow(
                service, case_id=case_id, phase=phase, execute=execute, progress=progress
            )
    except RuntimeError as error:
        raise APIError(getattr(error, 'code', 'registered_workflow_busy'), str(error)) from error


def _run_registered_workflow(service, *, case_id, phase='ready', execute=False, progress=None):
    cfg, index, auth, definition = load_definition(service, case_id)
    if phase not in REGISTERED_PHASES:
        raise APIError(
            'registered_phase',
            '登记入口支持fill、ready、materials、approve、export-system、export-list、finish和status',
        )
    if phase == 'export-list':
        from grid_export_workflow import run_grid_export

        return run_grid_export(
            service, case_id, cfg, index, auth, execute=execute, progress=progress
        )
    if phase == 'export-system':
        from system_pdf_batch import run_system_pdfs

        return run_system_pdfs(
            service, case_id, cfg, index, auth, execute=execute, progress=progress
        )
    if phase == 'finish':
        # 在批准前核对整条链的已登记输入，避免批准后才发现导出计划缺失。
        previews = {
            stage: _run_registered_workflow(
                service, case_id=case_id, phase=stage, execute=False, progress=progress
            )
            for stage in ('approve', 'export-system', 'export-list')
            if stage != 'export-list' or cfg.get('list_export_manifest')
        }
        if not execute:
            return {
                'ok': True,
                'case_id': case_id,
                'phase': 'finish',
                'state': 'registered_finish_preview',
                'system_write_requested': False,
                'stages': previews,
                'gates': previews['approve'].get('gates', []),
            }
        approval = _run_registered_workflow(
            service, case_id=case_id, phase='approve', execute=execute, progress=progress
        )
        if approval.get('state') != 'completed':
            return approval
        from system_pdf_batch import run_system_pdfs

        pdfs = run_system_pdfs(service, case_id, cfg, index, auth, execute=True, progress=progress)
        listing = None
        if pdfs['ok'] and cfg.get('list_export_manifest'):
            from grid_export_workflow import run_grid_export

            listing = run_grid_export(
                service, case_id, cfg, index, auth, execute=True, progress=progress
            )
        result = {
            'ok': pdfs['ok'] and (listing is None or listing['ok']),
            'case_id': case_id,
            'phase': 'finish',
            'state': pdfs['state'] if listing is None or listing['ok'] else listing['state'],
            'approval': approval,
            'system_pdfs': pdfs,
            'list_export': listing,
            'report_path': pdfs['report_path'],
            'submitted_count': approval['submitted_count'],
            'pdf_exported': pdfs['pdf_exported'],
            'held_count': pdfs['held_count'],
            'approval_elapsed_seconds': approval['elapsed_seconds'],
            'approval_within_target': approval['within_target'],
            'observations_are_cached': True,
        }
        current = service.root / '.local/registered_workflows' / case_id / 'current-result.json'
        if current.exists() and read_json(current).get('attachments'):
            result['attachments'] = read_json(current)['attachments']
        atomic_json(current, result)
        return result
    if phase == 'approve':
        from applicant_batch import run_applicant_batch

        manifest = checked_reference(service, cfg.get('approval_manifest'))
        session = RegisteredSession(service, case_id, cfg, index, auth, progress)
        return run_applicant_batch(service, session, manifest, execute=execute, progress=progress)
    if not execute:
        result = {
            'ok': True,
            'state': 'registered_preview',
            'case_id': case_id,
            'scenario': cfg['scenario'],
            'batch_index': str(index),
            'phase': phase,
            'system_write_requested': False,
            'definition': str(definition),
        }
        current = service.root / '.local/registered_workflows' / case_id / 'current-result.json'
        if current.is_file():
            result['last_run'] = {
                k: v
                for k, v in read_json(current).items()
                if k
                in (
                    'state',
                    'phase',
                    'report_path',
                    'applicant_ready_count',
                    'already_submitted_count',
                    'submitted_count',
                    'pdf_exported',
                    'held_count',
                    'attachments',
                    'approval_elapsed_seconds',
                    'approval_within_target',
                    'observations_are_cached',
                )
            }
        return result
    if phase == 'status':
        raise APIError('registered_execute', 'status不执行')
    if phase == 'materials':
        from material_packet_batch import run_registered_packets

        with file_lock(service.root / '.local' / ('materials-' + case_id + '.lock')):
            attachments = run_registered_packets(service, cfg, progress=progress)
            current = service.root / '.local/registered_workflows' / case_id / 'current-result.json'
            if current.exists():
                previous = read_json(current)
                previous['attachments'] = attachments
                atomic_json(current, previous)
            return {
                'ok': attachments['ok'],
                'case_id': case_id,
                'phase': 'materials',
                'attachments': attachments,
                'system_write_requested': False,
            }
    # A previously registered native rejection already settles that original
    # action. Reuse it before trying to wake a frozen page, never infer from a
    # missing tab or clear an unknown write.
    from batch_workflow import inspect_batch
    from submission_batch import SubmissionBatch, validate_authorization

    _, items = inspect_batch(service, cfg['scenario'], index)
    validate_authorization(service, auth, index, items, cfg['expected_userid'], prepare_only=True)
    for item in items:
        case = item['case_id']
        _, paths = service.resolve(case)
        pending_path = paths['state'].parent / 'pending_write.json'
        if not pending_path.exists():
            continue
        pending = read_json(pending_path)
        if pending.get('operation') != 'submission:next':
            continue
        source = Path(pending.get('verification', {}).get('save_receipt', ''))
        request = read_json(source.parent / 'request.json')
        tab = request.get('parameters', {}).get('tab_id')
        runner = SubmissionBatch(service, index, cfg['scenario'], tab, cfg['expected_userid'], auth)
        runner.fresh_workspace_mode = True
        plan = runner.plan(read_json(paths['form']), read_json(auth))
        runner.recover_pending(item, plan, pending, recorded_only=True)
    session = RegisteredSession(service, case_id, cfg, index, auth, progress)
    session.ensure()
    from reimbursement_workflow import _run_workflow

    if not any(service.overview(c).get('unverified_write') for c in session.ids):
        _collect_existing(session)
    result = _run_workflow(
        service,
        scenario=cfg['scenario'],
        batch_index=str(index),
        phase='run' if phase == 'fill' else 'prepare-submit',
        submission_authorization=str(auth),
        tab_id=session.state['tab_id'],
        expected_userid=cfg['expected_userid'],
        execute=True,
        progress=progress,
        workspace=session,
    )
    atomic_json(session.state_path, session.state)
    result.update(
        case_id=case_id,
        registered_definition=str(definition),
        session={'window_id': session.state['window_id'], 'tab_id': session.state['tab_id']},
        requested_terminal='verified_draft' if phase == 'fill' else 'applicant_review_pending',
    )
    atomic_json(session.folder / 'current-result.json', result)
    if phase == 'ready' and cfg.get('auxiliary_bindings'):
        from material_packet_batch import run_registered_packets

        result['attachments'] = run_registered_packets(service, cfg, progress=progress)
        result['ok'] = result.get('ok') is True and result['attachments']['ok'] is True
        atomic_json(session.folder / 'current-result.json', result)
    return result
