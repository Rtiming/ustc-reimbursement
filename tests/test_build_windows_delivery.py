"""完整离线包保留双重身份，只收已核来源，拒绝变化和额外wheel。"""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from build_distribution import encoded
import build_windows_delivery as owner
from offline_runtime import LOCKS


class WindowsDeliveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.archive = self.base / "source.zip"
        files = {"README.md": b"synthetic source", "data/distribution.json": encoded({"version": "1.0.0-beta.1"})}
        files.update({name: b"example==0.1\n" for name in LOCKS})
        rows = [{"path": name, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()} for name, raw in sorted(files.items())]
        self.source_manifest = {"version": "1.0.0-beta.1", "private_data_included": False,
                                "source_tree_sha256": hashlib.sha256(encoded(rows)).hexdigest(), "files": rows}
        with zipfile.ZipFile(self.archive, "w") as archive:
            for name, raw in files.items():
                archive.writestr("ustc-reimbursement/" + name, raw)
            archive.writestr("ustc-reimbursement/MANIFEST.json", encoded(self.source_manifest))
        self.digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.offline = self.base / "runtime"
        wheel = io.BytesIO()
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("example-0.1.dist-info/METADATA", "Name: example\nVersion: 0.1\nLicense-Expression: MIT\n")
            archive.writestr("example-0.1.dist-info/licenses/LICENSE", "synthetic public license")
        runtime_files = {"python/python.exe": b"synthetic never run", "python/LICENSE.txt": b"synthetic Python license", "wheels/example-0.1-py3-none-any.whl": wheel.getvalue()}
        runtime_rows = []
        for name, raw in runtime_files.items():
            path = self.offline / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            runtime_rows.append({"path": name, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        self.manifest = {"schema": "ustc-offline-runtime-v1", "platform": "windows-x86_64", "python_version": "3.12.13", "files": runtime_rows,
                         "requirements": {name: hashlib.sha256(files[name]).hexdigest() for name in LOCKS}, "upstream": {"packages": "https://pypi.org/simple"}}
        (self.offline / "manifest.json").write_bytes(encoded(self.manifest))
        p = patch.object(owner, "ROOT", self.base)
        p.start()
        self.addCleanup(p.stop)

    def build(self):
        return owner.build(self.archive, self.digest, self.offline, self.base / "delivery")

    def test_complete_archive_keeps_exact_source_runtime_and_license_provenance(self):
        (self.offline / "author-private.txt").write_text("must not be copied")
        result = self.build()
        self.assertFalse(result["private_data_included"])
        self.assertFalse(result["network_required_for_install"])
        self.assertEqual(result["source_archive_sha256"], self.digest)
        with zipfile.ZipFile(result["archive"]) as archive:
            self.assertIsNone(archive.testzip())
            names = archive.namelist()
            self.assertEqual(len(names), len(set(names)))
            self.assertEqual(archive.read("ustc-reimbursement/升级用源码包.zip"), self.archive.read_bytes())
            self.assertEqual(json.loads(archive.read("ustc-reimbursement/MANIFEST.json")), self.source_manifest)
            self.assertFalse(any("author-private" in name for name in names))
            licenses = json.loads(archive.read("ustc-reimbursement/离线依赖来源及许可.json"))
            self.assertEqual(licenses["wheels"][0]["license_expression"], "MIT")
            self.assertIn("python/LICENSE.txt", licenses["runtime_license_files"])
            self.assertIn("--with-ocr", archive.read("ustc-reimbursement/安装并启用本机OCR.cmd").decode())
            self.assertIn("-ExecutionPolicy Bypass", archive.read("ustc-reimbursement/安装报销工作台.cmd").decode())

    def test_wrong_source_or_runtime_change_creates_no_delivery(self):
        with self.assertRaisesRegex(ValueError, "SHA不同"):
            owner.build(self.archive, "0" * 64, self.offline, self.base / "delivery")
        (self.offline / "python/python.exe").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "已变化"):
            self.build()
        self.assertFalse((self.base / "delivery").exists())

    def test_extra_wheel_and_existing_output_are_rejected(self):
        extra = self.offline / "wheels/extra.whl"
        extra.write_bytes(b"unregistered")
        with self.assertRaisesRegex(ValueError, "未登记"):
            self.build()
        extra.unlink()
        output = self.base / "delivery"
        output.mkdir()
        (output / "keep").write_text("user file")
        with self.assertRaisesRegex(ValueError, "保留旧版本"):
            self.build()
        self.assertEqual((output / "keep").read_text(), "user file")


if __name__ == "__main__":
    unittest.main()
