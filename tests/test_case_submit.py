"""提交编排使用合成学校回执，验证授权、原任务、暂停和实际完成标记。"""

import copy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from assistant_api import AssistantService, atomic_json, read_json
from finance_native_create import ref
from case_finance import location


class CaseSubmitTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.service = AssistantService(temp.name, [temp.name])
        self.case = "ready_fixture"
        self.service.new_case(self.case)
        self.paths = self.service.resolve(self.case)[1]
        form = read_json(ROOT / "tests/fixtures/synthetic_form_config.json")
        form.update(
            case_id=self.case,
            expected_userid="TEST001",
            delivery={"delegate_finance_print": False, "originals_required": True},
        )
        atomic_json(self.paths["form"], form)
        self.connection = {
            "connection_id": "a" * 32,
            "expected_userid": "TEST001",
            "tab_id": "7",
            "namespace": "synthetic",
        }
        self.jobs = {}
        self.sent = []
        self.department = False
        self.fail_action = None
        for name, value in [("case_submit.scoped", (self.service, self.connection))]:
            p = patch(name, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        for obj, name, fn in [
            (self.service, "enqueue", self.enqueue),
            (self.service, "result", lambda case, job: self.jobs[job]["result"]),
        ]:
            p = patch.object(obj, name, side_effect=fn)
            p.start()
            self.addCleanup(p.stop)
        p = patch("case_submit.output", side_effect=self.output)
        p.start()
        self.addCleanup(p.stop)

    def enqueue(self, case, operation, params, request_id, execute=False):
        request_id = "j_" + hashlib.sha256(request_id.encode()).hexdigest()[:24]
        if request_id in self.jobs:
            return {
                "job_id": request_id,
                "status": self.jobs[request_id]["result"]["status"],
            }
        self.sent.append((operation, copy.deepcopy(params), execute, request_id))
        action = params.get("action")
        body = {
            "ok": True,
            "complete": True,
            "case_id": case,
            "serial": "123456789",
            "expected_userid": "TEST001",
            "native_error_present": False,
            "required_changes": {},
            "original": {"bill": {"t.status": "-99", "t.node_id": ""}},
            "environment": {},
            "promise_text": "合成学校承诺正文，实际学校承诺须现场读取。",
            "promise_sha256": "e" * 64,
        }
        if action == "workflow-inspect":
            folder = self.service.jobs / case / request_id
            folder.mkdir(parents=True)
            atomic_json(
                folder / "native-workflow-input.json",
                {
                    **{k: ref(self.paths[k]) for k in ["case", "form", "invoices"]},
                    "tab_id": "7",
                },
            )
        if action == "workflow-readback":
            original = self.jobs[params["job_id"]]["request"]["parameters"]["action"]
            if (
                original == "department"
                or original == "promise"
                and not self.department
            ):
                body["original"]["bill"]["t.node_id"] = "508"
        if operation == "record_result":
            original = self.jobs[params["job_id"]]["body"]
            body = {
                "ok": True,
                "pending_cleared": True,
                "independent_http_readback": True,
                "workflow_started": original.get("original", {})
                .get("bill", {})
                .get("t.node_id")
                == "508",
            }
        if action == "next":
            body["messages"] = {}
        status = (
            "unknown"
            if self.fail_action and action == self.fail_action
            else "completed"
        )
        self.jobs[request_id] = {
            "body": body,
            "request": {"operation": operation, "parameters": params},
            "result": {
                "status": status,
                "exit_code": 0 if status == "completed" else None,
            },
        }
        return {"job_id": request_id, "status": status}

    def output(self, service, case, job, operation=None, action=None):
        record = self.jobs[job]
        if record["result"]["status"] != "completed":
            raise ValueError("原任务结果未知")
        if operation:
            self.assertEqual(operation, record["request"]["operation"])
        if action:
            self.assertEqual(action, record["request"]["parameters"]["action"])
        return (
            {"job_id": job, "artifact_id": "source-ref", "sha256": "a" * 64},
            copy.deepcopy(record["body"]),
            record["request"],
        )

    def plan(self):
        original = self.service.case_finance(
            self.case, "ready_inspect", request_id="ready-inspect"
        )
        return self.service.case_finance(
            self.case, "ready_plan", job_id=original["job_id"]
        )

    def advance(self, plan, note="已核对本单及合成学校承诺"):
        return self.service.case_finance(
            self.case,
            "ready_continue",
            plan_id=plan["plan_id"],
            authorization_note=note,
            execute=True,
        )

    def finish(self, plan):
        for _ in range(40):
            value = self.advance(plan)
            if value["state"] in {
                "completed",
                "needs_review",
                "source_changed",
                "paused",
            }:
                return value
        self.fail("准备流程未在有限步骤内结束")

    def test_exact_promise_authority_and_no_final_approval(self):
        plan = self.plan()
        self.assertIn("合成学校承诺", plan["promise_text"])
        with self.assertRaisesRegex(ValueError, "首次办理"):
            self.advance(plan, None)
        value = self.finish(plan)
        self.assertEqual(value["state"], "completed")
        self.assertTrue(value["system_result_verified"])
        self.assertFalse(value["whole_claim_completed"])
        writes = [p["action"] for op, p, execute, _ in self.sent if execute]
        self.assertEqual(writes, ["options", "next", "promise"])
        self.assertEqual(
            value["next_action"]["arguments"]["connection_id"],
            self.connection["connection_id"],
        )
        count = len(self.sent)
        self.advance(plan)
        self.assertEqual(len(self.sent), count)

    def test_department_step_uses_original_promise_and_two_separate_readbacks(self):
        self.department = True
        value = self.finish(self.plan())
        self.assertEqual(value["state"], "completed")
        writes = [p for op, p, execute, _ in self.sent if execute]
        self.assertEqual(
            [p["action"] for p in writes], ["options", "next", "promise", "department"]
        )
        self.assertEqual(
            self.jobs[writes[-1]["job_id"]]["request"]["parameters"]["action"],
            "promise",
        )

    def test_pause_finishes_current_readback_before_next_write(self):
        plan = self.plan()
        self.advance(plan)
        self.service.case_finance(self.case, "ready_pause", plan_id=plan["plan_id"])
        value = self.finish(plan)
        self.assertEqual(value["state"], "paused")
        self.assertEqual([p["action"] for _, p, w, _ in self.sent if w], ["options"])
        value = self.finish(plan)
        self.assertEqual(value["state"], "completed")

    def test_changed_business_input_or_connection_never_continues(self):
        plan = self.plan()
        form = read_json(self.paths["form"])
        form["amount"] = "999.00"
        atomic_json(self.paths["form"], form)
        self.assertEqual(self.advance(plan)["state"], "source_changed")
        self.assertFalse(any(w for _, _, w, _ in self.sent))

    def test_failed_write_preserves_original_request_without_retry(self):
        self.fail_action = "next"
        plan = self.plan()
        value = self.finish(plan)
        self.assertEqual(value["state"], "needs_review")
        original = value["job_id"]
        count = len(self.sent)
        self.assertEqual(self.advance(plan)["job_id"], original)
        self.assertEqual(len(self.sent), count)
        self.assertEqual(sum(p.get("action") == "next" for _, p, _, _ in self.sent), 1)

    def test_native_errors_and_missing_promise_block_plan(self):
        job = self.service.case_finance(
            self.case, "ready_inspect", request_id="ready-inspect"
        )["job_id"]
        body = self.jobs[job]["body"]
        for name, value in [
            ("native_error_present", True),
            ("promise_text", ""),
            ("required_changes", {"basic": "changed"}),
        ]:
            old = body[name]
            body[name] = value
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.service.case_finance(self.case, "ready_plan", job_id=job)
            body[name] = old

    def test_false_completed_summary_does_not_claim_success(self):
        plan = self.plan()
        path = (
            location(self.service, self.case)
            / "ready"
            / plan["plan_id"]
            / "progress.json"
        )
        atomic_json(path, {"state": "completed", "step": 9, "jobs": {}, "rows": []})
        value = self.service.case_finance(
            self.case, "ready_status", plan_id=plan["plan_id"]
        )
        self.assertEqual(value["state"], "needs_review")
        self.assertFalse(value["system_result_verified"])


if __name__ == "__main__":
    unittest.main()
