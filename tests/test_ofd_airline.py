"""合成航空OFD：原生XML、页文字、金额及案件来源必须同时吻合。"""
import hashlib
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from xml.etree.ElementTree import Element, SubElement, tostring

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from invoice_audit import USTC, read_invoice
from case_invoice_sources import CANDIDATE_STATUS, audit_case


class OfdAirlineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.path=self.root/'source.ofd'
        self.fields={'ElectronicInvoiceAirTransportReceiptNumber':'99000000000000123456',
            'PassengerName':'合成旅客','IssueDate':'2026-01-02','NameOfPurchaser':USTC['title'],
            'UnifiedSocialCreditCodeOfPurchaser':USTC['tax_id'],'NameOfSeller':'合成航空公司',
            'measure':'iso4217:CNY','IssuingStatus':'正常','Fare':'100.00','FuelSurcharge':'20.00',
            'VatTaxAmount':'10.80','CivilAviationDevelopmentFund':'50.00','OtherTaxes':'0.00','TotalAmount':'180.80',
            'CarrierDate':'2026-01-01','DepartureStation':'合成甲机场','DestinationStation':'合成乙机场',
            'Flight':'MU9000','Class':'Y','ETicketNumber':'9990000000000'}

    def write(self, fields=None, page_fields=None, duplicate=False, blank=False):
        fields=fields or self.fields;shown=page_fields or fields
        root=Element('xbrl')
        for key,value in fields.items():SubElement(root,key).text=value
        page=Element('Page')
        if not blank:
            values=[shown[k] for k in ['ElectronicInvoiceAirTransportReceiptNumber','NameOfPurchaser',
                'UnifiedSocialCreditCodeOfPurchaser','PassengerName']]+['2026年01月02日']
            values+=['CNY '+shown[k] for k in ['Fare','FuelSurcharge','VatTaxAmount',
                'CivilAviationDevelopmentFund','OtherTaxes','TotalAmount']]
            for text in values:SubElement(page,'TextCode').text=text
        with zipfile.ZipFile(self.path,'w') as z:
            z.writestr('Doc_0/Pages/Page_0/Content.xml',tostring(page))
            z.writestr('Doc_0/Attachs/airline.xml',tostring(root))
            if duplicate:z.writestr('Doc_0/Attachs/second.xml',tostring(root))

    def test_native_fields_are_cross_checked_without_ocr_or_authenticity_claim(self):
        self.write()
        with patch('invoice_audit.ocr_image',side_effect=AssertionError('no OCR')):
            result=read_invoice(self.path,USTC['title'],USTC['tax_id'])
        p=result['pages'][0]
        self.assertEqual(p['invoice_number_candidates'],[self.fields['ElectronicInvoiceAirTransportReceiptNumber']])
        self.assertEqual(p['total_amount'],'180.80');self.assertIsNone(p['net_amount'])
        self.assertEqual(p['tax_amount'],'10.80');self.assertEqual(p['aviation_components']['civil_aviation_fund'],'50.00')
        self.assertTrue(p['validation']['title_matches_expected']);self.assertTrue(p['validation']['tax_id_matches_expected'])
        self.assertEqual(p['native_airline_fields']['PassengerName'],'合成旅客')
        self.assertFalse(result['authenticity_verified']);self.assertFalse(result['system_write'])

    def test_page_xml_mismatch_and_component_difference_are_rejected(self):
        self.write(page_fields={**self.fields,'TotalAmount':'181.00'})
        with self.assertRaisesRegex(ValueError,'页面文字'):read_invoice(self.path)
        self.write(fields={**self.fields,'TotalAmount':'181.00'})
        with self.assertRaisesRegex(ValueError,'分项合计'):read_invoice(self.path)

    def test_wrong_buyer_is_retained_as_mismatch_not_passed(self):
        self.write(fields={**self.fields,'NameOfPurchaser':'合成其他单位'})
        row=read_invoice(self.path,USTC['title'],USTC['tax_id'])['pages'][0]
        self.assertFalse(row['validation']['title_matches_expected'])
        self.assertIn('EXPECTED_BUYER_MISMATCH',row['issues'])

    def test_ambiguous_xml_and_unsupported_status_are_rejected(self):
        self.write(duplicate=True)
        with self.assertRaisesRegex(ValueError,'唯一'):read_invoice(self.path)
        self.write(fields={**self.fields,'IssuingStatus':'作废'})
        with self.assertRaisesRegex(ValueError,'状态'):read_invoice(self.path)

    @unittest.skipUnless(shutil.which('pdftotext'),'native audit adapter requires pdftotext')
    def test_case_audit_runs_existing_cli_with_bound_ofd_source(self):
        self.write();number=self.fields['ElectronicInvoiceAirTransportReceiptNumber']
        case={'case_id':'ofd_fixture','materials':[{'path':str(self.path),
            'sha256':hashlib.sha256(self.path.read_bytes()).hexdigest(),'status':CANDIDATE_STATUS,
            'invoice_records':[{'number':number,'page':1}]}]}
        out=self.root/'audit/result.json'
        result=audit_case(case,'ofd_fixture',[self.root],[number],out)
        self.assertTrue(result['source_bindings_verified']);self.assertFalse(result['selected'])
        self.assertEqual(result['reports'][0]['pages'][0]['extraction_method'],'ofd_text_and_embedded_xml')
        self.assertEqual(result['reports'][0]['sha256'],case['materials'][0]['sha256'])

    @unittest.skipUnless(shutil.which('pdftotext'),'native audit adapter requires pdftotext')
    def test_empty_ofd_text_never_bypasses_preflight(self):
        self.write(blank=True);number=self.fields['ElectronicInvoiceAirTransportReceiptNumber']
        case={'case_id':'ofd_fixture','materials':[{'path':str(self.path),
            'sha256':hashlib.sha256(self.path.read_bytes()).hexdigest(),'status':CANDIDATE_STATUS,
            'invoice_records':[{'number':number,'page':1}]}]}
        with self.assertRaisesRegex(ValueError,'原生文字'):
            audit_case(case,'ofd_fixture',[self.root],[number],self.root/'audit/result.json')


if __name__=='__main__':unittest.main()
