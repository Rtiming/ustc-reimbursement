from pathlib import Path
import copy
import hashlib
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from finance_feedback import project, export


def fixture():
    rows = [{'t.serial_no': '00123', 't.REQ_INFO': 'AB123-示例', 't.status': '-15', 't.lc_finish': 'T',
             't.status_name': '审批完成-缺少附件退回', 't.total_amt': '1,234.56', 't.abst': '示例活动',
             't.process_opinion': '1、补盖章清单\n2、说明用途，勿遗漏。'},
            {'t.serial_no': '00124', 't.REQ_INFO': 'AB123-示例', 't.status': '30', 't.lc_finish': 'T',
             't.status_name': '审批完成-支付完成', 't.total_amt': '56.00', 't.abst': '已完成',
             't.process_opinion': '历史处理意见'}]
    return {'ok': True, 'complete': True, 'action': 'bills-list', 'business_transport': 'native_http',
            'account_scope': 'current_applicant', 'expected_userid': 'AB123',
            'total_records': 2, 'observed_at': '2026-11-02T16:02:45Z', 'rows': rows}


SOURCE = {'case_id': 'fixture', 'job_id': 'j_'+'a'*24, 'artifact_id': 'fixture', 'sha256': 'a'*64}


class FeedbackTests(unittest.TestCase):
    def test_current_reason_is_exact_and_history_separate(self):
        value = fixture();r = project(value, 'AB123', SOURCE)
        self.assertEqual(len(r['rows']), 1)
        self.assertEqual(r['rows'][0]['reason_original'], value['rows'][0]['t.process_opinion'])
        self.assertIn(value['rows'][0]['t.process_opinion'], r['copy_text'])
        self.assertEqual(r['omitted_history_count'], 1)
        self.assertEqual(r['rows'][0]['serial'], '00123')
        self.assertFalse(r['system_write'])

    def test_missing_reason_is_not_successful_empty_result(self):
        value = fixture();value['rows'][0]['t.process_opinion'] = ''
        r = project(value, 'AB123', SOURCE)
        self.assertEqual(r['missing_reason_serials'], ['00123'])
        self.assertFalse(r['feedback_complete'])
        self.assertIn('系统未返回理由', r['copy_text'])

    def test_source_without_reason_column_reports_gap(self):
        value = fixture();del value['rows'][1]['t.process_opinion']
        r = project(value, 'AB123', SOURCE)
        self.assertIn('00124', r['missing_reason_serials'])

    def test_unknown_state_not_silently_dropped(self):
        value = fixture();value['rows'][0]['t.status'] = 'new-state'
        r = project(value, 'AB123', SOURCE)
        self.assertEqual(r['rows'][0]['category'], 'state_unverified')

    def test_reject_partial_duplicate_identity_and_bad_scope(self):
        for key, val in [('complete', False), ('expected_userid', 'OTHER'), ('total_records', 1)]:
            v = fixture();v[key] = val
            with self.assertRaises(ValueError):project(v, 'AB123', SOURCE)
        v=fixture();v['rows'][1]['t.serial_no']='00123'
        with self.assertRaises(ValueError):project(v, 'AB123', SOURCE)
        for serials in [['missing'], ['00123', '00123'], [], [123]]:
            with self.assertRaises(ValueError):project(fixture(), 'AB123', SOURCE, serials)

    def test_export_escapes_script_and_long_excel_text_is_lossless(self):
        v=fixture();original='=危险文本</script><script>alert(1)</script>\n'+'完整原文'*9000
        v['rows'][0]['t.process_opinion']=original
        r=project(v,'AB123',SOURCE)
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'new';export(r,out)
            page=(out/'财务反馈汇总.html').read_text(encoding='utf-8')
            self.assertNotIn('</script><script>alert(1)',page)
            self.assertIn(original,(out/'财务反馈原文.txt').read_text(encoding='utf-8'))
            plan=json.loads((out/'Excel交付计划.json').read_text(encoding='utf-8'))
            chunks=[x['反馈原文'] for x in plan['sheets'][1]['rows']]
            self.assertEqual(''.join(chunks),original)
            self.assertTrue(all(len(x)<=30000 for x in chunks))
            from openpyxl import load_workbook
            book=load_workbook(out/'财务挂单理由汇总.xlsx',data_only=False)
            sheet=book['财务反馈原文']
            actual=[sheet.cell(row,7).value for row in range(6,6+len(chunks))]
            self.assertEqual(''.join(actual),original)
            self.assertTrue(all(sheet.cell(row,7).data_type=='s' for row in range(6,6+len(chunks))))
            book.close()
            with self.assertRaises(FileExistsError):export(r,out)

    def test_history_opt_in_and_explicit_selection(self):
        r=project(fixture(),'AB123',SOURCE,['00124'],True)
        self.assertEqual(r['selected_bills'],1)
        self.assertEqual(r['rows'][0]['category'],'history')


if __name__=='__main__':unittest.main()
