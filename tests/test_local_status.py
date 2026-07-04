from __future__ import annotations

import io
import unittest
import urllib.error
from unittest.mock import patch

from gaia.local_llm import compact_prompt_for_local_model, check_lm_studio, lm_studio_models_endpoint, run_lm_studio
from gaia.server import Handler


class LocalStatusTests(unittest.TestCase):
    def test_models_endpoint_is_derived_from_chat_completions_endpoint(self) -> None:
        self.assertTrue(lm_studio_models_endpoint().endswith("/v1/models"))

    def test_health_check_timeout_is_reported_as_busy_not_unavailable(self) -> None:
        with patch("gaia.local_llm.urllib.request.urlopen", side_effect=TimeoutError("timeout")) as urlopen:
            status = check_lm_studio(timeout=1.5)

        self.assertFalse(status["available"])
        self.assertEqual(status["status"], "timeout")
        self.assertIn("занят", status["message"])
        urlopen.assert_called_once()
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 1.5)

    def test_unavailable_status_is_fast_and_readable(self) -> None:
        with patch("gaia.local_llm.urllib.request.urlopen", side_effect=ConnectionRefusedError("refused")):
            status = check_lm_studio(timeout=1.5)

        self.assertFalse(status["available"])
        self.assertEqual(status["status"], "unavailable")
        self.assertIn("LM Studio не отвечает", status["message"])

    def test_local_answer_timeout_is_not_reported_as_offline(self) -> None:
        with patch("gaia.local_llm.urllib.request.urlopen", side_effect=TimeoutError("timeout")):
            status = run_lm_studio("test")

        self.assertFalse(status["ok"])
        self.assertEqual(status["status"], "timeout")
        self.assertIn("генерация продолжается", status["error"])

    def test_local_prompt_is_compacted_inside_lore_context(self) -> None:
        prompt = (
            "intro\n"
            "# Эффективный контекст, выбранный Lore\n"
            + ("контекст " * 8000)
            + "\n# Источники выбора Lore\nsource\n# Запрос пользователя, после локальной обработки\nЧто дальше?"
        )

        compacted, changed = compact_prompt_for_local_model(prompt, limit=12000)

        self.assertTrue(changed)
        self.assertLessEqual(len(compacted), 12000)
        self.assertIn("# Источники выбора Lore", compacted)
        self.assertIn("полный prompt сохранен в Диагностике", compacted)
        self.assertIn("Что дальше?", compacted)

    def test_lm_studio_http_400_is_readable(self) -> None:
        error = urllib.error.HTTPError(
            url="http://127.0.0.1:1234/v1/chat/completions",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=io.BytesIO(b'{"error":{"message":"context length exceeded"}}'),
        )
        with patch("gaia.local_llm.urllib.request.urlopen", side_effect=error):
            status = run_lm_studio("test")

        self.assertFalse(status["ok"])
        self.assertEqual(status["status"], "bad_request")
        self.assertIn("context length exceeded", status["error"])

    def test_server_exposes_local_status_endpoint(self) -> None:
        names = Handler.do_GET.__code__.co_consts

        self.assertIn("/api/local-status", names)

    def test_analyze_rejects_empty_request_before_job_creation(self) -> None:
        names = Handler.handle_analyze.__code__.co_consts

        self.assertIn("Добавь запрос или файл для анализа.", names)


if __name__ == "__main__":
    unittest.main()
