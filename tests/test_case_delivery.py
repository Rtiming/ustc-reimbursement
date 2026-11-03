"""交付命名、原任务复用、实际下载字节与财务状态分离。"""

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from assistant_api import AssistantService, atomic_json, read_json
from case_delivery import download


class CaseDeliveryTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        self.s = AssistantService(self.root, [self.root])
        self.s.new_case("delivery_fixture")
        self.case = "delivery_fixture"
        self.paths = self.s.resolve(self.case)[1]
        form = read_json(ROOT / "tests/fixtures/synthetic_form_config.json")
        form.update(case_id=self.case, expected_userid="TEST001")
        atomic_json(self.paths["form"], form)
        self.connection = {
            "connection_id": "a" * 32,
            "expected_userid": "TEST001",
            "tab_id": "7",
            "namespace": "synthetic",
        }
        self.jobs = {}
        self.sent = []
        self.verified = {}
        p = patch("case_delivery.scoped", return_value=(self.s, self.connection))
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(self.s, "enqueue", side_effect=self.enqueue)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(self.s, "result", side_effect=lambda c, j: self.jobs[j])
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(
            self.s, "pdf_export_inspect", side_effect=lambda c, j: self.verified[j]
        )
        p.start()
        self.addCleanup(p.stop)

    def enqueue(self, case, operation, params, request_id, execute=False):
        self.sent.append((operation, params, request_id, execute))
        job = "j_" + hashlib.sha256(request_id.encode()).hexdigest()[:24]
        self.jobs[job] = {"status": "queued"}
        self.verified[job] = {"ok": False, "artifact_readback_verified": False}
        return {"job_id": job, "status": "queued"}

    def plan(self, **values):
        return self.s.case_delivery(
            self.case,
            "plan",
            business_date="2026-10-30",
            sequence=2,
            confirmation_note="合成澄清表确认日期与第2项",
            **values,
        )

    def test_date_sequence_and_attachment_choice_are_explicit(self):
        with self.assertRaises(ValueError):
            self.s.case_delivery(
                self.case, "plan", sequence=1, confirmation_note="合成"
            )
        with self.assertRaisesRegex(ValueError, "附件"):
            self.plan(kind="formal")
        value = self.plan(kind="formal", include_invoice_attachments=False)
        self.assertIn("2026-10-30", value["destination"])
        self.assertIn("2_合成测试活动.pdf", value["destination"])
        self.assertEqual(value["plan"]["view_kind"], "formal_print_api")
        self.assertFalse(value["plan"]["include_invoice_attachments"])
        self.assertEqual(self.sent, [])
        self.assertFalse(Path(value["destination"]).exists())

    def test_repeated_confirm_returns_same_original_job(self):
        plan = self.plan()
        args = {"plan_id": plan["plan_id"], "execute": True}
        first = self.s.case_delivery(self.case, "export", **args)
        second = self.s.case_delivery(self.case, "export", **args)
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(len(self.sent), 1)
        op, p, _, execute = self.sent[0]
        self.assertEqual(op, "pdf_export")
        self.assertEqual(p["export_plan"]["view_kind"], "details")
        self.assertTrue(execute)
        folder = self.root / "native-preview"
        folder.mkdir()
        request = {
            "operation": op,
            "parameters": p,
            "case_id": self.case,
            "execute": False,
            "request_id": "native-plan",
        }
        command = self.s.command(request, folder)
        self.assertIn(str(ROOT / "tools/finance_pdf_export.py"), command)
        self.assertFalse(Path(plan["destination"]).exists())

    def test_changed_form_does_not_export_other_scope(self):
        plan = self.plan()
        form = read_json(self.paths["form"])
        form["amount"] = "1.00"
        atomic_json(self.paths["form"], form)
        with self.assertRaisesRegex(ValueError, "改变"):
            self.s.case_delivery(
                self.case, "export", plan_id=plan["plan_id"], execute=True
            )
        self.assertEqual(self.sent, [])

    def test_download_requires_verified_current_bytes_not_program_exit(self):
        plan = self.plan()
        value = self.s.case_delivery(
            self.case, "export", plan_id=plan["plan_id"], execute=True
        )
        job = value["job_id"]
        self.jobs[job] = {"status": "completed"}
        with self.assertRaisesRegex(ValueError, "独立核验"):
            download(self.s, self.case, plan["plan_id"])
        pdf = Path(plan["destination"])
        pdf.parent.mkdir(parents=True)
        raw = b"%PDF-1.4\nsynthetic only"
        pdf.write_bytes(raw)
        self.verified[job] = {
            "ok": True,
            "artifact_readback_verified": True,
            "pdf_sha256": hashlib.sha256(raw).hexdigest(),
        }
        name, actual = download(self.s, self.case, plan["plan_id"])
        self.assertEqual(actual, raw)
        self.assertTrue(name.endswith(".pdf"))
        current = self.s.case_delivery(self.case, "status", plan_id=plan["plan_id"])
        self.assertFalse(current["financial_approval_completed"])
        pdf.write_bytes(raw + b"changed")
        with self.assertRaisesRegex(ValueError, "内容已改变"):
            download(self.s, self.case, plan["plan_id"])


if __name__ == "__main__":
    unittest.main()
