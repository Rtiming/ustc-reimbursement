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


def create_server(service, toolset='full'):
    from workspace_config import distribution_version
    server = MCPServer('ustc-reimbursement', version=distribution_version(PROJECT), instructions=(
        '先reimbursement_capabilities选择任务入口，照next_action和needed_inputs继续。办理财务先cases、overview明确case_id。'
        '使用compact工具集时，其他业务名称通过reimbursement_assist的discover/schema/call/prepare调用，不调用未暴露的工具；status接续原任务。'
        '耗时动作返回job_id，用result查询；同一操作重连保持request_id，不换号重发。'
        'execute默认false；明确本案授权后才执行已支持写入。写入完成要独立readback和record_result。'
        'completed仅表示程序成功退出，不代表已保存或审批通过。本人同意/验证码由用户完成。'
        '未知事实集中询问，定稿原样使用；新案不用旧案人员金额和原因。'))
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    local = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
    live = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True)
    invoice_read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
    ocr_work = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)

    @server.tool(annotations=live, structured_output=True)
    @expected_errors
    def reimbursement_assist(action: Literal['discover','schema','call','prepare','confirm','status'] = 'discover',
                             group: str | None = None, operation: str | None = None, inputs: dict | None = None,
                             case_id: str | None = None, intent_id: str | None = None, job_id: str | None = None,
                             confirmation: str | None = None) -> dict[str, Any]:
        """简明入口。discover选操作、schema取字段、call只读或预览；改动prepare返回冻结计划，获用户明确授权后confirm原intent_id。status接续原任务。无需GUI，不猜参数或重试未知写入。"""
        return service.assist(action, group, operation, inputs, case_id, intent_id, job_id, confirmation)

    @server.tool(annotations=read, structured_output=True)
    @expected_errors
    def reimbursement_capabilities(intent: CapabilityIntent = 'materials') -> dict[str, Any]:
        """不知道从哪里开始先调用。材料/规范/OCR发现仅返回离线工具，不探测用户浏览器资料；财务/导出才检查正式桥接线。均不启动浏览器或改写单据。"""
        result=service.capabilities(intent)
        if toolset=='compact':
            from agent_assist import compact_routes
            return compact_routes(result)
        return result

    @server.tool(annotations=local, structured_output=True)
    @expected_errors
    def reimbursement_material_review(action: Literal['list','register','summary','start','open','status','doctor','stop'] = 'list', workspace: str | None = None, port: int = 8766) -> dict[str, Any]:
        """本机反馈台统一入口。list查已登记工作台；workspace须位于material-root内。register只登记入口，summary读取回复/缺项/原单去向，open启动或复用本地页面。不会建财务单、保存或提交。构建、补件、复核与导出合同见guide review。"""
        return service.material_review(action, workspace, port)

    @server.tool(annotations=local, structured_output=True)
    @expected_errors
    def reimbursement_case_materials(case_id: str, action: Literal['inspect','save_document','save_expenses','remove_expense','save_issue','plan','review_open','save_dispositions','materialize','delivery_plan','delivery_build']='inspect', values: dict | list | None=None, expected_revision: str | None=None, confirmation_note: str | None=None, execute: bool=False, offset: int=0, limit: int=50) -> dict[str,Any]:
        """从本活动已导入原件登记明白纸、费用和付款/行程关联，打开补件工作台或按已选规则分单。无需技术路径；inspect返回材料身份、当前修订及下一步。保存为本地记录，不办理财务。"""
        return service.case_materials(case_id,action,values,expected_revision,confirmation_note,execute,offset,limit)

    @server.tool(annotations=local, structured_output=True)
    @expected_errors
    def reimbursement_case_delivery(case_id: str, action: Literal['inspect','plan','export','status','resume']='inspect',business_date: str | None=None,sequence: int | None=None,kind: Literal['details','formal']='details',include_invoice_attachments: bool | None=None,plan_id: str | None=None,confirmation_note: str | None=None,execute: bool=False) -> dict[str,Any]:
        """本案交付：inspect查看原计划；plan只填写已核业务日期、序号、明细/正式打印及附件选项，账号单号和来源自动绑定。核对后export使用固定HTTP导出，status独立检查PDF字节，resume只接续原导出，不重造文件或覆盖已有PDF。不要求手抄计划文件或job。"""
        return service.case_delivery(case_id,action,business_date,sequence,kind,include_invoice_attachments,plan_id,confirmation_note,execute)

    @server.tool(annotations=local, structured_output=True)
    @expected_errors
    def reimbursement_standards_import(bundle: dict,execute: bool=False,expected_snapshot: str | None=None) -> dict[str,Any]:
        """预览或导入用户选定的私人规范包ustc-private-standards-bundle-v1。自动使用安装时私人库，仅新增专项或替换空模板，已有非空专项不同则拒绝。预览显示规则范围及库快照；执行必须原expected_snapshot，保留原包/旧版/回读及事务。材料规则存在私人库，不修改发行源码或财务记录。"""
        return service.standards_import(bundle,execute,expected_snapshot)

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
    def reimbursement_standards_read(library_root: str | None = None, profile_id: str | None = None) -> dict[str, Any]:
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
    def reimbursement_pdf_export(case_id: str, export_plan: dict, export_root: str, tab_id: str, expected_userid: str, request_id: str, silent: bool = True, execute: bool = False, resume_job_id: str | None = None, source_job_id: str | None = None) -> dict[str, Any]:
        """固定HTTP导出同案PDF：details为学校打印历史明细预览，formal_print_api为正式打印。默认仅计划、静默；日期/序号/摘要及账号/单号/金额绑定，逐块和整件SHA核验原始字节，拒绝覆盖。resume_job_id只恢复已确认生成的原文件，未知生成不重发；source_job_id可引用完整本人清单归档历史单，逐项核原日期/摘要/金额及归档序号，不绑定本案单号。预览不算正式打印或批准。"""
        return service.pdf_export(case_id, export_plan, export_root, tab_id, expected_userid, request_id, silent, execute, resume_job_id, source_job_id)

    @server.tool(annotations=ocr_work, structured_output=True)
    @expected_errors
    def reimbursement_print_batch(case_id: str, request_id: str, merge_pdf: bool | None = None,
                                  finance_only: bool | None = None, output_root: str | None = None,
                                  sequences: list[int] | None = None, execute: bool = False) -> dict[str, Any]:
        """批量导出审批后的正式报销单。merge_pdf与finance_only独立；缺省读取本批配置，最终默认false/true。默认预览，执行返回job_id后查result；中断保留原任务。"""
        return service.print_batch(case_id, request_id, merge_pdf, finance_only, output_root, sequences, execute)

    @server.tool(annotations=read, structured_output=True)
    @expected_errors
    def reimbursement_business(case_id: str, action: str, tab_id: str, expected_userid: str,
                               request_id: str, domain: str | None = None, source_job_id: str | None = None,
                               print_job_id: str | None = None, reference_serial: str | None = None, staff_query: str | None = None, notes_xtype: str | None = None) -> dict[str, Any]:
        """固定HTTP：bills-list完整本人单据；approvals-list完整当前审批人清单（角色来自原生记录，不能推定权限）；bill-read读取基本/经费/支出/支付/审批/劳务/说明/预审/已归属票据；invoices-list读取日常票池，recognition-list读取识别明细，invoice-inventory完整读取两者用于上传后回读；upload-prepare核第一批真实原件但不上传；create-prepare核学校建单定义、模板及默认值，不创建；definitions核原生定义；basic/payments/notes/pre-review-prepare核本案原字段和变更计划，不保存；bills-export引用完整清单source_job_id导出原生EXCEL/PDF并逐单逐列核验，bills-export-recover引用原导出job只收取已生成文件；print-coverage核待投递覆盖；reference-bill-read凭同案完整本人清单source_job_id及reference_serial只读查历史单，不绑定本案或提供保存来源；domain=archive-summary保留七个完整摘要域，票据与分配另读；staff-lookup按staff_query教工号查询当前教师，不绑定单号、不保存。无点击、不审批。"""
        return service.business(case_id, action, tab_id, expected_userid, request_id, domain, source_job_id, print_job_id, reference_serial, staff_query, notes_xtype)

    @server.tool(annotations=read, structured_output=True)
    @expected_errors
    def reimbursement_pdf_export_inspect(case_id: str, job_id: str) -> dict[str, Any]:
        """PDF导出失败/中断后只读原任务意图、分块回执与文件SHA；不启动浏览器、不换任务重导、不清原marker。"""
        return service.pdf_export_inspect(case_id, job_id)

    @server.tool(annotations=local, structured_output=True)
    @expected_errors
    def reimbursement_finance_feedback(case_id: str, source_job_id: str,
            serials: list[str] | None = None, include_history: bool = False,
            output_dir: str | None = None, execute: bool = False) -> dict[str, Any]:
        """从同案完整bills-list回执获取财务挂单/审批退回原文；保留单号、状态、采集时间和来源SHA。历史意见默认分离，缺理由明确列出。读取不启动浏览器；execute仅在明确output_dir时生成全新HTML复制页、文本和Excel交付计划，不修改财务。"""
        return service.finance_feedback(case_id, source_job_id, serials, include_history, output_dir, execute)

    @server.tool(annotations=local, structured_output=True)
    @expected_errors
    def reimbursement_intake_review(case_id: str, intake_job_id: str | None = None, action: Literal['batches','inspect','preview','register'] = 'inspect',
            records: list[dict] | None = None, expected_revision: str | None = None,
            confirmation_note: str | None = None, execute: bool = False, offset: int = 0, limit: int = 50,
            material_id: str | None = None, page: int = 1) -> dict[str, Any]:
        """查看成功归档的材料并登记人工核对。inspect分页返回材料ID/来源SHA/页数/票号候选；register须逐项类别、完整票号页码、核对依据和刚读取的revision。默认预览；execute只经update_case登记本案候选来源，不选票、不更改核定金额、不写财务。普通电子发票20位；其他票据保留专用流程。"""
        return service.intake_review(case_id, intake_job_id, action, records, expected_revision, confirmation_note, execute, offset, limit, material_id, page)

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
    def reimbursement_guide(topic: GuideTopic = 'start', language: Literal['zh', 'en'] = 'zh') -> dict[str, Any]:
        """Read task guidance in Chinese or English / 按需读取中英文说明。language只影响文档，不改变权限、案件和请求。参数正本仍为tool_catalog。"""
        return service.guide(topic, language)

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
    def reimbursement_new_case(case_id: str, profile: CaseProfile = 'student_activity', title: str | None = None) -> dict[str, Any]:
        """建立空案件和五份参数文件。不会复用旧事实、授权或建财务单；已有case_id拒绝覆盖。"""
        return service.new_case(case_id, profile, title)

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
    def reimbursement_access(case_id: str, request_id: str, execute: bool = False, tab_id: str | None = None, action: Literal['check', 'check-page', 'check-backend', 'diagnose', 'ensure', 'new-interface', 'recover-interface', 'wake-interface', 'restore-context', 'applicant-verification-open', 'remuneration-activity-setup', 'remuneration-add-person', 'remuneration-basic-open', 'remuneration-clear-length-rejection', 'remuneration-close-person', 'remuneration-confirm-added', 'remuneration-confirm-create', 'remuneration-create-claim', 'remuneration-entry', 'remuneration-expenses-open', 'remuneration-funding-open', 'remuneration-funding-other-add', 'remuneration-funding-other-open', 'remuneration-funding-other-select', 'remuneration-funding-search', 'remuneration-inspect', 'remuneration-save', 'remuneration-readback', 'remuneration-recover', 'remuneration-lookup-person', 'remuneration-open-person', 'remuneration-payment-close', 'remuneration-payment-inspect', 'remuneration-payments-open', 'remuneration-student-category', 'remuneration-teacher-lookup'] | None = None, job_id: str | None = None, artifact_id: str | None = None, expected_userid: str | None = None, silent: bool | None = None, separate_window: bool | None = None, student_id: str | None = None, query: str | None = None, plan: dict | None = None, authority: dict | None = None, session_only: bool | None = None) -> dict[str, Any]:
        """财务检查、登录和本案薪酬办理。full_control按授权定位财务页；new-interface可separate_window，restore-context恢复本案上下文。薪酬经SHA原件、人员查询、添加和独立清单回读后建单；添加及建单须来源job_id/artifact_id，确认只承接原pending，不重放。查guide(remuneration/execution)，仍不代其他角色审批或输入验证码。"""
        params = {k: v for k, v in dict(tab_id=tab_id, action=action, job_id=job_id, artifact_id=artifact_id, expected_userid=expected_userid, silent=silent, separate_window=separate_window, student_id=student_id, query=query, plan=plan, authority=authority, session_only=session_only).items() if v is not None}
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
        """异常票据固定HTTP：inspect完整读取本人53/113及动作定义；preview按同案原件SHA/票号/原生ID冻结范围。cleanup仅登记清理意图，不删除；新发inspect后confirm才一次删除明确异常记录。正常/已绑定/无错误记录受保护。readback引用原confirm或其原marker恢复及两个新完整查询，ack-write核目标消失和非目标ID保留后清pending；明确服务端拒绝须完整回读未变化才可not_applied。recover只读原marker，不重发。refresh为HTTP查询，system_write=false；单票重识别先核原生检查和源文件确认，再调用固定OCR接口一次，独立回读正常认证或needs_correction；处理中不解pending，不退回旧控件。不释放他人预约，不自动重传。"""
        return service.invoice_retry(case_id, action, tab_id, request_id, execute,
            expected_userid, grid, target_guids, target_filecodes, planned_action, targets, source_job_id, source_artifact_id,
            pool_job_id, pool_artifact_id, recognition_job_id, recognition_artifact_id, inspection_job_id, inspection_artifact_id,
            plan_job_id, plan_artifact_id, receipt_job_id, receipt_artifact_id, job_id, artifact_id)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True), structured_output=True)
    @expected_errors
    def reimbursement_invoice_selection(case_id: str, action: Literal['preview', 'apply', 'readback', 'recover'], tab_id: str, request_id: str, execute: bool = False, source_job_id: str | None = None, source_artifact_id: str | None = None, audit_refs: list[dict[str, str]] | None = None, selectors: list[dict[str, str]] | None = None, mode: Literal['append', 'replace', 'remove'] | None = None, grid: Literal['53', '1669'] | None = None, serial: str | None = None, expected_userid: str | None = None, expected_before_guids: list[str] | None = None, snapshot_job_id: str | None = None, snapshot_artifact_id: str | None = None, preview_job_id: str | None = None, preview_artifact_id: str | None = None, job_id: str | None = None, artifact_id: str | None = None) -> dict[str, Any]:
        """完整HTTP票池+同案原件/票面审核冻结GUID选择。preview默认只读；apply须execute，仅原子保存本案manifest中的选择集，system_write=false，不改变页面勾选、不建单或提交。追加/替换/移除保留来源与审核SHA，抬头税号及已占用检查继续生效；已有单号不适用本入口。readback比较本地GUID集及新HTTP完整票池。recover按原job本地意图和前后SHA恢复，不覆盖其他编辑；旧控件预览不能派发。"""
        return service.invoice_selection(case_id, action, tab_id, request_id, execute,
            source_job_id, source_artifact_id, audit_refs, selectors, mode, grid, serial,
            expected_userid, expected_before_guids, snapshot_job_id, snapshot_artifact_id,
            preview_job_id, preview_artifact_id, job_id, artifact_id)

    @server.tool(annotations=live, structured_output=True)
    @expected_errors
    def reimbursement_stage(case_id: str, stage: Literal['basic', 'funding', 'categories', 'expenses', 'payments', 'notes', 'pre-review', 'no-contract', 'audit', 'review'], request_id: str, execute: bool = False, tab_id: str | None = None,noop_only: bool = False) -> dict[str, Any]:
        """草稿单阶段计划/执行；basic/funding/expenses/payments/notes/pre-review走固定HTTP，各阶段范围见business指南。execute=true需当前tab_id和本案账号参数；单次保存后business bill-read独立回读，再record_result，不重放未知写入。真实变更保存尚待验收。"""
        return service.enqueue(case_id, 'stage', {'stage': stage, 'tab_id': tab_id,'noop_only':noop_only}, request_id, execute)

    @server.tool(annotations=live, structured_output=True)
    @expected_errors
    def reimbursement_invoice(case_id: str, action: Literal['open-pool', 'open-upload', 'upload', 'upload-resume','upload-release', 'upload-recognize','upload-recognize-resume','upload-recover-recognition','nonvat-inspect','nonvat-save','nonvat-readback','nonvat-recover','nonvat-select','allowance-inspect','allowance-prepare','allowance-create','allowance-readback','allowance-recover','draft-cancel-prepare','draft-cancel','draft-cancel-recover','domestic-allowance-inspect','domestic-travel-add-prepare','domestic-travel-add-save','domestic-travel-add-recover','domestic-allowance-edit-prepare','domestic-allowance-edit-save','domestic-allowance-edit-recover','domestic-allowance-finalize', 'read', 'save-upload', 'create-draft','resume-draft'], request_id: str, execute: bool = False, tab_id: str | None = None, start: int = 0, count: int = 10, job_id: str | None = None) -> dict[str, Any]:
        """upload按来源SHA分块HTTP上传并一次保存，每批1至10份、每份≤5MiB。upload-resume只接原job及未发出的子阶段。upload-recognize引用原upload job，接续已保存PDF/OFD的原生识别，保留原pending；upload-recognize-resume引用原识别job，只发尚未准备的目标，未知请求不重发。随后business invoice-inventory完整独立回读及ack-write。create-draft核本案冻结选择、原件审核和学校分支后单次创建；完整GUID/金额回读后bind-serial。resume-draft只读原marker，不重建。未迁移写动作禁止点击回退。"""
        p={'action':action,'tab_id':tab_id}
        original_actions={'upload-resume','resume-draft','upload-recognize','upload-recognize-resume','upload-recover-recognition','nonvat-readback','nonvat-recover','allowance-prepare','allowance-create','allowance-readback','allowance-recover','draft-cancel','draft-cancel-recover','domestic-travel-add-save','domestic-travel-add-recover','domestic-allowance-edit-save','domestic-allowance-edit-recover'}
        if action in original_actions:p['job_id']=job_id
        elif action in {'upload','nonvat-save','domestic-travel-add-prepare','domestic-allowance-edit-prepare'}:
            p['start']=start
            if action=='upload':p['count']=count
        if action not in original_actions and job_id is not None:raise ValueError('job_id只用于固定业务原准备、保存回读或恢复任务')
        return service.enqueue(case_id, 'invoice', p, request_id, execute)

    @server.tool(annotations=live, structured_output=True)
    @expected_errors
    def reimbursement_correction(case_id: str, action: Literal['check', 'inspect', 'recover', 'verify', 'apply', 'begin', 'open', 'select', 'update', 'save', 'return', 'remark', 'basic', 'basic-read', 'basic-close', 'drafts', 'resume', 'returned-prepare', 'returned-open', 'returned-confirm', 'returned-recover'], plan: dict, request_id: str, execute: bool = False, tab_id: str | None = None, group: str = '', key: str = '', kind: str = '', job_id: str | None = None, artifact_id: str | None = None) -> dict[str, Any]:
        """退回原单续编及逐票拆分。returned-*用空plan：prepare核本人拒绝原单；open引用prepare；学校明确确认后confirm引用原open；recover只读原marker。同号草稿独立回读后才更新本地流程标记，不新建、不审批。专项拆分plan含原件SHA、GUID、人员、往返用途、经费与限额，仍须独立verify/ack-write。"""
        if action.startswith('returned-'):
            if plan or group or key or kind or artifact_id:raise ValueError('退回原单接续仅空plan、当前tab及原job_id')
            return service.enqueue(case_id,'correction',{'action':action,'tab_id':tab_id,**({'job_id':job_id}if job_id else{})},request_id,execute)
        return service.enqueue(case_id, 'correction', {'action': action, 'plan': plan, 'tab_id': tab_id, 'group': group, 'key': key, 'kind': kind, 'job_id': job_id, 'artifact_id': artifact_id}, request_id, execute)

    @server.tool(annotations=live, structured_output=True)
    @expected_errors
    def reimbursement_applicant_batch(case_id: str, manifest: dict, tab_id: str, request_id: str, execute: bool = False) -> dict[str, Any]:
        """既有单据的本人508节点批量同意。manifest须路径及SHA，逐单绑定输入和用户授权；保留短信停点、原pending、已完成跳过、独立回读和耗时。原request重连续办，不建单、不代其他角色审批或填验证码。"""
        return service.applicant_batch(case_id,manifest,tab_id,request_id,execute)

    @server.tool(annotations=live, structured_output=True)
    @expected_errors
    def reimbursement_submission(case_id: str, action: SubmissionAction, plan: dict, request_id: str, execute: bool = False, tab_id: str | None = None,job_id: str | None = None,artifact_id: str | None = None) -> dict[str, Any]:
        """inspect/options已接固定HTTP：完整合同/打印及其他辅助证据分组，保存后新的inspect再ack-write；无点击回退。其他流程写节点仍按迁移表核对。按本案明确授权逐步办理同号单据；applicant-agree另须applicant_agreement_authorized、意见同意及当前账号，限申请人508节点；验证码留现场，不代领导审批或付款。"""
        return service.enqueue(case_id, 'submission', {'action': action, 'plan': plan, 'tab_id': tab_id,'job_id':job_id,'artifact_id':artifact_id}, request_id, execute)

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

    @server.tool(annotations=read, structured_output=True)
    @expected_errors
    def reimbursement_task_view(case_id: str, job_id: str, wait_seconds: float = 0) -> dict[str, Any]:
        """优先用此入口读取任务结论和可直接调用的下一步；最多等待原job 10秒，不重发。保留来源引用、完整性与未核状态，默认不回传大段原始JSON；完整依据按details_call读取。程序完成不冒称财务完成。"""
        result=service.task_view(case_id,job_id,wait_seconds)
        if toolset=='compact':
            from agent_assist import compact
            return compact(result,case_id=case_id)
        return result

    @server.tool(annotations=local, structured_output=True)
    @expected_errors
    def reimbursement_case_details(case_id: str, action: Literal['inspect','draft','save'] = 'inspect',
            values: dict | None = None, expected_revision: str | None = None,
            confirmation_note: str | None = None, execute: bool = False) -> dict[str, Any]:
        """日常报销中文表单。inspect返回字段合同、已有值、全部显式缺项和revision；draft只暂存未确认填写；save须确认依据及原revision，复用update_case保存本地参数。不会填学校网页、创建单据或改变审批状态。个人收款仅姓名/学工号，银行卡由学校候选核实。"""
        return service.case_details(case_id,action,values,expected_revision,confirmation_note,execute)

    @server.tool(annotations=read, structured_output=True)
    @expected_errors
    def reimbursement_case_journey(case_id: str, action: Literal['inspect','invoice'] = 'inspect', index: int | None = None) -> dict[str, Any]:
        """按实际来源、票面核对、填写和原任务状态给出可执行下一步。inspect不会要求在建单前虚填单号；invoice返回本案SHA核实的原件核对信息。未知写入先回原任务。"""
        return service.case_journey(case_id,action,index)

    @server.tool(annotations=local, structured_output=True)
    @expected_errors
    def reimbursement_finance_connection(action: Literal['status','install_bridge','discover','verify','accept','bills','approvals','history'] = 'status',
            expected_userid: str | None = None, tab_id: str | None = None, connection_id: str | None = None,
            source_job_id: str | None = None, request_id: str | None = None, execute: bool = False) -> dict[str, Any]:
        """连接本人本机Edge账号，不接收密码或验证码。status/discover查连接；verify明确学工号、选中tab和request_id进行只读核验；原任务结束后accept原source_job_id得到connection_id；bills/approvals/history以新查询request_id分别读单据、待审批或历史清单。只有install_bridge需execute=true，登记当前Windows用户组件；不会写财务。无需先创建活动。"""
        return service.finance_connection(action,expected_userid,tab_id,connection_id,source_job_id,request_id,execute)

    @server.tool(annotations=local, structured_output=True)
    @expected_errors
    def reimbursement_material_requirements(case_id: str, action: Literal['inspect','context','acknowledge'] = 'inspect',
            values: dict | None = None, expected_revision: str | None = None, note: str | None = None, execute: bool = False) -> dict[str, Any]:
        """逐项显示当前材料规则、缺件和条件。context只登记有依据的已知布尔条件；acknowledge记录已查看差项以继续其他准备，所有缺件原样保留，绝不标记材料齐全。仅修改本地资料，须当前revision。"""
        return service.material_requirements(case_id,action,values,expected_revision,note,execute)

    @server.tool(annotations=local, structured_output=True)
    @expected_errors
    def reimbursement_intake_groups(case_id: str, intake_job_id: str, groups: list[dict] | None = None,
            expected_revision: str | None = None, execute: bool = False) -> dict[str, Any]:
        """多活动收件分组。先读取本案已成功归档的材料和目录候选；groups显式给title/profile/material_ids预览，核对范围后execute。每份材料只能归一个子活动；未分配保留原批次。沿冻结分组计划重连不重复建活动，不推定金额、票面通过或财务授权。"""
        return service.intake_groups(case_id,intake_job_id,groups,expected_revision,execute)

    @server.tool(annotations=local, structured_output=True)
    @expected_errors
    def reimbursement_receive_materials(case_id: str, paths: list[str], request_id: str,
            grouping: Literal['unassigned','directories'] = 'unassigned', execute: bool = False,
            source_sha256: str | None = None) -> dict[str, Any]:
        """从明确选定的本机原文件/材料文件夹收件，包括桌面路径。预览冻结逐文件SHA，沿next_action确认后将校验副本送入资料区，再用原material_intake归档。原文件保留，不扩大其他工具权限。文件或目录内容改变须重新预览。目录仅为分组候选，金额另核。"""
        return service.receive_materials(case_id,paths,request_id,grouping,execute,source_sha256)

    @server.tool(annotations=live, structured_output=True)
    @expected_errors
    def reimbursement_case_finance(case_id: str, action: Literal['inspect','bind','source_audit','pool','selection_preview','selection_apply','upload','create','bind_created','fill_plan','fill_continue','fill_pause','fill_status','fill_recover','ready_inspect','ready_plan','ready_continue','ready_status','ready_pause','ready_recover']='inspect',
            connection_id: str | None=None, job_id: str | None=None, invoice_numbers: list[str] | None=None,
            start: int=0, count: int=1, plan_id: str | None=None, authorization_note: str | None=None,
            request_id: str | None=None, execute: bool=False) -> dict[str,Any]:
        """本案财务向导。先inspect和bind已核本人连接；账号、tab和正式来源自动带入。原票audit、票池、选票预览/保存、建单/绑定仍是分开的步骤；fill_plan冻结完整填单范围，授权后fill_continue逐步接续原任务并独立回读，fill_pause暂停后续阶段。只用原job和next_action，不猜单号/重复建单，不模拟界面。"""
        return service.case_finance(case_id,action,connection_id,job_id,invoice_numbers,start,count,plan_id,authorization_note,request_id,execute)

    @server.tool(annotations=live, structured_output=True)
    @expected_errors
    def reimbursement_approver_batch(action: Literal['list','inspect','plan','status','run','resume','recover','reconcile','verification','pause','finalize'] = 'list',
            connection_id: str | None = None, source_job_id: str | None = None, selected_ids: list[str] | None = None,
            decision: Literal['agree','reject'] = 'agree', opinion: str | None = None, plan_id: str | None = None,
            authorization_note: str | None = None, request_id: str | None = None, execute: bool = False) -> dict[str, Any]:
        """按当前登录人的真实审批角色办理。list/status接续原批；inspect从本人收件箱完整待办列可选项；plan冻结所选项、角色、金额及逐字意见。run须明确本批授权和execute；verification只展示同项本人验证页，不读取验证码；resume沿原范围继续；pause只暂停后续项目；finalize只补已回读项目的文档；recover/reconcile只读原结果，不重发未知审批。每项独立核对日志及金额，步骤完成不等于整单审批或付款完成。"""
        return service.approver_batch(action,connection_id,source_job_id,selected_ids,decision,opinion,plan_id,authorization_note,request_id,execute)

    @server.resource('reimbursement://guide/{topic}')
    def read_guide(topic: str) -> str:
        """项目权威使用文档；topic见guide工具枚举。"""
        try: return service.guide(topic)['text']
        except ValueError as e: raise ResourceError(str(e)) from e
    if toolset == 'compact':
        from assistant_contract import json_routes
        for name in json_routes():
            if name not in {'reimbursement_assist','reimbursement_capabilities','reimbursement_task_view'}:
                server.remove_tool(name)
    elif toolset != 'full':
        raise ValueError('toolset须为full或compact')
    return server


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--workspace', type=str, default=str(PROJECT))
    ap.add_argument('--material-root', action='append', help='可多次指定，缺省项目所在活动目录；只声明材料路径，不包含登录资料')
    ap.add_argument('--toolset', choices=['full','compact'], default='full')
    args = ap.parse_args()
    create_server(AssistantService(args.workspace, args.material_root), args.toolset).run(transport='stdio')


if __name__ == '__main__': main()
