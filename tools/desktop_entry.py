"""当前用户的固定桌面入口；保留其他快捷方式，独立读取.lnk核对实际目标。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid

from assistant_api import atomic_json, read_json

DESCRIPTION = 'USTC reimbursement local workbench'


def shell(root, spec):
    path = root / '.local/client-configs' / ('shortcut-' + uuid.uuid4().hex + '.json')
    atomic_json(path, spec)
    try:
        command = [
            'powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
            '-ExecutionPolicy', 'Bypass', '-File',
            str(Path(__file__).with_name('windows_shortcut.ps1')), '-SpecPath', str(path),
        ]
        result = subprocess.run(command, capture_output=True, encoding='utf-8', timeout=20,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise ValueError('桌面入口未建立：' + result.stderr.strip())
        return json.loads(result.stdout.lstrip('\ufeff'))
    finally:
        path.unlink(missing_ok=True)


def expected(root):
    target = root / '.local/mcp-venv/Scripts/pythonw.exe'
    script = root / 'tools/operator_workbench.py'
    if not target.is_file() or not script.is_file():
        raise ValueError('先完成工作台运行环境安装，再建立桌面入口')
    return {'target': str(target), 'arguments': subprocess.list2cmdline([str(script), '--open']),
            'working_directory': str(root), 'description': DESCRIPTION}


def same(value, desired):
    return (all(os.path.normcase(value.get(key, '')) == os.path.normcase(desired[key])
                for key in ['target', 'working_directory'])
            and all(value.get(key) == desired[key] for key in ['arguments', 'description']))


def install(root, *, check_only=False, desktop_dir=None):
    if os.name != 'nt':
        return {'ok': True, 'state': 'not_windows', 'note': '本平台使用已生成的工作台启动入口'}
    root = Path(root).resolve()
    desired = expected(root)
    if os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('CI') == 'true' and desktop_dir is None:
        launcher = root / '.local/client-configs/打开报销工作台.cmd'
        if not launcher.is_file():
            raise ValueError('无交互桌面的验收环境缺少本地启动入口')
        return {'ok': True, 'state': 'headless_ci_launcher_verified',
                'launcher': str(launcher), 'desktop_shortcut_tested': False,
                'browser_started': False}
    receipt = root / '.local/client-configs/desktop-shortcut.json'
    if receipt.is_file():
        path = Path(read_json(receipt)['path'])
    else:
        desktop = Path(desktop_dir) if desktop_dir else Path(shell(root, {'action': 'desktop'})['path'])
        if not desktop.is_absolute() or not desktop.is_dir() or desktop.is_symlink():
            raise ValueError('当前用户桌面目录不可用，请先核对系统桌面位置')
        path = desktop / '报销工作台.lnk'
        if path.exists() and not same(shell(root, {'action': 'inspect', 'path': str(path)}), desired):
            suffix = hashlib.sha256(str(root).encode()).hexdigest()[:8]
            path = desktop / ('报销工作台-' + suffix + '.lnk')
    if path.is_symlink() or not path.is_absolute() or path.suffix.lower() != '.lnk':
        raise ValueError('原桌面入口路径不完整，未覆盖其他文件')
    if not path.exists():
        if check_only:
            raise ValueError('桌面入口缺失，请重跑安装以修复')
        observed = shell(root, {'action': 'create', 'path': str(path), **desired})
    else:
        observed = shell(root, {'action': 'inspect', 'path': str(path)})
    if not same(observed, desired):
        raise ValueError('已有桌面入口指向其他内容，已保留，请检查原入口')
    result = {'ok': True, 'state': 'verified', **observed,
              'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'browser_started': False}
    if not check_only:
        atomic_json(receipt, result)
    return result
