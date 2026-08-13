from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gaia.models import MemorySelection, MemorySource
from gaia.operational_context import OperationalContextStore, SafeProvenance, new_evidence, new_item
from gaia.operational_context_assembler import (
    HandledMemorySelection,
    HandledText,
    OperationalContextAssemblyError,
    OperationalContextPackageBudget,
    SessionContextItem,
    compose_operational_context_package,
)
from gaia.operational_context_retrieval import (
    AuthorityAmbiguity,
    OperationalContextReader,
    RetrievalRequest,
    RetrievalResult,
    TrustedLocalProcessingPolicy,
)


def authority(item_id: str = "oc_1", *, sensitivity: str = "standard") -> dict[str, object]:
    return {
        "id": item_id, "scope": "project", "scope_ref": "project_a", "kind": "requirement",
        "subject_ref": "subject_a", "value": "Current state", "sensitivity": sensitivity,
        "provenance": {"candidate_ref": f"candidate_{item_id}", "source_ref": "", "memory_ref": ""},
        "confirmation_ref": f"oce_{item_id}",
    }


def memory(text: str = "Durable history", *, handling: str = "standard") -> HandledMemorySelection:
    selection = MemorySelection(text, [MemorySource("mem_1", "Project A", "memory.md", "History", 1, 2, 10, ["history"])], 1, ["Project A"])
    return HandledMemorySelection(selection, handling)


class OperationalContextAssemblerTests(unittest.TestCase):
    def package(self, *, result: RetrievalResult | None = None, selected_memory: HandledMemorySelection | None = None, session: tuple[SessionContextItem, ...] = (), query_handling: str = "standard", task_handling: str = "standard", budget: int = 10_000):
        return compose_operational_context_package(
            query=HandledText("What is current?", query_handling), task=HandledText("Determine current authority", task_handling),
            retrieval_result=result or RetrievalResult((authority(),), (), ()),
            memory_selection=selected_memory, session_context=session,
            budget=OperationalContextPackageBudget(budget),
        )

    def test_layers_remain_separate_without_reconciliation(self):
        package = self.package(selected_memory=memory("Historical state differs"), session=(SessionContextItem("latest user subject", "standard"),))
        self.assertEqual(package.current_authority[0]["value"], "Current state")
        self.assertEqual(package.memory_selection.selection.text, "Historical state differs")
        self.assertEqual(package.session_context[0].text, "latest user subject")
        self.assertEqual(package.ambiguities, ())

    def test_ambiguity_is_preserved_and_not_promoted_to_authority(self):
        ambiguous = AuthorityAmbiguity.from_items([authority("oc_a"), authority("oc_b")])
        package = self.package(result=RetrievalResult((), (), (ambiguous,)))
        self.assertEqual(package.current_authority, ())
        self.assertEqual(package.ambiguities, (ambiguous,))
        self.assertEqual(package.metadata.handling, "standard")

    def test_restricted_item_or_ambiguity_makes_package_restricted(self):
        restricted_item = self.package(result=RetrievalResult((authority(sensitivity="restricted"),), (), ()), selected_memory=memory(), session=(SessionContextItem("standard session", "standard"),))
        restricted_ambiguity = AuthorityAmbiguity.from_items([authority("oc_a"), authority("oc_b", sensitivity="restricted")])
        ambiguous = self.package(result=RetrievalResult((), (), (restricted_ambiguity,)), selected_memory=memory(), session=(SessionContextItem("standard session", "standard"),))
        self.assertEqual(restricted_item.metadata.handling, "restricted")
        self.assertEqual(ambiguous.metadata.handling, "restricted")

    def test_standard_only_and_empty_inputs_are_normal(self):
        standard = self.package(selected_memory=memory(), session=(SessionContextItem("session", "standard"),))
        empty = self.package(result=RetrievalResult((), (), ()), selected_memory=None)
        self.assertEqual(standard.metadata.handling, "standard")
        self.assertEqual(empty.current_authority, ())
        self.assertIsNone(empty.memory_selection)
        self.assertEqual(empty.metadata.handling, "standard")

    def test_budget_omits_whole_units_without_false_authority(self):
        full = self.package(selected_memory=memory("M" * 2_000))
        authority_only = self.package(selected_memory=None)
        constrained = self.package(selected_memory=memory("M" * 2_000), budget=authority_only.metadata.used_chars)
        self.assertLessEqual(constrained.metadata.used_chars, constrained.metadata.total_chars)
        self.assertEqual(constrained.current_authority, full.current_authority)
        self.assertIsNone(constrained.memory_selection)
        self.assertIn({"layer": "memory", "reference": "lore_selection", "handling": "standard", "reason": "budget_exceeded"}, [item.as_dict() for item in constrained.omissions])

    def test_insufficient_budget_never_partially_includes_authority(self):
        result = RetrievalResult((authority(),), (), ())
        baseline = self.package(result=result)
        constrained = self.package(result=result, budget=baseline.metadata.used_chars - 1)
        self.assertEqual(constrained.current_authority, ())
        self.assertIn({"layer": "operational_context", "reference": "oc_1", "handling": "standard", "reason": "budget_exceeded"}, [item.as_dict() for item in constrained.omissions])

    def test_query_task_session_and_memory_handling_are_upstream_typed_inputs(self):
        query = self.package(query_handling="restricted")
        task = self.package(task_handling="restricted")
        session = self.package(session=(SessionContextItem("restricted session", "restricted"),))
        selected_memory = self.package(selected_memory=memory(handling="restricted"))
        for package in (query, task, session, selected_memory):
            self.assertEqual(package.metadata.handling, "restricted")
        self.assertEqual(query.metadata.query_handling, "restricted")
        self.assertEqual(task.metadata.task_handling, "restricted")
        self.assertEqual(session.metadata.session_handling, "restricted")
        self.assertEqual(selected_memory.metadata.memory_handling, "restricted")

    def test_omitted_restricted_authority_or_ambiguity_never_downgrades_handling(self):
        empty = self.package(result=RetrievalResult((), (), ()))
        restricted_authority = self.package(result=RetrievalResult((authority(sensitivity="restricted"),), (), ()), budget=empty.metadata.used_chars)
        ambiguity = AuthorityAmbiguity.from_items([authority("oc_a"), authority("oc_b", sensitivity="restricted")])
        restricted_ambiguity = self.package(result=RetrievalResult((), (), (ambiguity,)), budget=empty.metadata.used_chars)
        restricted_memory = self.package(selected_memory=memory(handling="restricted"), budget=empty.metadata.used_chars)
        restricted_session = self.package(session=(SessionContextItem("restricted session", "restricted"),), budget=empty.metadata.used_chars)
        for package in (restricted_authority, restricted_ambiguity, restricted_memory, restricted_session):
            self.assertEqual(package.metadata.handling, "restricted")
            self.assertIn("restricted", [item.handling for item in package.omissions])
        self.assertEqual(restricted_authority.current_authority, ())
        self.assertEqual(restricted_ambiguity.ambiguities, ())

    def test_v0_path_never_reads_or_changes_legacy_context_or_store(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = OperationalContextStore(Path(temporary))
            item = new_item(scope="project", scope_ref="project_a", kind="requirement", subject_ref="subject_a", value="Current", provenance=SafeProvenance(candidate_ref="candidate_1"), confirmation_ref="oce_1", item_id="oc_1")
            evidence = new_evidence(scope="project", scope_ref="project_a", action="promotion", target_item_id="oc_1", actor_ref="actor_1", candidate_ref="candidate_1", evidence_id="oce_1")
            store.create(item, evidence)
            before = store._partition_path("project", "project_a").read_bytes()
            request = RetrievalRequest("user_a", "project_a", "system_a", frozenset({"requirement"}), TrustedLocalProcessingPolicy(), 10, 10_000)
            retrieval = OperationalContextReader(store).retrieve(request)
            self.package(result=retrieval, selected_memory=memory())
            self.assertEqual(store._partition_path("project", "project_a").read_bytes(), before)

    def test_invalid_input_and_base_budget_fail_closed(self):
        with self.assertRaises(OperationalContextAssemblyError):
            compose_operational_context_package(query=HandledText("q", "standard"), task=HandledText("t", "standard"), retrieval_result=RetrievalResult((), (), ()), memory_selection=None, session_context=(), budget=OperationalContextPackageBudget(1))
        with self.assertRaises(OperationalContextAssemblyError):
            compose_operational_context_package(query="q", task=HandledText("t", "standard"), retrieval_result=RetrievalResult((), (), ()), memory_selection=None, session_context=(), budget=OperationalContextPackageBudget())  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
