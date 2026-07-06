from __future__ import annotations

import unittest
from unittest.mock import patch

from gaia.masking import mask_with_review
from gaia.orchestrator import create_package


class VeilMaskingTests(unittest.TestCase):
    def test_masks_person_phone_email_with_sentence_period(self) -> None:
        result = mask_with_review(
            "query",
            "Свяжись с Ивановым Иваном Ивановичем по телефону +7 999 123-45-67 и email test@example.com.",
        )

        self.assertEqual(result.review.total_replacements, 3)
        self.assertIn("[PERSON_", result.masked_text)
        self.assertIn("[PHONE_", result.masked_text)
        self.assertIn("[EMAIL_", result.masked_text)
        self.assertNotIn("test@example.com", result.masked_text)
        self.assertEqual(result.review.counts.get("PERSON"), 1)
        self.assertEqual(result.review.counts.get("PHONE"), 1)
        self.assertEqual(result.review.counts.get("EMAIL"), 1)
        self.assertFalse(result.review.unresolved_pii)

    def test_masks_address_passport_contract(self) -> None:
        result = mask_with_review(
            "document",
            "Адрес: г. Москва, ул. Тестовая, дом 5. Паспорт 4512 123456. Договор № АБ-123/45.",
        )

        self.assertIn("[ADDRESS_", result.masked_text)
        self.assertIn("[PASSPORT_", result.masked_text)
        self.assertIn("[CONTRACT_", result.masked_text)
        self.assertGreaterEqual(result.review.total_replacements, 3)

    def test_topic_pii_requires_manual_confirmation_without_blocking(self) -> None:
        result = mask_with_review("query", "Проверь паспорт клиента, реквизитов в тексте нет.")

        self.assertTrue(result.review.suspected_pii)
        self.assertFalse(result.review.unresolved_pii)
        self.assertTrue(result.review.manual_confirmation_required)
        self.assertEqual(result.review.total_replacements, 0)

    def test_privacy_policy_text_is_topic_risk_not_unresolved_pii(self) -> None:
        result = mask_with_review(
            "document",
            "Политика обработки персональных данных описывает порядок хранения ПД и ручное подтверждение.",
        )

        self.assertTrue(result.review.suspected_pii)
        self.assertFalse(result.review.unresolved_pii)
        self.assertTrue(result.review.manual_confirmation_required)

    def test_masks_inn_as_specific_category(self) -> None:
        result = mask_with_review("query", "Проверь ИНН 772345678901 и КПП 770101001.")

        self.assertIn("[INN_", result.masked_text)
        self.assertIn("[ID_", result.masked_text)
        self.assertEqual(result.review.counts.get("INN"), 1)
        self.assertEqual(result.review.counts.get("ID"), 1)

    def test_strict_dialog_privacy_masks_contextual_single_surname(self) -> None:
        result = mask_with_review(
            "dialog",
            "Какие документы нужно запросить у Иванова для риск-анализа?",
            strict_dialog_privacy=True,
        )

        self.assertIn("[PERSON_", result.masked_text)
        self.assertNotIn("Иванова", result.masked_text)
        self.assertEqual(result.review.counts.get("PERSON"), 1)

    def test_topic_pii_uses_manual_confirmation_route(self) -> None:
        with patch("gaia.orchestrator.write_run_journal"):
            package = create_package("Автопретензии", "Проверь паспорт клиента, реквизитов в тексте нет.", [])

        self.assertFalse(package.local_fallback_required)
        self.assertTrue(package.safe_for_codex_after_confirmation)
        self.assertTrue(package.query_mask_review)
        self.assertFalse(package.query_mask_review.unresolved_pii)
        self.assertTrue(package.query_mask_review.manual_confirmation_required)
        self.assertIn("ручного подтверждения", package.route)

    def test_final_prompt_masks_pii_from_lore_memory(self) -> None:
        memory_selection = type("MemorySelection", (), {
            "text": "Встречу вел Иванов Иван Иванович, решение принято.",
            "sources": [],
            "evidence_plan": [],
            "total_sections": 1,
            "group_code": "",
            "group_title": "",
            "group_sections": 0,
        })()
        with (
            patch("gaia.orchestrator.write_run_journal"),
            patch("gaia.orchestrator.select_project_memory", return_value=memory_selection),
        ):
            package = create_package("Автопретензии", "Сделай краткое резюме.", [])

        self.assertTrue(package.prompt_mask_review)
        self.assertEqual(package.prompt_mask_review.counts.get("PERSON"), 1)
        self.assertIn("[PERSON_", package.prompt)
        self.assertNotIn("Иванов Иван Иванович", package.prompt)
        self.assertFalse(package.local_fallback_required)

    def test_llm_review_can_only_raise_residual_risk(self) -> None:
        class FakeMasker:
            def apply(self, text: str):
                return type("Result", (), {"text": text, "counts": {}, "samples": {}})()

        settings = type("Settings", (), {"veil_llm_review": True, "veil_llm_review_timeout_seconds": 1})()
        with (
            patch("gaia.masking.SETTINGS", settings),
            patch("gaia.masking.load_privacy_masker", return_value=FakeMasker),
            patch("gaia.masking.review_masking_with_local_llm", return_value={
                "unresolved_pii": True,
                "reason": "Похожий на email остаток.",
                "categories": ["EMAIL"],
            }),
        ):
            result = mask_with_review("query", "Обычный текст без явных персональных данных.")

        self.assertTrue(result.review.unresolved_pii)
        self.assertIn("LLM review", result.review.markdown)
        self.assertIn("Похожий на email остаток.", result.review.unresolved_reason)


if __name__ == "__main__":
    unittest.main()
