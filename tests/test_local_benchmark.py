from __future__ import annotations

import json
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gaia.local_benchmark import main, run_controlled_benchmark
from gaia.models import MemorySelection, MemorySource


class LocalBenchmarkTests(unittest.TestCase):
    def test_runs_read_only_lore_to_hearth_measurement_for_explicit_provider(self) -> None:
        selection = MemorySelection(
            text="Подтвержденный контекст.",
            sources=[MemorySource("source-1", "Тест", "memory.md", "Подтвержденный узел", 1, 3, 10, ["контекст"])],
            total_sections=1,
            indexed_projects=["Тест"],
            evidence_plan=[],
        )
        masked = SimpleNamespace(
            masked_text="Безопасный benchmark prompt.",
            review=SimpleNamespace(unresolved_pii=False),
        )
        llm_result = {
            "ok": True,
            "provider": "ollama_qwen3_14b",
            "model": "qwen3:14b",
            "answer": '{"summary":"ok","key_observations":[],"risks":[],"open_questions":[],"next_steps":["check"]}',
            "prompt_chars_sent": 26,
            "prompt_compacted": False,
        }
        with (
            patch("gaia.local_benchmark.provider_configs", return_value={"ollama_qwen3_14b": {}}),
            patch("gaia.local_benchmark.select_project_memory", return_value=selection),
            patch("gaia.local_benchmark.build_prompt", return_value="Long Lore prompt"),
            patch("gaia.local_benchmark.mask_with_review", return_value=masked),
            patch("gaia.local_benchmark.run_local_llm_prompt", return_value=llm_result) as run_local,
        ):
            result = run_controlled_benchmark(
                provider_name="ollama_qwen3_14b",
                project="Тест",
                query="Проверь контекст",
                timeout=90,
            )

        self.assertTrue(result["response"]["ok"])
        self.assertTrue(result["response"]["structured"])
        self.assertEqual(result["context"]["selected_sources"], 1)
        self.assertEqual(result["context"]["selected_headings"], ["Подтвержденный узел"])
        self.assertFalse(result["context"]["lore_assists_enabled"])
        self.assertFalse(result["context"]["veil_llm_review_enabled"])
        self.assertEqual(result["response"]["sections"]["steps"], 1)
        self.assertNotIn("answer", result["response"])
        self.assertEqual(run_local.call_args.kwargs["provider_name"], "ollama_qwen3_14b")
        self.assertEqual(run_local.call_args.kwargs["timeout"], 90)

    def test_rejects_unknown_provider_before_reading_memory(self) -> None:
        with patch("gaia.local_benchmark.provider_configs", return_value={}):
            with self.assertRaisesRegex(ValueError, "Unknown local provider"):
                run_controlled_benchmark("missing", "Тест", "Запрос")

    def test_main_writes_report_only_to_explicit_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "report.json"
            with patch("gaia.local_benchmark.run_controlled_benchmark", return_value={"response": {"ok": True}}):
                code = main([
                    "--provider", "ollama_qwen3_14b",
                    "--project", "Тест",
                    "--query", "Запрос",
                    "--output", str(output),
                ])

            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"response": {"ok": True}})


if __name__ == "__main__":
    unittest.main()
