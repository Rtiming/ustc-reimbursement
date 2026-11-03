import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from assistant_api import AssistantService, atomic_json, read_json, APIError
from system_pdf_batch import validate_entries, run_system_pdfs, reusable_export
import hashlib


class SystemPdfBatchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.service = AssistantService(self.root, [self.root])
        self.service.new_case('fixture')
        _, paths = self.service.resolve('fixture')
        f = read_json(paths['form'])
        f.update(serial='123', amount='10.00')
        f['basic']['fields']['t-abst'] = '合成摘要'
        atomic_json(paths['form'], f)
        self.plan = {
            'case_id': 'fixture',
            'serial': '123',
            'summary': '合成摘要',
            'view_kind': 'details',
            'delivery_mode': 'rename_only',
            'filename_separator': ' ',
        }
        self.manifest = self.root / 'manifest.json'
        atomic_json(
            self.manifest,
            {
                'schema': 'ustc-system-pdf-batch-v1',
                'expected_userid': 'SYNTH001',
                'source': 'fixture',
                'cases': [
                    {
                        'case_id': 'fixture',
                        'amount': '10.00',
                        'export_root': str(self.root / 'pdf'),
                        'export_plan': self.plan,
                    }
                ],
            },
        )
        self.cfg = {
            'expected_userid': 'SYNTH001',
            'system_pdf_manifest': {
                'path': str(self.manifest),
                'sha256': hashlib.sha256(self.manifest.read_bytes()).hexdigest(),
            },
        }

    def test_preview_does_not_create_browser_session(self):
        with patch('system_pdf_batch.RegisteredSession') as session:
            result = run_system_pdfs(self.service, 'batch', self.cfg, None, None)
        self.assertEqual(result['documents'], 1)
        session.assert_not_called()

    def test_unknown_export_does_not_replace_original_page(self):
        folder = self.root / 'session'
        folder.mkdir()
        key = hashlib.sha256(self.manifest.read_bytes()).hexdigest()[:16]
        atomic_json(
            folder / ('exports-' + key) / 'fixture.json',
            {'phase': 'export_requested', 'export_job_id': 'original'},
        )
        session = SimpleNamespace(
            folder=folder, ids=['fixture'], state_path=folder / 'session.json', ensure=Mock()
        )
        with (
            patch('system_pdf_batch.RegisteredSession', return_value=session),
            patch.object(
                self.service, 'pdf_export_inspect', return_value={'ok': False, 'status': 'blocked'}
            ),
        ):
            result = run_system_pdfs(self.service, 'batch', self.cfg, None, None, execute=True)
        session.ensure.assert_not_called()
        self.assertFalse(result['ok'])
        self.assertEqual(result['state'], 'original_task_needs_readback')

    def test_changed_bill_or_output_scope_is_rejected(self):
        data = read_json(self.manifest)
        data['cases'][0]['export_plan']['serial'] = '999'
        atomic_json(self.manifest, data)
        with self.assertRaises(APIError):
            validate_entries(self.service, self.manifest, 'SYNTH001')

    def cached_export(self):
        entry = read_json(self.manifest)['cases'][0]
        job = 'j_' + 'a' * 24
        folder = self.service.jobs / 'fixture' / job
        atomic_json(
            folder / 'request.json',
            {
                'parameters': {
                    'export_plan': self.plan,
                    'export_root': entry['export_root'],
                }
            },
        )
        pdf = self.root / 'pdf' / 'native.pdf'
        pdf.parent.mkdir()
        pdf.write_bytes(b'opaque-original-bytes')
        atomic_json(
            folder / 'pdf_export/export_receipt.json',
            {
                'pdf': str(pdf),
                'pdf_sha256': hashlib.sha256(pdf.read_bytes()).hexdigest(),
            },
        )
        return entry, job, folder, pdf

    def test_completed_batch_reuses_files_without_any_browser_job(self):
        entry, job, _, pdf = self.cached_export()
        folder = self.root / 'session'
        key = hashlib.sha256(self.manifest.read_bytes()).hexdigest()[:16]
        atomic_json(
            folder / ('exports-' + key) / 'fixture.json',
            {
                'phase': 'export_requested',
                'export_job_id': job,
            },
        )
        session = SimpleNamespace(
            folder=folder,
            ids=['fixture'],
            state_path=folder / 'session.json',
            begin_case=Mock(),
            job=Mock(),
        )
        before = pdf.read_bytes()
        with (
            patch('system_pdf_batch.RegisteredSession', return_value=session),
            patch.object(
                self.service, 'pdf_export_inspect', return_value={'ok': True, 'status': 'ready'}
            ) as inspect,
        ):
            result = run_system_pdfs(self.service, 'batch', self.cfg, None, None, execute=True)
        self.assertTrue(result['ok'])
        self.assertEqual(result['pdf_exported'], 1)
        self.assertFalse(result['live_rechecked'])
        inspect.assert_called_once_with('fixture', job)
        session.begin_case.assert_not_called()
        session.job.assert_not_called()
        self.assertEqual(pdf.read_bytes(), before)

    def test_reuse_requires_same_output_directory_as_original_job(self):
        entry, job, _, _ = self.cached_export()
        entry['export_root'] = str(self.root / 'different')
        with patch.object(
            self.service, 'pdf_export_inspect', return_value={'ok': True, 'status': 'ready'}
        ):
            with self.assertRaisesRegex(APIError, '归档目录不同'):
                reusable_export(self.service, entry, {'export_job_id': job})

    def native_session(self, *, draft=False, fail_export=False):
        folder = self.root / 'session'
        folder.mkdir()
        session = SimpleNamespace(folder=folder, ids=['fixture'], state_path=folder/'session.json',
                                  state={'tab_id':'7'}, ensure=Mock(), begin_case=Mock())
        calls=[]
        def job(case, operation, parameters, label, execute=False, **kwargs):
            calls.append((operation,parameters,execute))
            if operation=='business':
                self.assertEqual(parameters['action'],'bills-list')
                return ({'job_id':'list','artifacts':[{'name':'output.json','artifact_id':'a','sha256':'f'*64}]},
                        {'ok':True,'complete':True,'rows':[{'t.serial_no':'123','t.status':'-99' if draft else '10'}], 'total_records':1})
            self.assertEqual(operation,'pdf_export')
            self.assertTrue(execute)
            self.assertEqual(parameters['export_plan'],self.plan)
            key=hashlib.sha256(self.manifest.read_bytes()).hexdigest()[:16]
            state=read_json(folder/('exports-'+key)/'fixture.json')
            self.assertEqual(state['phase'],'http_export_requested')
            kwargs['on_enqueued']({'job_id':'export'})
            if fail_export:
                raise APIError('original_unknown','original export not completed')
            return ({'job_id':'export'}, {'ok':True,'status':'ready','pdf':'native.pdf','pdf_sha256':'f'*64,
                    'kind':'system_view_preview_not_final_print','source_type':'native_business_api',
                    'details_transport':'native_http','approval_verified':False,'final_print_verified':False})
        session.job=job
        return session,calls

    def test_new_batch_uses_http_without_detail_controls(self):
        session,calls=self.native_session()
        with patch('system_pdf_batch.RegisteredSession',return_value=session):
            r=run_system_pdfs(self.service,'batch',self.cfg,None,None,execute=True)
        self.assertTrue(r['ok']);self.assertEqual(r['pdf_exported'],1)
        self.assertEqual([c[0]for c in calls],['business','pdf_export'])
        session.begin_case.assert_not_called()
        self.assertFalse(r['approval_completed'])

    def test_drafts_are_held_before_generation(self):
        session,calls=self.native_session(draft=True)
        with patch('system_pdf_batch.RegisteredSession',return_value=session):
            r=run_system_pdfs(self.service,'batch',self.cfg,None,None,execute=True)
        self.assertTrue(r['ok']);self.assertEqual(r['held_count'],1)
        self.assertEqual([c[0]for c in calls],['business'])

    def test_unknown_http_generation_keeps_original_job_and_blocks_new_run(self):
        session,calls=self.native_session(fail_export=True)
        with patch('system_pdf_batch.RegisteredSession',return_value=session):
            r=run_system_pdfs(self.service,'batch',self.cfg,None,None,execute=True)
            self.assertFalse(r['ok'])
            key=hashlib.sha256(self.manifest.read_bytes()).hexdigest()[:16]
            original=read_json(session.folder/('exports-'+key)/'fixture.json')
            self.assertEqual(original['export_job_id'],'export')
            with patch.object(self.service,'pdf_export_inspect',return_value={'ok':False,'status':'blocked'}):
                again=run_system_pdfs(self.service,'batch',self.cfg,None,None,execute=True)
        self.assertFalse(again['ok'])
        self.assertEqual(len(calls),2)


if __name__ == '__main__':
    unittest.main()
