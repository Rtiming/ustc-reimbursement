import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from finance_bill_state import index_bills, bound_bill, state
from submission import verify_applicant_agreement, verify_applicant_prepared_list


def snapshot():
    return {'ok': True, 'complete': True, 'business_transport': 'native_http', 'action': 'bills-list',
            'expected_userid': 'TEST001', 'account_scope': 'current_applicant', 'total_records': 1,
            'observed_at': '2026-10-28T00:00:00Z', 'rows': [{'t.serial_no': '123',
            't.REQ_INFO': 'TEST001-Example', 't.total_amt': '1,234.56', 't.status': '-2',
            't.lc_finish': 'F', 't.node_id': '508', 't.status_name': '申请人待审批'}]}


class BillStateTests(unittest.TestCase):
    def test_full_identity_amount_and_failure_cases(self):
        data = snapshot()
        self.assertEqual(bound_bill(data, '123', 'TEST001', '1234.56')['t.serial_no'], '123')
        for patch in ({'complete': False}, {'total_records': True}, {'total_records': 2},
                      {'expected_userid': 'OTHER'}, {'business_transport': 'dom'}, {'rows': []}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                index_bills({**data, **patch}, 'TEST001')
        for field, value in [('t.total_amt', '1,2,34.56'), ('t.REQ_INFO', 'OTHER-user')]:
            bad = copy.deepcopy(data); bad['rows'][0][field] = value
            with self.assertRaises(ValueError): bound_bill(bad, '123', 'TEST001', '1234.56')
        bad = {**data, 'total_records': 2, 'rows': data['rows'] * 2}
        with self.assertRaises(ValueError): index_bills(bad, 'TEST001')

    def test_prepared_and_approved_are_not_conflated(self):
        data = snapshot()
        self.assertTrue(verify_applicant_prepared_list(data, '123', 'TEST001', '1234.56')['applicant_ready_verified'])
        with self.assertRaises(ValueError): verify_applicant_agreement(data, '123', 'TEST001', '1234.56')
        data['rows'][0].update({'t.node_id': '449', 't.status_name': '审批中'})
        self.assertTrue(verify_applicant_agreement(data, '123', 'TEST001', '1234.56')['applicant_agreement_completed'])
        data['rows'][0].update({'t.status': '10', 't.lc_finish': 'T', 't.status_name': '已预约待投递'})
        self.assertEqual(state(data['rows'][0]), 'ready_for_delivery')
        with self.assertRaises(ValueError): verify_applicant_agreement(data, '123', 'TEST001', '1234.56')

    def test_returned_paid_and_unknown_are_distinct(self):
        row = snapshot()['rows'][0]
        for code, finish, label, expected in [('-15','T','审批完成-缺少附件退回','returned_after_approval'),
                ('30','T','已支付','paid'), ('-99','F','草稿','draft'), ('10','F','已预约待投递','unknown'),
                ('10','T','different','unknown'), ('new','F','new','unknown')]:
            self.assertEqual(state({**row, 't.status': code, 't.lc_finish': finish, 't.status_name': label}), expected)


if __name__ == '__main__': unittest.main()
