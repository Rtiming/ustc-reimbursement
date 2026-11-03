"""完整本人HTTP清单的身份、金额与已知状态；不持有案件或浏览器状态。"""
from __future__ import annotations

import re
from validate_config import money


def index_bills(snapshot, userid):
    rows = snapshot.get('rows')
    if (snapshot.get('ok') is not True or snapshot.get('complete') is not True or
            snapshot.get('business_transport') != 'native_http' or snapshot.get('action') != 'bills-list' or
            snapshot.get('expected_userid') != userid or snapshot.get('account_scope') != 'current_applicant' or
            not isinstance(rows, list) or type(snapshot.get('total_records')) is not int or
            len(rows) != snapshot['total_records'] or not snapshot.get('observed_at')):
        raise ValueError('必须引用同账号完整底层清单')
    indexed = {}
    for row in rows:
        serial = row.get('t.serial_no') if isinstance(row, dict) else None
        if (not isinstance(serial, str) or not re.fullmatch(r'\d+', serial) or serial in indexed or
                not str(row.get('t.REQ_INFO', '')).startswith(userid + '-')):
            raise ValueError('清单重复单号或账号不同')
        indexed[serial] = row
    return indexed


def bound_bill(snapshot, serial, userid, amount):
    row = index_bills(snapshot, userid).get(serial)
    native = row.get('t.total_amt') if row else None
    if (not isinstance(native, str) or not re.fullmatch(r'(?:\d+|[1-9]\d{0,2}(?:,\d{3})+)(?:\.\d{1,2})?', native) or
            money(native.replace(',', '')) != money(amount)):
        raise ValueError('完整清单未唯一读到同号同金额原单')
    return row


def state(row):
    """只解释已核实组合；unknown必须由业务owner核定，不能当草稿续写。"""
    status, finish, label = (row.get(k) for k in ('t.status', 't.lc_finish', 't.status_name'))
    if status == '10' and finish == 'T' and label == '已预约待投递':
        return 'ready_for_delivery'
    if status == '-15' and finish == 'T' and label == '审批完成-缺少附件退回':
        return 'returned_after_approval'
    if status == '30' and finish == 'T':
        return 'paid'
    if status == '-99' and finish == 'F':
        return 'draft'
    if status == '-2' and finish == 'F':
        return 'workflow_active'
    return 'unknown'


def is_http_snapshot(result):
    return result.get('action') == 'bills-list' or result.get('business_transport') == 'native_http'
