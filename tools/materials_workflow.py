"""材料整理的参数入口；归档、解包、候选提取和任务回执均由既有owner执行。"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import time

from assistant_api import APIError, read_json
from material_intake import IntakeError, SCHEMA, digest, discover, preview, read_review, reject_links
from material_intake_service import trusted_runtime, validate_request


from assistant_contract import CASE_PROFILES
PROFILES = frozenset(CASE_PROFILES)
WORKFLOW_SCHEMA = 'reimbursement-materials-workflow-v1'


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _case_exists(service, case_id, profile):
    try:
        _, paths = service.resolve(case_id)
    except APIError as error:
        if error.code == 'case_not_found':
            return False
        raise
    profiles = read_json(paths['case']).get('material_context', {}).get('profiles', [])
    if not isinstance(profiles, list) or profile not in profiles:
        raise APIError('materials_case_profile', '已有案件材料profile不符；不覆盖案件或把专项profile_id当作材料profile')
    return True


def _logical_request(request, *, automatic_review_sha):
    value = copy.deepcopy(request)
    for source in value['sources']:
        source['expected_sha256'] = None
    if automatic_review_sha and value.get('field_review'):
        value['field_review']['sha256'] = None
    return value


def _original_job(service, case_id, request, *, automatic_review_sha=False):
    """仅核对同案、同输出的既有材料意图；不建立另一套任务状态。"""
    folder = service.jobs / case_id
    reject_links(folder)
    found = []
    for path in folder.glob('*/request.json'):
        reject_links(path)
        old = read_json(path)
        if old.get('operation') != 'material_intake' or old.get('execute') is not True:
            continue
        business = old.get('parameters', {}).get('request', {})
        if business.get('output_dir') != request['output_dir']:
            continue
        if (_logical_request(business, automatic_review_sha=automatic_review_sha)
                != _logical_request(request, automatic_review_sha=automatic_review_sha)):
            raise APIError('materials_output_reserved', '此输出已属于不同的材料原任务；检查原job，不换号或覆盖输出')
        if old.get('case_id') != case_id or not isinstance(old.get('request_id'), str):
            raise APIError('materials_original_job', '原材料任务缺少同案稳定意图，不猜测接续')
        found.append({'job_id':path.parent.name, 'request_id':old['request_id'],
                      'status':read_json(path.parent/'status.json').get('status'), 'request':business})
    if len(found) > 1:
        raise APIError('materials_original_job', '同案同输出存在多个原任务；先核对原回执，不自动选取')
    return found[0] if found else None


def _wait(service, case_id, job_id, wait_seconds):
    deadline = time.monotonic() + wait_seconds
    while True:
        result = service.result(case_id, job_id)
        if result.get('status') not in {'queued', 'running'} or time.monotonic() >= deadline:
            return result
        time.sleep(min(0.25, max(0, deadline-time.monotonic())))


def _reverify_sources(request, receipt):
    """仅复用owner的有界文件盘点和原receipt，不再次解包或提取字段。"""
    fields = ('source_id', 'relative_path', 'sha256', 'bytes', 'group_strip_dirs')
    try:
        expected = receipt.get('originals')
        if not isinstance(expected, list) or not expected or any(
                not isinstance(row, dict) or any(key not in row for key in fields) for row in expected):
            return False, 'original_snapshot_unreverifiable', '原回执缺少冻结来源文件列表，不能称当前来源已重验'
        current_request = copy.deepcopy(request)
        for source in current_request['sources']:
            source['expected_sha256'] = None
        current = discover(current_request)
        fingerprint = lambda rows: sorted(_encoded({key:row[key] for key in fields}) for row in rows)
        if fingerprint(current) != fingerprint(expected):
            return False, 'current_source_drift', '当前来源的文件集合或字节与原归档快照不同；未重新归档或创建任务'
        read_review(request)
        return True, 'current_source_matches_original_snapshot', None
    except (IntakeError, OSError, ValueError, KeyError) as error:
        return False, 'current_source_unverified', '当前来源未能按原receipt重验：' + str(error)


def run_materials(service, *, case_id, source_paths, output_dir, group_strip_dirs,
                  execute=False, create_case=False, case_profile='student_activity',
                  field_review=None, wait_seconds=300):
    """预览完全只读；执行只创建明确请求的空案并接续正式材料job。"""
    service.case_id(case_id)
    if type(execute) is not bool or type(create_case) is not bool:
        raise APIError('materials_execute', 'execute及create_case须为布尔值')
    if not isinstance(case_profile, str) or case_profile not in PROFILES:
        raise APIError('materials_case_profile', 'case_profile仅支持'+ '/'.join(CASE_PROFILES) +'，不接受专项profile_id')
    if (isinstance(wait_seconds, bool) or not isinstance(wait_seconds, (int, float))
            or not math.isfinite(wait_seconds) or not 0 <= wait_seconds <= 300):
        raise APIError('materials_wait', 'wait_seconds须为0至300秒；超时后保留原job继续查询')
    if not isinstance(source_paths, (list, tuple)) or not source_paths:
        raise APIError('invalid_sources', 'source_paths须为非空绝对路径数组')
    review = copy.deepcopy(field_review)
    hash_review = isinstance(review, (str, Path))
    if hash_review:
        review = {'path':str(review), 'sha256':'0'*64}
    if isinstance(review, dict) and isinstance(review.get('path'), Path):
        review['path'] = str(review['path'])
    request = {'schema':SCHEMA, 'case_id':case_id,
               'sources':[{'id':'source_'+str(n), 'path':str(path), 'group_strip_dirs':group_strip_dirs}
                          for n, path in enumerate(source_paths)],
               'output_dir':str(output_dir), 'field_review':review}
    try:
        # 在resolve/读取人工核对文件之前检查原始路径的链接身份和全部授权根。
        request = validate_request(service, case_id, request)
        paths = [source['path'] for source in request['sources']]
        if len(set(paths)) != len(paths):
            raise APIError('materials_duplicate_source', '相同规范化来源不能以多个source.id重复归档')
        request['sources'].sort(key=lambda source:source['path'])
        for source in request['sources']:
            source['id'] = 'src_'+hashlib.sha256(source['path'].encode('utf-8')).hexdigest()[:20]
        request = validate_request(service, case_id, request)
        exists = _case_exists(service, case_id, case_profile)
        original = _original_job(service, case_id, request, automatic_review_sha=hash_review)
        request_id = ('materials-execute-' if execute else 'materials-preview-') + hashlib.sha256(
            _encoded({'request':request,'execute':execute})).hexdigest()[:40]
        report = {'schema':WORKFLOW_SCHEMA, 'ok':True, 'state':'preview_ready',
                  'case_id':case_id, 'case_profile':case_profile, 'case_exists':exists, 'case_created':False,
                  'request':request, 'request_id':request_id, 'preview':None,
                  'job_id':None, 'job':None, 'resumed_existing_job':False,
                  'artifacts':[], 'ocr_candidates':None, 'next_action':None,
                  'current_source_reverified':False, 'original_snapshot_verified':False,
                  'source_status':'not_reverified', 'source_verification_reason':None,
                  'system_write':False, 'filesystem_action_requested':execute, 'ocr_started':False}
        if original and not execute:
            report.update(state='original_job_available', job_id=original['job_id'], job=original,
                          request=original['request'], request_id=original['request_id'],
                          next_action={'tool':'reimbursement_result',
                            'arguments':{'case_id':case_id,'job_id':original['job_id']},
                            'reason':'只读发现原材料任务；预览不改变其状态，不换号或输出目录重放'})
            return report
        if original:
            request = original['request']
            report.update(request=request, request_id=original['request_id'], job_id=original['job_id'], resumed_existing_job=True)
        else:
            if hash_review:
                request['field_review']['sha256'] = digest(request['field_review']['path'])
            report['preview'] = preview(request)
            # 直接文件使用owner已流式盘点的SHA冻结；目录仍由原receipt逐文件列表核对。
            selected = {row['path']:row for row in report['preview']['files']}
            for source in request['sources']:
                if Path(source['path']).is_file():
                    source['expected_sha256'] = selected[source['path']]['sha256']
            request = validate_request(service, case_id, request)
            request_id = ('materials-execute-' if execute else 'materials-preview-') + hashlib.sha256(
                _encoded({'request':request,'execute':execute})).hexdigest()[:40]
            report.update(request=request, request_id=request_id)
            if not execute:
                report['next_action'] = {'tool':'run_materials', 'needed_inputs':['execute'],
                    'reason':'核对预览后明确执行；未知姓名、学号和金额留空，不运行OCR'}
                return report
            if not exists and not create_case:
                report.update(ok=False, state='case_creation_required', filesystem_action_requested=False,
                              next_action={'tool':'run_materials','needed_inputs':['create_case'],
                                           'reason':'尚无材料案件；仅execute+create_case可创建空案'})
                return report
            trusted_runtime(service)
            if not exists:
                try:
                    service.new_case(case_id, profile=case_profile)
                    report['case_created'] = True
                except APIError as error:
                    if error.code != 'case_exists':
                        raise
                if not _case_exists(service, case_id, case_profile):
                    raise APIError('materials_case_missing', '明确创建后仍无同案入口')
                report['case_exists'] = True
            queued = service.material_intake(case_id, request, request_id, execute=True)
            if not queued.get('job_id'):
                raise APIError('materials_job_missing', '正式材料入口没有原job_id；保留结果，不重新请求')
            report['job_id'] = queued['job_id']
            report['resumed_existing_job'] = bool(queued.get('reused_existing_job'))
        result = _wait(service, case_id, report['job_id'], wait_seconds)
        status = result.get('status')
        report.update(job=result, artifacts=result.get('artifacts', []), next_action=result.get('next_action'))
        if status in {'queued','running'}:
            report['state'] = 'material_job_pending'
        elif status == 'completed':
            receipt = next((a for a in report['artifacts'] if a.get('name') == 'receipt.json'), None)
            if receipt is None:
                raise APIError('materials_receipt_missing', '任务退出成功但无正式材料回执；不宣称归档完成')
            # result is a display view and may mask digit runs in source IDs or
            # filenames. Internal provenance compares the SHA-verified original
            # artifact, never the redacted presentation of that artifact.
            value = read_json(service.artifact(case_id, report['job_id'], receipt['artifact_id']))
            if (value.get('schema') != 'reimbursement.material-intake-receipt.v1'
                    or value.get('case_id') != case_id or value.get('status') != 'completed'):
                raise APIError('materials_receipt_unverified', '原任务回执未证明本案归档完成，不换号重放')
            reverified, source_status, reason = _reverify_sources(request, value)
            report.update(ok=reverified, state='materials_archived' if reverified else 'original_snapshot_only',
                          original_snapshot_verified=True, current_source_reverified=reverified,
                          source_status=source_status, source_verification_reason=reason,
                          ocr_candidates=value.get('ocr_candidates'))
            if not reverified:
                report['next_action'] = {'tool':'reimbursement_result',
                    'arguments':{'case_id':case_id,'job_id':report['job_id']},
                    'reason':'这里只证明原归档快照；先核对当前来源差异，不把新来源说成已归档，不换号重放原任务'}
        else:
            report.update(ok=False, state='material_job_unknown' if status=='unknown' else 'material_job_failed')
        if report['next_action'] is None:
            report['next_action'] = {'tool':'reimbursement_result',
                'arguments':{'case_id':case_id,'job_id':report['job_id']},
                'reason':'只查原材料任务及既有候选；不启动OCR或财务操作'}
        return report
    except IntakeError as error:
        raise APIError(error.code, str(error)) from error
