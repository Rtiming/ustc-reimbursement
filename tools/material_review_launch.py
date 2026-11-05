"""On-demand local launcher. Runtime access state stays outside case synchronization."""
import http.cookiejar
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
import importlib.metadata
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs,urlsplit
from material_review_model import digest,load,read_json,require,sha,ReviewError

ROOT=Path(__file__).resolve().parents[1]


def read_runtime(path):
    # The server removes its own record after shutdown; disappearance is expected.
    deadline=time.monotonic()+1
    while True:
        try:return read_json(path)
        except FileNotFoundError:return {}
        except PermissionError as error:
            # Windows中删除待完成文件会短暂返回ACCESS_DENIED；真实权限失败仍向上报告。
            if os.name!='nt' or getattr(error,'winerror',None) not in {5,32,33} or time.monotonic()>=deadline:raise
            time.sleep(.025)


def remove_runtime(record, expected):
    """启动、停止和服务器退出共用锁，旧进程不能删除新实例的记录。"""
    with launch_lock(record):
        deadline=time.monotonic()+1
        while True:
            current=read_runtime(record)
            if any(current.get(k)!=expected.get(k) for k in ('pid','url','workspace')):return False
            try:
                record.unlink(missing_ok=True);return True
            except PermissionError as error:
                if os.name!='nt' or getattr(error,'winerror',None)not in {5,32,33} or time.monotonic()>=deadline:raise
                time.sleep(.025)


@contextmanager
def launch_lock(record):
    """OS-released lock: simultaneous opens cannot dispatch two background servers."""
    with record.with_suffix('.lock').open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            stream.seek(0,2)
            if stream.tell()==0:stream.write(b'0');stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(),msvcrt.LK_LOCK,1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX)
        try:yield
        finally:
            if os.name=='nt':
                stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


def code_identity():
    paths = sorted((ROOT/'tools').glob('material_review_*.py'))
    paths += [ROOT/'tools/platform_runtime.py']
    paths += sorted((ROOT/'tools'/'material_review_ui').glob('*'))
    return digest({str(p.relative_to(ROOT)):sha(p) for p in paths if p.is_file()})


def doctor(workspace):
    import tempfile
    data,version=load(workspace)
    require(sys.version_info >= (3,10), '需要Python3.10以上')
    dependencies={}
    for name,module in [('PyMuPDF','fitz'),('Pillow','PIL')]:
        try:
            __import__(module)
            dependencies[name]=importlib.metadata.version(name)
        except (ImportError,importlib.metadata.PackageNotFoundError) as exc:
            raise ReviewError('缺少材料依赖，请使用当前Python安装requirements-materials.lock.txt：'+name) from exc
    with tempfile.TemporaryFile(dir=workspace) as stream:stream.write(b'write probe')
    return {'ok':True,'python':sys.version.split()[0],'dependencies':dependencies,'version':version,
            'storage':data.get('storage','source_roots'),'writable':True,'system_write':False}


def status(workspace):
    workspace=Path(workspace).resolve();load(workspace)
    record=runtime_file(workspace)
    info=read_runtime(record)
    running=info.get('workspace')==str(workspace) and live(info)
    return {'ok':True,'running':bool(running),'pid':info.get('pid') if running else None,
            'code_current':info.get('code_identity')==code_identity() if running else None,
            'workspace':str(workspace),'active_views':active_views(info) if running else 0,'system_write':False}


def stop(workspace):
    record=runtime_file(workspace)
    info=read_runtime(record)
    if info.get('workspace')!=str(Path(workspace).resolve()) or not live(info):
        return {'ok':True,'stopped':False,'running':False}
    u=urlsplit(info['url']);origin=f'http://127.0.0.1:{u.port}'
    token=parse_qs(u.fragment)['access'][0]
    req=urllib.request.Request(origin+'/api/shutdown',data=b'{}',headers={
        'Cookie':info.get('cookie_name','review_session')+'='+token,'Origin':origin,'X-Review-Action':'1','Content-Type':'application/json'})
    # 本机访问码不交给用户的外部代理。
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=3) as response:
        require(json.load(response).get('ok'),'工作台未确认停止')
    deadline=time.monotonic()+5
    cleaned=False
    while time.monotonic()<deadline:
        if not live(info):
            if not cleaned:
                remove_runtime(record,info)
                cleaned=True
            if os.name=='nt':
                from platform_runtime import process_alive
                if process_alive(info.get('pid')) is not False:
                    time.sleep(.05)
                    continue
            return {'ok':True,'stopped':True,'running':False,'system_write':False}
        time.sleep(.1)
    raise ReviewError('停止尚未读回，请核对status；不要按旧PID强制终止其他进程')


def runtime_file(workspace):
    root=ROOT/'.local'/'review-runtime';root.mkdir(parents=True,exist_ok=True)
    return root/(digest(str(Path(workspace).resolve()))[:20]+'.json')


def live(info):
    try:
        u=urlsplit(info['url'])
        require(u.scheme=='http' and u.hostname=='127.0.0.1','运行记录不是本机地址')
        token=parse_qs(u.fragment)['access'][0]
        route='/api/health' if info.get('code_identity') else '/api/data'
        req=urllib.request.Request(f'http://127.0.0.1:{u.port}'+route,headers={'Cookie':info.get('cookie_name','review_session')+'='+token})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=2) as r:
            return r.status==200 and (route=='/api/data' or json.load(r).get('workspace')==info['workspace'])
    except (OSError,KeyError,ValueError,urllib.error.URLError):return False


def active_views(info):
    try:
        u=urlsplit(info['url']);token=parse_qs(u.fragment)['access'][0]
        req=urllib.request.Request(f'http://127.0.0.1:{u.port}/api/health',headers={
            'Cookie':info.get('cookie_name','review_session')+'='+token})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=2) as response:
            data=json.load(response)
        require(data.get('workspace')==info['workspace'],'页面所属工作台变化')
        return int(data.get('active_views',0))
    except (OSError,ValueError,KeyError):return 0


def launch(workspace,port=8766,open_browser=False):
    with launch_lock(runtime_file(workspace)):
        return _launch(workspace,port,open_browser)


def _launch(workspace,port,open_browser):
    workspace=Path(workspace).resolve();load(workspace)
    doctor(workspace)
    record=runtime_file(workspace)
    if record.is_file():
        info=read_runtime(record)
        if info.get('workspace')==str(workspace) and live(info):
            require(info.get('code_identity')==code_identity(),'工作台代码已更新；请先stop，再open加载当前版本')
            active=active_views(info)
            opened=bool(open_browser and not active)
            if opened:webbrowser.open(info['url'])
            return {'ok':True,'reused':True,'url':info['url'],'workspace':str(workspace),
                    'active_views':active,'browser_opened':opened,
                    'next_action':'已有工作台页面，复用现有标签；无需重复打开' if active else '使用返回入口进入工作台'}
        record.unlink(missing_ok=True)
    log=record.with_suffix('.log')
    fd=os.open(log,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    with os.fdopen(fd,'ab') as stream:
        entry=[str(ROOT/'assistant.py'),'material-review'] if (ROOT/'assistant.py').is_file() else [str(ROOT/'review.py')]
        process=subprocess.Popen([sys.executable,*entry,'serve',
            '--workspace',str(workspace),'--port',str(port),'--runtime-info',str(record)],
            stdin=subprocess.DEVNULL,stdout=stream,stderr=stream,cwd=ROOT,start_new_session=True)
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        if record.is_file():
            info=read_runtime(record)
            if live(info):
                if open_browser:webbrowser.open(info['url'])
                return {'ok':True,'reused':False,'url':info['url'],'workspace':str(workspace),'pid':process.pid}
        require(process.poll() is None,f'工作台启动失败；请查看本机日志：{log}')
        time.sleep(.1)
    raise TimeoutError(f'工作台启动尚未确认，请先查看{log}，不要重复启动')
