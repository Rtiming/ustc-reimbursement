#!/usr/bin/env python3
"""独立任务执行进程；与助手的stdio生命周期分开，不自动重试。"""
import hashlib
import json
import os
import signal
import subprocess
import sys
import traceback
from contextlib import nullcontext
from pathlib import Path
from assistant_api import APIError, AssistantService, PROJECT, atomic_json, read_json
from operator_support import child_env, stamp
from platform_runtime import start_command
from assistant_contract import MATERIAL_OPERATIONS


def json_objects(text):
    decoder, found, pos = json.JSONDecoder(), [], 0
    while pos < len(text):
        start = text.find('{', pos)
        if start < 0: break
        try:
            value, used = decoder.raw_decode(text[start:])
            found.append(value); pos = start+used
        except ValueError: pos = start+1
    return found


def retain_unverified_cleanup(service, case_id, folder, result, cleanup):
    if cleanup.get('ok') is not False:
        return
    result.update(status='unknown', recovery_required=True, command_tree_termination_verified=False)
    names = ['case-'+case_id+'.lock'] if result.get('material_task') or result.get('local_material_task') else ['browser.lock', 'case-'+case_id+'.lock']
    for name in names:
        path = service.root/'.local/assistant_locks'/name
        if path.exists():
            lock = read_json(path)
            if lock.get('pid') == os.getpid():
                atomic_json(path, {**lock, 'retain_for_recovery': True, 'job_id': folder.name,
                                   'reason': 'command_tree_cleanup_unverified'})


def run(request_file, frozen_request=None):
    folder = request_file.parent
    request = frozen_request if frozen_request is not None else read_json(request_file)
    service = AssistantService(request['workspace'], request['material_roots'], request['python'])
    material_task = request['operation'] in MATERIAL_OPERATIONS
    ocr_task = request['operation'] == 'material_ocr'
    external = request['execute'] and not material_task and not (request['operation']=='invoice' and request['parameters'].get('action')=='read')
    result = {'status': 'failed', 'started_at_utc': stamp(), 'system_result_verified': False, 'external_action_requested': external}
    if material_task:
        result.update(material_task=True, system_write=False, filesystem_action_requested=request['execute'])
        if not ocr_task:
            result['local_material_task'] = True
    try:
        browser_operation = not material_task and (request['operation'] in ['tabs', 'read_system', 'access', 'invoice_verification', 'invoice_selection', 'invoice_retry'] or request['execute'])
        with service.mutex(request['case_id'], wait=3), (service.mutex(request['case_id'], wait=3, browser=True) if browser_operation else nullcontext()):
            from finance_open_draft import is_open_draft, ensure_globally_idle,pending_read_reference
            if is_open_draft(request):
                a=request['parameters'];recovery=pending_read_reference(a)
                ensure_globally_idle(service, request['case_id'], folder.name, target_tab_id=a.get('tab_id'),**({'recovery_source':recovery}if recovery else{}))
            if service.revision(request['case_id']) != request['revision']: raise APIError('revision_conflict', '排队后案件输入已变，任务未执行；overview核对')
            _, paths = service.resolve(request['case_id'])
            receipts = paths['state'].parent/'receipts'
            before = set(receipts.rglob('*.json')) if not material_task and receipts.exists() else set()
            cmd = service.command(request, folder)
            atomic_json(folder/'running.json', {'worker_pid': os.getpid()})
            atomic_json(folder/'status.json', {**result, 'status': 'running', 'operation': request['operation']})
            timed_out = False
            with (folder/'stdout.log').open('w', encoding='utf-8') as out, (folder/'stderr.log').open('w', encoding='utf-8') as err:
                process, command_owner = start_command(cmd, cwd=PROJECT, env=child_env(), stdout=out, stderr=err)
                # worker退出不代表它已派发的命令退出；恢复时分别核对，不能据父PID删锁。
                atomic_json(folder/'running.json', {'worker_pid': os.getpid(), 'command_pid': process.pid,
                                                  'command_started_at_utc': stamp()})
                try:
                    try: code = process.wait(timeout=300)
                    except subprocess.TimeoutExpired:
                        timed_out = True
                        if command_owner is not None:
                            result['command_tree_cleanup'] = command_owner.close()
                            command_owner = None
                            retain_unverified_cleanup(service, request['case_id'], folder, result, result['command_tree_cleanup'])
                        else:
                            os.killpg(process.pid, signal.SIGKILL)
                        code = process.wait(timeout=5)
                finally:
                    if command_owner is not None:
                        result['command_tree_cleanup'] = command_owner.close()
                        retain_unverified_cleanup(service, request['case_id'], folder, result, result['command_tree_cleanup'])
            values = json_objects((folder/'stdout.log').read_text(encoding='utf-8'))
            result.update(status='unknown' if result.get('recovery_required') or timed_out and external else 'failed' if timed_out or code else 'completed',
                          exit_code=code, command_timed_out=timed_out, messages=values[-3:])
            if material_task and request['execute'] and timed_out:
                result.update(status='unknown', recovery_required=True)
            error_text = (folder/'stderr.log').read_text(encoding='utf-8')
            if code or timed_out:
                native_error = None
                if not error_text.strip() and (folder/'output.json').is_file():
                    try:
                        envelope = read_json(folder/'output.json')
                        body = envelope.get('result', envelope)
                        if isinstance(body, dict) and isinstance(body.get('error'), str):
                            native_error = body['error']
                    except (OSError, ValueError, AttributeError):
                        pass  # 无有效回执时保留原退出/超时边界，不推测原生结果。
                result['error'] = error_text[-3000:] if error_text.strip() else native_error[-3000:] if native_error else (
                    '操作超时，先回读，不盲重试' if timed_out else f'操作失败（退出码{code}），先检查回执，不盲重试')
            candidates = sorted(set(receipts.rglob('*.json'))-before) if not material_task and receipts.exists() else []
            for p in [folder/'output.json', folder/'activity.docx', folder/'render/render_receipt.json']:
                if p.exists(): candidates.append(p)
            # 新正式能力只登记固定子回执，供同案SHA回读/恢复；不扫描任意路径。
            if request['operation'] == 'invoice_selection':
                for name in ['output-snapshot.json', 'output-before.json', 'output-native.json', 'output-after.json', 'output-after-error.json']:
                    p = folder/name
                    if p.exists(): candidates.append(p)
            elif request['operation'] == 'invoice_sources':
                p = folder/'output.audit-native.json'
                if p.exists(): candidates.append(p)
            elif is_open_draft(request):
                from finance_open_draft import ARTIFACTS
                for name in ARTIFACTS:
                    p = folder/name
                    if p.exists(): candidates.append(p)
            elif request['operation'] == 'pdf_export':
                for name in ['intent.json','export_receipt.json','failure.json']:
                    p = folder/'pdf_export'/name
                    if p.exists(): candidates.append(p)
                if values:
                    atomic_json(folder/'output.json', values[-1])
                    candidates.append(folder/'output.json')
            elif material_task:
                if ocr_task:
                    from material_ocr_service import collect_artifacts, result_summary
                else:
                    from material_intake_service import collect_artifacts, result_summary
                value = values[-1] if values else None
                candidates.extend(collect_artifacts(service, request, folder, value))
                result['messages'] = [result_summary(value)]
                if value.get('ok') is False and result['status'] != 'unknown':
                    result['status'] = 'failed'
                    detail = value.get('error', {})
                    if isinstance(detail, dict):
                        result.update(error=detail.get('message', '材料任务失败'), error_code=detail.get('code', 'material_intake_failed'))
            elif request['operation'] == 'invoice_retry':
                for name in ['native.json', 'inspection.json']:
                    p = folder/name
                    if p.exists(): candidates.append(p)
            artifacts = []
            for i, p in enumerate(candidates):
                artifacts.append({'artifact_id': 'a'+str(i), 'name': p.name, 'path': str(p.resolve()), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'format': p.suffix[1:]})
            result['artifacts'] = artifacts
            pending = paths['state'].parent/'pending_write.json'
            result['pending_write'] = read_json(pending) if not material_task and pending.exists() else None
            if material_task: result['next'] = '通过artifact_id查看材料或OCR回执；OCR只是候选文字，不代表核定金额或财务完成'
            elif pending.exists() or (timed_out and external): result['next'] = '先独立readback核对，再record_result ack-write；不重放原写入'
            else: result['next'] = 'overview读取当前版本、缺口和下一步；completed仅表示程序退出成功'
            if result.get('recovery_required'):
                result['next'] = '命令后代退出未能核验；保留运行锁和原请求，核对进程及业务回执，不重放'
            if request['operation'] == 'pdf_export':
                if request['execute']:
                    result['next_action'] = {'tool':'reimbursement_pdf_export_inspect',
                                             'arguments':{'case_id':request['case_id'],'job_id':folder.name},
                                             'reason':'只读原导出任务，不换目录或请求号重导'}
                else:
                    result['next_action'] = {'tool':'reimbursement_pdf_export',
                                             'arguments':{'case_id':request['case_id'],**request['parameters'],'execute':True},
                                             'needed_inputs':['request_id'],
                                             'reason':'核对预览和本次导出授权后，以独立的实际执行请求号导出；预览不表示已读取PDF'}
                if values and values[-1].get('read_attempted') and values[-1].get('executed') is None:
                    result['status'] = 'unknown'
                    result['next'] = 'PDF读取中断，使用pdf_export_inspect检查原回执；不重发导出'
    except Exception as e:
        result.update(status='unknown' if result.get('recovery_required') else 'failed', error=str(e), error_code=getattr(e, 'code', 'operation_error'), next='读取错误及overview后处理；不自动重试')
        traceback.print_exc(file=sys.stderr)
    if material_task:
        if ocr_task:
            body = result.get('messages', [{}])[-1] if result.get('messages') else {}
            original = request['parameters'].get('ocr_job_id', folder.name)
            if body.get('state') == 'waiting_original_remote' or result['status'] in {'failed', 'unknown'}:
                result['next_action'] = {'tool': 'reimbursement_material_ocr_resume',
                    'arguments': {'case_id': request['case_id'], 'ocr_job_id': original},
                    'needed_inputs': ['request_id'], 'reason': '只接续原OCR任务，不新建计算'}
            elif body.get('state') == 'no_ocr_needed' or body.get('can_execute') is False:
                result['next_action'] = {'tool': 'reimbursement_ocr_status', 'arguments': {},
                    'reason': '没有缺文字页时无需识别；提供方缺失时先配置，不直接执行'}
            elif not request['execute']:
                result['next_action'] = {'tool': 'reimbursement_material_ocr',
                    'arguments': {'case_id': request['case_id'], 'request': request['parameters']['request'], 'execute': True},
                    'needed_inputs': ['request_id'], 'reason': '核对候选及OCR配置后使用独立执行请求号'}
            else:
                result['next_action'] = {'tool': 'reimbursement_result', 'arguments': {'case_id': request['case_id'], 'job_id': folder.name},
                    'reason': '读文字证据、回接清单和票面候选；不把OCR值当作人工确认'}
        elif result['status'] == 'completed' and not request['execute']:
            result['next_action'] = {'tool':'reimbursement_material_intake',
                                     'arguments':{'case_id':request['case_id'], 'request':request['parameters']['request'], 'execute':True},
                                     'needed_inputs':['request_id'], 'reason':'核对预览后使用独立的实际执行请求号；预览没有归档原件'}
        else:
            result['next_action'] = {'tool':'reimbursement_result', 'arguments':{'case_id':request['case_id'], 'job_id':folder.name},
                                     'reason':'按原任务artifact_id只读查看回执；不换请求号或输出目录重放未知结果'}
        if result['status'] != 'completed':
            result['next'] = ('检查同一OCR job的日志及原workflow/run；不重新提交未知计算' if ocr_task else
                              '检查同一材料job的日志和原输出receipt.json；不转财务回读，不换请求号或目录重放未知结果')
    result['finished_at_utc'] = stamp()
    atomic_json(folder/'status.json', result)


if __name__ == '__main__': run(Path(sys.argv[1]).resolve())
