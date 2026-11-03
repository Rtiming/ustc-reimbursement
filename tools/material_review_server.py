"""Loopback-only review UI. Registered files only; no arbitrary path or finance API."""
import hmac
import io
import json
import mimetypes
import secrets
import os
import shutil
import threading
import time
import re
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit
from material_review_model import ReviewError, asset_path, load, require
from material_review_store import MAX_ATTACHMENT, append, state
from material_review_export import export_package

UI = Path(__file__).with_name('material_review_ui')


def preview(data, asset, workspace, page=1, box=None, highlight=None):
    path=asset_path(data,asset,workspace)
    require(type(page) is int and 1<=page<=asset['pages'],'材料页码越界')
    from PIL import Image, ImageOps, ImageDraw
    from material_review_model import validate_region
    if asset['suffix']=='.pdf':
        import fitz
        with fitz.open(path) as pdf:
            p=pdf[page-1];scale=min(2,1600/max(p.rect.width,p.rect.height))
            pix=p.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False)
            image=Image.frombytes('RGB',[pix.width,pix.height],pix.samples)
    elif asset['suffix'] in {'.jpg','.jpeg','.png','.webp','.bmp','.tif','.tiff'}:
        with Image.open(path) as im:image=ImageOps.exif_transpose(im).convert('RGB')
    else:raise ReviewError('此格式请下载原件查看')
    require(not (box is not None and highlight is not None), '裁切与整页圈注须分别请求')
    for region in (box, highlight):
        if region is not None:validate_region({'box':region,'basis':'查看已指定区域'})
    if highlight is not None:
        x,y,w,h=highlight
        width,height=image.size
        ImageDraw.Draw(image).rectangle((x*width,y*height,(x+w)*width,(y+h)*height),outline=(210,44,44),width=max(2,round(width/350)))
    if box is not None:
        x,y,w,h=box
        require(0<=x<1 and 0<=y<1 and 0<w<=1-x+1e-9 and 0<h<=1-y+1e-9,'局部坐标越界')
        width,height=image.size
        image=image.crop((round(x*width),round(y*height),round((x+w)*width),round((y+h)*height)))
    rotation=asset.get('rotation',0)
    require(rotation in (0,90,180,270),'预览旋转必须为90度倍数')
    if rotation:image=image.rotate(rotation,expand=True)
    image.thumbnail((1600,1600))
    out=io.BytesIO();image.save(out,'JPEG',quality=88)
    return out.getvalue()


class ReviewServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,workspace,port=0):
        self.workspace=Path(workspace).resolve();load(self.workspace)
        from material_review_launch import code_identity
        self.code_identity=code_identity()
        self.token=secrets.token_urlsafe(32);self.mutation_lock=threading.Lock();self.closing=False
        super().__init__(('127.0.0.1',port),Handler)
        self.origin=f'http://127.0.0.1:{self.server_port}'
        self.cookie_name=f'review_session_{self.server_port}'
        self.views={};self.view_lock=threading.Lock()

    def active_views(self):
        with self.view_lock:
            self.views={key:seen for key,seen in self.views.items() if time.monotonic()-seen < 75}
            return len(self.views)

    def view_event(self,request):
        require(set(request)=={'view_id','action'},'页面连接参数无效')
        key=request.get('view_id');action=request.get('action')
        require(isinstance(key,str) and re.fullmatch('[a-f0-9-]{36}',key),'页面标识无效')
        require(action in ('ping','close'),'页面动作无效')
        self.active_views()
        with self.view_lock:
            require(len(self.views)<100 or key in self.views,'已打开页面过多，请先关闭旧页面')
            if action=='close':self.views.pop(key,None)
            else:self.views[key]=time.monotonic()
        return {'ok':True,'active_views':self.active_views(),'system_write':False}


class Handler(BaseHTTPRequestHandler):
    server_version='MaterialReview/1'
    def log_message(self,*args):pass
    def response_headers(self,code,length,kind,headers=None):
        self.send_response(code)
        self.send_header('Content-Type',kind);self.send_header('Content-Length',str(length))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob:; object-src 'none'; frame-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for key,value in (headers or {}).items():self.send_header(key,value)
        self.end_headers()
    def send(self,code,body,kind='application/json; charset=utf-8',headers=None):
        if isinstance(body,(dict,list)):body=json.dumps(body,ensure_ascii=False).encode()
        if isinstance(body,str):body=body.encode()
        self.response_headers(code,len(body),kind,headers);self.wfile.write(body)
    def send_file(self,path,kind,filename):
        # Full handoffs can contain hundreds of MB; stream instead of duplicating in RAM.
        with Path(path).open('rb') as stream:
            self.response_headers(200,os.fstat(stream.fileno()).st_size,kind,
                {'Content-Disposition':"attachment; filename*=UTF-8''"+quote(filename,safe='')})
            shutil.copyfileobj(stream,self.wfile,length=1024*1024)
    def host_ok(self):
        require(self.headers.get('Host')==f'127.0.0.1:{self.server.server_port}','工作台仅接受本机固定地址')
    def authorized(self):
        cookie=SimpleCookie();cookie.load(self.headers.get('Cookie',''))
        candidate=cookie.get(self.server.cookie_name)
        return candidate is not None and hmac.compare_digest(candidate.value,self.server.token)
    def dataset(self):
        data,version=load(self.server.workspace);current=state(self.server.workspace,data)
        assets={**data['assets'],**current['attachments']}
        return data,version,current,assets
    def do_GET(self):
        try:
            self.host_ok();u=urlsplit(self.path);path=u.path
            static={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}
            if path in static:
                p=UI/static[path];self.send(200,p.read_bytes(),mimetypes.guess_type(p.name)[0]+'; charset=utf-8');return
            require(self.authorized(),'请从本机启动入口打开工作台')
            if path=='/api/health':
                self.send(200,{'ok':True,'workspace':str(self.server.workspace),'code_identity':self.server.code_identity,'active_views':self.server.active_views()});return
            data,version,current,assets=self.dataset()
            if path=='/api/data':
                from material_review_workflow import summary
                from material_review_service import feedback
                self.send(200,{'data':data,'version':version,'state':current,'feedback':feedback(data,current),'workflow':summary(self.server.workspace,data,current)});return
            q=parse_qs(u.query,keep_blank_values=True)
            if path.startswith('/api/preview/') or path.startswith('/api/original/'):
                aid=path.rsplit('/',1)[1];require(aid in assets,'材料未登记')
                asset=assets[aid]
                if path.startswith('/api/preview/'):
                    page=int(q.get('page',['1'])[0]);box=[float(x) for x in q['box'][0].split(',')] if 'box' in q else None
                    highlight=[float(x) for x in q['highlight'][0].split(',')] if 'highlight' in q else None
                    self.send(200,preview(data,asset,self.server.workspace,page,box,highlight),'image/jpeg')
                else:
                    p=asset_path(data,asset,self.server.workspace)
                    self.send_file(p,'application/octet-stream',asset['label'])
                return
            if path=='/api/export':
                if 'version' in q or 'revision' in q:
                    require(q.get('version') == [version] and q.get('revision') == [str(current['revision'])],
                            '筛选后材料或答复已更新，请刷新并重新选择导出问题')
                selected=q.get('item',[])
                mode=q.get('mode',['feedback'])[0]
                issue_ids=q.get('issue')
                if mode=='pdf':
                    from material_review_pdf import export_pdf
                    out,manifest=export_pdf(self.server.workspace,selected,issue_ids,(version,current['revision']))
                elif mode=='feedback':out,manifest=export_package(self.server.workspace,selected,issue_ids,(version,current['revision']))
                else:
                    from material_review_package import export_bundle
                    out,manifest=export_bundle(self.server.workspace,selected,mode,issue_ids,(version,current['revision']))
                self.send_file(out,'application/pdf' if mode=='pdf' else 'application/zip',out.name);return
            self.send(404,{'error':'接口不存在'})
        except (BrokenPipeError,ConnectionResetError):return
        except (ReviewError,ValueError,KeyError,OSError) as exc:self.send(400,{'error':str(exc)})
    def do_POST(self):
        try:
            self.host_ok();require(self.headers.get('Origin')==self.server.origin,'来源地址不匹配')
            if self.path=='/api/session':
                require(hmac.compare_digest(self.headers.get('X-Review-Token',''),self.server.token),'工作台访问码无效')
                self.send(200,{'ok':True},headers={'Set-Cookie':f'{self.server.cookie_name}={self.server.token}; Path=/; HttpOnly; SameSite=Strict'});return
            require(self.authorized() and self.headers.get('X-Review-Action')=='1','工作台会话失效')
            if self.path=='/api/shutdown':
                with self.server.mutation_lock:
                    self.server.closing=True
                    self.send(200,{'ok':True})
                    threading.Thread(target=self.server.shutdown,daemon=True).start()
                return
            length=int(self.headers.get('Content-Length','0'))
            require(0<length<=MAX_ATTACHMENT*4//3+100000,'请求过大或为空')
            require(self.path in ('/api/events','/api/workflow/inspect','/api/views'),'接口不存在')
            request=json.loads(self.rfile.read(length))
            if self.path=='/api/views':
                self.send(200,self.server.view_event(request));return
            if self.path=='/api/workflow/inspect':
                from material_review_workflow import inspect_formal
                self.send(200,inspect_formal(self.server.workspace,request));return
            require(request.get('action')!='answers_imported','Excel读回必须经过正式冻结校验入口')
            with self.server.mutation_lock:
                require(not self.server.closing,'工作台正在停止，请重新打开后保存')
                result=append(self.server.workspace,request)
            self.send(200,{'ok':True,'revision':result['revision'],'replayed':result['replayed']})
        except (ReviewError,ValueError,KeyError,OSError) as exc:self.send(409,{'error':str(exc)})


def serve(workspace,port=8766,runtime_info=None):
    require(type(port) is int and 0<=port<=65535,'端口无效')
    server=ReviewServer(workspace,port)
    info={'url':server.origin+'/?launch='+secrets.token_hex(8)+'#access='+server.token,'workspace':str(server.workspace),'system_write':False,'pid':os.getpid(),'cookie_name':server.cookie_name,'code_identity':server.code_identity}
    if runtime_info:
        from material_review_model import atomic_json
        atomic_json(runtime_info,info);Path(runtime_info).chmod(0o600)
    print(json.dumps(info,ensure_ascii=False),flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        server.server_close()
        if runtime_info:
            from material_review_launch import remove_runtime
            remove_runtime(Path(runtime_info),info)
