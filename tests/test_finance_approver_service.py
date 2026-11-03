from pathlib import Path
import copy
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
from assistant_api import atomic_json
from finance_approver_service import assert_can_dispatch
import test_finance_approver_batch as lifecycle


class ApproverServiceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = lifecycle.ApproverLifecycleTests(
            "test_two_items_complete_and_reopening_never_replays"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        self.service = f.service
        self.native = {
            "action": "approvals-list",
            "business_transport": "native_http",
            "ok": True,
            "complete": True,
            "case_id": f.case,
            "expected_userid": "TEST01",
            "account_scope": "current_approver",
            "query_scope": "native_approval_list_empty_filters",
            "system_write": False,
            "rows": [i["native_row"] for i in f.items],
            "total_records": 2,
        }
        self.envelope = {"phase": "script_completed", "result": self.native}
        self.request = {"execute": False, "parameters": {"expected_userid": "TEST01"}}
        self.source_job = "j_" + "9" * 24
        patcher = patch.object(
            self.service,
            "result",
            return_value={
                "status": "completed",
                "exit_code": 0,
                "artifacts": [{"name": "output.json", "artifact_id": "a0"}],
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(
            self.service,
            "_typed_case_artifact",
            return_value=(f.source, self.envelope, self.request),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def inspect(self):
        return self.service.approver_batch(
            "inspect",
            connection_id=self.fixture.connection["connection_id"],
            source_job_id=self.source_job,
        )

    def test_selection_frozen_plan_and_authority_are_separate(self):
        rows = self.inspect()["items"]
        self.assertEqual(len(rows), 2)
        self.assertNotIn("native_row", rows[0])
        args = {
            "connection_id": self.fixture.connection["connection_id"],
            "source_job_id": self.source_job,
            "selected_ids": [rows[1]["item_id"]],
            "decision": "agree",
            "opinion": "本次明确意见",
        }
        first = self.service.approver_batch("plan", **args)
        second = self.service.approver_batch("plan", **args)
        self.assertEqual(first["plan_id"], second["plan_id"])
        self.assertEqual(first["items"][0]["serial"], "1235")
        self.assertFalse(
            (
                self.fixture.root
                / ".local/approver-batches"
                / first["plan_id"]
                / "authority.json"
            ).exists()
        )
        with self.assertRaises(ValueError):
            self.service.approver_batch(
                "run", plan_id=first["plan_id"], execute=True, request_id="fixture"
            )
        with self.assertRaises(ValueError):
            self.service.approver_batch("plan", **{**args, "selected_ids": ["missing"]})

    def test_partial_history_or_other_identity_never_becomes_pending_selection(self):
        for key, bad in [
            ("complete", False),
            ("account_scope", "current_approver_history"),
            ("expected_userid", "OTHER01"),
            ("total_records", 3),
            ("system_write", True),
        ]:
            old = self.native[key]
            self.native[key] = bad
            with self.assertRaises(ValueError, msg=key):
                self.inspect()
            self.native[key] = old

    def test_special_role_is_visible_but_cannot_enter_batch(self):
        self.native["rows"][0]["t.isNeedCollab"] = "Y"
        row = self.inspect()["items"][0]
        self.assertFalse(row["supported"])
        self.assertTrue(row["reason"])
        with self.assertRaises(ValueError):
            self.service.approver_batch(
                "plan",
                connection_id=self.fixture.connection["connection_id"],
                source_job_id=self.source_job,
                selected_ids=[row["item_id"]],
                decision="agree",
                opinion="本次明确意见",
            )

    def test_agent_status_does_not_cache_previous_progress(self):
        plan = self.fixture.plan["plan_id"]
        first = self.service.assist(
            action="call",
            operation="approver_batch",
            inputs={"action": "status", "plan_id": plan},
        )
        self.assertEqual(first["state"], "planned")
        atomic_json(
            self.fixture.folder / "progress.json", {"state": "paused", "items": []}
        )
        second = self.service.assist(
            action="call",
            operation="approver_batch",
            inputs={"action": "status", "plan_id": plan},
        )
        self.assertEqual(second["state"], "paused")
        self.assertEqual(second["next_action"]["tool"], "reimbursement_assist")
        self.assertEqual(
            second["next_action"]["arguments"]["inputs"]["action"], "resume"
        )

    def test_pending_verification_prevents_other_financial_dispatch_but_not_materials(
        self,
    ):
        atomic_json(
            self.fixture.folder / "progress.json",
            {"state": "verification_required", "items": []},
        )
        request = {"operation": "stage", "execute": True, "parameters": {}}
        with self.assertRaises(ValueError):
            assert_can_dispatch(self.service, request)
        assert_can_dispatch(self.service, {**request, "operation": "material_intake"})
        assert_can_dispatch(
            self.service,
            {
                **request,
                "operation": "approver_batch",
                "parameters": {"plan_id": self.fixture.plan["plan_id"]},
            },
        )

    def test_inconsistent_completed_summary_never_hides_incomplete_items(self):
        plan=self.fixture.plan['plan_id']
        atomic_json(self.fixture.folder/'progress.json',{'state':'completed','items':[]})
        result=self.service.approver_batch('status',plan_id=plan)
        self.assertEqual(result['state'],'result_needs_review')
        self.assertFalse(result.get('system_result_verified',False))
        self.assertIn('不一致',result['error'])


if __name__ == "__main__":
    unittest.main()
