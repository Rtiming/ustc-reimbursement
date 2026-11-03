"""将当前活动的归档原件、费用关系、补件与原分单器连通；不办理财务。"""

from __future__ import annotations

import copy
import math
import re
from pathlib import Path

from assistant_contract import APIError
from material_review_model import digest, read_json

ACTIONS = (
    "inspect",
    "save_document",
    "save_expenses",
    "remove_expense",
    "save_issue",
    "plan",
    "review_open",
    "save_dispositions",
    "materialize",
    "delivery_plan",
    "delivery_build",
)


def require(condition, message):
    if not condition:
        raise APIError("case_materials", message)


def nonempty(value, label, limit=600):
    require(
        isinstance(value, str) and 0 < len(value.strip()) <= limit,
        label + "须填写有效文字",
    )
    return value.strip()


class SourceCatalog(list):
    """一次请求内复用同一归档回执的原件核验，避免每一费用重复读取整批清单。"""

    def __init__(self):
        super().__init__()
        self.verified = {}


def catalog(service, case_id):
    from intake_review import _source

    rows = SourceCatalog()
    batches = service.intake_review(case_id, action="batches")["batches"]
    for batch in batches:
        if batch["status"] != "completed":
            continue
        job_id = batch["intake_job_id"]
        inventory, _, sources, _ = _source(service, case_id, job_id)
        for row in sources:
            candidates = [
                {"number": number, "page": page["page"]}
                for page in inventory.get("pages", [])
                if page.get("occurrence_id") == row["occurrence_id"]
                for number in page.get("invoice_candidates", [])
            ]
            rows.append(
                {
                    "key": job_id + "/" + row["material_id"],
                    "job_id": job_id,
                    "material_id": row["material_id"],
                    "name": Path(row["filename"]).name,
                    "sha256": row["sha256"],
                    "kind": row["kind"],
                    "pages": row.get("page_count") or 1,
                    "invoice_candidates": candidates,
                    "preview_action": {
                        "tool": "reimbursement_intake_review",
                        "arguments": {
                            "case_id": case_id,
                            "intake_job_id": job_id,
                            "action": "preview",
                            "material_id": row["material_id"],
                            "page": 1,
                        },
                    },
                }
            )
    return rows


def source_ref(service, case_id, selection, sources):
    from intake_review import bound_file

    require(
        isinstance(selection, dict) and set(selection) == {"key", "page"},
        "材料关联须指定本批材料及页码",
    )
    row = next((r for r in sources if r["key"] == selection["key"]), None)
    require(row is not None, "关联材料不属于本活动已归档的原件")
    if row["key"] not in sources.verified:
        sources.verified[row["key"]] = bound_file(
            service, case_id, row["job_id"], row["material_id"]
        )
    path, raw, _, pages, _ = sources.verified[row["key"]]
    require(
        type(selection["page"]) is int and 1 <= selection["page"] <= (pages or 1),
        "材料页码超出原件范围",
    )
    return f"{path}#sha256={raw['sha256']}&page={selection['page']}"


def confirmed(refs, **values):
    return {"status": "confirmed", "source_refs": refs, **values}


def normalized(service, case_id, data, sources):
    """金额与收款分配继续交原分单器核验；关系登记不等于票面或付款真实性验证。"""
    document = data.get("document", {})
    refs = (
        [source_ref(service, case_id, document["source"], sources)]
        if document.get("source")
        else []
    )
    clarification = {}
    if document.get("business_date") and refs:
        clarification["business_date"] = confirmed(
            refs, value=document["business_date"]
        )
    for key in ("responsible_person", "form_filler"):
        if key in document and refs:
            clarification[key] = confirmed(refs, **document[key])
    invoices, expenses = [], []
    for expense in data.get("expenses", []):
        ids, all_refs = [], []
        for row in expense["invoices"]:
            ref = source_ref(service, case_id, row["source"], sources)
            ident = "invoice-" + digest([row["source"], row["number"]])[:20]
            ids.append(ident)
            all_refs.append(ref)
            invoices.append(
                confirmed(
                    [ref],
                    invoice_id=ident,
                    invoice_number=row["number"],
                    face_amount=row["face_amount"],
                )
            )
        evidence = []
        for row in expense.get("amount_evidence", []):
            ref = source_ref(service, case_id, row["source"], sources)
            evidence.append(
                confirmed(
                    [ref],
                    kind=row["kind"],
                    amount=row["amount"],
                    scope_id=expense["expense_id"],
                    amount_origin="document_content",
                )
            )
        payments = []
        for row in expense.get("payees", []):
            ref = source_ref(service, case_id, row["source"], sources)
            payments.append(
                confirmed(
                    [ref], **{k: row[k] for k in ("payee_id", "name", "mode", "amount")}
                )
            )
        extras = {
            key: [
                source_ref(service, case_id, x, sources) for x in expense.get(key, [])
            ]
            for key in ("payment_materials", "itinerary_materials")
        }
        expenses.append(
            confirmed(
                all_refs,
                expense_id=expense["expense_id"],
                scope_id=expense["expense_id"],
                description=expense["description"],
                invoice_ids=ids,
                amount_evidence=evidence,
                payments=payments,
                **extras,
            )
        )
    count = len(invoices)
    first = document.get("first_sequence", 1)
    return {
        "schema": "ustc-reimbursement-batch-input-v1",
        "case_id": case_id,
        "case_context": {},
        "documents": [
            {
                "document_id": case_id,
                "folder_name": document.get("folder_name"),
                "folder_source_ref": refs[0] if refs else None,
                "clarification": clarification,
                "invoices": invoices,
                "expenses": expenses,
                "export_sequences": list(range(first, first + math.ceil(count / 50))),
            }
        ],
    }


def snapshot(service, case_id):
    _, paths = service.resolve(case_id)
    case = read_json(paths["case"])
    data = copy.deepcopy(
        case.get("material_context", {}).get("workbench_materials", {})
    )
    data.setdefault("expenses", [])
    data.setdefault("issues", [])
    return case, data


def inspect(service, case_id, offset=0, limit=50):
    require(
        type(offset) is int
        and offset >= 0
        and type(limit) is int
        and 1 <= limit <= 100,
        "材料分页范围无效",
    )
    case, data = snapshot(service, case_id)
    sources = catalog(service, case_id)
    selection = case.get("material_context", {}).get("standards_selection")
    return {
        "ok": True,
        "schema": "ustc-case-materials-v1",
        "case_id": case_id,
        "title": case.get("description") or case_id,
        "revision": service.revision(case_id),
        "document": data.get("document", {}),
        "dispositions": data.get("dispositions", []),
        "generated_cases": __import__("case_materials_cases").existing(service, case_id),
        "expenses": data["expenses"][offset : offset + limit],
        "total_expenses": len(data["expenses"]),
        "issues": data["issues"],
        "materials": sources[offset : offset + limit],
        "total_materials": len(sources),
        "rules_selected": bool(selection),
        "system_write": False,
        "next_page_action": {
            "tool": "reimbursement_case_materials",
            "arguments": {"case_id": case_id, "offset": offset + limit, "limit": limit},
        }
        if offset + limit < max(len(sources), len(data["expenses"]))
        else None,
        "next_action": {
            "tool": "reimbursement_case_materials",
            "arguments": {"case_id": case_id, "action": "save_document"},
            "needed_inputs": ["values", "expected_revision", "confirmation_note"],
            "label": "核对明白纸与活动资料",
        },
        "note": "逐项保存费用、实际收款与材料关系；缺项不会阻止保存其他项。登记关联尚不代表发票认证、付款核验或学校已保存。",
    }


def validate_document(service, case_id, values, sources):
    require(
        isinstance(values, dict)
        and not set(values)
        - {
            "folder_name",
            "business_date",
            "source",
            "responsible_person",
            "form_filler",
            "first_sequence",
        },
        "活动材料字段无效",
    )
    nonempty(values.get("folder_name"), "原活动文件夹名称")
    source_ref(service, case_id, values.get("source"), sources)
    if values.get("business_date"):
        from datetime import date

        require(
            date.fromisoformat(values["business_date"]).isoformat()
            == values["business_date"],
            "请填写明白纸的真实业务日期",
        )
    require(
        type(values.get("first_sequence", 1)) is int
        and 1 <= values.get("first_sequence", 1) <= 99999,
        "交付起始序号须为正整数",
    )
    fields = {
        "name",
        "blank",
        "student_id",
        "employee_id",
        "phone",
        "wechat",
        "qq",
        "email",
    }
    fields |= {key + "_status" for key in fields - {"name", "blank"}}
    for key in ("responsible_person", "form_filler"):
        if key not in values:
            continue
        row = values[key]
        require(isinstance(row, dict) and not set(row) - fields, "联系人字段无效")
        require(type(row.get("blank", False)) is bool, "空白确认须明确")
        if row.get("blank"):
            require(
                not any(v for k, v in row.items() if k != "blank"),
                "确认空白的联系人不能同时填写其他信息",
            )
        else:
            nonempty(row.get("name"), "联系人姓名", 100)
            for k, v in row.items():
                if k == "blank":
                    continue
                require(isinstance(v, str) and len(v) <= 200, "联系人信息格式无效")
                if k.endswith("_status"):
                    require(
                        v in {"confirmed", "confirmed_blank", "unknown"},
                        "联系字段须据原件确认或保留待核",
                    )
    return copy.deepcopy(values)


def validate_expense(service, case_id, row, sources):
    allowed = {
        "expense_id",
        "description",
        "invoices",
        "amount_evidence",
        "payees",
        "payment_materials",
        "itinerary_materials",
    }
    require(isinstance(row, dict) and not set(row) - allowed, "费用字段无效")
    require(
        isinstance(row.get("expense_id"), str)
        and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", row["expense_id"]),
        "费用编号无效",
    )
    nonempty(row.get("description"), "费用说明")
    require(
        isinstance(row.get("invoices"), list) and 1 <= len(row["invoices"]) <= 100,
        "每项费用需要1至100张明确发票",
    )
    from reimbursement_readiness import amount

    for invoice in row["invoices"]:
        require(
            isinstance(invoice, dict)
            and set(invoice) == {"source", "number", "face_amount"},
            "发票须指定原件、完整票号、票面金额",
        )
        source_ref(service, case_id, invoice["source"], sources)
        require(
            isinstance(invoice["number"], str)
            and re.fullmatch(r"[0-9]{20}", invoice["number"]),
            "普通电子发票须20位票号，其他票种使用专项入口",
        )
        require(amount(invoice["face_amount"], "票面金额") > 0, "票面金额须大于0")
    for key in ("payment_materials", "itinerary_materials"):
        require(
            isinstance(row.get(key, []), list) and len(row.get(key, [])) <= 100,
            "辅助材料关联过多或格式无效",
        )
        for selection in row.get(key, []):
            source_ref(service, case_id, selection, sources)
    for evidence in row.get("amount_evidence", []):
        require(
            isinstance(evidence, dict)
            and set(evidence) == {"source", "kind", "amount"},
            "金额依据须指定材料、类型和金额",
        )
        require(evidence["kind"] in {"request", "list"}, "金额依据类型无效")
        source_ref(service, case_id, evidence["source"], sources)
        require(amount(evidence["amount"], "同范围金额") >= 0, "金额不能为负")
    for payee in row.get("payees", []):
        require(
            isinstance(payee, dict)
            and set(payee) == {"source", "payee_id", "name", "mode", "amount"},
            "实际收款须有材料依据、身份、方式和金额",
        )
        source_ref(service, case_id, payee["source"], sources)
        for key in ("payee_id", "name"):
            nonempty(payee[key], "收款身份", 100)
        require(
            payee["mode"] in {"bank_transfer", "corporate_transfer", "corporate_card"},
            "收款方式无效",
        )
        require(amount(payee["amount"], "收款金额") > 0, "收款金额须大于0")
    return copy.deepcopy(row)


def run(
    service,
    case_id,
    action="inspect",
    values=None,
    expected_revision=None,
    confirmation_note=None,
    execute=False,
    offset=0,
    limit=50,
):
    require(action in ACTIONS and type(execute) is bool, "材料操作无效")
    if action == "materialize":
        from case_materials_cases import run as materialize
        return materialize(service, case_id, values, expected_revision, confirmation_note, execute)
    if action in {"delivery_plan", "delivery_build"}:
        from case_materials_delivery import run as deliver
        require(values is None and (not execute or action == "delivery_build"), "请先预览本地材料交付范围")
        return deliver(service, case_id, execute, expected_revision, confirmation_note)
    if action == "inspect":
        require(not execute and values is None, "查看材料不接受写入内容")
        return inspect(service, case_id, offset, limit)
    revision = service.revision(case_id)
    case, data = snapshot(service, case_id)
    sources = catalog(service, case_id)
    if action == "plan":
        require(not execute and values is None, "分单规划不写财务")
        selection = case.get("material_context", {}).get("standards_selection", {})
        require(selection, "请先在私人规范中为本活动选择适用规则")
        result = service.prepare_batch(
            selection["library_root"],
            selection["profile_id"],
            selection["context"],
            selection["as_of"],
            normalized(service, case_id, data, sources),
        )
        expected_profile = selection.get("profile_sha256")
        if (
            expected_profile
            and result.get("rule_selection", {}).get("profile_sha256")
            != expected_profile
        ):
            raise APIError(
                "rules_changed",
                "已选规则版本发生变化，请先在私人规范中重新核对本活动适用规则",
            )
        require(
            service.revision(case_id) == revision,
            "规划期间活动资料已改变，请重新规划当前版本",
        )
        result.update(
            next_action={
                "tool": "reimbursement_case_materials",
                "arguments": {"case_id": case_id},
                "label": "按具体缺项继续核对本活动材料",
            },
            case_id=case_id,
            local_materials_only=True,
            saved_revision=service.revision(case_id),
        )
        return result
    if action == "review_open":
        from case_materials_review import open_review

        return open_review(service, case_id, case, data, sources, execute, revision)
    require(
        expected_revision == service.revision(case_id),
        "活动资料已改变，请重新打开后保存",
    )
    note = nonempty(confirmation_note, "本次核对依据", 2000)
    if action == "save_document":
        data["document"] = validate_document(service, case_id, values, sources)
    elif action == "save_expenses":
        require(
            isinstance(values, list) and 1 <= len(values) <= 100, "一次保存1至100项费用"
        )
        rows = [validate_expense(service, case_id, row, sources) for row in values]
        ids = [row["expense_id"] for row in rows]
        require(len(set(ids)) == len(ids), "费用编号不能重复")
        replacements = {row["expense_id"]: row for row in rows}
        data["expenses"] = [replacements.pop(row["expense_id"], row) for row in data["expenses"]] + list(replacements.values())
        numbers = [r["number"] for e in data["expenses"] for r in e["invoices"]]
        require(
            len(set(numbers)) == len(numbers),
            "同一发票不能在本活动重复申请；合并付款请关联同一凭证",
        )
    elif action == "remove_expense":
        require(
            isinstance(values, dict) and set(values) == {"expense_id"},
            "请选择移出本次规划的费用",
        )
        require(
            any(r["expense_id"] == values["expense_id"] for r in data["expenses"]),
            "费用不存在",
        )
        data["expenses"] = [
            r for r in data["expenses"] if r["expense_id"] != values["expense_id"]
        ]
    elif action == "save_dispositions":
        require(isinstance(values, list) and 1 <= len(values) <= 2000, "一次核对1至2000页材料去向")
        accepted = []
        for row in values:
            require(isinstance(row, dict) and not set(row) - {"source", "disposition", "reason", "paper_location"}, "材料去向字段无效")
            source_ref(service, case_id, row.get("source"), sources)
            require(row.get("disposition") in {"electronic", "paper", "exclude"}, "请选择电子附件、纸质登记或排除")
            value = copy.deepcopy(row)
            value["reason"] = nonempty(row.get("reason"), "本页去向依据", 2000)
            if row["disposition"] == "paper":
                value["paper_location"] = nonempty(row.get("paper_location"), "请先预览本地材料交付范围", 1000)
            accepted.append(value)
        keys = [digest(row["source"]) for row in accepted]
        require(len(keys) == len(set(keys)), "同一材料页不能重复指定去向")
        data["dispositions"] = [row for row in data.get("dispositions", []) if digest(row["source"]) not in keys] + accepted
    elif action == "save_issue":
        require(
            isinstance(values, dict)
            and not set(values)
            - {"issue_id", "title", "request", "owner", "materials"},
            "补件字段无效",
        )
        require(
            isinstance(values.get("issue_id"), str)
            and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", values["issue_id"]),
            "问题编号无效",
        )
        row = {
            key: nonempty(values.get(key), "补件内容")
            for key in ("issue_id", "title", "request", "owner")
        }
        row["materials"] = values.get("materials", [])
        for selection in row["materials"]:
            source_ref(service, case_id, selection, sources)
        row["basis"] = note
        data["issues"] = [
            r for r in data["issues"] if r["issue_id"] != row["issue_id"]
        ] + [row]
    data["last_confirmation"] = note
    if not execute:
        return {
            "ok": True,
            "preview": True,
            "case_id": case_id,
            "values": values,
            "system_write": False,
        }
    service.update_case(
        case_id,
        "case",
        {"material_context": {"workbench_materials": data}},
        expected_revision,
    )
    return {**inspect(service, case_id, offset, limit), "saved": True}
