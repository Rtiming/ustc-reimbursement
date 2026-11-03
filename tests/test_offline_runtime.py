"""离线依赖校验不得静默改用网络或接收未登记wheel。"""

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from material_review_model import atomic_json
from offline_runtime import verify, LOCKS


class OfflineRuntimeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.offline = self.root / "offline"
        self.files = []
        for name, raw in [
            ("python/python.exe", b"synthetic never executed"),
            ("wheels/example.whl", b"synthetic wheel bytes"),
        ]:
            path = self.offline / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            self.files.append(
                {
                    "path": name,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "size": len(raw),
                }
            )
        self.requirements = {}
        for name in LOCKS:
            path = self.root / name
            path.write_text("example==0.1\n", encoding="utf-8")
            self.requirements[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.manifest = {
            "schema": "ustc-offline-runtime-v1",
            "platform": "windows-x86_64",
            "python_version": "3.12.13",
            "requirements": self.requirements,
            "files": self.files,
        }
        self.write()

    def write(self):
        atomic_json(self.offline / "manifest.json", self.manifest)

    def test_complete_bundle_returns_local_only_paths(self):
        result = verify(self.root)
        self.assertFalse(result["network_required"])
        self.assertEqual(result["file_count"], 2)
        self.assertTrue(Path(result["python"]).is_relative_to(self.offline))

    def test_changed_missing_or_extra_package_blocks(self):
        path = self.offline / "wheels/example.whl"
        raw = path.read_bytes()
        path.write_bytes(b"altered")
        with self.assertRaisesRegex(ValueError, "已变化"):
            verify(self.root)
        path.write_bytes(raw)
        (path.parent / "extra.whl").write_bytes(b"unregistered")
        with self.assertRaisesRegex(ValueError, "未登记"):
            verify(self.root)

    def test_mismatched_locks_traversal_and_duplicate_rejected(self):
        self.manifest["files"].append(dict(self.files[0], path="../outside.exe"))
        self.write()
        with self.assertRaisesRegex(ValueError, "路径"):
            verify(self.root)
        self.manifest["files"].pop()
        self.manifest["files"].append(dict(self.files[0]))
        self.write()
        with self.assertRaisesRegex(ValueError, "重复"):
            verify(self.root)
        self.manifest["files"].pop()
        self.write()
        (self.root / LOCKS[0]).write_text("new==1\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "不匹配"):
            verify(self.root)


if __name__ == "__main__":
    unittest.main()
