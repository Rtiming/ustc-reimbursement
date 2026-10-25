"""显式选择本机浏览器通道；不探测后自动回退，不混用标签命名空间。"""
import os
import re
import sys


BACKEND_ENV = 'REIMBURSEMENT_EDGE_BACKEND'
NAMESPACE_ENV = 'REIMBURSEMENT_EDGE_TAB_NAMESPACE'
WINDOW_ENV = 'REIMBURSEMENT_EDGE_WINDOW_ID'
NM_NAMESPACE = re.compile(r'edge-native-messaging:[a-p]{32}:[a-f0-9]{32}')


def backend():
    default = 'edge_native_messaging' if sys.platform == 'win32' else 'edge_applescript'
    selected = os.environ.get(BACKEND_ENV, default)
    if selected not in {'edge_applescript', 'edge_native_messaging'}:
        raise ValueError('浏览器backend须明确为edge_applescript或edge_native_messaging；未派发')
    if sys.platform not in {'darwin', 'win32'} or (sys.platform == 'win32' and selected != 'edge_native_messaging'):
        raise ValueError('当前平台不支持所选浏览器backend；未派发')
    return selected


def mac_native_enabled():
    return sys.platform == 'darwin' and backend() == 'edge_native_messaging'


def mac_namespace():
    value = os.environ.get(NAMESPACE_ENV, '')
    if not NM_NAMESPACE.fullmatch(value):
        raise ValueError('Mac后台通道须传当前inventory给出的完整tab_id_namespace；不能沿用AppleEvent数字标签')
    return value


def mac_target():
    window = os.environ.get(WINDOW_ENV, '')
    if not re.fullmatch(r'[0-9]+', window):
        raise ValueError('Mac后台通道须传同次inventory核实的window_id；未派发')
    return {'tab_id_namespace': mac_namespace(), 'expected_window_id': window}


def transport_identity():
    selected = backend()
    namespace = (mac_namespace() if sys.platform == 'darwin' and selected == 'edge_native_messaging'
                 else 'edge-applescript' if selected == 'edge_applescript' else 'edge-native-messaging-windows')
    identity = {'backend': selected, 'tab_id_namespace': namespace, 'platform': sys.platform}
    if sys.platform == 'darwin' and selected == 'edge_native_messaging':
        identity['window_id'] = mac_target()['expected_window_id']
    return identity


def assert_original_transport(receipt):
    """旧Mac回执无transport字段时仅认原AppleEvent，不给它自动迁移权限。"""
    current = transport_identity()
    original = receipt.get('browser_transport')
    if original is None:
        original = {'backend': 'edge_applescript', 'tab_id_namespace': 'edge-applescript', 'platform': 'darwin'} if sys.platform == 'darwin' else current
    if original != current:
        raise ValueError('原回执浏览器通道或标签命名空间不同；保留原marker，不切换后端恢复或重放')
    return current
