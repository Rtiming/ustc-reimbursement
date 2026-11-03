"""真实归档输入接续材料、补件和51票规划；不连接财务。"""

import copy
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from assistant_api import AssistantService, atomic_json, read_json
from material_intake_service import configure_bundled_archive
from material_review_model import load
from case_materials import normalized, catalog


class CaseMaterialsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.materials = self.root / "private materials"
        self.materials.mkdir()
        self.s = AssistantService(self.root, [self.materials])
        self.case = "materials_fixture"
        self.s.new_case(self.case, title="合成活动")
        configure_bundled_archive(self.root)
        import fitz

        pdf = self.materials / "合成51票与明白纸付款行程.pdf"
        with fitz.open() as doc:
            for i in range(54):
                page = doc.new_page()
                page.insert_text((40, 50), "Synthetic " + str(99000000000000000000 + i))
            doc.save(pdf)
        result = self.s.material_intake(
            self.case,
            {
                "schema": "reimbursement.material-intake.v1",
                "case_id": self.case,
                "sources": [{"id": "test", "path": str(pdf), "group_strip_dirs": None}],
                "output_dir": str(self.materials / "received"),
            },
            "materials-intake-fixture",
            True,
        )
        deadline = time.monotonic() + 30
        while result["status"] in {"queued", "running"} and time.monotonic() < deadline:
            time.sleep(0.1)
            result = self.s.result(self.case, result["job_id"])
        self.assertEqual(result["status"], "completed", result)
        self.initial = self.s.case_materials(self.case)
        self.key = self.initial["materials"][0]["key"]

    def source(self, page=1):
        return {"key": self.key, "page": page}

    def save(self, action, values):
        return self.s.case_materials(
            self.case,
            action,
            values,
            self.s.revision(self.case),
            "合成测试逐页对照",
            True,
        )

    def document(self):
        return {
            "folder_name": "合成活动原文件夹",
            "source": self.source(52),
            "business_date": "2026-11-03",
            "first_sequence": 2,
            "responsible_person": {"name": "合成负责人", "phone": "synthetic-phone"},
        }

    def expense(self, i=0):
        return {
            "expense_id": "expense_" + str(i),
            "description": "合成费用" + str(i),
            "invoices": [
                {
                    "source": self.source(i + 1),
                    "number": str(99000000000000000000 + i),
                    "face_amount": "10.00",
                }
            ],
            "amount_evidence": [
                {"source": self.source(52), "kind": "request", "amount": "10.00"}
            ],
            "payees": [
                {
                    "source": self.source(52),
                    "payee_id": "SYN01",
                    "name": "合成收款人",
                    "mode": "bank_transfer",
                    "amount": "10.00",
                }
            ],
            "payment_materials": [self.source(53)],
            "itinerary_materials": [self.source(54)],
        }

    def rules(self):
        library = self.materials / "rules"
        self.s.standards_init(str(library), "fixture", "合成规则", execute=True)
        profile = self.s.standards_read(str(library), "fixture")["profile"]
        profile["sources"] = [
            {
                "source_id": "synthetic",
                "kind": "user_instruction",
                "location": "合成确认",
                "date": "2026-11-03",
            }
        ]
        effects = {
            "batch.max_invoices_per_form": 50,
            "batch.split_strategy": "minimal_balanced",
            "amount.strategy": "minimum_comparable",
            "summary.contact_priority": ["responsible_person", "form_filler"],
            "finance.required_roles": ["witness", "final_funding_approver"],
            "finance.prohibited_roles": ["pre_reviewer"],
            "summary.source": "folder_name",
            "summary.contact_location": "system_remarks",
            "payment.source": "clarification_sheet_and_case_evidence",
            "batch.summary_suffix": "chinese_parentheses",
            "export.directory_source": "clarification_business_date",
            "export.filename": "sequence_summary",
            "unknown.policy": "hold_and_ask",
        }
        for i, (target, value) in enumerate(effects.items()):
            profile["rules"].append(
                {
                    "rule_id": "rule" + str(i),
                    "revision": "0.1.0",
                    "statement": "合成明确规则",
                    "kind": "user_workflow",
                    "status": "confirmed",
                    "scope": {"all": True},
                    "source_refs": ["synthetic"],
                    "confirmation": {
                        "by": "user",
                        "at": "2026-11-03",
                        "source_refs": ["synthetic"],
                    },
                    "effect": {"action": "required", "target": target, "value": value},
                }
            )
        registry = read_json(library / "registry.json")
        path = library / registry["profiles"][0]["path"]
        # Locate the requested profile through the official registry, not another template.
        for entry in registry["profiles"]:
            if entry["profile_id"] == "fixture":
                path = library / entry["path"]
        atomic_json(path, profile)
        context = {
            "case_id": self.case,
            "selected_profile_ids": ["fixture"],
            "selection_basis": "合成确认",
        }
        self.s.update_case(
            self.case,
            "case",
            {
                "material_context": {
                    "standards_selection": {
                        "library_root": str(library),
                        "profile_id": "fixture",
                        "context": context,
                        "as_of": "2026-11-03",
                    }
                }
            },
            self.s.revision(self.case),
        )

    def test_51_invoices_use_saved_rules_and_real_sources_and_balance_26_25(self):
        self.rules()
        self.save("save_document", self.document())
        self.save("save_expenses", [self.expense(i) for i in range(51)])
        result = self.s.case_materials(self.case, "plan")
        self.assertEqual(
            result["plan"]["structure_readiness"]["status"],
            "ready",
            result.get("needed_inputs"),
        )
        self.assertEqual(
            result["plan"]["amount_readiness"]["status"],
            "ready",
            result.get("needed_inputs"),
        )
        forms = result["plan"]["documents"][0]["forms"]
        self.assertEqual(sorted(x["invoice_count"] for x in forms), [25, 26])
        self.assertEqual(sum(float(x["claim_amount"]) for x in forms), 510)
        self.assertEqual(forms[0]["summary"], "合成活动原文件夹（一）")
        self.assertIn("负责人", forms[0]["remarks"])
        self.assertEqual(result["plan"]["finance_readiness"]["status"], "blocked")
        self.assertFalse(result["system_write"])
        self.assertFalse(result["plan"]["finance_write_authorized"])

    def test_invalid_revision_cross_case_duplicate_and_changed_source_rejected(self):
        self.save("save_expenses", [self.expense()])
        with self.assertRaisesRegex(ValueError, "已改变"):
            self.s.case_materials(
                self.case,
                "save_expenses",
                [self.expense(1)],
                self.initial["revision"],
                "合成核对",
                True,
            )
        duplicate = copy.deepcopy(self.expense())
        duplicate["expense_id"] = "another"
        with self.assertRaisesRegex(ValueError, "重复申请"):
            self.save("save_expenses", [duplicate])
        wrong = self.expense(1)
        wrong["invoices"][0]["source"]["key"] = "other/mat-invalid"
        with self.assertRaisesRegex(ValueError, "不属于"):
            self.save("save_expenses", [wrong])
        wrong = self.expense(1)
        wrong["invoices"][0]["source"]["page"] = 999
        with self.assertRaisesRegex(ValueError, "页码"):
            self.save("save_expenses", [wrong])
        sources = catalog(self.s, self.case)
        data = {"expenses": [self.expense()]}
        from intake_review import bound_file

        path = bound_file(
            self.s, self.case, sources[0]["job_id"], sources[0]["material_id"]
        )[0]
        path.write_bytes(b"changed synthetic source")
        with self.assertRaises(ValueError):
            normalized(self.s, self.case, data, sources)

    def test_missing_payee_does_not_prevent_other_expense_and_workbench_keeps_events(
        self,
    ):
        self.rules()
        self.save("save_document", self.document())
        missing = self.expense()
        missing["payees"] = []
        self.save("save_expenses", [missing, self.expense(1)])
        result = self.s.case_materials(self.case, "plan")
        expenses = result["plan"]["documents"][0]["expenses"]
        self.assertEqual([x["status"] for x in expenses], ["blocked", "ready"])
        self.save(
            "save_issue",
            {
                "issue_id": "missing_payee",
                "title": "收款人待核",
                "request": "请确认实际收款人",
                "owner": "合成材料提供人",
                "materials": [self.source(52)],
            },
        )
        with patch(
            "material_review_launch.launch",
            return_value={"ok": True, "url": "http://127.0.0.1:12345"},
        ):
            result = self.s.case_materials(self.case, "review_open", execute=True)
            workspace = Path(result["workspace"])
            snapshot, version = load(workspace)
            self.assertEqual(snapshot["counts"]["expenses"], 2)
            self.assertEqual(snapshot["counts"]["issues"], 1)
            self.assertTrue(
                any(r["page"] == 53 for r in snapshot["expenses"]["expense_0"]["refs"])
            )
            sentinel = workspace / "events" / "sentinel.txt"
            sentinel.parent.mkdir()
            sentinel.write_text("preserved", encoding="utf-8")
            self.s.case_materials(self.case, "review_open", execute=True)
            self.assertEqual(load(workspace)[1], version)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserved")

    def test_preview_no_write_and_expense_pagination(self):
        before = self.s.revision(self.case)
        result = self.s.case_materials(
            self.case, "save_expenses", [self.expense()], before, "合成", False
        )
        self.assertTrue(result["preview"])
        self.assertEqual(self.s.revision(self.case), before)
        self.save("save_expenses", [self.expense(i) for i in range(3)])
        first = self.s.case_materials(self.case, limit=2)
        self.assertEqual(len(first["expenses"]), 2)
        next_page = self.s.case_materials(**first["next_page_action"]["arguments"])
        self.assertEqual(len(next_page["expenses"]), 1)
        self.assertIsNone(next_page["next_page_action"])


if __name__ == "__main__":
    unittest.main()
