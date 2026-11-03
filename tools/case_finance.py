"""本案财务向导：自动携带已核连接与原任务引用，业务仍由正式owner执行。"""

from pathlib import Path
import hashlib
import re
import uuid

from assistant_contract import APIError

ACTIONS = (
    "inspect",
    "bind",
    "source_audit",
    "pool",
    "selection_preview",
    "selection_apply",
    "upload",
    "create",
    "bind_created",
    "fill_plan",
    "fill_continue",
    "fill_pause",
    "fill_status",
    "fill_recover",
    "ready_inspect",
    "ready_plan",
    "ready_continue",
    "ready_status",
    "ready_pause",
    "ready_recover",
)


def location(service, case_id):
    service.resolve(case_id)
    path = service.root / ".local/case-finance" / case_id
    from material_intake import reject_links

    reject_links(path)
    return path


def record(service, case_id):
    from assistant_api import read_json

    path = location(service, case_id) / "connection.json"
    return read_json(path) if path.exists() else None


def scoped(service, case_id):
    from assistant_api import AssistantService, read_json
    from finance_connection import bound

    saved = record(service, case_id)
    if not saved:
        raise APIError("case_connection_missing", "先为本活动选择已核实的本人学校账号")
    connection, context = bound(service, saved["connection_id"])
    form = read_json(service.resolve(case_id)[1]["form"])
    if (
        connection["expected_userid"] != saved["expected_userid"]
        or form.get("expected_userid") != saved["expected_userid"]
    ):
        raise APIError(
            "case_connection_identity",
            "本案填报账号与所选本人连接不同，请先核对；未开始办理",
        )
    return AssistantService(
        service.root, service.material_roots, service.python, context
    ), connection


def output(service, case_id, job_id, operation=None, action=None):
    """只从正式同案任务取完整产物，不能把未知/失败的退出当作业务结果。"""
    from assistant_api import read_json

    result = service.result(case_id, job_id)
    request = read_json(service.job_folder(case_id, job_id) / "request.json")
    if (
        operation
        and request["operation"] != operation
        or action
        and request["parameters"].get("action") != action
    ):
        raise APIError("case_finance_source", "来源任务与本步操作不符")
    rows = [a for a in result.get("artifacts", []) if a["name"] == "output.json"]
    if (
        result["status"] != "completed"
        or result.get("exit_code") != 0
        or len(rows) != 1
    ):
        raise APIError("case_finance_wait", "原任务尚未完整成功，请查看原任务；未重发")
    path = service.artifact(case_id, job_id, rows[0]["artifact_id"])
    envelope = read_json(path)
    body = envelope.get("result", envelope)
    if not isinstance(body, dict) or body.get("ok") is False:
        raise APIError("case_finance_source", "原任务仍有未解决项，请核对原结果")
    return (
        {
            "job_id": job_id,
            "artifact_id": rows[0]["artifact_id"],
            "sha256": rows[0]["sha256"],
        },
        body,
        request,
    )


def remember(service, case_id, name, job):
    from assistant_api import atomic_json

    folder = location(service, case_id) / "jobs"
    folder.mkdir(parents=True, exist_ok=True)
    atomic_json(folder / (name + ".json"), {"action": name, "job_id": job["job_id"]})
    return {
        **job,
        "case_id": case_id,
        "workflow_action": name,
        "next_action": {
            "tool": "reimbursement_task_view",
            "arguments": {"case_id": case_id, "job_id": job["job_id"]},
        },
    }


def prior(service, case_id, name):
    from assistant_api import read_json

    path = location(service, case_id) / "jobs" / (name + ".json")
    if not path.exists():
        raise APIError("case_finance_source", "请先完成本案的" + name + "步骤")
    return read_json(path)["job_id"]


def inspect(service, case_id):
    from assistant_api import read_json

    _, paths = service.resolve(case_id)
    form = read_json(paths["form"])
    invoices = read_json(paths["invoices"])
    jobs = []
    for path in sorted((location(service, case_id) / "jobs").glob("*.json")):
        saved = read_json(path)
        result = service.result(case_id, saved["job_id"])
        jobs.append(
            {**saved, "status": result["status"], "exit_code": result.get("exit_code")}
        )
    connection = record(service, case_id)
    selection = None
    preview = next(
        (
            j
            for j in jobs
            if j["action"] == "selection_preview" and j["status"] == "completed"
        ),
        None,
    )
    if preview:
        ref, body, _ = output(
            service, case_id, preview["job_id"], "invoice_selection", "preview"
        )
        from invoice_selection import value as native_value, numbers

        selection = {
            "source": ref,
            "rows": [
                {
                    "number": "、".join(sorted(numbers(r["native_row"]))),
                    "amount": native_value(r["native_row"], "amount"),
                    "seller": native_value(r["native_row"], "seller"),
                }
                for r in body.get("bindings", {}).values()
            ],
            "before_count": len(body.get("before_guids", [])),
            "after_count": len(body.get("after_guids", [])),
        }
    return {
        "ok": True,
        "schema": "ustc-case-finance-v1",
        "case_id": case_id,
        "revision": service.revision(case_id),
        "connection": connection,
        "selection_plan": selection,
        "connections": service.finance_connection()["connections"],
        "serial": form.get("serial"),
        "summary": form.get("basic", {}).get("fields", {}).get("t-abst"),
        "amount": form.get("amount"),
        "originals": [
            {
                "index": i,
                "name": Path(r["path"]).name,
                "sha256": r["sha256"],
                "invoice_numbers": r.get("invoice_number_candidates", []),
                "reviewed": r.get("visual_check") == "verified",
            }
            for i, r in enumerate(invoices.get("original_files", []))
        ],
        "selected": invoices.get("selected_invoices", []),
        "jobs": jobs,
        "active_jobs": service.active_jobs(case_id),
        "pending_write": read_json(paths["state"].parent / "pending_write.json")
        if (paths["state"].parent / "pending_write.json").exists()
        else None,
        "fill_plans": [
            read_json(p)["plan_id"]
            for p in (location(service, case_id) / "fill").glob("*/plan.json")
        ],
        "ready_plans": [
            read_json(p)["plan_id"]
            for p in (location(service, case_id) / "ready").glob("*/plan.json")
        ],
        "system_write": False,
        "business_transport": "native_http",
        "note": "每次使用已核账号连接，学校仍逐次核对身份。选票、建单、保存、本人确认和付款分别验收。",
    }


def run(
    service,
    case_id,
    action="inspect",
    connection_id=None,
    job_id=None,
    invoice_numbers=None,
    start=0,
    count=1,
    plan_id=None,
    authorization_note=None,
    request_id=None,
    execute=False,
):
    from assistant_api import atomic_json, read_json

    if action not in ACTIONS or type(execute) is not bool:
        raise APIError("case_finance_action", "请选择本案财务向导中的固定步骤")
    _, paths = service.resolve(case_id)
    if action == "inspect":
        if execute:
            raise APIError("case_finance_action", "查看不执行财务")
        return inspect(service, case_id)
    if action.startswith("fill_"):
        from case_fill import run as fill

        return fill(service, case_id, action, plan_id, authorization_note, execute)
    if action.startswith("ready_"):
        from case_submit import run as ready

        return ready(
            service,
            case_id,
            action,
            job_id,
            plan_id,
            authorization_note,
            request_id,
            execute,
        )
    if action == "bind":
        from finance_connection import bound

        connection, _ = bound(service, connection_id)
        form = read_json(paths["form"])
        if form.get("expected_userid") not in (None, "", connection["expected_userid"]):
            raise APIError(
                "case_connection_identity",
                "本案已登记另一填报账号，请核对原案身份，不能自动改账号",
            )
        if (
            service.active_jobs(case_id)
            or (paths["state"].parent / "pending_write.json").exists()
        ):
            raise APIError("case_connection_busy", "先核对本案原任务，再更改连接")
        data = {
            k: connection[k]
            for k in ("connection_id", "expected_userid", "verified_at")
        }
        if execute:
            # 账号是受保护字段：只能从完整原生身份核验绑定，不能放开普通update_case。
            with service.mutex(case_id):
                current = read_json(paths["form"])
                if (
                    service.active_jobs(case_id)
                    or (paths["state"].parent / "pending_write.json").exists()
                    or current.get("expected_userid")
                    not in (None, "", connection["expected_userid"])
                ):
                    raise APIError(
                        "case_connection_changed", "绑定期间本案已变化，请刷新后核对"
                    )
                receipt = {
                    **data,
                    "case_id": case_id,
                    "source_job_id": connection.get("source_job_id"),
                    "source_sha256": connection.get("source_sha256"),
                    "before_form_sha256": hashlib.sha256(
                        paths["form"].read_bytes()
                    ).hexdigest(),
                }
                atomic_json(
                    location(service, case_id)
                    / "bindings"
                    / (uuid.uuid4().hex + ".json"),
                    receipt,
                )
                if current.get("expected_userid") != connection["expected_userid"]:
                    atomic_json(
                        paths["form"],
                        {**current, "expected_userid": connection["expected_userid"]},
                    )
                atomic_json(location(service, case_id) / "connection.json", data)
            return {**inspect(service, case_id), "saved": True}
        return {
            "ok": True,
            "case_id": case_id,
            "connection": data,
            "saved": execute,
            "system_write": False,
            "next_action": {
                "tool": "reimbursement_case_finance",
                "arguments": {"case_id": case_id},
            },
        }
    active, connection = scoped(service, case_id)
    tab, who = connection["tab_id"], connection["expected_userid"]
    request_id = request_id or "case-finance-" + uuid.uuid4().hex
    if action in {"source_audit", "pool", "selection_preview"} and execute:
        raise APIError("case_finance_action", "本步是只读核对，不接受执行开关")
    if action == "source_audit":
        result = active.invoice_sources(case_id, "audit", request_id, invoice_numbers)
    elif action == "pool":
        result = active.business(case_id, "invoice-inventory", tab, who, request_id)
    elif action == "selection_preview":
        ref, body, _ = output(
            active,
            case_id,
            job_id or prior(active, case_id, "source_audit"),
            "invoice_sources",
            "audit",
        )
        originals = body.get("source_manifest", {}).get("original_files", [])
        if not isinstance(invoice_numbers, list) or not 1 <= len(invoice_numbers) <= 20:
            raise APIError(
                "case_finance_selection",
                "请勾选本次1至20张原票，可分批追加；不选择其他活动票据",
            )
        selectors = []
        for number in invoice_numbers:
            matches = [
                r for r in originals if number in r.get("invoice_number_candidates", [])
            ]
            if len(matches) != 1:
                raise APIError(
                    "case_finance_selection",
                    "票号未唯一对应本案已核原件：" + str(number),
                )
            selector = {"invoice_number": number}
            if re.fullmatch(r"[0-9]{10}", number):
                selector["invoice_code"] = matches[0].get("invoice_code", "")
            selectors.append(selector)
        result = active.invoice_selection(
            case_id,
            "preview",
            tab,
            request_id,
            source_job_id=ref["job_id"],
            source_artifact_id=ref["artifact_id"],
            audit_refs=[{k: ref[k] for k in ("job_id", "artifact_id")}],
            selectors=selectors,
            mode="append",
            grid="53",
            expected_userid=who,
        )
    elif action == "selection_apply":
        ref, _, _ = output(
            active,
            case_id,
            job_id or prior(active, case_id, "selection_preview"),
            "invoice_selection",
            "preview",
        )
        if not execute:
            return {"ok": True, "preview": True, "source": ref, "system_write": False}
        result = active.invoice_selection(
            case_id,
            "apply",
            tab,
            request_id,
            True,
            preview_job_id=ref["job_id"],
            preview_artifact_id=ref["artifact_id"],
        )
    elif action in {"upload", "create"}:
        parameters = {
            "action": "upload" if action == "upload" else "create-draft",
            "tab_id": tab,
        }
        if action == "upload":
            parameters.update(start=start, count=count)
        result = active.enqueue(case_id, "invoice", parameters, request_id, execute)
    else:
        ref, body, _ = output(
            active,
            case_id,
            job_id or prior(active, case_id, "create"),
            "invoice",
            "create-draft",
        )
        serial = body.get("verification", {}).get("serial")
        if (
            body.get("draft_created") is not True
            or not isinstance(serial, str)
            or not re.fullmatch(r"\d+", serial)
        ):
            raise APIError(
                "case_finance_created",
                "原建单尚未完整回读，须恢复原任务，不能填写或猜测新单号",
            )
        if not execute:
            return {
                "ok": True,
                "preview": True,
                "serial": serial,
                "source": ref,
                "system_write": False,
            }
        result = active.enqueue(
            case_id,
            "record_result",
            {
                "kind": "bind-serial",
                "job_id": ref["job_id"],
                "artifact_id": ref["artifact_id"],
                "serial": serial,
                "note": "按原建单完整独立回读绑定本案单号",
            },
            request_id,
        )
    return remember(active, case_id, action, result)
