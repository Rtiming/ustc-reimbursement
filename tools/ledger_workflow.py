#!/usr/bin/env python3
"""总账日常入口：机器配置与材料请求分离；只由既有远程适配器计算和发布。"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path, PurePosixPath
import re
import shutil
import sys

import remote_reimbursement_ledger as remote

ROOT = Path(__file__).resolve().parents[1]
PROFILE_SCHEMA = "reimbursement.ledger-profile.v1"
PROFILE_DEFAULT = ROOT / ".local/ledger-profile.json"


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"须为JSON对象：{path}")
    return value


def load_profile(path: Path) -> dict:
    """配置只含机器位置和环境身份；案件金额及材料不放入机器配置。"""
    return validate_profile(read_json(path))


def validate_profile(profile: dict) -> dict:
    if not isinstance(profile, dict):
        raise ValueError("机器配置须为JSON对象")
    if set(profile) != {"schema", "target", "server_python", "probe_sha256", "roots"}:
        raise ValueError("机器配置须且仅含schema/target/server_python/probe_sha256/roots")
    if profile["schema"] != PROFILE_SCHEMA or profile["target"] != "master":
        raise ValueError("只支持ledger-profile.v1和已接入的master")
    if not isinstance(profile["probe_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", profile["probe_sha256"]):
        raise ValueError("probe_sha256须来自实际服务器环境核验")
    from remote_material_ocr import server_python_path
    profile["server_python"] = server_python_path(profile["server_python"])
    roots = profile["roots"]
    if not isinstance(roots, dict) or not roots:
        raise ValueError("roots须显式声明本机材料/代码根")
    for alias, raw in roots.items():
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", alias):
            raise ValueError(f"根别名不合法：{alias}")
        if not isinstance(raw, str) or not Path(raw).is_absolute() or not Path(raw).is_dir():
            raise ValueError(f"根须为已存在的本机绝对目录：{alias}")
        roots[alias] = str(Path(raw).resolve())
    return profile


def resolve_reference(value: object, roots: dict) -> str:
    """相对引用禁止跳出根，包括符号链接和另一平台的驱动器路径。"""
    if isinstance(value, str):
        if not Path(value).is_absolute():
            raise ValueError("旧式路径必须为本机绝对路径；跨设备使用root/path对象")
        return str(Path(value).resolve())
    if not isinstance(value, dict) or set(value) != {"root", "path"}:
        raise ValueError("可移植引用须且仅含root/path")
    alias, relative = value["root"], value["path"]
    if not isinstance(alias, str) or alias not in roots:
        raise ValueError(f"机器配置没有该根：{alias}")
    if (not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative
            or PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts
            or str(PurePosixPath(relative)) != relative or relative == "."):
        raise ValueError("path须为规范POSIX相对路径，不得有盘符、反斜杠或上级目录")
    root = Path(roots[alias]).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("引用经符号链接解析后越出已声明根")
    return str(path)


def transform_paths(request: dict, transform) -> dict:
    """仅映射合同定义的文件位置，不改JSON内部原始材料路径或业务事实。"""
    result = deepcopy(request)
    paths = result.get("input_paths")
    if not isinstance(paths, dict):
        raise ValueError("input_paths须为对象")
    result["input_paths"] = {key: transform(value) for key, value in paths.items()}
    if "paper_ocr" in result:
        paper = result["paper_ocr"]
        if not isinstance(paper, dict) or not isinstance(paper.get("pages"), list):
            raise ValueError("paper_ocr须含pages列表")
        for page in paper["pages"]:
            if not isinstance(page, dict) or "path" not in page:
                raise ValueError("paper_ocr每页须含path")
            page["path"] = transform(page["path"])
    if "ocr_result_paths" in result:
        if not isinstance(result["ocr_result_paths"], list):
            raise ValueError("ocr_result_paths须为列表")
        result["ocr_result_paths"] = [transform(value) for value in result["ocr_result_paths"]]
    return result


def resolved_request(request: dict, profile: dict) -> dict:
    result = transform_paths(request, lambda value: resolve_reference(value, profile["roots"]))
    if "server_python" in result and result["server_python"] != profile["server_python"]:
        raise ValueError("请求与机器配置的服务器环境不同，先核对环境，不静默覆盖")
    result["server_python"] = profile["server_python"]
    return result


def portable_request(request: dict, profile: dict) -> dict:
    def portable(value):
        path = Path(resolve_reference(value, profile["roots"]))
        candidates = [(alias, Path(root)) for alias, root in profile["roots"].items()
                      if path.is_relative_to(Path(root))]
        if not candidates:
            raise ValueError(f"路径不在配置根内，无法移植：{path}")
        alias, root = max(candidates, key=lambda pair: len(pair[1].parts))
        return {"root": alias, "path": path.relative_to(root).as_posix()}
    # 迁移先核原请求；不能把错误输入包装成成功转换。
    remote.normalize_request(resolved_request(request, profile))
    result = transform_paths(request, portable)
    result.pop("server_python", None)
    return result


def workflow_plan(request: dict, profile: dict, name: str, publish: Path) -> tuple[dict, dict]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
        raise ValueError("任务名须为1至64位ASCII字母、数字、短横线或下划线")
    if not publish.is_absolute():
        raise ValueError("发布目录须为本机绝对路径，不能相对当前终端猜测")
    base = (ROOT / ".local/ledger-runs" / name).resolve()
    if base.exists():
        raise FileExistsError(f"任务名已使用，先inspect原任务：{name}")
    resolved = resolved_request(request, profile)
    normalized = remote.normalize_request(resolved)
    plan = remote.pipeline_plan(normalized, base / "source", publish,
                                target=profile["target"], probe_sha256=profile["probe_sha256"],
                                destination=base / "run", results_to=base / "results")
    plan.update(workflow_name=name, workflow_dir=str(base), profile_schema=PROFILE_SCHEMA,
                executables={name: shutil.which(name) for name in ("git", "rtime")})
    return plan, resolved


def run_workflow(request_path: Path, profile_path: Path, name: str, publish: Path, *, apply=False) -> dict:
    request_bytes, profile_bytes = request_path.read_bytes(), profile_path.read_bytes()
    request = json.loads(request_bytes)
    if not isinstance(request, dict):
        raise ValueError("请求须为JSON对象")
    profile = validate_profile(json.loads(profile_bytes))
    plan, resolved = workflow_plan(request, profile, name, publish)
    if not apply:
        return plan
    missing = [name for name, path in plan["executables"].items() if path is None]
    if missing:
        raise ValueError("本机调用入口缺失，请按换设备指南配置PATH：" + ", ".join(missing))
    base = Path(plan["workflow_dir"])
    base.mkdir(parents=True, exist_ok=False, mode=0o700)
    record = {"schema": "reimbursement.ledger-workflow.v1", "status": "prepared",
              "started_at": datetime.now(timezone.utc).isoformat(), "plan": plan,
              "request_sha256": hashlib.sha256(request_bytes).hexdigest(),
              "profile_sha256": hashlib.sha256(profile_bytes).hexdigest(),
              "request_path": str(request_path.resolve()), "profile_path": str(profile_path.resolve()),
              "system_write": False}
    for filename, raw in (("request.json", request_bytes), ("profile.json", profile_bytes)):
        path = base / filename
        path.write_bytes(raw)
        path.chmod(0o600)
    remote.write_json(base / "resolved-request.json", resolved)
    remote.write_json(base / "workflow.json", record)
    try:
        result = remote.run_pipeline(base / "resolved-request.json", base / "source", publish,
                                     target=profile["target"], probe_sha256=profile["probe_sha256"],
                                     destination=base / "run", results_to=base / "results", apply=True)
        record.update(status=result["status"], ok=result["ok"], pipeline_receipt=str(base / "source/pipeline-receipt.json"))
    except Exception as exc:
        record.update(status="inspect_original_workflow", ok=False, error=str(exc))
    record["finished_at"] = datetime.now(timezone.utc).isoformat()
    remote.write_json(base / "workflow.json", record)
    return record


def inspect_workflow(name: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
        raise ValueError("任务名格式错误")
    base = ROOT / ".local/ledger-runs" / name
    record = read_json(base / "workflow.json")
    pipeline_path = base / "source/pipeline-receipt.json"
    if pipeline_path.is_file():
        pipeline = read_json(pipeline_path)
        if (pipeline.get("schema") != "reimbursement.ledger-pipeline.v1"
                or pipeline.get("case_id") != record.get("plan", {}).get("case_id")
                or Path(pipeline.get("project_dir", "")).resolve() != pipeline_path.parent.resolve()):
            raise ValueError("原任务与流水线回执身份不一致")
        # 发布恢复只更新原流水线回执；读取时以它为当前状态，保留入口历史。
        record["workflow_completion"] = {"status": record.get("status"), "ok": record.get("ok")}
        record.update(status=pipeline.get("status"), ok=pipeline.get("ok"))
        record["pipeline"] = {key: pipeline.get(key) for key in
                              ("status", "ok", "error", "receipt_path", "publication", "compute_exit_code")}
    run = base / "run"
    record["recovery_commands"] = [["rtime", "compute", action, str(run)] for action in ("status", "wait", "results")] if (run / "run.json").is_file() else []
    record["cleanup_rule"] = "不删除未知任务或意图；只按原run核验。已发布快照和原件不清理。"
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("plan", "run"):
        p = sub.add_parser(command)
        p.add_argument("--request", required=True, type=Path)
        p.add_argument("--profile", default=PROFILE_DEFAULT, type=Path)
        p.add_argument("--name", required=True)
        p.add_argument("--publish-dir", required=True, type=Path)
        if command == "run":
            p.add_argument("--apply", action="store_true")
    p = sub.add_parser("portable", help="把旧式请求转换为跨设备root/path引用，保留原请求")
    p.add_argument("--request", required=True, type=Path)
    p.add_argument("--profile", default=PROFILE_DEFAULT, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p = sub.add_parser("inspect", help="读取日志及原任务恢复命令，不启动或重投任务")
    p.add_argument("--name", required=True)
    args = parser.parse_args()
    try:
        if args.command == "portable":
            result = portable_request(read_json(args.request), load_profile(args.profile))
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            result = {"ok": True, "request_path": str(args.output.resolve()), "system_write": False}
        elif args.command == "inspect":
            result = inspect_workflow(args.name)
        else:
            result = run_workflow(args.request, args.profile, args.name, args.publish_dir,
                                  apply=args.command == "run" and args.apply)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 1 if result.get("ok") is False and result.get("status") != "preview" else 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc), "system_write": False}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
