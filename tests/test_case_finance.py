"""工作台编排的来源、权限和续办测试；学校写入owner有各自生命周期测试。"""

from pathlib import Path
import copy
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from assistant_api import AssistantService, atomic_json, read_json
from case_finance import location
from finance_native_stage import stage_contract


class CaseFinanceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.service = AssistantService(temp.name, [temp.name])
        self.case = "fixture"
        self.service.new_case(self.case)
        self.paths = self.service.resolve(self.case)[1]
        form = read_json(ROOT / "tests/fixtures/synthetic_form_config.json")
        form.update(
            case_id=self.case,
            expected_userid="TEST001",
            payments=[
                {
                    "mode": "bank_transfer",
                    "name": "合成甲",
                    "person_id": "TEST001",
                    "amount": "1000.00",
                }
            ],
        )
        form["basic"]["fields"].update(
            {"t-is_have_changefee": "N", "t-td_point": "退单地点"}
        )
        atomic_json(self.paths["form"], form)
        self.connection = {
            "connection_id": "a" * 32,
            "expected_userid": "TEST001",
            "tab_id": "7",
            "namespace": "test-only",
            "verified_at": "2026-01-01T00:00:00Z",
        }
        for name in ["case_finance.scoped", "case_fill.scoped"]:
            m = patch(name, return_value=(self.service, self.connection))
            m.start()
            self.addCleanup(m.stop)
        self.sent = []
        self.jobs = {}
        self.fail_save = False
        self.change_stage = "basic"
        m = patch.object(self.service, "enqueue", side_effect=self.enqueue)
        m.start()
        self.addCleanup(m.stop)
        m = patch.object(
            self.service,
            "result",
            side_effect=lambda case, job: self.jobs[job]["result"],
        )
        m.start()
        self.addCleanup(m.stop)
        m = patch("case_fill.output", side_effect=self.output)
        m.start()
        self.addCleanup(m.stop)

    def enqueue(self, case, op, params, request_id, execute=False):
        if request_id in self.jobs:
            return {"job_id": request_id, "status": "completed"}
        self.sent.append((op, copy.deepcopy(params), execute, request_id))
        body = {"ok": True}
        status = "completed"
        code = 0
        if op == "business" and params["action"].endswith("-prepare"):
            stage = params["action"][:-8]
            body = {
                "ok": True,
                "complete": True,
                "case_id": self.case,
                "serial": "123456789",
                "expected_userid": "TEST001",
                "action": stage + "-prepare",
                "business_transport": "native_http",
                "save_dispatched": False,
                "definition_sha256": stage_contract(stage)["definition_sha256"],
                "bill": {"t.status": "-99", "t.lc_finish": "F"},
                "original": {"existing": "same"},
                "changes": {"value": "new"} if stage == self.change_stage else {},
            }
        if op == "stage":
            if self.fail_save:
                status = "unknown"
                code = None
            atomic_json(
                self.paths["state"].parent / "pending_write.json",
                {"operation": "step:" + params["stage"]},
            )
        if op == "record_result" and params["kind"] == "ack-write":
            self.assertEqual(params["kind"], "ack-write")
            self.assertEqual(params["outcome"], "saved")
            pending = self.paths["state"].parent / "pending_write.json"
            self.assertTrue(pending.exists())
            pending.unlink()
            state = read_json(self.paths["state"])
            state.setdefault("operator_verified_stages", []).append(self.change_stage)
            atomic_json(self.paths["state"], state)
        self.jobs[request_id] = {
            "body": body,
            "result": {"status": status, "exit_code": code},
            "request": {"operation": op, "parameters": params},
        }
        return {"job_id": request_id, "status": status}

    def output(self, service, case, job, operation=None, action=None):
        data = self.jobs[job]
        if data["result"]["status"] != "completed":
            raise ValueError("原保存结果未知")
        return (
            {"job_id": job, "artifact_id": "real-ref", "sha256": "0" * 64},
            data["body"],
            data["request"],
        )

    def plan(self):
        return self.service.case_finance(self.case, "fill_plan")

    def advance(self, plan):
        return self.service.case_finance(
            self.case,
            "fill_continue",
            plan_id=plan["plan_id"],
            authorization_note="本次合成测试明确范围",
            execute=True,
        )

    def test_fill_reuses_form_and_native_jobs_then_independent_ack(self):
        plan = self.plan()
        with self.assertRaisesRegex(ValueError, "首次填写"):
            self.service.case_finance(
                self.case, "fill_continue", plan_id=plan["plan_id"], execute=True
            )
        self.assertEqual(self.sent, [])
        for _ in range(60):
            current = self.advance(plan)
            if current["state"] in {"completed", "needs_review"}:
                break
        self.assertEqual(current["state"], "completed", current)
        writes = [r for r in self.sent if r[2]]
        self.assertEqual([(r[0], r[1]["stage"]) for r in writes], [("stage", "basic")])
        index = next(i for i, r in enumerate(self.sent) if r[0] == "stage")
        self.assertEqual(
            self.sent[index + 1][1],
            {
                "action": "bill-read",
                "tab_id": "7",
                "expected_userid": "TEST001",
                "domain": "basic",
            },
        )
        self.assertEqual(self.sent[index + 2][0], "record_result")
        before = len(self.sent)
        self.advance(plan)
        self.assertEqual(len(self.sent), before)
        self.assertFalse(current["whole_claim_completed"])

    def test_unknown_save_preserves_pending_and_cannot_be_replayed(self):
        self.fail_save = True
        plan = self.plan()
        for _ in range(20):
            current = self.advance(plan)
            if current["state"] == "needs_review":
                break
        self.assertEqual(current["state"], "needs_review")
        self.assertTrue((self.paths["state"].parent / "pending_write.json").exists())
        before = len(self.sent)
        for _ in range(3):
            self.advance(plan)
        self.assertEqual(len(self.sent), before)
        self.assertEqual(len([r for r in self.sent if r[0] == "stage"]), 1)

    def test_pause_and_changed_source_do_not_start_later_writes(self):
        plan = self.plan()
        paused = self.service.case_finance(
            self.case, "fill_pause", plan_id=plan["plan_id"]
        )
        self.assertEqual(paused["state"], "paused")
        self.assertFalse(self.sent)
        form = read_json(self.paths['form']);form['amount']='2000.00';atomic_json(self.paths['form'],form)
        current=self.advance(plan)
        self.assertEqual(current['state'],'source_changed');self.assertFalse(self.sent)

    def test_recovery_reads_original_save_then_ack_without_second_save(self):
        self.fail_save=True;plan=self.plan()
        for _ in range(20):
            current=self.advance(plan)
            if current['state']=='needs_review':break
        saved=next(row for row in self.sent if row[0]=='stage')
        self.jobs[saved[3]]['result']['artifacts']=[{'name':'output.json','artifact_id':'original-save'}]
        resumed=self.service.case_finance(self.case,'fill_recover',plan_id=plan['plan_id'])
        self.assertEqual(resumed['phase'],'readback')
        recovery=self.sent[-1]
        self.assertEqual(recovery[0],'read_system');self.assertEqual(recovery[1]['job_id'],saved[3]);self.assertFalse(recovery[2])
        for _ in range(60):
            current=self.advance(plan)
            if current['state']in {'completed','needs_review'}:break
        self.assertEqual(current['state'],'completed',current)
        self.assertEqual(len([r for r in self.sent if r[0]=='stage']),1)

    def test_binding_uses_current_verified_identity_and_refuses_other_case_user(self):
        with patch("finance_connection.bound", return_value=(self.connection, {})):
            saved = self.service.case_finance(
                self.case, "bind", connection_id="a" * 32, execute=True
            )
            self.assertTrue(saved["saved"])
            self.assertEqual(
                read_json(location(self.service, self.case) / "connection.json")[
                    "expected_userid"
                ],
                "TEST001",
            )
            self.connection["expected_userid"] = "OTHER01"
            with self.assertRaisesRegex(ValueError, "另一填报账号"):
                self.service.case_finance(
                    self.case, "bind", connection_id="a" * 32, execute=True
                )

    def test_created_serial_is_taken_from_verified_result_not_model_input(self):
        body = {
            "ok": True,
            "draft_created": True,
            "verification": {"serial": "123456789", "independent_http_readback": True},
        }
        with patch(
            "case_finance.output",
            return_value=(
                {"job_id": "original", "artifact_id": "original-artifact"},
                body,
                {},
            ),
        ):
            value = self.service.case_finance(
                self.case, "bind_created", job_id="original", execute=True
            )
        request = self.sent[-1]
        self.assertEqual(request[0], "record_result")
        self.assertEqual(request[1]["serial"], "123456789")
        self.assertFalse(request[2])


if __name__ == "__main__":
    unittest.main()
