"""本机工作区绑定、运行时位置与安装环境；不读取浏览器或案件。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from assistant_contract import APIError, CASE_PROFILES

INSTALLATION_SCHEMA = 'ustc-installation-v1'


def portable_workspace(root):
    return (Path(root) / 'data/distribution.json').is_file()


def runtime_python(root):
    return (
        Path(root) / '.local/mcp-venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    )


def read_installation(root):
    root = Path(root).resolve()
    path = root / '.local/installation.json'
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding='utf-8'))
    if (
        not isinstance(value, dict)
        or value.get('schema') != INSTALLATION_SCHEMA
        or value.get('tool_root') != str(root)
        or not isinstance(value.get('material_root'), str)
        or not Path(value['material_root']).is_absolute()
    ):
        raise APIError(
            'installation_binding', '安装记录来自其他工具位置或绑定不完整；先运行安装自检'
        )
    return value


def resolve_material_roots(root, overrides=None, *, for_setup=False):
    root = Path(root).resolve()
    portable = portable_workspace(root)
    saved = read_installation(root) if portable and not overrides else None
    if for_setup and portable and not overrides and not saved:
        raise APIError(
            'installation_material_root',
            '试用包首次安装须显式给material-root，不自动授权工具父目录',
        )
    default = saved['material_root'] if saved else root / 'materials' if portable else root.parent
    values = overrides or [default]
    return list(dict.fromkeys(Path(value).expanduser().resolve() for value in values))


def installation_config(root, data_dir=None, profile_id=None, title=None, mode=None):
    root = Path(root).resolve()
    old = read_installation(root)
    data = (
        Path(data_dir).expanduser().resolve()
        if data_dir
        else Path(old['material_root'])
        if old
        else root / 'materials'
    )
    if old and str(data) != old['material_root']:
        raise ValueError('已有安装的数据目录不同，不能在重装时悄悄更换授权范围')
    selected_mode = mode or (old or {}).get('mode', CASE_PROFILES[0])
    if selected_mode not in CASE_PROFILES:
        raise APIError('installation_mode', '安装记录的材料场景无效，须核对本机配置')
    return {
        'schema': INSTALLATION_SCHEMA,
        'tool_root': str(root),
        'material_root': str(data),
        'library_root': str(data / '规范库'),
        'profile_id': profile_id or (old or {}).get('profile_id', 'my_project'),
        'title': title or (old or {}).get('title', '我的报销项目'),
        'mode': selected_mode,
    }


def private_environment(root, *, create_temp=True):
    """安装与自检只继承系统运行项；财务任务的显式浏览器绑定由其原owner管理。"""
    root = Path(root)
    keys = ('PATH', 'SystemRoot', 'WINDIR', 'COMSPEC', 'TEMP', 'TMP', 'TMPDIR', 'LANG', 'LC_ALL')
    if os.name == 'nt':
        # Windows系统组件需要当前接收者的系统目录变量；仍不继承Python、pip或客户端配置。
        keys += ('SystemDrive', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'PROGRAMDATA')
    result = {key: os.environ[key] for key in keys if key in os.environ}
    temporary = root / '.local/tmp'
    if create_temp:
        temporary.mkdir(parents=True, exist_ok=True)
    result.update(
        PYTHONUTF8='1',
        PYTHONIOENCODING='utf-8',
        PYTHONNOUSERSITE='1',
        PIP_CONFIG_FILE=os.devnull,
        PIP_DISABLE_PIP_VERSION_CHECK='1',
        PIP_NO_INPUT='1',
        PIP_CACHE_DIR=str(root / '.local/cache/pip'),
        TMPDIR=str(temporary),
        TMP=str(temporary),
        TEMP=str(temporary),
    )
    return result
