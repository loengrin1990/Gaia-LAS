from __future__ import annotations

import unittest
import time
from unittest.mock import patch

from gaia.lore_rerank import parse_json_object, rerank_with_local_llm


class LoreRerankTests(unittest.TestCase):
    def test_rerank_accepts_only_known_candidate_ids(self) -> None:
        candidates = [{"id": "known", "heading": "Known", "excerpt": "text"}]
        with patch("gaia.lore_rerank.run_lm_studio_prompt", return_value={
            "ok": True,
            "answer": '{"selected_ids":["known","hallucinated"]}',
        }):
            selected = rerank_with_local_llm("query", "profile", candidates, max_ids=4, timeout=1)

        self.assertIsNone(selected)

    def test_rerank_returns_none_on_bad_json(self) -> None:
        candidates = [{"id": "known", "heading": "Known", "excerpt": "text"}]
        with patch("gaia.lore_rerank.run_lm_studio_prompt", return_value={"ok": True, "answer": "not json"}):
            selected = rerank_with_local_llm("query", "profile", candidates, max_ids=4, timeout=1)

        self.assertIsNone(selected)

    def test_rerank_parses_json_from_markdown_fence(self) -> None:
        payload = parse_json_object('```json\n{"selected_ids":["a"]}\n```')

        self.assertEqual(payload, {"selected_ids": ["a"]})

    def test_rerank_deadline_falls_back_quickly(self) -> None:
        candidates = [{"id": "known", "heading": "Known", "excerpt": "text"}]

        def slow_llm(*args, **kwargs):
            time.sleep(2.0)
            return {"ok": True, "answer": '{"selected_ids":["known"]}'}

        started = time.monotonic()
        with patch("gaia.lore_rerank.run_lm_studio_prompt", side_effect=slow_llm):
            selected = rerank_with_local_llm("query", "profile", candidates, max_ids=4, timeout=1)
        elapsed = time.monotonic() - started

        self.assertIsNone(selected)
        self.assertLess(elapsed, 1.5)


if __name__ == "__main__":
    unittest.main()
