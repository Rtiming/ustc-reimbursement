import sys,tempfile,unittest,zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from invoice_audit import ofd_railway_fields,USTC

class RailwayOfd(unittest.TestCase):
    def fixture(self,changes=None,printed_changes=None):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);p=Path(t.name)/'ticket.ofd'
        fields={'ElectronicInvoiceRailwayETicketNumber':'12345678901234567890','NameOfPurchaser':USTC['title'],'UnifiedSocialCreditCodeOfPurchaser':USTC['tax_id'],'Name':'示例同学','measure':'iso4217:CNY','TypeOfBusiness':'售','DateOfIssue':'2026-06-18','TravelDate':'2026-06-11','Fare':'106.00','TotalAmountExcludingTax':'100.00','TaxAmount':'6.00','DepartureStation':'合肥南','DestinationStation':'上海虹桥','SeatLevel':'二等座','TrainNumber':'G100','DepartureTime':'08:30','NumberOfOriginalInvoice':'22345678901234567890'}
        printed={**fields,**(printed_changes or {})};fields.update(changes or {})
        xml='<x>'+''.join('<'+k+'>'+v+'</'+k+'>'for k,v in fields.items())+'</x>'
        with zipfile.ZipFile(p,'w')as z:z.writestr('Doc_0/Attachs/ticket.xml',xml)
        return p,[{'text':'\n'.join(printed.values())}]
    def test_current_number_and_actual_travel_date(self):
        p,pages=self.fixture();r=ofd_railway_fields(p,pages,USTC['title'],USTC['tax_id'])
        self.assertEqual(r['invoice_number_candidates'],['12345678901234567890'])
        self.assertEqual(r['referenced_invoice_numbers'],['22345678901234567890'])
        self.assertEqual(r['native_railway_fields']['TravelDate'],'2026-06-11')
        self.assertEqual(r['total_amount'],'106.00')
    def test_amount_or_printed_number_conflict_rejected(self):
        for change in [{'Fare':'107.00'},{'ElectronicInvoiceRailwayETicketNumber':'32345678901234567890'}]:
            p,pages=self.fixture(change)
            with self.assertRaises(ValueError):ofd_railway_fields(p,pages)
    def test_entity_rejected(self):
        p,pages=self.fixture()
        with zipfile.ZipFile(p,'w')as z:z.writestr('Doc_0/Attachs/ticket.xml','<!DOCTYPE x [<!ENTITY x SYSTEM "file:///example">]><x>&x;</x>')
        with self.assertRaises(ValueError):ofd_railway_fields(p,pages)

if __name__=='__main__':unittest.main()
