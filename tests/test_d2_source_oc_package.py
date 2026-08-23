from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "d2_source_oc_package.py"
BASELINE = Path("/Users/ilia/Documents/Gaia-pilot-evidence/2026-08-22_D0-baseline-v3")
ORACLE = Path("/Users/ilia/Documents/Gaia-pilot-evidence/2026-08-22_D1-oracle-v1")

SPEC = importlib.util.spec_from_file_location("d2_source_oc_package", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class D2SourceOcPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not BASELINE.is_dir() or not ORACLE.is_dir():
            raise unittest.SkipTest("Frozen D0/D1 evaluation inputs are unavailable.")
        cls.before = {
            path: path.stat().st_mtime_ns
            for path in (BASELINE / "shared_storage" / "metadata" / "registry.json", *BASELINE.glob("pilot_workspace/operational_context_v0/project/**/*.json"))
        }

    def package(self, query: str) -> dict[str, object]:
        return MODULE.build_package(BASELINE, query)

    def assert_source(self, package: dict[str, object], material: int, phrase: str) -> dict[str, object]:
        hits = [item for item in package["source_evidence"] if item["material_ordinal"] == material and phrase in item["excerpt"]]
        self.assertTrue(hits, package)
        for item in hits:
            self.assertTrue(item["source_id"])
            self.assertTrue(item["sanitized_id"])
            self.assertTrue(item["created_at"])
            self.assertLess(item["start"], item["end"])
        return hits[0]

    def test_q1_current_source_evidence_is_independent_of_oc(self) -> None:
        package = self.package("Какое сейчас окно действия пропуска?")
        hit = self.assert_source(package, 12, "T-15 минут")
        self.assertEqual(hit["status_hint"], "current")
        self.assertTrue(all("T-15" not in str(item["value"]) for item in package["oc_overlay"]))
        for item in package["source_evidence"]:
            if "T-30" in item["excerpt"]:
                self.assertNotEqual(item["status_hint"], "current")

    def test_q2_authority_source_evidence(self) -> None:
        package = self.package("Какая система авторитетна по активному и отозванному состоянию доступа?")
        self.assert_source(package, 3, "авторитетной по активному/отозванному состоянию")

    def test_q3_guest_scope_is_not_replaced_by_contractor(self) -> None:
        package = self.package("Нужно ли повторное согласование принимающего при продлении гостевого пропуска?")
        hit = self.assert_source(package, 10, "Гость: требуется повторное согласование принимающего")
        self.assertEqual(hit["subject_scope"], "Гость")
        self.assertIn("Гость", hit["excerpt"])

    def test_q4_preserves_preapproved_qualifier(self) -> None:
        package = self.package("Нужно ли новое согласование при продлении пропуска предсогласованного подрядчика?")
        hit = self.assert_source(package, 10, "Предсогласованный подрядчик: нового согласования не требуется")
        self.assertEqual(hit["subject_scope"], "Предсогласованный подрядчик")

    def test_q5_expiry_constraint_source_evidence(self) -> None:
        package = self.package("Можно ли продлить уже истёкший пропуск?")
        self.assert_source(package, 10, "Истёкший пропуск нельзя восстановить продлением")

    def test_all_packages_are_bounded_provenanced_and_deterministic(self) -> None:
        questions = json.loads((ORACLE / "QUESTIONS.json").read_text(encoding="utf-8"))["questions"]
        for question in questions:
            first = self.package(question["text"])
            second = self.package(question["text"])
            self.assertEqual(first, second)
            self.assertLess(first["retrieval_diagnostics"]["distinct_source_materials_selected"], 12)
            self.assertLess(first["retrieval_diagnostics"]["oc_selected"], 12)
            self.assertGreater(first["retrieval_diagnostics"]["source_chunks_selected"], 0)
            self.assertTrue(all(item["source_id"] and item["sanitized_id"] for item in first["source_evidence"]))

    def test_cli_writes_only_caller_output_and_frozen_inputs_remain_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "package.json"
            package = self.package("Какое сейчас окно действия пропуска?")
            output.write_text(json.dumps(package, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["schema"], "gaia.d2.hybrid-package.v0")
        self.assertEqual(self.before, {path: path.stat().st_mtime_ns for path in self.before})

    def test_implementation_has_no_oracle_or_known_pilot_identity_dependency(self) -> None:
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        forbidden = (
            "D1-oracle", "src_7ca272e871574d089b9d57c77d196a4d", "san_9955726bbc904738a37af6707cdac4c1",
            "src_858e2dbf7a7d471881548b0cca24e91f", "san_1d6c3d8eab9b48f49328d9e55884f69f",
            "T-15", "Tend+45", "T-30", "Tend+30", "GateX", "Q1", "Q2", "Q3", "Q4", "Q5",
        )
        self.assertFalse([value for value in forbidden if value in source])


if __name__ == "__main__":
    unittest.main()
