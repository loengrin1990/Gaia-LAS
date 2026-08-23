from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from gaia.dialogue_answer_dispatcher import (
    WORKSPACE_RESOLUTION_FAILED,
    DialogueAnswerDispatcher,
    build_hybrid_referent_hint,
    resolve_existing_dialogue_workspace,
)
from gaia.models import Conversation, ConversationMessage


def conversation(messages: list[ConversationMessage] | None = None) -> Conversation:
    return Conversation("dialogue", "workspace", "Рабочий", "active", "", "", "summary must not leave dialogue", messages or [])


def user(text: str) -> ConversationMessage:
    return ConversationMessage("user", "user", "", text, "")


def assistant(text: str) -> ConversationMessage:
    return ConversationMessage("assistant", "assistant", "", text, "")


class FakeHybridService:
    def __init__(self, result, calls):
        self.result = result; self.calls = calls

    def answer(self, query, workspace_id, *, referent_hint=""):
        self.calls.append((query, workspace_id, referent_hint))
        return self.result


class DialogueAnswerDispatcherTests(unittest.TestCase):
    def dispatch(self, path: str, *, result=None, messages=None, resolver=None):
        calls: list[tuple[str, str, str]] = []
        result = result or SimpleNamespace(status="SUCCESS", answer="Готовый ответ")
        dispatcher = DialogueAnswerDispatcher(
            lambda _: FakeHybridService(result, calls),
            resolver or (lambda _root, _project: ("SUCCESS", "ws_opaque")),
        )
        settings = SimpleNamespace(dialogue_read_path=path, storage_dir=Path("/private/tmp/gaia-dispatcher"))
        return dispatcher, settings, calls, conversation(messages)

    def test_legacy_uses_exact_existing_fallback_path_and_never_constructs_hybrid(self):
        dispatcher, settings, calls, item = self.dispatch("legacy", resolver=lambda *_: self.fail("legacy must not resolve workspace"))
        legacy_calls = []
        package = SimpleNamespace(prompt="legacy prompt")
        with patch("gaia.dialogue_answer_dispatcher.SETTINGS", settings):
            result = dispatcher.dispatch(run_local=True, conversation=item, query="Вопрос", safe_user_query="Вопрос", analysis_package=package, build_legacy_runtime=lambda *args: legacy_calls.append(args) or None, legacy_plain_answer=lambda prompt: {"ok": True, "answer": "Старый ответ"})
        self.assertEqual(result, {"ok": True, "answer": "Старый ответ", "read_path": "legacy"})
        self.assertEqual(len(legacy_calls), 1)
        self.assertEqual(calls, [])

    def test_hybrid_receives_only_masked_current_query_workspace_and_bounded_user_hint(self):
        messages = [user("Нужно ли повторное согласование гостя?"), assistant("Ответ ассистента нельзя использовать."), user("А для подрядчика?")]
        dispatcher, settings, calls, item = self.dispatch("hybrid", messages=messages)
        with patch("gaia.dialogue_answer_dispatcher.SETTINGS", settings), patch("gaia.dialogue_answer_dispatcher.run_lm_studio") as legacy:
            result = dispatcher.dispatch(run_local=True, conversation=item, query="raw query", safe_user_query="А для подрядчика?", analysis_package=SimpleNamespace(prompt="LEGACY SECRET"), build_legacy_runtime=lambda *args: self.fail("legacy runtime must not run"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["read_path"], "hybrid")
        self.assertEqual(calls, [("А для подрядчика?", "ws_opaque", "Нужно ли повторное согласование гостя?")])
        legacy.assert_not_called()

    def test_hybrid_failures_never_invoke_legacy_and_never_expose_partial_answer(self):
        for status in ("INSUFFICIENT_EVIDENCE", "LOCAL_PROVIDER_UNAVAILABLE", "PROVIDER_INCOMPLETE_RESPONSE", "PROVENANCE_UNRESOLVED"):
            with self.subTest(status=status):
                dispatcher, settings, calls, item = self.dispatch("hybrid", result=SimpleNamespace(status=status, answer="partial provider output"))
                with patch("gaia.dialogue_answer_dispatcher.SETTINGS", settings), patch("gaia.dialogue_answer_dispatcher.run_lm_studio") as legacy:
                    response = dispatcher.dispatch(run_local=True, conversation=item, query="Вопрос", safe_user_query="Вопрос", analysis_package=SimpleNamespace(prompt="legacy"), build_legacy_runtime=lambda *args: self.fail("legacy runtime must not run"))
                self.assertFalse(response["ok"])
                self.assertNotIn("partial provider output", response["answer"])
                self.assertEqual(response["read_path"], "hybrid")
                legacy.assert_not_called()
                self.assertEqual(len(calls), 1)

    def test_hybrid_fails_closed_when_workspace_resolution_fails_without_constructing_service(self):
        for resolution in ((WORKSPACE_RESOLUTION_FAILED, ""), ("WORKSPACE_RESOLUTION_AMBIGUOUS", "")):
            with self.subTest(resolution=resolution[0]):
                dispatcher, settings, calls, item = self.dispatch("hybrid", resolver=lambda *_: resolution)
                with patch("gaia.dialogue_answer_dispatcher.SETTINGS", settings):
                    response = dispatcher.dispatch(run_local=True, conversation=item, query="Вопрос", safe_user_query="Вопрос", analysis_package=SimpleNamespace(prompt="legacy"), build_legacy_runtime=lambda *args: self.fail("legacy runtime must not run"))
                self.assertFalse(response["ok"])
                self.assertEqual(response["failure_kind"], resolution[0])
                self.assertEqual(calls, [])

    def test_existing_workspace_resolution_uses_trimmed_project_mapping_and_validates_registry_without_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata = root / "metadata"
            metadata.mkdir()
            project = "SInt GateFlow"
            workspace_id = "ws_7003df9f4a5a4cf5a8a61559d74c3b08"
            key = hashlib.sha256(project.encode("utf-8")).hexdigest()
            (metadata / "intake_operations.json").write_text(json.dumps({"workspaces": {key: workspace_id}}), encoding="utf-8")
            (metadata / "registry.json").write_text(json.dumps({"workspaces": {workspace_id: {"workspace_id": workspace_id}}, "objects": {}}), encoding="utf-8")
            before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            self.assertEqual(resolve_existing_dialogue_workspace(root, f"  {project}  "), ("SUCCESS", workspace_id))
            after = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            self.assertEqual(after, before)

    def test_existing_workspace_resolution_rejects_missing_corrupt_or_invalid_mapping(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata = root / "metadata"
            metadata.mkdir()
            registry = metadata / "registry.json"
            registry.write_text(json.dumps({"workspaces": {}, "objects": {}}), encoding="utf-8")
            intake = metadata / "intake_operations.json"
            self.assertEqual(resolve_existing_dialogue_workspace(root, "SInt GateFlow"), (WORKSPACE_RESOLUTION_FAILED, ""))
            intake.write_text("{bad json", encoding="utf-8")
            self.assertEqual(resolve_existing_dialogue_workspace(root, "SInt GateFlow"), (WORKSPACE_RESOLUTION_FAILED, ""))
            key = hashlib.sha256("SInt GateFlow".encode("utf-8")).hexdigest()
            intake.write_text(json.dumps({"workspaces": {key: "ws_missing"}}), encoding="utf-8")
            self.assertEqual(resolve_existing_dialogue_workspace(root, "SInt GateFlow"), (WORKSPACE_RESOLUTION_FAILED, ""))

    def test_referent_hint_is_conservative_and_never_uses_assistant_or_summary(self):
        item = conversation([user("Нужно ли повторное согласование гостя?"), assistant("assistant evidence"), user("А для подрядчика?")])
        self.assertEqual(build_hybrid_referent_hint(item, "А для подрядчика?"), "Нужно ли повторное согласование гостя?")
        self.assertEqual(build_hybrid_referent_hint(item, "Какая система авторитетна по состоянию доступа?"), "")
        self.assertNotIn("assistant evidence", build_hybrid_referent_hint(item, "А для подрядчика?"))

    def test_run_local_false_calls_no_answer_path(self):
        dispatcher, settings, calls, item = self.dispatch("hybrid")
        with patch("gaia.dialogue_answer_dispatcher.SETTINGS", settings):
            result = dispatcher.dispatch(run_local=False, conversation=item, query="Вопрос", safe_user_query="Вопрос", analysis_package=SimpleNamespace(prompt="legacy"), build_legacy_runtime=lambda *args: self.fail("no path"))
        self.assertIsNone(result)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
