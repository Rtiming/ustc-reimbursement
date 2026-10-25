"""准备/登记Windows财务桥；只改本项目目录及HKCU本项目Native Messaging键。"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import zipfile

from platform_runtime import private_path, is_private_path
from windows_edge_bridge import ROOT, STATE, HOST_NAME, atomic_private, status
from build_extension import RUNTIME_FILES

REGISTRY = r'Software\Microsoft\Edge\NativeMessagingHosts' + '\\' + HOST_NAME
COMPAT_REGISTRY = r'Software\Google\Chrome\NativeMessagingHosts' + '\\' + HOST_NAME


def extension_id():
    manifest = json.loads((ROOT/'extension/manifest.json').read_text(encoding='utf-8'))
    digest = hashlib.sha256(base64.b64decode(manifest['key'])).hexdigest()[:32]
    return ''.join(chr(ord('a') + int(c, 16)) for c in digest)


def capabilities():
    if sys.platform == 'darwin':
        # Import at call time: mac setup reuses only this module's pure ID owner.
        from mac_bridge_setup import capabilities as mac_capabilities
        from finance_backend import backend
        native = mac_capabilities()
        selected = backend()
        return {**native, 'backend':selected,
                'native_messaging_host_installed':native['host_installed'],
                'native_messaging_connected':native['bridge_connected'],
                'native_messaging':native,
                'selected_backend_ready':native['bridge_connected'] if selected=='edge_native_messaging' else None,
                'next_action':native['next_action'] if selected=='edge_native_messaging' else 'use_existing_mac_native_bridge'}
    windows = sys.platform == 'win32'
    connected = status() if windows else {'ready': False, 'reason': 'macos_uses_applescript'}
    installed = False
    if windows:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY) as key:
                manifest, _ = winreg.QueryValueEx(key, '')
            installed = Path(manifest).resolve() == (STATE/'native-host.json').resolve() and Path(manifest).is_file()
        except FileNotFoundError:
            pass
    production_connected = bool(connected.get('ready') and not connected.get('synthetic_profile'))
    package = STATE/'package'/'extension'
    next_action = ('recover_original_browser_request' if production_connected and connected.get('active_request_id') else
                   'use_access_check_with_explicit_case_and_tab' if production_connected else
                   'load_project_extension_in_current_edge' if installed else 'install_project_native_host' if windows else 'use_existing_mac_native_bridge')
    return {'schema': 'ustc-finance-capabilities-v1', 'os': sys.platform, 'system_write': False,
            'browser_started': False, 'page_script_executed': False, 'host_installed': installed,
            'bridge_connected': production_connected, 'synthetic_test_connected': bool(connected.get('ready') and connected.get('synthetic_profile')),
            'active_request_id': connected.get('active_request_id'), 'active_operation': connected.get('active_operation'),
            'login_verified': False, 'business_write_verified': False,
            'secure_password_transport_available': bool(production_connected and connected.get('auth_pipe_name') and connected.get('auth_transport_alive') and connected.get('auth_pipe_acl_verified')),
            'credential_login_requires': '会话失效、本案execute授权、确切密码库引用；验证码仍由本人完成',
            'backend': 'edge_native_messaging' if windows else 'edge_applescript' if sys.platform == 'darwin' else 'unsupported',
            'silent_default': True, 'silent_read_supported': sys.platform in ['win32', 'darwin'],
            'silent_new_page_supported': windows, 'silent_false_scope': 'new-interface-or-explicit-pdf-export',
            'next_action': next_action, 'extension_directory': str(package) if windows else None,
            'required_user_action': ('在已正常登录的Edge打开edge://extensions，启用开发人员模式，加载此扩展目录；已有旧扩展先核对，勿删除浏览器数据。'
                                     if windows and installed and not production_connected else None),
            'unsupported': (['windows_material_safe_open_not_qualified'] + ([] if connected.get('auth_pipe_name') else ['windows_secure_auth_bridge_not_connected'])) if windows else []}


def _prepare_unlocked():
    STATE.mkdir(parents=True, exist_ok=True)
    private_path(STATE)
    package = STATE/'package'
    package.mkdir(exist_ok=True)
    private_path(package)
    target = package/'extension'
    target.mkdir(exist_ok=True)
    private_path(target)
    source_files = [ROOT/'extension'/name for name in sorted(RUNTIME_FILES)]
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files if p.is_file()}
    build_sha = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode('utf-8')).hexdigest()
    for path in source_files:
        if path.is_file():
            shutil.copyfile(path, target/path.name)
    bridge_script = target/'native_bridge.js'
    bridge_script.write_text(bridge_script.read_text(encoding='utf-8').replace('const BUILD_SHA = null;', 'const BUILD_SHA = '+json.dumps(build_sha)+';'), encoding='utf-8')
    archive = package/'报销财务扩展.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(target.iterdir()):
            bundle.write(path, 'extension/'+path.name)
    project_python = ROOT/'.local/mcp-venv/Scripts/python.exe'
    executable = str(project_python if project_python.is_file() else Path(sys.executable).resolve())
    host = str(ROOT/'tools/edge_native_host.py')
    if any(char in executable+host for char in ['%', '\n', '\r', '"']):
        raise ValueError('Native Messaging启动路径含不支持的shell字符')
    launcher = STATE/'native-host.cmd'
    # 二进制stdin属于Native Messaging，不能交给chcp；相对路径展开保留Unicode。
    launcher_python = r'%~dp0..\mcp-venv\Scripts\python.exe' if project_python.is_file() else executable
    with launcher.open('w', encoding='utf-8', newline='') as stream:
        stream.write('@echo off\r\n"'+launcher_python+'" -u "'+r'%~dp0..\..\tools\edge_native_host.py'+'" %*\r\n')
    private_path(launcher)
    manifest = {'name': HOST_NAME, 'description': 'USTC reimbursement fixed-tab bridge', 'path': str(launcher),
                'type': 'stdio', 'allowed_origins': ['chrome-extension://'+extension_id()+'/']}
    atomic_private(STATE/'native-host.json', manifest)
    record = {'schema': 'ustc-native-edge-installation-v1', 'extension_id': extension_id(), 'python': executable,
              'host_source': host, 'extension_directory': str(target), 'extension_archive': str(archive),
              'source_sha256': hashes, 'build_sha256': build_sha,
              'browser_profile_copied': False, 'global_browser_policy_changed': False}
    atomic_private(STATE/'installation.json', record)
    (package/'安装说明.txt').write_text('1. 运行本项目tools/windows_bridge_setup.py install，注册当前用户主机。\n'
        '2. 在现有Edge打开edge://extensions，打开开发人员模式，加载已解压的extension目录。\n'
        '3. 正常登录财务；运行tools/windows_bridge_setup.py status核对。\n'
        '不会复制登录或密码；不要使用独立测试profile进行真实财务。\n', encoding='utf-8')
    return record


def ensure_private_state():
    if STATE.is_symlink() or STATE.absolute() != STATE.resolve():
        raise ValueError('桥运行目录必须使用本机规范路径，不接受链接或重定向')
    try:
        STATE.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        if not STATE.is_dir() or not is_private_path(STATE):
            raise ValueError('桥运行目录属于其他用户或权限不符；请使用独立工具目录')
    else:
        private_path(STATE)


def assert_idle_state(directory):
    directory = Path(directory)
    if not is_private_path(directory):
        raise ValueError('旧主机运行目录不属于当前用户隔离范围')
    health = directory / 'status.json'
    if health.exists():
        if not is_private_path(health):
            raise ValueError('旧主机状态权限不符')
        value = json.loads(health.read_text(encoding='utf-8'))
        if value.get('active_request_id') or value.get('rescue_request_id'):
            raise ValueError('主机保留未完成请求，不准备、迁移或清理原现场')
    # 历史意图/响应原样保留；不在安装器中猜测金融结果或替案例恢复清理记录。


def prepare():
    from contextlib import nullcontext
    from platform_runtime import file_lock, windows_bridge_install_lock
    guard = windows_bridge_install_lock() if os.name == 'nt' else nullcontext()
    with guard:
        ensure_private_state()
        with file_lock(STATE / 'host.lock', busy_message='当前主机仍在运行，不重写已安装程序'):
            assert_idle_state(STATE)
            return _prepare_unlocked()


def registration_snapshot():
    if sys.platform != 'win32':
        raise ValueError('仅Windows存在本机Native Messaging注册')
    import winreg
    rows = []
    for location in (REGISTRY, COMPAT_REGISTRY):
        key_exists = False
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, location) as key:
                key_exists = True
                current, _ = winreg.QueryValueEx(key, '')
        except FileNotFoundError:
            current = None
        row = {'key': location, 'key_exists': key_exists, 'manifest': current}
        if current:
            path = Path(current)
            if not path.is_absolute() or path.name != 'native-host.json' or path.parent.name != 'edge-bridge' or path.parent.parent.name != '.local':
                raise ValueError('已有主机登记不属于本工具目录结构，不读取或覆盖')
            if path.is_symlink() or path.absolute() != path.resolve() or not path.is_file() or path.stat().st_size > 16384:
                raise ValueError('已有主机清单缺失、过大或是链接，先核对原接线')
            raw = path.read_bytes()
            value = json.loads(raw)
            if (value.get('name') != HOST_NAME or value.get('type') != 'stdio'
                    or Path(value.get('path', '')).resolve() != (path.parent / 'native-host.cmd').resolve()
                    or value.get('allowed_origins') != ['chrome-extension://' + extension_id() + '/']):
                raise ValueError('已有主机身份不同，不能迁移')
            row['manifest_sha256'] = hashlib.sha256(raw).hexdigest()
        rows.append(row)
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    return {'registrations': rows, 'registration_sha256': digest,
            'migration_required': any(x['manifest'] and Path(x['manifest']).resolve() != (STATE/'native-host.json').resolve() for x in rows)}


def install(expected_registration_sha256=None):
    if sys.platform != 'win32':
        raise ValueError('仅Windows安装Native Messaging；Mac继续原生桥')
    from platform_runtime import windows_bridge_install_lock
    with windows_bridge_install_lock():
        return _install_locked(expected_registration_sha256)


def _install_locked(expected_registration_sha256=None):
    snapshot = registration_snapshot()
    if expected_registration_sha256 is not None and snapshot['registration_sha256'] != expected_registration_sha256:
        raise ValueError('本机主机登记与预览不同，未覆盖')
    if snapshot['migration_required'] and expected_registration_sha256 is None:
        raise ValueError('已有其他位置的本项目主机；先bridge install预览，再携登记SHA迁移')
    ensure_private_state()
    import winreg
    expected = str(STATE/'native-host.json')
    # Edge文档声明Chrome登记为兼容回退；只登记本项目同名host，不改任何浏览器策略。
    from contextlib import ExitStack
    from platform_runtime import file_lock
    with ExitStack() as locks:
        locks.enter_context(file_lock(STATE / 'host.lock', busy_message='当前主机仍在运行，不重写已安装程序'))
        assert_idle_state(STATE)
        for old in {x['manifest'] for x in snapshot['registrations'] if x['manifest']}:
            directory = Path(old).parent
            if directory.resolve() == STATE.resolve():
                continue
            locks.enter_context(file_lock(directory / 'host.lock', busy_message='旧主机仍在运行，保留原连接，不迁移'))
            assert_idle_state(directory)
        if registration_snapshot()['registration_sha256'] != snapshot['registration_sha256']:
            raise ValueError('迁移前注册表或原清单发生变化，未覆盖')
        record = _prepare_unlocked()
        if snapshot['migration_required']:
            atomic_private(STATE/'previous-registration.json', snapshot)
        changed = []
        try:
            for location in [REGISTRY, COMPAT_REGISTRY]:
                changed.append(location)
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, location) as key:
                    winreg.SetValueEx(key, '', 0, winreg.REG_SZ, expected)
            after = registration_snapshot()
            if any(x['manifest'] != expected for x in after['registrations']):
                raise ValueError('新主机登记独立回读不一致')
        except Exception:
            # 仅恢复仍指向本次新主机的键；其他进程改过的值不覆盖。
            for location in changed:
                original_row = next(x for x in snapshot['registrations'] if x['key'] == location)
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, location) as key:
                        value, _ = winreg.QueryValueEx(key, '')
                except FileNotFoundError:
                    if not original_row['key_exists']:
                        try:
                            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, location)
                        except FileNotFoundError:
                            pass
                    continue
                if value != expected:
                    continue
                original = original_row['manifest']
                if original is None:
                    if original_row['key_exists']:
                        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, location) as key:
                            winreg.DeleteValue(key, '')
                    else:
                        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, location)
                else:
                    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, location) as key:
                        winreg.SetValueEx(key, '', 0, winreg.REG_SZ, original)
            raise
    return {**record, 'host_registered': True, 'registration_scope': 'current_user',
            'registration_keys': [REGISTRY, COMPAT_REGISTRY], 'extension_loaded': False,
            'registration_migrated': snapshot['migration_required']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'install', 'status'])
    args = parser.parse_args()
    result = {'prepare': prepare, 'install': install, 'status': capabilities}[args.action]()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
