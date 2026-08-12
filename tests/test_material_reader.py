from __future__ import annotations

from pathlib import Path
import re
import tempfile
import unittest

from gaia.context_compiler import ContextCompileError, ContextCompiler
from gaia.controlled_intake import ControlledIntake
from gaia.material_reader import MaterialReader
from gaia.provenance import ProvenanceStore
from gaia.provenance import ProvenanceError
from gaia.review import ReviewService


FIXTURE = Path(__file__).parent / "fixtures" / "RPM-0_acceptance_mixed_technical_architecture.pdf"
VISUAL_FRONTEND_PLACEMENT = 'VISUAL_LAYOUT: "Frontend" is placed inside a node group.'


class MaterialReaderAcceptanceTests(unittest.TestCase):
    def fixture(self) -> Path:
        self.assertTrue(FIXTURE.is_file(), f"RPM-1 acceptance fixture is required: {FIXTURE}")
        return FIXTURE

    def test_mixed_fixture_reaches_text_visual_cross_modal_context_with_provenance(self) -> None:
        fixture = self.fixture()
        reader = MaterialReader()
        result = reader.read_pdf(fixture.read_bytes())

        # Textual control: native PDF text stays usable; it is not raster-only.
        self.assertIn("Техническая архитектура", result.text)
        self.assertTrue(any(item.modality == "text" and item.state == "ready" for item in result.evidence))
        self.assertTrue(any(item.modality == "table_layout" and item.state == "ready" for item in result.evidence))

        # Visual-only: generic OCR geometry derives the Frontend placement from
        # the diagram.  The native page text names Frontend Service, but contains
        # neither this observation nor any placement/group relationship.
        native = next(item for item in result.evidence if item.evidence_id == "ev_text_p1")
        visual = next(item for item in result.evidence if item.evidence_id == "ev_visual_p1_1")
        self.assertEqual(visual.state, "ready")
        self.assertIn(VISUAL_FRONTEND_PLACEMENT, visual.text)
        self.assertNotIn(VISUAL_FRONTEND_PLACEMENT, native.text)
        self.assertNotRegex(native.text, r"Frontend.{0,120}(?:inside|group)|(?:inside|group).{0,120}Frontend")

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

            def visual_model(text: str) -> dict[str, object]:
                visual_match = re.search(re.escape(VISUAL_FRONTEND_PLACEMENT), text)
                if visual_match is not None:
                    return {"candidates": [{
                        "type": "requirement",
                        "title": "Frontend расположен в группе компонентов",
                        "statement": "Frontend расположен внутри визуально выделенной node group.",
                        "block": {"start": visual_match.start(), "end": visual_match.end()},
                        "material_evidence_ids": ["ev_visual_p1_1"],
                        "confidence": "high",
                        "requires_review": True,
                    }]}
                return {"candidates": []}

            visual_context = ContextCompiler(store, workspace, visual_model).compile(sanitized["artifact_id"], compiler_version="rpm-1-visual-only")[0]
            visual_support = intake.context_evidence("rpm-1-acceptance", visual_context["id"])
            self.assertEqual({item["modality"] for item in visual_support}, {"visual"})
            self.assertEqual({item["support"] for item in visual_support}, {"explicit_reference"})
            visual_evidence = next(item for item in visual_support if item["modality"] == "visual")
            self.assertEqual(visual_evidence["locator"], {"kind": "embedded_image", "page": 1, "image_index": 1})
            self.assertTrue(all(item["page"] == 1 and item["origin"] for item in visual_support))
            self.assertEqual(store.lineage(workspace, visual_context["id"])["source_id"], source["source_id"])
            self.assertEqual(visual_context["material_evidence_ids"], ["ev_visual_p1_1"])

            def cross_modal_model(text: str) -> dict[str, object]:
                visual_match = re.search(re.escape(VISUAL_FRONTEND_PLACEMENT), text)
                native_match = re.search(r"Frontend Service\s*Статическое SPA приложение", text)
                if visual_match is None or native_match is None:
                    return {"candidates": []}
                return {"candidates": [{
                    "type": "requirement",
                    "title": "Frontend SPA находится в группе компонентов",
                    "statement": "Frontend Service — статическое SPA приложение в визуально выделенной node group.",
                    "block": {"start": native_match.start(), "end": native_match.end()},
                    "material_evidence_ids": ["ev_text_p1", "ev_visual_p1_1"],
                    "material_evidence_modalities": ["text", "visual"],
                    "confidence": "high",
                    "requires_review": True,
                }]}

            cross_modal = ContextCompiler(store, workspace, cross_modal_model).compile(sanitized["artifact_id"], compiler_version="rpm-1-cross-modal")[0]
            support = intake.context_evidence("rpm-1-acceptance", cross_modal["id"])
            self.assertEqual({item["modality"] for item in support}, {"text", "visual"})
            self.assertEqual({item["support"] for item in support}, {"explicit_reference"})
            text_evidence = next(item for item in support if item["modality"] == "text")
            self.assertEqual(text_evidence["locator"], {"kind": "page", "page": 1})
            self.assertTrue(all(item["page"] == 1 and item["origin"] for item in support))
            self.assertEqual(store.lineage(workspace, cross_modal["id"])["source_id"], source["source_id"])
            self.assertEqual(cross_modal["material_evidence_ids"], ["ev_text_p1", "ev_visual_p1_1"])
            self.assertEqual(cross_modal["material_evidence_modalities"], ["text", "visual"])
            self.assertEqual({item["modality"] for item in cross_modal["material_evidence_links"]}, {"text", "visual"})
            self.assertGreaterEqual(len(cross_modal["block_links"]), 2)

            # Every negative case executes the production persistence guard, not
            # a test-double predicate.  No invalid reference can create a fully
            # grounded cross-modal context item.
            def declared_model(evidence_ids: list[str], modalities: list[str] | None, label: str) -> object:
                def model(text: str) -> dict[str, object]:
                    native_match = re.search(r"Frontend Service\s*Статическое SPA приложение", text)
                    if native_match is None:
                        return {"candidates": []}
                    candidate: dict[str, object] = {
                        "type": "requirement",
                        "title": f"Проверка обязательной multimodal опоры: {label}",
                        "statement": f"Frontend Service — статическое SPA приложение в визуально выделенной node group ({label}).",
                        "block": {"start": native_match.start(), "end": native_match.end()},
                        "material_evidence_ids": evidence_ids,
                        "confidence": "high",
                        "requires_review": True,
                    }
                    if modalities is not None:
                        candidate["material_evidence_modalities"] = modalities
                    return {"candidates": [candidate]}
                return model

            failures = {
                "missing_visual": (["ev_text_p1"], ["text", "visual"], "material_evidence_modality"),
                "missing_text": (["ev_visual_p1_1"], ["text", "visual"], "material_evidence_modality"),
                "arbitrary": (["ev_not_from_this_material"], ["text", "visual"], "material_evidence_unknown"),
                "wrong_modality": (["ev_table_p1", "ev_visual_p1_1"], ["text", "visual"], "material_evidence_modality"),
                "declared_visual_only": (["ev_visual_p1_1"], ["visual"], "material_evidence_modality"),
                "declared_text_only": (["ev_text_p1"], ["text"], "material_evidence_modality"),
                "modalities_omitted": (["ev_visual_p1_1"], None, "material_evidence_modality"),
            }
            for name, (evidence_ids, modalities, diagnostic_code) in failures.items():
                with self.subTest(name=name), self.assertRaises(ContextCompileError) as rejected:
                    ContextCompiler(store, workspace, declared_model(evidence_ids, modalities, name)).compile(
                        sanitized["artifact_id"], compiler_version=f"rpm-1-cross-modal-{name}",
                    )
                self.assertEqual(rejected.exception.diagnostic_code, diagnostic_code)

            weakened = ContextCompiler(
                store, workspace, declared_model(["ev_text_p1", "ev_visual_p1_1"], ["visual"], "weakened_declaration"),
            ).compile(sanitized["artifact_id"], compiler_version="rpm-1-cross-modal-weakened")[0]
            self.assertEqual(weakened["material_evidence_modalities"], ["text", "visual"])
            self.assertEqual({item["modality"] for item in weakened["material_evidence_links"]}, {"text", "visual"})
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
