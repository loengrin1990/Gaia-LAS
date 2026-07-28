from __future__ import annotations

import unittest
from unittest.mock import patch

from gaia.context_compiler import context_response_schema
from gaia.local_llm import local_llm_payload, run_local_llm_prompt


class ContextCompilerRouteTests(unittest.TestCase):
    def test_ollama_payload_uses_route_schema_and_limits(self) -> None:
        provider={"type":"ollama", "thinking":False, "json_mode":True, "context_length":8192}
        schema=context_response_schema(16)
        payload=local_llm_payload(provider,"qwen3.5:9b","system","prompt",0,max_tokens=2400,context_length=32768,response_schema=schema)
        self.assertEqual(payload["format"],schema); self.assertEqual(payload["options"]["num_ctx"],32768); self.assertEqual(payload["options"]["num_predict"],2400); self.assertEqual(payload["options"]["temperature"],0)

    def test_context_route_can_override_thinking_without_affecting_other_routes(self) -> None:
        provider={"type":"ollama", "thinking":False, "json_mode":True, "endpoint":"http://127.0.0.1:1"}
        route={"task":"context_compiler","provider":"test","model":"gpt-oss:20b","prompt_char_limit":9000,"max_tokens":2400,"context_length":16384,"temperature":0,"thinking":"low"}
        with patch("gaia.local_llm.resolve_route", return_value=route), patch("gaia.local_llm.provider_config",return_value=provider), patch("gaia.local_llm.urllib.request.urlopen") as call:
            call.return_value.__enter__.return_value.read.return_value=b'{"message":{"content":"{\\"candidates\\":[]}"},"done":true}'
            run_local_llm_prompt("prompt","system",task="context_compiler")
        body=__import__("json").loads(call.call_args.args[0].data.decode())
        self.assertEqual(body["think"],"low")
        self.assertFalse(provider["thinking"])

    def test_safe_metrics_do_not_contain_prompt_or_answer(self) -> None:
        provider={"type":"ollama","endpoint":"http://127.0.0.1:1","model":"qwen3.5:9b","enabled":True}
        with patch("gaia.local_llm.resolve_route", return_value={"task":"context_compiler","provider":"test","model":"qwen3.5:9b","prompt_char_limit":9000,"max_tokens":2400,"context_length":32768,"temperature":0}), patch("gaia.local_llm.provider_config",return_value=provider), patch("gaia.local_llm.urllib.request.urlopen") as call:
            call.return_value.__enter__.return_value.read.return_value=b'{"message":{"content":"{\\"candidates\\":[]}"},"done":true,"done_reason":"stop","eval_count":1}'
            result=run_local_llm_prompt("secret prompt","system",task="context_compiler")
        safe={key:result[key] for key in ("provider","model","route","done","done_reason","eval_count","num_ctx","num_predict","prompt_chars_sent","prompt_compacted")}
        self.assertNotIn("secret prompt",str(safe)); self.assertNotIn("answer",safe)
