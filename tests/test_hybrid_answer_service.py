from __future__ import annotations
import unittest
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

if __name__ == '__main__': unittest.main()
