#!/usr/bin/env python3
"""检查公开发行，核验后在新目录安装；原工具、案件和运行现场原地保留。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from assistant_api import atomic_json
from build_distribution import encoded, validate_member
from distribution_smoke import verify_members
from workspace_config import deployment_options, read_installation, distribution_version

REPOSITORY = 'Rtiming/ustc-reimbursement'
API = 'https://api.github.com/repos/' + REPOSITORY + '/releases'
DOWNLOAD = 'https://github.com/' + REPOSITORY + '/releases/download/'
VERSION = re.compile(r'v?(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta|rc)\.(\d+))?')
MAX_ZIP = 128 * 1024 * 1024


def version_key(value):
    match = VERSION.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise ValueError('不支持的发行版本格式')
    a, b, c, phase, number = match.groups()
    return (int(a), int(b), int(c), {'alpha': 0, 'beta': 1, 'rc': 2, None: 3}[phase], int(number or 0))


def fetch(url, limit):
    # 只有固定GitHub发布元数据/附件URL可到达这里，不消费任意镜像或认证环境。
    request = urllib.request.Request(url, headers={'User-Agent': 'ustc-reimbursement-updater', 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        final = urllib.parse.urlsplit(response.url)
        if final.scheme != 'https' or final.hostname not in {
            'api.github.com', 'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com', 'github-releases.githubusercontent.com'
        }:
            raise ValueError('发行下载跳转到未登记主机')
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('发行响应超过大小限制')
    return data


def select_release(current, channel='current', requested=None):
    version_key(current)
    preview = '-' in current if channel == 'current' else channel == 'preview'
    endpoint = API + ('/tags/' + requested if requested else '?per_page=100')
    if requested:
        version_key(requested)
    response = json.loads(fetch(endpoint, 2 * 1024 * 1024))
    rows = [response] if requested else response
    if not isinstance(rows, list) or len(rows) >= 100:
        raise ValueError('发行清单不完整；请使用明确的--version')
    candidates = []
    for row in rows:
        if not isinstance(row, dict) or row.get('draft') is not False:
            continue
        try:
            key = version_key(row.get('tag_name'))
        except ValueError:
            continue
        if type(row.get('prerelease')) is not bool or row['prerelease'] != ('-' in row['tag_name']):
            raise ValueError('发行渠道与版本不一致')
        if requested or preview or not row['prerelease']:
            candidates.append((key, row))
    if not candidates:
        return None
    row = max(candidates, key=lambda item: item[0])[1]
    tag = row['tag_name']; version = tag.removeprefix('v')
    names = ['ustc-reimbursement-' + version + '.zip', 'SHA256SUMS']
    assets = {}
    for name in names:
        matches = [asset for asset in row.get('assets', []) if asset.get('name') == name and asset.get('state') == 'uploaded']
        if len(matches) != 1 or matches[0].get('browser_download_url') != DOWNLOAD + tag + '/' + name:
            raise ValueError('发行附件缺失、重复或地址不属于本仓库')
        assets[name] = matches[0]
    return {'tag': tag, 'version': version, 'page': 'https://github.com/' + REPOSITORY + '/releases/tag/' + tag, 'assets': assets}


def archive_hash(release):
    text = fetch(release['assets']['SHA256SUMS']['browser_download_url'], 65536).decode('utf-8-sig')
    name = 'ustc-reimbursement-' + release['version'] + '.zip'
    values = []
    for line in text.splitlines():
        match = re.fullmatch(r'([a-fA-F0-9]{64})\s+\*?(.+)', line)
        if match and match[2] == name:
            values.append(match[1].lower())
    if len(values) != 1:
        raise ValueError('SHA256SUMS没有唯一的目标附件校验值')
    digest = release['assets'][name].get('digest')
    if digest is not None and digest != 'sha256:' + values[0]:
        raise ValueError('发行附件元数据与SHA256SUMS不一致')
    return values[0]


def extract_verified(archive, directory, version):
    with zipfile.ZipFile(archive) as package:
        infos = package.infolist()
        if len(infos) > 3000 or sum(item.file_size for item in infos) > 256 * 1024 * 1024:
            raise ValueError('发行包解压规模超过限制')
        names = [item.filename for item in infos]
        if len(names) != len(set(names)):
            raise ValueError('发行包包含重复成员')
        content = {}
        for item in infos:
            if not item.filename.startswith('ustc-reimbursement/') or item.is_dir() or (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('发行包包含不允许的路径或链接')
            relative = item.filename.removeprefix('ustc-reimbursement/')
            raw = package.read(item)
            validate_member(relative, raw)
            content[relative] = raw
        manifest = json.loads(content['MANIFEST.json'])
        if manifest.get('version') != version or manifest.get('private_data_included') is not False:
            raise ValueError('发行清单版本或私有资料边界不符')
        listed = manifest['files']
        if len({row['path'] for row in listed}) != len(listed) or set(content) != {'MANIFEST.json', *(row['path'] for row in listed)}:
            raise ValueError('发行清单与压缩包成员不完全相等')
        if hashlib.sha256(encoded(listed)).hexdigest() != manifest['source_tree_sha256']:
            raise ValueError('发行清单源码指纹不符')
        for row in listed:
            raw = content[row['path']]
            if len(raw) != row['size'] or hashlib.sha256(raw).hexdigest() != row['sha256']:
                raise ValueError('发行成员SHA不符')
        if json.loads(content['data/distribution.json'])['version'] != version:
            raise ValueError('安装入口与发行清单版本不一致')
        # 完整校验之后才写文件；权限仅沿已验清单的普通成员保留。
        for item in infos:
            path = directory / item.filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content[item.filename.removeprefix('ustc-reimbursement/')])
            if os.name != 'nt' and item.external_attr >> 16 & 0o111:
                path.chmod(0o755)
    target = directory / 'ustc-reimbursement'
    verify_members(target)
    return target


def run(root=ROOT, *, channel='current', version=None, download=False, install=False, destination=None, config_file=None):
    root = Path(root).resolve()
    current = distribution_version(root)
    release = select_release(current, channel, version)
    result = {'ok': True, 'current_version': current, 'channel': channel, 'update_available': bool(release and version_key(release['version']) > version_key(current)),
              'financial_write_executed': False, 'browser_actions_executed': False, 'state_migrated': False}
    if release:
        result.update(version=release['version'], release_page=release['page'])
    if not (download or install) or not release or not result['update_available'] and not version:
        return result
    target = Path(destination).expanduser().resolve() if destination else root.parent / ('ustc-reimbursement-' + release['version'])
    if target.is_relative_to(root) or root.is_relative_to(target):
        raise ValueError('更新目标须为原工具之外的新目录')
    checksum = archive_hash(release)
    if target.exists():
        proof = target / '.local/update-source.json'
        if not proof.is_file() or json.loads(proof.read_text()).get('archive_sha256') != checksum:
            raise ValueError('目标目录已存在且不是本次已验证发行，不覆盖')
        existing = verify_members(target)
        if existing['version'] != release['version'] or existing['source_tree_sha256'] != json.loads(proof.read_text()).get('source_tree_sha256'):
            raise ValueError('既有更新目录版本不符')
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix='.ustc-update-', dir=target.parent))
        archive = stage / 'release.zip'
        name = 'ustc-reimbursement-' + release['version'] + '.zip'
        raw = fetch(release['assets'][name]['browser_download_url'], MAX_ZIP)
        if hashlib.sha256(raw).hexdigest() != checksum:
            raise ValueError('发行ZIP的SHA256校验失败')
        archive.write_bytes(raw)
        extracted = extract_verified(archive, stage, release['version'])
        target.mkdir(mode=0o700, exist_ok=False)
        for child in extracted.iterdir():
            child.rename(target / child.name)
        atomic_json(target / '.local/update-source.json', {'repository': REPOSITORY, 'version': release['version'], 'archive_sha256': checksum, 'source_tree_sha256': verify_members(target)['source_tree_sha256']})
    result.update(prepared_path=str(target), archive_sha256=checksum, old_tool_preserved=str(root), existing_cases_kept_at=str(root / 'cases'))
    if install:
        old = read_installation(root)
        options = deployment_options(config_file) if config_file else (
            {'data_dir': old['material_root'], 'profile_id': old['profile_id'], 'title': old['title'], 'mode': old['mode']} if old else {})
        if not config_file:
            from ocr_runtime import read_profile
            ocr = read_profile(root)
            if ocr and ocr['profile']['backend'] == 'local':
                options['with_ocr'] = True
        if old and not Path(old['library_root']).is_dir():
            raise ValueError('原规范库丢失，先恢复原资料；更新不创建空白替代库')
        config = target / '.local/update-deployment.json'
        atomic_json(config, {'schema': 'ustc-deployment-v1', **options})
        command = ['cmd.exe', '/d', '/c', str(target / 'install.cmd')] if os.name == 'nt' else ['sh', str(target / 'install.sh')]
        log = target / '.local/update-install.log'
        result.update(ok=False, phase='installing', install_log=str(log))
        receipt = target / '.local/update-result.json'
        atomic_json(receipt, result)
        with log.open('w', encoding='utf-8') as output:
            process = subprocess.run([*command, '--config', str(config)], cwd=target, stdout=output, stderr=subprocess.STDOUT, timeout=900)
        status = target / '.local/self-check-current.json'
        if process.returncode or not status.is_file() or json.loads(status.read_text()).get('ok') is not True:
            result.update(phase='installation_incomplete', error='新目录安装未完成，原工具保留；查看install_log后接续同一更新目录')
            atomic_json(receipt, result)
            return result
        result.update(ok=True, phase='installed', client_config=str(target / '.local/client-configs/mcp.json'), next='在新目录使用CLI或合并新的MCP片段并重启该服务；原案和未知任务继续从旧目录接续')
        atomic_json(receipt, result)
    else:
        result['phase'] = 'downloaded_and_verified'
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--channel', choices=['current', 'stable', 'preview'], default='current')
    parser.add_argument('--version', help='明确发行标签；也可准备旧版作为回退')
    parser.add_argument('--download', action='store_true', help='仅下载并完整核验')
    parser.add_argument('--install', action='store_true', help='在新目录安装并自检，保留原目录')
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--config', type=Path)
    args = parser.parse_args(argv)
    try:
        result = run(channel=args.channel, version=args.version, download=args.download, install=args.install, destination=args.destination, config_file=args.config)
    except (ValueError, OSError, KeyError, TypeError, RuntimeError, subprocess.TimeoutExpired, zipfile.BadZipFile) as error:
        result = {'ok': False, 'error': str(error), 'old_tool_preserved': True, 'financial_write_executed': False}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
