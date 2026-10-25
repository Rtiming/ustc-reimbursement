"""只办理已准备到508的本人同意；复用原生动作、任务日志和独立回读。"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from assistant_api import APIError, atomic_json, read_json
from submission import (
    validate_plan,
    verify_applicant_ready,
    verify_applicant_prepared_list,
    verify_applicant_agreement,
    verify_sms_gate,
)
from submission_batch import business_fingerprint


def check_case_binding(service, row):
    case = row['case_id']
    _, paths = service.resolve(case)
    form = read_json(paths['form'])
    if (
        any(row.get(k) != form.get(k) for k in ('serial', 'amount'))
        or row.get('business_fingerprint') != business_fingerprint(form)
        or row.get('invoice_manifest_sha256')
        != hashlib.sha256(paths['invoices'].read_bytes()).hexdigest()
    ):
        raise APIError('applicant_input_drift', '本人批准清单与当前案件输入不同：' + case)
    return paths, form


def validate_manifest(service, manifest, userid, execute=False):
    value = read_json(manifest)
    if (
        value.get('schema') != 'ustc-applicant-batch-v1'
        or value.get('expected_userid') != userid
        or value.get('approval_opinion') != '同意'
        or not value.get('source')
        or not isinstance(value.get('cases'), list)
        or not value['cases']
    ):
        raise APIError('applicant_manifest', '本人批准清单须有明确账号、来源及案件列表')
    ids = [r.get('case_id') for r in value['cases']]
    if len(set(ids)) != len(ids):
        raise APIError('applicant_manifest', '本人批准清单不能重复案件')
    gates = []
    if value.get('applicant_agreement_authorized') is not True:
        gates.append('等待用户最后批量批准安排')
    for row in value['cases']:
        case = row['case_id']
        paths, form = check_case_binding(service, row)
        validate_plan(row['plan'], form)
        if (
            row['plan'].get('expected_userid') != userid
            or row['plan'].get('approval_opinion') != '同意'
        ):
            raise APIError('applicant_plan_identity', '逐单计划账号或意见不同')
        gate = (
            read_json(paths['case']).get('material_context', {}).get('applicant_agreement_gate', {})
        )
        if gate.get('status') == 'deferred_by_user':
            gates.append(case + '：保留稍后共同批准')
        proof = row['preparation_receipt']
        source = service.artifact(proof.get('case_id', case), proof['job_id'], proof['artifact_id'])
        if hashlib.sha256(source.read_bytes()).hexdigest() != proof['sha256']:
            raise APIError('applicant_preparation_changed', '准备待批回执SHA改变：' + case)
        native = read_json(source).get('result', {})
        try:
            verify_applicant_ready(native, row['serial'], userid)
        except ValueError:
            verify_applicant_prepared_list(native, row['serial'], userid, row['amount'])
    if execute and gates:
        raise APIError('applicant_wait_for_user', '；'.join(gates))
    return value, gates


def record_verification_completion(service, entry, userid, job, gate):
    """本人完成验证码可能由原生回调直接提交；只登记新观察，不再次点同意。"""
    case = entry['case_id']
    _, paths = service.resolve(case)
    if not gate or gate.get('case_id') != case:
        raise APIError('applicant_gate_receipt', '缺少本案原短信停点')
    old = service.artifact(case, gate['job_id'], gate['artifact_id'])
    if hashlib.sha256(old.read_bytes()).hexdigest() != gate['sha256']:
        raise APIError('applicant_gate_changed', '原短信停点回执变化')
    before = read_json(old)
    verify_sms_gate(before['result'], entry['serial'], userid)
    artifact = next(a for a in job['artifacts'] if a['name'] == 'output.json')
    after_path = service.artifact(case, job['job_id'], artifact['artifact_id'])
    after = read_json(after_path)
    if after.get('executed_at_utc', '') <= before.get('executed_at_utc', '') or after.get(
        'tab_id'
    ) != before.get('tab_id'):
        raise APIError(
            'applicant_verification_readback', '本人验证后须在原标签独立回读新的实际状态'
        )
    with service.mutex(case):
        if service.active_jobs(case) or (paths['state'].parent / 'pending_write.json').exists():
            raise APIError(
                'applicant_verification_pending', '仍有其他未完成任务，不能按本人验证续办登记'
            )
        form = read_json(paths['form'])
        verify_applicant_agreement(
            after['result'],
            entry['serial'],
            userid,
            entry['amount'],
            form['basic'].get('teacher_name'),
        )
        proof = {
            'case_id': case,
            'job_id': job['job_id'],
            'artifact_id': artifact['artifact_id'],
            'sha256': artifact['sha256'],
        }
        receipt = (
            paths['state'].parent
            / 'receipts'
            / ('applicant-verification-' + str(time.time_ns()) + '.json')
        )
        atomic_json(
            receipt,
            {
                'outcome': 'observed_user_verification_continuation',
                'original_sms_gate': gate,
                'native_evidence': proof,
                'financial_action_replayed': False,
            },
        )
        form.setdefault('review', {}).update(
            workflow_started=True, workflow_status='submitted_to_approval', node=''
        )
        state = read_json(paths['state'])
        state.update(
            status='submitted_to_approval',
            submission_completed=True,
            node='',
            operator_last_readback=str(after_path),
            verification_continuation_receipt=str(receipt),
        )
        atomic_json(paths['form'], form)
        atomic_json(paths['state'], state)
    return proof


def record_existing_submission(service, case, userid, reference):
    """已有后续审批按原生列表同步本地阶段，不发起批准、不解除任何pending。"""
    source_case = reference.get('case_id', case)
    path = service.artifact(source_case, reference['job_id'], reference['artifact_id'])
    if hashlib.sha256(path.read_bytes()).hexdigest() != reference['sha256']:
        raise APIError('submission_observation_changed', '后续审批观察回执SHA不符')
    envelope = read_json(path)
    native = envelope.get('result', {})
    request = read_json(service.job_folder(source_case, reference['job_id']) / 'request.json')
    parameters = request.get('parameters', {})
    independent = (
        request.get('operation') == 'read_system'
        and parameters.get('mode', 'readback') == 'readback'
    ) or (
        request.get('operation') == 'submission' and parameters.get('action') == 'status-readback'
    )
    if not independent or envelope.get('phase') != 'script_completed':
        raise APIError(
            'submission_observation_source', '须引用独立原生状态回读，不能用保存请求登记已提交'
        )
    _, paths = service.resolve(case)
    with service.mutex(case):
        if service.active_jobs(case) or (paths['state'].parent / 'pending_write.json').exists():
            raise APIError(
                'submission_observation_pending', '待核写入只能沿原动作核对，不能用状态观察解除'
            )
        form = read_json(paths['form'])
        verify_applicant_agreement(
            native, form['serial'], userid, form['amount'], form['basic'].get('teacher_name')
        )
        if (
            form.get('review', {}).get('workflow_started') is True
            and form['review'].get('workflow_status') == 'submitted_to_approval'
        ):
            return {'already_recorded': True}
        receipt = (
            paths['state'].parent
            / 'receipts'
            / ('observed-submission-' + str(time.time_ns()) + '.json')
        )
        atomic_json(
            receipt,
            {
                'case_id': case,
                'serial': form['serial'],
                'before_review': form.get('review', {}),
                'native_evidence': reference,
                'observed_at_utc': envelope.get('executed_at_utc'),
                'financial_action_requested': False,
            },
        )
        form.setdefault('review', {}).update(
            workflow_started=True, workflow_status='submitted_to_approval', node=''
        )
        state = read_json(paths['state'])
        state.update(
            status='submitted_to_approval',
            submission_completed=True,
            node='',
            submission_observation_receipt=str(receipt),
        )
        atomic_json(paths['form'], form)
        atomic_json(paths['state'], state)
    return {
        'observed_submission_recorded': True,
        'receipt': str(receipt),
        'financial_action_requested': False,
    }


def run_applicant_batch(service, session, manifest, *, execute=False, progress=None):
    manifest = Path(manifest)
    userid = session.cfg['expected_userid']
    value, gates = validate_manifest(service, manifest, userid, execute)
    fingerprint = hashlib.sha256(manifest.read_bytes()).hexdigest()
    if not execute:
        return {
            'ok': True,
            'state': 'applicant_batch_preview',
            'phase': 'approve',
            'case_id': session.case,
            'document_count': len(value['cases']),
            'system_write_requested': False,
            'gates': gates,
            'performance_target_seconds': 300,
            'performance_verified': False,
            'manifest_sha256': fingerprint,
        }
    folder = session.folder / ('approvals-' + fingerprint[:16])
    folder.mkdir(parents=True, exist_ok=True)
    if all(
        (folder / (row['case_id'] + '.json')).exists()
        and read_json(folder / (row['case_id'] + '.json')).get('phase') == 'completed'
        for row in value['cases']
    ):
        current = folder / 'current.json'
        if current.exists():
            previous = read_json(Path(read_json(current)['report_path']))
            if (
                previous.get('state') == 'completed'
                and previous.get('manifest_sha256') == fingerprint
            ):
                return {**previous, 'reused_completed_run': True, 'live_rechecked': False}
    report_path = folder / ('report-' + str(time.time_ns()) + '.json')
    start = time.monotonic()
    timing_path = folder / 'timing.json'
    prior_timing = read_json(timing_path) if timing_path.exists() else {}
    prior_seconds = prior_timing.get('active_seconds', 0)
    timing_complete = prior_timing.get('closed', True) and prior_timing.get('complete', True)
    report = {
        'ok': True,
        'state': 'running',
        'phase': 'approve',
        'case_id': session.case,
        'results': [],
        'report_path': str(report_path),
        'manifest_sha256': fingerprint,
        'performance_target_seconds': 300,
        'system_write_requested': False,
        'leadership_approval_completed': False,
        'payment_completed': False,
    }

    def checkpoint():
        report['run_elapsed_seconds'] = round(time.monotonic() - start, 3)
        report['elapsed_seconds'] = round(prior_seconds + report['run_elapsed_seconds'], 3)
        report['submitted_count'] = sum(
            r.get('status') in ('submitted_verified', 'already_submitted_verified')
            for r in report['results']
        )
        report['performance_verified'] = report['state'] == 'completed' and timing_complete
        report['timing_complete'] = timing_complete
        report['within_target'] = (
            report['elapsed_seconds'] <= 300 if report['performance_verified'] else None
        )
        atomic_json(report_path, report)
        atomic_json(folder / 'current.json', {'report_path': str(report_path)})
        atomic_json(
            timing_path,
            {
                'active_seconds': report['elapsed_seconds'],
                'closed': report['state'] != 'running',
                'complete': timing_complete,
                'human_wait_excluded': True,
            },
        )

    def read(case, label, predicate=None):
        for _ in range(12):
            job, native = session.job(
                case, 'read_system', {'mode': 'readback', 'tab_id': session.state['tab_id']}, label
            )
            if (
                native.get('identity', {}).get('userid') != userid
                or native.get('projid') != 'WF_YBX'
            ):
                raise APIError('applicant_identity', '回读身份变化')
            try:
                if predicate:
                    predicate(native)
                return job, native
            except ValueError:
                if native.get('visibleDialogs'):
                    raise
                time.sleep(0.2)
        raise APIError('applicant_readback', '页面尚未到预期节点，保留原任务')

    def record(case, job, outcome, label):
        artifact = next(a for a in job['artifacts'] if a['name'] == 'output.json')
        session.job(
            case,
            'record_result',
            {
                'kind': 'ack-write',
                'job_id': job['job_id'],
                'artifact_id': artifact['artifact_id'],
                'outcome': outcome,
                'note': '本人批准流水线独立回读：' + label,
            },
            'approve-ack',
        )
        return {
            'case_id': case,
            'job_id': job['job_id'],
            'artifact_id': artifact['artifact_id'],
            'sha256': artifact['sha256'],
        }

    def action(case, plan, name, request_id=None):
        return session.job(
            case,
            'submission',
            {'action': name, 'plan': plan, 'tab_id': session.state['tab_id']},
            'approve-' + name,
            True,
            request_id=request_id,
        )

    entries = value['cases']
    session.ids = list(dict.fromkeys(session.ids + [r['case_id'] for r in entries]))
    active = []
    for row in entries:
        state_path = folder / (row['case_id'] + '.json')
        saved = read_json(state_path) if state_path.exists() else {}
        if service.overview(row['case_id']).get('unverified_write') or saved.get('phase') in (
            'open_requested',
            'agreement_requested',
            'verification_required',
        ):
            active.append(row)
    if len(active) > 1:
        raise APIError('applicant_multiple_pending', '多个原单有未结束动作，先按各原任务回读')
    entries = active + [r for r in entries if r not in active]
    checkpoint()
    try:
        if active:
            session.ensure(preserve_original=True)
            if session.state.get('last_case_id') != active[0]['case_id']:
                raise APIError('applicant_original_page', '待核原单与登记工作页不同，不新建现场')
        else:
            session.begin_case(entries[0]['case_id'])
        for entry in entries:
            case = entry['case_id']
            state_path = folder / (case + '.json')
            saved = read_json(state_path) if state_path.exists() else {'phase': 'new', 'attempt': 1}
            row = {
                'case_id': case,
                'serial': entry['serial'],
                'amount': entry['amount'],
                'status': 'running',
            }
            report['results'].append(row)
            if saved.get('phase') == 'completed':
                row.update(
                    status='already_submitted_verified', native_evidence=saved['native_evidence']
                )
                checkpoint()
                continue
            # 冻结的业务输入按单复核，批准前不再重开草稿、预算或分类。
            if hashlib.sha256(manifest.read_bytes()).hexdigest() != fingerprint:
                raise APIError('applicant_manifest_drift', '运行中批准清单发生变化')
            check_case_binding(service, entry)
            plan = {**entry['plan'], 'applicant_agreement_authorized': True}
            session.state['last_case_id'] = case
            atomic_json(session.state_path, session.state)
            request_id = 'applicant-' + fingerprint[:16] + '-' + case + '-' + str(saved['attempt'])
            pending = service.overview(case).get('unverified_write')
            if pending and pending.get('operation') != 'submission:applicant-agree':
                raise APIError('applicant_other_pending', '先恢复原准备动作，不跨阶段批准')
            dispatched = None
            if saved['phase'] not in ('agreement_requested', 'verification_required'):
                opened = False
                if saved['phase'] == 'open_requested':
                    _, native = read(case, 'approve-recover-open')
                    try:
                        verify_applicant_ready(native, entry['serial'], userid)
                        opened = True
                    except ValueError:
                        pass
                saved['phase'] = 'open_requested'
                atomic_json(state_path, saved)
                if not opened:
                    action(case, plan, 'applicant-open', request_id + '-open')
                saved['phase'] = 'agreement_requested'
                atomic_json(state_path, saved)
                report['system_write_requested'] = True
                checkpoint()
                _, dispatched = action(case, plan, 'applicant-agree', request_id + '-agree')
            elif saved['phase'] == 'agreement_requested':
                # 同一request_id复用原job；即使进程在入队前退出也不另发一个意图。
                # 已有pending时先独立读真实结果；原job超时不能迫使重发。
                if not pending:
                    _, dispatched = action(case, plan, 'applicant-agree', request_id + '-agree')
            # 正常返回已经等待离开本人页；仅短信/恢复分支额外读取当前现场。
            if dispatched and dispatched.get('verification_required') is False:
                job, native = None, {}
            else:
                job, native = read(case, 'approve-after-action')
            try:
                verify_sms_gate(native, entry['serial'], userid)
            except ValueError:
                pass
            else:
                if service.overview(case).get('unverified_write'):
                    saved['verification_gate'] = record(
                        case, job, 'not_applied', '原生短信前置校验出现，审批过程未启动'
                    )
                saved['phase'] = 'verification_required'
                atomic_json(state_path, saved)
                row.update(
                    status='verification_required',
                    stage='sms',
                    resume_command='workflow --case-id '
                    + session.case
                    + ' --phase approve --execute',
                )
                if value.get('show_verification_interface') is True:
                    from mac_edge_bridge import show_verification, status

                    session.recover_frozen()
                    row['verification_interface'] = show_verification(
                        session.state['tab_id'],
                        tab_id_namespace=status()['tab_id_namespace'],
                        expected_window_id=session.state['window_id'],
                        serial=entry['serial'],
                        expected_userid=userid,
                    )
                report.update(ok=False, state='verification_required')
                checkpoint()
                return report
            if saved['phase'] == 'verification_required' and str(native.get('node')) == '508':
                verify_applicant_ready(native, entry['serial'], userid)
                saved.update(phase='agreement_requested', attempt=saved['attempt'] + 1)
                atomic_json(state_path, saved)
                _, dispatched = action(
                    case,
                    plan,
                    'applicant-agree',
                    'applicant-'
                    + fingerprint[:16]
                    + '-'
                    + case
                    + '-'
                    + str(saved['attempt'])
                    + '-agree',
                )
            _, paths = service.resolve(case)
            form = read_json(paths['form'])
            job, native = action(case, plan, 'status-readback')
            try:
                verify_applicant_agreement(
                    native,
                    entry['serial'],
                    userid,
                    entry['amount'],
                    form['basic'].get('teacher_name'),
                )
            except ValueError:
                job, native = read(
                    case,
                    'approve-persisted',
                    lambda n: verify_applicant_agreement(
                        n,
                        entry['serial'],
                        userid,
                        entry['amount'],
                        form['basic'].get('teacher_name'),
                    ),
                )
            if service.overview(case).get('unverified_write'):
                proof = record(case, job, 'saved', '同号同金额已进入后续审批；不代表领导批准或付款')
            else:
                if saved.get('verification_gate'):
                    proof = record_verification_completion(
                        service, entry, userid, job, saved['verification_gate']
                    )
                elif (
                    read_json(paths['form']).get('review', {}).get('workflow_status')
                    != 'submitted_to_approval'
                ):
                    raise APIError(
                        'applicant_missing_ack', '原动作未有pending且本案未登记批准完成，保留现场'
                    )
                else:
                    artifact = next(a for a in job['artifacts'] if a['name'] == 'output.json')
                    proof = {
                        'case_id': case,
                        'job_id': job['job_id'],
                        'artifact_id': artifact['artifact_id'],
                        'sha256': artifact['sha256'],
                    }
            saved.update(phase='completed', native_evidence=proof)
            atomic_json(state_path, saved)
            row.update(status='submitted_verified', native_evidence=proof)
            checkpoint()
            if progress:
                progress(row)
        report['state'] = 'completed'
        checkpoint()
        return report
    except (APIError, ValueError, OSError, KeyError) as error:
        if report['results'] and report['results'][-1].get('status') == 'running':
            report['results'][-1].update(
                status='held_original_task',
                error_code=getattr(error, 'code', 'applicant_error'),
                error=str(error),
            )
        report.update(
            ok=False,
            state='original_task_needs_readback',
            error_code=getattr(error, 'code', 'applicant_error'),
            error=str(error),
            handoff={
                'case_id': session.state.get('last_case_id') if hasattr(session, 'state') else None,
                'session_file': str(session.state_path),
                'resume_command': 'workflow --case-id '
                + session.case
                + ' --phase approve --execute',
                'action': '读取原job/pending和本报告；不换请求号重放',
            },
        )
        checkpoint()
        return report
