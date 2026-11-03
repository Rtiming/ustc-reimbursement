"""人工任务名称与公共工具的映射；执行合同仍以MCP目录及原业务owner为准。"""
from pathlib import Path
import copy
import json

GROUPS = {
    'activities': ('活动与批次', ['cases','overview','new_case','case_details','case_journey','update_case','check','batch_plan','prepare_batch','prepare_batch_file']),
    'materials': ('导入与核对', ['material_intake','intake_review','scan','ocr_status','material_ocr','material_ocr_resume','verify_invoice','search_spreadsheets','read_taxi_itineraries','audit_invoice_fields','record_invoice_verification']),
    'review': ('补件与反馈', ['case_materials','material_review','material_requirements','intake_groups','receive_materials']),
    'finance': ('财务办理', ['case_finance','business','invoice_verification','invoice_sources','invoice_selection','invoice','stage','expense_amount','invoice_edit','set_explanation']),
    'approval': ('本人确认与审批', ['approver_batch','applicant_batch','submission']),
    'feedback': ('财务挂单理由', ['finance_feedback']),
    'recovery': ('退回与恢复', ['correction','invoice_retry','read_system','record_result','result','task_view','pdf_export_inspect']),
    'rules': ('规范与专项', ['standards_read','standards_init','standards_add_profile','standards_change','standards_select','standards_import']),
    'delivery': ('清单与交付', ['case_delivery','pdf_export','print_batch','activity','render','ledger_query','ledger_trace']),
    'settings': ('连接与帮助', ['finance_connection','capabilities','guide','tabs','access','assist']),
}
TITLES = {
    'standards_import':'导入已核私人规范包',
    'case_materials':'本活动材料、费用与补件',
    'case_delivery':'本活动交付与系统PDF',
    'case_finance':'本活动财务办理向导',
    'approver_batch':'按当前角色批量审批',
    'receive_materials':'从已有文件路径收件',
    'intake_groups':'把收件材料分给多个活动',
    'material_requirements':'逐项核对材料差项',
    'finance_connection':'本人账号与财务收件箱',
    'case_journey':'本案下一步与逐票核对',
    'assist':'代理简明入口',
    'intake_review':'核对归档材料并登记票源',
    'task_view':'查看任务结论与下一步',
    'case_details':'填写并核定报销信息',
    'cases':'查看活动','overview':'查看当前进度','new_case':'新建活动','update_case':'维护报销信息','check':'检查材料与金额',
    'batch_plan':'检查分单方案','prepare_batch':'规划本批报销','prepare_batch_file':'从已核材料规划',
    'material_intake':'导入并整理材料','scan':'提取文字与票号','ocr_status':'检查识别服务','material_ocr':'识别扫描材料','material_ocr_resume':'接续原识别任务',
    'verify_invoice':'确认票面识别结果','search_spreadsheets':'查找名单与学工号','read_taxi_itineraries':'读取打车行程','audit_invoice_fields':'检查票面信息','record_invoice_verification':'登记官方查验结果',
    'material_review':'打开材料核对与补件','business':'读取学校单据与业务信息','invoice_verification':'核对票池状态','invoice_sources':'核对原票来源','invoice_selection':'选择报销发票','invoice':'上传票据与创建单据','stage':'填写并核验报销单','expense_amount':'核定逐票报销额','invoice_edit':'补正票据信息','set_explanation':'登记补充说明',
    'applicant_batch':'批量本人确认','submission':'准备并提交原单','finance_feedback':'取得挂单理由并汇总',
    'correction':'修正退回原单','invoice_retry':'处理异常票据','read_system':'回读或恢复原结果','record_result':'登记已核实结果','result':'查看原任务进度','pdf_export_inspect':'核查原导出任务',
    'standards_read':'查看适用规范','standards_init':'建立私人规范库','standards_add_profile':'增加专项','standards_change':'修订专项规则','standards_select':'选择本次适用规则',
    'pdf_export':'导出系统单据','print_batch':'批量正式打印','activity':'填写活动审批表','render':'预览文档页面','ledger_query':'查询报销总账','ledger_trace':'追溯费用与原件',
    'capabilities':'检查本机能力','guide':'查看操作帮助','tabs':'查找本机财务页面','access':'连接学校财务系统',
}


def catalog(root):
    from assistant_contract import json_routes
    source = json.loads((Path(root)/'mcp/tool_catalog.json').read_text(encoding='utf-8'))
    routes = json_routes()
    if set(routes) != {t['name'] for t in source['tools']}:
        raise ValueError('工具目录与当前业务合同不同，请先更新并验收目录')
    mapped = [name for _, names in GROUPS.values() for name in names]
    actual = {t['name'].removeprefix('reimbursement_') for t in source['tools']}
    if set(mapped) != actual or len(mapped) != len(set(mapped)):
        raise ValueError('人工入口覆盖与公共工具不一致，不能隐藏未接入功能')
    from finance_business_api import ACTIONS, DOMAINS
    rows = []
    for original in source['tools']:
        row = copy.deepcopy(original)
        name = row['name'].removeprefix('reimbursement_')
        row['title'] = TITLES[name]
        row['group'] = next(key for key, (_, values) in GROUPS.items() if name in values)
        row['route'] = routes[row['name']]
        if name == 'business':
            row['inputSchema']['properties']['action']['enum'] = list(ACTIONS)
            row['business_domains'] = list(DOMAINS)
        rows.append(row)
    return {'groups': [{'id': key, 'title': title} for key, (title, _) in GROUPS.items()], 'tools': rows}


def json_request(tool, arguments):
    """固定MCP映射保留enqueue合同；不反射任意服务方法。"""
    route = tool['route']
    if route['method'] != 'enqueue':
        return {'method': route['method'], 'params': arguments}
    common = {key: arguments[key] for key in ['case_id', 'request_id', 'execute'] if key in arguments}
    return {'method': 'enqueue', 'params': {**common, 'operation': route['operation'],
            'parameters': {key: value for key, value in arguments.items() if key not in common}}}


def read_allowed(tool, arguments):
    """同一动态读取判定供UI与agent使用，进度读取不得缓存为写入意图。"""
    if arguments.get('execute') is True:
        return False
    if tool.get('annotations', {}).get('readOnlyHint') is True:
        return True
    name=tool['name']
    if name in {'reimbursement_case_materials','reimbursement_intake_review','reimbursement_finance_feedback','reimbursement_case_details','reimbursement_material_requirements','reimbursement_intake_groups','reimbursement_receive_materials'}:
        return True
    if name=='reimbursement_finance_connection':
        return arguments.get('action','status') in {'status','discover'}
    if name=='reimbursement_approver_batch':
        return arguments.get('action','list') in {'list','status','inspect'}
    if name=='reimbursement_case_finance':
        return arguments.get('action','inspect') in {'inspect','fill_status','ready_status'}
    if name=='reimbursement_case_delivery':
        return arguments.get('action','inspect') in {'inspect','status'}
    return False


def financial_write_requested(tool_name, arguments):
    if arguments.get('execute') is not True:return False
    if tool_name=='reimbursement_case_finance':
        return arguments.get('action')in {'upload','create','fill_continue','ready_continue'}
    return tool_name in {'reimbursement_stage','reimbursement_invoice','reimbursement_submission',
        'reimbursement_correction','reimbursement_expense_amount','reimbursement_invoice_edit',
        'reimbursement_invoice_retry','reimbursement_applicant_batch','reimbursement_approver_batch'}
