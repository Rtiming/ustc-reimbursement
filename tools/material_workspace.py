#!/usr/bin/env python3
"""Build a source-verified, pre-claim material workspace. Never touches finance."""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import shutil
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote

SCHEMA = "ustc-material-workspace-v1"
ROLES = {"invoice", "itinerary", "explanation", "boarding", "validation", "blank", "paper_auxiliary"}
ROLE_NAMES = {"invoice": "发票", "itinerary": "行程单", "explanation": "说明", "boarding": "登机凭证", "validation": "校验材料", "blank": "空白页", "paper_auxiliary": "纸质辅助材料"}
STATUS_NAMES = {"reported_fields_match": "抬头、税号已匹配", "reported_fields_mismatch": "抬头或税号有误", "reported_buyer_problem": "抬头或税号有误", "recognition_reserved": "被预约占用", "pool_normal": "已认证正常", "normal_certified": "已认证正常", "pool_error": "识别异常", "abnormal_pool": "识别异常", "recognition_pending": "仍待识别", "not_attempted": "尚未上传", "save_requested_readback_unknown": "已请求保存，待完整回读", "uploaded_save_requested_complete_readback_pending": "已请求保存，待完整回读"}


def status_label(value):
    return STATUS_NAMES.get(value, value or "未核验")


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique(items, label):
    result = {}
    for item in items:
        key = item.get("id")
        require(isinstance(key, str) and key.strip(), f"{label}缺少id")
        require(key not in result, f"{label}重复id: {key}")
        result[key] = item
    return result


def pdf_pages(path):
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    with fitz.open(path) as doc:
        require(not doc.needs_pass, f"PDF需密码: {path}")
        return doc.page_count


def validate(plan):
    require(plan.get("schema") == SCHEMA, "不支持的材料输入schema")
    require(bool(plan.get("case_id")), "必须指定case_id")
    root = Path(plan["source_root"]).resolve(strict=True)
    require(root.is_dir(), "source_root必须是案件目录")
    assets = unique(plan["assets"], "材料页")
    expenses = unique(plan["expenses"], "费用")
    unique(plan.get("questions", []), "待核问题")
    hashes, page_counts, pages_by_path = {}, {}, defaultdict(set)
    carriers, seen_pages = defaultdict(set), set()

    def check_source(record):
        raw = Path(record.get("source_path", record.get("path", "")))
        require(raw.is_absolute(), f"材料路径必须绝对: {raw}")
        path = raw.resolve(strict=True)
        require(path.is_relative_to(root), f"来源不在案件目录内: {path}")
        require(path.is_file(), f"来源不是文件: {path}")
        sha = record.get("sha256", "")
        require(bool(re.fullmatch(r"[0-9a-f]{64}", sha)), f"来源SHA无效: {path}")
        if path not in hashes:
            hashes[path] = digest(path)
        require(hashes[path] == sha, f"来源SHA不符: {path}")
        return path

    for record in plan.get("input_evidence", []):
        check_source(record)
    for aid, asset in assets.items():
        require(isinstance(asset.get("person"), str) and bool(asset["person"].strip()), f"材料缺少人员，未知请明确标记: {aid}")
        require(isinstance(asset.get("source_path"), str) and bool(asset["source_path"]), f"材料缺少source_path: {aid}")
        kind, role = asset.get("kind"), asset.get("role")
        require(kind in {"electronic_page", "paper_page"}, f"材料载体无效: {aid}")
        require(role in ROLES, f"材料角色无效: {aid}")
        path = check_source(asset)
        require(path.suffix.lower() == ".pdf", f"材料源须是PDF: {aid}")
        if path not in page_counts:
            page_counts[path] = pdf_pages(path)
        page = asset.get("page")
        require(type(page) is int and 1 <= page <= page_counts[path], f"材料页码越界: {aid}")
        page_key = (kind, asset["sha256"], page)
        require(page_key not in seen_pages, f"同载体同源页重复登记: {aid}")
        seen_pages.add(page_key)
        pages_by_path[path].add(page)
        carriers[asset["sha256"]].add(kind)
        refs = asset.get("expense_ids", [])
        require(len(refs) == len(set(refs)), f"材料费用引用重复: {aid}")
        require(all(eid in expenses for eid in refs), f"材料引用未知费用: {aid}")
        resolution = asset.get("resolution")
        require(resolution in {"linked", "excluded_blank", "pending_link"}, f"材料去向无效: {aid}")
        if resolution == "linked":
            require(bool(refs), f"已关联材料没有费用: {aid}")
        if resolution == "excluded_blank":
            require(role == "blank" and not refs, f"只有无关联空白页可排除: {aid}")
        if resolution == "pending_link":
            require(bool(asset.get("basis") or asset.get("notes")), f"待关联材料须说明原因: {aid}")
        for eid in refs:
            require(aid in expenses[eid].get("asset_ids", []), f"材料与费用未双向关联: {aid} -> {eid}")

    for path, pages in pages_by_path.items():
        require(pages == set(range(1, page_counts[path] + 1)), f"来源PDF未逐页完整登记: {path}")
    for sha, kinds in carriers.items():
        require(len(kinds) == 1, f"同源文件混作纸质和电子: {sha}")

    invoices = {}
    invoice_assets = set()
    for eid, expense in expenses.items():
        require(isinstance(expense.get("person"), str) and bool(expense["person"].strip()), f"费用缺少人员: {eid}")
        require(isinstance(expense.get("label"), str) and bool(expense["label"].strip()), f"费用缺少说明: {eid}")
        require(isinstance(expense.get("versions"), list), f"费用缺少票版列表: {eid}")
        require(expense.get("claim_amount") is None and expense.get("payee") is None,
                f"建单前材料工作区不能填申请额或打款人: {eid}")
        refs = expense.get("asset_ids", [])
        require(len(refs) == len(set(refs)), f"费用材料重复引用: {eid}")
        require(bool(refs), f"费用没有材料: {eid}")
        for aid in refs:
            require(aid in assets, f"费用引用未知材料: {eid} -> {aid}")
            require(eid in assets[aid].get("expense_ids", []), f"费用与材料未双向关联: {eid} -> {aid}")
            person = assets[aid].get("person")
            require(not person or person == expense.get("person"), f"费用与材料人员不一致: {eid} -> {aid}")
        versions = expense.get("versions", [])
        amounts = set()
        for version in versions:
            number = version.get("invoice_number")
            require(isinstance(number, str) and bool(re.fullmatch(r"\d{8,20}", number)), f"发票号码须为完整数字字符串: {eid}")
            require(number not in invoices, f"同票号重复计入费用: {number}")
            invoices[number] = eid
            vrefs = version.get("asset_ids", [])
            require(bool(vrefs), f"票版缺来源页: {number}")
            for aid in vrefs:
                require(aid in refs and assets[aid]["role"] == "invoice", f"票版须引用本费用发票页: {number} -> {aid}")
                require(aid not in invoice_assets, f"发票页被不同票版重复使用: {aid}")
                invoice_assets.add(aid)
            if version.get("source_sha256"):
                require(version["source_sha256"] in {assets[x]["sha256"] for x in vrefs}, f"票版SHA与来源页不符: {number}")
            amount = version.get("amount_display", "")
            require(isinstance(amount, str) and re.fullmatch(r"\d+\.\d{2}", amount), f"票面金额须为两位小数字符串: {number}")
            amounts.add(amount)
        require(len(amounts) <= 1, f"同费用新旧票金额不同，须人工核对: {eid}")
    expected_invoice_assets = {aid for aid, a in assets.items() if a["role"] == "invoice"}
    require(invoice_assets == expected_invoice_assets, "存在未归票版的发票材料页")
    for question in plan.get("questions", []):
        require(all(eid in expenses for eid in question.get("expense_ids", [])), f"待核问题引用未知费用: {question['id']}")
    counts = {"electronic_pages": sum(a["kind"] == "electronic_page" for a in assets.values()),
              "paper_pages": sum(a["kind"] == "paper_page" for a in assets.values()),
              "invoice_versions": len(invoices), "expenses": len(expenses)}
    for key, value in plan.get("expected_counts", {}).items():
        require(key in counts and counts[key] == value, f"覆盖数量不符: {key}, 实际{counts.get(key)}, 预期{value}")
    return {"schema": SCHEMA, "case_id": plan["case_id"], "verified": True,
            "counts": counts, "source_files_verified": len(hashes),
            "resolutions": dict(Counter(a["resolution"] for a in assets.values())),
            "roles": dict(Counter(a["role"] for a in assets.values())),
            "claim_created": False, "finance_refreshed": False,
            "scope": "逐页来源完整性与材料归属；不代表票面审核放行、审批或付款"}


def safe_name(value):
    text = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(value)).strip(" .")[:100]
    return text or "未定人员"


def plain(value):
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def csv_cell(value):
    text = plain(value)
    # Spreadsheet applications must not evaluate source text as formulas.
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) or re.fullmatch(r"\d{16,}", text) else text


def write_csv(path, columns, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows([[csv_cell(v) for v in row] for row in rows])


def json_write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build(plan, output):
    qa = validate(plan)
    output = Path(output).absolute()
    require(not output.exists(), f"输出目录已存在，不覆盖: {output}")
    require(output.parent.is_dir(), "输出目录的父目录须已存在")
    assets = {a["id"]: a for a in plan["assets"]}
    version_by_asset = {aid: v for e in plan["expenses"] for v in e["versions"] for aid in v["asset_ids"]}
    people = list(dict.fromkeys(e["person"] for e in plan["expenses"]))
    people += [a["person"] for a in assets.values() if a["person"] not in people and a["resolution"] != "excluded_blank"]
    people = list(dict.fromkeys(people))
    person_dirs = {p: f"{i:02d}_{safe_name(p)}" for i, p in enumerate(people, 1)}
    copies, destinations = {}, {}
    by_sha = defaultdict(list)
    for a in assets.values():
        if a["kind"] == "electronic_page":
            by_sha[a["sha256"]].append(a)
    stage = Path(tempfile.mkdtemp(prefix=".material-workspace-", dir=output.parent))
    try:
        for person_dir in person_dirs.values():
            (stage / "01_按人材料" / person_dir / "电子原件").mkdir(parents=True)
        for sha, group in by_sha.items():
            active = [a for a in group if a["resolution"] != "excluded_blank"]
            if not active:
                continue
            persons = set(a["person"] for a in active)
            folder = person_dirs[active[0]["person"]] if len(persons) == 1 else "00_共同材料"
            first = active[0]
            version = version_by_asset.get(first["id"])
            if len(group) == 1 and version:
                name = f"发票_{version['amount_display']}_{version['invoice_number']}_{sha[:12]}.pdf"
            else:
                name = f"{ROLE_NAMES.get(first['role'], '材料')}_{len(group)}页_{sha[:12]}.pdf"
            rel = Path("01_按人材料") / folder / "电子原件" / safe_name(name)
            require(rel.as_posix() not in destinations, f"材料副本文件名冲突: {rel}")
            destinations[rel.as_posix()] = sha
            dest = stage / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(first["source_path"], dest)
            require(digest(dest) == sha, f"副本SHA不符: {rel}")
            copies[sha] = rel.as_posix()

        def link(a):
            base = quote(copies[a["sha256"]], safe="/") if a["sha256"] in copies else Path(a["source_path"]).as_uri()
            return f"{base}#page={a['page']}"

        def asset_caption(a):
            if a["kind"] == "paper_page":
                return f"纸质扫描第{a['page']}页·{ROLE_NAMES[a['role']]}"
            return f"{ROLE_NAMES[a['role']]}·原文件第{a['page']}页"

        expense_rows, version_rows, material_rows, question_rows = [], [], [], []
        h = html.escape
        questions_by_id = {q["id"]: q for q in plan.get("questions", [])}
        expenses_by_id = {e["id"]: e for e in plan["expenses"]}
        cards = []
        person_md = defaultdict(list)
        for e in plan["expenses"]:
            eid = e["id"]
            owned = [assets[aid] for aid in e["asset_ids"]]
            expense_rows.append([eid, e["person"], e["label"], len(e["versions"]),
                                 " / ".join(v["invoice_number"] for v in e["versions"]),
                                 ";".join(a["id"] for a in owned if a["kind"] == "electronic_page"),
                                 ";".join(str(a["page"]) for a in owned if a["kind"] == "paper_page"),
                                 e.get("needs_confirmation", []), e.get("notes", []), "未核定", "未核定"])
            rows, md_lines = [], [f"## {e['label']}", f"费用编号：{eid}。申请额、打款人未核定。", ""]
            for v in e["versions"]:
                version_rows.append([eid, e["person"], v["invoice_number"], v["amount_display"], v.get("issue_date"),
                                     v.get("version_role"), v.get("buyer_status"), v.get("finance_status"),
                                     v.get("finance_evidence"), v["asset_ids"]])
                vals = [v["invoice_number"], v["amount_display"], v.get("version_role", ""), status_label(v.get("buyer_status", "")), status_label(v.get("finance_status", ""))]
                rows.append("<tr>" + "".join(f"<td>{h(plain(x))}</td>" for x in vals) + "</tr>")
                md_lines.append(f"- 票号{v['invoice_number']}，票面{v['amount_display']}元；{plain(v.get('version_role'))}；购方：{status_label(v.get('buyer_status'))}。")
            a_links = [f'<li><a href="{h(link(a), quote=True)}">{h(asset_caption(a))}</a></li>' for a in owned]
            for a in owned:
                target = ("../../" + quote(copies[a["sha256"]], safe="/") + f"#page={a['page']}") if a["sha256"] in copies else link(a)
                md_lines.append(f"- [{asset_caption(a)}]({target})")
            notes = [plain(x) for x in e.get("notes", [])]
            notes += ["待核：" + (questions_by_id[x].get("topic", x) if x in questions_by_id else plain(x)) for x in e.get("needs_confirmation", [])]
            md_lines.extend(["", *[f"- {n}" for n in notes], ""])
            person_md[e["person"]].extend(md_lines)
            table = ('<table><thead><tr><th>票号</th><th>票面金额</th><th>版本</th><th>购方审核</th><th>历史系统状态</th></tr></thead><tbody>' + "".join(rows) + '</tbody></table>') if rows else '<p class="flag">现有材料未含发票，本次不列申请额。</p>'
            cards.append(f'<article data-kind="expense" data-person="{h(e["person"], quote=True)}"><h2>{h(e["person"])} · {h(e["label"])}</h2><p class="muted">{h(eid)} · 申请额、打款人未核定</p>{table}<ul>{"".join(a_links)}</ul><p>{"<br>".join(h(n) for n in notes)}</p></article>')
        for a in assets.values():
            material_rows.append([a["id"], a["person"], a["kind"], a["role"], a["page"], a["resolution"], a["expense_ids"],
                                  a["source_path"], a["sha256"], a.get("source_chain"), copies.get(a["sha256"], "仅引用原件"), a.get("basis"), a.get("notes", [])])
            if a["resolution"] == "pending_link" and not a["expense_ids"]:
                reason = plain(a.get("basis")) + " " + plain(a.get("notes", []))
                cards.append(f'<article data-kind="unlinked" data-person="{h(a["person"], quote=True)}"><h2>{h(a["person"])} · 待关联材料</h2><p class="flag">{h(reason)}</p><a href="{h(link(a), quote=True)}">{h(asset_caption(a))}</a></article>')
                target = ("../../" + quote(copies[a["sha256"]], safe="/") + f"#page={a['page']}") if a["sha256"] in copies else link(a)
                person_md[a["person"]].extend(["## 待关联材料", "", f"- [{asset_caption(a)}]({target})：{reason}", ""])
        for q in plan.get("questions", []):
            question_rows.append([q["id"], q.get("person"), q.get("topic"), q.get("question"), q.get("known"), q.get("basis"), q.get("priority"), q.get("expense_ids")])
        index_dir = stage / "02_台账"
        index_dir.mkdir()
        write_csv(index_dir / "费用与材料.csv", ["费用编号", "人员", "费用说明", "票版数", "发票号码", "电子页", "纸质页", "待核", "备注", "申请额", "打款人"], expense_rows)
        write_csv(index_dir / "全部票版.csv", ["费用编号", "人员", "票号", "票面金额", "开票日期", "版本", "购方审核", "历史系统状态", "系统证据", "材料页编号"], version_rows)
        cols = ["材料页编号", "人员", "载体", "类别", "文件内页码", "去向", "费用编号", "来源PDF", "来源SHA256", "来源链", "整理副本", "对应依据", "备注"]
        write_csv(index_dir / "全部材料页.csv", cols, material_rows)
        write_csv(index_dir / "纸质材料定位.csv", cols, [row for row in material_rows if row[2] == "paper_page"])
        write_csv(index_dir / "待核事项.csv", ["编号", "人员", "主题", "问题", "已知", "依据", "优先级", "费用编号"], question_rows)
        q_lines = ["# 集中待核事项", "", "已知答复和工具可自行查明的问题不重复向用户询问。以下只是登记，尚未发送。", ""]
        for q in plan.get("questions", []):
            q_lines.extend([f"## {q['id']} · {q.get('topic', '')}", plain(q.get("question")), "", "已知：" + plain(q.get("known")), "依据：" + plain(q.get("basis")), ""])
            ids = q.get("expense_ids", [])
            if len(ids) <= 12:
                q_lines.extend([f"- {expenses_by_id[eid]['person']}：{expenses_by_id[eid]['label']}（{eid}）" for eid in ids])
            elif ids:
                q_lines.append(f"涉及{len(ids)}项费用，详见台账中该问题编号。")
            q_lines.append("")
        (stage / "集中待核事项.md").write_text("\n".join(q_lines), encoding="utf-8")
        for person, folder in person_dirs.items():
            text = [f"# {person}材料索引", "", "此处按材料涉及人员归类；打款人和申请额待核。纸质页仅定位原扫描，电子多页说明保留完整原件。", "", *person_md[person]]
            (stage / "01_按人材料" / folder / "材料索引.md").write_text("\n".join(text), encoding="utf-8")
        counts = qa["counts"]
        summary = f"{counts['expenses']}项费用材料，{counts['invoice_versions']}个票号版本；电子{counts['electronic_pages']}页，纸质扫描{counts['paper_pages']}页。"
        note = "按费用合并新旧票号，保留全部版本；纸质仅作定位。此工作区用于核对，申请额、打款人、选票及最终附件尚未确定。"
        options = '<option value="">全部人员</option>' + ''.join(f'<option>{h(p)}</option>' for p in people)
        question_html = '<details><summary>查看集中待核事项（' + str(len(questions_by_id)) + '项）</summary>' + ''.join(f'<p><strong>{h(q["id"])} · {h(q.get("topic", ""))}</strong><br>{h(plain(q.get("question")))}<br><span class="muted">{h(plain(q.get("known")))}</span></p>' for q in questions_by_id.values()) + '</details>'
        page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>材料核对入口</title><style>
body{max-width:1100px;margin:32px auto;padding:0 20px;color:#192a35;background:#f5f7f9;font:16px/1.65 system-ui,sans-serif}h1{font-size:28px}h2{font-size:20px;margin:0}header,article{background:white;border:1px solid #dce3e8;border-radius:10px;padding:20px;margin:18px 0}table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:8px;border-bottom:1px solid #ddd;overflow-wrap:anywhere}a{color:#075c9e}input,select{padding:10px;font:inherit;border:1px solid #aaa;border-radius:6px;margin:4px}.muted{color:#526472;font-size:14px}.flag{color:#934500}li{margin:4px 0}[hidden]{display:none!important}@media(max-width:700px){table{display:block;overflow-x:auto}input{width:90%}}
</style><header><h1>''' + h(plan.get("title", "材料核对入口")) + '</h1><p>' + h(summary) + '</p><p>' + h(note) + '</p><p class="muted">系统状态来自历史回执：' + h(plan.get("historical_finance_as_of", "未刷新")) + '</p><p><a href="集中待核事项.md">集中待核事项</a> · <a href="02_台账/全部材料页.csv">全部材料页</a> · <a href="02_台账/费用与材料.csv">费用台账</a> · <a href="02_台账/全部票版.csv">票版台账</a></p>' + question_html + '<select id="person">' + options + '</select><input id="search" placeholder="查姓名、票号、金额或问题" aria-label="查材料"><p id="count"></p></header>' + ''.join(cards) + '''<script>
const cards=[...document.querySelectorAll('article')], person=document.getElementById('person'), search=document.getElementById('search');
function filter(){const p=person.value,q=search.value.trim().toLowerCase();let n=0,u=0;for(const c of cards){c.hidden=!!((p&&c.dataset.person!==p)||(q&&!c.textContent.toLowerCase().includes(q)));if(!c.hidden){if(c.dataset.kind==='expense')n++;else u++}}document.getElementById('count').textContent='显示'+n+'项费用材料'+(u?'，另有'+u+'页待关联材料':'')}person.addEventListener('change',filter);search.addEventListener('input',filter);filter();</script></html>'''
        page = page.replace('</h1>', '</h1><p><strong><a href="问题总结.html">先看问题总结与处理动作</a></strong> · <a href="问题总结.md">文字版</a></p>', 1)
        (stage / "材料核对入口.html").write_text(page, encoding="utf-8")
        readme = ["# " + plan.get("title", "材料工作区"), "", summary, "", note, "", "[打开可搜索入口](材料核对入口.html) · [集中待核事项](集中待核事项.md)", "", "系统历史状态：" + plan.get("historical_finance_as_of", "未刷新"), "", "## 按人查看", ""]
        readme[2:2] = ['[先看问题总结与处理动作](问题总结.html) · [文字版](问题总结.md)', '']
        readme += [f"- [{p}](01_按人材料/{quote(folder)}/材料索引.md)" for p, folder in person_dirs.items()]
        readme += ["", "## 来源和覆盖", "", "所有电子副本保持原件字节和SHA。多页PDF按完整文件只复制一次，各页在台账分别关联。纸质扫描及纯空白文件不复制为电子办理材料。票面购方审核、系统认证、预约占用、报销和付款状态分别判断。", "", *["- " + plain(n) for n in plan.get("notes", [])]]
        (stage / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")
        qa["electronic_files_copied"] = len(copies)
        qa["copy_manifest"] = [{"sha256": sha, "path": path} for sha, path in copies.items()]
        json_write(stage / "覆盖与副本校验.json", qa)
        json_write(stage / "材料工作区输入.json", plan)
        from checklist_delivery import register_delivery
        qa["table_delivery"] = register_delivery(stage, 'workspace', plan['case_id'],
                                                  plan.get('title', '材料清单'), published_source=output)
        json_write(stage / "覆盖与副本校验.json", qa)
        require(not output.exists(), f"输出已被其他进程创建: {output}")
        stage.rename(output)
        return qa
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="须为尚不存在的新目录；check-only时可省略")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if not args.check_only and args.output is None:
        parser.error("生成工作区须指定--output")
    plan = json.loads(args.input.read_text(encoding="utf-8"))
    try:
        result = validate(plan) if args.check_only else build(plan, args.output)
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f"材料工作区未生成: {exc}\n")
    print(json.dumps({k: v for k, v in result.items() if k != "copy_manifest"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
