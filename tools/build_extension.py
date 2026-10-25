#!/usr/bin/env python3
"""把已验证的原生适配器及探针加入跨平台扩展；生成可分发ZIP，不含会话数据。"""
import json
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "extension"
SCRIPTS = ["finance_read_probe.js", "finance_definition_probe.js", "browser_automation.js"]
RUNTIME_FILES = ["manifest.json", "popup.html", "popup.css", "popup.js", "native_bridge.js", *SCRIPTS]


def main():
    for name in SCRIPTS:
        shutil.copyfile(ROOT / "tools" / name, DEST / name)
    manifest = json.loads((DEST / "manifest.json").read_text())
    output = ROOT / "generated/reimbursement-automation-extension.zip"
    output.parent.mkdir(exist_ok=True)
    names = RUNTIME_FILES
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        for name in names:
            z.write(DEST / name, name)
    print(json.dumps({"version": manifest["version"], "files": names, "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
