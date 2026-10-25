"""原生列表导出：冻结单号范围、填写导出选项、等待本次唯一下载并归档。"""

from __future__ import annotations
import hashlib
import json
import time
import uuid
from pathlib import Path

from assistant_api import APIError, atomic_json, read_json
from finance_pdf_export import _atomic_new
from registered_session import RegisteredSession
from registered_inputs import checked_reference
from submission import view_plan


def downloaded_file(directory, title, started_at, format_):
    directory = Path(directory)
    if list(directory.glob(title + '*.crdownload')):
        return None
    extensions = {'EXCEL': {'.xls', '.xlsx'}, 'PDF': {'.pdf'}, 'DBF': {'.dbf'}}[format_]
    matches = [
        p
        for p in directory.glob(title + '*')
        if p.is_file()
        and not p.is_symlink()
        and p.suffix.lower() in extensions
        and p.stat().st_mtime >= started_at - 2
    ]
    if len(matches) > 1:
        raise APIError('grid_download_ambiguous', '同一次导出出现多个文件，不猜对应关系')
    if not matches:
        return None
    if not 0 < matches[0].stat().st_size <= 64 * 1024 * 1024:
        raise APIError('grid_download_size', '导出文件为空或超过64MiB')
    return matches[0]


def run_grid_export(service, case_id, cfg, index, auth, *, execute=False, progress=None):
    manifest = checked_reference(service, cfg.get('list_export_manifest'))
    plan = read_json(manifest)
    userid = cfg['expected_userid']
    if (
        plan.get('schema') != 'ustc-grid-export-v1'
        or plan.get('expected_userid') != userid
        or plan.get('format') not in ('EXCEL', 'PDF', 'DBF')
        or not plan.get('source')
    ):
        raise APIError('grid_export_plan', '原生列表导出须明确账号、格式和来源')
    cases = plan['cases']
    serials = []
    states = []
    for row in cases:
        form = read_json(service.resolve(row['case_id'])[1]['form'])
        if row['serial'] != form['serial']:
            raise APIError('grid_export_case_changed', '原生列表单号与案件不符')
        serials.append(row['serial'])
        states.append(
            {'serial': row['serial'], 'review': form.get('review', {}).get('workflow_status')}
        )
    if not serials or len(set(serials)) != len(serials):
        raise APIError('grid_export_scope', '原生导出范围为空或重复')
    output = Path(plan['output_root']).resolve()
    downloads = Path(plan['download_directory']).resolve()
    if not any(output.is_relative_to(r) for r in service.material_roots) or not downloads.is_dir():
        raise APIError('grid_export_paths', '输出根或本机下载目录不可用')
    if not execute:
        return {
            'ok': True,
            'state': 'grid_export_preview',
            'phase': 'export-list',
            'rows': len(serials),
            'format': plan['format'],
            'system_write_requested': False,
        }
    key = hashlib.sha256(
        manifest.read_bytes() + json.dumps(states, sort_keys=True).encode()
    ).hexdigest()[:16]
    session = RegisteredSession(service, case_id + '-table', cfg, index, auth, progress)
    folder = session.folder / key
    folder.mkdir(parents=True, exist_ok=True)
    state_path = folder / 'state.json'
    report_path = folder / 'report.json'
    state = (
        read_json(state_path)
        if state_path.exists()
        else {'title': '报销清单_' + uuid.uuid4().hex[:12], 'phase': 'new'}
    )
    report = {
        'case_id': case_id,
        'phase': 'export-list',
        'report_path': str(report_path),
        'rows': len(serials),
        'format': plan['format'],
        'system_write_requested': False,
    }
    try:
        if state.get('phase') == 'completed':
            target = Path(state['output'])
            if (
                not target.is_file()
                or hashlib.sha256(target.read_bytes()).hexdigest() != state['sha256']
            ):
                raise APIError('grid_export_existing_changed', '既有原生导出文件改变')
            report.update(
                ok=True,
                state='reused',
                output=str(target),
                sha256=state['sha256'],
                reused_completed_run=True,
                live_rechecked=False,
            )
            atomic_json(report_path, report)
            return report
        if state['phase'] not in ('new', 'menu_requested', 'download_requested'):
            raise APIError('grid_export_original_task', '原导出窗口尚未确认，保留原job和窗口')
        if state['phase'] == 'new':
            if list(downloads.glob(state['title'] + '*')):
                raise APIError('grid_export_title_conflict', '本次下载文件名已经存在')
            case = cases[0]['case_id']
            session.begin_case(case)
            session.job(
                case,
                'invoice_verification',
                {
                    'action': 'view',
                    'view': 'in-progress',
                    'expected_userid': userid,
                    'tab_id': session.state['tab_id'],
                },
                'grid-export-scope',
            )
            session.job(
                case,
                'invoice_verification',
                {
                    'action': 'full-grid',
                    'grid': '101',
                    'expected_userid': userid,
                    'tab_id': session.state['tab_id'],
                },
                'grid-export-list-ready',
            )
            form = read_json(service.resolve(case)[1]['form'])
            submission_plan = view_plan(form, userid, plan['source'])
            submission_plan['grid_export'] = {
                'title': state['title'],
                'format': plan['format'],
                'serials': serials,
            }
            state.update(
                phase='menu_requested',
                case_id=case,
                started_at=time.time(),
                submission_plan=submission_plan,
            )
            atomic_json(state_path, state)
        elif state['phase'] == 'menu_requested':
            # 重用原menu请求号及原页；后台任务层决定是否已执行，不另开导出窗口。
            session.ensure(preserve_original=True)
            if session.state.get('last_case_id') != state['case_id']:
                raise APIError('grid_export_original_page', '原列表导出与登记工作页不符')
        if state['phase'] == 'menu_requested':
            case = state['case_id']
            submission_plan = state['submission_plan']

            def menu_enqueued(queued):
                state['menu_job_id'] = queued['job_id']
                atomic_json(state_path, state)

            session.job(
                case,
                'submission',
                {
                    'action': 'grid-export-menu',
                    'plan': submission_plan,
                    'tab_id': session.state['tab_id'],
                },
                'grid-export-menu',
                True,
                request_id='grid-export-' + key + '-menu',
                on_enqueued=menu_enqueued,
            )
            state['phase'] = 'download_requested'
            atomic_json(state_path, state)

            def queued_receipt(queued):
                state['download_job_id'] = queued['job_id']
                atomic_json(state_path, state)

            session.job(
                case,
                'submission',
                {
                    'action': 'grid-export-execute',
                    'plan': submission_plan,
                    'tab_id': session.state['tab_id'],
                },
                'grid-export-execute',
                True,
                request_id='grid-export-' + key + '-execute',
                on_enqueued=queued_receipt,
            )
        deadline = time.monotonic() + 60
        last = None
        stable = 0
        while time.monotonic() < deadline:
            source = downloaded_file(downloads, state['title'], state['started_at'], plan['format'])
            current = (
                (str(source), source.stat().st_size, source.stat().st_mtime_ns) if source else None
            )
            stable = stable + 1 if current and current == last else 0
            last = current
            if stable >= 2:
                break
            time.sleep(0.5)
        else:
            raise APIError(
                'grid_export_download_pending',
                '原生下载尚未完成，沿原文件名继续等待，不重复点击执行',
            )
        raw = source.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        output.mkdir(parents=True, exist_ok=True)
        destination = output / ('快照_' + key) / ('系统原生报销单列表' + source.suffix.lower())
        destination.parent.mkdir(parents=True, exist_ok=True)
        _atomic_new(destination, raw)
        state.update(
            phase='completed', output=str(destination), sha256=digest, download_source=str(source)
        )
        atomic_json(state_path, state)
        report.update(
            ok=True,
            state='completed',
            output=str(destination),
            sha256=digest,
            download_source=str(source),
            download_title=state['title'],
        )
        atomic_json(
            output / '当前列表导出.json',
            {
                'output': str(destination),
                'sha256': digest,
                'report_path': str(report_path),
                'source_states': states,
            },
        )
    except (APIError, ValueError, OSError, KeyError) as error:
        report.update(
            ok=False,
            state='original_task_needs_readback',
            error=str(error),
            error_code=getattr(error, 'code', 'grid_export_error'),
            handoff={
                'state_file': str(state_path),
                'session_file': str(session.state_path),
                'resume_command': 'workflow --case-id '
                + case_id
                + ' --phase export-list --execute',
            },
        )
    atomic_json(report_path, report)
    return report
