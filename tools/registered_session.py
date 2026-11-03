"""登记工作页、原任务轮询与窗口生命周期；不决定报销阶段。"""

from __future__ import annotations

import os
import time
from pathlib import Path

from assistant_api import APIError, atomic_json, read_json
from registered_page_owner import RegisteredPageOwner


class RegisteredSession(RegisteredPageOwner):
    def __init__(self, service, case_id, cfg, index, auth, progress=None):
        self.service, self.case, self.cfg, self.index, self.auth, self.progress = (
            service,
            case_id,
            cfg,
            index,
            auth,
            progress,
        )
        self.folder = service.root / '.local/registered_workflows' / case_id
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_path = self.folder / 'session.json'
        self.ids = [r['case_id'] for r in read_json(index)['documents']]

    def open_window(self, case, bridge):
        """窗口创建也是有回执的原请求，未知结果不再新开一份工作页。"""
        from native_finance_bridge import new_finance_window
        path = self.folder / ('window-request-' + case + '.json')
        if path.exists():
            intent = read_json(path)
            if intent.get('namespace') != bridge['tab_id_namespace']:
                raise APIError('registered_original_namespace', '原开窗连接已改变；先恢复原请求，不另开工作页')
        else:
            intent = {'case_id': case, 'namespace': bridge['tab_id_namespace'],
                      'request_id': uuid.uuid4().hex, 'state': 'requested'}
            atomic_json(path, intent)
        opened = new_finance_window(request_id=intent['request_id'])
        atomic_json(path, {**intent, 'state': 'completed', 'result': opened})
        # 回执保留为独立历史；只有取得明确结果才移出pending入口。
        receipt = path.with_name('window-receipt-' + intent['request_id'] + '.json')
        atomic_json(receipt, {**intent, 'state': 'completed', 'result': opened})
        return {**opened, 'window_request_id': intent['request_id']}

    def finish_window_request(self, case, opened):
        path = self.folder / ('window-request-' + case + '.json')
        if not path.exists():
            return
        saved = read_json(self.state_path).get('creation_receipt', {})
        if read_json(path)['request_id'] != opened.get('window_request_id') or saved != opened:
            raise APIError('registered_window_receipt', '开窗回执尚未绑定到持久工作页，保留原请求')
        path.unlink()

    def access_parameters(self, tab):
        parameters = {'action': 'ensure', 'tab_id': tab}
        if self.cfg.get('login_mode', 'session_only') == 'session_only':
            parameters['session_only'] = True
        return parameters

    def job(
        self, case, operation, parameters, label, execute=False, request_id=None, on_enqueued=None
    ):
        if operation != 'record_result':
            from registered_inputs import assert_definition_current
            assert_definition_current(self.service, self.cfg)
        request_id = request_id or 'registered-' + label + '-' + str(time.time_ns())
        started = time.monotonic()
        metric = {
            'case_id': case,
            'operation': operation,
            'action': parameters.get('action') or parameters.get('mode'),
            'request_id': request_id,
            'label': label,
            'status': 'running',
            'started_at_ns': time.time_ns(),
        }
        metric_path = self.folder / 'job-metrics' / (str(metric['started_at_ns']) + '.json')
        atomic_json(metric_path, metric)
        try:
            bound_tab = getattr(self, 'state', {}).get('tab_id')
            if operation != 'record_result' and bound_tab and parameters.get('tab_id') == bound_tab:
                self.refresh_owned_state()
                self.configure_backend()
                os.environ['REIMBURSEMENT_EDGE_WINDOW_ID'] = self.state['window_id']
                os.environ['REIMBURSEMENT_REGISTERED_SESSION'] = str(self.state_path)
                self.recover_frozen(allow_access=operation == 'access')
            metric['preflight_seconds'] = round(time.monotonic() - started, 6)

            def enqueued(job):
                metric['job_id'] = job['job_id']
                metric['dispatch_seconds'] = round(
                    time.monotonic() - started - metric['preflight_seconds'], 6
                )
                # 先落业务恢复指针，再落诊断记录，避免计时写盘延迟原任务登记。
                if on_enqueued:
                    on_enqueued(job)
                atomic_json(metric_path, metric)

            result = self._job_result(case, operation, parameters, request_id, execute, enqueued)
            self.refresh_owned_state()
            metric['status'] = 'completed'
            return result
        except Exception as error:
            # 只记错误类型，不将表单、验证码、凭据或原生整页塞入性能记录。
            metric.update(status='failed', error_code=getattr(error, 'code', type(error).__name__))
            raise
        finally:
            metric['elapsed_seconds'] = round(time.monotonic() - started, 6)
            atomic_json(metric_path, metric)

    def _job_result(self, case, operation, parameters, request_id, execute, on_enqueued):
        """只轮询原任务；请求超时不会生成新的业务意图。"""
        a = self.service.enqueue(case, operation, parameters, request_id, execute)
        on_enqueued(a)
        deadline = time.monotonic() + 300
        while True:
            result = self.service.result(case, a['job_id'])
            if result.get('status') not in ('queued', 'running'):
                break
            if time.monotonic() > deadline:
                raise APIError(
                    'registered_original_job', '原后台任务仍在执行，只查原job，不新发业务'
                )
            time.sleep(0.3)
        if result.get('status') != 'completed' or result.get('exit_code') != 0:
            raise APIError(
                'registered_job', str(result.get('messages') or result.get('error') or result)
            )
        rows = [a for a in result.get('artifacts', []) if a.get('name') == 'output.json']
        body = (
            read_json(self.service.artifact(case, result['job_id'], rows[0]['artifact_id']))
            if len(rows) == 1
            else result['messages'][0]
            if len(result.get('messages',[]))==1 and isinstance(result['messages'][0],dict)
            else {}
        )
        while isinstance(body.get('result'), dict):
            body = body['result']
        return result, body

    def recover_frozen(self, allow_access=False):
        """迁移原文档保持原任务；不刷新页面，不重放未确认的金融动作。"""
        from native_finance_bridge import inventory, relocate_finance_tab
        from registered_inputs import assert_definition_current
        assert_definition_current(self.service, self.cfg)

        state = self.state
        if state.get('created_by_registered_workflow') is not True:
            return
        observed = inventory()
        if state.get('namespace') != observed.get('tab_id_namespace'):
            raise APIError('registered_original_namespace', '工作页连接身份已改变；不能迁移或唤醒旧数字标签')
        hits = [
            t
            for t in observed['tabs']
            if t['tab_id'] == state['tab_id'] and t['window_id'] == state['window_id']
        ]
        if len(hits) != 1:
            raise APIError('registered_tab_changed', '登记工作页或窗口已变化；先恢复原现场绑定')
        if hits[0]['path'] != 'https://cwzh.ustc.edu.cn/YBX/main.jsp':
            if allow_access and hits[0]['path'] in (
                'https://id.ustc.edu.cn/cas/login',
                'https://cwzh.ustc.edu.cn/WFManager/home2.jsp',
                'https://cwzh.ustc.edu.cn/YBX/login.html',
            ):
                return
            raise APIError('registered_tab_changed', '当前页不是登记财务主页面')
        if hits[0].get('frozen') is not True:
            return
        pending = self.folder / 'page-relocation-pending.json'
        if pending.exists():
            raise APIError(
                'registered_relocation_unknown',
                '原迁移尚未取得回执；只按原tab盘点，不重复迁移：' + str(pending),
            )
        intent = {
            'tab_id': state['tab_id'],
            'old_window_id': state['window_id'],
            'namespace': observed['tab_id_namespace'],
            'requested_at_ns': time.time_ns(),
        }
        atomic_json(pending, intent)
        result = relocate_finance_tab(
            state['tab_id'],
            tab_id_namespace=observed['tab_id_namespace'],
            expected_window_id=state['window_id'],
        )
        receipt = self.folder / ('page-relocation-' + str(intent['requested_at_ns']) + '.json')
        atomic_json(receipt, {**intent, 'result': result})
        state.update(
            window_id=result['window_id'],
            namespace=result['tab_id_namespace'],
            last_relocation_receipt=str(receipt),
        )
        atomic_json(self.state_path, state)
        pending.unlink()
        os.environ['REIMBURSEMENT_EDGE_WINDOW_ID'] = state['window_id']
        os.environ['REIMBURSEMENT_EDGE_TAB_NAMESPACE'] = state['namespace']
        if result.get('woken') is not True:
            raise APIError('registered_still_frozen', '原页已迁移但仍冻结；新窗口及原任务已保留')
        if self.progress:
            self.progress(
                {
                    'event': 'finance_page_recovered',
                    'case_id': state.get('last_case_id'),
                    'tab_id': state['tab_id'],
                    'window_id': state['window_id'],
                }
            )


    def ensure(self, *, preserve_original=False, _allow_reentry=True, _access_request_id=None):
        import sys

        if sys.platform not in {'darwin', 'win32'}:
            raise APIError('registered_platform', '登记流水线须在已接入原生桥的Windows或Mac执行')
        from registered_bridge import inventory, new_finance_window, wake_finance_tab

        bridge = self.configure_backend()
        state = read_json(self.state_path) if self.state_path.exists() else {}
        # 旧版本在登录完成前尚未保存namespace；只能从同一次原生创建回执补齐。
        if (not state.get('namespace') and state.get('creation_receipt',{}).get('tab_id_namespace') == bridge['tab_id_namespace']):
            state['namespace']=bridge['tab_id_namespace'];atomic_json(self.state_path,state)
        if preserve_original and state.get('namespace') != bridge['tab_id_namespace']:
            raise APIError('registered_original_namespace', '原工作页连接身份变化，保留原任务核对')
        snapshot = inventory()
        existing = [
            t
            for t in snapshot['tabs']
            if t.get('tab_id') == state.get('tab_id')
            and t.get('window_id') == state.get('window_id')
            and t.get('path') in {'https://cwzh.ustc.edu.cn/YBX/main.jsp',
                'https://cwzh.ustc.edu.cn/YBX/login.html','https://cwzh.ustc.edu.cn/WFManager/home2.jsp',
                'https://id.ustc.edu.cn/cas/login'}
        ]
        if (
            len(existing) == 1
            and state.get('case_id') == self.case
            and state.get('created_by_registered_workflow') is True
            and state.get('namespace') == bridge['tab_id_namespace']
        ):
            tab, window = state['tab_id'], state['window_id']
        else:
            if preserve_original or any(
                self.service.overview(case).get('unverified_write') for case in self.ids
            ):
                raise APIError('registered_original_page', '原工作页不在，不能用新页替代未结束现场')
            opened = self.open_window(self.ids[0], bridge)
            tab, window = str(opened['tab_id']), str(opened['window_id'])
            state = {
                'case_id': self.case,
                'tab_id': tab,
                'window_id': window,
                'creation_receipt': opened,
                'created_by_registered_workflow': True,
                'namespace':bridge['tab_id_namespace'],
                'owned_tab_ids':[str(tab)],
                'phase':'opening',
            }
            atomic_json(self.state_path, state)
            self.finish_window_request(self.ids[0], opened)
        os.environ['REIMBURSEMENT_EDGE_WINDOW_ID'] = window
        # 冻结页先恢复原文档，避免把渲染器暂停误判成登录失效。
        self.state = state
        if state.get('creation_receipt'):
            self.finish_window_request(state.get('last_case_id') or self.ids[0], state['creation_receipt'])
        if len(existing) == 1 and existing[0].get('frozen') is True:
            self.recover_frozen(allow_access=True)
            state=self.state
            window = state['window_id']
        case = state.get('last_case_id') if state.get('last_case_id') in self.ids else self.ids[0]
        if _access_request_id is None and state.get('backend_reentry_receipt'):
            proof=read_json(Path(state['backend_reentry_receipt']))
            if proof.get('phase')=='reentry_incomplete':
                return self.resume_backend_login()
            if proof.get('phase')!='verified':
                _access_request_id=proof.get('login_request_id')
        result, body = self.job(case, 'access', {'action': 'ensure', 'tab_id': tab}, 'ensure', True,
                               request_id=_access_request_id)
        window = self.state['window_id']
        if body.get('state') != 'ready':
            raise APIError('registered_login', '原生登录尚未就绪，未开始财务动作')
        # The school portal can create a different actual finance tab.
        tab = next(
            (
                str(m['tab_id'])
                for m in result.get('messages', [])
                if m.get('state') == 'ready' and m.get('tab_id')
            ),
            tab,
        )
        targets = [
            t
            for t in inventory()['tabs']
            if t['tab_id'] == tab
            and t['window_id'] == window
            and t['path'] == 'https://cwzh.ustc.edu.cn/YBX/main.jsp'
        ]
        if len(targets) != 1:
            raise APIError('registered_finance_tab', '未唯一取得实际财务主标签')
        if targets[0].get('frozen') is True:
            wake_finance_tab(
                tab, tab_id_namespace=bridge['tab_id_namespace'], expected_window_id=window
            )
        _, native = self.job(case, 'access', {'action':'check-backend','tab_id':tab,
            'expected_userid':self.cfg['expected_userid']}, 'backend-identity')
        if native.get('state') != 'native_backend_ready' or native.get('identity_verified') is not True:
            if (_allow_reentry and not preserve_original and
                    (native.get('http_status') in (401,403,404) or native.get('state') == 'backend_unconfirmed')):
                return self.recover_backend_session(case,native)
            raise APIError(
                'registered_backend', '原生YBX后端或账号未核实：'+str(native.get('state'))+'；不填单、不换接口或重放业务'
            )
        serial = native.get('current_serial_hint')
        by_serial = {
            read_json(self.service.resolve(c)[1]['form']).get('serial'): c for c in self.ids
        }
        current = by_serial.get(serial) or case
        # A saved applicant page is left intact. The batch uses a clean,
        # separately owned page rather than forcing that page through its
        # history-dependent Return/header routes.
        state=dict(self.state)
        state.update(
            tab_id=tab, window_id=window, namespace=bridge['tab_id_namespace'], last_case_id=current,
            phase='ready'
        )
        atomic_json(self.state_path, state)
        self.state = state
        self.initial_states = {}
        return state

    def resume_backend_login(self, original_job_id=None):
        """只恢复凭据消费前的明确失败；仍用原新界面，最多一个新认证意图。"""
        import hashlib
        from edge_runtime import browser_lock
        from finance_access import snapshot
        from finance_backend import assert_original_transport
        from windows_finance_auth import configuration
        proof_path=Path(self.state.get('backend_reentry_receipt',''))
        if not proof_path.is_file():raise APIError('registered_login_source','没有原新界面恢复回执')
        proof=read_json(proof_path);case=self.state.get('last_case_id')
        if(proof.get('phase')!='reentry_incomplete' or case not in self.ids or
                proof.get('new_tab_id')!=self.state.get('tab_id')or
                proof.get('namespace')!=self.state.get('namespace')):raise APIError('registered_login_source','不是原同界面凭据失败恢复')
        relocation=None
        if proof.get('new_window_id')!=self.state.get('window_id'):
            from material_intake import reject_links
            # 凭据尚未消费的原登录页可能跨多次冻结迁移；逐跳核对，不放宽财务marker规则。
            window=self.state['window_id'];chain=[];seen={window}
            while window!=proof.get('new_window_id'):
                if len(chain)>=8:raise APIError('registered_login_source','原认证页迁移链超过恢复预算')
                matches=[]
                for moved in self.folder.glob('page-relocation-*.json'):
                    reject_links(moved)
                    observed_move=read_json(moved)
                    if(observed_move.get('tab_id')==self.state['tab_id']and observed_move.get('namespace')==self.state['namespace']and
                            observed_move.get('result',{}).get('window_id')==window and observed_move.get('result',{}).get('woken')is True):
                        matches.append((moved,observed_move))
                if len(matches)!=1:raise APIError('registered_login_source','原认证页迁移身份不完整或不唯一')
                moved,observed_move=matches[0]
                if not chain and str(moved)!=self.state.get('last_relocation_receipt'):raise APIError('registered_login_source','不是最后登记的原认证页迁移')
                window=observed_move.get('old_window_id')
                if not isinstance(window,str)or window in seen:raise APIError('registered_login_source','原认证页迁移链断裂或循环')
                seen.add(window);chain.append({'path':str(moved),'sha256':hashlib.sha256(moved.read_bytes()).hexdigest()})
            relocation={'chain':list(reversed(chain))}
        if any(self.service.overview(c).get('unverified_write')for c in self.ids):
            raise APIError('registered_login_pending','有未知财务写入，只恢复原业务，不重新认证')
        jobs=self.service.root/'.local/assistant_jobs'/case
        if original_job_id is None:
            matches=[]
            for path in jobs.glob('j_*/request.json'):
                request=read_json(path)
                if request.get('operation')=='access'and request.get('request_id')==proof.get('login_request_id'):matches.append(path.parent.name)
            if len(matches)!=1:raise APIError('registered_login_source','原认证job不唯一')
            original_job_id=matches[0]
        folder=self.service.job_folder(case,original_job_id);request=read_json(folder/'request.json');status=self.service.result(case,original_job_id)
        parameters=request.get('parameters',{})
        if(request.get('operation')!='access'or parameters.get('action')not in (None,'ensure')or set(parameters)-{'action','tab_id'}or parameters.get('tab_id')!=self.state['tab_id']or
                request.get('execute')is not True or request.get('case_id')!=case or request.get('request_id')!=proof.get('login_request_id')or
                status.get('status')not in {'completed','failed'}or status.get('recovery_required')):
            raise APIError('registered_login_original','原认证任务尚未停止或不属于本次原界面')
        auth_path=folder/'output.auth.json';auth=read_json(auth_path)
        if(auth.get('schema')!='ustc-mac-credential-use-v1'or auth.get('phase')!='credential_use_unconfirmed'or auth.get('state')not in {'credential_use_failed','credential_consumer_unavailable'}or
                auth.get('login_submitted')is not None or auth.get('case_id')!=case or auth.get('request_id')!=request['request_id']or
                auth.get('tab_id')!=self.state['tab_id']or auth_path.with_suffix('.json.claim').exists()or
                auth.get('config_sha256')!=configuration()[1]):
            raise APIError('registered_login_unknown','不是原凭据消费前失败，不能重试未知登录')
        if relocation:
            from finance_backend import transport_identity
            current=transport_identity();original=auth.get('browser_transport',{})
            if(original.get('window_id')!=proof['new_window_id']or current.get('window_id')!=self.state['window_id']or
                    current.get('backend')!='edge_native_messaging'or current.get('tab_id_namespace')!=self.state['namespace']or
                    {k:v for k,v in original.items()if k!='window_id'}!={k:v for k,v in current.items()if k!='window_id'}):
                raise APIError('registered_login_source','原消费前认证通道漂移，不能领用迁移页')
        else:assert_original_transport(auth)
        with browser_lock():observed=snapshot(self.state['tab_id'])
        if observed.get('state')!='login_required'or observed.get('path')!='https://id.ustc.edu.cn/cas/login'or observed.get('password_ready')is not False:
            raise APIError('registered_login_page','原界面不再是空密码CAS登录页，先核对原登录状态')
        key=hashlib.sha256((proof['namespace']+':'+original_job_id).encode()).hexdigest()[:24]
        path=self.folder/('credential-login-resume-'+key+'.json');request_id='credential-login-resume-'+key
        if path.exists():raise APIError('registered_login_recovery_original','已有原认证恢复意图，只查原任务，不循环重新登录或开页')
        record={'schema':'ustc-registered-credential-login-resume-v1','phase':'intent_prepared','case_id':case,
            'original_job_id':original_job_id,'original_request_sha256':hashlib.sha256((folder/'request.json').read_bytes()).hexdigest(),
            'auth_receipt':str(auth_path),'auth_receipt_sha256':hashlib.sha256(auth_path.read_bytes()).hexdigest(),
            'tab_id':self.state['tab_id'],'window_id':self.state['window_id'],'namespace':self.state['namespace'],
            'request_id':request_id,'financial_business_replayed':False,'new_interface_created':False,'credential_values_persisted':False}
        if relocation:record['original_owned_view_relocation']=relocation
        atomic_json(path,record)
        try:
            state=self.ensure(_allow_reentry=False,_access_request_id=request_id)
        except Exception as error:
            record.update(phase='recovery_incomplete',error_code=getattr(error,'code',type(error).__name__));atomic_json(path,record);raise
        if any(state.get(k)!=record[k]for k in ['tab_id','window_id','namespace']):
            raise APIError('registered_login_recovery_identity','认证后原界面归属改变，保留原意图核对')
        record.update(phase='verified',backend_verified=True,identity_verified=True);atomic_json(path,record)
        proof.update(phase='verified',backend_verified=True,identity_verified=True,
            credential_login_recovery={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        atomic_json(proof_path,proof)
        return state

    def recover_backend_session(self, case, diagnostic):
        """每个失效文档最多新开一次；原页/意图保留，不重放任何财务请求。"""
        import hashlib
        pending=[c for c in self.ids if self.service.overview(c).get('unverified_write')]
        if pending:
            raise APIError('registered_backend_pending','存在未知财务写入；保留原页面，只恢复原任务和独立回读')
        original=dict(self.state)
        previous_reentry=original.get('backend_reentry_receipt')
        if previous_reentry and read_json(Path(previous_reentry)).get('phase')!='verified':
            raise APIError('registered_reentry_incomplete','上次新界面登录/后端核验尚未完成；保留原页和原登录意图，不循环开页')
        key=hashlib.sha256((original['namespace']+':'+original['tab_id']).encode()).hexdigest()[:20]
        path=self.folder/('backend-reentry-'+key+'.json')
        request_id='backend-reentry-'+key
        if path.exists():
            record=read_json(path)
            if record.get('original_tab_id')!=original['tab_id'] or record.get('namespace')!=original['namespace']:
                raise APIError('registered_reentry_identity','原重登录意图身份变化，不另开页')
        else:
            record={'phase':'open_intent','original_tab_id':original['tab_id'],'namespace':original['namespace'],
                'reason':diagnostic.get('state'),'http_status':diagnostic.get('http_status'),
                'request_id':request_id,'system_write':False,'business_replayed':False}
            atomic_json(path,record)
        def enqueued(job):
            record['open_job_id']=job['job_id'];atomic_json(path,record)
        job,opened=self.job(case,'access',{'action':'new-interface','tab_id':original['tab_id'],
            'silent':True,'separate_window':True},'backend-reentry',True,request_id=request_id,on_enqueued=enqueued)
        if opened.get('ok') is not True or not opened.get('page_opened'):
            raise APIError('registered_reentry_unconfirmed','原新界面意图尚未确认；只恢复原job，不再次开页')
        # 开页任务前可能已按原文档回执解除冻结并迁移窗口，保存其最新归属链。
        original=dict(self.state)
        tab=str(opened['new_tab_id']);window=str(opened['native_created_window_id'])
        self.state={'case_id':self.case,'tab_id':tab,'window_id':window,'namespace':original['namespace'],
            'created_by_registered_workflow':True,'phase':'opening','last_case_id':case,
            'creation_receipt':{'tab_id':tab,'window_id':window,'window_created':True},'owned_tab_ids':[tab],
            'previous_workpage':original,'backend_reentry_receipt':str(path)}
        atomic_json(self.state_path,self.state)
        record.update(phase='login_pending',new_tab_id=tab,new_window_id=window,login_request_id='reentry-login-'+key);atomic_json(path,record)
        try:
            state=self.ensure(_allow_reentry=False,_access_request_id='reentry-login-'+key)
        except Exception as error:
            record.update(phase='reentry_incomplete',error_code=getattr(error,'code',type(error).__name__))
            atomic_json(path,record);raise
        record.update(phase='verified',backend_verified=True,identity_verified=True);atomic_json(path,record)
        return state

    def configure_backend(self):
        import sys
        from registered_bridge import status

        if sys.platform not in {'darwin', 'win32'}:
            raise APIError('registered_platform', '登记工作页须使用Windows或Mac原生桥')
        bridge = status()
        if bridge.get('ready') is not True:
            raise APIError('registered_bridge', '本机原生桥未连接')
        os.environ.update(
            REIMBURSEMENT_EDGE_BACKEND='edge_native_messaging',
            REIMBURSEMENT_EDGE_TAB_NAMESPACE=bridge['tab_id_namespace'],
            REIMBURSEMENT_EXPECTED_USERID=self.cfg['expected_userid'],
        )
        if self.cfg.get('native_only_legacy_isolation') is True:
            os.environ['REIMBURSEMENT_NATIVE_ONLY_ISOLATION'] = '1'
        else:
            os.environ.pop('REIMBURSEMENT_NATIVE_ONLY_ISOLATION', None)
        return bridge

    def begin_case(self, case, force_new=False):
        from registered_bridge import status, new_finance_window, inventory, close_tab

        bridge = self.configure_backend()
        old = getattr(self, 'state', read_json(self.state_path) if self.state_path.exists() else {})
        pending = self.service.overview(case).get('unverified_write')
        if pending:
            if old.get('last_case_id') != case or old.get('namespace') != bridge['tab_id_namespace']:
                raise APIError(
                    'registered_pending_page', '原写入不在当前登记工作页，仅读原job恢复，不重建现场'
                )
            self.state = old
            os.environ['REIMBURSEMENT_EDGE_WINDOW_ID'] = old['window_id']
            return old['tab_id']
        # No financial action is replayed here. New native pages avoid stale
        # workflow context, frozen background documents and nested window caches.
        if (
            old.get('phase') == 'opening'
            and old.get('last_case_id') == case
            and old.get('created_by_registered_workflow') is True
            and old.get('namespace') == bridge['tab_id_namespace']
        ):
            fresh = old['creation_receipt']
            bridge = status()
            previous_workpage = old.get('previous_workpage', {})
            self.state = old
            self.finish_window_request(case, fresh)
        else:
            fresh = self.open_window(case, bridge)
            bridge = status()
            previous_workpage = old
            provisional = {
                'case_id': self.case,
                'created_by_registered_workflow': True,
                'phase': 'opening',
                'last_case_id': case,
                'tab_id': str(fresh['tab_id']),
                'window_id': str(fresh['window_id']),
                'namespace': bridge['tab_id_namespace'],
                'creation_receipt': fresh,
                'owned_tab_ids': [str(fresh['tab_id'])],
                'previous_workpage': old,
            }
            atomic_json(self.state_path, provisional)
            self.finish_window_request(case, fresh)
            self.state = provisional
        os.environ['REIMBURSEMENT_EDGE_TAB_NAMESPACE'] = bridge['tab_id_namespace']
        os.environ['REIMBURSEMENT_EDGE_WINDOW_ID'] = str(fresh['window_id'])
        tab = str(fresh['tab_id'])
        window = str(fresh['window_id'])
        result, body = self.job(
            case, 'access', self.access_parameters(tab), 'fresh-native-page', True
        )
        window = self.state['window_id']
        if body.get('state') != 'ready':
            raise APIError('registered_fresh_login', '新工作页登录尚未就绪，未开始本单写入')
        actual = next(
            (
                str(m['tab_id'])
                for m in result.get('messages', [])
                if m.get('state') == 'ready' and m.get('tab_id')
            ),
            tab,
        )
        hits = [
            t
            for t in inventory()['tabs']
            if t['window_id'] == window
            and t['tab_id'] == actual
            and t['path'] == 'https://cwzh.ustc.edu.cn/YBX/main.jsp'
        ]
        if len(hits) != 1:
            raise APIError('registered_fresh_target', '新工作页未取得实际财务主标签')
        _, native = self.job(
            case, 'read_system', {'mode': 'readback', 'tab_id': actual}, 'fresh-native-identity'
        )
        if native.get('identity', {}).get('userid') != self.cfg['expected_userid']:
            raise APIError('registered_fresh_user', '新工作页实际登录人不符')
        for attempt in range(12):
            if (
                str(native.get('currentPage')) == '6'
                and native.get('nativeRequests') == 0
                and not native.get('visibleDialogs')
            ):
                break
            time.sleep(0.25)
            _, native = self.job(
                case, 'read_system', {'mode': 'readback', 'tab_id': actual}, 'fresh-home-readiness'
            )
        else:
            raise APIError('registered_home_loading', '原生首页仍在加载；保留新页，不重复金融动作')
        state = {
            'case_id': self.case,
            'created_by_registered_workflow': True,
            'phase': 'ready',
            'tab_id': actual,
            'window_id': window,
            'namespace': bridge['tab_id_namespace'],
            'last_case_id': case,
            'creation_receipt': fresh,
            'owned_tab_ids': list(dict.fromkeys([tab, actual])),
        }
        atomic_json(self.state_path, state)
        self.state = state
        old = previous_workpage
        previous = old.get('last_case_id')
        if (
            old.get('created_by_registered_workflow') is True
            and old.get('namespace') == bridge['tab_id_namespace']
            and previous in self.ids
            and not self.service.overview(previous).get('unverified_write')
        ):
            # Close only exact tool-owned finance tabs. Any user-created tabs in
            # that window, unrelated browser windows and all old receipts remain.
            observed = inventory()
            known = set(old.get('owned_tab_ids') or [old.get('tab_id')])
            closed = []
            for entry in observed.get('tabs', []):
                if entry['window_id'] == old.get('window_id') and entry['tab_id'] in known:
                    closed.append(
                        close_tab(
                            entry['tab_id'],
                            tab_id_namespace=bridge['tab_id_namespace'],
                            expected_window_id=old['window_id'],
                        )
                    )
            atomic_json(
                self.folder / ('retired-' + str(time.time_ns()) + '.json'),
                {
                    'case_id': previous,
                    'closed': closed,
                    'pending_preserved': True,
                    'system_write': False,
                },
            )
        return actual
