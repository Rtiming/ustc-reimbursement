#!/usr/bin/env python3
"""助手项目入口。batch用明确参数接续正式批次；call保留同一接口的JSON调用。"""
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT/'tools'))
from assistant_api import APIError as APIError, AssistantService
from assistant_contract import dispatch_request, WORKFLOW_PHASES, CASE_PROFILES


def batch_index_path(value):
    path = Path(value)
    if not path.is_absolute():
        raise argparse.ArgumentTypeError('batch-index须为正式索引的本机绝对路径')
    return path


def batch_sequences(value):
    parts = [part.strip() for part in value.split(',')]
    if not parts or any(not part.isascii() or not part.isdecimal() or int(part) < 1 for part in parts):
        raise argparse.ArgumentTypeError('sequences使用逗号分隔的正整数，如4,5')
    sequences = [int(part) for part in parts]
    if len(set(sequences)) != len(sequences):
        raise argparse.ArgumentTypeError('sequences不能重复，避免重复安排同一单据')
    return sequences


def legacy_readback(value):
    parts = value.split(':')
    if len(parts) != 3 or any(not part for part in parts):
        raise argparse.ArgumentTypeError('legacy-readback使用CASE:JOB:ARTIFACT')
    return dict(zip(('case_id', 'job_id', 'artifact_id'), parts))


def grouping_depth(value):
    if value == 'none':
        return None
    if not value.isascii() or not value.isdecimal() or not 0 <= int(value) <= 20:
        raise argparse.ArgumentTypeError('group-strip-dirs使用0至20或none（暂不分组）')
    return int(value)


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--browser-backend', choices=['edge_applescript','edge_native_messaging'],
                    help='显式选择浏览器通道；失败或超时不自动回退')
    ap.add_argument('--tab-namespace', help='Mac后台通道当前inventory给出的完整标签命名空间')
    ap.add_argument('--window-id', help='与当前后台标签同次inventory核实的窗口ID')
    ap.add_argument('--native-only-isolation', action='store_true',
                    help='原生通道自洽隔离：不用AppleEvent证明旧标签已关闭，只绑定本次原生标签并记录AE关闭状态不可证')
    ap.add_argument('--legacy-ae-tab-proof', action='store_true',
                    help='仅Mac后台通道遗留外案隔离：额外读取原AppleEvent完整标签属性，不切换业务通道')
    sub = ap.add_subparsers(dest='command', required=True)
    bridge = sub.add_parser('bridge', help='本机原生扩展准备、安装预览与只读诊断')
    bridge.add_argument('action', choices=['prepare','install','status','inventory','diagnose',
                                          'finalize-normal-launcher','recover','tab-close','ping','reopen','update','new-window','relocate-frozen','installation-result'])
    bridge.add_argument('--execute', action='store_true', help='prepare/install实际执行；默认先预览')
    bridge.add_argument('--request-id', help='Windows交互安装的稳定请求标识')
    bridge.add_argument('--expected-registration-sha256', help='Windows安装预览返回的本机登记SHA')
    bridge.add_argument('--channel', choices=['stable','beta','dev','canary'], default='stable')
    bridge.add_argument('--python', type=batch_index_path, help='本机绝对Python解释器；不安装或下载运行时')
    bridge.add_argument('--phase', choices=['check','auto','sweep','verify'],
                        help='recover：check只读诊断；auto受控停止等待并重建连接；verify验收队列真的在执行')
    bridge.add_argument('--output', type=batch_index_path, help='recover回执的绝对路径')
    bridge.add_argument('--tab-id', help='recover auto/verify用本次inventory的数字标签')
    bridge.add_argument('--window-id', help='recover auto/verify用同次inventory的窗口ID')
    bridge.add_argument('--reason', help='recover auto写入恢复意图的原因说明')
    bridge.add_argument('--installed-review',type=batch_index_path,help='update专用：已核旧安装源码差异的冻结SHA回执')
    contacts = sub.add_parser('contacts', help='按原纸复核准备新联系人版本，或串行仅修正已登记草稿备注')
    contacts.add_argument('--phase', choices=['prepare','preview','run'], default='preview')
    contacts.add_argument('--batch-index', type=batch_index_path, required=True)
    contacts.add_argument('--manifest', type=batch_index_path)
    contacts.add_argument('--contact-review', type=batch_index_path, action='append')
    contacts.add_argument('--output-dir', type=batch_index_path)
    contacts.add_argument('--library-root', type=batch_index_path)
    contacts.add_argument('--profile-id')
    contacts.add_argument('--as-of')
    contacts.add_argument('--sequences', type=batch_sequences)
    contacts.add_argument('--tab-id')
    contacts.add_argument('--expected-userid')
    contacts.add_argument('--execute', action='store_true')
    contacts.add_argument('--material-root', action='append')
    confirmation = sub.add_parser('confirmation-read', help='只读Excel黄色确认栏，校验冻结来源，生成待复核答案')
    confirmation.add_argument('--workbook', type=batch_index_path, required=True)
    confirmation.add_argument('--manifest', type=batch_index_path, required=True)
    confirmation.add_argument('--output', type=batch_index_path, required=True)
    s = sub.add_parser('setup', help='配置项目专用MCP环境及客户端片段，不改助手全局配置')
    s.add_argument('--check-only', action='store_true')
    s.add_argument('--with-materials', action='store_true', help='同时配置锁定的PDF和图片材料依赖，不下载OCR模型')
    s.add_argument('--with-ocr', action='store_true', help='显式安装并启用本机OCR模型；默认不启用')
    s.add_argument('--material-root', action='append', help='MCP可读取的本机材料目录，可重复；不搬移案件或登录状态')
    c = sub.add_parser('call', help='标准JSON请求：method和params；stdout仅JSON结果')
    c.add_argument('--input', type=Path, required=True)
    c.add_argument('--material-root', action='append')
    b = sub.add_parser('batch', help='按注册专项和正式批次索引接续；默认仅状态，不需手写请求JSON')
    b.add_argument('--scenario', required=True, help='规范库已登记的profile_id；不提供默认专项')
    b.add_argument('--batch-index', required=True, type=batch_index_path, help='正式工具生成的索引绝对路径')
    b.add_argument('--phase', choices=['status', 'run', 'recover', 'verify'], default='status',
                   help='status只查状态；run接续办理；recover恢复原请求；verify只读核验已有草稿并派生本地完成证据')
    b.add_argument('--sequences', type=batch_sequences, help='仅处理指定整理序号，如4,5；省略时由正式索引确定范围')
    b.add_argument('--tab-id', help='执行时明确的当前财务标签，不从旧任务猜测')
    b.add_argument('--expected-userid', help='本批确认的当前登录工号，不继承旧案人员')
    b.add_argument('--execute', action='store_true',
                   help='仅run/recover/verify接受；省略时不访问浏览器，recover/verify执行也不重放写入')
    b.add_argument('--allow-incomplete', action='store_true',
                   help='仅来源及基本信息/经费已核而收款未知的案先办funding/basic；仍不放行付款、审核或提交')
    b.add_argument('--material-root', action='append', help='本次可访问的本机材料目录，可重复')
    w = sub.add_parser('workflow', help='逐案接续、确认已保存的超时恢复及清单交付；不需改代码或请求文件')
    w.add_argument('--case-id', help='已登记批次入口；自动读取本案指针、登录人、工作窗和准备授权')
    w.add_argument('--scenario')
    w.add_argument('--batch-index', type=batch_index_path)
    w.add_argument('--phase', choices=WORKFLOW_PHASES, default=None)
    w.add_argument('--submission-authorization', type=batch_index_path, help='批量提交授权JSON，绑定本批SHA、账号、单号及本人同意')
    w.add_argument('--sequences', type=batch_sequences)
    w.add_argument('--tab-id')
    w.add_argument('--expected-userid')
    w.add_argument('--execute', action='store_true')
    w.add_argument('--allow-incomplete', action='store_true')
    w.add_argument('--delivery-plan', type=batch_index_path)
    w.add_argument('--output-root', type=batch_index_path)
    w.add_argument('--legacy-readback', type=legacy_readback, action='append', default=[])
    w.add_argument('--render-excel', action='store_true')
    w.add_argument('--final-delivery', action='store_true', help='只在全部原任务已核清时结束交付快照；仍有pending会拒绝')
    w.add_argument('--node', type=batch_index_path, help='本机受信任Node运行时；与module-root成对')
    w.add_argument('--module-root', type=batch_index_path, help='本机受信任node_modules目录')
    w.add_argument('--material-root', action='append')
    w.add_argument('--full-output', action='store_true', help='执行后的stdout包含完整报告；缺省只给状态、计数与已落盘报告路径')
    m = sub.add_parser('materials', help='来源、分组与输出用参数给定；复用正式材料归档/识别候选入口')
    m.add_argument('--case-id', required=True)
    m.add_argument('--source', type=batch_index_path, action='append', required=True)
    m.add_argument('--output-dir', type=batch_index_path, required=True)
    m.add_argument('--group-strip-dirs', type=grouping_depth, required=True)
    m.add_argument('--case-profile', choices=CASE_PROFILES, default='student_activity')
    m.add_argument('--field-review', type=batch_index_path)
    m.add_argument('--wait-seconds', type=float, default=300)
    m.add_argument('--execute', action='store_true')
    m.add_argument('--create-case', action='store_true')
    m.add_argument('--material-root', action='append')
    args = ap.parse_args()
    if getattr(args, 'native_only_isolation', False):
        os.environ['REIMBURSEMENT_NATIVE_ONLY_ISOLATION'] = '1'
    if args.browser_backend:
        os.environ['REIMBURSEMENT_EDGE_BACKEND'] = args.browser_backend
    if args.tab_namespace:
        os.environ['REIMBURSEMENT_EDGE_TAB_NAMESPACE'] = args.tab_namespace
    if args.window_id:
        os.environ['REIMBURSEMENT_EDGE_WINDOW_ID'] = args.window_id
    if args.legacy_ae_tab_proof:
        os.environ['REIMBURSEMENT_LEGACY_AE_INVENTORY_PROOF'] = '1'
    if args.command == 'batch' and args.phase == 'status' and args.execute:
        ap.error('status不接受--execute；明确执行须选择run、recover或verify')
    if args.command == 'workflow':
        if args.phase is None:args.phase='ready' if args.case_id and args.execute else 'status'
        if args.case_id:
            if args.scenario or args.batch_index or args.tab_id or args.expected_userid or args.submission_authorization:
                ap.error('登记批次入口从本案配置读取身份与资料，不混入手写运行参数')
        elif not args.scenario or not args.batch_index:ap.error('给--case-id，或明确scenario与batch-index')
        if args.phase == 'status' and args.execute:
            ap.error('status不接受--execute')
        if bool(args.node) != bool(args.module_root):
            ap.error('node与module-root须成对给出')
        if not args.case_id and args.phase in ('submit','prepare-submit') and not args.submission_authorization:
            ap.error('submit须给--submission-authorization，绑定本批及当前账号的明确授权')
    if args.command == 'setup':
        from setup_reimbursement_mcp import setup
        r = setup(args.check_only, with_materials=args.with_materials, material_roots=args.material_root, with_ocr=args.with_ocr)
    elif args.command == 'bridge':
        if args.action in {'status', 'inventory', 'prepare', 'install', 'installation-result'} and not args.python and args.channel == 'stable':
            if args.phase or args.output or args.tab_id or args.window_id or args.reason:
                ap.error('接线入口不接受财务标签或恢复参数')
            from bridge_service import run as bridge_action
            r = bridge_action(args.action, args.execute, args.request_id, args.expected_registration_sha256)
        elif sys.platform == 'win32':
            ap.error('Windows使用bridge status/prepare/install/installation-result/inventory；财务工作页由登记流程绑定')
        elif args.action == 'inventory':
            if args.python: ap.error('inventory不接受--python')
            if args.phase or args.output or args.tab_id or args.window_id or args.reason: ap.error('inventory不接受recover参数')
            from mac_edge_bridge import inventory
            r = inventory()
        elif args.action in {'tab-close', 'ping'}:
            if args.python or args.channel != 'stable': ap.error('救援操作不接受--python或非stable通道')
            if args.phase or args.reason: ap.error('救援操作不接受--phase/--reason')
            import mac_edge_bridge as rescue_bridge
            if args.action == 'ping':
                if args.tab_id or args.window_id or args.output: ap.error('ping不接受标签/输出参数')
                r = rescue_bridge.ping()
            else:
                if not args.tab_id or not args.window_id: ap.error('tab-close须给--tab-id与--window-id')
                current = rescue_bridge.status()
                if current.get('ready') is not True: ap.error('扩展未就绪；不关标签')
                r = rescue_bridge.close_tab(args.tab_id,
                                            tab_id_namespace=current['tab_id_namespace'],
                                            expected_window_id=args.window_id)
                r = {**r, 'epoch': current['epoch']}
        elif args.action == 'relocate-frozen':
            if args.python or args.phase or args.reason or args.channel!='stable' or not args.tab_id or not args.window_id or not args.output:
                ap.error('relocate-frozen须给原工作页--tab-id、--window-id及新--output回执')
            if args.output.exists():ap.error('已有迁移回执，先读原回执和inventory，不重复迁移')
            from mac_edge_bridge import status, relocate_finance_tab
            from assistant_api import atomic_json
            current=status()
            args.output.parent.mkdir(parents=True,exist_ok=True)
            with args.output.open('x',encoding='utf-8') as intent:
                json.dump({'state':'relocation_requested','tab_id':args.tab_id,'old_window_id':args.window_id,'epoch':current['epoch'],'business_replayed':False},intent)
            r=relocate_finance_tab(args.tab_id,tab_id_namespace=current['tab_id_namespace'],expected_window_id=args.window_id)
            atomic_json(args.output,r)
        elif args.action == 'new-window':
            if args.python or args.phase or args.tab_id or args.window_id or args.reason or args.channel!='stable':ap.error('new-window只新开固定后台财务窗口，不接页面地址或旧窗口')
            from mac_edge_bridge import new_finance_window
            r=new_finance_window()
        elif args.action == 'reopen':
            if args.python or args.channel != 'stable': ap.error('reopen不接受--python或非stable通道')
            if args.phase or args.reason: ap.error('reopen不接受--phase/--reason')
            import mac_edge_bridge as rescue_bridge
            r = rescue_bridge.reopen_page(close_tab_id=args.tab_id, window_id=args.window_id)
        elif args.action == 'update':
            if args.python or args.channel!='stable' or args.tab_id or args.window_id or args.reason:
                ap.error('update只接受phase和output；固定更新本项目已登记扩展')
            from mac_bridge_update import apply,check,verify
            phase=args.phase or 'check'
            if phase=='check':r=check(args.installed_review)
            elif phase=='auto' and args.output:r=apply(args.output,args.installed_review)
            elif phase=='verify' and args.output and not args.installed_review:r=verify(args.output)
            else:ap.error('update使用check，或auto/verify并提供output')
        elif args.action == 'recover':
            if args.python or args.channel != 'stable': ap.error('recover不接受--python或非stable通道')
            if not args.output: ap.error('recover须给--output绝对回执路径')
            from mac_bridge_recover import auto, check, sweep, verify
            phase = args.phase or 'check'
            if phase == 'check' and (args.tab_id or args.window_id):
                ap.error('check不接标签/窗口参数')
            if phase == 'auto':
                r = auto(args.output, tab_id=args.tab_id, expected_window_id=args.window_id,
                         reason=args.reason)
            elif phase == 'sweep':
                if args.tab_id or args.window_id: ap.error('sweep不接标签/窗口参数')
                r = sweep(args.output)
            elif phase == 'verify':
                r = verify(args.output, tab_id=args.tab_id, expected_window_id=args.window_id)
            else:
                r = check(args.output)
        else:
            if args.phase or args.output or args.tab_id or args.window_id or args.reason: ap.error('此动作不接受recover参数')
            from mac_bridge_setup import prepare, install, capabilities, diagnose, finalize_normal_launcher
            if args.action in ['status','diagnose','finalize-normal-launcher']:
                if args.python: ap.error('此动作不接受--python')
                r = {'status':capabilities,'diagnose':diagnose,
                     'finalize-normal-launcher':finalize_normal_launcher}[args.action](channel=args.channel)
            else:
                if not args.execute:
                    r = {'ok': True, 'execute': False, 'action': args.action, 'channel': args.channel,
                         'python': str(args.python) if args.python else None, 'system_write': False}
                else:
                    r = {'prepare':prepare,'install':install}[args.action](channel=args.channel,
                        python_executable=str(args.python) if args.python else None)
    elif args.command == 'confirmation-read':
        from confirmation_workbook import read_answers
        r = read_answers(args.workbook, args.manifest, args.output)
    elif args.command == 'contacts':
        service = AssistantService(material_roots=args.material_root)
        if args.phase == 'prepare':
            if args.execute or args.manifest or args.sequences or args.tab_id or args.expected_userid:
                ap.error('prepare不接执行/现场参数；只生成新材料版本')
            if not all([args.contact_review,args.output_dir,args.library_root,args.profile_id,args.as_of]):
                ap.error('prepare须给contact-review、output-dir、library-root、profile-id及as-of')
            from contact_amendment_prepare import prepare_contact_amendment
            r = prepare_contact_amendment(service,batch_index=args.batch_index,
                contact_reviews=args.contact_review,output_dir=args.output_dir,
                library_root=args.library_root,profile_id=args.profile_id,as_of=args.as_of)
        else:
            if not args.manifest or any([args.contact_review,args.output_dir,args.library_root,args.profile_id,args.as_of]):
                ap.error('preview/run须给manifest，准备参数只用于prepare')
            if args.phase == 'preview' and args.execute:
                ap.error('preview不接受execute')
            from contact_amendment_run import run_amendments
            r = run_amendments(service,args.manifest,args.batch_index,args.sequences,
                args.tab_id,args.expected_userid,args.execute)
    elif args.command == 'materials':
        from materials_workflow import run_materials
        r = run_materials(AssistantService(material_roots=args.material_root),
            case_id=args.case_id, source_paths=[str(path) for path in args.source],
            output_dir=str(args.output_dir), group_strip_dirs=args.group_strip_dirs,
            execute=args.execute, create_case=args.create_case, case_profile=args.case_profile,
            field_review=str(args.field_review) if args.field_review else None, wait_seconds=args.wait_seconds)
    elif args.command == 'workflow':
        if args.case_id:
            from registered_workflow import run_registered_workflow
            r=run_registered_workflow(AssistantService(material_roots=args.material_root),case_id=args.case_id,phase=args.phase,execute=args.execute,
                progress=lambda event:print(json.dumps(event,ensure_ascii=False),file=sys.stderr,flush=True))
        else:
            from reimbursement_workflow import run_workflow
            runtime = {'node': str(args.node), 'module_root': str(args.module_root)} if args.node else None
            r = run_workflow(AssistantService(material_roots=args.material_root),
            scenario=args.scenario, batch_index=str(args.batch_index), phase=args.phase,
            sequences=args.sequences, tab_id=args.tab_id, expected_userid=args.expected_userid,
            execute=args.execute, allow_incomplete=args.allow_incomplete,
            delivery_plan=str(args.delivery_plan) if args.delivery_plan else None,
            output_root=str(args.output_root) if args.output_root else None,
            legacy_evidence=args.legacy_readback, render_excel=args.render_excel, runtime=runtime,
            final_delivery=args.final_delivery,
            submission_authorization=str(args.submission_authorization) if args.submission_authorization else None,
            progress=lambda event: print(json.dumps(event, ensure_ascii=False), file=sys.stderr, flush=True))
    elif args.command == 'batch':
        try:
            from batch_workflow import run_batch
        except ModuleNotFoundError as exc:
            if exc.name != 'batch_workflow':
                raise
            r = {'ok': False, 'state': 'batch_workflow_unavailable', 'error_code': 'batch_workflow_unavailable',
                 'error': '共享批次流程尚未接通；未执行财务动作', 'system_write': False}
        else:
            r = run_batch(AssistantService(material_roots=args.material_root),
                          scenario=args.scenario, batch_index=str(args.batch_index), phase=args.phase,
                          sequences=args.sequences, tab_id=args.tab_id,
                          expected_userid=args.expected_userid, execute=args.execute,
                          allow_incomplete=args.allow_incomplete)
    else:
        request = json.loads(args.input.read_text(encoding='utf-8'))
        r = dispatch_request(AssistantService(material_roots=args.material_root), request)
    if args.command == 'workflow' and r.get('report_path') and not args.full_output:
        # The complete immutable run report is already on disk. Avoid emitting
        # every source and native receipt twice into the caller's context/log.
        keep = {'schema','ok','state','scenario','phase','batch_index','execute','as_of_utc',
                'filled_count','audit_passed_count','material_gap_count','partially_filled_count',
                'held_count','counts_scope','issues','recoveries','report_path','delivery_counts',
                'system_write_requested','approval_completed','payment_completed','pdf_exported','submitted_count','already_submitted_count','applicant_ready_count',
                'elapsed_seconds','run_elapsed_seconds','within_target','performance_verified','timing_complete','performance_target_seconds','reused_completed_run','live_rechecked'}
        compact = {key:value for key,value in r.items() if key in keep}
        if r.get('approval'):compact['approval']={key:value for key,value in r['approval'].items()if key in keep and key!='results'}
        delivery = r.get('delivery', {})
        if delivery:
            compact['delivery'] = {key:delivery[key] for key in
                ['output_directory','progress_path','readable_summary','excel'] if key in delivery}
        r = compact
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r.get('ok', r.get('ready', True)) else 2


if __name__ == '__main__':
    try: raise SystemExit(main())
    except (ValueError, OSError, KeyError, TypeError, RuntimeError, sqlite3.Error) as e:
        fallback = 'ledger_database_error' if isinstance(e, sqlite3.Error) else 'runtime_failure' if isinstance(e, RuntimeError) else 'invalid_request'
        print(json.dumps({'ok': False, 'error_code': getattr(e, 'code', fallback), 'error': str(e)}, ensure_ascii=False))
        raise SystemExit(2)
