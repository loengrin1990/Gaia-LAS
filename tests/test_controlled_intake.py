from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gaia.controlled_intake import ControlledIntake
from gaia.provenance import ProvenanceError, ProvenanceStore


class ControlledIntakeTests(unittest.TestCase):
    def test_new_materials_are_isolated_versioned_and_survive_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "storage"
            intake = ControlledIntake(ProvenanceStore(root))
            intake.set_dictionary("synthetic-first", {"Организация": ["Организация Альфа"]})
            first = intake.admit("synthetic-first", [("brief.txt", b"synthetic material")])
            source = first["materials"][0]
            self.assertFalse(source["duplicate"])
            self.assertTrue(source["artifact_id"].startswith("art_"))
            self.assertEqual(intake.dictionary("synthetic-first")["Организация"], ["Организация Альфа"])
            self.assertEqual(intake.lineage("synthetic-first", source["artifact_id"])["source_id"], source["source_id"])
            with self.assertRaises(ProvenanceError):
                intake.admit("synthetic-first", [("brief.txt", b"synthetic material")])
            isolated = intake.admit("synthetic-second", [("brief.txt", b"synthetic material")])
            self.assertNotEqual(source["source_id"], isolated["materials"][0]["source_id"])
            changed = intake.admit("synthetic-first", [("brief.txt", b"synthetic revision")])
            self.assertEqual(changed["materials"][0]["status"], "new_version")
            self.assertEqual(ControlledIntake(ProvenanceStore(root)).operation("synthetic-first", first["operation_id"])["status"], "accepted")
            reprocessed = intake.reprocess_protection("synthetic-first", source["artifact_id"], "deterministic-v2")
            self.assertFalse(intake.store.object_metadata(intake._workspace_for("synthetic-first"), source["sanitized_id"])["current"])
            self.assertTrue(intake.store.object_metadata(intake._workspace_for("synthetic-first"), reprocessed["artifact_id"])["current"])
            self.assertFalse(reprocessed["export_allowed"])
            self.assertNotIn("text", intake.protection_metadata("synthetic-first", reprocessed["artifact_id"]))
            self.assertEqual(intake.protection_lineage("synthetic-first", reprocessed["artifact_id"])["source_id"], source["source_id"])
            updated = intake.add_dictionary_value("synthetic-first", reprocessed["artifact_id"], "Организация", "Организация Альфа")
            self.assertTrue(intake.store.object_metadata(intake._workspace_for("synthetic-first"), updated["artifact_id"])["current"])
            with self.assertRaises(ProvenanceError):
                intake.metadata("synthetic-second", source["source_id"])

    def test_missing_workspace_is_rejected_before_a_source_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            intake = ControlledIntake(ProvenanceStore(Path(tmp) / "storage"))
            with self.assertRaises(ProvenanceError):
                intake.admit("", [("brief.txt", b"synthetic")])

    def test_unsupported_or_binary_upload_is_rejected_before_controlled_admission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProvenanceStore(Path(tmp) / "storage")
            intake = ControlledIntake(store)
            for filename, content in (
                ("brief.docx", b"not a controlled DOCX"),
                ("table.xlsx", b"not a controlled XLSX"),
                ("recording.mp3", b"not a controlled recording"),
                ("binary.txt", b"\x00\x01not text"),
            ):
                with self.subTest(filename=filename), self.assertRaises(ProvenanceError):
                    intake.admit("format-control", [(filename, content)])
                self.assertEqual(store._registry()["workspaces"], {})
                self.assertEqual(store._registry()["objects"], {})

    def test_pdf_runtime_is_checked_before_a_source_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProvenanceStore(Path(tmp) / "storage")
            intake = ControlledIntake(store)
            with patch("gaia.controlled_intake.MaterialReader.ensure_pdf_runtime", side_effect=ProvenanceError("PDF-обработка недоступна")):
                with self.assertRaisesRegex(ProvenanceError, "PDF-обработка недоступна"):
                    intake.admit("pdf-runtime-control", [("brief.pdf", b"%PDF-synthetic")])
            self.assertEqual(store._registry()["workspaces"], {})
            self.assertEqual(store._registry()["objects"], {})
