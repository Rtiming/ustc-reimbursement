"""Serial, remarks-only corrections through existing registered finance jobs.

Preview validates local sources only. Execution never creates a draft, audits,
changes payments, or repeats an existing save request.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from contextlib import nullcontext
from pathlib import Path

from assistant_api import APIError, AssistantService, atomic_json, read_json
from batch_workflow import DraftBatch, _json_path, _verified_file
from finance_open_draft import verify_preview
from finance_basic_verify import verify_basic_fields
from edge_runtime import file_lock
from operator_support import stamp
from validate_config import load_context, validate_basic_fields

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _form_with_remarks(form, remarks):
    result = copy.deepcopy(form)
    result['basic']['fields']['t-cl_abst'] = remarks
    return result


def _protected_form_sha(form):
    result = _form_with_remarks(form, '')
    return hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _inputs(service, manifest_path, batch_index, sequences):
    path = _json_path(service, str(manifest_path)); manifest = read_json(path)
    if (manifest.get('schema') != 'ustc-contact-amendments-v1' or manifest.get('status') != 'prepared_local_only'
            or manifest.get('system_write') is not False or manifest.get('form_modified') is not False
            or manifest.get('allowed_form_fields') != ['t-cl_abst']):
        raise APIError('contact_manifest', '须用只允许t-cl_abst且未改系统/案件的正式联系人修正清单')
    for key in ['batch_index', 'source_input', 'new_input', 'new_plan', 'new_index']:
        item = manifest[key]; _verified_file(service, item['path'], item['sha256'])
    for item in manifest['contact_reviews']:
        _verified_file(service, item['path'], item['sha256'])
    index_path = _json_path(service, str(batch_index))
    if index_path != Path(manifest['new_index']['path']).resolve() or digest(index_path) != manifest['new_index']['sha256']:
        raise APIError('contact_index', '--batch-index须严格绑定manifest的新正式索引及SHA')
    index = read_json(index_path)
    if index.get('schema') not in ['ustc-reviewed-equal-amount-plan-index-v1', 'ustc-reviewed-equal-amount-plan-index-v2', 'ustc-reviewed-batch-plan-index-v1']:
        raise APIError('contact_index', '不支持该正式索引类型')
    old_rows = read_json(Path(manifest['batch_index']['path'])).get('documents', [])
    profile = manifest['profile']
    library_root = manifest.get('library_root')
    if not isinstance(library_root, str) or not Path(library_root).is_absolute():
        raise APIError('contact_library_root', 'manifest须明确当前设备规范库绝对library_root，不猜源码相对位置')
    current = service.standards_read(library_root, profile['profile_id'])
    if current['sha256'] != profile['sha256'] or current['profile']['revision'] != profile['revision']:
        raise APIError('contact_rules_changed', '联系人修正规范已变，须重新准备正式清单')
    wanted = None if sequences is None else set(sequences)
    if wanted is not None and (not wanted or any(type(n) is not int or n <= 0 for n in wanted)):
        raise APIError('contact_sequences', '序号须为非空正整数列表')
    indexed = {}
    for row in index.get('documents', []):
        seq = row.get('index')
        if type(seq) is not int or seq <= 0 or seq in indexed:
            raise APIError('contact_index', '正式索引序号缺失或重复')
        indexed[seq] = row
    rows, seen, cases = [], set(), set()
    for amendment in manifest.get('amendments', []):
        seq, case = amendment.get('sequence'), amendment.get('case_id')
        if type(seq) is not int or seq <= 0 or seq in seen or case in cases:
            raise APIError('contact_manifest', '修正序号/案件缺失或重复')
        seen.add(seq);cases.add(case)
        if wanted is not None and seq not in wanted: continue
        row = indexed.get(seq, {})
        old = [r for r in old_rows if r.get('index') == seq]
        if (row.get('case_id') != case or amendment.get('before_remarks_source') != 'frozen_index_not_live'
                or len(old) != 1 or old[0].get('case_id') != case or old[0].get('remarks') != amendment.get('before_remarks')
                or amendment.get('financial_fields_unchanged') is not True or amendment.get('form_modified') is not False):
            raise APIError('contact_binding', '修正清单与正式索引案件或允许字段不符')
        for item in amendment['evidence_refs'] + amendment['review_refs']:
            _verified_file(service, item['path'], item['sha256'])
        for key in ['normalized_input', 'official_plan']:
            item = amendment[key]
            if row.get(key) != item['path'] or row.get(key + '_sha256') != item['sha256']:
                raise APIError('contact_binding', '逐案新输入/计划未与正式索引绑定')
            _verified_file(service, item['path'], item['sha256'])
        after = amendment.get('after_remarks')
        if row.get('remarks') != after:
            raise APIError('contact_binding', '新备注与正式索引不符')
        plan = read_json(Path(amendment['official_plan']['path']))
        normalized = read_json(Path(amendment['normalized_input']['path']))
        selection = plan.get('rule_selection', {})
        forms = [form for doc in plan.get('plan', {}).get('documents', []) for form in doc.get('forms', [])]
        if (normalized.get('case_id') != case or plan.get('input_evidence') != amendment['normalized_input']
                or selection.get('case_id') != case or selection.get('profile_id') != profile['profile_id']
                or selection.get('profile_sha256') != profile['sha256'] or selection.get('profile_revision') != profile['revision']
                or selection.get('status') != 'ready' or len(forms) != 1
                or any(forms[0].get(key) != row.get(other) for key, other in [
                    ('remarks', 'remarks'), ('summary', 'summary'), ('claim_amount', 'claim_amount')])):
            raise APIError('contact_binding', '逐案正式计划、专项与摘要/备注/金额不符')
        # An unreviewed optional field is evidence absence, not an instruction
        # to block an otherwise confirmed contact. Unknown blocking defaults safe.
        gaps = amendment.get('gaps', [])
        blocking = any(not isinstance(gap, dict) or gap.get('blocking', True) is not False for gap in gaps)
        ready = amendment.get('ready_for_controlled_remarks_update') is True and not blocking
        local, reason = None, None
        if ready:
            if not isinstance(after, str) or not after.strip() or re.search(r'[\r\n\x00]', after):
                raise APIError('contact_value', '确认后备注须为明确非空单行文本')
            _, paths = service.resolve(case)
            local = load_context(paths['form']);validate_basic_fields(local['basic'])
            if (amendment.get('serial') not in (None, local['serial'])
                    or local['basic']['fields']['t-abst'] != row.get('summary')
                    or local['amount'] != row.get('claim_amount')
                    or local['basic']['fields'].get('t-cl_abst', '') not in [amendment.get('before_remarks'), after]
                    or local.get('review', {}).get('workflow_started') is not False):
                raise APIError('contact_local_baseline', '本机单号、金额、摘要、备注或流程状态不属于明确修正范围')
        else:
            reason = '联系人/联系方式仍有明确缺项；保持原案，不进入财务现场'
        rows.append({'sequence': seq, 'case_id': case, 'serial': local['serial'] if local else amendment.get('serial'),
                     'ready': ready, 'reason': reason, '_amendment': amendment, '_form': local})
    if wanted is not None and wanted - seen:
        raise APIError('contact_sequences', '所选序号不在修正清单中')
    return path, manifest, index_path, sorted(rows, key=lambda r: r['sequence'])


def _native_read(runner, case, label, serial):
    receipt, data, frame, art = runner.readback(case, label)
    verified_frame, verified_art = _registered_frame(runner, case, receipt, serial)
    if verified_frame != frame or art != verified_art:
        raise APIError('contact_readback', '只能使用同一注册SHA回执中的真实独立预览，不替换frame')
    return receipt, frame, art


def _registered_frame(runner, case, receipt, serial):
    envelope, verified_art = runner.artifact(case, receipt, envelope=True)
    verified = verify_preview(envelope, serial, runner.userid, runner.tab)
    frames = [f for f in verified['frames'] if f.get('id') == 'iframepage_print']
    if len(frames) != 1 or frames[0].get('raw_basic', {}).get('schema') != 'ustc-native-basic-text-v1':
        raise APIError('contact_readback', '备注修正须有原生完整raw_basic，不能用备注子串猜旧基线')
    return frames[0], verified_art


def _write_job(runner, case, label):
    rid = 'batch-' + runner.key + '-' + label
    job = 'j_' + hashlib.sha256((case + '\0' + rid).encode()).hexdigest()[:24]
    folder = runner.service.jobs/case/job
    if not folder.exists(): return None
    request = read_json(folder/'request.json')
    if (request.get('case_id') != case or request.get('request_id') != rid
            or request.get('operation') != 'stage' or request.get('execute') is not True
            or request.get('parameters') != {'stage': 'basic', 'tab_id': runner.tab}):
        raise APIError('contact_original_job', '原备注保存请求身份不符；不派发替代写入')
    return runner.service.result(case, job)


def _pending_ours(runner, case, form, original):
    _, paths = runner.service.resolve(case)
    pending_path = paths['state'].parent/'pending_write.json'
    if not pending_path.exists(): return None
    pending = read_json(pending_path)
    if (not original or pending.get('case_id') != case or pending.get('serial') != form['serial']
            or pending.get('operation') != 'step:basic' or pending.get('status') != 'await_independent_readback'):
        raise APIError('contact_pending', '存在不属于本次稳定备注修正的原pending，停止共享现场')
    directory = Path(pending.get('output_directory', ''))
    bound = False
    for art in original.get('artifacts', []):
        path = runner.service.artifact(case, original['job_id'], art['artifact_id'])
        bound |= path.parent == directory and directory.is_relative_to(paths['state'].parent/'receipts')
    if not bound:
        raise APIError('contact_pending', '原pending尚无同任务注册SHA回执归属，保留并只查原job')
    return pending


def _update_remarks(service, case, form, after, revision):
    expected = _form_with_remarks(form, after)
    if service.revision(case) != revision:
        raise APIError('contact_case_changed', '基线回读期间本案版本变化，停止不覆盖')
    if form == expected: return form
    service.update_case(case, 'form', {'basic': expected['basic']}, revision)
    _, paths = service.resolve(case)
    actual = read_json(paths['form'])
    if actual != expected:
        raise APIError('contact_patch_scope', '正式参数更新后除备注外的字段有变化，停止')
    return actual


def run_amendments(service, manifest, batch_index, sequences=None, tab_id=None, expected_userid=None, execute=False):
    """Hold the shared workflow OS lease through baseline, update, save and ack."""
    if type(execute) is not bool:
        raise APIError('contact_execute', 'execute须为明确布尔值')
    try:
        context = file_lock(service.root/'.local/workflow-orchestrator.lock') if execute else nullcontext()
        with context:
            return _run_amendments(service, manifest, batch_index, sequences, tab_id, expected_userid, execute)
    except RuntimeError as error:
        raise APIError(getattr(error, 'code', 'contact_workflow_runtime'), str(error)) from error


def _run_amendments(service, manifest, batch_index, sequences, tab_id, expected_userid, execute):
    path, document, index, rows = _inputs(service, manifest, batch_index, sequences)
    public = [{k: v for k, v in row.items() if not k.startswith('_')} for row in rows]
    result = {'ok': True, 'schema': 'ustc-contact-amendment-run-v1', 'manifest': str(path), 'manifest_sha256': digest(path),
              'batch_index': str(index), 'execute': execute, 'documents': public, 'results': [],
              'system_write': False, 'system_write_requested': False, 'full_draft_completed': False,
              'approval_completed': False, 'payment_completed': False}
    if not execute: return {**result, 'state': 'preview_only'}
    if (not isinstance(tab_id, str) or not re.fullmatch(r'[0-9]+', tab_id)
            or not isinstance(expected_userid, str) or not re.fullmatch(r'[A-Za-z0-9]{2,40}', expected_userid)):
        raise APIError('contact_identity', '执行须明确本次数字标签及2至40位字母数字账号')
    runner = DraftBatch(service, index, document['profile']['profile_id'], tab_id, expected_userid)
    manifest_sha, index_sha = result['manifest_sha256'], digest(index)
    for item in rows:
        if not item['ready']:
            result['results'].append({**{k: v for k, v in item.items() if not k.startswith('_')}, 'status': 'held_missing_contact'})
            continue
        case, amendment, form = item['case_id'], item['_amendment'], item['_form']
        # 请求号同时绑定本次标签：换标签继续必须是"新意图新编号"，不能沿用原编号改参数。
        prefix = ('contact-' + manifest_sha[:16] + '-' + str(item['sequence'])
                  + '-' + hashlib.sha256(str(tab_id).encode()).hexdigest()[:8])
        intent_path = runner.folder/case/(prefix + '-intent.json')
        try:
            if digest(path) != manifest_sha or digest(index) != index_sha:
                raise APIError('contact_source_changed', '清单或正式索引改变，停止共享现场')
            revision = service.revision(case)
            _, paths = service.resolve(case);form = load_context(paths['form'])
            if service.revision(case) != revision:
                raise APIError('contact_case_changed', '读取本案期间版本变化')
            if (_protected_form_sha(form) != _protected_form_sha(item['_form'])
                    or form['basic']['fields'].get('t-cl_abst', '') not in [amendment.get('before_remarks'), amendment['after_remarks']]
                    or form.get('review', {}).get('workflow_started') is not False
                    or form.get('expected_userid', expected_userid) != expected_userid):
                raise APIError('contact_case_changed', '本案非备注参数、账号或流程状态变化，停止不覆盖')
            binding = {'manifest_sha256': manifest_sha, 'index_sha256': index_sha, 'case_id': case,
                       'serial': form['serial'], 'tab_id': tab_id, 'expected_userid': expected_userid,
                       'before_remarks': amendment.get('before_remarks'), 'after_remarks': amendment['after_remarks'],
                       'protected_form_sha256': _protected_form_sha(form)}
            if intent_path.exists():
                intent = read_json(intent_path)
                if intent.get('binding') != binding: raise APIError('contact_intent_changed', '须接续原案同输入同标签备注意图')
            else:
                intent = {'schema': 'ustc-contact-amendment-intent-v1', 'binding': binding, 'phase': 'prepared', 'created_at_utc': stamp()}
                intent_path.parent.mkdir(parents=True, exist_ok=True)
                with intent_path.open('x', encoding='utf-8') as stream: json.dump(intent, stream, ensure_ascii=False, indent=2)
            original = _write_job(runner, case, prefix + '-basic-write')
            pending = _pending_ours(runner, case, form, original)
            if original and original.get('status') in ['queued', 'running']:
                raise APIError('contact_original_running', '原保存仍在执行，只查原job，不操作页面')
            if intent.get('phase') == 'basic_amended' and not pending:
                receipt = service.result(case, intent['readback_job_id'])
                frame, art = _registered_frame(runner, case, receipt, form['serial'])
                if art != intent.get('readback_artifact'):
                    raise APIError('contact_original_proof', '原独立核验SHA回执变化，停止')
                verify_basic_fields(_form_with_remarks(form, amendment['after_remarks']), frame)
                runner.verify_stage(case, 'basic', frame)
                result['results'].append({'sequence': item['sequence'], 'case_id': case, 'serial': form['serial'],
                    'status': 'basic_amended', 'reused_original_verification': True, 'live_readback_performed': False,
                    'readback_job_id': receipt['job_id'], 'readback_artifact': art, 'full_draft_completed': False})
                continue
            if not original and not pending:
                runner.request(case, prefix + '-open', operation='read_system', parameters={
                    'mode': 'open-draft', 'serial': form['serial'], 'expected_userid': expected_userid, 'tab_id': tab_id})
            receipt, frame, art = _native_read(runner, case, prefix + '-baseline', form['serial'])
            after_form = _form_with_remarks(form, amendment['after_remarks'])
            try:
                verify_basic_fields(after_form, frame)
                native_after = True
            except APIError:
                native_after = False
            if native_after:
                form = _update_remarks(service, case, form, amendment['after_remarks'], revision) if not pending else form
                verify_basic_fields(form, frame)
                runner.verify_stage(case, 'basic', frame)
                if pending: runner.ack(case, receipt, art, '恢复本次备注修正：独立同号原生完整basic字段与确认后参数一致；未重放保存。')
                status = 'basic_amended'
            else:
                before = amendment.get('before_remarks')
                if not isinstance(before, str): raise APIError('contact_baseline_unknown', '旧备注未知且原生未匹配after，不能猜空白或写入')
                verify_basic_fields(_form_with_remarks(form, before), frame)
                if original or pending:
                    raise APIError('contact_original_not_verified', '原保存结果尚未核定为after；保留原请求，不重发保存')
                form = _update_remarks(service, case, form, amendment['after_remarks'], revision)
                intent.update(phase='local_after_verified');atomic_json(intent_path, intent)
                runner.request(case, prefix + '-basic-plan', operation='stage', parameters={'stage': 'basic', 'tab_id': tab_id})
                if read_json(paths['form']) != form:
                    raise APIError('contact_case_changed', '计划后本案参数变化，不派发保存')
                intent.update(phase='save_intent', updated_at_utc=stamp());atomic_json(intent_path, intent)
                result.update(system_write=True, system_write_requested=True)
                runner.request(case, prefix + '-basic-write', operation='stage', parameters={'stage': 'basic', 'tab_id': tab_id}, execute=True)
                receipt, frame, art = _native_read(runner, case, prefix + '-saved', form['serial'])
                verify_basic_fields(form, frame)
                if read_json(paths['form']) != form:
                    raise APIError('contact_case_changed', '保存后的本案参数变化，保留pending不登记')
                runner.verify_stage(case, 'basic', frame)
                runner.ack(case, receipt, art, '仅备注纠正：同号独立原生basic全字段核验，摘要、证明人、电话、负责人费用和退单点不变；未审批或付款。')
                status = 'basic_amended'
            intent.update(phase=status, readback_job_id=receipt['job_id'], readback_artifact=art,
                          updated_at_utc=stamp(), full_draft_completed=False);atomic_json(intent_path, intent)
            result['results'].append({'sequence': item['sequence'], 'case_id': case, 'serial': form['serial'],
                                      'status': status, 'save_skipped': native_after, 'readback_job_id': receipt['job_id'],
                                      'readback_artifact': art, 'full_draft_completed': False})
        except (APIError, ValueError, OSError, KeyError, RuntimeError) as error:
            result.update(ok=False, state='stopped_verify_original')
            result['results'].append({'sequence': item['sequence'], 'case_id': case, 'status': 'stopped_verify_original',
                                      'error': str(error), 'no_replay': True})
            break
    result.setdefault('state', 'basic_amendments_verified')
    result['receipt_root'] = str(runner.folder)
    atomic_json(runner.folder/('contact-amendments-' + manifest_sha[:16] + '-result.json'), result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--batch-index', type=Path, required=True)
    parser.add_argument('--sequences', help='逗号分隔正整数；省略则按清单序号串行')
    parser.add_argument('--tab-id');parser.add_argument('--expected-userid')
    parser.add_argument('--material-root', type=Path, action='append', help='显式授权的材料根；可重复，跨设备不猜材料区位置')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    try:
        selected = None if args.sequences is None else [int(value) for value in args.sequences.split(',')]
        if args.material_root and any(not value.is_absolute() for value in args.material_root):
            raise APIError('contact_material_root', '材料根须为明确绝对路径')
        service = AssistantService(ROOT, args.material_root or [ROOT.parent])
        result = run_amendments(service, args.manifest, args.batch_index, selected, args.tab_id, args.expected_userid, args.execute)
    except (APIError, ValueError, OSError, KeyError) as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
