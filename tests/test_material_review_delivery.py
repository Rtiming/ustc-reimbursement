"""A recipient must be able to move the package and lose access to the sender."""
import base64
import json
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path

import test_material_review as fixture
from material_review_model import ReviewError, asset_path, load, validate
from material_review_package import export_bundle, csv_bytes
from material_review_store import append, events, state


class DeliveryTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.ReviewTest()
        self.fixture.setUp()
        self.ws = self.fixture.ws

    def tearDown(self):
        self.fixture.tearDown()

    def unpack(self, path, name='recipient'):
        directory = self.fixture.root/name
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(len(archive.namelist()), len(set(archive.namelist())))
            archive.extractall(directory)
        return directory

    def test_materials_scope_has_no_internal_records_and_is_complete(self):
        f = self.fixture
        f.data['items']['1']['paper_ids'] = []
        f.data['items']['1']['prior_reply'] = {'raw_answers': {'text': 'PRIVATE_INTERNAL_NOTE'}}
        f.data['expenses']['E1']['payments'] = [{'name': 'PRIVATE_PAYEE'}]
        f.publish()
        append(self.ws, f.req(text='PRIVATE_JOURNAL'))
        path, manifest = export_bundle(self.ws, ['1'])
        root = self.unpack(path)
        raw = (root/'data.js').read_text()
        self.assertNotIn('PRIVATE_', raw)
        self.assertNotIn(str(f.root), raw)
        self.assertEqual(manifest['item_ids'], ['1'])
        payload = json.loads(raw.removeprefix('window.REVIEW_EXPORT=').removesuffix(';'))
        asset = payload['data']['assets'][f.aid]
        self.assertEqual((root/asset['path']).read_bytes(), f.raw)
        self.assertIn('校验包.py', [e['name'] for e in manifest['files']])
        subprocess.run([sys.executable, str(root/'校验包.py')], check=True, capture_output=True)

    def test_public_exports_use_current_instructions_without_private_arrangements(self):
        from material_review_export import export_package
        f=self.fixture
        f.data['issues']['Q1']['feedback']={'issue_fingerprint':f.data['issues']['Q1']['fingerprint'],
            'project':'测试活动','request':'只补收款分配','context':'已有行程，不重复提交',
            'response':'按材料编号填写实际收款人','evidence':[{'asset_id':f.aid,'page':1}]}
        f.publish()
        path,_=export_bundle(self.ws,['1'])
        with zipfile.ZipFile(path) as z:
            packet=json.loads(z.read('data.js').decode().removeprefix('window.REVIEW_EXPORT=').rstrip(';'))
            q=packet['data']['issues']['Q1']
            self.assertEqual(q['request'],'按材料编号填写实际收款人')
            self.assertEqual(q['feedback']['context'],'已有行程，不重复提交')
            self.assertNotIn('issue_fingerprint',q['feedback'])
            self.assertEqual(packet['feedback']['rows'][0]['next_action'],q['request'])
            self.assertIn(q['request'],z.read('问题反馈.csv').decode('utf-8-sig'))
        path,_=export_package(self.ws,['1'])
        with zipfile.ZipFile(path) as z:
            self.assertIn('按材料编号填写实际收款人',z.read('index.html').decode())
        append(self.ws,f.req('defer',stage='waiting_materials',text='PRIVATE_DEFER'))
        path,_=export_bundle(self.ws,['1'],issue_ids=['Q1'])
        with zipfile.ZipFile(path) as z:
            raw=z.read('data.js').decode();self.assertNotIn('PRIVATE_DEFER',raw)
            packet=json.loads(raw.removeprefix('window.REVIEW_EXPORT=').rstrip(';'))
            self.assertEqual(packet['data']['issues']['Q1']['feedback']['response'],'')
            self.assertIn('已登记后续',packet['data']['issues']['Q1']['feedback']['request'])

    def test_offline_zoom_includes_current_feedback_evidence(self):
        import fitz,hashlib,io
        from PIL import Image
        f=self.fixture
        with fitz.open() as doc:
            p=doc.new_page(width=200,height=100)
            p.draw_rect(fitz.Rect(100,0,200,100),color=(0,0,1),fill=(0,0,1))
            raw=doc.tobytes()
        aid=hashlib.sha256(raw).hexdigest();path=f.root/'新定位.pdf';path.write_bytes(raw)
        f.data['assets'][aid]={'id':aid,'sha256':aid,'path':str(path),'label':'新定位.pdf',
            'suffix':'.pdf','kind':'发票','pages':1,'item_ids':['1'],'origins':[],'invoice_numbers':[]}
        f.data['issues']['Q1']['feedback']={'issue_fingerprint':f.data['issues']['Q1']['fingerprint'],
            'project':'测试活动','request':'说明蓝色区域','context':'原问题未带此定位',
            'evidence':[{'asset_id':aid,'page':1,'box':[.5,0,.5,1],'basis':'合成蓝色区域'}]}
        f.publish();bundle,_=export_bundle(self.ws,['1'])
        with zipfile.ZipFile(bundle) as z:
            packet=json.loads(z.read('data.js').decode().removeprefix('window.REVIEW_EXPORT=').rstrip(';'))
            name=packet['previews'][aid+':1:0.5,0,0.5,1']
            im=Image.open(io.BytesIO(z.read(name)))
            self.assertEqual(im.width,im.height)
            red,green,blue=im.getpixel((im.width//2,im.height//2))
            self.assertLess(red+green,10);self.assertGreater(blue,240)

    def test_handoff_survives_move_missing_original_and_can_continue(self):
        f = self.fixture
        append(self.ws, f.req('attachment', file={'name': '补充.txt', 'category': '明细清单',
               'base64': base64.b64encode(b'supplement').decode()}))
        before = (self.ws/'events'/'00000001.json').read_bytes()
        path, manifest = export_bundle(self.ws, mode='handoff')
        root = self.unpack(path, 'moved elsewhere')
        f.path.unlink()  # A sender-side path must not be usable during this test.
        recipient = root/'workspace'
        data, version = load(recipient)
        validate(data)
        self.assertEqual(asset_path(data, data['assets'][f.aid], recipient).read_bytes(), f.raw)
        self.assertEqual((recipient/'events'/'00000001.json').read_bytes(), before)
        current = state(recipient)
        self.assertEqual(current['issues']['Q1']['status'], 'replied')
        for asset in current['attachments'].values():
            asset_path(data, asset, recipient)
            self.assertIn(asset['sha256'], (root/'清单'/'材料目录.csv').read_text(encoding='utf-8-sig'))
        self.assertIn('仅用于合成测试', (root/'清单'/'处理记录.csv').read_text(encoding='utf-8-sig'))
        append(recipient, {**f.req(), 'version': version, 'request_id': 'recipient-reply', 'text': '接手人答复'})
        self.assertEqual(len(events(recipient)), 2)
        self.assertEqual(len(events(self.ws)), 1)
        # Use only the shipped code, in a new cwd, to export again.
        command = [sys.executable, str(root/'review.py')]
        result = subprocess.run([*command, 'verify', '--workspace', str(recipient)],
                                cwd=root, check=True, capture_output=True, text=True)
        self.assertTrue(json.loads(result.stdout)['ok'])
        result = subprocess.run([*command, 'export', '--mode', 'handoff', '--workspace', str(recipient)],
                                cwd=root, check=True, capture_output=True, text=True)
        self.assertTrue(Path(json.loads(result.stdout)['path']).is_file())
        self.assertFalse(any('.local/' in e['name'] or '.venv/' in e['name'] for e in manifest['files']))

    def test_public_progress_preserved_without_private_reply_or_defer_text(self):
        f = self.fixture
        for action, status in [('reply', 'replied'), ('defer', 'waiting_materials'),
                               ('defer', 'before_submit'), ('defer', 'hold')]:
            append(self.ws, f.req(action, stage=status, text='PRIVATE_OPERATOR_TEXT'))
            path, _ = export_bundle(self.ws, ['1'], issue_ids=['Q1'])
            with zipfile.ZipFile(path) as archive:
                raw = archive.read('data.js').decode()
                packet = json.loads(raw.removeprefix('window.REVIEW_EXPORT=').removesuffix(';'))
                self.assertNotIn('PRIVATE_OPERATOR_TEXT', raw)
                self.assertEqual(packet['state']['issues']['Q1']['status'], status)
                self.assertIn('原始缺项', packet['data']['issues']['Q1']['request'])
                self.assertIn(status, archive.read('清单/问题清单.csv').decode('utf-8-sig'))
                self.assertNotIn('PRIVATE_OPERATOR_TEXT', archive.read('清单/处理记录.csv').decode('utf-8-sig'))

    def test_issue_selection_is_exact_and_default_omits_replies(self):
        import copy
        from material_review_export import export_package
        from material_review_model import digest
        f = self.fixture
        q2 = copy.deepcopy(f.data['issues']['Q1'])
        q2.update(id='Q2', title='另一条问题')
        q2['fingerprint'] = digest(q2)
        f.data['issues']['Q2'] = q2
        f.data['items']['1']['issue_ids'].append('Q2')
        f.data['counts']['issues'] = 2
        f.publish()
        append(self.ws, f.req('reply'))
        for exporter in (export_bundle, export_package):
            _, manifest = exporter(self.ws, ['1'])
            self.assertEqual(manifest['issue_ids'], ['Q2'])
            _, manifest = exporter(self.ws, ['1'], issue_ids=['Q1'])
            self.assertEqual(manifest['issue_ids'], ['Q1'])
            for selected in ([], ['unknown']):
                with self.assertRaises(ReviewError):
                    exporter(self.ws, ['1'], issue_ids=selected)
            with self.assertRaises(ReviewError):
                exporter(self.ws, ['1'], issue_ids=['Q2'], expected_snapshot=(f.version, 0))
        with self.assertRaises(ReviewError):
            export_bundle(self.ws, mode='handoff', issue_ids=['Q1'])

    def test_handoff_launch_reuse_stop_and_restart(self):
        path, _ = export_bundle(self.ws, mode='handoff')
        root = self.unpack(path)
        command = [sys.executable, str(root/'review.py')]
        def run(action):
            r = subprocess.run([*command, action, '--workspace', str(root/'workspace'), '--port', '0'],
                               cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=15)
            self.assertEqual(r.returncode,0,{'action':action,'stderr':r.stderr,'stdout':r.stdout})
            return json.loads(r.stdout)
        try:
            first = run('start')
            self.assertFalse(first['reused'])
            self.assertEqual(run('start')['url'], first['url'])
            self.assertTrue(run('status')['running'])
            self.assertTrue(run('stop')['stopped'])
            self.assertFalse(run('status')['running'])
            second = run('start')
            self.assertFalse(second['reused'])
            from urllib.parse import urlsplit
            self.assertNotEqual(urlsplit(first['url']).query,urlsplit(second['url']).query)
            self.assertIn('launch=',urlsplit(second['url']).query)
        finally:
            run('stop')

    def test_concurrent_runtime_cleanup_preserves_restarted_instance(self):
        from concurrent.futures import ThreadPoolExecutor
        from material_review_launch import remove_runtime
        from material_review_model import atomic_json
        import tempfile
        with tempfile.TemporaryDirectory()as directory:
            record=Path(directory)/'runtime.json'
            old={'pid':101,'url':'http://127.0.0.1:1/#access=old','workspace':directory}
            atomic_json(record,old)
            with ThreadPoolExecutor(max_workers=2)as pool:
                values=list(pool.map(lambda _:remove_runtime(record,old),range(2)))
            self.assertEqual(sorted(values),[False,True]);self.assertFalse(record.exists())
            new={**old,'url':'http://127.0.0.1:1/#access=new'};atomic_json(record,new)
            self.assertFalse(remove_runtime(record,old))
            self.assertEqual(json.loads(record.read_text()),new)

    def test_changed_runtime_requires_restart_and_parallel_open_reuses(self):
        from concurrent.futures import ThreadPoolExecutor
        path, _ = export_bundle(self.ws, mode='handoff')
        root = self.unpack(path)
        command = [sys.executable, str(root/'review.py')]
        def run(action):
            return subprocess.run([*command, action, '--workspace', str(root/'workspace'), '--port', '0'],
                                  cwd=root, capture_output=True, text=True, timeout=15)
        try:
            with ThreadPoolExecutor(2) as executor:
                replies = list(executor.map(run, ['start', 'start']))
            self.assertTrue(all(r.returncode == 0 for r in replies), [r.stderr for r in replies])
            parsed = [json.loads(r.stdout) for r in replies]
            self.assertEqual(parsed[0]['url'], parsed[1]['url'])
            self.assertEqual(sorted(r['reused'] for r in parsed), [False, True])
            path = root/'tools'/'material_review_ui'/'app.js'
            path.write_text(path.read_text()+'\n// changed runtime fixture\n')
            self.assertNotEqual(run('start').returncode, 0)
            self.assertFalse(json.loads(run('status').stdout)['code_current'])
        finally:
            run('stop')

    def test_pdf_pages_and_locations_are_exported(self):
        import fitz
        f = self.fixture
        with fitz.open() as doc:
            doc.new_page().insert_text((30, 30), 'PAGE ONE')
            doc.new_page().insert_text((30, 30), 'PAGE TWO')
            raw = doc.tobytes()
        append(self.ws, f.req('attachment', file={'name': 'two #pages.pdf',
               'base64': base64.b64encode(raw).decode()}))
        asset = next(iter(state(self.ws)['attachments'].values()))
        append(self.ws, f.req('link', target={'asset_id': asset['id'], 'page': 2}))
        path, manifest = export_bundle(self.ws, ['1'])
        root = self.unpack(path)
        packet = json.loads((root/'data.js').read_text().removeprefix('window.REVIEW_EXPORT=').removesuffix(';'))
        self.assertIn(asset['id']+':2', packet['previews'])
        for name in packet['previews'].values():
            self.assertTrue((root/name).read_bytes().startswith(b'\xff\xd8'))
        self.assertEqual(len([e for e in manifest['files'] if e.get('role') == 'preview']), 2)

    def test_partial_handoff_and_unknown_scope_rejected(self):
        with self.assertRaises(ReviewError):
            export_bundle(self.ws, ['1'], 'handoff')
        for ids in (None, [], ['unknown']):
            with self.assertRaises(ReviewError):
                export_bundle(self.ws, ids)

    def test_portable_root_escape_and_changed_bytes_rejected(self):
        path, _ = export_bundle(self.ws, mode='handoff')
        root = self.unpack(path)
        workspace = root/'workspace'
        data, _ = load(workspace)
        asset = data['assets'][self.fixture.aid]
        original = asset_path(data, asset, workspace)
        original.write_bytes(b'changed')
        with self.assertRaises(ReviewError):
            asset_path(data, asset, workspace)
        for name in ('../private', '/outside', 'originals/../../private', 'originals\\..\\private'):
            asset['path'] = name
            with self.assertRaises(ReviewError):
                validate(data)

    def test_corrupt_package_check_fails(self):
        path, _ = export_bundle(self.ws, ['1'])
        root = self.unpack(path)
        (root/'app.js').write_text('bad')
        r = subprocess.run([sys.executable, str(root/'校验包.py')], capture_output=True)
        self.assertNotEqual(r.returncode, 0)

    def test_shared_material_not_leaked_and_export_not_left_half_written(self):
        f = self.fixture
        f.data['items']['2'] = {**f.data['items']['1'], 'id': '2', 'issue_ids': [], 'expense_ids': []}
        f.data['assets'][f.aid]['item_ids'].append('2')
        f.publish()
        path, manifest = export_bundle(self.ws, ['1'])
        self.assertEqual(len(manifest['omitted']), 1)
        self.assertEqual(manifest['counts']['assets'], 0)
        f.path.write_bytes(b'changed original')
        before = set((self.ws/'exports').iterdir())
        with self.assertRaises(ReviewError):
            export_bundle(self.ws, mode='handoff')
        self.assertEqual(set((self.ws/'exports').iterdir()), before)

    def test_csv_does_not_execute_labels_as_formulas(self):
        self.assertIn("'=cmd", csv_bytes([['=cmd']]).decode('utf-8-sig'))


if __name__ == '__main__':
    unittest.main()
