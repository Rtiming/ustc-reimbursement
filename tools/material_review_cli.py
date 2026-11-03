"""Unified local review command; implementation shared by assistant.py."""
from material_review_model import load, validate, asset_path


def configure(parser):
    parser.add_argument('action',choices=['build','serve','verify','export','import-answers','start','open','status','stop','doctor','import-workflow','list','register','summary'])
    parser.add_argument('--workspace')
    parser.add_argument('--material-root',action='append')
    parser.add_argument('--config')
    parser.add_argument('--port',type=int,default=8766)
    parser.add_argument('--item',action='append')
    parser.add_argument('--issue',action='append',help='只导出勾选的问题，可重复；省略时只含尚未答复的待处理问题')
    parser.add_argument('--mode',choices=['pdf','feedback','materials','handoff'],default='feedback')
    parser.add_argument('--workbook')
    parser.add_argument('--actor')
    parser.add_argument('--runtime-info',help='本机启动器运行记录，不能写入案件同步目录')


def run(args):
    from material_review_model import require
    if args.action in ('list','register','summary') or (args.action=='open' and not args.workspace):
        from assistant_api import AssistantService
        return AssistantService(material_roots=args.material_root).material_review(args.action,args.workspace,args.port)
    require(args.workspace,'此动作需要--workspace')
    if args.action=='import-workflow':
        from material_review_workflow import import_status
        require(args.config,'导入办理状态需要--config')
        return import_status(args.workspace,args.config)
    if args.action=='build':
        from material_review_build import build
        require(args.config,'build需要--config')
        return build(args.config,args.workspace)
    if args.action in ('start','open'):
        from material_review_launch import launch
        return launch(args.workspace,args.port,args.action=='open')
    if args.action in ('status','stop','doctor'):
        from material_review_launch import status, stop, doctor
        return {'status':status,'stop':stop,'doctor':doctor}[args.action](args.workspace)
    if args.action=='serve':
        from material_review_server import serve
        return serve(args.workspace,args.port,args.runtime_info)
    if args.action=='import-answers':
        from material_review_store import import_answers
        require(args.workbook and args.actor,'导入需要--workbook和--actor')
        return import_answers(args.workspace,args.workbook,args.actor)
    if args.action=='export':
        if args.mode=='pdf':
            from material_review_pdf import export_pdf
            path,manifest=export_pdf(args.workspace,args.item,args.issue)
        elif args.mode=='feedback':
            from material_review_export import export_package
            path,manifest=export_package(args.workspace,args.item,args.issue)
        else:
            from material_review_package import export_bundle
            path,manifest=export_bundle(args.workspace,args.item,args.mode,args.issue)
        return {'ok':True,'path':str(path),'manifest':manifest}
    from material_review_store import events,state
    data,version=load(args.workspace);validate(data)
    for asset in data['assets'].values():asset_path(data,asset,args.workspace)
    current=state(args.workspace,data)
    for asset in current['attachments'].values():asset_path(data,asset,args.workspace)
    return {'ok':True,'version':version,'counts':data['counts'],'events':len(events(args.workspace)),
            'stale_events':sum(len(x['stale_events']) for x in current['issues'].values()),'system_write':False}
