"""固定任务入口与私有规范库适配；只调用已声明工具，不开放任意文件或脚本。"""
import hashlib
import copy
import json
from pathlib import Path
import sys
from assistant_contract import GuidanceError, CASE_PROFILES, CAPABILITY_INTENTS, REGISTERED_PHASES
# 旧导入继续有效；规则库读写只由reimbursement_standards持有。
from reimbursement_standards import (
    standards_read as standards_read, standards_init as standards_init,
    standards_add_profile as standards_add_profile, standards_change as standards_change,
    standards_select, _library_entry as _library_entry,
)


def capabilities(intent='materials'):
    if intent not in CAPABILITY_INTENTS:
        raise GuidanceError('unknown_intent', 'intent须为materials、rules、ocr、finance或export')
    if intent in {'materials', 'rules', 'ocr'}:
        # 离线工作不需要用户浏览器注册、主目录资料或运行中的财务请求。
        platform = {'os': sys.platform, 'backend': 'not_probed', 'bridge_connected': None,
                    'registration_checked': False, 'bridge_status_checked': False,
                    'browser_started': False, 'page_script_executed': False,
                    'reason': 'offline_intent_no_browser_probe', 'active_request_id': None}
    else:
        from windows_bridge_setup import capabilities as platform_capabilities
        platform = platform_capabilities()
    blocked = platform['backend'] == 'unsupported' or (platform['os'] == 'win32' and not platform['bridge_connected'])
    next_steps = {
        'ocr': {'tool': 'reimbursement_ocr_status', 'arguments': {}, 'reason': '先确认OCR提供方，再从本案收件job选择缺文字页；不重复识别已有原生文字'},
        'materials': {'tool': 'reimbursement_guide', 'arguments': {'topic': 'intake'},
                      'reason': '先归档本批原件和来源，再选择专项，用prepare_batch_file检查分单及未决字段'},
        'rules': {'tool': 'reimbursement_standards_read', 'arguments': {}, 'needed_inputs': ['library_root'],
                  'reason': '列出已登记专项，不猜老师或经费对应关系'},
        'finance': {'tool': 'reimbursement_cases', 'arguments': {},
                    'reason': '选择明确案件，再overview、tabs和access只读核对现场'},
        'export': {'tool': 'reimbursement_cases', 'arguments': {},
                   'reason': '确认单号、当前PDF与导出日期和序号；预览不冒充正式打印件'},
    }
    if intent in {'finance', 'export'} and blocked:
        next_steps[intent] = {'tool': 'reimbursement_guide', 'arguments': {'topic': 'devices'},
                              'reason': '先完成本机正式桥接线；不使用其他浏览器工具替代'}
    recovering = intent in {'finance', 'export'} and bool(platform.get('active_request_id'))
    if recovering:
        next_steps[intent] = {'tool': 'reimbursement_overview', 'arguments': {}, 'needed_inputs': ['case_id'],
                              'reason': '桥中仍有原请求，先查本案任务和result/read_system；不新增tabs或重发业务'}
    return {'ok': True, 'schema': 'ustc-reimbursement-entry-v1', 'intent': intent, 'platform': platform,
            'system_write': False, 'status': 'needs_recovery' if recovering else 'needs_setup' if intent in {'finance', 'export'} and blocked else 'ready_for_discovery',
            'needed_inputs': next_steps[intent].get('needed_inputs', []), 'next_action': next_steps[intent],
            'parameter_cli': {'launcher': 'tools/reimbursement.cmd' if platform['os']=='win32' else 'bash tools/reimbursement.sh',
                'guide':'docs/助手快速开始.md',
                'commands': [{'name':'materials','default':'read_only_preview','inputs':['case-id','source','output-dir','group-strip-dirs']},
                             {'name':'workflow','default':'status','phases':['status','run','deliver','all','prepare-submit','submit'],
                              'inputs':['scenario','batch-index'],'submission_inputs':['submission-authorization','expected-userid','tab-id'],
                              'registered_case_inputs':['case-id'],
                              'registered_case_phases':list(REGISTERED_PHASES),
                              'registered_execute_default':'ready_to_applicant_pending_not_final_agreement',
                              'timeout_policy':'independent_saved_readback_before_resume'}],
                'note':'参数命令复用同一正式实现；不替代专项核定、本人验证或尚未验收的PDF通道'},
            'workflow': [
                {'step': '收件归档', 'tools': ['reimbursement_material_intake'], 'writes_finance': False},
                {'step': 'OCR与文字回接', 'tools': ['reimbursement_ocr_status', 'reimbursement_material_ocr', 'reimbursement_material_ocr_resume'], 'writes_finance': False},
                {'step': '规范', 'tools': ['reimbursement_standards_read', 'reimbursement_standards_select', 'reimbursement_standards_change']},
                {'step': '材料与分单', 'tools': ['reimbursement_prepare_batch_file', 'reimbursement_prepare_batch', 'reimbursement_batch_plan'], 'writes_finance': False},
                {'step': '选择案件', 'tools': ['reimbursement_cases', 'reimbursement_overview']},
                {'step': '核对现场', 'tools': ['reimbursement_tabs', 'reimbursement_access']},
                {'step': '上传、选票、建单', 'tools': ['reimbursement_invoice', 'reimbursement_invoice_selection', 'reimbursement_stage'],
                 'requires': '本案身份、材料、金额与动作授权；未知结果先result/read_system'},
                {'step': '提交与交付', 'tools': ['reimbursement_submission', 'reimbursement_read_system', 'reimbursement_pdf_export', 'reimbursement_pdf_export_inspect'],
                 'requires': '本人校验、审批、正式PDF和投递分别核实'}],
            'usage_groups': [
                {'id': 'intake', 'title': '收件与归档', 'entry': 'material_intake', 'finance_write': False},
                {'id': 'ocr', 'title': '扫描识别与原件关联', 'entry': 'material_ocr', 'finance_write': False},
                {'id': 'rules', 'title': '通用与专项规范', 'entry': 'standards_read', 'first_use': 'standards_init', 'finance_write': False},
                {'id': 'planning', 'title': '材料与分单', 'entry': 'prepare_batch_file', 'finance_write': False},
                {'id': 'finance', 'title': '财务办理与恢复', 'entry': 'cases → overview → enqueue', 'requires_local_adapter': True},
                {'id': 'delivery', 'title': '明细PDF与列表交付', 'entry': 'workflow export-system / export-list', 'distinct_actions': True},
            ],
            'scenario_modes': list(CASE_PROFILES),
            'rule_composition': {'field': 'extends', 'selection': 'explicit_registered_profile',
                                 'conflict_policy': 'block_without_sourced_exception', 'legacy_inherits_is_metadata': True},
            'note': 'ready_for_discovery仅表示可继续发现与检查，不表示已登录、可报销或取得写入授权'}


def batch_plan(service, data, export_root=None):
    from reimbursement_batch_plan import build_plan
    if export_root is not None:
        if not isinstance(export_root, str) or not Path(export_root).is_absolute():
            raise GuidanceError('invalid_output_path', '导出根目录须使用本机绝对路径')
        target = Path(export_root).resolve()
        if not any(target.is_relative_to(root) for root in service.material_roots):
            raise GuidanceError('material_root', '导出根目录须位于明确授权的material-root内')
    result = build_plan(data, export_root=export_root)
    result['ok'] = result.get('status') == 'ready'
    return result


def prepare_batch(service, library_root, profile_id, context, as_of, data, export_root=None):
    """把已选规则转换成受支持的规划参数，调用者不用手工重写专项规则。"""
    selection = standards_select(service, library_root, profile_id, context, as_of)
    if selection['status'] != 'ready':
        return {'ok': False, 'status': 'blocked', 'rule_selection': selection, 'plan': None,
                'needed_inputs': selection.get('issues', []), 'next_action': selection.get('next_action'), 'system_write': False}
    if not isinstance(data, dict) or data.get('case_id') != context['case_id']:
        raise GuidanceError('case_mismatch', '归一化材料与规范选择必须是同一case_id')
    if data.get('policy'):
        raise GuidanceError('policy_override', 'prepare_batch从已选规范生成policy；不要另填一份。特例先登记到本案规范')
    bindings = {'batch.max_invoices_per_form': 'max_invoices_per_form', 'batch.split_strategy': 'split_strategy',
                'amount.strategy': 'amount_strategy', 'summary.contact_priority': 'contact_priority',
                'finance.required_roles': 'required_roles', 'finance.prohibited_roles': 'prohibited_roles'}
    fixed = {'summary.source':'folder_name', 'summary.contact_location':('abstract', 'system_remarks'),
             'payment.source':'clarification_sheet_and_case_evidence', 'batch.summary_suffix':'chinese_parentheses',
             'export.directory_source':'clarification_business_date', 'export.filename':'sequence_summary',
             'unknown.policy':'hold_and_ask'}
    policy = {'status':'confirmed','source_refs':[]}
    issues, manual, seen_fixed = [], [], set()
    for rule in selection['applied_rules']:
        effect = rule['effect']
        target, value = effect['target'], effect['value']
        policy['source_refs'].append(rule['rule_id']+'@'+rule['revision'])
        if effect['action'] == 'recommended':
            manual.append({'rule_id':rule['rule_id'],'requirement':rule['statement'],'value':copy.deepcopy(value),
                           'mandatory':False,'reason':'建议保留供经办参考，不作为拒收或自动执行条件'})
        elif target in bindings and effect['action'] in {'required', 'default'}:
            policy[bindings[target]] = copy.deepcopy(value)
        elif target in fixed and (value in fixed[target] if isinstance(fixed[target], tuple) else value == fixed[target]) and effect['action'] in {'required', 'default'}:
            seen_fixed.add(target)
            if target == 'summary.contact_location':
                policy['contact_in_summary'] = value == 'abstract'
        elif target == 'finance.return_location' and effect['action'] in {'required', 'default'}:
            manual.append({'rule_id':rule['rule_id'],'requirement':rule['statement'],'value':copy.deepcopy(value),
                           'mandatory':True,'reason':'退单地点须与当前原生枚举核对后填写，规划不推定枚举值或财务已保存'})
        elif target == 'funding.lookup_hint':
            manual.append({'rule_id':rule['rule_id'],'requirement':rule['statement'],'value':copy.deepcopy(value),
                           'reason':'查找线索须在真实财务界面核对，不能直接当经费绑定'})
        else:
            issues.append({'field':'rules.'+rule['rule_id'],'code':'UNSUPPORTED_RULE_EFFECT',
                           'detail':'此要求尚未接入自动规划，先补工具或明确本案处理，不能静默忽略'})
    for key in ['max_invoices_per_form','split_strategy','amount_strategy','contact_priority','required_roles','prohibited_roles']:
        if key not in policy:
            issues.append({'field':'policy.'+key,'code':'CONFIRMED_RULE_REQUIRED','detail':'缺少对应已确认规则，不能由模型猜默认值'})
    for target in sorted(set(fixed)-seen_fixed):
        issues.append({'field':'policy.'+target,'code':'CONFIRMED_RULE_REQUIRED','detail':'当前规划会使用此行为，须有明确已选依据，不能默认为已确认'})
    if issues:
        return {'ok':False,'status':'blocked','rule_selection':selection,'needed_inputs':issues,'plan':None,'system_write':False,
                'next_action':{'tool':'reimbursement_standards_read','arguments':{'library_root':library_root,'profile_id':profile_id}}}
    prepared = copy.deepcopy(data)
    prepared['policy'] = policy
    plan = batch_plan(service, prepared, export_root)
    return {'ok':plan['ok'],'status':plan['status'],'rule_selection':selection,'effective_policy':policy,'plan':plan,
            'manual_requirements':manual,'needed_inputs':plan.get('needed_inputs',[]),
            'next_action':plan.get('next_action'),'system_write':False}


def prepare_batch_file(service, library_root, profile_id, context, as_of, input_path, output_path=None, export_root=None):
    from reimbursement_standards import safe_path
    def authorized(value):
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise GuidanceError('invalid_material_path', '输入和输出使用material-root内绝对JSON路径')
        p = Path(value).resolve()
        root = next((x for x in service.material_roots if p.is_relative_to(x)), None)
        if root is None or p.suffix != '.json':
            raise GuidanceError('material_root', '仅接受material-root内JSON，不读取凭据或其他格式')
        return safe_path(root, str(p.relative_to(root)))
    source = authorized(input_path)
    if source.stat().st_size > 16*1024*1024:
        raise GuidanceError('input_too_large', '归一化批次超过16MiB，请按已确认活动拆分输入')
    raw = source.read_bytes()
    # 解析冻结的同一字节，不能在校验后重开被替换的输入。
    from reimbursement_standards import _pairs
    data = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs)
    if not isinstance(data, dict) or data.get('schema') != 'ustc-reimbursement-batch-input-v1':
        raise GuidanceError('invalid_batch_input', '须先用归一化材料合同准备输入，不把任意JSON当报销批次')
    result = prepare_batch(service, library_root, profile_id, context, as_of, data, export_root)
    result['input_evidence'] = {'path':str(source),'sha256':hashlib.sha256(raw).hexdigest()}
    target = None
    if output_path is not None:
        target = authorized(output_path)
        if target == source:
            raise GuidanceError('output_collision', '规划输出不能覆盖原输入')
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('x', encoding='utf-8') as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
    plan = result.get('plan') or {}
    return {'ok':result['ok'],'status':result['status'],'system_write':False,'input_evidence':result['input_evidence'],
            'profile_sha256':result.get('rule_selection',{}).get('profile_sha256'),
            'output':str(target) if target else None,'output_sha256':hashlib.sha256(target.read_bytes()).hexdigest() if target else None,
            'documents':[{'document_id':d.get('document_id'),'status':d.get('status'),
                          'forms':len(d.get('forms',[])), 'needed_inputs':d.get('needed_inputs',[])} for d in plan.get('documents',[])],
            'needed_inputs':result.get('needed_inputs',[]),'next_action':result.get('next_action')}
