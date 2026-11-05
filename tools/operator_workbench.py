"""本机人工工作台：固定业务目录、确认意图与既有AssistantService。"""
from __future__ import annotations

import argparse
import hashlib
import hmac
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import secrets
import threading
import time
import urllib.request
from urllib.parse import parse_qs, quote, unquote, urlsplit
import webbrowser

from assistant_api import AssistantService, atomic_json, read_json
from assistant_contract import APIError, dispatch_request
from material_intake import reject_links
from platform_runtime import private_path
from workbench_catalog import catalog, json_request

ROOT = Path(__file__).resolve().parents[1]
UI = Path(__file__).with_name('operator_workbench_ui')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def code_identity():
    """启动时核对整套业务代码，避免更新后沿用旧服务。"""
    files=sorted((ROOT/'tools').glob('*.py'))+sorted((ROOT/'tools').glob('*.js'))+sorted(UI.glob('*'))+sorted((ROOT/'data').glob('*.json'))+[ROOT/'mcp/tool_catalog.json']
    return digest({str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()})


class Workbench:
    def __init__(self, service):
        self.service = service
        self.catalog = catalog(ROOT)
        self.tools = {row['name']: row for row in self.catalog['tools']}
        self.folder = service.root/'.local/operator-workbench'
        self.folder.mkdir(parents=True, exist_ok=True)
        private_path(self.folder)
        self.lock = threading.Lock()
        self.in_flight = 0

    def validate(self, tool_name, arguments):
        from jsonschema import Draft202012Validator
        require(tool_name in self.tools and isinstance(arguments, dict), '请选择已提供的业务操作')
        tool = self.tools[tool_name]
        schema = tool['inputSchema']
        require(not set(arguments)-set(schema.get('properties', {})), '包含当前操作不支持的字段')
        issues = sorted(Draft202012Validator(schema).iter_errors(arguments), key=lambda e: str(list(e.path)))
        if issues:
            issue = issues[0]
            path = '.'.join(str(v) for v in issue.path) or '本次操作'
            raise APIError('workbench_input', f'{path}：{issue.message}')
        return tool, json_request(tool, arguments)

    def prepare(self, tool_name, arguments):
        self.validate(tool_name,arguments)
        if tool_name=='reimbursement_case_finance' and arguments.get('action')in {'selection_apply','bind_created','ready_plan'} and not arguments.get('job_id'):
            from case_finance import prior
            key={'selection_apply':'selection_preview','bind_created':'create','ready_plan':'ready_inspect'}[arguments['action']]
            arguments={**arguments,'job_id':prior(self.service,arguments['case_id'],key)}
        if tool_name == 'reimbursement_receive_materials' and arguments.get('execute') is True:
            from receive_materials import plan
            _, _, fingerprint = plan(self.service, arguments['case_id'], arguments['paths'], arguments.get('grouping', 'unassigned'))
            if arguments.get('source_sha256', fingerprint) != fingerprint:
                raise APIError('receive_source_changed', '所选材料已改变，请重新预览再确认')
            arguments = {**arguments, 'source_sha256': fingerprint}
        tool, request = self.validate(tool_name, arguments)
        case_id = arguments.get('case_id')
        revision = self.service.revision(case_id) if case_id and tool_name != 'reimbursement_new_case' else None
        intent_id = secrets.token_hex(16)
        from workbench_catalog import financial_write_requested
        value = {'id': intent_id, 'tool': tool_name, 'title': tool['title'], 'request': request,
                 'revision': revision, 'created_at': time.time(), 'status': 'prepared',
                 'financial_execute_requested': financial_write_requested(tool_name,arguments),
                 'execution_requested': arguments.get('execute') is True,
                 'source_fingerprint': digest(request)}
        atomic_json(self.folder/'intents'/(intent_id+'.json'), value)
        return {'ok': True, 'intent_id': intent_id, 'title': tool['title'], 'arguments': arguments,
                'financial_execute_requested': value['financial_execute_requested'],
                'note': '请核对本次范围；实际办理仍需通过业务工具的来源、身份和权限检查。'}

    def intent(self, intent_id):
        require(isinstance(intent_id, str) and re.fullmatch('[a-f0-9]{32}', intent_id), '操作确认标识无效')
        path = self.folder/'intents'/(intent_id+'.json')
        reject_links(path)
        return path, read_json(path)

    def execute(self, intent_id, confirmation_note=None):
        from material_review_launch import launch_lock
        path, _ = self.intent(intent_id)
        with self.lock, launch_lock(path):
            value = read_json(path)
            if value['status'] != 'prepared':
                return {'ok': value['status'] == 'returned', 'intent_id': intent_id, 'replayed': True, **value}
            require(digest(value['request']) == value['source_fingerprint'], '操作计划发生变化，请重新核对')
            case = value['request']['params'].get('case_id')
            if value['revision'] is not None and self.service.revision(case) != value['revision']:
                raise APIError('revision_conflict', '活动资料已更新，请刷新并重新核对本次操作')
            value.update(status='dispatching', dispatched_at=time.time())
            if confirmation_note:
                value['confirmation_note'] = confirmation_note
            atomic_json(path, value)
            self.in_flight += 1
        try:
            result = dispatch_request(self.service, value['request'])
            value.update(status='returned', result=result)
        except Exception as error:
            # 已派发意图不能因为界面报错而重新执行；按原任务及原业务回执接续。
            value.update(status='needs_review', error_code=getattr(error, 'code', type(error).__name__), error=str(error),
                         safe_to_retry=False)
        value['finished_at'] = time.time()
        try:
            atomic_json(path, value)
        finally:
            with self.lock:
                self.in_flight -= 1
        return {'ok': value['status'] == 'returned', 'intent_id': intent_id, **value}

    def read(self, tool_name, arguments):
        tool, request = self.validate(tool_name, arguments)
        from workbench_catalog import read_allowed
        require(read_allowed(tool,arguments),
                '此操作需要先核对并确认')
        return dispatch_request(self.service, request)

    def tasks(self, case_id=None):
        if case_id:
            self.service.resolve(case_id)
        rows = []
        for path in sorted((self.folder/'intents').glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:100]:
            value = read_json(path)
            if case_id and value['request']['params'].get('case_id') != case_id:
                continue
            rows.append({key: value.get(key) for key in ['id','title','status','created_at','finished_at','result','error','safe_to_retry']})
        return rows

    def upload(self, stream, size, *, case_id, batch_id, name, sha256):
        self.service.resolve(case_id)
        require(re.fullmatch('[a-f0-9]{32}', batch_id or '') and re.fullmatch('[a-f0-9]{64}', sha256 or ''), '收件批次或文件校验信息无效')
        require(type(size) is int and 0 < size <= 100*1024*1024, '单个文件须大于0且不超过100MiB')
        require(isinstance(name, str) and len(name) <= 500 and '\\' not in name and ':' not in name, '文件名无效')
        relative = PurePosixPath(name)
        require(not relative.is_absolute() and relative.parts and all(p not in {'.','..',''} for p in relative.parts), '文件路径越界')
        require(relative.suffix.lower() in {'.pdf','.ofd','.png','.jpg','.jpeg','.webp','.doc','.docx','.xls','.xlsx','.csv','.txt','.zip'}, '暂不支持此文件格式')
        bucket = self.service.material_roots[0]/'原始收件'/case_id/batch_id
        target = bucket/relative
        reject_links(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        private_path(bucket)
        if target.exists():
            require(target.is_file() and target.stat().st_size == size and hashlib.sha256(target.read_bytes()).hexdigest() == sha256, '同名收件已有不同内容，请保留原件并重新选定批次')
            return {'ok':True,'reused':True,'source_dir':str(bucket),'path':str(target),'sha256':sha256,'system_write':False}
        temporary = target.with_name('.upload-'+hashlib.sha256(name.encode()).hexdigest()[:20]+'.part')
        written = 0
        digest_file = hashlib.sha256()
        created = False
        try:
            with temporary.open('xb') as out:
                created = True
                private_path(temporary)
                while written < size:
                    chunk = stream.read(min(1024*1024, size-written))
                    require(bool(chunk), '文件传输中断，请重新选择原文件')
                    out.write(chunk);digest_file.update(chunk);written += len(chunk)
                out.flush();os.fsync(out.fileno())
            require(digest_file.hexdigest() == sha256, '传输后的文件校验不一致，请重试原文件')
            os.link(temporary, target)
        finally:
            if created:
                temporary.unlink(missing_ok=True)
        return {'ok':True,'reused':False,'source_dir':str(bucket),'path':str(target),'sha256':sha256,'system_write':False}


class Server(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, service, port=0):
        super().__init__(('127.0.0.1', port), Handler)
        self.app = Workbench(service)
        self.token = secrets.token_urlsafe(32)
        self.csrf = secrets.token_urlsafe(32)
        self.cookie_name = 'ustc_workbench_'+str(self.server_port)
        self.origin = f'http://127.0.0.1:{self.server_port}'
        self.instance_id = secrets.token_hex(16)
        self.code_identity = code_identity()


class Handler(BaseHTTPRequestHandler):
    server_version = 'ReimbursementWorkbench/1'
    def log_message(self, *args):
        pass

    def send(self, code, data, kind='application/json; charset=utf-8', headers=None):
        if isinstance(data, (dict,list)):
            data = json.dumps(data, ensure_ascii=False)
        if isinstance(data, str):
            data = data.encode('utf-8')
        self.send_response(code)
        for key, value in {'Content-Type':kind, 'Content-Length':str(len(data)), 'Cache-Control':'no-store',
            'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; frame-src 'self'; frame-ancestors 'self'; object-src 'none'; base-uri 'none'",
            **(headers or {})}.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def guard(self, write=False):
        require(self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}', '工作台只接受本机固定地址')
        if write:
            require(self.headers.get('Origin') == self.server.origin, '请从当前工作台页面发起操作')

    def authenticated(self):
        cookie = SimpleCookie()
        cookie.load(self.headers.get('Cookie', ''))
        value = cookie.get(self.server.cookie_name)
        return value is not None and hmac.compare_digest(value.value, self.server.token)

    def body(self):
        length = int(self.headers.get('Content-Length', '0'))
        require(0 < length <= 4*1024*1024, '操作输入为空或超过4MiB')
        require(self.headers.get_content_type() == 'application/json', '请使用结构化业务输入')
        value = json.loads(self.rfile.read(length).decode('utf-8'))
        require(isinstance(value, dict), '业务输入须为对象')
        return value

    def do_GET(self):
        try:
            self.guard()
            url = urlsplit(self.path)
            path = unquote(url.path)
            static = {'/': UI/'index.html', '/app.js': UI/'app.js', '/input_contract.js': UI/'input_contract.js', '/style.css': UI/'style.css',
                      '/material_review.js': UI/'material_review.js',
                      '/case_details.js': UI/'case_details.js',
                      '/case_materials.js': UI/'case_materials.js',
                      '/journey.js': UI/'journey.js',
                      '/connection.js': UI/'connection.js',
                      '/case_finance.js': UI/'case_finance.js',
                      '/standards.js': UI/'standards.js',
                      '/delivery.js': UI/'delivery.js',
                      '/approver.js': UI/'approver.js',
                      '/material_requirements.js': UI/'material_requirements.js',
                      '/intake_groups.js': UI/'intake_groups.js',
                      '/财务反馈汇总.js': ROOT/'tools/finance_feedback_ui.js'}
            if path in static:
                file = static[path]
                kind = {'.html':'text/html','.js':'text/javascript','.css':'text/css'}[file.suffix]
                self.send(200, file.read_bytes(), kind+'; charset=utf-8')
                return
            if path == '/health':
                self.send(200, {'ok':True,'schema':'ustc-operator-workbench-v1','instance_id':self.server.instance_id,'code_identity':self.server.code_identity})
                return
            if not self.authenticated():
                self.send(401, {'ok':False,'error':'请从本机工作台启动入口打开此页面'})
                return
            query = parse_qs(url.query)
            if path == '/api/bootstrap':
                cases = self.server.app.service.cases()
                self.send(200, {'ok':True,'csrf':self.server.csrf, 'catalog':self.server.app.catalog,
                               'cases':cases['cases'], 'material_roots':[str(p) for p in self.server.app.service.material_roots]})
            elif path == '/api/tasks':
                self.send(200, {'ok':True,'tasks':self.server.app.tasks(query.get('case_id',[None])[0])})
            elif path == '/api/intent':
                _, value = self.server.app.intent(query.get('id',[''])[0])
                self.send(200, {'ok':True, **value})
            elif path == '/feedback-view':
                result = self.server.app.service.finance_feedback(query.get('case_id',[''])[0], query.get('source_job_id',[''])[0])
                payload = json.dumps(result,ensure_ascii=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
                page = (ROOT/'tools/finance_feedback_ui.html').read_text(encoding='utf-8').replace('/*REPORT_DATA*/{}',payload)
                self.send(200,page,'text/html; charset=utf-8')
            elif path == '/api/material-delivery-file':
                from case_materials_delivery import download
                item = download(self.server.app.service, query.get('case_id',[''])[0], query.get('delivery_id',[''])[0], query.get('artifact_name',[''])[0])
                self.send(200, Path(item['path']).read_bytes(), item['content_type'], headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(item['name'],safe='')})
            elif path == '/api/delivery-file':
                from case_delivery import download
                name,raw=download(self.server.app.service,query.get('case_id',[''])[0],query.get('plan_id',[''])[0])
                self.send(200,raw,'application/pdf',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(name,safe='')})
            elif path == '/api/invoice-preview':
                from case_journey import preview
                raw=preview(self.server.app.service, query.get('case_id',[''])[0],int(query.get('index',['-1'])[0]),int(query.get('page',['1'])[0]))
                self.send(200,raw,'image/jpeg')
            elif path == '/api/material-preview':
                result = self.server.app.service.intake_review(query.get('case_id',[''])[0],query.get('intake_job_id',[''])[0],
                    action='preview',material_id=query.get('material_id',[''])[0],page=int(query.get('page',['1'])[0]))
                if result.get('preview_path'):
                    self.send(200,Path(result['preview_path']).read_bytes(),result['content_type'])
                else:
                    self.send(200,result.get('text') or result.get('reason') or '此格式未提供预览','text/plain; charset=utf-8')
            elif path == '/api/material-original':
                from intake_review import bound_file
                file, row, _, _, _ = bound_file(self.server.app.service,query.get('case_id',[''])[0],query.get('intake_job_id',[''])[0],query.get('material_id',[''])[0])
                self.send(200,file.read_bytes(),'application/octet-stream',headers={
                    'Content-Disposition':"attachment; filename*=UTF-8''"+quote(Path(row['filename']).name,safe='')})
            else:
                self.send(404, {'ok':False,'error':'没有这个工作台入口'})
        except (ValueError,OSError,KeyError,TypeError) as error:
            self.send(400, {'ok':False,'error_code':getattr(error,'code','workbench_input'),'error':str(error)})

    def do_POST(self):
        try:
            path = urlsplit(self.path).path
            # JSON仅限4MiB，先读完请求体再返回拒绝，避免Windows因关闭含未读数据的连接而丢失错误响应。
            value = None if path == '/api/upload' else self.body()
            self.guard(write=True)
            if path == '/api/upload':
                require(self.authenticated() and hmac.compare_digest(self.headers.get('X-Workbench-CSRF',''),self.server.csrf), '页面会话已变化')
                query = parse_qs(urlsplit(self.path).query)
                result = self.server.app.upload(self.rfile, int(self.headers.get('Content-Length','0')),
                    case_id=query.get('case_id',[''])[0], batch_id=query.get('batch_id',[''])[0],
                    name=query.get('name',[''])[0], sha256=self.headers.get('X-File-SHA256',''))
                self.send(200,result)
                return
            if path == '/api/session':
                require(isinstance(value.get('token'), str) and hmac.compare_digest(value['token'], self.server.token), '请使用本次工作台启动入口')
                self.send(200, {'ok':True}, headers={'Set-Cookie':f'{self.server.cookie_name}={self.server.token}; HttpOnly; SameSite=Strict; Path=/'})
                return
            require(self.authenticated() and hmac.compare_digest(self.headers.get('X-Workbench-CSRF',''),self.server.csrf), '页面会话已变化，请重新打开工作台')
            app = self.server.app
            if path == '/api/stop':
                require(not value, '退出工作台不接受其他参数')
                require(not any(app.service.active_jobs(row['case_id']) for row in app.service.cases()['cases']), '仍有任务正在处理，请保留工作台，结束后再退出服务')
                require(app.in_flight == 0, '仍有操作正在派发，请先查看原任务')
                self.send(200, {'ok':True,'note':'工作台服务已请求退出；本机资料和原任务保留。'})
                threading.Thread(target=self.server.shutdown,daemon=True).start()
                return
            if path in {'/api/prepare','/api/execute'}:
                require(self.server.code_identity == code_identity(), '程序已更新，请先在“连接与帮助”退出工作台服务，再从固定入口重新打开。原任务和资料已保留。')
            if path == '/api/read':
                result = app.read(value.get('tool'), value.get('arguments', {}))
            elif path == '/api/prepare':
                result = app.prepare(value.get('tool'), value.get('arguments', {}))
            elif path == '/api/execute':
                require(set(value) == {'intent_id'}, '确认只接受原操作标识，不接受替换参数')
                result = app.execute(value['intent_id'])
            else:
                self.send(404, {'ok':False,'error':'没有这个业务入口'})
                return
            self.send(200, result)
        except (ValueError,OSError,KeyError,TypeError) as error:
            self.send(400, {'ok':False,'error_code':getattr(error,'code','workbench_input'),'error':str(error), 'safe_to_retry':False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--open', action='store_true')
    args = parser.parse_args()
    require(0 <= args.port <= 65535, '端口须为0至65535')
    from workspace_config import read_installation
    installation = read_installation(ROOT)
    material = args.data_dir or (Path(installation['material_root']) if installation else ROOT/'materials')
    reject_links(material)
    material.mkdir(parents=True, exist_ok=True)
    from material_review_launch import launch_lock
    from platform_runtime import process_alive
    service = AssistantService(ROOT, [material.resolve()])
    record = ROOT/'.local/operator-workbench/server.json'
    record.parent.mkdir(parents=True, exist_ok=True)
    private_path(record.parent)
    with launch_lock(record):
        if record.exists():
            reject_links(record)
            old = read_json(record)
            if process_alive(old.get('pid')) is True:
                require(old.get('material_root') == str(material.resolve()), '已有工作台使用另一资料目录，请先结束原工作台')
                origin = old.get('origin','')
                require(re.fullmatch(r'http://127\.0\.0\.1:[0-9]{1,5}', origin), '原工作台登记地址无效')
                try:
                    # 探测固定回环服务，不继承外部HTTP代理。
                    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(origin+'/health',timeout=2) as response:
                        health = json.load(response)
                except (OSError,ValueError) as error:
                    raise RuntimeError('原工作台仍在运行但未响应，请检查原任务；未创建第二个服务') from error
                require(health.get('instance_id') == old.get('instance_id'), '原端口已不是登记工作台，不接管其他服务')
                require(health.get('code_identity') == code_identity(), '程序已更新，原工作台仍运行旧版本。请关闭原工作台服务后重新打开；原任务和资料保持在本机。')
                if args.open:
                    webbrowser.open(old['url'])
                print(json.dumps({'ok':True,'reused':True,'origin':origin},ensure_ascii=False))
                return
        server = Server(service, args.port)
        runtime = {'schema':'ustc-operator-workbench-v1', 'pid':os.getpid(), 'origin':server.origin,
                   'instance_id':server.instance_id, 'url':server.origin+'/#launch='+server.token,
                   'code_identity':server.code_identity,
                   'material_root':str(material.resolve())}
        atomic_json(record, runtime)
    if args.open:
        webbrowser.open(runtime['url'])
    print(json.dumps({'ok':True,'origin':server.origin,'runtime_record':str(record)},ensure_ascii=False),flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if record.exists() and read_json(record).get('pid') == os.getpid():
            record.unlink()


if __name__ == '__main__':
    try:main()
    except Exception as error:
        import sys
        if sys.platform=='win32' and '--open' in sys.argv:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,'工作台暂未启动：\n'+str(error)+'\n\n资料和原任务均已保留。','报销工作台',0x10)
        raise
