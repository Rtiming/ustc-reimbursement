"""面向用户和小上下文agent的结果投影；事实仍来自原job和业务回执。"""
from pathlib import Path
import time

from assistant_api import read_json
from assistant_contract import APIError


def call(tool, arguments, label, *, confirmation=False):
    return {'kind':'call','tool':tool,'arguments':arguments,'label':label,'requires_user_confirmation':confirmation}


def run(service, case_id, job_id, wait_seconds=0):
    if type(wait_seconds) not in (int,float) or not 0 <= wait_seconds <= 10:
        raise APIError('task_wait','等待时间须为0至10秒')
    deadline=time.monotonic()+wait_seconds
    while True:
        raw=service.result(case_id,job_id)
        if raw['status'] not in {'queued','running'} or time.monotonic()>=deadline:break
        time.sleep(min(.35,max(0,deadline-time.monotonic())))
    request=read_json(service.job_folder(case_id,job_id)/'request.json')
    op=request['operation'];params=request['parameters'];status=raw['status']
    result={'schema':'ustc-task-view-v1','ok':raw.get('ok',False),'case_id':case_id,'job_id':job_id,
            'status':status,'operation':op,'system_result_verified':False,
            'source':{'job_id':job_id,'artifacts':[{k:a.get(k)for k in ['artifact_id','name','sha256']}for a in raw.get('artifacts',[])]},
            'rows':[],'next_action':None,'safe_to_retry':False}
    if op=='approver_batch':
        from finance_approver_service import view
        from approver_view import project
        result.update(project(view(service,params['plan_id'])))
        if status in {'queued','running'}:
            result.update(title='原批次正在处理',summary='本项回读后才进入下一项，可随时请求暂停后续项目。')
            result['next_action']=None
        elif result['approval_batch']['state']=='running':
            # Worker退出而批次仍running时，不提供再次派发；先保留原任务。
            result.update(title='原批次任务中断，待核对',summary='请核对原任务和原写入记录，未自动重发。')
            result['next_action']=call('reimbursement_approver_batch',{'action':'recover','plan_id':params['plan_id'],'execute':False},'核对原任务')
        return result
    if status in {'queued','running'}:
        result.update(title='任务正在处理',summary='程序会继续处理，重复查看不会再次发起业务。',retry_after_seconds=1)
        result['next_action']=call('reimbursement_task_view',{'case_id':case_id,'job_id':job_id,'wait_seconds':5},'等待原任务结果')
        return result
    if status != 'completed' or raw.get('exit_code') != 0:
        messages=raw.get('messages',[])
        message=next((m.get('error')for m in reversed(messages)if isinstance(m,dict)and m.get('error')),None)
        if isinstance(message,dict):message=message.get('message') or message.get('code')
        result.update(title='原任务需要处理',summary=raw.get('error')or message or raw.get('next')or'请核查原任务，未重新发起业务。')
        result['next_action']=call('reimbursement_result',{'case_id':case_id,'job_id':job_id},'查看原任务依据')
        return result
    artifacts=raw.get('artifacts',[])
    outputs=[a for a in artifacts if a['name']=='output.json']
    body=read_json(service.artifact(case_id,job_id,outputs[0]['artifact_id'])) if len(outputs)==1 else next((m for m in reversed(raw.get('messages',[]))if isinstance(m,dict)),{})
    native=body.get('result',body) if isinstance(body,dict) else {}
    if not isinstance(native,dict):native={}
    result.update(title='本步程序已返回',summary='本步已结束；保存、本人确认和后续审批仍须按业务回执分别核实。')
    if outputs:
        result['details_call']=call('reimbursement_result',{'case_id':case_id,'job_id':job_id,'artifact_id':outputs[0]['artifact_id']},'查看完整依据')
    if op=='invoice_sources' and params.get('action')=='manifest':
        files=native.get('original_files')
        if native.get('schema')!='ustc-case-invoice-sources-v1' or native.get('case_id')!=case_id or not isinstance(files,list) or native.get('expected_count')!=len(files):
            raise APIError('task_source','票源结果不完整，不能显示核对通过')
        numbers={n for f in files for n in f.get('invoice_number_candidates',[])}
        result.update(title='原件来源已核对',summary=f'已核对{len(files)}份原件、{len(numbers)}个票号。只确认来源和字节完整性，尚未审核票面或选入学校系统。',
            rows=[{'文件':f.get('name')or Path(f['path']).name,'票号':'、'.join(f.get('invoice_number_candidates',[])),
                   '来源类型':{'declared_original_file':'原文件的归档副本','archive_member_working_copy':'压缩包内的已校验副本'}.get(f.get('source_kind'),'来源待核')}for f in files[:20]],
            row_count=len(files),rows_truncated=len(files)>20)
        result['next_action']=call('reimbursement_record_result',{'case_id':case_id,'kind':'register-originals','job_id':job_id,
            'artifact_id':outputs[0]['artifact_id'],'request_id':'register-'+job_id},'登记已核原件')
    elif op=='record_result' and params.get('kind')=='register-originals' and native.get('ok')is True:
        problems=native.get('needs_review',[])
        result.update(title='原件已登记',summary=f"已登记{native.get('count',0)}份原件。"+('仍有原件问题需要核对。'if problems else'下一步填写并核定本次报销信息。'),issues=problems)
        result['next_action']={'kind':'human_review','issues':problems,'label':'先核对原件问题'} if problems else {
            **call('reimbursement_case_details',{'case_id':case_id},'填写报销信息'),'ui_form':'case_details'}
    elif op=='invoice_sources' and params.get('action')=='audit':
        result.update(title='原票购方审核结果',summary='票号、原件SHA和逐页审核结果已保留；此结果不代替税务真实性查验。',
            rows=[{'原文件':Path(r['path']).name,'页数':len(r.get('pages',[])),'票号':'、'.join(sorted({n for page in r.get('pages',[])for n in page.get('invoice_number_candidates',[])}))}for r in native.get('reports',[])])
    elif op=='invoice_selection' and params.get('action')=='preview':
        from invoice_selection import value as native_value
        bindings=native.get('bindings',{})
        result.update(title='本案选票预览',summary='以下是将保存在本案的票据；这里只预览，尚未创建学校报销单。',
            rows=[{'票号':r.get('native_row',{}).get('t.tk_no')or r.get('native_row',{}).get('t.tk_code_abst'),
                   '票面金额':native_value(r.get('native_row',{}),'amount'),'当前认证':r.get('native_row',{}).get('t.auth_rst_desc')}for r in bindings.values()])
    elif op=='business'and params.get('action')=='invoice-inventory':
        from invoice_selection import value as native_value
        grids=native.get('grids',[])
        if (native.get('complete')is not True or len(grids)!=2 or {str(g.get('funcno'))for g in grids}!={'53','113'}
            or native.get('identity',{}).get('userid')!=params.get('expected_userid')
            or any(g.get('complete')is not True or g.get('records')!=len(g.get('rows',[]))for g in grids)):
            raise APIError('task_source','票池或识别清单尚未完整取得')
        pool=next((g['rows']for g in grids if str(g.get('funcno'))=='53'),[])
        result.update(title='本人学校票池已读取',summary='完整票池共'+str(len(pool))+'项。请按本案原票核对认证、占用和识别结果；清单存在不等于已选入本案。',
            rows=[{'票号':r.get('t.tk_no')or r.get('t.tk_code_abst'),'票面金额':native_value(r,'amount'),
                   '认证结果':r.get('t.auth_rst_desc'),'已绑定单号':r.get('t.serial_no')or'尚未绑定'}for r in pool[:100]],row_count=len(pool),rows_truncated=len(pool)>100)
    elif op=='submission' and params.get('action')=='workflow-inspect':
        errors=native.get('native_error_messages',[])
        ready=native.get('complete')is True and native.get('native_error_present')is False and not native.get('required_changes') and native.get('original',{}).get('bill',{}).get('t.status')=='-99'
        result.update(title='学校提交条件已读取',summary='请核对学校原提示及本案提交范围。'+('可继续预览本单准备计划。'if ready else'仍有材料错误、未保存内容或流程状态待处理。'),
            issues=errors,rows=[{'学校提示':str(v)}for v in errors],promise_text=native.get('promise_text'),system_result_verified=False)
        result['next_action']=call('reimbursement_case_finance',{'case_id':case_id,'action':'ready_plan','job_id':job_id},'查看本单提交范围与学校承诺')if ready else call('reimbursement_case_finance',{'case_id':case_id},'返回本案核对资料与原任务')
    elif op=='material_intake':
        if native.get('ok')is not True:
            result.update(title='材料尚未整理完成',summary='请核对原任务问题；程序退出不等于材料完整。')
        elif request['execute']:
            result.update(title='材料已归档',summary=f"已归档{native.get('original_count',0)}份来源，{native.get('ocr_candidates',0)}项需要进一步识别。金额及财务状态尚未核定。")
            result['next_action']=call('reimbursement_intake_review',{'case_id':case_id,'intake_job_id':job_id},'对照原件核对材料')
        else:
            result.update(title='材料检查通过',summary=f"本批{native.get('file_count',0)}份来源文件可以进入整理。检查阶段尚未归档或创建财务单据。")
            result['next_action']=call('reimbursement_material_intake',{'case_id':case_id,'request':params['request'],
                'request_id':'intake-execute-'+job_id,'execute':True},'开始整理本批材料',confirmation=True)
    elif op=='business' and params.get('action')=='bills-list':
        from finance_bill_state import index_bills
        bills=index_bills(native,params['expected_userid'])
        result.update(title='学校单据已读取',summary=f'完整取得本人{len(bills)}张单据，采集时间为{native["observed_at"]}。',
            observed_at=native['observed_at'],row_count=len(bills))
        result['next_action']=call('reimbursement_finance_feedback',{'case_id':case_id,'source_job_id':job_id},'查看挂单和退回理由')
    elif op=='business' and params.get('action')in {'approvals-list','approval-history'}:
        rows=native.get('rows')
        is_history=params['action']=='approval-history'
        if (native.get('ok')is not True or native.get('complete')is not True or native.get('account_scope')!=('current_approver_history'if is_history else'current_approver')
                or native.get('expected_userid')!=params['expected_userid']or not isinstance(rows,list)or native.get('total_records')!=len(rows)):
            raise APIError('task_source','审批清单尚未完整核实，不能显示读取完成')
        result.update(title='本人审批处理记录已读取'if is_history else'本人待审批清单已读取',summary=f'完整取得{len(rows)}项学校审批记录。'+('历史记录不代表当前待办。'if is_history else'按原生角色和节点逐项核对后才能决定办理。'),
            row_count=len(rows),rows=[{'单号':r['t.serial_no'],'申请人':r.get('t.req_name'),'摘要':r.get('t.remark'),
                '金额':r.get('t.amount'),'当前角色':r.get('t.node_name1'),'状态':r.get('t.status_name')}for r in rows[:100]],rows_truncated=len(rows)>100,
            observed_at=native.get('observed_at'),system_result_verified=False)
        if not is_history:
            from finance_connection import run as connection_status
            matches=[r for r in connection_status(service,'status')['connections']if r['case_id']==case_id]
            if len(matches)==1:
                result['next_action']={**call('reimbursement_approver_batch',{'action':'inspect','connection_id':matches[0]['connection_id'],'source_job_id':job_id},'选择要办理的审批项'),'ui_form':'approver_batch'}
    elif op=='business' and params.get('action')=='approval-log':
        if native.get('complete')is not True or native.get('target',{}).get('serial')!=params['reference_serial']:
            raise APIError('task_source','原单审批日志不完整或单号不同')
        result.update(title='原单审批日志已读取',summary='按当前账号的原审批来源读取；历史日志与本次操作结果分别核对。',
            rows=[{'节点':r.get('t.node_name'),'处理人':r.get('t.operator'),'意见':r.get('t.op_comment'),'处理时间':r.get('t.op_date')}for r in native.get('history',[])],
            row_count=len(native.get('history',[])),system_result_verified=False)
    elif op=='access' and params.get('action')=='check-backend':
        ready=native.get('state')=='native_backend_ready'and native.get('identity_verified')is True
        result.update(title='本人账号已核验'if ready else'账号连接待核对',summary='学校后端和当前学工号已只读核实。'if ready else'请在本机Edge核对本人登录，再接续原任务。')
        if ready and case_id.startswith('account_'):
            result['next_action']=call('reimbursement_finance_connection',{'action':'accept','expected_userid':params['expected_userid'],'source_job_id':job_id},'登记已核连接')
    elif op=='record_result' and params.get('kind')=='ack-write' and native.get('ok')is True:
        result.update(title='本步核验结果已登记',summary='已按原业务回执登记本步结果；不表示整单审批或付款完成。',system_result_verified=True)
        result['next_action']=call('reimbursement_overview',{'case_id':case_id},'查看本案下一步')
    if result.get('next_action')is None:
        from case_finance import location
        related=any(read_json(p).get('job_id')==job_id for p in (location(service,case_id)/'jobs').glob('*.json'))
        if related:result['next_action']=call('reimbursement_case_finance',{'case_id':case_id},'继续本活动办理步骤')
    return result
