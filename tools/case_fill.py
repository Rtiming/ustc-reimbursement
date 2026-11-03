"""可断线接续的填单编排：每次推进一个原任务，跨入口共用范围和进度。"""

import hashlib
import json
import re

from assistant_contract import APIError
from case_finance import location, output, scoped


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def inputs(service, case_id):
    from assistant_api import read_json

    _, paths = service.resolve(case_id)
    return {name: read_json(paths[name]) for name in ("form", "invoices", "case")}


def load(service, case_id, plan_id):
    from assistant_api import read_json

    if not isinstance(plan_id, str) or not re.fullmatch("[a-f0-9]{32}", plan_id):
        raise APIError("case_fill_plan", "请选择本案已核范围的原填单计划")
    root = location(service, case_id) / "fill" / plan_id
    plan = read_json(root / "plan.json")
    if plan.get("case_id") != case_id or plan.get("plan_id") != plan_id:
        raise APIError("case_fill_plan", "填单计划不属于本案")
    core = {k: v for k, v in plan.items() if k != "plan_id"}
    if digest(core)[:32] != plan_id:
        raise APIError("case_fill_plan", "填单范围已改变，不能继续原计划")
    progress = (
        read_json(root / "progress.json")
        if (root / "progress.json").exists()
        else {
            "state": "planned",
            "index": 0,
            "phase": "prepare",
            "rows": [],
            "jobs": {},
        }
    )
    return root, plan, progress


def view(plan, progress, authority=False):
    state = progress["state"]
    inconsistent = state == 'completed' and (
        [r.get('stage') for r in progress['rows']] != plan['stages']
        or any(r.get('state') not in {'verified','verified_unchanged'} for r in progress['rows']))
    if inconsistent: state='needs_review'
    return {
        "ok": state not in {"needs_review", "source_changed"},
        "schema": "ustc-case-fill-v1",
        "case_id": plan["case_id"],
        "plan_id": plan["plan_id"],
        "state": state,
        "serial": plan["serial"],
        "expected_userid": plan["expected_userid"],
        "form": plan["inputs"]["form"],
        "stages": plan["stages"],
        "rows": progress["rows"],
        "index": progress["index"],
        "phase": progress["phase"],
        "job_id": progress.get("job_id"),
        "error": '汇总与逐域核验不一致，请核对原记录' if inconsistent else progress.get("error"),
        "pause_requested": progress.get("pause_requested", False),
        "authorized": authority,
        "needed_inputs": [] if authority else ['authorization_note：用户对本单号、金额、经费和实际收款范围的确认说明'],
        "system_result_verified": state == "completed",
        "whole_claim_completed": False,
        "next_action": None
        if state in {"completed", "needs_review", "source_changed", "paused"}
        else {
            "tool": "reimbursement_case_finance",
            "arguments": {
                "case_id": plan["case_id"],
                "action": "fill_continue",
                "plan_id": plan["plan_id"],
                "execute": True,
            },
            "label": "核对范围后开始填写" if not authority else "接续原填写步骤",
            "requires_user_confirmation": not authority,
        },
        "note": "保存后逐域独立回读；本计划只填写草稿，不提交本人审批或发送验证码。",
    }


def run(service, case_id, action, plan_id, authorization_note, execute):
    from assistant_api import atomic_json
    from material_review_launch import launch_lock

    if action == "fill_plan":
        active, connection = scoped(service, case_id)
        data = inputs(active, case_id)
        form = data["form"]
        from validate_config import validate, validate_basic_fields

        validate(form)
        validate_basic_fields(form['basic'])
        if not form.get("serial") or form.get("system_mode", "daily") != "daily":
            raise APIError("case_fill_serial", "先完成本案日常报销建单及原单号绑定")
        if active.active_jobs(case_id):
            raise APIError("case_busy", "先查看本案原任务")
        stages = (
            ["funding", "basic"]
            + (["categories"] if form.get("expense_categories") else [])
            + ["expenses", "payments"]
        )
        stages += (["notes"] if form.get("explanations") else []) + (
            ["pre-review"] if form.get("pre_review") else []
        )
        plan = {
            "schema": "ustc-case-fill-plan-v1",
            "case_id": case_id,
            "serial": form["serial"],
            "expected_userid": connection["expected_userid"],
            "connection_id": connection["connection_id"],
            "tab_id": connection["tab_id"],
            "namespace": connection["namespace"],
            "inputs": data,
            "stages": stages,
        }
        plan["plan_id"] = digest(plan)[:32]
        root = location(service, case_id) / "fill" / plan["plan_id"]
        root.mkdir(parents=True, exist_ok=True)
        with launch_lock(root / "plan.json"):
            if not (root / "plan.json").exists():
                atomic_json(root / "plan.json", plan)
        root, plan, progress = load(service, case_id, plan["plan_id"])
        return view(plan, progress, (root / "authority.json").exists())
    root, plan, progress = load(service, case_id, plan_id)
    if action == "fill_status":
        return view(plan, progress, (root / "authority.json").exists())
    if action == "fill_recover":
        if execute:
            raise APIError("case_fill_recover", "恢复仅回读原操作，不重新保存")
        with launch_lock(root / "progress.json"):
            root, plan, progress = load(service, case_id, plan_id)
            active, connection = scoped(service, case_id)
            if (
                any(
                    connection[k] != plan[k]
                    for k in ("connection_id", "expected_userid", "tab_id", "namespace")
                )
                or inputs(active, case_id) != plan["inputs"]
            ):
                raise APIError(
                    "case_fill_recover", "原账号窗口或填报范围已改变，请保留原任务核对"
                )
            if (
                progress["state"] != "needs_review"
                or not (root / "authority.json").exists()
            ):
                raise APIError("case_fill_recover", "仅恢复已授权且中断的原填写步骤")
            original = progress["jobs"].get(str(progress["index"]) + "-save")
            if not original:
                raise APIError(
                    "case_fill_recover", "本步尚无保存意图，请先核对原读取错误"
                )
            result = active.result(case_id, original)
            rows = [
                a for a in result.get("artifacts", []) if a["name"] == "output.json"
            ]
            if result["status"] in {"queued", "running"} or len(rows) != 1:
                raise APIError(
                    "case_fill_recover", "原保存仍在执行或缺少恢复回执，请查看原任务"
                )
            import uuid

            job = active.enqueue(
                case_id,
                "read_system",
                {
                    "mode": "recover",
                    "tab_id": connection["tab_id"],
                    "job_id": original,
                    "artifact_id": rows[0]["artifact_id"],
                },
                "casefill-recover-" + uuid.uuid4().hex,
            )
            progress.update(state="running", phase="readback", job_id=job["job_id"])
            progress.pop("error", None)
            progress["jobs"][str(progress["index"]) + "-readback"] = job["job_id"]
            atomic_json(root / "progress.json", progress)
            return view(plan, progress, True)
    if action == "fill_pause":
        with launch_lock(root / "progress.json"):
            root, plan, progress = load(service, case_id, plan_id)
            progress["pause_requested"] = True
            if progress["phase"] == "prepare" and not progress.get("job_id"):
                progress["state"] = "paused"
            atomic_json(root / "progress.json", progress)
        return view(plan, progress, (root / "authority.json").exists())
    if action != "fill_continue" or not execute:
        raise APIError("case_fill_action", "核对本次范围后才能接续填单")
    with launch_lock(root / "progress.json"):
        root, plan, progress = load(service, case_id, plan_id)
        authorized = (root / "authority.json").exists()
        if authorized:
            from assistant_api import read_json
            authority=read_json(root/'authority.json')
            if authority.get('plan_id')!=plan_id or authority.get('scope_sha256')!=digest(plan):
                raise APIError('case_fill_authority','原范围确认记录已改变，未继续派发')
        if not authorized:
            if (
                not isinstance(authorization_note, str)
                or not authorization_note.strip()
            ):
                raise APIError(
                    "case_fill_authority",
                    "首次填写须确认本计划单号、金额、经费和收款安排",
                )
            atomic_json(
                root / "authority.json",
                {
                    "plan_id": plan_id,
                    "scope_sha256": digest(plan),
                    "note": authorization_note,
                },
            )
        if progress["state"] in {"completed", "needs_review", "source_changed"}:
            return view(plan, progress, True)
        if progress["state"] == "paused":
            progress.update(state="running", pause_requested=False)
        active, connection = scoped(service, case_id)
        if (
            any(
                connection[k] != plan[k]
                for k in ("connection_id", "expected_userid", "tab_id", "namespace")
            )
            or inputs(active, case_id) != plan["inputs"]
        ):
            progress.update(
                state="source_changed",
                error="本案范围或本人连接已改变，请核对原任务；未写入变更后的内容。",
            )
            atomic_json(root / "progress.json", progress)
            return view(plan, progress, True)
        if progress.get("pause_requested"):
            if progress["phase"] == "prepare" and not progress.get("job_id"):
                progress["state"] = "paused"
                atomic_json(root / "progress.json", progress)
                return view(plan, progress, True)
        stage = plan["stages"][progress["index"]]
        phase = progress["phase"]
        key = str(progress["index"]) + "-" + phase
        tab = connection["tab_id"]
        who = connection["expected_userid"]
        try:
            if not progress.get("job_id"):
                if phase == "prepare":
                    op = "business"
                    params = {
                        "action": stage + "-prepare",
                        "tab_id": tab,
                        "expected_userid": who,
                    }
                    write = False
                elif phase == "save":
                    op = "stage"
                    params = {"stage": stage, "tab_id": tab}
                    write = True
                elif phase == "readback":
                    from finance_native_stage import READ_DOMAINS

                    _, prepared, _ = output(
                        active,
                        case_id,
                        progress["jobs"][str(progress["index"]) + "-prepare"],
                        "business",
                        stage + "-prepare",
                    )
                    own = (
                        stage == "categories"
                        or stage in {"funding", "expenses"}
                        and bool(prepared.get("original", {}).get("implicit_default"))
                    )
                    op = "business"
                    params = {
                        "action": stage + "-prepare" if own else "bill-read",
                        "tab_id": tab,
                        "expected_userid": who,
                    }
                    if not own:
                        params["domain"] = READ_DOMAINS[stage]
                    write = False
                else:
                    ref, _, reader = output(
                        active,
                        case_id,
                        progress["jobs"][str(progress["index"]) + "-readback"],
                    )
                    if reader["operation"] not in {"business", "read_system"}:
                        raise APIError(
                            "case_fill_ack", "核验来源须为原保存之后的正式读取"
                        )
                    op = "record_result"
                    params = {
                        "kind": "ack-write",
                        "job_id": ref["job_id"],
                        "artifact_id": ref["artifact_id"],
                        "outcome": "saved",
                        "note": "本案向导按原保存完成后的同单独立HTTP回读逐域核验",
                    }
                    write = False
                job = active.enqueue(
                    case_id, op, params, "casefill-" + plan_id + "-" + key, write
                )
                progress.update(job_id=job["job_id"], state="running")
                progress["jobs"][key] = job["job_id"]
                atomic_json(root / "progress.json", progress)
                return view(plan, progress, True)
            result = active.result(case_id, progress["job_id"])
            if result["status"] in {"queued", "running"}:
                return view(plan, progress, True)
            ref, body, request = output(active, case_id, progress["job_id"])
            if phase == "prepare":
                from finance_native_stage import (
                    validate_plan,
                    stage_contract,
                    contract_key,
                    stage_values,
                )

                c = {
                    "case_id": case_id,
                    "serial": plan["serial"],
                    "expected_userid": who,
                    "stage": stage,
                    contract_key(stage): stage_contract(stage),
                    **stage_values(plan["inputs"]["form"], stage),
                }
                validate_plan(c, body)
                from finance_registered_native import no_change

                unchanged = no_change(stage, body)
                progress["rows"].append(
                    {
                        "stage": stage,
                        "changes": body.get("changes"),
                        "state": "verified_unchanged" if unchanged else "prepared",
                        "source": ref,
                    }
                )
                progress["phase"] = "prepare" if unchanged else "save"
                if unchanged:
                    progress["index"] += 1
            elif phase == "save":
                progress["rows"][-1].update(state="saved_pending_readback", save=ref)
                progress["phase"] = "readback"
            elif phase == "readback":
                progress["rows"][-1]["readback"] = ref
                progress["phase"] = "ack"
            else:
                from assistant_api import read_json

                _, paths = active.resolve(case_id)
                if (
                    body.get("ok") is not True
                    or (paths["state"].parent / "pending_write.json").exists()
                    or stage
                    not in read_json(paths["state"]).get("operator_verified_stages", [])
                ):
                    raise APIError("case_fill_ack", "原保存尚未通过独立核验")
                progress["rows"][-1].update(state="verified", acknowledgement=ref)
                progress["index"] += 1
                progress["phase"] = "prepare"
            progress.pop("job_id", None)
            if progress["index"] == len(plan["stages"]):
                progress["state"] = "completed"
            elif progress.get("pause_requested") and progress["phase"] == "prepare":
                progress["state"] = "paused"
        except (APIError, ValueError, OSError, KeyError) as error:
            progress.update(state="needs_review", error=str(error))
        atomic_json(root / "progress.json", progress)
        return view(plan, progress, True)
