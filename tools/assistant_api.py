"""项目内助手接口：固定业务操作、显式案件、版本检查和可恢复后台任务。

本模块仅使用标准库。MCP和本地JSON调用共用它，不向模型开放shell或JS执行。
"""
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from finance_backend import mac_native_enabled
from assistant_contract import APIError, DOCS, CASE_PROFILES
from workspace_config import resolve_material_roots

from operator_support import bundle, init_case, stamp
from run_case import STAGES
from validate_config import money
from platform_runtime import background_popen_kwargs, private_path, process_alive
from assistant_worker_launch import launch_worker, WorkerLaunchUnknown

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path: sys.path.insert(0, str(PROJECT))
# 只折叠各入口已声明的默认值；目标、顺序、execute和非默认值仍属于不同意图。
REQUEST_DEFAULTS = {
    'check': {'hashes': True}, 'scan': {'invoices': True},
    'stage': {'tab_id': None},
    'invoice': {'start': 0, 'count': 10, 'tab_id': None},
    'read_system': {'mode': 'readback', 'job_id': None, 'artifact_id': None},
    'record_result': {'serial': None, 'outcome': None, 'note': ''},
    'activity': {'check_only': True, 'allow_incomplete': False},
    'correction': {'tab_id': None, 'group': '', 'key': '', 'kind': '', 'job_id': None, 'artifact_id': None},
    'submission': {'tab_id': None},
    'access': {'tab_id': None, 'action': None, 'job_id': None, 'artifact_id': None},
    'invoice_verification': dict.fromkeys(['invoice_numbers', 'view', 'job_id', 'artifact_id', 'operation_key', 'grid', 'expected_userid']),
    'invoice_sources': {'invoice_numbers': None},
    'pdf_export': {'silent': True},
    'invoice_edit': {'job_id': None, 'artifact_id': None, 'inspection_job_id': None, 'inspection_artifact_id': None},
    'expense_amount': dict.fromkeys(['source_job_id', 'source_artifact_id', 'job_id', 'artifact_id','claims_path']),
    'invoice_selection': dict.fromkeys(['source_job_id', 'source_artifact_id', 'audit_refs', 'selectors', 'mode', 'grid', 'serial',
        'expected_userid', 'expected_before_guids', 'snapshot_job_id', 'snapshot_artifact_id', 'preview_job_id', 'preview_artifact_id', 'job_id', 'artifact_id']),
    'invoice_retry': dict.fromkeys(['expected_userid', 'grid', 'target_guids', 'target_filecodes', 'planned_action', 'targets',
        'source_job_id', 'source_artifact_id', 'pool_job_id', 'pool_artifact_id', 'recognition_job_id', 'recognition_artifact_id',
        'inspection_job_id', 'inspection_artifact_id', 'plan_job_id', 'plan_artifact_id', 'receipt_job_id', 'receipt_artifact_id', 'job_id', 'artifact_id']),
}


def canonical_request(operation, parameters, execute):
    if not isinstance(parameters, dict) or not isinstance(execute, bool):
        raise APIError('invalid_arguments', '参数须为对象，execute须为布尔值')
    defaults = REQUEST_DEFAULTS.get(operation, {})
    normalized = {key: copy.deepcopy(value) for key, value in parameters.items()
                  if key not in defaults or type(value) is not type(defaults[key]) or value != defaults[key]}
    if operation == 'access' and normalized.get('action') == ('ensure' if execute else 'check'):
        normalized.pop('action')
    if operation == 'access' and normalized.get('action') == 'new-interface' and normalized.get('silent') is True:
        normalized.pop('silent')
    return {'operation': operation, 'parameters': normalized, 'execute': execute}


def request_fingerprint(request):
    return hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with tmp.open('x', encoding='utf-8') as stream:
            private_path(tmp)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def masked(data, _key='', _materials=False, _registered_paths=frozenset()):
    """银行卡/普通文本继续遮罩；声明的材料路径和规范业务标识可无损回读。"""
    if isinstance(data, dict):
        result = {k: masked(v, k, _materials or k in ['materials', 'original_files'], _registered_paths) for k, v in data.items()}
        name, path = data.get('name'), data.get('path')
        chain = data.get('source_chain')
        member = chain[-1] if isinstance(chain, list) and chain else chain
        declared_names = {Path(value).name for value in [path, member] if isinstance(value, str)}
        if _materials and isinstance(name, str) and name in declared_names and Path(name).suffix.lower() in ['.pdf', '.ofd', '.png', '.jpg', '.jpeg', '.webp', '.zip', '.doc', '.docx', '.xls', '.xlsx', '.csv', '.txt', '.md']:
            result['name'] = name
        return result
    if isinstance(data, list): return [masked(v, _key, _materials, _registered_paths) for v in data]
    if isinstance(data, str):
        if _key == 'path' and data in _registered_paths:
            return data
        # overview → update_case 的材料数组必须保持原样，不能修改日期式文件名。
        if _materials and _key in ['path', 'filename', 'source_chain', 'source_file', 'source_path', 'source_archive']:
            return data
        if (_key.endswith('sha256') or _key == 'filename_sha256_candidate') and re.fullmatch(r'[a-fA-F0-9]{64}', data):
            return data
        # 乐观锁版本必须能从 overview 原样传回 update_case。
        if _key in ['revision', 'expected_revision'] and re.fullmatch(r'[a-fA-F0-9]{64}', data):
            return data
        if _key == 'operation_key' and re.fullmatch(r'op_[a-f0-9]{20}', data):
            return data
        if _key == 'job_id' and re.fullmatch(r'j_[a-f0-9]{24}', data):
            return data
        if _key in ['guid', 'native_guid', 'native_main_guid', 't.tk_guid', 't.tk_mainguid', 'file_code', 't.filesequcode'] and re.fullmatch(r'[a-fA-F0-9]{32}', data):
            return data
        if _key in ['tax_id', 'buyer_tax_id', 'seller_tax_id', 'expected_tax_id'] and re.fullmatch(r'(?:[A-Za-z0-9]{15}|[A-Za-z0-9]{18}|[A-Za-z0-9]{20})', data):
            return data
        if _key in ['invoice_number', 'invoice_number_candidates', 'invoice_numbers'] and re.fullmatch(r'[0-9]{20}', data):
            return data
        return re.sub(r'(?<!\d)(\d{6})(\d{6,9})(\d{4})(?!\d)', lambda m: m[1]+'*'*len(m[2])+m[3], data)
    return data


def merge(old, patch):
    out = copy.deepcopy(old)
    for k, v in patch.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


class AssistantService:
    def __init__(self, workspace=PROJECT, material_roots=None, python=None):
        self.root = Path(workspace).resolve()
        self.python = str(python or sys.executable)
        self.material_roots = resolve_material_roots(self.root, material_roots)
        self.jobs = self.root/'.local/assistant_jobs'

    @staticmethod
    def case_id(value):
        if not isinstance(value, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{1,63}', value):
            raise APIError('invalid_case_id', '案件标识为2至64位小写字母、数字、下划线或短横线，以字母开头')
        return value

    def resolve(self, case_id):
        self.case_id(case_id)
        p = self.root/'cases'/case_id/'operator.json'
        default = self.root/'data/operator_default.json'
        if default.exists() and read_json(default).get('case_id') == case_id: p = default
        if not p.is_file(): raise APIError('case_not_found', '案件不存在；先cases列出或new_case建立空案')
        if not p.resolve().is_relative_to(self.root): raise APIError('path_escape', '案件入口不能指向项目外')
        b, paths = bundle(p)
        if b['case_id'] != case_id or any(not x.is_relative_to(p.parent.resolve()) for x in paths.values()):
            raise APIError('case_mismatch', '案件标识或资料路径不匹配')
        if any(read_json(x).get('case_id') != case_id for x in paths.values()):
            raise APIError('case_mismatch', '四份案件资料的case_id必须一致')
        return p, paths

    def revision(self, case_id):
        p, paths = self.resolve(case_id)
        h = hashlib.sha256(p.read_bytes())
        for key in ['case', 'form', 'invoices', 'state']: h.update(paths[key].read_bytes())
        return h.hexdigest()

    @contextmanager
    def mutex(self, case_id, wait=0, browser=False):
        self.case_id(case_id)
        folder = self.root/'.local/assistant_locks'
        folder.mkdir(parents=True, exist_ok=True)
        lock = folder/('browser.lock' if browser else 'case-'+case_id+'.lock')
        lock_id = uuid.uuid4().hex
        deadline = time.monotonic()+wait
        while True:
            try:
                with lock.open('x', encoding='utf-8') as f:
                    private_path(lock)
                    json.dump({'pid': os.getpid(), 'lock_id': lock_id, 'started_at_utc': stamp()}, f)
                break
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise APIError('case_busy', '此案已有操作持有锁；先查任务结果。进程异常遗留锁须按接口手册核对，不自动删除')
                time.sleep(0.1)
        try: yield
        finally:
            try:
                current = read_json(lock)
            except (OSError, ValueError):
                current = None
            # 无法证明子树结束时保留锁；也不能删除已被另一持有者替换的锁。
            if isinstance(current, dict) and current.get('lock_id') == lock_id and current.get('retain_for_recovery') is not True:
                lock.unlink(missing_ok=True)

    def cases(self):
        paths = [self.root/'data/operator_default.json', *sorted((self.root/'cases').glob('*/operator.json'))]
        rows, seen = [], set()
        for p in paths:
            if not p.exists(): continue
            c = read_json(p).get('case_id')
            if c in seen: continue
            _, files = self.resolve(c)
            form, state = read_json(files['form']), read_json(files['state'])
            rows.append({'case_id': c, 'serial': form.get('serial'), 'amount': form.get('amount'), 'status': state.get('status')})
            seen.add(c)
        return {'ok': True, 'cases': rows, 'system_write': False}

    def active_jobs(self, case_id):
        rows = []
        for p in (self.jobs/case_id).glob('*/status.json'):
            r = read_json(p)
            if r['status'] in ['queued', 'running']: rows.append({'job_id': p.parent.name, 'status': r['status']})
        return rows

    def overview(self, case_id, include_parameters=False):
        from reimburse import overview
        p, paths = self.resolve(case_id)
        if read_json(paths['case']).get('material_context', {}).get('workflow_entry'):
            from registered_inputs import load_definition
            cfg, index, auth, definition = load_definition(self, case_id)
            if read_json(paths['form']).get('serial'):
                raise APIError('registered_batch_serial', '批次导航入口不能同时冒充一张财务单')
            result = {'ok':True, 'case_id':case_id, 'role':'registered_batch', 'serial':None,
                      'revision':self.revision(case_id), 'api_version':1, 'system_write':False,
                      'document_count':len(read_json(index)['documents']), 'definition':str(definition),
                      'input_index':str(index), 'financial_state':'read_each_document_not_batch_root',
                      'next_action':{'tool':'parameter_cli', 'arguments':['workflow','--case-id',case_id],
                                     'reason':'本案是批次入口；按登记资料查看状态，再明确选择填单或准备待批'}}
            if include_parameters: result['parameters'] = {k:read_json(paths[k]) for k in ['case','form']}
            return masked(result)
        r = overview(p)
        r.update(ok=True, revision=self.revision(case_id), active_jobs=self.active_jobs(case_id), api_version=1)
        r['input_validation'] = self.input_validation(paths)
        if include_parameters: r['parameters'] = {k: read_json(paths[k]) for k in ['case', 'form']}
        if r['active_jobs']: r['next_action'] = {'tool': 'reimbursement_result', 'reason': '先查正在执行的任务，不启动第二个操作'}
        elif r['unverified_write']: r['next_action'] = {'tool': 'reimbursement_read_system', 'mode': 'readback', 'reason': r['do_next']}
        elif r['known_state'] == 'correction_saved_await_resubmission': r['next_action'] = {'tool': 'reimbursement_read_system', 'mode': 'status', 'reason': r['do_next']}
        elif r['known_state'] == 'submitted_to_approval': r['next_action'] = {'tool': 'reimbursement_read_system', 'mode': 'status', 'reason': '本案已进入待审批列表；先读当前状态，不重复提交。领导审批、验证码/本人后续确认和付款仍分别记录'}
        elif r['correction_in_progress']: r['next_action'] = {'tool': 'reimbursement_correction', 'action': 'check', 'reason': r['do_next']}
        elif r['workflow_started']: r['next_action'] = {'tool': 'reimbursement_check', 'reason': '已有本人审核流程；补材料和交用户审核，不重建'}
        elif r['input_overview_missing']: r['next_action'] = {'tool': 'reimbursement_update_case', 'reason': '按缺项集中补输入；revision用于防止覆盖其他人修改'}
        elif not r['input_validation']['ok']: r['next_action'] = {'tool': 'reimbursement_update_case', 'reason': r['input_validation']['error']}
        elif not r['counts']['invoices']: r['next_action'] = {'tool': 'reimbursement_scan', 'reason': '扫描一票一文件原件，再登记'}
        elif not r['serial']: r['next_action'] = {'tool': 'reimbursement_invoice', 'reason': '按workflow规程读池、上传、认证、匹配，再创建和绑定单号'}
        else: r['next_action'] = {'tool': 'reimbursement_stage', 'stage': r['recommended_stage'], 'execute': False, 'reason': '先生成本阶段计划'}
        return masked(r)

    @staticmethod
    def input_validation(paths):
        from validate_config import validate
        c = read_json(paths['form'])
        # 仅在内存提供占位单号以提前检查业务输入；不写入、不作为建单证据。
        c['serial'] = c.get('serial') or '0'
        try:
            validate(c)
            return {'ok': True, 'input_check_only': True}
        except (ValueError, KeyError, TypeError) as e:
            return {'ok': False, 'input_check_only': True, 'error': str(e)}

    def check(self, case_id, hashes=True):
        from operator_support import check
        p, paths = self.resolve(case_id)
        r = check(p, hashes)
        r.update(revision=self.revision(case_id), input_validation=self.input_validation(paths), system_write=False)
        if not r['input_validation']['ok']: r['ok'] = False
        return masked(r)

    def new_case(self, case_id, profile='student_activity'):
        self.case_id(case_id)
        if profile not in CASE_PROFILES: raise APIError('unsupported_profile', '未知材料profile')
        with self.mutex(case_id):
            try: self.resolve(case_id)
            except APIError as e:
                if e.code != 'case_not_found': raise
            else: raise APIError('case_exists', '案件已存在，不覆盖；overview后继续办理')
            dest = self.root/'cases'/case_id
            if not dest.resolve().is_relative_to(self.root): raise APIError('path_escape', '案件目录不能指向项目外')
            r = init_case(case_id, dest, profile)
            r['revision'] = self.revision(case_id)
            return r

    def update_case(self, case_id, document, changes, expected_revision):
        allowed = {'form': {'amount', 'funding', 'expense_allocations', 'expense_categories', 'budget_allocation_mode', 'payments', 'basic', 'pre_review', 'contract', 'delivery', 'review'},
                   'case': {'material_context', 'materials', 'delivery', 'description', 'decisions', 'questions'}}
        if document not in allowed or not isinstance(changes, dict) or not changes or set(changes)-allowed[document]:
            raise APIError('invalid_patch', '只更新fields文档中的业务输入；不编辑单号、票据GUID、state或说明来源')
        if 'review' in changes and (not isinstance(changes['review'], dict) or set(changes['review'])-{'ordinary_promise_authorized'}):
            raise APIError('protected_state', 'review只允许按用户授权登记ordinary_promise_authorized，不伪造流程状态')
        with self.mutex(case_id):
            _, paths = self.resolve(case_id)
            if self.active_jobs(case_id): raise APIError('case_busy', '先等待本案任务结束再修改参数')
            if document == 'form' and (paths['state'].parent/'pending_write.json').exists():
                raise APIError('pending_write', '上次写入尚未核对，先回查再修改填报参数')
            if self.revision(case_id) != expected_revision: raise APIError('revision_conflict', '案件已被修改，重新overview后合并，不覆盖旧版本')
            if document == 'form' and read_json(paths['form']).get('review', {}).get('workflow_started'):
                raise APIError('workflow_started', '已有流程，普通草稿参数接口不改当前单据；材料仍可在case中登记')
            candidate = merge(read_json(paths[document]), changes)
            self.validate_patch(changes, document)
            before = read_json(paths[document])
            log = paths['state'].parent/'receipts'/('input_'+uuid.uuid4().hex+'.json')
            atomic_json(log, {'changed_at_utc': stamp(), 'document': document, 'before': before, 'after': candidate, 'system_write': False})
            atomic_json(paths[document], candidate)
            return {'ok': True, 'revision': self.revision(case_id), 'input_receipt': str(log), 'system_write': False, 'next': '运行check核对金额及缺件；更新输入不等于已保存系统'}

    def validate_patch(self, value, document):
        if document == 'case':
            if 'materials' in value:
                if not isinstance(value['materials'], list): raise APIError('invalid_patch', 'materials须为列表')
                for row in value['materials']:
                    if not isinstance(row, dict): raise APIError('invalid_patch', '材料行须为对象')
                    if row.get('path'): self.material_path(row['path'])
            return
        if 'expense_categories' in value:
            from validate_config import validate_categories
            validate_categories(value['expense_categories'])
        if 'budget_allocation_mode' in value and value['budget_allocation_mode'] not in ['manual','system_default_single_funding']:
            raise APIError('invalid_patch','预算模式仅支持manual或system_default_single_funding')
        if 'basic' in value:
            b = value['basic']
            if not isinstance(b, dict) or set(b)-{'teacher_id', 'teacher_name', 'fields'}: raise APIError('invalid_patch', 'basic字段不在已接通目录')
            fields = b.get('fields', {})
            if not isinstance(fields, dict) or set(fields)-{'t-abst', 't-req_mobile', 't-req_tele', 't-is_have_changefee', 't-td_point', 't-cl_abst'}:
                raise APIError('invalid_patch', 'basic.fields包含未知财务字段')
            if any(not isinstance(x, str) for x in fields.values()): raise APIError('invalid_patch', '财务字段须为字符串')
        def walk(x):
            if isinstance(x, dict):
                for k, v in x.items():
                    if k in ['amount', 'search_amount']: money(v)
                    if k in ['person_id', 'teacher_id'] and not isinstance(v, str): raise APIError('invalid_patch', '工号和学号须为字符串')
                    walk(v)
            elif isinstance(x, list):
                for v in x: walk(v)
        walk(value)
        for key in ['funding', 'expense_allocations', 'payments', 'pre_review']:
            if key in value and (not isinstance(value[key], list) or any(not isinstance(x, dict) for x in value[key])):
                raise APIError('invalid_patch', key+'须为对象列表')
        if 'review' in value and not isinstance(value['review'].get('ordinary_promise_authorized'), bool):
            raise APIError('invalid_patch', '承诺授权须为布尔值，依据本案用户授权登记')

    def set_explanation(self, case_id, text, expected_revision, record_only=True):
        if not isinstance(text, str) or not text.strip() or len(text)>30000 or not isinstance(record_only, bool):
            raise APIError('invalid_explanation', '提供完整定稿正文和布尔record_only')
        with self.mutex(case_id):
            _, paths = self.resolve(case_id)
            if self.active_jobs(case_id): raise APIError('case_busy', '先等待本案任务结束')
            if (paths['state'].parent/'pending_write.json').exists(): raise APIError('pending_write', '上次写入尚未核对，先回查再导入说明')
            if self.revision(case_id) != expected_revision: raise APIError('revision_conflict', '先重新overview再导入说明')
            form = read_json(paths['form'])
            if form.get('review', {}).get('workflow_started') and not record_only:
                raise APIError('workflow_started', '已有流程的说明只留档；不能借导入说明自动安排线上修改')
            source = paths['form'].parent/'receipts'/('explanation_'+uuid.uuid4().hex+'.txt')
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(text, encoding='utf-8')
            notes = form.setdefault('explanations', {})
            canonical = text.rstrip('\r\n')
            notes.update(general=canonical, source_file=str(source), source_text_sha256=hashlib.sha256(canonical.encode()).hexdigest(), record_only=record_only)
            atomic_json(paths['form'], form)
            return {'ok': True, 'revision': self.revision(case_id), 'source_file': str(source), 'record_only': record_only, 'system_write': False}

    def verify_invoice(self, case_id, index, invoice_numbers, note, expected_revision, pool_job_id=None, pool_artifact_id=None, native_guid=None):
        if type(index) is not int or index<0 or not isinstance(invoice_numbers, list) or not invoice_numbers or any(not isinstance(x, str) or not re.fullmatch(r'\d{8}|\d{10}|\d{20}', x) for x in invoice_numbers):
            raise APIError('invoice_candidates', 'index为从0开始的原件索引，invoice_numbers为核对过的8/20位发票号或10位财政票据号字符串列表')
        if not isinstance(note, str) or not note.strip(): raise APIError('verification_note', '记录查看原件核对了哪些字段；不修改原件')
        with self.mutex(case_id):
            _, paths = self.resolve(case_id)
            if self.active_jobs(case_id) or (paths['state'].parent/'pending_write.json').exists(): raise APIError('case_busy', '先结束任务和待回查写入')
            if self.revision(case_id)!=expected_revision: raise APIError('revision_conflict', '重新overview后核对票据')
            m = read_json(paths['invoices'])
            if m.get('serial') or m.get('draft_creation_requested'): raise APIError('claim_exists', '建单后不改原件选择依据')
            rows = m.get('original_files', [])
            if index>=len(rows): raise APIError('invoice_index', '原件索引越界')
            row = rows[index]
            if hashlib.sha256(Path(row['path']).read_bytes()).hexdigest()!=row['sha256']: raise APIError('original_changed', '原件SHA变化，先核对，不修改票据')
            previous = copy.deepcopy(row)
            row.update(invoice_number_candidates=list(dict.fromkeys(invoice_numbers)), visual_check='verified', visual_check_note=note, visual_checked_at_utc=stamp())
            if native_guid:
                if len(invoice_numbers)!=1: raise APIError('binding_number', '航空原生绑定须唯一票号')
                receipt = self.artifact(case_id, pool_job_id, pool_artifact_id)
                data = read_json(receipt).get('result', {})
                matches = [r for g in data.get('grids', []) if str(g.get('funcno'))=='53' for r in g.get('rows', []) if r.get('t.tk_guid')==native_guid]
                if len(matches)!=1 or matches[0].get('t.tk_type')!='3PLANE' or matches[0].get('t.serial_no') or matches[0].get('t.tk_mod_flag')!='NORMAL':
                    raise APIError('native_binding', '只接受完整回读中唯一、未占用、NORMAL航空票记录')
                x = matches[0]
                row['verified_native_binding'] = {'source_sha256': row['sha256'], 'confirmed': True, 'receipt': str(receipt), 'guid': native_guid,
                    'number': invoice_numbers[0], 'type': x['t.tk_type'], 'display': x['t.tk_display_abst'],
                    'amount': x['t.tk_total_amt'].replace(',', ''), 'person_id': x['t.tk_buysno'], 'operator_check_note': note}
            atomic_json(paths['state'].parent/'receipts'/('invoice_review_'+uuid.uuid4().hex+'.json'), {'index': index, 'before': previous, 'after': row, 'system_write': False})
            atomic_json(paths['invoices'], m)
            return {'ok': True, 'revision': self.revision(case_id), 'index': index, 'system_write': False, 'note': '这是操作助手核对记录，不是税务认证'}

    def material_path(self, value):
        if not isinstance(value, str) or not Path(value).is_absolute(): raise APIError('invalid_material_path', '材料使用本机绝对路径')
        p = Path(value).resolve()
        if not any(p.is_relative_to(root) for root in self.material_roots): raise APIError('material_root', '材料路径不在启动时声明的material-root中')
        if not p.exists(): raise APIError('material_missing', '材料不存在：'+str(p))
        if p.is_file() and p.suffix.lower() not in ['.pdf', '.ofd', '.png', '.jpg', '.jpeg', '.webp', '.doc', '.docx', '.xlsx', '.xls', '.csv', '.txt']:
            raise APIError('material_format', '仅处理材料文件格式，不读凭据或浏览器资料')
        return p

    def search_spreadsheets(self, paths, query, exact=False, person_lookup=False, limit=30):
        from search_spreadsheets import search
        if not isinstance(paths, list) or not paths or any(not isinstance(x, str) for x in paths):
            raise APIError('invalid_material_path', 'paths须为非空绝对路径列表')
        if not isinstance(exact, bool) or not isinstance(person_lookup, bool):
            raise APIError('invalid_search', 'exact和person_lookup须为布尔值')
        files = []
        for value in paths:
            p = self.material_path(value)
            candidates = sorted(x for x in p.rglob('*') if x.is_file() and x.suffix.lower() in {'.xlsx', '.csv'} and not x.name.startswith('~$')) if p.is_dir() else [p]
            for candidate in candidates:
                checked = self.material_path(str(candidate.resolve()))
                if checked.suffix.lower() not in {'.xlsx', '.csv'}:
                    raise APIError('material_format', '助手检索入口使用xlsx或csv；旧xls须先另存xlsx')
                files.append(checked)
        result = search(files, query, exact, person_lookup, limit)
        result['system_write'] = False
        return result

    def guide(self, topic='start'):
        if topic not in DOCS: raise APIError('unknown_guide', 'topic须为'+','.join(DOCS))
        portable = (PROJECT / 'data/distribution.json').is_file()
        p = PROJECT / ('docs/用户指南.md' if portable and topic not in {'fields', 'intake', 'planning', 'standards', 'ocr'} else DOCS[topic])
        return {'ok': True, 'topic': topic, 'path': str(p), 'text': p.read_text(encoding='utf-8'), 'system_write': False}

    def capabilities(self, intent='materials'):
        from assistant_guidance import capabilities
        return capabilities(intent)

    def standards_read(self, library_root, profile_id=None):
        from reimbursement_standards import standards_read
        return standards_read(self, library_root, profile_id)

    def standards_init(self, library_root, profile_id, title, mode='student_activity', execute=False):
        from reimbursement_standards import standards_init
        return standards_init(self, library_root, profile_id, title, mode, execute)

    def standards_add_profile(self, library_root, profile_id, title, parent_ids=None,
                              execute=False, expected_registry_sha256=None):
        from reimbursement_standards import standards_add_profile
        return standards_add_profile(self, library_root, profile_id, title, parent_ids, execute, expected_registry_sha256)

    def standards_change(self, library_root, profile_id, change, execute=False, expected_sha256=None):
        from reimbursement_standards import standards_change
        return standards_change(self, library_root, profile_id, change, execute, expected_sha256)

    def standards_select(self, library_root, profile_id, context, as_of):
        from reimbursement_standards import standards_select
        return standards_select(self, library_root, profile_id, context, as_of)

    def batch_plan(self, data, export_root=None):
        from assistant_guidance import batch_plan
        return batch_plan(self, data, export_root)

    def prepare_batch(self, library_root, profile_id, context, as_of, data, export_root=None):
        from assistant_guidance import prepare_batch
        return prepare_batch(self, library_root, profile_id, context, as_of, data, export_root)

    def prepare_batch_file(self, library_root, profile_id, context, as_of, input_path, output_path=None, export_root=None):
        from assistant_guidance import prepare_batch_file
        return prepare_batch_file(self, library_root, profile_id, context, as_of, input_path, output_path, export_root)

    def material_intake(self, case_id, request, request_id, execute=False):
        from material_intake_service import validate_request
        self.resolve(case_id)
        business = validate_request(self, case_id, request)
        result = self.enqueue(case_id, 'material_intake', {'request': business}, request_id, execute)
        result.update(system_write=False, filesystem_action_requested=execute,
                      next='reimbursement_result读取同一材料任务；这是本机材料登记，不是财务建单')
        return result

    def ocr_status(self):
        from ocr_runtime import status
        return status(self.root)

    def material_ocr(self, case_id, request, request_id, execute=False):
        result = self.enqueue(case_id, 'material_ocr', {'action': 'run', 'request': request}, request_id, execute)
        return {**result, 'system_write': False, 'next': 'result读取OCR计划、原运行或文字证据；不转财务ack，机器文字须复核'}

    def material_ocr_resume(self, case_id, ocr_job_id, request_id):
        result = self.enqueue(case_id, 'material_ocr', {'action': 'resume', 'ocr_job_id': ocr_job_id}, request_id, True)
        return {**result, 'system_write': False, 'next': 'result读取原OCR续办状态；不重复提交计算或进行财务确认'}

    def pdf_export(self, case_id, export_plan, export_root, tab_id, expected_userid, request_id, silent=True, execute=False):
        parameters = {'export_plan':export_plan,'export_root':export_root,'tab_id':tab_id,
                      'expected_userid':expected_userid,'silent':silent}
        return self.enqueue(case_id, 'pdf_export', parameters, request_id, execute)

    def pdf_export_inspect(self, case_id, job_id):
        from finance_pdf_export import inspect_export, encoded, digest
        from reimbursement_batch_plan import safe_filename
        from reimbursement_standards import safe_path, iso_date
        folder = self.job_folder(case_id, job_id)
        request = read_json(folder/'request.json')
        if request.get('operation') != 'pdf_export' or request.get('execute') is not True:
            raise APIError('wrong_source_job', '只读检查须引用本案真实PDF导出任务，不接受其他案件/操作目录')
        status = self.result(case_id, job_id)
        if status['status'] not in ['completed','failed','unknown']:
            raise APIError('job_unfinished', '原PDF任务尚在运行，继续result，不启动第二次导出')
        artifacts = status.get('artifacts', [])
        registered_intent = False
        for name in ['intent.json','export_receipt.json','failure.json']:
            matching = [row for row in artifacts if row.get('name') == name]
            if name == 'intent.json':
                registered_intent = bool(matching)
            for row in matching:
                checked = self.artifact(case_id, job_id, row['artifact_id'])
                if name == 'export_receipt.json':
                    receipt = read_json(checked)
                    pdf = Path(receipt.get('pdf','')).resolve()
                    root = Path(request['parameters']['export_root']).resolve()
                    if not pdf.is_relative_to(root) or pdf.suffix.lower() != '.pdf':
                        raise APIError('path_escape', '导出回执中的PDF不属于原任务导出根')
        parameters = request['parameters']
        original = canonical_request(request['operation'], parameters, request['execute'])
        if request_fingerprint(original) != request.get('fingerprint'):
            raise APIError('request_changed', '原导出请求指纹不符，保留现场，不重放')
        plan = parameters['export_plan']
        root = Path(parameters['export_root']).resolve()
        if not any(root.is_relative_to(x) for x in self.material_roots) or plan.get('case_id') != case_id:
            raise APIError('material_root', '原导出目的地已不在当前授权范围或案件不符')
        separator=plan.get('filename_separator','_')
        if separator not in ('_',' '):raise APIError('export_separator','原导出文件名分隔无效')
        relative = iso_date(plan['business_date']).isoformat()+'/'+safe_filename(str(plan['sequence'])+separator+plan['summary'])+'.pdf'
        destination = safe_path(root, relative)
        binding = {'case_id':case_id,'serial':plan['serial'],'tab_id':parameters['tab_id'],
                   'expected_userid':parameters['expected_userid'],'plan_sha256':digest(encoded(plan)),
                   'destination':str(destination),'job_status':status['status']}
        inspected = inspect_export(folder/'pdf_export', expected_binding=binding)
        inspected['registration_status'] = 'worker_registered' if registered_intent else 'worker_incomplete'
        if not registered_intent:
            inspected.update(ok=False, status='blocked',
                             note='原worker未完成回执登记；仅提供核对后的原任务证据，不自动确认执行完成')
        return inspected

    def ledger_query(self, case_id, table='expenses', person=None, invoice_number=None, record_id=None, limit=200):
        """查询同案已登记且SHA匹配的总账快照。"""
        from reimbursement_ledger import query
        with self._ledger_snapshot(case_id) as frozen:
            return query(frozen, case_id=case_id, table=table, person=person,
                         invoice_number=invoice_number, record_id=record_id, limit=limit)

    def ledger_trace(self, case_id, person=None, expense_id=None, invoice_number=None,
                     document_id=None, page=None, limit=200):
        """按单个锚点追溯原有多对多关联；不因共享材料反推费用相同。"""
        from reimbursement_ledger_trace import trace
        with self._ledger_snapshot(case_id) as frozen:
            return trace(frozen, case_id=case_id, person=person, expense_id=expense_id,
                         invoice_number=invoice_number, document_id=document_id, page=page, limit=limit)

    @contextmanager
    def _ledger_snapshot(self, case_id):
        """两个只读入口共用已验证字节的私有副本，关闭后自动清理。"""
        _, paths = self.resolve(case_id)
        registration = read_json(paths['case']).get('material_context', {}).get('reimbursement_ledger', {})
        value = registration.get('database_path')
        digest = registration.get('database_sha256')
        if not isinstance(value, str) or not Path(value).is_absolute() or not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
            raise APIError('ledger_unregistered', '案件尚未登记总账database_path和database_sha256')
        target = Path(value).resolve()
        if not any(target.is_relative_to(root) for root in self.material_roots) or target.suffix != '.sqlite':
            raise APIError('ledger_path', '总账路径须位于声明的材料根且为SQLite快照')
        if not target.is_file() or target.stat().st_size > 256*1024*1024:
            raise APIError('ledger_missing', '总账快照缺失或超过256MiB查询边界')
        with target.open('rb') as stream:
            frozen_bytes = stream.read(256*1024*1024 + 1)
        if len(frozen_bytes) > 256*1024*1024:
            raise APIError('ledger_missing', '总账快照超过256MiB查询边界')
        if hashlib.sha256(frozen_bytes).hexdigest() != digest:
            raise APIError('ledger_changed', '总账SHA与登记不符；先核验新快照，不读漂移数据')
        # Query only the bytes just verified. A synced source may be replaced
        # after hashing; reopening it would break the registered SHA contract.
        # Close the temporary file before SQLite opens it, including on Windows.
        with tempfile.TemporaryDirectory(prefix='reimbursement-ledger-query-') as directory:
            frozen = Path(directory)/'ledger.sqlite'
            with frozen.open('xb') as stream:
                private_path(frozen)
                stream.write(frozen_bytes)
            yield frozen

    def read_taxi_itineraries(self, paths):
        from taxi_itinerary import read_itinerary
        if not isinstance(paths,list) or not 1<=len(paths)<=20:raise APIError('invalid_material_path','paths须为1至20份绝对材料路径')
        reports=[read_itinerary(self.material_path(p)) for p in paths]
        return {'ok':all(r['ok'] for r in reports),'reports':reports,'system_write':False}

    def audit_invoice_fields(self, paths, profile='none'):
        from invoice_audit import read_invoice, USTC
        if not isinstance(paths,list) or not 1<=len(paths)<=20:raise APIError('invalid_material_path','paths须为1至20份绝对材料路径')
        if profile not in ['none','ustc']:raise APIError('invalid_profile','profile使用none或ustc；基金抬头需另核对实际主体')
        reports=[read_invoice(self.material_path(p),USTC['title'] if profile=='ustc' else None,USTC['tax_id'] if profile=='ustc' else None) for p in paths]
        return {'ok':True,'reports':reports,'profile':profile,'authenticity_verified':False,'system_write':False}

    def record_invoice_verification(self, case_id, invoice_number, receipt_path, official_url, result, note):
        from invoice_audit import verification_receipt
        _, paths=self.resolve(case_id)
        report=verification_receipt(invoice_number,self.material_path(receipt_path),official_url,result,note)
        target=paths['case'].parent/'verification_receipts'/f'{invoice_number}_{report["receipt_sha256"][:16]}.json'
        with self.mutex(case_id):
            if target.exists() and read_json(target)!=report:raise APIError('receipt_conflict','同票同回执已有不同登记；保留旧登记，核对后另存新回执')
            if not target.exists():atomic_json(target,report)
        return {'ok':True,'record_path':str(target),**report}

    def invoice_verification(self, case_id, action, tab_id, request_id,
                             invoice_numbers=None, view=None, job_id=None, artifact_id=None, operation_key=None,
                             grid=None, expected_userid=None):
        """复用任务机制静默查询本账号发票；不接受前台、脚本或任意回执路径。"""
        parameters = {'action': action, 'tab_id': tab_id}
        for key, value in [('invoice_numbers', invoice_numbers), ('view', view),
                           ('job_id', job_id), ('artifact_id', artifact_id), ('operation_key', operation_key),
                           ('grid', grid), ('expected_userid', expected_userid)]:
            if value is not None:
                parameters[key] = value
        return {**self.enqueue(case_id, 'invoice_verification', parameters, request_id),
                'system_write': False, 'scope': '当前登录人可见视图；query-used仅本账号已使用池，不证明全校已报或红冲'}

    def invoice_sources(self, case_id, action, request_id, invoice_numbers=None):
        """仅从本案声明候选生成来源清单/购方字段审核，不登记已选或金额。"""
        params = {'action': action}
        if invoice_numbers is not None: params['invoice_numbers'] = invoice_numbers
        return {**self.enqueue(case_id, 'invoice_sources', params, request_id),
                'system_write': False, 'selected': False}

    def invoice_retry(self, case_id, action, tab_id, request_id, execute=False,
                      expected_userid=None, grid=None, target_guids=None, target_filecodes=None,
                      planned_action=None, targets=None, source_job_id=None, source_artifact_id=None,
                      pool_job_id=None, pool_artifact_id=None, recognition_job_id=None, recognition_artifact_id=None,
                      inspection_job_id=None, inspection_artifact_id=None, plan_job_id=None, plan_artifact_id=None,
                      receipt_job_id=None, receipt_artifact_id=None, job_id=None, artifact_id=None):
        """异常处理固定目录；先读取原生状态，稳定任务号留证，不接受路径或脚本。"""
        params = {'action': action, 'tab_id': tab_id}
        values = locals().copy()
        for key in ['expected_userid', 'grid', 'target_guids', 'target_filecodes', 'planned_action', 'targets',
                    'source_job_id', 'source_artifact_id', 'pool_job_id', 'pool_artifact_id',
                    'recognition_job_id', 'recognition_artifact_id', 'inspection_job_id', 'inspection_artifact_id',
                    'plan_job_id', 'plan_artifact_id', 'receipt_job_id', 'receipt_artifact_id', 'job_id', 'artifact_id']:
            if values[key] is not None: params[key] = values[key]
        return self.enqueue(case_id, 'invoice_retry', params, request_id, execute)

    def invoice_edit(self, case_id, action, tab_id, request_id, invoice_number, expected_userid, execute=False,
                     job_id=None, artifact_id=None, inspection_job_id=None, inspection_artifact_id=None):
        params = dict(action=action, tab_id=tab_id, invoice_number=invoice_number, expected_userid=expected_userid)
        if job_id is not None: params['job_id'] = job_id
        if artifact_id is not None: params['artifact_id'] = artifact_id
        if inspection_job_id is not None: params['inspection_job_id'] = inspection_job_id
        if inspection_artifact_id is not None: params['inspection_artifact_id'] = inspection_artifact_id
        return self.enqueue(case_id, 'invoice_edit', params, request_id, execute)

    def expense_amount(self, case_id, action, group, subject, expected_userid, tab_id, request_id, execute=False,
                       source_job_id=None, source_artifact_id=None, job_id=None, artifact_id=None,claims_path=None):
        params = dict(action=action, group=group, subject=subject, expected_userid=expected_userid, tab_id=tab_id)
        for key, value in [('source_job_id', source_job_id), ('source_artifact_id', source_artifact_id), ('job_id', job_id), ('artifact_id', artifact_id),('claims_path',claims_path)]:
            if value is not None: params[key] = value
        return self.enqueue(case_id, 'expense_amount', params, request_id, execute)

    def _expense_amount_command(self, case_id, params, folder, output, execute, request_id):
        from finance_expense_amount import base_request, bind_source, validate_source, open_script, return_script, same_target,prepare_reduction,save_script
        action = params.get('action')
        if action not in ['open', 'inspect', 'return', 'recover','save'] or execute and action not in ['open', 'return','save']:
            raise APIError('expense_amount_action', '仅支持窗口open/inspect/return/recover及有完整逐票准备的save')
        if action!='save' and params.get('claims_path')is not None:raise APIError('expense_amount_action','claims_path仅用于save')
        _, paths = self.resolve(case_id)
        form = read_json(paths['form'])
        try: request = base_request(form, params.get('group'), params.get('subject'), params.get('expected_userid'), params.get('tab_id'))
        except ValueError as error: raise APIError('expense_amount_identity', str(error)) from error
        extra = []
        if action in ['open', 'return','save']:
            if params.get('job_id') is not None or params.get('artifact_id') is not None:
                raise APIError('expense_amount_source', 'open/return仅接受source_job_id/source_artifact_id独立检查')
            source, data, previous = self._typed_case_artifact(case_id, params.get('source_job_id'), params.get('source_artifact_id'), 'expense_amount', ['inspect'])
            if previous['parameters'].get('tab_id') != request['tab_id']:
                raise APIError('expense_amount_source', '来源必须为本标签独立检查')
            try:
                if action == 'open':
                    if form.get('review', {}).get('workflow_started'): raise ValueError('已有流程，不能打开草稿金额修改窗口')
                    provenance = {'path': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'job_id': params['source_job_id'], 'artifact_id': params['source_artifact_id']}
                    request = bind_source(request, data, provenance)
                    open_script(request, request_id)
                elif action=='return':
                    frozen = read_json(self.job_folder(case_id, params['source_job_id'])/'expense_amount_request.json')
                    if not same_target(request, data.get('result', data)): raise ValueError('返回检查的案件/账号/单号/分组不符')
                    request = validate_source(frozen)
                    return_script(request, data, request_id)
                    extra = ['--readback', str(source)]
                else:
                    frozen=read_json(self.job_folder(case_id,params['source_job_id'])/'expense_amount_request.json')
                    if not same_target(request,data.get('result',data)):raise ValueError('减报来源目标不同')
                    request=validate_source(frozen)
                    claims_file=Path(str(params.get('claims_path','')))
                    if (not claims_file.is_absolute()or not claims_file.is_file()or claims_file.suffix.lower()!='.json'
                        or not any(claims_file.resolve().is_relative_to(root)for root in self.material_roots)
                        or claims_file.stat().st_size>1024*1024):
                        raise ValueError('逐票申请清单须为授权材料根内的明确JSON文件')
                    claims=read_json(claims_file)
                    preparation=prepare_reduction(request,data,{'path':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'job_id':params['source_job_id'],'artifact_id':params['source_artifact_id']},claims)
                    save_script(request,preparation,request_id)
                    prepared_path=folder/'expense_amount_reduction.json';atomic_json(prepared_path,preparation)
                    extra=['--preparation',str(prepared_path)]
            except (ValueError, TypeError, KeyError, OSError) as error:
                raise APIError('expense_amount_source', str(error)) from error
        else:
            if params.get('source_job_id') is not None or params.get('source_artifact_id') is not None:
                raise APIError('expense_amount_source', '检查/恢复只接受原open/return的job_id/artifact_id')
            if action == 'recover' or params.get('job_id') is not None or params.get('artifact_id') is not None:
                source, data, previous = self._typed_case_artifact(case_id, params.get('job_id'), params.get('artifact_id'), 'expense_amount', ['open', 'return'])
                original = read_json(self.job_folder(case_id, params['job_id'])/'expense_amount_request.json')
                if any(original.get(k) != request.get(k) for k in ['case_id', 'serial', 'group', 'subject', 'expected_userid', 'tab_id']):
                    raise APIError('expense_amount_source', '须引用同案同号同组同标签的原窗口意图')
                try: request = validate_source(original)
                except (ValueError, OSError) as error: raise APIError('expense_amount_source', str(error)) from error
                if action == 'recover':
                    marker = data.get('result_marker')
                    if data.get('tab_id') != request['tab_id'] or not re.fullmatch(r'reimbursement-result-[a-f0-9]{32}', str(marker)):
                        raise APIError('expense_amount_recovery', '原任务须保留同标签的原生marker；不重放')
                    extra = ['--original-marker', marker]
        request_path = folder/'expense_amount_request.json'
        atomic_json(request_path, request)
        bundle_path, _ = self.resolve(case_id)
        return [self.python, str(PROJECT/'reimburse.py'), '--bundle', str(bundle_path), 'expense-amount', action,
                '--input', str(request_path), '--request-id', request_id, '--output', str(output), *extra] + (['--execute'] if execute else [])

    def _invoice_edit_command(self, case_id, params, folder, output, execute, request_id):
        from finance_invoice_edit import validate, SCHEMA, MEDICAL_SCHEMA
        action = params.get('action')
        if action not in ['run','open','medical-run','medical-open','medical-save','medical-close','medical-return','journey','airport-open','airport-query','airport-select','save','resume-save','close','inspect','recover'] or execute != (action in ['run','open','medical-run','medical-open','medical-save','medical-close','medical-return','journey','airport-open','airport-query','airport-select','save','resume-save','close']):
            raise APIError('invoice_edit_action', '仅支持已适配的行程维护动作及inspect/recover只读')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', request_id):
            raise APIError('invoice_edit_request', '行程修改须稳定ASCII请求号')
        _, paths = self.resolve(case_id)
        registered = read_json(paths['case']).get('material_context', {}).get('invoice_edit_manifest')
        if not isinstance(registered, dict):
            raise APIError('invoice_edit_source', '先在本案登记出行人修改清单及SHA')
        path = Path(str(registered.get('path', '')))
        if not path.is_absolute() or not path.is_file() or path.suffix != '.json' or not any(path.resolve().is_relative_to(root) for root in self.material_roots):
            raise APIError('invoice_edit_source', '修改清单须在本案材料根内的已登记JSON')
        if hashlib.sha256(path.read_bytes()).hexdigest() != registered.get('sha256'):
            raise APIError('invoice_edit_source', '登记的修改清单SHA改变')
        manifest = read_json(path)
        if manifest.get('case_id') != case_id or manifest.get('expected_userid') != params.get('expected_userid'):
            raise APIError('invoice_edit_source', '修改清单须同案同账号')
        entries = [v for v in manifest.get('entries', []) if v.get('invoice_number') == params.get('invoice_number')]
        if len(entries) != 1:
            raise APIError('invoice_edit_source', '修改清单没有唯一目标')
        request = {**entries[0], 'schema':entries[0].get('schema',SCHEMA), 'case_id':case_id, 'expected_userid':params.get('expected_userid'), 'tab_id':params.get('tab_id')}
        try:
            self.material_path(request.get('source_path'))
            for evidence in request.get('evidence', []): self.material_path(evidence.get('path'))
            validate(request)
            if request['schema']==MEDICAL_SCHEMA and action not in ['medical-run','medical-open','medical-save','medical-close','medical-return','inspect','recover']:raise ValueError('医疗票当前只支持原生窗口检查')
            if action.startswith('medical-') and request['schema']!=MEDICAL_SCHEMA:raise ValueError('医疗入口须登记医疗原件')
        except (ValueError, KeyError, TypeError, OSError) as error:
            raise APIError('invoice_edit_source', str(error)) from error
        if action in ['medical-save','medical-run']:
            confirmed=read_json(paths['case']).get('material_context',{}).get('medical_insurance_type_confirmation',{})
            if confirmed.get('confirmed') is not True or confirmed.get('value')!='自费' or not confirmed.get('user_text') or {'invoice_code':request['invoice_code'],'invoice_number':request['invoice_number']} not in confirmed.get('invoices',[]):
                raise APIError('invoice_medical_confirmation','本案尚无此票医保类型的明确确认；不能从现金支付自行猜填参保险种')
        extra = []
        if action == 'recover':
            prior = read_json(self.job_folder(case_id, params.get('job_id'))/'request.json')
            if prior.get('operation') != 'invoice_edit' or prior.get('case_id') != case_id or prior.get('parameters', {}).get('tab_id') != request['tab_id'] or prior.get('parameters', {}).get('invoice_number') != request['invoice_number']:
                raise APIError('invoice_edit_source', '恢复必须引用同案同票同标签原任务')
            original = read_json(self.job_folder(case_id, params.get('job_id'))/'edit_request.json')
            if original.get('case_id') != case_id or original.get('tab_id') != request['tab_id'] or original.get('invoice_number') != request['invoice_number'] or original.get('expected_userid') != request['expected_userid']:
                raise APIError('invoice_edit_source', '原始请求身份或票号不符')
            try: request = validate(original)
            except (ValueError, OSError) as error: raise APIError('invoice_edit_source', str(error)) from error
            request_id = prior['request_id']
            if prior.get('parameters',{}).get('action')=='medical-run':
                from finance_invoice_edit import medical_workflow_recovery_source
                native=medical_workflow_recovery_source(self.job_folder(case_id,params.get('job_id')))
            else:
                native = read_json(self.job_folder(case_id, params.get('job_id'))/'native.json')
            if native.get('tab_id') != request['tab_id'] or not re.fullmatch(r'reimbursement-result-[a-f0-9]{32}',str(native.get('result_marker',''))):
                raise APIError('invoice_edit_source', '原任务没有可核对的结果标记')
            extra = ['--original-marker', native['result_marker']]
        elif params.get('job_id') is not None or params.get('artifact_id') is not None:
            raise APIError('invoice_edit_source', '仅recover接受原任务引用')
        if action in ['save','resume-save','airport-select','medical-save']:
            inspected, data, previous = self._typed_case_artifact(case_id, params.get('inspection_job_id'), params.get('inspection_artifact_id'), 'invoice_edit', ['recover'] if action=='resume-save' else ['inspect'])
            if previous.get('parameters', {}).get('tab_id') != request['tab_id']:
                raise APIError('invoice_edit_source', '保存只接受本标签的独立表单回执')
            from finance_invoice_edit import save_script, resume_save_script, airport_select_script, medical_save_script
            try: {'medical-save':medical_save_script,'save':save_script,'resume-save':resume_save_script,'airport-select':airport_select_script}[action](request, data, request_id)
            except (ValueError, KeyError, TypeError) as error: raise APIError('invoice_edit_source', str(error)) from error
            extra = ['--inspection', str(inspected)]
        elif params.get('inspection_job_id') is not None or params.get('inspection_artifact_id') is not None:
            raise APIError('invoice_edit_source', '仅save/airport-select接受独立表单回执')
        request_path = folder/'edit_request.json'
        atomic_json(request_path, request)
        return [self.python, str(PROJECT/'tools/finance_invoice_edit.py'), action, '--request', str(request_path),
                '--request-id', request_id, '--output', str(output)] + extra + (['--execute'] if execute else [])

    def _invoice_retry_command(self, case_id, params, folder, output, execute, request_id):
        from finance_invoice_retry import validate_request
        action, tab = params.get('action'), params.get('tab_id')
        reads = ['inspect', 'preview', 'readback', 'recover']
        writes = ['refresh', 'reidentify', 'cleanup', 'confirm']
        if action not in reads + writes:
            raise APIError('invoice_retry_action', '异常处理只支持inspect/preview/refresh/reidentify/cleanup/confirm/readback/recover')
        if execute != (action in writes):
            raise APIError('invoice_retry_execute', '刷新/重识别/清理/确认须execute=true；检查/预览/回读/恢复须false')
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', request_id):
            raise APIError('invoice_retry_request_id', '异常处理使用1至80位ASCII字母数字/下划线/短横线稳定请求号')
        if not isinstance(tab, str) or not re.fullmatch(r'[0-9]+', tab):
            raise APIError('invoice_retry_request', '异常处理须明确当前数字tab_id字符串')
        plan_fields = {'plan_job_id', 'plan_artifact_id'}
        capture_fields = {'pool_job_id', 'pool_artifact_id', 'recognition_job_id', 'recognition_artifact_id'}
        inspect_fields = {'inspection_job_id', 'inspection_artifact_id'}
        fields = {
            'inspect': {'expected_userid', 'grid', 'target_guids', 'target_filecodes'},
            'preview': {'expected_userid', 'grid', 'planned_action', 'targets', 'source_job_id', 'source_artifact_id'} | capture_fields | inspect_fields,
            'refresh': plan_fields, 'reidentify': plan_fields, 'cleanup': plan_fields,
            'confirm': plan_fields | inspect_fields | {'receipt_job_id', 'receipt_artifact_id'},
            'readback': plan_fields | capture_fields | {'receipt_job_id', 'receipt_artifact_id'}, 'recover': plan_fields | {'job_id', 'artifact_id'},
        }[action]
        if set(params) - {'action', 'tab_id'} - fields:
            raise APIError('invoice_retry_arguments', '参数与固定异常处理动作不符')
        cmd = [self.python, str(PROJECT/'tools/finance_invoice_retry.py'), action, '--output', str(output)]

        def reference(prefix, operation, actions):
            path, value, previous = self._typed_case_artifact(case_id, params.get(prefix+'job_id'),
                params.get(prefix+'artifact_id'), operation, actions)
            if operation != 'invoice_sources' and previous['parameters'].get('tab_id') != tab:
                raise APIError('invoice_retry_source', '只接受同案同标签正式回执')
            if value.get('tab_id') is not None and value['tab_id'] != tab:
                raise APIError('invoice_retry_source', '原生回执标签与当前目标不同')
            return path, value, previous

        def captures(who):
            from finance_invoice_reconcile import grid_scope
            out = []
            for prefix, grid in [('pool_', '53'), ('recognition_', '113')]:
                path, value, previous = reference(prefix, 'invoice_verification', ['full-grid', 'recover'])
                try:
                    _, _, scope = grid_scope(value, grid, who)
                    if not scope['complete']:
                        raise ValueError('完整表范围不足：'+','.join(scope['incomplete_reasons']))
                except ValueError as error:
                    raise APIError('invoice_retry_capture', str(error)) from error
                out.append((path, value, previous))
            return out

        if action in ['inspect', 'preview']:
            who = params.get('expected_userid')
            if not isinstance(who, str) or not 1 <= len(who) <= 128 or who != who.strip():
                raise APIError('invoice_retry_identity', '须明确当前账号expected_userid，不接受空白或非字符串身份')
            request = {'case_id': case_id, 'tab_id': tab, 'expected_userid': who, 'grid': params.get('grid')}
            if request['grid'] not in ['53', '113']:
                raise APIError('invoice_retry_request', '异常处理grid须为53或113字符串')
            for key in ['target_guids', 'target_filecodes'] if action == 'inspect' else ['targets']:
                if key in params: request[key] = params[key]
            if action == 'preview': request['action'] = params.get('planned_action')
            try:
                request = validate_request(request)
                for key in ['target_guids', 'target_filecodes']:
                    values = request.get(key, [])
                    if len({x.lower() for x in values}) != len(values):
                        raise ValueError('目标GUID/filecode不能重复')
                if action == 'preview':
                    if request['action'] not in ['refresh', 'reidentify', 'cleanup']:
                        raise ValueError('planned_action只支持refresh/reidentify/cleanup')
                    targets = request.setdefault('targets', [])
                    if not isinstance(targets, list) or len(targets) > 20:
                        raise ValueError('targets须为最多20个对象')
                    native_key = 'native_guid' if request['grid'] == '53' else 'filecode'
                    for target in targets:
                        if not isinstance(target, dict) or set(target) not in ({'invoice_number', 'source_sha256', native_key}, {'invoice_number', 'invoice_code', 'source_sha256', native_key}):
                            raise ValueError('每个target只接受完整票号、源SHA和对应原生GUID/filecode')
                        from invoice_identity import source_identity
                        source_identity({'invoice_number_candidates': [target['invoice_number']], 'invoice_code': target.get('invoice_code')}, True)
                        if len(target['invoice_number']) == 20 and 'invoice_code' in target:
                            raise ValueError('20位税票不接受医疗代码域')
                        for key, pattern in [('source_sha256', r'[a-f0-9]{64}'), (native_key, r'[a-fA-F0-9]{32}')]:
                            if not isinstance(target[key], str) or not re.fullmatch(pattern, target[key]):
                                raise ValueError('target票号、SHA或原生标识格式不符')
            except ValueError as error:
                raise APIError('invoice_retry_request', str(error)) from error
            request_path = folder/'retry_request.json'
            if action == 'inspect':
                atomic_json(request_path, request)
                return cmd + ['--request', str(request_path), '--request-id', request_id]
            if 'source_job_id' in params or 'source_artifact_id' in params:
                source, manifest, _ = reference('source_', 'invoice_sources', ['manifest', 'audit'])
                if manifest.get('schema') == 'ustc-case-invoice-audit-v1': manifest = manifest.get('source_manifest')
                if not isinstance(manifest, dict) or manifest.get('schema') != 'ustc-case-invoice-sources-v1' or manifest.get('case_id') != case_id:
                    raise APIError('invoice_retry_source', '预览只接受同案已声明原件生成的正式来源manifest')
                provenance = {'kind': 'invoice_sources_artifact', 'job_id': params['source_job_id'], 'artifact_id': params['source_artifact_id']}
            else:
                _, paths = self.resolve(case_id)
                registered = read_json(paths['case']).get('material_context', {}).get('verification_manifest')
                if not isinstance(registered, dict) or not isinstance(registered.get('path'), str) or not Path(registered['path']).is_absolute() or not isinstance(registered.get('sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', registered['sha256']):
                    raise APIError('invoice_retry_source', '需source任务引用，或本案已登记verification_manifest绝对路径及SHA；不接受临时路径')
                source = Path(registered['path']).resolve()
                if not source.is_file() or source.suffix.lower() != '.json' or not any(source.is_relative_to(root) for root in self.material_roots):
                    raise APIError('invoice_retry_source', '已登记验证manifest须在声明材料根内且为JSON文件')
                if hashlib.sha256(source.read_bytes()).hexdigest() != registered['sha256']:
                    raise APIError('invoice_retry_source', '已登记验证manifest的实际SHA发生变化')
                manifest = read_json(source)
                if not isinstance(manifest, dict) or manifest.get('case_id') != case_id or manifest.get('expected_userid') != who or manifest.get('verification_only') is not True:
                    raise APIError('invoice_retry_source', '已登记验证manifest须同案、同预期账号且明确verification_only')
                provenance = {'kind': 'case_registered_verification_manifest', 'case_id': case_id,
                              'case_revision': self.revision(case_id), 'selected': False}
            if manifest.get('expected_userid') not in (None, who):
                raise APIError('invoice_retry_identity', '来源manifest与预期财务身份不同')
            manifest = {**manifest, 'expected_userid': who,
                        'formal_source_artifact': {**provenance, 'path': str(source),
                            'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}}
            for item in manifest.get('original_files', []): self.material_path(item.get('path'))
            (pool_path, pool, _), (recognition_path, recognition, _) = captures(who)
            inspection_path, inspection, _ = reference('inspection_', 'invoice_retry', ['inspect'])
            try:
                from finance_invoice_retry import preview
                preview(request, manifest, pool, recognition, inspection)  # 纯校验，不执行或覆盖最终计划。
            except (ValueError, KeyError, TypeError) as error:
                raise APIError('invoice_retry_preview', str(error)) from error
            source_path = folder/'retry_source_manifest.json'
            atomic_json(request_path, request)
            atomic_json(source_path, manifest)
            return cmd + ['--request', str(request_path), '--manifest', str(source_path),
                          '--pool', str(pool_path), '--recognition', str(recognition_path), '--inspection', str(inspection_path)]

        plan_path, plan, plan_request = reference('plan_', 'invoice_retry', ['preview'])
        if plan.get('case_id') != case_id or plan.get('request', {}).get('tab_id') != tab:
            raise APIError('invoice_retry_plan', '操作只接受同案同标签正式预览计划')
        try:
            from finance_invoice_retry import validate_plan, verify_plan_evidence
            validate_plan(plan, action if action in ['refresh', 'reidentify', 'cleanup'] else None)
            verify_plan_evidence(plan)
            frozen_source = read_json(plan['inputs_evidence']['manifest']['path'])
            provenance = frozen_source.get('formal_source_artifact', {})
            if not provenance.get('path') or hashlib.sha256(Path(provenance['path']).read_bytes()).hexdigest() != provenance.get('sha256'):
                raise ValueError('正式来源manifest原回执SHA变化或缺少来源绑定')
        except (ValueError, KeyError, TypeError, OSError) as error:
            raise APIError('invoice_retry_plan', str(error)) from error
        plan_sha = hashlib.sha256(plan_path.read_bytes()).hexdigest()
        cmd += ['--plan', str(plan_path), '--plan-sha256', plan_sha]
        if action in ['refresh', 'reidentify', 'cleanup']:
            try:
                from finance_invoice_retry import action_script
                action_script(plan, request_id)  # 只验证脚本能由固定合同生成，不执行浏览器。
            except (ValueError, KeyError, TypeError) as error:
                raise APIError('invoice_retry_plan', str(error)) from error
            return cmd + ['--request-id', request_id, '--execute']
        if action == 'readback':
            receipt_action = 'confirm' if plan['action'] in ['cleanup', 'reidentify'] else plan['action']
            receipt_path, receipt, prior = reference('receipt_', 'invoice_retry', [receipt_action])
            if prior.get('execute') is not True or any(prior['parameters'].get(key) != params.get(key) for key in plan_fields):
                raise APIError('invoice_retry_readback', '回读须引用同案同一计划已执行的正式动作回执；清理和重识别须完成confirm')
            captures_now = captures(plan['request']['expected_userid'])
            for label, (path, _, _) in zip(['pool', 'recognition'], captures_now):
                if str(path.resolve()) == str(Path(plan['inputs_evidence'][label]['path']).resolve()):
                    raise APIError('invoice_retry_readback', '回读须引用独立新完整查询，不能复用计划前基线')
                if hashlib.sha256(path.read_bytes()).hexdigest() == plan['inputs_evidence'][label]['sha256']:
                    raise APIError('invoice_retry_readback', '复制的旧基线也不能冒充独立回读')
                cmd += ['--'+label, str(path)]
            try:
                from finance_invoice_retry import readback
                readback(plan, captures_now[0][1], captures_now[1][1], receipt)
            except (ValueError, KeyError, TypeError) as error:
                raise APIError('invoice_retry_readback', str(error)) from error
            return cmd + ['--receipt', str(receipt_path), '--receipt-sha256', hashlib.sha256(receipt_path.read_bytes()).hexdigest()]
        if action == 'confirm':
            if plan['action'] not in ['cleanup', 'reidentify']:
                raise APIError('invoice_retry_confirm', '确认只适用于同一cleanup或reidentify计划')
            receipt_path, receipt, prior = reference('receipt_', 'invoice_retry', [plan['action']])
            inspection_path, inspection, _ = reference('inspection_', 'invoice_retry', ['inspect'])
            if prior.get('execute') is not True or any(prior['parameters'].get(key) != params.get(key) for key in plan_fields):
                raise APIError('invoice_retry_confirm', '确认须引用本案同一计划已发起的清理或重识别正式回执')
            try:
                from finance_invoice_retry import validate_confirm_evidence
                validate_confirm_evidence(plan, receipt, inspection)
            except (ImportError, ValueError, KeyError, TypeError) as error:
                raise APIError('invoice_retry_confirm', str(error)) from error
            return cmd + ['--receipt', str(receipt_path), '--receipt-sha256', hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
                          '--inspection', str(inspection_path), '--request-id', request_id, '--execute']
        if params.get('artifact_id') is not None:
            _, receipt, prior = reference('', 'invoice_retry', ['refresh', 'reidentify', 'cleanup', 'confirm'])
        else:
            # 超时可能早于任何回执落盘。原job请求本身绑定意图；此分支仅查原marker。
            original_folder = self.job_folder(case_id, params.get('job_id'))
            original_status = self.result(case_id, params['job_id'])
            if original_status.get('status') not in ['unknown', 'failed']:
                raise APIError('invoice_retry_recovery', '无回执恢复只适用于已结束且失败或结果未知的原动作job')
            prior = read_json(original_folder/'request.json')
            old = prior.get('parameters', {})
            if prior.get('case_id') != case_id or prior.get('operation') != 'invoice_retry' or old.get('action') not in ['refresh', 'reidentify', 'cleanup', 'confirm'] or old.get('tab_id') != tab:
                raise APIError('invoice_retry_recovery', '无回执恢复须为同案同标签的原正式动作job')
        if prior.get('execute') is not True or any(prior['parameters'].get(key) != params.get(key) for key in plan_fields):
            raise APIError('invoice_retry_recovery', '恢复只引用本案同一计划的原动作job，不重发或换请求号')
        original_id = prior.get('request_id')
        if not isinstance(original_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', original_id):
            raise APIError('invoice_retry_recovery', '原正式动作缺少可核对的稳定请求号')
        return cmd + ['--request-id', original_id]

    def invoice_selection(self, case_id, action, tab_id, request_id, execute=False,
                          source_job_id=None, source_artifact_id=None, audit_refs=None,
                          selectors=None, mode=None, grid=None, serial=None,
                          expected_userid=None, expected_before_guids=None,
                          snapshot_job_id=None, snapshot_artifact_id=None,
                          preview_job_id=None, preview_artifact_id=None,
                          job_id=None, artifact_id=None):
        """独立选票：默认预览，同案来源/审核/预览SHA；执行只变更勾选。"""
        params = {'action': action, 'tab_id': tab_id}
        values = locals().copy()
        for key in ['source_job_id', 'source_artifact_id', 'audit_refs', 'selectors', 'mode', 'grid', 'serial',
                    'expected_userid', 'expected_before_guids', 'snapshot_job_id', 'snapshot_artifact_id',
                    'preview_job_id', 'preview_artifact_id', 'job_id', 'artifact_id']:
            if values[key] is not None: params[key] = values[key]
        return self.enqueue(case_id, 'invoice_selection', params, request_id, execute)

    def _typed_case_artifact(self, case_id, job_id, artifact_id, operation, actions):
        if not isinstance(job_id, str) or not isinstance(artifact_id, str):
            raise APIError('invoice_source_artifact', '须引用同案result返回的job_id/artifact_id')
        path = self.artifact(case_id, job_id, artifact_id)
        request = read_json(self.job_folder(case_id, job_id)/'request.json')
        parameters = request.get('parameters') if isinstance(request, dict) else None
        if path.suffix != '.json' or not isinstance(parameters, dict) or request.get('case_id') != case_id or request.get('operation') != operation or parameters.get('action') not in actions:
            raise APIError('invoice_source_artifact', '回执须来自同案指定正式任务，不能注册任意路径或自造审核')
        data = read_json(path)
        if not isinstance(data, dict): raise APIError('invoice_source_artifact', '正式JSON回执须为对象')
        return path, data, request

    def _invoice_sources_command(self, case_id, params, output):
        action = params.get('action')
        if action not in ['manifest', 'audit']:
            raise APIError('invoice_sources_action', '来源任务只支持manifest/audit')
        numbers = params.get('invoice_numbers')
        if numbers is not None and (not isinstance(numbers, list) or not 1 <= len(numbers) <= 20 or any(not isinstance(n, str) or not re.fullmatch(r'[0-9]{20}', n) for n in numbers) or len(set(numbers)) != len(numbers)):
            raise APIError('invoice_sources_numbers', '票号须为1至20个不同的20位ASCII字符串')
        if action == 'audit' and numbers is None:
            raise APIError('invoice_sources_numbers', '审核必须指定1至20个完整票号，不默认批量OCR全部材料')
        _, paths = self.resolve(case_id)
        cmd = [self.python, str(PROJECT/'tools/case_invoice_sources.py'), action,
               '--case-file', str(paths['case']), '--case-id', case_id, '--output', str(output)]
        for root in self.material_roots: cmd += ['--material-root', str(root)]
        if numbers is not None: cmd += ['--numbers', *numbers]
        return cmd

    def _invoice_selection_command(self, case_id, params, folder, output, execute, request_id):
        from invoice_selection import validate_request
        action, tab = params.get('action'), params.get('tab_id')
        if action not in ['preview', 'apply', 'readback', 'recover']:
            raise APIError('invoice_selection_action', '选票支持preview/apply/readback/recover')
        if not isinstance(tab, str) or not re.fullmatch(r'[0-9]+', tab):
            raise APIError('tab_id_required', '选票须明确本次数字标签ID')
        fields = {'preview': {'source_job_id', 'source_artifact_id', 'audit_refs', 'selectors', 'mode', 'grid', 'serial',
                             'expected_userid', 'expected_before_guids', 'snapshot_job_id', 'snapshot_artifact_id'},
                  'apply': {'preview_job_id', 'preview_artifact_id'},
                  'readback': {'preview_job_id', 'preview_artifact_id', 'snapshot_job_id', 'snapshot_artifact_id'},
                  'recover': {'job_id', 'artifact_id'}}[action]
        if set(params)-{'action', 'tab_id'}-fields:
            raise APIError('invoice_selection_arguments', '参数与固定选票动作不符')
        if execute != (action == 'apply'):
            raise APIError('invoice_selection_execute', '只有apply要求明确execute=true；preview/readback/recover均为false')
        if action == 'recover':
            source, receipt, previous = self._typed_case_artifact(case_id, params.get('job_id'), params.get('artifact_id'), 'invoice_selection', ['preview', 'apply', 'readback'])
            body = receipt.get('result') if isinstance(receipt, dict) else None
            marker = body.get('result_marker') if isinstance(body, dict) else None
            if receipt.get('tab_id') != tab or previous['parameters'].get('tab_id') != tab or not isinstance(marker, str) or not re.fullmatch(r'reimbursement-result-[a-f0-9]{32}', marker):
                raise APIError('invoice_selection_recovery', '恢复引用原生子回执同标签原marker，不接受聚合结果或重发')
            return [self.python, str(PROJECT/'tools/finance_invoice_verification.py'), 'recover', '--receipt', str(source), '--output', str(output), '--tab-id', tab]
        cmd = [self.python, str(PROJECT/'tools/invoice_selection.py'), action, '--output', str(output)]
        if action == 'preview':
            source, manifest, _ = self._typed_case_artifact(case_id, params.get('source_job_id'), params.get('source_artifact_id'), 'invoice_sources', ['manifest', 'audit'])
            if manifest.get('schema') == 'ustc-case-invoice-audit-v1':
                manifest = manifest.get('source_manifest')
                source = folder/'selection_source_manifest.json'
                atomic_json(source, manifest)
            if not isinstance(manifest, dict) or manifest.get('schema') != 'ustc-case-invoice-sources-v1' or manifest.get('case_id') != case_id:
                raise APIError('invoice_selection_source', '只接受同案已声明候选生成的正式来源清单')
            audit_refs = params.get('audit_refs')
            if not isinstance(audit_refs, list) or not 1 <= len(audit_refs) <= 20:
                raise APIError('invoice_selection_audits', '须给1至20份同案正式audit回执引用')
            if len({json.dumps(ref, sort_keys=True) for ref in audit_refs}) != len(audit_refs):
                raise APIError('invoice_selection_audits', '审核回执引用不能重复')
            audits = []
            for ref in audit_refs:
                if not isinstance(ref, dict) or set(ref) != {'job_id', 'artifact_id'}:
                    raise APIError('invoice_selection_audits', 'audit_refs仅接受job_id/artifact_id')
                audit, data, _ = self._typed_case_artifact(case_id, ref['job_id'], ref['artifact_id'], 'invoice_sources', ['audit'])
                if data.get('schema') != 'ustc-case-invoice-audit-v1' or data.get('case_id') != case_id or data.get('profile') != 'ustc' or data.get('source_bindings_verified') is not True:
                    raise APIError('invoice_selection_audits', '审核须为同案候选原字节SHA绑定的ustc正式回执')
                audited_sources = {r['sha256']: r['path'] for r in (data.get('source_manifest') or {}).get('original_files', [])}
                if not isinstance(data.get('reports'), list) or not data['reports'] or any(not isinstance(r, dict) or audited_sources.get(r.get('sha256')) != r.get('path') for r in data['reports']):
                    raise APIError('invoice_selection_audits', '审核报告path/SHA与正式声明来源不符')
                audits.append(audit)
            selectors = params.get('selectors')
            if not isinstance(selectors, list) or len(selectors) > 20:
                raise APIError('invoice_selection_selectors', '每批最多20个条件，replace可明确空列表')
            request = {'case_id': case_id, 'tab_id': tab, 'grid': params.get('grid', '53'),
                       'mode': params.get('mode', 'append'), 'selectors': selectors}
            for key in ['serial', 'expected_userid', 'expected_before_guids']:
                if key in params: request[key] = params[key]
            if 'expected_userid' in request and (not isinstance(request['expected_userid'], str) or not 1 <= len(request['expected_userid']) <= 128):
                raise APIError('invoice_selection_selectors', 'expected_userid须为原生身份字符串')
            try: request = validate_request(request)
            except ValueError as error: raise APIError('invoice_selection_selectors', str(error)) from error
            if request['grid'] == '1669':
                _, paths = self.resolve(case_id)
                if request.get('serial') != read_json(paths['form']).get('serial'):
                    raise APIError('invoice_selection_serial', '1669须为本案已登记真实单号')
            request_path = folder/'selection_request.json';atomic_json(request_path, request)
            cmd += ['--request', str(request_path), '--case-manifest', str(source), '--audit-receipts', *map(str, audits)]
        else:
            preview, plan, previous = self._typed_case_artifact(case_id, params.get('preview_job_id'), params.get('preview_artifact_id'), 'invoice_selection', ['preview'])
            if plan.get('schema') != 'ustc-invoice-selection-v1' or plan.get('case_id') != case_id or plan.get('request', {}).get('tab_id') != tab or previous['parameters'].get('tab_id') != tab:
                raise APIError('invoice_selection_preview', '引用同案同标签正式preview的原始计划')
            if action == 'apply':
                if plan.get('apply_ready') is not True or plan.get('selection_available') is not True or plan.get('context_available') is not True:
                    raise APIError('invoice_selection_not_ready', '候选预览缺真实勾选或上下文，不可apply')
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', request_id):
                    raise APIError('invoice_selection_request_id', 'apply请求号须为1至80位ASCII字母数字/下划线/横线')
                cmd += ['--execute', '--request-id', request_id]
            cmd += ['--preview', str(preview), '--preview-sha256', hashlib.sha256(preview.read_bytes()).hexdigest()]
        if 'snapshot_job_id' in params or 'snapshot_artifact_id' in params:
            snapshot, data, previous = self._typed_case_artifact(case_id, params.get('snapshot_job_id'), params.get('snapshot_artifact_id'), 'invoice_verification', ['snapshot', 'surface', 'recover'])
            if data.get('tab_id') != tab or previous['parameters'].get('tab_id') != tab:
                raise APIError('invoice_selection_snapshot', '来源snapshot须为同案同标签正式只读回执')
            cmd += ['--snapshot', str(snapshot)]
        return cmd

    def _invoice_verification_command(self, case_id, parameters, folder, output):
        action = parameters.get('action')
        if action not in ['snapshot', 'full-grid', 'surface', 'surface-plan', 'read-detail', 'view', 'query-used', 'recover']:
            raise APIError('unsupported_invoice_verification', '只支持snapshot/full-grid/surface/surface-plan/read-detail/view/query-used/recover静默只读能力')
        tab = parameters.get('tab_id')
        if not isinstance(tab, str) or not re.fullmatch(r'[0-9]+', tab):
            raise APIError('tab_id_required', '发票查询必须传本次tabs查到的当前数字tab_id')
        fields = {'snapshot': set(), 'full-grid': {'grid', 'expected_userid'}, 'surface': set(), 'view': {'view', 'expected_userid'}, 'query-used': {'invoice_numbers'},
                  'recover': {'job_id', 'artifact_id'},
                  'surface-plan': {'job_id', 'artifact_id', 'operation_key'},
                  'read-detail': {'job_id', 'artifact_id', 'operation_key'}}[action]
        if set(parameters) - {'action', 'tab_id'} - fields:
            raise APIError('invoice_verification_arguments', '参数与只读发票查询动作不匹配')
        cmd = [self.python, str(PROJECT/'tools/finance_invoice_verification.py'), action,
               '--output', str(output)]
        if action == 'full-grid':
            if parameters.get('grid') not in ['53', '113', '101']:
                raise APIError('invoice_verification_grid', '完整回读只接受53、113或101字符串表号')
            who = parameters.get('expected_userid')
            if not isinstance(who, str) or not 1 <= len(who) <= 128 or who != who.strip():
                raise APIError('invoice_verification_identity', '完整回读须明确当前expected_userid')
            cmd += ['--grid', parameters['grid'], '--expected-userid', who]
        if action == 'surface':
            return [self.python, str(PROJECT/'tools/finance_surface.py'), 'snapshot', '--output', str(output)]
        if action in ['surface-plan', 'read-detail']:
            from finance_surface import detail_script, make_plan
            source, provenance = self._finance_surface_source(case_id, parameters, tab)
            try:
                if action == 'read-detail':
                    detail_script(read_json(source), parameters.get('operation_key'), case_id, tab)
                else:
                    make_plan(read_json(source), parameters.get('operation_key'), case_id, tab, provenance)
            except ValueError as error:
                raise APIError('finance_surface_operation', str(error)) from error
            return [self.python, str(PROJECT/'tools/finance_surface.py'),
                    'detail' if action == 'read-detail' else 'plan', '--receipt', str(source),
                    '--operation-key', parameters['operation_key'], '--case-id', case_id,
                    '--source-job-id', parameters['job_id'], '--source-artifact-id', parameters['artifact_id'],
                    '--output', str(output)]
        if action == 'view':
            if parameters.get('view') not in ['home', 'used', 'recognition', 'pool', 'drafts', 'in-progress', 'awaiting-approval','all-bills']:
                raise APIError('invoice_verification_view', '只支持home/used/recognition/pool/drafts/in-progress原生只读视图')
            cmd.append(parameters['view'])
            if 'expected_userid' in parameters:
                who = parameters['expected_userid']
                if not isinstance(who, str) or not re.fullmatch(r'[A-Za-z0-9_.@-]{1,80}', who):
                    raise APIError('invoice_verification_identity', '原生导航expected_userid须为已核对的ASCII账号')
                cmd += ['--expected-userid', who]
        elif action == 'query-used':
            numbers = parameters.get('invoice_numbers')
            if not isinstance(numbers, list) or not 1 <= len(numbers) <= 6 or any(
                    not isinstance(n, str) or not re.fullmatch(r'[0-9]{20}', n) for n in numbers):
                raise APIError('invoice_verification_numbers', '每批1至6个20位字符串票号，不接受数字类型')
            if len(set(numbers)) != len(numbers):
                raise APIError('invoice_verification_numbers', '同批票号不能重复')
            numbers_path = folder/'invoice_numbers.json'
            atomic_json(numbers_path, numbers)
            cmd += ['--numbers', str(numbers_path)]
        elif action == 'recover':
            prior_job = parameters.get('job_id')
            prior_artifact = parameters.get('artifact_id')
            if not isinstance(prior_job, str) or not isinstance(prior_artifact, str):
                raise APIError('invoice_verification_recovery', '恢复必须引用本案旧job_id和artifact_id')
            source = self.artifact(case_id, prior_job, prior_artifact)
            request_file = self.job_folder(case_id, prior_job)/'request.json'
            if source.suffix != '.json' or not request_file.is_file():
                raise APIError('invoice_verification_recovery', '只接受本案发票查询任务的JSON原回执')
            previous = read_json(request_file)
            old_parameters = previous.get('parameters') if isinstance(previous, dict) else None
            if not isinstance(old_parameters, dict) or previous.get('operation') != 'invoice_verification' or old_parameters.get('action') not in ['snapshot', 'full-grid', 'surface', 'read-detail', 'view', 'query-used']:
                raise APIError('invoice_verification_recovery', '原回执必须来自同一能力的查询任务，不能重放其他动作')
            receipt = read_json(source)
            body = receipt.get('result') if isinstance(receipt, dict) else None
            marker = body.get('result_marker') if isinstance(body, dict) else None
            if not isinstance(receipt, dict) or receipt.get('tab_id') != tab or old_parameters.get('tab_id') != tab or not isinstance(marker, str) or not re.fullmatch(r'reimbursement-result-[a-f0-9]{32}', marker):
                raise APIError('invoice_verification_recovery', '原回执必须含同标签有效标记；不猜标记、不重发查询')
            cmd += ['--receipt', str(source)]
        return cmd

    def _finance_surface_source(self, case_id, parameters, tab):
        """只从同案固定只读任务回执解析已观察入口；不接受路径、JS 或自造目录。"""
        job, artifact_id = parameters.get('job_id'), parameters.get('artifact_id')
        key = parameters.get('operation_key')
        if not isinstance(job, str) or not isinstance(artifact_id, str) or not isinstance(key, str) or not re.fullmatch(r'op_[a-f0-9]{20}', key):
            raise APIError('finance_surface_source', '引用同案已完成 job_id/artifact_id 及 surface 返回的 operation_key')
        source = self.artifact(case_id, job, artifact_id)
        request_path = self.job_folder(case_id, job)/'request.json'
        if source.suffix != '.json' or not request_path.is_file():
            raise APIError('finance_surface_source', '字段来源须为同案固定只读任务 JSON 回执')
        previous = read_json(request_path)
        old = previous.get('parameters') if isinstance(previous, dict) else None
        if not isinstance(old, dict) or previous.get('operation') != 'invoice_verification' or old.get('action') not in ['snapshot', 'surface', 'recover'] or old.get('tab_id') != tab:
            raise APIError('finance_surface_source', '字段来源须为同案同标签 snapshot/surface/recover 回执')
        receipt = read_json(source)
        if not isinstance(receipt, dict) or receipt.get('tab_id') != tab:
            raise APIError('finance_surface_source', '来源标签与本次 tab_id 不符')
        return source, {'job_id': job, 'artifact_id': artifact_id, 'path': str(source),
                        'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}

    def enqueue(self, case_id, operation, parameters, request_id, execute=False):
        self.resolve(case_id)
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', request_id):
            raise APIError('request_id_required', 'request_id使用1至96位字母数字/点/下划线/短横线；同一意图重连重试保持相同编号')
        request = canonical_request(operation, parameters, execute)
        fingerprint = request_fingerprint(request)
        job_id = 'j_'+hashlib.sha256((case_id+'\0'+request_id).encode()).hexdigest()[:24]
        folder = self.jobs/case_id/job_id
        with self.mutex(case_id):
            if folder.exists():
                old = read_json(folder/'request.json')
                # 旧任务保留原字节与指纹，只按同一默认值规则比较，不重建或重跑。
                previous = canonical_request(old['operation'], old['parameters'], old['execute'])
                if request_fingerprint(previous) != fingerprint: raise APIError('request_id_conflict', '相同request_id不能更改动作或参数；新意图使用新编号')
                return {'ok': True, 'job_id': job_id, 'reused_existing_job': True, 'status': read_json(folder/'status.json')['status'], 'next': 'reimbursement_result读取原任务，不再次执行'}
            if self.active_jobs(case_id): raise APIError('case_busy', '本案有未结束任务，先result核对')
            from finance_open_draft import is_open_draft, ensure_globally_idle,pending_read_reference
            if is_open_draft(request):
                with self.mutex(case_id, browser=True):
                    a=request['parameters'];recovery=pending_read_reference(a)
                    ensure_globally_idle(self, case_id, job_id, target_tab_id=a.get('tab_id'),**({'recovery_source':recovery}if recovery else{}))
            folder.mkdir(parents=True)
            private_path(folder)
            request.update(case_id=case_id, request_id=request_id, fingerprint=fingerprint, revision=self.revision(case_id), workspace=str(self.root),
                           material_roots=list(map(str, self.material_roots)), python=self.python, queued_at_utc=stamp())
            try:
                self.command(request, folder)  # 先验证固定操作和参数，未知接口不会进入队列。
                atomic_json(folder/'request.json', request)
                atomic_json(folder/'status.json', {'status': 'queued', 'queued_at_utc': request['queued_at_utc'], 'system_result_verified': False})
                process = launch_worker(self.python, folder/'request.json', cwd=PROJECT)
                atomic_json(folder/'process.json', {'worker_pid': process.pid})
                # 服务器在线时回收自己的子进程；服务器退出不会终止独立任务。
                threading.Thread(target=process.wait, daemon=True).start()
            except Exception as e:
                atomic_json(folder/'status.json', {'status': 'unknown' if isinstance(e, WorkerLaunchUnknown) else 'failed',
                    'error': str(e), 'system_result_verified': False, 'recovery_required': isinstance(e, WorkerLaunchUnknown)})
                if not (folder/'request.json').exists():
                    atomic_json(folder/'request.json', request)
                if isinstance(e, WorkerLaunchUnknown):
                    return {'ok':False,'job_id':job_id,'case_id':case_id,'status':'unknown','recovery_required':True,
                            'system_result_verified':False,'next':'用reimbursement_result读取本任务；启动结果未知，不换请求号或重放'}
                raise
        return {'ok': True, 'job_id': job_id, 'status': 'queued', 'reused_existing_job': False, 'system_result_verified': False,
                'next': '用reimbursement_result查结果；退出助手不会重放任务。完成写入后仍需独立回读与record_result'}

    def artifact(self, case_id, job_id, artifact_id):
        folder = self.job_folder(case_id, job_id)
        status = read_json(folder/'status.json')
        if status['status'] not in ['completed', 'failed', 'unknown']: raise APIError('job_unfinished', '任务尚未结束')
        rows = status.get('artifacts', [])
        row = next((x for x in rows if x['artifact_id'] == artifact_id), None)
        if not row: raise APIError('artifact_missing', 'artifact_id须来自result列出的回执')
        p = Path(row['path']).resolve()
        _, paths = self.resolve(case_id)
        if not p.is_relative_to(folder) and not p.is_relative_to(paths['state'].parent/'receipts'):
            raise APIError('artifact_scope', '回执不属于本案任务')
        if hashlib.sha256(p.read_bytes()).hexdigest() != row['sha256']: raise APIError('artifact_changed', '回执生成后已被改动')
        return p

    def job_folder(self, case_id, job_id):
        self.resolve(case_id)
        if not isinstance(job_id, str) or not re.fullmatch(r'j_[a-f0-9]{24}', job_id): raise APIError('invalid_job_id', 'job_id须来自接口返回')
        folder = self.jobs/case_id/job_id
        if not folder.is_dir(): raise APIError('job_missing', '此案件没有该任务')
        if not folder.resolve().is_relative_to(self.jobs.resolve()): raise APIError('path_escape', '任务路径不符')
        return folder

    def result(self, case_id, job_id, artifact_id=None):
        folder = self.job_folder(case_id, job_id)
        r = read_json(folder/'status.json')
        if r['status'] in ['queued', 'running']:
            pidfile = folder/('process.json' if r['status']=='queued' else 'running.json')
            if pidfile.exists():
                pid = read_json(pidfile).get('worker_pid')
                if type(pid) is int and pid > 0:
                    if process_alive(pid) is False:
                        # 进程可能在首次读状态后正常结束；先保留其刚写下的最终回执。
                        r = read_json(folder/'status.json')
                        if r['status'] in ['queued', 'running']:
                            r.update(status='unknown', worker_exit_observed_at_utc=stamp(), recovery_required=True,
                                     next='工作进程已退出但无最终回执；先独立回读。不要换request_id重试或删除锁')
                            # 仅登记未知，不释放运行锁或财务pending。
                            atomic_json(folder/'status.json', r)
        if artifact_id:
            p = self.artifact(case_id, job_id, artifact_id)
            r['artifact'] = read_json(p) if p.suffix=='.json' else {'path': str(p), 'format': p.suffix[1:], 'next': '文档原件在本机；DOCX用render生成页面供查看'}
        registered_paths = set()
        if r['status'] in ['completed', 'failed', 'unknown']:
            for row in r.get('artifacts', []):
                try:
                    verified = self.artifact(case_id, job_id, row.get('artifact_id'))
                except (APIError, OSError):
                    continue  # 不为无效/改变的条目放开路径；仍可按artifact_id得到明确业务错误。
                row['path'] = str(verified)
                registered_paths.add(str(verified))
        # completed只表示程序完成。原生重识别守卫也会弹确认，旧回执曾误标false；
        # 保留原回执字节，统一结果以已验证的按钮阶段明确尚需确认。
        request_path = folder/'request.json'
        request = read_json(request_path) if request_path.is_file() else {}
        if request.get('operation') == 'access':
            output = next((row for row in r.get('artifacts', []) if row.get('name') == 'output.json' and row.get('path') in registered_paths), None)
            if output:
                native = read_json(output['path'])
                if native.get('schema') == 'ustc-finance-background-plan-v1' and request.get('parameters', {}).get('action') == 'new-interface':
                    r.update(workflow_state='legacy_browser_bridge_disabled', page_opened=False,
                        next='旧助手浏览器开页方案已停用，不再派发；已有意图仅可盘点原结果，继续使用项目正式脚本')
                elif native.get('schema') == 'ustc-finance-session-capability-v1':
                    r.update(workflow_state=native.get('state'), page_opened=False, next=native.get('next'))
                elif native.get('schema') == 'ustc-finance-page-open-v2':
                    r.update(workflow_state=native.get('state'), page_opened=native.get('page_opened', False),
                        new_tab_id=native.get('new_tab_id'), quiet_verified_at_observation_points=native.get('quiet_verified_at_observation_points', False), next=native.get('next'))
                elif native.get('schema') == 'ustc-finance-page-health-v1':
                    r.update(page_health=native.get('state'), preview_ready=native.get('preview_ready', False), next=native.get('next'))
        if request.get('operation') == 'invoice_retry' and request.get('parameters', {}).get('action') in ['cleanup', 'reidentify']:
            output = next((row for row in r.get('artifacts', []) if row.get('name') == 'output.json' and row.get('path') in registered_paths), None)
            if output:
                envelope = read_json(output['path'])
                action_result = envelope.get('result', {})
                if envelope.get('phase') == 'script_completed' and action_result.get('ok') is True and action_result.get('case_id') == case_id and action_result.get('action') == request['parameters']['action'] and action_result.get('marker', {}).get('phase') == 'button_returned':
                    r.update(needs_confirmation=True, requires_independent_readback=True,
                             next='原生按钮已返回，仍须独立inspect核对确认弹窗后confirm，再回读；尚未证明重识别或清理完成')
        r.update(job_id=job_id, case_id=case_id, ok=r['status']=='completed', system_result_verified=False)
        return masked(r, _registered_paths=registered_paths)

    def command(self, request, folder):
        """唯一命令目录。参数不是shell文本，路径由本案和回执标识解析。"""
        p, _ = self.resolve(request['case_id'])
        op, a, execute = request['operation'], request['parameters'], request['execute']
        if not isinstance(a, dict) or not isinstance(execute, bool): raise APIError('invalid_arguments', '参数须为对象，execute须为布尔值')
        allowed = {'material_intake': {'request'}, 'material_ocr': {'action', 'request', 'ocr_job_id'},
                   'pdf_export': {'export_plan','export_root','tab_id','expected_userid','silent'},
                   'check': {'hashes'}, 'tabs': set(), 'scan': {'paths', 'invoices'}, 'stage': {'stage', 'tab_id'},
                   'invoice': {'action', 'start', 'count', 'tab_id'}, 'read_system': {'mode', 'tab_id', 'job_id', 'artifact_id', 'serial', 'expected_userid'},
                   'record_result': {'kind', 'job_id', 'artifact_id', 'serial', 'outcome', 'note'}, 'activity': {'values', 'check_only', 'allow_incomplete'},
                   'render': {'job_id', 'artifact_id'},
                   'correction': {'action', 'plan', 'group', 'key', 'kind', 'tab_id', 'job_id', 'artifact_id'},
                   'submission': {'action', 'plan', 'tab_id'},
                   'access': {'action', 'tab_id', 'job_id', 'artifact_id', 'expected_userid', 'silent'},
                   'invoice_verification': {'action', 'tab_id', 'invoice_numbers', 'view', 'job_id', 'artifact_id', 'operation_key', 'grid', 'expected_userid'},
                   'invoice_edit': {'action', 'tab_id', 'invoice_number', 'expected_userid', 'job_id', 'artifact_id', 'inspection_job_id', 'inspection_artifact_id'},
                   'expense_amount': {'action', 'group', 'subject', 'expected_userid', 'tab_id', 'source_job_id', 'source_artifact_id', 'job_id', 'artifact_id','claims_path'},
                   'invoice_sources': {'action', 'invoice_numbers'},
                   'invoice_retry': {'action', 'tab_id', 'expected_userid', 'grid', 'target_guids', 'target_filecodes',
                       'planned_action', 'targets', 'source_job_id', 'source_artifact_id', 'pool_job_id', 'pool_artifact_id',
                       'recognition_job_id', 'recognition_artifact_id', 'inspection_job_id', 'inspection_artifact_id',
                       'plan_job_id', 'plan_artifact_id', 'receipt_job_id', 'receipt_artifact_id', 'job_id', 'artifact_id'},
                   'invoice_selection': {'action', 'tab_id', 'source_job_id', 'source_artifact_id', 'audit_refs',
                       'selectors', 'mode', 'grid', 'serial', 'expected_userid', 'expected_before_guids',
                       'snapshot_job_id', 'snapshot_artifact_id', 'preview_job_id', 'preview_artifact_id', 'job_id', 'artifact_id'}}
        if op not in allowed or set(a)-allowed[op]: raise APIError('unknown_operation', '动作或参数不在固定接口目录')
        if execute and op not in ['stage', 'invoice', 'correction', 'submission', 'access', 'invoice_selection', 'invoice_retry', 'invoice_edit', 'expense_amount', 'pdf_export', 'material_intake', 'material_ocr']: raise APIError('invalid_execute', '只有已支持执行目录允许execute，选票仅apply')
        cmd = [self.python, str(PROJECT/'reimburse.py'), '--bundle', str(p)]
        output = folder/'output.json'
        tab = a.get('tab_id')
        if op == 'read_system' and a.get('mode') == 'open-draft':
            from finance_open_draft import command
            return command(self, request, folder)
        if op == 'read_system' and {'serial', 'expected_userid'} & set(a):
            raise APIError('open_draft_parameters', 'serial/expected_userid仅供固定open-draft入口')
        if op == 'material_intake':
            from material_intake_service import command
            return command(self, request, folder)
        if op == 'material_ocr':
            from material_ocr_service import command
            return command(self, request, folder)
        if op == 'pdf_export':
            from finance_pdf_export import export_pdf as preview_pdf_export
            _, paths = self.resolve(request['case_id'])
            form = read_json(paths['form'])
            case = {'case_id':request['case_id'],'serial':form.get('serial')}
            root = a.get('export_root')
            if not isinstance(root, str) or not Path(root).is_absolute() or not any(Path(root).resolve().is_relative_to(x) for x in self.material_roots):
                raise APIError('material_root', 'PDF导出根须是material-root内的明确绝对路径')
            preview_pdf_export(case, a.get('export_plan'), root, folder/'pdf_export', tab, a.get('expected_userid'), a.get('silent',True), False)
            atomic_json(folder/'pdf_case.json', case)
            atomic_json(folder/'pdf_plan.json', a['export_plan'])
            command = [self.python,str(PROJECT/'tools/finance_pdf_export.py'),'--case',str(folder/'pdf_case.json'),
                       '--plan',str(folder/'pdf_plan.json'),'--export-root',root,'--output-directory',str(folder/'pdf_export'),
                       '--tab-id',tab,'--expected-userid',a['expected_userid']]
            return command+(['--no-silent'] if a.get('silent',True) is False else [])+(['--execute'] if execute else [])
        if op in ['stage', 'invoice', 'correction', 'read_system'] and (execute or op == 'read_system'):
            if not isinstance(tab, str) or not re.fullmatch(r'\d+', tab): raise APIError('tab_id_required', '现场操作必须传tabs查到的当前财务tab_id')
        if op == 'access':
            if tab is not None and (not isinstance(tab, str) or not re.fullmatch(r'\d+', tab)):
                raise APIError('invalid_tab_id', 'tab_id须为当前数字ID')
            action = a.get('action') or ('ensure' if execute else 'check')
            if action=='diagnose':
                if execute or not isinstance(tab,str) or set(a)-{'action','tab_id'}:
                    raise APIError('finance_session_arguments','diagnose仅对明确tab_id只读诊断，不登录或导航')
                return [self.python,str(PROJECT/'tools/finance_access.py'),'diagnose','--tab-id',tab,'--request-id',request['request_id'],'--output',str(output)]
            if action == 'check-page':
                if execute or not isinstance(tab, str) or not re.fullmatch(r'[0-9]+', tab) or set(a)-{'action','tab_id','expected_userid'} or not isinstance(a.get('expected_userid'), str) or not re.fullmatch(r'[A-Za-z0-9]{2,40}', a['expected_userid']):
                    raise APIError('finance_session_arguments', 'check-page仅静默只读，须明确tab_id和expected_userid，不接URL或脚本')
                return [self.python, str(PROJECT/'tools/finance_session.py'), 'check-page', '--case-id', request['case_id'], '--tab-id', tab, '--expected-userid', a['expected_userid'], '--output', str(output)]
            if action in ['new-interface', 'recover-interface','wake-interface']:
                cmd = [self.python, str(PROJECT/'tools/finance_session.py'), action,
                       '--case-id', request['case_id'], '--output', str(output)]
                if action in ['new-interface','wake-interface']:
                    if not execute or (action=='wake-interface' and tab is None) or set(a)-({'action','tab_id','silent'} if action=='new-interface' else {'action','tab_id'}):
                        raise APIError('finance_session_arguments', '开页须execute=true；唤醒须旧tab_id；不接URL/脚本/其他路径')
                    if 'silent' in a and type(a['silent']) is not bool: raise APIError('finance_session_arguments','silent须为布尔开关')
                    return cmd+['--request-id', request['request_id']]+(['--tab-id', tab] if tab is not None else [])+(['--allow-foreground-open'] if a.get('silent',True) is False else [])
                if execute or set(a)-{'action', 'job_id', 'artifact_id'}:
                    raise APIError('finance_session_arguments', '恢复只引用同案job/artifact，execute=false，不新开界面')
                source = self.artifact(request['case_id'], a.get('job_id'), a.get('artifact_id'))
                previous = read_json(self.job_folder(request['case_id'], a.get('job_id'))/'request.json')
                native = read_json(source)
                if previous.get('operation') != 'access' or previous.get('parameters', {}).get('action') != 'new-interface' or native.get('schema') not in ['ustc-finance-session-v1','ustc-finance-background-plan-v1','ustc-finance-page-open-v2'] or native.get('case_id') != request['case_id'] or native.get('request_id') != previous.get('request_id') or native.get('old_tab_id') != previous.get('parameters', {}).get('tab_id'):
                    raise APIError('finance_session_source', '只能盘点同案正式新界面原意图/SHA回执，不重发启动')
                return cmd+['--request-id', previous['request_id'], '--receipt', str(source)]+(['--tab-id', native['old_tab_id']] if native['old_tab_id'] is not None else [])+(['--allow-foreground-open'] if native.get('silent',True) is False else [])
            if action not in ['check', 'ensure'] or (action == 'ensure') != execute or set(a)-{'action', 'tab_id'}:
                raise APIError('finance_session_arguments', 'access仅check/ensure/new-interface/recover-interface；动作与execute须一致')
            cmd = [self.python, str(PROJECT/'tools/finance_access.py'), 'ensure' if execute else 'check']
            if execute:
                cmd += ['--request-id', request['request_id'], '--output', str(output)]
                if sys.platform == 'win32' or mac_native_enabled():
                    cmd += ['--case-id', request['case_id'], '--allow-credential-login']
                if tab is not None: cmd += ['--keep-internal-tab']
        elif op == 'invoice_verification':
            cmd = self._invoice_verification_command(request['case_id'], a, folder, output)
        elif op == 'invoice_sources':
            cmd = self._invoice_sources_command(request['case_id'], a, output)
        elif op == 'invoice_retry':
            return self._invoice_retry_command(request['case_id'], a, folder, output, execute, request['request_id'])
        elif op == 'invoice_edit':
            return self._invoice_edit_command(request['case_id'], a, folder, output, execute, request['request_id'])
        elif op == 'expense_amount':
            return self._expense_amount_command(request['case_id'], a, folder, output, execute, request['request_id'])
        elif op == 'invoice_selection':
            cmd = self._invoice_selection_command(request['case_id'], a, folder, output, execute, request['request_id'])
        elif op == 'check': cmd += ['check', '--output', str(output)]+(['--hashes'] if a.get('hashes', True) else [])
        elif op == 'tabs': cmd += ['tabs']
        elif op == 'scan':
            if not isinstance(a.get('paths'), list) or not a['paths']: raise APIError('invalid_paths', '给出材料绝对路径列表')
            cmd += ['scan', *map(str, [self.material_path(x) for x in a['paths']]), '--output', str(output)]+(['--invoices'] if a.get('invoices', True) else [])
        elif op == 'stage':
            if a.get('stage') not in STAGES: raise APIError('unsupported_stage', 'stage须为已支持阶段')
            from reimburse import guard_step
            _, paths = self.resolve(request['case_id'])
            guard_step(read_json(paths['form']), a['stage'])
            cmd += ['step', a['stage']]+(['--execute'] if execute else [])
        elif op == 'invoice':
            action = a.get('action')
            if action == 'upload':
                start, count = a.get('start', 0), a.get('count', 10)
                if type(start) is not int or type(count) is not int or start<0 or not 1<=count<=10: raise APIError('batch_size', 'start为非负整数，count为1至10')
                _, paths = self.resolve(request['case_id'])
                rows = read_json(paths['invoices']).get('original_files', [])[start:start+count]
                if any(row.get('ocr_issues') and row.get('visual_check')!='verified' for row in rows):
                    raise APIError('invoice_needs_review', '本批原件有OCR异常；先查看原件并verify_invoice登记核对')
                cmd += ['upload', '--start', str(start), '--count', str(count)]
            elif action in ['open-pool', 'open-upload', 'read', 'save-upload', 'create-draft','resume-draft']: cmd += ['invoice', action]
            else: raise APIError('unsupported_invoice_action', '未知发票动作；无删除接口')
            if execute: cmd += ['--execute']
        elif op == 'correction':
            from correction import validate_plan
            action = a.get('action')
            if action not in ['check', 'verify', 'apply', 'begin', 'open', 'select', 'update', 'save', 'return', 'remark', 'basic', 'basic-read', 'basic-close', 'drafts', 'resume']:
                raise APIError('unsupported_correction', '只提供逐票拆分动作，不提供审批同意或重建')
            if not isinstance(a.get('plan'), dict): raise APIError('correction_plan', 'plan须含票据、人员、往返用途及经费上限')
            for row in a['plan'].get('people', []):
                source = self.material_path(row['source_file'])
                if source.suffix.lower() not in ['.pdf', '.ofd', '.png', '.jpg', '.jpeg']:
                    raise APIError('correction_source', '逐票方案只接受原始票据文件')
            _, paths = self.resolve(request['case_id'])
            validate_plan(a['plan'], read_json(paths['form']))
            if action == 'basic':
                from correction import basic_fields
                basic_fields(a['plan'])
            proposal = folder/'correction_input.json'; atomic_json(proposal, a['plan'])
            cmd += ['correction', action, '--input', str(proposal), '--output', str(output)]
            for flag in ['group', 'key', 'kind']:
                if a.get(flag): cmd += ['--'+flag, a[flag]]
            if action == 'verify': cmd += ['--readback', str(self.artifact(request['case_id'], a.get('job_id'), a.get('artifact_id')))]
            if execute: cmd += ['--execute']
        elif op == 'submission':
            from submission import ACTIONS, validate_plan
            action = a.get('action')
            if action not in ACTIONS: raise APIError('unsupported_submission', '提交动作须为inspect/delivery-open/options/next/promise/department/applicant-agree')
            if not isinstance(a.get('plan'), dict): raise APIError('submission_plan', 'plan须包含本案单号、摘要、电话、无合同、打印选项及用户授权')
            _, paths = self.resolve(request['case_id'])
            form = read_json(paths['form'])
            validate_plan(a['plan'], form)
            if execute and (not isinstance(tab, str) or not re.fullmatch(r'\d+', tab)):
                raise APIError('tab_id_required', '提交现场操作必须传当前财务tab_id')
            proposal = folder/'submission_input.json'; atomic_json(proposal, a['plan'])
            cmd += ['submit', action, '--input', str(proposal), '--output', str(output)]
            if execute: cmd += ['--execute']
        elif op == 'read_system':
            mode = a.get('mode', 'readback')
            if mode in ['status', 'readback']:
                _, paths = self.resolve(request['case_id'])
                if read_json(paths['form']).get('serial'): cmd += [mode, '--output', str(output)]
                else: cmd += ['invoice', 'read', '--execute']  # 建单前也能独立读上传追踪和原生票据池。
            elif mode == 'recover': cmd += ['recover', '--receipt', str(self.artifact(request['case_id'], a.get('job_id'), a.get('artifact_id')))]
            else: raise APIError('unsupported_read', 'mode为status/readback/recover/open-draft')
        elif op == 'record_result':
            source = self.artifact(request['case_id'], a.get('job_id'), a.get('artifact_id'))
            kind = a.get('kind')
            flags = {'register-originals': '--scan-result', 'select-invoices': '--pool-receipt', 'bind-serial': '--receipt', 'ack-write': '--readback'}
            if kind not in flags: raise APIError('unsupported_record', '未知回执登记动作')
            cmd += [kind, flags[kind], str(source)]
            if kind == 'bind-serial':
                if not re.fullmatch(r'\d+', a.get('serial') or ''): raise APIError('invalid_serial', 'serial须为实际系统数字单号')
                cmd += ['--serial', a['serial']]
            if kind == 'ack-write':
                if a.get('outcome') not in ['saved', 'staged', 'not_applied', 'needs_correction'] or not a.get('note', '').strip():
                    raise APIError('readback_note', 'outcome和按清单核对的note必填；不以请求发出作为saved')
                cmd += ['--outcome', a['outcome'], '--note', a['note']]
        elif op == 'activity':
            if not isinstance(a.get('values'), dict): raise APIError('invalid_activity', 'values须为模板输入对象')
            if not isinstance(a.get('allow_incomplete',False),bool) or not isinstance(a.get('check_only',True),bool):raise APIError('invalid_activity','allow_incomplete/check_only须为布尔值')
            atomic_json(folder/'activity_input.json', a['values'])
            cmd += ['activity', '--input', str(folder/'activity_input.json')]
            if a.get('allow_incomplete',False):cmd += ['--allow-incomplete']
            if a.get('check_only', True): cmd += ['--check-only']
            else: cmd += ['--output', str(folder/'activity.docx')]
        elif op == 'render':
            source = self.artifact(request['case_id'], a.get('job_id'), a.get('artifact_id'))
            if source.suffix != '.docx': raise APIError('render_source', 'render选activity任务中的DOCX产物')
            cmd += ['render', '--input', str(source), '--directory', str(folder/'render')]
        if tab and op != 'invoice_selection': cmd += ['--tab-id', tab]
        return cmd
