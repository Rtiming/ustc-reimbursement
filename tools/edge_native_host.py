"""本机扩展Native Messaging主机；请求先留意图，重连不重放上一连接。"""
import json
import os
from pathlib import Path
import queue
import re
import struct
import sys
import threading
import time
import uuid


def _startup_diagnostic(phase, error_type=None, *, state=None):
    """Best-effort, bounded bootstrap metadata, independent of project imports.

    Never create a new runtime root, follow a state-directory symlink, record
    exception text/argv, or write anything to the Native Messaging stdout pipe.
    """
    phases = {'python_entered', 'project_imported', 'project_import_failed',
              'main_entered', 'installation_read_failed'}
    if phase not in phases:
        return False
    try:
        if state is None:
            source = Path(__file__).resolve(strict=True)
            if source.name != 'edge_native_host.py' or source.parent.name != 'tools':
                return False
            state = source.parent.parent / '.local' / 'edge-bridge'
        state = Path(state)
        info = state.lstat()
        # Installation creates this private directory. A failure must not turn
        # an untrusted path into a new diagnostic destination.
        if state.is_symlink() or not state.is_dir() or info.st_mode & 0o077:
            return False
        if hasattr(os, 'getuid') and info.st_uid != os.getuid():
            return False
        event = {'schema':'ustc-native-bootstrap-v1', 'phase':phase,
                 'pid':os.getpid(), 'observed_at':time.time(), 'system_write':False}
        if error_type is not None:
            if not isinstance(error_type, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}', error_type):
                return False
            event['error_type'] = error_type
        raw = (json.dumps(event, separators=(',', ':')) + '\n').encode('utf-8')
        if len(raw) > 1024:
            return False
        target = state/'last-native-bootstrap.json'
        temporary = target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
        descriptor = None
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            with os.fdopen(descriptor, 'wb') as stream:
                descriptor = None
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary.exists():
                temporary.unlink()
        return True
    except (OSError, ValueError, TypeError):
        return False


if __name__ == '__main__':
    _startup_diagnostic('python_entered')
try:
    from platform_runtime import private_path
    from windows_edge_bridge import STATE, MAX_REQUEST, atomic_private
    # 救援操作绕过业务队列；业务操作停滞超过上限时主机停止等待并留证。
    RESCUE_OPERATIONS = frozenset({'ping', 'tab-close', 'reload'})
    ACTIVE_STALL_SECONDS = 45.0
except Exception as error:
    if __name__ == '__main__':
        _startup_diagnostic('project_import_failed', type(error).__name__)
    raise
if __name__ == '__main__':
    _startup_diagnostic('project_imported')


def read_frame(stream):
    header = stream.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise ValueError('incomplete_header')
    size = struct.unpack('<I', header)[0]
    if not 0 < size <= 16_000_000:
        raise ValueError('message_size')
    data = bytearray()
    while len(data) < size:
        part = stream.read(size-len(data))
        if not part:
            raise ValueError('incomplete_message')
        data.extend(part)
    return json.loads(bytes(data).decode('utf-8'))


def write_frame(stream, value):
    data = json.dumps(value, ensure_ascii=False).encode('utf-8')
    if len(data) > 1_000_000:
        raise ValueError('message_size')
    stream.write(struct.pack('<I', len(data)) + data)
    stream.flush()


def serve(input_stream, output_stream):
    from edge_runtime import file_lock
    STATE.mkdir(parents=True, exist_ok=True)
    private_path(STATE)
    for name in ['requests', 'responses', 'intents', 'auth-intents']:
        (STATE/name).mkdir(exist_ok=True)
        private_path(STATE/name)
    epoch = uuid.uuid4().hex
    messages = queue.Queue()
    def receiver():
        try:
            while True:
                item = read_frame(input_stream)
                messages.put(item)
                if item is None:
                    return
        except Exception:
            messages.put(None)
    threading.Thread(target=receiver, daemon=True).start()
    # 同一代码目录只准一个浏览器会话接通，避免profile之间路由漂移。
    with file_lock(STATE/'host.lock'):
        hello = messages.get(timeout=10)
        if not isinstance(hello, dict) or hello.get('type') != 'hello' or hello.get('protocol') != 1:
            raise ValueError('invalid_handshake')
        installed = json.loads((STATE/'installation.json').read_text(encoding='utf-8'))
        if hello.get('extension_id') != installed['extension_id'] or hello.get('build_sha256') != installed['build_sha256']:
            # 只留可核对的版本身份，不能把任意握手正文写进诊断。
            atomic_private(STATE/'last-handshake-mismatch.json', {
                'schema':'ustc-native-handshake-mismatch-v1','observed_at':time.time(),
                'expected_extension_id':installed['extension_id'],
                'expected_build_sha256':installed['build_sha256'],
                'observed_extension_id':hello.get('extension_id') if isinstance(hello.get('extension_id'),str) and re.fullmatch('[a-p]{32}',hello['extension_id']) else None,
                'observed_build_sha256':hello.get('build_sha256') if isinstance(hello.get('build_sha256'),str) and re.fullmatch('[a-f0-9]{64}',hello['build_sha256']) else None,
                'synthetic_profile':hello.get('synthetic_profile') is True,'system_write':False})
            if sys.platform=='darwin':
                from mac_bridge_update import recover_stale_handshake
                if recover_stale_handshake(hello,installed,messages,output_stream,STATE):
                    return
            raise ValueError('extension_build_mismatch_reload_installed_package')
        health = {'schema': 'ustc-native-edge-host-v1', 'epoch': epoch, 'host_pid': os.getpid(),
                  'connected': True, 'heartbeat': time.time(), 'extension_id': hello.get('extension_id'),
                  'build_sha256': hello['build_sha256'],
                  'synthetic_profile': hello.get('synthetic_profile') is True}
        auth_queue=queue.Queue()
        auth_server=None
        if (os.name=='nt' or sys.platform=='darwin') and not health['synthetic_profile']:
            from windows_auth_protocol import validate,MemoryRequest
            def authenticate(packet):
                try: packet=validate(packet)
                except ValueError:
                    return {'ok':False,'state':'invalid_auth_request','login_submitted':False,'system_write':False}
                if sys.platform=='darwin' and packet.get('expected_epoch')!=epoch:
                    packet.clear()
                    return {'ok':False,'state':'auth_intent_mismatch','login_submitted':False,'system_write':False}
                if health.get('active_request_id') or not auth_queue.empty():
                    packet.clear()
                    return {'ok':False,'state':'bridge_busy','login_submitted':False,'system_write':False}
                pending=MemoryRequest(packet);packet.clear()
                auth_queue.put(pending)
                try: return pending.answer.get(timeout=max(.01,pending.deadline-time.monotonic()))
                except queue.Empty:
                    pending.cancel()
                    return {'ok':False,'state':'auth_result_unknown','login_submitted':None,'system_write':False}
            pipe_name='\\\\.\\pipe\\ustc-reimbursement-auth-'+epoch
            try:
                if os.name=='nt':
                    from windows_auth_pipe import AuthPipeServer
                    auth_server=AuthPipeServer(pipe_name,authenticate)
                    health['auth_pipe_name']=pipe_name
                    health['auth_transport']='current_user_named_pipe_memory_only'
                else:
                    from mac_auth_socket import AuthSocketServer
                    auth_server=AuthSocketServer(authenticate)
                    health['auth_socket_path']=auth_server.name
                    health['auth_transport']='current_user_unix_socket_memory_only'
                health['auth_transport_alive']=auth_server.thread.is_alive()
                health['auth_pipe_acl_verified']=auth_server.acl_is_private()
            except Exception:
                health['auth_transport_error']='auth_pipe_setup_failed'
        atomic_private(STATE/'status.json', health)
        active = None
        active_deadline = None
        try:
            while True:
                try:
                    item = messages.get(timeout=.05)
                except queue.Empty:
                    item = 'poll'
                if item is None:
                    return
                if isinstance(item, dict):
                    health['heartbeat'] = time.time()
                    if auth_server is not None:
                        health['auth_transport_alive']=auth_server.thread.is_alive()
                    atomic_private(STATE/'status.json', health)
                    if active and item.get('id') == active['id']:
                        if active.get('channel')=='memory-auth':
                            from windows_auth_protocol import sanitize
                            reply=sanitize(item)
                            auth_record={**active['intent'],'phase':'completed','result':reply}
                            atomic_private(STATE/'auth-intents'/(active['id']+'.json'),auth_record)
                        else:
                            reply = {**item, 'epoch': epoch, 'received_at': time.time()}
                        response_id = active['id']
                        auth_answer=active.get('answer')
                        active = None
                        active_deadline = None
                        health.update(active_request_id=None, active_operation=None)
                        atomic_private(STATE/'status.json', health)
                        if auth_answer is not None:
                            auth_answer.put(reply)
                        else:
                            atomic_private(STATE/'responses'/(response_id+'.json'), reply, exclusive=True)
                    else:
                        # 救援或迟到响应：只回填确实存在且尚未答复的请求，不改active。
                        answer_id = item.get('id')
                        if isinstance(answer_id, str) and re.fullmatch(r'[a-f0-9]{32}', answer_id):
                            answer_path = STATE/'responses'/(answer_id+'.json')
                            if ((STATE/'requests'/(answer_id+'.json')).exists()
                                    and not answer_path.exists()):
                                try:
                                    atomic_private(answer_path, {**item, 'epoch': epoch,
                                                                 'received_at': time.time()}, exclusive=True)
                                    health.update(rescue_request_id=None, rescue_operation=None)
                                    atomic_private(STATE/'status.json', health)
                                except FileExistsError:
                                    pass
                            elif ((STATE/'requests'/(answer_id+'.json')).exists()
                                    and answer_path.exists()):
                                # 已标记停止等待的请求若晚到真实答案，另存不覆盖原标记。
                                try:
                                    previous = json.loads(answer_path.read_text(encoding='utf-8'))
                                except (OSError, ValueError):
                                    previous = {}
                                late_path = STATE/'responses'/(answer_id+'.late.json')
                                if (isinstance(previous, dict)
                                        and previous.get('error_code') == 'request_stalled'
                                        and not late_path.exists()):
                                    try:
                                        atomic_private(late_path, {**item, 'epoch': epoch,
                                                                   'received_at': time.time()}, exclusive=True)
                                    except FileExistsError:
                                        pass
                if active is not None and active_deadline is not None and time.time() > active_deadline:
                    # 停止等待：只解除active并留证；原请求字节、派发意图与未知结果都不动，也不重发。
                    stalled = {'id': active['id'], 'ok': False, 'error_code': 'request_stalled',
                               'epoch': epoch, 'stalled_at': time.time(),
                               'note': '主机停止等待；原操作结果仍未知，未重发'}
                    try:
                        atomic_private(STATE/'responses'/(active['id']+'.json'), stalled, exclusive=True)
                    except FileExistsError:
                        pass
                    active = None
                    active_deadline = None
                    health.update(active_request_id=None, active_operation=None)
                    atomic_private(STATE/'status.json', health)
                try: pending_auth=auth_queue.get_nowait() if active is None else None
                except queue.Empty: pending_auth=None
                if pending_auth is not None:
                    metadata=pending_auth.metadata;auth_answer=pending_auth.answer
                    auth_path=STATE/'auth-intents'/(metadata['id']+'.json')
                    if auth_path.exists():
                        previous=json.loads(auth_path.read_text(encoding='utf-8'))
                        same=all(previous.get(k)==v for k,v in metadata.items())
                        auth_answer.put(previous.get('result') if same and isinstance(previous.get('result'),dict) else
                                        {'ok':False,'state':'auth_original_intent_retained' if same else 'auth_intent_mismatch','login_submitted':None,'system_write':False})
                        pending_auth.cancel()
                        continue
                    atomic_private(auth_path,{**metadata,'phase':'dispatch_intent'},exclusive=True)
                    health.update(active_request_id=metadata['id'],active_operation='cas-login')
                    atomic_private(STATE/'status.json',health)
                    arguments=pending_auth.take_for_dispatch()
                    if arguments is None:
                        expired={'ok':False,'state':'auth_request_expired','login_submitted':None,'system_write':False}
                        atomic_private(auth_path,{**metadata,'phase':'expired_not_dispatched','result':expired})
                        health.update(active_request_id=None,active_operation=None);atomic_private(STATE/'status.json',health)
                        auth_answer.put(expired)
                        continue
                    active={'id':metadata['id'],'channel':'memory-auth','answer':auth_answer,'intent':metadata}
                    wire={'schema':'ustc-native-edge-v1','id':metadata['id'],'epoch':epoch,'operation':'cas-login','expires_at':pending_auth.expires_at,
                          'arguments':arguments}
                    write_frame(output_stream,wire)
                    wire['arguments'].clear()
                    continue
                for path in sorted((STATE/'requests').glob('*.json')):
                    if not re.fullmatch(r'[a-f0-9]{32}\.json', path.name):
                        continue
                    intent = STATE/'intents'/path.name
                    if intent.exists():
                        continue
                    if path.stat().st_size > MAX_REQUEST:
                        continue
                    packet = json.loads(path.read_text(encoding='utf-8'))
                    # 上个连接和已过期请求都不派发，未知结果由上层原marker恢复。
                    if packet.get('epoch') != epoch or packet.get('expires_at', 0) <= time.time():
                        continue
                    if packet.get('schema') != 'ustc-native-edge-v1' or packet.get('id')+'.json' != path.name:
                        continue
                    rescue = packet.get('operation') in RESCUE_OPERATIONS
                    if active is not None and not rescue:
                        continue
                    atomic_private(intent, {'id': packet['id'], 'epoch': epoch, 'dispatch_intent_at': time.time(),
                                            'no_replay': True}, exclusive=True)
                    if rescue:
                        health.update(rescue_request_id=packet['id'], rescue_operation=packet['operation'])
                    else:
                        active = packet
                        active_deadline = time.time() + ACTIVE_STALL_SECONDS
                        health.update(active_request_id=packet['id'], active_operation=packet['operation'])
                    atomic_private(STATE/'status.json', health)
                    write_frame(output_stream, packet)
                    break
        finally:
            if auth_server is not None: auth_server.close()
            health.update(connected=False, heartbeat=time.time())
            atomic_private(STATE/'status.json', health)


def main():
    if os.name != 'nt' and sys.platform != 'darwin':
        raise RuntimeError('Native Messaging主机仅支持Windows或macOS')
    # Native Messaging使用长度帧，Windows不能进行CRLF文本转换。
    if os.name == 'nt':
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    _startup_diagnostic('main_entered', state=STATE)
    try:
        installed = json.loads((STATE/'installation.json').read_text(encoding='utf-8'))
    except Exception as error:
        _startup_diagnostic('installation_read_failed', type(error).__name__, state=STATE)
        raise
    valid_origin = len(sys.argv) >= 2 and sys.argv[1] == 'chrome-extension://' + installed['extension_id'] + '/'
    attempt = {'schema': 'ustc-native-start-v1', 'pid': os.getpid(), 'started_at': time.time(),
               'phase': 'entered', 'origin_valid': valid_origin, 'system_write': False}
    atomic_private(STATE/'last-native-start.json', attempt)
    try:
        if not valid_origin:
            raise RuntimeError('unregistered_extension_origin')
        serve(sys.stdin.buffer, sys.stdout.buffer)
    except Exception as error:
        attempt.update(phase='failed', error_type=type(error).__name__,
                       error_code=str(error) if str(error) in {'invalid_handshake', 'extension_build_mismatch_reload_installed_package', 'unregistered_extension_origin'} else 'native_start_failed')
        atomic_private(STATE/'last-native-start.json', attempt)
        raise
    else:
        attempt.update(phase='disconnected')
        atomic_private(STATE/'last-native-start.json', attempt)


if __name__ == '__main__':
    main()
