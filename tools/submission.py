"""显式授权的现有单据提交；每次只执行一个原生动作并独立回读。"""
import json
import re
from pathlib import Path
from validate_config import money

ACTIONS = ['inspect', 'status', 'status-readback', 'print-inspect', 'template-inspect', 'detail-open', 'detail-continue', 'detail-return', 'grid-export-menu', 'grid-export-execute', 'system-print-open', 'system-print-choice', 'system-print-read', 'system-print-dismiss', 'applicant-open', 'verification-cancel', 'applicant-return', 'dismiss-validation', 'delivery-open', 'options', 'next', 'promise', 'department', 'applicant-agree']
WRITE_ACTIONS = ['options', 'next', 'promise', 'department', 'applicant-agree']

def view_plan(form, expected_userid, source):
    """明细与列表导出复用同案绑定；最终批准仍明确关闭。

    submit_authorized沿用原动作验证合同，调用者只执行固定查看/导出动作。
    此计划不授予applicant-agree，也不替代打印的专属授权。
    """
    plan = {
        'case_id': form['case_id'], 'serial': form['serial'], 'amount': form['amount'],
        'summary': form['basic']['fields']['t-abst'],
        'phone': form['basic']['fields']['t-req_mobile'],
        'contract_exists': False, 'delegate_finance_print': False,
        'submit_authorized': True, 'applicant_agreement_authorized': False,
        'approval_opinion': '同意', 'expected_userid': expected_userid, 'source': source,
    }
    validate_plan(plan, form)
    return plan

def validate_plan(plan, form):
    if any(plan.get(k) != form.get(k) for k in ['case_id', 'serial']) or money(plan['amount']) != money(form['amount']):
        raise ValueError('提交计划案件、单号或金额不符')
    if plan.get('submit_authorized') is not True or not plan.get('source'):
        raise ValueError('提交须有本案用户明确授权')
    if plan.get('summary') != form['basic']['fields']['t-abst'] or plan.get('phone') != form['basic']['fields']['t-req_mobile']:
        raise ValueError('提交摘要或电话与本案已核验输入不符')
    if form.get('contract', {}).get('exists') is not False or plan.get('contract_exists') is not False:
        raise ValueError('本入口只支持已核对无合同的单据')
    if form.get('delivery', {}).get('originals_required') is True and plan.get('delegate_finance_print') is not False:
        raise ValueError('有原件交付必须不委托财务打印')
    if 'expected_userid' in plan and not re.fullmatch(r'[A-Za-z0-9]{2,40}', plan['expected_userid'] or ''):
        raise ValueError('提交账号必须是明确的原生工号')
    if 'department_approvers' in plan:
        choices = plan['department_approvers']
        funding = form.get('funding', [])
        if (not isinstance(choices, list) or len(choices) != len(funding)
                or any(not isinstance(a, dict) or set(a) != {'project', 'id', 'name', 'source'}
                       or a['project'] != f['code'] or not re.fullmatch(r'[A-Za-z0-9]{2,40}', a['id'] or '')
                       or not all(isinstance(a[k], str) and a[k].strip() for k in ('name', 'source'))
                       or (f.get('department_approver') and any(f['department_approver'].get(k) != a[k] for k in ('id', 'name')))
                       for a, f in zip(choices, funding))):
            raise ValueError('单位审批人计划须按本案经费逐项绑定原生工号、姓名和来源，不能覆盖已确认身份')
    return {'ok': True, 'case_id': form['case_id'], 'serial': form['serial'], 'system_write': False}

def script(form, plan, action, manifest=None):
    if action=='status-readback':
        route=script(form,plan,'status',manifest)
        reader=(Path(__file__).parent/'actions/read_current_state.js').read_text()
        return '(async()=>{await '+route+';return await '+reader+';})()'
    if action in ('detail-open','detail-continue','detail-return','grid-export-menu','grid-export-execute','system-print-open','system-print-choice','system-print-read','system-print-dismiss'):
        if not re.fullmatch(r'[A-Za-z0-9]{2,40}',plan.get('expected_userid') or ''):raise ValueError('系统打印须明确当前账号')
        if action not in ('system-print-read','detail-open','detail-continue','detail-return','grid-export-menu','grid-export-execute') and (plan.get('print_export_authorized') is not True or plan.get('include_invoice_attachments') is not False):
            raise ValueError('本入口只接受本案明确导出系统单且附件另行整理的授权')
        source=Path(__file__).with_suffix('.js').read_text()+'\n'+(Path(__file__).parent/'system_print.js').read_text()
        method={'detail-open':'details','detail-continue':'detailContinue','detail-return':'detailReturn','grid-export-menu':'exportMenu','grid-export-execute':'exportExecute','system-print-open':'open','system-print-choice':'choose','system-print-read':'read','system-print-dismiss':'dismiss'}[action]
        args=json.dumps(form,ensure_ascii=False)+','+json.dumps(plan,ensure_ascii=False)
        return '(async()=>{\n'+source+'\nReimbursementSubmission.configure('+args+');ReimbursementSystemPrint.configure('+args+');return await ReimbursementSystemPrint.'+method+'();})()'
    methods = {'inspect': 'inspect', 'status':'submissionStatus', 'print-inspect':'printInspect', 'template-inspect':'templateInspect', 'applicant-open':'applicantOpen', 'verification-cancel':'verificationCancel', 'applicant-return':'applicantReturn', 'dismiss-validation':'dismissValidation', 'delivery-open': 'deliveryOpen', 'options':'options', 'next':'next', 'promise':'promise', 'department':'department', 'applicant-agree':'applicantAgree'}
    if action not in methods: raise ValueError('未知提交动作')
    if action == 'applicant-open' and (not re.fullmatch(r'[A-Za-z0-9]{2,40}',plan.get('expected_userid') or '')
            or form.get('review',{}).get('workflow_status')!='applicant_review_pending'):
        raise ValueError('只打开本案已核申请人待审批任务，不进入其他审批角色')
    if action == 'promise' and form.get('review', {}).get('workflow_started'):
        raise ValueError('普通承诺已发起；核对原流程，不重复提交')
    if action == 'applicant-agree':
        if (plan.get('applicant_agreement_authorized') is not True or plan.get('approval_opinion') != '同意'
                or not re.fullmatch(r'[A-Za-z0-9]{2,40}', plan.get('expected_userid') or '')
                or form.get('review', {}).get('workflow_status') != 'applicant_review_pending'):
            raise ValueError('本人同意仅支持本案明确授权、当前申请人508节点及意见同意')
    config = dict(form)
    if action == 'status' and not re.fullmatch(r'[A-Za-z0-9]{2,40}', plan.get('expected_userid') or ''):
        raise ValueError('提交状态导航须明确当前账号')
    if action == 'next':
        if not isinstance(manifest, dict) or any(manifest.get(k) != form.get(k) for k in ('case_id', 'serial')):
            raise ValueError('下一步须绑定本案原发票清单，分别核对票面额和申请额')
        invoices = manifest.get('selected_invoices', [])
        if (len(invoices) != form['expected_invoice_count'] or len(invoices) != manifest.get('expected_count')
                or any(not row.get('guid') for row in invoices)
                or len({row['guid'] for row in invoices}) != len(invoices)):
            raise ValueError('本案原发票清单数量或原生GUID不完整')
        total = sum((money(row['amount']) for row in invoices), money('0'))
        if total != money(manifest['expected_amount']) or total < money(form['amount']):
            raise ValueError('原发票票面合计不闭合或少于申请额')
        config['submission_invoice_face_total'] = str(total)
    source = Path(__file__).with_suffix('.js').read_text()
    if action == 'status':
        from finance_open_draft import navigation_script
        face = manifest.get('expected_amount') if isinstance(manifest, dict) and manifest.get('case_id') == form['case_id'] and manifest.get('serial') == form['serial'] else form['amount']
        registration = {'case_id':form['case_id'],'serial':form['serial'],'amount':form['amount'],
                        'invoice_face_amount':face,'summary':form['basic']['fields']['t-abst']}
        route = navigation_script(plan['expected_userid'],[registration],view='in-progress')
        source += '\nconst savedPreview=document.getElementById("iframepage_print");if(savedPreview&&savedPreview.getClientRects().length&&$.UC.bindData("#61-serialno#")==='+json.dumps(form['serial'])+'&&![...document.querySelectorAll(\'.foot_winWF_YBX_76 button[btnname="同意"]\')].some(e=>e.getClientRects().length)){return await '+route+';}\n'
    if action == 'department':
        funding = [dict(f) for f in form.get('funding', [])]
        if 'department_approvers' in plan:
            validate_plan(plan, form)
            for f, a in zip(funding, plan['department_approvers']):
                f['department_approver'] = {k: a[k] for k in ('id', 'name', 'source')}
        if not funding or any(not all(f.get('department_approver', {}).get(k) for k in ['id', 'name', 'source']) for f in funding):
            raise ValueError('单位审批人须有本案姓名、原生候选工号和确认来源')
        source = (Path(__file__).parent/'browser_automation.js').read_text()+'\n'+source
        config['funding'] = funding
    return '(async()=>{\n'+source+'\nReimbursementSubmission.configure('+json.dumps(config,ensure_ascii=False)+','+json.dumps(plan,ensure_ascii=False)+');return await ReimbursementSubmission.'+methods[action]+'();})()'

def _proof_groups(fields):
    """每个原生单选组必须带完整所属行；缺失来源不能默认为合同。"""
    grouped = {}
    for field in fields:
        name = field.get('name', '')
        if not name.startswith('form_proof_choose'):
            continue
        row = field.get('proof_row')
        cells = row.get('cells') if isinstance(row, dict) else None
        if (not isinstance(row, dict) or row.get('complete') is not True
                or not isinstance(cells, list) or len(cells) != 4
                or any(not isinstance(cell, str) for cell in cells)
                or not cells[0] or not cells[1]
                or [row.get(key) for key in ('type', 'description', 'note')] != cells[:3]
                or not isinstance(row.get('text'), str)
                or re.sub(r'\s+', '', row['text']) != re.sub(r'\s+', '', ''.join(cells))):
            raise ValueError('辅助证据radio缺少完整原生行类型/描述，不能默认作合同')
        if field.get('type') != 'radio':
            raise ValueError('辅助证据选项不是原生radio')
        group = grouped.setdefault(name, {'name': name, 'row': row, 'fields': []})
        if group['row'] != row:
            raise ValueError('同组辅助证据的原生行元数据不一致')
        group['fields'].append(field)
    for group in grouped.values():
        rows = group['fields']
        if (len(rows) != 2 or sorted(field.get('value', '') for field in rows) != ['N', 'Y']
                or sum(field.get('checked') is True for field in rows) != 1):
            raise ValueError('辅助证据原生Y/N组选项不完整或已选项不唯一')
    return list(grouped.values())


def _proof_inventory(frame, serial, groups):
    """完整同号预览和全量扫描闭合后，才有资格判定合同控件不适用。"""
    inventory = frame.get('proof_inventory')
    tight = re.sub(r'\s+', '', frame.get('visibleText', ''))
    serials = re.findall(r'报销单号[:：]([0-9]+)', tight)
    sections = {'title': '中国科学技术大学报销单' in tight, 'serial': serials == [serial],
                'basic': '基本信息' in tight, 'expenses': '报销项信息' in tight, 'payments': '支付信息' in tight,
                'invoices': '票据一览' in tight, 'promise': '报销承诺' in tight,
                'footer': '财务审批意见' in tight and '会计审核' in tight,
                'ticket_summary': bool(re.search(r'共选择[0-9]+张发票[,，、]?合计[0-9,.]+元', tight))}
    counts = ('table_count', 'row_count', 'input_count', 'captured_input_count', 'proof_control_count',
              'proof_radio_count', 'proof_group_count', 'contract_row_count', 'contract_text_count',
              'contract_bound_text_count', 'contract_unbound_text_count')
    if (not isinstance(inventory, dict) or inventory.get('schema') != 'ustc-native-proof-inventory-v1'
            or inventory.get('preview_complete') is not True or inventory.get('scan_complete') is not True
            or inventory.get('truncated') is not False or inventory.get('frame_id') != 'iframepage_print'
            or frame.get('id') != 'iframepage_print' or inventory.get('serial') != serial
            or inventory.get('preview_serials') != [serial] or serials != [serial]
            or inventory.get('frame_url') != 'https://cwzh.ustc.edu.cn/YBX/redirectCustomPage.action'
            or inventory.get('frame_url') != frame.get('url')
            or inventory.get('document_ready_state') != 'complete' or inventory.get('frame_visible') is not True
            or inventory.get('sections') != sections or not all(sections.values())
            or any(type(inventory.get(key)) is not int or inventory[key] < 0 for key in counts)):
        raise ValueError('缺少完整同号原生预览proof_inventory，不能推定合同控件不适用')
    fields = frame.get('fields', [])
    proof_fields = [field for field in fields if field.get('name', '').startswith('form_proof_choose')]
    tables = frame.get('tables', [])
    contracts = [group for group in groups if re.fullmatch(r'合同号(?:\s*\[[\s\S]*\])?', group['row']['type'])]
    native_contracts = [row for table in tables for row in table
                        if row and re.fullmatch(r'合同号(?:\s*\[[\s\S]*\])?', row[0])]
    rows = inventory.get('contract_rows')
    if (inventory['table_count'] != len(tables) or not tables
            or inventory['row_count'] != sum(len(table) for table in tables) or inventory['row_count'] < 1
            or inventory['input_count'] < inventory['captured_input_count']
            or inventory['captured_input_count'] != len(fields)
            or inventory['proof_control_count'] != len(proof_fields)
            or inventory['proof_radio_count'] != len(proof_fields)
            or inventory['proof_group_count'] != len(groups)
            or inventory['contract_row_count'] != len(contracts) or len(native_contracts) != len(contracts)
            or not isinstance(rows, list) or len(rows) != len(contracts)
            or inventory['contract_text_count'] != tight.count('合同号')
            or inventory['contract_bound_text_count'] != sum(''.join(row).count('合同号') for row in native_contracts)
            or inventory['contract_unbound_text_count'] != 0
            or inventory['contract_text_count'] != inventory['contract_bound_text_count']
            or inventory['contract_text_count'] < len(contracts)):
        raise ValueError('辅助证据全量清点计数或合同radio提取不闭合')
    covered = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('合同原生行清点不是完整对象')
        group = next((group for group in contracts if row.get('control_names') == [group['name']]), None)
        if (group is None or group['name'] in covered or row.get('type') != group['row']['type']
                or row.get('cell_count') != 4 or row.get('control_count') != 2 or row.get('radio_count') != 2):
            raise ValueError('合同类型行未完整提取原生radio，不能放行')
        covered.add(group['name'])
    if not contracts and inventory['contract_text_count']:
        raise ValueError('预览有合同号文本但无对应radio，不能推定不适用')
    return inventory, contracts


def verify_options(result, serial):
    frames = [f for f in result.get('frames', []) if '报销单号：'+serial in f.get('visibleText', '')]
    if len(frames) != 1: raise ValueError('选项核对须同号唯一单据预览')
    fields = frames[0].get('fields', [])
    prints = [e for e in fields if e.get('name') == 'form_grantcwprint']
    if (len(prints) != 2 or any(e.get('type') != 'radio' for e in prints)
            or sorted(e.get('value', '') for e in prints) != ['N', 'Y']
            or [e['value'] for e in prints if e.get('checked') is True] != ['N']):
        raise ValueError('合同无或委托打印否未独立回读，不能登记')
    groups = _proof_groups(fields)
    inventory, contracts = _proof_inventory(frames[0], serial, groups)
    if any(not re.fullmatch(r'共\s*0\s*笔', group['row']['description'])
           or [e['value'] for e in group['fields'] if e.get('checked') is True] != ['N'] for group in contracts):
        raise ValueError('明确合同号组须共0笔且选择否；其他辅助业务不能冒充合同')
    others = [{'name': group['name'], **group['row'],
               'checked_value': next(e['value'] for e in group['fields'] if e.get('checked') is True),
               'handled': False, 'status': 'requires_case_review'}
              for group in groups if group not in contracts]
    gaps = [row for row in others if re.search(r'错误|尚未|未绑定', row['note'])]
    return {'ok': True, 'contract_exists': False, 'delegate_finance_print': False,
            'contract_control_present': bool(contracts),
            'contract_control_status': 'present_confirmed_no_contract' if contracts else 'not_applicable_in_complete_preview',
            'proof_inventory': inventory,
            'verified_scope': 'contract_and_finance_print_only',
            'contract_groups_verified': len(contracts), 'other_auxiliary_rows': others,
            'material_gaps': gaps}

def verify_next(result, serial):
    frames = [f for f in result.get('frames', []) if '报销单号：'+serial in f.get('visibleText', '')]
    # 新草稿的承诺书为主页面700表单，此时原单预览iframe已关闭。
    text=result.get('visibleText','')
    native_promise=(result.get('serial')==serial and result.get('projid')=='WF_YBX'
        and any(str(f.get('funcno'))=='700' and f.get('title')=='承诺书' for f in result.get('forms',[]))
        and '本人郑重承诺' in text and '已阅读遵守且提交' in text and '上一步' in text)
    if not native_promise:
        raise ValueError('原生下一步后未到本人承诺页面')
    return {'ok': True, 'promise_page': True}

def verify_applicant_ready(result, serial, expected_userid):
    if (not expected_userid or result.get('serial') != serial or result.get('projid') != 'WF_YBX'
            or str(result.get('node')) != '508' or result.get('identity', {}).get('userid') != expected_userid
            or not any(str(f.get('funcno')) == '109' and f.get('title') == '审批意见填写' for f in result.get('forms', []))):
        raise ValueError('须独立回读同号、同账号的申请人508审核节点')
    rows = [row for grid in result.get('grids', []) if grid.get('visible') and str(grid.get('funcno')) == '1816'
            and not grid.get('loading') for row in grid.get('rows', [])
            if str(row.get('t.node_id')) == '508' and row.get('t.node_name') == '预约流程-申请人'
            and str(row.get('t.appinfo', '')).startswith(expected_userid+'-') and not row.get('t.operator')]
    if len(rows) != 1:
        raise ValueError('申请人待审日志未唯一绑定当前登录人')
    return {'ok': True, 'applicant_review_pending': True, 'node': '508', 'submitted_to_approval_list': False}


def verify_missing_supplement_rejection(result, plan):
    dialogs=result.get('visibleDialogs',[])
    if (result.get('projid')!='WF_YBX' or result.get('serial')!=plan['serial']
            or result.get('identity',{}).get('userid')!=plan['expected_userid']
            or str(result.get('node') or '') not in ('','508','535') or len(dialogs)!=1
            or not re.fullmatch(r'提示信息\s*Close\s*还有(?:[1-9][0-9]*个补充说明未填写|资产购置清单\s*等[1-9][0-9]*个辅助证据未关联明细)！\s*确认',dialogs[0].get('text',''))
            or '已阅读遵守且提交' in result.get('visibleText','')):
        raise ValueError('不是同号下一步被明确缺补充说明拦截的原生回读')
    frames=[f for f in result.get('frames',[]) if f.get('id')=='iframepage_print' and f.get('proof_inventory',{}).get('preview_complete') is True
            and f.get('proof_inventory',{}).get('serial')==plan['serial']]
    if len(frames)!=1:raise ValueError('缺说明拒绝须保留完整同号原单预览')
    totals=re.findall(r'金额总计[:：]\s*([0-9,.]+)',frames[0].get('visibleText',''))
    if len(totals)!=1 or money(totals[0].replace(',',''))!=money(plan['amount']):
        raise ValueError('被拦截原单金额与提交计划不同')
    from native_material_requirements import material_gaps
    gaps=material_gaps(frames[0])
    if not gaps:raise ValueError('未从原单定位到具体必填说明或附件缺项')
    return {'ok':True,'next_not_applied':True,'material_gaps':gaps,'native_message':dialogs[0]['text']}


def verify_sms_gate(result, serial, userid):
    ready=verify_applicant_ready(result,serial,userid)
    dialogs=result.get('visibleDialogs',[]);inputs=result.get('visibleInputs',[])
    if (len(dialogs)!=1 or not dialogs[0].get('text','').startswith('验证码\n')
            or not any(str(f.get('funcno'))=='1362' for f in result.get('forms',[]))
            or not {'formWF_YBX_1362_t-tel','formWF_YBX_1362_t-code'}.issubset({x.get('id') for x in inputs})
            or not {'确定','取消'}.issubset({b.get('btnname') for b in dialogs[0].get('buttons',[])})):
        raise ValueError('未独立读到本单申请人508的原生1362短信前置校验')
    return {**ready,'verification_required':True,'agreement_procedure_started':False}


def verify_applicant_prepared_list(result, serial, userid, amount):
    if result.get('projid')!='WF_YBX' or result.get('identity',{}).get('userid')!=userid:
        raise ValueError('待本人批准列表账号或项目不同')
    rows=[r for g in result.get('grids',[]) if g.get('visible') and not g.get('loading')
          for r in g.get('rows',[]) if str(r.get('t.serial_no','')).strip()==serial]
    if len(rows)!=1 or money(str(rows[0].get('t.total_amt','')).replace(',',''))!=money(amount):
        raise ValueError('待本人批准列表未唯一读到同号同金额原单')
    row=rows[0]
    if str(row.get('t.node_id'))!='508' or not str(row.get('t.REQ_INFO',row.get('t.req_info',''))).startswith(userid+'-'):
        raise ValueError('同号原单未证实处于当前本人申请人508节点')
    return {'ok':True,'applicant_ready_verified':True,'approval_completed':False,'payment_completed':False}


def verify_department(result, serial, expected, amount=None, expected_userid=None):
    if result.get('serial') not in [None, '', serial] or result.get('projid') != 'WF_YBX':
        raise ValueError('审批人保存回读单号或系统不符')
    if str(result.get('node')) == '508':
        if not expected or any(not x.get('id') or not x.get('name') for x in expected):
            raise ValueError('审批人保存回执缺少明确候选')
        return {**verify_applicant_ready(result, serial, expected_userid), 'department_approvers_requested': expected}
    if '待审批' not in result.get('visibleText', ''):
        raise ValueError('审批人保存后未回到待审批列表')
    if not expected or any(not x.get('id') or not x.get('name') for x in expected):
        raise ValueError('审批人保存回执缺少明确候选')
    rows=[row for grid in result.get('grids', []) if not grid.get('loading') for row in grid.get('rows', []) if str(row.get('t.serial_no', '')).strip()==serial]
    if len(rows)!=1:
        raise ValueError('待审批列表须独立读到唯一同号单据，不能只看页面标题')
    row=rows[0]
    statuses=[str(row.get(k,'')) for k in ['t.status_name','t.status_name_dtl','t.status_code_name','t.flow_status']]
    if not any('待审批' in value or '审批中' in value for value in statuses):
        raise ValueError('同号单据未明确显示待审批或审批中')
    if amount is not None and money(str(row.get('t.total_amt','')).replace(',',''))!=money(amount):
        raise ValueError('待审批同号单据金额不同')
    return {'ok': True, 'department_approvers_requested': expected, 'submitted_to_approval_list': True, 'observed_status': statuses}


def verify_applicant_agreement(result, serial, expected_userid, amount, expected_prover=None):
    if result.get('projid') != 'WF_YBX' or result.get('identity', {}).get('userid') != expected_userid:
        raise ValueError('本人同意后回读系统或账号变化')
    rows = [row for grid in result.get('grids', []) if grid.get('visible') and not grid.get('loading')
            for row in grid.get('rows', []) if str(row.get('t.serial_no', '')).strip() == serial]
    if len(rows) != 1 or money(str(rows[0].get('t.total_amt', '')).replace(',', '')) != money(amount):
        raise ValueError('本人同意后须独立读到唯一同号、同金额单据')
    row = rows[0]
    statuses = [str(row.get(k, '')) for k in ('t.status_name', 't.status_name_dtl', 't.status_code_name', 't.flow_status')]
    native_prover = (row.get('t.status') == '-2' and row.get('t.node_id') == '449'
                    and row.get('t.project_id') == 'WF_YBX' and row.get('t.wf_name') == 'YBX_LC'
                    and row.get('t.basetable_key') == serial and str(row.get('t.uni_key', '')).isdigit()
                    and str(row.get('t.REQ_INFO', '')).startswith(expected_userid+'-')
                    and bool(expected_prover) and row.get('t.status_name') == '证明人/验收人['+expected_prover+']')
    if (not native_prover and not any('审批中' in status or '待审批' in status for status in statuses)) or any('申请人' in status for status in statuses):
        raise ValueError('同号原单尚未证实离开申请人节点进入后续审批')
    return {'ok': True, 'applicant_agreement_completed': True, 'submitted_to_approval_list': True, 'observed_status': statuses,
            'next_node': row.get('t.node_id'), 'leadership_approval_completed': False, 'payment_completed': False}

def verify_promise(result, serial):
    forms = {str(f.get('funcno')): f for f in result.get('forms', [])}
    if result.get('serial') != serial or result.get('projid') != 'WF_YBX' or '1369' not in forms or '请选择单位审批人' not in result.get('visibleText', ''):
        raise ValueError('本人承诺后未进入单位审批人选择环节')
    return {'ok': True, 'promise_submitted': True, 'department_approver_stage': True}
