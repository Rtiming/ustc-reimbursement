"""现场缺口的最小复现；不读取个人资料或连接学校系统。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
from reimburse import require_native_platform, applicant_review_observed
from finance_expense_amount import SCHEMA, validate
from windows_auth_pipe import private_pipe_acl_matches
import windows_edge_bridge as bridge


class WindowsIntegrationTests(unittest.TestCase):
    def test_shared_windows_status_file_retries_only_frozen_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'status.json'
            path.write_text('{"before":true}')
            original = os.replace
            blocked = PermissionError('synthetic sharing conflict')
            blocked.winerror = 5
            attempts = []

            def replace(source, target):
                attempts.append((source, target, source.read_bytes()))
                if len(attempts) < 3:
                    self.assertEqual(json.loads(path.read_text()), {'before': True})
                    raise blocked
                return original(source, target)

            with patch('windows_edge_bridge.sys.platform', 'win32'), patch.object(bridge.os, 'replace', side_effect=replace):
                bridge.atomic_private(path, {'after': True})
            self.assertEqual(json.loads(path.read_text()), {'after': True})
            self.assertEqual(len(attempts), 3)
            self.assertTrue(all(row == attempts[0] for row in attempts))
            self.assertEqual(list(Path(temp).glob('*.tmp')), [])

    def test_persistent_status_permission_failure_keeps_original_and_exclusive_intent(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'status.json'
            path.write_text('{"before":true}')
            blocked = PermissionError('synthetic persistent denial')
            blocked.winerror = 5
            with patch('windows_edge_bridge.sys.platform', 'win32'), patch.object(bridge.os, 'replace', side_effect=blocked) as replace, patch.object(bridge.time, 'monotonic', side_effect=[0, 2]):
                with self.assertRaises(PermissionError):
                    bridge.atomic_private(path, {'after': True})
                self.assertEqual(replace.call_count, 1)
            self.assertEqual(json.loads(path.read_text()), {'before': True})
            with self.assertRaises(FileExistsError):
                bridge.atomic_private(path, {'intent': True}, exclusive=True)

    def test_missing_node_development_runtime_is_explicitly_reported(self):
        import node_support
        with patch.object(node_support.shutil, 'which', return_value=None):
            with self.assertRaises(unittest.SkipTest):
                node_support.node_run('process.exit(0)', text=True)

    def test_powershell_bom_request_uses_the_same_dispatch(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / '请求 with spaces.json'
            value = {'method': 'capabilities', 'params': {'intent': 'materials'}}
            path.write_text(json.dumps(value), encoding='utf-8-sig')
            result = subprocess.run([sys.executable, str(ROOT / 'assistant.py'), 'call', '--input', str(path)],
                                    capture_output=True, text=True, encoding='utf-8', timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)['ok'])

    def test_platform_gate_uses_the_selected_backend(self):
        with patch.dict(os.environ, {'REIMBURSEMENT_EDGE_BACKEND': 'edge_native_messaging'}):
            with patch('finance_backend.sys.platform', 'win32'):
                require_native_platform('123', require_tab=True)
                with self.assertRaises(ValueError):
                    require_native_platform(require_tab=True)
            with patch('finance_backend.sys.platform', 'linux'):
                with self.assertRaises(ValueError):
                    require_native_platform('123', require_tab=True)
        with patch.dict(os.environ, {'REIMBURSEMENT_EDGE_BACKEND': 'edge_applescript'}), patch('finance_backend.sys.platform', 'win32'):
            with self.assertRaises(ValueError):
                require_native_platform('123', require_tab=True)

    def test_draft_node_does_not_claim_applicant_review(self):
        draft = {'node': '508', 'serial': '123', 'previewVisible': True}
        self.assertFalse(applicant_review_observed(draft))
        self.assertFalse(applicant_review_observed({**draft, 'applicantAgreementVisible': False}))
        self.assertTrue(applicant_review_observed({**draft, 'applicantAgreementVisible': True}))

    def test_unassigned_subject_is_exact_and_still_bound(self):
        request = {'schema': SCHEMA, 'case_id': 'fixture', 'serial': '123', 'group': '测试组',
                   'subject': '*', 'expected_userid': 'TEST001', 'tab_id': '1'}
        self.assertEqual(validate(request)['subject'], '*')
        for subject in ('**', '* OR 1=1', '', '30201&other'):
            with self.assertRaises(ValueError):
                validate({**request, 'subject': subject})

    def test_builtin_administrator_pipe_keeps_exact_principals(self):
        user = 'S-1-5-21-123-456-789-500'
        rows = [(0, 0, 0x1f01ff, sid) for sid in (user, 'S-1-5-18', 'S-1-5-32-544')]
        self.assertTrue(private_pipe_acl_matches(0x1000, rows, user))
        self.assertFalse(private_pipe_acl_matches(0x1000, rows + [(0, 0, 0x1f01ff, 'S-1-1-0')], user))
