"""报销项原生窗口、具备逐票依据的单次减报保存及持久回读。"""
from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path

SCHEMA = 'ustc-expense-amount-window-v1'
INSPECT_SCHEMA = SCHEMA + '-inspection'
PENDING_SCHEMA = SCHEMA + '-pending'
GUID = '8EB30222662E4EE1AA118D8912DF3EDE'
RETURN_GUID = 'F462D6AAA1AE47E289317CA3535488F6'
SAVE_GUID = '49D46B91A5624545BDB348D1D2DF3639'
SAVE_PROC = '`$da4bb0cce5ee9adce0375b8930aec200:#390-t.tk_guid#;#390-t.t_amt#;#390-t.ord#;#390-t.invoice_amt#;#61-serialno#;#0-userinfo.userid#'
SAVE_ACTS = '#61-tksbcodecnt#==0:41@#61-tksbcodecnt#==0:41@#61-tksbcodecnt#>0:276'
# 已核对原生驱动：call_proc完成并经handleMsg成功后才executeActs/routeActs。
# tksbcodecnt可由保存过程返回，保存前未定义不能等同0，也不应要求它预先存在。
DEFERRED_BRANCH_DRIVER_SHA256 = '0ae94460df6ec38fe02af75bd6724123def589f49902e3a7a15b1ad499234cb6'
ROW_FIELDS = ['t-t_desc', 't-invoice_amt_xs', 't-zf_zmt', 't-t_amt', 't-invoice_amt',
              't-old_amt', 't-serial_no', 't-tk_guid', 't-ord']
REDUCTION_SCHEMA = SCHEMA + '-reduction-preparation'
CHECK = '`$11058ed91db744a8feb8dbbcdfc1c7c6:#61-xtype#;#61-serialno#;#0-userinfo.userid#'
PROC = '`$8a164538e6b43f2994c05419f2ac4fd4:#61-xtype#;#61-serialno#;#0-userinfo.userid#'
BUTTON_KEYS = ['btnguid', 'caption', 'pre_compute', 'pre_check', 'call_proc', 'check_func', 'procs',
               'post_check', 'acts', 'affects', 'jscontent', 'clickquery', 'clickqueryhint',
               'beforeclick', 'afterclick', 'customFunc', 'hasIntf_beforeclick', 'hasIntf_afterclick']


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def base_request(form, group, subject, expected_userid, tab_id):
    request = {'schema': SCHEMA, 'case_id': form.get('case_id'), 'serial': form.get('serial'),
               'group': group, 'subject': subject, 'expected_userid': expected_userid, 'tab_id': tab_id}
    validate(request)
    if form.get('system_mode', 'daily') != 'daily':
        raise ValueError('报销项窗口仅适配日常报销')
    return request


def validate(request):
    if not isinstance(request, dict) or request.get('schema') != SCHEMA:
        raise ValueError('须使用正式报销项窗口请求')
    for key, pattern in [('case_id', r'[A-Za-z0-9_-]{1,96}'), ('serial', r'[0-9]+'),
                         ('subject', r'[0-9]{2,12}'), ('expected_userid', r'[A-Za-z0-9_.@-]{1,80}'), ('tab_id', r'[0-9]+')]:
        if not isinstance(request.get(key), str) or not re.fullmatch(pattern, request[key]):
            raise ValueError('窗口请求身份或参数不符：' + key)
    group = request.get('group')
    if not isinstance(group, str) or not group.strip() or len(group) > 80 or re.search(r'[\r\n\'"<>!&]', group):
        raise ValueError('须有明确的原生费用分组')
    return request


def _body(receipt):
    body = receipt.get('result', receipt)
    if not isinstance(body, dict) or body.get('ok') is not True or body.get('schema') != INSPECT_SCHEMA:
        raise ValueError('须引用成功的独立报销项窗口检查')
    return body


def same_target(request, body):
    return (body.get('projid') == 'WF_YBX' and body.get('serial') == request['serial']
            and body.get('identity', {}).get('userid') == request['expected_userid']
            and body.get('target') == {k: request[k] for k in ['case_id', 'serial', 'group', 'subject', 'expected_userid', 'tab_id']})


def compact(source):
    return re.sub(r'\s+', '', source or '')


def validate_open_definition(definition):
    expected = {'btnguid': GUID, 'caption': '报销项详情', 'call_proc': True, 'check_func': CHECK,
                'procs': [PROC], 'pre_compute': False, 'pre_check': False, 'post_check': True,
                'acts': '', 'affects': '', 'jscontent': '', 'clickquery': False, 'customFunc': '',
                'hasIntf_beforeclick': False, 'hasIntf_afterclick': False}
    if any(definition.get(k) != value for k, value in expected.items()):
        raise ValueError('报销项原生入口定义改变或缺项')
    if compact(definition.get('beforeclick')) != 'function(op){}' or compact(definition.get('afterclick')) != 'function(op){$.page.open(41,282,null,null,{dialogshow:true});}':
        raise ValueError('报销项原生打开处理器尚未适配')


def bind_source(request, receipt, source):
    validate(request)
    observed = _body(receipt)
    if not same_target(request, observed) or observed.get('capture_complete') is not True:
        raise ValueError('来源检查须完整且同案、同账号、同单号、同分组和标签')
    if observed.get('window', {}).get('visible'):
        raise ValueError('报销项窗口已经打开，先检查原窗口，不再次打开')
    if observed.get('native_node') != '':
        raise ValueError('当前不是已确认的无流程草稿上下文')
    entries = observed.get('entries', [])
    if len(entries) != 1 or entries[0].get('visible') is not True:
        raise ValueError('须有唯一可见的本组报销金额入口')
    entry = entries[0]
    parts = str(entry.get('parameters', '')).split('&')
    if len(parts) != 3 or parts[0] != request['group'] or parts[1].strip() != request['subject'] or not parts[2].startswith(request['group'] + '!P!'):
        raise ValueError('原生入口的分组、科目或类型不符')
    if entry.get('title') != '可点击修改报销金额，修改的金额不能大于发票金额' or not re.fullmatch(r'[0-9,]+(?:\.[0-9]{1,2})?', str(entry.get('amount', ''))):
        raise ValueError('金额入口提示或显示金额改变')
    validate_open_definition(observed.get('open_definition', {}))
    path = Path(str(source.get('path', '')))
    if not path.is_absolute() or hashlib.sha256(path.read_bytes()).hexdigest() != source.get('sha256'):
        raise ValueError('来源回执路径或SHA不符')
    return {**request, 'source': source, 'entry': entry, 'open_definition': observed['open_definition']}


def validate_source(request):
    validate(request)
    if not request.get('source') or not request.get('entry'):
        raise ValueError('打开操作须先冻结独立原生来源')
    source = request['source']
    bound = bind_source({k: request[k] for k in ['schema', 'case_id', 'serial', 'group', 'subject', 'expected_userid', 'tab_id']},
                        json.loads(Path(source['path']).read_text()), source)
    if bound != request:
        raise ValueError('冻结请求与原生来源不一致')
    return request


def _amount(value, *, native=False):
    if not isinstance(value, str):
        raise ValueError('金额须为明确字符串')
    pattern = r'(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]{1,2})?' if native else r'[0-9]+(?:\.[0-9]{1,2})?'
    if not re.fullmatch(pattern, value):
        raise ValueError('金额格式不符')
    return Decimal(value.replace(',', ''))


def _source_ref(source):
    if not isinstance(source, dict) or not isinstance(source.get('path'), str):
        raise ValueError('须有明确来源路径和SHA')
    path = Path(source['path'])
    if not path.is_absolute() or not re.fullmatch(r'[a-f0-9]{64}', str(source.get('sha256', ''))):
        raise ValueError('来源路径或SHA不符')
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    if h.hexdigest() != source['sha256']:
        raise ValueError('来源SHA已改变')
    return dict(source)


def _save_definition(observed):
    buttons = [b for b in observed.get('buttons', []) if b.get('caption') == '保存' and b.get('winno') == '282'
               and b.get('visible') is True and b.get('disabled') is False and b.get('guid') == SAVE_GUID]
    definitions = [d['definition'] for d in observed.get('button_definitions', [])
                   if d.get('winno') == '282' and d.get('definition', {}).get('btnguid') == SAVE_GUID]
    if len(buttons) != 1 or len(definitions) != 1:
        raise ValueError('390保存定义或可见按钮不唯一')
    definition = definitions[0]
    expected = dict.fromkeys(BUTTON_KEYS, '')
    expected.update(btnguid=SAVE_GUID, caption='保存', pre_compute=False, pre_check=True,
                    call_proc=True, check_func='', procs=[SAVE_PROC], post_check=True,
                    acts=SAVE_ACTS, clickquery=False, hasIntf_beforeclick=False, hasIntf_afterclick=False)
    for key, value in expected.items():
        if key in ['beforeclick', 'afterclick']:
            if compact(definition.get(key)) != 'function(op){}':
                raise ValueError('390保存处理器变化')
        elif type(definition.get(key)) is not type(value) or definition.get(key) != value:
            raise ValueError('390保存定义变化或缺项：' + key)
    return definition


def _reduction_rows(request, observed):
    forms = [f for f in observed.get('forms', []) if f.get('funcno') == '390']
    if len(forms) != 1 or forms[0].get('visible') is not True or forms[0].get('multiRec') is not True:
        raise ValueError('须有唯一可见的完整390多行表单')
    form = forms[0]
    count = form.get('multiCount')
    if type(count) is not int or not 0 < count <= 100:
        raise ValueError('390行数未知或超出准备范围')
    inputs = form.get('inputs', [])
    ids = [i.get('id') for i in inputs]
    expected = {f'formWF_YBX_390_{field}_{i}' for i in range(count) for field in ROW_FIELDS}
    if len(ids) != len(set(ids)) or set(ids) != expected:
        raise ValueError('390字段重复、缺项或出现未核实字段')
    by_id = {i['id']: i for i in inputs}
    rows = []
    for index in range(count):
        fields = {field: by_id[f'formWF_YBX_390_{field}_{index}'] for field in ROW_FIELDS}
        value = {field: entry.get('value') for field, entry in fields.items()}
        if value['t-serial_no'] != request['serial'] or not re.fullmatch(r'[A-F0-9]{32}', str(value['t-tk_guid'])):
            raise ValueError('390逐票GUID或单号不符')
        numbers = re.findall(r'(?<![0-9])[0-9]{20}(?![0-9])', str(value['t-t_desc']))
        if len(numbers) != 1 or not re.fullmatch(r'[0-9]+', str(value['t-ord'])):
            raise ValueError('390票号或原生ord不唯一')
        face = _amount(value['t-invoice_amt'], native=True)
        current = _amount(value['t-t_amt'], native=True)
        if face <= 0 or current < 0 or current > face or _amount(value['t-invoice_amt_xs'], native=True) != face:
            raise ValueError('390票面、显示金额或当前金额矛盾')
        if fields['t-t_amt'].get('readonly') is not False or fields['t-t_amt'].get('disabled') is not False or fields['t-t_amt'].get('visible') is not True:
            raise ValueError('390当前金额不是已观察的可编辑字段')
        rows.append({'index': index, 'invoice_guid': value['t-tk_guid'], 'invoice_number': numbers[0],
                     'face_amount': f'{face:.2f}', 'current_amount': f'{current:.2f}', 'ord': value['t-ord'],
                     'serial': value['t-serial_no'], 'fields': fields})
    if len({r['invoice_guid'] for r in rows}) != count or len({r['invoice_number'] for r in rows}) != count:
        raise ValueError('390存在重复GUID或票号')
    return rows


def prepare_reduction(request, receipt, source, invoice_claims):
    """只读冻结完整逐票减报候选；不产生保存脚本、授权或持久化成功结论。

    invoice_claims须列出本窗口全部票，逐项含GUID、完整票号、票面、实报、
    amount_origin与source_refs。来源只校验已给定文件SHA，不从文件名猜金额。
    """
    validate_source(request)
    source = _source_ref(source)
    observed = _body(receipt)
    if json.loads(Path(source['path']).read_text()) != receipt:
        raise ValueError('检查内容与已校SHA的来源回执不同')
    window = observed.get('window', {})
    if (not same_target(request, observed) or observed.get('capture_complete') is not True
            or observed.get('native_node') != '' or window.get('owner_page') != 'WF_YBX_392'
            or window.get('winno') != '282' or window.get('visible') is not True
            or window.get('xtype') != request['entry']['parameters']):
        raise ValueError('准备须有同案同账号同单号同标签同分组的完整282独立检查')
    markers = [m for m in observed.get('markers', []) if m.get('marker', {}).get('request_digest') == digest(request)]
    if (len(markers) != 1 or receipt.get('tab_id') != request['tab_id']
            or not re.fullmatch(r'reimbursement-result-[a-f0-9]{32}', str(receipt.get('result_marker', '')))):
        raise ValueError('准备须保留唯一原打开意图及同标签独立检查标记')
    save_definition = _save_definition(observed)
    rows = _reduction_rows(request, observed)
    if (observed.get('entries') != [request['entry']]
            or sum((_amount(r['current_amount']) for r in rows), Decimal(0)) != _amount(request['entry']['amount'], native=True)):
        raise ValueError('390逐票当前金额与冻结的原生分组金额不闭合')
    if not isinstance(invoice_claims, list) or len(invoice_claims) != len(rows):
        raise ValueError('须显式列出窗口全部票，其他票不得省略或推定不变')
    claims = {}
    allowed = {'invoice_guid', 'invoice_number', 'face_amount', 'claim_amount', 'amount_origin', 'source_refs'}
    for claim in invoice_claims:
        if not isinstance(claim, dict) or set(claim) != allowed or claim.get('invoice_guid') in claims:
            raise ValueError('逐票申报字段缺失、未知或GUID重复')
        claims[claim['invoice_guid']] = claim
    if set(claims) != {r['invoice_guid'] for r in rows}:
        raise ValueError('逐票申报GUID与完整原生窗口不符')
    for row in rows:
        claim = claims[row['invoice_guid']]
        face, current, amount = _amount(row['face_amount']), _amount(row['current_amount']), _amount(claim['claim_amount'])
        if claim['invoice_number'] != row['invoice_number'] or _amount(claim['face_amount']) != face or not 0 <= amount <= current:
            raise ValueError('逐票票号、票面或减报金额与原生行不符')
        origin = claim['amount_origin']
        if origin not in ['filename_explicit_claim', 'document_content', 'invoice_face'] or origin == 'invoice_face' and amount != face:
            raise ValueError('减报须有明确实报来源，不能拿票面或未知文件名作减报依据')
        refs = claim['source_refs']
        if not isinstance(refs, list) or not refs:
            raise ValueError('逐票实报来源不得留空')
        for ref in refs:
            if not isinstance(ref, dict) or type(ref.get('page')) is not int or ref['page'] < 1:
                raise ValueError('逐票来源须有明确页码')
            _source_ref(ref)
        row.update(claim_amount=f'{amount:.2f}', change_amount=f'{current-amount:.2f}',
                   changed=amount != current, amount_origin=origin, source_refs=refs)
    reasons = []
    branch = observed.get('branch', {})
    raw = branch.get('tksbcodecnt')
    value_known = type(raw) is int and raw >= 0 or isinstance(raw, str) and bool(re.fullmatch(r'[0-9]+', raw))
    known = (value_known and branch.get('known') is True
             and branch.get('observed_type') == ('number' if type(raw) is int else 'string')
             and branch.get('save_target') == ('41' if int(raw) == 0 else '276'))
    driver = observed.get('native_button_driver')
    deferred = (branch == {'tksbcodecnt': '', 'observed_type': 'string', 'known': False, 'save_target': None}
                and observed.get('raw_branch_context') == [
                    {'key': key, 'present': False, 'data_property': False, 'type': 'undefined', 'value': None}
                    for key in ['61-TKSBCODECNT', 'WF_YBX$61-TKSBCODECNT']]
                and isinstance(driver, str)
                and hashlib.sha256(driver.encode()).hexdigest() == DEFERRED_BRANCH_DRIVER_SHA256)
    if not known and not deferred:
        reasons.append('native_tksbcodecnt_missing_or_unknown')
    elif known and int(raw) != 0:
        reasons.append('native_save_branch_276_not_adapted')
    return {'schema': REDUCTION_SCHEMA, 'state': 'blocked' if reasons else 'prepared_readonly',
            'target': observed['target'], 'source': source, 'request_digest': digest(request),
            'rows': rows, 'face_total': f'{sum((_amount(r["face_amount"]) for r in rows), Decimal(0)):.2f}',
            'current_total': f'{sum((_amount(r["current_amount"]) for r in rows), Decimal(0)):.2f}',
            'claim_total': f'{sum((_amount(r["claim_amount"]) for r in rows), Decimal(0)):.2f}',
            'change_total': f'{sum((_amount(r["change_amount"]) for r in rows), Decimal(0)):.2f}',
            'changed_invoice_guids': [r['invoice_guid'] for r in rows if r['changed']],
            'unchanged_invoice_guids': [r['invoice_guid'] for r in rows if not r['changed']],
            'change_required': any(r['changed'] for r in rows),
            'branch': branch, 'save_definition': save_definition, 'blocked_reasons': reasons, 'system_write': False,
            'branch_evaluation': 'after_native_save_proc' if deferred else 'observed_before_save',
            'native_button_driver': driver if deferred else None,
            'amount_save_implemented': True, 'amount_save_requested': False,
            'financial_amount_verified': False, 'max_save_attempts': 1, 'replay_allowed': False,
            'requires_native_single_save_and_fresh_persistent_readback': True}


JS_COMMON = r'''
 const req=REQUEST,vis=e=>!!e&&e.getClientRects().length>0;
 const target={case_id:req.case_id,serial:req.serial,group:req.group,subject:req.subject,expected_userid:req.expected_userid,tab_id:req.tab_id};
 const guard=()=>{if(location.origin!=='https://cwzh.ustc.edu.cn'||$.getProjID()!=='WF_YBX'||String($.UC.bindData('#0-userinfo.userid#'))!==req.expected_userid||String($.UC.bindData('#61-serialno#'))!==req.serial)throw Error('报销项窗口账号、项目或单号不符');};
 guard();
 const stable=v=>JSON.stringify(v,(_,x)=>x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.keys(x).sort().map(k=>[k,x[k]])):x);
 const definition=b=>Object.fromEntries(BUTTON_KEYS.map(k=>{const d=Object.getOwnPropertyDescriptor(b,k);if(d&&!('value'in d))throw Error('原生定义含访问器');const v=d?.value;return[k,typeof v==='function'?Function.prototype.toString.call(v):v??null];}));
 const openDefinition=()=>{const buttons=($.page.list.WF_YBX_41?.wins||[]).flatMap(w=>String(w.winno)==='41'?w.func_btns||[]:[]).filter(b=>b.btnguid===GUID);if(buttons.length!==1)throw Error('报销项原生按钮定义不唯一');return definition(buttons[0]);};
 const entries=()=>{const f=document.getElementById('iframepage_print'),text=f?.contentDocument?.body?.innerText;if(!vis(f)||typeof text!=='string'||!text.includes('报销单号：'+req.serial))return[];
  return[...f.contentDocument.querySelectorAll('[onclick]')].filter(e=>vis(e)&&e.tagName==='TD').flatMap(e=>{const onclick=e.getAttribute('onclick')||'',m=/^pageOpen_v1\('报销项详情','([^']+)'\)$/.exec(onclick);if(!m)return[];const parts=m[1].split('&');return parts.length===3&&parts[0]===req.group&&parts[1].trim()===req.subject?[{element:e,onclick,parameters:m[1],amount:(e.innerText||'').trim(),title:e.getAttribute('title'),visible:true}]:[];});};
 const entryData=entry=>Object.fromEntries(Object.entries(entry).filter(([k])=>k!=='element'));
 // 实测loadedButtons将窗口282绑定至WF_YBX_392；不从$.page.open参数推断页面归属。
 const windowState=()=>{const wins=($.page.list.WF_YBX_392?.wins||[]).filter(w=>String(w.winno)==='282'),content=document.getElementById('winWF_YBX_282');
  const roots=wins.map(w=>({winno:'282',content,element:content?.closest?.('.ui-dialog')||content}));
  return{loaded:wins.length===1,owner_page:'WF_YBX_392',winno:'282',main_winno:'282',definition:wins.length===1?wins[0]:null,visible:roots.some(x=>vis(x.element)),current_page:String($.page.currPage),xtype:String($.UC.bindData('#61-xtype#')||''),roots};};
 const markers=()=>Object.entries(window.__ustcExpenseAmount||{}).filter(([,m])=>stable(m.target)===stable(target)).map(([request_id,marker])=>({request_id,marker}));
'''


def common(request):
    validate(request)
    return JS_COMMON.replace('BUTTON_KEYS', json.dumps(BUTTON_KEYS)).replace('GUID', json.dumps(GUID)).replace('REQUEST', json.dumps(request, ensure_ascii=False))


def validate_reduction_plan(request, preparation):
    """保存前从冻结检查和原始依据重新计算，不信任外部传入的合计或行映射。"""
    if not isinstance(preparation,dict) or preparation.get('schema')!=REDUCTION_SCHEMA:
        raise ValueError('须引用逐票减报准备结果')
    source=_source_ref(preparation.get('source',{}))
    receipt=json.loads(Path(source['path']).read_text())
    keys=['invoice_guid','invoice_number','face_amount','claim_amount','amount_origin','source_refs']
    claims=[{k:r[k]for k in keys}for r in preparation.get('rows',[])]
    expected=prepare_reduction(request,receipt,source,claims)
    # 早期只读准备的能力展示位为false；它不授予写权限，保留已冻结回执及digest。
    if preparation.get('amount_save_implemented') is False:
        expected['amount_save_implemented']=False
    if expected!=preparation or expected['blocked_reasons'] or not expected['change_required']:
        raise ValueError('逐票减报准备内容变化、无须调整或原生分支尚未适配')
    return expected


def save_script(request, preparation, request_id):
    validate_reduction_plan(request,preparation)
    if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}',request_id):
        raise ValueError('减报保存须稳定请求号')
    return r'''(()=>{COMMON
 const plan=PLAN,requestId=REQUEST_ID,read=()=>INSPECT;
 const registry=window.__ustcExpenseAmount||(window.__ustcExpenseAmount={});
 if(registry[requestId])throw Error('已有减报保存意图，只读原结果，不重发');
 const expected=Object.fromEntries(plan.rows.flatMap(r=>Object.values(r.fields).map(f=>[f.id,{value:f.value,type:f.type,disabled:f.disabled,readonly:f.readonly,visible:f.visible}])));
 const check=()=>{
  guard();const o=read();
  if(!o.ok||!o.capture_complete||o.native_node!==''||!o.row_capture_complete||stable(o.target)!==stable(plan.target)||!o.window.visible||o.window.xtype!==req.entry.parameters||stable(o.entries)!==stable([req.entry])||stable(o.branch)!==stable(plan.branch))throw Error('保存前窗口、身份、金额入口或原生分支改变');
  if(plan.branch_evaluation==='after_native_save_proc'&&(o.native_button_driver!==plan.native_button_driver||String($.button?.btnClickFunc)!==plan.native_button_driver||o.raw_branch_context.some(c=>c.present)))throw Error('原生保存后分支驱动或未定义上下文改变');
  const definitions=o.button_definitions.filter(b=>b.winno==='282'&&b.definition.btnguid===SAVE_GUID);
  const buttons=o.buttons.filter(b=>b.winno==='282'&&b.guid===SAVE_GUID&&b.visible&&!b.disabled);
  if(definitions.length!==1||buttons.length!==1||stable(definitions[0].definition)!==stable(plan.save_definition))throw Error('保存按钮或原生定义改变');
  if(o.forms.length!==1||o.forms[0].multiCount!==plan.rows.length||o.forms[0].validation_errors.length||o.forms[0].inputs.length!==Object.keys(expected).length)throw Error('原生完整行数或校验状态改变');
  for(const f of o.forms[0].inputs){const e=expected[f.id];if(!e||['value','type','disabled','readonly','visible'].some(k=>f[k]!==e[k]))throw Error('逐票原字段或身份变化');}
  return o;
 };
 check();registry[requestId]={target,request_digest:REQUEST_DIGEST,phase:'editing_amounts',amount_save_requested:false,plan_digest:PLAN_DIGEST};
 for(const row of plan.rows.filter(r=>r.changed)){
  check();const id=row.fields['t-t_amt'].id,e=document.getElementById(id);expected[id].value=row.claim_amount;
  $(e).val(row.claim_amount).trigger('change').trigger('blur');check();
 }
 $.F.getDataToContext('390');check();
 const roots=windowState().roots.filter(r=>vis(r.element)),buttons=roots.flatMap(r=>[...r.element.querySelectorAll('button[btnname]')]).filter(b=>b.getAttribute('guid')===SAVE_GUID&&b.getAttribute('btnname')==='保存'&&vis(b)&&!b.disabled);
 if(buttons.length!==1)throw Error('最终保存按钮不唯一');
 registry[requestId].phase='save_requested';registry[requestId].amount_save_requested=true;
 buttons[0].click();registry[requestId].phase='save_button_returned';
 return{ok:true,schema:'ustc-expense-amount-save-v1',serial:req.serial,identity:{userid:req.expected_userid},target,request_id:requestId,request_digest:REQUEST_DIGEST,plan_digest:PLAN_DIGEST,group_claim_amount:plan.claim_total,system_write:true,amount_save_requested:true,financial_amount_verified:false,requires_independent_readback:true};
})()'''.replace('INSPECT',inspect_script(request)).replace('COMMON',common(request)).replace('PLAN_DIGEST',json.dumps(digest(preparation))).replace('REQUEST_DIGEST',json.dumps(digest(request))).replace('REQUEST_ID',json.dumps(request_id)).replace('SAVE_GUID',json.dumps(SAVE_GUID)).replace('PLAN',json.dumps(preparation,ensure_ascii=False))


def verify_saved_group(previous,receipt,outcome):
    verification=previous.get('verification',{});request=verification.get('request');plan=verification.get('preparation')
    validate_reduction_plan(request,plan)
    original=json.loads(Path(verification['action_output']).read_text());action=original.get('result',{})
    if (outcome!='saved' or verification.get('schema')!=PENDING_SCHEMA or action.get('ok')is not True
        or action.get('amount_save_requested')is not True or action.get('plan_digest')!=digest(plan)
        or action.get('request_digest')!=digest(request) or action.get('target')!=plan['target'] or action.get('serial')!=request['serial']
        or receipt.get('tab_id')!=request['tab_id'] or receipt.get('result_marker')==original.get('result_marker')):
        raise ValueError('减报只登记完整独立回读证实的保存；未知结果不解除')
    data=receipt.get('result',{})
    if data.get('ok')is not True or data.get('projid')!='WF_YBX' or data.get('serial')!=request['serial'] or data.get('identity',{}).get('userid')!=request['expected_userid'] or data.get('visibleDialogs'):
        raise ValueError('减报回读账号、单号或弹框状态不符')
    frames=[f for f in data.get('frames',[])if f.get('id')=='iframepage_print'];
    if len(frames)!=1:raise ValueError('减报独立预览不唯一')
    frame=frames[0];proof=frame.get('proof_inventory',{})
    if not proof.get('preview_complete')or not proof.get('scan_complete')or proof.get('truncated')is not False or proof.get('preview_serials')!=[request['serial']]:raise ValueError('减报独立预览不完整')
    controls=[c for c in frame.get('controls',[])if c.get('tag')=='TD' and c.get('onclick')==request['entry']['onclick']]
    if len(controls)!=1 or _amount(controls[0].get('text'),native=True)!=_amount(plan['claim_total']):raise ValueError('原分组保存金额未独立回读相符')
    return {'stage':'save','amount_save_requested':True,'group_total_verified':True,'group_claim_total':plan['claim_total'],
            'invoice_row_persistence_verified':False,'requires_reopen_row_verification':True,'financial_amount_verified':False}


def verify_saved_rows(request,receipt,preparation):
    """保存后重新打开的原生行逐票核对；只比较，不再填写或保存。"""
    validate_source(request);observed=_body(receipt)
    if (observed.get('target')!=preparation.get('target') or not same_target(request,observed)
        or not observed.get('capture_complete') or not observed.get('row_capture_complete')
        or not observed.get('window',{}).get('visible') or observed.get('native_node')!=''
        or _amount(request['entry']['amount'],native=True)!=_amount(preparation['claim_total'])
        or not any(m.get('marker',{}).get('request_digest')==digest(request) and m['marker'].get('phase')=='open_button_returned'for m in observed.get('markers',[]))):
        raise ValueError('须在已保存金额的原生预览重新打开同组，并取得完整独立行回读')
    rows=_reduction_rows(request,observed);expected={r['invoice_guid']:r for r in preparation['rows']}
    if len(rows)!=len(expected):raise ValueError('重新打开后的完整逐票行数不同')
    for row in rows:
        old=expected.get(row['invoice_guid'])
        if not old or any(row[k]!=old[k]for k in ['invoice_number','face_amount','ord','serial']) or row['current_amount']!=old['claim_amount']:
            raise ValueError('重新打开后的逐票身份或申报金额不同')
    return {'verified':True,'invoice_row_persistence_verified':True,'rows':[{'invoice_guid':r['invoice_guid'],'invoice_number':r['invoice_number'],'face_amount':r['face_amount'],'claim_amount':r['current_amount']}for r in rows],
            'claim_total':preparation['claim_total'],'system_write':False}


def verify_case_reductions(service, case_id, form, manifest, expenses, invoices, references):
    """批次只接正式保存及保存后重开行的注册回执；不接受手填verified标志。"""
    expected, mappings = {}, {}
    for expense in expenses:
        ids = expense.get('invoice_ids', [])
        if len(ids) != 1 or ids[0] in expected:
            raise ValueError('减报批次须为已核逐票费用，不推定多票之间的分配')
        invoice = invoices[ids[0]]; number = invoice['invoice_number']
        face, claim = _amount(invoice['face_amount']), _amount(expense['claim_amount'])
        if not 0 <= claim <= face or number in expected:
            raise ValueError('逐票核定额或票号冲突')
        expected[number] = {'face_amount': f'{face:.2f}', 'claim_amount': f'{claim:.2f}'}
        if claim < face:
            mappings[number] = expense.get('native_expense_allocations')
    selected = manifest.get('selected_invoices', [])
    if (len(selected) != len(expected) or {s['number'] for s in selected} != set(expected)
            or any(_amount(s['amount']) != _amount(expected[s['number']]['face_amount']) for s in selected)
            or sum((_amount(v['claim_amount']) for v in expected.values()), Decimal(0)) != _amount(form['amount'])):
        raise ValueError('选票原票面、核定逐票范围或总额不闭合')
    if not isinstance(references, list) or not references:
        raise ValueError('少报须登记原保存及重开行的正式任务引用')
    selected_by_number = {s['number']: s for s in selected}; verified = {}
    for ref in references:
        if not isinstance(ref, dict) or set(ref) != {'save_job_id','save_artifact_id','rows_job_id','rows_artifact_id'}:
            raise ValueError('减报回执引用结构不符')
        _, saved, save_job = service._typed_case_artifact(case_id, ref['save_job_id'], ref['save_artifact_id'], 'expense_amount', ['save'])
        _, rows, _ = service._typed_case_artifact(case_id, ref['rows_job_id'], ref['rows_artifact_id'], 'expense_amount', ['inspect'])
        save_folder = service.job_folder(case_id, ref['save_job_id']); row_folder = service.job_folder(case_id, ref['rows_job_id'])
        request = json.loads((save_folder/'expense_amount_request.json').read_text())
        preparation = json.loads((save_folder/'expense_amount_reduction.json').read_text())
        validate_reduction_plan(request, preparation)
        action = saved.get('result', {})
        if (save_job.get('execute') is not True or saved.get('phase') != 'script_completed' or action.get('ok') is not True
                or action.get('amount_save_requested') is not True or action.get('request_digest') != digest(request)
                or action.get('plan_digest') != digest(preparation) or action.get('serial') != form['serial']
                or request['case_id'] != case_id or request['serial'] != form['serial']
                or saved.get('browser_transport') != rows.get('browser_transport')
                or rows.get('created_at_utc','') <= saved.get('executed_at_utc','')):
            raise ValueError('须为本单真实保存之后的独立逐票持久回读')
        row_request = json.loads((row_folder/'expense_amount_request.json').read_text())
        proof = verify_saved_rows(row_request, rows, preparation)
        for row in proof['rows']:
            number = row['invoice_number']; wanted = expected.get(number); selected_row = selected_by_number.get(number)
            if (number in verified or not wanted or not selected_row or row['invoice_guid'] != selected_row['guid']
                    or any(row[k] != wanted[k] for k in ['face_amount','claim_amount'])):
                raise ValueError('持久行回读与已核原票、GUID或逐票金额不同')
            if number in mappings:
                parts = mappings[number]
                if (not isinstance(parts,list) or len(parts)!=1 or parts[0].get('native_expense_id')!=row['invoice_guid']
                        or _amount(parts[0]['amount'])!=_amount(row['claim_amount'])):
                    raise ValueError('正式计划的原生GUID分配与持久行回读不同')
            verified[number] = row
    if not set(mappings) <= set(verified):
        raise ValueError('存在未取得逐票持久回读的减报发票')
    return {'verified': True, 'reduced_invoice_numbers': sorted(mappings), 'claim_total': form['amount'], 'system_write': False}


def inspect_script(request, original_marker=None):
    from finance_invoice_verification import NATIVE_SERIALIZER_JS
    return r'''(()=>{COMMON
 const mask=(key,value)=>/password|passwd|token|session|captcha|authorization|cookie/i.test(key)?'[protected]':/account|bankacnt|cnt1|cardno|acnt/i.test(key)&&typeof value==='string'?value.replace(/\b\d{12,19}\b/g,m=>m.slice(0,4)+'****'+m.slice(-4)):value;
 SERIALIZER
 const state=windowState(),forms=[],buttons=[],buttonDefinitions=[];
 for(const [funcno,f]of Object.entries($.F?.list||{})){
  if(funcno!=='390'||String(f.winno)!=='282')continue;
  const id=$.F.idFunc.getFormID(funcno),element=document.getElementById(id);
  if(!element||!state.roots.some(r=>r.element?.contains(element)))continue;
  forms.push({funcno,id,title:f.title||'',multiRec:f.multiRec,multiCount:f.multiCount,visible:vis(element),loadedDefinition:nativeValue(f,'','metadata','forms.390.loadedDefinition'),fields:nativeValue(f.formFields||[],'','metadata','forms.'+funcno+'.fields'),
   validation_errors:($.F.validators?.[funcno]?.errorList||[]).map(e=>({id:e.element?.id,message:e.message})),
   inputs:[...element.querySelectorAll('input,select,textarea')].filter(e=>e.type!=='password').map(e=>({id:e.id,type:e.type,value:mask(e.id+' '+(e.name||''),e.value),visible:vis(e),disabled:!!e.disabled,readonly:!!e.readOnly,onclick:e.getAttribute('onclick'),onchange:e.getAttribute('onchange'),options:e.options?[...e.options].map(o=>({value:o.value,text:o.text,selected:o.selected})):null}))});
 }
 for(const win of ($.page.list.WF_YBX_392?.wins||[]).filter(w=>String(w.winno)==='282')){
  const root=state.roots.find(r=>r.winno===String(win.winno))?.element;
  const guids=new Set((win.func_btns||[]).map(b=>b.btnguid));
  for(const b of root?.querySelectorAll('button[btnname]')||[])if(guids.has(b.getAttribute('guid')))buttons.push({winno:String(win.winno),caption:b.getAttribute('btnname'),guid:b.getAttribute('guid'),visible:vis(b),disabled:!!b.disabled});
  for(const b of win.func_btns||[])buttonDefinitions.push({winno:String(win.winno),definition:definition(b)});
 }
 const currentEntries=entries().map(entryData),preview=document.getElementById('iframepage_print'),previewText=preview?.contentDocument?.body?.innerText||'';
 const rowFields=ROW_FIELDS,expenseRows=[];
 for(const form of forms){const count=Number.isSafeInteger(form.multiCount)&&form.multiCount>=0?Math.min(form.multiCount,100):0;
  for(let i=0;i<count;i++){const fields=Object.fromEntries(rowFields.map(name=>{const matches=form.inputs.filter(input=>input.id==='formWF_YBX_390_'+name+'_'+i);return[name,matches.length===1?matches[0]:null];}));
   expenseRows.push({index:i,fields,complete:rowFields.every(name=>fields[name]!==null)});}}
 const branchRaw=$.UC.bindData('#61-tksbcodecnt#'),branchKnown=typeof branchRaw==='number'?Number.isSafeInteger(branchRaw)&&branchRaw>=0:typeof branchRaw==='string'&&/^[0-9]+$/.test(branchRaw);
 const result={ok:true,schema:INSPECT_SCHEMA,observedAt:new Date().toISOString(),projid:'WF_YBX',serial:req.serial,identity:{userid:req.expected_userid},target,
  window:{loaded:state.loaded,owner_page:state.owner_page,winno:state.winno,main_winno:state.main_winno,visible:state.visible,current_page:state.current_page,xtype:state.xtype,
   roots:state.roots.map(r=>({winno:r.winno,content_id:r.content?.id||null,container_id:r.element?.id||null,container_class:r.element?.className||null,visible:vis(r.element)}))},
  window_definition:nativeValue(state.definition,'','metadata','WF_YBX_392.wins.282'),native_node:String($.UC.bindData('#61-node_id#')||''),
  entries:currentEntries,open_definition:openDefinition(),forms,buttons,button_definitions:buttonDefinitions,
  expense_rows:expenseRows,row_capture_complete:forms.length===1&&forms[0].multiRec===true&&Number.isSafeInteger(forms[0].multiCount)&&forms[0].multiCount>0&&forms[0].multiCount<=100&&expenseRows.length===forms[0].multiCount&&expenseRows.every(row=>row.complete),
  branch:{tksbcodecnt:typeof branchRaw==='string'||typeof branchRaw==='number'?branchRaw:null,observed_type:typeof branchRaw,known:branchKnown,save_target:branchKnown?(Number(branchRaw)===0?'41':'276'):null},
  raw_branch_context:['61-TKSBCODECNT','WF_YBX$61-TKSBCODECNT'].map(key=>{const d=$.UC.userData&&Object.getOwnPropertyDescriptor($.UC.userData,key);return{key,present:!!d,data_property:!!d&&'value'in d,type:typeof d?.value,value:typeof d?.value==='string'||typeof d?.value==='number'?d.value:null};}),
  native_button_driver:typeof $.button?.btnClickFunc==='function'?String($.button.btnClickFunc):null,
  preview:{visible:vis(preview),same_serial:previewText.includes('报销单号：'+req.serial)},markers:markers(),
  original_operation_result:ORIGINAL_MARKER?(()=>{const text=document.getElementById(ORIGINAL_MARKER)?.textContent;if(!text||text==='pending')return{state:text||'missing'};try{return JSON.parse(text);}catch{return{state:'unparseable'};}})():null,
  system_write:false,amount_save_implemented:true,amount_save_requested:false};
 result.capture_complete=serialization.omissionCount===0;
 return finalizeCapture(result);
})()'''.replace('SERIALIZER', NATIVE_SERIALIZER_JS).replace('ROW_FIELDS', json.dumps(ROW_FIELDS)).replace('INSPECT_SCHEMA', json.dumps(INSPECT_SCHEMA)).replace('ORIGINAL_MARKER', json.dumps(original_marker)).replace('COMMON', common(request))


def open_script(request, request_id):
    validate_source(request)
    return r'''(()=>{COMMON
 const requestId=REQUEST_ID,registry=window.__ustcExpenseAmount||(window.__ustcExpenseAmount={});
 if(registry[requestId])throw Error('原报销项打开意图已存在，只读恢复');
 if(String($.UC.bindData('#61-node_id#')||'').trim())throw Error('当前已有流程，不打开金额修改窗口');
 if(windowState().visible||[...document.querySelectorAll('.ui-dialog')].some(vis))throw Error('已有窗口，不能再次打开报销项');
 if(stable(openDefinition())!==stable(req.open_definition))throw Error('原生报销项定义已改变');
 const current=entries();if(current.length!==1||stable(entryData(current[0]))!==stable(req.entry))throw Error('可见分组金额入口或金额改变');
 registry[requestId]={target,request_digest:REQUEST_DIGEST,phase:'open_requested',amount_save_requested:false};
 current[0].element.click();registry[requestId].phase='open_button_returned';
 return{ok:true,schema:SCHEMA,action:'open',request_id:requestId,request_digest:REQUEST_DIGEST,serial:req.serial,identity:{userid:req.expected_userid},target,marker:registry[requestId],
  system_write:true,amount_save_requested:false,backend_effects_verified:false,requires_independent_readback:true};
})()'''.replace('REQUEST_ID', json.dumps(request_id)).replace('REQUEST_DIGEST', json.dumps(digest(request))).replace('SCHEMA', json.dumps(SCHEMA)).replace('COMMON', common(request))


def return_definition_safe(definition, winno):
    """仅识别无业务动作的原生关闭；新处理器留待真实检查，不按按钮文案猜。"""
    if definition.get('btnguid') == RETURN_GUID or definition.get('acts') == '1==1:41':
        expected = {'btnguid': RETURN_GUID, 'caption': '返回', 'pre_compute': False, 'pre_check': False,
                    'call_proc': False, 'check_func': '', 'procs': [], 'post_check': False, 'acts': '1==1:41',
                    'affects': '', 'jscontent': '', 'clickquery': False, 'clickqueryhint': '', 'customFunc': '',
                    'hasIntf_beforeclick': False, 'hasIntf_afterclick': False}
        return (str(winno) == '282' and all(type(definition.get(k)) is type(v) and definition[k] == v for k, v in expected.items())
                and compact(definition.get('beforeclick')) == 'function(op){}'
                and compact(definition.get('afterclick')) == 'function(op){}')
    if definition.get('caption') != '返回' or definition.get('call_proc') is not False or definition.get('procs') not in [[], None]:
        return False
    if any(definition.get(k) not in ['', False, None] for k in ['pre_compute', 'pre_check', 'post_check', 'check_func', 'acts', 'affects', 'jscontent', 'clickquery', 'customFunc', 'hasIntf_beforeclick', 'hasIntf_afterclick']):
        return False
    if compact(definition.get('beforeclick')) not in ['', 'function(op){}']:
        return False
    # 只接受单一关闭调用，无保存、刷新、HTTP、其他页面或额外表达式。
    return compact(definition.get('afterclick')) in {
        f'function(op){{$.page.close({winno});}}', f'function(op){{$.page.closeWin({winno});}}'}


def return_script(request, inspection, request_id):
    validate_source(request)
    observed = _body(inspection)
    if not same_target(request, observed) or not observed.get('capture_complete') or not observed.get('window', {}).get('visible') or observed['window'].get('owner_page') != 'WF_YBX_392' or observed['window'].get('winno') != '282' or observed['window'].get('xtype') != request['entry']['parameters']:
        raise ValueError('返回须引用同案同组的完整282窗口独立检查')
    marker = [m for m in observed.get('markers', []) if m.get('marker', {}).get('request_digest') == digest(request)]
    if len(marker) != 1:
        raise ValueError('返回须有唯一原打开意图，不接管其他窗口')
    buttons = [b for b in observed.get('buttons', []) if b.get('caption') == '返回' and b.get('visible') and not b.get('disabled')]
    definitions = [d for d in observed.get('button_definitions', []) if len(buttons) == 1 and d.get('winno') == buttons[0]['winno'] and d.get('definition', {}).get('caption') == '返回']
    if len(buttons) != 1 or len(definitions) != 1 or not return_definition_safe(definitions[0]['definition'], definitions[0]['winno']):
        raise ValueError('原生返回定义尚未证明仅关闭；保留窗口，先审查独立回执')
    fields = [i for f in observed.get('forms', []) for i in f.get('inputs', [])]
    return r'''(()=>{COMMON
 const state=windowState(),requestId=REQUEST_ID,registry=window.__ustcExpenseAmount||(window.__ustcExpenseAmount={});
 if(registry[requestId])throw Error('原返回意图已存在，只读恢复');
 if(!state.visible||state.xtype!==req.entry.parameters)throw Error('不是原报销项窗口');
 const original=registry[OPEN_ID];if(!original||original.request_digest!==REQUEST_DIGEST||stable(original.target)!==stable(target))throw Error('原打开标记不符');
 for(const field of FIELDS){const e=document.getElementById(field.id);if(!e||e.value!==field.value||!!e.disabled!==field.disabled||!!e.readOnly!==field.readonly)throw Error('检查后的窗口字段已改变，不丢弃修改');}
 const win=($.page.list.WF_YBX_392?.wins||[]).filter(w=>String(w.winno)===WINNO);const defs=win.flatMap(w=>w.func_btns||[]).filter(b=>b.caption==='返回');
 if(defs.length!==1||stable(definition(defs[0]))!==stable(RETURN_DEFINITION))throw Error('原生返回处理器已改变');
 const scope=state.roots.find(r=>r.winno===WINNO)?.element,buttons=[...(scope?.querySelectorAll('button[btnname="返回"]')||[])].filter(e=>vis(e)&&!e.disabled&&e.getAttribute('guid')===defs[0].btnguid);
 if(buttons.length!==1)throw Error('原生返回按钮不唯一');
 registry[requestId]={target,request_digest:REQUEST_DIGEST,phase:'return_requested',amount_save_requested:false};buttons[0].click();registry[requestId].phase='return_button_returned';
 return{ok:true,schema:SCHEMA,action:'return',serial:req.serial,identity:{userid:req.expected_userid},target,request_id:requestId,request_digest:REQUEST_DIGEST,system_write:true,amount_save_requested:false,requires_independent_readback:true};
})()'''.replace('REQUEST_ID', json.dumps(request_id)).replace('OPEN_ID', json.dumps(marker[0]['request_id'])).replace('REQUEST_DIGEST', json.dumps(digest(request))).replace('FIELDS', json.dumps(fields, ensure_ascii=False)).replace('WINNO', json.dumps(definitions[0]['winno'])).replace('RETURN_DEFINITION', json.dumps(definitions[0]['definition'], ensure_ascii=False)).replace('SCHEMA', json.dumps(SCHEMA)).replace('COMMON', common(request))


def verify_ack(previous, receipt, outcome):
    verification = previous.get('verification', {})
    request = verification.get('request')
    if verification.get('schema') != PENDING_SCHEMA or not request or outcome != 'staged':
        raise ValueError('报销项窗口仅可登记staged，不可登记金额saved或解除未知意图')
    validate_source(request)
    if previous.get('case_id') != request['case_id'] or previous.get('serial') != request['serial']:
        raise ValueError('原pending与窗口请求的案件或单号不符')
    observed = _body(receipt)
    if not same_target(request, observed) or not observed.get('capture_complete') or observed.get('window', {}).get('owner_page') != 'WF_YBX_392' or observed['window'].get('winno') != '282':
        raise ValueError('报销项窗口回读身份不符或存在缺项')
    action_output = Path(str(verification.get('action_output', '')))
    original = json.loads(action_output.read_text()) if action_output.is_file() else {}
    if (receipt.get('tab_id') != request['tab_id'] or not re.fullmatch(r'reimbursement-result-[a-f0-9]{32}', str(receipt.get('result_marker', '')))
            or receipt.get('result_marker') == original.get('result_marker')):
        raise ValueError('须使用同标签独立检查，不接受原动作回执')
    marker = [m for m in observed.get('markers', []) if m.get('request_id') == verification.get('request_id') and m.get('marker', {}).get('request_digest') == digest(request)]
    if len(marker) != 1:
        raise ValueError('独立检查缺少本次原生请求标记')
    action = previous['operation'].removeprefix('expense_amount:')
    if action == 'open':
        if not observed.get('window', {}).get('visible') or observed['window'].get('xtype') != request['entry']['parameters'] or not any(f.get('funcno') == '390' and f.get('visible') and f.get('inputs') for f in observed.get('forms', [])):
            raise ValueError('282窗口及本组xtype尚未独立回读完成')
    elif action == 'return':
        if observed.get('window', {}).get('visible') or not observed.get('preview', {}).get('visible') or not observed['preview'].get('same_serial') or observed.get('entries') != [request['entry']]:
            raise ValueError('返回后的同号原分组金额预览尚未独立核对')
    else:
        raise ValueError('未知报销项pending动作')
    return {'stage': action, 'amount_save_requested': False, 'financial_amount_verified': False}
