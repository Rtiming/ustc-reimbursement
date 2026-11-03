from pathlib import Path
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from assistant_api import AssistantService, read_json
from material_intake_service import configure_bundled_archive


class ReceiveMaterialsTests(unittest.TestCase):
    def test_path_only_intake_uses_original_worker_and_compact_continuation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            materials = root / "materials"
            materials.mkdir()
            source = root / "桌面合成材料.txt"
            source.write_text("synthetic original", encoding="utf-8")
            service = AssistantService(root, [materials])
            service.new_case("fixture")
            configure_bundled_archive(root)
            planned = service.assist(
                "call",
                operation="receive_materials",
                case_id="fixture",
                inputs={"paths": [str(source)], "request_id": "preview-once"},
            )
            self.assertFalse(Path(planned["output_dir"]).exists())
            self.assertFalse((materials / "原始收件").exists())
            step = planned["next_action"]
            self.assertEqual(step["tool"], "reimbursement_assist")
            plan = service.assist(**step["arguments"])
            self.assertFalse(plan['financial_execute_requested'])
            done = service.assist(
                "confirm",
                intent_id=plan["intent_id"],
                confirmation="用户要求归档这一份合成材料",
            )
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                result = service.result("fixture", done["job_id"])
                if result["status"] not in {"queued", "running"}:
                    break
                time.sleep(0.1)
            self.assertEqual(result["status"], "completed")
            request = read_json(
                service.job_folder("fixture", done["job_id"]) / "request.json"
            )
            self.assertEqual(request["operation"], "material_intake")
            self.assertTrue(request["execute"])
            self.assertTrue(
                Path(
                    request["parameters"]["request"]["sources"][0]["path"]
                ).is_relative_to(materials)
            )
            files = service.intake_review("fixture", done["job_id"])
            self.assertEqual(files["total_materials"], 1)
            self.assertEqual(source.read_text(encoding="utf-8"), "synthetic original")
            with self.assertRaises(ValueError):
                service.receive_materials(
                    "fixture", [str(source), str(source)], "duplicate"
                )
            with self.assertRaises(ValueError):
                service.receive_materials("fixture", [str(root)], "outside")

    def test_directory_changes_and_overlapping_sources_are_not_implicitly_approved(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            materials = root / "materials"
            materials.mkdir()
            external = root / "桌面"
            external.mkdir()
            source = external / "a.txt"
            source.write_text("one", encoding="utf-8")
            service = AssistantService(root, [materials])
            service.new_case("fixture")
            planned = service.receive_materials("fixture", [str(external)], "p")
            args = planned["next_action"]["arguments"]
            (external / "new.txt").write_text("not reviewed", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "改变"):
                service.receive_materials(**args)
            self.assertFalse((materials / "原始收件").exists())
            with self.assertRaisesRegex(ValueError, "重复来源"):
                service.receive_materials(
                    "fixture", [str(external), str(source)], "overlap"
                )

    def test_confirmation_freezes_current_bytes_without_relaxing_other_material_roots(
        self,
    ):
        from operator_workbench import Workbench

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            materials = root / "materials"
            materials.mkdir()
            source = root / "a.txt"
            source.write_text("one", encoding="utf-8")
            service = AssistantService(root, [materials])
            service.new_case("fixture")
            app = Workbench(service)
            prepared = app.prepare(
                "reimbursement_receive_materials",
                {
                    "case_id": "fixture",
                    "paths": [str(source)],
                    "request_id": "freeze",
                    "execute": True,
                },
            )
            source.write_text("two", encoding="utf-8")
            result = app.execute(prepared["intent_id"], "确认第一版原件")
            self.assertEqual(result["error_code"], "receive_source_changed")
            self.assertFalse((materials / "原始收件").exists())
            with self.assertRaises(ValueError):
                service.material_path(str(source))


if __name__ == "__main__":
    unittest.main()
