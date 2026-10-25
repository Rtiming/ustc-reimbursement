#!/usr/bin/env python3
"""只读盘点既有材料；哈希相同仅表示文件重复，不推断重复报销。"""
import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

SKIP = {".DS_Store", ".git", "__pycache__", ".venv", "node_modules"}


def inventory(sources):
    """Scan only explicitly supplied (source_id, directory) pairs."""
    summaries, files, digest_groups = [], [], defaultdict(list)
    for source_id, base in sources:
        base = Path(base).expanduser().resolve()
        extensions, size, count = Counter(), 0, 0
        if not base.is_dir():
            summaries.append({"id": source_id, "path": str(base), "missing": True})
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file() or p.is_symlink() or SKIP.intersection(p.relative_to(base).parts):
                continue
            # Hash without rewriting or loading whole archives into memory.
            sha = hashlib.sha256()
            with p.open("rb") as f:
                for block in iter(lambda: f.read(1024 * 1024), b""):
                    sha.update(block)
            n = p.stat().st_size
            record = {"source_id": source_id, "path": str(p), "relative_path": str(p.relative_to(base)),
                      "bytes": n, "extension": p.suffix.lower(), "sha256": sha.hexdigest()}
            files.append(record)
            digest_groups[record["sha256"]].append(str(p))
            extensions[p.suffix.lower() or "(none)"] += 1
            size += n
            count += 1
        summaries.append({"id": source_id, "path": str(base), "file_count": count,
                          "bytes": size, "extensions": dict(extensions)})
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "checked_at_timezone": "UTC",
              "scope_note": "包含旧版、历史参考及生成包，文件数不等于发票数；未迁移或修改源文件。",
              "sources": summaries, "files": files,
              "identical_file_groups": [{"sha256": k, "paths": v} for k, v in digest_groups.items() if len(v) > 1]}
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, action="append", default=[], help="待盘点目录，可重复；不默认扫描任何案件")
    ap.add_argument("--source", action="append", default=[], metavar="ID=PATH", help="带稳定标识的待盘点目录，可重复")
    ap.add_argument("--output", type=Path, required=True, help="新建盘点回执，不覆盖既有文件")
    args = ap.parse_args()
    sources = [(f"source_{index}", path) for index, path in enumerate(args.root, 1)]
    for value in args.source:
        source_id, separator, path = value.partition("=")
        if not separator or not source_id.strip() or not path.strip():
            ap.error("--source 须为 ID=PATH")
        sources.append((source_id, Path(path)))
    if not sources:
        ap.error("至少显式提供一个 --root 或 --source；不会自动扫描真实案件")
    if len({source_id for source_id, _ in sources}) != len(sources):
        ap.error("来源标识不可重复")
    if args.output.exists():
        ap.error("输出已存在，请使用新回执路径")
    report = inventory(sources)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        output.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"sources": report['sources'], "identical_file_groups": len(report["identical_file_groups"]),
                      "output": str(args.output)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
