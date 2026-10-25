"""保留官方活动审批表版式，只修改已映射的正文单元格，使用Python标准库。"""
import hashlib
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from safe_xml import parse_dom, read_zip_member
from zipfile import ZipFile
from operator_support import ROOT, stamp, write_json
from validate_config import money

TEMPLATE_DIR = ROOT/'templates/activity_approval'
TEMPLATE = TEMPLATE_DIR/'官方附件_心理健康教育_20260414.docx'
TEXT_FIELDS = {'project_name': (0, 1, 70), 'applicant_unit': (1, 1, 55), 'application_date': (1, 3, 20),
               'leader_name': (2, 2, 12), 'leader_id': (2, 4, 20), 'leader_phone': (2, 6, 20),
               'leader_email': (3, 2, 60), 'organization_plan': (6, 1, 450)}
ALLOWED = set(TEXT_FIELDS) | {'template_id', 'start_date', 'end_date', 'participant_count', 'off_campus', 'budget_items', 'self_funded', 'self_funding_source', 'requested_support', 'leader_address', 'implementation_time', 'budget_is_estimate', 'actual_project_total', 'show_self_funding', 'show_project_total', 'require_prior_application'}
ALLOWED.add('alignments')
ALIGNMENT_ALIASES = {
    'left': 'left', 'center': 'center', 'right': 'right', 'both': 'both', 'justify': 'both',
    '左对齐': 'left', '居中': 'center', '右对齐': 'right', '两端对齐': 'both',
}
ALIGNMENT_KEYS = set(TEXT_FIELDS) | {'implementation_time', 'participant_count', 'off_campus', 'summary', 'budget.name', 'budget.unit_price', 'budget.quantity', 'budget.amount'}


def normalized_alignments(values, errors=None):
    """将用户可读的对齐选项归一化为Word w:jc值，拒绝未映射槽位。"""
    raw = values.get('alignments', {}) if isinstance(values, dict) else {}
    if raw in [None, '']:
        return {}
    if not isinstance(raw, dict):
        if errors is not None:
            errors.append({'field': 'alignments', 'error': '须为字段到对齐方式的对象'})
        return {}
    result = {}
    for key, value in raw.items():
        if key not in ALIGNMENT_KEYS:
            if errors is not None:
                errors.append({'field': f'alignments.{key}', 'error': '不是已映射的表格槽位'})
            continue
        if not isinstance(value, str) or value not in ALIGNMENT_ALIASES:
            if errors is not None:
                errors.append({'field': f'alignments.{key}', 'error': '使用left/center/right/both或左对齐/居中/右对齐/两端对齐'})
            continue
        result[key] = ALIGNMENT_ALIASES[value]
    return result


def amount_basis(v, budget_total, errors, unresolved=None):
    """估算预算与实际票面金额分列；不取消默认资金平衡检查。"""
    for key, default in [('budget_is_estimate',False),('show_self_funding',True),('show_project_total',True),('require_prior_application',False)]:
        if type(v.get(key,default)) is not bool:
            errors.append({'field':key,'error':'须为布尔值'})
    if v.get('show_project_total',True) is False and v.get('budget_is_estimate') is True:
        errors.append({'field':'show_project_total','error':'申请前预算不显示项目合计时，不能同时启用实际金额分列'})
    if not v.get('budget_is_estimate',False):
        if v.get('actual_project_total') not in [None,'']:
            errors.append({'field':'actual_project_total','error':'实际金额与预算分列须显式budget_is_estimate=true'})
        return budget_total
    if v.get('actual_project_total') in [None,'']:
        if unresolved is not None:unresolved.append('actual_project_total')
        else:errors.append({'field':'actual_project_total','error':'估算模式须给出有来源的实际票面金额'})
        return None
    try:
        actual=money(v['actual_project_total'])
        if actual<=0:raise ValueError('实际金额须大于0')
        if budget_total is not None and actual>budget_total:raise ValueError('实际金额超过估算预算，请核对预算')
        return actual
    except (ValueError,TypeError) as exc:
        errors.append({'field':'actual_project_total','error':str(exc)})
        return None


def application_order(v, errors):
    if v.get('require_prior_application') is not True or not v.get('application_date'):return
    try:
        start=date.fromisoformat(v['start_date'])
    except (KeyError,TypeError,ValueError):
        errors.append({'field':'start_date','error':'检查申报早于实施须给出准确开始日期'})
        return
    value=v['application_date']
    match=re.fullmatch(r'(\d{4})年(\d{1,2})月(\d{1,2})日',value) if isinstance(value,str) else None
    try:
        applied=date(*map(int,match.groups())) if match else date.fromisoformat(value)
        if applied>=start:raise ValueError('申报日期须早于实施开始日期；不能由工具自动倒填')
    except (TypeError,ValueError) as exc:
        errors.append({'field':'application_date','error':'使用实际的YYYY-MM-DD或年月日日期：'+str(exc)})


def inspect_input(v):
    errors = []
    if not isinstance(v, dict):
        return {'ok': False, 'errors': [{'field': 'values', 'error': '须为对象'}]}
    for key in sorted(set(v) - ALLOWED):
        errors.append({'field': key, 'error': '未映射字段，不能静默丢弃；审批意见/签章/编号由实际审批填写'})
    normalized_alignments(v, errors)
    if v.get('template_id') != 'ustc_activity_20260414':
        errors.append({'field': 'template_id', 'error': '使用ustc_activity_20260414；来源和适用性见模板说明'})
    for key, (_, _, limit) in TEXT_FIELDS.items():
        text = v.get(key)
        if not isinstance(text, str) or not text.strip():
            errors.append({'field': key, 'error': '必填；未知事实集中询问，不用旧案填空'})
        elif len(text) > limit:
            errors.append({'field': key, 'error': f'此模板栏容量为{limit}字符；请压缩文字或改用实际主管部门模板'})
    for key, limit in [('leader_address',70),('implementation_time',45)]:
        if v.get(key) is not None and (not isinstance(v[key],str) or len(v[key])>limit):
            errors.append({'field':key,'error':'字段类型或容量不符'})
    if isinstance(v.get('leader_address'), str) and v['leader_address'] and isinstance(v.get('organization_plan'),str) and len(v['organization_plan']+'\n项目联系人地址：'+v['leader_address'])>450:
        errors.append({'field':'organization_plan','error':'含联系人地址后超出450字符'})
    for key, pattern in [('leader_id', r'[A-Za-z0-9]+'), ('leader_phone', r'\+?[\d -]{7,20}'), ('leader_email', r'[^\s@]+@[^\s@]+\.[^\s@]+')]:
        if v.get(key) and (not isinstance(v[key], str) or not re.fullmatch(pattern, v[key])): errors.append({'field': key, 'error': '格式不完整'})
    try:
        start, end = date.fromisoformat(v['start_date']), date.fromisoformat(v['end_date'])
        if end < start: raise ValueError('结束日期早于开始日期')
    except (KeyError, TypeError, ValueError) as exc:
        errors.append({'field': 'start_date/end_date', 'error': '使用YYYY-MM-DD且结束不早于开始：'+str(exc)})
    if type(v.get('participant_count')) is not int or v['participant_count'] <= 0:
        errors.append({'field': 'participant_count', 'error': '必须是正整数'})
    if type(v.get('off_campus')) is not bool:
        errors.append({'field': 'off_campus', 'error': '必须明确true/false；不能沿用模板默认否'})
    items = v.get('budget_items', [])
    total = Decimal(0)
    if not isinstance(items, list) or not 1 <= len(items) <= 8:
        errors.append({'field': 'budget_items', 'error': '此模板最多8项预算，至少1项'})
        items = []
    for i, row in enumerate(items):
        try:
            if not isinstance(row, dict): raise ValueError('预算项须为对象')
            if set(row) - {'name', 'unit_price', 'quantity', 'amount'}: raise ValueError('预算含未映射字段')
            if not isinstance(row.get('name'), str) or not 1 <= len(row['name']) <= 20: raise ValueError('预算名称须1至20字符')
            amount = money(row['amount'])
            if amount <= 0: raise ValueError('预算金额须大于0')
            total += amount
            unit, count = row.get('unit_price', ''), row.get('quantity', '')
            if bool(unit) != bool(count): raise ValueError('单价和数量须一起给出，或都留空只给总额')
            if unit:
                if not isinstance(count, str) or not re.fullmatch(r'\d+(?:\.\d{1,2})?', count): raise ValueError('数量应为正数字符串')
                if Decimal(count) <= 0 or money(unit) * Decimal(count) != amount: raise ValueError('单价乘数量与该项金额不一致')
        except (KeyError, ValueError, TypeError) as exc:
            errors.append({'field': f'budget_items[{i}]', 'error': str(exc)})
    summary_total=amount_basis(v,total,errors)
    application_order(v,errors)
    try:
        own, request = money(v['self_funded']), money(v['requested_support'])
        if summary_total is not None and own + request != summary_total: raise ValueError('自筹加申请资助须等于项目合计金额；估算模式按实际票面合计平衡')
        if v.get('show_self_funding',True) is False and own!=0:raise ValueError('有自筹金额不能隐藏自筹部分')
        if own > 0 and not v.get('self_funding_source'): raise ValueError('自筹大于0需要来源')
    except (KeyError, ValueError, TypeError) as exc:
        errors.append({'field': 'self_funded/requested_support', 'error': str(exc)})
    return {'ok': not errors, 'errors': errors, 'budget_total': str(total), 'summary_total':str(summary_total) if summary_total is not None else None, 'system_write': False,
            'approval_status': 'draft_not_approved',
            'conditional_materials': ['校外活动组织单位安全承诺书', '校外活动个人安全承诺书'] if v.get('off_campus') is True else [],
            'template_scope': '2026-04-14人工智能与数据科学学院心理健康活动官方附件，结构与本地团学历史表相近；使用前核对主管单位版本'}


def inspect_draft(v):
    """允许未知字段留白的本地草稿，不给未知日期、预算或经费编默认值。"""
    errors, unresolved = [], []
    if not isinstance(v, dict):return {'ok':False,'errors':[{'field':'values','error':'须为对象'}]}
    for key in set(v)-ALLOWED:errors.append({'field':key,'error':'未映射字段，不代填审批/签章'})
    normalized_alignments(v, errors)
    if v.get('template_id') != 'ustc_activity_20260414':errors.append({'field':'template_id','error':'模板标识错误'})
    for key,(_,_,limit) in TEXT_FIELDS.items():
        value=v.get(key)
        if value in [None,'']:unresolved.append(key)
        elif not isinstance(value,str) or len(value)>limit:errors.append({'field':key,'error':'字段类型或容量不符'})
    for key,pattern in [('leader_id',r'[A-Za-z0-9]+'),('leader_phone',r'\+?[\d -]{7,20}'),('leader_email',r'[^\s@]+@[^\s@]+\.[^\s@]+')]:
        if v.get(key) and (not isinstance(v[key],str) or not re.fullmatch(pattern,v[key])):errors.append({'field':key,'error':'格式不完整'})
    for key,limit in [('leader_address',70),('implementation_time',45)]:
        if v.get(key) is not None and (not isinstance(v[key],str) or len(v[key])>limit):errors.append({'field':key,'error':'字段类型或容量不符'})
    plan, address = v.get('organization_plan'), v.get('leader_address')
    if isinstance(plan, str) and isinstance(address, str) and address and len(plan+'\n项目联系人地址：'+address)>450:
        errors.append({'field':'organization_plan','error':'含联系人地址后超出450字符'})
    if not v.get('implementation_time'):
        try:
            start,end=date.fromisoformat(v['start_date']),date.fromisoformat(v['end_date'])
            if end<start:raise ValueError('日期倒置')
        except (KeyError,TypeError,ValueError):
            if v.get('start_date') or v.get('end_date'):errors.append({'field':'start_date/end_date','error':'给出完整有效起止日期，或只填implementation_time'})
            else:unresolved.append('implementation_time')
    if v.get('participant_count') is None:unresolved.append('participant_count')
    elif type(v['participant_count']) is not int or v['participant_count']<=0:errors.append({'field':'participant_count','error':'须为正整数'})
    if v.get('off_campus') is None:unresolved.append('off_campus')
    elif type(v['off_campus']) is not bool:errors.append({'field':'off_campus','error':'须为布尔值'})
    items=v.get('budget_items') or []
    if not isinstance(items,list) or len(items)>8:errors.append({'field':'budget_items','error':'预算须为至多8项的数组'});items=[]
    total=Decimal('0');all_amounts=bool(items)
    if not items:unresolved.append('budget_items')
    for i,row in enumerate(items):
        try:
            if not isinstance(row,dict) or set(row)-{'name','unit_price','quantity','amount'}:raise ValueError('预算字段错误')
            if not isinstance(row.get('name'),str) or not 1<=len(row['name'])<=20:raise ValueError('预算名称须1至20字')
            if row.get('amount') in [None,'']:
                all_amounts=False;unresolved.append(f'budget_items[{i}].amount')
            else:
                amount=money(row['amount'])
                if amount<=0:raise ValueError('预算金额须大于0')
                total+=amount
            unit,count=row.get('unit_price'),row.get('quantity')
            if bool(unit)!=bool(count):raise ValueError('单价数量须同时填或留空')
            if unit:
                if not isinstance(count,str) or not re.fullmatch(r'\d+(?:\.\d{1,2})?',count) or Decimal(count)<=0:raise ValueError('数量错误')
                if row.get('amount') in [None,''] or money(unit)*Decimal(count)!=money(row['amount']):raise ValueError('单价数量金额不符')
        except (ValueError,TypeError,KeyError) as exc:errors.append({'field':f'budget_items[{i}]','error':str(exc)})
    funding={}
    for key in ['self_funded','requested_support']:
        if v.get(key) in [None,'']:unresolved.append(key)
        else:
            try:funding[key]=money(v[key])
            except (TypeError,ValueError) as exc:errors.append({'field':key,'error':str(exc)})
    summary_total=amount_basis(v,total if all_amounts else None,errors,unresolved)
    application_order(v,errors)
    if summary_total is not None and len(funding)==2 and sum(funding.values())!=summary_total:errors.append({'field':'budget','error':'自筹和资助与项目合计不符'})
    if v.get('show_self_funding',True) is False and funding.get('self_funded') not in [None,Decimal(0)]:errors.append({'field':'self_funded','error':'有自筹金额不能隐藏自筹部分'})
    if funding.get('self_funded',0)>0 and not v.get('self_funding_source'):unresolved.append('self_funding_source')
    return {'ok':not errors,'errors':errors,'unresolved_fields':unresolved,'budget_total':str(total) if all_amounts else None,'summary_total':str(summary_total) if summary_total is not None else None,
            'approval_status':'draft_not_approved','system_write':False,
            'conditional_materials':['校外活动组织单位安全承诺书','校外活动个人安全承诺书'] if v.get('off_campus') is True else [],
            'template_scope':'只复用官方表结构，主管单位适用性及历史项目补办要求需另核对'}


def children(node, tag):
    return [n for n in node.childNodes if getattr(n, 'tagName', '') == tag]


def text_of(node):
    return ''.join(n.firstChild.data for n in node.getElementsByTagName('w:t') if n.firstChild)


def set_text(doc, cell, text):
    paragraphs = children(cell, 'w:p')
    prototype = paragraphs[0].cloneNode(True)
    runs = prototype.getElementsByTagName('w:r')
    rprops = children(runs[0], 'w:rPr')[0].cloneNode(True) if runs and children(runs[0], 'w:rPr') else None
    for p in paragraphs: cell.removeChild(p)
    for line in text.split('\n'):
        p = prototype.cloneNode(True)
        for n in list(p.childNodes):
            if getattr(n, 'tagName', '') != 'w:pPr': p.removeChild(n)
        run = doc.createElement('w:r')
        if rprops: run.appendChild(rprops.cloneNode(True))
        node = doc.createElement('w:t'); node.setAttribute('xml:space', 'preserve'); node.appendChild(doc.createTextNode(line))
        run.appendChild(node); p.appendChild(run); cell.appendChild(p)


def set_alignment(doc, cell, alignment):
    """设置单元格内所有段落的水平对齐，不改变字号、边框或单元格网格。"""
    for paragraph in children(cell, 'w:p'):
        props = children(paragraph, 'w:pPr')
        if props:
            ppr = props[0]
        else:
            ppr = doc.createElement('w:pPr')
            paragraph.insertBefore(ppr, paragraph.firstChild)
        for old in list(children(ppr, 'w:jc')):
            ppr.removeChild(old)
        jc = doc.createElement('w:jc')
        jc.setAttribute('w:val', alignment)
        ppr.appendChild(jc)


def apply_alignments(doc, cells, values):
    alignments = normalized_alignments(values)
    for key, alignment in alignments.items():
        if key in TEXT_FIELDS:
            row, cell, _ = TEXT_FIELDS[key]
            set_alignment(doc, cells[row][cell], alignment)
        elif key == 'implementation_time':
            set_alignment(doc, cells[4][1], alignment)
        elif key == 'participant_count':
            set_alignment(doc, cells[4][3], alignment)
        elif key == 'off_campus':
            set_alignment(doc, cells[5][1], alignment)
        elif key == 'summary':
            set_alignment(doc, cells[12][1], alignment)
        elif key.startswith('budget.'):
            budget_key = key.split('.', 1)[1]
            col_offset = {'name': 0, 'unit_price': 1, 'quantity': 2, 'amount': 3}[budget_key]
            for i, _item in enumerate(values.get('budget_items') or []):
                row = 8 + i // 2
                col = 1 + (i % 2) * 4
                set_alignment(doc, cells[row][col + col_offset], alignment)


def build(values, output, allow_incomplete=False):
    checked = inspect_draft(values) if allow_incomplete else inspect_input(values)
    if not checked['ok']: return checked
    output = Path(output).resolve()
    if output.suffix.lower() != '.docx' or output.exists() or TEMPLATE_DIR.resolve() in output.parents:
        raise ValueError('输出须为尚不存在的.docx，放在案件/generated目录，不覆盖模板或已有文件')
    registry = __import__('json').loads((TEMPLATE_DIR/'registry.json').read_text(encoding='utf-8'))
    digest = hashlib.sha256(TEMPLATE.read_bytes()).hexdigest()
    if digest != registry['templates'][0]['converted_docx_sha256']: raise ValueError('模板SHA变化，不能继续按旧字段映射填写')
    with ZipFile(TEMPLATE) as z:
        doc = parse_dom(read_zip_member(z, 'word/document.xml'))
        table = doc.getElementsByTagName('w:tbl')[0]
        rows = children(table, 'w:tr')
        cells = [children(row, 'w:tc') for row in rows]
        if len(rows) != 14 or text_of(cells[13][0]) != '审批意见': raise ValueError('模板结构变化')
        approval_before = cells[13][1].toxml()
        # 原附件校外安全提示行固定高度会裁切中文；仅放宽该行，保留字号、列宽和页尺寸。
        for height in rows[5].getElementsByTagName('w:trHeight'):
            height.setAttribute('w:hRule', 'atLeast')
        for key, (row, cell, _) in TEXT_FIELDS.items(): set_text(doc, cells[row][cell], values.get(key) or '')
        # 十位学号在原12pt窄栏会断行；只收小该输入槽的字号，保留网格和其他格式。
        if len(values.get('leader_id') or '') >= 10:
            for size in cells[2][4].getElementsByTagName('w:sz'):
                size.setAttribute('w:val', '20')
        if values.get('leader_address'):
            set_text(doc, cells[6][1], (values.get('organization_plan') or '')+'\n项目联系人地址：'+values['leader_address'])
        if values.get('implementation_time'):
            time_text=values['implementation_time']
        elif values.get('start_date') and values.get('end_date'):
            start,end=date.fromisoformat(values['start_date']),date.fromisoformat(values['end_date'])
            fmt=lambda d:f'{d.year}年{d.month}月{d.day}日'
            time_text=fmt(start)+'—'+fmt(end)+f'（共{(end-start).days+1}天）'
        else:time_text=''
        set_text(doc, cells[4][1], time_text)
        set_text(doc, cells[4][3], str(values['participant_count'])+'人' if values.get('participant_count') else '')
        set_text(doc, cells[5][1], '' if values.get('off_campus') is None else '是' if values['off_campus'] else '否')
        for i, item in enumerate(values.get('budget_items') or []):
            row = 8+i//2; col = 1+(i%2)*4
            for j, key in enumerate(['name', 'unit_price', 'quantity', 'amount']):
                val = item.get(key) or ''
                if key in ['unit_price', 'amount'] and val:
                    val = format(Decimal(val), 'f').rstrip('0').rstrip('.') if '.' in val else val
                set_text(doc, cells[row][col+j], val)
        summary = f"项目合计金额：{checked['summary_total'] or '____'}元"
        if values.get('show_project_total',True) is False:
            budget=checked['budget_total'] or '____'
            if '.' in budget:budget=budget.rstrip('0').rstrip('.')
            summary=f"预算合计：{budget}元"
        elif values.get('budget_is_estimate') is True:
            summary += '（本批实际票面合计）'
            estimate=checked['budget_total'] or '____'
            if '.' in estimate:estimate=estimate.rstrip('0').rstrip('.')
            summary = f"预算估算合计：{estimate}元\n"+summary
        if values.get('show_self_funding',True):
            summary += f"；其中，自筹经费金额：{values.get('self_funded') if values.get('self_funded') not in [None,''] else '____'}元（来源：{values.get('self_funding_source') or '____'}）"
        summary += f"\n申报资助金额：{values.get('requested_support') if values.get('requested_support') not in [None,''] else '____'}元"
        set_text(doc, cells[12][1], summary)
        apply_alignments(doc, cells, values)
        if cells[13][1].toxml() != approval_before: raise ValueError('审批栏意外改变')
        output.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(output, 'w') as final:
            for info in z.infolist(): final.writestr(info, doc.toxml(encoding='UTF-8') if info.filename == 'word/document.xml' else z.read(info.filename))
    with ZipFile(TEMPLATE) as original, ZipFile(output) as final:
        changed = [n for n in original.namelist() if original.read(n) != final.read(n)]
    if changed != ['word/document.xml']: raise ValueError('修改范围超过正文XML')
    receipt = {'ok': True, 'generated_at_utc': stamp(), 'output': str(output), 'output_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
               'template': str(TEMPLATE), 'template_sha256': digest, 'changed_package_parts': changed,
               'approval_cell_unchanged': True, 'approval_status': 'draft_not_approved', 'budget_total': checked['budget_total'], 'summary_total':checked['summary_total'], 'budget_is_estimate':values.get('budget_is_estimate',False), 'show_project_total':values.get('show_project_total',True), 'alignments':normalized_alignments(values),
               'render_verified': False, 'unresolved_fields':checked.get('unresolved_fields',[]), 'allow_incomplete':allow_incomplete,
               'next': '运行统一render命令并检查所有页面；实际签批后再作为报销审批材料', 'system_write': False}
    write_json(output.with_suffix('.receipt.json'), receipt)
    return receipt
