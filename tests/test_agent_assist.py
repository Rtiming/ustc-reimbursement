from pathlib import Path
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from assistant_api import AssistantService
from operator_workbench import Workbench


class AssistTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        (self.root/'mcp').mkdir();shutil.copyfile(ROOT/'mcp/tool_catalog.json',self.root/'mcp/tool_catalog.json')
        self.service=AssistantService(self.root,[self.root])

    def test_discovery_schema_and_missing_fields_no_arbitrary_dispatch(self):
        # 资料工作区无需复制源码目录；工具合同来自当前正式代码owner。
        (self.root/'mcp/tool_catalog.json').unlink()
        result=self.service.assist()
        self.assertIn('groups',result);self.assertEqual(result['cases'],[])
        result=self.service.assist('schema',operation='new_case')
        self.assertNotIn('case_id',result['input_schema']['required'])
        result=self.service.assist('prepare',operation='new_case')
        self.assertEqual(result['error_code'],'missing_inputs')
        for operation in ['__getattribute__','assist']:
            with self.assertRaises(ValueError):self.service.assist('call',operation=operation)
        with self.assertRaises(ValueError):self.service.assist('call',operation='new_case',inputs={'case_id':'fixture'})

    def test_plan_confirm_reconnect_and_cross_instance_lock(self):
        plan=self.service.assist('prepare',operation='new_case',inputs={'case_id':'fixture','title':'合成代理活动'})
        self.assertFalse((self.root/'cases/fixture').exists())
        with self.assertRaises(ValueError):self.service.assist('confirm',intent_id=plan['intent_id'])
        def run(_):
            return self.service.assist('confirm',intent_id=plan['intent_id'],confirmation='用户明确要求建立此合成活动')
        with ThreadPoolExecutor(max_workers=2)as pool:
            results=list(pool.map(run,range(2)))
        self.assertEqual(len(self.service.cases()['cases']),1)
        self.assertEqual(sum(row.get('replayed',False)for row in results),1)
        status=self.service.assist('status',intent_id=plan['intent_id'])
        self.assertEqual(status['intent_status'],'returned')
        _,stored=Workbench(self.service).intent(plan['intent_id'])
        self.assertEqual(stored['confirmation_note'],'用户明确要求建立此合成活动')
        with self.assertRaises(ValueError):
            self.service.assist('confirm',intent_id=plan['intent_id'],inputs={'case_id':'another'},confirmation='yes')

    def test_compact_toolset_and_full_schema_size(self):
        from reimbursement_mcp import create_server
        async def inspect():
            full=await create_server(self.service).list_tools()
            compact=await create_server(self.service,'compact').list_tools()
            return full,compact
        full,small=asyncio.run(inspect())
        self.assertEqual({t.name for t in small},{'reimbursement_assist','reimbursement_task_view','reimbursement_capabilities'})
        encode=lambda rows:json.dumps([t.model_dump(by_alias=True)for t in rows],ensure_ascii=False)
        self.assertLess(len(encode(small)),len(encode(full))*.15)

    def test_name_only_creation_and_next_actions_stay_in_compact_toolset(self):
        plan=self.service.assist('prepare',operation='new_case',inputs={'title':'合成名字足够'})
        self.assertTrue(plan['arguments']['case_id'].startswith('activity_'))
        result=self.service.assist('confirm',intent_id=plan['intent_id'],confirmation='用户要求建立合成活动')
        action=result['next_action'];self.assertEqual(action['tool'],'reimbursement_assist')
        journey=self.service.assist(**action['arguments'])
        self.assertEqual(journey['next_action']['tool'],'reimbursement_assist')
        self.assertEqual(journey['next_action']['arguments']['operation'],'receive_materials')
        again=self.service.assist('prepare',operation='new_case',inputs={'title':'合成名字足够'})
        self.assertTrue(again['existing_case']);self.assertEqual(len(self.service.cases()['cases']),1)

    def test_completed_task_view_does_not_loop_back_to_status(self):
        from agent_assist import compact
        view={'schema':'ustc-task-view-v1','job_id':'j_'+'1'*24,'case_id':'fixture','status':'completed',
              'next_action':{'tool':'reimbursement_case_details','arguments':{'case_id':'fixture'}}}
        next_action=compact(view,case_id='fixture')['next_action']
        self.assertEqual(next_action['tool'],'reimbursement_assist')
        self.assertEqual(next_action['arguments']['operation'],'case_details')

    def test_standards_prepare_requires_preview_sha_before_creating_intent(self):
        from test_reimbursement_standards import change, profile
        library = self.root/'private-rules'
        self.service.standards_init(str(library), 'fixture', '合成专项', execute=True)
        request = change()
        request['sources'] = profile()['sources']
        inputs = {'library_root': str(library), 'profile_id': 'fixture', 'change': request}
        app = Workbench(self.service)
        for missing in ({}, {'expected_sha256': None}, {'expected_sha256': ''}):
            result = self.service.assist('prepare', operation='standards_change', inputs={**inputs, **missing})
            self.assertEqual(result['error_code'], 'missing_inputs')
            self.assertEqual(result['needed_inputs'][0]['field'], 'expected_sha256')
            self.assertNotIn('intent_id', result)
            self.assertEqual(list((app.folder/'intents').glob('*.json')), [])
            action = result['next_action']
            self.assertEqual(action['tool'], 'reimbursement_assist')
            self.assertEqual(action['arguments']['action'], 'call')
            self.assertFalse(action['arguments']['inputs']['execute'])
            self.assertEqual(action['arguments']['inputs']['change'], request)
        preview = self.service.assist(**action['arguments'])
        self.assertFalse(preview['executed'])
        planned = self.service.assist('prepare', operation='standards_change',
            inputs={**inputs, 'expected_sha256': preview['before_sha256']})
        self.assertIn('intent_id', planned)
        saved = self.service.assist('confirm', intent_id=planned['intent_id'], confirmation='用户确认这条合成变更')
        self.assertTrue(saved['executed'])
        self.assertEqual(saved['intent_status'], 'returned')


if __name__=='__main__':unittest.main()
