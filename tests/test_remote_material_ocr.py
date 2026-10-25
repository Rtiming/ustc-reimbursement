import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / 'tools' / 'remote_material_ocr.py'
spec = importlib.util.spec_from_file_location('remote_material_ocr', MODULE)
ocr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ocr)


class RemoteMaterialOcrTests(unittest.TestCase):
    def test_shared_kernel_finishes_receipt_and_returns_the_requested_item(self):
        class Page:
            width = 100
            height = 50

            def copy(self):
                return Page()

            def save(self, path):
                Path(path).write_bytes(b'synthetic-rendered-image')

            def close(self):
                pass

        class Array:
            def __init__(self, value):
                self.value = value

            def tolist(self):
                return self.value

        engine = lambda value: ([[[[0, 0], [20, 0], [20, 10], [0, 10]], 'SYNTHETIC', 0.9]], [0.01])
        modules = {
            'cv2': SimpleNamespace(
                setNumThreads=lambda count: None,
                COLOR_RGB2BGR=1,
                cvtColor=lambda image, kind: image,
            ),
            'numpy': SimpleNamespace(asarray=lambda value: Array(value)),
            'rapidocr_onnxruntime': SimpleNamespace(RapidOCR=lambda **kwargs: engine),
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source.png'
            source.write_bytes(b'synthetic-original')
            item = {
                'id': 'requested-item',
                'sha256': ocr.sha256(source),
                'pages': [1],
                'rotate': [0],
            }
            with (
                patch.dict('sys.modules', modules),
                patch.object(ocr, 'render_page', return_value=Page()),
                patch.object(ocr, 'environment_probe', return_value={'synthetic': True}),
            ):
                result = ocr.recognize(
                    {'dpi': 250},
                    item,
                    source,
                    root / 'output',
                    'synthetic-run',
                    1,
                    manifest_sha256='a' * 64,
                    execution_site='remote',
                )
            self.assertEqual(result['item'], 'requested-item')
            receipt = json.loads((root / 'output/result.json').read_text())
            self.assertEqual(receipt['pages'][0]['text'], 'SYNTHETIC')
            self.assertEqual(receipt['status'], 'machine_read_only')

    def test_prepare_declares_only_requested_sources_and_deduplicates_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / '票据.pdf'
            pdf.write_bytes(b'fixture-pdf-content')
            request = root / 'request.json'
            request.write_text(
                json.dumps(
                    [
                        {'id': 'page1', 'path': str(pdf), 'pages': [1]},
                        {'id': 'page2', 'path': str(pdf), 'pages': [2]},
                    ]
                )
            )
            project = root / 'task'
            prepared = ocr.prepare(request, project, '/data/user/venv/bin/python', 'master', 250, 3)
            contract = json.loads((project / 'rtime-compute.json').read_text())
            self.assertEqual(prepared['distinct_files'], 1)
            self.assertEqual(len(contract['inputs']), 2)
            self.assertEqual(contract['resources']['array']['items'], ['page1', 'page2'])
            self.assertEqual(contract['allowed_targets'], ['master'])
            self.assertEqual(contract['command']['argv'][0], '/data/user/venv/bin/python')
            self.assertEqual(prepared['worker_sha256'], ocr.sha256(MODULE))
            self.assertEqual((project / 'worker.py').read_bytes(), MODULE.read_bytes())
            with self.assertRaisesRegex(ValueError, '必须为空'):
                ocr.prepare(request, project, '/python', 'master', 250, 3)

    def test_server_python_uses_posix_rules_without_consulting_host_paths(self):
        with patch.object(
            ocr, 'Path', side_effect=AssertionError('remote path must not use host Path')
        ):
            for path in ('/data/user/venv/bin/python', '/opt/环境 with spaces/bin/python'):
                self.assertEqual(ocr.server_python_path(path), path)
            for path in (
                None,
                123,
                '',
                'python',
                'venv/bin/python',
                'C:/Python/python.exe',
                r'C:\Python\python.exe',
                r'\\server\share\python.exe',
                '//server/share/python',
                '/opt/../bin/python',
                '/opt/\x00python',
                '/',
            ):
                with (
                    self.subTest(path=path),
                    self.assertRaisesRegex(ValueError, 'POSIX绝对文件路径'),
                ):
                    ocr.server_python_path(path)

    def test_invalid_server_path_stops_before_material_reads_or_task_creation(self):
        with (
            patch.object(ocr, 'normalize_requests') as normalize,
            patch.object(Path, 'read_text') as read,
            patch.object(Path, 'mkdir') as mkdir,
        ):
            with self.assertRaisesRegex(ValueError, 'POSIX绝对文件路径'):
                ocr.prepare(
                    Path('missing.json'), Path('unused'), 'C:/Python/python.exe', 'master', 250, 3
                )
            normalize.assert_not_called()
            read.assert_not_called()
            mkdir.assert_not_called()

    def test_invalid_pages_ids_and_rotations_fail_before_computation(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / 'input.pdf'
            pdf.write_bytes(b'stub')
            for item in [
                {'id': '../escape', 'path': str(pdf)},
                {'id': 'ok', 'path': str(pdf), 'pages': [0]},
                {'id': 'ok', 'path': str(pdf), 'pages': [True]},
                {'id': 'ok', 'path': str(pdf), 'pages': [1, 1]},
                {'id': 'ok', 'path': str(pdf), 'rotate': [45]},
            ]:
                with self.subTest(item=item), self.assertRaises(ValueError):
                    ocr.normalize_requests([item])
            with self.assertRaises(ValueError):
                ocr.normalize_requests(
                    [{'id': 'same', 'path': str(pdf)}, {'id': 'same', 'path': str(pdf)}]
                )

    def test_source_sha_mismatch_and_path_escape_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = root / 'inputs'
            inputs.mkdir()
            pdf = inputs / 'input.pdf'
            pdf.write_bytes(b'original')
            row = {'mount_path': 'input.pdf', 'sha256': ocr.sha256(pdf)}
            self.assertEqual(ocr.safe_material_path(inputs, row), pdf.resolve())
            pdf.write_bytes(b'modified')
            with self.assertRaisesRegex(ValueError, 'SHA'):
                ocr.safe_material_path(inputs, row)
            outside = root / 'other.pdf'
            outside.write_bytes(b'outside')
            with self.assertRaisesRegex(ValueError, '输入边界'):
                ocr.safe_material_path(
                    inputs, {'mount_path': '../other.pdf', 'sha256': ocr.sha256(outside)}
                )

    def test_no_accidental_local_work(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            self.assertRaisesRegex(ValueError, '服务器rtime compute'),
        ):
            ocr.work(Path('missing'), Path('missing'), 'task', Path('missing'))

    def test_managed_cas_symlink_keeps_declared_filename_and_checks_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            inputs = root / 'inputs'
            inputs.mkdir()
            cas = root / 'cas-object'
            cas.write_bytes(b'known-input-bytes')
            material = inputs / 'material.pdf'
            material.symlink_to(cas)
            row = {'mount_path': 'material.pdf', 'sha256': ocr.sha256(cas)}
            self.assertEqual(ocr.safe_material_path(inputs, row), material)
            self.assertEqual(ocr.safe_material_path(inputs, row).suffix, '.pdf')
            cas.write_bytes(b'changed-cas')
            with self.assertRaisesRegex(ValueError, 'SHA'):
                ocr.safe_material_path(inputs, row)


if __name__ == '__main__':
    unittest.main()
