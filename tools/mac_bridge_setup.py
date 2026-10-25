"""Prepare/register this project's user-level macOS Edge Native Messaging host.

No browser starts, profile copies, policy changes, credentials or HTTP server.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import uuid

from build_extension import RUNTIME_FILES
import mac_edge_bridge as bridge
from platform_runtime import private_path, process_alive
from windows_bridge_setup import extension_id
from windows_edge_bridge import atomic_private

ROOT = Path(__file__).resolve().parents[1]
STATE = bridge.STATE
CHANNELS = {'stable':'Microsoft Edge','beta':'Microsoft Edge Beta',
            'dev':'Microsoft Edge Dev','canary':'Microsoft Edge Canary'}


def registration_path(channel='stable'):
    if channel not in CHANNELS: raise ValueError('channel须为stable/beta/dev/canary')
    return Path.home()/'Library/Application Support'/CHANNELS[channel]/'NativeMessagingHosts'/(bridge.HOST_NAME+'.json')


def _active_launcher(record=None):
    if record is None:
        try:
            path=STATE/'installation.json'
            record=json.loads(path.read_text(encoding='utf-8')) if path.is_file() and path.stat().st_size<=64_000 else {}
        except (OSError,ValueError): record={}
    relocated=record.get('launcher_relocation') if isinstance(record,dict) else None
    target=diagnostic_directory()/'native-host.sh'
    if (isinstance(record,dict) and record.get('launcher_location')=='user_application_support'
            and record.get('launcher')==str(target) and record.get('extension_id')==extension_id()):
        return target
    if (isinstance(relocated,dict) and relocated.get('schema')=='ustc-native-launcher-relocation-v1'
            and relocated.get('directory')==str(target.parent) and record.get('launcher')==str(target)):
        return target
    return STATE/'native-host.sh'


def _manifest(launcher=None):
    return {'name':bridge.HOST_NAME,'description':'USTC reimbursement fixed-tab bridge',
            'path':str((Path(launcher) if launcher is not None else _active_launcher()).absolute()),'type':'stdio',
            'allowed_origins':['chrome-extension://'+extension_id()+'/']}


def diagnostic_directory():
    return Path.home()/'Library/Application Support/USTC-Reimbursement/native-messaging'/extension_id()


def _launcher_command(executable, host, diagnostic=None):
    if diagnostic is None:
        return '#!/bin/sh\nexec '+shlex.quote(str(executable))+' -u '+shlex.quote(str(host))+' "$@"\n'
    # Temporary startup diagnosis. Native stdout/stdin are inherited unchanged;
    # stderr only passes through an in-memory FIFO and a fixed allowlist filter.
    prefix = '#!/bin/sh\numask 077\ndiag_dir='+shlex.quote(str(diagnostic))+'\n'
    return prefix + r'''event="$diag_dir/last-launcher.json"
event_tmp="$diag_dir/entry-$$.tmp"
stderr_log="$diag_dir/stderr-diagnostic.json"
stderr_tmp="$diag_dir/stderr-$$.tmp"
fifo="$diag_dir/stderr-$$.fifo"
if [ ! -d "$diag_dir" ] || [ -L "$diag_dir" ]; then exit 73; fi
write_event() {
 (set -C; printf '{"schema":"ustc-native-launcher-start-v1","phase":"%s","pid":%s,"host_exit_code":%s,"raw_stderr_persisted":false}\n' "$1" "$$" "$2" > "$event_tmp") || return 1
 /bin/mv -f "$event_tmp" "$event"
}
write_event launcher_entered null || exit 73
if ! /usr/bin/mkfifo -m 600 "$fifo"; then write_event diagnostic_pipe_failed null; exit 73; fi
if ! (set -C; : > "$stderr_tmp"); then /bin/rm -f "$fifo"; exit 73; fi
LC_ALL=C /usr/bin/awk '
BEGIN { first=1; events=0; lines=0; bytes=0; print "{\"schema\":\"ustc-native-stderr-v1\",\"error_classes\":[" }
{
 lines++; bytes+=length($0)+1; code="";
 if ($0 ~ /^PermissionError:/) code="PermissionError";
 else if ($0 ~ /^ModuleNotFoundError:/) code="ModuleNotFoundError";
 else if ($0 ~ /^ImportError:/) code="ImportError";
 else if ($0 ~ /^FileNotFoundError:/) code="FileNotFoundError";
 else if ($0 ~ /^SyntaxError:/) code="SyntaxError";
 else if ($0 ~ /^RuntimeError:/) code="RuntimeError";
 else if ($0 ~ /^OSError:/) code="OSError";
 else if ($0 ~ /^ValueError:/) code="ValueError";
 else if ($0 ~ /^TypeError:/) code="TypeError";
 else if (index($0,"Operation not permitted")) code="operation_not_permitted";
 else if (index($0,"Permission denied")) code="permission_denied";
 else if (index($0,"No such file or directory")) code="file_not_found";
 else if (index($0,"Bad CPU type in executable")) code="bad_cpu_type";
 else if (index($0,"Exec format error")) code="exec_format_error";
 else if (index($0,"bad interpreter")) code="bad_interpreter";
 else if ($0 ~ /^dyld\[/) code="dynamic_loader_error";
 if (code!="") { events++; if (events<=8) { if (!first) printf ","; printf "\"%s\"",code; first=0; } }
}
END { printf "],\"stderr_lines\":%d,\"normalized_line_bytes\":%d,\"truncated\":%s,\"raw_stderr_persisted\":false}\n",lines,bytes,(events>8?"true":"false") }
' < "$fifo" > "$stderr_tmp" &
filter_pid=$!
write_event python_start_requested null
''' + shlex.quote(str(executable))+' -u '+shlex.quote(str(host))+r''' "$@" 2> "$fifo"
host_exit=$?
wait "$filter_pid"
/bin/rm -f "$fifo"
/bin/mv -f "$stderr_tmp" "$stderr_log"
write_event host_exited "$host_exit"
exit "$host_exit"
'''


def _replace_private_bytes(path, raw, mode):
    path = Path(path); temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('xb') as stream:
            temporary.chmod(mode); stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        if temporary.exists(): temporary.unlink()


def _idle():
    path=STATE/'status.json'
    if path.exists():
        health=json.loads(path.read_text(encoding='utf-8'))
        if health.get('active_request_id') or health.get('connected') is True and process_alive(health.get('host_pid')) is True:
            raise ValueError('现有Native Messaging连接或原请求仍在；不替换运行中的主机安装')
    for intent in (STATE/'intents').glob('*.json'):
        if not (STATE/'responses'/intent.name).exists():
            raise ValueError('已有派发意图缺少原响应；保留原请求，不升级或重建连接')


def _prepare_locked(channel='stable',python_executable=None):
    if sys.platform!='darwin': raise ValueError('Mac主机安装仅支持macOS')
    registration_path(channel);_idle()
    previous=None;installation=STATE/'installation.json'
    if installation.exists():
        if installation.is_symlink() or installation.stat().st_size>64_000:
            raise ValueError('已有安装记录路径或大小不符；不覆盖')
        previous=json.loads(installation.read_text(encoding='utf-8'))
        # finalize-normal-launcher已把入口常规化的记录可以继续安装：launcher_relocation
        # 是保留的审计历史，不是永久阻断；诊断包装必须确已清除。
        finalized = (isinstance(previous.get('launcher_finalization'), dict)
                     and previous.get('launcher_diagnostics') is None)
        if (not isinstance(previous,dict)
                or (previous.get('launcher_location')!='user_application_support' and not finalized)
                or (previous.get('launcher_relocation') and not finalized)
                or previous.get('launcher_diagnostics')):
            raise ValueError('已有legacy或诊断入口；仅用明确迁移/finalize动作，不能由prepare/install覆盖；迁移回退使用restore-launcher')
    executable=Path(python_executable or sys.executable)
    if (not executable.is_absolute() or not executable.is_file() or not os.access(executable,os.X_OK)
            or any(char in str(executable) for char in ['\0','\n','\r'])):
        raise ValueError('须明确可执行的本机Python绝对路径')
    host=(ROOT/'tools/edge_native_host.py').resolve()
    if not host.is_file(): raise ValueError('正式Native Messaging主机源码缺失')
    launcher=_private_diagnostic_directory()/'native-host.sh';command=_launcher_command(executable,host)
    if any(char in str(host) for char in ['\0','\n','\r']): raise ValueError('主机源码路径含控制字符')
    if launcher.is_symlink() or launcher.exists() and not launcher.is_file():
        raise ValueError('标准入口已有其他文件类型；保留原入口')
    if previous is not None:
        if (previous.get('launcher')!=str(launcher) or not launcher.is_file()
                or launcher.stat().st_mode & 0o777 != 0o700
                or hashlib.sha256(launcher.read_bytes()).hexdigest()!=previous.get('launcher_sha256')
                or previous.get('host_source')!=str(host)
                or previous.get('extension_id')!=extension_id()):
            raise ValueError('已登记标准入口身份或SHA不符；不覆盖')
    elif launcher.exists() and (launcher.read_text(encoding='utf-8')!=command or launcher.stat().st_mode & 0o777 != 0o700):
        raise ValueError('标准入口已有其他内容；保留原入口')
    source=[ROOT/'extension'/name for name in sorted(RUNTIME_FILES)]
    if any(not p.is_file() for p in source): raise ValueError('正式扩展运行文件不完整')
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source}
    build=hashlib.sha256(json.dumps(hashes,sort_keys=True).encode('utf-8')).hexdigest()
    target=STATE/'package'/build/'extension'
    target.mkdir(parents=True,exist_ok=True);private_path(STATE);private_path(target.parent);private_path(target)
    for path in source:
        destination=target/path.name;content=path.read_bytes()
        if path.name=='native_bridge.js':
            text=content.decode('utf-8')
            if text.count('const BUILD_SHA = null;')!=1: raise ValueError('正式扩展BUILD_SHA占位不唯一')
            content=text.replace('const BUILD_SHA = null;','const BUILD_SHA = '+json.dumps(build)+';').encode('utf-8')
        if destination.exists() and destination.read_bytes()!=content:
            raise ValueError('同SHA扩展目录已存在不同字节，保留原文件')
        if not destination.exists():
            with destination.open('xb') as stream:stream.write(content)
            private_path(destination)
    temporary=launcher.with_name(launcher.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf-8') as stream:
            temporary.chmod(0o700);stream.write(command);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,launcher)
    finally:
        if temporary.exists():temporary.unlink()
    atomic_private(STATE/'native-host.json',_manifest(launcher))
    record={'schema':'ustc-native-edge-installation-v1','backend':bridge.BACKEND,'channel':channel,
            'extension_id':extension_id(),'python':str(executable),'host_source':str(host),
            'host_source_sha256':hashlib.sha256(host.read_bytes()).hexdigest(),
            'launcher':str(launcher.absolute()),'launcher_sha256':hashlib.sha256(launcher.read_bytes()).hexdigest(),
            'launcher_location':'user_application_support','launcher_mode':'single_process_exec',
            'extension_directory':str(target.absolute()),'source_sha256':hashes,'build_sha256':build,
            'browser_profile_copied':False,'global_browser_policy_changed':False,
            'browser_started':False,'page_script_executed':False,'system_write':False,
            'lock_screen_operation_verified':False,'registration_scope':'current_user'}
    atomic_private(STATE/'installation.json',record)
    return {**record,'prepared':True,'host_registered':False,'extension_loaded':None,
            'registration_path':str(registration_path(channel))}


def prepare(channel='stable',python_executable=None):
    if sys.platform!='darwin': raise ValueError('Mac主机安装仅支持macOS')
    registration_path(channel)
    STATE.mkdir(parents=True,exist_ok=True);private_path(STATE)
    from edge_runtime import file_lock
    with file_lock(STATE/'host.lock'):
        return _prepare_locked(channel,python_executable)


def install(channel='stable',python_executable=None):
    if sys.platform!='darwin': raise ValueError('Mac主机安装仅支持macOS')
    path=registration_path(channel);expected=_manifest()
    if path.is_symlink(): raise ValueError('已有同名主机登记为符号链接；保留原登记')
    if path.exists() and json.loads(path.read_text(encoding='utf-8'))!=expected:
        raise ValueError('已有其他位置或来源的同名主机；保留现状，不覆盖')
    record=prepare(channel,python_executable)
    path.parent.mkdir(parents=True,exist_ok=True)
    atomic_private(path,_manifest(Path(record['launcher'])))
    return {**record,'host_registered':True,'local_installation_changed':True,
            'required_user_action':'在当前已登录Edge的扩展页加载或重新加载extension_directory；不重启或复制浏览器profile。'}


def capabilities(channel='stable'):
    """Read only this host's registration and private heartbeat; never inventory."""
    if channel not in CHANNELS: raise ValueError('channel须为stable/beta/dev/canary')
    # A tools-only checkout remains a valid materials entry. Without its own
    # public package, do not resolve a user launcher or inspect any installation.
    missing = [name for name in sorted(RUNTIME_FILES)
               if (ROOT/'extension'/name).is_symlink() or not (ROOT/'extension'/name).is_file()]
    source_error = 'extension_source_missing' if missing else None
    if source_error is None:
        try: extension_id()
        except (OSError, ValueError, KeyError, TypeError): source_error = 'extension_source_invalid'
    if source_error:
        return {'schema':'ustc-finance-capabilities-v1','os':sys.platform,'backend':bridge.BACKEND,
                'channel':channel,'source_available':False,'missing_extension_files':missing,
                'host_installed':False,'registration_path':None,'registration_error':None,
                'registration_checked':False,'bridge_status_checked':False,'launcher_executable':False,
                'bridge_connected':False,'tab_id_namespace':None,'epoch':None,
                'active_request_id':None,'active_operation':None,'extension_directory':None,
                'reason':source_error,'next_action':'restore_project_extension_sources',
                'secure_password_transport_available':False,'silent_default':True,
                'silent_read_supported':False,'silent_new_page_supported':False,
                'browser_started':False,'page_script_executed':False,'system_write':False,
                'foreground_state':'unknown','screen_locked':'unknown','lock_screen_operation_verified':False,
                'login_verified':False,'business_write_verified':False,
                'unsupported':['project_extension_sources_unavailable','mac_native_new_page_not_qualified',
                               'mac_native_credential_transport_not_implemented','screen_lock_live_behavior_not_verified']}
    path=registration_path(channel);installed=False;registration_error=None
    try:
        installed=path.is_file() and not path.is_symlink() and json.loads(path.read_text(encoding='utf-8'))==_manifest()
    except (OSError,ValueError):registration_error='invalid_registration'
    connected={'ready':False,'reason':'unsupported_platform'}
    if sys.platform=='darwin':
        try:connected=bridge.status(state=STATE)
        except bridge.BridgeUnavailable as error:connected={'ready':False,'reason':error.code}
    record={}
    try:
        if (STATE/'installation.json').is_file():record=json.loads((STATE/'installation.json').read_text(encoding='utf-8'))
        if not isinstance(record,dict):raise ValueError('invalid installation')
    except (OSError,ValueError):registration_error='invalid_installation_record'
    launcher=_active_launcher(record)
    return {'schema':'ustc-finance-capabilities-v1','os':sys.platform,'backend':bridge.BACKEND,
            'channel':channel,'source_available':True,'missing_extension_files':[],
            'registration_checked':True,'bridge_status_checked':sys.platform=='darwin',
            'host_installed':installed,'registration_path':str(path),
            'registration_error':registration_error,'launcher_executable':launcher.is_file() and os.access(launcher,os.X_OK),
            'bridge_connected':installed and connected.get('ready') is True,
            'tab_id_namespace':connected.get('tab_id_namespace'),'epoch':connected.get('epoch'),
            'active_request_id':connected.get('active_request_id'),'active_operation':connected.get('active_operation'),
            'extension_directory':record.get('extension_directory'),
            'reason':connected.get('reason'),'secure_password_transport_available':connected.get('secure_password_transport_available') is True,
            'silent_default':True,'silent_read_supported':True,'silent_new_page_supported':connected.get('ready') is True,
            'automatic_update_supported':True,'missing_finance_page_auto_open_supported':True,
            'browser_started':False,'page_script_executed':False,'system_write':False,
            'foreground_state':'unknown','screen_locked':'unknown','lock_screen_operation_verified':False,
            'login_verified':False,'business_write_verified':False,
            'next_action':'use_explicit_native_namespace_and_window_binding' if installed and connected.get('ready') is True
                          else 'reconnect_registered_extension' if installed else 'install_project_native_host',
            'unsupported':([] if connected.get('secure_password_transport_available') is True else ['credential_transport_not_connected'])+
                          ['screen_lock_live_behavior_not_verified']}


_IMPORT_DIAGNOSTIC = """import json,sys
sys.path.insert(0,sys.argv[1])
try:
 import edge_native_host
 result={'ok':True,'stage':'host_imported_without_main','python_version':list(sys.version_info[:3]),'serve_called':False}
except BaseException as error:
 result={'ok':False,'stage':'host_import','error_type':type(error).__name__,'serve_called':False}
print(json.dumps(result,separators=(',',':')))
"""


def _installation_chain(channel='stable'):
    if sys.platform != 'darwin':
        raise ValueError('Mac主机诊断仅支持macOS')
    path = registration_path(channel)
    result = {'schema':'ustc-mac-native-diagnostic-v1', 'channel':channel,
              'browser_contacted':False, 'native_host_main_called':False,
              'queue_created':False, 'installation_changed':False, 'system_write':False,
              'browser_child_access_verified':False, 'lock_screen_operation_verified':False,
              'checks':{}, 'stage':'static_chain'}
    checks = result['checks']
    try:
        installation = STATE/'installation.json'
        if not installation.is_file() or installation.stat().st_size > 64_000:
            raise ValueError('invalid_installation')
        record = json.loads(installation.read_text(encoding='utf-8'))
        if not isinstance(record, dict):
            raise ValueError('invalid_installation')
        host = (ROOT/'tools/edge_native_host.py').resolve(strict=True)
        launcher = _active_launcher(record)
        executable = Path(record.get('python', ''))
        checks['registered_manifest_matches'] = (path.is_file() and not path.is_symlink()
            and path.stat().st_size <= 64_000 and json.loads(path.read_text(encoding='utf-8')) == _manifest(launcher))
        checks['installed_host_path_matches'] = record.get('host_source') == str(host)
        checks['installed_launcher_path_matches'] = record.get('launcher') == str(launcher.absolute())
        checks['python_absolute_executable'] = (executable.is_absolute() and executable.is_file()
            and os.access(executable, os.X_OK) and not any(c in str(executable) for c in ['\0','\n','\r']))
        diagnostics = record.get('launcher_diagnostics')
        diagnostic = diagnostic_directory() if diagnostics else None
        checks['diagnostic_directory_matches'] = diagnostics is None or (isinstance(diagnostics,dict)
            and diagnostics.get('directory') == str(diagnostic))
        expected = _launcher_command(executable,host,diagnostic)
        checks['launcher_private_executable'] = (launcher.is_file() and not launcher.is_symlink()
            and launcher.stat().st_mode & 0o777 == 0o700 and os.access(launcher, os.X_OK))
        checks['launcher_literal_matches'] = launcher.is_file() and launcher.read_text(encoding='utf-8') == expected
        checks['launcher_sha_matches_installation'] = launcher.is_file() and hashlib.sha256(launcher.read_bytes()).hexdigest() == record.get('launcher_sha256')
        result['host_source_sha_matches_installation'] = hashlib.sha256(host.read_bytes()).hexdigest() == record.get('host_source_sha256')
    except (OSError, ValueError, TypeError):
        return {**result, 'ok':False, 'error_type':'InvalidInstallationChain'},None,None,None
    if not all(checks.values()):
        return {**result, 'ok':False, 'error_type':'InstallationChainMismatch'},None,None,None
    return result,record,host,executable


def diagnose(channel='stable'):
    """Import only; success does not qualify browser-child/locked-session access."""
    result,record,host,executable = _installation_chain(channel)
    if record is None:
        return result
    result['stage'] = 'isolated_import'
    try:
        process = subprocess.run([str(executable), '-I', '-S', '-B', '-c', _IMPORT_DIAGNOSTIC, str(host.parent)],
            cwd='/', env={'HOME':str(Path.home()), 'PATH':'/usr/bin:/bin:/usr/sbin:/sbin', 'LANG':'en_US.UTF-8'},
            stdin=subprocess.DEVNULL, capture_output=True, timeout=5)
        result.update(returncode=process.returncode, stderr_bytes=len(process.stderr),
                      stderr_sha256=hashlib.sha256(process.stderr).hexdigest())
        # The fixed child emits only a small allowlisted result. Never return
        # interpreter stderr, exception text, source, argv or the environment.
        if len(process.stdout) > 2048:
            raise ValueError('invalid_import_diagnostic')
        value = json.loads(process.stdout)
        if (not isinstance(value, dict) or type(value.get('ok')) is not bool
                or value.get('serve_called') is not False
                or value.get('stage') not in {'host_imported_without_main','host_import'}):
            raise ValueError('invalid_import_diagnostic')
        error_type = value.get('error_type')
        if error_type is not None and (not isinstance(error_type,str) or not error_type.isidentifier() or len(error_type)>80):
            raise ValueError('invalid_import_diagnostic')
        result.update(import_result={k:value[k] for k in ['ok','stage','error_type','serve_called','python_version'] if k in value},
                      ok=process.returncode == 0 and value['ok'] is True, stage=value['stage'])
        return result
    except subprocess.TimeoutExpired:
        return {**result, 'ok':False, 'error_type':'TimeoutExpired'}
    except (OSError, ValueError, TypeError):
        return {**result, 'ok':False, 'error_type':'ImportDiagnosticUnavailable'}


def _private_diagnostic_directory():
    directory = diagnostic_directory()
    support = Path.home()/'Library/Application Support'
    if not support.is_dir() or support.is_symlink():
        raise ValueError('当前用户Application Support目录不可用；不改登记')
    for path in [support/'USTC-Reimbursement',directory.parent,directory]:
        if not path.exists() and not path.is_symlink(): path.mkdir(mode=0o700)
        info=path.lstat()
        if (path.is_symlink() or not path.is_dir() or info.st_mode & 0o077
                or hasattr(os,'getuid') and info.st_uid != os.getuid()):
            raise ValueError('诊断目录必须为当前用户私有目录；不修改其他目录权限')
    return directory


def _backup_exact(path, raw):
    if path.exists():
        if path.is_symlink() or path.read_bytes()!=raw:
            raise ValueError('同SHA备份已有不同内容；保留原备份')
        return
    with path.open('xb') as stream:
        path.chmod(0o600);stream.write(raw);stream.flush();os.fsync(stream.fileno())


def enable_diagnostics(channel='stable'):
    """Temporarily instrument this installed launcher, without touching its package."""
    if sys.platform!='darwin': raise ValueError('Mac主机诊断仅支持macOS')
    from edge_runtime import file_lock
    with file_lock(STATE/'host.lock'):
        _idle()
        validation,record,host,executable=_installation_chain(channel)
        if record is None: raise ValueError('当前安装链未通过静态校验；不修改launcher')
        if record.get('launcher_diagnostics'):
            return {'ok':True,'diagnostics_enabled':True,'reused_existing_configuration':True,
                    'diagnostic_directory':str(diagnostic_directory()),'installation_changed':False,
                    'browser_contacted':False,'system_write':False}
        if _active_launcher(record)!=STATE/'native-host.sh':
            raise ValueError('internal入口已完成常规化；不能复用外置临时诊断动作')
        for tool in ['/usr/bin/awk','/usr/bin/mkfifo','/bin/mv','/bin/rm']:
            if not os.access(tool,os.X_OK): raise ValueError('系统诊断过滤工具不可用；不修改launcher')
        directory=_private_diagnostic_directory()
        launcher=STATE/'native-host.sh';installation=STATE/'installation.json'
        prior_launcher=launcher.read_bytes();prior_installation=installation.read_bytes()
        launcher_sha=hashlib.sha256(prior_launcher).hexdigest();installation_sha=hashlib.sha256(prior_installation).hexdigest()
        history=STATE/'diagnostic-backups';history.mkdir(exist_ok=True);private_path(history)
        launcher_backup=history/('launcher-'+launcher_sha+'.sh')
        installation_backup=history/('installation-'+installation_sha+'.json')
        _backup_exact(launcher_backup,prior_launcher);_backup_exact(installation_backup,prior_installation)
        command=_launcher_command(executable,host,directory).encode('utf-8')
        diagnostics={'schema':'ustc-native-launcher-diagnostic-v1','directory':str(directory),
            'previous_launcher_sha256':launcher_sha,'previous_installation_sha256':installation_sha,
            'launcher_backup':str(launcher_backup),'installation_backup':str(installation_backup),
            'raw_stderr_persisted':False,'stdout_unchanged':True,'temporary':True}
        changed={**record,'launcher_sha256':hashlib.sha256(command).hexdigest(),'launcher_diagnostics':diagnostics}
        try:
            _replace_private_bytes(launcher,command,0o700)
            atomic_private(installation,changed)
        except Exception:
            _replace_private_bytes(launcher,prior_launcher,0o700)
            _replace_private_bytes(installation,prior_installation,0o600)
            raise
        return {'ok':True,'diagnostics_enabled':True,'diagnostic_directory':str(directory),
                'launcher_event':str(directory/'last-launcher.json'),'stderr_diagnostic':str(directory/'stderr-diagnostic.json'),
                'guard_host_lock_and_idle_passed':True,'installation_changed':True,'browser_contacted':False,
                'package_changed':False,'manifest_changed':False,'build_sha256':record['build_sha256'],
                'launcher_sha256':changed['launcher_sha256'],'backups':diagnostics,'system_write':False}


def disable_diagnostics(channel='stable'):
    """Restore exact pre-diagnostic launcher/installation bytes while the host is idle."""
    if sys.platform!='darwin': raise ValueError('Mac主机诊断仅支持macOS')
    from edge_runtime import file_lock
    with file_lock(STATE/'host.lock'):
        _idle()
        validation,record,host,executable=_installation_chain(channel)
        if record is None: raise ValueError('当前安装链未通过静态校验；不恢复launcher')
        if record.get('launcher_relocation'):
            raise ValueError('先用restore-launcher恢复外置入口，再取消临时诊断')
        diagnostics=record.get('launcher_diagnostics')
        if not diagnostics: return {'ok':True,'diagnostics_enabled':False,'installation_changed':False,'system_write':False}
        history=STATE/'diagnostic-backups';originals={}
        for kind,suffix in [('launcher','sh'),('installation','json')]:
            digest=diagnostics.get('previous_'+kind+'_sha256')
            if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
                raise ValueError('原诊断备份身份不完整；不恢复')
            path=history/(kind+'-'+digest+'.'+suffix)
            if path.is_symlink() or str(path)!=diagnostics.get(kind+'_backup'):
                raise ValueError('原诊断备份路径不符；不恢复')
            raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=digest: raise ValueError('原诊断备份SHA不符；不恢复')
            originals[kind]=raw
        original=json.loads(originals['installation'])
        if (original.get('build_sha256')!=record.get('build_sha256') or original.get('extension_id')!=record.get('extension_id')
                or originals['launcher']!=_launcher_command(executable,host).encode('utf-8')):
            raise ValueError('原诊断备份不属于同一安装；不恢复')
        launcher=STATE/'native-host.sh';installation=STATE/'installation.json'
        current_launcher=launcher.read_bytes();current_installation=installation.read_bytes()
        try:
            _replace_private_bytes(launcher,originals['launcher'],0o700)
            _replace_private_bytes(installation,originals['installation'],0o600)
        except Exception:
            _replace_private_bytes(launcher,current_launcher,0o700)
            _replace_private_bytes(installation,current_installation,0o600)
            raise
        return {'ok':True,'diagnostics_enabled':False,'original_bytes_restored':True,'installation_changed':True,
                'package_changed':False,'manifest_changed':False,'browser_contacted':False,'system_write':False}


def _relocation_backups(raws):
    history=STATE/'relocation-backups';history.mkdir(exist_ok=True);private_path(history)
    saved={}
    for kind,raw in raws.items():
        digest=hashlib.sha256(raw).hexdigest();suffix='sh' if kind=='launcher' else 'json'
        path=history/(kind+'-'+digest+'.'+suffix);_backup_exact(path,raw)
        saved[kind]={'sha256':digest,'path':str(path)}
    return saved


def relocate_launcher(channel='stable'):
    """Move only this tiny executable entry into current-user Application Support.

    The registered Python, source, extension, runtime state and OS access checks
    stay exactly where they were. This does not grant filesystem permissions.
    """
    if sys.platform!='darwin': raise ValueError('Mac主机入口迁移仅支持macOS')
    from edge_runtime import file_lock
    with file_lock(STATE/'host.lock'):
        _idle()
        validation,record,host,executable=_installation_chain(channel)
        if record is None: raise ValueError('当前安装链未通过静态校验；不迁移launcher')
        if record.get('launcher_relocation'):
            return {'ok':True,'launcher_relocated':True,'reused_existing_configuration':True,
                    'launcher':record['launcher'],'installation_changed':False,'browser_contacted':False,'system_write':False}
        if not record.get('launcher_diagnostics'):
            raise ValueError('本次定位须先启用私有启动诊断；不迁移无诊断入口')
        directory=_private_diagnostic_directory();target=directory/'native-host.sh'
        launcher=STATE/'native-host.sh';installation=STATE/'installation.json'
        registration=registration_path(channel);project_manifest=STATE/'native-host.json'
        raws={'launcher':launcher.read_bytes(),'installation':installation.read_bytes(),
              'registration':registration.read_bytes(),'project_manifest':project_manifest.read_bytes()}
        if json.loads(raws['project_manifest'])!=_manifest(launcher):
            raise ValueError('项目manifest与已登记入口不同；不迁移')
        if target.is_symlink() or target.exists() and (not target.is_file() or target.read_bytes()!=raws['launcher']):
            raise ValueError('目标launcher已有其他内容；保留原入口')
        created=not target.exists();backups=_relocation_backups(raws)
        relocation={'schema':'ustc-native-launcher-relocation-v1','directory':str(directory),
                    'source_launcher':str(launcher),'target_created':created,'backups':backups,
                    'runtime_moved':False,'permissions_changed':False,'stdout_unchanged':True}
        changed={**record,'launcher':str(target),'launcher_relocation':relocation}
        manifest=_manifest(target)
        try:
            if created:
                with target.open('xb') as stream:
                    target.chmod(0o700);stream.write(raws['launcher']);stream.flush();os.fsync(stream.fileno())
            elif target.stat().st_mode & 0o777 != 0o700:
                raise ValueError('目标launcher权限不是0700；不修改其他文件权限')
            atomic_private(installation,changed)
            atomic_private(project_manifest,manifest)
            atomic_private(registration,manifest)
        except Exception:
            _replace_private_bytes(installation,raws['installation'],0o600)
            _replace_private_bytes(project_manifest,raws['project_manifest'],0o600)
            _replace_private_bytes(registration,raws['registration'],0o600)
            if created and target.is_file() and not target.is_symlink() and target.read_bytes()==raws['launcher']:
                target.unlink()
            raise
        return {'ok':True,'launcher_relocated':True,'launcher':str(target),
                'launcher_sha256':hashlib.sha256(raws['launcher']).hexdigest(),'diagnostic_directory':str(directory),
                'guard_host_lock_and_idle_passed':True,'installation_changed':True,'manifest_path_only_changed':True,
                'python':record['python'],'host_source':record['host_source'],'build_sha256':record['build_sha256'],
                'package_changed':False,'runtime_moved':False,'permissions_changed':False,'browser_contacted':False,
                'backups':backups,'system_write':False}


def restore_launcher(channel='stable'):
    """Restore the exact project manifests and installation recorded before relocation."""
    if sys.platform!='darwin': raise ValueError('Mac主机入口恢复仅支持macOS')
    from edge_runtime import file_lock
    with file_lock(STATE/'host.lock'):
        _idle()
        validation,record,host,executable=_installation_chain(channel)
        if record is None: raise ValueError('当前安装链未通过静态校验；不恢复launcher')
        relocation=record.get('launcher_relocation')
        if not relocation:
            return {'ok':True,'launcher_relocated':False,'installation_changed':False,'system_write':False}
        target=diagnostic_directory()/'native-host.sh';launcher=STATE/'native-host.sh'
        installation=STATE/'installation.json';registration=registration_path(channel);project_manifest=STATE/'native-host.json'
        saved=relocation.get('backups');raws={}
        if not isinstance(saved,dict) or set(saved)!={'launcher','installation','registration','project_manifest'}:
            raise ValueError('原入口备份不完整；不恢复')
        for kind,value in saved.items():
            digest=value.get('sha256') if isinstance(value,dict) else None
            if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
                raise ValueError('原入口备份SHA不完整；不恢复')
            path=STATE/'relocation-backups'/(kind+'-'+digest+('.sh' if kind=='launcher' else '.json'))
            if str(path)!=value.get('path') or path.is_symlink(): raise ValueError('原入口备份路径不符；不恢复')
            raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=digest: raise ValueError('原入口备份SHA不符；不恢复')
            raws[kind]=raw
        original=json.loads(raws['installation'])
        if (original.get('launcher')!=str(launcher) or relocation.get('source_launcher')!=str(launcher)
                or any(original.get(k)!=record.get(k) for k in ['extension_id','build_sha256','python','host_source'])
                or any(json.loads(raws[k])!=_manifest(launcher) for k in ['registration','project_manifest'])
                or launcher.is_symlink() or launcher.read_bytes()!=raws['launcher']):
            raise ValueError('原入口身份或外置launcher已变；不恢复')
        paths={'installation':installation,'registration':registration,'project_manifest':project_manifest}
        current={kind:path.read_bytes() for kind,path in paths.items()}
        try:
            for kind,path in paths.items(): _replace_private_bytes(path,raws[kind],0o600)
        except Exception:
            for kind,path in paths.items(): _replace_private_bytes(path,current[kind],0o600)
            raise
        removed=False
        if (relocation.get('target_created') is True
                and hashlib.sha256(target.read_bytes()).hexdigest()==record.get('launcher_sha256')):
            target.unlink();removed=True
        return {'ok':True,'launcher_relocated':False,'original_bytes_restored':True,'internal_launcher_removed':removed,
                'installation_changed':True,'package_changed':False,'runtime_moved':False,
                'permissions_changed':False,'browser_contacted':False,'system_write':False}


def finalize_normal_launcher(channel='stable'):
    """Keep the standard internal path and replace its temporary diagnostic wrapper.

    Requires a disconnected, idle host. It never terminates a host, touches its
    epoch/queues, or changes either manifest; the caller owns browser lifecycle.
    """
    if sys.platform!='darwin': raise ValueError('Mac主机入口常规化仅支持macOS')
    from edge_runtime import file_lock
    with file_lock(STATE/'host.lock'):
        _idle()
        validation,record,host,executable=_installation_chain(channel)
        if record is None: raise ValueError('当前安装链未通过静态校验；不常规化launcher')
        target=diagnostic_directory()/'native-host.sh';relocation=record.get('launcher_relocation')
        command=_launcher_command(executable,host).encode('utf-8');digest=hashlib.sha256(command).hexdigest()
        if (record.get('launcher_location')=='user_application_support' and not relocation
                and record.get('launcher_mode')=='single_process_exec' and record.get('launcher_sha256')==digest):
            return {'ok':True,'normal_launcher':True,'reused_existing_configuration':True,
                    'launcher':str(target),'installation_changed':False,'browser_contacted':False,'system_write':False}
        if not isinstance(relocation,dict) or _active_launcher(record)!=target:
            raise ValueError('须为已登记的标准internal入口；不更改外置安装')
        if record.get('launcher_finalization'):
            if (record.get('launcher_diagnostics') is not None or record.get('launcher_sha256')!=digest
                    or relocation.get('active_launcher_sha256')!=digest or relocation.get('launcher_mode')!='single_process_exec'):
                raise ValueError('已登记常规入口的SHA或模式不符；保留当前安装')
            return {'ok':True,'normal_launcher':True,'reused_existing_configuration':True,
                    'launcher':str(target),'installation_changed':False,'browser_contacted':False,'system_write':False}
        if not isinstance(record.get('launcher_diagnostics'),dict):
            raise ValueError('原临时诊断记录缺失；不猜测入口历史')
        _private_diagnostic_directory()
        installation=STATE/'installation.json';prior_launcher=target.read_bytes();prior_installation=installation.read_bytes()
        prior_relocation=json.dumps(relocation,sort_keys=True,separators=(',',':')).encode('utf-8')
        history=STATE/'normal-launcher-backups';history.mkdir(exist_ok=True);private_path(history)
        backups={}
        for kind,raw in [('launcher',prior_launcher),('installation',prior_installation),('relocation',prior_relocation)]:
            sha=hashlib.sha256(raw).hexdigest();path=history/(kind+'-'+sha+('.sh' if kind=='launcher' else '.json'))
            _backup_exact(path,raw);backups[kind]={'path':str(path),'sha256':sha}
        finalization={'schema':'ustc-native-normal-launcher-v1','backups':backups,
                      'diagnostic_history':record['launcher_diagnostics'],
                      'launcher_mode':'single_process_exec','stdout_unchanged':True,'manifest_changed':False}
        changed={**record,'launcher_sha256':digest,'launcher_finalization':finalization,
                 'launcher_relocation':{**relocation,'active_launcher_sha256':digest,'launcher_mode':'single_process_exec'}}
        changed.pop('launcher_diagnostics')
        try:
            _replace_private_bytes(target,command,0o700)
            atomic_private(installation,changed)
        except Exception:
            _replace_private_bytes(target,prior_launcher,0o700)
            _replace_private_bytes(installation,prior_installation,0o600)
            raise
        return {'ok':True,'normal_launcher':True,'launcher':str(target),'launcher_sha256':digest,
                'guard_host_lock_and_idle_passed':True,'installation_changed':True,'manifest_changed':False,
                'package_changed':False,'runtime_moved':False,'permissions_changed':False,'browser_contacted':False,
                'host_terminated':False,'epoch_changed':False,'backups':backups,'system_write':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','install','status','diagnose','enable-diagnostics','disable-diagnostics','relocate-launcher','restore-launcher','finalize-normal-launcher'])
    parser.add_argument('--channel',choices=sorted(CHANNELS),default='stable')
    parser.add_argument('--python',dest='python_executable')
    args=parser.parse_args()
    actions={'status':capabilities,'diagnose':diagnose,'enable-diagnostics':enable_diagnostics,'disable-diagnostics':disable_diagnostics,
             'relocate-launcher':relocate_launcher,'restore-launcher':restore_launcher,'finalize-normal-launcher':finalize_normal_launcher}
    if args.action in actions and args.python_executable:parser.error('诊断动作不接受--python或更改解释器')
    result = (actions[args.action](args.channel) if args.action in actions
              else {'prepare':prepare,'install':install}[args.action](args.channel,args.python_executable))
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
