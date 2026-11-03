"""四种打印组合、来源隔离、异步参数和失败恢复的合成验证。"""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import pymupdf as fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from assistant_api import APIError, AssistantService, atomic_json, read_json
from finance_print_batch import prepare, preview, run
from print_delivery import deliver, fingerprint


def pdf(path, labels):
    with fitz.open() as doc:
        for index, label in enumerate(labels):
            page = doc.new_page(width=842 if index % 2 else 595, height=595 if index % 2 else 842)
            page.insert_text((30, 40), label)
        doc.save(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PrintTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.service = AssistantService(self.root, [self.root])
        self.service.new_case('batch')
        rows = []
        for n in (1, 2):
            case = 'bill' + str(n)
            self.service.new_case(case)
            _, paths = self.service.resolve(case)
            form = read_json(paths['form'])
            form.update(serial=str(100 + n), amount='10.00')
            form['basic']['fields'].update({'t-abst': 'Synthetic ' + str(n), 't-req_mobile': '000'})
            form['contract']['exists'] = False
            atomic_json(paths['form'], form)
            plan = {'schema': 'ustc-finance-pdf-export-plan-v1', 'case_id': case,
                    'serial': form['serial'], 'status': 'confirmed', 'business_date': '2026-10-27',
                    'sequence': n, 'summary': form['basic']['fields']['t-abst'],
                    'source_refs': ['synthetic'], 'view_kind': 'details'}
            rows.append({'case_id': case, 'amount': '10.00', 'export_plan': plan})
        self.manifest = self.root / 'manifest.json'
        atomic_json(self.manifest, {'schema': 'ustc-system-pdf-batch-v1',
            'expected_userid': 'SYNTH001', 'source': 'synthetic', 'cases': rows})
        self.cfg = {'expected_userid': 'SYNTH001', 'print_output_root': str(self.root / 'exports'),
                    'system_pdf_manifest': {'path': str(self.manifest),
                    'sha256': hashlib.sha256(self.manifest.read_bytes()).hexdigest()}}
        self.index = self.root / 'index.json'
        atomic_json(self.index, {'documents': [{'case_id': r['case_id']} for r in rows]})
        self.loader = patch('finance_print_batch.load_definition', return_value=(self.cfg, self.index, None, None))
        self.loader.start()
        self.addCleanup(self.loader.stop)

    def spec(self, **options):
        return prepare(self.service, 'batch', **options)[3]

    def test_four_independent_combinations_and_no_browser_preview(self):
        for merge in (False, True):
            for only in (False, True):
                with self.subTest(merge=merge, only=only), patch('finance_print_batch.RegisteredSession') as session:
                    result = run(self.service, 'batch', merge_pdf=merge, finance_only=only)
                    self.assertEqual(result['options'], {'merge_pdf': merge, 'finance_only': only})
                    self.assertEqual(result['documents'], 2)
                    self.assertEqual(result['plan']['documents'][0]['export_plan']['include_invoice_attachments'], not only)
                    session.assert_not_called()
        self.assertFalse((self.root / 'exports').exists())

    def test_options_defaults_overrides_and_strict_types(self):
        self.cfg['print_options'] = {'merge_pdf': True, 'finance_only': False}
        self.assertEqual(self.spec()['options'], {'merge_pdf': True, 'finance_only': False})
        self.assertEqual(self.spec(merge_pdf=False)['options'], {'merge_pdf': False, 'finance_only': False})
        for value in ('false', 0, 1, []):
            with self.assertRaises(APIError):
                self.spec(finance_only=value)
        self.cfg['print_options']['unexpected'] = True
        with self.assertRaises(APIError):
            self.spec()

    def test_selection_scope_path_and_input_binding(self):
        self.assertEqual([r['serial'] for r in self.spec(sequences=[2])['documents']], ['102'])
        for seq in ([3], [1, 1], [], ['1']):
            with self.assertRaises(APIError):
                self.spec(sequences=seq)
        with self.assertRaises(APIError):
            self.spec(output_root=str(self.root.parent / 'outside'))
        form_path = self.service.resolve('bill1')[1]['form']
        form = read_json(form_path)
        form['amount'] = '11.00'
        atomic_json(form_path, form)
        with self.assertRaises(APIError):
            self.spec()

    def test_merge_switch_reuses_same_native_documents_but_content_switch_does_not(self):
        first, merged, full = self.spec(), self.spec(merge_pdf=True), self.spec(finance_only=False)
        self.assertEqual(first['documents'], merged['documents'])
        self.assertNotEqual(first['documents'], full['documents'])
        self.assertNotEqual(fingerprint(first), fingerprint(merged))

    def test_async_request_freezes_both_options_and_uses_existing_job_layer(self):
        with patch.object(self.service, 'enqueue', return_value={'job_id': 'same'}) as enqueue:
            self.service.print_batch('batch', 'print-1', False, False, sequences=[2])
        args = enqueue.call_args.args
        self.assertEqual(args[:2], ('batch', 'print_batch'))
        self.assertFalse(args[2]['finance_only'])
        self.assertFalse(args[2]['merge_pdf'])
        self.assertEqual(args[2]['sequences'], [2])
        self.assertEqual(args[3:], ('print-1', False))

    def test_queued_input_drift_is_rejected_before_any_browser(self):
        before = fingerprint(self.spec())
        self.cfg['print_options'] = {'finance_only': False}
        with patch('finance_print_batch.RegisteredSession') as session, self.assertRaises(APIError):
            run(self.service, 'batch', execute=True, expected_fingerprint=before)
        session.assert_not_called()

    def test_unknown_action_stops_before_opening_or_replacing_page(self):
        spec = self.spec()
        folder = self.root / '.local/registered_workflows/batch-print'
        atomic_json(folder / 'documents' / (fingerprint(spec['documents'][0]) + '.json'),
                    {'phase': 'opening', 'attempt': 1, 'action_job_id': 'original'})
        with patch.object(self.service, 'result', return_value={'status': 'unknown'}), \
             patch('finance_print_batch.RegisteredSession.begin_case') as begin, \
             patch('finance_print_batch.RegisteredSession.ensure') as ensure:
            result = run(self.service, 'batch', execute=True)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error_code'], 'print_original_action')
        begin.assert_not_called()
        ensure.assert_not_called()

    def test_completed_exports_reused_without_browser_even_when_merge_changes(self):
        spec = self.spec()
        folder = self.root / '.local/registered_workflows/batch-print'
        sources = {}
        for entry in spec['documents']:
            case = entry['case_id']
            p = self.root / (case + '.pdf')
            sha = pdf(p, [entry['serial'] + ' finance'])
            sources[case] = {'status': 'reused', 'pdf': str(p), 'pdf_sha256': sha}
            atomic_json(folder / 'documents' / (fingerprint(entry) + '.json'),
                        {'phase': 'returned', 'export_job_id': case})
        with patch('finance_print_batch._source', side_effect=lambda svc, entry, state: sources[entry['case_id']]), \
             patch('finance_print_batch.RegisteredSession.begin_case') as begin, \
             patch('finance_print_batch.RegisteredSession.job') as job:
            result = run(self.service, 'batch', execute=True, merge_pdf=True)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['pdf_exported'], 2)
        self.assertTrue(Path(result['delivery']['merged_pdf']).is_file())
        begin.assert_not_called()
        job.assert_not_called()

    def native_session(self, *, reject_first=False, resume_phase=None):
        """模拟学校可打印/待审批响应；实际调用顺序与产物独立断言。"""
        folder = self.root / '.local/registered_workflows/batch-print'
        folder.mkdir(parents=True, exist_ok=True)
        session = SimpleNamespace(folder=folder, ids=['bill1', 'bill2'],
            state_path=folder / 'session.json', state={'tab_id': '7', 'last_case_id': 'bill1'})
        session.begin_case = Mock()
        session.ensure = Mock()
        calls, scene = [], {'phase': resume_phase or 'list_ready'}
        def job(case, operation, params, label, execute=False, request_id=None, on_enqueued=None):
            calls.append((case, operation, params, execute))
            queued = {'job_id': 'native-' + str(len(calls))}
            if on_enqueued:
                on_enqueued(queued)
            if operation == 'pdf_export':
                if reject_first and case=='bill1':return queued, {'ok':True,'school_rejected':True,'school_message':'审核未完成'}
                from finance_pdf_export import export_relative_path
                plan = params['export_plan']
                path = Path(params['export_root']) / export_relative_path(plan, plan)
                path.parent.mkdir(parents=True, exist_ok=True)
                labels = [case + ' finance1', case + ' finance2']
                if plan['include_invoice_attachments']:
                    labels.append(case + ' invoice')
                return queued, {'ok': True, 'kind': 'system_formal_print', 'final_print_verified': True,
                                'pdf': str(path), 'pdf_sha256': pdf(path, labels),'business_transport':'native_http'}
            raise AssertionError(operation)
        session.job = job
        return session, calls

    def test_native_batch_all_combinations_pass_options_and_keep_bill_order(self):
        for merge in (False, True):
            for only in (False, True):
                with self.subTest(merge=merge, only=only):
                    session, calls = self.native_session()
                    # 每个合成场景是新目录，避免把已生成的模拟文件误作新学校打印。
                    root = self.root / ('native-' + str(merge) + str(only))
                    with patch('finance_print_batch.RegisteredSession', return_value=session):
                        result = run(self.service, 'batch', execute=True, output_root=str(root),
                                     merge_pdf=merge, finance_only=only)
                    self.assertTrue(result['ok'], result)
                    self.assertEqual(result['pdf_exported'], 2)
                    choices = [r for r in calls if r[1] == 'pdf_export']
                    self.assertEqual(len(calls),2)
                    self.assertTrue(all(r[2]['export_plan']['view_kind']=='formal_print_api' for r in choices))
                    self.assertEqual(len(choices), 2)
                    self.assertTrue(all(r[2]['export_plan']['include_invoice_attachments'] is (not only) for r in choices))
                    self.assertEqual([r['serial'] for r in result['results']], ['101', '102'])
                    self.assertEqual(bool(result['delivery']['merged_pdf']), merge)

    def test_school_pending_bill_is_held_and_next_bill_still_prints(self):
        session, calls = self.native_session(reject_first=True)
        with patch('finance_print_batch.RegisteredSession', return_value=session):
            result = run(self.service, 'batch', execute=True, merge_pdf=True)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['state'], 'completed_with_holds')
        self.assertEqual(result['pdf_exported'], 1)
        self.assertEqual(result['held_count'], 1)
        self.assertEqual([c for c,op,_,_ in calls if op=='pdf_export'], ['bill1','bill2'])

    def test_recovery_runs_before_other_bills_and_delivery_keeps_manifest_order(self):
        spec=self.spec();session,calls=self.native_session()
        atomic_json(session.folder/'documents'/(fingerprint(spec['documents'][1])+'.json'),
                    {'phase':'export_requested','attempt':1,'export_job_id':'original'})
        with patch('finance_print_batch.RegisteredSession',return_value=session), \
             patch('finance_print_batch._source',side_effect=lambda svc,e,state: {'status':'resume_download','resume_job_id':'original'} if e['case_id']=='bill2' else None):
            result=run(self.service,'batch',execute=True)
        self.assertTrue(result['ok'],result)
        self.assertEqual([case for case,_,_,_ in calls],['bill2','bill1'])
        self.assertEqual([row['case_id'] for row in result['results']],['bill1','bill2'])
        self.assertEqual(calls[0][2]['resume_job_id'],'original')
        session.ensure.assert_called_once_with(preserve_original=True)

    def test_pending_api_export_cannot_start_new_request_or_page(self):
        spec = self.spec(sequences=[1])
        session, calls = self.native_session()
        atomic_json(session.folder / 'documents' / (fingerprint(spec['documents'][0]) + '.json'),
                    {'phase': 'export_requested', 'attempt': 1, 'export_job_id': 'original'})
        with patch('finance_print_batch.RegisteredSession', return_value=session), \
             patch.object(self.service,'pdf_export_inspect',return_value={'status':'blocked'}):
            result=run(self.service,'batch',execute=True,sequences=[1])
        self.assertFalse(result['ok'])
        self.assertEqual(calls,[])
        session.ensure.assert_not_called()


class DeliveryTests(unittest.TestCase):
    def test_four_combinations_order_orientation_originals_and_reuse(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            for only in (True, False):
                rows, expected = [], []
                for serial in ('102', '101'):
                    labels = [serial + ' finance1', serial + ' finance2']
                    if not only:
                        labels.append(serial + ' invoice')
                    expected.extend(labels)
                    p = root / (serial + str(only) + '.pdf')
                    sha = pdf(p, labels)
                    rows.append({'case_id': 'bill' + serial, 'serial': serial, 'summary': 'Fixture',
                                 'status': 'exported', 'pdf': str(p), 'pdf_sha256': sha})
                for merge in (False, True):
                    result = deliver(rows, root / 'delivery', merge_pdf=merge, finance_only=only)
                    self.assertEqual(result['exported_count'], 2)
                    self.assertEqual(bool(result['merged_pdf']), merge)
                    self.assertTrue(deliver(rows, root / 'delivery', merge_pdf=merge, finance_only=only)['reused'])
                    if merge:
                        with fitz.open(result['merged_pdf']) as doc:
                            self.assertEqual([p.get_text().strip() for p in doc], expected)
                            self.assertEqual([x[2] for x in doc.get_toc()], [1, 3 if only else 4])
                            self.assertGreater(doc[1].rect.width, doc[1].rect.height)
                    for r in rows:
                        self.assertEqual(hashlib.sha256(Path(r['pdf']).read_bytes()).hexdigest(), r['pdf_sha256'])

    def test_unmerged_does_not_parse_pdf_and_missing_files_are_not_silently_skipped(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            p = root / 'opaque.pdf'
            p.write_bytes(b'opaque native transport bytes')
            rows = [{'case_id': 'bill1', 'serial': '101', 'status': 'exported', 'pdf': str(p),
                     'pdf_sha256': hashlib.sha256(p.read_bytes()).hexdigest()}]
            with patch('pymupdf.open', side_effect=AssertionError('不做正文复核')):
                result = deliver(rows, root / 'out', merge_pdf=False, finance_only=True)
            p.unlink()
            with self.assertRaises(APIError):
                deliver(rows, root / 'out', merge_pdf=False, finance_only=True)

    def test_partial_merge_is_named_as_partial_and_tampered_delivery_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            p = root / 'one.pdf'
            rows = [{'case_id': 'bill1', 'serial': '101', 'status': 'exported', 'pdf': str(p),
                     'pdf_sha256': pdf(p, ['finance'])},
                    {'case_id': 'bill2', 'serial': '102', 'status': 'held_waiting_approval', 'issue': 'pending'}]
            result = deliver(rows, root / 'out', merge_pdf=True, finance_only=True)
            self.assertFalse(result['complete'])
            self.assertIn('部分完成', result['merged_pdf'])
            Path(result['merged_pdf']).write_bytes(b'changed')
            with self.assertRaises(APIError):
                deliver(rows, root / 'out', merge_pdf=True, finance_only=True)


if __name__ == '__main__':
    unittest.main()
