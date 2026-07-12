from __future__ import annotations

import json
from http.cookies import SimpleCookie
from ipaddress import ip_address
import secrets
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from email import policy as email_policy
from email.message import Message
from email.parser import BytesHeaderParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .config import SETTINGS, SUPPORTED_EXTENSIONS, ConfigError, ensure_dirs
from .archive import apply_retention, retention_status
from .conversations import (
    ConversationError,
    add_user_turn,
    archive_conversation,
    create_conversation,
    get_conversation,
    list_conversations,
)
from .jobs import JobQueueFullError, cancel_job, get_job, job_to_dict, submit_analyze_job
from .launchers import launch_module
from .local_llm import check_local_llm, run_lm_studio
from .models import ApiError
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
from .scribe_inbox import (
    duplicate_inbox_item,
    ignore_inbox_item,
    index_inbox_item,
    list_scribe_inbox,
    package_inbox_item,
    preview_inbox_item,
)
from .ui import INDEX_HTML


MAX_JSON_BODY_SIZE = 1_000_000
MAX_MULTIPART_BODY_SIZE = 25_000_000
MAX_UPLOAD_FILE_SIZE = 20_000_000
MAX_UPLOAD_FILES = 8
MAX_ACTIVE_UPLOADS = 2
MULTIPART_READ_CHUNK_SIZE = 64 * 1024
UPLOAD_REQUEST_SLOTS = threading.BoundedSemaphore(MAX_ACTIVE_UPLOADS)
SESSION_COOKIE_NAME = "gaia_local_session"
SESSION_TOKEN = secrets.token_urlsafe(32)


@dataclass(frozen=True)
class MultipartField:
    name: str
    value: str
    content: bytes
    filename: str = ""


class UploadCapacityError(ValueError):
    pass


def json_response(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store, max-age=0")
    handler.send_header("Pragma", "no-cache")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def api_error_payload(code: str, message: str, details: dict[str, Any] | None = None, trace_id: str = "") -> dict[str, Any]:
    error = ApiError(
        code=code,
        message=message,
        details=details or {},
        trace_id=trace_id or f"gaia-{uuid.uuid4().hex[:12]}",
    )
    return {"error": asdict(error)}


def error_response(
    handler: BaseHTTPRequestHandler,
    code: str,
    message: str,
    status: int,
    details: dict[str, Any] | None = None,
) -> None:
    json_response(handler, api_error_payload(code, message, details), status)


def text_response(
    handler: BaseHTTPRequestHandler,
    body: str,
    content_type: str = "text/html; charset=utf-8",
    set_cookie: str = "",
) -> None:
    data = body.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Cache-Control", "no-store, max-age=0")
    handler.send_header("Pragma", "no-cache")
    if set_cookie:
        handler.send_header("Set-Cookie", set_cookie)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def session_cookie() -> str:
    return f"{SESSION_COOKIE_NAME}={SESSION_TOKEN}; HttpOnly; SameSite=Strict; Path=/"


def mutation_is_authorized(handler: BaseHTTPRequestHandler) -> bool:
    try:
        is_loopback = ip_address(str(handler.client_address[0])).is_loopback
    except (AttributeError, ValueError):
        return False
    if not is_loopback:
        return False
    host = handler.headers.get("Host", "")
    origin = handler.headers.get("Origin", "")
    if not host or origin != f"http://{host}":
        return False
    cookie = SimpleCookie()
    try:
        cookie.load(handler.headers.get("Cookie", ""))
    except (TypeError, ValueError):
        return False
    token = cookie.get(SESSION_COOKIE_NAME)
    return bool(token and secrets.compare_digest(token.value, SESSION_TOKEN))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/" or self.path.startswith("/?"):
            text_response(self, INDEX_HTML, set_cookie=session_cookie())
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
            json_response(self, check_local_llm())
            return
        if self.path == "/api/retention-status":
            json_response(self, retention_status())
            return
        if self.path.startswith("/api/jobs/"):
            job_id = self.path.rsplit("/", 1)[-1]
            job = get_job(job_id)
            if job is None:
                error_response(self, "job_not_found", "job not found", 404)
                return
            json_response(self, job_to_dict(job))
            return
        error_response(self, "not_found", "not found", 404)

    def do_POST(self) -> None:
        if not mutation_is_authorized(self):
            error_response(self, "mutation_not_authorized", "Открой Gaia в локальном браузере и повтори действие.", 403)
            return
        try:
            self.dispatch_POST()
        except ValueError as exc:
            error_response(self, "invalid_request", str(exc), 400)

    def dispatch_POST(self) -> None:
        route = urlparse(self.path).path
        if route == "/api/analyze":
            self.handle_analyze()
            return
        if route.startswith("/api/jobs/"):
            self.handle_job_action()
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
        error_response(self, "not_found", "not found", 404)

    def do_PATCH(self) -> None:
        if not mutation_is_authorized(self):
            error_response(self, "mutation_not_authorized", "Открой Gaia в локальном браузере и повтори действие.", 403)
            return
        try:
            self.dispatch_PATCH()
        except ValueError as exc:
            error_response(self, "invalid_request", str(exc), 400)

    def dispatch_PATCH(self) -> None:
        route = urlparse(self.path).path
        if route.startswith("/api/projects/"):
            self.handle_project_action()
            return
        if route.startswith("/api/groups/"):
            self.handle_group_action()
            return
        error_response(self, "not_found", "not found", 404)

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
            error_response(self, "project_registry_error", str(exc), 400)
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
            error_response(self, "project_registry_error", str(exc), 400)
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
            error_response(self, "project_registry_error", str(exc), 400)
            return
        error_response(self, "not_found", "not found", 404)

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
            error_response(self, "project_registry_error", str(exc), 400)
            return
        error_response(self, "not_found", "not found", 404)

    def handle_analyze(self) -> None:
        try:
            form = parse_multipart(self)
        except UploadCapacityError as exc:
            error_response(self, "upload_capacity_full", str(exc), 503)
            return
        except ValueError as exc:
            error_response(self, "invalid_multipart", str(exc), 400)
            return
        project = multipart_value(form, "project")
        profile = multipart_value(form, "profile")
        query = multipart_value(form, "query")
        uploaded: list[tuple[str, bytes]] = []
        for item in multipart_files(form, "files"):
            if not item.filename:
                continue
            suffix = Path(item.filename).suffix.lower()
            if suffix not in SUPPORTED_EXTENSIONS:
                error_response(
                    self,
                    "unsupported_file_type",
                    f"Неподдерживаемый тип файла: {item.filename}",
                    400,
                    {"filename": item.filename},
                )
                return
            if len(item.content) > MAX_UPLOAD_FILE_SIZE:
                error_response(self, "file_too_large", f"Файл превышает лимит {MAX_UPLOAD_FILE_SIZE} bytes.", 413)
                return
            uploaded.append((item.filename, item.content))
        if len(uploaded) > MAX_UPLOAD_FILES:
            error_response(self, "too_many_files", f"Разрешено не более {MAX_UPLOAD_FILES} файлов за запрос.", 413)
            return
        if not query.strip() and not uploaded:
            error_response(self, "empty_analyze_request", "Добавь запрос или файл для анализа.", 400)
            return
        try:
            job = submit_analyze_job(project, query, uploaded, profile)
        except JobQueueFullError as exc:
            error_response(self, "job_queue_full", str(exc), 429)
            return
        except Exception as exc:
            error_response(self, "analyze_failed", str(exc), 500)
            return
        json_response(self, {
            "job_id": job.id,
            "status": job.status,
            "message": job.message,
            "progress": job.progress,
            "status_url": f"/api/jobs/{job.id}",
        }, 202)

    def handle_job_action(self) -> None:
        parts = urlparse(self.path).path.split("/")
        job_id = parts[3] if len(parts) >= 4 else ""
        action = parts[4] if len(parts) >= 5 else ""
        if action != "cancel":
            error_response(self, "not_found", "not found", 404)
            return
        job = cancel_job(job_id)
        if job is None:
            error_response(self, "job_not_found", "job not found", 404)
            return
        json_response(self, job_to_dict(job))

    def handle_get_conversations(self) -> None:
        route = urlparse(self.path)
        if route.path == "/api/conversations":
            params = parse_qs(route.query)
            project = params.get("project", [""])[0]
            try:
                conversations = list_conversations(project)
            except ConversationError as exc:
                error_response(self, "conversation_error", str(exc), 400)
                return
            json_response(self, {"conversations": [asdict(item) for item in conversations]})
            return
        conversation_id = route.path.rsplit("/", 1)[-1]
        try:
            conversation = get_conversation(conversation_id)
        except ConversationError as exc:
            error_response(self, "conversation_not_found", str(exc), 404)
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
            error_response(self, "conversation_error", str(exc), 400)
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
                error_response(self, "conversation_not_found", str(exc), 404)
                return
            json_response(self, asdict(conversation))
            return
        if action == "messages":
            self.handle_conversation_message(conversation_id)
            return
        error_response(self, "not_found", "not found", 404)

    def handle_conversation_message(self, conversation_id: str) -> None:
        content_type = self.headers.get("Content-Type", "")
        uploaded: list[tuple[str, bytes]] = []
        profile = None
        run_local = False
        if content_type.startswith("multipart/form-data"):
            try:
                form = parse_multipart(self)
            except UploadCapacityError as exc:
                error_response(self, "upload_capacity_full", str(exc), 503)
                return
            except ValueError as exc:
                error_response(self, "invalid_multipart", str(exc), 400)
                return
            text = multipart_value(form, "text")
            profile = multipart_value(form, "profile") or None
            run_local = multipart_value(form, "run_local").lower() == "true"
            for item in multipart_files(form, "files"):
                if not item.filename:
                    continue
                suffix = Path(item.filename).suffix.lower()
                if suffix not in SUPPORTED_EXTENSIONS:
                    error_response(
                        self,
                        "unsupported_file_type",
                        f"Неподдерживаемый тип файла: {item.filename}",
                        400,
                        {"filename": item.filename},
                    )
                    return
                if len(item.content) > MAX_UPLOAD_FILE_SIZE:
                    error_response(self, "file_too_large", f"Файл превышает лимит {MAX_UPLOAD_FILE_SIZE} bytes.", 413)
                    return
                uploaded.append((item.filename, item.content))
            if len(uploaded) > MAX_UPLOAD_FILES:
                error_response(self, "too_many_files", f"Разрешено не более {MAX_UPLOAD_FILES} файлов за запрос.", 413)
                return
        else:
            payload = self.read_json()
            text = str(payload.get("text") or "")
            profile = str(payload.get("profile") or "") or None
            run_local = bool(payload.get("run_local"))
        try:
            result = add_user_turn(conversation_id, text, uploaded, profile_id=profile, run_local=run_local)
        except ConversationError as exc:
            error_response(self, "conversation_error", str(exc), 400)
            return
        except Exception as exc:
            error_response(self, "conversation_message_failed", str(exc), 500)
            return
        json_response(self, result, 201)

    def handle_scribe_draft(self) -> None:
        payload = self.read_json()
        package = self.package_from_payload_or_job(payload)
        if package is None:
            error_response(self, "job_not_ready", "job is not ready", 409)
            return
        try:
            draft = create_scribe_draft(package)
        except ValueError as exc:
            error_response(self, "scribe_blocked", str(exc), 409)
            return
        except Exception as exc:
            error_response(self, "scribe_draft_failed", str(exc), 500)
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
            error_response(self, "scribe_inbox_error", str(exc), 400)
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
            error_response(self, "scribe_inbox_error", str(exc), 400)
            return
        error_response(self, "not_found", "not found", 404)

    def handle_scribe_plan(self) -> None:
        payload = self.read_json()
        package = self.package_from_payload_or_job(payload)
        if package is None:
            error_response(self, "job_not_ready", "job is not ready", 409)
            return
        try:
            plan = create_scribe_plan(package)
        except Exception as exc:
            error_response(self, "scribe_plan_failed", str(exc), 500)
            return
        json_response(self, asdict(plan), 200 if plan.status != "blocked" else 409)

    def handle_scribe_apply(self) -> None:
        payload = self.read_json()
        job_id = str(payload.get("job_id", ""))
        selected_ids = payload.get("selected_item_ids", [])
        if not isinstance(selected_ids, list):
            error_response(self, "invalid_request", "selected_item_ids must be a list", 400)
            return
        selected_actions = payload.get("selected_item_actions", {})
        if not isinstance(selected_actions, dict):
            error_response(self, "invalid_request", "selected_item_actions must be an object", 400)
            return
        package = self.package_from_payload_or_job(payload)
        if package is None:
            error_response(self, "job_not_ready", "job is not ready", 409)
            return
        try:
            item_actions = {str(key): str(value) for key, value in selected_actions.items()}
            result = apply_scribe_plan(package, [str(item) for item in selected_ids], item_actions)
            origin = package.get("scribe_origin") if isinstance(package, dict) else {}
            duplicate_skip = any(action == "skip_duplicate" for action in item_actions.values())
            if isinstance(origin, dict) and origin.get("type") == "inbox" and origin.get("relative_path"):
                project = str(package.get("project") or "")
                relative_path = str(origin.get("relative_path") or "")
                if duplicate_skip:
                    duplicate_inbox_item(project, relative_path)
                elif result.applied:
                    index_inbox_item(project, relative_path)
        except ValueError as exc:
            error_response(self, "scribe_blocked", str(exc), 409)
            return
        except Exception as exc:
            error_response(self, "scribe_apply_failed", str(exc), 500)
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
            error_response(self, "job_not_found", "job not found", 404)
            return
        if job.status != "done" or not job.result:
            error_response(self, "job_not_ready", "job is not ready", 409)
            return
        selected_ids = payload.get("selected_memory_source_ids", [])
        if not isinstance(selected_ids, list):
            error_response(self, "invalid_request", "selected_memory_source_ids must be a list", 400)
            return
        try:
            rebuilt = rebuild_prompt(job.result, selected_ids)
        except ValueError as exc:
            error_response(self, "rebuild_error", str(exc), 400)
            return
        except Exception as exc:
            error_response(self, "rebuild_failed", str(exc), 500)
            return
        json_response(self, rebuilt, 200)

    def read_json(self) -> dict[str, Any]:
        length = content_length(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        if length > MAX_JSON_BODY_SIZE:
            raise ValueError(f"JSON body is too large; limit is {MAX_JSON_BODY_SIZE} bytes.")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def log_message(self, format: str, *args: Any) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {format % args}")


def content_length(value: str | None) -> int:
    try:
        length = int(value or "0")
    except ValueError:
        raise ValueError("Content-Length must be an integer.")
    if length < 0:
        raise ValueError("Content-Length must be non-negative.")
    return length


def parse_multipart(handler: BaseHTTPRequestHandler) -> list[MultipartField]:
    content_type = handler.headers.get("Content-Type", "")
    if not content_type.startswith("multipart/form-data"):
        raise ValueError("Content-Type must be multipart/form-data.")
    length = content_length(handler.headers.get("Content-Length", "0"))
    if length > MAX_MULTIPART_BODY_SIZE:
        raise ValueError(f"Multipart body is too large; limit is {MAX_MULTIPART_BODY_SIZE} bytes.")
    if not UPLOAD_REQUEST_SLOTS.acquire(blocking=False):
        raise UploadCapacityError("Сервис уже обрабатывает максимальное число загрузок. Повтори запрос позже.")
    try:
        return parse_multipart_stream(handler.rfile, content_type, length)
    finally:
        UPLOAD_REQUEST_SLOTS.release()


def parse_multipart_stream(stream: Any, content_type: str, length: int) -> list[MultipartField]:
    """Parse multipart incrementally, without retaining the whole HTTP body or MIME tree."""
    boundary = multipart_boundary(content_type)
    delimiter = b"--" + boundary
    marker = b"\r\n" + delimiter
    remaining = length
    pending = bytearray()

    def read_from_stream(size: int) -> bytes:
        nonlocal remaining
        if size < 0:
            raise ValueError("Multipart body is invalid.")
        chunks = []
        if pending:
            take = min(size, len(pending))
            chunks.append(bytes(pending[:take]))
            del pending[:take]
            size -= take
        if size:
            if size > remaining:
                raise ValueError("Multipart body ended unexpectedly.")
            chunk = stream.read(size)
            if len(chunk) != size:
                raise ValueError("Multipart body ended unexpectedly.")
            remaining -= size
            chunks.append(chunk)
        return b"".join(chunks)

    def read_line() -> bytes:
        line = bytearray()
        while True:
            if len(line) > 16_384:
                raise ValueError("Multipart headers are too large.")
            byte = read_from_stream(1)
            line.extend(byte)
            if line.endswith(b"\n"):
                return bytes(line)

    if read_line().rstrip(b"\r\n") != delimiter:
        raise ValueError("Multipart body is invalid.")

    fields: list[MultipartField] = []
    while True:
        raw_headers = bytearray()
        while True:
            line = read_line()
            raw_headers.extend(line)
            if line in {b"\r\n", b"\n"}:
                break
        headers = BytesHeaderParser(policy=email_policy.default).parsebytes(bytes(raw_headers))
        if headers.get_content_disposition() != "form-data":
            raise ValueError("Multipart part is not form-data.")
        name = headers.get_param("name", header="content-disposition")
        if not name:
            raise ValueError("Multipart field name is missing.")

        content = bytearray()
        buffered = bytearray()
        while True:
            if remaining <= 0 and not pending:
                raise ValueError("Multipart body ended unexpectedly.")
            read_size = min(MULTIPART_READ_CHUNK_SIZE, len(pending) or remaining)
            buffered.extend(read_from_stream(read_size))
            boundary_at = buffered.find(marker)
            if boundary_at >= 0:
                content.extend(buffered[:boundary_at])
                pending.extend(buffered[boundary_at + len(marker):])
                break
            safe_length = max(0, len(buffered) - len(marker) + 1)
            if safe_length:
                content.extend(buffered[:safe_length])
                del buffered[:safe_length]

        filename = headers.get_filename() or ""
        charset = headers.get_content_charset() or "utf-8"
        value = "" if filename else bytes(content).decode(charset, errors="ignore")
        fields.append(MultipartField(name=str(name), value=value, content=bytes(content), filename=filename))

        boundary_end = read_from_stream(2)
        if boundary_end == b"--":
            return fields
        if boundary_end != b"\r\n":
            raise ValueError("Multipart boundary is invalid.")


def multipart_boundary(content_type: str) -> bytes:
    headers = Message()
    headers["Content-Type"] = content_type
    boundary = headers.get_param("boundary", header="content-type")
    if not isinstance(boundary, str) or not boundary:
        raise ValueError("Multipart boundary is missing.")
    try:
        return boundary.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("Multipart boundary is invalid.") from exc


def multipart_value(fields: list[MultipartField], name: str) -> str:
    for field in fields:
        if field.name == name and not field.filename:
            return field.value
    return ""


def multipart_files(fields: list[MultipartField], name: str) -> list[MultipartField]:
    return [field for field in fields if field.name == name and field.filename]


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
