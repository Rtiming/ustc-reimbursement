"""真实Windows Edge独立无登录profile验桥；不访问财务，不读取现用浏览器资料。"""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import time
import uuid
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from platform_runtime import start_command, private_path
from windows_edge_bridge import ROOT, STATE, request, status, inventory, atomic_private
from windows_bridge_setup import install


def run(output):
    if sys.platform != 'win32':
        raise ValueError('此验收仅限Windows原生Edge')
    if status().get('ready'):
        raise ValueError('现有财务桥已连接；不抢占用户会话，不启动合成profile')
    install()
    work = STATE/'smoke'/uuid.uuid4().hex
    work.mkdir(parents=True)
    private_path(work)
    extension = work/'extension'
    shutil.copytree(STATE/'package/extension', extension)
    class FixtureHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != '/reimbursement-smoke.html':
                self.send_error(404)
                return
            body = b'<!doctype html><title>USTC local bridge fixture</title><p id="fixture">synthetic-only-42</p>'
            self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), FixtureHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    fixture_url = 'http://127.0.0.1:'+str(server.server_port)+'/reimbursement-smoke.html'
    manifest_path = extension/'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['host_permissions'].append('http://127.0.0.1/*')
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    script = extension/'native_bridge.js'
    script.write_text(script.read_text(encoding='utf-8').replace('const SYNTHETIC = false;', 'const SYNTHETIC = true;').replace('const SYNTHETIC_URL = null;', 'const SYNTHETIC_URL = '+json.dumps(fixture_url)+';'), encoding='utf-8')
    browser = next((p for p in [Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)'))/'Microsoft/Edge/Application/msedge.exe', Path(os.environ.get('ProgramFiles', 'C:/Program Files'))/'Microsoft/Edge/Application/msedge.exe'] if p.is_file()), None)
    if not browser:
        raise ValueError('未找到本机Edge可执行文件')
    command = [str(browser), '--headless=new', '--no-first-run', '--no-default-browser-check',
               '--user-data-dir='+str(work/'profile'), '--disable-extensions-except='+str(extension),
               '--load-extension='+str(extension), 'about:blank']
    record = {'schema': 'ustc-windows-bridge-smoke-v1', 'ok': False, 'synthetic_profile': True,
              'real_finance_accessed': False, 'login_verified': False, 'profile_copied': False,
              'system_write': False, 'work_directory': str(work), 'started_at': time.time()}
    owner = None
    with (work/'edge.log').open('w', encoding='utf-8') as log:
        try:
            process, owner = start_command(command, cwd=ROOT, env={**os.environ, 'PYTHONUTF8': '1'}, stdout=log, stderr=log)
            deadline = time.monotonic()+30
            while not status().get('ready') and time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError('独立Edge在Native Messaging连接前退出')
                time.sleep(.15)
            bridge = status()
            if not bridge.get('ready') or not bridge.get('synthetic_profile'):
                raise RuntimeError('合成Edge扩展未接通Native Messaging；不代表真实财务不可用')
            before = inventory()
            result = request('smoke', timeout=20)
            after = inventory()
            foreground = (before['frontmost_bundle_id'], before['frontmost_process_id']) == (after['frontmost_bundle_id'], after['frontmost_process_id'])
            modes_ok = all(m['read'].get('marker') == 'synthetic-only-42' and m['creation']['old_active_tab_restored'] for m in result['modes'])
            record.update(ok=foreground and modes_ok and result['before']['windows'] == result['after']['windows'],
                          frontmost_preserved=foreground, result=result,
                          browser_version_observed=browser.stat().st_mtime, bridge_connected=True)
        except Exception as error:
            record.update(error=str(error), error_type=type(error).__name__)
        finally:
            if owner is not None:
                record['process_cleanup'] = owner.close()
                process.wait(timeout=5)
            server.shutdown()
            server.server_close()
    record['finished_at'] = time.time()
    if record.get('process_cleanup', {}).get('ok') is not True:
        record['ok'] = False
    atomic_private(output, record, exclusive=True)
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    result = run(parser.parse_args().output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['ok'] else 1)
