#!/usr/bin/env python3
"""CI复用实际安装器验收发行ZIP，输入仅为合成资料。"""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

from build_distribution import build


def main():
    root = Path(__file__).resolve().parents[1]
    work = root / '.local/public-ci'
    work.mkdir(parents=True, exist_ok=False)
    reports = work / 'reports'
    reports.mkdir()
    receipt = build(work / 'release', execute=True, root=root)
    extracted = work / '新用户 with spaces'
    with zipfile.ZipFile(receipt['archive']) as archive:
        archive.extractall(extracted)
    package = extracted / 'ustc-reimbursement'
    env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
    python = package / '.local/mcp-venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    install = (['powershell.exe', '-NoProfile', '-File', str(package / 'tools/bootstrap_install.ps1')]
               if os.name == 'nt' else ['sh', str(package / 'install.sh')])
    steps = []
    result = {'ok': False, 'steps': steps, 'browser_actions_executed': False,
              'archive_sha256': receipt['archive_sha256'], 'platform': platform.system(), 'machine': platform.machine()}
    try:
        commands = [
            ('install-ocr', install + ['--with-ocr']),
            ('offline-check', install + ['--check']),
            ('regression', [str(python), '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py']),
            ('stdio-material-ocr', [str(python), 'tools/distribution_smoke.py', '--mcp', '--materials', '--ocr', '--output', str(reports / 'smoke.json')]),
        ]
        for name, command in commands:
            print('Running ' + name, flush=True)
            with (reports / (name + '.log')).open('w', encoding='utf-8') as log:
                process = subprocess.run(command, cwd=package, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=900)
            steps.append({'name': name, 'returncode': process.returncode})
            if process.returncode:
                print((reports / (name + '.log')).read_text(encoding='utf-8')[-16000:])
                return 1
        result['ok'] = True
        return 0
    finally:
        for path in (package / '.local/self-checks').glob('*/result.json'):
            shutil.copy2(path, reports / ('self-check-' + path.parent.name + '.json'))
        for path in (package / '.local/self-checks').glob('*/smoke.log'):
            shutil.copy2(path, reports / ('smoke-' + path.parent.name + '.log'))
        for path in (package / '.local/self-checks').glob('*/install.log'):
            shutil.copy2(path, reports / ('dependencies-' + path.parent.name + '.log'))
        (reports / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    raise SystemExit(main())
