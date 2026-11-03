"""构建与校验Windows离线运行依赖；只含固定运行时和公开依赖，不复制现有虚拟环境。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
import zipfile

from material_intake import reject_links
from material_review_model import atomic_json

ROOT = Path(__file__).resolve().parents[1]
LOCKS = (
    "requirements-mcp.lock.txt",
    "requirements-materials.lock.txt",
    "requirements-ocr.lock.txt",
)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def member(root, name):
    if (
        not isinstance(name, str)
        or not name
        or "\\" in name
        or ":" in name
        or name.startswith("/")
        or any(p in {"", ".", ".."} for p in name.split("/"))
    ):
        raise ValueError("离线包成员路径无效")
    path = root / name
    reject_links(path)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("离线包成员越界")
    return path


def verify(root, offline_dir=None):
    """校验整个离线包后才交给pip；失败不回退联网。"""
    root = Path(root).resolve()
    offline = Path(offline_dir) if offline_dir is not None else root / "offline"
    reject_links(offline)
    offline = offline.resolve()
    manifest = json.loads((offline / "manifest.json").read_text(encoding="utf-8"))
    if (
        manifest.get("schema") != "ustc-offline-runtime-v1"
        or manifest.get("platform") != "windows-x86_64"
    ):
        raise ValueError("离线运行包平台或格式不符")
    locks = manifest.get("requirements", {})
    if set(locks) != set(LOCKS) or any(
        sha(root / name) != digest for name, digest in locks.items()
    ):
        raise ValueError("离线依赖与此版本不匹配，请携带配套离线包；本次不联网替代")
    seen = set()
    for row in manifest.get("files", []):
        name = row["path"]
        if name.casefold() in seen:
            raise ValueError("离线包成员重复")
        seen.add(name.casefold())
        path = member(offline, name)
        if (
            not path.is_file()
            or path.stat().st_size != row["size"]
            or sha(path) != row["sha256"]
        ):
            raise ValueError("离线运行依赖缺失或已变化：" + name)
    if "python/python.exe" not in seen or not any(
        p.startswith("wheels/") and p.endswith(".whl") for p in seen
    ):
        raise ValueError("离线包缺少固定Python或依赖")
    # 未列入回执的wheel不能参与依赖解析。
    actual = {
        "wheels/" + p.name.casefold()
        for p in (offline / "wheels").iterdir()
        if p.is_file()
    }
    if actual != {name for name in seen if name.startswith("wheels/")}:
        raise ValueError("离线依赖目录出现未登记成员")
    return {
        "ok": True,
        "manifest_sha256": sha(offline / "manifest.json"),
        "python": str(offline / "python/python.exe"),
        "wheels": str(offline / "wheels"),
        "file_count": len(seen),
        "network_required": False,
    }


def build(output, uv_archive, root=ROOT):
    if os.name != "nt":
        raise ValueError("此构建器为Windows x64离线包")
    root, output, uv_archive = (
        Path(root).resolve(),
        Path(output).absolute(),
        Path(uv_archive).resolve(),
    )
    reject_links(output)
    if output.exists():
        raise ValueError("输出已存在，请保留原构建并使用新目录")
    rows = list(
        csv.DictReader(
            (root / "distribution/bootstrap.tsv").open(encoding="utf-8"), delimiter="\t"
        )
    )
    row = next(r for r in rows if r["platform"] == "windows-x86_64")
    if sha(uv_archive) != row["sha256"]:
        raise ValueError("安装工具与冻结来源SHA不同")
    stage = root / ".local/offline-build" / uuid.uuid4().hex
    stage.mkdir(parents=True)
    uv = stage / "uv.exe"
    with zipfile.ZipFile(uv_archive) as archive:
        uv.write_bytes(archive.read(row["member"]))
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.upper().startswith(("UV_", "PIP_", "PYTHON")) and k != "VIRTUAL_ENV"
    }
    env.update(
        UV_PYTHON_INSTALL_DIR=str(stage / "runtime"),
        UV_CACHE_DIR=str(root / ".local/cache/uv-offline-build"),
        UV_PYTHON_BIN_DIR=str(stage / "bin"),
        UV_NO_CONFIG="1",
        PYTHONUTF8="1",
    )
    print("准备固定Python运行时…", flush=True)
    listing = subprocess.run(
        [
            str(uv),
            "python",
            "list",
            row["python"],
            "--only-downloads",
            "--show-urls",
            "--output-format",
            "json",
            "--no-config",
        ],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    upstream = [
        r
        for r in json.loads(listing.stdout)
        if r["key"] == "cpython-" + row["python"] + "-windows-x86_64-none"
    ]
    if len(upstream) != 1:
        raise ValueError("固定Python的官方来源不唯一")
    subprocess.run(
        [
            str(uv),
            "python",
            "install",
            row["python"],
            "--no-bin",
            "--no-registry",
            "--no-config",
        ],
        env=env,
        check=True,
        timeout=300,
    )
    runtime = stage / "runtime" / upstream[0]["key"]
    reject_links(runtime)
    output.mkdir(parents=True)
    shutil.copytree(
        runtime,
        output / "python",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    wheels = output / "wheels"
    wheels.mkdir()
    print("下载固定材料处理、助手和OCR依赖…", flush=True)
    command = [
        str(output / "python/python.exe"),
        "-B",
        "-X",
        "utf8",
        "-I",
        "-m",
        "pip",
        "--isolated",
        "download",
        "--disable-pip-version-check",
        "--index-url",
        "https://pypi.org/simple",
        "--only-binary",
        ":all:",
        "--dest",
        str(wheels),
    ]
    for name in LOCKS:
        command += ["-r", str(root / name)]
    with (stage / "download.log").open("w", encoding="utf-8") as log:
        subprocess.run(
            command,
            env=env,
            check=True,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=900,
        )
    files = []
    for path in sorted(output.rglob("*")):
        reject_links(path)
        if path.is_file() and "__pycache__" not in path.parts:
            files.append(
                {
                    "path": path.relative_to(output).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": sha(path),
                }
            )
    manifest = {
        "schema": "ustc-offline-runtime-v1",
        "platform": row["platform"],
        "python_version": row["python"],
        "requirements": {name: sha(root / name) for name in LOCKS},
        "files": files,
        "upstream": {
            "bootstrap": row,
            "python": upstream[0],
            "packages": "https://pypi.org/simple",
        },
        "private_data_included": False,
        "virtual_environment_copied": False,
    }
    atomic_json(output / "manifest.json", manifest)
    result = {
        "ok": True,
        "offline_dir": str(output),
        "manifest_sha256": sha(output / "manifest.json"),
        "file_count": len(files),
        "bytes": sum(r["size"] for r in files),
        "build_log": str(stage / "download.log"),
    }
    atomic_json(stage / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["build", "verify"])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--uv-archive", type=Path)
    args = parser.parse_args()
    if args.action == "build" and (not args.output or not args.uv_archive):
        parser.error("构建需要--output与--uv-archive")
    result = (
        build(args.output, args.uv_archive, args.root)
        if args.action == "build"
        else verify(args.root)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
