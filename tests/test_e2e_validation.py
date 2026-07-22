from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gaia.controlled_intake import ControlledIntake
from gaia.provenance import ProvenanceStore
from gaia.server import Handler, SESSION_COOKIE_NAME, SESSION_TOKEN


class EndToEndValidationTests(unittest.TestCase):
    """The user-visible loopback path; every payload is synthetic."""

    def test_confirmed_material_reaches_context_summary_and_survives_restart(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        store = ProvenanceStore(Path(temporary.name) / "storage")
        project, other = "synthetic-e2e", "synthetic-other"
        email = "E2E" + "-EMAIL@example.test"
        phone = "+7 900 111 22 33"
        secret = "secret" + ": E2E-TOKEN-991"
        organization = "ORGANIZATION" + "_E2E"
        raw_markers = [email, phone, secret, organization]
        first_text = (
            f"{email}; {phone}; https://intranet.e2e.test/path; {secret}. "
            f"{organization} решила использовать локальный маршрут. "
            "Требование: проверять материал. Риск: задержка. Открытый вопрос: срок. "
            "Действие: Роль_1 проверяет материал до 2030-01-01."
        )
        second_text = "Решение: использовать иной локальный маршрут. Действие: Роль_1 проверяет материал до 2030-01-01."
        server: ThreadingHTTPServer | None = None

        def review_model(text: str) -> dict[str, object]:
            if organization in text:
                start = text.index(organization)
                false_start = text.index("Риск")
                return {"findings": [
                    {"category": "Организация", "start": start, "end": start + len(organization), "confidence": "high", "reason_code": "residual", "requires_review": True},
                    {"category": "Другое", "start": false_start, "end": false_start + 4, "confidence": "low", "reason_code": "false_positive", "requires_review": True},
                ]}
            return {"findings": []}

        def context_model(text: str) -> dict[str, object]:
            conflict = "иной локальный" in text
            decision = "Использовать иной локальный маршрут." if conflict else "Использовать локальный маршрут."
            return {"candidates": [
                {"type": "requirement", "title": "Проверка материала", "statement": "Проверять материал.", "block": {"start": 0, "end": 1}, "confidence": "high", "requires_review": True},
                {"type": "decision", "title": "Маршрут", "statement": decision, "block": {"start": 0, "end": 1}, "confidence": "high", "requires_review": True},
                {"type": "risk", "title": "Задержка", "statement": "Есть риск задержки.", "block": {"start": 0, "end": 1}, "confidence": "medium", "requires_review": True},
                {"type": "open_question", "title": "Срок", "statement": "Срок не указан.", "block": {"start": 0, "end": 1}, "confidence": "low", "requires_review": True},
                {"type": "action", "title": "Проверка", "statement": "Проверить материал.", "block": {"start": 0, "end": 1}, "confidence": "high", "requires_review": True, "actor_ref": "Роль_1", "deadline": "2030-01-01"},
            ]}

        with patch("gaia.controlled_intake.default_store", return_value=store), patch("gaia.review.local_model_review", side_effect=review_model), patch("gaia.context_compiler.local_context_model", side_effect=context_model), patch("gaia.server.submit_analyze_job", return_value=SimpleNamespace(id="job_e2e")):
            def start_server() -> ThreadingHTTPServer:
                instance = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                threading.Thread(target=instance.serve_forever, daemon=True).start()
                return instance

            def request(method: str, path: str, payload: dict[str, object] | bytes | None = None, content_type: str = "application/json") -> tuple[int, dict[str, object]]:
                assert server is not None
                port = server.server_address[1]
                if isinstance(payload, bytes):
                    body = payload
                elif payload is None:
                    body = None
                else:
                    body = json.dumps(payload).encode("utf-8")
                headers = {"Host": f"127.0.0.1:{port}", "Origin": f"http://127.0.0.1:{port}", "Cookie": f"{SESSION_COOKIE_NAME}={SESSION_TOKEN}", "Content-Type": content_type}
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
                connection.request(method, path, body, headers)
                response = connection.getresponse()
                data = json.loads(response.read().decode("utf-8"))
                connection.close()
                return response.status, data

            def upload(text: str) -> dict[str, object]:
                boundary = "----e2e-boundary"
                body = (
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"project\"\r\n\r\n{project}\r\n"
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"synthetic.txt\"\r\nContent-Type: text/plain\r\n\r\n{text}\r\n"
                    f"--{boundary}--\r\n"
                ).encode("utf-8")
                status, response = request("POST", "/api/analyze", body, f"multipart/form-data; boundary={boundary}")
                self.assertEqual(status, 202)
                return response

            try:
                server = start_server()
                initial = upload(first_text)
                review = initial["review"]
                assert isinstance(review, dict)
                old_sanitized = str(review["artifact_id"])
                status, materials = request("GET", f"/api/materials?project={project}")
                self.assertEqual(status, 200)
                self.assertEqual(len(materials["materials"]), 1)
                self.assertEqual(materials["materials"][0]["review_state"], "requires_review")
                self.assertNotIn(email, json.dumps(materials, ensure_ascii=False))
                self.assertNotIn(secret, json.dumps(materials, ensure_ascii=False))
                self.assertEqual(request("POST", f"/api/context/{old_sanitized}/compile", {"project": project})[0], 400)
                status, safe_report = request("GET", f"/api/protection/{old_sanitized}?project={project}")
                self.assertEqual(status, 200)
                self.assertFalse(safe_report["export_allowed"])
                self.assertNotIn(email, json.dumps(safe_report, ensure_ascii=False))
                status, lineage = request("GET", f"/api/protection/{old_sanitized}/lineage?project={project}")
                self.assertEqual(status, 200)
                source_id = str(lineage["source_id"])
                self.assertEqual(request("GET", f"/api/materials/{source_id}?project={project}")[0], 200)
                first_finding = review["findings"][0]
                second_finding = review["findings"][1]
                self.assertEqual(request("POST", f"/api/reviews/{old_sanitized}/decision", {"project": project, "finding_id": second_finding["finding_id"], "decision": "keep"})[0], 200)
                status, replaced = request("POST", f"/api/reviews/{old_sanitized}/decision", {"project": project, "finding_id": first_finding["finding_id"], "decision": "replace"})
                self.assertEqual(status, 200)
                new_sanitized = str(replaced["new_version"]["artifact_id"])
                self.assertNotEqual(old_sanitized, new_sanitized)
                self.assertEqual(replaced["review"]["artifact_id"], new_sanitized)
                self.assertFalse(replaced["review"]["confirmed"])
                self.assertEqual(len(replaced["review"]["carried_decisions"]), 2)
                status, new_review = request("GET", f"/api/reviews/{new_sanitized}?project={project}")
                self.assertEqual(status, 200)
                self.assertEqual(new_review["artifact_id"], new_sanitized)
                self.assertEqual(len(new_review["carried_decisions"]), 2)
                status, listed_after_replace = request("GET", f"/api/materials?project={project}")
                self.assertEqual(status, 200)
                self.assertEqual(listed_after_replace["materials"][0]["sanitized_id"], new_sanitized)
                self.assertEqual(request("POST", f"/api/reviews/{old_sanitized}/confirm", {"project": project})[0], 400)
                status, confirmed = request("POST", f"/api/reviews/{new_sanitized}/confirm", {"project": project})
                self.assertEqual(status, 202)
                self.assertEqual(confirmed["artifact_id"], new_sanitized)
                status, first_context = request("POST", f"/api/context/{new_sanitized}/compile", {"project": project})
                self.assertEqual(status, 202)
                candidates = first_context["candidates"]
                self.assertEqual(len(candidates), 5)
                by_type = {item["item_type"]: item for item in candidates}
                self.assertEqual(request("POST", f"/api/context/{by_type['requirement']['id']}/decision", {"project": project, "decision": "confirm"})[0], 200)
                status, edited = request("POST", f"/api/context/{by_type['requirement']['id']}/decision", {"project": project, "decision": "edit", "title": "Уточнённая проверка", "statement": "Проверять подтверждённый материал."})
                self.assertEqual(status, 200)
                self.assertEqual(request("POST", f"/api/context/{edited['id']}/decision", {"project": project, "decision": "confirm"})[0], 200)
                self.assertEqual(request("POST", f"/api/context/{by_type['decision']['id']}/decision", {"project": project, "decision": "confirm"})[0], 200)
                self.assertEqual(request("POST", f"/api/context/{by_type['action']['id']}/decision", {"project": project, "decision": "confirm"})[0], 200)
                self.assertEqual(request("POST", f"/api/context/{by_type['risk']['id']}/decision", {"project": project, "decision": "reject"})[0], 200)
                status, summary = request("GET", f"/api/context/summary?project={project}&type=action&actor=true&deadline=true")
                self.assertEqual(status, 200)
                self.assertEqual(len(summary["action"]), 1)
                self.assertFalse(summary["risk"])

                second_review = upload(second_text)["review"]
                second_sanitized = str(second_review["artifact_id"])
                self.assertEqual(request("POST", f"/api/reviews/{second_sanitized}/confirm", {"project": project})[0], 202)
                status, second_context = request("POST", f"/api/context/{second_sanitized}/compile", {"project": project})
                self.assertEqual(status, 202)
                second_items = {item["item_type"]: item for item in second_context["candidates"]}
                self.assertEqual(len(second_items["action"]["source_links"]), 2)
                conflict = second_items["decision"]
                self.assertEqual(conflict["status"], "conflicted")
                self.assertEqual(request("POST", f"/api/context/{conflict['id']}/conflict", {"project": project, "resolution": "keep_both"})[0], 200)
                self.assertEqual(len(request("GET", f"/api/context/summary?project={project}&type=decision&conflict=true")[1]["decision"]), 2)

                for path in (f"/api/materials/{source_id}?project={other}", f"/api/protection/{new_sanitized}?project={other}", f"/api/reviews/{new_sanitized}?project={other}", f"/api/context/{edited['id']}?project={other}"):
                    self.assertEqual(request("GET", path)[0], 404)
                self.assertEqual(request("GET", f"/api/materials?project={other}")[1]["materials"], [])
                self.assertEqual(request("POST", f"/api/context/{new_sanitized}/compile", {"project": other})[0], 400)

                server.shutdown(); server.server_close(); server = start_server()
                self.assertEqual(request("GET", f"/api/materials/{source_id}/lineage?project={project}")[0], 200)
                self.assertEqual(request("GET", f"/api/reviews/{new_sanitized}?project={project}")[0], 200)
                status, restarted_summary = request("GET", f"/api/context/summary?project={project}")
                self.assertEqual(status, 200)
                self.assertEqual(len(restarted_summary["decision"]), 2)
                safe_files = [path for path in store.root.rglob("*") if path.is_file() and not any(zone in path.parts for zone in ("sources", "artifacts", "sanitized", "pseudonyms"))]
                safe_text = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in safe_files)
                for marker in raw_markers:
                    self.assertNotIn(marker, safe_text)
            finally:
                if server:
                    server.shutdown(); server.server_close()
                temporary.cleanup()
