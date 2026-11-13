"""Local material discovery must not initialise reviews or cross workspaces."""
from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from gaia.controlled_intake import ControlledIntake
from gaia.protection import protect
from gaia.provenance import ProvenanceStore
from gaia.review import ReviewService
from gaia.server import Handler


class MaterialReadonlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ProvenanceStore(Path(self.tmp.name) / "store")
        self.intake = ControlledIntake(self.store)
        admitted = self.intake.admit("first", [("synthetic.txt", "Резервный маршрут согласован.".encode())])
        self.workspace = admitted["workspace_id"]
        self.artifact = admitted["materials"][0]["sanitized_id"]
        self.intake._workspace_for("second")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.store_patch = patch("gaia.controlled_intake.default_store", return_value=self.store)
        self.store_patch.start()
        self.addCleanup(self.store_patch.stop)
        model_patch = patch("gaia.review.local_model_review", side_effect=AssertionError("No model during discovery"))
        self.model = model_patch.start()
        self.addCleanup(model_patch.stop)

    def snapshot(self):
        return {str(p.relative_to(self.store.root)): p.read_bytes() for p in self.store.root.rglob("*") if p.is_file()}

    def get(self, project="first", artifact=None, read_only=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=3)
        suffix = "&read_only=1" if read_only else ""
        connection.request("GET", f"/api/reviews/{artifact or self.artifact}?project={project}{suffix}")
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
        return response.status, payload

    def test_missing_review_is_read_without_creating_state_even_on_repeat(self):
        before = self.snapshot()
        for _ in range(2):
            status, payload = self.get()
            self.assertEqual(status, 200)
            self.assertEqual(payload["cleaned_text"], "Резервный маршрут согласован.")
            self.assertEqual(payload["state"], "not_started")
            self.assertFalse(payload["confirmed"])
            self.assertEqual(set(payload), {"artifact_id", "state", "findings", "decisions", "confirmed", "error_code", "trace_id", "confirmation_method", "carried_decisions", "cleaned_text"})
        self.assertEqual(before, self.snapshot())
        self.model.assert_not_called()

    def test_existing_confirmation_has_the_same_projection_and_no_write(self):
        review = ReviewService(self.store, self.workspace, lambda _: {"status": "completed", "findings": []})
        review.start(self.artifact)
        review.confirm(self.artifact)
        expected = review.get(self.artifact, include_text=True)
        before = self.snapshot()
        self.assertEqual(self.get(), (200, expected))
        self.assertEqual(before, self.snapshot())
        self.model.assert_not_called()

    def test_foreign_unknown_and_path_like_ids_are_safe_read_only_refusals(self):
        before = self.snapshot()
        for project, artifact in [("second", None), ("unknown", None), ("first", "..%2Fregistry"), ("first", "missing")]:
            with self.subTest(project=project, artifact=artifact):
                status, payload = self.get(project, artifact)
                self.assertEqual(status, 404)
                text = json.dumps(payload, ensure_ascii=False)
                self.assertNotIn("Резервный", text)
                self.assertNotIn(str(self.store.root), text)
        self.assertEqual(before, self.snapshot())
        self.model.assert_not_called()

    def test_stale_source_or_cleaned_version_and_corruption_are_rejected(self):
        metadata = self.store.object_metadata(self.workspace, self.artifact)
        newer = protect(self.store, self.workspace, metadata["parents"][0], rules_version="synthetic-v2")["sanitized"]
        before = self.snapshot()
        self.assertEqual(self.get()[0], 404)
        self.assertEqual(before, self.snapshot())
        self.artifact = newer["artifact_id"]
        path = self.store.root / "sanitized" / self.workspace / (self.artifact + ".txt")
        path.write_text("SYNTHETIC_CORRUPTION")
        before = self.snapshot()
        self.assertEqual(self.get()[0], 404)
        self.assertEqual(before, self.snapshot())
        path.write_text("Резервный маршрут согласован.")
        self.intake.admit("first", [("synthetic.txt", "Новый маршрут ожидает решения.".encode())])
        before = self.snapshot()
        self.assertEqual(self.get()[0], 404)
        self.assertEqual(before, self.snapshot())

    def test_default_review_get_still_restores_state_without_running_model(self):
        self.assertFalse((self.store.root / "metadata" / "review_state.json").exists())
        self.assertEqual(self.get(read_only=False)[0], 200)
        state = json.loads((self.store.root / "metadata" / "review_state.json").read_text())
        self.assertEqual(state[self.artifact]["state"], "not_started")
        self.model.assert_not_called()


if __name__ == "__main__":
    unittest.main()
