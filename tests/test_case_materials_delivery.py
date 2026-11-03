"""真实合成归档→本地清单与逐页打印；不连接学校。"""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import test_case_materials as base
from case_materials_delivery import run, download
from assistant_api import read_json
from intake_review import bound_file


class MaterialDeliveryTests(unittest.TestCase):
    def setUp(self):
        import fitz
        new_page = fitz.Document.new_page
        def mixed_pages(doc, *args, **kwargs):
            if not args and not kwargs and len(doc) == 0:
                kwargs = {"width": 842, "height": 595}
            return new_page(doc, *args, **kwargs)
        with patch.object(fitz.Document, "new_page", mixed_pages):
            base.CaseMaterialsTests.setUp(self)
    source = base.CaseMaterialsTests.source
    save = base.CaseMaterialsTests.save
    expense = base.CaseMaterialsTests.expense
    document = base.CaseMaterialsTests.document
    rules = base.CaseMaterialsTests.rules

    def setup_materials(self, *, paper_invoice=False, missing=False):
        self.rules()
        self.save("save_document", self.document())
        expenses = [self.expense(i) for i in range(2)]
        if missing:
            expenses[0]["payees"] = []
            expenses[0]["payment_materials"] = []
        self.save("save_expenses", expenses)
        dispositions = []
        for page in range(1, 55):
            mode = "electronic" if page in {1, 2, 52, 53, 54} else "exclude"
            if paper_invoice and page == 2:
                mode = "paper"
            row = {"source": self.source(page), "disposition": mode,
                   "reason": "合成测试按原件确认材料去向"}
            if mode == "paper":
                row["paper_location"] = "合成纸票待随附"
            dispositions.append(row)
        self.s.update_case(self.case, "case", {"material_context": {"workbench_materials": {"dispositions": dispositions}}}, self.s.revision(self.case))

    def build(self):
        return run(self.s, self.case, True, self.s.revision(self.case), "合成范围与纸电逐页核对")

    def test_real_a4_packet_common_payment_once_and_full_xlsx(self):
        import fitz
        from openpyxl import load_workbook
        self.setup_materials()
        before = self.s.revision(self.case)
        preview = run(self.s, self.case)
        self.assertTrue(preview["preview"])
        self.assertTrue(preview["can_build_pdf"])
        self.assertEqual(self.s.revision(self.case), before)
        result = self.build()
        self.assertEqual(result["packet_pages"], 5)
        self.assertFalse(result["official_information_page_included"])
        folder = Path(result["output_directory"])
        index = read_json(folder / "附件逐页索引.json")
        self.assertEqual([r["role"] for r in index["pages"]], ["supporting", "invoice", "invoice", "itinerary", "payment"])
        self.assertEqual([r["page_orientation"] for r in index["pages"]], ["portrait", "landscape", "portrait", "portrait", "portrait"])
        self.assertEqual(sum(r["page"] == 52 for r in index["pages"]), 1)
        with fitz.open(folder / "附件单面打印包.pdf") as doc:
            self.assertEqual(len(doc), 5)
            self.assertTrue(all(abs(sorted([p.rect.width, p.rect.height])[0] - 595.276) < 0.1 for p in doc))
            self.assertIn("Simplex", doc.xref_get_key(doc.pdf_catalog(), "ViewerPreferences")[1])
            self.assertTrue(all(p.get_pixmap().width > 0 for p in doc))
        book = load_workbook(folder / "报销清单.xlsx", data_only=False, read_only=True)
        self.assertEqual(book.sheetnames[:4], ["问题总结", "拟收款核对", "材料及金额明细", "纸质登记"])
        self.assertEqual(book["材料及金额明细"].cell(6, 3).value, "99000000000000000000")
        self.assertEqual(book["材料及金额明细"].cell(6, 3).data_type, "s")
        book.close()
        artifact = download(self.s, self.case, result["delivery_id"], "报销清单.xlsx")
        self.assertEqual(Path(artifact["path"]), folder / "报销清单.xlsx")
        self.assertTrue(self.build()["reused"])

    def test_paper_invoice_only_registered_missing_payment_and_payee_stay_unknown(self):
        from openpyxl import load_workbook
        self.setup_materials(paper_invoice=True, missing=True)
        result = self.build()
        self.assertEqual(result["scope"]["paper_pages"], 1)
        self.assertEqual(result["packet_pages"], 4)
        self.assertTrue(any(r["问题编号"].startswith("PAYEE-") for r in result["issues"]))
        self.assertTrue(any(r["问题编号"].startswith("PAYMENT-") for r in result["issues"]))
        folder = Path(result["output_directory"])
        index = read_json(folder / "附件逐页索引.json")
        self.assertFalse(any(r["page"] == 1 for r in index["pages"]))
        book = load_workbook(folder / "报销清单.xlsx", read_only=True)
        self.assertIsNone(book["材料及金额明细"].cell(6, 5).value)
        self.assertEqual(book["纸质登记"].cell(6, 3).value, "合成纸票待随附")
        book.close()

    def test_unreviewed_carrier_exports_clear_issues_without_wrong_pdf(self):
        self.rules()
        self.save("save_document", self.document())
        self.save("save_expenses", [self.expense()])
        preview = run(self.s, self.case)
        self.assertFalse(preview["can_build_pdf"])
        self.assertEqual(preview["scope"]["unreviewed_pages"], 54)
        result = self.build()
        names = [x["name"] for x in result["artifacts"]]
        self.assertIn("报销清单.xlsx", names)
        self.assertNotIn("附件单面打印包.pdf", names)
        self.assertEqual(result["packet_pages"], 0)

    def test_changed_source_or_wrong_revision_and_download_traversal_rejected(self):
        self.setup_materials()
        with self.assertRaisesRegex(ValueError, "已改变"):
            run(self.s, self.case, True, "stale", "合成")
        result = self.build()
        with self.assertRaises(ValueError):
            download(self.s, self.case, result["delivery_id"], "../form.json")
        with self.assertRaises(ValueError):
            download(self.s, self.case, result["delivery_id"], "不存在.pdf")
        path = bound_file(self.s, self.case, self.initial["materials"][0]["job_id"], self.initial["materials"][0]["material_id"])[0]
        path.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            run(self.s, self.case)

    def test_tampered_artifact_never_rebuilt_or_downloaded(self):
        self.setup_materials()
        result = self.build()
        (Path(result["output_directory"]) / "报销清单.xlsx").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "已改变"):
            self.build()
        with self.assertRaisesRegex(ValueError, "已改变"):
            download(self.s, self.case, result["delivery_id"], "报销清单.xlsx")

    def test_same_sha_page_alias_deduplicated_and_paper_conflict_rejected(self):
        from case_materials import catalog, SourceCatalog
        self.setup_materials()
        original = catalog(self.s, self.case)
        alias_key = original[0]["key"] + "-alias"
        def aliases(service, case_id):
            rows = SourceCatalog()
            rows.extend(copy.deepcopy(original))
            rows.append({**copy.deepcopy(original[0]), "key": alias_key})
            return rows
        _, paths = self.s.resolve(self.case)
        data = read_json(paths["case"])["material_context"]["workbench_materials"]
        alias_dispositions = [{**copy.deepcopy(x), "source": {"key": alias_key, "page": x["source"]["page"]}} for x in data["dispositions"]]
        data["dispositions"] += alias_dispositions
        data["expenses"][1]["payment_materials"] = [{"key": alias_key, "page": 53}]
        self.s.update_case(self.case, "case", {"material_context": {"workbench_materials": data}}, self.s.revision(self.case))
        with patch("case_materials_delivery.catalog", aliases), patch("case_materials.catalog", aliases):
            result = self.build()
            self.assertEqual(result["packet_pages"], 5)
            data["dispositions"][-2]["disposition"] = "paper"
            self.s.update_case(self.case, "case", {"material_context": {"workbench_materials": data}}, self.s.revision(self.case))
            with self.assertRaisesRegex(ValueError, "纸电去向冲突"):
                run(self.s, self.case)


if __name__ == "__main__":
    unittest.main()
