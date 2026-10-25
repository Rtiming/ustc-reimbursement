#!/usr/bin/env python3
"""真实stdio MCP验收，隔离空案与模板，不调用浏览器或当前财务单据。"""
import asyncio
import argparse
import json
import sys
import tempfile
import time
from importlib.metadata import version
from pathlib import Path
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from assistant_api import AssistantService, PROJECT, atomic_json, read_json
from operator_support import stamp
from assistant_contract import tool_catalog


def payload(result):
    if result.is_error: raise AssertionError(str(result.content))
    return result.structured_content


async def wait_job(client, case_id, job_id):
    deadline = time.monotonic()+90
    interval = 0.1
    while time.monotonic()<deadline:
        r = payload(await client.call_tool('reimbursement_result', {'case_id': case_id, 'job_id': job_id}))
        if r['status'] not in ['queued', 'running']:
            assert r['status']=='completed', r
            return r
        await asyncio.sleep(interval)
        interval = min(interval*2, 1.0)
    raise AssertionError('演示任务超时')


async def main(render=False, write_catalog=False, output=None):
    # 干净源码检出也应可验收，不读取任何真实案件作为前置条件或测试夹具。
    with tempfile.TemporaryDirectory(prefix='reimbursement-mcp-acceptance-') as temp:
        parameters = StdioServerParameters(command=sys.executable, args=[str(PROJECT/'tools/reimbursement_mcp.py'), '--workspace', temp, '--material-root', temp], cwd=str(PROJECT),
                                           env={'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'})
        async with Client(parameters, read_timeout_seconds=30) as client:
            tools = await client.list_tools()
            names = [t.name for t in tools.tools]
            assert len(names) >= 20, names
            assert 'reimbursement_submission' in names, names
            assert not any(x in names for x in ['delete', 'captcha', 'withdraw'])
            catalog = tool_catalog(tools.tools, version('mcp'))
            if write_catalog:
                atomic_json(PROJECT/'mcp/tool_catalog.json', catalog)
            empty = payload(await client.call_tool('reimbursement_cases'))
            assert empty['cases']==[]
            entry = payload(await client.call_tool('reimbursement_capabilities', {'intent':'materials'}))
            assert entry['system_write'] is False and entry['platform']['page_script_executed'] is False
            assert entry['next_action']['tool'] == 'reimbursement_guide'
            quickstart = payload(await client.call_tool('reimbursement_guide', {'topic':'quickstart'}))
            assert quickstart['system_write'] is False and 'prepare_batch_file' in quickstart['text']
            for topic in ('intake', 'planning', 'export', 'windows'):
                instructions = payload(await client.call_tool('reimbursement_guide', {'topic':topic}))
                assert instructions['system_write'] is False and instructions['text']
            r = payload(await client.call_tool('reimbursement_new_case', {'case_id': 'demo_mcp'}))
            ov = payload(await client.call_tool('reimbursement_overview', {'case_id': 'demo_mcp', 'include_parameters': True}))
            assert ov['revision']==r['revision'] and ov['parameters']['form']['payments']==[]
            update = payload(await client.call_tool('reimbursement_update_case', {'case_id': 'demo_mcp', 'document': 'form', 'changes': {'amount': '600.00'}, 'expected_revision': ov['revision']}))
            conflict = await client.call_tool('reimbursement_update_case', {'case_id': 'demo_mcp', 'document': 'form', 'changes': {'amount': '700.00'}, 'expected_revision': ov['revision']})
            assert conflict.is_error
            assert 'revision_conflict' in str(conflict.content), conflict
            guides = await client.read_resource('reimbursement://guide/start')
            assert guides.contents
            standards = payload(await client.call_tool('reimbursement_guide', {'topic': 'standards'}))
            assert standards['topic'] == 'standards' and standards['system_write'] is False
            assert standards['text'] == (PROJECT/'skills/ustc-reimbursement/references/standards-system.md').read_text(encoding='utf-8')
            checked = payload(await client.call_tool('reimbursement_check', {'case_id': 'demo_mcp'}))
            assert not checked['ok']
            values = read_json(PROJECT/'templates/activity_approval/input_demo.json')
            a = payload(await client.call_tool('reimbursement_activity', {'case_id': 'demo_mcp', 'values': values, 'request_id': 'activity-01', 'check_only': not render}))
            b = payload(await client.call_tool('reimbursement_activity', {'case_id': 'demo_mcp', 'values': values, 'request_id': 'activity-01', 'check_only': not render}))
            assert a['job_id']==b['job_id'] and b['reused_existing_job']
            made = await wait_job(client, 'demo_mcp', a['job_id'])
            if render:
                doc = next(x for x in made['artifacts'] if x['format']=='docx')
                render_job = payload(await client.call_tool('reimbursement_render', {'case_id': 'demo_mcp', 'job_id': a['job_id'], 'artifact_id': doc['artifact_id'], 'request_id': 'render-01'}))
                rendered = await wait_job(client, 'demo_mcp', render_job['job_id'])
                rr = next(x for x in rendered['artifacts'] if x['name']=='render_receipt.json')
                detail = payload(await client.call_tool('reimbursement_result', {'case_id': 'demo_mcp', 'job_id': render_job['job_id'], 'artifact_id': rr['artifact_id']}))
                assert detail['artifact']['page_count']>=1 and not detail['artifact']['visual_review_completed']
            # 启动本地任务后主动关闭MCP连接，独立worker仍应完成。
            detached = payload(await client.call_tool('reimbursement_activity', {'case_id': 'demo_mcp', 'values': values, 'request_id': 'detached-01'}))
        service = AssistantService(temp, [temp], sys.executable)
        deadline = time.monotonic()+20
        while time.monotonic()<deadline:
            final = service.result('demo_mcp', detached['job_id'])
            if final['status'] not in ['queued', 'running']: break
            await asyncio.sleep(0.1)
        assert final['status']=='completed', final
        async with Client(parameters, mode='legacy', read_timeout_seconds=30) as legacy:
            old_tools = await legacy.list_tools()
            assert len(old_tools.tools)==len(names)
            legacy_cases = payload(await legacy.call_tool('reimbursement_cases'))
            assert legacy_cases['cases'][0]['case_id']=='demo_mcp'
    result = {'ok': True, 'checked_at_utc': stamp(), 'transport': 'stdio', 'tool_count': len(names),
              'checks': ['stdio_negotiation_and_tools_list', 'capabilities_and_quickstart_readonly', 'isolated_case_intake', 'revision_conflict', 'resource_read', 'standards_guide_readonly', 'material_gaps',
                         'idempotent_request', 'activity_input_check', 'worker_survives_mcp_disconnect', 'legacy_initialize']
                        + (['activity_docx', 'render_pdf_png'] if render else []),
              'browser_actions_executed': False, 'real_case_data_required': False,
              'catalog_updated': write_catalog, 'render_checked': render, 'dsh_actual_client_verified': False}
    atomic_json(output or PROJECT/'.local/smoke/mcp_smoke.json', result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--render', action='store_true', help='额外验证DOCX/PDF渲染，需本机LibreOffice和pdftoppm')
    parser.add_argument('--write-catalog', action='store_true', help='显式更新共享MCP目录；默认验收不改源码')
    parser.add_argument('--output', type=Path, help='验收回执；默认.local/smoke/mcp_smoke.json')
    options = parser.parse_args()
    asyncio.run(main(options.render, options.write_catalog, options.output))
