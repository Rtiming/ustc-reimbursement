import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from confirmation_workbook import read_answers


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RoundtripTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / 'base.xlsx'
        self.edited = self.root / 'edited.xlsx'
        self.question = self.root / 'questions.json'
        self.manifest = self.root / 'mapping.json'
        self.make(self.base)
        self.question.write_text(json.dumps({'cards': [{'id': 'synthetic', 'title': 'Synthetic', 'sequences': [1],
            'fields': [{'key': 'phone', 'required': True}], 'evidence': []}]}))
        self.manifest.write_text(json.dumps({'schema': 'ustc-confirmation-roundtrip-v1',
            'baseline': {'path': str(self.base), 'sha256': digest(self.base)},
            'questions': {'path': str(self.question), 'sha256': digest(self.question)},
            'sources': [], 'worksheet': 'Review', 'controls': [
                {'card_id': 'synthetic', 'key': 'phone', 'anchor': 'B2'},
                {'card_id': 'synthetic', 'key': 'result', 'anchor': 'B3'}]}))

    def make(self, path, phone='', result='', title='Source', formula=False, image=b'original'):
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Review" sheetId="1" r:id="r1"/></sheets></workbook>')
            z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Target="worksheets/sheet1.xml"/></Relationships>')
            cells = ''.join(f'<c r="{address}" t="inlineStr"><is><t>{value}</t></is></c>' for address, value in [('A1', title), ('B2', phone), ('B3', result)])
            if formula: cells += '<c r="B4"><f>1+1</f><v>2</v></c>'
            z.writestr('xl/worksheets/sheet1.xml', f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1">{cells}</row></sheetData><mergeCells><mergeCell ref="B2:C2"/></mergeCells></worksheet>')
            z.writestr('xl/media/image1.png', image)

    def read(self):
        return read_answers(self.edited, self.manifest)

    def test_blank_stays_pending(self):
        self.make(self.edited)
        self.assertEqual(self.read()['counts'], {'pending': 1})

    def test_valid_answer_is_review_candidate_not_case_write(self):
        self.make(self.edited, '17700000001', '确认')
        result = self.read()
        self.assertEqual(result['counts'], {'user_confirmed_pending_review': 1})
        self.assertFalse(result['system_write']); self.assertFalse(result['case_modified'])

    def test_confirm_without_answer_is_incomplete(self):
        self.make(self.edited, result='确认')
        self.assertEqual(self.read()['counts'], {'incomplete_confirmation': 1})

    def test_twelve_digits_cannot_become_confirmed(self):
        self.make(self.edited, '177000000011', '确认')
        self.assertEqual(self.read()['counts'], {'incomplete_confirmation': 1})

    def test_unmarked_input_stays_draft(self):
        self.make(self.edited, '17700000001')
        self.assertEqual(self.read()['counts'], {'draft_answer': 1})

    def test_source_formula_and_image_changes_rejected(self):
        for change in [{'title': 'Changed'}, {'formula': True}, {'image': b'changed'}]:
            with self.subTest(change=change):
                self.make(self.edited, **change)
                with self.assertRaises(ValueError): self.read()

    def test_changed_question_source_rejected(self):
        self.make(self.edited)
        self.question.write_text('{}')
        with self.assertRaises(ValueError): self.read()

    def test_existing_report_cannot_be_overwritten(self):
        self.make(self.edited)
        report = self.root / 'report.json'
        report.write_text('original')
        with self.assertRaises(FileExistsError): read_answers(self.edited, self.manifest, report)
        self.assertEqual(report.read_text(), 'original')

    def two_sheets(self, path, second_phone='17700000002', second_title='Source'):
        self.make(path, '17700000001')
        with zipfile.ZipFile(path) as z:
            parts = {n: z.read(n) for n in z.namelist()}
        parts['xl/workbook.xml'] = parts['xl/workbook.xml'].replace(b'</sheets>', b'<sheet name="Amounts" sheetId="2" r:id="r2"/></sheets>')
        parts['xl/_rels/workbook.xml.rels'] = parts['xl/_rels/workbook.xml.rels'].replace(b'</Relationships>', b'<Relationship Id="r2" Target="worksheets/sheet2.xml"/></Relationships>')
        parts['xl/worksheets/sheet2.xml'] = parts['xl/worksheets/sheet1.xml'].replace(b'17700000001', second_phone.encode()).replace(b'Source', second_title.encode())
        with zipfile.ZipFile(path, 'w') as z:
            for n, data in parts.items(): z.writestr(n, data)

    def v2_setup(self):
        self.two_sheets(self.base)
        q = json.loads(self.question.read_text())
        q['cards'].append({**q['cards'][0], 'id': 'second'})
        self.question.write_text(json.dumps(q))
        m = json.loads(self.manifest.read_text())
        m['schema'] = 'ustc-confirmation-roundtrip-v2'
        m['baseline']['sha256'] = digest(self.base)
        m['questions']['sha256'] = digest(self.question)
        m.pop('worksheet')
        m['controls'] = [{**c, 'worksheet': 'Review'} for c in m['controls']]
        m['controls'] += [{**c, 'worksheet': 'Amounts', 'card_id': 'second'} for c in m['controls'][:]]
        self.manifest.write_text(json.dumps(m))

    def test_v2_preserves_prior_answers_and_tracks_only_new_edits(self):
        self.v2_setup()
        self.two_sheets(self.edited)
        self.assertEqual(self.read()['changed_fields'], 0)
        self.two_sheets(self.edited, second_phone='17700000003')
        r = self.read()
        self.assertEqual(r['changed_fields'], 1)
        self.assertEqual(r['documents'][1]['changed_since_baseline']['phone'], {'before': '17700000002', 'after': '17700000003'})
        self.assertEqual(r['documents'][0]['answers']['phone'], '17700000001')

    def test_v2_clearing_answer_is_a_change_and_nonanswer_changes_fail(self):
        self.v2_setup()
        self.two_sheets(self.edited, second_phone='')
        self.assertEqual(self.read()['documents'][1]['changed_since_baseline']['phone']['after'], '')
        self.two_sheets(self.edited, second_title='Changed')
        with self.assertRaises(ValueError): self.read()

    def test_v2_duplicate_same_sheet_anchor_rejected(self):
        self.v2_setup()
        self.two_sheets(self.edited)
        m = json.loads(self.manifest.read_text())
        m['controls'][2]['worksheet'] = 'Review'
        self.manifest.write_text(json.dumps(m))
        with self.assertRaisesRegex(ValueError, '重复答案锚点'): self.read()

    def test_v1_still_requires_blank_baseline(self):
        self.make(self.base, '17700000001')
        m = json.loads(self.manifest.read_text())
        m['baseline']['sha256'] = digest(self.base)
        self.manifest.write_text(json.dumps(m))
        self.make(self.edited, '17700000001')
        with self.assertRaisesRegex(ValueError, '答案必须为空'): self.read()


if __name__ == '__main__':
    unittest.main()
