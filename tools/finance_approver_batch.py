"""逐项审批与独立回读。结果未知保留原pending和marker，不再次派发。"""

from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
from datetime import datetime, timezone

from assistant_api import AssistantService, atomic_json, read_json, PROJECT
from finance_native_create import ref, checked, checked_text, frozen, browser
from finance_approver_service import SCHEMA, load

STEP_SCHEMA = "ustc-approver-step-v1"


def snapshot(config, plan, item, baseline=None, readback=False):
    return {
        "case_id": plan["case_id"],
        "plan_id": plan["plan_id"],
        "expected_userid": plan["expected_userid"],
        "target": item["native_row"],
        "source": plan["source"],
        "decision": plan["decision"],
        "opinion": plan["opinion"],
        "contract": config["contract"],
        "adapter": config["adapter"],
        "tab_id": config["tab_id"],
        **{
            key: config[key]
            for key in [
                "queue_contracts",
                "history_contracts",
                "log_contracts",
                "context_contracts",
                "submission_contracts",
            ]
        },
        "baseline": baseline,
        "readback": readback,
    }


def observation(c, envelope):
    body = envelope.get("result", {})
    if (
        envelope.get("phase") != "script_completed"
        or envelope.get("tab_id") != c["tab_id"]
        or body.get("ok") is not True
        or body.get("complete") is not True
        or body.get("action") != "approver-http-inspect"
        or body.get("business_transport") != "native_http"
        or body.get("system_write") is not False
        or body.get("case_id") != c["case_id"]
        or body.get("serial") != c["target"]["t.serial_no"]
        or body.get("node_id") != c["target"]["t.node_id"]
        or body.get("expected_userid") != c["expected_userid"]
        or body.get("decision") != c["decision"]
        or body.get("opinion") != c["opinion"]
    ):
        raise ValueError("审批回读未完整绑定原账号、单号、角色及意见")
    return body


def same_binding(left, right):
    from validate_config import money

    keys = [
        "t.serial_no",
        "t.req_name",
        "t.remark",
        "t.uni_prj",
        "t.wfname",
        "t.wf_name",
        "t.uni_key",
        "t.node_id",
        "t.collab_ord",
        "t.isNeedCollab",
        "t.signtype",
        "t.nodecnt",
        "t.curr_mans",
    ]
    return all(left.get(k) == right.get(k) for k in keys) and money(
        left["t.amount"].replace(",", "")
    ) == money(right["t.amount"].replace(",", ""))


def verify_ack(previous, envelope, outcome):
    """由原record_result/acknowledge调用；不允许人工直接勾选审批成功。"""
    verification = previous["verification"]
    step = checked(verification["input"])
    c = step["browser_input"]
    checked(step["plan"])
    checked(step["authority"])
    checked_text(c["adapter"])
    prepared = observation(c, checked(verification["prepare"]))
    saved = checked(verification["save"])
    body = saved.get("result", {})
    observed = observation(c, envelope)
    if (
        step.get("schema") != STEP_SCHEMA
        or previous.get("case_id") != c["case_id"]
        or previous.get("serial") != c["target"]["t.serial_no"]
        or saved.get("phase") != "script_completed"
        or saved.get("tab_id") != c["tab_id"]
        or body.get("action") != "approver-http-decide"
        or body.get("case_id") != c["case_id"]
        or body.get("serial") != c["target"]["t.serial_no"]
        or body.get("expected_userid") != c["expected_userid"]
        or body.get("node_id") != c["target"]["t.node_id"]
        or body.get("decision") != c["decision"]
        or body.get("opinion") != c["opinion"]
    ):
        raise ValueError("原审批派发回执未闭合，只恢复原任务")
    writer = {**c, "pending_registered": True, "authorized": True, "prepared": prepared}
    expected = (
        "(async()=>{"
        + checked_text(c["adapter"])
        + ";return await reimbursementApproverDecide("
        + json.dumps(writer, ensure_ascii=False)
        + ");})()"
    )
    if (
        checked_text({"path": saved["script"], "sha256": saved["source_sha256"]})
        != expected
    ):
        raise ValueError("原审批脚本不匹配冻结输入")
    reader = {**c, "baseline": prepared, "readback": True}
    expected_read = (
        "(async()=>{"
        + checked_text(c["adapter"])
        + ";return await reimbursementApproverInspect("
        + json.dumps(reader, ensure_ascii=False)
        + ");})()"
    )
    if (
        checked_text({"path": envelope["script"], "sha256": envelope["source_sha256"]})
        != expected_read
    ):
        raise ValueError("回读不是本项独立读取器")
    if datetime.fromisoformat(
        envelope["dispatch_intent_at_utc"]
    ) <= datetime.fromisoformat(saved["executed_at_utc"]):
        raise ValueError("审批回读必须晚于原派发返回")
    if outcome == "saved":
        if (
            body.get("approval_dispatched") is not True
            or observed.get("completed") is not True
        ):
            raise ValueError("审批成功须原派发和同角色、同意见、同金额的独立回读")
    elif outcome == "not_applied":
        rows = observed.get("original", {}).get("pending_rows", [])
        if (
            body.get("approval_dispatched") is not False
            or observed.get("completed")
            or len(rows) != 1
            or not same_binding(rows[0], c["target"])
            or observed.get("completed_count") != prepared["completed_count"]
            or observed.get("matching_comment_count")
            != prepared["matching_comment_count"]
        ):
            raise ValueError("未应用须明确未派发，且原节点与原日志保持不变")
    else:
        raise ValueError("本审批记录只支持saved或not_applied，不接受宽泛完成标记")
    return {
        "schema": STEP_SCHEMA,
        "plan_id": c["plan_id"],
        "item_id": step["item_id"],
        "serial": c["target"]["t.serial_no"],
        "node_id": c["target"]["t.node_id"],
        "expected_userid": c["expected_userid"],
        "decision": c["decision"],
        "opinion": c["opinion"],
        "approval_step_completed": outcome == "saved",
        "whole_claim_completed": False,
        "financial_action_replayed": False,
        "outcome": outcome,
    }


def recover(c, path):
    from edge_runtime import browser_lock, read_marker
    from finance_backend import assert_original_transport

    saved = read_json(path)
    if saved.get("phase") == "script_completed":
        return saved
    assert_original_transport(saved)
    if saved.get("phase") in {"not_dispatched", "intent_prepared"} or not re.fullmatch(
        "reimbursement-result-[a-f0-9]{32}", str(saved.get("result_marker", ""))
    ):
        raise ValueError("没有可恢复的原派发marker，不能重新发送审批")
    with browser_lock():
        body = read_marker(saved["result_marker"], c["tab_id"], timeout=45)
    if not isinstance(body, dict):
        raise ValueError("原审批结果仍未知，保留原pending")
    return {
        **saved,
        "phase": "script_completed",
        "result": body,
        "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        "completion_time_basis": "recovery_observed_upper_bound",
        "marker_recovered_without_replay": True,
    }


def run(path, output):
    from reimburse import begin_write, acknowledge

    config = read_json(path)
    checked(config["plan"])
    checked(config["authority"])
    checked(config["source_snapshot"])
    checked_text(config["adapter"])
    service = AssistantService(config["workspace"], config["material_roots"])
    root, plan = load(service, config["plan_id"])
    _, paths = service.resolve(plan["case_id"])
    progress_path = root / "progress.json"
    progress = (
        read_json(progress_path)
        if progress_path.exists()
        else {
            "schema": SCHEMA,
            "plan_id": plan["plan_id"],
            "plan_sha256": plan["fingerprint"],
            "state": "planned",
            "items": [
                {
                    "item_id": i["item_id"],
                    "serial": i["serial"],
                    "summary": i["summary"],
                    "amount": i["amount"],
                    "role": i["role"],
                    "state": "not_started",
                }
                for i in plan["items"]
            ],
        }
    )
    if progress.get("plan_sha256") != plan["fingerprint"]:
        raise ValueError("原批次进度与计划指纹不同")
    output = Path(output)
    work = output.parent

    def finish(state, error=None):
        progress.update(state=state, last_job_id=config["job_id"])
        if error:
            progress["error"] = str(error)
        else:
            progress.pop("error", None)
        atomic_json(progress_path, progress)
        result = {
            **progress,
            "ok": state
            not in {"unknown_result", "result_needs_review", "blocked_before_dispatch"},
            "case_id": plan["case_id"],
            "whole_claim_completed": False,
            "safe_to_retry": False,
            "financial_action_replayed": False,
        }
        atomic_json(output, result)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 2

    if progress["state"] == "completed":
        return finish("completed")
    from finance_connection import bound

    connection, _ = bound(service, config["connection_id"])
    config["tab_id"] = connection["tab_id"]
    if connection["expected_userid"] != plan["expected_userid"]:
        raise ValueError("当前账号与原审批范围不同")
    if config["action"] != "reconcile" and any(
        connection[k] != plan["connection"][k]
        for k in ["tab_id", "window_id", "namespace"]
    ):
        raise ValueError("原审批页面身份改变，不重发")
    pending = paths["state"].parent / "pending_write.json"
    if config["action"] in {"recover", "reconcile"}:
        if not pending.exists():
            if progress["state"] != "running":
                return finish(progress["state"])
            # 此runner已取得统一case/browser锁。补齐“ack已落盘、进度尚未写回”
            # 的断点；没有原ack就不能把派发意图重新变成未开始。
            try:
                for row in progress["items"]:
                    if row["state"] != "dispatch_intent":
                        continue
                    step = checked(row["input"])
                    ack_path = (
                        Path(row["input"]["path"]).parent / "operator_readback_ack.json"
                    )
                    ack = read_json(ack_path)
                    if (
                        ack.get("verification", {}).get("input") != row["input"]
                        or step["item_id"] != row["item_id"]
                    ):
                        raise ValueError("原回读确认记录与本项不一致")
                    proof = verify_ack(
                        ack, read_json(ack["readback_receipt"]), ack["outcome"]
                    )
                    row.update(
                        state="verified"
                        if proof["approval_step_completed"]
                        else "not_started",
                        acknowledgement=str(ack_path),
                    )
                if any(
                    r["state"]
                    not in {"verified", "not_started", "awaiting_verification"}
                    for r in progress["items"]
                ):
                    raise ValueError("原批次停点尚未完整核对")
                return finish("ready_to_resume")
            except Exception as error:
                return finish("result_needs_review", error)
        prior = read_json(pending)
        verification = prior.get("verification", {})
        if (
            verification.get("schema") != STEP_SCHEMA
            or verification.get("plan_id") != plan["plan_id"]
        ):
            raise ValueError("pending不属于原审批批次")
        step = checked(verification["input"])
        c = step["browser_input"]
        row = next(r for r in progress["items"] if r["item_id"] == step["item_id"])
        if config["action"] == "reconcile" and config["tab_id"] != c["tab_id"]:
            return finish(
                "result_needs_review",
                "原页已改变；保留原回执，需要独立来源核对，未冒称恢复了原marker。",
            )
        try:
            recovered = recover(c, verification["save"]["path"])
            receipt = work / "recovered-original.json"
            frozen(receipt, recovered)
            # 原回执原样保留；恢复引用另存，不改写原派发状态。
            prior["verification"]["save"] = ref(receipt)
            atomic_json(pending, prior)
            prepared = observation(c, checked(verification["prepare"]))
            reader = {**c, "baseline": prepared, "readback": True}
            readback = work / "independent-readback.json"
            seen = browser(reader, "reimbursementApproverInspect", readback)
            report = observation(c, seen)
            outcome = "saved" if report.get("completed") else "not_applied"
            ack = acknowledge(
                paths,
                readback,
                "原审批任务恢复后，按节点、操作者、意见、金额独立回读",
                outcome,
            )
            row.update(
                state="verified" if outcome == "saved" else "not_started",
                acknowledgement=ack,
            )
            return finish("ready_to_resume")
        except Exception as error:
            return finish("unknown_result", error)
    if pending.exists():
        return finish("unknown_result", "原写入尚未回读，请先恢复原任务。")
    if config["action"] == "run" and progress["state"] not in {"planned", "running"}:
        return finish(progress["state"])
    if config["action"] == "resume" and progress["state"] not in {
        "verification_required",
        "ready_to_resume",
        "paused",
        "documents_pending",
    }:
        return finish(progress["state"])
    if config["action"] == "finalize" and progress["state"] != "documents_pending":
        return finish(progress["state"])
    if config["action"] == "verification":
        if progress["state"] != "verification_required":
            return finish(progress["state"])
        waiting = [
            r for r in progress["items"] if r["state"] == "awaiting_verification"
        ]
        if len(waiting) != 1:
            raise ValueError("本人验证必须对应原批次中唯一的当前停点")
        row = waiting[0]
        item = next(i for i in plan["items"] if i["item_id"] == row["item_id"])
        baseline = checked(row["baseline"])["result"]
        c = snapshot(config, plan, item, baseline)
        c["adapter"] = config["verification_adapter"]
        checked_text(c["adapter"])
        receipt = work / "human-verification.json"
        try:
            observed = browser(
                c, "reimbursementApproverVerification", receipt, {"authorized": True}
            )
            body = observed.get("result", {})
            if (
                observed.get("phase") != "script_completed"
                or body.get("ok") is not True
                or body.get("system_write") is not False
                or body.get("approval_dispatched") is not False
                or body.get("code_read") is not False
                or body.get("case_id") != plan["case_id"]
                or body.get("serial") != item["serial"]
                or body.get("node_id") != item["native_row"]["t.node_id"]
                or body.get("expected_userid") != plan["expected_userid"]
                or body.get("decision") != plan["decision"]
                or body.get("opinion") != plan["opinion"]
            ):
                raise ValueError("本人验证界面未完整绑定本项原角色及意见")
            row["verification_receipt"] = ref(receipt)
            if body.get("state") in {"already_verified", "already_processed"}:
                return finish("ready_to_resume")
            if (
                body.get("state") != "verification_required"
                or body.get("verification_window_visible") is not True
            ):
                raise ValueError("原生验证码界面尚未明确可见")
            from registered_bridge import show_approver_verification

            shown = show_approver_verification(
                connection["tab_id"],
                tab_id_namespace=connection["namespace"],
                expected_window_id=connection["window_id"],
                serial=item["serial"],
                expected_userid=plan["expected_userid"],
                node_id=item["native_row"]["t.node_id"],
            )
            display = work / "human-verification-display.json"
            atomic_json(display, shown)
            row["verification_display"] = ref(display)
            return finish("verification_required")
        except Exception as error:
            return finish("verification_required", error)
    if progress["state"] == "running" and progress.get("last_job_id") not in (
        None,
        config["job_id"],
    ):
        return finish("unknown_result", "原批次尚未完成，不能用新请求重复执行。")
    progress.update(state="running", last_job_id=config["job_id"])
    atomic_json(progress_path, progress)

    def finalize_item(item, row):
        if row.get("finalized"):
            return
        original = checked(row["baseline"])["result"]
        c = snapshot(config, plan, item, original, readback=True)
        target = work / ("finalize-" + item["item_id"] + ".json")
        envelope = browser(
            c, "reimbursementApproverFinalize", target, {"authorized": True}, write=True
        )
        body = envelope.get("result", {})
        if (
            envelope.get("phase") != "script_completed"
            or body.get("ok") is not True
            or body.get("action") != "approver-http-finalize"
            or body.get("approval_dispatched") is not False
            or body.get("case_id") != plan["case_id"]
            or body.get("serial") != item["serial"]
            or body.get("node_id") != item["native_row"]["t.node_id"]
            or body.get("expected_userid") != plan["expected_userid"]
            or body.get("decision") != plan["decision"]
            or body.get("opinion") != plan["opinion"]
            or body.get("cache_refreshed") is not True
        ):
            raise ValueError(
                "本项审批已回读，但原生文档或缓存整理尚待核对；只续整理，不重发审批。"
            )
        row.update(finalized=True, finalization=ref(target))
        atomic_json(progress_path, progress)

    for index, item in enumerate(plan["items"]):
        row = progress["items"][index]
        if row["state"] == "verified":
            try:
                finalize_item(item, row)
            except Exception as error:
                return finish("documents_pending", error)
            continue
        if config["action"] == "finalize":
            return finish("ready_to_resume")
        control = root / "control.json"
        if control.exists() and read_json(control).get("pause_requested") is True:
            return finish("paused")
        if row["state"] not in {"not_started", "awaiting_verification"}:
            return finish("unknown_result", "此项原结果尚未闭合，未继续新审批。")
        step_dir = work / ("item-" + item["item_id"])
        step_dir.mkdir(exist_ok=True)
        baseline = checked(row["baseline"])["result"] if row.get("baseline") else None
        c = snapshot(config, plan, item, baseline)
        try:
            prepared_path = step_dir / "prepare.json"
            prepared_envelope = browser(
                c, "reimbursementApproverInspect", prepared_path
            )
            prepared = observation(c, prepared_envelope)
            if not row.get("baseline"):
                row["baseline"] = ref(prepared_path)
            if prepared.get("completed"):
                row.update(
                    state="verified",
                    verified_by="independent_native_log",
                    readback=ref(prepared_path),
                )
                atomic_json(progress_path, progress)
                try:
                    finalize_item(item, row)
                except Exception as error:
                    return finish("documents_pending", error)
                continue
            if prepared.get("verification_required"):
                row["state"] = "awaiting_verification"
                row["prepare"] = ref(prepared_path)
                return finish("verification_required")
            if prepared.get("state") != "ready":
                return finish(
                    "blocked_before_dispatch", "原审批项已变化，先核对本项角色及状态。"
                )
            step = {
                "schema": STEP_SCHEMA,
                "item_id": item["item_id"],
                "plan": config["plan"],
                "authority": config["authority"],
                "browser_input": c,
            }
            step_file = step_dir / "input.json"
            frozen(step_file, step)
            saved_path = step_dir / "save.json"
            begin_write(
                paths,
                {"case_id": plan["case_id"], "serial": item["serial"]},
                "approver:" + plan["decision"],
                step_dir,
                {
                    "schema": STEP_SCHEMA,
                    "plan_id": plan["plan_id"],
                    "input": ref(step_file),
                    "prepare": ref(prepared_path),
                    "save": {"path": str(saved_path), "sha256": None},
                },
            )
            row.update(
                state="dispatch_intent",
                input=ref(step_file),
                original_write_job_id=config["job_id"],
            )
            atomic_json(progress_path, progress)
            saved = browser(
                c,
                "reimbursementApproverDecide",
                saved_path,
                {"pending_registered": True, "authorized": True, "prepared": prepared},
                write=True,
            )
            pending_value = read_json(pending)
            pending_value["verification"]["save"] = ref(saved_path)
            atomic_json(pending, pending_value)
            if saved.get("phase") != "script_completed":
                return finish(
                    "unknown_result", "审批已进入原派发路径，结果尚未完整返回。"
                )
            reader = {**c, "baseline": prepared, "readback": True}
            readback_path = step_dir / "readback.json"
            observed = observation(
                c, browser(reader, "reimbursementApproverInspect", readback_path)
            )
            outcome = "saved" if observed.get("completed") else "not_applied"
            ack = acknowledge(
                paths,
                readback_path,
                "本批按原节点、当前操作者、原意见和金额逐项独立核验",
                outcome,
            )
            row.update(
                state="verified" if outcome == "saved" else "not_started",
                readback=ref(readback_path),
                acknowledgement=ack,
            )
            atomic_json(progress_path, progress)
            if outcome != "saved":
                if saved.get("result", {}).get("state") == "verification_required":
                    row["state"] = "awaiting_verification"
                    return finish("verification_required")
                return finish(
                    "blocked_before_dispatch",
                    "原检查未批准本项，保留原生原因，请先处理后再重新核定范围。",
                )
            try:
                finalize_item(item, row)
            except Exception as error:
                return finish("documents_pending", error)
        except Exception as error:
            return finish(
                "unknown_result" if pending.exists() else "blocked_before_dispatch",
                error,
            )
    return finish("completed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.input, args.output))
