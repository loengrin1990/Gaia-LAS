from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import tempfile
import unittest

from gaia.context_compiler import ContextCompiler
from gaia.controlled_intake import ControlledIntake
from gaia.material_reader import MaterialReader
from gaia.provenance import ProvenanceStore
from gaia.provenance import ProvenanceError
from gaia.review import ReviewService


FIXTURE_ENV = "RPM_ACCEPTANCE_FIXTURE"


class MaterialReaderAcceptanceTests(unittest.TestCase):
    def fixture(self) -> Path:
        value = os.environ.get(FIXTURE_ENV, "")
        if not value:
            self.skipTest(f"Set {FIXTURE_ENV} to run the exact RPM-1 acceptance fixture.")
        path = Path(value)
        if not path.is_file():
            self.skipTest(f"The configured RPM-1 fixture is unavailable: {path}")
        return path

    @unittest.skipUnless(shutil.which("tesseract"), "Tesseract is required for the visual acceptance case.")
    def test_mixed_fixture_reaches_text_visual_cross_modal_context_with_provenance(self) -> None:
        fixture = self.fixture()
        reader = MaterialReader()
        result = reader.read_pdf(fixture.read_bytes())

        # Textual control: native PDF text stays usable; it is not raster-only.
        self.assertIn("Техническая архитектура", result.text)
        self.assertTrue(any(item.modality == "text" and item.state == "ready" for item in result.evidence))
        self.assertTrue(any(item.modality == "table_layout" and item.state == "ready" for item in result.evidence))

        # Visual-only: this label comes from the embedded diagram image, which
        # native page text does not expose. No fixture-specific mapping is used.
        visual = [item for item in result.evidence if item.modality == "visual"]
        self.assertTrue(visual)
        self.assertTrue(any(item.state == "ready" and "ocr service" in item.text.casefold() for item in visual))

        temporary = tempfile.TemporaryDirectory()
        try:
            store = ProvenanceStore(Path(temporary.name) / "storage")
            intake = ControlledIntake(store)
            admitted = intake.admit("rpm-1-acceptance", [(fixture.name, fixture.read_bytes())])
            workspace = admitted["workspace_id"]
            material = admitted["materials"][0]
            source = store.source_metadata(workspace, material["source_id"])
            sanitized = store.object_metadata(workspace, material["sanitized_id"])
            review = ReviewService(store, workspace, lambda _: {"status": "completed", "findings": []})
            review.start(sanitized["artifact_id"])
            review.confirm(sanitized["artifact_id"])

            def model(text: str) -> dict[str, object]:
                visual_match = re.search(r"Frontend SPA", text)
                if visual_match is not None:
                    return {"candidates": [{
                        "type": "requirement",
                        "title": "Визуальный компонент Frontend SPA",
                        "statement": "На архитектурной схеме присутствует Frontend SPA.",
                        "block": {"start": visual_match.start(), "end": visual_match.end()},
                        "confidence": "high",
                        "requires_review": True,
                    }]}
                match = re.search(r"Backend Service \(FastAPI\)\s+OCR Service", text)
                if match is None:
                    return {"candidates": []}
                return {"candidates": [{
                    "type": "requirement",
                    "title": "Передача документов в OCR",
                    "statement": "Backend Service передаёт документы в OCR Service.",
                    "block": {"start": match.start(), "end": match.end()},
                    "confidence": "high",
                    "requires_review": True,
                }]}

            context = ContextCompiler(store, workspace, model).compile(sanitized["artifact_id"])
            self.assertEqual(len(context), 2)
            visual_context = next(item for item in context if item["title"] == "Визуальный компонент Frontend SPA")
            visual_support = intake.context_evidence("rpm-1-acceptance", visual_context["id"])
            self.assertEqual({item["modality"] for item in visual_support}, {"visual"})
            self.assertEqual(visual_support[0]["locator"], {"kind": "embedded_image", "page": 1, "image_index": 1})
            cross_modal = next(item for item in context if item["title"] == "Передача документов в OCR")
            support = intake.context_evidence("rpm-1-acceptance", cross_modal["id"])
            self.assertIn("table_layout", {item["modality"] for item in support})
            self.assertIn("visual", {item["modality"] for item in support})
            self.assertTrue(all(item["page"] >= 1 and item["locator"] for item in support))
            self.assertEqual(store.lineage(workspace, visual_context["id"])["source_id"], source["source_id"])
            self.assertEqual(store.lineage(workspace, cross_modal["id"])["source_id"], source["source_id"])
        finally:
            temporary.cleanup()

    def test_visual_failure_is_partial_and_preserves_native_text_and_table(self) -> None:
        fixture = self.fixture()
        result = MaterialReader(ocr_command="rpm-1-absent-ocr").read_pdf(fixture.read_bytes())
        self.assertIn("Техническая архитектура", result.text)
        self.assertTrue(any(item.modality == "table_layout" and item.state == "ready" for item in result.evidence))
        self.assertTrue(any(item.modality == "visual" and item.state == "unsupported" for item in result.evidence))

    def test_text_only_intake_preserves_existing_extraction_path(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        try:
            store = ProvenanceStore(Path(temporary.name) / "storage")
            intake = ControlledIntake(store)
            result = intake.admit("rpm-text-control", [("control.txt", b"ordinary text-only material")])
            artifact_id = result["materials"][0]["artifact_id"]
            workspace = result["workspace_id"]
            text = (store.root / "artifacts" / workspace / f"{artifact_id}.txt").read_text(encoding="utf-8")
            self.assertEqual(text, "ordinary text-only material")
            self.assertNotIn("material_evidence", store.object_metadata(workspace, artifact_id))
        finally:
            temporary.cleanup()

    def test_unknown_workspace_evidence_lookup_is_read_only(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        try:
            store = ProvenanceStore(Path(temporary.name) / "storage")
            intake = ControlledIntake(store)
            before = store._registry()["workspaces"]
            with self.assertRaises(ProvenanceError):
                intake.context_evidence("unknown-rpm-project", "ctx_unknown")
            self.assertEqual(store._registry()["workspaces"], before)
        finally:
            temporary.cleanup()
