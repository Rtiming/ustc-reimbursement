"""登记工作页的归属、冻结迁移和原文档恢复；不派发业务请求。"""
from __future__ import annotations

import os
import time
from pathlib import Path

from assistant_api import APIError, atomic_json, read_json

REGISTERED_ROOT = Path(__file__).resolve().parents[1] / '.local/registered_workflows'


def verify_marker_relocations(receipt, original, current):
    """Validate the recorded original-document chain; never relax epoch/backend."""
    import hashlib
    from material_intake import reject_links
    records = receipt.get('marker_recoveries')
    if (not isinstance(records, list) or not records or len(records) > 2
            or original.get('backend') != 'edge_native_messaging'
            or {k:v for k,v in original.items() if k != 'window_id'} != {k:v for k,v in current.items() if k != 'window_id'}):
        return False
    window = original.get('window_id')
    for row in records:
        if not isinstance(row, dict) or not isinstance(row.get('receipt'), str): return False
        path = Path(row['receipt'])
        if (not path.is_absolute() or path.parent.parent != REGISTERED_ROOT or
                not path.name.startswith('page-relocation-') or path.suffix != '.json'):
            return False
        try:
            reject_links(path)
            if hashlib.sha256(path.read_bytes()).hexdigest() != row.get('receipt_sha256'): return False
            proof = read_json(path)
        except (OSError, ValueError):
            return False
        if (row.get('old_window_id') != window or row.get('tab_id') != receipt.get('tab_id')
                or row.get('original_marker') != receipt.get('result_marker') or row.get('script_reexecuted') is not False
                or proof.get('old_window_id') != window or proof.get('tab_id') != receipt.get('tab_id')
                or proof.get('namespace') != original.get('tab_id_namespace')
                or proof.get('result', {}).get('window_id') != row.get('window_id')
                or proof.get('result', {}).get('woken') is not True):
            return False
        window = row['window_id']
    return window == current.get('window_id')


def current_registered_marker_binding(receipt, current):
    """原marker恢复时接纳登记owner随后完成的单次原文档迁移。"""
    import hashlib
    import re
    filename=os.environ.get('REIMBURSEMENT_REGISTERED_SESSION')
    original=receipt.get('browser_transport')
    if not filename or not isinstance(original,dict) or not re.fullmatch(r'reimbursement-result-[a-f0-9]{32}',str(receipt.get('result_marker',''))):
        return False
    try:
        owner=RegisteredPageOwner.from_state_path(filename,receipt.get('tab_id'))
        path=Path(owner.state['last_relocation_receipt'])
        from material_intake import reject_links
        if path.parent!=owner.folder or not path.name.startswith('page-relocation-') or path.suffix!='.json':return False
        reject_links(path)
        proof=read_json(path)
        rows=list(receipt.get('marker_recoveries',[]))
        rows.append({'old_window_id':proof['old_window_id'],'window_id':owner.state['window_id'],
            'receipt':str(path),'receipt_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'tab_id':receipt['tab_id'],'original_marker':receipt['result_marker'],'script_reexecuted':False})
        return verify_marker_relocations({**receipt,'marker_recoveries':rows},original,current)
    except (APIError,OSError,ValueError,KeyError,TypeError):
        return False


class RegisteredPageOwner:
    @classmethod
    def before_child_dispatch(cls, tab_id):
        """子阶段只接纳同tab/epoch且有迁移回执的最新窗口；不重放业务。"""
        filename=os.environ.get('REIMBURSEMENT_REGISTERED_SESSION')
        if not filename:return
        from material_intake import reject_links
        from finance_backend import transport_identity
        path=Path(filename);reject_links(path)
        if not path.is_absolute() or path.name!='session.json' or path.parent.parent!=REGISTERED_ROOT:
            raise APIError('registered_page_owner','子阶段session不属于本工具')
        state=read_json(path);transport=transport_identity()
        if (state.get('case_id')!=path.parent.name or state.get('created_by_registered_workflow')is not True
                or state.get('tab_id')!=tab_id or tab_id not in state.get('owned_tab_ids',[])
                or state.get('namespace')!=transport.get('tab_id_namespace')
                or transport.get('backend')!='edge_native_messaging'):
            raise APIError('registered_page_owner','子阶段页身份改变；不派发')
        owner=cls();owner.state_path=path;owner.folder=path.parent;owner.progress=None
        owner.state={**state,'window_id':transport.get('window_id')}
        owner.refresh_owned_state()
        owner.recover_frozen()

    @classmethod
    def from_state_path(cls, filename, tab_id):
        """Only runtime-owned pages can be moved while reading an original marker."""
        from material_intake import reject_links
        from finance_backend import transport_identity
        path = Path(filename); reject_links(path)
        root = REGISTERED_ROOT
        if not path.is_absolute() or path.name != 'session.json' or path.parent.parent != root:
            raise APIError('registered_page_owner', '原页恢复只接受本工具登记session')
        state = read_json(path); transport = transport_identity()
        if (state.get('case_id') != path.parent.name or state.get('created_by_registered_workflow') is not True
                or state.get('tab_id') != tab_id or tab_id not in state.get('owned_tab_ids', [])
                or state.get('namespace') != transport.get('tab_id_namespace')
                or state.get('window_id') != transport.get('window_id')
                or transport.get('backend') != 'edge_native_messaging'):
            raise APIError('registered_page_owner', '原页归属或连接身份不同；不移动、不重放')
        owner = cls()
        owner.state, owner.state_path, owner.folder, owner.progress = state, path, path.parent, None
        return owner

    def refresh_owned_state(self):
        """Adopt only a proved child-runtime relocation, preserving tab and epoch."""
        if not self.state_path.exists() or not getattr(self, 'state', None):
            return
        fresh = read_json(self.state_path)
        if fresh.get('window_id') == self.state.get('window_id'):
            return
        if any(fresh.get(k) != self.state.get(k) for k in ('case_id','tab_id','namespace','created_by_registered_workflow')):
            raise APIError('registered_page_owner', '子任务恢复后的页身份不同')
        receipt = Path(fresh.get('last_relocation_receipt', ''))
        if receipt.parent != self.folder:
            raise APIError('registered_page_owner', '缺少本session原页迁移回执')
        proof = read_json(receipt)
        if (proof.get('old_window_id') != self.state['window_id'] or proof.get('tab_id') != self.state['tab_id']
                or proof.get('namespace') != self.state['namespace']
                or proof.get('result', {}).get('window_id') != fresh['window_id']
                or proof.get('result', {}).get('woken') is not True):
            raise APIError('registered_page_owner', '原页迁移回执不闭合')
        self.state = fresh
        os.environ['REIMBURSEMENT_EDGE_WINDOW_ID'] = fresh['window_id']

    def recover_frozen(self, allow_access=False):
        """迁移原文档保持原任务；不刷新页面，不重放未确认的金融动作。"""
        from registered_bridge import inventory, relocate_finance_tab, frozen_relocation_options

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
            if not (allow_access and hits[0]['path'] in (
                'https://id.ustc.edu.cn/cas/login',
                'https://cwzh.ustc.edu.cn/WFManager/home2.jsp',
                'https://cwzh.ustc.edu.cn/YBX/login.html',
            )):
                raise APIError('registered_tab_changed', '当前页不是登记财务主页面')
        if hits[0].get('frozen') is not True:
            return
        pending = self.folder / 'page-relocation-pending.json'
        if pending.exists():
            self.settle_rejected_relocation(pending)
        if pending.exists():
            raise APIError(
                'registered_relocation_unknown',
                '原迁移尚未取得回执；只按原tab盘点，不重复迁移：' + str(pending),
            )
        self.release_initial_portal(observed)
        options=frozen_relocation_options(observed,state['tab_id'],state['window_id'])
        intent = {
            'tab_id': state['tab_id'],
            'old_window_id': state['window_id'],
            'namespace': observed['tab_id_namespace'],
            'requested_at_ns': time.time_ns(),
            'selection_binding': options,
        }
        atomic_json(pending, intent)
        result = relocate_finance_tab(
            state['tab_id'],
            tab_id_namespace=observed['tab_id_namespace'],
            expected_window_id=state['window_id'],
            **({'allow_access': True} if allow_access else {}),
            **options,
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

    def settle_rejected_relocation(self, pending):
        """只收尾扩展在变更窗口前明确拒绝的迁移，未知响应仍保留pending。"""
        import hashlib
        from registered_bridge import STATE

        intent = read_json(pending)
        matches = []
        for path in (STATE / 'requests').glob('*.json'):
            request = read_json(path)
            args = request.get('arguments', {})
            if (request.get('operation') != 'relocate-finance-tab' or
                    request.get('created_at', 0) < intent['requested_at_ns'] / 1e9 - 1 or
                    args.get('tab_id') != intent['tab_id'] or
                    args.get('expected_window_id') != intent['old_window_id'] or
                    request.get('epoch') != intent['namespace'].rsplit(':', 1)[-1]):
                continue
            response = STATE / 'responses' / path.name
            if not response.exists():
                continue
            reply = read_json(response)
            if (reply.get('id') == request['id'] and reply.get('epoch') == request['epoch'] and
                    reply.get('ok') is False and reply.get('error_code') == 'relocation_target_not_isolated_frozen'):
                matches.append({'request': str(path), 'request_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                'response': str(response), 'response_sha256': hashlib.sha256(response.read_bytes()).hexdigest()})
        if len(matches) == 1:
            atomic_json(self.folder / ('page-relocation-rejected-' + str(intent['requested_at_ns']) + '.json'),
                        {**intent, 'phase': 'rejected_before_mutation', 'proof': matches[0]})
            pending.rename(self.folder / ('page-relocation-original-' + str(intent['requested_at_ns']) + '.json'))

    def release_initial_portal(self, observed):
        """只关闭本工作流创建并登记的旧门户页，主财务页及用户页保留。"""
        from registered_bridge import close_tab

        state = self.state
        creation = state.get('creation_receipt', {})
        portal = creation.get('tab_id')
        if (state.get('created_by_registered_workflow') is not True or creation.get('window_created') is not True or
                not portal or portal == state['tab_id'] or portal not in state.get('owned_tab_ids', []) or
                state.get('initial_portal_closed') is True):
            return
        matches = [t for t in observed['tabs'] if t['tab_id'] == portal]
        if not matches:
            if observed.get('tab_inventory_complete') is True and portal not in observed.get('all_tab_ids', []):
                state['initial_portal_closed'] = True
                atomic_json(self.state_path, state)
            return
        if (len(matches) != 1 or matches[0]['window_id'] != creation['window_id'] or
                matches[0]['path'] != 'https://cwzh.ustc.edu.cn/WFManager/home2.jsp'):
            return
        receipt = close_tab(portal, tab_id_namespace=observed['tab_id_namespace'], expected_window_id=creation['window_id'])
        atomic_json(self.folder / 'initial-portal-close.json', receipt)
        state['initial_portal_closed'] = True
        atomic_json(self.state_path, state)
