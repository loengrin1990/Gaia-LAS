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
    derived_session_context,
    new_free_form_text,
    trusted_system_text,
)
from gaia.operational_context_retrieval import (
    AuthorityAmbiguity,
    OperationalContextReader,
    RetrievalRequest,
    RetrievalResult,
    TrustedLocalProcessingPolicy,
)
from gaia.privacy_boundary import (
    HandledInput,
    HandlingEvidence,
    ValidatedPrivacyInput,
    evaluate_external_eligibility,
    trusted_system_control,
)


def authority(item_id: str = "oc_1", *, sensitivity: str = "standard") -> dict[str, object]:
    return {
        "id": item_id, "scope": "project", "scope_ref": "project_a", "kind": "requirement",
        "subject_ref": "subject_a", "value": "Current state", "sensitivity": sensitivity,
        "provenance": {"candidate_ref": f"candidate_{item_id}", "source_ref": "", "memory_ref": ""},
        "confirmation_ref": f"oce_{item_id}",
    }


def evidence(kind: str = "trusted_system_control", reference: str = "pb0_response_format_v1") -> HandlingEvidence:
    return HandlingEvidence(kind, reference)


def memory(text: str = "Durable history", *, handling: str = "standard") -> HandledMemorySelection:
    selection = MemorySelection(text, [MemorySource("mem_1", "Project A", "memory.md", "History", 1, 2, 10, ["history"])], 1, ["Project A"])
    return HandledMemorySelection(selection, handling, evidence("reviewed_memory_standard", "memory_review_1") if handling == "standard" else None)


def session(text: str, handling: str = "standard") -> SessionContextItem:
    return SessionContextItem(text, handling, evidence("derived_from_standard", "session_1") if handling == "standard" else None)


class OperationalContextAssemblerTests(unittest.TestCase):
    def package(self, *, result: RetrievalResult | None = None, selected_memory: HandledMemorySelection | None = None, session: tuple[SessionContextItem, ...] = (), query_handling: str = "standard", task_handling: str = "standard", budget: int = 10_000):
        return compose_operational_context_package(
            query=trusted_system_text("pb0_response_format_v1") if query_handling == "standard" else HandledText("What is current?", query_handling), task=trusted_system_text("pb0_response_format_v1") if task_handling == "standard" else HandledText("Determine current authority", task_handling),
            retrieval_result=result or RetrievalResult((authority(),), (), ()),
            memory_selection=selected_memory, session_context=session,
            budget=OperationalContextPackageBudget(budget),
        )

    def test_layers_remain_separate_without_reconciliation(self):
        package = self.package(selected_memory=memory("Historical state differs"), session=(session("latest user subject"),))
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
        restricted_item = self.package(result=RetrievalResult((authority(sensitivity="restricted"),), (), ()), selected_memory=memory(), session=(session("standard session"),))
        restricted_ambiguity = AuthorityAmbiguity.from_items([authority("oc_a"), authority("oc_b", sensitivity="restricted")])
        ambiguous = self.package(result=RetrievalResult((), (), (restricted_ambiguity,)), selected_memory=memory(), session=(session("standard session"),))
        self.assertEqual(restricted_item.metadata.handling, "restricted")
        self.assertEqual(ambiguous.metadata.handling, "restricted")

    def test_standard_only_and_empty_inputs_are_normal(self):
        standard = self.package(selected_memory=memory(), session=(session("session"),))
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
            compose_operational_context_package(query=trusted_system_text("pb0_response_format_v1"), task=trusted_system_text("pb0_response_format_v1"), retrieval_result=RetrievalResult((), (), ()), memory_selection=None, session_context=(), budget=OperationalContextPackageBudget(1))
        with self.assertRaises(OperationalContextAssemblyError):
            compose_operational_context_package(query="q", task=HandledText("t", "standard"), retrieval_result=RetrievalResult((), (), ()), memory_selection=None, session_context=(), budget=OperationalContextPackageBudget())  # type: ignore[arg-type]

    def test_pb0_new_user_content_and_legacy_memory_are_unknown_and_local_only(self):
        legacy = HandledMemorySelection.legacy(memory().selection)
        package = compose_operational_context_package(
            query=new_free_form_text("new project fact"),
            task=trusted_system_text("pb0_response_format_v1"),
            retrieval_result=RetrievalResult((), (), ()), memory_selection=legacy,
        )
        self.assertEqual(package.metadata.query_handling, "unknown")
        self.assertEqual(package.metadata.memory_handling, "unknown")
        self.assertFalse(package.metadata.disclosure.eligible_for_external)
        self.assertEqual(package.metadata.disclosure.decision, "local_processing_required")
        self.assertEqual(package.metadata.query_handling, "unknown")
        self.assertEqual(package.metadata.handling, "unknown")

    def test_pb0_evidence_is_required_for_external_eligibility(self):
        package = self.package(result=RetrievalResult((authority(),), (), ()), selected_memory=memory(), session=(session("derived"),))
        self.assertTrue(package.metadata.disclosure.eligible_for_external)
        self.assertEqual(package.metadata.disclosure.decision, "external_allowed")
        with self.assertRaises(OperationalContextAssemblyError):
            HandledText("unproven", "standard")

    def test_pb0_restricted_unknown_conflict_and_budget_omission_fail_closed(self):
        conflict = AuthorityAmbiguity.from_items([authority("oc_a"), authority("oc_b", sensitivity="restricted")])
        baseline = self.package(result=RetrievalResult((), (), ()))
        packages = (
            self.package(query_handling="unknown"),
            self.package(result=RetrievalResult((authority(sensitivity="restricted"),), (), ())),
            self.package(result=RetrievalResult((), (), (conflict,))),
            self.package(selected_memory=HandledMemorySelection.legacy(memory().selection), budget=baseline.metadata.used_chars),
        )
        for package in packages:
            self.assertFalse(package.metadata.disclosure.eligible_for_external)
            self.assertEqual(package.metadata.disclosure.decision, "local_processing_required")
        self.assertEqual(packages[2].metadata.handling, "restricted")
        self.assertEqual(packages[3].metadata.handling, "unknown")

    def test_pb0_trusted_system_control_is_standard_and_no_external_call_is_performed(self):
        control = trusted_system_text("pb0_response_format_v1")
        package = compose_operational_context_package(
            query=control,
            task=trusted_system_text("pb0_response_format_v1"),
            retrieval_result=RetrievalResult((), (), ()), memory_selection=None,
        )
        self.assertTrue(package.metadata.disclosure.eligible_for_external)
        self.assertEqual(package.metadata.disclosure.decision, "external_allowed")

    def test_pb0_forged_system_control_cannot_make_semantic_user_text_standard(self):
        package = compose_operational_context_package(
            query=HandledText("секрет пользователя", "standard", evidence()),
            task=trusted_system_text("pb0_response_format_v1"),
            retrieval_result=RetrievalResult((), (), ()), memory_selection=None,
        )
        self.assertFalse(package.metadata.disclosure.eligible_for_external)
        self.assertEqual(package.metadata.disclosure.decision, "local_processing_required")

    def test_pb0_final_eligibility_rejects_direct_caller_created_handled_input(self):
        forged = HandledInput("standard", HandlingEvidence("trusted_system_control", "pb0_response_format_v1"))
        decision = evaluate_external_eligibility((forged,))
        self.assertFalse(decision.eligible_for_external)
        self.assertEqual(decision.decision, "local_processing_required")

    def test_pb0_final_eligibility_rejects_manual_validated_looking_input_with_copied_evidence(self):
        forged = ValidatedPrivacyInput(
            "standard",
            "system_control",
            HandlingEvidence("trusted_system_control", "pb0_response_format_v1"),
            "Arbitrary user semantic text.",
        )
        decision = evaluate_external_eligibility((forged,))
        self.assertFalse(decision.eligible_for_external)
        self.assertEqual(decision.decision, "local_processing_required")

    def test_pb0_final_eligibility_accepts_only_attested_registered_system_control(self):
        control = trusted_system_control("pb0_response_format_v1")
        decision = evaluate_external_eligibility((control,))
        self.assertTrue(decision.eligible_for_external)
        self.assertEqual(decision.decision, "external_allowed")

    def test_pb0_unknown_restricted_and_omitted_inputs_share_non_distinguishing_public_result(self):
        baseline = self.package(result=RetrievalResult((), (), ()))
        packages = (
            self.package(query_handling="unknown"),
            self.package(result=RetrievalResult((authority(sensitivity="restricted"),), (), ())),
            self.package(selected_memory=HandledMemorySelection.legacy(memory().selection), budget=baseline.metadata.used_chars),
        )
        for package in packages:
            self.assertEqual(package.metadata.disclosure.decision, "local_processing_required")
            self.assertEqual(package.metadata.disclosure.__dict__, {"eligible_for_external": False, "decision": "local_processing_required"})

    def test_pb0_session_inherits_the_strictest_contributor(self):
        restricted_session = derived_session_context(
            "derived session result",
            (HandledInput("standard", evidence()), HandledInput("restricted")),
            derivation_ref="session_derived_1",
        )
        unknown_session = derived_session_context(
            "derived session result", (HandledInput("unknown"),), derivation_ref="session_derived_2",
        )
        self.assertEqual(restricted_session.handling, "restricted")
        self.assertEqual(unknown_session.handling, "unknown")


if __name__ == "__main__":
    unittest.main()
