#!/usr/bin/env python3
"""按固定源码允许清单构建公开发行包；不复制案件、凭据、规范正本或运行环境。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SUFFIXES = {'.py', '.js', '.mjs', '.sh', '.cmd', '.ps1'}
FORBIDDEN_PARTS = {
    '.git',
    '.local',
    'cases',
    'private',
    'evidence',
    'generated',
    '规范库',
    '__pycache__',
}
ALIASES = {
    'LICENSE': 'distribution/LICENSE',
    'NOTICE': 'distribution/NOTICE',
    'THIRD_PARTY_NOTICES.md': 'distribution/THIRD_PARTY_NOTICES.md',
    'THIRD_PARTY_NOTICES.en.md': 'distribution/THIRD_PARTY_NOTICES.en.md',
    'CONTRIBUTING.md': 'distribution/CONTRIBUTING.md',
    'SECURITY.md': 'distribution/SECURITY.md',
    '.gitattributes': 'distribution/gitattributes',
    '.gitleaks.toml': 'distribution/gitleaks.toml',
    '.github/workflows/ci.yml': 'distribution/ci.yml',
    'README.md': 'distribution/README.md',
    'README.en.md': 'distribution/README.en.md',
    'AGENTS.en.md': 'distribution/AGENTS.en.md',
    'CONTRIBUTING.en.md': 'distribution/CONTRIBUTING.en.md',
    'SECURITY.en.md': 'distribution/SECURITY.en.md',
    'AGENTS.md': 'distribution/AGENTS.md',
    '.gitignore': 'distribution/gitignore',
    'install.sh': 'distribution/install.sh',
    'install.cmd': 'distribution/install.cmd',
    '安装与自检.command': 'distribution/安装与自检.command',
    'docs/用户指南.md': 'distribution/用户指南.md',
    'docs/助手快速开始.md': 'distribution/用户指南.md',
    'docs/助手接口使用.md': 'distribution/用户指南.md',
    'docs/助手操作规程.md': 'distribution/用户指南.md',
    'docs/模式与场景.md': 'distribution/模式与场景.md',
    'docs/报销规范与材料规则.md': 'distribution/材料与可选能力.md',
    'docs/材料整理与单面打印.md': 'distribution/材料与可选能力.md',
    'docs/报销总账与数据格式.md': 'distribution/材料与可选能力.md',
    'templates/activity_approval/README.md': 'distribution/activity-template.md',
    'docs/换设备与首次运行.md': 'docs/部署指南.md',
    'docs/财务PDF导出.md': 'distribution/财务操作与导出.md',
    'docs/Windows静默财务桥.md': 'distribution/财务操作与导出.md',
    'skills/ustc-reimbursement/references/standards-system.md': 'distribution/模式与场景.md',
    'skills/ustc-reimbursement/SKILL.md': 'distribution/skills/ustc-reimbursement/SKILL.md',
    'data/workflow_defaults.json': 'distribution/data/workflow_defaults.json',
    'data/material_rules.json': 'distribution/data/material_rules.json',
    'data/distribution.json': 'distribution/data/distribution.json',
}


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def validate_member(name, raw):
    path = Path(name)
    if (
        path.is_absolute()
        or PureWindowsPath(name).drive
        or '\\' in name
        or any(p in {'', '.', '..'} or p.rstrip('. ') != p for p in name.split('/'))
        or set(path.parts) & FORBIDDEN_PARTS
        or any(
            re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', p) for p in path.parts
        )
    ):
        raise ValueError('禁止进入试用包的路径：' + name)
    if path.suffix in SOURCE_SUFFIXES | {'.json', '.md', '.txt', '.html', '.css'}:
        text = raw.decode('utf-8')
        patterns = (
            r'/' + r'Users/(?!example(?:/|\b))[^\s/]+/',
            r'/' + r'Volumes/[^\s/]+/',
            r'\bPB\d{8,}\b',
            r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
            r'\bsk-[A-Za-z0-9_-]{24,}\b',
        )
        if any(re.search(pattern, text) for pattern in patterns):
            raise ValueError('成员含个人路径、身份或疑似凭据，须回原源码处理：' + name)


def collect_sources(root=ROOT):
    root = Path(root).resolve()
    recipe = json.loads((root / 'distribution/source-files.json').read_text(encoding='utf-8'))
    names = recipe['source_files']
    if recipe.get('schema') != 'ustc-distribution-sources-v1' or len(set(names)) != len(names):
        raise ValueError('分发源码清单格式不符或重复')
    for name in names:
        validate_member(name, b'')
    paths = [root / name for name in names]
    members = {}
    for path in sorted(set(paths)):
        if path.is_symlink() or any(
            parent.is_symlink()
            for parent in path.parents
            if parent != root and parent.is_relative_to(root)
        ):
            raise ValueError('试用包不跟随源码链接：' + str(path.relative_to(root)))
        if not path.is_file():
            raise ValueError('缺少必需源码：' + str(path.relative_to(root)))
        name = path.relative_to(root).as_posix()
        raw = path.read_bytes()
        validate_member(name, raw)
        members[name] = raw
    for target, source in ALIASES.items():
        if source not in members:
            raise ValueError('缺少分发版入口：' + source)
        members[target] = members[source]
    dependencies = json.loads(members['distribution/dependencies.json'])
    for dependency in (dependencies['archive_helper'], dependencies.get('vendor_snapshot')):
        if dependency and digest(members[dependency['path']]) != dependency['sha256']:
            raise ValueError('分发归档依赖与冻结SHA不同，先核对共享owner')
    return members


def build(output_dir, *, execute=False, root=ROOT):
    root = Path(root).resolve()
    target = Path(output_dir)
    if not target.is_absolute() or target.exists():
        raise ValueError('output-dir须为尚不存在的绝对目录；不覆盖旧交付')
    members = collect_sources(root)
    version = json.loads(members['data/distribution.json'])['version']
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?', version):
        raise ValueError('分发版本须为明确版本号，不接受路径或未知标签')
    files = [
        {'path': name, 'size': len(raw), 'sha256': digest(raw)}
        for name, raw in sorted(members.items())
    ]
    tree_sha = digest(encoded(files))
    try:
        commit = subprocess.check_output(
            ['git', '-C', str(root), 'rev-parse', 'HEAD'], stderr=subprocess.DEVNULL, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    manifest = {
        'schema': 'ustc-distribution-v1',
        'version': version,
        'source_base_commit': commit,
        'source_tree_sha256': tree_sha,
        'source_commit_is_not_complete_worktree_identity': True,
        'files': files,
        'private_data_included': False,
        'components': {
            'extension': json.loads(members['extension/manifest.json'])['version'],
            'mcp_dependencies': members['requirements-mcp.lock.txt'].decode().splitlines(),
            'material_dependencies': members['requirements-materials.lock.txt']
            .decode()
            .splitlines(),
        },
        'validation': '解压后运行tools/distribution_smoke.py；环境和财务实机分别验收',
    }
    result = {
        'ok': True,
        'execute': execute,
        'version': version,
        'file_count': len(files),
        'source_tree_sha256': tree_sha,
        'output_dir': str(target),
        'system_write': False,
    }
    if not execute:
        return result
    target.mkdir(parents=True, exist_ok=False)
    archive = target / ('ustc-reimbursement-' + version + '.zip')
    members['MANIFEST.json'] = encoded(manifest)
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for name, raw in sorted(members.items()):
            info = zipfile.ZipInfo('ustc-reimbursement/' + name, date_time=(2026, 10, 24, 0, 0, 0))
            info.create_system = 3  # 固定ZIP属性来源，避免同一源码随构建系统改变字节。
            info.compress_type = zipfile.ZIP_DEFLATED
            mode = 0o100755 if Path(name).suffix in {'.sh', '.command'} else 0o100644
            info.external_attr = mode << 16
            output.writestr(info, raw)
    with zipfile.ZipFile(archive) as built:
        if built.testzip() is not None:
            raise ValueError('试用包CRC检查失败')
        for row in files:
            if digest(built.read('ustc-reimbursement/' + row['path'])) != row['sha256']:
                raise ValueError('打包后成员SHA不符')
    result.update(
        archive=str(archive),
        archive_sha256=digest(archive.read_bytes()),
        archive_size=archive.stat().st_size,
    )
    (target / 'build-receipt.json').write_bytes(encoded(result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    print(json.dumps(build(args.output_dir, execute=args.execute), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
