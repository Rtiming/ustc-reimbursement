import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from assistant_api import APIError, atomic_json
from ocr_runtime import read_profile, status, probe_local, EXPECTED_MODELS


class OcrRuntimeTests(unittest.TestCase):
    def test_unconfigured_status_neither_imports_models_nor_executes_programs(self):
        with tempfile.TemporaryDirectory() as temp, patch('ocr_runtime.subprocess.run') as execute:
            result = status(Path(temp))
        self.assertFalse(result['configured'])
        self.assertFalse(result['ready_for_request'])
        execute.assert_not_called()

    def test_local_requires_explicit_enablement_and_small_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile = {
                'schema': 'ustc-ocr-provider-v1',
                'backend': 'local',
                'local_execution_enabled': False,
                'probe_sha256': 'a' * 64,
                'dpi': 250,
                'max_pages': 20,
                'wait_seconds': 180,
            }
            atomic_json(root / '.local/ocr-provider.json', profile)
            with self.assertRaises(APIError):
                read_profile(root)
            profile['local_execution_enabled'] = True
            profile['max_pages'] = 21
            atomic_json(root / '.local/ocr-provider.json', profile)
            with self.assertRaises(APIError):
                read_profile(root)

    def test_missing_or_changed_model_cannot_be_resigned_as_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for models in ([], [{'name': key, 'sha256': '0' * 64} for key in EXPECTED_MODELS]):
                process = SimpleNamespace(
                    returncode=0, stdout=json.dumps({'models': models}).encode()
                )
                with (
                    patch('ocr_runtime.subprocess.run', return_value=process),
                    self.assertRaisesRegex(APIError, '模型缺失或SHA变化'),
                ):
                    probe_local(root)


if __name__ == '__main__':
    unittest.main()
