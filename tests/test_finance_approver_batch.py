"""真实任务文件和pending生命周期；学校HTTP由合成回执替代。"""

import contextlib
import copy
from datetime import datetime, timedelta, timezone
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from assistant_api import AssistantService, atomic_json, read_json
from finance_native_create import ref
from finance_approver_service import SCHEMA, fingerprint, view
import finance_approver_batch as batch


class ApproverLifecycleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.service = AssistantService(self.root, [self.root])
        self.case = "account_fixture"
        self.service.new_case(self.case)
        self.connection = {
            "connection_id": "a" * 32,
            "case_id": self.case,
            "expected_userid": "TEST01",
            "tab_id": "7",
            "window_id": "8",
            "namespace": "synthetic",
        }
        self.items = []
        for serial in ["1234", "1235"]:
            target = {
                "t.serial_no": serial,
                "t.req_name": "APPLICANT - Other",
                "t.remark": "Synthetic " + serial,
                "t.uni_prj": "TEST",
                "t.wfname": "YBX_LC",
                "t.wf_name": "YBX_LC",
                "t.uni_key": serial + "0",
                "t.node_id": "449",
                "t.collab_ord": "none",
                "t.isNeedCollab": "F",
                "t.signtype": "no",
                "t.nodecnt": "1",
                "t.curr_mans": "TEST01 - Teacher",
                "t.amount": "10.00",
            }
            self.items.append(
                {
                    "item_id": serial,
                    "serial": serial,
                    "summary": target["t.remark"],
                    "amount": "10.00",
                    "role": "证明人",
                    "native_row": target,
                }
            )
        body = {
            "schema": SCHEMA,
            "case_id": self.case,
            "expected_userid": "TEST01",
            "connection": self.connection,
            "source": {},
            "items": self.items,
            "decision": "agree",
            "opinion": "情况属实",
        }
        digest = fingerprint(body)
        self.plan = {**body, "fingerprint": digest, "plan_id": digest[:32]}
        self.folder = self.root / ".local/approver-batches" / self.plan["plan_id"]
        self.folder.mkdir(parents=True)
        atomic_json(self.folder / "plan.json", self.plan)
        atomic_json(self.folder / "authority.json", {"user_text": "合成授权"})
        self.adapter = self.root / "adapter.js"
        self.adapter.write_text("// synthetic", encoding="utf-8")
        self.source = self.root / "source.json"
        atomic_json(self.source, {})
        self.config = {
            "workspace": str(self.root),
            "material_roots": [str(self.root)],
            "plan_id": self.plan["plan_id"],
            "plan": ref(self.folder / "plan.json"),
            "authority": ref(self.folder / "authority.json"),
            "source_snapshot": ref(self.source),
            "adapter": ref(self.adapter),
            "connection_id": self.connection["connection_id"],
            "contract": {},
            "queue_contracts": [],
            "history_contracts": [],
            "log_contracts": [],
            "context_contracts": [],
            "submission_contracts": [],
        }
        self.done = set()
        self.writes = []
        self.finalized = []
        self.tick = 0
        self.mode = "normal"
        self.sms = False
        patcher = patch("finance_connection.bound", return_value=(self.connection, {}))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.pending = (
            self.service.resolve(self.case)[1]["state"].parent / "pending_write.json"
        )

    def browser(self, c, method, output, extra=None, *, write=False):
        import json

        self.tick += 1
        output = Path(output)
        serial = c["target"]["t.serial_no"]
        config = {**c, **(extra or {})}
        code = (
            "(async()=>{"
            + self.adapter.read_text(encoding="utf-8")
            + ";return await "
            + method
            + "("
            + json.dumps(config, ensure_ascii=False)
            + ");})()"
        )
        script = output.with_suffix(".js")
        script.write_text(code, encoding="utf-8")
        stamp = datetime(2026, 11, 3, tzinfo=timezone.utc) + timedelta(
            seconds=self.tick * 2
        )
        envelope = {
            "phase": "script_completed",
            "tab_id": "7",
            "script": str(script),
            "source_sha256": ref(script)["sha256"],
            "dispatch_intent_at_utc": stamp.isoformat(),
            "executed_at_utc": (stamp + timedelta(seconds=1)).isoformat(),
        }
        base = {
            "case_id": self.case,
            "serial": serial,
            "node_id": "449",
            "expected_userid": "TEST01",
            "decision": "agree",
            "opinion": "情况属实",
            "business_transport": "native_http",
            "ok": True,
        }
        if method == "reimbursementApproverInspect":
            done = serial in self.done
            body = {
                **base,
                "action": "approver-http-inspect",
                "complete": True,
                "system_write": False,
                "completed": done and c.get("baseline") is not None,
                "completed_count": int(done),
                "matching_comment_count": int(done),
                "verification_required": self.sms,
                "state": "approval_step_verified"
                if done
                else "verification_required"
                if self.sms
                else "ready",
                "original": {"pending_rows": [] if done else [c["target"]]},
            }
            if self.mode == "wrong_actor" and c.get("readback"):
                body["expected_userid"] = "OTHER01"
        elif method == "reimbursementApproverDecide":
            self.writes.append(serial)
            dispatched = self.mode != "expires"
            if dispatched:
                self.done.add(serial)
            else:
                self.sms = True
            body = {
                **base,
                "action": "approver-http-decide",
                "approval_dispatched": dispatched,
                "state": "submitted_independent_readback_required"
                if dispatched
                else "verification_required",
            }
            if self.mode == "pause_after_first":
                atomic_json(self.folder / "control.json", {"pause_requested": True})
            if self.mode == "unknown":
                envelope["phase"] = "dispatched"
                envelope["result_marker"] = "reimbursement-result-" + "b" * 32
        elif method == "reimbursementApproverFinalize":
            self.assertTrue(write)
            self.assertIn(serial, self.done)
            self.finalized.append(serial)
            if self.mode == "document_failure":
                raise TimeoutError("synthetic document timeout")
            body = {
                **base,
                "action": "approver-http-finalize",
                "approval_dispatched": False,
                "cache_refreshed": True,
            }
        else:
            raise AssertionError(method)
        envelope["result"] = body
        atomic_json(output, envelope)
        return envelope

    def execute(self, action="run"):
        job = "j_" + str(self.tick + 1).zfill(24)
        work = self.root / job
        work.mkdir(exist_ok=True)
        config = {**self.config, "action": action, "job_id": job}
        atomic_json(work / "input.json", config)
        with (
            patch.object(batch, "browser", side_effect=self.browser),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            code = batch.run(work / "input.json", work / "output.json")
        return code, read_json(work / "output.json")

    def test_two_items_complete_and_reopening_never_replays(self):
        code, result = self.execute()
        self.assertEqual(code, 0, result)
        self.assertEqual(result["state"], "completed")
        self.assertEqual(self.writes, ["1234", "1235"])
        self.assertEqual(self.finalized, ["1234", "1235"])
        self.assertFalse(self.pending.exists())
        state = read_json(self.service.resolve(self.case)[1]["state"])
        self.assertFalse(state["last_approver_action"]["whole_claim_completed"])
        self.execute("resume")
        self.assertEqual(self.writes, ["1234", "1235"])

    def test_human_verification_current_item_is_read_before_remaining_dispatch(self):
        self.sms = True
        _, result = self.execute()
        self.assertEqual(result["state"], "verification_required")
        self.assertEqual(self.writes, [])
        self.done.add("1234")
        self.sms = False
        _, result = self.execute("resume")
        self.assertEqual(result["state"], "completed", result)
        self.assertEqual(self.writes, ["1235"])
        self.assertEqual(result["items"][0]["verified_by"], "independent_native_log")

    def test_expiry_before_approval_clears_only_proven_unapplied_pending(self):
        self.mode = "expires"
        _, result = self.execute()
        self.assertEqual(result["state"], "verification_required", result)
        self.assertFalse(self.pending.exists())
        self.assertEqual(self.done, set())
        self.assertEqual(result["items"][0]["state"], "awaiting_verification")

    def test_unknown_result_requires_original_recovery_then_remaining_only(self):
        self.mode = "unknown"
        _, result = self.execute()
        self.assertEqual(result["state"], "unknown_result")
        self.assertTrue(self.pending.exists())
        original = read_json(self.pending)
        saved_path = original["verification"]["save"]["path"]
        saved = read_json(saved_path)
        _, again = self.execute("resume")
        self.assertEqual(again["state"], "unknown_result")
        self.assertEqual(self.writes, ["1234"])
        self.mode = "normal"
        with patch.object(
            batch, "recover", return_value={**saved, "phase": "script_completed"}
        ):
            _, recovered = self.execute("recover")
        self.assertEqual(recovered["state"], "ready_to_resume", recovered)
        self.assertFalse(self.pending.exists())
        self.assertEqual(read_json(saved_path)["phase"], "dispatched")
        _, result = self.execute("resume")
        self.assertEqual(result["state"], "completed", result)
        self.assertEqual(self.writes, ["1234", "1235"])

    def test_pause_keeps_completed_item_and_continues_remaining(self):
        self.mode = "pause_after_first"
        _, result = self.execute()
        self.assertEqual(result["state"], "paused", result)
        self.assertEqual(self.writes, ["1234"])
        self.assertFalse(self.pending.exists())
        self.service.approver_batch("pause", plan_id=self.plan["plan_id"])
        self.assertTrue(view(self.service, self.plan["plan_id"])["pause_requested"])
        atomic_json(self.folder / "control.json", {"pause_requested": False})
        self.mode = "normal"
        _, result = self.execute("resume")
        self.assertEqual(result["state"], "completed")
        self.assertEqual(self.writes, ["1234", "1235"])

    def test_document_failure_only_repeats_document_generation(self):
        self.mode = "document_failure"
        _, result = self.execute()
        self.assertEqual(result["state"], "documents_pending", result)
        self.assertFalse(self.pending.exists())
        self.assertEqual(self.writes, ["1234"])
        self.mode = "normal"
        _, result = self.execute("finalize")
        self.assertEqual(result["state"], "ready_to_resume")
        self.assertEqual(self.writes, ["1234"])
        _, result = self.execute("resume")
        self.assertEqual(result["state"], "completed")
        self.assertEqual(self.writes, ["1234", "1235"])

    def test_different_actor_readback_cannot_clear_pending(self):
        self.mode = "wrong_actor"
        _, result = self.execute()
        self.assertEqual(result["state"], "unknown_result")
        self.assertTrue(self.pending.exists())
        self.assertEqual(self.writes, ["1234"])
        self.assertEqual(self.finalized, [])


if __name__ == "__main__":
    unittest.main()
