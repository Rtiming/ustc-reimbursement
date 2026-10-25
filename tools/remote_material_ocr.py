#!/usr/bin/env python3
"""准备材料识别合同及执行纯OCR工作；传输、调度和回收由rtime compute负责。"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import sys
import time


SCHEMA = "reimbursement.remote-ocr.v1"
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z")
# stage先核验环境，再落源码；probe因此不能依赖源码快照占位符。
PROBE_CODE = """import importlib.metadata,importlib.util,json,platform,pathlib,hashlib
package=pathlib.Path(importlib.util.find_spec('rapidocr_onnxruntime').origin).parent
versions={name:importlib.metadata.version(name) for name in ('rapidocr_onnxruntime','PyMuPDF','Pillow','onnxruntime','opencv-python','numpy')}
models=[{'name':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted((package/'models').glob('*.onnx'))]
print(json.dumps({'python':platform.python_version(),'versions':versions,'models':models},ensure_ascii=False,sort_keys=True))
"""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_requests(raw: object) -> list[dict]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("请求须为非空数组")
    rows, seen = [], set()
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError("每项须有稳定id")
        if not ID_RE.fullmatch(item["id"]) or item["id"] in seen:
            raise ValueError("id须唯一，且只含ASCII字母、数字、点、下划线或连字符")
        seen.add(item["id"])
        path = Path(item.get("path", "")).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"}:
            raise ValueError(f"不支持或缺失的材料：{path}")
        pages = item.get("pages", [1])
        if not isinstance(pages, list) or not pages or any(type(p) is not int or p < 1 for p in pages):
            raise ValueError("pages须为从1开始的正整数数组")
        if len(set(pages)) != len(pages):
            raise ValueError("pages不可重复")
        if path.suffix.lower() != ".pdf" and pages != [1]:
            raise ValueError("普通图像只能选择page 1")
        rotations = item.get("rotate", [0])
        if not isinstance(rotations, list) or not rotations or any(type(r) is not int or r not in (0, 90, 180, 270) for r in rotations):
            raise ValueError("rotate须为0/90/180/270数组")
        if len(set(rotations)) != len(rotations):
            raise ValueError("rotate不可重复")
        digest = sha256(path)
        rows.append({"id": item["id"], "source_path": str(path), "sha256": digest,
                     "suffix": path.suffix.lower(), "pages": pages, "rotate": rotations,
                     "mount_path": f"materials/{digest}{path.suffix.lower()}"})
    return rows


def server_python_path(python: str) -> str:
    """远端解释器属于Linux节点，不能用调用方Windows路径规则判断。"""
    if (not isinstance(python, str) or not python or '\x00' in python or '\\' in python
            or python.startswith('//') or not PurePosixPath(python).is_absolute()
            or '..' in PurePosixPath(python).parts or not PurePosixPath(python).name):
        raise ValueError('服务器Python须为无上级跳转的POSIX绝对文件路径')
    return python


def prepare(request: Path, project: Path, python: str, target: str, dpi: int, concurrency: int, probe_sha256: str | None = None) -> dict:
    """只冻结识别参数与源码，不在本机读取/渲染PDF内容。"""
    if target != "master":
        raise ValueError("本入口当前只接已核验的master；新节点先验证环境及调度政策")
    if dpi < 72 or dpi > 400 or concurrency < 1 or concurrency > 8:
        raise ValueError("dpi须在72—400之间，并发须在1—8之间")
    python = server_python_path(python)
    if probe_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", probe_sha256):
        raise ValueError("probe SHA须为64位小写十六进制")
    rows = normalize_requests(json.loads(request.read_text(encoding="utf-8")))
    project = project.resolve()
    if project.exists() and any(project.iterdir()):
        raise ValueError("任务目录必须为空，防止覆盖旧识别证据")
    project.mkdir(parents=True, exist_ok=True)
    project.chmod(0o700)
    common_root = Path(os.path.commonpath([str(Path(row["source_path"]).parent) for row in rows]))
    manifest = {"schema": SCHEMA, "dpi": dpi, "request_sha256": sha256(request), "items": rows}
    write_json(project / "manifest.json", manifest)
    shutil.copyfile(Path(__file__).resolve(), project / "worker.py")
    distinct = {row["mount_path"]: row for row in rows}
    inputs = [{"id": "manifest", "root": "project", "path": "manifest.json", "mount_path": "manifest.json", "required": True}]
    for index, row in enumerate(distinct.values()):
        inputs.append({"id": f"material-{index:04d}", "root": "workspace",
                       "path": str(Path(row["source_path"]).relative_to(common_root)),
                       "mount_path": row["mount_path"], "required": True})
    contract = {
        "schema": "rtime.compute.project.v1", "project_id": "reimbursement-material-ocr",
        "workspace_root": os.path.relpath(common_root, project),
        "source": {"kind": "git", "exploratory_include": ["worker.py"]}, "inputs": inputs,
        "command": {"argv": [python, "{project_root}/worker.py", "work", "--manifest", "{input:manifest}",
                             "--input-root", "{input_root}", "--item", "{array_item}", "--output-dir", "{task_output_dir}"],
                    "working_directory": "."},
        "environment": {"kind": "system", "lock_files": []},
        "resources": {"cores": 2, "min_cores": 1, "memory_gb": 8, "walltime_minutes": 20, "gpus": 0,
                      "scratch": {"memory_gb": 1, "tmpdir": True},
                      "array": {"count": len(rows), "max_concurrency": min(concurrency, len(rows)), "items": [row["id"] for row in rows]}},
        "outputs": {"required_globs": ["result.json"], "collect_globs": ["*.json", "*.txt", "*.png"]},
        "allowed_targets": [target],
    }
    if probe_sha256 is not None:
        contract["environment"].update({"probe_argv": [python, "-c", PROBE_CODE],
                                        "expected_probe_sha256": probe_sha256})
    write_json(project / "rtime-compute.json", contract)
    return {"project_dir": str(project), "items": len(rows), "distinct_files": len(distinct),
            "worker_sha256": sha256(project / "worker.py"), "request_sha256": manifest["request_sha256"],
            "next_step": "初始化本任务源码快照Git后，通过rtime compute run PROJECT --target master --mode exploratory --apply执行"}


def environment_probe() -> dict:
    package = Path(importlib.util.find_spec('rapidocr_onnxruntime').origin).parent
    versions = {name: importlib.metadata.version(name) for name in ("rapidocr_onnxruntime", "PyMuPDF", "Pillow", "onnxruntime", "opencv-python", "numpy")}
    return {"python": platform.python_version(), "versions": versions,
            "models": [{"name": path.name, "sha256": sha256(path)} for path in sorted((package / "models").glob("*.onnx"))]}


def safe_material_path(root: Path, row: dict) -> Path:
    relative = PurePosixPath(row["mount_path"])
    if relative.is_absolute() or ".." in relative.parts or "\\" in row["mount_path"]:
        raise ValueError("材料不在声明的输入边界内")
    # 公共层mount tree用符号链接指向CAS；路径须安全，字节以清单SHA绑定。
    path = root.resolve() / relative
    if not path.is_file():
        raise ValueError("材料不在声明的输入边界内")
    if sha256(path) != row["sha256"]:
        raise ValueError("材料SHA与清单不一致")
    return path


def render_page(path: Path, page: int, dpi: int):
    from PIL import Image, ImageOps
    if path.suffix.lower() == ".pdf":
        import fitz
        with fitz.open(path) as document:
            if page > len(document):
                raise ValueError(f"页码{page}超出PDF共{len(document)}页")
            selected = document[page - 1]
            if math.ceil(selected.rect.width * dpi / 72) * math.ceil(selected.rect.height * dpi / 72) > 25000000:
                raise ValueError("页面超过2500万像素，请降低dpi")
            pix = selected.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
            if pix.width * pix.height > 25000000:
                raise ValueError("页面超过2500万像素，请降低dpi")
            return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    with Image.open(path) as image:
        image.seek(0)
        if image.width * image.height > 25000000:
            raise ValueError("图像超过2500万像素")
        return ImageOps.exif_transpose(image).convert("RGB")


def work(manifest_path: Path, root: Path, item_id: str, output: Path) -> dict:
    if not os.environ.get("RTIME_COMPUTE_RUN_ID"):
        raise ValueError("批量OCR必须由服务器rtime compute运行，不允许本机直接执行work")
    os.umask(0o077)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA:
        raise ValueError("识别清单schema不支持")
    row = next((item for item in manifest["items"] if item["id"] == item_id), None)
    if row is None:
        raise ValueError("清单没有本task id")
    path = safe_material_path(root, row)
    threads = int(os.environ.get("SLURM_CPUS_PER_TASK", os.environ.get("RTIME_COMPUTE_ALLOCATED_CORES", "1")))
    return recognize(manifest, row, path, output, os.environ["RTIME_COMPUTE_RUN_ID"], threads,
                     manifest_sha256=sha256(manifest_path), execution_site="remote")


def recognize(manifest, row, path, output, run_id, threads, *, manifest_sha256, execution_site):
    """两个显式执行适配器共用的识别内核；来源、调度和权限由调用方核验。"""
    if execution_site not in {"remote", "local"} or not 1 <= threads <= 8:
        raise ValueError("OCR执行位置或线程预算不符")
    if sha256(path) != row["sha256"]:
        raise ValueError("OCR原件SHA已变化")
    output.mkdir(parents=True, exist_ok=True)
    output.chmod(0o700)
    import cv2
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR
    cv2.setNumThreads(threads)
    engine = RapidOCR(intra_op_num_threads=threads, inter_op_num_threads=1, use_cuda=False,
                      text_score=0.3, det_limit_side_len=1600, det_limit_type="max")
    start = time.monotonic()
    pages = []
    for number in row["pages"]:
        original = render_page(path, number, manifest["dpi"])
        for angle in row["rotate"]:
            # 保存识别实际所见页面，避免旋转后框坐标指向原图。
            page = original.rotate(-angle, expand=True) if angle else original.copy()
            stem = f"page-{number:04d}-rot-{angle:03d}"
            page.save(output / f"{stem}.png")
            bgr = cv2.cvtColor(np.asarray(page), cv2.COLOR_RGB2BGR)
            recognized, elapsed = engine(bgr)
            lines = [{"box": np.asarray(line[0]).tolist(), "text": line[1], "confidence": float(line[2])} for line in (recognized or [])]
            text_value = "\n".join(line["text"] for line in lines)
            (output / f"{stem}.txt").write_text(text_value + "\n", encoding="utf-8")
            page_record = {"page": number, "rotation_clockwise": angle, "image": f"{stem}.png", "image_sha256": sha256(output / f"{stem}.png"),
                           "width": page.width, "height": page.height, "text": text_value, "lines": lines, "ocr_elapsed": elapsed}
            write_json(output / f"{stem}.json", page_record)
            pages.append(page_record)
            page.close()
        original.close()
    result = {"schema": SCHEMA, "status": "machine_read_only", "item": row, "pages": pages,
              "worker_sha256": sha256(Path(__file__).resolve()), "manifest_sha256": manifest_sha256,
              "environment": environment_probe(), "host": platform.node(), "threads": threads,
              "run_id": run_id, "execution_site": execution_site, "seconds": time.monotonic() - start,
              "verification": "OCR仅供检索与逐页人工核对，未裁定报销资格、金额和材料齐备性"}
    if sha256(path) != row["sha256"]:
        raise ValueError("OCR期间原件字节变化，保留诊断，不发布结果")
    write_json(output / "result.json", result)
    return {"item": row['id'], "pages": len(pages), "seconds": result["seconds"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare")
    p.add_argument("--request", type=Path, required=True)
    p.add_argument("--project-dir", type=Path, required=True)
    p.add_argument("--python", required=True, help="已验证的服务器Python绝对路径")
    p.add_argument("--target", default="master")
    p.add_argument("--dpi", type=int, default=250)
    p.add_argument("--concurrency", type=int, default=3)
    p.add_argument("--probe-sha256", help="含末尾换行的probe原始stdout SHA，提供后每次启动均核验")
    commands.add_parser("probe")
    p = commands.add_parser("work")
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--input-root", type=Path, required=True)
    p.add_argument("--item", required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.request, args.project_dir, args.python, args.target, args.dpi, args.concurrency, args.probe_sha256)
        elif args.command == "probe":
            result = environment_probe()
        else:
            result = work(args.manifest, args.input_root, args.item, args.output_dir)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
