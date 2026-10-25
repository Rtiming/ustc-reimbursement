"""编排预检和任务计时的离线合同；没有真实批准测速。"""

import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from assistant_api import APIError, atomic_json, read_json
from registered_session import RegisteredSession
from registered_workflow import run_registered_workflow


class RegisteredWorkflowTests(unittest.TestCase):
    def test_non_boolean_execute_never_reaches_a_financial_stage(self):
        with patch('registered_workflow._run_registered_workflow') as run:
            for value in ('false', 'true', 1, 0, None):
                with self.assertRaises(APIError):
                    run_registered_workflow(SimpleNamespace(), case_id='batch', execute=value)
            run.assert_not_called()

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        index = self.root / 'index.json'
        atomic_json(index, {'documents': [{'case_id': 'fixture'}]})
        self.service = SimpleNamespace(root=self.root)
        self.session = RegisteredSession(self.service, 'batch', {}, index, None)

    def test_job_timing_uses_original_id_and_excludes_sensitive_parameters(self):
        output = self.root / 'output.json'
        atomic_json(output, {'result': {'ok': True}})
        self.service.enqueue = Mock(return_value={'job_id': 'original'})
        self.service.result = Mock(
            return_value={
                'job_id': 'original',
                'status': 'completed',
                'exit_code': 0,
                'artifacts': [{'name': 'output.json', 'artifact_id': 'a0'}],
            }
        )
        self.service.artifact = Mock(return_value=output)
        callback = Mock()
        result, body = self.session.job(
            'fixture',
            'record_result',
            {'secret': 'DO_NOT_LOG'},
            'ack',
            request_id='stable-request',
            on_enqueued=callback,
        )
        self.assertEqual(result['job_id'], 'original')
        self.assertTrue(body['ok'])
        callback.assert_called_once_with({'job_id': 'original'})
        metric = read_json(next((self.session.folder / 'job-metrics').glob('*.json')))
        self.assertEqual(metric['request_id'], 'stable-request')
        self.assertEqual(metric['status'], 'completed')
        self.assertGreaterEqual(metric['elapsed_seconds'], metric['preflight_seconds'])
        self.assertNotIn('DO_NOT_LOG', json.dumps(metric))

    def test_failed_job_keeps_original_id_and_failure_metric(self):
        self.service.enqueue = Mock(return_value={'job_id': 'original'})
        self.service.result = Mock(
            return_value={
                'job_id': 'original',
                'status': 'failed',
                'exit_code': 1,
                'error': 'failed',
            }
        )
        with self.assertRaises(APIError):
            self.session.job('fixture', 'record_result', {}, 'ack', request_id='stable-request')
        self.service.enqueue.assert_called_once()
        metric = read_json(next((self.session.folder / 'job-metrics').glob('*.json')))
        self.assertEqual(metric['job_id'], 'original')
        self.assertEqual(metric['status'], 'failed')

    def test_finish_validates_export_inputs_before_any_approval(self):
        cfg = {'approval_manifest': {'path': 'fixture', 'sha256': 'unused'}, 'list_export_manifest': {'path': 'fixture', 'sha256': 'unused'}}
        with (
            patch('registered_workflow.load_definition', return_value=(cfg, None, None, None)),
            patch(
                'registered_workflow.checked_reference', return_value=self.root / 'manifest.json'
            ),
            patch('registered_workflow.RegisteredSession'),
            patch('applicant_batch.run_applicant_batch', return_value={'gates': []}) as approve,
            patch(
                'system_pdf_batch.run_system_pdfs',
                side_effect=APIError('bad_export', '导出输入变化'),
            ),
        ):
            with self.assertRaisesRegex(APIError, '导出输入变化'):
                run_registered_workflow(self.service, case_id='batch', phase='finish', execute=True)
        self.assertEqual(approve.call_count, 1)
        self.assertFalse(approve.call_args.kwargs['execute'])

    def test_finish_preview_describes_all_three_distinct_stages(self):
        cfg = {'approval_manifest': {'path': 'fixture', 'sha256': 'unused'}, 'list_export_manifest': {'path': 'fixture', 'sha256': 'unused'}}
        with (
            patch('registered_workflow.load_definition', return_value=(cfg, None, None, None)),
            patch(
                'registered_workflow.checked_reference', return_value=self.root / 'manifest.json'
            ),
            patch('registered_workflow.RegisteredSession'),
            patch(
                'applicant_batch.run_applicant_batch', return_value={'gates': ['待本人']}
            ) as approve,
            patch('system_pdf_batch.run_system_pdfs', return_value={'documents': 2}) as pdf,
            patch('grid_export_workflow.run_grid_export', return_value={'rows': 2}) as listing,
        ):
            result = run_registered_workflow(self.service, case_id='batch', phase='finish')
        self.assertEqual(set(result['stages']), {'approve', 'export-system', 'export-list'})
        self.assertEqual(result['gates'], ['待本人'])
        self.assertFalse(result['system_write_requested'])
        for call in (approve, pdf, listing):
            self.assertFalse(call.call_args.kwargs['execute'])

    def test_legacy_imports_keep_the_same_type_and_function(self):
        import registered_inputs
        import registered_workflow

        self.assertIs(registered_workflow.RegisteredSession, RegisteredSession)
        self.assertIs(registered_workflow.checked_reference, registered_inputs.checked_reference)
        self.assertIs(registered_workflow.load_definition, registered_inputs.load_definition)


if __name__ == '__main__':
    unittest.main()
