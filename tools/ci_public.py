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
    install = (['cmd.exe', '/d', '/c', 'install.cmd']
               if os.name == 'nt' else ['sh', str(package / 'install.sh')])
    update = (['cmd.exe', '/d', '/c', 'update.cmd']
              if os.name == 'nt' else ['sh', str(package / 'update.sh')])
    deployment = reports / 'deployment.json'
    deployment.write_text(json.dumps({'schema': 'ustc-deployment-v1',
        'data_dir': str(package / '材料 with spaces'), 'profile_id': 'deployment_ci',
        'title': 'Portable acceptance', 'mode': 'student_activity', 'with_ocr': True}, ensure_ascii=False), encoding='utf-8')
    steps = []
    result = {'ok': False, 'steps': steps, 'browser_actions_executed': False,
              'archive_sha256': receipt['archive_sha256'], 'platform': platform.system(), 'machine': platform.machine()}
    try:
        commands = [
            ('deployment-plan', [sys.executable, '-B', '-X', 'utf8', '-I', 'tools/portable_install.py', '--config', str(deployment), '--plan']),
            ('install-ocr', install + ['--config', str(deployment)]),
            ('offline-check', install + ['--config', str(deployment), '--check']),
            ('update-launcher', update + ['--help']),
            ('regression', [str(python), '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py']),
            ('native-bridge-contract', ['node', 'tests/test_native_bridge.cjs']),
            ('native-exports-contract', ['node', 'tests/test_system_print.cjs']),
            ('applicant-agreement-contract', ['node', 'tests/test_applicant_submission.cjs']),
            ('stdio-material-ocr', [str(python), 'tools/distribution_smoke.py', '--mcp', '--materials', '--ocr', '--output', str(reports / 'smoke.json')]),
        ]
        for name, command in commands:
            print('Running ' + name, flush=True)
            with (reports / (name + '.log')).open('w', encoding='utf-8') as log:
                process = subprocess.run(command, cwd=package, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=900)
            steps.append({'name': name, 'returncode': process.returncode})
            if name == 'deployment-plan' and (package / '.local').exists():
                raise ValueError('只读部署计划创建了安装状态')
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
