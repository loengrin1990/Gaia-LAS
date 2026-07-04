from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gaia.conversations import add_user_turn, create_conversation, list_conversations
from gaia.models import AnalysisPackage


class ConversationTests(unittest.TestCase):
    def test_project_conversations_are_stored_per_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = SimpleNamespace(
                service_docs=Path(tmp) / "service",
                projects=Path(tmp) / "projects",
            )
            with (
                patch("gaia.conversations.SETTINGS", settings),
                patch("gaia.projects.SETTINGS", settings),
                patch("gaia.conversations.project_names", return_value=["Автопретензии", "Хаб"]),
            ):
                first = create_conversation("Автопретензии", "Первый")
                create_conversation("Хаб", "Второй")
                conversations = list_conversations("Автопретензии")

        self.assertEqual([item.id for item in conversations], [first.id])
        self.assertEqual(conversations[0].project, "Автопретензии")

    def test_add_user_turn_saves_message_and_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = SimpleNamespace(
                service_docs=Path(tmp) / "service",
                projects=Path(tmp) / "projects",
            )
            fake_package = AnalysisPackage(
                run_id="run-1",
                project="Автопретензии",
                profile_id="default",
                profile_title="Обычный анализ",
                route="Codex/ChatGPT после ручного подтверждения",
                safe_for_codex_after_confirmation=True,
                local_fallback_required=False,
                policy_notes=[],
                memory_chars=0,
                memory_sources=[],
                evidence_plan=[],
                memory_total_sections=0,
                query_mask_status="выполнено",
                query_mask_replacements=0,
                query_mask_review=None,
                masked_query="Что дальше?",
                files=[],
                prompt="safe prompt",
                journal_path="",
                safety_audit_path="",
            )

            with (
                patch("gaia.conversations.SETTINGS", settings),
                patch("gaia.projects.SETTINGS", settings),
                patch("gaia.conversations.project_names", return_value=["Автопретензии"]),
                patch("gaia.conversations.create_package", return_value=fake_package) as create_package_mock,
            ):
                conversation = create_conversation("Автопретензии", "Рабочий")
                result = add_user_turn(conversation.id, "Что дальше?", run_local=False)
                conversations = list_conversations("Автопретензии")

        self.assertEqual(result["package"]["run_id"], "run-1")
        self.assertTrue(create_package_mock.call_args.kwargs["strict_dialog_privacy"])
        self.assertEqual(len(conversations[0].messages), 1)
        self.assertEqual(conversations[0].messages[0].role, "user")
        self.assertIn("Что дальше?", conversations[0].rolling_summary)


if __name__ == "__main__":
    unittest.main()
