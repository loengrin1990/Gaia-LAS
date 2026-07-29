from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from gaia.context_overview import overview
from gaia.controlled_intake import ControlledIntake
from gaia.provenance import ProvenanceStore
from gaia.server import Handler


def item(name: str, **values: object) -> dict[str, object]:
    result = {
        "id": name, "kind": "context", "workspace_id": "one", "status": "confirmed", "current": True,
        "item_type": "decision", "title": name, "statement": "Подтверждённый элемент.", "actor_ref": "",
        "deadline": "", "explicit_status": "", "priority": "", "updated_at": "2026-07-28T00:00:00",
        "source_links": ["safe"], "relation_ids": [],
    }
    result.update(values)
    return result


class ContextOverviewTests(unittest.TestCase):
    def test_uses_only_current_confirmed_items_for_content_and_keeps_workflow_separate(self) -> None:
        payload = overview([
            item("decision"), item("pending", status="requires_review", item_type="risk"),
            item("conflict", status="conflicted", item_type="action"), item("rejected", status="rejected"),
            item("old", current=False),
        ])
        self.assertEqual(payload["workflow"], {"confirmed": 1, "requires_review": 1, "conflicted": 1, "pending_total": 2})
        self.assertEqual(payload["counts"]["decision"], 1)
        self.assertEqual(payload["counts"]["risk"], 0)
        self.assertEqual(payload["highlights"]["decisions"][0]["title"], "decision")

    def test_actions_with_deadline_are_highlighted_first_and_projection_is_safe(self) -> None:
        payload = overview([
            item("without", item_type="action", updated_at="2026-07-29T00:00:00"),
            item("with", item_type="action", deadline="до пятницы", actor_ref="[Роль]", relation_ids=["related"]),
        ])
        self.assertEqual([row["title"] for row in payload["highlights"]["actions"]], ["with", "without"])
        self.assertEqual(payload["attention"], {"risks": 0, "open_questions": 0, "actions_without_actor": 1, "actions_without_deadline": 1, "related_items": 1})
        self.assertEqual(payload["actors"], [{"value": "[Роль]", "count": 1}])
        forbidden = {"id", "workspace_id", "source_links", "relation_ids", "block", "model", "prompt"}
        self.assertFalse(forbidden & set(payload["highlights"]["actions"][0]))

    def test_actor_facets_and_attention_exclude_rejected_and_superseded_versions(self) -> None:
        payload = overview([
            item("safe", item_type="risk", actor_ref="[Роль проекта]"),
            item("rejected", actor_ref="[Не показывать]", status="rejected"),
            item("superseded", actor_ref="[Не показывать]", current=False),
            item("question", item_type="open_question"),
        ])
        self.assertEqual(payload["actors"], [{"value": "[Роль проекта]", "count": 1}])
        self.assertEqual(payload["attention"]["risks"], 1)
        self.assertEqual(payload["attention"]["open_questions"], 1)

    def test_limits_are_enforced(self) -> None:
        payload = overview([item(f"risk-{i}", item_type="risk", updated_at=f"2026-07-{i:02d}T00:00:00") for i in range(1, 8)])
        self.assertEqual(len(payload["highlights"]["risks"]), 5)

    def test_http_overview_is_workspace_isolated_read_only_and_has_safe_diagnostics(self) -> None:
        temporary = tempfile.TemporaryDirectory(); store = ProvenanceStore(Path(temporary.name) / "store")
        intake = ControlledIntake(store); first = intake._workspace_for("first"); second = intake._workspace_for("second")
        store._add(item("first_ctx", workspace_id=first, title="Только первое")); store._add(item("second_ctx", workspace_id=second, title="Только второе"))
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler); threading.Thread(target=server.serve_forever, daemon=True).start()
        captured: list[tuple[tuple[object, ...], dict[str, object]]] = []
        def request(project: str) -> tuple[int, dict[str, object]]:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
            connection.request("GET", f"/api/context/overview?project={project}"); response = connection.getresponse(); payload = json.loads(response.read()); connection.close(); return response.status, payload
        try:
            with patch("gaia.controlled_intake.default_store", return_value=store), patch("gaia.server.emit_runtime_diagnostic", side_effect=lambda *args, **kwargs: captured.append((args, kwargs))):
                self.assertEqual(request("first")[1]["highlights"]["decisions"][0]["title"], "Только первое")
                self.assertEqual(request("second")[1]["highlights"]["decisions"][0]["title"], "Только второе")
                before = {str(path.relative_to(store.root)): path.read_bytes() for path in store.root.rglob("*") if path.is_file()}
                status, payload = request("never-created")
                after = {str(path.relative_to(store.root)): path.read_bytes() for path in store.root.rglob("*") if path.is_file()}
            self.assertEqual(status, 200); self.assertEqual(payload["current_context_count"], 0); self.assertEqual(before, after)
            self.assertTrue(captured)
            diagnostic = captured[0][1]
            self.assertNotIn("Только первое", json.dumps(diagnostic, ensure_ascii=False))
            self.assertNotIn("project", diagnostic)
        finally:
            server.shutdown(); server.server_close(); temporary.cleanup()
