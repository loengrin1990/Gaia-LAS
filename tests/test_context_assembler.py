from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gaia.context_assembler import DialogueContextBudget, compose_dialogue_context, select_trusted_context
from gaia.context_compiler import ContextService
from gaia.models import MemorySelection
from gaia.provenance import ProvenanceStore


def record(store: ProvenanceStore, workspace: str, identifier: str, **values: object) -> dict[str, object]:
    item = store._record(identifier, workspace, "context", status="confirmed", current=True,
                         item_type="decision", title="Локальный маршрут", statement="Использовать локальную проверку.",
                         actor_ref="[Координатор]", deadline="", explicit_status="назначено", priority="высокий",
                         source_links=["san_1"], block_links=[{"start": 10, "end": 20}], parents=["san_1"],
                         relation_ids=[], confirmed_at="2026-08-09T10:00:00", updated_at="2026-08-09T10:00:00")
    item.update(values)
    return item


class ContextAssemblerTests(unittest.TestCase):
    def setup(self):
        temporary = tempfile.TemporaryDirectory()
        store = ProvenanceStore(Path(temporary.name) / "store")
        workspace, other = store.create_workspace(), store.create_workspace()
        return temporary, store, workspace, other

    def test_selection_is_query_scoped_workspace_isolated_and_read_only(self) -> None:
        temporary, store, workspace, other = self.setup()
        try:
            store._add(record(store, workspace, "ctx_selected", title="Пилотный маршрут"))
            store._add(record(store, workspace, "ctx_other_query", title="Другое решение"))
            store._add(record(store, workspace, "ctx_unconfirmed", title="Пилот без подтверждения", status="requires_review"))
            store._add(record(store, workspace, "ctx_old", title="Пилот устарел", current=False))
            store._add(record(store, other, "ctx_other_workspace", title="Пилот другого проекта"))
            before = store.registry_path.read_bytes()
            selected = select_trusted_context(ContextService(store, workspace), "пилот")
            self.assertEqual([item.id for item in selected], ["ctx_selected"])
            self.assertEqual(selected[0].source_links, ("san_1",))
            self.assertEqual(selected[0].block_links, ({"start": 10, "end": 20},))
            self.assertEqual(selected[0].parents, ("san_1",))
            self.assertEqual(store.registry_path.read_bytes(), before)
        finally:
            temporary.cleanup()

    def test_empty_query_never_selects_an_unscoped_context_dump(self) -> None:
        temporary, store, workspace, _ = self.setup()
        try:
            store._add(record(store, workspace, "ctx_one"))
            self.assertEqual(select_trusted_context(ContextService(store, workspace), ""), ())
        finally:
            temporary.cleanup()

    def test_selection_accepts_every_current_context_type(self) -> None:
        temporary, store, workspace, _ = self.setup()
        try:
            types = ("requirement", "decision", "risk", "open_question", "action")
            for index, item_type in enumerate(types):
                store._add(record(store, workspace, f"ctx_{item_type}", item_type=item_type,
                                  title=f"Пилот {index}"))
            selected = select_trusted_context(
                ContextService(store, workspace), "пилот",
                DialogueContextBudget(max_context_items=len(types)),
            )
            self.assertEqual({item.item_type for item in selected}, set(types))
        finally:
            temporary.cleanup()

    def test_composition_preserves_authority_memory_and_provenance_layers(self) -> None:
        temporary, store, workspace, _ = self.setup()
        try:
            store._add(record(store, workspace, "ctx_one", title="Пилотный маршрут"))
            selected = select_trusted_context(ContextService(store, workspace), "пилот")
            memory = MemorySelection("Историческое обоснование.", [], 1, 1)
            result = compose_dialogue_context(selected, memory, DialogueContextBudget(total_chars=500, reserved_context_chars=100))
            self.assertEqual(result.current_authority, selected)
            self.assertIs(result.memory_selection, memory)
            self.assertEqual(result.memory_text, memory.text)
            self.assertEqual(result.metadata.authority_policy, "operational-context-confirmed-current-v1")
            self.assertLessEqual(result.metadata.context_chars + result.metadata.memory_chars, 500)
        finally:
            temporary.cleanup()

    def test_unused_capacity_moves_between_layers_deterministically(self) -> None:
        temporary, store, workspace, _ = self.setup()
        try:
            store._add(record(store, workspace, "ctx_one", title="Пилот", statement="Проверить."))
            selected = select_trusted_context(ContextService(store, workspace), "пилот")
            context_only = compose_dialogue_context(selected, None, DialogueContextBudget(total_chars=200, reserved_context_chars=20))
            self.assertEqual(context_only.current_authority, selected)
            memory_only = compose_dialogue_context((), MemorySelection("x" * 200, [], 1, 1), DialogueContextBudget(total_chars=100, reserved_context_chars=40))
            self.assertEqual(memory_only.memory_text, "x" * 100)
            both = compose_dialogue_context(selected, MemorySelection("y" * 200, [], 1, 1), DialogueContextBudget(total_chars=100, reserved_context_chars=70))
            self.assertEqual(both.current_authority, selected)
            self.assertLessEqual(both.metadata.context_chars + both.metadata.memory_chars, 100)
            self.assertEqual(both.metadata.memory_chars, 100 - both.metadata.context_chars)
        finally:
            temporary.cleanup()

    def test_budget_validation_and_atomic_context_records(self) -> None:
        with self.assertRaises(ValueError):
            DialogueContextBudget(total_chars=0)
        with self.assertRaises(ValueError):
            DialogueContextBudget(total_chars=10, reserved_context_chars=11)
        with self.assertRaises(ValueError):
            DialogueContextBudget(max_context_items=0)
