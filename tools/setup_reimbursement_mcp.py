"""只配置当前项目；不安装dsh、不改全局助手配置、不启动财务动作。"""

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from assistant_api import PROJECT, atomic_json
from operator_support import stamp
from workspace_config import (
    portable_workspace,
    resolve_material_roots,
    runtime_python,
    private_environment,
)


def locked_packages(path):
    return dict(
        line.strip().split('==', 1)
        for line in path.read_text(encoding='utf-8').splitlines()
        if line.strip() and not line.lstrip().startswith('#')
    )


def installed_versions(python, names, env=None):
    """真实依赖状态不由安装marker代替；缺失包返回null并交原安装入口修复。"""
    if not python.exists():
        return {}
    code = (
        'import json,sys;from importlib.metadata import version,PackageNotFoundError\n'
        'out={}\nfor name in json.loads(sys.argv[1]):\n'
        ' try:out[name]=version(name)\n except PackageNotFoundError:out[name]=None\n'
        'print(json.dumps(out))'
    )
    probe = subprocess.run(
        [str(python), '-X', 'utf8', '-I', '-c', code, json.dumps(list(names))],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    try:
        value = json.loads(probe.stdout)
        return value if probe.returncode == 0 and isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def material_versions(python, names, env=None):
    if not python.exists():
        return {}
    probe = subprocess.run(
        [
            str(python),
            '-X', 'utf8', '-I',
            '-c',
            'import json,sys,pymupdf,PIL,defusedxml,pyexpat; '
            'from importlib.metadata import version; '
            'sys.exit(2) if pyexpat.version_info < (2,8,5) else None; '
            'print(json.dumps({n:version(n) for n in json.loads(sys.argv[1])}))',
            json.dumps(list(names)),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    if probe.returncode != 0:
        return {}
    try:
        return json.loads(probe.stdout)
    except json.JSONDecodeError:
        return {}


def client_configuration(python, args, project):
    """三种客户端片段来自同一个绑定；生成与自检共用这份格式。"""
    q = lambda value: json.dumps(value, ensure_ascii=False)
    command = {'command': str(python), 'args': args, 'cwd': str(project)}
    return {
        'mcp.json': json.dumps({'mcpServers': {'ustc_reimbursement': command}}, ensure_ascii=False, indent=2) + '\n',
        'codex.toml': '[mcp_servers.ustc_reimbursement]\ncommand = ' + q(str(python))
                      + '\nargs = ' + q(args) + '\ncwd = ' + q(str(project)) + '\n',
        'dsh.patch.yml': '- id: reimbursement-project\n  name: "@deepseek-ai/dsh-mcp-client"\n  config:\n'
                         '    serverName: ustc_reimbursement\n    transport: stdio\n    command: '
                         + q(str(python)) + '\n    args: ' + q(args) + '\n    cwd: ' + q(str(project))
                         + '\n    toolCallTimeoutMs: 30000\n',
    }


def client_configuration_matches(directory, expected):
    try:
        return all((directory / name).read_text(encoding='utf-8') == text
                   for name, text in expected.items())
    except (OSError, UnicodeError):
        return False


def setup(check_only=False, with_materials=False, material_roots=None, with_ocr=False):
    portable = portable_workspace(PROJECT)
    from ocr_runtime import read_profile as read_ocr_profile, status as ocr_status, configure_local

    old_ocr = read_ocr_profile(PROJECT)
    if with_ocr and old_ocr and old_ocr['profile']['backend'] != 'local':
        raise ValueError('已有远程OCR配置，--with-ocr不会自动切换提供方')
    ocr_requested = with_ocr or bool(old_ocr and old_ocr['profile']['backend'] == 'local')
    if ocr_requested:
        with_materials = True
    roots = resolve_material_roots(PROJECT, material_roots, for_setup=True)
    if portable and sys.version_info < (3, 11):
        raise ValueError('试用包的固定归档依赖要求Python3.11以上；请使用相应解释器重新运行setup')
    if any(not path.is_dir() for path in roots):
        raise ValueError('material-root须为本机存在的目录')
    local = PROJECT / '.local'
    python = runtime_python(PROJECT)
    runtime = python.parent.parent
    env = private_environment(PROJECT, create_temp=not check_only) if portable else None
    requirements = PROJECT / 'requirements-mcp.lock.txt'
    if not requirements.exists():
        requirements = PROJECT / 'requirements-mcp.txt'
    expected_mcp = locked_packages(requirements)
    actual_mcp = installed_versions(python, expected_mcp, env) if portable else {}
    pip_prefix = [str(python), '-m', 'pip'] + (['--isolated'] if portable else [])
    pip_options = (
        ['--index-url', 'https://pypi.org/simple', '--only-binary', ':all:'] if portable else []
    )
    if portable and (PROJECT / 'offline').exists():
        from offline_runtime import verify
        offline = verify(PROJECT)
        pip_options = ['--no-index', '--find-links', offline['wheels'], '--only-binary', ':all:']
    if not check_only:
        local.mkdir(exist_ok=True)
        if not python.exists():
            subprocess.run(
                [sys.executable, '-m', 'venv', str(runtime)], check=True, stdout=sys.stderr, env=env
            )
        marker = local / 'mcp_install.json'
        digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
        previous = json.loads(marker.read_text(encoding='utf-8')) if marker.exists() else {}
        if (
            previous.get('requirements_sha256') != digest
            or previous.get('runtime_python') != str(python)
            or (portable and actual_mcp != expected_mcp)
        ):
            subprocess.run(
                [
                    *pip_prefix,
                    'install',
                    '--disable-pip-version-check',
                    '--no-cache-dir',
                    *pip_options,
                    '-r',
                    str(requirements),
                ],
                check=True,
                stdout=sys.stderr,
                env=env,
            )
            atomic_json(
                marker,
                {
                    'requirements_sha256': digest,
                    'runtime_python': str(python),
                    'installed_at_utc': stamp(),
                },
            )
            if portable:
                actual_mcp = installed_versions(python, expected_mcp, env)
    materials = PROJECT / 'requirements-materials.lock.txt'
    expected_materials = locked_packages(materials)
    installed_materials = material_versions(python, expected_materials, env)
    if with_materials and not check_only and installed_materials != expected_materials:
        subprocess.run(
            [
                *pip_prefix,
                'install',
                '--disable-pip-version-check',
                '--no-cache-dir',
                *pip_options,
                '-r',
                str(materials),
            ],
            check=True,
            stdout=sys.stderr,
            env=env,
        )
        installed_materials = material_versions(python, expected_materials, env)
        atomic_json(
            local / 'materials_install.json',
            {
                'requirements_sha256': hashlib.sha256(materials.read_bytes()).hexdigest(),
                'runtime_python': str(python),
                'versions': installed_materials,
                'installed_at_utc': stamp(),
            },
        )
    materials_ready = installed_materials == expected_materials
    if ocr_requested:
        ocr_requirements = PROJECT / 'requirements-ocr.lock.txt'
        expected_ocr = locked_packages(ocr_requirements)
        actual_ocr = installed_versions(python, expected_ocr, env)
        if not check_only and actual_ocr != expected_ocr:
            subprocess.run(
                [
                    *pip_prefix,
                    'install',
                    '--disable-pip-version-check',
                    '--no-cache-dir',
                    *pip_options,
                    '-r',
                    str(ocr_requirements),
                    '-r',
                    str(materials),
                ],
                check=True,
                stdout=sys.stderr,
                env=env,
            )
        if not check_only:
            try:
                configure_local(PROJECT)
            except ValueError as error:
                if getattr(error, 'code', None) != 'ocr_models_changed':
                    raise
                # 只修复本工具虚拟环境中锁定包的模型，不接受变更后的SHA作新基线。
                subprocess.run(
                    [
                        *pip_prefix,
                        'install',
                        '--disable-pip-version-check',
                        '--no-cache-dir',
                        *pip_options,
                        '--force-reinstall',
                        '--no-deps',
                        'rapidocr-onnxruntime==' + expected_ocr['rapidocr-onnxruntime'],
                    ],
                    check=True,
                    stdout=sys.stderr,
                    env=env,
                )
                configure_local(PROJECT)
    ocr = ocr_status(PROJECT)
    version = None
    if python.exists():
        probe = subprocess.run(
            [
                str(python),
                '-X', 'utf8', '-I',
                '-c',
                'from importlib.metadata import version; from mcp.server import MCPServer; print(version("mcp"))',
            ],
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
        )
        if probe.returncode == 0:
            version = probe.stdout.strip()
    sdk_ready = version == expected_mcp.get('mcp') and (not portable or actual_mcp == expected_mcp)
    config_dir = local / 'client-configs'
    config_files = {
        'standard_json': str(config_dir / 'mcp.json'),
        'codex_toml': str(config_dir / 'codex.toml'),
        'dsh_patch': str(config_dir / 'dsh.patch.yml'),
    }
    args = [str(PROJECT / 'tools/reimbursement_mcp.py'), '--workspace', str(PROJECT)]
    for root in roots:
        args.extend(['--material-root', str(root)])
    q = lambda x: json.dumps(x, ensure_ascii=False)
    dsh_patch = (
        '- insert:\n    - id: reimbursement-project\n      name: "@deepseek-ai/dsh-mcp-client"\n      config:\n'
        '        serverName: ustc_reimbursement\n        transport: stdio\n        command: '
        + q(str(python)) + '\n        args: ' + q(args + ['--toolset','compact']) + '\n        cwd: ' + q(str(PROJECT))
        + '\n        toolCallTimeoutMs: 30000\n'
    )
    shell_prefix = '#!/bin/sh\nset -eu\nREIMBURSE_TOOL_ROOT=$(CDPATH= cd -P -- "$(dirname -- "$0")/../.." && pwd -P)\ncd "$REIMBURSE_TOOL_ROOT"\n'
    launchers = {
        '打开报销工作台.command': shell_prefix + 'exec "$REIMBURSE_TOOL_ROOT/.local/mcp-venv/bin/python" "$REIMBURSE_TOOL_ROOT/tools/operator_workbench.py" --open\n',
        '打开报销工作台.cmd': '@echo off\r\nchcp 65001 >nul\r\nset "REIMBURSE_TOOL_ROOT=%~dp0..\\.."\r\nif not exist "%REIMBURSE_TOOL_ROOT%\\.local\\mcp-venv\\Scripts\\pythonw.exe" (echo 请先运行安装与自检。 & pause & exit /b 2)\r\nstart "" "%REIMBURSE_TOOL_ROOT%\\.local\\mcp-venv\\Scripts\\pythonw.exe" "%REIMBURSE_TOOL_ROOT%\\tools\\operator_workbench.py" --open\r\n',
        '打开反馈台.command': shell_prefix + 'exec sh "$REIMBURSE_TOOL_ROOT/tools/reimbursement.sh" material-review open --port 0\n',
        '打开反馈台.cmd': '@echo off\r\nchcp 65001 >nul\r\ncall "%~dp0..\\..\\tools\\reimbursement.cmd" material-review open --port 0\r\nif errorlevel 1 pause\r\n',
        '启动DSH助手.command': shell_prefix + 'command -v dsh >/dev/null 2>&1 || { printf "%s\\n" "请先安装DSH并配置自己的模型，再重开此入口。"; exit 2; }\nexec dsh --profile web --patch "$REIMBURSE_TOOL_ROOT/.local/client-configs/dsh.patch.yml"\n',
        '启动DSH助手.cmd': '@echo off\r\nchcp 65001 >nul\r\nwhere dsh >nul 2>nul\r\nif errorlevel 1 (echo 请先安装DSH并配置自己的模型，再重开此入口。 & pause & exit /b 2)\r\ncd /d "%~dp0..\\.."\r\ncall dsh --profile web --patch "%~dp0dsh.patch.yml"\r\n',
    }
    if sdk_ready and not check_only:
        command = {'command': str(python), 'args': args, 'cwd': str(PROJECT)}
        atomic_json(config_dir / 'mcp.json', {'mcpServers': {'ustc_reimbursement': command}})
        atomic_json(config_dir / 'mcp-compact.json', {'mcpServers': {'ustc_reimbursement': {**command, 'args':args+['--toolset','compact']}}})
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / 'codex.toml').write_text(
            '[mcp_servers.ustc_reimbursement]\ncommand = '
            + q(str(python))
            + '\nargs = '
            + q(args)
            + '\ncwd = '
            + q(str(PROJECT))
            + '\n',
            encoding='utf-8',
        )
        (config_dir / 'dsh.patch.yml').write_text(dsh_patch, encoding='utf-8')
        for name, content in launchers.items():
            target = config_dir / name
            target.write_bytes(content.encode('utf-8'))
            if name.endswith('.command'):
                target.chmod(0o755)
        source = PROJECT / 'skills/ustc-reimbursement'
        for host in ['.agents', '.dsh']:
            dest = PROJECT / host / 'skills/ustc-reimbursement'
            # 共享入口已指向正本时保留软链接；其他入口复制完整技能包。
            if dest.resolve() != source.resolve():
                shutil.copytree(source, dest, dirs_exist_ok=True)
    config_current = False
    if all(Path(p).exists() for p in config_files.values()):
        saved = json.loads((config_dir / 'mcp.json').read_text(encoding='utf-8'))['mcpServers'][
            'ustc_reimbursement'
        ]
        config_current = (
            saved.get('command') == str(python)
            and saved.get('args') == args
            and saved.get('cwd') == str(PROJECT)
            and (config_dir / 'dsh.patch.yml').read_text(encoding='utf-8') == dsh_patch
            and all((config_dir / name).is_file() and (config_dir / name).read_bytes() == content.encode('utf-8')
                    for name, content in launchers.items())
        )
    ready = (
        sdk_ready
        and config_current
        and (not with_materials or materials_ready)
        and (not ocr_requested or ocr['ready_for_request'])
    )
    archive_runtime = None
    if portable:
        from material_intake_service import configure_bundled_archive

        archive_runtime = configure_bundled_archive(
            PROJECT, package_root=PROJECT, check_only=check_only
        )
        ready = ready and archive_runtime['configured']
    result = {
        'ok': ready,
        'ready': ready,
        'checked_at_utc': stamp(),
        'mcp_sdk_version': version,
        'runtime_python': str(python),
        'transport': 'stdio',
        'client_config_files': config_files,
        'user_entrypoints': {name: str(config_dir / name) for name in launchers},
        'materials_requested': with_materials,
        'ocr_requested': ocr_requested,
        'ocr': ocr,
        'materials_ready': materials_ready,
        'material_versions': installed_materials,
        'mcp_dependency_versions': actual_mcp,
        'mcp_dependencies_verified': portable and actual_mcp == expected_mcp,
        'material_roots': [str(root) for root in roots],
        'client_config_files_present': all(Path(p).exists() for p in config_files.values()),
        'client_config_matches_current_project': config_current,
        'project_skill_source': str(PROJECT / 'skills/ustc-reimbursement/SKILL.md'),
        'global_assistant_configuration_changed': False,
        'browser_started': False,
        'system_write': False,
        'bundled_archive_runtime': archive_runtime,
        'next': '读docs/助手接口使用.md；dsh从本项目工作区用--patch加载生成片段。其他助手合并本项目配置片段。',
    }
    if not check_only:
        atomic_json(local / 'assistant_setup.json', result)
    return result
