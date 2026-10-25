#!/usr/bin/env python3
"""分发包安装、自检和专项初始化；不依赖作者的技能目录或全局AI配置。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from assistant_api import AssistantService, atomic_json, read_json
from distribution_smoke import verify_members
from platform_runtime import file_lock
from setup_reimbursement_mcp import setup

# 保留旧导入位置，使用统一工作区绑定与隔离环境。
from workspace_config import installation_config, private_environment, runtime_python
from assistant_contract import CASE_PROFILES


def ensure_profile(service, config):
    library, pid = config['library_root'], config['profile_id']
    if not Path(library).exists():
        return service.standards_init(library, pid, config['title'], config['mode'], execute=True)
    rows = service.standards_read(library)['profiles']
    if any(row['profile_id'] == pid for row in rows):
        # 安装重跑不修改用户已经写入的任何规则、标题或确认。
        return {
            'ok': True,
            'reused': True,
            'profile': service.standards_read(library, pid)['profile'],
        }
    parents = ['common'] if any(row['profile_id'] == 'common' for row in rows) else []
    preview = service.standards_add_profile(library, pid, config['title'], parents)
    return service.standards_add_profile(
        library,
        pid,
        config['title'],
        parents,
        execute=True,
        expected_registry_sha256=preview['before_registry_sha256'],
    )


def checks(root, config, folder, *, ocr_smoke=False):
    result = {
        'ok': False,
        'required': [],
        'optional': [
            {
                'name': '科大网页办理',
                'state': 'needs_user_setup',
                'detail': '本人登录、Edge接线及实机动作另行验收',
            },
            {
                'name': 'Excel成品创作',
                'state': 'not_bundled',
                'detail': '另配表格创作运行时；基础收件和规范不依赖它',
            },
            {
                'name': '远程总账与手机采集',
                'state': 'not_bundled',
                'detail': '按需接入服务，本次不安装私人基础设施',
            },
        ],
        'browser_actions_executed': False,
        'financial_write_executed': False,
    }

    def check(name, operation):
        try:
            value = operation()
            passed = value.get('ok', value.get('ready', True)) is True
            result['required'].append({'name': name, 'ok': passed, 'details': value})
            return passed
        except (ValueError, OSError, KeyError, RuntimeError) as error:
            result['required'].append({'name': name, 'ok': False, 'error': str(error)})
            return False

    def source_integrity():
        source = verify_members(root)
        return {'ok': True, 'version': source['version'], 'verification_mode': source['verification_mode']}

    check('源码与发行文件完整性', source_integrity)
    ready = check(
        '本机运行时与代理配置',
        lambda: setup(
            check_only=True, with_materials=True, material_roots=[config['material_root']]
        ),
    )
    service = AssistantService(root, [config['material_root']])
    ocr = service.ocr_status()
    result['optional'].append(
        {
            'name': 'OCR提供方',
            'state': 'configured' if ocr['configured'] else 'not_configured',
            'detail': '已配置' + str(ocr['backend']) + '；实际识别另有回执'
            if ocr['configured']
            else '可配置远程提供方，或显式--with-ocr启用本机模型',
        }
    )
    check(
        '私人规范库与当前专项',
        lambda: service.standards_read(config['library_root'], config['profile_id']),
    )
    if ready:

        def exercise():
            python = runtime_python(root)
            output = folder / 'smoke.json'
            command = [
                str(python),
                '-X', 'utf8', '-I',
                str(root / 'tools/distribution_smoke.py'),
                '--mcp',
                '--materials',
                '--output',
                str(output),
            ]
            if ocr_smoke:
                command.append('--ocr')
            with (folder / 'smoke.log').open('w', encoding='utf-8') as log:
                process = subprocess.run(
                    command,
                    cwd=root,
                    env=private_environment(root),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=120,
                )
            if process.returncode or not output.exists():
                raise ValueError('正式接口演练失败，请查看同目录smoke.log')
            return read_json(output)

        check(
            'CLI、MCP、归档与OCR实际演练' if ocr_smoke else 'CLI、MCP与原件归档实际演练', exercise
        )
    else:
        result['required'].append(
            {
                'name': 'CLI、MCP与原件归档实际演练',
                'ok': False,
                'state': 'not_run_missing_dependency',
            }
        )
    result['ok'] = all(item['ok'] for item in result['required'])
    return result


def write_report(folder, result):
    atomic_json(folder / 'result.json', result)
    rows = ''.join(
        '<tr><td>'
        + html.escape(row['name'])
        + '</td><td>'
        + ('通过' if row['ok'] else '需处理')
        + '</td><td>'
        + html.escape(row.get('error', ''))
        + '</td></tr>'
        for row in result.get('required', [])
    )
    optional = ''.join(
        '<li>' + html.escape(row['name'] + '：' + row['detail']) + '</li>'
        for row in result.get('optional', [])
    )
    content = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>报销工具自检</title>'
    content += '<style>body{font:16px system-ui;max-width:900px;margin:40px auto;line-height:1.7}td{padding:10px;border-bottom:1px solid #ddd}</style>'
    content += '<h1>' + ('基础功能已就绪' if result.get('ok') else '安装或自检尚有缺项') + '</h1>'
    content += (
        '<p>已检查本机工具、专项和正式接口。空专项仍须按真实来源配置规则；本次没有执行真实财务动作。</p><table>'
        + rows
        + '</table>'
    )
    content += (
        '<h2>按需接入</h2><ul>'
        + optional
        + '</ul><p>详细记录：同目录result.json和smoke.log。</p></html>'
    )
    (folder / '报告.html').write_text(content, encoding='utf-8')


def run(
    root=ROOT,
    *,
    check_only=False,
    data_dir=None,
    profile_id=None,
    title=None,
    mode=None,
    with_ocr=False,
):
    root = Path(root).resolve()
    if not (root / 'data/distribution.json').is_file():
        raise ValueError('此入口只安装已构建的分发包，不改变维护者原工作区')
    if sys.version_info < (3, 11):
        raise ValueError('请从install.sh/install.cmd进入，自动取得独立Python运行时')
    manifest = verify_members(root)
    config = installation_config(root, data_dir, profile_id, title, mode)
    local = root / '.local'
    if local.is_symlink():
        raise ValueError('本机运行目录不能指向其他位置；保留原目录后使用新解压包')
    local.mkdir(exist_ok=True)
    folder = local / 'self-checks' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    folder.mkdir(parents=True)
    report = {
        'ok': False,
        'phase': 'preflight',
        'version': manifest['version'],
        'config': config,
        'report_path': str(folder / 'result.json'),
    }
    try:
        with file_lock(local / 'portable-install.lock'):
            if not check_only:
                if (local / 'installation.json').exists() and not Path(
                    config['library_root']
                ).exists():
                    raise ValueError('已登记的规范库丢失；先恢复原资料，不将它重置为空模板')
                Path(config['material_root']).mkdir(parents=True, exist_ok=True)
                report['phase'] = 'dependencies'
                atomic_json(folder / 'result.json', report)
                # setup是依赖与客户端配置的唯一owner；此入口只编排它。
                environment = private_environment(root)
                command = [
                    sys.executable,
                    '-X', 'utf8', '-I',
                    str(root / 'assistant.py'),
                    'setup',
                    '--with-materials',
                    '--material-root',
                    config['material_root'],
                ]
                if with_ocr:
                    command.append('--with-ocr')
                with (folder / 'install.log').open('w', encoding='utf-8') as log:
                    process = subprocess.run(
                        command,
                        cwd=root,
                        env=environment,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=600,
                    )
                if process.returncode:
                    raise ValueError(
                        '依赖安装未完成，查看同目录install.log；修复网络后重跑原安装入口'
                    )
                report['phase'] = 'profile'
                ensure_profile(AssistantService(root, [config['material_root']]), config)
                atomic_json(local / 'installation.json', config)
            report.update(
                checks(root, config, folder, ocr_smoke=with_ocr and not check_only), phase='checked'
            )
    except (ValueError, OSError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        report.update(ok=False, error=str(error))
    write_report(folder, report)
    atomic_json(
        local / 'self-check-current.json',
        {
            'result': str(folder / 'result.json'),
            'html': str(folder / '报告.html'),
            'ok': report['ok'],
        },
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--check', action='store_true', help='仅自检已安装环境，不联网安装或修改规则'
    )
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--profile-id')
    parser.add_argument('--title')
    parser.add_argument('--mode', choices=CASE_PROFILES)
    parser.add_argument(
        '--with-ocr', action='store_true', help='显式安装本机OCR并以合成页验收；默认不安装'
    )
    args = parser.parse_args()
    try:
        result = run(
            check_only=args.check,
            data_dir=args.data_dir,
            profile_id=args.profile_id,
            title=args.title,
            mode=args.mode,
            with_ocr=args.with_ocr,
        )
    except (ValueError, OSError, KeyError, RuntimeError) as error:
        result = {'ok': False, 'phase': 'preflight', 'error': str(error)}
    print(
        json.dumps(
            {
                key: result.get(key)
                for key in ('ok', 'phase', 'version', 'report_path', 'error', 'config')
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
