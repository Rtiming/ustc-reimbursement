"""PDF分块和排他落盘的合成验证；不访问浏览器或真实财务。"""
import base64
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from finance_pdf_export import (KIND, MAX_BYTES, MAX_CHUNK, _atomic_new, _runtime_stage,
                               decode_chunk, digest, export_pdf, inspect_export, pdf_script,
                               persist_verified_pdf)
from finance_pdf_export import BINDING_KEYS, MAX_INTENT_JSON, MAX_STAGE_JSON, _read_snapshot


def case():
    return {'case_id': 'synthetic-pdf', 'serial': '12345'}


def plan():
    return {'schema': 'ustc-finance-pdf-export-plan-v1', **case(), 'status': 'confirmed',
            'business_date': '2026-09-02', 'sequence': '001', 'summary': '合成活动；联系人合成人',
            'source_refs': ['synthetic-confirmation'], 'delivery_mode':'content'}


def pdf_bytes(serial='12345', pages=2):
    with pymupdf.open() as document:
        for _ in range(pages):
            page = document.new_page()
            page.insert_text((20, 30), '报销单号：' + serial, fontname='china-s', fontsize=12)
        return document.tobytes()


def metadata(raw):
    return {'ok': True, 'kind': KIND, **case(), 'userid': 'DEMO01', 'origin': 'https://cwzh.ustc.edu.cn',
            'pathname': '/YBX/main.jsp', 'project': 'WF_YBX', 'node': 'synthetic-preview',
            'session_token': 'a' * 32, 'pdf_fingerprint': 'synthetic-fingerprint', 'pages': 2,
            'size': len(raw), 'sha256': digest(raw), 'pdf_serial': '12345',
            'viewer_path': 'https://cwzh.ustc.edu.cn/synthetic-viewer', 'source_type': 'synthetic_test'}


def config():
    return {**case(), 'tab_id': '7', 'expected_userid': 'DEMO01', 'silent': True,
            'session_token': 'a' * 32, 'max_bytes': MAX_BYTES, 'max_chunk': MAX_CHUNK}


class PdfExportTests(unittest.TestCase):
    def test_api_plan_requires_form_amount_summary_and_preserves_transport_contract(self):
        from finance_pdf_export import _request, _metadata
        mapped={**plan(),'amount':'10.00','view_kind':'formal_print_api','delivery_mode':'rename_only','include_invoice_attachments':False}
        form={**case(),'amount':'10.00','basic':{'fields':{'t-abst':mapped['summary']}}}
        with tempfile.TemporaryDirectory()as temp:
            req=_request(form,mapped,temp,'7','DEMO01',True,131072)
            self.assertEqual(req['expected_amount'],'10.00')
            self.assertEqual(req['expected_summary'],mapped['summary'])
            req['session_token']='a'*32
            meta={**metadata(b'%PDF-native'),'kind':'system_formal_print','pages':None,'view_kind':'formal_print_api','final_print_verified':True,'include_invoice_attachments':False}
            _metadata(meta,req)
            with self.assertRaises(ValueError):_metadata({**meta,'include_invoice_attachments':True},req)
            with self.assertRaises(ValueError):_request({**form,'amount':'11.00'},mapped,temp,'7','DEMO01',True,131072)
            self.assertIn('reimbursementPrintApi',pdf_script('begin',req))

    def test_details_http_requires_current_form_and_keeps_preview_identity(self):
        from finance_pdf_export import _request, _metadata
        mapped={**plan(),'view_kind':'details','delivery_mode':'rename_only'}
        form={**case(),'amount':'10','basic':{'fields':{'t-abst':mapped['summary']}}}
        with tempfile.TemporaryDirectory() as temp:
            req=_request(form,mapped,temp,'7','DEMO01',True,131072)
            self.assertEqual(req['expected_amount'],'10.00')
            self.assertEqual(req['details_transport'],'native_http')
            self.assertIn('reimbursementDetailsApi',pdf_script('begin',req))
            req['session_token']='a'*32
            meta={**metadata(b'%PDF-test'),'pages':None,'view_kind':'details','details_transport':'native_http',
                  'business_transport':'native_http','final_print_verified':False,'approval_verified':False}
            _metadata(meta,req)
            for change in ({'final_print_verified':True},{'details_transport':'legacy_viewer'},{'approval_verified':True}):
                with self.assertRaises(ValueError):_metadata({**meta,**change},req)
            with self.assertRaises(ValueError):_request(case(),mapped,temp,'7','DEMO01',True,131072)
            with self.assertRaises(ValueError):_request(form,{**mapped,'amount':'11'},temp,'7','DEMO01',True,131072)

    def test_details_recovery_only_accepts_known_generated_original(self):
        from finance_pdf_export import _request, print_recovery, encoded
        mapped={**plan(),'view_kind':'details','delivery_mode':'rename_only'}
        form={**case(),'amount':'10','basic':{'fields':{'t-abst':mapped['summary']}}}
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'task';(out/'stages').mkdir(parents=True)
            req=_request(form,mapped,temp,'7','DEMO01',True,131072);req['session_token']='a'*32
            (out/'intent.json').write_bytes(encoded({'request':req,'export_plan':mapped}))
            script=b'// original synthetic details adapter'
            (out/'stages/00000_begin.js').write_bytes(script)
            record={'phase':'script_completed','tab_id':'7','source_sha256':digest(script),
                    'result':{'ok':False,'error':'DETAILS_API_DOWNLOAD_UNKNOWN'}}
            stage=out/'stages/00000_begin.json';stage.write_bytes(encoded(record))
            self.assertEqual(print_recovery(out,{k:req[k] for k in BINDING_KEYS})['kind'],'native_details_original')
            record['result']['error']='DETAILS_API_BILL_STATE_OR_SUMMARY';stage.write_bytes(encoded(record))
            self.assertEqual(print_recovery(out,{k:req[k] for k in BINDING_KEYS})['kind'],'native_details_read_rejection')
            for error in ('DETAILS_API_DEFINITION_CHANGED','DETAILS_API_REQUEST_UNKNOWN:timeout'):
                record['result']['error']=error;stage.write_bytes(encoded(record))
                with self.assertRaises(ValueError):print_recovery(out,{k:req[k] for k in BINDING_KEYS})

    def test_recovery_requires_completed_original_evidence_and_fixed_pdf_reference(self):
        from finance_pdf_export import _request, print_recovery, encoded
        mapped={**plan(),'amount':'10.00','view_kind':'formal_print_api','delivery_mode':'rename_only','include_invoice_attachments':False}
        form={**case(),'amount':'10.00','basic':{'fields':{'t-abst':mapped['summary']}}}
        with tempfile.TemporaryDirectory()as temp:
            root=Path(temp);output=root/'task';(output/'stages').mkdir(parents=True)
            req=_request(form,mapped,root,'7','DEMO01',True,131072);req['session_token']='a'*32
            (output/'intent.json').write_bytes(encoded({'request':req,'export_plan':mapped}))
            script=b'// synthetic original transport';(output/'stages/00000_begin.js').write_bytes(script)
            record={'phase':'script_completed','tab_id':'7','source_sha256':digest(script),
                'result':{'ok':False,'error':'PRINT_API_PDF_REFERENCE_INVALID:abc123&fixture.PDF&.PDF'}}
            stage=output/'stages/00000_begin.json';stage.write_bytes(encoded(record))
            binding={k:req[k] for k in BINDING_KEYS}
            recovered=print_recovery(output,binding)
            self.assertEqual(recovered['reference'],'abc123&fixture.PDF&.PDF')
            for error,kind in [('PRINT_API_BILL_CONTENT_MISMATCH','read_rejection'),('PRINT_API_PENDING_RECOVER_ORIGINAL','not_dispatched')]:
                record['result']['error']=error;stage.write_bytes(encoded(record))
                self.assertEqual(print_recovery(output,binding)['kind'],kind)
            for error in ['PRINT_API_REQUEST_UNKNOWN:timeout','PRINT_API_PDF_REFERENCE_INVALID:https://example.test/file.pdf']:
                record['result']['error']=error;stage.write_bytes(encoded(record))
                with self.assertRaises(ValueError):print_recovery(output,binding)
            record['result']['error']='PRINT_API_PDF_REFERENCE_INVALID:abc123&fixture.PDF&.PDF'
            record['phase']='unknown';stage.write_bytes(encoded(record))
            with self.assertRaises(ValueError):print_recovery(output,binding)

    def test_native_large_chunk_stays_under_message_budget_and_preserves_bytes(self):
        raw=b'x'*MAX_CHUNK;meta=metadata(raw);request=config()
        chunk={**meta,'action':'pdf_chunk','offset':0,'chunk_size':len(raw),'chunk_sha256':digest(raw),'base64':base64.b64encode(raw).decode()}
        self.assertLess(len(json.dumps({'id':'a'*32,'result':json.dumps(chunk)}).encode()),1_000_000)
        self.assertEqual(decode_chunk(chunk,meta,request,0,len(raw)),raw)

    def test_details_view_binds_separate_frame_kind_and_allows_no_workflow_node(self):
        raw=b'%PDF-details-fixture';mapped={**plan(),'view_kind':'details','delivery_mode':'rename_only'}
        observed={**metadata(raw),'view_kind':'details','node':''}
        with tempfile.TemporaryDirectory()as temp:
            result=persist_verified_pdf(raw,observed,case(),mapped,temp,Path(temp)/'receipt.json',tab_id='7',expected_userid='DEMO01')
            self.assertEqual(result['document_identity']['view_kind'],'details');self.assertFalse(result['content_verified'])
        with tempfile.TemporaryDirectory()as temp,self.assertRaises(ValueError):
            persist_verified_pdf(raw,observed,case(),plan(),temp,Path(temp)/'receipt.json',tab_id='7',expected_userid='DEMO01')

    def test_rename_only_pipeline_accepts_transport_finish_without_content_verification(self):
        raw=b'%PDF-synthetic-transport-fixture';mapped=plan();mapped.pop('delivery_mode');mapped['filename_separator']=' '
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'transport-run'
            def stage(action,request,directory,index):
                value=metadata(raw)|{'session_token':request['session_token'],'action':'pdf_'+action}
                if action=='chunk':
                    part=raw[request['offset']:request['offset']+request['length']]
                    value.update(offset=request['offset'],chunk_size=len(part),chunk_sha256=digest(part),base64=base64.b64encode(part).decode())
                if action=='finish':value.update(transport_finished=True,reverified=False,content_verified=False)
                script=directory/f'{index:05d}_{action}.js';receipt=directory/f'{index:05d}_{action}.json'
                script.write_bytes(pdf_script(action,request).encode())
                receipt.write_text(json.dumps({'phase':'script_completed','tab_id':'7','script':str(script),'source_sha256':digest(script.read_bytes()),'result':value}))
                return value,{'path':str(receipt),'sha256':digest(receipt.read_bytes())}
            with patch('finance_pdf_export._runtime_stage',side_effect=stage),patch('finance_pdf_export._verified_pdf',side_effect=AssertionError('不能核对正文')):
                result=export_pdf(case(),mapped,temp,output,'7','DEMO01',execute=True)
            self.assertEqual(result['status'],'ready',result);self.assertFalse(result['content_verified'])
            self.assertTrue(Path(result['pdf']).name.startswith('001 '))
            self.assertEqual(Path(result['pdf']).read_bytes(),raw)
            self.assertEqual(inspect_export(output)['status'],'ready')

    def test_default_delivery_uses_mapping_without_parsing_or_content_review(self):
        raw=b'%PDF-synthetic-download-bytes-not-for-content-parsing'
        mapped=plan();mapped.pop('delivery_mode')
        with tempfile.TemporaryDirectory() as temp, patch('finance_pdf_export._verified_pdf') as content_check:
            result=persist_verified_pdf(raw,metadata(raw),case(),mapped,temp,Path(temp)/'receipt.json',
                                        tab_id='7',expected_userid='DEMO01')
            self.assertEqual(Path(result['pdf']).read_bytes(),raw)
            self.assertEqual(result['delivery_mode'],'rename_only')
            self.assertFalse(result['content_verified'])
            content_check.assert_not_called()

    def incomplete_fixture(self, directory):
        output = Path(directory, 'incomplete-run').resolve()
        with patch('finance_pdf_export._runtime_stage', side_effect=RuntimeError('synthetic interruption')):
            export_pdf(case(), plan(), directory, output, '7', 'DEMO01', execute=True)
        intent = json.loads((output / 'intent.json').read_text(encoding='utf-8'))
        binding = {key: intent['request'][key] for key in BINDING_KEYS}
        return output, intent, binding | {'job_status': 'unknown'}

    def unknown_stage_fixture(self, output, request):
        script, receipt = output / 'stages/00000_begin.js', output / 'stages/00000_begin.json'
        script.write_bytes(pdf_script('begin', request).encode('utf-8'))
        record = {'phase': 'unknown', 'script': str(script), 'source_sha256': digest(script.read_bytes()),
                  'tab_id': request['tab_id'], 'result_marker': 'synthetic-original-marker',
                  'result': {'ok': False, 'execution_status': 'unknown_browser_local_recover_original_marker'}}
        receipt.write_text(json.dumps(record), encoding='utf-8')
        return receipt, record

    def test_preview_does_not_read_browser_or_create_files(self):
        with tempfile.TemporaryDirectory() as temp, patch('finance_pdf_export._runtime_stage') as runtime:
            result = export_pdf(case(), plan(), temp, Path(temp, 'new-run'), '7', 'DEMO01')
            self.assertEqual(result['status'], 'ready')
            self.assertFalse(result['executed'])
            self.assertTrue(result['silent'])
            self.assertEqual(result['kind'], KIND)
            self.assertEqual(list(Path(temp).iterdir()), [])
            runtime.assert_not_called()

    def test_missing_or_wrong_identity_and_plan_are_rejected_before_browser(self):
        with tempfile.TemporaryDirectory() as temp, patch('finance_pdf_export._runtime_stage') as runtime:
            for modified in [plan() | {'serial': '999'}, plan() | {'business_date': None},
                             plan() | {'sequence': None}, plan() | {'summary': ''},
                             plan() | {'status': 'proposed'}, plan() | {'relative_path': '../outside.pdf'}]:
                with self.subTest(plan=modified), self.assertRaises(ValueError):
                    export_pdf(case(), modified, temp, Path(temp, 'run'), '7', 'DEMO01', execute=True)
            for tab, who in [(None, 'DEMO01'), ('7', ''), ('wrong', 'DEMO01')]:
                with self.assertRaises(ValueError):
                    export_pdf(case(), plan(), temp, Path(temp, 'run'), tab, who, execute=True)
            runtime.assert_not_called()
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_chunk_integrity_and_identity_checks(self):
        raw = pdf_bytes()
        initial = metadata(raw)
        part = raw[:1024]
        value = {**initial, 'action': 'pdf_chunk', 'offset': 0, 'chunk_size': len(part),
                 'chunk_sha256': digest(part), 'base64': base64.b64encode(part).decode()}
        self.assertEqual(decode_chunk(value, initial, config(), 0, len(part)), part)
        for modifications in [{'offset': 1}, {'userid': 'OTHER'}, {'pdf_fingerprint': 'changed'},
                              {'pages': 3}, {'chunk_sha256': '0' * 64}, {'base64': '!!'}, {'size': len(raw) + 1}]:
            with self.subTest(modifications=modifications), self.assertRaises(ValueError):
                decode_chunk(value | modifications, initial, config(), 0, len(part))

    def test_verified_synthetic_pdf_is_explicitly_not_a_finance_artifact(self):
        raw = pdf_bytes()
        with tempfile.TemporaryDirectory() as temp:
            receipt = Path(temp, 'synthetic-receipt.json')
            result = persist_verified_pdf(raw, metadata(raw), case(), plan(), temp, receipt,
                                          tab_id='7', expected_userid='DEMO01')
            self.assertEqual(result['kind'], 'synthetic_test_pdf_not_finance')
            self.assertFalse(result['final_print_verified'])
            self.assertEqual(Path(result['pdf']).read_bytes(), raw)
            self.assertEqual(result['pdf_sha256'], digest(raw))
            self.assertTrue(receipt.is_file())
            with self.assertRaises(FileExistsError):
                persist_verified_pdf(raw, metadata(raw), case(), plan(), temp, Path(temp, 'another.json'),
                                     tab_id='7', expected_userid='DEMO01')

    def test_bytes_without_native_proof_cannot_masquerade_as_finance(self):
        raw = pdf_bytes()
        meta = metadata(raw)
        del meta['source_type']
        with tempfile.TemporaryDirectory() as temp, self.assertRaisesRegex(ValueError, '原生读取证据'):
            persist_verified_pdf(raw, meta, case(), plan(), temp, Path(temp, 'receipt.json'), tab_id='7', expected_userid='DEMO01')
        self.assertEqual(meta['kind'], KIND)

    def test_page_count_sha_and_body_serial_are_independently_verified(self):
        for raw, update in [(pdf_bytes(), {'pages': 3}), (pdf_bytes(), {'sha256': '0' * 64}),
                            (pdf_bytes('99999'), {}), (b'%PDF-fake-byte-header', {})]:
            with tempfile.TemporaryDirectory() as temp, self.subTest(update=update):
                meta = metadata(raw) | update
                with self.assertRaises((ValueError, pymupdf.FileDataError)):
                    persist_verified_pdf(raw, meta, case(), plan(), temp, Path(temp, 'receipt.json'),
                                         tab_id='7', expected_userid='DEMO01')
                self.assertEqual(list(Path(temp).iterdir()), [])

    def test_existing_receipt_blocks_before_pdf_is_written(self):
        raw = pdf_bytes()
        with tempfile.TemporaryDirectory() as temp:
            receipt = Path(temp, 'receipt.json')
            receipt.write_text('old')
            with self.assertRaises(FileExistsError):
                persist_verified_pdf(raw, metadata(raw), case(), plan(), temp, receipt, tab_id='7', expected_userid='DEMO01')
            self.assertEqual(list(Path(temp).iterdir()), [receipt])
            self.assertEqual(receipt.read_text(), 'old')

    def test_atomic_publish_never_overwrites_a_racing_file(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp, 'same.pdf')
            actual_link = os.link
            def race(source, destination):
                Path(destination).write_bytes(b'other-task')
                return actual_link(source, destination)
            with patch('finance_pdf_export.os.link', side_effect=race), self.assertRaises(FileExistsError):
                _atomic_new(target, b'this-task')
            self.assertEqual(target.read_bytes(), b'other-task')
            self.assertEqual(list(Path(temp).iterdir()), [target])

    def test_runtime_is_explicitly_bound_and_silence_is_a_switch(self):
        for silent in (True, False):
            with tempfile.TemporaryDirectory() as temp:
                request = config() | {'silent': silent}
                def run(command, **kwargs):
                    self.assertEqual(command[command.index('--tab-id') + 1], '7')
                    self.assertEqual(command[command.index('--operation-kind') + 1], 'read')
                    self.assertEqual('--foreground' in command, not silent)
                    script = Path(command[2])
                    receipt = Path(command[command.index('--output') + 1])
                    receipt.write_text(json.dumps({'phase': 'script_completed', 'tab_id': '7',
                                                  'source_sha256': digest(script.read_bytes()), 'result': {'ok': True}}), encoding='utf-8')
                    return SimpleNamespace(returncode=0)
                with patch('finance_pdf_export.subprocess.run', side_effect=run):
                    value, _ = _runtime_stage('chunk', request | {'offset': 0, 'length': 1024}, Path(temp), 1)
                self.assertTrue(value['ok'])

    def test_failure_points_to_original_read_only_inspection(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp, 'original-run').resolve()
            with patch('finance_pdf_export._runtime_stage', side_effect=RuntimeError('synthetic transport failure')):
                result = export_pdf(case(), plan(), temp, output, '7', 'DEMO01', execute=True)
            self.assertEqual(result['status'], 'blocked')
            self.assertIsNone(result['executed'])
            self.assertTrue(result['read_attempted'])
            self.assertTrue(result['next_action']['read_only'])
            self.assertEqual(result['next_action']['output_directory'], str(output))
            self.assertEqual(result['next_action']['cli']['args'], ['--inspect-output', str(output)])
            with patch('finance_pdf_export._runtime_stage') as runtime:
                inspected = inspect_export(output)
                runtime.assert_not_called()
            self.assertEqual(inspected['status'], 'blocked')
            self.assertFalse(inspected['pdf_exists'])
            with self.assertRaises(FileExistsError):
                export_pdf(case(), plan(), temp, output, '7', 'DEMO01', execute=True)

    def test_full_synthetic_pipeline_validates_every_chunk_receipt_then_publishes(self):
        raw, calls = pdf_bytes(), []
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp, 'synthetic-pipeline')
            def stage(action, request, directory, index):
                calls.append(action)
                value = metadata(raw) | {'session_token': request['session_token'], 'action': 'pdf_' + action}
                if action == 'chunk':
                    part = raw[request['offset']:request['offset'] + request['length']]
                    value.update(offset=request['offset'], chunk_size=len(part), chunk_sha256=digest(part),
                                 base64=base64.b64encode(part).decode())
                if action == 'finish':
                    value['reverified'] = True
                script, receipt = directory / f'{index:05d}_{action}.js', directory / f'{index:05d}_{action}.json'
                script.write_bytes(pdf_script(action, request).encode('utf-8'))
                receipt.write_text(json.dumps({'phase': 'script_completed', 'tab_id': '7', 'script': str(script),
                                               'source_sha256': digest(script.read_bytes()), 'result': value}), encoding='utf-8')
                return value, {'path': str(receipt), 'sha256': digest(receipt.read_bytes())}
            with patch('finance_pdf_export._runtime_stage', side_effect=stage):
                result = export_pdf(case(), plan(), temp, output, '7', 'DEMO01', execute=True, chunk_size=1024)
            self.assertEqual(result['status'], 'ready', result)
            self.assertEqual(result['kind'], 'synthetic_test_pdf_not_finance')
            self.assertGreaterEqual(calls.count('chunk'), 2)
            self.assertEqual(Path(result['pdf']).read_bytes(), raw)
            self.assertEqual(inspect_export(output)['status'], 'ready')
            Path(result['pdf']).write_bytes(b'changed')
            self.assertEqual(inspect_export(output)['status'], 'blocked')

    def test_unregistered_unknown_stages_are_inspected_only_under_frozen_binding(self):
        with tempfile.TemporaryDirectory() as temp:
            output, intent, binding = self.incomplete_fixture(temp)
            stage, _ = self.unknown_stage_fixture(output, intent['request'])
            with patch('finance_pdf_export._runtime_stage') as runtime:
                result = inspect_export(output, binding)
            runtime.assert_not_called()
            self.assertEqual(result['status'], 'blocked')
            self.assertTrue(result['binding_verified'])
            self.assertEqual(result['source_job_status'], 'unknown')
            self.assertEqual(result['stages'][0]['marker'], 'synthetic-original-marker')
            self.assertEqual(result['stages'][0]['sha256'], digest(stage.read_bytes()))
            self.assertFalse(result['approval_verified'])

    def test_each_frozen_binding_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            output, _, binding = self.incomplete_fixture(temp)
            replacements = {'case_id': 'different-case', 'serial': '99999', 'tab_id': '8',
                            'expected_userid': 'OTHER', 'plan_sha256': '0' * 64,
                            'destination': str(Path(temp, 'foreign.pdf'))}
            for field, value in replacements.items():
                with self.subTest(field=field), self.assertRaisesRegex(ValueError, '冻结绑定'):
                    inspect_export(output, binding | {field: value})
            with self.assertRaisesRegex(ValueError, '六个'):
                inspect_export(output, {'case_id': case()['case_id']})

    def test_unknown_with_verified_pdf_does_not_upgrade_job_status(self):
        raw = pdf_bytes()
        with tempfile.TemporaryDirectory() as temp:
            output, intent, binding = self.incomplete_fixture(temp)
            meta = metadata(raw) | {'session_token': intent['request']['session_token']}
            persist_verified_pdf(raw, meta, case(), plan(), temp, output / 'export_receipt.json',
                                 tab_id='7', expected_userid='DEMO01')
            result = inspect_export(output, binding)
            self.assertTrue(result['artifact_readback_verified'])
            self.assertEqual(result['status'], 'blocked')
            self.assertFalse(result['ok'])
            self.assertEqual(result['source_job_status'], 'unknown')
            self.assertEqual(result['needed_inputs'][0]['code'], 'ORIGINAL_JOB_UNRESOLVED')
            self.assertEqual(inspect_export(output)['status'], 'ready')

    def test_receipt_destination_redirect_is_rejected_before_any_pdf_read(self):
        raw = pdf_bytes()
        with tempfile.TemporaryDirectory() as temp:
            output, intent, binding = self.incomplete_fixture(temp)
            receipt = output / 'export_receipt.json'
            persist_verified_pdf(raw, metadata(raw) | {'session_token': intent['request']['session_token']},
                                 case(), plan(), temp, receipt, tab_id='7', expected_userid='DEMO01')
            record = json.loads(receipt.read_text(encoding='utf-8'))
            record['pdf'] = str(Path(temp, 'unrelated.pdf'))
            Path(record['pdf']).write_bytes(b'private synthetic sentinel')
            receipt.write_text(json.dumps(record), encoding='utf-8')
            with patch('finance_pdf_export._read_snapshot', wraps=_read_snapshot) as reader:
                with self.assertRaisesRegex(ValueError, '目的地'):
                    inspect_export(output, binding)
            self.assertFalse(any(str(call.args[1]).endswith('.pdf') for call in reader.call_args_list))

    def test_fixed_intent_and_stage_directory_links_are_rejected(self):
        for target in ('intent.json', 'stages'):
            with tempfile.TemporaryDirectory() as temp, self.subTest(target=target):
                output, _, binding = self.incomplete_fixture(temp)
                path = output / target
                outside = Path(temp, 'outside-' + target)
                if path.is_dir():
                    outside.mkdir()
                    path.rmdir()
                else:
                    outside.write_bytes(path.read_bytes())
                    path.unlink()
                try:
                    path.symlink_to(outside, target_is_directory=outside.is_dir())
                except OSError:
                    self.skipTest('当前系统未允许创建测试符号链接')
                with self.assertRaisesRegex(ValueError, '链接|重解析'):
                    inspect_export(output, binding)

    def test_stage_script_escape_and_foreign_identity_are_rejected(self):
        for change in ({'script': '/outside/secret.js'}, {'tab_id': '8'}, {'result': {'case_id': 'other-case'}}):
            with tempfile.TemporaryDirectory() as temp, self.subTest(change=change):
                output, intent, binding = self.incomplete_fixture(temp)
                receipt, record = self.unknown_stage_fixture(output, intent['request'])
                record.update(change)
                receipt.write_text(json.dumps(record), encoding='utf-8')
                with self.assertRaisesRegex(ValueError, '路径|标签|原任务'):
                    inspect_export(output, binding)

    def test_json_size_limits_apply_before_parsing(self):
        for relative, limit in [('intent.json', MAX_INTENT_JSON), ('stages/00000_begin.json', MAX_STAGE_JSON)]:
            with tempfile.TemporaryDirectory() as temp, self.subTest(relative=relative):
                output, _, binding = self.incomplete_fixture(temp)
                (output / relative).write_bytes(b' ' * (limit + 1))
                with self.assertRaisesRegex(ValueError, '有界普通文件'):
                    inspect_export(output, binding)

    def test_parse_and_returned_sha_use_one_frozen_read(self):
        with tempfile.TemporaryDirectory() as temp:
            output, _, binding = self.incomplete_fixture(temp)
            original = (output / 'intent.json').read_bytes()
            def replace_after_read(root, relative, limit):
                raw, snapshot = _read_snapshot(root, relative, limit)
                if relative == 'intent.json':
                    changed = json.loads(raw)
                    changed['concurrent_note'] = 'replaced after captured bytes'
                    (output / relative).write_text(json.dumps(changed), encoding='utf-8')
                return raw, snapshot
            with patch('finance_pdf_export._read_snapshot', side_effect=replace_after_read):
                result = inspect_export(output, binding)
            snapshot = next(x for x in result['evidence_snapshots'] if x['relative_path'] == 'intent.json')
            self.assertEqual(snapshot['sha256'], digest(original))
            self.assertNotEqual(snapshot['sha256'], digest((output / 'intent.json').read_bytes()))

    def test_fd_and_path_ctime_changes_remain_independent_stop_conditions(self):
        fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_mode', 'st_file_attributes')
        for interface in ('fd', 'path'):
            with tempfile.TemporaryDirectory() as temp, self.subTest(interface=interface):
                path = Path(temp, 'record.json')
                path.write_bytes(b'{}')
                if interface == 'fd':
                    original = os.fstat
                    target = 'finance_pdf_export.os.fstat'
                    mutate_on = 2
                else:
                    from finance_pdf_export import _not_reparse
                    original = _not_reparse
                    target = 'finance_pdf_export._not_reparse'
                    mutate_on = 3
                calls = 0
                def changed(*args):
                    nonlocal calls
                    calls += 1
                    value = original(*args)
                    if calls == mutate_on:
                        values = {field: getattr(value, field, 0) for field in fields}
                        values['st_ctime_ns'] += 1
                        return SimpleNamespace(**values)
                    return value
                with patch(target, side_effect=changed), self.assertRaisesRegex(ValueError, '变化'):
                    _read_snapshot(Path(temp), 'record.json', 100)


@unittest.skipUnless(shutil.which('node'), 'Node unavailable')
class PdfScriptTests(unittest.TestCase):
    def run_fixture(self, **options):
        request = config()
        if options.get('formal'):
            request.update(view_kind='formal_print', include_invoice_attachments=options.get('include', False), delivery_mode='rename_only')
        sources = [pdf_script('begin', request), pdf_script('chunk', request | {'offset': 0, 'length': 16}),
                   pdf_script('finish', request)]
        harness = r'''
const vm=require('node:vm'),{webcrypto}=require('node:crypto'),o=OPTIONS;
let userid=o.wrongUser?'OTHER':'DEMO01',serial=o.wrongMainSerial?'99999':'12345';
let bytes=new Uint8Array(Buffer.from('%PDF-1.7\nsynthetic bytes for isolated JS test'));
const pdf={numPages:2,fingerprints:['synthetic-fingerprint'],getData:async()=>bytes,
 getPage:async()=>({getTextContent:async()=>({items:[{str:'报销单号：'+(o.wrongPdfSerial?'99999':'12345')}]})})};
const viewer={location:{origin:o.crossOrigin?'https://other.invalid':'https://cwzh.ustc.edu.cn',pathname:'/viewer'},PDFViewerApplication:{pdfDocument:pdf}};
const outer={getClientRects:()=>o.hidden?[]:[1],contentDocument:{querySelector:()=>({contentWindow:viewer})}};
const $={getProjID:()=>o.wrongProject?'OTHER':'WF_YBX',UC:{bindData:k=>k==='#0-userinfo.userid#'?userid:k==='#61-serialno#'?serial:'preview'}};
const ctx=vm.createContext({$,document:{querySelector:()=>outer,querySelectorAll:()=>o.ambiguous?[outer,outer]:[outer]},location:{origin:'https://cwzh.ustc.edu.cn',pathname:'/YBX/main.jsp'},
 crypto:webcrypto,btoa:s=>Buffer.from(s,'binary').toString('base64'),Uint8Array,console});ctx.window=ctx;
if(o.formal){ctx.YBX_JSONDATA={'确认报销信息':{'入口':o.wrongEntry?'报销单列表':'PDF打印报销单列表'}};
 if(!o.missingIntent)ctx.__ustcSystemPrintIntentV1={kind:'formal_print',phase:o.notChosen?'print_open_requested':'print_requested',case_id:'synthetic-pdf',serial:'12345',userid:'DEMO01',include_invoice_attachments:o.wrongOption?!o.include:!!o.include};}
(async()=>{const results=[];let error=null;
 try{results.push(await vm.runInContext(SOURCES[0],ctx));
  if(o.changeOption)ctx.__ustcSystemPrintIntentV1.include_invoice_attachments=!ctx.__ustcSystemPrintIntentV1.include_invoice_attachments;
  if(o.changeUser)userid='OTHER';if(o.changeFingerprint)pdf.fingerprints[0]='changed';
  if(o.replaceDocument)viewer.PDFViewerApplication.pdfDocument={...pdf};
  results.push(await vm.runInContext(SOURCES[1],ctx));
  if(o.changeBytes)bytes=new Uint8Array(Buffer.from('%PDF-1.7\nchanged bytes in original document'));
  results.push(await vm.runInContext(SOURCES[2],ctx));
 }catch(e){error=String(e.message||e);}
 console.log(JSON.stringify({results,error,cachePresent:Object.hasOwn(ctx,'__ustcReimbursementPdfExportV1')}));
})();
'''.replace('OPTIONS', json.dumps(options)).replace('SOURCES', json.dumps(sources, ensure_ascii=True))
        result = subprocess.run(['node'], input=harness.encode('utf-8'), capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        return json.loads(result.stdout)

    def test_formal_print_requires_exact_native_intent_option_and_entry(self):
        for include in (False, True):
            result = self.run_fixture(formal=True, include=include)
            self.assertIsNone(result['error'], result)
            self.assertEqual(result['results'][0]['kind'], 'system_formal_print')
            self.assertTrue(result['results'][0]['final_print_verified'])
            self.assertEqual(result['results'][0]['include_invoice_attachments'], include)
        for key in ('missingIntent', 'notChosen', 'wrongEntry', 'ambiguous', 'wrongOption'):
            result = self.run_fixture(formal=True, include=False, **{key: True})
            self.assertIsNotNone(result['error'], key)
            self.assertEqual(result['results'], [])
        changed = self.run_fixture(formal=True, changeOption=True)
        self.assertIsNotNone(changed['error'])
        self.assertTrue(changed['cachePresent'])

    def test_begin_chunk_finish_keeps_same_identity_and_releases_own_cache(self):
        result = self.run_fixture()
        self.assertIsNone(result['error'])
        self.assertFalse(result['cachePresent'])
        self.assertEqual([x['action'] for x in result['results']], ['pdf_begin', 'pdf_chunk', 'pdf_finish'])
        begin, chunk, finish = result['results']
        self.assertEqual(begin['sha256'], finish['sha256'])
        self.assertEqual(len(base64.b64decode(chunk['base64'])), 16)
        self.assertTrue(finish['reverified'])

    def test_wrong_native_or_pdf_identity_stops_before_data_is_exported(self):
        for option in ['wrongUser', 'wrongMainSerial', 'wrongPdfSerial', 'crossOrigin', 'hidden', 'wrongProject']:
            with self.subTest(option=option):
                result = self.run_fixture(**{option: True})
                self.assertIsNotNone(result['error'])
                self.assertEqual(result['results'], [])

    def test_document_or_identity_changes_stop_without_losing_original_session(self):
        for option in ['changeUser', 'changeFingerprint', 'replaceDocument', 'changeBytes']:
            with self.subTest(option=option):
                result = self.run_fixture(**{option: True})
                self.assertIsNotNone(result['error'])
                self.assertTrue(result['cachePresent'])
                self.assertNotIn('pdf_finish', [x['action'] for x in result['results']])


if __name__ == '__main__':
    unittest.main()
