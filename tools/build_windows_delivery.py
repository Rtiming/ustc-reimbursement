"""组合已冻结源码与Windows离线依赖；不读取开发者案件、凭据或虚拟环境。"""

from __future__ import annotations

import argparse
import email
import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
import zipfile

from build_distribution import encoded
from material_intake import reject_links
from offline_runtime import member, sha, verify
from release_update import extract_verified

ROOT = Path(__file__).resolve().parents[1]


def notices(offline, manifest):
    wheels = []
    for row in manifest["files"]:
        if not row["path"].startswith("wheels/"):
            continue
        with zipfile.ZipFile(member(offline, row["path"])) as archive:
            metadata = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
            if len(metadata) != 1:
                raise ValueError("wheel缺少唯一许可证元数据：" + row["path"])
            info = email.message_from_bytes(archive.read(metadata[0]))
            licenses = [n for n in archive.namelist() if not n.endswith("/") and
                        re.search(r"(?i)(^|/)(licen[sc]e[^/]*|copying[^/]*|notice[^/]*)$", n)]
            wheels.append({"file": row["path"], "sha256": row["sha256"],
                           "name": info.get("Name"), "version": info.get("Version"),
                           "license_expression": info.get("License-Expression"),
                           "license": info.get("License"), "home_page": info.get("Home-page"),
                           "project_urls": info.get_all("Project-URL", []),
                           "license_members": licenses})
    runtime_licenses = [r["path"] for r in manifest["files"] if r["path"].startswith("python/") and
                        re.search(r"(?i)(^|/)(licen[sc]e[^/]*|copying[^/]*|notice[^/]*)$", r["path"])]
    return {"schema": "ustc-windows-third-party-v1", "upstream": manifest["upstream"],
            "python_version": manifest["python_version"], "runtime_license_files": runtime_licenses,
            "wheels": wheels, "note": "原始许可证随Python文件和wheel原包保留；wheel在离线安装时解包到本机环境。"}


def build(source_archive, source_sha256, offline, output_dir):
    source_archive, offline = Path(source_archive), Path(offline)
    output_dir = Path(output_dir)
    for path in (source_archive, offline, output_dir):
        reject_links(path)
    if not output_dir.is_absolute() or output_dir.exists():
        raise ValueError("交付目录须为尚不存在的绝对目录，保留旧版本")
    if source_archive.stat().st_size > 128 * 1024 * 1024:
        raise ValueError("源码ZIP超过固定规模")
    source_bytes = source_archive.read_bytes()
    if not re.fullmatch(r"[a-f0-9]{64}", str(source_sha256)) or hashlib.sha256(source_bytes).hexdigest() != source_sha256:
        raise ValueError("源码ZIP与确认的SHA不同")
    with zipfile.ZipFile(io.BytesIO(source_bytes)) as archive:
        source_manifest = json.loads(archive.read("ustc-reimbursement/MANIFEST.json"))
    stage_parent = ROOT / ".local/windows-delivery-build"
    stage_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=stage_parent) as temporary:
        frozen_archive = Path(temporary) / "source.zip"
        frozen_archive.write_bytes(source_bytes)
        source = extract_verified(frozen_archive, Path(temporary), source_manifest["version"])
        validation = verify(source, offline_dir=offline)
        manifest_path = offline / "manifest.json"
        manifest_bytes = manifest_path.read_bytes()
        if hashlib.sha256(manifest_bytes).hexdigest() != validation["manifest_sha256"]:
            raise ValueError("离线清单在校验后发生变化")
        manifest = json.loads(manifest_bytes)
        third_party = notices(offline, manifest)
        receipt = {"schema": "ustc-windows-delivery-v1", "version": source_manifest["version"],
                   "platform": manifest["platform"], "source_archive_sha256": source_sha256,
                   "source_tree_sha256": source_manifest["source_tree_sha256"],
                   "offline_manifest_sha256": validation["manifest_sha256"],
                   "offline_file_count": validation["file_count"],
                   "offline_bytes": sum(r["size"] for r in manifest["files"]),
                   "private_data_included": False, "virtual_environment_copied": False,
                   "network_required_for_install": False,
                   "source_upgrade_archive": "升级用源码包.zip"}
        readme = f"""# Windows完整离线安装包\n\n版本：{receipt['version']}，适用Windows x64。\n\n1. 将整个ZIP解压到可长期保留、当前用户可写的目录（建议D盘）。不要直接在压缩包内运行。\n2. 双击“安装报销工作台.cmd”。无需另装Python；默认安装材料处理和agent环境。要在本机识别扫描件，双击“安装并启用本机OCR.cmd”。\n3. 看到安装自检通过后，使用桌面“报销工作台”或“打开报销工作台.cmd”。保留程序目录及其中的资料和.local目录。\n4. 在“规范与专项”导入另附的私人规范包；新材料、已核金额、经费和本人账号仍逐案确认。\n5. 学校办理需要网络、Edge接线及本人登录；验证码由本人在学校页面完成。\n\nagent配置由安装器生成在.local/client-configs：mcp-compact.json供简明工具入口，mcp.json供全部功能；按docs/用户指南.md接入自己的客户端和模型。\n\n离线重装：再次双击原安装入口，保留资料、私人规则和任务。程序更新：使用工作台维护入口选择“升级用源码包.zip”；回退使用本机升级回执，不用新请求重做未知财务操作。不同版本依赖锁变化时，应同时携带配套offline目录。\n\n安装自检是本机工具与合成资料验收。老师电脑的账号、浏览器及真实单据办理由现场验收确认。\n\n来源和许可证见THIRD_PARTY_NOTICES.md、“离线依赖来源及许可.json”及offline/manifest.json。源码与运行依赖身份见“完整包内容回执.json”；外层ZIP校验见旁边的SHA256SUMS。\n"""
        entries = {
            "安装报销工作台.cmd": b'@echo off\r\nsetlocal\r\npowershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\\bootstrap_install.ps1" %*\r\nset "installResult=%errorlevel%"\r\npause\r\nexit /b %installResult%\r\n',
            "安装并启用本机OCR.cmd": b'@echo off\r\nsetlocal\r\npowershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\\bootstrap_install.ps1" --with-ocr %*\r\nset "installResult=%errorlevel%"\r\npause\r\nexit /b %installResult%\r\n',
            "打开报销工作台.cmd": b'@echo off\r\nsetlocal\r\nstart "" "%~dp0.local\\mcp-venv\\Scripts\\pythonw.exe" "%~dp0tools\\operator_workbench.py" --open\r\n',
            "先读安装说明.md": readme.encode("utf-8"),
            "完整包内容回执.json": encoded(receipt),
            "离线依赖来源及许可.json": encoded(third_party),
        }
        if set(entries).intersection(r["path"] for r in source_manifest["files"]):
            raise ValueError("完整包固定入口与源码成员重名")
        output_dir.mkdir(parents=True)
        archive_path = output_dir / ("ustc-reimbursement-windows-x64-full-" + receipt["version"] + ".zip")

        def write(archive, name, raw):
            info = zipfile.ZipInfo("ustc-reimbursement/" + name, date_time=(2026, 11, 3, 0, 0, 0))
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, raw)

        with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for row in source_manifest["files"]:
                write(archive, row["path"], (source / row["path"]).read_bytes())
            write(archive, "MANIFEST.json", (source / "MANIFEST.json").read_bytes())
            write(archive, "升级用源码包.zip", source_bytes)
            write(archive, "offline/manifest.json", manifest_bytes)
            for row in manifest["files"]:
                raw = member(offline, row["path"]).read_bytes()
                if len(raw) != row["size"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
                    raise ValueError("构建期间离线成员发生变化：" + row["path"])
                write(archive, "offline/" + row["path"], raw)
            for name, raw in entries.items():
                write(archive, name, raw)
        with zipfile.ZipFile(archive_path) as archive:
            if archive.testzip() is not None:
                raise ValueError("完整包CRC校验失败")
        result = {"ok": True, **receipt, "archive": str(archive_path),
                  "archive_sha256": sha(archive_path), "archive_bytes": archive_path.stat().st_size}
        (output_dir / "build-receipt.json").write_bytes(encoded(result))
        (output_dir / "SHA256SUMS").write_text(result["archive_sha256"] + "  " + archive_path.name + "\n", encoding="utf-8")
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-archive", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--offline", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.source_archive, args.source_sha256, args.offline, args.output_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
