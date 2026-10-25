"""仅从本批真实准备回执生成批准清单；缺回执保留缺项，不把规划当作已保存。"""

from pathlib import Path

from assistant_api import APIError, atomic_json, read_json
from applicant_batch import validate_manifest_value
from registered_inputs import checked_reference, WorkflowDefinition, assert_definition_current
from submission_batch import business_fingerprint
from workflow_registration import destination, encoded, sha


def publish_from_preparation(service, case_id, cfg, definition, report):
    if not cfg.get('approval_plans') or cfg.get('approval_manifest'):
        return {'generated': False, 'reason': 'existing_or_legacy_definition'}
    if not isinstance(cfg, WorkflowDefinition) or Path(definition).resolve() != Path(cfg.source_reference['path']).resolve():
        raise APIError('workflow_definition_identity', '必须使用流程开始时冻结的登记引用')
    assert_definition_current(service, cfg)
    plan_path = checked_reference(service, cfg['approval_plans'])
    plans = read_json(plan_path)['plans']
    ready = {row['case_id']: row for row in report.get('results', [])
             if row.get('status') in ('applicant_ready_verified', 'already_applicant_ready_verified')}
    missing = [x['case_id'] for x in plans if x['case_id'] not in ready]
    if missing:
        return {'generated': False, 'state': 'preparation_incomplete', 'case_ids_without_ready_proof': missing,
                'next_action': '保留已准备结果；已批准的单据可直接导出，其他缺项按原回执解决，不重复准备或批准'}
    rows = []
    for entry in plans:
        case = entry['case_id']
        _, paths = service.resolve(case)
        form = read_json(paths['form'])
        proof = dict(ready[case]['native_evidence'])
        proof.setdefault('case_id', case)
        rows.append({'case_id': case, 'serial': form['serial'], 'amount': form['amount'],
                     'business_fingerprint': business_fingerprint(form),
                     'invoice_manifest_sha256': sha(paths['invoices'].read_bytes()),
                     'plan': entry['plan'], 'preparation_receipt': proof})
    manifest = {'schema': 'ustc-applicant-batch-v1', 'expected_userid': cfg['expected_userid'],
                'source': cfg['source'], 'approval_opinion': '同意',
                'applicant_agreement_authorized': cfg.get('approval_authorized', False),
                'show_verification_interface': cfg.get('show_verification_interface', False), 'cases': rows}
    validate_manifest_value(service, manifest, cfg['expected_userid'])
    folder = destination(service, cfg['registration_directory'])
    raw = encoded(manifest)
    path = folder / ('approval-' + sha(raw) + '.json')
    if path.exists():
        if path.read_bytes() != raw:
            raise APIError('workflow_approval_changed', '既有批准输入已变化，保留原版本')
    else:
        with path.open('xb') as stream:
            stream.write(raw)
    old_ref = cfg.source_reference
    new_cfg = {**cfg, 'approval_manifest': {'path': str(path), 'sha256': sha(raw)}, 'supersedes': old_ref}
    new_raw = encoded(new_cfg)
    new_path = folder / ('workflow-' + sha(new_raw) + '.json')
    if not new_path.exists():
        with new_path.open('xb') as stream:
            stream.write(new_raw)
    elif new_path.read_bytes() != new_raw:
        raise APIError('workflow_definition_changed', '既有工作流版本变化')
    new_ref = {'path': str(new_path), 'sha256': sha(new_raw)}
    with service.mutex(case_id):
        assert_definition_current(service, cfg)
        _, root_paths = service.resolve(case_id)
        case = read_json(root_paths['case'])
        if case.get('material_context', {}).get('workflow_entry') != old_ref:
            raise APIError('workflow_registration_changed', '准备期间批次登记已变化，不覆盖新登记')
        case['material_context']['workflow_entry'] = new_ref
        atomic_json(root_paths['case'], case)
    return {'generated': True, 'definition': new_ref, 'approval_manifest': new_cfg['approval_manifest'],
            'prepared_cases': len(rows), 'approval_authorized': cfg.get('approval_authorized', False), 'system_write': False}
