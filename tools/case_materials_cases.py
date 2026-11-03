"""确认本活动分单后登记可续办子案；本地生成不创建学校单据。"""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path

from assistant_contract import APIError
from material_review_model import digest, read_json


def require(value, message):
    if not value:
        raise APIError("materialized_cases", message)


def folder(service, case_id):
    from material_intake import reject_links
    path = service.root / ".local/materialized-cases" / case_id
    reject_links(path)
    return path


def existing(service, case_id):
    path = folder(service, case_id) / "receipt.json"
    if not path.exists():
        return None
    value = read_json(path)
    require(value.get("parent_case_id") == case_id, "原分单回执不属于本活动")
    return value


def describe(service, receipt, replayed=False):
    cases = []
    for row in receipt["cases"]:
        child = row["case_id"]
        _, paths = service.resolve(child)
        case, form = read_json(paths["case"]), read_json(paths["form"])
        require(case.get("material_context", {}).get("materialized_from", {}).get("plan_sha256") == row.get("plan_sha256", receipt["plan_sha256"]), "子案来源与原确认分单不符，保留现有记录")
        cases.append({**row, "serial": form.get("serial"), "revision": service.revision(child),
                      "next_action": {"tool": "reimbursement_case_journey", "arguments": {"case_id": child}, "label": "继续本单核对和填报"}})
    return {"ok": True, "schema": "ustc-materialized-cases-v1", "parent_case_id": receipt["parent_case_id"],
            "plan_sha256": receipt["plan_sha256"], "cases": cases, "held_forms": receipt.get("held_forms", []),
            "replayed": replayed, "system_write": False, "school_documents_created": False,
            "next_action": cases[0]["next_action"] if cases else None,
            "note": "已登记本地分单。原件与申请范围保留来源；逐票审核、学校账号、经费及收款核验后才可办理学校单据。"}


def proposal(service, case_id):
    from case_materials import catalog, normalized, snapshot
    case, data = snapshot(service, case_id)
    sources = catalog(service, case_id)
    normalized_input = normalized(service, case_id, data, sources)
    result = service.case_materials(case_id, "plan")
    plan = result.get("plan")
    require(plan is not None, "先按本活动规则完成分单预览")
    require(plan["structure_readiness"]["status"] == "ready", "票据归属或分单结构有待核项，请先处理")
    ready, held = [], []
    for document in plan["documents"]:
        for form in document["forms"]:
            row = {"document_id": document["document_id"], **copy.deepcopy(form), "invoice_numbers": [x["invoice_number"] for x in document["invoices"] if x["invoice_id"] in form["invoice_ids"]]}
            (ready if form["status"] == "ready" and form.get("claim_amount") is not None else held).append(row)
    require(ready, "尚无材料、金额和收款分配已核定的分单；请按预览缺项继续核对")
    binding = {"parent_case_id": case_id, "input": normalized_input,
               "plan": plan, "profile_sha256": result.get("rule_selection", {}).get("profile_sha256")}
    return case, data, sources, normalized_input, result, ready, held, digest(binding)


def invoice_bytes(service, parent_case, row, sources):
    from case_materials import source_ref
    from case_invoice_sources import _verified_bytes
    source_ref(service, parent_case, row["source"], sources)
    source = next(x for x in sources if x["key"] == row["source"]["key"])
    path, meta, _, pages, _ = sources.verified[source["key"]]
    raw = _verified_bytes(str(path), meta["sha256"], service.material_roots, 30 * 1024 * 1024)
    page = row["source"]["page"]
    if path.suffix.lower() == ".pdf":
        if pages == 1:
            return raw, ".pdf", str(path), meta["sha256"], page
        import fitz
        with fitz.open(stream=raw, filetype="pdf") as original, fitz.open() as single:
            single.insert_pdf(original, from_page=page - 1, to_page=page - 1)
            return single.tobytes(garbage=4, deflate=True, no_new_id=True), ".pdf", str(path), meta["sha256"], page
    require(page == 1 and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".ofd"}, "此原件格式尚不能生成一票一文件，请先转换并核对来源")
    return raw, path.suffix.lower(), str(path), meta["sha256"], page


def persist_new(path, raw):
    from material_intake import reject_links
    from platform_runtime import private_path
    reject_links(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        require(path.read_bytes() == raw, "已有派生原件与本次来源不同，未覆盖；请核对原分单")
    else:
        with path.open("xb") as stream:
            private_path(path)
            stream.write(raw)


def run(service, case_id, values=None, expected_revision=None, confirmation_note=None, execute=False):
    from assistant_api import atomic_json
    from case_materials import catalog, source_ref
    require(type(execute) is bool, "执行标记须明确")
    case, data, sources, normalized_input, prepared, ready, held, fingerprint = proposal(service, case_id)
    previous = existing(service, case_id)
    if previous and previous["plan_sha256"] == fingerprint:
        return describe(service, previous, True)
    published=[]
    for entry in service.cases()["cases"]:
        _, files = service.resolve(entry["case_id"])
        origin=read_json(files["case"]).get("material_context",{}).get("materialized_from",{})
        if origin.get("parent_case_id")==case_id:
            published.append((entry["case_id"],origin))
    for child,origin in published:
        matching=[form for form in ready if set(form["invoice_numbers"])==set(origin.get("invoice_numbers",[]))]
        require(len(matching)==1 and origin.get("scope_sha256")==digest(matching[0]),
                "原分单已有登记且本次票据范围或金额已改变，请从原子案核对修正；未创建重叠新案")
    intent_path=folder(service,case_id)/"intent.json"
    intent=read_json(intent_path) if intent_path.exists() else None
    require(not intent or intent.get("status")=="completed" or intent.get("plan_sha256")==fingerprint,
            "先前分单尚未完成且父资料已改变；保留原冻结意图和子案，请先核对恢复原范围，未新建")
    preview = {"ok": True, "schema": "ustc-materialize-preview-v1", "case_id": case_id, "plan_sha256": fingerprint,
               "revision": service.revision(case_id), "forms": ready, "held_forms": held, "system_write": False,
               "next_action": {"tool": "reimbursement_case_materials", "arguments": {"case_id": case_id, "action": "materialize",
                   "values": {"plan_sha256": fingerprint}, "expected_revision": service.revision(case_id), "execute": True},
                   "needed_inputs": ["confirmation_note"], "label": "确认本地分单并继续填报"}}
    if not execute:
        return preview
    require(isinstance(values, dict) and set(values) == {"plan_sha256"} and values["plan_sha256"] == fingerprint, "分单计划已变化，请重新预览并确认")
    require(expected_revision == service.revision(case_id), "活动资料已改变，请重新预览分单")
    require(isinstance(confirmation_note, str) and 0 < len(confirmation_note.strip()) <= 2000, "请记录本次分单确认依据")
    parent_profile = case.get("material_context", {}).get("profiles", ["student_activity"])[0]
    by_id = {x["invoice_id"]: x for d in prepared["plan"]["documents"] for x in d["invoices"]}
    original_by_number = {i["number"]: i for e in data["expenses"] for i in e["invoices"]}
    rows = []
    with service.mutex(case_id):
        previous = existing(service, case_id)
        if previous and previous["plan_sha256"]==fingerprint:
            return describe(service, previous, True)
        require(service.revision(case_id) == expected_revision, "确认期间活动已变化，请重新核对")
        pending={"schema":"ustc-materialize-intent-v1","parent_case_id":case_id,
                 "parent_revision":expected_revision,"plan_sha256":fingerprint,"status":"in_progress",
                 "forms":ready,"held_forms":held,"input":normalized_input,"confirmation_note":confirmation_note.strip()}
        atomic_json(intent_path,pending)
        for form in ready:
            numbers = [by_id[i]["invoice_number"] for i in form["invoice_ids"]]
            child = case_id[:42] + "-" + digest(sorted(numbers))[:16]
            stage = folder(service, case_id) / "staging" / child
            destination = service.root / "cases" / child
            if destination.exists():
                _, known_paths = service.resolve(child)
                known = read_json(known_paths["case"]).get("material_context", {}).get("materialized_from", {})
                require(known.get("parent_case_id") == case_id and known.get("scope_sha256") == digest(form) and known.get("invoice_numbers") == numbers,
                        "同范围子案已存在且来源不同；请从活动列表核对原单，不重新建案")
                rows.append({"case_id": child, "title": form["summary"], "invoice_count": len(numbers), "amount": form["claim_amount"], "invoice_numbers": numbers, "plan_sha256":known["plan_sha256"]})
                continue
            # 每份子案先完整写入暂存目录，再原子登记；中断重启不会暴露半份案件。
            from operator_support import init_case
            from material_intake import reject_links
            reject_links(stage)
            if not stage.exists():
                init_case(child, stage, parent_profile)
            original_files, materials = [], []
            derived = service.material_roots[0] / "workbench-derived" / case_id / fingerprint / child
            for number in numbers:
                raw, suffix, source_path, source_sha, page = invoice_bytes(service, case_id, original_by_number[number], sources)
                target = derived / (number + suffix)
                persist_new(target, raw)
                sha = hashlib.sha256(raw).hexdigest()
                origin = {"path": source_path, "sha256": source_sha, "page": page}
                original_files.append({"path": str(target), "sha256": sha, "invoice_number_candidates": [number],
                                       "visual_check": "not_reviewed", "derived_from": origin})
                materials.append({"path": str(target), "sha256": sha, "status": "candidate_invoice_source_not_selected",
                                  "invoice_records": [{"number": number, "page": 1}], "derived_from": origin})
            child_case, child_form = read_json(stage / "case.json"), read_json(stage / "form.json")
            child_case.update(description=form["summary"], materials=materials)
            child_case["material_context"]["materialized_from"] = {"parent_case_id": case_id, "parent_revision": expected_revision,
                "plan_sha256": fingerprint, "scope_sha256": digest(form), "expense_ids": form["expense_ids"], "invoice_numbers": numbers,
                "confirmation_note": confirmation_note.strip(), "export": form["export"]}
            child_form.update(amount=form["claim_amount"], expected_invoice_count=len(numbers))
            child_form["basic"]["fields"].update({"t-abst": form["summary"], "t-cl_abst": form.get("remarks") or ""})
            # 只有已核分单的支付身份和金额进入草稿；经费、证明人、账号等未知字段继续保留空白。
            payments = {}
            from decimal import Decimal
            for payment in form["payments"]:
                key = (payment["payee_id"], payment["mode"])
                require(key not in payments or payments[key]["name"] == payment["name"], "同收款身份姓名矛盾，请核对")
                if key not in payments:
                    payments[key] = {"person_id": payment["payee_id"], "name": payment["name"], "mode": payment["mode"], "amount": "0.00"}
                payments[key]["amount"] = format(Decimal(payments[key]["amount"]) + Decimal(payment["amount"]), ".2f")
            child_form["payments"] = list(payments.values())
            atomic_json(stage / "case.json", child_case)
            atomic_json(stage / "form.json", child_form)
            atomic_json(stage / "invoices.json", {"case_id": child, "serial": None, "expected_count": len(numbers), "original_files": original_files})
            destination.parent.mkdir(parents=True, exist_ok=True)
            stage.rename(destination)
            rows.append({"case_id": child, "title": form["summary"], "invoice_count": len(numbers), "amount": form["claim_amount"], "invoice_numbers": numbers, "plan_sha256":fingerprint})
        receipt = {"schema": "ustc-materialized-cases-v1", "parent_case_id": case_id, "parent_revision": expected_revision,
                   "plan_sha256": fingerprint, "confirmation_note": confirmation_note.strip(), "cases": rows, "held_forms": held,
                   "source_selections": [{"key": row["key"], "page": 1} for row in sources]}
        if previous:
            atomic_json(folder(service,case_id)/"history"/(previous["plan_sha256"]+".json"),previous)
        atomic_json(folder(service, case_id) / "receipt.json", receipt)
        atomic_json(intent_path,{**pending,"status":"completed"})
    return describe(service, receipt)
