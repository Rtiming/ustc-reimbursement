import base64
import hashlib
import io
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from material_review_model import SCHEMA, ReviewError, atomic_json, digest, load, validate
from material_review_store import append, events, state
from material_review_server import ReviewServer, preview
from material_review_export import export_package


class ReviewTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.ws=self.root/'workspace'
        self.raw=b'fixture original';self.path=self.root/'evidence.txt';self.path.write_bytes(self.raw)
        self.aid=hashlib.sha256(self.raw).hexdigest()
        issue={'id':'Q1','item_id':'1','title':'金额关系待说明','request':'请说明差额','known':'原材料已收到',
               'requirements':['核实身份','核实差额'],'targets':[{'asset_id':self.aid,'page':1}],
               'refs':[],'sendable':True,'status':'pending','queue':'待说明','club':'示例社团'}
        issue['fingerprint']=digest(issue)
        self.data={'schema':SCHEMA,'title':'合成测试','batch_id':'synthetic','roots':[str(self.root)],'sources':{},
           'items':{'1':{'id':'1','title':'活动甲','club':'示例社团','asset_ids':[self.aid],'expense_ids':['E1'],'issue_ids':['Q1']}},
           'assets':{self.aid:{'id':self.aid,'sha256':self.aid,'path':str(self.path),'label':'证据.txt','suffix':'.txt','kind':'辅助材料','pages':1,'item_ids':['1'],'origins':[],'invoice_numbers':[]}},
           'expenses':{'E1':{'id':'E1','item_id':'1','refs':[{'asset_id':self.aid,'page':1}]}},'issues':{'Q1':issue},'counts':{'items':1,'assets':1,'issues':1,'expenses':1}}
        self.publish()
    def tearDown(self):self.temp.cleanup()
    def publish(self):
        self.version=digest(self.data);atomic_json(self.ws/'versions'/f'{self.version}.json',self.data)
        atomic_json(self.ws/'current.json',{'version':self.version})
    def req(self,action='reply',**kwargs):
        return {'request_id':'request-'+str(len(events(self.ws))), 'version':self.version,'revision':len(events(self.ws)),
                'actor':'合成测试记录人','issue_id':'Q1','action':action,'text':'仅用于合成测试',**kwargs}
    def test_page_leases_expire_close_and_never_append_business_events(self):
        from unittest.mock import patch
        import material_review_launch as launch
        from material_review_model import atomic_json
        server=ReviewServer(self.ws,0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        key='11111111-1111-1111-1111-111111111111'
        try:
            origin=server.origin
            headers={'Cookie':server.cookie_name+'='+server.token,'Origin':origin,'X-Review-Action':'1','Content-Type':'application/json'}
            def post(action):
                req=urllib.request.Request(origin+'/api/views',data=json.dumps({'view_id':key,'action':action}).encode(),headers=headers)
                with urllib.request.urlopen(req) as r:return json.load(r)
            self.assertEqual(post('ping')['active_views'],1)
            self.assertEqual(post('ping')['active_views'],1)
            self.assertEqual(events(self.ws),[])
            with patch.object(launch,'ROOT',self.root),patch.object(launch,'code_identity',return_value=server.code_identity),patch.object(launch.webbrowser,'open') as opened:
                atomic_json(launch.runtime_file(self.ws),{'url':origin+'/?launch=fixture#access='+server.token,'workspace':str(self.ws.resolve()),'cookie_name':server.cookie_name,'code_identity':server.code_identity})
                result=launch.launch(self.ws,0,True)
                self.assertFalse(result['browser_opened']);self.assertEqual(result['active_views'],1);opened.assert_not_called()
                self.assertEqual(post('close')['active_views'],0)
                result=launch.launch(self.ws,0,True)
                self.assertTrue(result['browser_opened']);opened.assert_called_once()
            post('ping');server.views[key]-=76
            self.assertEqual(server.active_views(),0)
            with self.assertRaises(urllib.error.HTTPError):post('kill')
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_two_workspace_sessions_do_not_replace_each_other(self):
        servers=[ReviewServer(self.ws,0),ReviewServer(self.ws,0)]
        threads=[threading.Thread(target=s.serve_forever,daemon=True) for s in servers]
        for t in threads:t.start()
        try:
            self.assertNotEqual(servers[0].cookie_name,servers[1].cookie_name)
            cookies='; '.join(s.cookie_name+'='+s.token for s in servers)
            for server in servers:
                req=urllib.request.Request(server.origin+'/api/health',headers={'Cookie':cookies})
                with urllib.request.urlopen(req) as response:self.assertTrue(json.load(response)['ok'])
            wrong=urllib.request.Request(servers[0].origin+'/api/health',headers={'Cookie':servers[1].cookie_name+'='+servers[1].token})
            with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(wrong)
        finally:
            for server in servers:server.shutdown();server.server_close()
            for t in threads:t.join()

    def test_contract_and_snapshot_tamper(self):
        self.assertTrue(validate(self.data));load(self.ws)
        path=self.ws/'versions'/f'{self.version}.json';d=json.loads(path.read_text());d['title']='tampered';atomic_json(path,d)
        with self.assertRaises(ReviewError):load(self.ws)
    def test_cross_item_and_page_rejected(self):
        self.data['issues']['Q1']['targets'][0]['page']=2
        with self.assertRaises(ReviewError):validate(self.data)
        self.data['issues']['Q1']['targets'][0]['page']=1;self.data['assets'][self.aid]['item_ids']=[]
        with self.assertRaises(ReviewError):validate(self.data)
    def test_root_escape_rejected(self):
        self.data['assets'][self.aid]['path']='/outside/private.txt'
        with self.assertRaises(ReviewError):validate(self.data)
    def test_reply_persists_and_retry_is_idempotent(self):
        req=self.req();r=append(self.ws,req);self.assertEqual(r['revision'],1)
        self.assertTrue(append(self.ws,req)['replayed']);self.assertEqual(len(events(self.ws)),1)
        self.assertEqual(state(self.ws)['issues']['Q1']['status'],'replied')
        with self.assertRaises(ReviewError):append(self.ws,{**req,'text':'changed'})
    def test_concurrent_stale_revision_does_not_overwrite(self):
        req=self.req();append(self.ws,req)
        with self.assertRaises(ReviewError):append(self.ws,{**req,'request_id':'another-request'})
        self.assertEqual(len(events(self.ws)),1)
    def test_actor_optional_preserves_idempotency(self):
        req=self.req();req.pop('actor')
        self.assertEqual(append(self.ws,req)['event']['actor'],'未署名')
        self.assertTrue(append(self.ws,req)['replayed'])
        for actor in [None,123,'x'*101]:
            with self.assertRaises(ReviewError):append(self.ws,self.req(actor=actor))
    def test_partial_review_not_closed(self):
        for checks,evidence in [([True,False],[{'asset_id':self.aid,'page':1}]),([True,True],[])]:
            with self.assertRaises(ReviewError):append(self.ws,self.req('review',checks=checks,evidence=evidence))
        append(self.ws,self.req('review',checks=[True,True],evidence=[{'asset_id':self.aid,'page':1}]))
        self.assertEqual(state(self.ws)['issues']['Q1']['status'],'reviewed')
        append(self.ws,self.req('reopen'));self.assertEqual(state(self.ws)['issues']['Q1']['status'],'pending')
    def test_source_change_retains_history_but_not_old_review(self):
        append(self.ws,self.req('review',checks=[True,True],evidence=[{'asset_id':self.aid,'page':1}]))
        self.data['issues']['Q1']['fingerprint']=digest('changed conditions');self.publish()
        s=state(self.ws)['issues']['Q1'];self.assertEqual(s['status'],'pending');self.assertEqual(len(s['events']),1);self.assertEqual(s['stale_events'],[1])
    def test_attachment_bytes_and_reply_state(self):
        raw=b'supplemental fixture';req=self.req('attachment',file={'name':'../补充说明.txt','base64':base64.b64encode(raw).decode()})
        append(self.ws,req);s=state(self.ws);self.assertEqual(s['issues']['Q1']['status'],'replied')
        a=next(iter(s['attachments'].values()));self.assertEqual(a['label'],'补充说明.txt');self.assertEqual((self.ws/a['path']).read_bytes(),raw)
    def test_invalid_and_oversize_attachment_rejected(self):
        for file in [{'name':'x.html','base64':'eA=='},{'name':'x.pdf','base64':'eA=='},{'name':'x.txt','base64':'!!!'}]:
            with self.assertRaises((ReviewError,ValueError)):append(self.ws,self.req('attachment',file=file))
        self.assertEqual(events(self.ws),[])
    def test_same_attachment_across_items_keeps_both_owners(self):
        self.data['items']['2']={**self.data['items']['1'],'id':'2','issue_ids':['Q2'],'expense_ids':[]}
        self.data['assets'][self.aid]['item_ids'].append('2');self.data['issues']['Q2']={**self.data['issues']['Q1'],'id':'Q2','item_id':'2'};self.publish()
        file={'name':'新增.txt','base64':base64.b64encode(b'new file').decode()}
        append(self.ws,self.req('attachment',file=file));append(self.ws,self.req('attachment',issue_id='Q2',file=file))
        self.assertEqual(next(iter(state(self.ws)['attachments'].values()))['item_ids'],['1','2'])
    def test_event_tamper_and_missing_sequence_rejected(self):
        append(self.ws,self.req());p=self.ws/'events'/'00000001.json';e=json.loads(p.read_text());e['text']='changed';atomic_json(p,e)
        with self.assertRaises(ReviewError):events(self.ws)
    def test_export_is_scoped_and_excludes_reviewed(self):
        out,m=export_package(self.ws,['1']);self.assertEqual(m['issue_ids'],['Q1']);self.assertFalse(m['messages_sent'])
        with zipfile.ZipFile(out) as z:self.assertIn('反馈说明.md',z.namelist());self.assertEqual(len(m['files']),1)
        append(self.ws,self.req('review',checks=[True,True],evidence=[{'asset_id':self.aid,'page':1}]))
        _,m=export_package(self.ws,['1']);self.assertEqual(m['issue_ids'],[])
    def test_shared_asset_not_sent_outside_scope(self):
        self.data['assets'][self.aid]['item_ids'].append('2');self.publish()
        _,m=export_package(self.ws,['1']);self.assertEqual(m['files'],[]);self.assertEqual(len(m['omitted']),1)
    def test_original_drift_rejected(self):
        self.path.write_text('changed')
        with self.assertRaises(ReviewError):export_package(self.ws,['1'])
        with self.assertRaises(ReviewError):append(self.ws,self.req('review',checks=[True,True],evidence=[{'asset_id':self.aid,'page':1}]))
    def test_pdf_upload_keeps_pages_and_previews_selected_page(self):
        import importlib.util
        if not importlib.util.find_spec('fitz') or not importlib.util.find_spec('PIL'):
            self.skipTest('requires configured material dependencies')
        import fitz
        with fitz.open() as doc:
            doc.new_page().insert_text((30,30),'SYNTHETIC PAGE ONE')
            doc.new_page().insert_text((30,30),'SYNTHETIC PAGE TWO')
            raw=doc.tobytes()
        append(self.ws,self.req('attachment',file={'name':'two-pages.pdf','category':'明细清单','base64':base64.b64encode(raw).decode()}))
        a=next(iter(state(self.ws)['attachments'].values()))
        self.assertEqual(a['pages'],2);self.assertTrue(preview(self.data,a,self.ws,2).startswith(bytes([255,216])))
        with self.assertRaises(ReviewError):append(self.ws,self.req('attachment',file={'name':'broken.pdf','base64':base64.b64encode(b'%PDF-broken').decode()}))

    def test_item_without_issue_can_receive_classified_files(self):
        request=self.req('attachment',issue_id='item:1',file={'name':'付款说明.txt','category':'付款凭证','base64':base64.b64encode(b'item intake fixture').decode()})
        append(self.ws,request);current=state(self.ws)
        self.assertEqual(current['issues']['item:1']['status'],'replied')
        self.assertEqual(next(iter(current['attachments'].values()))['declared_category'],'付款凭证')
        self.assertEqual(len(load(self.ws)[0]['issues']),1)
        with self.assertRaises(ReviewError):append(self.ws,self.req('review',issue_id='item:1',checks=[],evidence=[{'asset_id':self.aid,'page':1}]))

    def test_builder_is_repeatable_and_pins_sources(self):
        from material_review_build import build
        material={'序号':1,'事项':'合成活动','材料类别':'电子组','票面合计':None,'纸面登记额':None,
                  '申请或已建单额':None,'单号':'','本次系统状态':'未建单','材料结论':'待核','问题与下一步':'核实','来源':'synthetic'}
        issue={'事项序号':'1','问题编号':'Q1','问题':'补说明','状态':'待核','社团':'示例','办理队列':'具体事实待说明',
               '处理责任':'经办','已确认依据':'合成来源','具体所需答复':'补说明','关闭条件':'说明已核','材料来源':[],
               '涉及金额':None,'金额含义':'未知'}
        inputs={'issues':{'materials':[material],'issues':[issue]},'normalized':{'documents':[{'document_id':'g1','export_sequences':[1]}]},
                'mapping':{'groups':[{'group_id':'g1','files':[{'filename':'证据.txt','working_copy':str(self.path),'sha256':self.aid}]}],'photos':[]}}
        config={'title':'合成构建','batch_id':'synthetic','roots':[str(self.root)],'batch_root':str(self.root),'sources':{},'expected_counts':{'items':1,'issues':1}}
        for key,value in inputs.items():
            path=self.root/(key+'.json');atomic_json(path,value);config['sources'][key]={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        cp=self.root/'config.json';atomic_json(cp,config);workspace=self.root/'built'
        first=build(cp,workspace);second=build(cp,workspace)
        self.assertEqual(first['version'],second['version']);self.assertEqual(first['counts']['assets'],1)
        self.assertIsNone(load(workspace)[0]['items']['1']['claim_total'])
        authority=self.root/'draft-scope.txt';authority.write_text('Only prepare a draft')
        inputs['normalized']['documents'][0]['material_review']={'draft_authorized':True,'draft_authority':{'path':str(authority),'sha256':hashlib.sha256(authority.read_bytes()).hexdigest()}}
        path=self.root/'normalized.json';atomic_json(path,inputs['normalized']);config['sources']['normalized']['sha256']=hashlib.sha256(path.read_bytes()).hexdigest();atomic_json(cp,config)
        build(cp,workspace);self.assertEqual(load(workspace)[0]['items']['1']['draft_authority']['scope'],'draft_only')
        authority.write_text('Changed instruction')
        with self.assertRaises(ReviewError):build(cp,workspace)
        (self.root/'issues.json').write_text('{}')
        with self.assertRaises(ReviewError):build(cp,workspace)

    def test_http_auth_origin_and_fixed_routes(self):
        server=ReviewServer(self.ws,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            origin=server.origin
            def send(path,body=None,headers=None):
                req=urllib.request.Request(origin+path,data=body,headers=headers or {})
                return urllib.request.urlopen(req)
            with self.assertRaises(urllib.error.HTTPError):send('/api/data')
            with self.assertRaises(urllib.error.HTTPError):send('/api/session',b'',{'Origin':'http://evil.invalid','X-Review-Token':server.token})
            r=send('/api/session',b'',{'Origin':origin,'X-Review-Token':server.token});cookie=r.headers['Set-Cookie'].split(';')[0]
            r=send('/api/data',headers={'Cookie':cookie});self.assertEqual(json.load(r)['data']['title'],'合成测试')
            r=send('/api/health',headers={'Cookie':cookie});self.assertTrue(json.load(r)['ok'])
            r=send('/api/original/'+self.aid,headers={'Cookie':cookie});self.assertEqual(r.read(),self.raw)
            r=send('/api/export?item=1',headers={'Cookie':cookie})
            with zipfile.ZipFile(io.BytesIO(r.read())) as z:self.assertIn('manifest.json',z.namelist())
            with self.assertRaises(urllib.error.HTTPError):send('/api/events',json.dumps(self.req()).encode(),{'Cookie':cookie,'Origin':'http://evil.invalid','X-Review-Action':'1'})
            with self.assertRaises(urllib.error.HTTPError):send('/api/original/../../etc/passwd',headers={'Cookie':cookie})
            r=send('/api/events',json.dumps(self.req()).encode(),{'Cookie':cookie,'Origin':origin,'X-Review-Action':'1'});self.assertTrue(json.load(r)['ok'])
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
