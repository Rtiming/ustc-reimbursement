"""Parameter-only orchestration; business writes and evidence stay with their owners.

Timeout recovery uses the original pending write's independent readback. This
module never replays an unknown write or supplies a missing business field.
"""
from __future__ import annotations

import hashlib
import json
import time
from contextlib import nullcontext
import uuid
from datetime import datetime, timezone
from pathlib import Path

from assistant_api import APIError, atomic_json
from batch_workflow import run_batch
from edge_runtime import file_lock


COMPLETE = {'draft_filled_native_audit_passed', 'draft_filled_system_material_gaps'}
PARTIAL = 'draft_partially_filled_missing_payment'
RECOVERABLE = {'step:basic', 'step:funding', 'step:expenses', 'step:payments',
               'step:audit', 'submission:options'}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _error(error):
    return {'error_code': getattr(error, 'code', 'workflow_stage_error'), 'error': str(error)}


def _counts(rows):
    return {
        'filled_count': sum(r.get('status') in COMPLETE for r in rows),
        'audit_passed_count': sum(r.get('status') == 'draft_filled_native_audit_passed' for r in rows),
        'material_gap_count': sum(r.get('status') == 'draft_filled_system_material_gaps' for r in rows),
        'partially_filled_count': sum(r.get('status') == PARTIAL for r in rows),
        'held_count': sum(r.get('status') == 'held_missing_inputs' for r in rows),
    }


def run_workflow(service, **parameters):
    """One financial pipeline per local checkout; OS releases the lease on exit.

    This is a separate orchestration lock, never the per-operation browser lock
    acquired by child jobs. Status and local-only delivery do not acquire it.
    """
    try:
        context = (file_lock(service.root/'.local/workflow-orchestrator.lock')
                   if parameters.get('execute') is True and parameters.get('phase', 'status') in {'run','all','submit','prepare-submit'}
                   else nullcontext())
        with context:
            return _run_workflow(service, **parameters)
    except RuntimeError as error:
        raise APIError(getattr(error, 'code', 'workflow_runtime'), str(error)) from error


def _run_workflow(service, *, scenario, batch_index, phase='status', sequences=None,
                 tab_id=None, expected_userid=None, execute=False, allow_incomplete=False,
                 delivery_plan=None, output_root=None, legacy_evidence=(), render_excel=False,
                 runtime=None, progress=None, final_delivery=False, submission_authorization=None, workspace=None):
    """Run eligible drafts and optionally refresh delivery, with bounded saved recovery.

    ``all`` adds delivery to draft filling. It grants no approval/payment authority.
    Every recovery is readback-only; a non-matching editor remains blocked.
    """
    if phase in ('submit','prepare-submit'):
        if not submission_authorization:
            raise APIError('workflow_submission_authorization','submit须给绑定本批及当前账号的submission_authorization')
        from submission_batch import run_submissions
        return run_submissions(service,scenario=scenario,batch_index=batch_index,authorization=submission_authorization,
            tab_id=tab_id,expected_userid=expected_userid,sequences=sequences,execute=execute,progress=progress,prepare_only=phase=='prepare-submit',workspace=workspace)
    if phase not in {'status', 'run', 'deliver', 'all'}:
        raise APIError('workflow_phase', 'phase只支持status/run/deliver/all')
    if any(type(value) is not bool for value in [execute, render_excel, final_delivery]):
        raise APIError('workflow_execute', 'execute、render_excel、final_delivery须为布尔值')
    if execute and phase == 'status':
        raise APIError('workflow_execute', 'status不接受execute')
    common = dict(scenario=scenario, batch_index=batch_index, sequences=sequences,
                  tab_id=tab_id, expected_userid=expected_userid, allow_incomplete=allow_incomplete)
    started = time.monotonic()
    inspected = run_batch(service, phase='status', execute=False, **common)
    report = {'schema': 'reimbursement-workflow-v1', 'ok': True, 'as_of_utc': _now(),
              'scenario': scenario, 'batch_index': inspected['batch_index'], 'phase': phase,
              'execute': execute, 'documents': inspected['documents'], 'results': [],
              'recoveries': [], 'issues': [], 'system_write_requested': False,
              'approval_completed': False, 'payment_completed': False, 'pdf_exported': 0,
              'counts_scope':'selected_run_results_only',
              'performance':{'scope':'orchestrator_wall_time_not_browser_cpu_or_speedup',
                             'steps':[{'phase':'status','elapsed_seconds':round(time.monotonic()-started,4)}]}}
    report.update(_counts([]))
    if not execute:
        report.update(state='status_only', **_counts([]))
        if phase in {'all', 'deliver'} and not (delivery_plan and output_root):
            report['needed_inputs'] = ['清单交付须给delivery_plan及output_root']
        return report
    if phase in {'all', 'deliver'} and not (delivery_plan and output_root):
        raise APIError('workflow_delivery_inputs', 'all/deliver须先给delivery_plan和output_root，避免办理后无交付入口')
    if phase in {'all', 'run'} and (not tab_id or not expected_userid):
        raise APIError('workflow_identity', '实际办理须明确当前tab_id和expected_userid')
    if phase in {'all', 'deliver'}:
        from batch_delivery import refresh_delivery
        # Check the frozen delivery input and authorization before any finance
        # action. A business path is data, never executable helper code.
        delivery_preflight = refresh_delivery(service, scenario=scenario, batch_index=batch_index,
            base_delivery_plan=delivery_plan, output_root=output_root,
            expected_userid=expected_userid, legacy_evidence=legacy_evidence,
            check_only=True, render_excel=False)
        if not delivery_preflight.get('ok'):
            raise APIError('workflow_delivery_preflight', '交付输入或引用回执未通过预检，先处理其issues；未开始财务动作')

    # A report is navigation only, kept locally; originals and registered job
    # artifacts remain the authority. No business-directory code is executed.
    index = Path(inspected['batch_index'])
    identity = hashlib.sha256(index.read_bytes() + scenario.encode()).hexdigest()[:12]
    folder = service.root / '.local' / 'workflows' / identity
    run_name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '_' + uuid.uuid4().hex[:8]
    report_path = folder / (run_name + '.json')
    attempted_recoveries = set()

    def checkpoint(event=None):
        report.update(as_of_utc=_now(), **_counts(report['results']))
        report['performance']['elapsed_seconds'] = round(time.monotonic()-started,4)
        report['report_path'] = str(report_path)
        atomic_json(report_path, report)
        atomic_json(folder/'current.json', {'schema': report['schema'], 'report': str(report_path),
                    'sha256': hashlib.sha256(report_path.read_bytes()).hexdigest(), 'as_of_utc': report['as_of_utc']})
        if progress is not None and event is not None:
            progress(event)

    def batch_call(phase, **changes):
        tick = time.monotonic()
        try:
            return run_batch(service, phase=phase, execute=phase!='status', **{**common, **changes})
        finally:
            report['performance']['steps'].append({'phase':phase,'sequences':changes.get('sequences',sequences),
                                                    'elapsed_seconds':round(time.monotonic()-tick,4)})

    def recover(case_id, sequence):
        overview = service.overview(case_id)
        if overview.get('active_jobs'):
            raise APIError('workflow_original_running', '原任务仍运行，只查原result；不并发恢复或重发')
        pending = overview.get('unverified_write') or {}
        if pending.get('operation') not in RECOVERABLE:
            raise APIError('workflow_original_requires_specific_recovery', '原结果须专用恢复核对；通用流程不猜测或重放')
        # A new timestamp alone is not enough: the full original pending identity
        # is retained, so the same uncertain intent cannot loop through retries.
        key = json.dumps({'case_id': case_id, 'pending': pending}, sort_keys=True, ensure_ascii=False)
        if key in attempted_recoveries:
            raise APIError('workflow_recovery_already_attempted', '同一原pending已经核对过，保留现场，不循环恢复')
        attempted_recoveries.add(key)
        recovered = batch_call('recover', sequences=[sequence])
        if not recovered.get('ok') or recovered.get('state') != 'original_saved_independently_verified':
            raise APIError('workflow_recovery_unverified', '原目标未确认持久保存，保留原任务；未重发写入')
        report['recoveries'].append({'case_id': case_id, 'sequence': sequence,
                                     'original_pending': pending, 'result': recovered})
        checkpoint({'event': 'original_saved_independently_verified', 'sequence': sequence, 'case_id': case_id})

    items = inspected['documents']
    if phase in {'all', 'run'}:
        # Inspect the whole selected scope before slicing it into single cases.
        # A pending later row still owns the shared page. Finish that original
        # case first, then return to the remaining selected order.
        originals = [item for item in items if item['recovery_required']]
        if originals:
            original = originals[0]
            try:
                if len(originals) != 1:
                    raise APIError('workflow_multiple_originals', '所选范围存在多个原任务，先逐一核对共享现场，不开始新案')
                recover(original['case_id'], original['sequence'])
                fresh = batch_call('status')
                report['documents'] = fresh['documents']
                if any(item['recovery_required'] for item in fresh['documents']):
                    raise APIError('workflow_original_not_settled', '原任务核对后仍有待恢复状态，不开始新案')
                items = sorted(fresh['documents'], key=lambda item: item['case_id'] != original['case_id'])
            except (ValueError, OSError, KeyError, RuntimeError) as error:
                report['ok'] = False
                report['issues'].append({'case_id': original['case_id'], **_error(error)})
                report['results'].append({'sequence': original['sequence'], 'case_id': original['case_id'],
                                           'status': 'stopped_verify_original', **_error(error)})
                items = []
                checkpoint({'event': 'stopped_verify_original', 'case_id': original['case_id']})
        for item in items:
            case, seq = item['case_id'], item['sequence']
            if workspace and case in workspace.initial_states:
                report['results'].append({'sequence':seq,'case_id':case,**workspace.initial_states[case]})
                checkpoint({'event':'existing_workflow_preserved','sequence':seq,'case_id':case});continue
            if not item['ready_for_draft'] and not item['recovery_required']:
                report['results'].append({**item, 'status': 'held_missing_inputs'})
                checkpoint({'event': 'held_missing_inputs', 'sequence': seq, 'case_id': case})
                continue
            try:
                if workspace:
                    common['tab_id']=workspace.begin_case(case)
                    if item.get('serial') and not item['recovery_required']:
                        workspace.job(case,'read_system',{'mode':'open-draft','serial':item['serial'],
                            'expected_userid':expected_userid,'tab_id':common['tab_id']},'fill-open-original')
                if item['recovery_required']:
                    recover(case, seq)
                # Each case has at most six standard save stages. Eight guards
                # against future accidental loops without replaying any intent.
                for _ in range(8):
                    report['system_write_requested'] = True
                    result = batch_call('run', sequences=[seq])
                    rows = result.get('results', [])
                    if (result.get('ok') and len(rows) == 1 and rows[0].get('case_id') == case
                            and rows[0].get('status') in COMPLETE | {PARTIAL, 'held_missing_inputs'}):
                        report['results'].append(rows[0])
                        checkpoint({'event': 'case_finished', 'sequence': seq,
                                    'case_id': case, 'status': rows[0].get('status')})
                        break
                    # The failed original remains registered. A failed readback
                    # never enters the next case, and no original request is sent
                    # here: the core skips only independently verified stages.
                    current = service.overview(case)
                    if not current.get('unverified_write') and not current.get('active_jobs'):
                        failure = rows[0] if len(rows) == 1 else result
                        raise APIError(failure.get('error_code', 'workflow_step_stopped'),
                                       failure.get('error') or '原步骤未完成；无待核写入，保留原步骤回执')
                    recover(case, seq)
                else:
                    raise APIError('workflow_recovery_limit', '单案恢复次数已达界限，保留原回执')
            except (ValueError, OSError, KeyError, RuntimeError) as error:
                report['ok'] = False
                report['results'].append({'sequence': seq, 'case_id': case,
                                           'status': 'stopped_verify_original', **_error(error)})
                report['issues'].append({'sequence': seq, 'case_id': case, **_error(error)})
                checkpoint({'event': 'stopped_verify_original', 'sequence': seq, 'case_id': case})
                current=service.overview(case)
                global_failure=any(token in str(error) for token in ('登录人','当前账号','身份变化','授权','规范已','SHA改变'))
                if workspace and not current.get('unverified_write') and not current.get('active_jobs') and not global_failure:
                    report['results'][-1]['status']='isolated_case_error'
                    checkpoint({'event':'case_error_isolated','sequence':seq,'case_id':case})
                    continue
                break

    if phase in {'all', 'deliver'}:
        # Even an unresolved finance write may be safely listed in a read-only
        # delivery refresh. It never becomes a filled or exported claim.
        try:
            report['delivery'] = refresh_delivery(service, scenario=scenario,
                batch_index=batch_index, base_delivery_plan=delivery_plan, output_root=output_root,
                expected_userid=expected_userid, legacy_evidence=legacy_evidence,
                in_progress=not final_delivery or not report['ok'], render_excel=render_excel, runtime=runtime)
            report['delivery_counts'] = report['delivery'].get('counts', {})
            if not report['delivery'].get('ok'):
                report['ok'] = False
                report['issues'].append({'stage': 'delivery', 'error': '清单刷新未完成；保留已有单据和交付回执'})
        except (ValueError, OSError, KeyError, RuntimeError, ImportError) as error:
            report['ok'] = False
            report['issues'].append({'stage': 'delivery', **_error(error)})
    report['state'] = ('stopped_with_original_preserved' if not report['ok'] else
                       'delivery_finished' if phase == 'deliver' else
                       'finished_with_gaps' if report['held_count'] or report['material_gap_count']
                       or report['partially_filled_count'] else 'draft_stages_finished')
    checkpoint({'event': report['state']})
    return report
