import sys
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from finance_transport_policy import check_dispatch


class TransportPolicyTests(unittest.TestCase):
    def request(self, operation, action, execute):
        return {'operation': operation, 'parameters': {'action': action, 'mode': action}, 'execute': execute}

    def test_legacy_write_never_dispatches(self):
        for operation in ['stage', 'invoice', 'correction', 'submission', 'invoice_selection', 'invoice_retry', 'invoice_edit', 'expense_amount']:
            with self.subTest(operation=operation), self.assertRaisesRegex(ValueError, 'native_http_adapter_required'):
                check_dispatch(self.request(operation, 'save', True))
            check_dispatch(self.request(operation, 'preview', False))

    def test_query_click_paths_do_not_escape_as_read_only(self):
        for action in ['view', 'query-used', 'full-grid', 'refresh']:
            with self.assertRaises(ValueError):
                check_dispatch(self.request('invoice_verification', action, False))
        with self.assertRaises(ValueError):
            check_dispatch(self.request('read_system', 'open-draft', False))

    def test_http_local_and_lifecycle_still_dispatch(self):
        for operation, action, execute in [('business','bills-list',False),('business','print-coverage',False),
                ('print_batch','print',True),('access','ensure',True),
                ('access','new-interface',True),('read_system','readback',False),('record_result','saved',False),
                ('material_intake','prepare',True)]:
            check_dispatch(self.request(operation, action, execute))
        request = self.request('pdf_export', 'export', True)
        with self.assertRaises(ValueError):
            check_dispatch(request)
        request['parameters']['export_plan'] = {'view_kind': 'formal_print_api'}
        check_dispatch(request)

    def test_complete_inventory_only_allows_fixed_http_grids_with_account(self):
        for grid in ['53','113','101']:
            request=self.request('invoice_verification','full-grid',False)
            request['parameters'].update(grid=grid,expected_userid='TEST001')
            check_dispatch(request)
            for change in [{'grid':'unbounded'},{'expected_userid':'bad id'}]:
                with self.assertRaises(ValueError):check_dispatch({**request,'parameters':{**request['parameters'],**change}})

    def test_canonical_access_default_does_not_disable_http_login(self):
        check_dispatch({'operation': 'access', 'parameters': {'tab_id': '7'}, 'execute': True})
        check_dispatch({'operation': 'access', 'parameters': {'tab_id': '7'}, 'execute': False})

    def test_wake_only_accepts_native_page_recovery(self):
        with patch('finance_backend.mac_native_enabled',return_value=True):
            check_dispatch(self.request('access','wake-interface',True))
        with patch('finance_backend.mac_native_enabled',return_value=False),self.assertRaises(ValueError):
            check_dispatch(self.request('access','wake-interface',True))


if __name__ == '__main__':
    unittest.main()
