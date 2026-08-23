from __future__ import annotations

import unittest

from gaia.hybrid_read_path import (
    DeterministicLexicalSourceRetriever,
    OperationalOverlaySelector,
    SourceRetrievalRequest,
    assemble_hybrid_package,
)
from gaia.source_evidence import MaterialAvailability, SourceMaterial


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
