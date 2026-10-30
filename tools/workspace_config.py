"""本机工作区绑定、运行时位置与安装环境；不读取浏览器或案件。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from assistant_contract import APIError, CASE_PROFILES

INSTALLATION_SCHEMA = 'ustc-installation-v1'
DEPLOYMENT_SCHEMA = 'ustc-deployment-v1'


def deployment_options(path=None, **overrides):
    """部署声明仅接收已有安装参数，不接收命令、环境或凭据。"""
    value = {}
    keys = {'data_dir', 'profile_id', 'title', 'mode', 'with_ocr'}
    if path is not None:
        value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        if not isinstance(value, dict) or value.get('schema') != DEPLOYMENT_SCHEMA:
            raise ValueError('部署配置须使用ustc-deployment-v1对象')
        if set(value) - keys - {'schema'}:
            raise ValueError('部署配置包含不支持的字段；仅允许资料目录、专项、场景及OCR开关')
        value = {k: v for k, v in value.items() if k != 'schema'}
    for key, item in overrides.items():
        if key not in keys:
            raise ValueError('不支持的安装参数')
        if item is None:
            continue
        previous = value.get(key)
        if key == 'data_dir':
            item = str(Path(item).expanduser().resolve())
        if key in value and previous != item:
            raise ValueError('命令行与部署配置冲突：' + key)
        value[key] = item
    if 'data_dir' in value and (
        not isinstance(value['data_dir'], str) or not Path(value['data_dir']).is_absolute()
    ):
        raise ValueError('部署配置data_dir须为本机绝对路径')
    if 'with_ocr' in value and type(value['with_ocr']) is not bool:
        raise ValueError('部署配置with_ocr须为布尔值')
    if any(key in value and not isinstance(value[key], str) for key in ('profile_id', 'title', 'mode')):
        raise ValueError('部署配置专项与材料场景须为字符串')
    return value


def validate_installation_fields(value):
    if (
        not isinstance(value.get('profile_id'), str)
        or not re.fullmatch(r'[a-z][a-z0-9_-]{1,63}', value['profile_id'])
        or value['profile_id'] == 'common'
        or not isinstance(value.get('title'), str)
        or not value['title'].strip()
        or value.get('mode') not in CASE_PROFILES
    ):
        raise APIError('installation_profile', '安装专项ID、名称或材料场景无效')
    if value.get('library_root') != str(Path(value['material_root']) / '规范库'):
        raise APIError('installation_library', '规范库须绑定到已登记资料根内')


def distribution_version(root):
    path = Path(root) / 'data/distribution.json'
    return json.loads(path.read_text(encoding='utf-8'))['version'] if path.is_file() else '0.1.0'


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
    if 'deployed_source' in value and not isinstance(value['deployed_source'], dict):
        raise APIError('installation_source', '安装源码指纹记录损坏')
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


def standards_library(root):
    """安装绑定是规范库唯一入口；仅旧业务工作树兼容原根目录布局。"""
    saved = read_installation(root)
    if saved:
        return Path(saved['library_root']).resolve()
    if portable_workspace(root):
        raise APIError('installation_required', '先安装并绑定自己的资料目录和规范库')
    return Path(root).resolve() / '规范库'


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
    selected_mode = mode if mode is not None else (old or {}).get('mode', CASE_PROFILES[0])
    if selected_mode not in CASE_PROFILES:
        raise APIError('installation_mode', '安装记录的材料场景无效，须核对本机配置')
    config = {
        'schema': INSTALLATION_SCHEMA,
        'tool_root': str(root),
        'material_root': str(data),
        'library_root': str(data / '规范库'),
        'profile_id': profile_id if profile_id is not None else (old or {}).get('profile_id', 'my_project'),
        'title': title if title is not None else (old or {}).get('title', '我的报销项目'),
        'mode': selected_mode,
    }
    validate_installation_fields(config)
    if old and 'deployed_source' in old:
        config['deployed_source'] = old['deployed_source']
    return config


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
