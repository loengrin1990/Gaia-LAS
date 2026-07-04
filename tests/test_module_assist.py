from __future__ import annotations

import unittest

from gaia.module_assist import normalize_project_diagnostics, normalize_scribe_payload, normalize_veil_review


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


if __name__ == "__main__":
    unittest.main()
