"""新用户登记、原计划SHA、真实准备证据及两类导出配置的边界；全部为合成数据。"""

import copy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from assistant_api import AssistantService, APIError, atomic_json, read_json
from registered_inputs import load_definition
from workspace_config import standards_library


class WorkflowRegistrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root, self.material = self.base / 'tool', self.base / 'data'
        self.material.mkdir()
        atomic_json(self.root / 'data/distribution.json', {'version': 'fixture'})
        atomic_json(self.root / '.local/installation.json', {'schema': 'ustc-installation-v1', 'tool_root': str(self.root), 'material_root': str(self.material), 'library_root': str(self.material / '规范库'), 'profile_id': 'alpha'})
        self.service = AssistantService(self.root, [self.material])
        self.service.standards_init(str(self.material / '规范库'), 'alpha', '合成专项', execute=True)
        profile = self.service.standards_read(str(self.material / '规范库'), 'alpha')
        self.service.new_case('batch_demo')
        self.service.new_case('expense_demo')
        _, paths = self.service.resolve('expense_demo')
        form = read_json(paths['form'])
        # 仅种入测试夹具，正式接口不允许凭空写入单号或票据GUID。
        form.update(serial='12345', amount='10.00')
        form['basic']['fields'].update({'t-abst': '合成活动', 't-cl_abst': '', 't-req_mobile': 'fixture-phone'})
        form['contract']['exists'] = False
        form['delivery']['delegate_finance_print'] = False
        atomic_json(paths['form'], form)
        self.normalized = self.material / 'input.json'
        atomic_json(self.normalized, {'schema': 'ustc-reimbursement-batch-input-v1', 'case_id': 'expense_demo', 'documents': [{'folder_name': '合成活动'}]})
        self.plan = self.material / 'plan.json'
        atomic_json(self.plan, {'ok': True, 'status': 'ready', 'rule_selection': {'status': 'ready', 'case_id': 'expense_demo', 'profile_id': 'alpha', 'profile_sha256': profile['sha256'], 'profile_revision': profile['profile']['revision']},
                               'input_evidence': {'path': str(self.normalized), 'sha256': hashlib.sha256(self.normalized.read_bytes()).hexdigest()},
                               'effective_policy': {'contact_in_summary': False},
                               'plan': {'documents': [{'forms': [{'status': 'ready', 'summary_base': '合成活动', 'summary': '合成活动', 'remarks': '', 'claim_amount': '10.00', 'export': {'business_date': '2026-10-01', 'sequence': 1}}]}]}})
        (self.material / 'downloads').mkdir()
        self.request = {'schema': 'ustc-workflow-registration-v1', 'scenario': 'alpha', 'expected_userid': 'fixture01', 'source': '合成测试，无财务授权',
                        'documents': [{'case_id': 'expense_demo', 'plan_path': str(self.plan)}],
                        'delivery': {'export_root': str(self.material / 'exports'), 'download_directory': str(self.material / 'downloads')}}
        self.output = self.material / 'registration'

    def test_new_user_library_binding_and_both_exports_are_generated(self):
        self.assertEqual(standards_library(self.root), self.material / '规范库')
        before = self.service.revision('batch_demo')
        preview = self.service.register_workflow('batch_demo', self.request, str(self.output))
        self.assertFalse(self.output.exists())
        self.assertEqual(self.service.revision('batch_demo'), before)
        result = self.service.register_workflow('batch_demo', self.request, str(self.output), True, preview['preview_sha256'])
        self.assertTrue(result['executed'])
        self.assertTrue((self.material / 'exports').is_dir())
        self.assertFalse(result['financial_write_executed'])
        cfg, index, auth, _ = load_definition(self.service, 'batch_demo')
        self.assertEqual(read_json(index)['documents'][0]['case_id'], 'expense_demo')
        self.assertFalse(read_json(auth)['submit_authorized'])
        self.assertIn('system_pdf_manifest', cfg)
        self.assertIn('list_export_manifest', cfg)
        self.assertEqual(cfg['login_mode'], 'session_only')
        self.assertFalse(cfg['show_verification_interface'])
        self.assertEqual(self.service.overview('batch_demo')['role'], 'registered_batch')
        self.assertNotIn('approval_manifest', cfg)
        with self.assertRaisesRegex(APIError, '真实准备回执'):
            self.service.workflow('batch_demo', 'approve')

    def test_stale_preview_is_rejected_before_writing(self):
        preview = self.service.register_workflow('batch_demo', self.request, str(self.output))
        _, paths = self.service.resolve('batch_demo')
        case = read_json(paths['case']); case['description'] = 'changed'
        atomic_json(paths['case'], case)
        with self.assertRaisesRegex(APIError, '预览已变化'):
            self.service.register_workflow('batch_demo', self.request, str(self.output), True, preview['preview_sha256'])
        self.assertFalse(self.output.exists())

    def test_manual_verification_window_requires_explicit_selection(self):
        from workflow_registration import build_registration
        request = copy.deepcopy(self.request)
        request['show_verification_interface'] = True
        _, files, _, _ = build_registration(self.service, 'batch_demo', request, str(self.output))
        self.assertTrue(files['workflow.json']['show_verification_interface'])
        request['show_verification_interface'] = 'true'
        with self.assertRaises(APIError):
            build_registration(self.service, 'batch_demo', request, str(self.output))

    def test_modified_input_duplicate_case_and_escape_are_rejected(self):
        duplicate = copy.deepcopy(self.request); duplicate['documents'] *= 2
        with self.assertRaisesRegex(APIError, '不能重复'):
            self.service.register_workflow('batch_demo', duplicate, str(self.output))
        with self.assertRaises(APIError):
            self.service.register_workflow('batch_demo', self.request, str(self.base / 'outside'))
        self.normalized.write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(APIError, 'SHA不符'):
            self.service.register_workflow('batch_demo', self.request, str(self.output))
        self.assertFalse(self.output.exists())

    def test_known_export_conflict_is_rejected_before_registration(self):
        folder = self.material / 'exports/2026-10-01'
        folder.mkdir(parents=True)
        target = folder / '1_合成活动.pdf'
        target.write_bytes(b'existing original')
        with self.assertRaises(FileExistsError):
            self.service.register_workflow('batch_demo', self.request, str(self.output))
        self.assertFalse(self.output.exists())
        self.assertEqual(target.read_bytes(), b'existing original')

    def test_fabricated_preparation_receipt_cannot_register_approval(self):
        request = copy.deepcopy(self.request)
        request['authorize_approve'] = True
        request['documents'][0]['preparation_receipt'] = {'case_id': 'expense_demo', 'job_id': 'missing', 'artifact_id': 'fake', 'sha256': '0' * 64}
        with self.assertRaises(APIError):
            self.service.register_workflow('batch_demo', request, str(self.output))
        self.assertFalse(self.output.exists())

    def test_revoked_authorization_cannot_be_restored_by_old_preparation(self):
        from workflow_approval import publish_from_preparation
        request = copy.deepcopy(self.request)
        request['authorize_approve'] = True
        preview = self.service.register_workflow('batch_demo', request, str(self.output))
        self.service.register_workflow('batch_demo', request, str(self.output), True, preview['preview_sha256'])
        cfg, _, _, definition = load_definition(self.service, 'batch_demo')
        original_ref = cfg.source_reference
        # 只隔离证据解析来测试发布CAS；不把这些合成标识当作原生回执。
        report = {'results': [{'case_id': 'expense_demo', 'status': 'applicant_ready_verified',
                               'native_evidence': {'job_id': 'synthetic', 'artifact_id': 'a0', 'sha256': 'a' * 64}}]}
        def revoke(*args):
            current = read_json(definition); current['approval_authorized'] = False
            atomic_json(definition, current)
            _, paths = self.service.resolve('batch_demo')
            case = read_json(paths['case'])
            case['material_context']['workflow_entry']['sha256'] = hashlib.sha256(definition.read_bytes()).hexdigest()
            atomic_json(paths['case'], case)
        with patch('workflow_approval.validate_manifest_value', side_effect=revoke):
            with self.assertRaisesRegex(APIError, '授权已变更'):
                publish_from_preparation(self.service, 'batch_demo', cfg, definition, report)
        _, paths = self.service.resolve('batch_demo')
        latest = read_json(paths['case'])['material_context']['workflow_entry']
        self.assertNotEqual(latest['sha256'], original_ref['sha256'])
        self.assertEqual(latest['path'], str(definition))
        self.assertFalse(read_json(latest['path'])['approval_authorized'])

    def test_active_session_rejects_changed_definition_before_a_new_job(self):
        from registered_session import RegisteredSession
        from types import SimpleNamespace
        preview = self.service.register_workflow('batch_demo', self.request, str(self.output))
        self.service.register_workflow('batch_demo', self.request, str(self.output), True, preview['preview_sha256'])
        cfg, index, auth, definition = load_definition(self.service, 'batch_demo')
        session = RegisteredSession(self.service, 'batch_demo', cfg, index, auth)
        definition.write_bytes(definition.read_bytes() + b' ')
        with patch.object(self.service, 'enqueue') as enqueue:
            with self.assertRaises(APIError):
                session.job('expense_demo', 'read_system', {'mode': 'readback'}, 'fixture')
            enqueue.assert_not_called()
