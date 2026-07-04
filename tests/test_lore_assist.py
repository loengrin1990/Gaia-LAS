from __future__ import annotations

import unittest
from unittest.mock import patch

from gaia.lore_assist import (
    detect_gap_with_local_llm,
    normalize_gap_payload,
    normalize_rewrite_terms,
    rewrite_query_terms_with_local_llm,
)


class LoreAssistTests(unittest.TestCase):
    def test_query_rewrite_accepts_only_clean_terms(self) -> None:
        payload = {"terms": [" поля ", "БФ", "ДО", "`bad`", {"no": "x"}, ""]}

        self.assertEqual(normalize_rewrite_terms(payload), ["поля", "БФ", "ДО"])

    def test_query_rewrite_falls_back_on_bad_json(self) -> None:
        with patch("gaia.lore_assist.call_lm_studio_with_deadline", return_value={"ok": True, "answer": "nope"}):
            terms = rewrite_query_terms_with_local_llm("query", "project", "", [], timeout=1)

        self.assertEqual(terms, [])

    def test_gap_payload_is_limited_to_allowed_statuses(self) -> None:
        self.assertIsNone(normalize_gap_payload({"status": "invented"}))
        self.assertEqual(
            normalize_gap_payload({"status": "partial", "notes": [" Есть общий контекст. "], "missing_terms": ["таблица"]}),
            {"status": "partial", "notes": ["Есть общий контекст."], "missing_terms": ["таблица"]},
        )

    def test_gap_detector_returns_none_on_bad_response(self) -> None:
        with patch("gaia.lore_assist.call_lm_studio_with_deadline", return_value={"ok": False, "error": "timeout"}):
            gap = detect_gap_with_local_llm("query", [], [], timeout=1)

        self.assertIsNone(gap)


if __name__ == "__main__":
    unittest.main()
