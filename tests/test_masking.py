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

    def test_unresolved_pii_is_reported_when_detector_sees_risk_without_replacement(self) -> None:
        result = mask_with_review("query", "Проверь паспорт клиента, реквизитов в тексте нет.")

        self.assertTrue(result.review.suspected_pii)
        self.assertTrue(result.review.unresolved_pii)
        self.assertEqual(result.review.total_replacements, 0)

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

    def test_unresolved_pii_blocks_external_route(self) -> None:
        with patch("gaia.orchestrator.write_run_journal"):
            package = create_package("Автопретензии", "Проверь паспорт клиента, реквизитов в тексте нет.", [])

        self.assertTrue(package.local_fallback_required)
        self.assertFalse(package.safe_for_codex_after_confirmation)
        self.assertTrue(package.query_mask_review)
        self.assertTrue(package.query_mask_review.unresolved_pii)
        self.assertIn("Локально", package.route)

    def test_llm_review_can_only_raise_residual_risk(self) -> None:
        settings = type("Settings", (), {"veil_llm_review": True, "veil_llm_review_timeout_seconds": 1})()
        with (
            patch("gaia.masking.SETTINGS", settings),
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
