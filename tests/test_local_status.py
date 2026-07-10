from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from gaia.local_llm import (
    TASK_PROJECT_HEALTH,
    check_local_llm,
    check_local_provider,
    compact_prompt_for_local_model,
    check_lm_studio,
    lm_studio_models_endpoint,
    parse_json_object,
    run_lm_studio,
    run_lm_studio_prompt,
)
from gaia.server import Handler


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class LocalStatusTests(unittest.TestCase):
    def test_models_endpoint_is_derived_from_chat_completions_endpoint(self) -> None:
        self.assertTrue(lm_studio_models_endpoint().endswith("/v1/models"))

    def test_health_check_timeout_is_reported_as_busy_not_unavailable(self) -> None:
        with patch("gaia.local_llm.urllib.request.urlopen", side_effect=TimeoutError("timeout")) as urlopen:
            status = check_local_provider("ollama_qwen3_8b", timeout=1.5)

        self.assertFalse(status["available"])
        self.assertEqual(status["status"], "timeout")
        self.assertIn("занят", status["message"])
        urlopen.assert_called_once()
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 1.5)

    def test_unavailable_status_is_fast_and_readable(self) -> None:
        with patch("gaia.local_llm.urllib.request.urlopen", side_effect=ConnectionRefusedError("refused")):
            status = check_local_provider("ollama_qwen3_8b", timeout=1.5)

        self.assertFalse(status["available"])
        self.assertEqual(status["status"], "unavailable")
        self.assertIn("ollama_qwen3_8b не отвечает", status["message"])

    def test_local_answer_timeout_is_not_reported_as_offline(self) -> None:
        with patch("gaia.local_llm.urllib.request.urlopen", side_effect=TimeoutError("timeout")):
            status = run_lm_studio("test")

        self.assertFalse(status["ok"])
        self.assertEqual(status["status"], "timeout")
        self.assertIn("генерация продолжается", status["error"])

    def test_local_answer_payload_uses_configured_generation_limit(self) -> None:
        response = FakeResponse({"message": {"content": "ok"}})
        with patch("gaia.local_llm.urllib.request.urlopen", return_value=response) as urlopen:
            status = run_lm_studio("test")

        self.assertTrue(status["ok"])
        self.assertEqual(status["route"], "hearth")
        self.assertEqual(status["provider"], "ollama_qwen3_14b")
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["options"]["num_predict"], 900)
        self.assertEqual(payload["model"], "qwen3:14b")
        self.assertIn("Верни только JSON object", payload["messages"][0]["content"])

    def test_task_route_can_select_non_default_provider(self) -> None:
        response = FakeResponse({"message": {"content": "ok"}})
        with patch("gaia.local_llm.urllib.request.urlopen", return_value=response) as urlopen:
            status = run_lm_studio_prompt("test", "system", task=TASK_PROJECT_HEALTH)

        self.assertTrue(status["ok"])
        self.assertEqual(status["route"], "project_health")
        self.assertEqual(status["provider"], "ollama_qwen3_8b")
        self.assertEqual(status["model"], "qwen3:8b")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/chat")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertFalse(payload["think"])
        self.assertEqual(payload["format"], "json")
        self.assertEqual(payload["options"]["num_ctx"], 8192)
        self.assertEqual(payload["options"]["num_predict"], 300)

    def test_explicit_provider_override_does_not_require_route_edit(self) -> None:
        response = FakeResponse({"message": {"content": "ok"}})
        with patch("gaia.local_llm.urllib.request.urlopen", return_value=response) as urlopen:
            status = run_lm_studio_prompt(
                "test",
                "system",
                provider_name="ollama_qwen3_14b",
            )

        self.assertTrue(status["ok"])
        self.assertEqual(status["provider"], "ollama_qwen3_14b")
        self.assertEqual(status["model"], "qwen3:14b")
        self.assertEqual(json.loads(urlopen.call_args.args[0].data.decode("utf-8"))["model"], "qwen3:14b")

    def test_local_status_exposes_providers_and_routes(self) -> None:
        response = FakeResponse({"models": [{"name": "qwen3:8b"}, {"name": "qwen3:14b"}]})
        with patch("gaia.local_llm.urllib.request.urlopen", return_value=response):
            status = check_local_llm(timeout=1.5)

        self.assertTrue(status["available"])
        self.assertIn("lm_studio", status["providers"])
        self.assertIn("ollama_qwen3_8b", status["providers"])
        self.assertEqual(status["routes"]["project_health"]["provider"], "ollama_qwen3_8b")
        self.assertEqual(status["routes"]["hearth"]["max_tokens"], 900)
        self.assertEqual(status["routes"]["project_health"]["max_tokens"], 300)
        self.assertIn("ready", status["routes"]["project_health"])

    def test_ollama_health_checks_the_configured_model(self) -> None:
        response = FakeResponse({"models": [{"name": "qwen3:8b"}, {"name": "qwen3:14b"}]})
        with patch("gaia.local_llm.urllib.request.urlopen", return_value=response):
            status = check_local_llm(timeout=1.5)

        provider = status["providers"]["ollama_qwen3_8b"]
        self.assertTrue(provider["available"])
        self.assertTrue(provider["configured_model_available"])
        self.assertTrue(status["routes"]["project_health"]["ready"])
        self.assertTrue(status["routes"]["hearth"]["ready"])

    def test_local_answer_normalizes_structured_json(self) -> None:
        response = FakeResponse({
            "message": {
                "content": json.dumps({
                        "summary": "Главный риск в тестировании.",
                        "key_observations": ["нет GPU"],
                        "risks": [{"title": "Тестирование", "level": "high", "reason": "нет стенда", "mitigation": "уточнить дату"}],
                        "open_questions": ["когда стенд?"],
                        "next_steps": ["проверить график"],
                }, ensure_ascii=False)
            }
        })
        with patch("gaia.local_llm.urllib.request.urlopen", return_value=response):
            status = run_lm_studio("test")

        self.assertTrue(status["ok"])
        self.assertEqual(status["structured_answer"]["summary"], "Главный риск в тестировании.")
        self.assertEqual(status["structured_answer"]["risks"][0]["level"], "high")
        self.assertIn("Краткий вывод", status["answer"])

    def test_json_object_can_be_extracted_from_fenced_response(self) -> None:
        payload = parse_json_object('```json\n{"summary": "ok"}\n```')

        self.assertEqual(payload, {"summary": "ok"})

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
