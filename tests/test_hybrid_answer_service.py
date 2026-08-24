from __future__ import annotations
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from gaia.hybrid_answer_service import HybridAnswerService, normalize_used_fact
from gaia.source_evidence import MaterialAvailability, SourceMaterial

class HybridAnswerServiceTests(unittest.TestCase):
    def test_terminal_punctuation_is_normalized(self):
        self.assertEqual(normalize_used_fact("san_abc.,"), "san_abc")
        self.assertEqual(normalize_used_fact("src_xyz,"), "src_xyz")

    def test_missing_registry_is_typed_and_does_not_call_model(self):
        called = []
        service = HybridAnswerService("/private/tmp/gaia-d3a-missing", reasoner=lambda _: called.append(True) or {})
        result = service.answer("вопрос", "workspace")
        self.assertEqual(result.status, "SOURCE_REGISTRY_UNAVAILABLE")
        self.assertEqual(called, [])

    def test_current_package_provenance_never_resolves_foreign_id(self):
        package = {"source_evidence": [{"source_id":"src-1","sanitized_id":"san-1","start_offset":1,"end_offset":2,"literal_annotations":{}}], "oc_overlay": []}
        self.assertIsNone(HybridAnswerService._provenance(("src-outside",), package))
        self.assertEqual(HybridAnswerService._provenance(("san-1",), package)[0]["evidence_layer"], "source_evidence")

    def test_source_only_answer_is_grounded_without_oc(self):
        material = SourceMaterial(MaterialAvailability.AVAILABLE, "src-1", "workspace", "san-1", text="Подтвержденное правило продления.")
        response = {"ok": True, "answer": "Подтвержденный ответ", "used_facts": ["san-1."], "provider": "local", "model": "model", "done_reason": "stop"}
        with patch("gaia.hybrid_answer_service.SourceEvidenceReader.inventory", return_value=(material,)), patch("gaia.hybrid_answer_service.OperationalContextReader.retrieve", side_effect=RuntimeError("OC unavailable")):
            result = HybridAnswerService("/unused", reasoner=lambda _: response).answer("правило продления", "workspace")
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(result.used_facts_normalized, ("san-1",))
        self.assertEqual(result.resolved_provenance[0]["evidence_layer"], "source_evidence")

    def test_empty_evidence_never_calls_reasoner(self):
        called = []
        with patch("gaia.hybrid_answer_service.SourceEvidenceReader.inventory", return_value=()), patch("gaia.hybrid_answer_service.OperationalContextReader.retrieve", return_value=type("R", (), {"eligible_items": ()})()):
            result = HybridAnswerService("/unused", reasoner=lambda _: called.append(True) or {}).answer("вопрос", "workspace", referent_hint="только поиск")
        self.assertEqual(result.status, "INSUFFICIENT_EVIDENCE")
        self.assertEqual(called, [])

    def test_reasoner_exception_is_a_typed_local_failure(self):
        material = SourceMaterial(MaterialAvailability.AVAILABLE, "src-1", "workspace", "san-1", text="Подтвержденное правило.")
        with patch("gaia.hybrid_answer_service.SourceEvidenceReader.inventory", return_value=(material,)), patch("gaia.hybrid_answer_service.OperationalContextReader.retrieve", return_value=type("R", (), {"eligible_items": ()})()):
            result = HybridAnswerService("/unused", reasoner=lambda _: (_ for _ in ()).throw(RuntimeError("route unavailable"))).answer("правило", "workspace")
        self.assertEqual(result.status, "LOCAL_PROVIDER_UNAVAILABLE")

    def test_oc_backed_by_current_query_hit_is_allowed(self):
        material = SourceMaterial(MaterialAvailability.AVAILABLE, "src-1", "workspace", "san-1", text="Правило подтверждено.")
        eligible = ({"id": "oc-1", "kind": "requirement", "subject_ref": "subject", "value": "Правило подтверждено", "reference": "", "lifecycle": "active", "confirmation": "confirmed", "provenance": {"source_ref": "san-1", "candidate_ref": "candidate-1"}},)
        response = {"ok": True, "answer": "Подтвержденный ответ", "used_facts": ["oc-1"], "provider": "local", "model": "model", "done_reason": "stop"}
        with patch("gaia.hybrid_answer_service.SourceEvidenceReader.inventory", return_value=(material,)), patch("gaia.hybrid_answer_service.OperationalContextReader.retrieve", return_value=type("R", (), {"eligible_items": eligible})()):
            result = HybridAnswerService("/unused", reasoner=lambda _: response).answer("правило", "workspace")
        self.assertEqual(result.status, "SUCCESS")
        self.assertTrue(result.oc_applied)

    def test_oc_backed_by_valid_source_not_in_current_query_hits_is_allowed(self):
        material = SourceMaterial(MaterialAvailability.AVAILABLE, "src-1", "workspace", "san-1", text="Другая тема.")
        eligible = ({"id": "oc-1", "kind": "requirement", "subject_ref": "subject", "value": "Правило подтверждено", "reference": "", "lifecycle": "active", "confirmation": "confirmed", "provenance": {"source_ref": "san-1", "candidate_ref": "candidate-1"}},)
        response = {"ok": True, "answer": "Подтвержденный ответ", "used_facts": ["oc-1"], "provider": "local", "model": "model", "done_reason": "stop"}
        with patch("gaia.hybrid_answer_service.SourceEvidenceReader.inventory", return_value=(material,)), patch("gaia.hybrid_answer_service.OperationalContextReader.retrieve", return_value=type("R", (), {"eligible_items": eligible})()):
            result = HybridAnswerService("/unused", reasoner=lambda _: response).answer("правило", "workspace")
        self.assertEqual(result.status, "SUCCESS")
        self.assertTrue(result.oc_applied)
        self.assertTrue(result.provenance_complete)

    def test_oc_with_dangling_source_ref_never_reaches_reasoner(self):
        material = SourceMaterial(MaterialAvailability.AVAILABLE, "src-1", "workspace", "san-1", text="Другая тема.")
        eligible = ({"id": "oc-1", "kind": "requirement", "subject_ref": "subject", "value": "Правило подтверждено", "reference": "", "lifecycle": "active", "confirmation": "confirmed", "provenance": {"source_ref": "san-missing", "candidate_ref": "candidate-1"}},)
        called = []
        with patch("gaia.hybrid_answer_service.SourceEvidenceReader.inventory", return_value=(material,)), patch("gaia.hybrid_answer_service.OperationalContextReader.retrieve", return_value=type("R", (), {"eligible_items": eligible})()):
            result = HybridAnswerService("/unused", reasoner=lambda _: called.append(True) or {}).answer("правило", "workspace")
        self.assertEqual(result.status, "INSUFFICIENT_EVIDENCE")
        self.assertEqual(called, [])

    def test_oc_only_answer_with_unresolvable_provenance_never_reaches_reasoner(self):
        material = SourceMaterial(MaterialAvailability.AVAILABLE, "src-1", "workspace", "san-1", text="Другая тема.")
        eligible = ({"id": "oc-1", "kind": "requirement", "subject_ref": "subject", "value": "Правило подтверждено", "reference": "", "lifecycle": "active", "confirmation": "confirmed", "provenance": {"candidate_ref": "candidate-1"}},)
        called = []
        with patch("gaia.hybrid_answer_service.SourceEvidenceReader.inventory", return_value=(material,)), patch("gaia.hybrid_answer_service.OperationalContextReader.retrieve", return_value=type("R", (), {"eligible_items": eligible})()):
            result = HybridAnswerService("/unused", reasoner=lambda _: called.append(True) or {}).answer("правило", "workspace")
        self.assertEqual(result.status, "INSUFFICIENT_EVIDENCE")
        self.assertEqual(called, [])

    def test_unmatched_scoped_decisions_do_not_block_source_only_answer(self):
        material = SourceMaterial(MaterialAvailability.AVAILABLE, "src-1", "workspace", "san-1", text="Для заявки нужны подтверждённые данные.")
        eligible = ({"id": "oc_extension", "kind": "decision", "subject_ref": "extension", "value": "Срок заявки на пропуск не определён.", "lifecycle": "active", "confirmation": "confirmed", "provenance": {"source_ref": "san-1"}, "authority_scope": {"label": "Продление пропуска", "query_aliases": ["продлить пропуск"]}},)
        response = {"ok": True, "answer": "Нужны подтверждённые данные.", "used_facts": ["san-1"], "provider": "local", "model": "model", "done_reason": "stop"}
        captured = {}
        with patch("gaia.hybrid_answer_service.SourceEvidenceReader.inventory", return_value=(material,)), patch("gaia.hybrid_answer_service.OperationalContextReader.retrieve", return_value=SimpleNamespace(eligible_items=eligible)):
            result = HybridAnswerService("/unused", reasoner=lambda package: captured.update(package) or response).answer("Какой срок заявки на пропуск?", "workspace")
        self.assertEqual(result.status, "SUCCESS")
        self.assertFalse(result.oc_applied)
        self.assertEqual([hit["item"]["id"] for hit in captured["oc_overlay"]], ["oc_extension"])

    def test_applicable_scoped_authority_requires_existing_used_fact_citation(self):
        material = SourceMaterial(MaterialAvailability.AVAILABLE, "src-1", "workspace", "san-1", text="Продление после истечения возможно.")
        eligible = ({"id": "oc_extension", "kind": "decision", "subject_ref": "extension", "value": "Продление только до истечения.", "lifecycle": "active", "confirmation": "confirmed", "provenance": {"source_ref": "san-1"}, "authority_scope": {"label": "Продление пропуска", "query_aliases": ["продлить пропуск"]}},)
        source_only = {"ok": True, "answer": "После истечения возможно.", "used_facts": ["san-1"], "provider": "local", "model": "model", "done_reason": "stop"}
        authority = {**source_only, "answer": "Только до истечения.", "used_facts": ["oc_extension"]}
        with patch("gaia.hybrid_answer_service.SourceEvidenceReader.inventory", return_value=(material,)), patch("gaia.hybrid_answer_service.OperationalContextReader.retrieve", return_value=SimpleNamespace(eligible_items=eligible)):
            rejected = HybridAnswerService("/unused", reasoner=lambda _: source_only).answer("Можно ли продлить пропуск после его истечения?", "workspace")
            accepted = HybridAnswerService("/unused", reasoner=lambda _: authority).answer("Можно ли продлить пропуск после его истечения?", "workspace")
        self.assertEqual(rejected.status, "CURRENT_AUTHORITY_NOT_USED")
        self.assertEqual(accepted.status, "SUCCESS")
        self.assertEqual(accepted.resolved_provenance[0]["evidence_layer"], "applicable_current_authority")

    def test_matched_authority_is_not_duplicated_in_ordinary_overlay(self):
        material = SourceMaterial(MaterialAvailability.AVAILABLE, "src-1", "workspace", "san-1", text="Продление после истечения возможно.")
        eligible = ({"id": "oc_extension", "kind": "decision", "subject_ref": "extension", "value": "Продление только до истечения.", "lifecycle": "active", "confirmation": "confirmed", "provenance": {"source_ref": "san-1"}, "authority_scope": {"label": "Продление пропуска", "query_aliases": ["продлить пропуск"]}},)
        captured = {}
        response = {"ok": True, "answer": "Только до истечения.", "used_facts": ["oc_extension"], "provider": "local", "model": "model", "done_reason": "stop"}
        with patch("gaia.hybrid_answer_service.SourceEvidenceReader.inventory", return_value=(material,)), patch("gaia.hybrid_answer_service.OperationalContextReader.retrieve", return_value=SimpleNamespace(eligible_items=eligible)):
            result = HybridAnswerService("/unused", reasoner=lambda package: captured.update(package) or response).answer("Можно ли продлить пропуск после истечения?", "workspace")
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(captured["oc_overlay"], [])
        self.assertEqual([hit["item"]["id"] for hit in captured["applicable_current_authority"]], ["oc_extension"])

    def test_inactive_unconfirmed_or_unbacked_scoped_decision_is_not_current_authority(self):
        material = SourceMaterial(MaterialAvailability.AVAILABLE, "src-1", "workspace", "san-1", text="Продлить пропуск можно только после проверки подтверждённых данных.")
        scope = {"label": "Продление пропуска", "query_aliases": ["продлить пропуск"]}
        eligible = (
            {"id": "oc_inactive", "kind": "decision", "subject_ref": "inactive", "value": "x", "lifecycle": "superseded", "confirmation": "confirmed", "provenance": {"source_ref": "san-1"}, "authority_scope": scope},
            {"id": "oc_unconfirmed", "kind": "decision", "subject_ref": "unconfirmed", "value": "x", "lifecycle": "active", "confirmation": "pending", "provenance": {"source_ref": "san-1"}, "authority_scope": scope},
            {"id": "oc_unbacked", "kind": "decision", "subject_ref": "unbacked", "value": "x", "lifecycle": "active", "confirmation": "confirmed", "provenance": {"source_ref": "san-missing"}, "authority_scope": scope},
        )
        response = {"ok": True, "answer": "Нужны подтверждённые данные.", "used_facts": ["san-1"], "provider": "local", "model": "model", "done_reason": "stop"}
        with patch("gaia.hybrid_answer_service.SourceEvidenceReader.inventory", return_value=(material,)), patch("gaia.hybrid_answer_service.OperationalContextReader.retrieve", return_value=SimpleNamespace(eligible_items=eligible)):
            result = HybridAnswerService("/unused", reasoner=lambda _: response).answer("Можно ли продлить пропуск после истечения?", "workspace")
        self.assertEqual(result.status, "SUCCESS")
        self.assertFalse(result.oc_applied)

if __name__ == '__main__': unittest.main()
