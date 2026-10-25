"""Mac后台财务桥卡死请求的诊断、受控自动恢复与恢复后执行验收。

卡死的页面执行承诺挂在扩展Service Worker的**模块级**队列上（`native_bridge.js`
的 `let queue = Promise.resolve()`）。所以主机重启后扩展虽然会在30秒心跳内重连，
仍可能出现"心跳正常、请求全挂"的假恢复；反过来，诱因标签已被关掉时，重启主机
又是最快且足够的恢复动作。两者不能靠猜，必须**做完再证明**。

本模块三步：
1. ``check``：只读取证并分类（idle/late_response_present/not_dispatched/
   in_flight/stuck_expired），给出有序处置建议。
2. ``auto``：仅当请求"已派发、已过期、有派发意图、无响应"且主机身份可绑定时，
   先落一份恢复意图（保留原未知结果），再停止等待、等扩展用新epoch重连，最后
   调用verify。**从不重放原请求**；停止等待不等于原操作未执行。
3. ``verify``：用一次**不执行页面JS**的原生标签校验证明队列真的在执行，而不是
   只有心跳；未恢复时如实返回busy/超时，并保留原active。
"""
import hashlib
import json
import os
import re
import signal
import sys
import time
from pathlib import Path

from windows_edge_bridge import atomic_private
from platform_runtime import is_private_path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT/'.local'/'edge-bridge'
SCHEMA = 'ustc-mac-bridge-recover-v1'
RECOVERY_SCHEMA = 'ustc-mac-bridge-recovery-v1'
ID_PATTERN = re.compile(r'[a-f0-9]{32}')
RECOVERY_DIR = 'recovery'

# 分类到有序处置建议。轻的在前：先看那个标签有没有弹框，再关标签，最后才重载扩展。
ADVICE = {
    'idle': ['通道无未决请求；可直接inventory核对当前命名空间后办理'],
    'late_response_present': [
        '迟到响应已落盘：主机读到同ID最终响应后会自行解除active，无需人工动作',
        '重新执行 bridge status 确认 active_request_id 已清空，再 inventory',
        '不要把迟到响应当成原业务已成功；按原回执独立回读',
    ],
    'not_dispatched': [
        '该请求没有派发意图，说明从未发给扩展；它已过期，不会被派发，也不会重放',
        '核对为何status仍指向它；必要时按原marker只读恢复，不重新派发',
    ],
    'in_flight': [
        '该请求尚未过期且无响应：仍在正常等候窗口内，不要同时派发其他请求',
        '等候期满再重新check；不要用新请求顶替',
    ],
    'stuck_expired': [
        '首选自动恢复：bash tools/reimbursement.sh bridge recover --phase auto；'
        '它先落恢复意图再停止等待，随后必须验收通过才允许办业务',
        '若auto验收为queue_still_blocked，说明扩展队列仍堵在同一个SW实例，'
        '此时在edge://extensions把本项目扩展开关一次或重新加载（唯一仍需人工的一步）',
        '若怀疑是该标签自己的模态框/渲染卡死，可先在无未决请求时用tab-close关掉该标签',
        '恢复后必须先用本模块 --phase verify 通过，才允许办理业务',
    ],
}


def _mac():
    if sys.platform != 'darwin':
        raise RuntimeError('Mac后台财务桥恢复仅支持Mac；Windows沿用它自己的主机合同')


def _private(path):
    """只接受本用户私有的状态文件；权限不符时不读取内容。"""
    try:
        return is_private_path(path)
    except OSError:
        return False


def _read(path):
    """返回 (value, sha256)；不存在或权限不符返回 (None, None)。"""
    path = Path(path)
    if not path.exists() or not _private(path):
        return None, None
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        return None, hashlib.sha256(raw).hexdigest()
    return value, hashlib.sha256(raw).hexdigest()


def _source_evidence(packet):
    """脚本只留摘要和长度，回执不复制页面执行源。"""
    source = ((packet or {}).get('arguments') or {}).get('source')
    if not isinstance(source, str):
        return {'source_sha256': None, 'source_characters': None}
    return {'source_sha256': hashlib.sha256(source.encode('utf-8')).hexdigest(),
            'source_characters': len(source)}


def _classify(active, request, intent, response, now):
    """按"是否派发/是否过期/是否已有响应"分类；分类只决定建议，不触发动作。"""
    if not isinstance(active, str) or not ID_PATTERN.fullmatch(active):
        return 'idle'
    if response is not None:
        return 'late_response_present'
    if intent is None:
        return 'not_dispatched'
    expires = (request or {}).get('expires_at')
    if isinstance(expires, (int, float)) and expires > now:
        return 'in_flight'
    return 'stuck_expired'


def diagnose(state=None, now=None):
    """只读汇总卡死现场。不派发请求、不改任何文件。"""
    directory = STATE if state is None else Path(state)
    now = time.time() if now is None else float(now)
    status, status_sha = _read(directory/'status.json')
    status = status if isinstance(status, dict) else {}
    active = status.get('active_request_id')
    evidence = {
        'schema': SCHEMA,
        'mode': 'check',
        'state_dir': str(directory),
        'observed_at': now,
        'host': {
            'connected': status.get('connected'),
            'epoch': status.get('epoch'),
            'host_pid': status.get('host_pid'),
            'active_request_id': active,
            'active_operation': status.get('active_operation'),
            'heartbeat_age_seconds': (round(now-status['heartbeat'], 1)
                                      if isinstance(status.get('heartbeat'), (int, float)) else None),
            'status_sha256': status_sha,
        },
        'request': None, 'intent': None, 'response': None,
        'classification': None, 'recommended_actions': [],
        'host_recycle_insufficient': True,
        'host_recycle_reason': ('扩展队列是Service Worker模块级变量；杀主机后30秒内会重连，'
                                '但队列仍堵在同一SW实例，表现为心跳正常而请求全挂'),
        'mutation_performed': False,
        'system_write': False,
        'page_script_executed': False,
        'browser_accessed': False,
    }
    if isinstance(active, str) and ID_PATTERN.fullmatch(active):
        request, request_sha = _read(directory/'requests'/(active+'.json'))
        intent, intent_sha = _read(directory/'intents'/(active+'.json'))
        response, response_sha = _read(directory/'responses'/(active+'.json'))
        request = request if isinstance(request, dict) else None
        intent = intent if isinstance(intent, dict) else None
        args = (request or {}).get('arguments') or {}
        evidence['request'] = None if request is None else {
            'path': str(directory/'requests'/(active+'.json')),
            'sha256': request_sha,
            'operation': request.get('operation'),
            'scope': args.get('scope'),
            'tab_id': args.get('tab_id'),
            'expected_window_id': args.get('expected_window_id'),
            'created_at': request.get('created_at'),
            'expires_at': request.get('expires_at'),
            'expired_by_seconds': (round(now-request['expires_at'], 1)
                                   if isinstance(request.get('expires_at'), (int, float)) else None),
            **_source_evidence(request),
        }
        evidence['intent'] = None if intent is None else {
            'path': str(directory/'intents'/(active+'.json')),
            'sha256': intent_sha,
            'dispatch_intent_at': intent.get('dispatch_intent_at'),
            'no_replay': intent.get('no_replay'),
        }
        evidence['response'] = None if response_sha is None else {
            'path': str(directory/'responses'/(active+'.json')),
            'sha256': response_sha,
        }
    # 分类用原字段判断过期，不用回执里裁剪后的摘要。
    packet = None
    if isinstance(active, str) and ID_PATTERN.fullmatch(active):
        packet, _ = _read(directory/'requests'/(active+'.json'))
        packet = packet if isinstance(packet, dict) else None
    classification = _classify(active, packet, evidence['intent'], evidence['response'], now)
    evidence['classification'] = classification
    evidence['recommended_actions'] = list(ADVICE.get(classification, []))
    evidence['ok'] = True
    return evidence


def check(output, state=None, now=None):
    """诊断并写出可审计回执；本地只读，不访问浏览器、不写财务。"""
    _mac()
    receipt = diagnose(state=state, now=now)
    receipt['receipt_written'] = True
    receipt['output'] = str(output)
    atomic_private(Path(output), receipt)
    return receipt


def _identity(directory, host_pid):
    """主机身份绑定：last-native-start.json 的 pid 必须就是当前 status 里的 host_pid。"""
    record, sha = _read(Path(directory)/'last-native-start.json')
    matched = (isinstance(record, dict) and record.get('pid') == host_pid
               and record.get('origin_valid') is True
               and record.get('phase') in {'entered', 'disconnected', 'failed'})
    return {'path': str(Path(directory)/'last-native-start.json'), 'sha256': sha,
            'pid': None if not isinstance(record, dict) else record.get('pid'),
            'matched': matched}


def _alive(pid, kill):
    try:
        kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def mark_stalled(directory, now=None, marked_by='sweep'):
    """把"过期且无响应"的派发意图标记为已停止等待。

    只写 responses 侧标记；requests/intents 原字节不动，晚到的真实答案由主机另存
    .late.json，不会覆盖本标记。未过期或已有答复的一律跳过。
    """
    reference = time.time() if now is None else float(now)
    marked = []
    for intent_path in sorted((Path(directory)/'intents').glob('*.json')):
        answer_path = Path(directory)/'responses'/intent_path.name
        if answer_path.exists():
            continue
        packet, _ = _read(Path(directory)/'requests'/intent_path.name)
        if not isinstance(packet, dict):
            continue
        expires = packet.get('expires_at')
        if not isinstance(expires, (int, float)) or expires > reference:
            continue
        marker = {'id': packet.get('id'), 'ok': False, 'error_code': 'request_stalled',
                  'epoch': packet.get('epoch'), 'stalled_at': reference,
                  'marked_by': 'bridge recover --phase ' + str(marked_by),
                  'note': '过期且无响应的派发意图；停止等待，原结果仍未知，未重发'}
        try:
            atomic_private(answer_path, marker, exclusive=True)
        except FileExistsError:
            continue
        marked.append({'request_id': packet.get('id'), 'response': str(answer_path)})
    return marked


def sweep(output, state=None, now=None):
    """只标记过期孤儿意图并落回执；不重启主机、不访问浏览器、不重发。"""
    _mac()
    directory = STATE if state is None else Path(state)
    receipt = {'schema': SCHEMA, 'mode': 'sweep', 'observed_at': time.time(),
               'ok': True, 'state': 'swept', 'system_write': False,
               'browser_accessed': False, 'page_script_executed': False,
               'replayed_original_request': False,
               'stalled_marked': mark_stalled(directory, now=now, marked_by='sweep'),
               'output': str(output)}
    atomic_private(Path(output), receipt)
    return receipt


def auto(output, timeout=45, stop_timeout=10, state=None, now=None,
         inventory=None, validate=None, tab_id=None, expected_window_id=None,
         reason=None, kill=None):
    """受控停止等待并重建连接，随后必须验收通过。

    只在"已派发、已过期、有派发意图、无响应、主机身份可绑定"时动作；先落恢复
    意图再停止等待，原请求永不被重放，其未知结果单独保留。
    """
    _mac()
    kill = kill or os.kill
    directory = STATE if state is None else Path(state)
    output = Path(output)
    receipt = {
        'schema': SCHEMA, 'mode': 'auto', 'observed_at': time.time(),
        'ok': False, 'state': None, 'system_write': False, 'browser_accessed': False,
        'page_script_executed': False, 'replayed_original_request': False,
        'business_allowed': False, 'host_stopped': False, 'reconnected_epoch': None,
        'before': None, 'host_identity': None, 'recovery_intent': None,
        'verify': None, 'next_action': None, 'error': None,
        'outcome_unknown_for_original_request': None,
    }

    def done(state, ok=False, **extra):
        receipt.update(state=state, ok=ok, **extra)
        receipt['output'] = str(output)
        atomic_private(output, receipt)
        return receipt

    diagnosis = diagnose(state=directory, now=now)
    receipt['before'] = {key: diagnosis[key] for key in
                         ('classification', 'host', 'request', 'intent', 'response')}
    classification = diagnosis['classification']
    if classification == 'idle':
        return done('nothing_to_recover', ok=True,
                    next_action='通道无未决请求；inventory核对账号与标签后即可办理')
    if classification != 'stuck_expired':
        return done('manual_review_required',
                    error='该分类不适用自动停止等待：' + str(classification),
                    next_action='按check回执的建议处置；不重启主机、不重放原请求')
    host, request, intent = diagnosis['host'], diagnosis['request'], diagnosis['intent']
    pid = host.get('host_pid')
    identity = _identity(directory, pid)
    receipt['host_identity'] = identity
    checks = {
        'request_expired': bool(request) and (request.get('expired_by_seconds') or 0) > 0,
        'no_response_yet': diagnosis['response'] is None,
        'dispatch_intent_with_no_replay': bool(intent) and intent.get('no_replay') is True,
        'host_connected': host.get('connected') is True,
        'host_pid_usable': isinstance(pid, int) and not isinstance(pid, bool)
                           and pid > 0 and pid != os.getpid(),
        'host_identity_bound': identity['matched'] is True,
    }
    failed = sorted(name for name, value in checks.items() if not value)
    if failed:
        return done('preconditions_failed',
                    error='自动恢复前置条件不满足：' + ','.join(failed),
                    next_action='保留现场并按check回执人工核对；不猜着重启主机')

    record = {
        'schema': RECOVERY_SCHEMA, 'request_id': host.get('active_request_id'),
        'epoch': host.get('epoch'), 'host_pid': pid,
        'operation': request.get('operation'), 'tab_id': request.get('tab_id'),
        'expected_window_id': request.get('expected_window_id'),
        'request_sha256': request.get('sha256'), 'intent_sha256': intent.get('sha256'),
        'source_sha256': request.get('source_sha256'),
        'reason': reason or '卡死请求已过期且无响应；受控停止等待并重建连接',
        'phase': 'stop_waiting_dispatch_intent', 'no_replay': True,
        'system_write': False, 'created_at': time.time(),
        'outcome_unknown_for_original_request': True,
        'note': '停止等待不等于原操作未执行；原结果仍未知，须按原job独立回读',
    }
    record_path = directory/RECOVERY_DIR/(str(record['request_id']) + '.json')
    try:
        atomic_private(record_path, record, exclusive=True)
    except FileExistsError:
        previous, _ = _read(record_path)
        stable = lambda value: {k: v for k, v in (value or {}).items() if k != 'created_at'}
        if stable(previous) != stable(record):
            return done('recovery_intent_conflict',
                        error='已有不同输入的恢复意图；不覆盖，先人工核对',
                        recovery_intent={'path': str(record_path)})
    receipt['recovery_intent'] = {'path': str(record_path),
                                  'sha256': hashlib.sha256(record_path.read_bytes()).hexdigest()}
    receipt['outcome_unknown_for_original_request'] = True

    try:
        kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except OSError as error:
        return done('stop_failed', error=(str(error) or type(error).__name__)[:300],
                    next_action='未发出停止信号；按check回执人工核对')
    deadline = time.monotonic() + max(0.0, float(stop_timeout))
    while time.monotonic() < deadline and _alive(pid, kill):
        time.sleep(0.1)
    receipt['host_stopped'] = not _alive(pid, kill)
    if not receipt['host_stopped']:
        return done('host_still_running',
                    error='停止信号已发出但主机仍存活；不升级为强制终止',
                    next_action='人工确认该进程，或在edge://extensions重载扩展')

    receipt['stalled_marked'] = mark_stalled(directory, now=now, marked_by='auto')

    import mac_edge_bridge as bridge
    kwargs = {'state': directory}
    old_epoch = host.get('epoch')
    new = None
    deadline = time.monotonic() + max(0.0, float(timeout))
    while time.monotonic() < deadline:
        try:
            current = bridge.status(**kwargs)
        except Exception:
            current = None
        if (isinstance(current, dict) and current.get('ready') is True
                and current.get('epoch') and current['epoch'] != old_epoch
                and not current.get('active_request_id')):
            new = current
            break
        time.sleep(0.25)
    if new is None:
        return done('reconnect_pending',
                    error='等不到带新epoch的重连；扩展可能已断开',
                    next_action='在edge://extensions重载本项目扩展，再跑 --phase verify')
    receipt['reconnected_epoch'] = new['epoch']

    verify_output = output.with_name(output.stem + '.verify.json')
    verified = verify(verify_output, tab_id=tab_id, expected_window_id=expected_window_id,
                      state=directory, inventory=inventory, validate=validate)
    receipt['verify'] = verified
    if verified.get('business_allowed') is True:
        return done('recovered', ok=True, business_allowed=True,
                    next_action='重新inventory核对账号与标签后可办理业务；一次验收不证明保存或打印已通')
    return done('queue_still_blocked', error=verified.get('error'),
                next_action='扩展队列仍堵在同一个SW实例：在edge://extensions重载本项目扩展，再跑 --phase verify')



def verify(output, tab_id=None, expected_window_id=None, timeout=8,
           state=None, inventory=None, validate=None):
    """恢复动作之后证明队列真的在执行。

    只用原生标签校验（不执行页面JS）；仍在等待原请求时如实返回 busy 且不派发。
    """
    _mac()
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 20:
        raise ValueError('验收timeout须为0至20秒之间的有限正数')
    receipt = {
        'schema': SCHEMA, 'mode': 'verify', 'observed_at': time.time(),
        'ok': False, 'state': None, 'queue_executes': False,
        'active_request_id_before': None, 'active_request_id_after': None,
        'epoch_before': None, 'epoch_after': None,
        'tab_id': None if tab_id is None else str(tab_id),
        'expected_window_id': None if expected_window_id is None else str(expected_window_id),
        'validated': False, 'page_script_executed': False, 'system_write': False,
        'business_allowed': False, 'next_action': None, 'error': None,
        'after_read': None, 'after_error': None,
    }
    if tab_id is not None and not re.fullmatch(r'[0-9]+', str(tab_id)):
        raise ValueError('tab_id须为数字原生标签ID')
    if expected_window_id is not None and not re.fullmatch(r'[0-9]+', str(expected_window_id)):
        raise ValueError('expected_window_id须为数字原生窗口ID')
    import mac_edge_bridge as bridge
    kwargs = {} if state is None else {'state': Path(state)}

    def finish():
        """每条路径都真实重读一次主机状态再落盘，不用空值冒充已恢复。"""
        try:
            after = bridge.status(**kwargs)
            receipt['active_request_id_after'] = after.get('active_request_id')
            receipt['epoch_after'] = after.get('epoch') or receipt.get('epoch_after')
            receipt['after_read'] = True
        except Exception as error:
            receipt['after_read'] = False
            receipt['after_error'] = (str(error) or type(error).__name__)[:200]
        receipt['output'] = str(output)
        atomic_private(Path(output), receipt)
        return receipt

    status = bridge.status(**kwargs)
    receipt['epoch_before'] = status.get('epoch')
    receipt['active_request_id_before'] = status.get('active_request_id')
    if status.get('active_request_id'):
        receipt.update(state='bridge_busy',
                       error='原请求仍在等待；不派发新请求，先按check处置',
                       next_action='bash tools/reimbursement.sh bridge recover --phase check --output /本机/新回执.json')
        return finish()
    inventory = inventory or bridge.inventory
    validate = validate or bridge.validate_tab
    try:
        snapshot = inventory()
        namespace = snapshot.get('tab_id_namespace')
        receipt['epoch_after'] = snapshot.get('epoch')
        if tab_id is not None:
            ids = {str(tab.get('tab_id')) for tab in snapshot.get('tabs') or []}
            if str(tab_id) not in ids:
                receipt.update(state='target_tab_missing',
                               error='目标标签不在本次inventory的财务标签内；不执行校验')
                return finish()
            if expected_window_id is None:
                receipt.update(state='window_id_required',
                               error='给出tab_id时必须同时给同次inventory的expected_window_id')
                return finish()
            validate(str(tab_id), tab_id_namespace=namespace,
                     expected_window_id=str(expected_window_id), timeout=timeout)
            receipt['validated'] = True
        receipt.update(ok=True, state='queue_executes', queue_executes=True,
                       business_allowed=True,
                       next_action='重新inventory核对账号与标签后可办理业务；一次成功不证明保存或打印已通')
    except TimeoutError as error:
        receipt.update(state='queue_still_blocked', error=str(error)[:400],
                       next_action='队列仍未执行：按check建议重载扩展，不要继续派发')
    except Exception as error:
        receipt.update(state='verify_failed', error=(str(error) or type(error).__name__)[:400],
                       next_action='核对status与inventory回执；未明确成功前不办业务')
    return finish()


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Mac后台财务桥卡死请求诊断、受控恢复与恢复后验收')
    parser.add_argument('--phase', choices=['check', 'auto', 'sweep', 'verify'], default='check')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--tab-id')
    parser.add_argument('--expected-window-id')
    parser.add_argument('--timeout', type=float, default=8)
    parser.add_argument('--reason', help='auto：写入恢复意图的原因说明')
    args = parser.parse_args()
    if args.phase == 'check':
        result = check(args.output)
    elif args.phase == 'sweep':
        result = sweep(args.output)
    elif args.phase == 'auto':
        result = auto(args.output, tab_id=args.tab_id,
                      expected_window_id=args.expected_window_id, reason=args.reason)
    else:
        result = verify(args.output, tab_id=args.tab_id,
                        expected_window_id=args.expected_window_id, timeout=args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result.get('ok') else 2


if __name__ == '__main__':
    raise SystemExit(main())
