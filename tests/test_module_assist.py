from __future__ import annotations

import unittest
from unittest.mock import patch

from gaia.module_assist import (
    classify_scribe_candidates_with_local_llm,
    normalize_project_diagnostics,
    normalize_scribe_payload,
    normalize_veil_review,
)


class ModuleAssistTests(unittest.TestCase):
    def test_veil_review_requires_boolean_risk(self) -> None:
        self.assertIsNone(normalize_veil_review({"unresolved_pii": "yes"}))
        self.assertEqual(
            normalize_veil_review({"unresolved_pii": True, "reason": "Остался email.", "categories": ["EMAIL", "bad"]}),
            {"unresolved_pii": True, "reason": "Остался email.", "categories": ["EMAIL"]},
        )

    def test_scribe_payload_keeps_known_categories_only(self) -> None:
        payload = normalize_scribe_payload({
            "decisions": [" Решение "],
            "rules": ["`bad`"],
            "unknown": ["x"],
        })

        self.assertEqual(payload["decisions"], ["Решение"])
        self.assertEqual(payload["rules"], [])
        self.assertNotIn("unknown", payload)

    def test_project_diagnostics_are_strictly_normalized(self) -> None:
        diagnostics = normalize_project_diagnostics({
            "diagnostics": [
                {"severity": "warning", "title": "Нет source-summary", "detail": "Есть исходники.", "action": "Добавить summary."},
                {"severity": "bad", "title": "x", "detail": "y", "action": "z"},
            ]
        })

        self.assertEqual(diagnostics, [{
            "severity": "warning",
            "title": "Нет source-summary",
            "detail": "Есть исходники.",
            "action": "Добавить summary.",
        }])

    def test_scribe_classifier_excerpt_keeps_architecture_windows_for_long_audio(self) -> None:
        long_intro = "начало встречи " * 1000
        long_tail = "конец встречи " * 1000
        package = {
            "project": "ГДРС-отчет",
            "masked_query": "Выдели текущую и планируемую архитектуру.",
            "files": [{
                "name": "meeting.mp3",
                "extraction_note": "готово: transcript.txt",
                "masked_text": f"{long_intro} Архитектура: СКУД -> синхронизатор -> БД -> Face ID и отчет. {long_tail}",
            }],
        }

        captured = {}

        def fake_call(prompt: str, timeout: int, system: str) -> dict:
            captured["prompt"] = prompt
            return {"ok": True, "answer": '{"decisions":[],"rules":[],"risks":[],"open_questions":[],"technical_facts":[],"exclude":[]}'}

        with patch("gaia.module_assist.call_lm_studio_with_deadline", side_effect=fake_call):
            classify_scribe_candidates_with_local_llm(package, timeout=1)

        self.assertIn("meeting.mp3", captured["prompt"])
        self.assertIn("СКУД -> синхронизатор -> БД -> Face ID", captured["prompt"])


if __name__ == "__main__":
    unittest.main()
