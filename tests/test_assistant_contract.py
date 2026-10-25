"""固定接口映射与兼容别名；拒绝借统一入口暴露内部管理方法。"""

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from assistant_contract import APIError, GuidanceError, dispatch_request, json_routes, tool_catalog


class ContractTests(unittest.TestCase):
    def test_dispatch_uses_same_service_and_rejects_internal_method(self):
        service = SimpleNamespace(overview=Mock(return_value={'ok': True}), resolve=Mock())
        result = dispatch_request(service, {'method': 'overview', 'params': {'case_id': 'fixture'}})
        self.assertTrue(result['ok'])
        service.overview.assert_called_once_with(case_id='fixture')
        with self.assertRaises(APIError):
            dispatch_request(service, {'method': 'resolve', 'params': {'case_id': 'fixture'}})
        service.resolve.assert_not_called()

    def test_task_tool_routes_remain_explicit_enqueue_operations(self):
        routes = json_routes()
        self.assertEqual(
            routes['reimbursement_expense_amount'],
            {'method': 'enqueue', 'operation': 'expense_amount'},
        )
        self.assertEqual(
            routes['reimbursement_standards_add_profile'], {'method': 'standards_add_profile'}
        )
        self.assertEqual(
            routes['reimbursement_submission'], {'method': 'enqueue', 'operation': 'submission'}
        )
        with self.assertRaises(APIError):
            dispatch_request(SimpleNamespace(), {'method': 'expense_amount'})

    def test_catalog_checks_missing_extra_and_duplicate_mcp_tools(self):
        def tool(name):
            return SimpleNamespace(model_dump=lambda **kwargs: {'name': name})

        tools = [tool(name) for name in json_routes()]
        result = tool_catalog(tools, 'fixture')
        self.assertEqual(set(result['json_routes']), {row['name'] for row in result['tools']})
        for invalid in (tools[:-1], tools + [tool('arbitrary_execute')], tools + [tools[0]]):
            with self.assertRaises(APIError):
                tool_catalog(invalid, 'fixture')

    def test_legacy_error_and_rules_imports_keep_identity(self):
        import assistant_api
        import assistant_guidance
        import reimbursement_standards

        self.assertIs(assistant_api.APIError, APIError)
        self.assertIs(assistant_guidance.GuidanceError, GuidanceError)
        self.assertIs(
            assistant_guidance.standards_add_profile, reimbursement_standards.standards_add_profile
        )


if __name__ == '__main__':
    unittest.main()
