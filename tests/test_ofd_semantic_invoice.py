import sys,tempfile,unittest,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from invoice_audit import read_invoice,USTC

class OfdSemanticInvoiceTests(unittest.TestCase):
    def fixture(self,change=None):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);p=Path(t.name)/'source.ofd'
        values={'InvoiceNumber':'12345678901234567890','IssueTime':'2026年07月03日','BuyerName':USTC['title'],'BuyerIdNum':USTC['tax_id'],
                'SellerName':'示例酒店','SellerIdNum':USTC['tax_id'],'TotalTax-includedAmount':'106.00','TotalAmWithoutTax':'100.00','TotalTaxAm':'6.00'}
        values.update(change or {});ns='http://www.ofdspec.org/2016';objects=[];fields=[]
        for i,(key,value)in enumerate(values.items(),10):
            objects.append(f'<ofd:TextObject ID="{i}"><ofd:TextCode>{value}</ofd:TextCode></ofd:TextObject>')
            fields.append(f'<{key}><ofd:ObjectRef PageRef="4">{i}</ofd:ObjectRef></{key}>')
        tag=f'<EInvoice xmlns:ofd="{ns}"><EIid><ofd:ObjectData>12345678901234567890</ofd:ObjectData></EIid>'+''.join(fields)+'</EInvoice>'
        with zipfile.ZipFile(p,'w')as z:
            z.writestr('Doc_0/Document.xml',f'<ofd:Document xmlns:ofd="{ns}"><ofd:Pages><ofd:Page ID="4" BaseLoc="Pages/Page_0/Content.xml"/></ofd:Pages></ofd:Document>')
            z.writestr('Doc_0/Pages/Page_0/Content.xml',f'<ofd:Page xmlns:ofd="{ns}">'+''.join(objects)+'</ofd:Page>')
            z.writestr('Doc_0/Tags/Tag.xml',tag)
        return p

    def test_fields_resolve_exact_printed_objects_without_ocr(self):
        r=read_invoice(self.fixture(),USTC['title'],USTC['tax_id']);p=r['pages'][0]
        self.assertEqual(p['total_amount'],'106.00');self.assertEqual(p['net_amount'],'100.00')
        self.assertEqual(p['buyer_title'],USTC['title']);self.assertTrue(p['validation']['tax_id_matches_expected'])
        self.assertEqual(p['extraction_method'],'ofd_semantic_tags_and_page_text');self.assertFalse(r['authenticity_verified'])

    def test_conflicting_number_and_unclosed_amount_are_rejected(self):
        for change in [{'InvoiceNumber':'22345678901234567890'},{'TotalTax-includedAmount':'107.00'}]:
            with self.subTest(change=change),self.assertRaises(ValueError):read_invoice(self.fixture(change))

if __name__=='__main__':unittest.main()
