from pathlib import Path
import hashlib
import json
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from checklist_native import render,cell_value


class NativeChecklistTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        source=self.root/'原来源.txt';source.write_text('synthetic frozen source',encoding='utf-8');self.source=source
        self.plan={'schema':'ustc-checklist-delivery-v1','case_id':'fixture','system_write':False,
            'sources':[{'path':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}],
            'sheets':[{'name':'问题总结','title':'=literal title','columns':[{'key':'票号','type':'text'},{'key':'金额','type':'money'},{'key':'说明','type':'text'}],
                       'rows':[{'票号':'00012345678901234567','金额':'10.20','说明':'=HYPERLINK("example.invalid")'},
                               {'票号':'B02','金额':'未核定','说明':'保留未知'}, {'票号':'B03','金额':None,'说明':'无数据'}]}]}
        self.path=self.root/'plan.json';self.path.write_text(json.dumps(self.plan,ensure_ascii=False),encoding='utf-8')

    def test_real_xlsx_preserves_text_numeric_unknown_and_literal_formula(self):
        from openpyxl import load_workbook
        output=self.root/'中文 清单.xlsx';receipt=render(self.path,output)
        self.assertTrue(receipt['ok']);self.assertEqual(receipt['verified_cells'],9)
        with output.open('rb')as stream:self.assertEqual(stream.read(2),b'PK')
        book=load_workbook(output,data_only=False)
        self.assertEqual(book.active['A6'].value,'00012345678901234567');self.assertEqual(book.active['A6'].data_type,'s')
        self.assertEqual(book.active['B6'].value,10.2);self.assertEqual(book.active['B7'].value,'未核定')
        self.assertIsNone(book.active['B8'].value);self.assertEqual(book.active['C6'].data_type,'s');self.assertEqual(book.active['A2'].data_type,'s')
        book.close()
        with self.assertRaises(ValueError):render(self.path,output)

    def test_drift_and_silent_truncation_rejected(self):
        self.source.write_text('changed',encoding='utf-8')
        with self.assertRaises(ValueError):render(self.path,self.root/'new.xlsx')
        self.assertFalse((self.root/'new.xlsx').exists())
        with self.assertRaises(ValueError):cell_value('x'*32768,'text')
        with self.assertRaises(ValueError):cell_value(float('nan'),'number')
        with self.assertRaises(ValueError):cell_value('10.201','money')


if __name__=='__main__':unittest.main()
