"""本机工作台探测不经过用户的外部代理。"""
import http.server
import json
import os
from pathlib import Path
import sys
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import material_review_launch as launch


class LoopbackProxyTests(unittest.TestCase):
    def test_health_and_page_reuse_ignore_broken_external_proxy(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({'workspace': 'fixture', 'active_views': 2}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        proxy = {key: 'http://127.0.0.1:1' for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy')}
        proxy.update(NO_PROXY='', no_proxy='')
        info = {'url': f'http://127.0.0.1:{server.server_port}/#access=synthetic',
                'workspace': 'fixture', 'code_identity': 'fixture'}
        try:
            with patch.dict(os.environ, proxy):
                with self.assertRaises(urllib.error.URLError):
                    urllib.request.build_opener().open(info['url'], timeout=1)
                self.assertTrue(launch.live(info))
                self.assertEqual(launch.active_views(info), 2)
                self.assertFalse(launch.live({**info, 'workspace': 'another'}))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
