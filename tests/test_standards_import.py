"""私人规则导入与公开源码隔离，校验原库快照和失败回退。"""

from pathlib import Path
import copy
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from assistant_api import AssistantService, atomic_json, read_json
from workspace_config import material_rules_path
import standards_import as owner


class StandardsImportTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        self.data = self.root / "private-data"
        self.data.mkdir()
        self.library = self.data / "规范库"
        self.s = AssistantService(self.root, [self.data])
        self.s.standards_init(
            str(self.library), "local_fixture", "合成专项", execute=True
        )
        atomic_json(
            self.root / ".local/installation.json",
            {
                "schema": "ustc-installation-v1",
                "tool_root": str(self.root),
                "material_root": str(self.data),
                "library_root": str(self.library),
                "profile_id": "local_fixture",
            },
        )
        self.profile = self.s.standards_read(profile_id="local_fixture")["profile"]
        self.profile["sources"] = [
            {"source_id": "test", "kind": "synthetic", "location": "合成明确确认记录"}
        ]
        self.profile["rules"] = [
            {
                "rule_id": "rule1",
                "revision": "0.1.0",
                "statement": "仅合成测试要求",
                "kind": "user_workflow",
                "status": "proposed",
                "scope": {"all": True},
                "source_refs": ["test"],
                "effect": {
                    "action": "required",
                    "target": "manual.review",
                    "value": True,
                },
            }
        ]
        self.rules = {
            "schema_version": "0.1",
            "rules": [
                {
                    "id": "invoice",
                    "role": "invoice",
                    "profiles": ["*"],
                    "basis": "synthetic",
                    "level": "required",
                    "instruction": "对照原件核对",
                    "when_all": {},
                    "source": "合成规则来源",
                }
            ],
        }
        self.bundle = {
            "schema": owner.SCHEMA,
            "profiles": [self.profile],
            "material_rules": self.rules,
            "provenance": {"source": "合成用户选择的私人规范包"},
        }

    def test_preview_then_import_preserves_public_template_and_is_idempotent(self):
        public = self.root / "data/material_rules.json"
        atomic_json(public, {"configuration_status": "needs_confirmation", "rules": []})
        before = public.read_bytes()
        preview = self.s.standards_import(self.bundle)
        self.assertFalse(preview["executed"])
        self.assertFalse((self.library / "material_rules.json").exists())
        saved = self.s.standards_import(self.bundle, True, preview["expected_snapshot"])
        self.assertTrue(saved["executed"])
        self.assertEqual(public.read_bytes(), before)
        self.assertEqual(
            material_rules_path(self.root), self.library / "material_rules.json"
        )
        self.assertEqual(
            self.s.standards_read(profile_id="local_fixture")["profile"], self.profile
        )
        second = self.s.standards_import(self.bundle)
        self.assertTrue(
            self.s.standards_import(self.bundle, True, second["expected_snapshot"])[
                "executed"
            ]
        )
        self.s.new_case("test_case")
        checked = self.s.check("test_case")
        self.assertFalse(
            any(e["field"] == "case.material_context" for e in checked["errors"])
        )
        self.assertTrue(
            any(
                r["role"] == "invoice" and r["issue"] == "missing"
                for r in checked["material_audit"]["checks"]
            )
        )

    def test_changed_bundle_and_nonempty_profile_are_not_overwritten(self):
        preview = self.s.standards_import(self.bundle)
        changed = copy.deepcopy(self.bundle)
        changed["profiles"][0]["title"] = "Changed"
        with self.assertRaisesRegex(ValueError, "已改变"):
            self.s.standards_import(changed, True, preview["expected_snapshot"])
        self.s.standards_import(self.bundle, True, preview["expected_snapshot"])
        with self.assertRaisesRegex(ValueError, "非空专项"):
            self.s.standards_import(changed)

    def test_interrupted_import_restores_only_its_own_changes(self):
        preview = self.s.standards_import(self.bundle)
        before = {
            p.relative_to(self.library): p.read_bytes()
            for p in self.library.rglob("*.json")
        }
        actual = owner.replace_bytes
        calls = 0

        def fail_second(path, payload):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("合成磁盘写入失败")
            return actual(path, payload)

        with (
            patch.object(owner, "replace_bytes", side_effect=fail_second),
            self.assertRaisesRegex(OSError, "合成磁盘"),
        ):
            self.s.standards_import(self.bundle, True, preview["expected_snapshot"])
        for path, raw in before.items():
            self.assertEqual((self.library / path).read_bytes(), raw)
        self.assertFalse((self.library / "material_rules.json").exists())

    def test_import_rejects_empty_rules_and_path_escape(self):
        empty = copy.deepcopy(self.bundle)
        empty["material_rules"]["rules"] = []
        with self.assertRaises(ValueError):
            self.s.standards_import(empty)
        bad = copy.deepcopy(self.bundle)
        bad["profiles"][0]["profile_id"] = "../escape"
        with self.assertRaises(ValueError):
            self.s.standards_import(bad)


if __name__ == "__main__":
    unittest.main()
