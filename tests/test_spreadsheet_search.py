"""公开合成表格，标识为测试值，不包含真实学号。"""
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from search_spreadsheets import search
from assistant_api import AssistantService, APIError


class SpreadsheetSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.file = self.root/'名单.xlsx'
        shared = ['姓名', '学号', '测试甲', 'DEMO-02', '测试乙', 'DEMO-03', '99000000000000001025', 'DEMO-04']
        with zipfile.ZipFile(self.file, 'w') as z:
            z.writestr('xl/sharedStrings.xml', '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'+''.join('<si><t>'+escape(x)+'</t></si>' for x in shared)+'</sst>')
            z.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="名单" sheetId="1" r:id="r1"/></sheets></workbook>')
            z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Target="worksheets/sheet1.xml"/></Relationships>')
            z.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="D1" t="s"><v>0</v></c><c r="E1" t="s"><v>1</v></c></row><row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2" t="s"><v>3</v></c><c r="D2" t="s"><v>4</v></c><c r="E2" t="s"><v>5</v></c><c r="G2" t="s"><v>6</v></c><c r="H2"><f>1+1</f><v>2</v></c></row><row r="3"><c r="A3" t="s"><v>2</v></c><c r="B3" t="s"><v>7</v></c></row></sheetData></worksheet>')

    def tearDown(self):
        self.temp.cleanup()

    def test_person_lookup_handles_repeated_column_groups(self):
        r = search([self.file], '测试乙', person_lookup=True)
        self.assertEqual(r['unique_name_student_id_pairs'], [('测试乙', 'DEMO-03')])
        self.assertEqual(r['matches'][0]['cell'], 'D2')
        self.assertEqual(r['matches'][0]['student_id_cell'], 'E2')

    def test_same_name_different_ids_are_not_silently_bound(self):
        r = search([self.file], '测试甲', person_lookup=True)
        self.assertEqual(len(r['unique_name_student_id_pairs']), 2)
        self.assertEqual(r['total'], 2)

    def test_long_identifier_is_preserved_and_formula_is_only_cached(self):
        r = search([self.file], '99000000000000001025', exact=True)
        self.assertEqual(r['matches'][0]['value'], '99000000000000001025')
        r = search([self.file], '2', exact=True)
        self.assertTrue(r['matches'][0]['cached_formula_value'])

    def test_csv_and_limit_report_truncation(self):
        p = self.root/'名单.csv'; p.write_text('姓名,学号\n甲,PB1\n甲,PB2\n', encoding='utf-8-sig')
        r = search([p], '甲', person_lookup=True, limit=1)
        self.assertEqual(r['total'], 2)
        self.assertTrue(r['truncated'])

    def test_api_enforces_declared_material_roots_even_for_symlinks(self):
        service = AssistantService(self.root, [self.root])
        r = service.search_spreadsheets([str(self.file)], '测试乙', person_lookup=True)
        self.assertFalse(r['system_write'])
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside)/'outside.xlsx'; target.write_bytes(self.file.read_bytes())
            (self.root/'outside.xlsx').symlink_to(target)
            with self.assertRaises(APIError) as cm:
                service.search_spreadsheets([str(self.root)], '测试乙')
            self.assertEqual(cm.exception.code, 'material_root')


if __name__ == '__main__':
    unittest.main()
