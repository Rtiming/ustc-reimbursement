"""核对保存前已明确拒绝的原支付请求；绝不把编辑值当已保存。"""
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from validate_config import money

# 正式d984b2a旧合计预检版本；只恢复该完整源结构，不接受相似代码片段。
LEGACY_SAVE_LIBRARY_SHA = '05720adba3b97d6fc2b25af2a4224f241dc128e1e266355782fa498348063a8b'

OLD_PREFLIGHT = """async function savePayments(c){
  guard(c);if(cents($('#formWF_YBX_19_t-bx_amt').val())!==cents(c.amount)||cents($('#formWF_YBX_19_t-yt_amt').val())!==cents(c.amount)||cents($('#formWF_YBX_19_t-wt_amt').val())!==0)throw new Error('支付总额尚未闭合');"""


def verify_unsaved_payments(paths, previous, read, outcome):
    """仅接已观察的旧合计预检拒绝；未知/已点击保存仍沿原恢复合同。"""
    if previous.get('operation') != 'step:payments' or outcome not in ['needs_correction', 'not_applied']:
        raise ValueError('支付编辑回读只核对明确未保存的原请求，不能登记saved')
    folder = Path(previous['output_directory']).resolve()
    case_root = paths['state'].parent.resolve()
    if not folder.is_relative_to(case_root / 'receipts'):
        raise ValueError('原支付回执不属于本案')
    source = json.loads((folder / '02_save-payments.json').read_text(encoding='utf-8'))
    body = source.get('result', {})
    script = Path(source.get('script', ''))
    if (source.get('schema') != 'ustc-edge-operation-v2' or source.get('phase') != 'script_completed'
            or body != {'ok':False,'error':'支付总额尚未闭合'}
            or not script.is_absolute() or not script.is_file()
            or hashlib.sha256(script.read_bytes()).hexdigest() != source.get('source_sha256')):
        raise ValueError('没有可核对的保存前明确拒绝及原脚本SHA；保留原支付意图')
    original_js = script.read_text(encoding='utf-8')
    compact = lambda text: re.sub(r'\s+', '', text)
    if (compact(OLD_PREFLIGHT) not in compact(original_js)
            or original_js.count("throw new Error('支付总额尚未闭合')") != 1):
        raise ValueError('不能证明拒绝发生在原保存点击之前')
    if (not read.get('executed_at_utc') or not source.get('executed_at_utc') or not previous.get('started_at_utc')
            or datetime.fromisoformat(source['executed_at_utc']) <= datetime.fromisoformat(previous['started_at_utc'])
            or datetime.fromisoformat(read['executed_at_utc']) <= datetime.fromisoformat(source['executed_at_utc'])
            or read.get('result_marker') == source.get('result_marker')
            or read.get('tab_id') != source.get('tab_id')
            or not re.fullmatch(r'[0-9]+', str(read.get('tab_id', '')))
            or not re.fullmatch(r'reimbursement-result-[a-f0-9]{32}', str(source.get('result_marker', '')))
            or not re.fullmatch(r'reimbursement-result-[a-f0-9]{32}', str(read.get('result_marker', '')))):
        raise ValueError('须有原请求之后、同标签的独立支付编辑回读')
    form = json.loads(paths['form'].read_text(encoding='utf-8'))
    call = re.search(r'\nreturn await ReimbursementAutomation\.savePayments\((\{[^\n]*\}),false\);\s*\}\)\(\)\s*$', original_js)
    if not call or not original_js.startswith('(async()=>{\n'):
        raise ValueError('原脚本不是固定savePayments调用')
    library = original_js[len('(async()=>{\n'):call.start()]
    if hashlib.sha256(library.encode('utf-8')).hexdigest() != LEGACY_SAVE_LIBRARY_SHA:
        raise ValueError('原保存源结构不属于已核对的旧版本，不能按相似预检解锁')
    requested = json.loads(call.group(1))
    if any(requested.get(key) != form.get(key) for key in ['case_id', 'serial', 'amount', 'payments']):
        raise ValueError('原支付请求输入与当前本案不同')
    state = json.loads(paths['state'].read_text(encoding='utf-8'))
    creation_path = Path(state.get('creation_readback', '')).resolve()
    if (state.get('case_id') != form.get('case_id') or state.get('serial') != form.get('serial')
            or not creation_path.is_relative_to(case_root / 'receipts') or not creation_path.is_file()):
        raise ValueError('须有本案已绑定单号的原生创建身份依据')
    creation = json.loads(creation_path.read_text(encoding='utf-8'))
    created = creation.get('result', {})
    expected_userid = created.get('identity', {}).get('userid')
    if (created.get('ok') is not True or created.get('projid') != 'WF_YBX' or created.get('serial') != form.get('serial')
            or not isinstance(expected_userid, str) or not re.fullmatch(r'[A-Za-z0-9]{2,40}', expected_userid)
            or not creation.get('executed_at_utc')
            or datetime.fromisoformat(creation['executed_at_utc']) >= datetime.fromisoformat(previous['started_at_utc'])
            or form.get('expected_userid') not in [None, '', expected_userid]):
        raise ValueError('本案创建回读的单号、时间或账号不符')
    native = read.get('result', {})
    if (form.get('case_id') != previous.get('case_id') or form.get('serial') != previous.get('serial')
            or native.get('ok') is not True or native.get('projid') != 'WF_YBX'
            or native.get('serial') != form.get('serial')
            or native.get('identity', {}).get('userid') != expected_userid
            or native.get('visibleDialogs')):
        raise ValueError('独立支付编辑回读案件、单号、账号或窗口状态不符')
    forms = native.get('forms', [])
    transfer_forms = [f for f in forms if str(f.get('funcno')) == '22']
    if (not {'19', '22'}.issubset({str(f.get('funcno')) for f in forms})
            or len(transfer_forms) != 1):
        raise ValueError('缺少原生支付合计及转卡表单定义')
    inputs = native.get('visibleInputs', [])

    def value(field):
        values = [x.get('value') for x in inputs if x.get('id') == field]
        if len(values) != 1 or not isinstance(values[0], str):
            raise ValueError('原生支付字段缺失或不唯一：' + field)
        return values[0]

    numeric = lambda field: money(value(field).replace(',', ''))
    expected = money(form['amount'])
    if (numeric('formWF_YBX_19_t-bx_amt') != expected or numeric('formWF_YBX_19_t-yt_amt') != expected
            or numeric('formWF_YBX_19_t-wt_amt') != 0):
        raise ValueError('原生支付合计尚未自然闭合，不改写合计或重放保存')
    plans = form.get('payments', [])
    if not plans or any(p.get('mode') != 'bank_transfer' for p in plans):
        raise ValueError('保存前拒绝恢复目前只接已核转卡')
    if type(transfer_forms[0].get('multiCount')) is not int or transfer_forms[0]['multiCount'] != len(plans):
        raise ValueError('原生转卡完整记录数与本案不符')
    actual_ids = [x.get('id') for x in inputs if re.fullmatch(r'formWF_YBX_22_t-sno_\d+', x.get('id', ''))]
    if set(actual_ids) != {'formWF_YBX_22_t-sno_' + str(i) for i in range(len(plans))} or len(actual_ids) != len(plans):
        raise ValueError('原生收款行数与本案不同')
    for index, payment in enumerate(plans):
        field = lambda name: 'formWF_YBX_22_t-' + name + '_' + str(index)
        if (value(field('sno')) != payment['person_id'] or value(field('sname')) != payment['name']
                or numeric(field('amount')) != money(payment['amount']) or not value(field('cnt1')).strip()
                or value(field('remark')) != payment.get('remark', '')):
            raise ValueError('原生收款身份、银行卡、金额或附言与本案不符')
    if sum((money(p['amount']) for p in plans), money('0')) != expected:
        raise ValueError('本案收款配置不闭合')
    return {'state': 'original_payment_not_saved_before_click', 'save_requested': False,
            'current_native_totals_ready': True, 'payments_saved': False}
