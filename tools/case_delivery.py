"""本案交付向导：命名由用户确认，导出和原任务恢复仍由正式PDF owner执行。"""

import hashlib
import re
from pathlib import Path

from assistant_contract import APIError
from case_fill import digest, inputs
from case_finance import scoped

ACTIONS = ("inspect", "plan", "export", "status", "resume")


def location(service, case_id):
    service.resolve(case_id)
    from material_intake import reject_links

    root = service.root / ".local/case-delivery" / case_id
    reject_links(root)
    return root


def load(service, case_id, plan_id):
    from assistant_api import read_json

    if not isinstance(plan_id, str) or not re.fullmatch("[a-f0-9]{32}", plan_id):
        raise APIError("delivery_plan", "请选择本活动原交付计划")
    root = location(service, case_id) / plan_id
    plan = read_json(root / "plan.json")
    if (
        plan["case_id"] != case_id
        or digest({k: v for k, v in plan.items() if k != "plan_id"})[:32] != plan_id
    ):
        raise APIError("delivery_plan", "原交付计划内容已改变")
    return (
        root,
        plan,
        read_json(root / "progress.json") if (root / "progress.json").exists() else {},
    )


def status(service, case_id, plan_id):
    root, plan, p = load(service, case_id, plan_id)
    job = p.get("job_id")
    checked = None
    state = "planned"
    if job:
        raw = service.result(case_id, job)
        state = raw["status"]
        if state not in {"queued", "running"}:
            try:
                checked = service.pdf_export_inspect(case_id, job)
            except (ValueError, OSError, KeyError) as error:
                checked = {"ok": False, "error": str(error)}
            state = (
                "verified"
                if checked.get("artifact_readback_verified") is True
                and checked.get("ok") is True
                else "needs_review"
            )
    next_action = None
    if state == "planned":
        next_action = {
            "tool": "reimbursement_case_delivery",
            "arguments": {
                "case_id": case_id,
                "action": "export",
                "plan_id": plan_id,
                "execute": True,
            },
            "requires_user_confirmation": True,
            "label": "确认日期、序号与导出范围",
        }
    elif state in {"queued", "running"}:
        next_action = {
            "tool": "reimbursement_case_delivery",
            "arguments": {"case_id": case_id, "action": "status", "plan_id": plan_id},
            "label": "查看原导出进度",
        }
    return {
        "ok": state != "needs_review",
        "schema": "ustc-case-delivery-v1",
        "case_id": case_id,
        "plan_id": plan_id,
        "state": state,
        "job_id": job,
        "plan": plan["export_plan"],
        "expected_userid": plan["expected_userid"],
        "destination": plan["destination"],
        "verification": checked,
        "next_action": next_action,
        "download_url": "/api/delivery-file?case_id=" + case_id + "&plan_id=" + plan_id
        if state == "verified"
        else None,
        "system_write": False,
        "financial_approval_completed": False,
        "note": "按真实学校单据生成PDF；文件落盘和完整性单独核验。明细PDF与正式打印分别标注，不代表审批或付款完成。",
    }


def run(
    service,
    case_id,
    action="inspect",
    business_date=None,
    sequence=None,
    kind="details",
    include_invoice_attachments=None,
    plan_id=None,
    confirmation_note=None,
    execute=False,
):
    from assistant_api import atomic_json, read_json
    from material_review_launch import launch_lock
    from finance_pdf_export import export_relative_path

    if action not in ACTIONS or type(execute) is not bool:
        raise APIError("delivery_action", "请选择本案交付步骤")
    if action == "inspect":
        if execute:
            raise APIError("delivery_action", "查看交付不执行导出")
        _, paths = service.resolve(case_id)
        form = read_json(paths["form"])
        return {
            "ok": True,
            "schema": "ustc-case-delivery-list-v1",
            "case_id": case_id,
            "serial": form.get("serial"),
            "summary": form.get("basic", {}).get("fields", {}).get("t-abst"),
            "amount": form.get("amount"),
            "plans": [
                status(service, case_id, p.parent.name)
                for p in location(service, case_id).glob("*/plan.json")
            ],
            "system_write": False,
            "needed_inputs": [
                "材料对应的业务日期",
                "本批明确序号",
                "导出明细或正式打印",
                "正式打印是否包括票据附件",
            ],
        }
    if action == "status":
        return status(service, case_id, plan_id)
    active, connection = scoped(service, case_id)
    data = inputs(active, case_id)
    form = data["form"]
    if action == "plan":
        if execute or kind not in {"details", "formal"}:
            raise APIError("delivery_plan", "先选择查看明细或正式打印并预览")
        if kind == "formal" and type(include_invoice_attachments) is not bool:
            raise APIError("delivery_attachments", "请明确正式打印是否包括票据附件")
        if not isinstance(confirmation_note, str) or not confirmation_note.strip():
            raise APIError("delivery_basis", "请说明业务日期和序号的依据")
        native = {
            "schema": "ustc-finance-pdf-export-plan-v1",
            "case_id": case_id,
            "serial": form.get("serial"),
            "amount": form.get("amount"),
            "summary": form.get("basic", {}).get("fields", {}).get("t-abst"),
            "status": "confirmed",
            "business_date": business_date,
            "sequence": sequence,
            "view_kind": "details" if kind == "details" else "formal_print_api",
            "delivery_mode": "rename_only",
            "source_refs": [confirmation_note],
        }
        if kind == "formal":
            native["include_invoice_attachments"] = include_invoice_attachments
        relative = export_relative_path(form, native)
        root = (
            active.material_roots[0]
            / "交付"
            / case_id
            / (
                "明细"
                if kind == "details"
                else "正式打印含附件"
                if include_invoice_attachments
                else "正式打印仅财务"
            )
        )
        plan = {
            "schema": "ustc-case-delivery-plan-v1",
            "case_id": case_id,
            "inputs": data,
            "export_plan": native,
            "export_root": str(root),
            "destination": str(root / relative),
            **{
                k: connection[k]
                for k in ("connection_id", "expected_userid", "tab_id", "namespace")
            },
        }
        plan["plan_id"] = digest(plan)[:32]
        folder = location(active, case_id) / plan["plan_id"]
        folder.mkdir(parents=True, exist_ok=True)
        with launch_lock(folder / "plan.json"):
            if not (folder / "plan.json").exists():
                atomic_json(folder / "plan.json", plan)
        return status(active, case_id, plan["plan_id"])
    if not execute:
        raise APIError(
            "delivery_confirm", "核对导出范围后确认；恢复同样只接续原导出任务"
        )
    folder, plan, p = load(active, case_id, plan_id)
    with launch_lock(folder / "progress.json"):
        folder, plan, p = load(active, case_id, plan_id)
        if inputs(active, case_id) != plan["inputs"] or any(
            connection[k] != plan[k]
            for k in ("connection_id", "expected_userid", "tab_id", "namespace")
        ):
            raise APIError(
                "delivery_source_changed",
                "本案或账号连接已改变，请先核对原计划，不能混入新范围",
            )
        if action == "export" and p.get("job_id"):
            return status(active, case_id, plan_id)
        params = {
            "export_plan": plan["export_plan"],
            "export_root": plan["export_root"],
            "tab_id": plan["tab_id"],
            "expected_userid": plan["expected_userid"],
            "silent": True,
        }
        request_id = "delivery-" + plan_id
        if action == "resume":
            if not p.get("job_id"):
                raise APIError("delivery_resume", "本计划尚未创建原导出任务")
            checked = status(active, case_id, plan_id)
            if checked["state"] != "needs_review":
                return checked
            params["resume_job_id"] = p["job_id"]
            request_id += "-resume-" + p["job_id"]
        from material_intake import reject_links

        target = Path(plan["export_root"])
        reject_links(target)
        if not any(
            target.resolve().is_relative_to(r.resolve()) for r in active.material_roots
        ):
            raise APIError("delivery_output", "交付目录不在已授权资料根内")
        target.mkdir(parents=True, exist_ok=True)
        job = active.enqueue(case_id, "pdf_export", params, request_id, True)
        atomic_json(
            folder / "progress.json",
            {
                "job_id": job["job_id"],
                "original_job_id": p.get("original_job_id") or job["job_id"],
            },
        )
        return status(active, case_id, plan_id)


def download(service, case_id, plan_id):
    value = status(service, case_id, plan_id)
    if value["state"] != "verified":
        raise APIError("delivery_unverified", "文件尚未完成原任务独立核验")
    path = Path(value["destination"])
    from material_intake import reject_links

    reject_links(path)
    if (
        not any(
            path.resolve().is_relative_to(p.resolve()) for p in service.material_roots
        )
        or path.suffix.lower() != ".pdf"
        or path.stat().st_size > 64 * 1024 * 1024
    ):
        raise APIError("delivery_file", "交付文件不在本次已核范围")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != value["verification"].get("pdf_sha256"):
        raise APIError("delivery_changed", "文件内容已改变，请核对原交付回执")
    return path.name, raw
