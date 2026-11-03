"""独立凭据服务适配：业务仅传引用与固定消费命令，不持有密码库或账号秘密。"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import sys


def configuration(path):
    raw=Path(path).read_bytes();value=json.loads(raw.decode('utf-8-sig'))
    allowed={'username','credential_reference','credential_backend','secret_values_in_project','credential_cli','credential_config','credential_command'}
    if (not isinstance(value,dict)or set(value)-allowed or value.get('credential_backend')not in {'rtime-credentials','json-command'}or
        value.get('secret_values_in_project')is not False or not re.fullmatch(r'[A-Za-z0-9_@.+-]{2,128}',str(value.get('username','')))or
        not isinstance(value.get('credential_reference'),str)or not value['credential_reference'].strip()or
        value['credential_reference'].startswith('YOUR_')or value['username']=='ACCOUNT_ID'):
        raise ValueError('invalid_credential_configuration')
    if value['credential_backend']=='json-command':
        command=value.get('credential_command')
        if (not isinstance(command,list)or not command or not all(isinstance(v,str)and v and not any(ord(c)<32 for c in v)for v in command)or
            not Path(command[0]).is_absolute()or value.get('credential_cli')):raise ValueError('invalid_credential_command')
    elif 'credential_command'in value:raise ValueError('invalid_credential_command')
    for key in ('credential_cli','credential_config'):
        if key in value and (not isinstance(value[key],str)or not Path(value[key]).is_absolute()or any(ord(c)<32 for c in value[key])):
            raise ValueError('invalid_credential_provider_path')
    if value.get('credential_config'):
        config=Path(value['credential_config'])
        if not config.is_file():raise ValueError('credential_config_unavailable')
        if os.name!='nt'and(config.stat().st_uid!=os.getuid()or config.stat().st_mode&0o077):raise ValueError('credential_config_not_private')
        raw+=b'\0'+hashlib.sha256(config.read_bytes()).digest()
    return value,hashlib.sha256(raw).hexdigest()


def invocation(conf):
    """部署者配置命令；本案API不能指定URL、密码或临时替换凭据后端。"""
    if conf.get('credential_backend','rtime-credentials')=='json-command':
        command=list(conf['credential_command'])
    else:
        cli=Path(conf.get('credential_cli',Path.home()/'.ai-skills/rtime-credentials/scripts/cli.py'))
        if not cli.is_file():raise ValueError('credential_tool_unavailable')
        command=[sys.executable,str(cli)]
    env={k:v for k,v in os.environ.items()if k not in {'FINANCE_CAS_USERNAME','FINANCE_CAS_PASSWORD'}}
    env.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8')
    if conf.get('credential_config'):
        config=Path(conf['credential_config'])
        if not config.is_file():raise ValueError('credential_config_unavailable')
        if os.name!='nt'and(config.stat().st_uid!=os.getuid()or config.stat().st_mode&0o077):raise ValueError('credential_config_not_private')
        env.update(RTIME_CREDENTIAL_CONFIG=str(config),CREDENTIAL_PROVIDER_CONFIG=str(config))
    return [*command,'use'],env
