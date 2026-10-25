#!/usr/bin/env python3
"""新用户包验收：真实CLI和可选stdio MCP，合成资料，不接浏览器或财务。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import shutil

ROOT = Path(__file__).resolve().parents[1]


def verify_members(root):
    root = Path(root).resolve()
    manifest_path = root / 'MANIFEST.json'
    if not manifest_path.exists() and (root / '.git').exists():
        # Git检出按当前源码清单验证；不伪装成带冻结SHA的发行包。
        from build_distribution import collect_sources, ALIASES, encoded, digest

        members = collect_sources(root)
        for target in ALIASES:
            path = root / target
            if path.is_symlink() or not path.is_file() or path.read_bytes() != members[target]:
                raise ValueError('公开入口与源码不一致，运行tools/prepare_public_checkout.py：' + target)
        files = [
            {'path': name, 'size': len(raw), 'sha256': digest(raw)}
            for name, raw in sorted(members.items())
        ]
        return {
            'version': json.loads(members['data/distribution.json'])['version'],
            'files': files,
            'verification_mode': 'current_git_checkout',
            'source_tree_sha256': digest(encoded(files)),
        }
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    for item in manifest['files']:
        relative = Path(item['path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('清单成员越界')
        path = root / relative
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('包成员不完整或已变化：' + item['path'])
    return {**manifest, 'verification_mode': 'release_manifest'}


def call_cli(root, request, material_root, temp):
    path = temp / 'request.json'
    path.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
    command = [
        sys.executable,
        str(root / 'assistant.py'),
        'call',
        '--input',
        str(path),
        '--material-root',
        str(material_root),
    ]
    completed = subprocess.run(command, cwd=temp, capture_output=True, text=True, timeout=30)
    if completed.returncode:
        raise ValueError('CLI演练失败：' + completed.stdout + completed.stderr)
    return json.loads(completed.stdout)


async def check_mcp(root, material, workspace):
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters
    from importlib.metadata import version
    from assistant_contract import tool_catalog

    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            str(root / 'tools/reimbursement_mcp.py'),
            '--workspace',
            str(workspace),
            '--material-root',
            str(material),
        ],
        cwd=str(root),
        env={'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'},
    )
    async with Client(parameters, read_timeout_seconds=30) as client:
        catalog = tool_catalog((await client.list_tools()).tools, version('mcp'))
        names = [item['name'] for item in catalog['tools']]

        async def invoke(name, args):
            result = await client.call_tool(name, args)
            if result.is_error:
                raise ValueError('MCP演练失败：' + str(result.content))
            return result.structured_content

        assert 'reimbursement_standards_init' in names
        discovery = await invoke('reimbursement_capabilities', {'intent': 'rules'})
        assert discovery['platform']['registration_checked'] is False
        assert discovery['platform']['bridge_status_checked'] is False
        assert 'registration_path' not in discovery['platform']
        empty = await invoke('reimbursement_cases', {})
        assert empty['cases'] == []
        guide = await invoke('reimbursement_guide', {'topic': 'quickstart'})
        assert 'prepare_batch_file' in guide['text']
        preview = await invoke(
            'reimbursement_standards_init',
            {
                'library_root': str(material / 'mcp-rules'),
                'profile_id': 'demo_mcp',
                'title': '合成专项',
            },
        )
        assert preview['executed'] is False and not (material / 'mcp-rules').exists()
        await invoke('reimbursement_new_case', {'case_id': 'demo_mcp'})
        overview = await invoke(
            'reimbursement_overview', {'case_id': 'demo_mcp', 'include_parameters': True}
        )
        form = overview['parameters']['form']
        assert form['contract']['exists'] is None and not form['payments'] and not form['funding']
        return {
            'ok': True,
            'protocol': 'stdio',
            'tools_discovered': len(names),
            'json_routes_verified': True,
            'browser_actions_executed': False,
        }


def run(root=ROOT, with_mcp=False, with_materials=False, with_ocr=False):
    root = Path(root).resolve()
    manifest = verify_members(root)
    with tempfile.TemporaryDirectory(prefix='reimbursement-distribution-') as temp_name:
        temp = Path(temp_name).resolve()
        material = temp / 'materials'
        material.mkdir()
        empty = call_cli(root, {'method': 'cases'}, material, temp)
        # 不修改接收者已有cases；新案合同检查在临时workspace中进行。
        sys.path.insert(0, str(root / 'tools'))
        from assistant_api import AssistantService

        service = AssistantService(temp / 'workspace', [material])
        service.new_case('demo_portable')
        form = service.overview('demo_portable', include_parameters=True)['parameters']['form']
        assert form['contract']['exists'] is None and form['budget_allocation_mode'] is None
        assert form['funding'] == [] and form['payments'] == []
        args = {
            'library_root': str(material / 'rules'),
            'profile_id': 'demo_club',
            'title': '合成专项',
        }
        preview = call_cli(root, {'method': 'standards_init', 'params': args}, material, temp)
        assert not preview['executed'] and not (material / 'rules').exists()
        created = call_cli(
            root, {'method': 'standards_init', 'params': {**args, 'execute': True}}, material, temp
        )
        assert created['executed'] and not created['system_write']
        selection = call_cli(
            root,
            {
                'method': 'standards_select',
                'params': {
                    'library_root': str(material / 'rules'),
                    'profile_id': 'demo_club',
                    'as_of': '2026-10-24',
                    'context': {
                        'case_id': 'demo_portable',
                        'selected_profile_ids': ['demo_club'],
                        'selection_basis': '合成验收选择',
                    },
                },
            },
            material,
            temp,
        )
        assert (
            selection['composition_order'] == ['common', 'demo_club']
            and not selection['applied_rules']
        )
        from audit_materials import audit

        try:
            audit(
                {'case_id': 'demo_portable'},
                json.loads((root / 'data/material_rules.json').read_text()),
            )
        except ValueError as error:
            assert '尚未配置' in str(error)
        else:
            raise AssertionError('未配置材料规则不得报告齐全')
        materials = {'tested': False}
        if with_materials:
            if sys.version_info < (3, 11):
                raise ValueError('真实原件归档演练须使用Python3.11以上')
            from material_intake_service import configure_bundled_archive

            configure_bundled_archive(service.root, package_root=root)
            source = material / '合成收件.txt'
            source.write_text('合成收件，仅验证原字节归档，不是报销凭证。', encoding='utf-8')
            original = source.read_bytes()
            destination = material / 'intake'
            queued = service.material_intake(
                'demo_portable',
                {
                    'schema': 'reimbursement.material-intake.v1',
                    'case_id': 'demo_portable',
                    'sources': [{'id': 'synthetic', 'path': str(source), 'group_strip_dirs': None}],
                    'output_dir': str(destination),
                },
                'portable-intake-01',
                execute=True,
            )
            deadline = time.monotonic() + 60
            while True:
                job = service.result('demo_portable', queued['job_id'])
                if job['status'] not in {'queued', 'running'}:
                    break
                if time.monotonic() >= deadline:
                    raise ValueError('原材料任务未结束；不重发')
                time.sleep(0.1)
            assert job['status'] == 'completed' and job['exit_code'] == 0, job
            receipt = json.loads((destination / 'receipt.json').read_text(encoding='utf-8'))
            assert receipt['status'] == 'completed' and receipt['original_count'] == 1, receipt
            assert source.read_bytes() == original
            assert Path(receipt['originals'][0]['snapshot']).read_bytes() == original
            materials = {
                'tested': True,
                'original_count': 1,
                'bytes_preserved': True,
                'ocr_started': False,
            }
        ocr = {'tested': False}
        if with_ocr:
            from PIL import Image, ImageDraw, ImageFont
            from material_intake_service import configure_bundled_archive

            provider = root / '.local/ocr-provider.json'
            if not provider.is_file():
                raise ValueError('OCR演练须先明确提供方')
            configure_bundled_archive(service.root, package_root=root)
            shutil.copy2(provider, service.root / '.local/ocr-provider.json')
            image = Image.new('RGB', (1400, 450), 'white')
            draw = ImageDraw.Draw(image)
            font = ImageFont.load_default(size=56)
            draw.multiline_text(
                (50, 50),
                'OCR SELF TEST\nAMOUNT 123.45\nSYNTHETIC ONLY',
                fill='black',
                font=font,
                spacing=25,
            )
            source = material / 'synthetic-ocr.png'
            image.save(source)
            image.close()

            def wait_original(job):
                deadline = time.monotonic() + 240
                while True:
                    value = service.result('demo_portable', job['job_id'])
                    if value['status'] not in {'queued', 'running'}:
                        break
                    if time.monotonic() > deadline:
                        raise ValueError('原OCR演练尚未完成，保留原任务')
                    time.sleep(0.2)
                if value['status'] != 'completed':
                    raise ValueError(str(value.get('error', value)))
                return value

            intake = service.material_intake(
                'demo_portable',
                {
                    'schema': 'reimbursement.material-intake.v1',
                    'case_id': 'demo_portable',
                    'sources': [{'id': 'ocr', 'path': str(source), 'group_strip_dirs': None}],
                    'output_dir': str(material / 'ocr-intake'),
                },
                'portable-ocr-intake',
                execute=True,
            )
            wait_original(intake)
            recognized = wait_original(
                service.material_ocr(
                    'demo_portable',
                    {'intake_job_id': intake['job_id']},
                    'portable-ocr-execute',
                    execute=True,
                )
            )
            evidence = next(
                row for row in recognized['artifacts'] if row['name'] == 'text-evidence.json'
            )
            data = json.loads(
                service.artifact(
                    'demo_portable', recognized['job_id'], evidence['artifact_id']
                ).read_text()
            )
            text = '\n'.join(row['text'] for row in data['records'])
            assert '123.45' in text and 'SELF' in text, text
            ocr = {
                'tested': True,
                'source_pages': 1,
                'expected_tokens_found': True,
                'financial_write_executed': False,
            }
        mcp = (
            asyncio.run(check_mcp(root, material, temp / 'mcp-workspace'))
            if with_mcp
            else {'tested': False}
        )
        return {
            'ok': True,
            'version': manifest['version'],
            'verification_mode': manifest['verification_mode'],
            'verified_files': len(manifest['files']),
            'existing_case_count': len(empty['cases']),
            'cli_checked': True,
            'mcp': mcp,
            'materials': materials,
            'ocr': ocr,
            'synthetic_inputs_only': True,
            'browser_actions_executed': False,
            'financial_write_executed': False,
            'all_ai_clients_tested': False,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mcp', action='store_true')
    parser.add_argument('--materials', action='store_true')
    parser.add_argument(
        '--ocr', action='store_true', help='明确对合成图片运行已配置OCR；默认不执行'
    )
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.output.is_absolute() or args.output.exists():
        raise ValueError('output须为尚不存在的绝对回执路径')
    result = run(with_mcp=args.mcp, with_materials=args.materials, with_ocr=args.ocr)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
