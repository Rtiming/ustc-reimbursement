"""原目录源码升级/回退不会迁移案件，未知财务任务阻止替换。"""

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "tests")]
from assistant_api import AssistantService, atomic_json, read_json
from release_update import extract_verified
from test_release_update import package
import release_maintenance as owner


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.base = Path(t.name)
        old = self.base / "old.zip"
        old.write_bytes(package("1.0.0-beta.1"))
        self.root = extract_verified(old, self.base / "installed", "1.0.0-beta.1")
        self.archive = self.base / "new.zip"
        self.archive.write_bytes(package("2.0.0-beta.1"))
        self.digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.material = self.base / "private-data"
        self.material.mkdir()
        atomic_json(
            self.root / ".local/installation.json",
            {
                "schema": "ustc-installation-v1",
                "tool_root": str(self.root),
                "material_root": str(self.material),
                "library_root": str(self.material / "规范库"),
            },
        )
        self.s = AssistantService(self.root, [self.material])
        self.s.new_case("keep_case")
        self.originals = {
            p.relative_to(self.root): p.read_bytes()
            for p in (self.root / "cases").rglob("*")
            if p.is_file()
        }
        p = patch.object(owner, "install_check", return_value={"ok": True})
        p.start()
        self.addCleanup(p.stop)

    def upgrade(self, execute=True):
        return owner.run(
            self.root, archive=self.archive, archive_sha256=self.digest, execute=execute
        )

    def test_upgrade_and_rollback_keep_case_paths_and_bytes(self):
        preview = self.upgrade(False)
        self.assertFalse(preview["executed"])
        self.assertEqual(
            read_json(self.root / "data/distribution.json")["version"], "1.0.0-beta.1"
        )
        result = self.upgrade()
        self.assertTrue(result["ok"])
        self.assertEqual(
            read_json(self.root / "data/distribution.json")["version"], "2.0.0-beta.1"
        )
        reverted = owner.run(self.root, rollback_id=result["rollback_id"], execute=True)
        self.assertTrue(reverted["ok"])
        self.assertEqual(
            read_json(self.root / "data/distribution.json")["version"], "1.0.0-beta.1"
        )
        for path, raw in self.originals.items():
            self.assertEqual((self.root / path).read_bytes(), raw)
        self.assertFalse((self.root / ".local/maintenance-active.json").exists())

    def test_unknown_financial_result_blocks_upgrade(self):
        pending = self.s.resolve("keep_case")[1]["state"].parent / "pending_write.json"
        atomic_json(pending, {"operation": "synthetic-unknown"})
        with self.assertRaisesRegex(ValueError, "原操作待核"):
            self.upgrade()
        self.assertEqual(
            read_json(self.root / "data/distribution.json")["version"], "1.0.0-beta.1"
        )
        self.assertTrue(pending.exists())

    def test_failed_install_restores_and_checks_original_source(self):
        with patch.object(
            owner,
            "install_check",
            side_effect=[ValueError("合成安装失败"), {"ok": True}],
        ):
            result = self.upgrade()
        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "restored_and_checked")
        self.assertEqual(
            read_json(self.root / "data/distribution.json")["version"], "1.0.0-beta.1"
        )

    def test_partial_source_can_rollback_but_manual_edit_is_preserved(self):
        result = self.upgrade()
        backup = self.root / ".local/upgrades" / result["rollback_id"] / "before"
        (self.root / "data/distribution.json").write_bytes(
            (backup / "data/distribution.json").read_bytes()
        )
        reverted = owner.run(self.root, rollback_id=result["rollback_id"], execute=True)
        self.assertTrue(reverted["ok"])
        second = self.upgrade()
        (self.root / "README.md").write_text("user changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "不能覆盖"):
            owner.run(self.root, rollback_id=second["rollback_id"], execute=True)
        self.assertEqual((self.root / "README.md").read_text(), "user changed")

    def test_maintenance_marker_blocks_new_financial_jobs(self):
        atomic_json(
            self.root / ".local/maintenance-active.json", {"operation": "upgrade"}
        )
        with self.assertRaisesRegex(ValueError, "正在升级"):
            self.s.enqueue("keep_case", "scan", {}, "new-test")


if __name__ == "__main__":
    unittest.main()
