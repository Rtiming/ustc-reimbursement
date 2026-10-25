"""公共接口目录与枚举的唯一正本；只描述固定入口，不持有业务状态。"""

from __future__ import annotations

CASE_PROFILES = ('student_activity', 'research_purchase', 'travel')
CAPABILITY_INTENTS = ('materials', 'rules', 'ocr', 'finance', 'export')
MATERIAL_OPERATIONS = frozenset({'material_intake', 'material_ocr'})
REGISTERED_PHASES = (
    'status',
    'fill',
    'ready',
    'prepare-submit',
    'materials',
    'approve',
    'export-system',
    'export-list',
    'finish',
)
WORKFLOW_PHASES = (
    'status',
    'run',
    'deliver',
    'all',
    'submit',
    'prepare-submit',
    'ready',
    'fill',
    'materials',
    'approve',
    'export-system',
    'export-list',
    'finish',
)
DOCS = {
    'quickstart': 'docs/助手快速开始.md',
    'start': 'docs/助手接口使用.md',
    'workflow': 'docs/助手操作规程.md',
    'fields': 'docs/参数与字段目录.md',
    'materials': 'docs/报销规范与材料规则.md',
    'printing': 'docs/材料整理与单面打印.md',
    'activity': 'templates/activity_approval/README.md',
    'devices': 'docs/换设备与首次运行.md',
    'ledger': 'docs/报销总账与数据格式.md',
    'standards': 'skills/ustc-reimbursement/references/standards-system.md',
    'intake': 'docs/材料归档与归一化入口.md',
    'planning': 'docs/规范维护与批次规划.md',
    'export': 'docs/财务PDF导出.md',
    'windows': 'docs/Windows静默财务桥.md',
    'ocr': 'docs/OCR统一流程.md',
}
JSON_METHODS = frozenset(
    {
        'cases',
        'overview',
        'guide',
        'new_case',
        'update_case',
        'set_explanation',
        'verify_invoice',
        'search_spreadsheets',
        'read_taxi_itineraries',
        'audit_invoice_fields',
        'record_invoice_verification',
        'invoice_verification',
        'invoice_sources',
        'invoice_selection',
        'invoice_retry',
        'invoice_edit',
        'ledger_query',
        'ledger_trace',
        'check',
        'enqueue',
        'result',
        'capabilities',
        'standards_init',
        'standards_add_profile',
        'standards_read',
        'standards_change',
        'standards_select',
        'batch_plan',
        'prepare_batch',
        'prepare_batch_file',
        'material_intake',
        'ocr_status',
        'material_ocr',
        'material_ocr_resume',
        'pdf_export',
        'pdf_export_inspect',
    }
)
# 这些MCP入口通过已有任务层执行；不能按名称猜成JSON直调方法。
MCP_JOB_OPERATIONS = (
    'scan',
    'tabs',
    'access',
    'expense_amount',
    'stage',
    'invoice',
    'correction',
    'submission',
    'read_system',
    'record_result',
    'activity',
    'render',
)


class APIError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class GuidanceError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def dispatch_request(service, request):
    """CLI只派发既有明确方法；不开放对象属性、任意函数或任意脚本。"""
    if not isinstance(request, dict) or request.get('method') not in JSON_METHODS:
        raise APIError('unknown_method', 'method不在固定接口目录')
    return getattr(service, request['method'])(**request.get('params', {}))


def json_routes():
    routes = {
        'reimbursement_' + method: {'method': method}
        for method in sorted(JSON_METHODS - {'enqueue'})
    }
    routes.update(
        {
            'reimbursement_' + operation: {'method': 'enqueue', 'operation': operation}
            for operation in MCP_JOB_OPERATIONS
        }
    )
    return routes


def tool_catalog(tools, sdk_version):
    """目录由真实MCP schema产生，同时验证每项都有明确JSON路径。"""
    rows = [tool.model_dump(by_alias=True, exclude_none=True) for tool in tools]
    names = [row['name'] for row in rows]
    routes = json_routes()
    if len(set(names)) != len(names) or set(names) != set(routes):
        raise APIError('interface_catalog_drift', '实际MCP工具与公共接口目录不同，须同步同一合同')
    return {'api_version': 1, 'mcp_sdk': sdk_version, 'tools': rows, 'json_routes': routes}
