from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gaia.provenance import ProvenanceError, ProvenanceStore


class ProvenanceStorageTests(unittest.TestCase):
    def test_end_to_end_versions_isolation_and_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "storage"
            store = ProvenanceStore(root)
            first = store.create_workspace()
            second = store.create_workspace()
            payload = b"Synthetic source for provenance"

            source = store.accept_bytes(first, payload, "text/plain")
            duplicate = store.accept_bytes(first, payload, "text/plain")
            isolated = store.accept_bytes(second, payload, "text/plain")
            self.assertEqual(source["source_id"], duplicate["source_id"])
            self.assertNotEqual(source["source_id"], isolated["source_id"])
            self.assertNotEqual(source["checksum"], source["source_id"])
            self.assertTrue(store.verify_source(first, source["source_id"]))

            extraction = store.create_extraction(first, source["source_id"], "extractor-v1")
            cleaned = store.create_sanitized(first, extraction["artifact_id"], "rules-v1", "[PERSON_1]")
            context = store.create_context_item(first, "requirement", [cleaned["artifact_id"]])
            export = store.create_export(first, [context["context_item_id"]], "template-v1")
            revised = store.create_sanitized(first, extraction["artifact_id"], "rules-v2", "[PERSON_1]")

            self.assertFalse(store.object_metadata(first, cleaned["artifact_id"])["current"])
            self.assertTrue(revised["current"])
            self.assertEqual(revised["previous_id"], cleaned["artifact_id"])
            self.assertEqual(store.lineage(first, export["export_id"])["source_id"], source["source_id"])
            with self.assertRaises(ProvenanceError):
                store.create_export(first, [store.create_context_item(second, "risk", [store.create_sanitized(second, store.create_extraction(second, isolated["source_id"], "extractor-v1")["artifact_id"], "rules-v1", "safe")["artifact_id"]])["context_item_id"]], "template-v1")

            store.set_pseudonym(first, "synthetic-key", "[PERSON_1]")
            self.assertNotIn("synthetic-key", str(store.export_metadata(first, export["export_id"])))
            reopened = ProvenanceStore(root)
            self.assertEqual(reopened.lineage(first, export["export_id"])["export_id"], export["export_id"])
            preview = reopened.migration_preview()
            self.assertTrue(preview["read_only"])
            self.assertGreaterEqual(preview["counts"]["sources"], 2)

    def test_integrity_failure_and_recovery_do_not_mark_partial_source_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProvenanceStore(Path(tmp) / "storage")
            workspace = store.create_workspace()
            source = store.accept_bytes(workspace, b"safe", "text/plain")
            (store.source_path(workspace, source["source_id"])).write_bytes(b"changed")
            self.assertFalse(store.verify_source(workspace, source["source_id"]))
            self.assertEqual(store.source_metadata(workspace, source["source_id"])["integrity_status"], "changed")
            orphan = store.source_zone(workspace) / "orphan.bin"
            orphan.parent.mkdir(parents=True, exist_ok=True)
            orphan.write_bytes(b"partial")
            self.assertEqual(store.recover()["removed_orphans"], 1)


if __name__ == "__main__":
    unittest.main()
