"""退回单的逐票经费校验；金额按分计算，不通过总额推断票据资格。"""
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from validate_config import money

ACTIONS = ['begin', 'select', 'update', 'save', 'return', 'open', 'remark', 'basic', 'basic-read', 'basic-close', 'drafts', 'resume']


def basic_fields(plan):
    fields = plan.get('basic_fields')
    allowed = {'t-abst', 't-req_mobile', 't-req_tele'}
    if not isinstance(fields, dict) or not fields or set(fields) - allowed or any(not isinstance(v, str) or not v.strip() for v in fields.values()):
        raise ValueError('退回基本信息仅允许明确的摘要和联系电话')
    if any(not re.fullmatch(r'[0-9+() -]{5,24}', value) for key, value in fields.items() if key != 't-abst'):
        raise ValueError('联系电话格式无效')
    return fields


def validate_plan(p, c, hashes=True):
    if p.get('serial') != c.get('serial') or p.get('case_id') != c.get('case_id') or money(p['amount']) != money(c['amount']):
        raise ValueError('拆分方案与本案身份或总额不符')
    policy = p['policy']
    saved_policy=c.get('funding_policy', {})
    if saved_policy.get('invoice_level_required') and any(policy.get(k)!=saved_policy.get(k) for k in ['project','team_cap','person_cap']):
        raise ValueError('方案与本案已确定的经费上限不符')
    if money(policy['target']) > money(policy['team_cap']):
        raise ValueError('团队上限不足以覆盖目标金额')
    totals, guids = defaultdict(lambda: money('0')), set()
    for row in p['people']:
        if row['invoice_guid'] in guids:
            raise ValueError('同一发票重复分担')
        guids.add(row['invoice_guid'])
        if row['source_kind'] not in policy['allowed_kinds'] or row['source_kind'] not in ['去程机票','返程机票','去程火车票','返程火车票']:
            raise ValueError('经费只允许已确认的往返交通票据')
        amount = money(row['wc_amount'])
        if amount <= 0 or amount > money(row['invoice_amount']):
            raise ValueError('票据金额不足或分担金额无效')
        if not row.get('person_id') or not row.get('route'):
            raise ValueError('票据缺少人员或行程绑定')
        totals[row['person_id']] += amount
        if hashes and hashlib.sha256(Path(row['source_file']).read_bytes()).hexdigest() != row['source_sha256']:
            raise ValueError('拆分原件SHA变化')
    if any(x > money(policy['person_cap']) for x in totals.values()):
        raise ValueError('单人往返合计超过上限')
    if sum(totals.values(), money('0')) != money(policy['target']):
        raise ValueError('逐票分担合计与目标不符')
    funds = {x['code']: money(x['amount']) for x in p['funding']}
    if len(funds) != len(p['funding']) or funds.get(policy['project']) != money(policy['target']) or sum(funds.values(), money('0')) != money(c['amount']):
        raise ValueError('经费合计或团委目标不符')
    return {'ok': True, 'serial': p['serial'], 'target': policy['target'], 'person_count': len(totals),
            'max_person_amount': str(max(totals.values())), 'system_write': False}


def bindings(text):
    # 原生t.sj_bf多项分摊的分隔符由页面决定；每项都有稳定四元组。
    matches = re.findall(r'(\d+)-([A-Z]+\d+)-([A-Z]+\d+)-(\d+(?:\.\d{1,2})?)(?!\d)', str(text))
    if str(text).strip() and not matches:
        raise ValueError('系统逐票绑定格式变化')
    result = {}
    for subject, project, budget, amount in matches:
        key = (subject, project, budget)
        if key in result:
            raise ValueError('系统逐票重复经费绑定')
        result[key] = money(amount)
    return result


def verify_group(read, plan, group):
    r = read.get('result', read)
    if r.get('ok') is not True or r.get('serial') != plan['serial'] or r.get('projid') != 'WF_YBX':
        raise ValueError('须独立回读同一单据')
    if group not in plan['expected_groups']:
        raise ValueError('未知票据分组')
    grids = [g for g in r.get('grids', []) if g.get('id') == 'gridWF_YBX_1669']
    if len(grids) != 1 or not grids[0].get('rows'):
        raise ValueError('完整逐票拆分列表未回读')
    rows = grids[0]['rows']
    if grids[0].get('loading') or grids[0].get('records') and int(grids[0]['records']) != len(rows):
        raise ValueError('逐票分页回读不完整')
    policy = plan['policy']
    targets = {p['invoice_guid']: p for p in plan['people'] if p['group'] == group}
    seen, total = set(), money('0')
    details = []
    for row in rows:
        if row.get('t.serial_no') != plan['serial']:
            raise ValueError('逐票列表混入其他单据')
        parts = bindings(row['t.sj_bf'])
        full = money(str(row['t.amt']).replace(',', ''))
        if sum(parts.values(), money('0')) != full:
            raise ValueError('票据分担未闭合：'+row['t.tk_guid'])
        wc = sum((a for (_, project, _), a in parts.items() if project == policy['project']), money('0'))
        if any(project == policy['project'] and a > 0 and (subject != policy['subject'] or budget != policy['budget']) for (subject, project, budget), a in parts.items()):
            raise ValueError('团委分担混入非交通科目')
        p = targets.get(row['t.tk_guid'])
        eligible = p and (group != '交通运输服务' or '国内机票款' in row['t.invoice_type'])
        expected = money(p['wc_amount']) if eligible else money('0')
        if wc != expected:
            raise ValueError('团委逐票金额不符或承担了非往返交通：'+row['t.tk_guid'])
        if eligible:
            if row['t.tk_guid'] in seen:
                raise ValueError('目标发票明细重复匹配')
            seen.add(row['t.tk_guid'])
        total += wc
        details.append({'guid': row['t.tk_guid'], 'detail': row['t.invoice_type'], 'wc_amount': str(wc)})
    if seen != set(targets):
        raise ValueError('目标交通发票未全部绑定')
    return {'ok': True, 'serial': plan['serial'], 'group': group, 'wc_amount': str(total), 'details': details,
            'system_write': False, 'verification': 'independent_invoice_bindings'}


def script(c, plan, action, key='', group='', kind=''):
    if action not in ACTIONS:
        raise ValueError('未知修正动作')
    if action in ['select', 'update'] and not re.fullmatch(r'\d+!P![A-Z]+\d+!P![A-Z]+\d+', key):
        raise ValueError('需指定原生科目/经费/预算键')
    if action == 'update' and (group not in plan['expected_groups'] or kind not in ['wc', 'ww', 'life-ww', 'clear']):
        raise ValueError('分组或修正模式未配置')
    source = Path(__file__).with_name('invoice_split.js').read_text()
    if action == 'open' and group not in plan['expected_groups']:
        raise ValueError('未配置的发票分组')
    args = [basic_fields(plan)] if action == 'basic' else [key, group, kind, plan] if action == 'update' else [key] if action == 'select' else [plan.get('native_groups',{}).get(group,group)] if action == 'open' else [funding_remark(plan)] if action=='remark' else []
    method = 'basicClose' if action == 'basic-close' else 'basicRead' if action == 'basic-read' else 'returnToPreview' if action == 'return' else action
    return '(async()=>{\n'+source+'\nReimbursementSplit.configure('+json.dumps(c, ensure_ascii=False)+');\nreturn await ReimbursementSplit.'+method+'(...'+json.dumps(args, ensure_ascii=False)+');\n})()'


def basic_matches(read_result, serial, expected):
    if read_result.get('serial') != serial or read_result.get('projid') != 'WF_YBX' or read_result.get('ok') is not True:
        return False
    # basic-read从保存后的预览重新打开原生字段，独立读取后关闭，无保存操作。
    return read_result.get('action') == 'read_persisted_basic' and all(read_result.get('fields', {}).get(key) == value for key, value in expected.items())


def funding_remark(plan):
    policy=plan['policy'];others=[f for f in plan['funding'] if f['code']!=policy['project']]
    if len(others)!=1:raise ValueError('需要唯一其他经费')
    return ('经费拆分：校团委（'+policy['project']+'）'+policy['target']+'元，仅承担往返交通票款，单人不超过'+policy['person_cap']+'元；其余'+others[0]['amount']+'元从'+others[0]['code']+'报销。')


def remark_matches(read_result, serial, expected):
    # 财务预览把中文逗号转为英文逗号；只处理已观察到的这一显示转换。
    normalize=lambda s:s.replace('，', ',')
    for f in read_result.get('frames', []):
        if ('报销单号：'+serial) not in f.get('visibleText',''):continue
        for table in f.get('tables', []):
            for row in table:
                if len(row)==2 and row[0]=='备注' and normalize(row[1])==normalize(expected):return True
    return False


def expected_update(read, plan, group, key, kind):
    rows = next(g['rows'] for g in read['result']['grids'] if g['id']=='gridWF_YBX_1669')
    target_key = tuple(key.split('!P!'))
    targets = {p['invoice_guid']: p for p in plan['people'] if p['group']==group}
    changes = {}
    for row in rows:
        identity = (row['t.tk_guid'], row['t.inner_ord'])
        if identity in changes: raise ValueError('逐票明细重复')
        parts = bindings(row['t.sj_bf'])
        source = money(row['t.amt'].replace(',', ''))
        p = targets.get(row['t.tk_guid'])
        eligible = p and (group!='交通运输服务' or '国内机票款' in row['t.invoice_type'])
        wc = money(p['wc_amount']) if eligible else money('0')
        if wc > source: raise ValueError('原生票据明细金额不足')
        value = money('0') if kind=='clear' else wc if kind=='wc' else source-wc if kind=='ww' or eligible else parts.get(target_key, money('0'))
        changes[identity] = {'value': value, 'other': {k:v for k,v in parts.items() if k!=target_key and v>0}}
    return changes


def verify_update(read, plan, key, expected):
    r=read['result']
    if r.get('ok') is not True or r.get('serial')!=plan['serial'] or r.get('projid')!='WF_YBX':
        raise ValueError('更新后回读身份不符')
    grid=next(g for g in r['grids'] if g['id']=='gridWF_YBX_1669')
    if grid.get('loading') or int(grid.get('records', len(grid['rows'])))!=len(grid['rows']):
        raise ValueError('更新回读未完整加载')
    target_key=tuple(key.split('!P!'));seen=set()
    for row in grid['rows']:
        identity=(row['t.tk_guid'],row['t.inner_ord']);parts=bindings(row['t.sj_bf'])
        if row.get('t.serial_no')!=plan['serial'] or identity not in expected or identity in seen:
            raise ValueError('更新回读票据身份变化')
        seen.add(identity)
        want=expected[identity]
        if parts.get(target_key,money('0'))!=want['value'] or {k:v for k,v in parts.items() if k!=target_key and v>0}!=want['other']:
            raise ValueError('更新后的经费绑定不符，停止后续写入')
    if seen!=set(expected):raise ValueError('更新回读漏票')
    return True


def verify_preview(read, plan):
    r=read.get('result',read)
    if r.get('ok') is not True or r.get('serial')!=plan['serial'] or r.get('projid')!='WF_YBX':
        raise ValueError('最终预览单据身份不符')
    previews=[f for f in r.get('frames',[]) if ('报销单号：'+plan['serial']) in f.get('visibleText','')]
    if len(previews)!=1:raise ValueError('最终单据预览不唯一')
    funds=defaultdict(lambda:money('0'));subjects=defaultdict(lambda:money('0'));rows=[]
    for table in previews[0].get('tables',[]):
        for row in table:
            if len(row)==5 and re.fullmatch(r'[A-Z]+\d+/[A-Z]+\d+-支出',row[2]):
                project,budget=row[2].split('/',1);amount=money(row[4].replace(',',''))
                funds[project]+=amount;subjects[(project,row[1])]+=amount;rows.append(row)
    expected={f['code']:money(f['amount']) for f in plan['funding']}
    if dict(funds)!=expected:raise ValueError('最终预览经费合计与方案不一致')
    if any(project==plan['policy']['project'] and caption!='差旅费' and amount>0 for (project,caption),amount in subjects.items()):
        raise ValueError('最终预览团委混入非交通科目')
    return {'ok':True,'serial':plan['serial'],'amount':plan['amount'],'funding':{k:str(v) for k,v in funds.items()},
            'subjects':[{'project':k[0],'caption':k[1],'amount':str(v)} for k,v in subjects.items()],
            'expense_rows':rows,'verification':'saved_preview_funding_totals','system_write':False}
