#!/usr/bin/env python3
"""项目专用stdio MCP。启动只注册接口，不连接浏览器或修改任何单据。"""
import argparse
import sys
import json
from functools import wraps
from pathlib import Path
from typing import Any, Literal
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from mcp.server.mcpserver.exceptions import ToolError, ResourceError
from assistant_api import AssistantService, PROJECT
from assistant_contract import CASE_PROFILES, CAPABILITY_INTENTS, DOCS, REGISTERED_PHASES
CaseProfile = Literal[tuple(CASE_PROFILES)]
CapabilityIntent = Literal[tuple(CAPABILITY_INTENTS)]
GuideTopic = Literal[tuple(DOCS)]
RegisteredPhase = Literal[tuple(REGISTERED_PHASES)]
from submission import ACTIONS as SUBMISSION_ACTIONS
SubmissionAction = Literal[tuple(SUBMISSION_ACTIONS)]


def expected_errors(fn):
    """将业务拒绝以可操作的错误码返回，避免SDK把它隐藏成通用崩溃。"""
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try: return fn(*args, **kwargs)
        except (ValueError, OSError, KeyError, TypeError, RuntimeError) as e:
            fallback = 'runtime_failure' if isinstance(e, RuntimeError) else 'invalid_input'
            raise ToolError(json.dumps({'ok': False, 'error_code': getattr(e, 'code', fallback), 'error': str(e),
                                       'next': '根据错误核对输入或读取overview/result；不要盲重试写入'}, ensure_ascii=False)) from e
    return wrapped


def create_server(service):
    from workspace_config import distribution_version
    server = MCPServer('ustc-reimbursement', version=distribution_version(PROJECT), instructions=(
        '先reimbursement_capabilities选择任务入口，照next_action和needed_inputs继续。办理财务先cases、overview明确case_id。'
        '耗时动作返回job_id，用result查询；同一操作重连保持request_id，不换号重发。'
        'execute默认false；明确本案授权后才执行已支持写入。写入完成要独立readback和record_result。'
        'completed仅表示程序成功退出，不代表已保存或审批通过。本人同意/验证码由用户完成。'
        '未知事实集中询问，定稿原样使用；新案不用旧案人员金额和原因。'))
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    local = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
    live = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True)
    invoice_read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
    ocr_work = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)

    @server.tool(annotations=read, structured_output=True)
    @expected_errors
    def reimbursement_capabilities(intent: CapabilityIntent = 'materials') -> dict[str, Any]:
        """不知道从哪里开始先调用。材料/规范/OCR发现仅返回离线工具，不探测用户浏览器资料；财务/导出才检查正式桥接线。均不启动浏览器或改写单据。"""
        return service.capabilities(intent)

    @server.tool(annotations=local, structured_output=True)
    @expected_errors
    def reimbursement_bridge(action: Literal['status', 'prepare', 'install', 'installation-result', 'inventory'] = 'status',
                             execute: bool = False, request_id: str | None = None,
                             expected_registration_sha256: str | None = None) -> dict[str, Any]:
        """本机浏览器接线。先预览安装；execute需原预览登记SHA及稳定request_id。仅安装本工具扩展，不登录、不办财务。"""
        return service.bridge(action, execute, request_id, expected_registration_sha256)

    @server.tool(annotations=local, structured_output=True)
    @expected_errors
    def reimbursement_register_workflow(case_id: str, request: dict[str, Any], output_dir: str,
                                        execute: bool = False, expected_preview_sha256: str | None = None) -> dict[str, Any]:
        """从本机已核单据及逐案正式计划生成批次索引、授权、两类导出与登记指针。先预览再核SHA执行；不编造单号或准备回执。"""
        return service.register_workflow(case_id, request, output_dir, execute, expected_preview_sha256)

    @server.tool(annotations=live, structured_output=True)
    @expected_errors
    def reimbursement_workflow(case_id: str, phase: RegisteredPhase = 'status', execute: bool = False) -> dict[str, Any]:
        """接续已登记整批流程。先status/预览；验证码本人填。未知结果只恢复原阶段；明细PDF和列表导出分别支持。长批次可用同合同CLI并保留进程/回执。"""
        return service.workflow(case_id, phase, execute)

    @server.tool(annotations=read, structured_output=True)
    @expected_errors
    def reimbursement_standards_read(library_root: str, profile_id: str | None = None) -> dict[str, Any]:
        """读取material-root内规范库；不填profile_id先列专项，选定后返回规范及SHA。不能用人名自动套旧案。"""
        return service.standards_read(library_root, profile_id)

    @server.tool(annotations=local, structured_output=True)
    @expected_errors
    def reimbursement_standards_init(library_root: str, profile_id: str, title: str,
                                     mode: CaseProfile = 'student_activity',
                                     execute: bool = False) -> dict[str, Any]:
        """为新用户预览或建立全新私人规范库，含共用与专项空模板；已有目录不覆盖，不预置规则确认、账号或财务授权。"""
        return service.standards_init(library_root, profile_id, title, mode, execute)

    @server.tool(annotations=local, structured_output=True)
    @expected_errors
    def reimbursement_standards_add_profile(library_root: str, profile_id: str, title: str,
                                           parent_ids: list[str] | None = None, execute: bool = False,
                                           expected_registry_sha256: str | None = None) -> dict[str, Any]:
        """在已有私人库追加空专项。先预览，再以原registry SHA执行；只引用已登记父模式，不覆盖已有专项或自动确认规则。"""
        return service.standards_add_profile(library_root, profile_id, title, parent_ids, execute, expected_registry_sha256)

    @server.tool(annotations=local, structured_output=True)
    @expected_errors
    def reimbursement_standards_change(library_root: str, profile_id: str, change: dict, execute: bool = False, expected_sha256: str | None = None) -> dict[str, Any]:
        """增补/修订/停用专项规则，默认预览。真实写须刚回读SHA、来源、理由与确认，保留版本和审计；不写财务。合同见guide standards。"""
        return service.standards_change(library_root, profile_id, change, execute, expected_sha256)

    @server.tool(annotations=read, structured_output=True)
    @expected_errors
    def reimbursement_standards_select(library_root: str, profile_id: str, context: dict, as_of: str) -> dict[str, Any]:
        """按本案范围和YYYY-MM-DD业务日期选已确认规则；未知、冲突及未确认分别列出，不自动取得财务授权。"""
        return service.standards_select(library_root, profile_id, context, as_of)

    @server.tool(annotations=read, structured_output=True)
    @expected_errors
    def reimbursement_batch_plan(data: dict, export_root: str | None = None) -> dict[str, Any]:
        """归一化材料生成分单、最低可比金额、摘要与PDF命名计划。未知留空并给needed_inputs；不写文件、不建财务单、不生成假PDF。输入见guide standards。"""
        return service.batch_plan(data, export_root)

    @server.tool(annotations=read, structured_output=True)
    @expected_errors
    def reimbursement_prepare_batch(library_root: str, profile_id: str, context: dict, as_of: str, data: dict, export_root: str | None = None) -> dict[str, Any]:
        """新批次优先调用：自动选用已登记专项生成规划参数，再检查归一化材料、分单和导出计划。data不另写policy；不支持或未知规则明确阻塞，不写财务。"""
        return service.prepare_batch(library_root, profile_id, context, as_of, data, export_root)

    @server.tool(annotations=local, structured_output=True)
    @expected_errors
    def reimbursement_prepare_batch_file(library_root: str, profile_id: str, context: dict, as_of: str, input_path: str, output_path: str | None = None, export_root: str | None = None) -> dict[str, Any]:
        """大量材料优先此入口：读取已授权归一化JSON，自动按已选规范规划；可写全新JSON结果，返回摘要/缺项/SHA，模型无需把所有票据抄进参数。原输入不改，不写财务。"""
        return service.prepare_batch_file(library_root, profile_id, context, as_of, input_path, output_path, export_root)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False), structured_output=True)
    @expected_errors
    def reimbursement_material_intake(case_id: str, request: dict[str, Any], request_id: str, execute: bool = False) -> dict[str, Any]:
        """异步盘点/归档已授权材料，默认预览。case_id是本机材料任务登记，request.case_id须一致；仅execute=true写全新目录。返回job_id后用result，重连保持request_id。不占浏览器，不启动OCR或财务；工具依赖由本机配置注入，request不能传Python路径。"""
        return service.material_intake(case_id, request, request_id, execute)

    @server.tool(annotations=read, structured_output=True)
    @expected_errors
    def reimbursement_ocr_status() -> dict[str, Any]:
        """读取本工作区OCR提供方及依赖就绪度；不识别、不上传材料、不探测浏览器。远程环境在任务启动时按探针核验。"""
        return service.ocr_status()

    @server.tool(annotations=ocr_work, structured_output=True)
    @expected_errors
    def reimbursement_material_ocr(case_id: str, request: dict[str, Any], request_id: str, execute: bool = False) -> dict[str, Any]:
        """从本案已完成收件job选择无文字候选，默认只预览。request含intake_job_id，可选candidate_ids/rotation；执行按已配置提供方识别并回接文字证据和票面候选，不核定金额。"""
        return service.material_ocr(case_id, request, request_id, execute)

    @server.tool(annotations=ocr_work, structured_output=True)
    @expected_errors
    def reimbursement_material_ocr_resume(case_id: str, ocr_job_id: str, request_id: str) -> dict[str, Any]:
        """沿原OCR执行job及原compute run等待/回收，不重新提交未知计算；已完成结果按SHA复用。"""
        return service.material_ocr_resume(case_id, ocr_job_id, request_id)

    @server.tool(annotations=local, structured_output=True)
    @expected_errors
    def reimbursement_pdf_export(case_id: str, export_plan: dict, export_root: str, tab_id: str, expected_userid: str, request_id: str, silent: bool = True, execute: bool = False) -> dict[str, Any]:
        """导出已打开的同案PDF预览，默认仅计划、默认静默。确认计划含日期/序号/摘要，执行绑定账号/tab/单号并分块核验，拒绝覆盖。返回job_id后查result；预览不是正式打印件。"""
        return service.pdf_export(case_id, export_plan, export_root, tab_id, expected_userid, request_id, silent, execute)

    @server.tool(annotations=read, structured_output=True)
    @expected_errors
    def reimbursement_pdf_export_inspect(case_id: str, job_id: str) -> dict[str, Any]:
        """PDF导出失败/中断后只读原任务意图、分块回执与文件SHA；不启动浏览器、不换任务重导、不清原marker。"""
        return service.pdf_export_inspect(case_id, job_id)

    @server.tool(annotations=read, structured_output=True)
    @expected_errors
    def reimbursement_cases() -> dict[str, Any]:
        """列出项目内案件及本地状态。先明确case_id；不默认拿四川线旧案作为新案。"""
        return service.cases()

    @server.tool(annotations=read, structured_output=True)
    @expected_errors
    def reimbursement_overview(case_id: str, include_parameters: bool = False) -> dict[str, Any]:
        """案件总览、revision、缺项、待回查写入、后台任务和next_action。状态是缓存；改参数前include_parameters=true。"""
        return service.overview(case_id, include_parameters)

    @server.tool(annotations=read, structured_output=True)
    @expected_errors
    def reimbursement_guide(topic: GuideTopic = 'start') -> dict[str, Any]:
        """按需取得完整输入示例及操作说明：intake收件归档、planning规范维护与批次规划、export原生PDF、windows静默桥；quickstart总入口，standards规范体系，workflow财务顺序，materials材料要求。"""
        return service.guide(topic)

    @server.tool(annotations=read, structured_output=True)
    @expected_errors
    def reimbursement_ledger_query(case_id: str, table: Literal['persons','expenses','invoices','invoice_items','documents','expense_invoices','expense_documents','invoice_documents','finance_observations','issues','source_records'] = 'expenses', person: str | None = None, invoice_number: str | None = None, record_id: str | None = None, limit: int = 200) -> dict[str, Any]:
        """只读查询本案已登记且SHA匹配的远程生成总账；按人、完整票号或稳定记录ID取费用/票据/材料/历史观察。返回截断标识，不接SQL或数据库路径，不读实时财务系统。"""
        return service.ledger_query(case_id, table, person, invoice_number, record_id, limit)

    @server.tool(annotations=read, structured_output=True)
    @expected_errors
    def reimbursement_ledger_trace(case_id: str, person: str | None = None, expense_id: str | None = None, invoice_number: str | None = None, document_id: str | None = None, page: int | None = None, limit: int = 200) -> dict[str, Any]:
        """从人员、费用ID、完整票号或材料ID中选择一个锚点，追溯费用/票版/材料/原关系。page仅用于材料精确页，不将同一合并文件中的其他费用混入；只读本案已登记SHA快照，不读实时财务。"""
        return service.ledger_trace(case_id, person, expense_id, invoice_number, document_id, page, limit)

    @server.tool(annotations=local, structured_output=True)
    @expected_errors
    def reimbursement_new_case(case_id: str, profile: CaseProfile = 'student_activity') -> dict[str, Any]:
        """建立空案件和五份参数文件。不会复用旧事实、授权或建财务单；已有case_id拒绝覆盖。"""
        return service.new_case(case_id, profile)

    @server.tool(annotations=local, structured_output=True)
    @expected_errors
    def reimbursement_update_case(case_id: str, document: Literal['case', 'form'], changes: dict, expected_revision: str) -> dict[str, Any]:
        """按fields目录合并本地业务输入；数组整体替换，字典递归合并。expected_revision取最新overview；不编辑state/单号/GUID。"""
        return service.update_case(case_id, document, changes, expected_revision)

    @server.tool(annotations=local, structured_output=True)
    @expected_errors
    def reimbursement_set_explanation(case_id: str, text: str, expected_revision: str, record_only: bool = True) -> dict[str, Any]:
        """原样导入已确定说明并登记正文SHA；默认只留档。已有流程只允许留档，不自动改线上说明。"""
        return service.set_explanation(case_id, text, expected_revision, record_only)

    @server.tool(annotations=local, structured_output=True)
    @expected_errors
    def reimbursement_verify_invoice(case_id: str, index: int, invoice_numbers: list[str], note: str, expected_revision: str, pool_job_id: str | None = None, pool_artifact_id: str | None = None, native_guid: str | None = None) -> dict[str, Any]:
        """查看原件后登记OCR异常核对、票号候选和说明，保留原SHA。航空票号未显示时可附完整池回读job/artifact及native_guid，核对人员金额后唯一绑定。"""
        return service.verify_invoice(case_id, index, invoice_numbers, note, expected_revision, pool_job_id, pool_artifact_id, native_guid)

    @server.tool(annotations=read, structured_output=True)
    @expected_errors
    def reimbursement_search_spreadsheets(paths: list[str], query: str, exact: bool = False, person_lookup: bool = False, limit: int = 30) -> dict[str, Any]:
        """只读搜索本地xlsx/csv，明确绝对路径或材料目录；返回文件SHA、工作表及单元格。person_lookup按姓名精确查相邻学号列，同名多学号须核对，不自动绑定人员。"""
        return service.search_spreadsheets(paths, query, exact, person_lookup, limit)

    @server.tool(annotations=read, structured_output=True)
    @expected_errors
    def reimbursement_read_taxi_itineraries(paths: list[str]) -> dict[str, Any]:
        """只读解析1至20份滴滴/高德文字PDF，返回逐程起点、终点、时间、金额、页码及合计核对。地点提示不确认公务用途。"""
        return service.read_taxi_itineraries(paths)

    @server.tool(annotations=read, structured_output=True)
    @expected_errors
    def reimbursement_audit_invoice_fields(paths: list[str], profile: Literal['none','ustc'] = 'none') -> dict[str, Any]:
        """提取票号日期、抬头、购销税号、财政校验码，检查统一信用代码校验位；ustc显式比对本校主体。生成分平台官方查验字段，不自动验证码、不宣称真伪已查。"""
        return service.audit_invoice_fields(paths, profile)

    @server.tool(annotations=local, structured_output=True)
    @expected_errors
    def reimbursement_record_invoice_verification(case_id: str, invoice_number: str, receipt_path: str, official_url: str, result: Literal['matched','mismatch','not_found','voided','red_issued','failed'], note: str) -> dict[str, Any]:
        """独立登记人工完成的官方查询回执，含本票号和查验结果，保存来源SHA；原发票不能作查验回执。此登记不证明自动联网验真或报销批准。"""
        return service.record_invoice_verification(case_id, invoice_number, receipt_path, official_url, result, note)

    @server.tool(annotations=read, structured_output=True)
    @expected_errors
    def reimbursement_check(case_id: str, hashes: bool = True) -> dict[str, Any]:
        """核对金额、人员输入、单号关系、材料规则及可选原件SHA。缺单号在建单前正常存在；不等于材料交付或审批通过。"""
        return service.check(case_id, hashes)

    @server.tool(annotations=local, structured_output=True)
    @expected_errors
    def reimbursement_scan(case_id: str, paths: list[str], request_id: str, invoices: bool = True) -> dict[str, Any]:
        """文字层优先和SHA缓存OCR。paths是本机绝对材料路径；返回job_id，result查低置信和一文件多票等异常。"""
        return service.enqueue(case_id, 'scan', {'paths': paths, 'invoices': invoices}, request_id)

    @server.tool(annotations=local, structured_output=True)
    @expected_errors
    def reimbursement_tabs(case_id: str, request_id: str) -> dict[str, Any]:
        """只读列出本机macOS Edge财务标签ID。先result取当前ID，后续现场动作明确传tab_id，不沿用旧设备ID。"""
        return service.enqueue(case_id, 'tabs', {}, request_id)

    @server.tool(annotations=live, structured_output=True)
    @expected_errors
    def reimbursement_access(case_id: str, request_id: str, execute: bool = False, tab_id: str | None = None, action: Literal['check', 'check-page', 'diagnose', 'ensure', 'new-interface', 'recover-interface','wake-interface'] | None = None, job_id: str | None = None, artifact_id: str | None = None, expected_userid: str | None = None, silent: bool | None = None, session_only: bool | None = None) -> dict[str, Any]:
        """财务检查与登录。new-interface须execute=true，可省略旧tab_id；silent默认true，不支持时不回退。明确silent=false允许原生短暂置前并恢复原标签。recover-interface只读同案原job/artifact，继承原开页模式，不重开。check-page需身份，diagnose只读错误页；不输出密码/Cookie。"""
        params = {k: v for k, v in dict(tab_id=tab_id, action=action, job_id=job_id, artifact_id=artifact_id, expected_userid=expected_userid, silent=silent, session_only=session_only).items() if v is not None}
        return service.enqueue(case_id, 'access', params, request_id, execute)

    @server.tool(annotations=invoice_read, structured_output=True)
    @expected_errors
    def reimbursement_invoice_verification(case_id: str, action: Literal['snapshot', 'full-grid', 'surface', 'surface-plan', 'read-detail', 'view', 'query-used', 'recover'], tab_id: str, request_id: str, invoice_numbers: list[str] | None = None, view: Literal['home', 'used', 'recognition', 'pool', 'drafts', 'in-progress', 'awaiting-approval','all-bills'] | None = None, job_id: str | None = None, artifact_id: str | None = None, operation_key: str | None = None, grid: Literal['53', '113','101'] | None = None, expected_userid: str | None = None) -> dict[str, Any]:
        """静默只读当前权限发票字段与原生明细；full-grid须grid=53/113/101及expected_userid并提取完整表，101为本人报销单当前查询范围。view all-bills打开全部报销单入口。view recognition在零队列时须expected_userid，核对固定原生导航定义；会清空客户端详情字段，回执明确client_state_changed，仍不写服务器票据。surface提取隐显列/控件/分页，surface-plan引用同案SHA回执和operation_key只生成计划，read-detail仅已适配识别明细。query-used每批1至6个20位字符串票号；recover原标记不重发。无前台/JS/URL输入；认证、预约、报销和付款分别记录。"""
        extra = {k: v for k, v in dict(grid=grid, expected_userid=expected_userid).items() if v is not None}
        return service.invoice_verification(case_id, action, tab_id, request_id, invoice_numbers, view, job_id, artifact_id, operation_key, **extra)

    @server.tool(annotations=read, structured_output=True)
    @expected_errors
    def reimbursement_invoice_sources(case_id: str, action: Literal['manifest', 'audit'], request_id: str, invoice_numbers: list[str] | None = None) -> dict[str, Any]:
        """从同案已登记候选原件/ZIP成员及实际SHA生成来源manifest或固定ustc购方审核artifact。audit限定1至20完整票号，仅原生文本PDF，不在本机OCR；未选择、不定金额、不证明验真。"""
        return service.invoice_sources(case_id, action, request_id, invoice_numbers)

    @server.tool(annotations=live, structured_output=True)
    @expected_errors
    def reimbursement_invoice_edit(case_id: str, action: Literal['run','open','medical-run','medical-open','medical-save','medical-close','medical-return','journey','airport-open','airport-query','airport-select','save','resume-save','close','inspect','recover'], tab_id: str, request_id: str, invoice_number: str, expected_userid: str, execute: bool = False, job_id: str | None = None, artifact_id: str | None = None, inspection_job_id: str | None = None, inspection_artifact_id: str | None = None) -> dict[str, Any]:
        """按本案登记且SHA冻结的invoice_edit_manifest办理行程或医疗医保类型缺项。run串行办理行程；medical-run串行补录并重新打开核对原字段；医疗也可经medical-open/inspect核对，medical-save须本案逐票本人确认及独立inspect，只补自费类型并留原生HTTP响应；medical-return只返回本票供重开核对。金额、票号和原件受保护；recover只查原请求，不接受任意字段、脚本、URL或前台参数。"""
        return service.invoice_edit(case_id, action, tab_id, request_id, invoice_number, expected_userid, execute, job_id, artifact_id, inspection_job_id, inspection_artifact_id)

    @server.tool(annotations=live, structured_output=True)
    @expected_errors
    def reimbursement_expense_amount(case_id: str, action: Literal['open', 'inspect', 'return', 'recover','save'], group: str, subject: str, expected_userid: str, tab_id: str, request_id: str, execute: bool = False, source_job_id: str | None = None, source_artifact_id: str | None = None, job_id: str | None = None, artifact_id: str | None = None,claims_path: str | None = None) -> dict[str, Any]:
        """检查或调整同案报销项金额。先inspect冻结可见分组及原生定义；open/return各留pending并独立inspect后ack staged。save须同案已打开窗口的完整独立检查及claims_path逐票来源，重算准备，只保存一次已适配的41分支；先独立回读分组总额ack saved，再重新打开核对逐票持久金额。recover只读原open/return标记，不重放。JSON使用enqueue operation=expense_amount。"""
        return service.expense_amount(case_id, action, group, subject, expected_userid, tab_id, request_id, execute, source_job_id, source_artifact_id, job_id, artifact_id,claims_path)

    @server.tool(annotations=live, structured_output=True)
    @expected_errors
    def reimbursement_invoice_retry(case_id: str, action: Literal['inspect', 'preview', 'refresh', 'reidentify', 'cleanup', 'confirm', 'readback', 'recover'], tab_id: str, request_id: str, execute: bool = False, expected_userid: str | None = None, grid: Literal['53', '113'] | None = None, target_guids: list[str] | None = None, target_filecodes: list[str] | None = None, planned_action: Literal['refresh', 'reidentify', 'cleanup'] | None = None, targets: list[dict[str, str]] | None = None, source_job_id: str | None = None, source_artifact_id: str | None = None, pool_job_id: str | None = None, pool_artifact_id: str | None = None, recognition_job_id: str | None = None, recognition_artifact_id: str | None = None, inspection_job_id: str | None = None, inspection_artifact_id: str | None = None, plan_job_id: str | None = None, plan_artifact_id: str | None = None, receipt_job_id: str | None = None, receipt_artifact_id: str | None = None, job_id: str | None = None, artifact_id: str | None = None) -> dict[str, Any]:
        """异常票据先inspect及完整53/113查询，再preview冻结原件/原生目标/清理范围；来源引用同案source任务，或已登记且SHA核对的verification_manifest，无需已选票或建单。刷新、重识别、清理、确认须execute=true并引用同案预览SHA；refresh只触发原生查询，回执system_write=false。重识别及清理均分两步：confirm还需对应原动作回执和独立真实弹框检查。readback必须引用同计划成功动作receipt（cleanup/reidentify须confirm），53/113新查询均晚于动作回执和计划基线。recover只查原动作marker，不重放；原job为unknown/failed且无回执时可省artifact_id。全部走稳定job与日志，不接路径/JS/URL/前台参数，不释放他人预约或自动重传。"""
        return service.invoice_retry(case_id, action, tab_id, request_id, execute,
            expected_userid, grid, target_guids, target_filecodes, planned_action, targets, source_job_id, source_artifact_id,
            pool_job_id, pool_artifact_id, recognition_job_id, recognition_artifact_id, inspection_job_id, inspection_artifact_id,
            plan_job_id, plan_artifact_id, receipt_job_id, receipt_artifact_id, job_id, artifact_id)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True), structured_output=True)
    @expected_errors
    def reimbursement_invoice_selection(case_id: str, action: Literal['preview', 'apply', 'readback', 'recover'], tab_id: str, request_id: str, execute: bool = False, source_job_id: str | None = None, source_artifact_id: str | None = None, audit_refs: list[dict[str, str]] | None = None, selectors: list[dict[str, str]] | None = None, mode: Literal['append', 'replace', 'remove'] | None = None, grid: Literal['53', '1669'] | None = None, serial: str | None = None, expected_userid: str | None = None, expected_before_guids: list[str] | None = None, snapshot_job_id: str | None = None, snapshot_artifact_id: str | None = None, preview_job_id: str | None = None, preview_artifact_id: str | None = None, job_id: str | None = None, artifact_id: str | None = None) -> dict[str, Any]:
        """独立选票，默认preview；同案来源/审核/快照/preview以job/artifact SHA引用。apply须具体授权及execute=true，只改原生勾选、不建单/提交/删除。缺selarrrow/身份/上下文、占用或购方审核失败不能新增；recover仅原marker不重发。"""
        return service.invoice_selection(case_id, action, tab_id, request_id, execute,
            source_job_id, source_artifact_id, audit_refs, selectors, mode, grid, serial,
            expected_userid, expected_before_guids, snapshot_job_id, snapshot_artifact_id,
            preview_job_id, preview_artifact_id, job_id, artifact_id)

    @server.tool(annotations=live, structured_output=True)
    @expected_errors
    def reimbursement_stage(case_id: str, stage: Literal['basic', 'funding', 'expenses', 'payments', 'notes', 'pre-review', 'no-contract', 'audit', 'review'], request_id: str, execute: bool = False, tab_id: str | None = None) -> dict[str, Any]:
        """日常草稿的单阶段计划/执行，默认仅生成计划。execute=true需当前tab_id和本案授权。保存后独立回读；review止于本人审核。"""
        return service.enqueue(case_id, 'stage', {'stage': stage, 'tab_id': tab_id}, request_id, execute)

    @server.tool(annotations=live, structured_output=True)
    @expected_errors
    def reimbursement_invoice(case_id: str, action: Literal['open-pool', 'open-upload', 'upload', 'read', 'save-upload', 'create-draft','resume-draft'], request_id: str, execute: bool = False, tab_id: str | None = None, start: int = 0, count: int = 10) -> dict[str, Any]:
        """发票目录、上传、认证和建日常草稿。默认生成脚本；每批1至10票。未知创建不重放；resume-draft只接续本案最后一次独立回读并ack的明确拒绝，保留原意图。上传和认证后独立回读再登记。"""
        return service.enqueue(case_id, 'invoice', {'action': action, 'start': start, 'count': count, 'tab_id': tab_id}, request_id, execute)

    @server.tool(annotations=live, structured_output=True)
    @expected_errors
    def reimbursement_correction(case_id: str, action: Literal['check', 'verify', 'apply', 'begin', 'open', 'select', 'update', 'save', 'return', 'remark', 'basic', 'basic-read', 'basic-close', 'drafts', 'resume'], plan: dict, request_id: str, execute: bool = False, tab_id: str | None = None, group: str = '', key: str = '', kind: str = '', job_id: str | None = None, artifact_id: str | None = None) -> dict[str, Any]:
        """退回单逐票拆分。plan含原件SHA、GUID、人员、往返用途、经费和个人/团队限额；默认校验/生成计划。update/save后须独立回读、verify和ack-write。不重新生成审批流程。"""
        return service.enqueue(case_id, 'correction', {'action': action, 'plan': plan, 'tab_id': tab_id, 'group': group, 'key': key, 'kind': kind, 'job_id': job_id, 'artifact_id': artifact_id}, request_id, execute)

    @server.tool(annotations=live, structured_output=True)
    @expected_errors
    def reimbursement_submission(case_id: str, action: SubmissionAction, plan: dict, request_id: str, execute: bool = False, tab_id: str | None = None) -> dict[str, Any]:
        """按本案明确授权逐步提交同号单据；每步独立回读。applicant-agree另须applicant_agreement_authorized、意见同意及当前账号，限申请人508节点；验证码留现场，不代领导审批或付款。"""
        return service.enqueue(case_id, 'submission', {'action': action, 'plan': plan, 'tab_id': tab_id}, request_id, execute)

    @server.tool(annotations=read, structured_output=True)
    @expected_errors
    def reimbursement_read_system(case_id: str, tab_id: str, request_id: str, mode: Literal['status', 'readback', 'recover', 'open-draft'] = 'readback', job_id: str | None = None, artifact_id: str | None = None, serial: str | None = None, expected_userid: str | None = None) -> dict[str, Any]:
        """现场独立回读或恢复旧标记。open-draft须本案serial及当前expected_userid；默认全案无active/pending。已发出支付保存且旧预览为空时，job_id/artifact_id仅引用同案独立空预览回执，经原保存源码及新标签资格核验后找回原单，不保存/审核/ack或清pending。recover仅读原标记。"""
        params = {'mode': mode, 'tab_id': tab_id, 'job_id': job_id, 'artifact_id': artifact_id}
        if serial is not None: params['serial'] = serial
        if expected_userid is not None: params['expected_userid'] = expected_userid
        return service.enqueue(case_id, 'read_system', params, request_id)

    @server.tool(annotations=local, structured_output=True)
    @expected_errors
    def reimbursement_record_result(case_id: str, kind: Literal['register-originals', 'select-invoices', 'bind-serial', 'ack-write'], job_id: str, artifact_id: str, request_id: str, serial: str | None = None, outcome: Literal['saved', 'staged', 'not_applied', 'needs_correction'] | None = None, note: str = '') -> dict[str, Any]:
        """用result列出的本案回执登记原件、选择GUID、绑定真实单号或解待回读锁。ack-write必须检查独立回读并填写outcome/note，不能把请求当保存。"""
        return service.enqueue(case_id, 'record_result', {'kind': kind, 'job_id': job_id, 'artifact_id': artifact_id, 'serial': serial, 'outcome': outcome, 'note': note}, request_id)

    @server.tool(annotations=local, structured_output=True)
    @expected_errors
    def reimbursement_activity(case_id: str, values: dict, request_id: str, check_only: bool = True, allow_incomplete: bool = False) -> dict[str, Any]:
        """按原模板检查/生成团学审批表；先guide activity。allow_incomplete仅用于未知字段留白草稿，缺项列回执，签章和审批不代填。"""
        return service.enqueue(case_id, 'activity', {'values': values, 'check_only': check_only, 'allow_incomplete': allow_incomplete}, request_id)

    @server.tool(annotations=local, structured_output=True)
    @expected_errors
    def reimbursement_render(case_id: str, job_id: str, artifact_id: str, request_id: str) -> dict[str, Any]:
        """渲染activity任务的DOCX为PDF及全部页面PNG，供模型检查字体、裁切和审批栏；渲染成功不等于视觉检查已完成。"""
        return service.enqueue(case_id, 'render', {'job_id': job_id, 'artifact_id': artifact_id}, request_id)

    @server.tool(annotations=read, structured_output=True)
    @expected_errors
    def reimbursement_result(case_id: str, job_id: str, artifact_id: str | None = None) -> dict[str, Any]:
        """读取后台任务状态和回执目录；按artifact_id取JSON完整回执（长号码脱敏）或文档路径。queued/running等待；failed/unknown先核对，不重放。"""
        return service.result(case_id, job_id, artifact_id)

    @server.resource('reimbursement://guide/{topic}')
    def read_guide(topic: str) -> str:
        """项目权威使用文档；topic见guide工具枚举。"""
        try: return service.guide(topic)['text']
        except ValueError as e: raise ResourceError(str(e)) from e
    return server


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--workspace', type=str, default=str(PROJECT))
    ap.add_argument('--material-root', action='append', help='可多次指定，缺省项目所在活动目录；只声明材料路径，不包含登录资料')
    args = ap.parse_args()
    create_server(AssistantService(args.workspace, args.material_root)).run(transport='stdio')


if __name__ == '__main__': main()
