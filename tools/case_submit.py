"""从已核草稿准备到本人确认；学校承诺正文、原任务和逐步回读保持绑定。"""

import hashlib
import re
import uuid

from assistant_contract import APIError
from case_finance import location, scoped, output, remember, prior
from case_fill import digest, inputs


def submission_plan(form, who, source):
    from submission import view_plan, validate_plan

    delivery = form.get("delivery", {})
    if (
        type(delivery.get("delegate_finance_print")) is not bool
        or type(delivery.get("originals_required")) is not bool
    ):
        raise APIError(
            "case_ready_delivery", "先核对是否委托财务打印、是否提交纸质原件"
        )
    plan = view_plan(form, who, source)
    plan["delegate_finance_print"] = delivery["delegate_finance_print"]
    choices = []
    for funding in form["funding"]:
        choice = funding.get("department_approver")
        if choice:
            choices.append({"project": funding["code"], **choice})
    if choices:
        plan["department_approvers"] = choices
    validate_plan(plan, form)
    return plan


def load(service, case_id, plan_id):
    from assistant_api import read_json

    if not isinstance(plan_id, str) or not re.fullmatch("[a-f0-9]{32}", plan_id):
        raise APIError("case_ready_plan", "请选择本案原提交准备计划")
    root = location(service, case_id) / "ready" / plan_id
    plan = read_json(root / "plan.json")
    if (
        plan.get("case_id") != case_id
        or digest({k: v for k, v in plan.items() if k != "plan_id"})[:32] != plan_id
    ):
        raise APIError("case_ready_plan", "原准备范围不一致")
    progress = (
        read_json(root / "progress.json")
        if (root / "progress.json").exists()
        else {"state": "planned", "step": 0, "jobs": {}, "rows": []}
    )
    return root, plan, progress


def view(plan, p, authorized):
    state = p["state"]
    if state == "completed" and not any(
        r.get("workflow_started") is True and r.get("independent_http_readback") is True
        for r in p["rows"]
    ):
        state = "needs_review"
    next_action = None
    if state == "completed":
        next_action = {
            "tool": "reimbursement_finance_connection",
            "arguments": {
                "action": "approvals",
                "connection_id": plan["connection_id"],
                "request_id": "ready-approvals-" + plan["plan_id"],
            },
            "label": "读取本人实际待办，逐项确认本人节点",
        }
    elif state not in {"needs_review", "source_changed", "paused"}:
        next_action = {
            "tool": "reimbursement_case_finance",
            "arguments": {
                "case_id": plan["case_id"],
                "action": "ready_continue",
                "plan_id": plan["plan_id"],
                "execute": True,
            },
            "requires_user_confirmation": not authorized,
            "label": "核对范围与学校承诺后继续" if not authorized else "接续原提交准备",
        }
    return {
        "ok": state not in {"needs_review", "source_changed"},
        "schema": "ustc-case-ready-v1",
        "case_id": plan["case_id"],
        "plan_id": plan["plan_id"],
        "state": state,
        "step": p["step"],
        "job_id": p.get("job_id"),
        "rows": p["rows"],
        "form": plan["inputs"]["form"],
        "expected_userid": plan["expected_userid"],
        "promise_text": plan["promise_text"],
        "promise_sha256": plan["promise_sha256"],
        "authorized": authorized,
        "error": p.get("error"),
        "pause_requested": p.get("pause_requested", False),
        "system_result_verified": state == "completed",
        "whole_claim_completed": False,
        "needed_inputs": []
        if authorized
        else [
            "authorization_note：本人核对本单、打印选项、经费单位审批人及所显示学校承诺后的确认说明"
        ],
        "next_action": next_action,
        "note": "本流程准备到学校的申请人确认节点；最终本人同意、短信验证及后续审批分别办理。",
    }


def run(service, case_id, action, job_id, plan_id, note, request_id, execute):
    from assistant_api import atomic_json, read_json
    from material_review_launch import launch_lock

    if action == "ready_status":
        root, plan, p = load(service, case_id, plan_id)
        return view(plan, p, (root / "authority.json").exists())
    if action == "ready_pause":
        root, plan, p = load(service, case_id, plan_id)
        with launch_lock(root / "progress.json"):
            root, plan, p = load(service, case_id, plan_id)
            p["pause_requested"] = True
            if p["step"] in {0, 3, 6, 9} and not p.get("job_id"):
                p["state"] = "paused"
            atomic_json(root / "progress.json", p)
        return view(plan, p, (root / "authority.json").exists())
    active, connection = scoped(service, case_id)
    data = inputs(active, case_id)
    form = data["form"]
    if action == "ready_inspect":
        if execute:
            raise APIError("case_ready_action", "查看学校提交条件不写入财务")
        plan = submission_plan(
            form, connection["expected_userid"], "本案工作台只读核对学校提交条件"
        )
        job = active.enqueue(
            case_id,
            "submission",
            {
                "action": "workflow-inspect",
                "plan": plan,
                "tab_id": connection["tab_id"],
            },
            request_id or "ready-read-" + uuid.uuid4().hex,
        )
        return remember(active, case_id, action, job)
    if action == "ready_plan":
        ref, body, original = output(
            active,
            case_id,
            job_id or prior(active, case_id, "ready_inspect"),
            "submission",
            "workflow-inspect",
        )
        frozen = read_json(
            active.job_folder(case_id, ref["job_id"]) / "native-workflow-input.json"
        )
        from finance_native_create import checked

        if (
            any(
                checked(frozen[key]) != data[key]
                for key in ("form", "case", "invoices")
            )
            or frozen["tab_id"] != connection["tab_id"]
        ):
            raise APIError(
                "case_ready_source", "读取后本案或连接发生变化，请重新核对学校提交条件"
            )
        if (
            body.get("complete") is not True
            or body.get("expected_userid") != connection["expected_userid"]
            or body.get("serial") != form.get("serial")
            or body.get("original", {}).get("bill", {}).get("t.status") != "-99"
            or body.get("native_error_present") is not False
        ):
            raise APIError(
                "case_ready_condition",
                "学校尚有材料错误或本单已进入流程，请查看原校验结果，不重新提交",
            )
        if body.get("required_changes"):
            raise APIError(
                "case_ready_changes",
                "学校现有草稿与本案资料不一致，请先完成原填写计划及独立回读",
            )
        if (
            not isinstance(body.get("promise_text"), str)
            or not body["promise_text"].strip()
            or not re.fullmatch("[a-f0-9]{64}", body.get("promise_sha256", ""))
        ):
            raise APIError(
                "case_ready_promise", "尚未取得学校承诺正文，先用当前版本重新读取"
            )
        plan = {
            "schema": "ustc-case-ready-plan-v1",
            "case_id": case_id,
            "inputs": data,
            "source": ref,
            **{
                k: connection[k]
                for k in ("connection_id", "expected_userid", "tab_id", "namespace")
            },
            "promise_text": body["promise_text"],
            "promise_sha256": body["promise_sha256"],
        }
        plan["plan_id"] = digest(plan)[:32]
        root = location(active, case_id) / "ready" / plan["plan_id"]
        root.mkdir(parents=True, exist_ok=True)
        with launch_lock(root / "plan.json"):
            if not (root / "plan.json").exists():
                atomic_json(root / "plan.json", plan)
        root, plan, p = load(active, case_id, plan["plan_id"])
        return view(plan, p, (root / "authority.json").exists())
    root, plan, p = load(active, case_id, plan_id)
    if action not in {"ready_continue", "ready_recover"} or execute != (
        action == "ready_continue"
    ):
        raise APIError("case_ready_action", "请核对并确认具体提交准备范围")
    with launch_lock(root / "progress.json"):
        root, plan, p = load(active, case_id, plan_id)
        authority = root / "authority.json"
        if not authority.exists():
            if action == "ready_recover":
                raise APIError("case_ready_authority", "只能核查已有明确授权的原任务")
            if not isinstance(note, str) or not note.strip():
                raise APIError(
                    "case_ready_authority", "首次办理须确认所显示的学校承诺及本单范围"
                )
            atomic_json(
                authority,
                {
                    "plan_id": plan_id,
                    "scope_sha256": digest(plan),
                    "note": note,
                    "applicant_agreement_authorized": False,
                },
            )
        auth = read_json(authority)
        if auth.get("scope_sha256") != digest(plan):
            raise APIError("case_ready_authority", "原准备授权范围已变化")
        stable = lambda v: {
            **v,
            "form": {k: x for k, x in v["form"].items() if k != "review"},
        }
        if stable(data) != stable(plan["inputs"]) or any(
            connection[k] != plan[k]
            for k in ("connection_id", "expected_userid", "tab_id", "namespace")
        ):
            p.update(
                state="source_changed",
                error="原单、金额、经费、材料或本人连接已改变，请保留原任务核对",
            )
            atomic_json(root / "progress.json", p)
            return view(plan, p, True)
        native = submission_plan(
            form,
            connection["expected_userid"],
            str(authority)
            + "#sha256="
            + hashlib.sha256(authority.read_bytes()).hexdigest(),
        )
        native["promise_sha256"] = plan["promise_sha256"]
        tab = connection["tab_id"]
        steps = [
            "options",
            "options-read",
            "options-ack",
            "next",
            "next-read",
            "next-ack",
            "start",
            "start-read",
            "start-ack",
        ]
        if p.get("department_required"):
            steps += ["department", "department-read", "department-ack"]
        if p["state"] == "completed":
            return view(plan, p, True)
        if p["step"] >= len(steps):
            raise APIError("case_ready_progress", "原准备进度与逐步结果不一致")
        step = steps[p["step"]]
        if action == "ready_recover":
            base = step.split("-")[0]
            original = p["jobs"].get(base)
            if p["state"] != "needs_review" or not original:
                raise APIError("case_ready_recover", "本步没有可恢复的原保存任务")
            if active.result(case_id, original)["status"] in {"queued", "running"}:
                raise APIError("case_ready_recover", "原保存仍在执行，请等待原任务")
            job = active.enqueue(
                case_id,
                "submission",
                {
                    "action": "options-recover"
                    if base == "options"
                    else "workflow-recover",
                    "plan": native,
                    "tab_id": tab,
                    "job_id": original,
                },
                "ready-recover-" + uuid.uuid4().hex,
            )
            p.update(
                state="running", step=steps.index(base + "-read"), job_id=job["job_id"]
            )
            p["jobs"][base + "-read"] = job["job_id"]
            p["recovered_source"] = base
            p.pop("error", None)
            atomic_json(root / "progress.json", p)
            return view(plan, p, True)
        if p["state"] in {"needs_review", "source_changed"}:
            return view(plan, p, True)
        if p["state"] == "paused":
            p.update(state="running", pause_requested=False)
        if (
            p.get("pause_requested")
            and p["step"] in {0, 3, 6, 9}
            and not p.get("job_id")
        ):
            p["state"] = "paused"
            atomic_json(root / "progress.json", p)
            return view(plan, p, True)
        try:
            if not p.get("job_id"):
                op = "submission"
                params = {"plan": native, "tab_id": tab}
                write = False
                if step.endswith("-ack"):
                    read_step = step.replace("-ack", "-read")
                    ref, observed, _ = output(
                        active, case_id, p["jobs"][read_step], "submission"
                    )
                    outcome = "staged" if step == "next-ack" else "saved"
                    if (
                        step == "start-ack"
                        and observed["original"]["bill"].get("t.node_id") != "508"
                    ):
                        outcome = "staged"
                    op = "record_result"
                    params = {
                        "kind": "ack-write",
                        "job_id": ref["job_id"],
                        "artifact_id": ref["artifact_id"],
                        "outcome": outcome,
                        "note": "本单已确认范围及学校承诺，原操作结束后独立回读；未进行最终本人同意",
                    }
                elif step.endswith("-read"):
                    base = step[:-5]
                    ref, _, _ = output(active, case_id, p["jobs"][base], "submission")
                    params["action"] = (
                        "inspect" if base == "options" else "workflow-readback"
                    )
                    params["job_id"] = ref["job_id"]
                    if base != "options":
                        params["artifact_id"] = ref["artifact_id"]
                elif step in {"start", "department"}:
                    source_step = "next" if step == "start" else "start"
                    source = p["jobs"][
                        source_step + "-read"
                        if p.get("recovered_source") == source_step
                        else source_step
                    ]
                    ref, _, _ = output(active, case_id, source, "submission")
                    _, observed, _ = output(
                        active,
                        case_id,
                        p["jobs"]["next-read" if step == "start" else "start-read"],
                        "submission",
                    )
                    params.update(
                        action="department"
                        if step == "department"
                        or observed["environment"].get("0-buss_var_list")
                        else "promise",
                        job_id=ref["job_id"],
                        artifact_id=ref["artifact_id"],
                    )
                    write = True
                else:
                    params["action"] = step
                    write = True
                job = active.enqueue(
                    case_id, op, params, "caseready-" + plan_id + "-" + step, write
                )
                p.update(state="running", job_id=job["job_id"])
                p["jobs"][step] = job["job_id"]
                atomic_json(root / "progress.json", p)
                return view(plan, p, True)
            if active.result(case_id, p["job_id"])["status"] in {"queued", "running"}:
                return view(plan, p, True)
            ref, body, _ = output(active, case_id, p["job_id"])
            p["rows"].append(
                {
                    "step": step,
                    "source": ref,
                    "state": "returned_not_whole_claim_complete",
                    "workflow_started": body.get("workflow_started") is True,
                    "independent_http_readback": body.get("independent_http_readback")
                    is True,
                }
            )
            if step == "next-ack":
                if p.get("recovered_source") == "next":
                    rec_ref, _, _ = output(
                        active, case_id, p["jobs"]["next-read"], "submission"
                    )
                    envelope = read_json(
                        active.artifact(
                            case_id, rec_ref["job_id"], rec_ref["artifact_id"]
                        )
                    )
                    from finance_native_create import checked

                    saved = checked(envelope["original_save_recovery"])["result"]
                else:
                    _, saved, _ = output(
                        active, case_id, p["jobs"]["next"], "submission"
                    )
                if any(
                    str(v or "").strip() for v in saved.get("messages", {}).values()
                ):
                    raise APIError(
                        "case_ready_native_message",
                        "学校返回需本人核对的信息：" + str(saved["messages"]),
                    )
            if step.endswith("-ack"):
                _, paths = active.resolve(case_id)
                if (paths["state"].parent / "pending_write.json").exists():
                    raise APIError(
                        "case_ready_pending", "原操作核验尚未释放，不能进入下一步"
                    )
            if step == "start-ack" and body.get("workflow_started") is not True:
                p["department_required"] = True
            if (
                step in {"start-ack", "department-ack"}
                and body.get("workflow_started") is True
            ):
                p["state"] = "completed"
            elif step == "department-ack":
                raise APIError(
                    "case_ready_node",
                    "单位审批人步骤仍未独立核到申请人节点，请核对原任务",
                )
            p["step"] += 1
            p.pop("job_id", None)
        except (APIError, ValueError, OSError, KeyError) as error:
            p.update(state="needs_review", error=str(error))
        atomic_json(root / "progress.json", p)
        return view(plan, p, True)
