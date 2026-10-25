"""本案授权后的密码库消费者；密码只在子进程环境、受保护管道和浏览器内存。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from windows_edge_bridge import ROOT,atomic_private,status
from windows_auth_protocol import validate


def mac_mode():
    from finance_backend import mac_native_enabled
    return mac_native_enabled()


def bridge_status():
    if mac_mode():
        from finance_backend import mac_target
        from mac_edge_bridge import _target
        # Bind the existing explicit namespace/window; do not discover a fallback.
        binding=mac_target()
        current,_=_target('0',**binding)
        return current
    return status()


def transport_ready(bridge):
    return bool(bridge.get('ready') and not bridge.get('synthetic_profile') and
                (bridge.get('auth_socket_path') if mac_mode() else bridge.get('auth_pipe_name')) and
                bridge.get('auth_transport_alive') and bridge.get('auth_pipe_acl_verified'))


def memory_call(bridge,packet):
    if mac_mode():
        from mac_auth_socket import call
        return call(bridge['auth_socket_path'],bridge['host_pid'],packet)
    from windows_auth_pipe import call
    return call(bridge['auth_pipe_name'],bridge['host_pid'],packet)


def configuration():
    path=ROOT/'data/finance_access.json'
    if not path.is_file(): raise ValueError('credential_reference_required')
    raw=path.read_bytes();value=json.loads(raw.decode('utf-8-sig'))
    if (not isinstance(value,dict) or set(value)-{'username','credential_reference','credential_backend','secret_values_in_project'} or
        value.get('credential_backend')!='rtime-credentials' or value.get('secret_values_in_project') is not False or
        not isinstance(value.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_@.+-]{2,128}',value['username']) or
        not isinstance(value.get('credential_reference'),str) or not value['credential_reference'].strip() or
        value['credential_reference'].startswith('YOUR_') or value['username']=='ACCOUNT_ID'):
        raise ValueError('invalid_credential_configuration')
    return value,hashlib.sha256(raw).hexdigest()


def context(case_id,request_id,tab_id):
    if (not isinstance(case_id,str) or not re.fullmatch(r'[a-z][a-z0-9_-]{1,63}',case_id) or
        not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}',request_id) or
        not isinstance(tab_id,str) or not re.fullmatch(r'[0-9]+',tab_id)):
        raise ValueError('invalid_auth_context')
    value={'schema':'ustc-windows-credential-use-v1','case_id':case_id,'request_id':request_id,'tab_id':tab_id,
           'authorized':True,'system_write':False,'credential_values_persisted':False,'login_verified':False}
    if mac_mode():
        from finance_backend import transport_identity
        value.update(schema='ustc-mac-credential-use-v1',browser_transport=transport_identity())
    return value


def use_credentials(case_id,request_id,tab_id,output):
    """仅在调用者已确认会话缺失时调用；同一取用意图不再次调用密码库。"""
    from finance_access import snapshot
    from edge_runtime import browser_lock
    base=context(case_id,request_id,tab_id)
    output=Path(output)
    with browser_lock():
        observed=snapshot(tab_id)
    if observed['state'] in ['ready','finance_home']:
        return {**base,'state':'session_reused','login_submitted':False,'credential_accessed':False}
    if observed['state']!='login_required':
        return {**base,'state':'owner_interaction_required','login_submitted':False,'credential_accessed':False}
    if output.exists():
        prior=json.loads(output.read_text(encoding='utf-8'))
        if any(prior.get(key)!=base.get(key) for key in ['case_id','request_id','tab_id','browser_transport']): raise ValueError('auth_intent_mismatch')
        return {**prior,'reused_existing_auth_intent':True}
    bridge=bridge_status()
    if not transport_ready(bridge):
        return {**base,'state':'secure_auth_transport_unavailable','login_submitted':False,'credential_accessed':False}
    if bridge.get('active_request_id'):
        return {**base,'state':'bridge_busy','login_submitted':False,'credential_accessed':False}
    conf,digest=configuration()
    base.update(phase='credential_use_intent',config_sha256=digest,login_submitted=None)
    atomic_private(output,base,exclusive=True)
    credential_cli=Path.home()/'.ai-skills/rtime-credentials/scripts/cli.py'
    if not credential_cli.is_file():
        result={**base,'phase':'not_dispatched','state':'credential_tool_unavailable','login_submitted':False,'credential_accessed':False}
        atomic_private(output,result);return result
    consumer=[sys.executable,str(Path(__file__).resolve()),'consume','--case-id',case_id,'--request-id',request_id,
              '--tab-id',tab_id,'--output',str(output.resolve())]
    request={'bindings':{name:{'reference':conf['credential_reference'],'field':field} for name,field in
                         [('FINANCE_CAS_USERNAME','username'),('FINANCE_CAS_PASSWORD','password')]},
             'argv':consumer,'timeout':35}
    environment={k:v for k,v in os.environ.items() if k not in ['FINANCE_CAS_USERNAME','FINANCE_CAS_PASSWORD']}
    environment.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8')
    try:
        # 输入只有凭据引用与固定argv；输出原文不进入日志或回执。
        completed=subprocess.run([sys.executable,str(credential_cli),'use'],input=json.dumps(request),
                                 text=True,encoding='utf-8',capture_output=True,env=environment,timeout=45)
        wrapped=json.loads(completed.stdout)
        tool_ok=completed.returncode==0 and isinstance(wrapped,dict) and wrapped.get('ok') is True
    except (OSError,ValueError,subprocess.SubprocessError):
        tool_ok=False
    result=json.loads(output.read_text(encoding='utf-8'))
    if result.get('phase')=='credential_use_intent':
        result.update(phase='credential_use_unconfirmed',state='credential_consumer_unavailable' if tool_ok else 'credential_use_failed',login_submitted=None)
        atomic_private(output,result)
    return result


def consume(case_id,request_id,tab_id,output):
    """由credential_use启动；所有异常只输出固定状态，不打印异常文本。"""
    base=context(case_id,request_id,tab_id);output=Path(output)
    prior=json.loads(output.read_text(encoding='utf-8'))
    if prior.get('phase')!='credential_use_intent' or any(prior.get(k)!=base.get(k) for k in ['case_id','request_id','tab_id','authorized','browser_transport']):
        raise ValueError('auth_intent_mismatch')
    conf,digest=configuration()
    if digest!=prior.get('config_sha256'): raise ValueError('credential_configuration_changed')
    claim=output.with_suffix(output.suffix+'.claim')
    atomic_private(claim,{'case_id':case_id,'request_id':request_id,'tab_id':tab_id,'consumer_pid':os.getpid()},exclusive=True)
    username=os.environ.pop('FINANCE_CAS_USERNAME','')
    password=os.environ.pop('FINANCE_CAS_PASSWORD','')
    packet={'schema':'ustc-cas-memory-login-v1','case_id':case_id,'request_id':request_id,'tab_id':tab_id,
            'expected_username':conf['username'],'username':username,'password':password,'authorized':True,'expires_at':time.time()+15}
    result={**prior,'phase':'consumer_started','credential_accessed':True}
    atomic_private(output,result)
    try:
        packet=validate(packet)
        from edge_runtime import browser_lock
        from finance_access import snapshot
        with browser_lock():
            observed=snapshot(tab_id)
            if observed['state'] in ['ready','finance_home']:
                value={'ok':True,'state':'session_reused','login_submitted':False,'system_write':False}
            elif observed['state']!='login_required':
                value={'ok':False,'state':'owner_interaction_required','login_submitted':False,'system_write':False}
            else:
                bridge=bridge_status()
                if not transport_ready(bridge) or bridge.get('active_request_id'):
                    raise ValueError('secure_auth_transport_unavailable')
                if mac_mode():
                    from finance_backend import mac_target
                    packet.update(expected_epoch=bridge['epoch'],expected_window_id=mac_target()['expected_window_id'])
                    packet=validate(packet)
                result['phase']='memory_dispatch_intent';atomic_private(output,result)
                packet['expires_at']=time.time()+15
                value=memory_call(bridge,packet)
        # Native主机已白名单化结果；消费者再次只接受固定标量字段。
        allowed={'login_submitted','challenge_required','login_error','wrong_login_page','wrong_login_service','identity_mismatch',
                 'login_controls_not_ready','credential_required','native_error','auth_result_unknown','auth_original_intent_retained',
                 'auth_intent_mismatch','auth_transport_failed','invalid_auth_request','session_reused','owner_interaction_required','bridge_busy','auth_request_expired'}
        state=value.get('state') if isinstance(value,dict) else None
        if state not in allowed: raise ValueError('invalid_auth_response')
        result.update(phase='consumer_completed',state=state,login_submitted=value.get('login_submitted') if type(value.get('login_submitted')) in (bool,type(None)) else None)
    except Exception:
        result.update(phase='consumer_stopped',state='auth_result_unknown' if result['phase']=='memory_dispatch_intent' else 'auth_preflight_failed',
                      login_submitted=None if result['phase']=='memory_dispatch_intent' else False)
    finally:
        packet.clear();username=password=''
    atomic_private(output,result)
    return result


def verify_reference(case_id):
    if not isinstance(case_id,str) or not re.fullmatch(r'[a-z][a-z0-9_-]{1,63}',case_id):
        raise ValueError('invalid_auth_context')
    conf,_=configuration()
    username=os.environ.pop('FINANCE_CAS_USERNAME','');password=os.environ.pop('FINANCE_CAS_PASSWORD','')
    try:
        return {'ok':bool(username and password and username==conf['username']), 'case_id':case_id,
                'username_present':bool(username),'password_present':bool(password),
                'matches_configured_username':username==conf['username'], 'system_write':False,
                'login_submitted':False,'credential_values_returned':False}
    finally: username=password=''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['consume','verify-reference'])
    parser.add_argument('--case-id',required=True);parser.add_argument('--request-id')
    parser.add_argument('--tab-id');parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    try:
        if args.action=='verify-reference':
            if args.request_id or args.tab_id or args.output:raise ValueError('invalid_auth_context')
            result=verify_reference(args.case_id)
        else:
            if os.name!='nt' and not mac_mode():raise ValueError('native_backend_required')
            result=consume(args.case_id,args.request_id,args.tab_id,args.output)
    except Exception: result={'ok':False,'state':'auth_consumer_stopped','login_submitted':None,'system_write':False}
    print(json.dumps(result,ensure_ascii=True))
    return 0 if result.get('ok') is True or result.get('state') in ['login_submitted','session_reused'] else 2


if __name__=='__main__': raise SystemExit(main())
