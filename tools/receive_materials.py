"""明确选定文件的收件入口；副本入授权资料区后复用正式归档worker。"""

from pathlib import Path
import hashlib
import json

from assistant_contract import APIError
from material_intake import discover, normalize, reject_links, digest

FORMATS = {
    ".pdf",
    ".ofd",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".csv",
    ".txt",
    ".zip",
}


def plan(service, case_id, paths, grouping="unassigned"):
    service.resolve(case_id)
    if (
        not isinstance(paths, list)
        or not 1 <= len(paths) <= 100
        or any(not isinstance(p, str) for p in paths)
        or grouping not in {"unassigned", "directories"}
    ):
        raise APIError(
            "receive_inputs", "提供1至100个明确授权的原文件或材料文件夹绝对路径"
        )
    resolved = []
    for value in paths:
        path = Path(value)
        if not path.is_absolute():
            raise APIError("receive_path", "请给出原文件的绝对路径：" + value)
        reject_links(path)
        resolved.append(path.resolve())
    if len(set(resolved)) != len(resolved):
        raise APIError("receive_duplicate", "同一来源路径不能重复")
    request = normalize(
        {
            "schema": "reimbursement.material-intake.v1",
            "case_id": case_id,
            "sources": [
                {
                    "id": "source_" + str(i + 1),
                    "path": str(p),
                    "group_strip_dirs": 0 if grouping == "directories" else None,
                }
                for i, p in enumerate(resolved)
            ],
            "output_dir": str(
                service.material_roots[0] / "整理批次" / case_id / "agent_preview"
            ),
        }
    )
    files = discover(request)
    if len({row["path"] for row in files}) != len(files):
        raise APIError("receive_duplicate", "所选文件夹包含重复来源，请去掉重叠的选择")
    for row in files:
        if Path(row["path"]).suffix.lower() not in FORMATS:
            raise APIError(
                "receive_format", "请只选报销材料；暂不支持此文件：" + row["path"]
            )
        if not 0 < row["bytes"] <= 100 * 1024 * 1024:
            raise APIError(
                "receive_size", "单个文件须大于0且不超过100MiB：" + row["path"]
            )
    identity = {
        "case_id": case_id,
        "paths": list(map(str, resolved)),
        "grouping": grouping,
        "files": files,
    }
    signature = hashlib.sha256(
        json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    request["output_dir"] = str(
        service.material_roots[0] / "整理批次" / case_id / ("agent_" + signature[:24])
    )
    return request, files, signature


def run(
    service,
    case_id,
    paths,
    request_id,
    grouping="unassigned",
    execute=False,
    source_sha256=None,
):
    request, files, signature = plan(service, case_id, paths, grouping)
    if source_sha256 is not None and source_sha256 != signature:
        raise APIError(
            "receive_source_changed",
            "原文件、文件夹内容或分组已改变；请重新预览当前材料，未归档变更内容",
        )
    if not execute:
        return {
            "ok": True,
            "schema": "ustc-receive-materials-preview-v1",
            "case_id": case_id,
            "file_count": len(files),
            "source_bytes": sum(r["bytes"] for r in files),
            "source_sha256": signature,
            "files": files,
            "output_dir": request["output_dir"],
            "system_write": False,
            "filesystem_action_requested": False,
            "summary": "已核对所选材料；确认后保留原文件，将校验副本归档到本机资料区。",
            "next_action": {
                "tool": "reimbursement_receive_materials",
                "requires_user_confirmation": True,
                "label": "归档本批材料",
                "arguments": {
                    "case_id": case_id,
                    "paths": paths,
                    "request_id": request_id + "-archive",
                    "grouping": grouping,
                    "source_sha256": signature,
                    "execute": True,
                },
            },
        }
    from assistant_api import atomic_json, read_json
    from operator_workbench import Workbench
    from material_review_launch import launch_lock

    receipt_path = (
        service.root / ".local/intake-imports" / case_id / (signature + ".json")
    )
    reject_links(receipt_path)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    app = Workbench(service)
    copies = []
    with launch_lock(receipt_path):
        for row in files:
            path = Path(row["path"])
            reject_links(path)
            # 与人的文件选择器使用同一收件、限额和SHA回读，不扩大其他工具的材料根权限。
            with path.open("rb") as stream:
                saved = app.upload(
                    stream,
                    row["bytes"],
                    case_id=case_id,
                    batch_id=signature[:32],
                    name=row["source_id"] + "/" + row["relative_path"],
                    sha256=row["sha256"],
                )
            if digest(path) != row["sha256"]:
                raise APIError(
                    "receive_source_changed",
                    "复制期间原件改变；保留已校验副本，重新核对当前原件",
                )
            copies.append({**row, "copy_path": saved["path"]})
        receipt = {
            "schema": "ustc-receive-materials-v1",
            "case_id": case_id,
            "source_sha256": signature,
            "files": copies,
            "system_write": False,
        }
        if receipt_path.exists() and read_json(receipt_path) != receipt:
            raise APIError(
                "receive_receipt_changed", "原收件回执不一致，请保留原批次检查"
            )
        if not receipt_path.exists():
            atomic_json(receipt_path, receipt)
    bucket = service.material_roots[0] / "原始收件" / case_id / signature[:32]
    for source in request["sources"]:
        original = Path(source["path"])
        source["path"] = str(
            bucket / source["id"] / (original.name if original.is_file() else "")
        )
        if original.is_file():
            source["expected_sha256"] = next(
                row["sha256"] for row in files if row["source_id"] == source["id"]
            )
    return {
        **service.material_intake(case_id, request, request_id, True),
        "case_id": case_id,
        "source_sha256": signature,
        "import_receipt": str(receipt_path),
        "note": "原件保留，校验副本已收件；归档任务完成后继续核对。目录只是分组候选，金额另核。",
    }
