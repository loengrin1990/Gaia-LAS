from __future__ import annotations

import json
from pathlib import Path
import unittest

from gaia.config import ConfigError, config_version, validate_local_endpoint, validate_loopback_host


class ConfigContractTests(unittest.TestCase):
    def test_missing_config_version_defaults_to_current_version(self) -> None:
        self.assertEqual(config_version({}), 1)

    def test_config_version_must_be_supported(self) -> None:
        with self.assertRaisesRegex(ConfigError, "Unsupported config_version 2"):
            config_version({"config_version": 2})

    def test_config_version_must_be_integer(self) -> None:
        with self.assertRaisesRegex(ConfigError, "must be an integer"):
            config_version({"config_version": "1"})

    def test_local_llm_endpoint_must_use_loopback_ip(self) -> None:
        with self.assertRaisesRegex(ConfigError, "remote LLM endpoints are forbidden"):
            validate_local_endpoint("https://llm.example.com/v1/chat/completions", "local_llm.providers.test.endpoint")

    def test_ipv4_and_ipv6_loopback_endpoints_are_accepted(self) -> None:
        validate_local_endpoint("http://127.0.0.1:11434/api/chat", "endpoint")
        validate_local_endpoint("http://[::1]:11434/api/chat", "endpoint")

    def test_server_host_must_use_loopback_ip(self) -> None:
        with self.assertRaisesRegex(ConfigError, "loopback IP"):
            validate_loopback_host("0.0.0.0", "server.host")
        validate_loopback_host("127.0.0.1", "server.host")

    def test_example_context_compiler_route_matches_the_stage_7_contract(self) -> None:
        payload = json.loads((Path(__file__).resolve().parents[1] / "config.example.json").read_text(encoding="utf-8"))
        provider = payload["local_llm"]["providers"]["ollama_qwen3_14b"]
        route = payload["local_llm"]["routes"]["context_compiler"]
        self.assertEqual(provider["endpoint"], "http://127.0.0.1:11434/api/chat")
        self.assertEqual(route["provider"], "ollama_qwen3_14b")
        self.assertEqual(route["model"], "qwen3:14b")
        self.assertFalse(route["thinking"])
        self.assertEqual(route["structured_output"], "schema")
        self.assertEqual(route["temperature"], 0)
        self.assertEqual(route["context_length"], 16384)


if __name__ == "__main__":
    unittest.main()
