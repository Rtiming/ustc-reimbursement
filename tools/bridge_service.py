"""CLI/JSON/MCP共用的有限接线入口；安装与财务办理分开。"""

import sys
from assistant_contract import APIError

ACTIONS = ('status', 'prepare', 'install', 'installation-result', 'inventory')


def run(action='status', execute=False, request_id=None, expected_registration_sha256=None):
    if action not in ACTIONS or type(execute) is not bool:
        raise APIError('bridge_arguments', '桥接线只支持固定action及布尔execute')
    if action in ('status', 'inventory', 'installation-result') and execute:
        raise APIError('bridge_readonly', '状态和原安装结果不接受execute；不会重复运行安装')
    if sys.platform not in ('win32', 'darwin'):
        return {'ok': False, 'state': 'unsupported_platform', 'financial_write_executed': False,
                'next_action': 'Linux可用材料、OCR与规范功能；财务桥使用Windows/macOS'}
    if action == 'status':
        from windows_bridge_setup import capabilities
        return {'ok': True, 'capabilities': capabilities(), 'financial_write_executed': False}
    if action == 'inventory':
        from native_finance_bridge import inventory
        return inventory()
    if action == 'installation-result':
        if sys.platform != 'win32':
            raise APIError('bridge_platform', 'Mac安装结果通过status检查')
        from windows_bridge_tasks import result
        return result(request_id)
    if sys.platform == 'win32':
        from windows_bridge_setup import prepare, registration_snapshot
        if action == 'prepare':
            return prepare() if execute else {'ok': True, 'execute': False, 'action': action, 'system_write': False}
        snapshot = registration_snapshot()
        if not execute:
            return {'ok': True, 'execute': False, 'action': action, **snapshot,
                    'financial_write_executed': False, 'next_action': '携registration_sha256和稳定request_id执行；只安装固定扩展'}
        from windows_bridge_tasks import start
        return start(request_id, expected_registration_sha256)
    from mac_bridge_setup import prepare, install
    if not execute:
        return {'ok': True, 'execute': False, 'action': action, 'financial_write_executed': False}
    return {'prepare': prepare, 'install': install}[action]()
