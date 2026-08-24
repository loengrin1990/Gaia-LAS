from __future__ import annotations

import json
import unittest

from gaia.hybrid_reasoning import SYSTEM_INSTRUCTION, USER_FACING_PROTOCOL_LEAKAGE, run_hybrid_reasoning, serialize_reasoning_context


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode()


class HybridReasoningTests(unittest.TestCase):
    package = {"query": "Проверь общее правило.", "source_evidence": [], "oc_overlay": []}

    @staticmethod
    def request_for(payload):
        return lambda *_args, **_kwargs: FakeResponse(payload)

    @staticmethod
    def capture_request(payload, captured):
        def request(req, **_kwargs):
            captured["payload"] = json.loads(req.data.decode())
            return FakeResponse(payload)
        return request

    def test_compact_context_keeps_evidence_not_audit_diagnostics(self):
        package = {
            "query": "Проверь общее правило без специального subject scope.",
            "source_evidence": [{
                "excerpt": "Подтверждённый фрагмент.", "source_id": "source-1",
                "sanitized_id": "sanitized-1", "start_offset": 11,
                "end_offset": 35, "literal_annotations": {"current": True},
            }],
            "oc_overlay": [{"item": {
                "id": "oc-1", "value": "Подтверждённое правило", "lifecycle": "active",
                "confirmation": "confirmed", "provenance": {"candidate_ref": "c-1"},
            }, "relevance_score": 1.0}],
            "applicable_current_authority": [{"item": {"id": "oc-authority", "value": "Текущее правило", "lifecycle": "active", "confirmation": "confirmed", "provenance": {"source_ref": "sanitized-1"}}}],
            "observed_tensions": [], "unresolved": [],
            "diagnostics": {
                "oc_retrieval": {"raw": "x" * 2000},
                "source": {"material_diagnostics": ["not model-facing"]},
            },
        }
        before = json.dumps(package, ensure_ascii=False, sort_keys=True)
        first = serialize_reasoning_context(package)

        self.assertEqual(first, serialize_reasoning_context(package))
        self.assertEqual(before, json.dumps(package, ensure_ascii=False, sort_keys=True))
        self.assertEqual(first.count('"query":'), 1)
        self.assertIn('"excerpt":"Подтверждённый фрагмент."', first)
        self.assertIn('"source_id":"source-1"', first)
        self.assertIn('"sanitized_id":"sanitized-1"', first)
        self.assertIn('"start_offset":11', first)
        self.assertIn('"end_offset":35', first)
        self.assertIn('"value":"Подтверждённое правило"', first)
        self.assertIn('"applicable_current_authority":[{"confirmation":"confirmed","id":"oc-authority"', first)
        self.assertNotIn("oc_retrieval", first)
        self.assertNotIn("material_diagnostics", first)
        self.assertLess(len(first), len(before) * 0.5)

    def test_incomplete_provider_response_is_preserved_without_semantic_parse(self):
        raw = "Окно T-15 / Tend+45\nUSED_FACTS: source-1"
        response = {"model": "ministral-3:14b", "created_at": "2026-08-23T00:00:00Z", "message": {"role": "assistant", "content": raw}, "done": False, "prompt_eval_count": 77, "eval_count": 8}

        result = run_hybrid_reasoning(self.package, request=self.request_for(response))

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "PROVIDER_INCOMPLETE_RESPONSE")
        self.assertEqual(result["raw_content"], raw)
        self.assertEqual(result["ollama_metadata"]["prompt_eval_count"], 77)
        self.assertNotIn("answer", result)

    def test_complete_free_form_output_keeps_used_facts_and_disables_json_mode(self):
        raw = "Текущее окно — T-15 / Tend+45.\nUSED_FACTS: source-1, sanitized-1"
        response = {"model": "ministral-3:14b", "message": {"role": "assistant", "content": raw}, "done": True, "done_reason": "stop", "prompt_eval_count": 77, "eval_count": 8}
        captured = {}

        result = run_hybrid_reasoning(self.package, request=self.capture_request(response, captured))

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["answer"], "Текущее окно — T-15 / Tend+45.")
        self.assertEqual(result["used_facts"], ["source-1", "sanitized-1"])
        self.assertNotIn("format", captured["payload"])
        self.assertFalse(captured["payload"]["stream"])
        self.assertEqual(result["ollama_metadata"]["eval_count"], 8)

    def test_internal_identifiers_are_allowed_only_in_used_facts(self):
        package = {"query": "Вопрос", "source_evidence": [{"excerpt": "Фрагмент.", "source_id": "source-1", "sanitized_id": "sanitized-1", "start_offset": 0, "end_offset": 9}], "oc_overlay": [], "applicable_current_authority": [{"item": {"id": "oc-authority"}}]}
        response = {"model": "ministral-3:14b", "message": {"role": "assistant", "content": "Понятный ответ.\nUSED_FACTS: source-1, oc-authority"}, "done": True, "done_reason": "stop"}
        result = run_hybrid_reasoning(package, request=self.request_for(response))
        self.assertTrue(result["ok"])
        self.assertEqual(result["answer"], "Понятный ответ.")

    def test_body_internal_protocol_leakage_is_rejected(self):
        package = {"query": "Вопрос", "source_evidence": [{"excerpt": "Фрагмент.", "source_id": "source-1", "sanitized_id": "sanitized-1", "start_offset": 0, "end_offset": 9}], "oc_overlay": [], "applicable_current_authority": [{"item": {"id": "oc-authority"}}]}
        for leaked in ("oc-authority", "source-1", "CURRENT_AUTHORITY", "OC ID"):
            with self.subTest(leaked=leaked):
                response = {"model": "ministral-3:14b", "message": {"role": "assistant", "content": f"Ответ содержит {leaked}.\nUSED_FACTS: source-1"}, "done": True, "done_reason": "stop"}
                result = run_hybrid_reasoning(package, request=self.request_for(response))
                self.assertFalse(result["ok"])
                self.assertEqual(result["status"], USER_FACING_PROTOCOL_LEAKAGE)

    def test_reasoning_instruction_preserves_evidence_strength_without_domain_hints(self):
        self.assertIn("Не усиливай утверждения сверх evidence", SYSTEM_INSTRUCTION)
        self.assertIn("scope, qualifiers, quantifiers, exclusivity, modality и confidence", SYSTEM_INSTRUCTION)
        self.assertIn("только/все/обязательно/точно", SYSTEM_INSTRUCTION)
        self.assertIn("краткий свободный текстовый ответ", SYSTEM_INSTRUCTION)
        self.assertIn("USED_FACTS: <id>, <id>", SYSTEM_INSTRUCTION)
        self.assertIn("applicable CURRENT_AUTHORITY", SYSTEM_INSTRUCTION)
        self.assertIn("Никогда не показывай", SYSTEM_INSTRUCTION)
        for forbidden in ("GateX", "GateFlow", "Q2", "authoritative system"):
            self.assertNotIn(forbidden, SYSTEM_INSTRUCTION)
