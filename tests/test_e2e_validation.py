from __future__ import annotations

import http.client
import json
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gaia.controlled_intake import ControlledIntake
from gaia.provenance import ProvenanceStore
from gaia.review import LocalReviewError, ReviewService
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
            f"{organization} решила использовать локальный маршрут.\n\n"
            "ТРЕБОВАНИЯ\n\nПроверять материал.\n\nРЕШЕНИЯ\n\nИспользовать локальный маршрут.\n\nРИСКИ\n\nЕсть риск задержки.\n\nОТКРЫТЫЕ ВОПРОСЫ\n\nСрок не указан.\n\nДЕЙСТВИЯ\n\nОтветственный [Координатор-Север] должен проверить материал до 10 сентября 2026 года."
        )
        second_text = "РЕШЕНИЯ\n\nИспользовать иной локальный маршрут.\n\nДЕЙСТВИЯ\n\nОтветственный [Координатор-Север] должен проверить материал до 10 сентября 2026 года."
        server: ThreadingHTTPServer | None = None
        review_calls = 0

        def review_model(text: str) -> dict[str, object]:
            nonlocal review_calls
            review_calls += 1
            if review_calls == 1 and organization in text:
                organization_start = text.index(organization)
                verb_start = text.index("решила")
                return {"status": "completed", "findings": [
                    {"category": "Организация", "start": organization_start, "end": organization_start + len(organization), "confidence": "high", "reason_code": "residual", "requires_review": True},
                    {"category": "Другое", "start": verb_start, "end": verb_start + len("решила"), "confidence": "low", "reason_code": "false_positive", "requires_review": True},
                ]}
            return {"status": "completed", "findings": []}

        def context_model(text: str, **_: object) -> dict[str, object]:
            value = text.strip()
            if "решила" in value:
                return {"candidates": []}
            title = {"Проверять материал.": "Проверка материала", "Есть риск задержки.": "Задержка", "Срок не указан.": "Срок"}.get(value, "Маршрут" if "маршрут" in value else "Проверка")
            return {"candidates": [{"type": "action", "title": title, "statement": value, "evidence_id": "E1", "confidence": "high", "requires_review": True}]}

        with patch("gaia.controlled_intake.default_store", return_value=store), patch("gaia.controlled_intake.ReviewService", side_effect=lambda current_store, workspace: ReviewService(current_store, workspace, review_model)), patch("gaia.context_compiler.local_context_model", side_effect=context_model), patch("gaia.server.submit_analyze_job", return_value=SimpleNamespace(id="job_e2e")):
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

            def compiled_candidates(artifact_id: str) -> list[dict[str, object]]:
                status, started = request("POST", f"/api/context/{artifact_id}/compile", {"project": project})
                self.assertEqual(status, 202)
                for _ in range(50):
                    _, job = request("GET", str(started["status_url"]))
                    if job["status"] in {"done", "failed", "cancelled"}:
                        self.assertEqual(job["status"], "done")
                        return list(job["result"]["candidates"])
                    time.sleep(0.02)
                self.fail("Контекстная job не завершилась в тесте")

            try:
                server = start_server()
                initial = upload(first_text)
                review = initial["review"]
                assert isinstance(review, dict)
                old_sanitized = str(review["artifact_id"])
                status, materials = request("GET", f"/api/materials?project={project}")
                self.assertEqual(status, 200)
                self.assertEqual(len(materials["materials"]), 1)
                self.assertEqual(materials["materials"][0]["review_state"], "not_started")
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
                status, review = request("POST", f"/api/reviews/{old_sanitized}/check", {"project": project})
                self.assertEqual(status, 200)
                self.assertEqual(review["state"], "requires_review")
                self.assertEqual(request("POST", f"/api/reviews/{old_sanitized}/check", {"project": project})[0], 200)
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
                status, new_review = request("POST", f"/api/reviews/{new_sanitized}/check", {"project": project})
                self.assertEqual(status, 200)
                self.assertEqual(new_review["state"], "ready_for_confirmation")
                status, listed_after_replace = request("GET", f"/api/materials?project={project}")
                self.assertEqual(status, 200)
                self.assertEqual(listed_after_replace["materials"][0]["sanitized_id"], new_sanitized)
                self.assertEqual(request("POST", f"/api/reviews/{old_sanitized}/confirm", {"project": project})[0], 400)
                status, confirmed = request("POST", f"/api/reviews/{new_sanitized}/confirm", {"project": project})
                self.assertEqual(status, 202)
                self.assertEqual(confirmed["artifact_id"], new_sanitized)
                candidates = compiled_candidates(new_sanitized)
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
                self.assertEqual(request("POST", f"/api/reviews/{second_sanitized}/check", {"project": project})[0], 200)
                self.assertEqual(request("POST", f"/api/reviews/{second_sanitized}/confirm", {"project": project})[0], 202)
                second_items = {item["item_type"]: item for item in compiled_candidates(second_sanitized)}
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

    def test_manual_confirmation_allows_existing_context_compiler_route(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        store = ProvenanceStore(Path(temporary.name) / "storage")
        project = "synthetic-manual"
        server: ThreadingHTTPServer | None = None

        def context_model(text: str, **_: object) -> dict[str, object]:
            return {"candidates": [
                {"type": "requirement", "title": "Проверка", "statement": "Проверять материал вручную.", "evidence_id": "E1", "confidence": "high", "requires_review": True},
            ]}

        def indeterminate(_: str) -> dict[str, object]:
            raise LocalReviewError("local_model_timeout", "synthetic", {"trace_id": "gaia-test", "stage": "local_review", "provider": "synthetic", "response_chars": 0})

        with patch("gaia.controlled_intake.default_store", return_value=store), patch("gaia.review.local_model_review", side_effect=indeterminate), patch("gaia.context_compiler.local_context_model", side_effect=context_model), patch("gaia.server.submit_analyze_job", return_value=SimpleNamespace(id="job_manual")):
            def request(method: str, path: str, payload: dict[str, object] | bytes | None = None, content_type: str = "application/json") -> tuple[int, dict[str, object]]:
                assert server is not None
                body = payload if isinstance(payload, bytes) else (json.dumps(payload).encode("utf-8") if payload is not None else None)
                port = server.server_address[1]
                headers = {"Host": f"127.0.0.1:{port}", "Origin": f"http://127.0.0.1:{port}", "Cookie": f"{SESSION_COOKIE_NAME}={SESSION_TOKEN}", "Content-Type": content_type}
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
                connection.request(method, path, body, headers)
                response = connection.getresponse()
                data = json.loads(response.read().decode("utf-8"))
                connection.close()
                return response.status, data

            def compiled_candidates(artifact_id: str) -> list[dict[str, object]]:
                status, started = request("POST", f"/api/context/{artifact_id}/compile", {"project": project})
                self.assertEqual(status, 202)
                for _ in range(50):
                    _, job = request("GET", str(started["status_url"]))
                    if job["status"] in {"done", "failed", "cancelled"}:
                        self.assertEqual(job["status"], "done")
                        return list(job["result"]["candidates"])
                    time.sleep(0.02)
                self.fail("Контекстная job не завершилась в тесте")

            try:
                server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                threading.Thread(target=server.serve_forever, daemon=True).start()
                boundary = "----manual-boundary"
                body = (
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"project\"\r\n\r\n{project}\r\n"
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"manual.txt\"\r\nContent-Type: text/plain\r\n\r\nТРЕБОВАНИЯ\n\nПроверять материал вручную.\r\n"
                    f"--{boundary}--\r\n"
                ).encode("utf-8")
                status, accepted = request("POST", "/api/analyze", body, f"multipart/form-data; boundary={boundary}")
                self.assertEqual(status, 202)
                artifact_id = str(accepted["review"]["artifact_id"])
                self.assertEqual(request("POST", f"/api/reviews/{artifact_id}/check", {"project": project})[1]["state"], "manual_review_required")
                self.assertEqual(request("POST", f"/api/context/{artifact_id}/compile", {"project": project})[0], 400)
                self.assertEqual(request("POST", f"/api/reviews/{artifact_id}/confirm", {"project": project})[0], 202)
                repeated_status, repeated = request("POST", f"/api/reviews/{artifact_id}/confirm", {"project": project})
                self.assertEqual(repeated_status, 200)
                self.assertEqual(repeated["confirmation_method"], "manual")
                status, review = request("GET", f"/api/reviews/{artifact_id}?project={project}")
                self.assertEqual(status, 200)
                self.assertTrue(review["confirmed"])
                self.assertEqual(review["confirmation_method"], "manual")
                self.assertEqual(len(compiled_candidates(artifact_id)), 1)
                self.assertEqual(len(compiled_candidates(artifact_id)), 1)
            finally:
                if server:
                    server.shutdown(); server.server_close()
                temporary.cleanup()
