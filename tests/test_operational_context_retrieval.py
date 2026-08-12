from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from gaia.operational_context import OperationalContextStore, SafeProvenance, new_evidence, new_item
from gaia.operational_context_retrieval import (
    OperationalContextReader,
    RetrievalRequest,
    TrustedLocalProcessingPolicy,
)


class OperationalContextRetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(); self.store = OperationalContextStore(Path(self.tmp.name)); self.reader = OperationalContextReader(self.store)

    def tearDown(self) -> None: self.tmp.cleanup()

    def request(self, **changes):
        base = dict(user_ref="user_a", project_ref="project_a", system_ref="system_a", supported_kinds=frozenset({"requirement", "action"}), trusted_local_policy=TrustedLocalProcessingPolicy(), max_items=10, max_chars=10_000)
        base.update(changes); return RetrievalRequest(**base)

    def add(self, *, item_id="oc_1", evidence_id="oce_1", scope="project", scope_ref="project_a", kind="requirement", subject_ref="subject_1", value="Synthetic value", sensitivity="standard"):
        provenance = SafeProvenance(candidate_ref=f"candidate_{item_id}")
        item = new_item(scope=scope, scope_ref=scope_ref, kind=kind, subject_ref=subject_ref, value=value, provenance=provenance, confirmation_ref=evidence_id, sensitivity=sensitivity, item_id=item_id)
        evidence = new_evidence(scope=scope, scope_ref=scope_ref, action="promotion", target_item_id=item_id, actor_ref="actor_1", candidate_ref=f"candidate_{item_id}", evidence_id=evidence_id)
        return self.store.create(item, evidence)

    def test_exact_project_isolation_and_no_fallback(self):
        self.add(item_id="oc_a", evidence_id="oce_a"); self.add(item_id="oc_b", evidence_id="oce_b", scope_ref="project_b")
        result = self.reader.retrieve(self.request()).as_dict()
        self.assertEqual([item["id"] for item in result["eligible_items"]], ["oc_a"])
        self.assertEqual(self.reader.retrieve(self.request(project_ref="project_missing")).eligible_items, ())

    def test_active_confirmation_kind_and_trusted_local_sensitivity_eligibility(self):
        self.add(item_id="oc_ok", evidence_id="oce_ok", subject_ref="subject_ok")
        self.add(item_id="oc_secret", evidence_id="oce_secret", subject_ref="subject_secret", sensitivity="restricted")
        self.add(item_id="oc_action", evidence_id="oce_action", scope="user", scope_ref="user_a", kind="action", subject_ref="subject_action")
        self.add(item_id="oc_system", evidence_id="oce_system", scope="system", scope_ref="system_a", subject_ref="subject_system")
        denied = self.reader.retrieve(self.request()).as_dict()
        self.assertEqual([item["id"] for item in denied["eligible_items"]], ["oc_action", "oc_ok", "oc_system"])
        self.assertIn({"reason": "trusted_local_sensitivity_denied", "item_id": "oc_secret"}, denied["exclusions"])
        allowed = self.reader.retrieve(self.request(trusted_local_policy=TrustedLocalProcessingPolicy(frozenset({"standard", "restricted"})))).as_dict()
        self.assertIn("oc_secret", [item["id"] for item in allowed["eligible_items"]])
        kinds = self.reader.retrieve(self.request(supported_kinds=frozenset({"action"}))).as_dict()
        self.assertEqual(kinds["eligible_items"][0]["id"], "oc_action")
        self.assertIn({"reason": "unsupported_kind", "item_id": "oc_ok"}, kinds["exclusions"])

    def test_lifecycle_task_filter_budget_and_atomic_metadata(self):
        old = self.add(item_id="oc_old", evidence_id="oce_old", subject_ref="subject_old")
        retirement = new_evidence(scope="project", scope_ref="project_a", action="retirement", target_item_id=old["id"], actor_ref="actor_1", evidence_id="oce_retired")
        self.store.retire(scope="project", scope_ref="project_a", item_id=old["id"], evidence=retirement)
        first = self.add(item_id="oc_1", evidence_id="oce_1", subject_ref="subject_1"); second = self.add(item_id="oc_2", evidence_id="oce_2", subject_ref="subject_2")
        result = self.reader.retrieve(self.request(task_subject_refs=frozenset({"subject_1"}), max_items=1, max_chars=10_000)).as_dict()
        self.assertEqual([item["id"] for item in result["eligible_items"]], [first["id"]])
        self.assertIn({"reason": "not_applicable", "item_id": second["id"]}, result["exclusions"])
        self.assertIn({"reason": "not_active", "item_id": old["id"]}, result["exclusions"])
        bounded = self.reader.retrieve(self.request(max_items=1, max_chars=10_000)).as_dict()
        self.assertEqual(len(bounded["eligible_items"]), 1); self.assertIn({"reason": "budget_exceeded", "item_id": "oc_2"}, bounded["exclusions"])
        self.assertIn("provenance", bounded["eligible_items"][0]); self.assertIn("confirmation_ref", bounded["eligible_items"][0])

    def test_invalid_partition_is_safe_and_not_authority(self):
        self.add(); path = self.store._partition_path("project", "project_a"); state = json.loads(path.read_text(encoding="utf-8")); state["evidence"]["oce_1"]["candidate_ref"] = "candidate_wrong"; path.write_text(json.dumps(state), encoding="utf-8")
        result = self.reader.retrieve(self.request()).as_dict()
        self.assertEqual(result["eligible_items"], []); self.assertIn({"reason": "invalid_record"}, result["exclusions"])

    def test_cross_scope_project_and_system_candidates_are_ambiguous_without_composition_rule(self):
        self.add(item_id="oc_project", evidence_id="oce_project", value="A")
        self.add(item_id="oc_system", evidence_id="oce_system", scope="system", scope_ref="system_a", value="B")
        result = self.reader.retrieve(self.request()).as_dict()
        self.assertEqual(result["eligible_items"], [])
        self.assertEqual(len(result["ambiguities"]), 1)
        ambiguity = result["ambiguities"][0]
        self.assertEqual((ambiguity["kind"], ambiguity["subject_ref"], ambiguity["derived_sensitivity"]), ("requirement", "subject_1", "standard"))
        self.assertEqual({item["item_id"] for item in ambiguity["involved_authorities"]}, {"oc_project", "oc_system"})
        self.assertEqual({item["scope"] for item in ambiguity["involved_authorities"]}, {"project", "system"})
        self.assertEqual({item["provenance"]["candidate_ref"] for item in ambiguity["involved_authorities"]}, {"candidate_oc_project", "candidate_oc_system"})

    def test_cross_scope_user_and_project_candidates_are_ambiguous_without_composition_rule(self):
        self.add(item_id="oc_user", evidence_id="oce_user", scope="user", scope_ref="user_a", kind="action", subject_ref="action_1")
        self.add(item_id="oc_project", evidence_id="oce_project", kind="action", subject_ref="action_1")
        result = self.reader.retrieve(self.request()).as_dict()
        self.assertEqual(result["eligible_items"], [])
        self.assertEqual({item["item_id"] for item in result["ambiguities"][0]["involved_authorities"]}, {"oc_user", "oc_project"})

    def test_different_subjects_coexist(self):
        self.add(item_id="oc_project", evidence_id="oce_project", subject_ref="subject_a")
        self.add(item_id="oc_system", evidence_id="oce_system", scope="system", scope_ref="system_a", subject_ref="subject_b")
        result = self.reader.retrieve(self.request()).as_dict()
        self.assertEqual({item["id"] for item in result["eligible_items"]}, {"oc_project", "oc_system"})
        self.assertEqual(result["ambiguities"], [])

    def test_restricted_candidate_participates_and_derives_restricted_ambiguity(self):
        self.add(item_id="oc_standard", evidence_id="oce_standard", value="A", sensitivity="standard")
        self.add(item_id="oc_restricted", evidence_id="oce_restricted", scope="system", scope_ref="system_a", value="B", sensitivity="restricted")
        result = self.reader.retrieve(self.request(trusted_local_policy=TrustedLocalProcessingPolicy(frozenset({"standard", "restricted"})))).as_dict()
        self.assertEqual(result["eligible_items"], [])
        self.assertEqual(result["ambiguities"][0]["derived_sensitivity"], "restricted")
        self.assertEqual({item["item_id"] for item in result["ambiguities"][0]["involved_authorities"]}, {"oc_standard", "oc_restricted"})

    def test_trusted_local_denial_is_not_downstream_disclosure(self):
        self.add(item_id="oc_restricted", evidence_id="oce_restricted", sensitivity="restricted")
        denied = self.reader.retrieve(self.request()).as_dict()
        allowed_policy = TrustedLocalProcessingPolicy(frozenset({"standard", "restricted"}))
        allowed = self.reader.retrieve(self.request(trusted_local_policy=allowed_policy)).as_dict()
        self.assertEqual(denied["eligible_items"], [])
        self.assertEqual([item["id"] for item in allowed["eligible_items"]], ["oc_restricted"])
        self.assertNotIn("allow_restricted", RetrievalRequest.__dataclass_fields__)
        for future_external_consumer_capability in (False, True):
            self.assertEqual(
                self.reader.retrieve(self.request(trusted_local_policy=allowed_policy)).as_dict(),
                allowed,
                msg=f"external capability {future_external_consumer_capability} must not affect OC-2",
            )

    def test_budget_never_turns_a_conflict_into_a_winner(self):
        self.add(item_id="oc_project", evidence_id="oce_project")
        self.add(item_id="oc_system", evidence_id="oce_system", scope="system", scope_ref="system_a")
        result = self.reader.retrieve(self.request(max_items=1)).as_dict()
        self.assertEqual(result["eligible_items"], [])
        self.assertEqual(len(result["ambiguities"]), 1)

    def test_empty_state_never_reads_legacy(self):
        result = self.reader.retrieve(self.request()).as_dict()
        self.assertEqual(result, {"eligible_items": [], "exclusions": [], "ambiguities": []})


if __name__ == "__main__": unittest.main()
