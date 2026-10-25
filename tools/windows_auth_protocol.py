"""固定CAS登录协议；可持久化的部分不含密码或含密码脚本。"""
import hashlib
import re
import math
import time
import queue
import threading

STATES={'login_submitted','challenge_required','login_error','wrong_login_page','wrong_login_service',
        'identity_mismatch','login_controls_not_ready','credential_required','native_error'}


def validate(packet):
    keys={'schema','case_id','request_id','tab_id','expected_username','username','password','authorized','expires_at'}
    optional={'expected_epoch','expected_window_id'}
    if not isinstance(packet,dict) or not keys<=set(packet) or set(packet)-keys-optional or packet.get('schema')!='ustc-cas-memory-login-v1' or packet.get('authorized') is not True:
        raise ValueError('invalid_auth_request')
    patterns={'case_id':r'[a-z][a-z0-9_-]{1,63}','request_id':r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}',
              'tab_id':r'[0-9]+','expected_username':r'[A-Za-z0-9_@.+-]{2,128}'}
    if any(not isinstance(packet.get(k),str) or not re.fullmatch(pattern,packet[k]) for k,pattern in patterns.items()):
        raise ValueError('invalid_auth_request')
    if packet.get('username')!=packet['expected_username']:
        raise ValueError('identity_mismatch')
    if optional & set(packet) and (not optional<=set(packet) or
        not isinstance(packet['expected_epoch'],str) or not re.fullmatch(r'[a-f0-9]{32}',packet['expected_epoch']) or
        not isinstance(packet['expected_window_id'],str) or not re.fullmatch(r'[0-9]+',packet['expected_window_id'])):
        raise ValueError('invalid_auth_request')
    if not isinstance(packet.get('password'),str) or not 0<len(packet['password'])<=8192 or '\0' in packet['password']:
        raise ValueError('invalid_auth_request')
    expiry=packet.get('expires_at')
    if isinstance(expiry,bool) or not isinstance(expiry,(int,float)) or not math.isfinite(expiry) or not 0<expiry<=time.time()+25:
        raise ValueError('invalid_auth_request')
    return dict(packet)


def intent(packet):
    key='\0'.join(packet[k] for k in ['case_id','request_id','tab_id'])
    return {'schema':'ustc-cas-memory-intent-v1','id':hashlib.sha256(key.encode()).hexdigest()[:32],
            'case_id':packet['case_id'],'request_id':packet['request_id'],'tab_id':packet['tab_id'],
            'expected_username_sha256':hashlib.sha256(packet['expected_username'].encode()).hexdigest(),
            'no_replay':True,'system_write':False,
            **{k:packet[k] for k in ['expected_epoch','expected_window_id'] if k in packet}}


def sanitize(reply):
    body=reply.get('result') if isinstance(reply,dict) and reply.get('ok') is True else None
    if not isinstance(body,dict) or body.get('state') not in STATES:
        return {'ok':False,'state':'auth_result_unknown','login_submitted':None,'system_write':False}
    state=body['state']
    submitted=state=='login_submitted'
    if body.get('login_submitted') is not submitted:
        return {'ok':False,'state':'auth_result_unknown','login_submitted':None,'system_write':False}
    return {'ok':submitted,'state':state,'login_submitted':submitted,'system_write':False,
            'credential_values_returned':False,'login_verified':False}


class MemoryRequest:
    """冻结客户端截止时间；超时取消尚未派发的秘密，不能重新续期。"""
    def __init__(self,packet):
        self.metadata=intent(packet)
        self.expires_at=packet['expires_at']
        self.deadline=time.monotonic()+max(0,min(20,self.expires_at-time.time()))
        self.packet=dict(packet);self.answer=queue.Queue(maxsize=1)
        self.lock=threading.Lock();self.cancelled=False;self.dispatched=False

    def cancel(self):
        with self.lock:
            self.cancelled=True
            if not self.dispatched:self.packet.clear()

    def take_for_dispatch(self):
        with self.lock:
            if self.cancelled or time.monotonic()>=self.deadline or time.time()>=self.expires_at:
                self.packet.clear();self.cancelled=True
                return None
            args={key:self.packet[key] for key in ['tab_id','username','password','expected_username']}
            args.update({k:self.packet[k] for k in ['expected_epoch','expected_window_id'] if k in self.packet})
            self.dispatched=True;self.packet.clear()
            return args
