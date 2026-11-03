import sys
import types
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import test_material_review as fixture
from material_review_model import ReviewError
from material_review_store import append,state
from material_review_service import run,feedback,deployment

class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.f=fixture.ReviewTest();self.f.setUp()
        self.service=types.SimpleNamespace(root=self.f.root/'formal',material_roots=[self.f.root])
    def tearDown(self):self.f.tearDown()
    def test_registry_explicit_isolated_and_no_material_copy(self):
        self.assertEqual(run(self.service)['workspaces'],[])
        run(self.service,'register',str(self.f.ws))
        r=run(self.service)['workspaces'];self.assertEqual(len(r),1);self.assertTrue(r[0]['available'])
        run(self.service,'register',str(self.f.ws));self.assertEqual(len(run(self.service)['workspaces']),1)
        self.service.material_roots=[self.f.root/'other']
        self.assertEqual(run(self.service)['workspaces'],[])
        with self.assertRaises(ReviewError):run(self.service,'summary',str(self.f.ws))
    def test_deferred_is_not_reviewed_and_new_reply_requires_review(self):
        append(self.f.ws,self.f.req())
        append(self.f.ws,self.f.req('defer',stage='waiting_materials',text='按本人安排留空，待真实收款材料'))
        r=run(self.service,'summary',str(self.f.ws))['feedback']
        self.assertEqual(r['counts'],{'waiting_materials':1});self.assertEqual(r['open_count'],1)
        self.assertIn('留空',r['rows'][0]['next_action'])
        append(self.f.ws,self.f.req())
        r=feedback(self.f.data,state(self.f.ws))
        self.assertEqual(r['counts'],{'replied':1});self.assertIn('无需重复答复',r['rows'][0]['next_action'])
    def test_bad_action_stage_and_scope_rejected(self):
        for action,workspace in [('submit',str(self.f.ws)),('register','relative'),('summary','/outside')]:
            with self.assertRaises(ReviewError):run(self.service,action,workspace)
        with self.assertRaises(ReviewError):append(self.f.ws,self.f.req('defer',stage='approved'))
        with self.assertRaises(ReviewError):append(self.f.ws,self.f.req('defer',issue_id='item:1',stage='hold'))
    def test_deployment_registers_explicit_workspaces_and_default_open_requires_one(self):
        from unittest.mock import patch
        import material_review_launch
        self.assertFalse(run(self.service,'open')['ok'])
        with self.assertRaises(ReviewError):deployment(self.service,[str(self.f.ws)],check_only=True)
        with self.assertRaises((ReviewError,OSError)):
            deployment(self.service,[str(self.f.ws),str(self.f.root/'missing')])
        self.assertEqual(run(self.service)['workspaces'],[])
        self.assertTrue(deployment(self.service,[str(self.f.ws)])['ok'])
        self.assertTrue(deployment(self.service,[str(self.f.ws)],check_only=True)['ok'])
        with patch.object(material_review_launch,'launch',return_value={'ok':True}) as launch:
            self.assertTrue(run(self.service,'open',port=0)['ok'])
            launch.assert_called_once_with(self.f.ws.resolve(),0,True)
        self.assertEqual(fixture.events(self.f.ws),[])
    def test_submission_wait_does_not_change_finance_or_close(self):
        append(self.f.ws,self.f.req('defer',stage='before_submit'))
        r=run(self.service,'summary',str(self.f.ws))
        self.assertFalse(r['system_write']);self.assertEqual(r['feedback']['open_count'],1)
        self.assertFalse(r['workflow']['available'])
        self.f.data['issues']['Q1']['fingerprint']='changed';self.f.publish()
        self.assertEqual(state(self.f.ws)['issues']['Q1']['status'],'pending')

    def test_agent_summary_uses_current_copy_but_keeps_existing_arrangements(self):
        f=self.f
        f.data['issues']['Q1']['feedback']={'issue_fingerprint':f.data['issues']['Q1']['fingerprint'],
            'project':'测试活动','context':'行程已在案','request':'只需收款分配','response':'按已有编号填写实际收款人'}
        f.publish()
        row=run(self.service,'summary',str(f.ws))['feedback']['rows'][0]
        self.assertEqual(row['title'],'行程已在案')
        self.assertEqual(row['next_action'],'按已有编号填写实际收款人')
        self.assertEqual(state(f.ws)['revision'],0)
        append(f.ws,f.req('defer',stage='waiting_materials',text='本人安排一起处理'))
        row=run(self.service,'summary',str(f.ws))['feedback']['rows'][0]
        self.assertEqual(row['next_action'],'本人安排一起处理')
        self.assertEqual(row['status'],'waiting_materials')

    def test_replied_and_reviewed_never_fall_back_to_old_requests(self):
        f=self.f;append(f.ws,f.req())
        current=state(f.ws)
        self.assertNotIn('next_action',current['issues']['Q1'])
        self.assertIn('无需重复答复',feedback(f.data,current)['rows'][0]['next_action'])
        append(f.ws,f.req('review',checks=[True,True],
                         evidence=[{'asset_id':f.aid,'page':1}]))
        row=run(self.service,'summary',str(f.ws))['feedback']['rows'][0]
        self.assertEqual(row['status'],'reviewed')
        self.assertEqual(row['next_action'],'材料已复核，无需重复答复。')

if __name__=='__main__':unittest.main()
