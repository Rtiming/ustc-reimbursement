"""登记工作页、原任务轮询与窗口生命周期；不决定报销阶段。"""

from __future__ import annotations

import os
import time

from assistant_api import APIError, atomic_json, read_json


class RegisteredSession:
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

    def job(
        self, case, operation, parameters, label, execute=False, request_id=None, on_enqueued=None
    ):
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
                self.configure_backend()
                os.environ['REIMBURSEMENT_EDGE_WINDOW_ID'] = self.state['window_id']
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
            else {}
        )
        while isinstance(body.get('result'), dict):
            body = body['result']
        return result, body

    def recover_frozen(self, allow_access=False):
        """迁移原文档保持原任务；不刷新页面，不重放未确认的金融动作。"""
        from mac_edge_bridge import inventory, relocate_finance_tab

        state = self.state
        if state.get('created_by_registered_workflow') is not True:
            return
        observed = inventory()
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

    def ensure(self, *, preserve_original=False):
        import sys

        if sys.platform != 'darwin':
            raise APIError('registered_platform', '当前自动设备接入仅在Mac原生桥完成实机验收')
        from mac_edge_bridge import inventory, new_finance_window, wake_finance_tab

        bridge = self.configure_backend()
        state = read_json(self.state_path) if self.state_path.exists() else {}
        if preserve_original and state.get('namespace') != bridge['tab_id_namespace']:
            raise APIError('registered_original_namespace', '原工作页连接身份变化，保留原任务核对')
        snapshot = inventory()
        existing = [
            t
            for t in snapshot['tabs']
            if t.get('tab_id') == state.get('tab_id')
            and t.get('window_id') == state.get('window_id')
            and t.get('path') == 'https://cwzh.ustc.edu.cn/YBX/main.jsp'
        ]
        if (
            len(existing) == 1
            and state.get('case_id') == self.case
            and state.get('created_by_registered_workflow') is True
        ):
            tab, window = state['tab_id'], state['window_id']
        else:
            if preserve_original or any(
                self.service.overview(case).get('unverified_write') for case in self.ids
            ):
                raise APIError('registered_original_page', '原工作页不在，不能用新页替代未结束现场')
            opened = new_finance_window()
            tab, window = str(opened['tab_id']), str(opened['window_id'])
            state = {
                'case_id': self.case,
                'tab_id': tab,
                'window_id': window,
                'creation_receipt': opened,
                'created_by_registered_workflow': True,
            }
            atomic_json(self.state_path, state)
        os.environ['REIMBURSEMENT_EDGE_WINDOW_ID'] = window
        # 冻结页先恢复原文档，避免把渲染器暂停误判成登录失效。
        self.state = state
        if len(existing) == 1 and existing[0].get('frozen') is True:
            self.recover_frozen()
            window = state['window_id']
        case = state.get('last_case_id') if state.get('last_case_id') in self.ids else self.ids[0]
        result, body = self.job(case, 'access', {'action': 'ensure', 'tab_id': tab}, 'ensure', True)
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
        _, native = self.job(case, 'read_system', {'mode': 'readback', 'tab_id': tab}, 'identity')
        if native.get('identity', {}).get('userid') != self.cfg['expected_userid']:
            raise APIError(
                'registered_login_user', '本机登录人与本案明确账号不同，不填单、不切换其他账号'
            )
        serial = native.get('serial')
        by_serial = {
            read_json(self.service.resolve(c)[1]['form']).get('serial'): c for c in self.ids
        }
        current = by_serial.get(serial) or case
        # A saved applicant page is left intact. The batch uses a clean,
        # separately owned page rather than forcing that page through its
        # history-dependent Return/header routes.
        state.update(
            tab_id=tab, window_id=window, namespace=bridge['tab_id_namespace'], last_case_id=current
        )
        atomic_json(self.state_path, state)
        self.state = state
        self.initial_states = {}
        return state

    def configure_backend(self):
        import sys
        from mac_edge_bridge import status

        if sys.platform != 'darwin':
            raise APIError('registered_platform', '登记工作页当前只在Mac原生桥验收')
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
        from mac_edge_bridge import status, new_finance_window, inventory, close_tab

        self.configure_backend()
        old = getattr(self, 'state', read_json(self.state_path) if self.state_path.exists() else {})
        pending = self.service.overview(case).get('unverified_write')
        if pending:
            if old.get('last_case_id') != case:
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
        ):
            fresh = old['creation_receipt']
            bridge = status()
            previous_workpage = old.get('previous_workpage', {})
            self.state = old
        else:
            fresh = new_finance_window()
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
            self.state = provisional
        os.environ['REIMBURSEMENT_EDGE_TAB_NAMESPACE'] = bridge['tab_id_namespace']
        os.environ['REIMBURSEMENT_EDGE_WINDOW_ID'] = str(fresh['window_id'])
        tab = str(fresh['tab_id'])
        window = str(fresh['window_id'])
        result, body = self.job(
            case, 'access', {'action': 'ensure', 'tab_id': tab}, 'fresh-native-page', True
        )
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
