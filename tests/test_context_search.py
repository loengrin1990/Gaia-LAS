from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from gaia.context_search import ContextSearchError, parse_params, search
from gaia.provenance import ProvenanceStore
from gaia.server import Handler


def item(identifier: str, **values: object) -> dict[str, object]:
    result = {
        "id": identifier, "kind": "context", "workspace_id": "one", "status": "confirmed", "current": True,
        "item_type": "decision", "title": "Локальный маршрут", "statement": "Использовать локальную проверку.",
        "actor_ref": "", "deadline": "", "explicit_status": "", "priority": "", "updated_at": "2026-07-28T00:00:00",
        "source_links": ["san_1"], "relation_ids": [],
    }
    result.update(values)
    return result


class ContextSearchTests(unittest.TestCase):
    def params(self, **values: str | list[str]):
        query = {key: value if isinstance(value, list) else [value] for key, value in values.items()}
        return parse_params(query)

    def test_corpus_includes_only_current_confirmed_context_and_safe_projection(self) -> None:
        items = [item("ok"), item("review", status="requires_review"), item("old", current=False), item("wrong", kind="sanitized")]
        payload = search(items, self.params())
        self.assertEqual(payload["total"], 1)
        self.assertEqual(set(payload["results"][0]), {"item_type", "title", "statement", "actor_ref", "deadline", "explicit_status", "priority", "updated_at", "source_count", "has_related"})
        self.assertNotIn("id", payload["results"][0])

    def test_normalization_and_and_semantics_cover_visible_fields(self) -> None:
        entries = [item("one", title="Ёлка — маршрут", statement="Проверять\nлокально", actor_ref="[Роль-01]", deadline="завтра", explicit_status="назначено", priority="высокий")]
        self.assertEqual(search(entries, self.params(q="елка, ЛОКАЛЬНО"))["total"], 1)
        for value in ("роль 01", "завтра", "назначено", "высокий"):
            self.assertEqual(search(entries, self.params(q=value))["total"], 1)
        self.assertEqual(search(entries, self.params(q="елка отсутствует"))["total"], 0)

    def test_filters_pagination_sorting_and_facets_are_deterministic(self) -> None:
        entries = [
            item("b", item_type="action", title="Бета", actor_ref="[Роль]", deadline="сегодня", relation_ids=["x"], updated_at="2026-07-02T00:00:00"),
            item("a", item_type="risk", title="Альфа", updated_at="2026-07-01T00:00:00"),
            item("c", item_type="decision", title="Альфа", updated_at="2026-07-01T00:00:00"),
        ]
        self.assertEqual(search(entries, self.params(type=["action", "risk"], actor_presence="present", deadline_presence="present", related="present"))["total"], 1)
        self.assertEqual(search(entries, self.params(actor_presence="missing"))["total"], 2)
        self.assertEqual(search(entries, self.params(related="none"))["total"], 2)
        page = search(entries, self.params(sort="title_asc", limit="1", offset="1"))
        self.assertEqual((page["total"], page["returned"], page["has_more"]), (3, 1, True))
        self.assertEqual([row["title"] for row in search(entries, self.params(sort="updated_desc"))["results"]], ["Бета", "Альфа", "Альфа"])
        self.assertEqual([row["item_type"] for row in search(entries, self.params(sort="title_asc"))["results"]], ["risk", "decision", "action"])
        self.assertEqual(search(entries, self.params())["facets"]["actors"], [{"value": "[Роль]", "count": 1}])

    def test_relevance_title_and_exact_title_rank_first(self) -> None:
        entries = [item("statement", title="Другое", statement="локальный маршрут"), item("title", title="Локальный маршрут")]
        results = search(entries, self.params(q="локальный маршрут"))["results"]
        self.assertEqual([row["title"] for row in results], ["Локальный маршрут", "Другое"])

    def test_invalid_parameters_are_rejected(self) -> None:
        invalid = [
            {"q": ["x" * 201]}, {"q": [" ".join("x" for _ in range(17))]}, {"type": ["unknown"]},
            {"actor": ["роль"], "actor_presence": ["missing"]}, {"limit": ["-1"]}, {"offset": ["half"]}, {"sort": ["random"]},
        ]
        for query in invalid:
            with self.subTest(query=query), self.assertRaises(ContextSearchError):
                parse_params(query)

    def test_loopback_endpoint_is_read_only_and_diagnostics_exclude_query_text(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        store = ProvenanceStore(Path(temporary.name) / "store")
        workspace = store.create_workspace()
        store._add(item("ctx_1", workspace_id=workspace, title="Секретный результат"))
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        captured: list[tuple[tuple[object, ...], dict[str, object]]] = []
        try:
            intake = SimpleNamespace(context=lambda _: SimpleNamespace(list=lambda: [item("ctx_1", workspace_id=workspace, title="Секретный результат")]))
            with patch("gaia.server.ControlledIntake", return_value=intake), patch("gaia.server.emit_runtime_diagnostic", side_effect=lambda *args, **kwargs: captured.append((args, kwargs))):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
                connection.request("GET", "/api/context/search?project=search-project&q=%D1%81%D0%B5%D0%BA%D1%80%D0%B5%D1%82%D0%BD%D1%8B%D0%B9")
                response = connection.getresponse(); payload = json.loads(response.read()); connection.close()
            self.assertEqual(response.status, 200)
            self.assertEqual(payload["total"], 1)
            diagnostic = captured[0][1]
            self.assertNotIn("q", diagnostic)
            self.assertNotIn("Секретный", json.dumps(diagnostic, ensure_ascii=False))
        finally:
            server.shutdown(); server.server_close(); temporary.cleanup()
