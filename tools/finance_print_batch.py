"""审批后正式打印的批次合同、原任务恢复与交付编排。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

from assistant_api import APIError, AssistantService, PROJECT, atomic_json, read_json
from material_intake import reject_links
from print_delivery import deliver, fingerprint
from registered_inputs import checked_reference, load_definition
from registered_session import RegisteredSession


def prepare(service, case_id, *, merge_pdf=None, finance_only=None, output_root=None,
            sequences=None):
    cfg, index, auth, definition = load_definition(service, case_id)
    defaults = cfg.get('print_options', {})
    if not isinstance(defaults, dict) or set(defaults) - {'merge_pdf', 'finance_only'}:
        raise APIError('print_options', 'print_options仅接受merge_pdf和finance_only')
    options = {}
    for key, supplied, default in [('merge_pdf', merge_pdf, False), ('finance_only', finance_only, True)]:
        value = supplied if supplied is not None else defaults.get(key, default)
        if type(value) is not bool:
            raise APIError('print_options', key + '必须使用布尔值')
        options[key] = value
    root = output_root if output_root is not None else cfg.get('print_output_root')
    if not isinstance(root, str) or not Path(root).is_absolute():
        raise APIError('print_output_root', '明确给出output_root，或在本批登记print_output_root')
    reject_links(Path(root))
    root = Path(root).resolve()
    if not any(root.is_relative_to(p) for p in service.material_roots):
        raise APIError('print_output_root', '正式打印输出必须位于授权材料根内')
    manifest = checked_reference(service, cfg.get('system_pdf_manifest'))
    value = read_json(manifest)
    if value.get('schema') != 'ustc-system-pdf-batch-v1' or value.get('expected_userid') != cfg['expected_userid'] or not value.get('source'):
        raise APIError('print_manifest', '复用已登记的同账号单据/日期/序号/摘要清单')
    rows = value.get('cases')
    if not isinstance(rows, list) or not rows or len(rows) > 500:
        raise APIError('print_scope', '正式打印批次须包含1至500单')
    if sequences is not None and (not isinstance(sequences, list) or not sequences
            or any(type(n) is not int or n < 1 for n in sequences)
            or len(set(sequences)) != len(sequences)):
        raise APIError('print_sequences', 'sequences必须是不重复的正整数列表')
    selected, seen_cases, seen_serials, seen_sequences, destinations = [], set(), set(), set(), set()
    from finance_pdf_export import export_relative_path
    for row in rows:
        plan = dict(row['export_plan'])
        sequence = int(plan['sequence'])
        if sequences is not None and sequence not in sequences:
            continue
        case = row['case_id']
        _, paths = service.resolve(case)
        form = read_json(paths['form'])
        if (any(plan.get(k) != form.get(k) for k in ('case_id', 'serial'))
                or row.get('amount') != form.get('amount')
                or plan.get('summary') != form['basic']['fields']['t-abst']):
            raise APIError('print_binding', '打印清单与本案单号、金额或摘要不同：' + case)
        if case in seen_cases or plan['serial'] in seen_serials:
            raise APIError('print_scope', '本批案件或单号重复')
        seen_cases.add(case)
        seen_serials.add(plan['serial'])
        seen_sequences.add(sequence)
        plan.update(view_kind='formal_print_api', amount=form['amount'], delivery_mode='rename_only',
                    include_invoice_attachments=not options['finance_only'])
        native_root = root / ('仅财务信息' if options['finance_only'] else '含附件')
        relative = export_relative_path({'case_id': case, 'serial': plan['serial']}, plan)
        destination = str(native_root / relative)
        if destination.casefold() in destinations:
            raise APIError('print_name_collision', '本批正式打印文件名冲突')
        destinations.add(destination.casefold())
        selected.append({'case_id': case, 'serial': plan['serial'], 'amount': form['amount'],
                         'summary': plan['summary'], 'sequence': sequence, 'export_plan': plan,
                         'export_root': str(native_root), 'destination': destination,
                         'form_sha256': hashlib.sha256(paths['form'].read_bytes()).hexdigest()})
    if not selected or sequences is not None and set(sequences) != seen_sequences:
        raise APIError('print_sequences', '所选序号未完整命中已登记打印清单')
    # 来源中的顺序就是交付顺序，不在合并时再次排序。
    spec = {'schema': 'ustc-formal-print-batch-v1', 'case_id': case_id,
            'options': options, 'output_root': str(root), 'sequences': sequences,
            'source': value['source'], 'manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest(),
            'documents': selected}
    return cfg, index, auth, spec


def preview(spec):
    return {'ok': True, 'state': 'formal_print_preview', 'phase': 'print',
            'case_id': spec['case_id'], 'options': spec['options'],
            'documents': len(spec['documents']), 'plan': spec,
            'approval_rechecked': False, 'system_write_requested': False,
            'note': '执行时由学校正式打印入口判断是否可打印；此计划不证明审批完成'}


def _source(service, entry, state):
    job = state.get('export_job_id')
    if not job:
        return None
    checked = service.pdf_export_inspect(entry['case_id'], job)
    request = read_json(service.job_folder(entry['case_id'], job) / 'request.json')
    params = request['parameters']
    if params['export_plan'] != entry['export_plan'] or params['export_root'] != entry['export_root']:
        raise APIError('print_original_options', '原正式打印文件与本次附件选项或命名不同')
    if checked.get('status') != 'ready' or checked.get('kind') != 'system_formal_print' or checked.get('final_print_verified') is not True:
        from finance_pdf_export import print_recovery,digest,encoded
        try:
            recovery=print_recovery(service.job_folder(entry['case_id'],job)/'pdf_export',{
                'case_id':entry['case_id'],'serial':entry['serial'],'tab_id':params['tab_id'],
                'expected_userid':params['expected_userid'],'plan_sha256':digest(encoded(entry['export_plan'])),'destination':entry['destination']})
        except (ValueError,OSError):
            raise APIError('print_original_export', '原正式打印导出尚未核验；保留原job：' + job)
        if recovery.get('kind')=='not_dispatched':return {'status':'not_dispatched','source_job_id':job,'evidence':recovery}
        return {'status':'resume_download','resume_job_id':job}
    return {'status': 'reused', 'pdf': checked['pdf'], 'pdf_sha256': checked['pdf_sha256'],
            'export_job_id': job, 'final_print_verified': True}


def _pending(service, case, state):
    if state.get('action_job_id'):
        result = service.result(case, state['action_job_id'])
        if result.get('status') != 'completed' or result.get('exit_code') != 0:
            raise APIError('print_original_action', '先核对原打印动作，不重发：' + state['action_job_id'])


def run(service, case_id, *, merge_pdf=None, finance_only=None, output_root=None,
        sequences=None, execute=False, progress=None, expected_fingerprint=None):
    if type(execute) is not bool:
        raise APIError('print_execute', 'execute必须使用布尔值')
    cfg, index, auth, spec = prepare(service, case_id, merge_pdf=merge_pdf,
        finance_only=finance_only, output_root=output_root, sequences=sequences)
    if expected_fingerprint is not None and fingerprint(spec) != expected_fingerprint:
        raise APIError('print_input_changed', '排队后打印计划或来源发生变化，未执行')
    if not execute:
        return preview(spec)
    session = RegisteredSession(service, case_id + '-print', cfg, index, auth, progress)
    session.ids = list(dict.fromkeys(session.ids + [e['case_id'] for e in spec['documents']]))
    report_path = session.folder / ('print-' + str(time.time_ns()) + '.json')
    report = {'ok': True, 'phase': 'print', 'case_id': case_id, 'state': 'running',
              'options': spec['options'], 'plan_fingerprint': fingerprint(spec),
              'report_path': str(report_path), 'results': [], 'approval_verified': False,
              'physical_print_requested': False, 'system_write_requested': True, 'business_transport':'native_http'}

    def checkpoint():
        report['pdf_exported'] = sum(bool(r.get('pdf')) for r in report['results'])
        report['held_count'] = sum(r['status'].startswith('held_') for r in report['results'])
        atomic_json(report_path, report)
        atomic_json(session.folder / 'current-print-result.json', report)

    states, paths, cached, recoveries, pending = {}, {}, {}, {}, []
    checkpoint()
    try:
        for entry in spec['documents']:
            case = entry['case_id']
            path = session.folder / 'documents' / (fingerprint(entry) + '.json')
            paths[case] = path
            state = read_json(path) if path.exists() else {}
            states[case] = state
            old = _source(service, entry, state)
            if old and old['status']=='not_dispatched':
                state.update(phase='rejected_before_dispatch',rejected_job_id=old['source_job_id'],recovery_evidence=old['evidence'])
                state.pop('export_job_id',None)
                atomic_json(path,state)
                old=None
            if old:
                (recoveries if old['status']=='resume_download' else cached)[case] = old
            if state and state.get('phase') not in ('returned', 'rejected_wait_approval', 'rejected_before_dispatch'):
                pending.append(case)
            if not state and Path(entry['destination']).exists():
                raise APIError('print_existing_unbound', '目标文件已存在但无对应正式回执，不覆盖')
        # 切换范围或附件开关不能绕过同一批次尚未结束的原现场。
        selected_paths = set(paths.values())
        for old_path in (session.folder / 'documents').glob('*.json'):
            if old_path not in selected_paths and read_json(old_path).get('phase') not in ('returned', 'rejected_wait_approval', 'rejected_before_dispatch'):
                raise APIError('print_other_pending', '另一范围或附件选项仍有原打印任务：' + str(old_path))
        if len(pending) > 1:
            raise APIError('print_pending_scope', '存在多个未结束打印现场，须逐个核对原任务')
        # 一个登记会话承载整个批次；逐单只执行固定HTTP适配器。
        # 已有未知任务先检查原job，不能以新页面或新request_id替代。
        for case in pending:
            _pending(service, case, states[case])
            if case not in cached and case not in recoveries:
                raise APIError('print_original_export', '原接口导出未完成；先核对原job，不重发')
        if len(cached) != len(spec['documents']):
            session.ensure(**({'preserve_original':True} if recoveries else {}))
        by_case={}
        for entry in sorted(spec['documents'],key=lambda e:e['case_id'] not in recoveries):
            case, state, path = entry['case_id'], states[entry['case_id']], paths[entry['case_id']]
            row = {k: entry[k] for k in ('case_id', 'serial', 'summary')}
            row['status'] = 'running'
            by_case[case]=row
            report['results']=[by_case[e['case_id']] for e in spec['documents'] if e['case_id'] in by_case]
            checkpoint()
            if case in cached:
                row.update(cached[case])
                state.update(phase='returned')
                atomic_json(path, state)
                checkpoint()
                continue
            if hashlib.sha256(service.resolve(case)[1]['form'].read_bytes()).hexdigest() != entry['form_sha256']:
                raise APIError('print_form_changed', '运行中本案输入变化')
            Path(entry['export_root']).mkdir(parents=True, exist_ok=True)
            from finance_pdf_export import export_pdf
            export_pdf(read_json(service.resolve(case)[1]['form']),entry['export_plan'],entry['export_root'],
                session.folder/'preview-only',session.state['tab_id'],cfg['expected_userid'])
            if case in recoveries:state['recovery_source_job_id']=recoveries[case]['resume_job_id']
            state.update(phase='export_requested', attempt=state.get('attempt', 0) + 1)
            atomic_json(path, state)
            session.state['last_case_id'] = case
            atomic_json(session.state_path, session.state)
            prefix = 'print-' + fingerprint(entry)[:20] + '-' + str(state['attempt'])
            Path(entry['export_root']).mkdir(parents=True, exist_ok=True)
            def export_enqueued(job):
                state.update(export_job_id=job['job_id'])
                atomic_json(path, state)
            started = time.monotonic()
            result, exported = session.job(case, 'pdf_export', {
                'export_plan': entry['export_plan'], 'export_root': entry['export_root'],
                'tab_id': session.state['tab_id'], 'expected_userid': cfg['expected_userid'],
                'silent': True,**({'resume_job_id':recoveries[case]['resume_job_id']} if case in recoveries else {})}, 'formal-api-pdf', True, request_id=prefix + '-pdf',
                on_enqueued=export_enqueued)
            row['elapsed_seconds'] = round(time.monotonic() - started, 3)
            if exported.get('school_rejected') is True:
                state.update(phase='rejected_wait_approval', rejected_job_id=result['job_id'])
                state.pop('export_job_id', None)
                row.update(status='held_not_printable', issue=exported['school_message'],
                           native_status=exported.get('native_status'), source_job_id=result['job_id'])
            else:
                if (exported.get('ok') is not True or exported.get('kind') != 'system_formal_print'
                        or exported.get('final_print_verified') is not True
                        or exported.get('business_transport') != 'native_http'):
                    raise APIError('print_export_pending', '未取得完整底层接口打印回执')
                state.update(phase='returned')
                row.update(status='exported', pdf=exported['pdf'], pdf_sha256=exported['pdf_sha256'],
                           export_job_id=result['job_id'], final_print_verified=True)
            atomic_json(path, state)
            if progress:
                progress({'event': row['status'], 'case_id': case, 'serial': entry['serial']})
            checkpoint()
        report['delivery'] = deliver(report['results'], spec['output_root'], **spec['options'])
        report.update(state='completed_with_holds' if report['held_count'] else 'completed')
    except (APIError, ValueError, OSError, KeyError, TypeError) as error:
        report.update(ok=False, state='original_task_needs_readback', error=str(error),
                      error_code=getattr(error, 'code', 'formal_print_error'))
        for row in report['results']:
            if row['status'] == 'running':
                row.update(status='held_original_task', issue=str(error))
        known = {r['case_id']: r for r in report['results']}
        report['results'] = [known.get(e['case_id']) or {
            'case_id': e['case_id'], 'serial': e['serial'], 'summary': e['summary'],
            'status': 'held_not_attempted', 'issue': '批次在此单执行前停止：' + str(error)
        } for e in spec['documents']]
        report['recovery'] = {'session_file': str(session.state_path),
            'instruction': '先读原job及报告；保留原工作页，恢复沿相同选项和输出根，不重放未知动作'}
    checkpoint()
    return report


def command(service, request, folder):
    params = request['parameters']
    _, _, _, spec = prepare(service, request['case_id'], **{k: v for k, v in params.items() if k != 'plan_fingerprint'})
    if fingerprint(spec) != params.get('plan_fingerprint'):
        raise APIError('print_input_changed', '正式打印冻结计划与当前输入不符')
    return [service.python, str(PROJECT / 'tools/finance_print_batch.py'), '--request', str(folder / 'request.json')]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True, type=Path)
    args = parser.parse_args()
    request = read_json(args.request)
    service = AssistantService(request['workspace'], request['material_roots'], request['python'])
    params = dict(request['parameters'])
    expected = params.pop('plan_fingerprint')
    from edge_runtime import file_lock
    with file_lock(service.root / '.local/workflow-orchestrator.lock'):
        result = run(service, request['case_id'], execute=request['execute'],
                     expected_fingerprint=expected, **params)
    atomic_json(args.request.parent / 'output.json', result)
    print(json.dumps({k: result.get(k) for k in ('ok', 'state', 'report_path', 'pdf_exported', 'held_count', 'error')}, ensure_ascii=False))
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
