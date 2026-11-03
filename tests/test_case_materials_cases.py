"""????????????????????????????"""
import unittest
from pathlib import Path
from test_case_materials import CaseMaterialsTests as Fixture
from assistant_api import read_json
from case_invoice_sources import build_manifest

class MaterializedCasesTests(unittest.TestCase):
    setUp=Fixture.setUp
    source=Fixture.source
    save=Fixture.save
    document=Fixture.document
    expense=Fixture.expense
    rules=Fixture.rules

    def prepared(self, count=51):
        self.rules()
        self.save("save_document",self.document())
        self.save("save_expenses",[self.expense(i) for i in range(count)])
        return self.s.case_materials(self.case,"materialize")

    def confirm(self, preview):
        return self.s.case_materials(self.case,"materialize",
            {"plan_sha256":preview["plan_sha256"]},preview["revision"],"?????????",True)

    def test_51_forms_materialize_to_26_25_source_bound_cases_and_replay(self):
        preview=self.prepared()
        self.assertEqual(len(self.s.cases()["cases"]),1)
        result=self.confirm(preview)
        self.assertEqual(sorted(x["invoice_count"] for x in result["cases"]),[25,26])
        self.assertEqual(len(self.s.cases()["cases"]),3)
        for row in result["cases"]:
            _,paths=self.s.resolve(row["case_id"])
            form=read_json(paths["form"]); case=read_json(paths["case"])
            self.assertEqual(form["amount"],row["amount"])
            self.assertFalse(form["funding"])
            self.assertIsNone(form["serial"])
            manifest=build_manifest(case,row["case_id"],self.s.material_roots)
            self.assertEqual(len(manifest["original_files"]),row["invoice_count"])
            self.assertEqual(set(r["invoice_number_candidates"][0] for r in manifest["original_files"]),set(row["invoice_numbers"]))
            for item in manifest["original_files"]:
                import fitz
                with fitz.open(item["path"]) as pdf:self.assertEqual(len(pdf),1)
        replay=self.s.case_materials(self.case,"materialize")
        self.assertTrue(replay["replayed"])
        self.assertEqual(result["cases"],replay["cases"])
        self.assertEqual(len(self.s.cases()["cases"]),3)

    def test_recover_after_child_published_preserves_its_edits_and_source_change_rejects(self):
        result=self.confirm(self.prepared(2))
        child=result["cases"][0]["case_id"]
        self.s.update_case(child,"form",{"basic":{"fields":{"t-req_mobile":"synthetic-phone"}}},self.s.revision(child))
        receipt=self.root/".local/materialized-cases"/self.case/"receipt.json"
        receipt.unlink()
        recovered=self.confirm(self.s.case_materials(self.case,"materialize"))
        self.assertEqual(recovered["cases"][0]["case_id"],child)
        self.assertEqual(self.s.case_details(child)["values"]["phone"],"synthetic-phone")
        source=__import__("case_materials").catalog(self.s,self.case)[0]
        original=__import__("intake_review").bound_file(self.s,self.case,source["job_id"],source["material_id"])[0]
        original.write_bytes(b"changed")
        with self.assertRaises(ValueError):self.s.case_materials(self.case,"materialize")

    def test_stale_or_unconfirmed_plan_cannot_create_and_dispositions_are_explicit(self):
        preview=self.prepared(1)
        with self.assertRaises(ValueError):
            self.s.case_materials(self.case,"materialize",{"plan_sha256":"0"*64},preview["revision"],"bad",True)
        self.assertEqual(len(self.s.cases()["cases"]),1)
        with self.assertRaises(ValueError):
            self.save("save_dispositions",[{"source":self.source(1),"disposition":"paper","reason":"????"}])
        saved=self.save("save_dispositions",[{"source":self.source(1),"disposition":"paper","reason":"????","paper_location":"?????"}])
        self.assertEqual(saved["dispositions"][0]["paper_location"],"?????")

    def test_completed_held_form_can_be_added_without_recreating_existing_case(self):
        self.rules();self.save("save_document",self.document())
        expenses=[self.expense(i) for i in range(51)];expenses[0]["payees"]=[]
        self.save("save_expenses",expenses)
        first=self.confirm(self.s.case_materials(self.case,"materialize"))
        self.assertEqual(len(first["cases"]),1)
        first_child=first["cases"][0]["case_id"]
        self.save("save_expenses",[self.expense(0)])
        # ???????????????????????????
        preview=self.s.case_materials(self.case,"materialize")
        second=self.confirm(preview)
        self.assertEqual(len(second["cases"]),2)
        self.assertIn(first_child,[row["case_id"] for row in second["cases"]])
        self.assertEqual(len(self.s.cases()["cases"]),3)

    def test_published_orphan_prevents_changed_plan_with_overlapping_invoices(self):
        first=self.confirm(self.prepared())
        receipt=self.root/".local/materialized-cases"/self.case/"receipt.json";receipt.unlink()
        case,paths=self.s.resolve(self.case)
        record=read_json(paths["case"]);record["material_context"]["workbench_materials"]["expenses"]=[self.expense(0)]
        __import__("assistant_api").atomic_json(paths["case"],record)
        with self.assertRaises(ValueError):self.s.case_materials(self.case,"materialize")
        self.assertEqual(len(self.s.cases()["cases"]),3)


del Fixture
