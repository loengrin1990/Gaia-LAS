from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import DOCUMENT_EXTENSIONS, SETTINGS
from .excel_preview import preview_xlsx
from .orchestrator import create_package
from .projects import existing_project_dir


EXCLUDED_DIRS = {"Память_Graph", ".git", "__pycache__", "Транскрипции"}
EXCLUDED_SUFFIXES = {".tmp", ".bak"}
MAX_INBOX_ITEMS = 200
HIDDEN_STATUSES = {"ignored", "indexed"}


@dataclass
class ScribeInboxItem:
    id: str
    project: str
    relative_path: str
    name: str
    suffix: str
    size: int
    modified_at: str
    status: str
    kind: str
    preview: dict[str, Any] | None = None


def list_scribe_inbox(project: str, include_preview: bool = False) -> list[ScribeInboxItem]:
    project_dir = existing_project_dir(project)
    state = read_state(project)
    indexed_haystack = indexed_source_haystack(project_dir)
    items: list[ScribeInboxItem] = []
    for path in sorted(project_dir.rglob("*"), key=lambda item: item.stat().st_mtime, reverse=True):
        if len(items) >= MAX_INBOX_ITEMS:
            break
        if not is_candidate(path, project_dir):
            continue
        relative = path.relative_to(project_dir).as_posix()
        item_id = item_identifier(project, relative)
        status = str(state.get(item_id, {}).get("status") or "new")
        if status in HIDDEN_STATUSES or source_is_indexed(path, project_dir, indexed_haystack):
            continue
        stat = path.stat()
        preview = None
        if include_preview and path.suffix.lower() == ".xlsx":
            preview = preview_xlsx(path).to_dict()
        items.append(ScribeInboxItem(
            id=item_id,
            project=project,
            relative_path=relative,
            name=path.name,
            suffix=path.suffix.lower(),
            size=stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            status=status,
            kind=file_kind(path),
            preview=preview,
        ))
    return items


def preview_inbox_item(project: str, relative_path: str) -> dict[str, Any]:
    path = resolve_project_file(project, relative_path)
    if path.suffix.lower() == ".xlsx":
        return {
            "item": asdict(item_from_path(project, path)),
            "excel": preview_xlsx(path).to_dict(),
        }
    text = path.read_text(encoding="utf-8", errors="ignore") if path.suffix.lower() in {".txt", ".md"} else ""
    return {
        "item": asdict(item_from_path(project, path)),
        "preview_text": text[:8000],
    }


def package_inbox_item(
    project: str,
    relative_path: str,
    profile_id: str | None = None,
    instruction: str = "",
) -> dict[str, Any]:
    path = resolve_project_file(project, relative_path)
    query = instruction.strip() or default_instruction(path)
    package = create_package(project, query, [(path.name, path.read_bytes())], profile_id)
    package_payload = asdict(package)
    package_payload["scribe_origin"] = {
        "type": "inbox",
        "relative_path": relative_path,
        "name": path.name,
        "kind": file_kind(path),
    }
    mark_item(project, relative_path, "prepared")
    return {
        "item": asdict(item_from_path(project, path)),
        "package": package_payload,
    }


def ignore_inbox_item(project: str, relative_path: str) -> ScribeInboxItem:
    path = resolve_project_file(project, relative_path)
    mark_item(project, relative_path, "ignored")
    return item_from_path(project, path)


def index_inbox_item(project: str, relative_path: str) -> None:
    path = resolve_project_file(project, relative_path)
    mark_item(project, relative_path, "indexed")


def default_instruction(path: Path) -> str:
    if path.suffix.lower() == ".xlsx":
        return (
            "Проанализируй структурно нормализованный Excel как источник для памяти. "
            "Выдели только устойчивые решения, правила, статусы, риски, открытые вопросы "
            "и полезные source-summary; не переноси сырые строки таблицы целиком."
        )
    return (
        "Проанализируй файл как источник для обновления проектной памяти. "
        "Выдели устойчивые факты, решения, правила, риски и открытые вопросы."
    )


def is_candidate(path: Path, project_dir: Path) -> bool:
    if not path.is_file():
        return False
    if path.name.startswith(".") or path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    if path.suffix.lower() not in DOCUMENT_EXTENSIONS:
        return False
    relative_parts = path.relative_to(project_dir).parts
    if any(part in EXCLUDED_DIRS for part in relative_parts):
        return False
    if path.name == ".gaia-project.json":
        return False
    lower_name = path.name.lower()
    excluded_markers = (
        "память.md",
        "источники.md",
        "журнал памяти.md",
        "индекс памяти.md",
        "автоизвлеченный текст.md",
    )
    return not any(lower_name.endswith(marker) for marker in excluded_markers)


def item_from_path(project: str, path: Path) -> ScribeInboxItem:
    project_dir = existing_project_dir(project)
    relative = path.relative_to(project_dir).as_posix()
    status = str(read_state(project).get(item_identifier(project, relative), {}).get("status") or "new")
    stat = path.stat()
    return ScribeInboxItem(
        id=item_identifier(project, relative),
        project=project,
        relative_path=relative,
        name=path.name,
        suffix=path.suffix.lower(),
        size=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        status=status,
        kind=file_kind(path),
    )


def indexed_source_haystack(project_dir: Path) -> str:
    candidates = []
    for path in [
        *project_dir.glob("* - Источники.md"),
        *project_dir.glob("* - Журнал памяти.md"),
    ]:
        if path.is_file():
            candidates.append(path)
    graph_root = project_dir / "Память_Graph"
    if graph_root.exists():
        candidates.extend(
            path for path in graph_root.rglob("*.md")
            if path.is_file() and "90_Archive" not in path.relative_to(graph_root).parts
        )
    parts = []
    for path in candidates:
        try:
            parts.append(path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
    return "\n".join(parts)


def source_is_indexed(path: Path, project_dir: Path, haystack: str) -> bool:
    if not haystack:
        return False
    relative = path.relative_to(project_dir).as_posix()
    return relative in haystack or str(path) in haystack or path.name in haystack


def file_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        return "excel"
    if suffix in {".txt", ".md"}:
        return "text"
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".docx":
        return "docx"
    return suffix.lstrip(".") or "file"


def resolve_project_file(project: str, relative_path: str) -> Path:
    project_dir = existing_project_dir(project).resolve()
    path = (project_dir / relative_path).resolve()
    if project_dir not in path.parents and path != project_dir:
        raise ValueError("Файл должен находиться внутри проекта.")
    if not is_candidate(path, project_dir):
        raise ValueError("Файл не подходит для Scribe Inbox.")
    return path


def item_identifier(project: str, relative_path: str) -> str:
    return hashlib.sha1(f"{project}\n{relative_path}".encode("utf-8")).hexdigest()[:16]


def mark_item(project: str, relative_path: str, status: str) -> None:
    state = read_state(project)
    item_id = item_identifier(project, relative_path)
    state[item_id] = {
        "relative_path": relative_path,
        "status": status,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    write_state(project, state)


def state_path(project: str) -> Path:
    root = SETTINGS.service_docs / "Scribe Inbox State"
    root.mkdir(parents=True, exist_ok=True)
    safe = "".join(char if char.isalnum() or char in "-_" else "-" for char in project).strip("-") or "project"
    return root / f"{safe}.json"


def read_state(project: str) -> dict[str, Any]:
    path = state_path(project)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def write_state(project: str, state: dict[str, Any]) -> None:
    state_path(project).write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
