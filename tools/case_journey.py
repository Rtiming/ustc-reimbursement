"""从既有案件证据计算下一步；不另存进度、不把缺少单号当作建单前阻碍。"""
from pathlib import Path
import hashlib

from assistant_contract import APIError


def call(tool, case_id, label, **arguments):
    return {'tool':'reimbursement_'+tool, 'arguments':{'case_id':case_id, **arguments}, 'label':label}


def invoice(service, case_id, index):
    from assistant_api import read_json
    _, paths=service.resolve(case_id)
    rows=read_json(paths['invoices']).get('original_files',[])
    if type(index)is not int or not 0 <= index < len(rows):
        raise APIError('invoice_index','请从本案待核原件选择一项')
    row=rows[index];path=service.material_path(row['path'])
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:
        raise APIError('original_changed','原件已经改变，请先核对来源；未确认票面')
    import fitz
    if path.suffix.lower()=='.pdf':
        with fitz.open(path)as pdf:pages=len(pdf)
    else:pages=1
    return {'ok':True,'schema':'ustc-invoice-face-view-v1','case_id':case_id,'index':index,
            'name':path.name,'sha256':row['sha256'],'page_count':pages,
            'invoice_numbers':row.get('invoice_number_candidates',[]),'visual_check':row.get('visual_check'),
            'note':row.get('visual_check_note',''),'revision':service.revision(case_id),'system_write':False}


def preview(service, case_id, index, page):
    from assistant_api import read_json
    from material_review_server import preview as render
    bound=invoice(service,case_id,index)
    _,paths=service.resolve(case_id);row=read_json(paths['invoices'])['original_files'][index]
    path=service.material_path(row['path'])
    asset={'path':str(path),'sha256':row['sha256'],'suffix':path.suffix.lower(),'pages':bound['page_count']}
    return render({'roots':[str(root) for root in service.material_roots]},asset,service.root,page)


def run(service, case_id, action='inspect', index=None):
    from assistant_api import read_json
    if action=='invoice':return invoice(service,case_id,index)
    if action!='inspect' or index is not None:raise APIError('journey_action','请选择本案进度或具体原件核对')
    _,paths=service.resolve(case_id)
    case=read_json(paths['case']);form=read_json(paths['form']);manifest=read_json(paths['invoices'])
    overview=service.overview(case_id);steps=[]
    if overview.get('role')=='registered_batch':
        return {'ok':True,'schema':'ustc-case-journey-v1','case_id':case_id,'title':case.get('description',case_id),
                'steps':[], 'next_action':overview['next_action'],'note':'这是已登记批次，请按批次逐单核对。','system_write':False}
    if overview.get('active_jobs'):
        first=overview['active_jobs'][0]
        return {'ok':True,'schema':'ustc-case-journey-v1','case_id':case_id,'steps':[],
                'summary':'本案正在处理，先查看原任务。','next_action':call('task_view',case_id,'查看原任务',job_id=first['job_id']),'system_write':False}
    if overview.get('unverified_write'):
        return {'ok':True,'schema':'ustc-case-journey-v1','case_id':case_id,'steps':[],
                'summary':'原操作结果尚待核实，暂停新建和保存。','next_action':call('overview',case_id,'查看原操作及恢复依据'),'system_write':False}
    originals=manifest.get('original_files',[])
    batches=service.intake_review(case_id,action='batches')['batches']
    intake_next=batches[0]['next_action']if batches else {'ui_group':'materials','label':'选择并导入材料',
        'tool':'reimbursement_assist','arguments':{'action':'schema','operation':'receive_materials','case_id':case_id},
        'needed_inputs':['待导入的原文件或文件夹路径']}
    steps.append({'title':'原件归档与来源登记','state':'complete'if originals else'needs_input',
                  'detail':f'已登记{len(originals)}份原件，已有{len(batches)}个收件批次。','action':intake_next})
    checks=[]
    for i,row in enumerate(originals):
        try:
            path=service.material_path(row['path'])
            source_ok=hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256']
        except (ValueError,OSError):source_ok=False
        checks.append({'index':i,'name':Path(row['path']).name,'source_ok':source_ok,'reviewed':row.get('visual_check')=='verified',
                       'action':call('case_journey',case_id,'对照原件审核票面',action='invoice',index=i)})
    unreviewed=[r for r in checks if not r['reviewed'] or not r['source_ok']]
    steps.append({'title':'逐票核对','state':'complete'if checks and not unreviewed else'needs_input',
                  'detail':f'票面已核对{len(checks)-len(unreviewed)}/{len(checks)}份。核对票号、抬头税号、金额、日期及实际费用；这一步不代表官方认证。',
                  'action':unreviewed[0]['action']if unreviewed else intake_next})
    details=service.case_details(case_id)
    ready=not details['needed_inputs']and details['input_validation']['ok']
    steps.append({'title':'金额、经费与实际收款','state':'complete'if ready else'needs_input',
                  'detail':'本地填写检查通过；学校实际经费和收款人仍需联网核对。'if ready else '、'.join(r['label']for r in details['needed_inputs'])or details['input_validation'].get('error','请核对填写'),
                  'action':call('case_details',case_id,'继续填写报销信息')})
    # 材料规则仍由原check/audit产生；不复制或覆盖专项例外。
    material=service.material_requirements(case_id)
    steps.append({'title':'材料要求与付款凭证','state':'reviewed_with_gaps'if material['reviewed_current']else'needs_review',
                  'detail':('已查看当前材料差项；未解决事项仍保留，可继续其他准备。'if material['reviewed_current']else'逐项查看材料、付款凭证和待核定的适用条件。纸质原件只登记，电子附件按页整理。'),
                  'action':call('material_requirements',case_id,'查看具体材料差项')})
    serial=form.get('serial')
    steps.append({'title':'学校连接与票池核验','state':'needs_review',
                  'detail':'连接本人账号，核对官方认证、票据占用、实际经费及收款身份。',
                  'action':{'ui_group':'settings','label':'连接学校财务系统','tool':'reimbursement_finance_connection','arguments':{'action':'status'}}})
    steps.append({'title':'建单、填写与本人确认','state':'created'if serial else'not_started',
                  'detail':f'已绑定学校单号{serial}，后续状态以学校回读为准。'if serial else'尚未建单；完成前面的准备后，由学校返回单号，不需要手填一个新单号。',
                  'action':call('case_finance',case_id,'继续本案财务办理')})
    primary=next((row['action']for row in steps if row['state']not in {'complete','reviewed_with_gaps'}),None)
    return {'ok':True,'schema':'ustc-case-journey-v1','case_id':case_id,'title':case.get('description',case_id),
            'revision':service.revision(case_id),'steps':steps,'originals':checks,'next_action':primary,
            'summary':'按以下步骤继续；已保存的资料会保留，后续结果按实际回执更新。','system_write':False}
