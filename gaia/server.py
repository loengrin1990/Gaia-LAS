from __future__ import annotations

import cgi
import json
from dataclasses import asdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .config import SETTINGS, SUPPORTED_EXTENSIONS, ConfigError, ensure_dirs
from .archive import apply_retention
from .conversations import (
    ConversationError,
    add_user_turn,
    archive_conversation,
    create_conversation,
    get_conversation,
    list_conversations,
)
from .jobs import get_job, job_to_dict, submit_analyze_job
from .launchers import launch_module
from .local_llm import check_lm_studio, run_lm_studio
from .profiles import profile_payloads
from .projects import (
    ProjectRegistryError,
    create_group,
    create_project,
    list_groups,
    list_projects,
    project_names,
    repair_project,
    update_group,
    update_project,
    validate_project,
)
from .rebuild import rebuild_prompt
from .scribe import apply_scribe_plan, create_scribe_draft, create_scribe_plan
from .scribe_inbox import ignore_inbox_item, index_inbox_item, list_scribe_inbox, package_inbox_item, preview_inbox_item
from .ui import INDEX_HTML


def json_response(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store, max-age=0")
    handler.send_header("Pragma", "no-cache")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def text_response(handler: BaseHTTPRequestHandler, body: str, content_type: str = "text/html; charset=utf-8") -> None:
    data = body.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Cache-Control", "no-store, max-age=0")
    handler.send_header("Pragma", "no-cache")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/" or self.path.startswith("/?"):
            text_response(self, INDEX_HTML)
            return
        if self.path == "/api/projects":
            json_response(self, {
                "projects": project_names(),
                "project_records": [asdict(project) for project in list_projects()],
                "groups": [asdict(group) for group in list_groups()],
            })
            return
        if self.path == "/api/groups":
            json_response(self, {"groups": [asdict(group) for group in list_groups()]})
            return
        if self.path == "/api/profiles":
            json_response(self, {"profiles": profile_payloads()})
            return
        if self.path.startswith("/api/conversations"):
            self.handle_get_conversations()
            return
        if self.path.startswith("/api/scribe-inbox"):
            self.handle_get_scribe_inbox()
            return
        if self.path == "/api/local-status":
            json_response(self, check_lm_studio())
            return
        if self.path.startswith("/api/jobs/"):
            job_id = self.path.rsplit("/", 1)[-1]
            job = get_job(job_id)
            if job is None:
                json_response(self, {"error": "job not found"}, 404)
                return
            json_response(self, job_to_dict(job))
            return
        json_response(self, {"error": "not found"}, 404)

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        if route == "/api/analyze":
            self.handle_analyze()
            return
        if route == "/api/conversations":
            self.handle_create_conversation()
            return
        if route.startswith("/api/conversations/"):
            self.handle_conversation_action()
            return
        if route.startswith("/api/scribe-inbox/"):
            self.handle_scribe_inbox_action()
            return
        if route == "/api/projects":
            self.handle_create_project()
            return
        if route == "/api/groups":
            self.handle_create_group()
            return
        if route.startswith("/api/projects/"):
            self.handle_project_action()
            return
        if route.startswith("/api/groups/"):
            self.handle_group_action()
            return
        if route == "/api/local-answer":
            payload = self.read_json()
            json_response(self, run_lm_studio(str(payload.get("prompt", ""))))
            return
        if route == "/api/scribe-draft":
            self.handle_scribe_draft()
            return
        if route == "/api/scribe-plan":
            self.handle_scribe_plan()
            return
        if route == "/api/scribe-apply":
            self.handle_scribe_apply()
            return
        if route == "/api/rebuild-prompt":
            self.handle_rebuild_prompt()
            return
        if route == "/api/launch":
            payload = self.read_json()
            response = launch_module(str(payload.get("module", "")))
            json_response(self, response, 200 if response.get("ok") else 400)
            return
        json_response(self, {"error": "not found"}, 404)

    def do_PATCH(self) -> None:
        route = urlparse(self.path).path
        if route.startswith("/api/projects/"):
            self.handle_project_action()
            return
        if route.startswith("/api/groups/"):
            self.handle_group_action()
            return
        json_response(self, {"error": "not found"}, 404)

    def handle_create_project(self) -> None:
        payload = self.read_json()
        try:
            project = create_project(
                str(payload.get("code", "")),
                str(payload.get("title", "")),
                str(payload.get("group_code", "")),
                str(payload.get("description", "")),
            )
        except ProjectRegistryError as exc:
            json_response(self, {"error": str(exc)}, 400)
            return
        json_response(self, asdict(project), 201)

    def handle_create_group(self) -> None:
        payload = self.read_json()
        try:
            group = create_group(
                str(payload.get("code", "")),
                str(payload.get("title", "")),
                str(payload.get("description", "")),
            )
        except ProjectRegistryError as exc:
            json_response(self, {"error": str(exc)}, 400)
            return
        json_response(self, asdict(group), 201)

    def handle_project_action(self) -> None:
        parts = urlparse(self.path).path.split("/")
        project = unquote(parts[3]) if len(parts) >= 4 else ""
        action = parts[4] if len(parts) >= 5 else ""
        payload = self.read_json()
        try:
            if action == "validate":
                json_response(self, validate_project(project))
                return
            if action == "repair":
                json_response(self, asdict(repair_project(project)))
                return
            if action == "archive":
                json_response(self, asdict(update_project(project, {"status": "archived"})))
                return
            if action == "":
                json_response(self, asdict(update_project(project, payload)))
                return
        except ProjectRegistryError as exc:
            json_response(self, {"error": str(exc)}, 400)
            return
        json_response(self, {"error": "not found"}, 404)

    def handle_group_action(self) -> None:
        parts = urlparse(self.path).path.split("/")
        group = unquote(parts[3]) if len(parts) >= 4 else ""
        action = parts[4] if len(parts) >= 5 else ""
        payload = self.read_json()
        try:
            if action == "archive":
                json_response(self, asdict(update_group(group, {"status": "archived"})))
                return
            if action == "":
                json_response(self, asdict(update_group(group, payload)))
                return
        except ProjectRegistryError as exc:
            json_response(self, {"error": str(exc)}, 400)
            return
        json_response(self, {"error": "not found"}, 404)

    def handle_analyze(self) -> None:
        form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ={
            "REQUEST_METHOD": "POST",
            "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
        })
        project = field_value(form, "project")
        profile = field_value(form, "profile")
        query = field_value(form, "query")
        uploaded: list[tuple[str, bytes]] = []
        file_fields = form["files"] if "files" in form else []
        if not isinstance(file_fields, list):
            file_fields = [file_fields]
        for item in file_fields:
            if not getattr(item, "filename", ""):
                continue
            content = item.file.read()
            suffix = Path(item.filename).suffix.lower()
            if suffix not in SUPPORTED_EXTENSIONS:
                json_response(self, {"error": f"Неподдерживаемый тип файла: {item.filename}"}, 400)
                return
            uploaded.append((item.filename, content))
        if not query.strip() and not uploaded:
            json_response(self, {"error": "Добавь запрос или файл для анализа."}, 400)
            return
        try:
            job = submit_analyze_job(project, query, uploaded, profile)
        except Exception as exc:
            json_response(self, {"error": str(exc)}, 500)
            return
        json_response(self, {
            "job_id": job.id,
            "status": job.status,
            "message": job.message,
            "progress": job.progress,
            "status_url": f"/api/jobs/{job.id}",
        }, 202)

    def handle_get_conversations(self) -> None:
        route = urlparse(self.path)
        if route.path == "/api/conversations":
            params = parse_qs(route.query)
            project = params.get("project", [""])[0]
            try:
                conversations = list_conversations(project)
            except ConversationError as exc:
                json_response(self, {"error": str(exc)}, 400)
                return
            json_response(self, {"conversations": [asdict(item) for item in conversations]})
            return
        conversation_id = route.path.rsplit("/", 1)[-1]
        try:
            conversation = get_conversation(conversation_id)
        except ConversationError as exc:
            json_response(self, {"error": str(exc)}, 404)
            return
        json_response(self, asdict(conversation))

    def handle_create_conversation(self) -> None:
        payload = self.read_json()
        try:
            conversation = create_conversation(
                str(payload.get("project") or ""),
                str(payload.get("title") or ""),
            )
        except ConversationError as exc:
            json_response(self, {"error": str(exc)}, 400)
            return
        json_response(self, asdict(conversation), 201)

    def handle_conversation_action(self) -> None:
        parts = urlparse(self.path).path.split("/")
        conversation_id = parts[3] if len(parts) >= 4 else ""
        action = parts[4] if len(parts) >= 5 else ""
        if action == "archive":
            try:
                conversation = archive_conversation(conversation_id)
            except ConversationError as exc:
                json_response(self, {"error": str(exc)}, 404)
                return
            json_response(self, asdict(conversation))
            return
        if action == "messages":
            self.handle_conversation_message(conversation_id)
            return
        json_response(self, {"error": "not found"}, 404)

    def handle_conversation_message(self, conversation_id: str) -> None:
        content_type = self.headers.get("Content-Type", "")
        uploaded: list[tuple[str, bytes]] = []
        profile = None
        run_local = False
        if content_type.startswith("multipart/form-data"):
            form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": content_type,
                "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
            })
            text = field_value(form, "text")
            profile = field_value(form, "profile") or None
            run_local = field_value(form, "run_local").lower() == "true"
            file_fields = form["files"] if "files" in form else []
            if not isinstance(file_fields, list):
                file_fields = [file_fields]
            for item in file_fields:
                if not getattr(item, "filename", ""):
                    continue
                content = item.file.read()
                suffix = Path(item.filename).suffix.lower()
                if suffix not in SUPPORTED_EXTENSIONS:
                    json_response(self, {"error": f"Неподдерживаемый тип файла: {item.filename}"}, 400)
                    return
                uploaded.append((item.filename, content))
        else:
            payload = self.read_json()
            text = str(payload.get("text") or "")
            profile = str(payload.get("profile") or "") or None
            run_local = bool(payload.get("run_local"))
        try:
            result = add_user_turn(conversation_id, text, uploaded, profile_id=profile, run_local=run_local)
        except ConversationError as exc:
            json_response(self, {"error": str(exc)}, 400)
            return
        except Exception as exc:
            json_response(self, {"error": str(exc)}, 500)
            return
        json_response(self, result, 201)

    def handle_scribe_draft(self) -> None:
        payload = self.read_json()
        package = self.package_from_payload_or_job(payload)
        if package is None:
            json_response(self, {"error": "job is not ready"}, 409)
            return
        try:
            draft = create_scribe_draft(package)
        except ValueError as exc:
            json_response(self, {"error": str(exc)}, 409)
            return
        except Exception as exc:
            json_response(self, {"error": str(exc)}, 500)
            return
        json_response(self, asdict(draft), 201)

    def handle_get_scribe_inbox(self) -> None:
        route = urlparse(self.path)
        params = parse_qs(route.query)
        project = params.get("project", [""])[0]
        relative_path = params.get("path", [""])[0]
        try:
            if route.path == "/api/scribe-inbox/preview":
                json_response(self, preview_inbox_item(project, relative_path))
                return
            items = list_scribe_inbox(project, include_preview=params.get("preview", ["false"])[0] == "true")
        except Exception as exc:
            json_response(self, {"error": str(exc)}, 400)
            return
        json_response(self, {"items": [asdict(item) for item in items]})

    def handle_scribe_inbox_action(self) -> None:
        action = urlparse(self.path).path.rsplit("/", 1)[-1]
        payload = self.read_json()
        project = str(payload.get("project") or "")
        relative_path = str(payload.get("path") or "")
        try:
            if action == "package":
                result = package_inbox_item(
                    project,
                    relative_path,
                    profile_id=str(payload.get("profile") or "") or None,
                    instruction=str(payload.get("instruction") or ""),
                )
                json_response(self, result, 201)
                return
            if action == "ignore":
                item = ignore_inbox_item(project, relative_path)
                json_response(self, asdict(item), 200)
                return
        except Exception as exc:
            json_response(self, {"error": str(exc)}, 400)
            return
        json_response(self, {"error": "not found"}, 404)

    def handle_scribe_plan(self) -> None:
        payload = self.read_json()
        package = self.package_from_payload_or_job(payload)
        if package is None:
            json_response(self, {"error": "job is not ready"}, 409)
            return
        try:
            plan = create_scribe_plan(package)
        except Exception as exc:
            json_response(self, {"error": str(exc)}, 500)
            return
        json_response(self, asdict(plan), 200 if plan.status != "blocked" else 409)

    def handle_scribe_apply(self) -> None:
        payload = self.read_json()
        job_id = str(payload.get("job_id", ""))
        selected_ids = payload.get("selected_item_ids", [])
        if not isinstance(selected_ids, list):
            json_response(self, {"error": "selected_item_ids must be a list"}, 400)
            return
        package = self.package_from_payload_or_job(payload)
        if package is None:
            json_response(self, {"error": "job is not ready"}, 409)
            return
        try:
            result = apply_scribe_plan(package, [str(item) for item in selected_ids])
            origin = package.get("scribe_origin") if isinstance(package, dict) else {}
            if isinstance(origin, dict) and origin.get("type") == "inbox" and origin.get("relative_path") and result.applied:
                index_inbox_item(str(package.get("project") or ""), str(origin.get("relative_path") or ""))
        except ValueError as exc:
            json_response(self, {"error": str(exc)}, 409)
            return
        except Exception as exc:
            json_response(self, {"error": str(exc)}, 500)
            return
        json_response(self, asdict(result), 200)

    def package_from_payload_or_job(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        package = payload.get("package")
        if isinstance(package, dict):
            return package
        job_id = str(payload.get("job_id", ""))
        job = get_job(job_id)
        if job is None or job.status != "done" or not job.result:
            return None
        return job.result

    def handle_rebuild_prompt(self) -> None:
        payload = self.read_json()
        job_id = str(payload.get("job_id", ""))
        job = get_job(job_id)
        if job is None:
            json_response(self, {"error": "job not found"}, 404)
            return
        if job.status != "done" or not job.result:
            json_response(self, {"error": "job is not ready"}, 409)
            return
        selected_ids = payload.get("selected_memory_source_ids", [])
        if not isinstance(selected_ids, list):
            json_response(self, {"error": "selected_memory_source_ids must be a list"}, 400)
            return
        try:
            rebuilt = rebuild_prompt(job.result, selected_ids)
        except ValueError as exc:
            json_response(self, {"error": str(exc)}, 400)
            return
        except Exception as exc:
            json_response(self, {"error": str(exc)}, 500)
            return
        json_response(self, rebuilt, 200)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def log_message(self, format: str, *args: Any) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {format % args}")


def field_value(form: cgi.FieldStorage, name: str) -> str:
    field = form[name] if name in form else None
    if field is None:
        return ""
    if isinstance(field, list):
        field = field[0]
    value = field.value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value)


def main() -> int:
    try:
        ensure_dirs()
    except ConfigError as exc:
        print(f"Gaia config error: {exc}")
        return 2
    if SETTINGS is None:
        print("Gaia config error: settings are unavailable.")
        return 2
    if SETTINGS.retention_cleanup_on_startup:
        report = apply_retention()
        print(
            "Gaia retention cleanup: "
            f"runs={len(report.removed_runs)} journals={len(report.removed_journals)} "
            f"audits={len(report.removed_audits)} skipped={len(report.skipped)}"
        )
    try:
        server = ThreadingHTTPServer((SETTINGS.host, SETTINGS.port), Handler)
    except OSError as exc:
        print(f"Gaia server error: cannot bind http://{SETTINGS.host}:{SETTINGS.port}: {exc}")
        return 2
    print(f"Gaia Local Analytics: http://{SETTINGS.host}:{SETTINGS.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    return 0
