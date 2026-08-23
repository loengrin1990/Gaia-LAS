from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

from gaia.hybrid_read_path import (
    DeterministicLexicalSourceRetriever,
    OperationalOverlaySelector,
    SourceRetrievalRequest,
    assemble_hybrid_package,
)
from gaia.source_evidence import MaterialAvailability, SourceMaterial


ROOT = Path(__file__).resolve().parents[1]
FROZEN_D3_2C_INPUTS = Path("/Users/ilia/Documents/Gaia-pilot-evidence/2026-08-22_D0-baseline-v3")
PACKAGE_BUILDER = ROOT / "scripts" / "d2_source_oc_package.py"


def frozen_source_materials() -> tuple[SourceMaterial, ...]:
    spec = importlib.util.spec_from_file_location("d2_source_oc_package_for_hybrid_read_path", PACKAGE_BUILDER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return tuple(
        SourceMaterial(
            MaterialAvailability.AVAILABLE,
            source.source_id,
            "frozen_d3_2c_workspace",
            source.sanitized_id,
            source.created_at,
            "text/plain",
            source.text,
        )
        for source in module.load_sources(FROZEN_D3_2C_INPUTS)
    )


class HybridReadPathTests(unittest.TestCase):
    def materials(self) -> tuple[SourceMaterial, ...]:
        return (
            SourceMaterial(MaterialAvailability.AVAILABLE, "src_1", "ws_a", "san_1", "2026-01-01", "text/plain", "# Условия\n\nТекущий порядок: продление требует подтверждения принимающей стороны."),
            SourceMaterial(MaterialAvailability.AVAILABLE, "src_2", "ws_a", "san_2", "2026-01-02", "text/plain", "# Справочно\n\nАрхивная заметка о другом процессе."),
            SourceMaterial(MaterialAvailability.UNPARSED_OR_MISSING_SANITIZED, "src_3", "ws_a", detail="current_sanitized_missing_or_ambiguous"),
        )

    def test_retrieval_is_deterministic_bounded_and_preserves_offsets(self) -> None:
        request = SourceRetrievalRequest("Нужно ли подтверждение принимающей стороне?", "ws_a", max_hits=1, max_chars=5000)
        retriever = DeterministicLexicalSourceRetriever()
        first = retriever.retrieve(request, self.materials())
        second = retriever.retrieve(request, self.materials())
        self.assertEqual(first, second)
        self.assertEqual(len(first.hits), 1)
        hit = first.hits[0]
        source = self.materials()[0].text
        self.assertEqual(hit.excerpt, source[hit.start_offset:hit.end_offset])
        self.assertEqual((hit.source_id, hit.sanitized_id, hit.workspace_id), ("src_1", "san_1", "ws_a"))
        self.assertIn("unparsed_or_missing_sanitized", [item["state"] for item in first.material_diagnostics])

    def test_literal_annotation_is_not_semantic_subject_truth(self) -> None:
        result = DeterministicLexicalSourceRetriever().retrieve(SourceRetrievalRequest("текущий порядок", "ws_a"), self.materials())
        annotations = result.hits[0].literal_annotations
        self.assertIn("current", annotations["literal_status_cues"])
        self.assertNotIn("subject_scope", annotations)
        self.assertNotIn("authority", annotations)

    def test_oc_overlay_is_query_aware_bounded_and_keeps_source_visible(self) -> None:
        source = DeterministicLexicalSourceRetriever().retrieve(SourceRetrievalRequest("текущий порядок подтверждения", "ws_a"), self.materials())
        eligible = (
            {"id": "oc_match", "kind": "requirement", "subject_ref": "access", "value": "Порядок подтверждения будет пересмотрен после проверки", "lifecycle": "active", "confirmation": "confirmed", "provenance": {"candidate_ref": "c_1"}},
            {"id": "oc_other", "kind": "risk", "subject_ref": "other", "value": "Другая тема", "lifecycle": "active", "confirmation": "confirmed", "provenance": {"candidate_ref": "c_2"}},
            {"id": "oc_ineligible", "kind": "risk", "subject_ref": "old", "value": "Порядок подтверждения", "lifecycle": "retired", "confirmation": "confirmed"},
        )
        overlay = OperationalOverlaySelector().select("текущий порядок подтверждения", eligible, max_items=1)
        package = assemble_hybrid_package("текущий порядок подтверждения", source, overlay)
        self.assertEqual([item.item["id"] for item in overlay], ["oc_match"])
        self.assertTrue(package.source_evidence)
        self.assertTrue(package.oc_overlay)
        self.assertTrue(package.observed_tensions)

    def test_no_source_match_is_not_reported_as_unavailable_material(self) -> None:
        result = DeterministicLexicalSourceRetriever().retrieve(SourceRetrievalRequest("несвязанный запрос", "ws_a"), self.materials())
        package = assemble_hybrid_package("несвязанный запрос", result, ())
        self.assertEqual(package.unresolved, ("no_matching_queryable_source_evidence",))
        self.assertIn("unparsed_or_missing_sanitized", [item["state"] for item in result.material_diagnostics])

    def test_structural_expansion_is_bounded_provenanced_and_generic(self) -> None:
        text = "# Документ\n\n## Основание\n\nЛокальный якорь правила.\n\n## Текущее состояние\n\nВводная строка раздела.\n\nТекущий маркер доступен.\n\n## Примечание\n\nДополнительный текст.\n"
        material = SourceMaterial(MaterialAvailability.AVAILABLE, "src_local", "ws_a", "san_local", "2026-01-03", "text/plain", text)
        request = SourceRetrievalRequest("текущий маркер", "ws_a", max_hits=1, max_chars=5_000)
        first = DeterministicLexicalSourceRetriever().retrieve(request, (material,))
        second = DeterministicLexicalSourceRetriever().retrieve(request, (material,))
        primary = [hit for hit in first.hits if hit.selection_role == "primary_hit"]
        expansion = [hit for hit in first.hits if hit.selection_role == "context_expansion"]
        self.assertEqual(first, second)
        self.assertEqual(len(primary), 1)
        self.assertTrue(expansion)
        self.assertTrue(all(hit.expansion_relation == "preceding_section" for hit in expansion))
        self.assertTrue(all(hit.expanded_from["start_offset"] == primary[0].start_offset for hit in expansion))
        self.assertTrue(all(len(hit.excerpt) <= 600 for hit in expansion))
        self.assertLessEqual(sum(len(hit.excerpt) for hit in expansion), 1_600)
        self.assertIn("Локальный якорь правила.", "\n".join(hit.excerpt for hit in expansion))

    def test_coverage_selection_keeps_material_with_uncovered_query_aspect(self) -> None:
        query = "Какой контроль действует для активного и отозванного доступа?"
        partial = "Текущий текущий контроль активного доступа."
        materials = tuple(
            SourceMaterial(MaterialAvailability.AVAILABLE, f"src_partial_{index}", "ws_a", f"san_partial_{index}", text=partial)
            for index in range(4)
        ) + (
            SourceMaterial(MaterialAvailability.AVAILABLE, "src_coverage", "ws_a", "san_coverage", text="Контроль активного и отозванного доступа."),
        )
        result = DeterministicLexicalSourceRetriever().retrieve(SourceRetrievalRequest(query, "ws_a", max_hits=4), materials)
        primary = [hit for hit in result.hits if hit.selection_role == "primary_hit"]
        self.assertEqual(primary[0].source_id, "src_partial_0")
        self.assertIn("src_coverage", [hit.source_id for hit in primary])
        self.assertEqual(len(primary), 4)
        self.assertEqual(result.diagnostics["coverage_promotions"], 1)

    def test_simple_query_keeps_highest_lexical_hit_first(self) -> None:
        materials = (
            SourceMaterial(MaterialAvailability.AVAILABLE, "src_best", "ws_a", "san_best", text="Подтверждённое правило продления доступа."),
            SourceMaterial(MaterialAvailability.AVAILABLE, "src_other", "ws_a", "san_other", text="Доступ доступен в другом процессе."),
        )
        result = DeterministicLexicalSourceRetriever().retrieve(SourceRetrievalRequest("правило продления доступа", "ws_a", max_hits=2), materials)
        primary = [hit for hit in result.hits if hit.selection_role == "primary_hit"]
        self.assertEqual(primary[0].source_id, "src_best")
        self.assertEqual(result.diagnostics["coverage_promotions"], 0)

    def test_one_new_weak_token_does_not_force_irrelevant_diversity(self) -> None:
        query = "контроль активного доступа audit"
        materials = (
            SourceMaterial(MaterialAvailability.AVAILABLE, "src_best", "ws_a", "san_best", text="Контроль активного доступа подтверждён."),
            SourceMaterial(MaterialAvailability.AVAILABLE, "src_relevant", "ws_a", "san_relevant", text="Контроль доступа действует для активного режима."),
            SourceMaterial(MaterialAvailability.AVAILABLE, "src_weak", "ws_a", "san_weak", text="audit"),
        )
        result = DeterministicLexicalSourceRetriever().retrieve(SourceRetrievalRequest(query, "ws_a", max_hits=2), materials)
        primary = [hit for hit in result.hits if hit.selection_role == "primary_hit"]
        self.assertNotIn("src_weak", [hit.source_id for hit in primary])
        self.assertEqual(result.diagnostics["coverage_promotions"], 0)

    def test_coverage_selection_is_deterministic_and_stays_within_primary_bound(self) -> None:
        query = "Какой контроль действует для активного и отозванного доступа?"
        materials = tuple(
            SourceMaterial(MaterialAvailability.AVAILABLE, f"src_{index}", "ws_a", f"san_{index}", text="Текущий текущий контроль активного доступа.")
            for index in range(4)
        ) + (SourceMaterial(MaterialAvailability.AVAILABLE, "src_coverage", "ws_a", "san_coverage", text="Контроль активного и отозванного доступа."),)
        request = SourceRetrievalRequest(query, "ws_a", max_hits=3)
        first = DeterministicLexicalSourceRetriever().retrieve(request, materials)
        second = DeterministicLexicalSourceRetriever().retrieve(request, materials)
        self.assertEqual(first, second)
        self.assertLessEqual(sum(hit.selection_role == "primary_hit" for hit in first.hits), request.max_hits)

    def test_frozen_d3_2c_authority_query_keeps_combined_qualifier_in_package(self) -> None:
        if not FROZEN_D3_2C_INPUTS.is_dir():
            self.skipTest("Frozen D3.2C sanitized inputs are unavailable.")
        query = "Какая система авторитетна по активному и отозванному состоянию доступа?"
        result = DeterministicLexicalSourceRetriever().retrieve(
            SourceRetrievalRequest(query, "frozen_d3_2c_workspace", max_hits=4),
            frozen_source_materials(),
        )
        self.assertTrue(any("авторитетной по активному/отозванному состоянию" in hit.excerpt for hit in result.hits))
