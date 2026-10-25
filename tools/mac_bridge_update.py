"""Update only this registered extension through its existing fixed reload channel."""
import hashlib
import json
import os
from pathlib import Path
import re
import queue
import time
import uuid

from build_extension import RUNTIME_FILES
from windows_edge_bridge import atomic_private
from platform_runtime import is_private_path,process_alive
import mac_edge_bridge as bridge

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'.local/edge-bridge'


def digest(raw):return hashlib.sha256(raw).hexdigest()


def materialize(raws,build):
    result=dict(raws)
    code=result['native_bridge.js'].decode('utf-8')
    if code.count('const BUILD_SHA = null;')!=1:raise ValueError('extension_build_placeholder_invalid')
    result['native_bridge.js']=code.replace('const BUILD_SHA = null;','const BUILD_SHA = '+json.dumps(build)+';').encode()
    return result


def prepare(installed_review=None):
    current=bridge.status()
    disconnected=(current.get('ready') is not True and process_alive(current.get('host_pid')) is False)
    if (current.get('ready') is not True and not disconnected) or current.get('synthetic_profile') or current.get('active_request_id'):
        raise ValueError('bridge_not_idle_and_ready')
    if not re.fullmatch('[a-f0-9]{32}',current.get('epoch','')):raise ValueError('bridge_identity_unavailable')
    installation=STATE/'installation.json'
    if installation.is_symlink() or not is_private_path(installation):raise ValueError('installation_not_private')
    record=json.loads(installation.read_text());folder=Path(record['extension_directory'])
    accepted={}
    if installed_review:
        review=json.loads(Path(installed_review).read_text())
        if (review.get('schema')!='ustc-installed-source-review-v1' or review.get('installation_sha256')!=digest(installation.read_bytes())
            or review.get('reviewed') is not True or not review.get('reason')):
            raise ValueError('invalid_installed_source_review')
        accepted=review.get('normalized_sha256',{})
    if not folder.is_relative_to(STATE/'package') or folder.is_symlink() or not folder.is_dir():raise ValueError('registered_package_path_invalid')
    old={}
    for name in RUNTIME_FILES:
        p=folder/name
        # Packaged source is not secret; prior installers may use 0644. Require
        # current-user ownership, no group/world write, and the registered SHA.
        if p.is_symlink() or not p.is_file() or p.stat().st_uid!=os.getuid() or p.stat().st_mode & 0o022:
            raise ValueError('registered_package_file_invalid')
        raw=p.read_bytes();normalized=raw
        if name=='native_bridge.js':
            needle='const BUILD_SHA = '+json.dumps(record['build_sha256'])+';'
            code=raw.decode();
            if code.count(needle)!=1:raise ValueError('installed_build_binding_invalid')
            normalized=code.replace(needle,'const BUILD_SHA = null;').encode()
        if digest(normalized)!=record['source_sha256'].get(name) and digest(normalized)!=accepted.get(name):
            raise ValueError('installed_package_drift')
        old[name]=raw
    # Existing installed code must already implement this exact finite maintenance action.
    installed=old['native_bridge.js'].decode()
    if "message.operation === 'reload'" not in installed or 'chrome.runtime.reload()' not in installed:
        raise ValueError('bootstrap_manual_load_required')
    historical=[]
    for intent in (STATE/'intents').glob('*.json'):
        if not(STATE/'responses'/intent.name).is_file():
            original=json.loads(intent.read_text());request=STATE/'requests'/intent.name
            pending=json.loads(request.read_text()) if request.is_file() else {}
            prior_epoch=original.get('epoch')
            if (not isinstance(prior_epoch,str) or not re.fullmatch('[a-f0-9]{32}',prior_epoch)
                or prior_epoch==current.get('epoch') or pending.get('epoch')!=prior_epoch
                or pending.get('operation') not in {'page-read','inventory','validate-tab','ping','tab-origin'}
                or not isinstance(pending.get('expires_at'),(int,float)) or pending['expires_at']>=time.time()):
                raise ValueError('unknown_native_intent_retained')
            historical.append({'id':intent.stem,'epoch':prior_epoch,'operation':pending['operation'],
                               'outcome':'unknown_preserved','intent_sha256':digest(intent.read_bytes())})
    for request in (STATE/'requests').glob('*.json'):
        if not(STATE/'responses'/request.name).exists() and json.loads(request.read_text()).get('expires_at',0)>time.time():
            raise ValueError('pending_native_request')
    raws={n:(ROOT/'extension'/n).read_bytes()for n in RUNTIME_FILES}
    old_manifest=json.loads(old['manifest.json']);new_manifest=json.loads(raws['manifest.json'])
    # Updating code must not silently expand permissions, identity, origins or launchers.
    for field in ['key','permissions','host_permissions','optional_permissions','optional_host_permissions','externally_connectable']:
        if old_manifest.get(field)!=new_manifest.get(field):raise ValueError('extension_permissions_or_identity_changed')
    hashes={n:digest(raw)for n,raw in raws.items()};build=digest(json.dumps(hashes,sort_keys=True).encode())
    new=materialize(raws,build)
    updated={**record,'build_sha256':build,'source_sha256':hashes,'host_source_sha256':digest((ROOT/'tools/edge_native_host.py').read_bytes()),
             'previous_build_sha256':record['build_sha256'],'update_method':'registered_extension_fixed_reload'}
    updated['retained_historical_read_intents']=historical
    return current,record,folder,old,new,updated


def check(installed_review=None):
    current,old,folder,before,after,new=prepare(installed_review)
    return {'ok':True,'action':'update_check','current_build':old['build_sha256'],'target_build':new['build_sha256'],
            'files_changed':[n for n in before if before[n]!=after[n]],'registered_directory':str(folder),
            'host_changed':old.get('host_source_sha256')!=new['host_source_sha256'],'automatic_reload_supported':True,
            'connection_ready':current.get('ready') is True,'reconnect_needed':current.get('ready') is not True,
            'browser_settings_page_accessed':False,'system_write':False,'update_performed':False}


def _write_bytes(path,raw):
    tmp=path.with_name(path.name+'.update-'+uuid.uuid4().hex)
    try:
        with tmp.open('xb')as stream:tmp.chmod(0o600);stream.write(raw)
        tmp.replace(path)
    finally:tmp.unlink(missing_ok=True)


def recover_stale_handshake(hello,installed,messages,output_stream,state):
    """离线更新后的旧worker仅获一次固定重载，不接通业务或凭据通道。"""
    from edge_native_host import write_frame
    state=Path(state)
    try:
        receipt_path=Path(installed.get('last_update_receipt',''))
        if (state.resolve()!=STATE.resolve() or not receipt_path.is_absolute() or receipt_path.is_symlink()
            or not receipt_path.resolve().is_relative_to((ROOT/'.local').resolve()) or not is_private_path(receipt_path)):
            return False
        receipt=json.loads(receipt_path.read_text())
        if (receipt.get('schema')!='ustc-mac-bridge-update-v1'
            or receipt.get('phase') not in {'installed_awaiting_reconnect','await_reconnect_verification'}
            or receipt.get('reload_requested') is not False or receipt.get('update_installed') is not True
            or hello.get('synthetic_profile') is True or hello.get('extension_id')!=installed.get('extension_id')
            or hello.get('build_sha256')!=receipt.get('previous_build')
            or installed.get('build_sha256')!=receipt.get('target_build')
            or set(receipt.get('expected_file_sha256',{}))!=set(RUNTIME_FILES)):
            return False
        folder=Path(receipt['registered_directory'])
        if (str(folder)!=installed.get('extension_directory') or not folder.is_relative_to(state/'package')
            or folder.is_symlink() or any((folder/n).is_symlink() or digest((folder/n).read_bytes())!=value for n,value in receipt['expected_file_sha256'].items())):
            return False
        request_id=uuid.uuid4().hex
        receipt.update(reload_requested=True,phase='handshake_reload_intent',handshake_reload_request_id=request_id,
                       observed_previous_build=hello['build_sha256'],business_channel_opened=False,credential_channel_opened=False)
        atomic_private(receipt_path,receipt)
    except (OSError,ValueError,KeyError,TypeError):
        return False
    # 先冻结意图再发一次。超时只保存unknown，下一握手不能重复派发。
    try:
        write_frame(output_stream,{'schema':'ustc-native-edge-v1','id':request_id,'operation':'reload','arguments':{},
                                   'expires_at':time.time()+8})
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            message=messages.get(timeout=max(.01,deadline-time.monotonic()))
            if message is None:break
            if isinstance(message,dict) and message.get('id')==request_id:
                accepted=message.get('ok') is True and isinstance(message.get('result'),dict) and message['result'].get('reloading') is True
                receipt.update(phase='handshake_reload_acknowledged' if accepted else 'handshake_reload_unknown',reload_acknowledged=accepted)
                atomic_private(receipt_path,receipt);return True
    except (OSError,ValueError,queue.Empty):
        pass
    receipt.update(phase='handshake_reload_unknown',reload_acknowledged=False,no_reload_replay=True)
    atomic_private(receipt_path,receipt);return True


def verify(output,timeout=35):
    output=Path(output);receipt=json.loads(output.read_text());deadline=time.monotonic()+timeout
    if receipt.get('schema')!='ustc-mac-bridge-update-v1' or not re.fullmatch('[a-f0-9]{32}',receipt.get('update_id','')):
        raise ValueError('invalid_update_receipt')
    while True:
        current=bridge.status()
        if (current.get('ready') is True and current.get('build_sha256')==receipt['target_build']
            and current.get('epoch')!=receipt['previous_epoch']):
            folder=Path(receipt['registered_directory'])
            if not folder.is_relative_to(STATE/'package') or any(digest((folder/n).read_bytes())!=value for n,value in receipt['expected_file_sha256'].items()):
                raise ValueError('updated_package_readback_mismatch')
            ping=bridge.ping()
            receipt.update(ok=True,phase='verified',new_epoch=current['epoch'],new_host_pid=current['host_pid'],
                           secure_password_transport_available=current.get('secure_password_transport_available') is True,
                           ping_verified=ping.get('pong') is True,updated_at=time.time())
            atomic_private(output,receipt);return receipt
        if time.monotonic()>=deadline:
            receipt.update(ok=False,phase='await_reconnect_verification',no_reload_replay=True)
            atomic_private(output,receipt);return receipt
        time.sleep(.25)


def apply(output,installed_review=None):
    from edge_runtime import browser_lock
    output=Path(output)
    if output.exists():raise ValueError('update_receipt_exists_use_verify')
    with browser_lock():
        current,old,folder,before,after,new=prepare(installed_review)
        update_id=uuid.uuid4().hex;backup=STATE/'update-backups'/update_id;backup.mkdir(parents=True);backup.chmod(0o700)
        for name,raw in before.items():_write_bytes(backup/name,raw)
        atomic_private(backup/'installation.json',old)
        receipt={'schema':'ustc-mac-bridge-update-v1','ok':False,'phase':'prepared','update_id':update_id,
                 'previous_build':old['build_sha256'],'target_build':new['build_sha256'],'previous_epoch':current['epoch'],
                 'registered_directory':str(folder),'backup_directory':str(backup),'backup_sha256':{n:digest(raw)for n,raw in before.items()},
                 'expected_file_sha256':{n:digest(raw)for n,raw in after.items()},'system_write':False,'browser_settings_page_accessed':False,
                 'reload_requested':False,'created_at':time.time()}
        receipt['retained_historical_read_intents']=new['retained_historical_read_intents']
        if installed_review:receipt['installed_source_review']={'path':str(Path(installed_review).resolve()),'sha256':digest(Path(installed_review).read_bytes())}
        atomic_private(output,receipt,exclusive=True)
        try:
            for name,raw in after.items():_write_bytes(folder/name,raw)
            for name,raw in after.items():
                if (folder/name).read_bytes()!=raw:raise ValueError('published_file_readback_mismatch')
            new['last_update_receipt']=str(output.resolve());atomic_private(STATE/'installation.json',new)
            receipt['phase']='files_published';atomic_private(output,receipt)
        except Exception:
            for name,raw in before.items():_write_bytes(folder/name,raw)
            atomic_private(STATE/'installation.json',old)
            receipt.update(phase='rolled_back_before_reload',rollback_verified=all((folder/n).read_bytes()==v for n,v in before.items()))
            atomic_private(output,receipt);raise
        if current.get('ready') is not True:
            # A stopped host cannot accept reload. Publish the registered package,
            # preserve session state, and verify when the extension reconnects.
            receipt.update(ok=True,phase='installed_awaiting_reconnect',update_installed=True,
                           live_version_verified=False,reload_requested=False)
            atomic_private(output,receipt);return receipt
        # The old connected host intentionally receives only its existing fixed reload
        # operation. No URL, page script, credential or browser settings control is used.
        request_id=uuid.uuid4().hex
        packet={'schema':'ustc-native-edge-v1','id':request_id,'epoch':current['epoch'],'operation':'reload','arguments':{},
                'created_at':time.time(),'expires_at':time.time()+10}
        receipt.update(phase='reload_intent',reload_requested=True,reload_request_id=request_id)
        atomic_private(output,receipt)
        atomic_private(STATE/'requests'/(request_id+'.json'),packet,exclusive=True)
        return verify(output)
