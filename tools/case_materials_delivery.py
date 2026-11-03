"""本活动材料关系到清单和A4附件；复用打印与Excel owner，不接学校业务。"""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import re
import uuid
from pathlib import Path

from assistant_contract import APIError
from case_materials import catalog, snapshot, source_ref
from material_review_model import atomic_json, digest, read_json

SCHEMA = "ustc-case-materials-delivery-v1"
ROOT = Path(__file__).resolve().parents[1]
MIME = {".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pdf": "application/pdf", ".csv": "text/csv; charset=utf-8",
        ".md": "text/markdown; charset=utf-8", ".html": "text/html; charset=utf-8",
        ".json": "application/json; charset=utf-8"}


def require(ok, message):
    if not ok:
        raise APIError("case_materials_delivery", message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def identity(selection):
    require(isinstance(selection, dict) and set(selection) == {"key", "page"}, "材料引用须明确原件和页码")
    return selection["key"], selection["page"]


def issue(code, title, owner="整案", basis="本活动保存的材料关系", next_step="回到材料核对页补充并确认", amount=None):
    return dict(zip(["问题编号", "状态", "人员/费用", "涉及金额", "问题", "已确认依据", "下一步"],
                    [code, "待核", owner, amount, title, basis, next_step]))


def prepare(service, case_id):
    """逐页去向独立于业务缺项；未知不能靠文件扩展名变成电子原件。"""
    from intake_review import bound_file
    revision = service.revision(case_id)
    case, data = snapshot(service, case_id)
    sources = catalog(service, case_id)
    paths = {}
    for row in sources:
        selection = {"key": row["key"], "page": 1}
        source_ref(service, case_id, selection, sources)
        path, raw, _, pages, _ = sources.verified[row["key"]]
        paths[row["key"]] = {"path": str(path), "sha256": raw["sha256"], "name": row["name"], "pages": pages or 1,
                              "kind": row["kind"], "job_id": row["job_id"], "material_id": row["material_id"]}
    issues, blocking, dispositions = [], [], {}
    for row in data.get("dispositions", []):
        source_ref(service, case_id, row.get("source"), sources)
        key = identity(row["source"])
        require(key not in dispositions, "同一原件页不能重复登记去向")
        require(row.get("disposition") in {"electronic", "paper", "exclude"}, "材料页须明确电子附件、纸质登记或有据排除")
        require(isinstance(row.get("reason"), str) and row["reason"].strip(), "材料去向须保留核对依据")
        dispositions[key] = copy.deepcopy(row)
    coverage = []
    for key, source in paths.items():
        for page in range(1, source["pages"] + 1):
            row = dispositions.get((key, page))
            if not row:
                problem = issue("PAGE-" + digest([key, page])[:12], "尚未确认本页是电子原件、纸质登记还是有据排除", source["name"] + "第" + str(page) + "页")
                issues.append(problem); blocking.append(problem)
            coverage.append({"source": {"key": key, "page": page}, "path": source["path"], "sha256": source["sha256"],
                             "name": source["name"], "disposition": row.get("disposition") if row else None,
                             "reason": row.get("reason", "待确认") if row else "待确认",
                             "paper_location": row.get("paper_location") if row else None})
    carrier_modes = {}
    for row in coverage:
        modes = carrier_modes.setdefault((row["sha256"], row["source"]["page"]), set())
        modes.add(row["disposition"])
        require(not {"paper", "electronic"}.issubset(modes), "同字节材料页的纸电去向冲突，请先核对重复来源")
    for row in data.get("issues", []):
        issues.append(issue(row["issue_id"], row["title"], row.get("owner") or "待核", row.get("basis") or "本活动问题登记", row.get("request") or "沿原问题核实"))
    expenses = data.get("expenses", [])
    if not expenses:
        problem = issue("NO-EXPENSE", "尚未核对费用与发票关系")
        issues.append(problem); blocking.append(problem)
    plan, evaluation = None, {}
    try:
        planned = service.case_materials(case_id, "plan")
        plan = planned.get("plan")
        if plan:
            evaluation = {x["expense_id"]: x for d in plan.get("documents", []) for x in d.get("expenses", [])}
            for n, problem in enumerate(planned.get("needed_inputs", []), 1):
                issues.append(issue("PLAN-" + str(n), problem.get("detail") or problem.get("code") or "规划待核", problem.get("field") or "整案"))
    except APIError as error:
        if getattr(error, "code", None) not in {"case_materials", "rules_changed"}:
            raise
        issues.append(issue("RULES", str(error), next_step="选择本活动适用的已确认规则后重新规划"))
    units, invoice_refs, roles = [], set(), {}
    def physical(key):
        return paths[key[0]]["sha256"], key[1]
    physical_roles = {}
    for expense in expenses:
        for row in expense["invoices"]:
            source_ref(service, case_id, row["source"], sources)
            key = identity(row["source"])
            require(physical(key) not in invoice_refs, "同一材料页被多张发票使用；请先拆分或核对票页关系")
            invoice_refs.add(physical(key)); roles[key] = "invoice"; physical_roles[physical(key)] = "invoice"
            require(dispositions.get(key, {}).get("disposition") != "exclude", "仍在申请范围的发票页不能排除打印和纸质登记")
            units.append({"expense_id": expense["expense_id"], "description": expense["description"], "invoice": row})
        if not expense.get("payees"):
            issues.append(issue("PAYEE-" + expense["expense_id"], "实际收款身份与分配金额尚未核清", expense["description"]))
        if not expense.get("payment_materials"):
            issues.append(issue("PAYMENT-" + expense["expense_id"], "本次未关联实际付款凭证；只附已有材料，不留空页", expense["description"], next_step="按适用规则确认是否需补实际支付记录；订单不当作付款凭证"))
    # 一份共用凭证只在其最后一张关联发票及行程之后附一次。
    support_last, support_keys = {}, {}
    for index, unit in enumerate(units):
        expense = next(x for x in expenses if x["expense_id"] == unit["expense_id"])
        for field in ("itinerary_materials", "payment_materials"):
            for selection in expense.get(field, []):
                source_ref(service, case_id, selection, sources)
                key = identity(selection)
                require(physical(key) not in invoice_refs, "发票页不能同时冒充独立付款凭证或行程")
                role = "payment" if field == "payment_materials" else "itinerary"
                require(physical_roles.get(physical(key), role) == role, "同一材料页不能同时充当付款凭证和行程")
                physical_roles[physical(key)] = role
                roles[key] = role; support_last[physical(key)] = index; support_keys[physical(key)] = key
    ordered, emitted = [], set()
    def append(key, role):
        if physical(key) in emitted or dispositions.get(key, {}).get("disposition") != "electronic":
            return
        source = paths[key[0]]
        if source["kind"] not in {"pdf", "png", "jpg", "jpeg", "webp", "tif", "tiff", "bmp"}:
            problem = issue("FORMAT-" + digest(key)[:12], "此电子材料格式暂不能排成A4附件，请提供已核对的PDF或图片副本", source["name"])
            issues.append(problem); blocking.append(problem); return
        emitted.add(physical(key))
        ordered.append({"id": "material-" + digest(key)[:16], "kind": "source", "role": role,
                        "label": source["name"], "path": source["path"], "page": key[1] - 1,
                        "sha256": source["sha256"], "trim": False})
    for row in coverage:
        key = identity(row["source"])
        if physical(key) not in physical_roles:
            append(key, "supporting")
    for index, unit in enumerate(units):
        append(identity(unit["invoice"]["source"]), "invoice")
        for role in ("itinerary", "payment"):
            for physical_key, last in support_last.items():
                key = support_keys[physical_key]
                if last == index and roles[key] == role:
                    append(key, role)
    for key, role in roles.items():
        if dispositions.get(key, {}).get("disposition") == "exclude":
            problem = issue("EXCLUDED-" + digest(key)[:12], "仍有关联的付款或行程材料被排除，请先核对本次范围", paths[key[0]]["name"])
            issues.append(problem); blocking.append(problem)
    _, case_paths = service.resolve(case_id)
    form = read_json(case_paths["form"])
    profile = read_json(ROOT / "data/printing_profile.json")
    require(all(profile.get(k, k in {"one_material_per_page", "auto_orientation"}) is v for k, v in
                {"one_material_per_page": True, "auto_orientation": True, "show_material_labels": False, "show_headers": False, "show_footers": False}.items()),
            "本入口只交付逐页A4、自动横竖、无附加页眉页脚的附件")
    fingerprint = digest({"schema": SCHEMA, "case_id": case_id, "revision": revision, "data": data,
                          "sources": paths, "plan": plan, "printing_profile": profile})
    require(service.revision(case_id) == revision, "规划期间本活动资料已变更，请重新打开")
    return {"revision": revision, "case": case, "data": data, "sources": paths, "coverage": coverage,
            "issues": issues, "blocking": blocking, "ordered": ordered, "evaluation": evaluation,
            "form": form, "profile": profile, "delivery_id": "delivery-" + fingerprint[:32]}


def destination(service, case_id, delivery_id):
    from material_intake import reject_links
    require(re.fullmatch(r"delivery-[a-f0-9]{32}", str(delivery_id)) is not None, "材料交付编号无效")
    service.resolve(case_id)
    root = Path(service.material_roots[0]) / "报销交付" / case_id / delivery_id
    reject_links(root)
    return root


def sheet(name, headers, rows, note=""):
    from checklist_delivery import column_spec
    return {"name": name, "title": name, "note": note, "columns": [column_spec(x) for x in headers], "rows": rows}


def workbook_plan(case_id, bundle, source, pages):
    from checklist_delivery import ISSUE_COLUMNS, ISSUE_NOTE, SCHEMA as CHECKLIST_SCHEMA
    payees, details, paper, coverage_rows = [], [], [], []
    sources = bundle["sources"]
    def ref(selection):
        source = sources[selection["key"]]
        return source["name"] + "第" + str(selection["page"]) + "页"
    for expense in bundle["data"].get("expenses", []):
        evaluated = bundle["evaluation"].get(expense["expense_id"], {})
        for row in expense.get("payees", []):
            payees.append(dict(zip(["费用", "拟收款人", "学工号或单位编号", "支付方式", "拟收款金额/元", "核对依据", "状态"],
                [expense["description"], row["name"], row["payee_id"], row["mode"], row["amount"], ref(row["source"]), "本地核对记录，未表示已付款"])))
        for invoice in expense["invoices"]:
            key = identity(invoice["source"]); original = sources[key[0]]
            details.append(dict(zip(["费用编号", "费用", "完整发票号码", "票面金额/元", "本项核定申请额/元", "申请额说明", "原件", "原件页码", "电子包页码", "原件SHA256", "付款材料", "行程材料", "状态"],
                [expense["expense_id"], expense["description"], invoice["number"], invoice["face_amount"],
                 evaluated.get("claim_amount") if evaluated.get("status") == "ready" else None,
                 "按费用项列示，同费用多票时此列不重复求和", original["path"], key[1], pages.get(key), original["sha256"],
                 "；".join(ref(x) for x in expense.get("payment_materials", [])) or "本次未附",
                 "；".join(ref(x) for x in expense.get("itinerary_materials", [])) or "未登记", "已登记关联，票面与实际付款仍需核实"])))
    for row in bundle["coverage"]:
        key = identity(row["source"])
        coverage_rows.append(dict(zip(["材料", "原件页码", "去向", "电子包页码", "依据", "SHA256"],
                                     [row["path"], key[1], row["disposition"] or "待核", pages.get(key), row["reason"], row["sha256"]])))
        if row["disposition"] == "paper":
            paper.append(dict(zip(["纸质材料", "原件页码", "实物安排", "核对扫描件", "依据", "状态"],
                                  [row["name"], key[1], row["paper_location"] or "实物待定位", row["path"], row["reason"], "只登记；扫描件不进入电子打印包"])))
    sheets = [sheet("问题总结", ISSUE_COLUMNS, bundle["issues"], ISSUE_NOTE),
              sheet("拟收款核对", ["费用", "拟收款人", "学工号或单位编号", "支付方式", "拟收款金额/元", "核对依据", "状态"], payees),
              sheet("材料及金额明细", ["费用编号", "费用", "完整发票号码", "票面金额/元", "本项核定申请额/元", "申请额说明", "原件", "原件页码", "电子包页码", "原件SHA256", "付款材料", "行程材料", "状态"], details),
              sheet("纸质登记", ["纸质材料", "原件页码", "实物安排", "核对扫描件", "依据", "状态"], paper,
                    "未登记纸质材料不等于没有纸质原件；沿逐页去向与实物安排核对。"),
              sheet("逐页来源覆盖", ["材料", "原件页码", "去向", "电子包页码", "依据", "SHA256"], coverage_rows)]
    return {"schema": CHECKLIST_SCHEMA, "case_id": case_id, "title": (bundle["case"].get("description") or case_id) + "材料交付",
            "note": "本地材料核对快照，不表示学校建单、正式信息面、审批或付款完成。", "system_write": False,
            "sources": [{"path": str(source), "sha256": sha(source)}, *[{"path": r["path"], "sha256": r["sha256"]} for r in sources.values()]],
            "sheets": sheets}


def verified_receipt(folder):
    from material_intake import reject_links
    reject_links(folder)
    receipt = read_json(folder / "交付回执.json")
    require(receipt.get("schema") == SCHEMA, "交付回执格式不符")
    for row in receipt["artifacts"]:
        require(Path(row["name"]).name == row["name"] and row["name"] not in {".", ".."}, "交付成员名称无效")
        path = folder / row["name"]; reject_links(path)
        require(path.is_file() and sha(path) == row["sha256"], "已交付文件已改变或缺失；不覆盖旧快照")
    return receipt


def run(service, case_id, execute=False, expected_revision=None, confirmation_note=None):
    require(type(execute) is bool, "execute须为明确布尔值")
    bundle = prepare(service, case_id)
    folder = destination(service, case_id, bundle["delivery_id"])
    common = {"ok": True, "schema": SCHEMA, "case_id": case_id, "delivery_id": bundle["delivery_id"],
              "revision": bundle["revision"], "system_write": False, "local_materials_only": True,
              "official_information_page_included": False, "can_build_pdf": not bundle["blocking"] and bool(bundle["ordered"]),
              "needed_inputs": bundle["blocking"], "issues": bundle["issues"],
              "scope": {"invoice_count": sum(len(x["invoices"]) for x in bundle["data"].get("expenses", [])),
                        "electronic_pages": len(bundle["ordered"]), "paper_pages": sum(x["disposition"] == "paper" for x in bundle["coverage"]),
                        "unreviewed_pages": sum(x["disposition"] is None for x in bundle["coverage"])},
              "note": "交付清单和已确认的电子附件，纸质原件只登记；正式学校信息面须在审批后另取。"}
    if not execute:
        return {**common, "preview": True, "artifacts": verified_receipt(folder)["artifacts"] if (folder / "交付回执.json").exists() else []}
    require(expected_revision == bundle["revision"], "活动资料已改变，请重新预览当前版本后导出")
    require(isinstance(confirmation_note, str) and 0 < len(confirmation_note.strip()) <= 2000, "请确认本次交付范围与材料去向")
    with service.mutex(case_id):
        require(service.revision(case_id) == bundle["revision"], "导出前活动资料已改变，请重新预览")
        if folder.exists():
            require((folder / "交付回执.json").exists(), "此交付尚无完成回执，保留原目录供核对，不重建或覆盖")
            return {**verified_receipt(folder), "reused": True}
        from platform_runtime import private_path
        folder.parent.mkdir(parents=True, exist_ok=True); private_path(folder.parent)
        staging = folder.with_name(".building-" + bundle["delivery_id"] + "-" + uuid.uuid4().hex[:8])
        staging.mkdir(); private_path(staging)
        source = staging / "材料关系快照.json"
        frozen = {"schema": SCHEMA, "case_id": case_id, "revision": bundle["revision"], "data": bundle["data"], "sources": bundle["sources"]}
        atomic_json(source, frozen, exclusive=True)
        # 清单渲染器核验一个位置稳定的不可变输入，发布改名不改变其来源指针。
        frozen_source = folder.parent / (bundle["delivery_id"] + "-source.json")
        if frozen_source.exists():
            require(read_json(frozen_source) == frozen, "冻结输入已有不同内容，不覆盖")
        else:
            atomic_json(frozen_source, frozen, exclusive=True)
        names, page_map, packet_records = [], {}, []
        if common["can_build_pdf"]:
            from print_packet import Packet
            packet = Packet({"case_id": case_id, "serial": bundle["form"].get("serial"), "title": "附件单面打印包", "sections": [{"title": "本活动已核电子附件", "items": bundle["ordered"]}]}, staging, bundle["profile"])
            try:
                packet.build(); packet.doc.save(staging / "附件单面打印包.pdf", garbage=4, deflate=True)
                packet_records = packet.records
            finally:
                packet.doc.close()
            for item in packet_records:
                for row in bundle["coverage"]:
                    if (row["sha256"], row["source"]["page"]) == (item["sha256"], item["page"] + 1):
                        page_map[identity(row["source"])] = item["output_page"]
            names.append("附件单面打印包.pdf")
            atomic_json(staging / "附件逐页索引.json", {"case_id": case_id, "pages": packet_records, "simplex": True,
                        "one_material_per_page": True, "auto_orientation": True, "headers": False, "footers": False,
                        "official_information_page_included": False}, exclusive=True)
            names.append("附件逐页索引.json")
        from checklist_delivery import write_issue_summary
        from checklist_native import render
        plan = workbook_plan(case_id, bundle, frozen_source, page_map)
        plan_path = staging / "清单交付计划.json"; atomic_json(plan_path, plan, exclusive=True)
        write_issue_summary(plan, staging); render(plan_path, staging / "报销清单.xlsx")
        names += ["报销清单.xlsx", "问题总结.html", "问题总结.md", "材料关系快照.json"]
        # 生成期间的来源、活动版本和打印开关必须仍是本次冻结版本。
        current = prepare(service, case_id)
        require(current["delivery_id"] == bundle["delivery_id"], "生成期间材料或规则改变；保留未发布目录，重新预览")
        artifacts = [{"name": name, "kind": Path(name).suffix[1:], "content_type": MIME[Path(name).suffix], "sha256": sha(staging / name),
                      "size": (staging / name).stat().st_size, "path": str(folder / name)} for name in names]
        result = {**common, "built": True, "reused": False, "output_directory": str(folder), "confirmation_note": confirmation_note.strip(),
                  "artifacts": artifacts, "packet_pages": len(packet_records)}
        atomic_json(staging / "交付回执.json", result, exclusive=True)
        staging.rename(folder)
        return verified_receipt(folder)


def download(service, case_id, delivery_id, artifact_name):
    """白名单文件下载；路径只能由已核回执解析，不接外部文件路径。"""
    require(isinstance(artifact_name, str) and Path(artifact_name).name == artifact_name and not any(x in artifact_name for x in ("/", "\\", "\x00")), "下载文件名称无效")
    folder = destination(service, case_id, delivery_id)
    receipt = verified_receipt(folder)
    require(receipt.get("case_id") == case_id and receipt.get("delivery_id") == delivery_id, "交付回执不属于本活动")
    rows = [r for r in receipt["artifacts"] if r["name"] == artifact_name]
    require(len(rows) == 1, "此文件不在本次交付白名单中")
    return {**rows[0], "path": str(folder / artifact_name), "case_id": case_id, "delivery_id": delivery_id}
