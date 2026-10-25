#!/usr/bin/env python3
"""在任何浏览器写入前校验参数；只支持本次实测的日常报销支付方式。"""
import re
import hashlib
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path


def money(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d+(?:\.\d{1,2})?', value):
        raise ValueError('金额必须是非负金额字符串，最多两位小数')
    return Decimal(value)


def validate_basic_fields(basic, *, require_native_fields=True):
    """仅校验基本信息，不需单号；新草稿可在本地预检，不补未知字段。"""
    if not isinstance(basic, dict):
        raise ValueError('基本信息须为对象')
    teacher = basic.get('teacher_id', '')
    name = basic.get('teacher_name')
    if not isinstance(teacher, str) or not re.fullmatch(r'[A-Za-z0-9]+', teacher) or teacher.upper().startswith(('PB', 'SA', 'SB', 'BA')) or not isinstance(name, str) or not name.strip():
        raise ValueError('需要已确认教师证明人的姓名和工号，不使用学生工号代替')
    fields = basic.get('fields', {})
    allowed = {'t-abst', 't-req_mobile', 't-req_tele', 't-is_have_changefee', 't-td_point', 't-cl_abst'}
    if not isinstance(fields, dict) or set(fields) - allowed or any(not isinstance(v, str) for v in fields.values()):
        raise ValueError('基本字段超出已接通目录或不是字符串')
    if not fields.get('t-abst', '').strip() or not (fields.get('t-req_mobile', '').strip() or fields.get('t-req_tele', '').strip()):
        raise ValueError('需要报销摘要和至少一种联系方式')
    for key, values in [('t-is_have_changefee', {'N', 'Y'}), ('t-td_point', {'退单地点', '退单地点2'})]:
        if (require_native_fields or key in fields) and fields.get(key) not in values:
            raise ValueError('基本信息原生必填选项缺失或未接通：' + key)
    return basic


def validate_corporate_payments(payments, total):
    """Only complete, evidenced company remittances; never fabricate a person binding."""
    if not isinstance(payments, list) or not payments:
        raise ValueError('对公付款须为非空列表')
    identities, users = [], set()
    for p in payments:
        if not isinstance(p, dict) or p.get('mode') != 'corporate_transfer':
            raise ValueError('对公转账不支持混合支付')
        for key in ['name', 'bank_name']:
            if (not isinstance(p.get(key), str) or not p[key].strip()
                    or p[key] != p[key].strip() or re.search(r'[\x00-\x1f\x7f]', p[key])):
                raise ValueError('对公收款名称或开户行不完整')
        account = p.get('bank_account')
        if (not isinstance(account, str) or not re.fullmatch(r'[0-9 ]+', account)
                or not re.fullmatch(r'[0-9]{12,30}', account.replace(' ', ''))):
            raise ValueError('对公账号只允许12至30位数字及ASCII格式空格')
        if 'person_id' in p:
            raise ValueError('对公收款不使用或伪造个人工号')
        if not isinstance(p.get('expected_userid'), str) or not re.fullmatch(r'[A-Za-z0-9]+', p['expected_userid']):
            raise ValueError('对公付款须明确本案当前登录工号')
        users.add(p['expected_userid'])
        refs = p.get('source_refs')
        if (not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or not ref.strip() for ref in refs)
                or len(refs) != len(set(refs))):
            raise ValueError('对公收款信息须引用本案已核来源')
        if money(p.get('amount')) <= 0:
            raise ValueError('对公付款金额须大于0')
        if 'remark' in p and (not isinstance(p['remark'], str)
                or sum(2 if ord(ch) > 255 else 1 for ch in p['remark']) > 25
                or re.search(r'[\x00-\x1f\x7f]', p['remark'])):
            raise ValueError('对公附言须为不超过25字符单位的单行短文本')
        identities.append((p['name'], p['bank_name'], account.replace(' ', '')))
    if len(users) != 1:
        raise ValueError('对公付款的本案登录工号须一致')
    if len(identities) != len(set(identities)) or len({x[2] for x in identities}) != len(identities):
        raise ValueError('对公收款账户重复，须按已核对象合并')
    if sum((money(p['amount']) for p in payments), Decimal('0')) != money(total):
        raise ValueError('对公付款合计与报销总额不符')
    return payments


def validate(c):
    if c.get('system_mode', 'daily') != 'daily':
        raise ValueError('系统模式尚未实测接通：'+str(c['system_mode']))
    if not c.get('case_id') or not isinstance(c.get('serial'), str) or not re.fullmatch(r'\d+', c['serial']):
        raise ValueError('必须给案件标识和当前系统单号')
    total = money(c['amount'])
    if total <= 0:
        raise ValueError('报销金额须大于0')
    validate_categories(c.get('expense_categories', []))
    # 保留旧案的完整校验合同；独立basic阶段要求全部已接通原生必填选项。
    validate_basic_fields(c.get('basic', {}), require_native_fields=False)
    mode=c.get('budget_allocation_mode','manual')
    if mode not in ['manual','system_default_single_funding']: raise ValueError('未知预算分配模式')
    automatic=mode=='system_default_single_funding'
    if automatic and (len(c.get('funding',[]))!=1 or c.get('expense_allocations') or c.get('funding_policy',{}).get('invoice_level_required')):
        raise ValueError('系统自动预算仅用于单经费且无逐票限制的案件，不同时填写手工分摊')
    for group in ['payments', 'funding'] + ([] if automatic else ['expense_allocations']):
        if sum((money(x['amount']) for x in c[group]), Decimal(0)) != total:
            raise ValueError(group + '合计与报销总额不符')
    corporate = any(p.get('mode') == 'corporate_transfer' for p in c['payments'])
    if corporate:
        validate_corporate_payments(c['payments'], c['amount'])
    for p in ([] if corporate else c['payments']):
        if p['mode'] not in ['bank_transfer', 'corporate_card']:
            raise ValueError('该支付方式未实测接通：' + p['mode'])
        if not p.get('name') or not re.fullmatch(r'[A-Za-z0-9]+', p.get('person_id', '')) or money(p['amount']) <= 0:
            raise ValueError('收款人姓名、工号或金额不完整')
    duplicates = [k for k, n in Counter((p['mode'], p['person_id']) for p in ([] if corporate else c['payments'])).items() if n > 1]
    if duplicates:
        raise ValueError('同一收款人同一支付方式应合并：' + str(duplicates))
    codes = [p['code'] for p in c['funding']]
    if len(codes) != len(set(codes)):
        raise ValueError('重复经费卡')
    for f in c['funding']:
        if not automatic and sum((money(x['amount']) for x in c['expense_allocations'] if x['project'] == f['code']), Decimal(0)) != money(f['amount']):
            raise ValueError('经费卡分摊合计不符：' + f['code'])
    if any(x['project'] not in codes for x in c['expense_allocations']):
        raise ValueError('分摊包含配置以外的经费卡')
    leaves = [(x['project'], x['subject'], x['budget']) for x in c['expense_allocations']]
    if len(leaves) != len(set(leaves)):
        raise ValueError('费用分摊预算叶节点重复')
    cards = [p for p in c['payments'] if p['mode'] == 'corporate_card']
    if len(cards) > 1:
        raise ValueError('多条公务卡选择尚待实测，请分案适配后再启用')
    for p in cards:
        t = p.get('transaction', {})
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', t.get('date', '')) or not t.get('merchant') or not re.fullmatch(r'\d{4}', t.get('card_suffix', '')):
            raise ValueError('公务卡唯一匹配条件不完整')
        if money(t.get('search_amount', p['amount'])) < money(p['amount']):
            raise ValueError('公务卡实报金额不能超过消费金额')
    return c


def validate_categories(plans):
    """分类更正独立于预算分配；冻结真实分组、整组金额及逐票GUID。"""
    if not isinstance(plans,list): raise ValueError('费用分类须为列表')
    groups=[]; guids=[]
    for p in plans:
        if not isinstance(p,dict) or set(p)!={'group','subject','subject_name','amount','invoice_guids','source'}:
            raise ValueError('费用分类字段不完整或含未知字段')
        if (not isinstance(p['group'],str) or not p['group'].strip() or len(p['group'])>80 or re.search(r'''[\r\n'"<>!&]''',p['group'])
            or not isinstance(p['subject'],str) or not re.fullmatch(r'[0-9]{5,12}',p['subject'])
            or not isinstance(p['subject_name'],str) or not p['subject_name'].strip()
            or not isinstance(p['source'],str) or not p['source'].strip() or money(p['amount'])<=0):
            raise ValueError('费用分类须有实际分组、科目、金额及依据')
        ids=p['invoice_guids']
        if not isinstance(ids,list) or not ids or len(ids)>100 or any(not isinstance(x,str) or not re.fullmatch(r'[A-F0-9]{32}',x) for x in ids):
            raise ValueError('费用分类须冻结完整发票GUID')
        groups.append(p['group']);guids.extend(ids)
    if len(groups)!=len(set(groups)) or len(guids)!=len(set(guids)):raise ValueError('费用分类分组或发票GUID重复')


def load_context(path):
    """仅供读取身份或打开已登记单据的原生栏目；不放行填写及保存。"""
    c = json.loads(Path(path).read_text(encoding='utf-8'))
    if (not isinstance(c.get('case_id'), str) or not c['case_id'].strip() or
        not isinstance(c.get('serial'), str) or not re.fullmatch(r'[0-9]+', c['serial']) or
        c.get('system_mode', 'daily') != 'daily' or money(c.get('amount')) <= 0):
        raise ValueError('读取栏目须有明确案件、单号、日常模式和正金额')
    return c


def load_funding(path):
    """先选择实际项目，才会生成费用预算树；本阶段只校验身份、总额和经费。"""
    c = load_context(path)
    funds = c.get('funding')
    if not isinstance(funds, list) or not funds:
        raise ValueError('须有已核对的经费项目')
    codes = []
    for item in funds:
        if (not isinstance(item, dict) or not isinstance(item.get('code'), str)
                or not re.fullmatch(r'[A-Za-z0-9]+', item['code']) or money(item.get('amount')) <= 0):
            raise ValueError('经费项目代码或金额不完整')
        codes.append(item['code'])
    if len(set(codes)) != len(codes): raise ValueError('重复经费卡')
    if sum((money(x['amount']) for x in funds), Decimal(0)) != money(c['amount']):
        raise ValueError('funding合计与报销总额不符')
    return c


def load_basic(path):
    """只放行基本信息阶段；仍需真实单号，不以收款或经费未齐阻止此阶段。"""
    c = load_context(path)
    validate_basic_fields(c.get('basic', {}))
    return c


def load(path):
    """读取填报配置，并核对外部定稿正文与内嵌正文一致，防止旧版本混入。"""
    path = Path(path).resolve()
    c = validate(json.loads(path.read_text(encoding='utf-8')))
    notes = c.get('explanations', {})
    if notes.get('source_file'):
        source = (path.parent / notes['source_file']).resolve()
        text = source.read_text(encoding='utf-8').rstrip('\r\n')
        digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
        if text != notes.get('general') or digest != notes.get('source_text_sha256'):
            raise ValueError('说明定稿与配置不同，请先运行 set_case_explanation.py 更新配置')
    return c
