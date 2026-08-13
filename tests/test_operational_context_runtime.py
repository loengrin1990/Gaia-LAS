from __future__ import annotations

import unittest
from unittest.mock import patch

from gaia.models import MemorySelection, MemorySource
from gaia.operational_context_assembler import (
    HandledMemorySelection,
    OperationalContextPackageBudget,
    compose_operational_context_package,
    new_free_form_text,
    trusted_system_text,
)
from gaia.operational_context_retrieval import AuthorityAmbiguity, RetrievalResult
from gaia.privacy_boundary import HandlingEvidence
from gaia.operational_context_runtime import (
    NORMAL_CONTEXT_TOKENS,
    OC5_LOCAL_MODEL,
    SAFE_MISSING_CONTEXT_MESSAGE,
    SAFE_OVERSIZE_MESSAGE,
    SAFE_RUNTIME_RESPONSE_MESSAGE,
    run_operational_context_dialogue,
)
from gaia.local_llm import resolve_route


def authority(item_id: str, sensitivity: str = "standard") -> dict[str, object]:
    return {
        "id": item_id, "scope": "project", "scope_ref": "project_a", "kind": "requirement",
        "subject_ref": "subject_a", "value": "Current state", "sensitivity": sensitivity,
        "provenance": {"candidate_ref": f"candidate_{item_id}", "source_ref": "", "memory_ref": ""},
        "confirmation_ref": f"oce_{item_id}",
    }


def delivery_status_authority() -> dict[str, object]:
    item = authority("oc_delivery", "unknown")
    item.update({"subject_ref": "delivery_status", "value": "Материалы находятся на проверке качества."})
    return item


def standard_memory() -> HandledMemorySelection:
    selection = MemorySelection("History", [MemorySource("m", "P", "memory.md", "History", 1, 1, 1, [])], 1, ["P"])
    return HandledMemorySelection(selection, "standard", HandlingEvidence("reviewed_memory_standard", "memory_review_1"))


def standard_package(result: RetrievalResult) -> object:
    return compose_operational_context_package(
        query=trusted_system_text("pb0_response_format_v1"), task=trusted_system_text("pb0_response_format_v1"),
        retrieval_result=result, memory_selection=standard_memory(), budget=OperationalContextPackageBudget(65_536),
    )


class OperationalContextRuntimeTests(unittest.TestCase):
    def test_internal_missing_envelope_is_never_a_dialogue_answer(self) -> None:
        package = compose_operational_context_package(
            query=new_free_form_text("Какой сейчас статус поставки?"), task=trusted_system_text("pb0_response_format_v1"),
            retrieval_result=RetrievalResult((), (), ()), memory_selection=None,
        )
        result = run_operational_context_dialogue(package, local_executor=lambda *args, **kwargs: {"ok": True, "answer": '{"status":"missing"}', "model": OC5_LOCAL_MODEL})
        self.assertEqual(result.answer, SAFE_MISSING_CONTEXT_MESSAGE)

    def test_internal_missing_envelope_is_normalized_on_external_route(self) -> None:
        package = standard_package(RetrievalResult((), (), ()))
        result = run_operational_context_dialogue(package, external_executor=lambda _: {"ok": True, "answer": '{"status":"missing"}', "model": "external"})
        self.assertEqual(result.answer, SAFE_MISSING_CONTEXT_MESSAGE)

    def test_internal_response_schema_cannot_hide_confirmed_delivery_status(self) -> None:
        package = compose_operational_context_package(
            query=new_free_form_text("Какой сейчас статус поставки?"), task=trusted_system_text("pb0_response_format_v1"),
            retrieval_result=RetrievalResult((delivery_status_authority(),), (), ()), memory_selection=None,
        )
        result = run_operational_context_dialogue(
            package,
            local_executor=lambda *args, **kwargs: {"ok": True, "answer": '{"status":"missing"}', "model": OC5_LOCAL_MODEL},
        )
        self.assertEqual(result.answer, "Текущий статус поставки: Материалы находятся на проверке качества.")

    def test_internal_response_schema_renders_only_user_answer(self) -> None:
        package = compose_operational_context_package(
            query=new_free_form_text("Вопрос"), task=trusted_system_text("pb0_response_format_v1"),
            retrieval_result=RetrievalResult((), (), ()), memory_selection=None,
        )
        result = run_operational_context_dialogue(
            package,
            local_executor=lambda *args, **kwargs: {"ok": True, "answer": '{"response_text":"Нужны дополнительные данные.","reasoning":"internal","schema_error":true}', "model": OC5_LOCAL_MODEL},
        )
        self.assertEqual(result.answer, SAFE_RUNTIME_RESPONSE_MESSAGE)

    def test_conflict_keeps_both_alternatives_and_recovers_from_internal_error(self) -> None:
        lead = authority("oc_lead", "unknown"); lead["value"] = "Финальное согласование выполняет руководитель проекта."
        finance = authority("oc_finance", "unknown"); finance["value"] = "Финальное согласование выполняет финансовый контролёр."
        ambiguity = AuthorityAmbiguity.from_items([lead, finance])
        package = compose_operational_context_package(
            query=new_free_form_text("Кто выполняет финальное согласование?"), task=trusted_system_text("pb0_response_format_v1"),
            retrieval_result=RetrievalResult((), (), (ambiguity,)), memory_selection=None,
        )
        calls: list[tuple[str, str, dict[str, object]]] = []
        responses = iter([
            {"ok": True, "answer": '{"reasoning_error":true,"response_text":""}', "model": OC5_LOCAL_MODEL},
            {"ok": True, "answer": "В текущем контексте есть противоречие: финальное согласование указано и за руководителем проекта, и за финансовым контролёром. Однозначно определить актуальный вариант нельзя.", "model": OC5_LOCAL_MODEL},
        ])
        result = run_operational_context_dialogue(package, local_executor=lambda prompt, system, **kwargs: calls.append((prompt, system, kwargs)) or next(responses))
        self.assertIn("руководителем проекта", result.answer)
        self.assertIn("финансовым контролёром", result.answer)
        self.assertNotIn("reasoning_error", result.answer)
        self.assertEqual(len(calls), 2)
        self.assertIn("руководитель проекта", calls[0][0])
        self.assertIn("финансовый контролёр", calls[0][0])
        self.assertFalse(calls[0][2]["json_mode"])

    def test_recovery_failure_is_human_readable_and_never_uses_external(self) -> None:
        package = compose_operational_context_package(
            query=new_free_form_text("Вопрос"), task=trusted_system_text("pb0_response_format_v1"),
            retrieval_result=RetrievalResult((), (), ()), memory_selection=None,
        )
        external: list[str] = []
        result = run_operational_context_dialogue(
            package,
            local_executor=lambda *args, **kwargs: {"ok": True, "answer": '{"reasoning_error":true,"response_text":""}', "model": OC5_LOCAL_MODEL},
            external_executor=lambda prompt: external.append(prompt) or {"ok": True, "answer": "leak"},
        )
        self.assertEqual(result.answer, SAFE_RUNTIME_RESPONSE_MESSAGE)
        self.assertEqual(external, [])

    def test_requested_json_with_internal_looking_key_is_not_globally_blocked(self) -> None:
        package = compose_operational_context_package(
            query=new_free_form_text("Верни JSON"), task=trusted_system_text("pb0_response_format_v1"),
            retrieval_result=RetrievalResult((), (), ()), memory_selection=None,
        )
        calls = []
        result = run_operational_context_dialogue(package, local_executor=lambda *args, **kwargs: calls.append(args) or {"ok": True, "answer": '{"response_text":"пользовательский JSON"}', "model": OC5_LOCAL_MODEL})
        self.assertEqual(result.answer, '{"response_text":"пользовательский JSON"}')
        self.assertIn("явно запросил JSON", calls[0][1])

    def test_requested_json_never_allows_internal_reasoning_error_to_leak(self) -> None:
        package = compose_operational_context_package(
            query=new_free_form_text("Верни JSON"), task=trusted_system_text("pb0_response_format_v1"),
            retrieval_result=RetrievalResult((), (), ()), memory_selection=None,
        )
        responses = iter([
            {"ok": True, "answer": '{"reasoning_error":true,"response_text":""}', "model": OC5_LOCAL_MODEL},
            {"ok": True, "answer": '{"requested":"json"}', "model": OC5_LOCAL_MODEL},
        ])
        result = run_operational_context_dialogue(package, local_executor=lambda *args, **kwargs: next(responses))
        self.assertEqual(result.answer, '{"requested":"json"}')
    def test_configured_oc5_route_uses_qwen_3_5_9b(self) -> None:
        route = resolve_route("operational_context_dialogue")
        self.assertEqual(route["model"], OC5_LOCAL_MODEL)
        self.assertEqual(route["context_length"], NORMAL_CONTEXT_TOKENS)

    def test_free_form_unknown_uses_qwen_local_route(self) -> None:
        package = compose_operational_context_package(
            query=new_free_form_text("Какой текущий статус?"), task=trusted_system_text("pb0_response_format_v1"),
            retrieval_result=RetrievalResult((authority("oc_a"),), (), ()), memory_selection=None,
        )
        calls: list[dict[str, object]] = []
        result = run_operational_context_dialogue(package, local_executor=lambda *args, **kwargs: calls.append(kwargs) or {"ok": True, "answer": "Ответ", "model": OC5_LOCAL_MODEL})
        self.assertTrue(result.ok)
        self.assertEqual(result.route, "local")
        self.assertEqual(result.model, OC5_LOCAL_MODEL)
        self.assertEqual(calls[0]["task"], "operational_context_dialogue")

    def test_unknown_current_authority_stays_local_only(self) -> None:
        package = compose_operational_context_package(
            query=new_free_form_text("Статус"), task=trusted_system_text("pb0_response_format_v1"),
            retrieval_result=RetrievalResult((authority("oc_unknown", "unknown"),), (), ()), memory_selection=None,
        )
        external: list[str] = []
        result = run_operational_context_dialogue(package, local_executor=lambda *args, **kwargs: {"ok": True, "answer": "Локальный ответ"}, external_executor=lambda _: external.append("called") or {"ok": True, "answer": "leak"})
        self.assertEqual(result.route, "local"); self.assertEqual(external, [])

    def test_restricted_conflict_never_reaches_external_executor(self) -> None:
        ambiguity = AuthorityAmbiguity.from_items([authority("oc_standard"), authority("oc_restricted", "restricted")])
        package = standard_package(RetrievalResult((), (), (ambiguity,)))
        external_calls: list[str] = []
        result = run_operational_context_dialogue(
            package,
            local_executor=lambda *args, **kwargs: {"ok": True, "answer": "Есть неразрешённое расхождение", "model": OC5_LOCAL_MODEL},
            external_executor=lambda prompt: external_calls.append(prompt) or {"ok": True, "answer": "leak"},
        )
        self.assertEqual(result.route, "local")
        self.assertEqual(external_calls, [])
        self.assertIn("restricted", str(package.as_dict()))

    def test_unknown_conflict_never_reaches_external_executor(self) -> None:
        ambiguity = AuthorityAmbiguity.from_items([authority("oc_standard"), authority("oc_unknown", "unknown")])
        package = standard_package(RetrievalResult((), (), (ambiguity,)))
        external_calls: list[str] = []
        result = run_operational_context_dialogue(
            package,
            local_executor=lambda *args, **kwargs: {"ok": True, "answer": "Есть неразрешённое расхождение", "model": OC5_LOCAL_MODEL},
            external_executor=lambda prompt: external_calls.append(prompt) or {"ok": True, "answer": "leak"},
        )
        self.assertEqual(result.route, "local")
        self.assertEqual(external_calls, [])
        self.assertIn("unknown", str(package.as_dict()))

    def test_all_standard_conflict_is_external_eligible_without_winner(self) -> None:
        ambiguity = AuthorityAmbiguity.from_items([authority("oc_a"), authority("oc_b")])
        package = standard_package(RetrievalResult((), (), (ambiguity,)))
        sent: list[str] = []
        result = run_operational_context_dialogue(package, external_executor=lambda prompt: sent.append(prompt) or {"ok": True, "answer": "Уточните расхождение", "model": "test-external"})
        self.assertTrue(result.ok)
        self.assertEqual(result.route, "external_eligible")
        self.assertEqual(len(sent), 1)
        self.assertIn("ambiguities", sent[0])

    def test_oversize_is_rejected_before_any_provider_call(self) -> None:
        package = compose_operational_context_package(
            query=new_free_form_text("x" * (NORMAL_CONTEXT_TOKENS * 5)), task=trusted_system_text("pb0_response_format_v1"),
            retrieval_result=RetrievalResult((), (), ()), memory_selection=None,
            budget=OperationalContextPackageBudget(NORMAL_CONTEXT_TOKENS * 8),
        )
        result = run_operational_context_dialogue(package, local_executor=lambda *args, **kwargs: self.fail("provider must not be called"))
        self.assertFalse(result.ok)
        self.assertTrue(result.oversize_rejected)
        self.assertEqual(result.answer, SAFE_OVERSIZE_MESSAGE)

    def test_omitted_required_unit_is_rejected_before_any_provider_call(self) -> None:
        base = compose_operational_context_package(
            query=new_free_form_text("Запрос"), task=trusted_system_text("pb0_response_format_v1"),
            retrieval_result=RetrievalResult((), (), ()), memory_selection=None,
        )
        package = compose_operational_context_package(
            query=new_free_form_text("Запрос"), task=trusted_system_text("pb0_response_format_v1"),
            retrieval_result=RetrievalResult((authority("oc_a"),), (), ()), memory_selection=None,
            budget=OperationalContextPackageBudget(base.metadata.used_chars),
        )
        result = run_operational_context_dialogue(package, local_executor=lambda *args, **kwargs: self.fail("provider must not be called"))
        self.assertTrue(result.oversize_rejected)
        self.assertEqual(result.answer, SAFE_OVERSIZE_MESSAGE)

    def test_missing_oc5_route_fails_instead_of_using_default_provider(self) -> None:
        package = compose_operational_context_package(
            query=new_free_form_text("Запрос"), task=trusted_system_text("pb0_response_format_v1"),
            retrieval_result=RetrievalResult((), (), ()), memory_selection=None,
        )
        with patch("gaia.operational_context_runtime.resolve_route", return_value={"provider": "lm_studio", "model": "local-model"}):
            result = run_operational_context_dialogue(package, local_executor=lambda *args, **kwargs: self.fail("default provider must not be called"))
        self.assertFalse(result.ok)
        self.assertEqual(result.route, "local")

    def test_changed_response_reserve_is_rejected_before_provider_call(self) -> None:
        package = compose_operational_context_package(
            query=new_free_form_text("Запрос"), task=trusted_system_text("pb0_response_format_v1"),
            retrieval_result=RetrievalResult((), (), ()), memory_selection=None,
        )
        route = resolve_route("operational_context_dialogue")
        route["max_tokens"] = 901
        with patch("gaia.operational_context_runtime.resolve_route", return_value=route):
            result = run_operational_context_dialogue(package, local_executor=lambda *args, **kwargs: self.fail("provider must not be called"))
        self.assertFalse(result.ok)
        self.assertEqual(result.route, "local")

    def test_local_failure_has_no_external_fallback(self) -> None:
        package = compose_operational_context_package(
            query=new_free_form_text("Запрос"), task=trusted_system_text("pb0_response_format_v1"),
            retrieval_result=RetrievalResult((authority("oc_restricted", "restricted"),), (), ()), memory_selection=None,
        )
        result = run_operational_context_dialogue(package, local_executor=lambda *args, **kwargs: {"ok": False}, external_executor=lambda _: self.fail("external fallback is forbidden"))
        self.assertFalse(result.ok)
        self.assertEqual(result.route, "local")


if __name__ == "__main__":
    unittest.main()
