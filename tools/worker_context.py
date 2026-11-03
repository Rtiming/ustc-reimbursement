"""冻结非秘密的财务连接上下文，供Windows独立worker恢复；不传凭据。"""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import threading

LOCK = threading.RLock()
KEYS = frozenset({'REIMBURSEMENT_EDGE_BACKEND','REIMBURSEMENT_EDGE_TAB_NAMESPACE',
    'REIMBURSEMENT_EDGE_WINDOW_ID','REIMBURSEMENT_EXPECTED_USERID',
    'REIMBURSEMENT_NATIVE_ONLY_ISOLATION','REIMBURSEMENT_LEGACY_AE_INVENTORY_PROOF',
    'REIMBURSEMENT_REGISTERED_SESSION'})
OPERATIONS = frozenset({'approver_batch','business','access','tabs','read_system','stage','invoice','invoice_verification',
    'invoice_selection','invoice_retry','invoice_edit','expense_amount','pdf_export','print_batch',
    'submission','correction','record_result'})


def validate(value, root):
    if not isinstance(value, dict) or set(value)-KEYS or any(not isinstance(v,str) or not v for v in value.values()):
        raise ValueError('worker仅接受已登记的非秘密连接上下文')
    patterns = {'REIMBURSEMENT_EDGE_BACKEND': r'edge_(?:applescript|native_messaging)',
        'REIMBURSEMENT_EDGE_TAB_NAMESPACE': r'edge-native-messaging:[a-p]{32}:[a-f0-9]{32}',
        'REIMBURSEMENT_EDGE_WINDOW_ID': r'[0-9]+', 'REIMBURSEMENT_EXPECTED_USERID': r'[A-Za-z0-9]{2,40}',
        'REIMBURSEMENT_NATIVE_ONLY_ISOLATION': r'[01]', 'REIMBURSEMENT_LEGACY_AE_INVENTORY_PROOF': r'[01]'}
    for key, pattern in patterns.items():
        if key in value and not re.fullmatch(pattern, value[key]):
            raise ValueError('worker连接字段格式无效：'+key)
    if 'REIMBURSEMENT_EDGE_TAB_NAMESPACE' in value and 'REIMBURSEMENT_EDGE_WINDOW_ID' not in value:
        raise ValueError('完整连接命名空间须同时绑定窗口')
    if 'REIMBURSEMENT_REGISTERED_SESSION' in value:
        from material_intake import reject_links
        path=Path(value['REIMBURSEMENT_REGISTERED_SESSION'])
        reject_links(path)
        parent=(Path(root)/'.local/registered_workflows').resolve()
        if not path.is_absolute() or path.name!='session.json' or not path.resolve().is_relative_to(parent):
            raise ValueError('登记会话只能引用本工具私有运行目录')
    return dict(value)


@contextmanager
def applied(root, context):
    if context is None:
        yield None
        return
    value=validate(context,root)
    with LOCK:
        before={key:os.environ.get(key) for key in KEYS}
        try:
            for key in KEYS:
                if key in value:os.environ[key]=value[key]
                else:os.environ.pop(key,None)
            yield value
        finally:
            for key, old in before.items():
                if old is None:os.environ.pop(key,None)
                else:os.environ[key]=old


@contextmanager
def task_context(root, operation, explicit=None):
    if operation not in OPERATIONS:
        yield None
        return
    with LOCK:
        value=explicit if explicit is not None else {key:os.environ[key] for key in KEYS if key in os.environ}
        with applied(root,value) as frozen:
            yield frozen
