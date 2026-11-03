"""在原安装目录升级或回退源码，保留案件、私人规则和原任务的绝对路径。"""

import argparse
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request
import uuid
import zipfile

from assistant_api import AssistantService, atomic_json, read_json
from build_distribution import validate_member
from distribution_smoke import verify_members
from material_intake import reject_links
from material_review_launch import launch_lock
from release_update import extract_verified
from standards_import import replace_bytes
from workspace_config import read_installation, runtime_python, private_environment


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def source_files(root, manifest):
    paths = [r["path"] for r in manifest["files"]] + ["MANIFEST.json"]
    folded = set()
    result = {}
    for relative in paths:
        validate_member(relative, b"")
        if relative.casefold() in folded:
            raise ValueError("源码成员名称冲突")
        folded.add(relative.casefold())
        path = root / relative
        reject_links(path)
        result[relative] = path.read_bytes()
    return result


def idle(root):
    service = AssistantService(root)
    for row in service.cases()["cases"]:
        case_id = row["case_id"]
        _, paths = service.resolve(case_id)
        if service.active_jobs(case_id):
            raise ValueError("仍有原任务未结束，暂不更新：" + case_id)
        if (paths["state"].parent / "pending_write.json").exists():
            raise ValueError("仍有财务原操作待核，保留原版本接续：" + case_id)
    # 批次状态也必须闭合，不能只因其worker退出就认为允许升级。
    for path in (root / ".local/approver-batches").glob("*/progress.json"):
        if read_json(path).get("state") in {
            "running",
            "needs_verification",
            "needs_review",
        }:
            raise ValueError("原审批批次仍待本人或结果核验")


def stop_workbench(root):
    from platform_runtime import process_alive

    record = root / ".local/operator-workbench/server.json"
    if not record.exists():
        return
    info = read_json(record)
    if process_alive(info.get("pid")) is not True:
        return
    origin = info.get("origin", "")
    if not re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}", origin):
        raise ValueError("工作台原地址无效")
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )

    def call(path, value=None, headers=None):
        request = urllib.request.Request(
            origin + path,
            data=json.dumps(value).encode() if value is not None else None,
            headers={
                "Content-Type": "application/json",
                "Origin": origin,
                **(headers or {}),
            },
        )
        with opener.open(request, timeout=5) as response:
            return json.load(response)

    health = call("/health")
    if health.get("instance_id") != info.get("instance_id"):
        raise ValueError("原端口属于另一服务，未接管")
    token = info.get("url", "").partition("#launch=")[2]
    if not token:
        raise ValueError("缺本机原工作台入口，先从工作台退出服务")
    call("/api/session", {"token": token})
    csrf = call("/api/bootstrap")["csrf"]
    call("/api/stop", {}, {"X-Workbench-CSRF": csrf})
    until = time.monotonic() + 8
    while record.exists() and time.monotonic() < until:
        time.sleep(0.1)
    if record.exists():
        raise ValueError("原工作台尚未退出，未替换程序")


def install_check(root, folder):
    folder.mkdir(parents=True, exist_ok=True)
    log = folder / "install.log"
    with log.open("wb") as stream:
        result = subprocess.run(
            [
                str(runtime_python(root)),
                "-X",
                "utf8",
                "-I",
                str(root / "tools/portable_install.py"),
            ],
            cwd=root,
            env=private_environment(root),
            stdout=stream,
            stderr=subprocess.STDOUT,
            timeout=900,
        )
    current = root / ".local/self-check-current.json"
    if (
        result.returncode
        or not current.exists()
        or read_json(current).get("ok") is not True
    ):
        raise ValueError("此版本安装自检未通过，详见" + str(log))
    return read_json(current)


def swap(root, before, after):
    touched = []
    try:
        for relative in sorted(set(before) | set(after)):
            old = before.get(relative)
            new = after.get(relative)
            if old == new:
                continue
            path = root / relative
            reject_links(path)
            if (path.read_bytes() if path.exists() else None) != old:
                raise ValueError("程序文件在更新前已改变：" + relative)
            if new is None:
                path.unlink()
            else:
                replace_bytes(path, new)
            touched.append(relative)
    except Exception:
        for relative in reversed(touched):
            path = root / relative
            if (path.read_bytes() if path.exists() else None) != after.get(relative):
                continue
            if before.get(relative) is None:
                path.unlink()
            else:
                replace_bytes(path, before[relative])
        raise


def prepare(root, archive, archive_sha256):
    archive = Path(archive).resolve()
    reject_links(archive)
    if (
        archive.stat().st_size > 128 * 1024 * 1024
        or not re.fullmatch("[a-f0-9]{64}", str(archive_sha256))
        or sha(archive.read_bytes()) != archive_sha256
    ):
        raise ValueError("更新ZIP的大小或SHA与本次选定内容不符")
    with zipfile.ZipFile(archive) as package:
        version = json.loads(package.read("ustc-reimbursement/MANIFEST.json"))[
            "version"
        ]
    stage = root / ".local/upgrades/staged" / archive_sha256
    reject_links(stage)
    if not stage.exists():
        stage.mkdir(parents=True)
        target = extract_verified(archive, stage, version)
    else:
        target = stage / "ustc-reimbursement"
    new = verify_members(target)
    old = verify_members(root)
    before = source_files(root, old)
    after = source_files(target, new)
    for relative in set(after) - set(before):
        if (root / relative).exists():
            raise ValueError("新版源码与已有用户文件同名，保留原文件：" + relative)
    return (
        before,
        after,
        {
            "ok": True,
            "action": "upgrade",
            "old_source": old["source_tree_sha256"],
            "new_source": new["source_tree_sha256"],
            "old_version": old["version"],
            "new_version": new["version"],
            "archive_sha256": archive_sha256,
            "changed_files": sum(
                before.get(k) != after.get(k) for k in set(before) | set(after)
            ),
            "cases_moved": False,
            "financial_write_executed": False,
            "executed": False,
        },
    )


def run(root, *, archive=None, archive_sha256=None, rollback_id=None, execute=False):
    root = Path(root).resolve()
    reject_links(root)
    if not (root / "MANIFEST.json").is_file() or not read_installation(root):
        raise ValueError("维护入口只操作已安装的独立发行包，不改开发工作树")
    folder = None
    if rollback_id:
        if not re.fullmatch("[a-f0-9]{32}", rollback_id):
            raise ValueError("回退编号须来自本机升级回执")
        folder = root / ".local/upgrades" / rollback_id
        reject_links(folder)
        receipt = read_json(folder / "receipt.json")
        old = verify_members(folder / "before")
        after = source_files(folder / "before", old)
        staged = (
            root
            / ".local/upgrades/staged"
            / receipt["archive_sha256"]
            / "ustc-reimbursement"
        )
        newer = verify_members(staged)
        if (
            old["source_tree_sha256"] != receipt["old_source"]
            or newer["source_tree_sha256"] != receipt["new_source"]
        ):
            raise ValueError("升级回退来源与原回执不同")
        new_files = source_files(staged, newer)
        # 中途断电时逐文件只接受原版本或本次新版本；遇到第三种内容保留，不能覆盖人工修改。
        before = {}
        for relative in set(after) | set(new_files):
            path = root / relative
            reject_links(path)
            raw = path.read_bytes() if path.exists() else None
            if raw not in (after.get(relative), new_files.get(relative)):
                raise ValueError(
                    "现有文件不属于此次升级前后版本，不能覆盖：" + relative
                )
            if raw is not None:
                before[relative] = raw
        current = {"source_tree_sha256": receipt["new_source"]}
        report = {
            "ok": True,
            "action": "rollback",
            "old_source": current["source_tree_sha256"],
            "new_source": old["source_tree_sha256"],
            "rollback_id": rollback_id,
            "executed": False,
            "cases_moved": False,
            "financial_write_executed": False,
        }
    else:
        before, after, report = prepare(root, archive, archive_sha256)
    if not execute:
        return report
    with launch_lock(root / ".local/release-maintenance"):
        active_marker = root / ".local/maintenance-active.json"
        if active_marker.exists():
            from platform_runtime import process_alive

            prior = read_json(active_marker)
            if process_alive(prior.get("pid")) is True:
                raise ValueError("另一个维护进程仍在运行")
            if not rollback_id or prior.get("rollback_id") != rollback_id:
                raise ValueError("原维护中断，先按原回退编号恢复")
        idle(root)
        stop_workbench(root)
        idle(root)
        if not rollback_id:
            rollback_id = uuid.uuid4().hex
            folder = root / ".local/upgrades" / rollback_id
            folder.mkdir(parents=True)
            for relative, raw in before.items():
                path = folder / "before" / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            atomic_json(
                folder / "receipt.json",
                {**report, "rollback_id": rollback_id, "phase": "prepared"},
            )
        audit = folder / ("rollback" if report["action"] == "rollback" else "apply")
        audit.mkdir(exist_ok=True)
        marker = root / ".local/maintenance-active.json"
        atomic_json(
            marker,
            {
                "rollback_id": rollback_id,
                "pid": os.getpid(),
                "operation": report["action"],
            },
        )
        idle(root)
        swapped = False
        try:
            swap(root, before, after)
            swapped = True
            if verify_members(root)["source_tree_sha256"] != report["new_source"]:
                raise ValueError("更新源码回读不符")
            validation = install_check(root, audit)
            report.update(
                executed=True,
                rollback_id=rollback_id,
                phase="installed",
                validation=validation,
            )
            marker.unlink()
        except Exception as error:
            restored = False
            if swapped:
                try:
                    swap(root, after, before)
                    restored = True
                except Exception as restore_error:
                    report["restore_error"] = str(restore_error)
            report.update(
                ok=False,
                executed=swapped,
                phase="restored_source_requires_check" if restored else "needs_review",
                error=str(error),
                source_restored=restored,
            )
            if restored:
                try:
                    report["restoration_validation"] = install_check(
                        root, audit / "restore"
                    )
                    report["phase"] = "restored_and_checked"
                    marker.unlink()
                except Exception as check_error:
                    report["restoration_error"] = str(check_error)
        atomic_json(audit / "result.json", report)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--sha256")
    parser.add_argument("--rollback")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        result = run(
            Path(__file__).resolve().parents[1],
            archive=args.archive,
            archive_sha256=args.sha256,
            rollback_id=args.rollback,
            execute=args.execute,
        )
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        subprocess.TimeoutExpired,
    ) as error:
        result = {"ok": False, "error": str(error), "financial_write_executed": False}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
