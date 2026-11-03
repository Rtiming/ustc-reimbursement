"""本机账号连接与收件箱；固定原生桥和既有只读任务，不接收密码或验证码。"""
from pathlib import Path
import hashlib
import re

from assistant_contract import APIError

ACTIONS=('status','install_bridge','discover','verify','accept','bills','approvals','history')


def account_case(who):
    if not isinstance(who,str)or not re.fullmatch('[A-Za-z0-9]{2,40}',who):
        raise APIError('connection_identity','请填写本人学校登录学工号，不填写密码')
    return 'account_'+hashlib.sha256(who.encode()).hexdigest()[:20]


def context(snapshot,tab,who):
    return {'REIMBURSEMENT_EDGE_BACKEND':'edge_native_messaging',
            'REIMBURSEMENT_EDGE_TAB_NAMESPACE':snapshot['tab_id_namespace'],
            'REIMBURSEMENT_EDGE_WINDOW_ID':tab['window_id'],'REIMBURSEMENT_EXPECTED_USERID':who}


def bound(service, connection_id):
    from assistant_api import read_json
    from material_intake import reject_links
    from registered_bridge import inventory
    if not isinstance(connection_id,str)or not re.fullmatch('[a-f0-9]{32}',connection_id):
        raise APIError('connection_missing','请先核对本人学校账号连接')
    path=service.root/'.local/finance-connections'/(connection_id+'.json');reject_links(path)
    value=read_json(path)
    if value.get('connection_id')!=connection_id or account_case(value['expected_userid'])!=value['case_id']:
        raise APIError('connection_binding','账号连接记录不完整，请重新核验')
    source=service.artifact(value['case_id'],value['source_job_id'],value['source_artifact_id'])
    if hashlib.sha256(source.read_bytes()).hexdigest()!=value['source_sha256']:
        raise APIError('connection_source','原账号核验回执已改变')
    snapshot=inventory();matches=[t for t in snapshot['tabs']if t['tab_id']==value['tab_id']and t['window_id']==value['window_id']and t['path']=='https://cwzh.ustc.edu.cn/YBX/main.jsp']
    if len(matches)!=1 or snapshot['tab_id_namespace']!=value['namespace']:
        raise APIError('connection_changed','财务窗口或连接已改变。重新核对本人连接后继续，未知任务仍保留原回执。')
    return value,context(snapshot,matches[0],value['expected_userid'])


def run(service, action='status', expected_userid=None, tab_id=None, connection_id=None, source_job_id=None,
        request_id=None, execute=False):
    from assistant_api import AssistantService,read_json,atomic_json
    from windows_bridge_setup import capabilities
    from registered_bridge import inventory
    if action not in ACTIONS or type(execute)is not bool:raise APIError('connection_action','请选择固定账号连接操作')
    if action in {'verify','bills','approvals','history'} and (not isinstance(request_id,str)or not re.fullmatch('[A-Za-z0-9_-]{1,80}',request_id)):
        raise APIError('connection_request','请提供本次固定request_id，断线后继续查询原任务，不换号重发')
    if action=='status':
        rows=[]
        for path in (service.root/'.local/finance-connections').glob('*.json'):
            row=read_json(path)
            rows.append({k:row[k]for k in ['connection_id','expected_userid','case_id','verified_at']})
        return {'ok':True,'schema':'ustc-finance-connection-v1','platform':capabilities(),'connections':rows,
                'login_url':'https://cwzh.ustc.edu.cn/WFManager/home2.jsp','system_write':False,
                'note':'在本机Edge完成正常登录，再返回核对连接。密码和验证码只在学校页面输入。'}
    if action=='install_bridge':
        if not execute:return {'ok':True,'preview':True,'changes':['安装本项目连接组件并登记到当前Windows用户'],'financial_write':False}
        from windows_bridge_setup import install
        return {**install(),'financial_write':False,'next':'在Edge扩展页加载返回的extension_dir，再回到本页检查连接。'}
    if action=='discover':
        if execute:raise APIError('connection_action','发现连接不接受execute')
        snapshot=inventory()
        return {'ok':True,'schema':'ustc-finance-connection-candidates-v1','namespace':snapshot['tab_id_namespace'],
                'candidates':[{'tab_id':t['tab_id'],'window_id':t['window_id'],'label':'已打开的学校财务页面'}for t in snapshot['tabs']if t['path']=='https://cwzh.ustc.edu.cn/YBX/main.jsp'],
                'system_write':False,'note':'选择当前登录的财务页面，再只读核对本人学工号。'}
    if action in {'verify','accept'}:
        case_id=account_case(expected_userid)
        if execute:raise APIError('connection_action','账号核验不接受财务执行')
        if action=='verify':
            snapshot=inventory();matches=[t for t in snapshot['tabs']if t['tab_id']==tab_id and t['path']=='https://cwzh.ustc.edu.cn/YBX/main.jsp']
            if len(matches)!=1:raise APIError('connection_tab','请重新查找并选择实际财务页面')
            try:service.resolve(case_id)
            except APIError as error:
                if error.code!='case_not_found':raise
                service.new_case(case_id,title='我的财务收件箱（'+expected_userid+'）')
                service.update_case(case_id,'case',{'material_context':{'account_inbox':True,'expected_userid':expected_userid}},service.revision(case_id))
            saved=read_json(service.resolve(case_id)[1]['case']).get('material_context',{})
            if saved.get('account_inbox')is not True or saved.get('expected_userid')!=expected_userid:
                raise APIError('connection_scope','账号收件箱与既有案件冲突，不覆盖')
            scoped=AssistantService(service.root,service.material_roots,service.python,context(snapshot,matches[0],expected_userid))
            result=scoped.enqueue(case_id,'access',{'action':'check-backend','tab_id':tab_id,'expected_userid':expected_userid},request_id,False)
            return {**result,'case_id':case_id,'next_action':{'tool':'reimbursement_finance_connection','arguments':{
                'action':'accept','expected_userid':expected_userid,'source_job_id':result['job_id']},'label':'查看账号核验结果'}}
        result=service.result(case_id,source_job_id)
        if result['status']in {'queued','running'}:
            return {'ok':True,'pending':True,'job_id':source_job_id,'case_id':case_id,'retry_after_seconds':1,'system_write':False}
        outputs=[a for a in result.get('artifacts',[])if a['name']=='output.json']
        if result['status']!='completed'or result.get('exit_code')!=0 or len(outputs)!=1:
            raise APIError('connection_check_failed','账号核验尚未成功，请查看原任务或重新登录，不办理财务')
        path,body,request=service._typed_case_artifact(case_id,source_job_id,outputs[0]['artifact_id'],'access',['check-backend'])
        native=body.get('result',body)
        if (native.get('schema')!='ustc-finance-backend-health-v1'or native.get('state')!='native_backend_ready'
                or native.get('identity_verified')is not True or native.get('system_write')is not False
                or request.get('execute')is not False or request['parameters']['expected_userid']!=expected_userid):
            raise APIError('connection_identity_unverified','学校当前登录人与填写账号不一致或后端未就绪，请在原页面核对登录')
        frozen=request.get('runtime_context',{});snapshot=inventory()
        matches=[t for t in snapshot['tabs']if t['tab_id']==request['parameters']['tab_id']and t['window_id']==frozen.get('REIMBURSEMENT_EDGE_WINDOW_ID')and t['path']=='https://cwzh.ustc.edu.cn/YBX/main.jsp']
        if len(matches)!=1 or frozen.get('REIMBURSEMENT_EDGE_TAB_NAMESPACE')!=snapshot['tab_id_namespace']:
            raise APIError('connection_changed','核验期间连接发生变化，请重新核对')
        identity=hashlib.sha256((expected_userid+'|'+snapshot['tab_id_namespace']+'|'+matches[0]['tab_id']).encode()).hexdigest()[:32]
        value={'schema':'ustc-finance-connection-v1','connection_id':identity,'case_id':case_id,'expected_userid':expected_userid,
               'tab_id':matches[0]['tab_id'],'window_id':matches[0]['window_id'],'namespace':snapshot['tab_id_namespace'],
               'source_job_id':source_job_id,'source_artifact_id':outputs[0]['artifact_id'],
               'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'verified_at':native['observed_at_utc']}
        atomic_json(service.root/'.local/finance-connections'/(identity+'.json'),value)
        return {'ok':True,'connected':True,**value,'system_write':False,'note':'当前账号已核实；每次业务仍独立核对身份。'}
    if execute:raise APIError('connection_action','清单读取不接受execute')
    value,runtime=bound(service,connection_id)
    scoped=AssistantService(service.root,service.material_roots,service.python,runtime)
    result=scoped.business(value['case_id'],{'bills':'bills-list','approvals':'approvals-list','history':'approval-history'}[action],value['tab_id'],value['expected_userid'],request_id)
    return {**result,'case_id':value['case_id'],'system_write':False,'connection_id':connection_id}
