import importlib.util
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile
import xml.etree.ElementTree as E

spec=importlib.util.spec_from_file_location('normalize_identifier_text',Path(__file__).parents[1]/'tools/normalize_identifier_text.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class IdentifierTests(unittest.TestCase):
    def fixture(self,path,kind='str'):
        ns=m.NS
        styles=f'<styleSheet xmlns="{ns}"><fonts><font><name val="Arial"/><sz val="11"/></font></fonts><cellXfs><xf fontId="0"/></cellXfs></styleSheet>'
        sheet=f'<worksheet xmlns="{ns}"><sheetData><row r="5"><c r="A5" t="str"><v>完整订单号</v></c></row><row r="6"><c r="A6" t="{kind}"><v>1234567890123456789012345678</v></c><c r="B6"><v>12.34</v></c><c r="C6"><f>SUM(B6)</f><v>12.34</v></c></row></sheetData></worksheet>'
        with ZipFile(path,'w') as z:
            z.writestr('xl/styles.xml',styles);z.writestr('xl/worksheets/sheet1.xml',sheet);z.writestr('untouched.bin',b'unchanged')

    def test_text_runs_preserve_full_value_and_other_cells(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);self.fixture(p/'source.xlsx');old=(p/'source.xlsx').read_bytes()
            result=m.normalize(p/'source.xlsx',p/'new.xlsx');self.assertEqual(result['cells'],1)
            self.assertEqual((p/'source.xlsx').read_bytes(),old)
            with ZipFile(p/'new.xlsx') as z:
                root=E.fromstring(z.read('xl/worksheets/sheet1.xml'))
                cell=next(c for c in root.iter(m.T('c')) if c.get('r')=='A6')
                self.assertEqual(cell.get('t'),'inlineStr')
                self.assertEqual(''.join(x.text for x in cell.iter(m.T('t'))),'1234567890123456789012345678')
                self.assertEqual(next(c for c in root.iter(m.T('c')) if c.get('r')=='B6').find(m.T('v')).text,'12.34')
                self.assertEqual(z.read('untouched.bin'),b'unchanged')

    def test_numeric_identifier_is_not_repaired_by_formatting(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);self.fixture(p/'source.xlsx',kind='n')
            with self.assertRaises(ValueError):m.normalize(p/'source.xlsx',p/'new.xlsx')
            self.assertFalse((p/'new.xlsx').exists())

    def test_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);self.fixture(p/'source.xlsx');(p/'new.xlsx').write_bytes(b'keep')
            with self.assertRaises(ValueError):m.normalize(p/'source.xlsx',p/'new.xlsx')
            self.assertEqual((p/'new.xlsx').read_bytes(),b'keep')


if __name__=='__main__':unittest.main()
