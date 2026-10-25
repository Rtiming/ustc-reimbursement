#!/usr/bin/env python3
"""总账的薄远程适配器：本机只准备合同，ETL与持久化在受管服务器执行。"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile


SCHEMA = "reimbursement.remote-ledger.v1"
TOOL_DIR = Path(__file__).resolve().parent
INPUT_KEYS = frozenset({"receivables", "review", "workspace", "field_audit",
                        "electronic_inventory", "tencent_audit", "medical_audit",
                        "payee_candidates", "paper_links", "case"})
OPTIONAL_INPUT_KEYS = frozenset({"finance_audit"})
OUTPUT_NAMES = ("ledger.json", "ledger.sqlite", "summary.json", "workbook_data.json", "validation.json",
                "ledger.xlsx", "review.html", "workbook_validation.json", "amounts.svg", "manifest.json")
SOURCE_NAMES = ("reimbursement_ledger.py", "reimbursement_ledger_legacy.py", "reimbursement_ledger_export.py",
                "invoice_face_fields.py", "reimbursement_ledger_trace.py", "worker.py")
# 环境核验先于源码落位，因此使用不依赖源码路径的自包含probe。
PROBE_CODE = """import importlib.metadata,json,platform,sqlite3
print(json.dumps({'python':platform.python_version(),'sqlite':sqlite3.sqlite_version,'versions':{name:importlib.metadata.version(name) for name in ('XlsxWriter','Pillow')}},ensure_ascii=False,sort_keys=True))
"""


def environment_probe() -> dict:
    return {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
            "versions": {name: importlib.metadata.version(name) for name in ("XlsxWriter", "Pillow")}}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    # 先完整关闭临时文件再替换，避免中断留下半份意图/结果回执；Windows也可替换。
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=f".{path.name}.",
                                         dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def expected_totals(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"expenses", "persons", "amount_fen"}:
        raise ValueError("expected_totals须明确expenses/persons/amount_fen三个字段")
    if any(type(number) is not int or number < 0 for number in value.values()):
        raise ValueError("预期数量和分金额须为非负整数，不能使用浮点或bool")
    return dict(value)


def input_record(key: str, raw_path: object, **metadata: object) -> dict:
    if not isinstance(raw_path, str) or not Path(raw_path).is_absolute():
        raise ValueError(f"{key}输入须为本机绝对JSON文件路径")
    path = Path(raw_path).resolve()
    if not path.is_file() or path.suffix.lower() != ".json":
        raise ValueError(f"缺失或不是JSON输入：{key}: {path}")
    digest = sha256(path)
    return {"key": key, "source_path": str(path), "sha256": digest,
            "size_bytes": path.stat().st_size, "mount_path": f"json/{digest}.json", **metadata}


def normalize_request(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("请求须为JSON对象")
    case_id = raw.get("case_id")
    if not isinstance(case_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", case_id):
        raise ValueError("case_id须为稳定ASCII标识")
    paths = raw.get("input_paths")
    if not isinstance(paths, dict) or not INPUT_KEYS <= set(paths) <= INPUT_KEYS | OPTIONAL_INPUT_KEYS:
        raise ValueError("input_paths须包含约定的10个JSON输入key，可附加finance_audit")
    rows = [input_record(key, paths[key]) for key in sorted(paths)]
    if "paper_ocr" in raw:
        paper = raw["paper_ocr"]
        if not isinstance(paper, dict):
            raise ValueError("paper_ocr须为明确document_sha256和pages的对象")
        document_sha = paper.get("document_sha256")
        pages = paper.get("pages")
        if not isinstance(document_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", document_sha):
            raise ValueError("paper_ocr document_sha256须为64位小写十六进制")
        if not isinstance(pages, list) or not 1 <= len(pages) <= 1000:
            raise ValueError("paper_ocr须包含1至1000页现成JSON")
        seen = set()
        for page in pages:
            if not isinstance(page, dict) or type(page.get("page")) is not int or page["page"] < 1 or page["page"] in seen:
                raise ValueError("paper_ocr页号须为唯一正整数")
            seen.add(page["page"])
            rows.append(input_record(f"paper_ocr:{page['page']}", page.get("path"),
                                     document_sha256=document_sha, page=page["page"], kind="paper_ocr"))
    if "ocr_result_paths" in raw:
        result_paths = raw["ocr_result_paths"]
        if not isinstance(result_paths, list) or not 1 <= len(result_paths) <= 1000:
            raise ValueError("ocr_result_paths须包含1至1000个现成JSON路径")
        seen_paths = set()
        for value in result_paths:
            if not isinstance(value, str) or not Path(value).is_absolute():
                raise ValueError("ocr_result_paths须为本机绝对JSON文件路径")
            source = Path(value).resolve()
            if source in seen_paths:
                continue
            rows.append(input_record(f"ocr_result:{len(seen_paths)}", str(source), kind="ocr_result"))
            seen_paths.add(source)
    # 解释器是服务器路径；本机Windows不能按本机驱动器规则解释它。
    from remote_material_ocr import server_python_path
    return {"case_id": case_id, "expected_totals": expected_totals(raw.get("expected_totals")),
            "server_python": server_python_path(raw.get("server_python")), "inputs": rows}


def prepare(request_path: Path, project: Path, target: str = "master", probe_sha256: str | None = None) -> dict:
    if target != "master":
        raise ValueError("当前仅使用已核验的master受管计算入口")
    if probe_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", probe_sha256):
        raise ValueError("probe SHA须为64位小写十六进制")
    request_bytes = request_path.read_bytes()
    normalized = normalize_request(json.loads(request_bytes))
    project = project.resolve()
    if project.exists() and any(project.iterdir()):
        raise ValueError("任务目录必须为空，不覆盖旧任务和未知结果")
    sources = {"reimbursement_ledger.py": TOOL_DIR / "reimbursement_ledger.py",
               "reimbursement_ledger_legacy.py": TOOL_DIR / "reimbursement_ledger_legacy.py",
               "reimbursement_ledger_export.py": TOOL_DIR / "reimbursement_ledger_export.py",
               "invoice_face_fields.py": TOOL_DIR / "invoice_face_fields.py",
               "reimbursement_ledger_trace.py": TOOL_DIR / "reimbursement_ledger_trace.py",
               "worker.py": Path(__file__).resolve()}
    for name, path in sources.items():
        if not path.is_file():
            raise ValueError(f"总账源码尚未就绪：{name}")
    project.mkdir(parents=True, exist_ok=True)
    project.chmod(0o700)
    for name, source in sources.items():
        shutil.copyfile(source, project / name)
    source_records = [{"filename": name, "sha256": sha256(project / name)} for name in SOURCE_NAMES]
    # Windows的代码仓和材料可能分属D/C盘，不能假定所有输入有公共盘符。
    # 同盘仍直接声明输入；跨盘只冻结去重后的JSON，业务解析仍由服务器执行。
    try:
        common_root = Path(os.path.commonpath([str(Path(row["source_path"]).parent) for row in normalized["inputs"]]))
        workspace_relative = os.path.relpath(common_root, project)
        cross_volume = False
    except ValueError:
        common_root, cross_volume = project, True
        workspace_relative = "."
    manifest = {"schema": SCHEMA, **normalized, "source_workspace_root": str(common_root),
                "input_staging": "project_json_copies" if cross_volume else "direct_workspace",
                "request_sha256": hashlib.sha256(request_bytes).hexdigest(), "sources": source_records}
    write_json(project / "inputs-manifest.json", manifest)
    inputs = [{"id": "manifest", "root": "project", "path": "inputs-manifest.json", "mount_path": "inputs-manifest.json", "required": True}]
    distinct = {row["mount_path"]: row for row in normalized["inputs"]}
    for index, row in enumerate(distinct.values()):
        if cross_volume:
            relative = f"frozen-inputs/{row['sha256']}.json"
            frozen = project / relative
            frozen.parent.mkdir(exist_ok=True)
            shutil.copyfile(row["source_path"], frozen)
            frozen.chmod(0o600)
            if sha256(frozen) != row["sha256"]:
                raise ValueError(f"跨盘冻结时输入SHA变化：{row['key']}")
        else:
            relative = str(Path(row["source_path"]).relative_to(common_root))
        inputs.append({"id": f"source-{index:04d}", "root": "project" if cross_volume else "workspace",
                       "path": relative,
                       "mount_path": row["mount_path"], "required": True})
    python = normalized["server_python"]
    contract = {
        "schema": "rtime.compute.project.v1", "project_id": "reimbursement-ledger",
        "workspace_root": workspace_relative,
        "source": {"kind": "git", "exploratory_include": list(SOURCE_NAMES)}, "inputs": inputs,
        "command": {"argv": [python, "{project_root}/worker.py", "work"], "working_directory": "."},
        "environment": {"kind": "system", "lock_files": []},
        "resources": {"cores": 1, "memory_gb": 4, "walltime_minutes": 15, "gpus": 0,
                      "array": {"count": 1, "max_concurrency": 1, "items": [normalized["case_id"]]}},
        "outputs": {"required_globs": list(OUTPUT_NAMES), "collect_globs": list(OUTPUT_NAMES)},
        "allowed_targets": [target],
    }
    if probe_sha256 is not None:
        contract["environment"].update({"probe_argv": [python, "-c", PROBE_CODE], "expected_probe_sha256": probe_sha256})
    write_json(project / "rtime-compute.json", contract)
    return {"project_dir": str(project), "case_id": normalized["case_id"], "input_keys": sorted(row['key'] for row in normalized['inputs']),
            "distinct_input_files": len(distinct), "expected_totals": normalized["expected_totals"],
            "sources": source_records, "request_sha256": manifest["request_sha256"],
            "input_manifest_sha256": sha256(project / "inputs-manifest.json"),
            "contract_file_sha256": sha256(project / "rtime-compute.json"),
            "probe_sha256": probe_sha256,
            "output_names": list(OUTPUT_NAMES),
            "compute_argv": ["rtime", "compute", "run", str(project), "--target", target, "--mode", "exploratory", "--apply"],
            "next_step": "给本任务独立源码快照建立Git基线，再按compute_argv由公共入口冻结、执行和回收"}


def read_input(root: Path, row: dict) -> object:
    name = row.get("mount_path", "")
    digest = row.get("sha256", "")
    if not isinstance(name, str) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or name != f"json/{digest}.json":
        raise ValueError("输入mount路径或SHA不符合冻结合同")
    # 保留CAS符号链接，按最终读取字节的SHA核验，不要求CAS落在mount目录内。
    path = root / PurePosixPath(name)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError(f"输入字节SHA漂移：{row.get('key')}")
    return json.loads(raw)


def work() -> dict:
    env_input = os.environ.get("RTIME_COMPUTE_INPUT_ROOT")
    env_output = os.environ.get("RTIME_COMPUTE_OUTPUT_DIR")
    run_id = os.environ.get("RTIME_COMPUTE_RUN_ID")
    if not env_input or not env_output or not run_id:
        raise ValueError("总账worker必须由服务器rtime compute提供输入、输出与run身份，不允许本机直接计算")
    input_root, output = Path(env_input).resolve(), Path(env_output).resolve()
    if not input_root.is_dir() or not output.is_dir() or output == input_root or output.is_relative_to(input_root):
        raise ValueError("受管输入和输出目录不存在或相互重叠")
    if any(output.iterdir()):
        raise ValueError("受管输出已有结果，先核对原任务，禁止覆盖或重放")
    manifest_path = input_root / "inputs-manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get("schema") != SCHEMA:
        raise ValueError("总账输入清单schema不支持")
    totals = expected_totals(manifest.get("expected_totals"))
    rows = manifest.get("inputs")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("受管输入清单格式错误")
    if not all(isinstance(row.get("key"), str) for row in rows):
        raise ValueError("受管输入key须为字符串")
    paper_rows = [row for row in rows if row.get("kind") == "paper_ocr"]
    ocr_rows = [row for row in rows if row.get("kind") == "ocr_result"]
    normal_rows = [row for row in rows if row.get("kind") is None]
    keys = {row["key"] for row in normal_rows}
    if (len(rows) != len({row["key"] for row in rows}) or len(paper_rows) + len(ocr_rows) + len(normal_rows) != len(rows)
            or not INPUT_KEYS <= keys <= INPUT_KEYS | OPTIONAL_INPUT_KEYS):
        raise ValueError("受管输入须为10个必需key及可选finance_audit，key不能重复")
    if paper_rows:
        if len(paper_rows) > 1000:
            raise ValueError("受管paper_ocr最多1000页")
        document_sha = paper_rows[0].get("document_sha256")
        if not isinstance(document_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", document_sha):
            raise ValueError("受管paper_ocr原扫描SHA格式错误")
        for row in paper_rows:
            page = row.get("page")
            if (type(page) is not int or page < 1 or row["key"] != f"paper_ocr:{page}"
                    or row.get("document_sha256") != document_sha
                    or not isinstance(row.get("source_path"), str) or not row["source_path"]):
                raise ValueError("受管paper_ocr页号、原扫描SHA或来源不一致")
    if ocr_rows:
        if len(ocr_rows) > 1000 or {row["key"] for row in ocr_rows} != {f"ocr_result:{i}" for i in range(len(ocr_rows))}:
            raise ValueError("受管ocr_result须为最多1000个从0开始的连续唯一索引")
        source_paths = set()
        for row in ocr_rows:
            source = row.get("source_path")
            # 来源是准备端路径，不按服务器宿主系统解释Windows驱动器。
            if (not isinstance(source, str) or not source
                    or not (PurePosixPath(source).is_absolute() or PureWindowsPath(source).is_absolute())
                    or source in source_paths):
                raise ValueError("受管ocr_result来源须为唯一绝对路径")
            source_paths.add(source)
    source_root = Path(__file__).resolve().parent
    records = manifest.get("sources")
    if not isinstance(records, list) or len(records) != len(SOURCE_NAMES) or {row.get("filename") for row in records} != set(SOURCE_NAMES):
        raise ValueError("受管源码清单不完整")
    for row in records:
        if sha256(source_root / row["filename"]) != row.get("sha256"):
            raise ValueError(f"源码SHA漂移：{row['filename']}")
    inputs_by_key = {row["key"]: read_input(input_root, row) for row in normal_rows}
    if paper_rows:
        inputs_by_key["paper_ocr"] = {"document_sha256": document_sha,
            "pages": [{"page": row["page"], "ocr_source": {"path": row["source_path"], "sha256": row["sha256"]},
                       "ocr": read_input(input_root, row)} for row in sorted(paper_rows, key=lambda row: row["page"])]}
    if ocr_rows:
        inputs_by_key["ocr_results"] = {"reports": [
            {"source_path": row["source_path"], "sha256": row["sha256"], "result": read_input(input_root, row)}
            for row in sorted(ocr_rows, key=lambda row: int(row["key"].split(":")[1]))]}
    # 只从公共层核验过的同一源码快照加载固定接口，不接受用户提供任意模块或命令。
    sys.path.insert(0, str(source_root))
    from reimbursement_ledger_legacy import build_legacy_ledger
    from reimbursement_ledger import write_ledger
    from reimbursement_ledger_export import export_workbook
    dataset = build_legacy_ledger(inputs_by_key, manifest["case_id"])
    if not isinstance(dataset, dict) or dataset.get("case_id") != manifest["case_id"]:
        raise ValueError("适配器返回案件身份不符")
    dataset['provenance'] = {**dataset.get('provenance', {}), 'remote_run_id': run_id, 'input_manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
                             'sources': records, 'inputs': rows, 'environment': environment_probe()}
    # Keep the original path as evidence, and a separate root-relative reference for another device.
    source_root_text = manifest.get('source_workspace_root', '').replace('\\', '/').rstrip('/')
    for document in dataset.get('documents', []):
        for reference in document.get('references', []):
            value = reference.get('path')
            if isinstance(value, str) and source_root_text and value.replace('\\', '/').startswith(source_root_text + '/'):
                reference['portable_ref'] = {'root': 'materials', 'path': value.replace('\\', '/')[len(source_root_text)+1:]}
    os.umask(0o077)
    ledger_result = write_ledger(dataset, output, totals)
    workbook_result = export_workbook(output / "workbook_data.json", output)
    for name in OUTPUT_NAMES[:-1]:
        if not (output / name).is_file():
            raise ValueError(f"总账固定产物缺失：{name}")
    write_json(output / "manifest.json", {**manifest, "run_id": run_id,
               "input_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
               "output_hashes": {name: sha256(output / name) for name in OUTPUT_NAMES[:-1]}})
    return {"case_id": manifest["case_id"], "run_id": run_id, "outputs": list(OUTPUT_NAMES),
            "ledger_result": ledger_result, "workbook_result": workbook_result}


def verify_collection(summary: dict, prepared: dict) -> dict:
    """仅核回执及固定产物字节；不在本机重算费用或重新导出工作簿。"""
    if summary.get("status") != "completed" or not summary.get("run_id"):
        raise ValueError("原远程任务尚未确认完成，须按原run恢复，不重投")
    destination = summary.get("collection", {}).get("destination")
    if not isinstance(destination, str) or not Path(destination).is_absolute():
        raise ValueError("固定计算工具未返回可核验的回收目录")
    root = Path(destination).resolve()
    collection = json.loads((root / "collection.json").read_text(encoding="utf-8"))
    if (collection.get("schema") != "rtime.compute.collection.v1"
            or collection.get("run_id") != summary["run_id"]
            or collection.get("completeness") != "complete"
            or collection.get("terminal_state") != "completed"
            or collection.get("transfer_verified") is not True
            or collection.get("receipt_count") != 1
            or collection.get("expected_receipt_count") != 1
            or collection.get("receipt_states") != {"completed": 1}
            or collection.get("missing_outputs") != []
            or collection.get("failed_tasks") != []
            or collection.get("missing_receipt_tasks") != []):
        raise ValueError("原回收证据未完整通过，不发布或重投")
    receipt = json.loads((root / "receipts/task-000000.json").read_text(encoding="utf-8"))
    expected_probe = prepared.get("probe_sha256")
    if not isinstance(expected_probe, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_probe):
        raise ValueError("准备回执缺少环境probe身份，先从原任务合同核验，不猜补")
    if (receipt.get("schema") != "rtime.compute.task-receipt.v1"
            or receipt.get("state") != "completed" or type(receipt.get("exit_code")) is not int or receipt["exit_code"] != 0
            or type(receipt.get("worker_exit_code")) is not int or receipt["worker_exit_code"] != 0
            or receipt.get("environment_probe_sha256") != expected_probe
            or receipt.get("missing_required_globs") != []
            or receipt.get("run_id") != summary["run_id"] or receipt.get("task_index") != 0
            or receipt.get("project_id") != "reimbursement-ledger"):
        raise ValueError("原task回执未成功或身份不符")
    artifacts = collection.get("artifacts")
    expected_paths = {f"task-000000/{name}" for name in OUTPUT_NAMES}
    if (not isinstance(artifacts, list) or len(artifacts) != len(OUTPUT_NAMES)
            or collection.get("artifact_count") != len(OUTPUT_NAMES)
            or {row.get("path") for row in artifacts} != expected_paths):
        raise ValueError("回收产物必须恰为本合同声明的10项")
    task = root / "outputs/task-000000"
    hashes = {}
    for row in artifacts:
        name = PurePosixPath(row["path"]).name
        path = task / name
        digest = row.get("sha256")
        if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or not path.is_file() or path.is_symlink() or sha256(path) != digest
                or path.stat().st_size != row.get("size_bytes")):
            raise ValueError(f"回收产物SHA或字节数不符：{name}")
        hashes[name] = digest
    worker_artifacts = receipt.get("artifacts")
    if (not isinstance(worker_artifacts, list) or len(worker_artifacts) != len(OUTPUT_NAMES)
            or {row.get("path") for row in worker_artifacts} != set(OUTPUT_NAMES)
            or any(row.get("sha256") != hashes[row["path"]]
                   or row.get("size_bytes") != (task / row["path"]).stat().st_size for row in worker_artifacts)):
        raise ValueError("worker与回收清单的产物身份不一致")
    manifest = json.loads((task / "manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("schema") != SCHEMA or manifest.get("case_id") != prepared["case_id"]
            or manifest.get("run_id") != summary["run_id"]
            or manifest.get("request_sha256") != prepared["request_sha256"]
            or manifest.get("input_manifest_sha256") != prepared["input_manifest_sha256"]
            or manifest.get("expected_totals") != prepared["expected_totals"]
            or manifest.get("sources") != prepared["sources"]
            or manifest.get("output_hashes") != {name: hashes[name] for name in OUTPUT_NAMES[:-1]}):
        raise ValueError("worker产物清单未绑定同一案件、输入、源码或输出SHA")
    validation = json.loads((task / "validation.json").read_text(encoding="utf-8"))
    workbook = json.loads((task / "workbook_validation.json").read_text(encoding="utf-8"))
    cells = workbook.get("saved_cell_validation", {})
    chart = workbook.get("chart_validation", {})
    if validation.get("ok") is not True:
        raise ValueError("总账业务核验未通过，不发布")
    if (workbook.get("ok") is not True or workbook.get("automatic_quality_gate") is not True
            or workbook.get("manual_preview_required") is not False
            or workbook.get("formula_errors") != []
            or workbook.get("formula_cached_values_present") is not True
            or workbook.get("server_computed_cached_values") is not True
            or cells.get("ok") is not True or cells.get("errors") != []
            or type(cells.get("cells_checked")) is not int or cells["cells_checked"] < 1
            or chart.get("ok") is not True or chart.get("format") != "SVG"
            or chart.get("source") != "same_server_summary"
            or chart.get("manual_preview_required") is not False
            or chart.get("persons") != prepared["expected_totals"]["persons"]):
        raise ValueError("工作簿单元格/公式/缓存或图表自动质量门未通过，不发布")
    return {"task_output_dir": str(task), "collection_path": str(root / "collection.json"),
            "receipt_path": str(root / "receipts/task-000000.json"), "hashes": hashes,
            "quality_gates": {"ledger": True, "saved_cells": True, "formulas": True, "chart": True,
                              "automatic_quality_gate": True, "manual_preview_required": False}}


def publish_verified(verified: dict, publish: Path) -> dict:
    if publish.exists():
        raise FileExistsError("发布目录已存在，禁止覆盖")
    publish.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{publish.name}.stage-", dir=publish.parent))
    reserved = False
    try:
        for name in OUTPUT_NAMES:
            target = stage / name
            shutil.copyfile(Path(verified["task_output_dir"]) / name, target)
            target.chmod(0o600)
            if sha256(target) != verified["hashes"][name]:
                raise ValueError(f"发布复制后SHA不符：{name}")
        # 在复制与核验全部完成后独占目标名，防止覆盖并发创建的发布目录。
        publish.mkdir(mode=0o700)
        reserved = True
        if os.name == "nt":
            publish.rmdir()  # Windows rename本身拒绝既有目标。
            reserved = False
            stage.rename(publish)
        else:
            os.replace(stage, publish)  # 只替换本调用刚独占的空目录，10项产物一起可见。
            reserved = False
        return {"publish_dir": str(publish), "files": [{"name": name, "sha256": verified["hashes"][name]} for name in OUTPUT_NAMES],
                "sync_boundary": "publish_dir_only; jobs/runs/results remain machine-local"}
    finally:
        if stage.exists():
            shutil.rmtree(stage)
        if reserved:
            try:
                publish.rmdir()
            except OSError:
                pass  # 不删除后来出现的任何文件。


def recover_pipeline(receipt_path: Path, collection_root: Path, *, apply: bool = False) -> dict:
    """只核验、发布原run已经回收的结果；不提交、等待或重跑计算。"""
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("schema") != "reimbursement.ledger-pipeline.v1" or not isinstance(receipt.get("prepared"), dict):
        raise ValueError("不是已准备的总账流水线回执")
    project = receipt_path.resolve().parent
    prepared = dict(receipt["prepared"])
    if Path(prepared.get("project_dir", "")).resolve() != project:
        raise ValueError("源码任务目录与原准备回执不一致，跨设备任务先走公共attach流程")
    if sha256(project / "inputs-manifest.json") != prepared.get("input_manifest_sha256"):
        raise ValueError("原输入清单SHA已变化")
    run_path = receipt.get("destination") or receipt.get("run_summary", {}).get("run_dir")
    if not run_path:
        argv = receipt.get("compute_argv", [])
        if argv.count("--destination") == 1:
            run_path = argv[argv.index("--destination") + 1]
    if not isinstance(run_path, str) or not Path(run_path).is_absolute():
        raise ValueError("缺少原run目录，先查原计算任务；不另开任务")
    run = json.loads((Path(run_path) / "run.json").read_text(encoding="utf-8"))
    contract_path = project / "rtime-compute.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    # run.contract_sha256是公共工具的归一化语义身份，不是合同文件字节SHA。
    # 使用它实际保存的合同副本比对原字节，并核对本工具依赖的执行/输入身份。
    contract_sha = sha256(contract_path)
    if (run.get("project_id") != "reimbursement-ledger" or not run.get("run_id")
            or sha256(Path(run_path) / "project-contract.json") != contract_sha
            or prepared.get("contract_file_sha256", contract_sha) != contract_sha
            or run.get("command") != contract.get("command")
            or not any(row.get("id") == "manifest" and row.get("sha256") == prepared["input_manifest_sha256"]
                       for row in run.get("input_records", []))):
        raise ValueError("原run未绑定同一总账合同和输入清单")
    previous_id = receipt.get("run_summary", {}).get("run_id")
    if previous_id and previous_id != run["run_id"]:
        raise ValueError("原回执与run身份不一致")
    probe = run.get("environment", {}).get("expected_probe_sha256")
    if prepared.get("probe_sha256") is not None and prepared["probe_sha256"] != probe:
        raise ValueError("原准备回执与run环境身份不一致")
    # 旧回执未单列probe，只允许从已核验合同对应的原run补出同一身份。
    if probe != contract.get("environment", {}).get("expected_probe_sha256"):
        raise ValueError("原run与合同的环境身份不一致")
    prepared["probe_sha256"] = probe
    summary = {"status": "completed", "run_id": run["run_id"],
               "collection": {"destination": str(collection_root.resolve())}}
    verified = verify_collection(summary, prepared)
    publish = Path(receipt["publish_dir"])
    if not publish.is_absolute():
        raise ValueError("原发布位置不是绝对路径")
    already = publish.exists()
    if already and (not publish.is_dir() or {p.name for p in publish.iterdir()} != set(OUTPUT_NAMES)
                    or any((publish / name).is_symlink() or not (publish / name).is_file()
                           or sha256(publish / name) != verified["hashes"][name] for name in OUTPUT_NAMES)):
        raise ValueError("发布目录已有其他或变化的内容，保留原状，不覆盖")
    result = {"ok": True, "status": "already_published" if already else "verified_ready_to_publish",
              "apply": apply, "run_id": run["run_id"], "publish_dir": str(publish),
              "verified": verified, "system_write": False, "compute_submitted": False}
    if apply:
        publication = {"publish_dir": str(publish), "files": [{"name": name, "sha256": digest}
                       for name, digest in verified["hashes"].items()]} if already else publish_verified(verified, publish)
        receipt.update(ok=True, status="published", verified=verified, publication=publication)
        receipt.setdefault("recoveries", []).append({"run_id": run["run_id"], "collection": str(collection_root.resolve()),
                                                     "already_published": already})
        write_json(receipt_path, receipt)
        result.update(status="published", publication=publication)
    return result


def pipeline_plan(normalized: dict, project: Path, publish: Path, *, target: str = "master", probe_sha256: str,
                  destination: Path | None = None, results_to: Path | None = None, apply: bool = False) -> dict:
    """校验路径和参数并返回固定调用计划，不写文件或启动计算。"""
    project, publish = project.resolve(), publish.resolve()
    destination = destination.resolve() if destination is not None else None
    results_to = results_to.resolve() if results_to is not None else None
    if target != "master" or not re.fullmatch(r"[0-9a-f]{64}", probe_sha256):
        raise ValueError("run须使用master及明确的64位环境probe SHA")
    for label, path in [("project", project), ("publish", publish), ("destination", destination), ("results-to", results_to)]:
        if path is not None and path.exists():
            raise FileExistsError(f"{label}目录已存在；核对原任务，不覆盖或重复")
    for transient in (project, destination, results_to):
        if transient is not None and (publish == transient or publish.is_relative_to(transient) or transient.is_relative_to(publish)):
            raise ValueError("publish-dir必须与本机job/run/results目录分开")
    transient_paths = [p for p in (project, destination, results_to) if p is not None]
    for index, first in enumerate(transient_paths):
        if any(first == other or first.is_relative_to(other) or other.is_relative_to(first)
               for other in transient_paths[index + 1:]):
            raise ValueError("本机source/run/results目录须互不重叠")
    compute = ["rtime", "compute", "run", str(project), "--target", target, "--mode", "exploratory", "--apply"]
    if destination is not None:
        compute += ["--destination", str(destination)]
    if results_to is not None:
        compute += ["--results-to", str(results_to)]
    if sys.platform == "win32":
        # CreateProcess不会像交互终端那样按PATHEXT补出rtime.cmd。
        compute[0] = shutil.which("rtime") or "rtime"
        # 已部署入口是受管批处理；不把路径里的CMD元字符交给批处理展开。
        if compute[0].lower().endswith((".cmd", ".bat")) and any(
                any(char in arg for char in '&|<>^%!()"\r\n\x00') for arg in compute):
            raise ValueError("Windows受管批处理入口的路径不支持CMD元字符，请使用普通目录名")
    receipt = {"schema": "reimbursement.ledger-pipeline.v1", "ok": False, "case_id": normalized["case_id"],
               "status": "preview", "apply": apply, "project_dir": str(project), "publish_dir": str(publish),
               "destination": str(destination) if destination else None,
               "results_to": str(results_to) if results_to else None,
               "expected_totals": normalized["expected_totals"], "compute_argv": compute,
               "input_count": len(normalized["inputs"]),
               "distinct_input_files": len({row["sha256"] for row in normalized["inputs"]}),
               "outputs": list(OUTPUT_NAMES), "system_write": False,
               "sync_boundary": "publish_dir_only; jobs/runs/results remain machine-local"}
    return receipt


def run_pipeline(request: Path, project: Path, publish: Path, *, target: str = "master", probe_sha256: str,
                 destination: Path | None = None, results_to: Path | None = None, apply: bool = False) -> dict:
    normalized = normalize_request(json.loads(request.read_text(encoding="utf-8")))
    receipt = pipeline_plan(normalized, project, publish, target=target, probe_sha256=probe_sha256,
                            destination=destination, results_to=results_to, apply=apply)
    if not apply:
        return receipt
    project, publish = Path(receipt["project_dir"]), Path(receipt["publish_dir"])
    compute = receipt["compute_argv"]
    prepared = prepare(request, project, target, probe_sha256)
    receipt_path = project / "pipeline-receipt.json"
    receipt.update(status="prepared", prepared=prepared, receipt_path=str(receipt_path))
    write_json(receipt_path, receipt)
    compute_started = False
    try:
        for command in (["git", "init", "-b", "evidence"],
                        ["git", "add", "--", *SOURCE_NAMES, "inputs-manifest.json", "rtime-compute.json"],
                        ["git", "-c", "user.name=rtime", "-c", "user.email=rtime@local", "commit", "-m", "冻结远程报销总账任务源码与输入合同"]):
            process = subprocess.run(command, cwd=project, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if process.returncode != 0:
                raise RuntimeError("任务Git基线未完成：" + process.stderr)
            if command[1] == "init":
                exclude = project / ".git/info/exclude"
                if not exclude.parent.is_dir():
                    raise ValueError("Git未在本任务目录创建独立基线，不继续冻结")
                existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
                if "/pipeline-receipt.json" not in existing.splitlines():
                    exclude.write_text(existing + ("\n" if existing and not existing.endswith("\n") else "")
                                       + "/pipeline-receipt.json\n", encoding="utf-8")
                if (project / "frozen-inputs").exists():
                    with exclude.open("a", encoding="utf-8") as stream:
                        stream.write("/frozen-inputs/\n")
        receipt["status"] = "running_verify_before_retry"
        write_json(receipt_path, receipt)
        compute_started = True
        process = subprocess.run(compute, capture_output=True, text=True, encoding="utf-8", errors="replace")
        receipt.update(compute_exit_code=process.returncode, compute_stdout=process.stdout, compute_stderr=process.stderr)
        summary = json.loads(process.stdout)
        receipt["run_summary"] = summary
        if process.returncode != 0:
            state = summary.get("wait", {}).get("state")
            receipt["status"] = "failed_original_run" if state == "failed" else "unknown_verify_before_retry"
            receipt["error"] = "原计算调用未成功；保留原run和回执，不换任务或重投"
        elif summary.get("status") != "completed":
            receipt.update(status="unknown_verify_before_retry", error="原远程任务尚未确认完成，不发布或重投")
        else:
            verified = verify_collection(summary, prepared)
            receipt["verified"] = verified
            receipt["publication"] = publish_verified(verified, publish)
            receipt.update(ok=True, status="published")
    except Exception as exc:
        receipt.update(status="failed_output_verification" if "run_summary" in receipt else
                       "unknown_verify_before_retry" if compute_started else "preparation_failed", error=str(exc))
    write_json(receipt_path, receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare")
    p.add_argument("--request", type=Path, required=True)
    p.add_argument("--project-dir", type=Path, required=True)
    p.add_argument("--target", default="master")
    p.add_argument("--probe-sha256")
    p = commands.add_parser("run", help="预览或执行固定远程计算、自动核验及全新目录发布")
    p.add_argument("--request", type=Path, required=True)
    p.add_argument("--project-dir", type=Path, required=True)
    p.add_argument("--target", default="master")
    p.add_argument("--probe-sha256", required=True)
    p.add_argument("--destination", type=Path)
    p.add_argument("--results-to", type=Path)
    p.add_argument("--publish-dir", type=Path, required=True)
    p.add_argument("--apply", action="store_true")
    p = commands.add_parser("probe")
    p.add_argument("--sha256", action="store_true", help="输出正常probe stdout（含末尾换行）的SHA")
    commands.add_parser("work")
    p = commands.add_parser("recover", help="核验原run已回收结果并继续发布，绝不重投")
    p.add_argument("--receipt", type=Path, required=True)
    p.add_argument("--collection", type=Path, required=True)
    p.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "probe":
            raw = json.dumps(environment_probe(), ensure_ascii=False, sort_keys=True) + "\n"
            print(hashlib.sha256(raw.encode()).hexdigest() if args.sha256 else raw, end="\n" if args.sha256 else "")
            return 0
        if args.command == "run":
            result = run_pipeline(args.request, args.project_dir, args.publish_dir, target=args.target,
                                  probe_sha256=args.probe_sha256, destination=args.destination,
                                  results_to=args.results_to, apply=args.apply)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0 if not args.apply or result.get("ok") else 1
        if args.command == "recover":
            print(json.dumps(recover_pipeline(args.receipt, args.collection, apply=args.apply), ensure_ascii=False, sort_keys=True))
            return 0
        result = prepare(args.request, args.project_dir, args.target, args.probe_sha256) if args.command == "prepare" else work()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
