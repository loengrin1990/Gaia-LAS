from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
import json

from gaia.operational_context import (KIND_REGISTRY, ConfirmationEvidence, OperationalContextError, OperationalContextStore,
                                      SafeProvenance, new_evidence, new_item)


class OperationalContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(); self.store = OperationalContextStore(Path(self.tmp.name))
        self.provenance = SafeProvenance(candidate_ref="candidate_1", memory_ref="memory_1")

    def tearDown(self) -> None: self.tmp.cleanup()

    def draft(self, *, item_id: str = "oc_1", evidence_id: str = "oce_1", scope: str = "project", scope_ref: str = "project_a", kind: str = "requirement", subject_ref: str = "subject_1", value: str = "Synthetic value", **changes):
        return new_item(scope=scope, scope_ref=scope_ref, kind=kind, subject_ref=subject_ref, value=value, provenance=self.provenance, confirmation_ref=evidence_id, item_id=item_id, **changes)

    def evidence(self, item, *, action="promotion", evidence_id="oce_1", prior_item_id="", source_ref="", candidate_ref="candidate_1"):
        return new_evidence(scope=item.scope, scope_ref=item.scope_ref, action=action, target_item_id=item.id, actor_ref="actor_1", source_ref=source_ref, candidate_ref=candidate_ref, prior_item_id=prior_item_id, evidence_id=evidence_id)

    def create(self, **kwargs):
        item = self.draft(**kwargs); return item, self.store.create(item, self.evidence(item, evidence_id=item.confirmation_ref))

    def test_valid_model_and_closed_registry(self):
        item, saved = self.create(); self.assertEqual(saved["id"], item.id); self.assertEqual(item.identity, ("project", "project_a", "requirement", "subject_1")); self.assertIn("requirement", KIND_REGISTRY)
        with self.assertRaises(OperationalContextError): self.draft(kind="anything")
        with self.assertRaises(OperationalContextError): self.draft(scope="unknown")
        with self.assertRaises(OperationalContextError): self.draft(scope_ref="")
        with self.assertRaises(OperationalContextError): self.draft(subject_ref="")
        with self.assertRaises(OperationalContextError): self.draft(value="", reference="")
        with self.assertRaises(OperationalContextError): self.draft(sensitivity="secret")
        with self.assertRaises(OperationalContextError): self.draft(scope="user", kind="requirement")

    def test_safe_provenance_and_confirmation_fail_closed(self):
        with self.assertRaises(OperationalContextError): SafeProvenance.from_mapping({"source_path": "/private/document.txt"})
        with self.assertRaises(OperationalContextError): SafeProvenance(source_ref="/private/document.txt")
        item = self.draft(); bad = self.evidence(item, evidence_id="oce_x"); self.assertNotEqual(item.confirmation_ref, bad.id)
        with self.assertRaises(OperationalContextError): self.store.create(item, bad)
        with self.assertRaises(OperationalContextError): ConfirmationEvidence("oce_2", "project", "project_a", "promotion", "oc_2", "actor_1", "", candidate_ref="candidate_1")

    def test_confirmation_evidence_must_match_item_provenance(self):
        item = self.draft()
        with self.assertRaises(OperationalContextError): self.store.create(item, self.evidence(item, candidate_ref="candidate_other"))
        source_provenance = SafeProvenance(source_ref="source_1")
        source_item = new_item(scope="project", scope_ref="project_a", kind="requirement", subject_ref="subject_source", value="Synthetic", provenance=source_provenance, confirmation_ref="oce_source", item_id="oc_source")
        with self.assertRaises(OperationalContextError): self.store.create(source_item, self.evidence(source_item, evidence_id="oce_source", source_ref="source_other", candidate_ref=""))
        self.store.create(item, self.evidence(item))
        self.store.create(source_item, self.evidence(source_item, evidence_id="oce_source", source_ref="source_1", candidate_ref=""))

    def test_scope_isolation_and_exact_reads(self):
        self.create(item_id="oc_a", evidence_id="oce_a", scope_ref="project_a")
        other, _ = self.create(item_id="oc_b", evidence_id="oce_b", scope_ref="project_b")
        self.assertEqual([x["id"] for x in self.store.list_scope(scope="project", scope_ref="project_a")], ["oc_a"])
        with self.assertRaises(OperationalContextError): self.store.get(scope="project", scope_ref="project_a", item_id=other.id)
        user = self.draft(item_id="oc_u", evidence_id="oce_u", scope="user", scope_ref="user_a", kind="action"); self.store.create(user, self.evidence(user, evidence_id="oce_u"))
        self.assertEqual(self.store.list_scope(scope="user", scope_ref="user_a")[0]["id"], "oc_u")
        with self.assertRaises(OperationalContextError): self.store.list_scope(scope="project", scope_ref="")

    def test_replacement_atomic_lineage_and_retirement(self):
        old, _ = self.create()
        failed = self.draft(item_id="oc_2", evidence_id="oce_2", supersedes_id=old.id)
        with self.assertRaises(OperationalContextError): self.store.replace(old.id, failed, self.evidence(failed, action="promotion", evidence_id="oce_2", prior_item_id=old.id))
        self.assertEqual(self.store.get(scope="project", scope_ref="project_a", item_id=old.id)["lifecycle"], "active")
        new = self.draft(item_id="oc_2", evidence_id="oce_2", supersedes_id=old.id, value="New synthetic value")
        self.store.replace(old.id, new, self.evidence(new, action="replacement", evidence_id="oce_2", prior_item_id=old.id))
        self.assertEqual(self.store.get(scope="project", scope_ref="project_a", item_id=old.id)["lifecycle"], "superseded")
        self.assertEqual(self.store.lineage(scope="project", scope_ref="project_a", item_id=old.id)["superseded_by"], [new.id])
        retire = self.evidence(new, action="retirement", evidence_id="oce_3")
        self.store.retire(scope="project", scope_ref="project_a", item_id=new.id, evidence=retire)
        self.assertEqual(self.store.get(scope="project", scope_ref="project_a", item_id=new.id)["lifecycle"], "retired")
        with self.assertRaises(OperationalContextError): self.store.retire(scope="project", scope_ref="project_a", item_id=new.id, evidence=retire)

    def test_semantic_fields_are_frozen_and_evidence_is_immutable(self):
        item, _ = self.create(); evidence = self.evidence(item, evidence_id="oce_1")
        with self.assertRaises(OperationalContextError): self.store.create(item, evidence)
        with self.assertRaises(OperationalContextError): self.store.create(replace(item, value="mutated"), self.evidence(replace(item, value="mutated"), evidence_id="oce_1"))
        saved = self.store.get(scope="project", scope_ref="project_a", item_id=item.id)
        self.assertEqual(saved["value"], "Synthetic value")
        self.assertNotIn("/private", str(self.store.evidence(scope="project", scope_ref="project_a", evidence_id="oce_1")))

    def test_legacy_context_is_not_read_or_migrated(self):
        self.create(); self.assertFalse((Path(self.tmp.name) / "registry.json").exists())
        self.assertFalse((Path(self.tmp.name) / "operational_context_v0" / "context").exists())

    def test_corrupted_persisted_partitions_fail_closed(self):
        item, _ = self.create(); path = self.store._partition_path("project", "project_a")
        cases = [
            {"schema_version": 2, "items": {}, "evidence": {}},
            {"schema_version": 1, "items": {item.id: {"id": item.id}}, "evidence": {}},
            {"schema_version": 1, "items": {}, "evidence": {"oce_bad": {"id": "oce_bad"}}},
            {"schema_version": 1, "items": {item.id: {**self.store.get(scope="project", scope_ref="project_a", item_id=item.id), "scope_ref": "project_b"}}, "evidence": {"oce_1": self.store.evidence(scope="project", scope_ref="project_a", evidence_id="oce_1")}},
        ]
        for payload in cases:
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(OperationalContextError): self.store.list_scope(scope="project", scope_ref="project_a")


if __name__ == "__main__": unittest.main()
