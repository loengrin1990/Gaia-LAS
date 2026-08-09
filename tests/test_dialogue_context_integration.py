from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gaia.context_assembler import DialogueContextBudget, select_trusted_context
from gaia.context_compiler import ContextService
from gaia.conversations import build_context_search_query
from gaia.controlled_intake import ControlledIntake
from gaia.models import MemorySelection, MemorySource
from gaia.orchestrator import create_package
from gaia.provenance import ProvenanceStore


def context_record(store: ProvenanceStore, workspace: str, identifier: str, **values: object) -> dict[str, object]:
    item = store._record(identifier, workspace, "context", status="confirmed", current=True,
                         item_type="decision", title="Текущее состояние B", statement="Текущий статус проекта: B.",
                         source_links=["san_b"], block_links=[{"start": 0, "end": 24}], parents=["san_b"],
                         relation_ids=[], confirmed_at="2026-08-09T12:00:00", updated_at="2026-08-09T12:00:00")
    item.update(values)
    return item


class DialogueContextIntegrationTests(unittest.TestCase):
    def memory(self, text: str = "Историческое состояние проекта: A.") -> MemorySelection:
        return MemorySelection(text, [MemorySource("mem-a", "Проект A", "memory.md", "История", 10, 12, 90, ["состояние"])], 1, ["Проект A"])

    def package(self, reader: ContextService | None, memory: MemorySelection | None) -> object:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = SimpleNamespace(runs_dir=root / "runs")
            with (
                patch("gaia.orchestrator.SETTINGS", settings),
                patch("gaia.orchestrator.journal_path", return_value=str(root / "journal.md")),
                patch("gaia.orchestrator.safety_audit_path", return_value=str(root / "audit.md")),
                patch("gaia.orchestrator.write_run_journal"),
                patch("gaia.orchestrator.select_project_memory", return_value=memory),
            ):
                return create_package("Проект A", "Текущий статус", [], dialogue_context_reader=reader)

    def test_dialogue_package_keeps_context_and_memory_as_distinct_layers(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        try:
            store = ProvenanceStore(Path(temporary.name) / "store"); workspace = store.create_workspace()
            store._add(context_record(store, workspace, "ctx_b"))
            package = self.package(ContextService(store, workspace), self.memory())
            self.assertIn("# Текущий операционный контекст", package.prompt)
            self.assertIn("# Память проекта, выбранная Lore", package.prompt)
            self.assertIn("Текущий статус проекта: B.", package.prompt)
            self.assertIn("Историческое состояние проекта: A.", package.prompt)
            self.assertIn("текущим источником истины для состояния проекта", package.prompt)
            self.assertIn("id: ctx_b", package.prompt)
            self.assertEqual(package.dialogue_context.current_authority[0].source_links, ("san_b",))
            self.assertEqual(package.memory_sources[0].path, "memory.md")
        finally:
            temporary.cleanup()

    def test_dialogue_reader_applies_confirmed_current_and_workspace_filters(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        try:
            store = ProvenanceStore(Path(temporary.name) / "store"); first, second = store.create_workspace(), store.create_workspace()
            store._add(context_record(store, first, "ctx_ok"))
            store._add(context_record(store, first, "ctx_review", status="requires_review"))
            store._add(context_record(store, first, "ctx_old", current=False))
            store._add(context_record(store, second, "ctx_other"))
            package = self.package(ContextService(store, first), self.memory())
            self.assertEqual([item.id for item in package.dialogue_context.current_authority], ["ctx_ok"])
        finally:
            temporary.cleanup()

    def test_empty_layer_combinations_are_deterministic(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        try:
            store = ProvenanceStore(Path(temporary.name) / "store"); workspace = store.create_workspace()
            store._add(context_record(store, workspace, "ctx_b"))
            both = self.package(ContextService(store, workspace), self.memory())
            context_only = self.package(ContextService(store, workspace), None)
            empty_workspace = store.create_workspace()
            memory_only = self.package(ContextService(store, empty_workspace), self.memory())
            neither = self.package(None, None)
            self.assertEqual(len(both.dialogue_context.current_authority), 1)
            self.assertIn("Lore не выбрал релевантный материал памяти.", context_only.prompt)
            self.assertIn("Историческое состояние проекта: A.", memory_only.prompt)
            self.assertIsNone(neither.dialogue_context)
            self.assertNotIn("# Текущий операционный контекст", neither.prompt)
        finally:
            temporary.cleanup()

    def test_non_dialogue_package_keeps_the_existing_lore_only_prompt(self) -> None:
        package = self.package(None, self.memory())
        self.assertIsNone(package.dialogue_context)
        self.assertIn("# Эффективный контекст, выбранный Lore", package.prompt)
        self.assertNotIn("# Текущий операционный контекст", package.prompt)

    def test_dialogue_context_uses_bounded_query_while_lore_receives_the_full_query(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        try:
            store = ProvenanceStore(Path(temporary.name) / "store"); workspace = store.create_workspace()
            store._add(context_record(store, workspace, "ctx_b"))
            full_query = "исторический контекст " * 20
            seen: list[str] = []
            with tempfile.TemporaryDirectory() as run_dir:
                settings = SimpleNamespace(runs_dir=Path(run_dir) / "runs")
                with (
                    patch("gaia.orchestrator.SETTINGS", settings),
                    patch("gaia.orchestrator.journal_path", return_value=str(Path(run_dir) / "journal.md")),
                    patch("gaia.orchestrator.safety_audit_path", return_value=str(Path(run_dir) / "audit.md")),
                    patch("gaia.orchestrator.write_run_journal"),
                    patch("gaia.orchestrator.select_project_memory", side_effect=lambda project, query, **_: seen.append(query) or self.memory()),
                ):
                    package = create_package("Проект A", full_query, [], dialogue_context_reader=ContextService(store, workspace), dialogue_context_query="текущий статус")
            self.assertEqual(seen, [full_query])
            self.assertEqual([item.id for item in package.dialogue_context.current_authority], ["ctx_b"])
        finally:
            temporary.cleanup()

    def test_follow_up_query_recovers_subject_from_the_latest_user_turn(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        try:
            store = ProvenanceStore(Path(temporary.name) / "store"); workspace = store.create_workspace()
            store._add(context_record(
                store, workspace, "ctx_b", title="Статус карточки",
                statement="Текущий пользовательский статус карточки после автоматической обработки: B.",
            ))
            from gaia.models import Conversation, ConversationMessage
            conversation = Conversation(
                "dialogue", "Проект A", "Рабочий", "active", "", "", "",
                [ConversationMessage("prior", "user", "", "Мы обсуждаем пользовательский статус карточки после автоматической обработки.", "")],
            )
            query = build_context_search_query(conversation, "А какой сейчас его статус?")
            selected = select_trusted_context(ContextService(store, workspace), query)
            self.assertEqual([item.id for item in selected], ["ctx_b"])
        finally:
            temporary.cleanup()

    def test_existing_workspace_reader_never_creates_a_workspace(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        try:
            root = Path(temporary.name) / "store"; store = ProvenanceStore(root); intake = ControlledIntake(store)
            workspace = intake._workspace_for("Проект A")
            store._add(context_record(store, workspace, "ctx_b"))
            before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            settings = SimpleNamespace(storage_dir=root)
            with patch("gaia.conversations.SETTINGS", settings):
                from gaia.conversations import existing_dialogue_context_reader
                reader = existing_dialogue_context_reader("Проект A")
                missing = existing_dialogue_context_reader("Проект без workspace")
            self.assertIsInstance(reader, ContextService)
            self.assertIsNone(missing)
            after = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            self.assertEqual(after, before)
        finally:
            temporary.cleanup()

    def test_shared_budget_is_used_for_dialogue_layers(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        try:
            store = ProvenanceStore(Path(temporary.name) / "store"); workspace = store.create_workspace()
            store._add(context_record(store, workspace, "ctx_b"))
            package = self.package(ContextService(store, workspace), self.memory("A" * 500))
            metadata = package.dialogue_context.metadata
            self.assertEqual(metadata.total_chars, DialogueContextBudget().total_chars)
            self.assertEqual(package.memory_chars, metadata.memory_chars)
            self.assertLessEqual(metadata.context_chars + metadata.memory_chars, metadata.total_chars)
        finally:
            temporary.cleanup()

    def test_malformed_eligible_context_fails_without_memory_only_fallback(self) -> None:
        class MalformedReader:
            def list(self):
                return [{
                    "id": "ctx_bad", "kind": "context", "status": "confirmed", "current": True,
                    "item_type": "decision", "title": "Текущий статус", "statement": "B", "block_links": ["not-a-record"],
                }]
        with self.assertRaises(ValueError):
            self.package(MalformedReader(), self.memory())
