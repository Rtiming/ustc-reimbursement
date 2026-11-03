"""从当前案件生成原材料反馈工作台快照，保留既有答复事件。"""

from pathlib import Path
from material_review_model import atomic_json, digest, validate, SCHEMA
from case_materials import source_ref, require


def open_review(service, case_id, case, data, sources, execute, expected_revision):
    from intake_review import bound_file
    from material_review_service import run as review_service
    from material_review_model import now

    root = Path(service.material_roots[0]) / "材料核对与补件" / case_id
    if not execute:
        return {
            "ok": True,
            "case_id": case_id,
            "workspace": str(root),
            "system_write": False,
            "preview": True,
        }
    item = {
        "id": case_id,
        "title": case.get("description") or case_id,
        "club": "本活动",
        "material_type": "归档材料",
        "face_total": None,
        "paper_total": None,
        "claim_total": None,
        "bill_number": case.get("serial"),
        "finance_status": "待学校回读",
        "conclusion": "材料关系登记",
        "next_action": "逐项核对与补件",
        "queue": "材料核对",
        "asset_ids": [],
        "paper_ids": [],
        "expense_ids": [],
        "issue_ids": [],
        "history": [],
        "source": "本活动已归档原件",
    }
    result = {
        "schema": SCHEMA,
        "title": item["title"],
        "batch_id": case_id,
        "roots": [str(p) for p in service.material_roots],
        "sources": {},
        "items": {case_id: item},
        "assets": {},
        "expenses": {},
        "issues": {},
        "warnings": [],
        "resolved_prior": [],
        "system_write": False,
    }
    lookup = {}
    for row in sources:
        path, raw, _, pages, _ = bound_file(
            service, case_id, row["job_id"], row["material_id"]
        )
        aid = raw["sha256"]
        lookup[row["key"]] = aid
        result["assets"][aid] = {
            "id": aid,
            "sha256": aid,
            "path": str(path),
            "label": row["name"],
            "suffix": path.suffix.lower(),
            "kind": "辅助材料",
            "pages": pages or 1,
            "item_ids": [case_id],
            "origins": [],
            "invoice_numbers": [],
        }
        if aid not in item["asset_ids"]:
            item["asset_ids"].append(aid)

    def visual(selection):
        source_ref(service, case_id, selection, sources)
        return {"asset_id": lookup[selection["key"]], "page": selection["page"]}

    if data.get("document", {}).get("source"):
        aid = visual(data["document"]["source"])["asset_id"]
        result["assets"][aid]["kind"] = "明白纸"
        item["paper_ids"].append(aid)
    for expense in data["expenses"]:
        refs = []
        for invoice in expense["invoices"]:
            ref = visual(invoice["source"])
            refs.append(ref)
            asset = result["assets"][ref["asset_id"]]
            asset["kind"] = "发票"
            if invoice["number"] not in asset["invoice_numbers"]:
                asset["invoice_numbers"].append(invoice["number"])
        for key, label in (
            ("payment_materials", "付款凭证"),
            ("itinerary_materials", "行程材料"),
        ):
            for selection in expense.get(key, []):
                ref = visual(selection)
                refs.append(ref)
                asset = result["assets"][ref["asset_id"]]
                asset["kind"] = (
                    label if asset["kind"] == "辅助材料" else "混合材料（按关联页核对）"
                )
        eid = expense["expense_id"]
        result["expenses"][eid] = {
            "id": eid,
            "item_id": case_id,
            "label": expense["description"],
            "refs": refs,
            "invoice_numbers": [x["number"] for x in expense["invoices"]],
            "status": "待独立核验",
            "payments": expense.get("payees", []),
            "association": "用户按原件明确登记",
        }
        item["expense_ids"].append(eid)
    for issue in data["issues"]:
        row = {
            "id": issue["issue_id"],
            "item_id": case_id,
            "title": issue["title"],
            "state": "待答复",
            "club": "本活动",
            "queue": "补件",
            "owner": issue["owner"],
            "known": issue["basis"],
            "request": issue["request"],
            "requirements": [issue["request"]],
            "targets": [],
            "refs": [visual(x) for x in issue.get("materials", [])],
            "amount": None,
            "amount_scope": "未推定",
            "status": "pending",
            "sendable": True,
            "tone": "amber",
        }
        row["fingerprint"] = digest(row)
        result["issues"][row["id"]] = row
        item["issue_ids"].append(row["id"])
    result["counts"] = {
        key: len(result[key]) for key in ("items", "issues", "assets", "expenses")
    }
    validate(result)
    version = digest(result)
    target = root / "versions" / (version + ".json")
    with service.mutex(case_id):
        require(
            service.revision(case_id) == expected_revision,
            "材料已改变，请按当前版本重新打开补件台",
        )
        if not target.exists():
            atomic_json(target, result, exclusive=True)
        atomic_json(
            root / "current.json",
            {
                "schema": "ustc-material-review-current-v1",
                "version": version,
                "config": "case_materials",
                "updated_at": now(),
            },
        )
    registered = review_service(service, "register", str(root))
    launched = review_service(service, "start", str(root), port=0)
    return {**registered, **launched, "case_id": case_id, "system_write": False}
