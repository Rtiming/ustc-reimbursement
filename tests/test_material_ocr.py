"""OCR接线、原任务恢复与文字回接；合成结果不冒充真实引擎验收。"""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from assistant_api import AssistantService, APIError, atomic_json, read_json
from material_intake import ocr_candidates
from material_ocr_service import input_plan, command
from ocr_workflow import fingerprint, run, publish_text


class MaterialOcrTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.materials = self.root / 'materials'
        self.materials.mkdir()
        self.service = AssistantService(self.root / 'workspace', [self.materials])
        self.service.new_case('fixture')
        self.source = self.materials / 'scan.png'
        self.source.write_bytes(b'synthetic-image-bytes')
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.inventory = {
            'files': [
                {
                    'working_copy': str(self.source),
                    'sha256': digest,
                    'kind': 'png',
                    'material_id': 'synthetic',
                    'occurrence_id': 'synthetic-occurrence',
                    'source_chain': str(self.source),
                    'root_source': str(self.source),
                }
            ],
            'pages': [],
        }
        self.job_id = 'j_' + '1' * 24
        self.folder = self.service.jobs / 'fixture' / self.job_id
        atomic_json(self.folder / 'request.json', {'operation': 'material_intake', 'execute': True})
        artifacts = []
        for index, (name, value) in enumerate(
            (
                ('material_inventory.json', self.inventory),
                ('ocr_request_candidates.json', ocr_candidates(self.inventory)),
            )
        ):
            path = self.folder / name
            atomic_json(path, value)
            artifacts.append(
                {
                    'name': name,
                    'path': str(path),
                    'artifact_id': 'a' + str(index),
                    'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
        atomic_json(
            self.folder / 'status.json',
            {'status': 'completed', 'exit_code': 0, 'job_id': self.job_id, 'artifacts': artifacts},
        )
        self.request = {'intake_job_id': self.job_id}
        self.plan = input_plan(self.service, 'fixture', self.request)
        self.runtime = {
            'profile': {
                'backend': 'rtime_compute',
                'compute_cli': '/configured/rtime',
                'wait_seconds': 1,
                'target': 'master',
            }
        }

    def test_plan_binds_same_case_hash_and_does_not_start_ocr(self):
        self.assertEqual(len(self.plan['requests']), 1)
        self.assertEqual(
            self.plan['requests'][0]['expected_sha256'],
            hashlib.sha256(self.source.read_bytes()).hexdigest(),
        )
        with patch('ocr_workflow.subprocess.run') as execute:
            preview = run(self.plan, self.runtime, self.root / 'unused')
        self.assertEqual(preview['pages'], 1)
        execute.assert_not_called()
        self.assertFalse((self.root / 'unused').exists())

    def test_changed_source_unknown_selection_and_wrong_job_are_rejected(self):
        with self.assertRaises(APIError):
            input_plan(self.service, 'fixture', {**self.request, 'candidate_ids': ['unknown']})
        with self.assertRaises(APIError):
            input_plan(self.service, 'fixture', {**self.request, 'rotation': True})
        self.source.write_bytes(b'changed')
        with self.assertRaisesRegex(APIError, 'SHA'):
            input_plan(self.service, 'fixture', self.request)

    def test_missing_provider_allows_preview_but_refuses_execution(self):
        request = {
            'case_id': 'fixture',
            'parameters': {'action': 'run', 'request': self.request},
            'execute': False,
        }
        folder = self.root / 'preview'
        folder.mkdir()
        argv = command(self.service, request, folder)
        self.assertNotIn('--execute', argv)
        self.assertEqual(
            read_json(folder / 'ocr-runtime.json')['profile']['backend'], 'unconfigured'
        )
        request['execute'] = True
        with self.assertRaisesRegex(APIError, '未配置'):
            command(self.service, request, self.root / 'execute')

    def test_empty_selection_skips_provider_and_engine(self):
        plan = {**self.plan, 'requests': []}
        with patch('ocr_workflow.subprocess.run') as execute:
            result = run(plan, {'profile': {'backend': 'none'}}, self.root / 'unused', execute=True)
        self.assertEqual(result['state'], 'no_ocr_needed')
        execute.assert_not_called()

    def remote_pending(self):
        folder = self.root / 'work' / fingerprint(self.plan, self.runtime)[:24]
        atomic_json(
            folder / 'state.json',
            {'fingerprint': fingerprint(self.plan, self.runtime), 'phase': 'remote_pending'},
        )
        atomic_json(folder / 'run/run.json', {'run_id': 'original'})
        return folder

    def test_resume_waits_original_run_without_submitting_again(self):
        self.remote_pending()
        response = SimpleNamespace(
            returncode=0,
            stdout=json.dumps({'status': 'needs_attention', 'wait': {'state': 'active'}}),
            stderr='',
        )
        with patch('ocr_workflow.subprocess.run', return_value=response) as execute:
            result = run(self.plan, self.runtime, self.root / 'work', execute=True, resume=True)
        self.assertEqual(result['state'], 'waiting_original_remote')
        self.assertEqual(execute.call_args.args[0][1:3], ['compute', 'wait'])
        self.assertEqual(execute.call_count, 1)

    def test_missing_original_resume_cannot_create_a_new_run(self):
        with patch('ocr_workflow.subprocess.run') as submit:
            with self.assertRaisesRegex(ValueError, '原OCR工作目录缺失'):
                run(self.plan, self.runtime, self.root / 'missing-work', execute=True, resume=True)
        submit.assert_not_called()
        self.assertFalse((self.root / 'missing-work').exists())

    def test_failed_remote_is_not_reported_as_waiting_or_published(self):
        self.remote_pending()
        response = SimpleNamespace(
            returncode=2,
            stdout=json.dumps({'status': 'needs_attention', 'wait': {'state': 'failed'}}),
            stderr='',
        )
        with (
            patch('ocr_workflow.subprocess.run', return_value=response),
            patch('ocr_workflow.publish_text') as publish,
        ):
            result = run(self.plan, self.runtime, self.root / 'work', execute=True, resume=True)
        self.assertFalse(result['ok'])
        self.assertEqual(result['state'], 'failed_original_remote')
        publish.assert_not_called()

    def test_publish_links_text_to_original_and_reuses_existing_review_parser(self):
        raw = self.root / 'raw'
        raw.mkdir()
        image = raw / 'page.png'
        image.write_bytes(b'ocr-view')
        report = raw / 'result.json'
        request = self.plan['requests'][0]
        atomic_json(
            report,
            {
                'schema': 'reimbursement.remote-ocr.v1',
                'status': 'machine_read_only',
                'item': {'id': request['id'], 'sha256': request['expected_sha256']},
                'pages': [
                    {
                        'page': 1,
                        'rotation_clockwise': 0,
                        'image': 'page.png',
                        'image_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
                        'text': 'SYNTHETIC OCR TEST 123.45',
                        'lines': [
                            {'confidence': 0.9, 'text': 'SYNTHETIC OCR TEST 123.45', 'box': []}
                        ],
                    }
                ],
            },
        )
        out = self.root / 'published'
        out.mkdir()
        result = publish_text(self.plan, [report], out, 'rtime_compute')
        self.assertEqual(result['reused_text_pages'], 1)
        imported = read_json(Path(result['inventory']))
        self.assertEqual(imported['pages'][0]['occurrence_id'], 'synthetic-occurrence')
        self.assertEqual(imported['pages'][0]['native_text'], '')
        self.assertIsNone(result['claim_amount'])
        self.assertTrue(result['machine_read_only'])
        evidence = read_json(Path(result['text_evidence']))
        self.assertEqual(evidence['records'][0]['source_sha256'], request['expected_sha256'])
        changed = {
            **self.plan,
            'inventory': {
                **self.inventory,
                'pages': [
                    {
                        'sha256': request['expected_sha256'],
                        'page': 1,
                        'native_text': 'original text',
                    }
                ],
            },
        }
        with self.assertRaisesRegex(ValueError, '不覆盖'):
            publish_text(changed, [report], out, 'rtime_compute')

    def test_pdf_native_fallback_never_runs_ocr(self):
        import pymupdf
        from scan_materials import extract_pdf

        pdf = self.root / 'native.pdf'
        document = pymupdf.open()
        page = document.new_page()
        page.insert_text((30, 40), 'NATIVE TEXT ONLY 123.45')
        document.new_page()
        document.save(pdf)
        document.close()
        with (
            patch('scan_materials.shutil.which', return_value=None),
            patch.dict(sys.modules, {'pypdf': None}),
            patch('scan_materials.ocr_image') as ocr,
        ):
            pages = extract_pdf(pdf, 'chi_sim', 3)
        self.assertEqual([p['method'] for p in pages], ['pdf_text', 'unreadable'])
        ocr.assert_not_called()


if __name__ == '__main__':
    unittest.main()
