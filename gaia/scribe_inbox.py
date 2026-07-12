from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import DOCUMENT_EXTENSIONS, SETTINGS
from .excel_preview import preview_xlsx
from .extraction import extract_text
from .orchestrator import create_package
from .projects import existing_project_dir, project_record
from .storage import atomic_write_text, path_lock
from .scribe import content_based_title, source_name_is_low_signal


EXCLUDED_DIRS = {"Память_Graph", ".git", "__pycache__", "Транскрипции"}
EXCLUDED_SUFFIXES = {".tmp", ".bak"}
MAX_INBOX_ITEMS = 200
HIDDEN_STATUSES = {"ignored", "indexed", "duplicate"}


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


def duplicate_inbox_item(project: str, relative_path: str) -> None:
    path = resolve_project_file(project, relative_path)
    mark_item(project, relative_path, "duplicate")


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
    source_registries = [*project_dir.glob("* - Источники.md")]
    journal_files = [*project_dir.glob("* - Журнал памяти.md")]
    for path in journal_files:
        if path.is_file():
            candidates.append(path)
    graph_root = project_dir / "Память_Graph"
    if graph_root.exists():
        candidates.extend(
            path for path in graph_root.rglob("*.md")
            if path.is_file()
            and "90_Archive" not in path.relative_to(graph_root).parts
            and not is_source_map_node(path)
        )
    parts = []
    for path in source_registries:
        if path.is_file():
            try:
                parts.append(indexed_source_registry_text(path))
            except Exception:
                continue
    for path in candidates:
        try:
            parts.append(path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
    return "\n".join(parts)


def indexed_source_registry_text(path: Path) -> str:
    lines = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.startswith("| `"):
            lines.append(line)
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 6:
            lines.append(line)
            continue
        mode = cells[2].lower()
        masking = cells[3].lower()
        memory_status = cells[4].lower()
        comment = cells[5].lower()
        if source_registry_row_is_indexed(mode, masking, memory_status, comment):
            lines.append(line)
    return "\n".join(lines)


def source_registry_row_is_indexed(mode: str, masking: str, memory_status: str, comment: str) -> bool:
    if mode == "только источник":
        return True
    if memory_status and memory_status != "не упомянут явно":
        return True
    if masking == "выполнено":
        return True
    indexed_markers = (
        "учтен в [[",
        "учтён в [[",
        "покрыт в памяти",
        "упомянут в памяти",
    )
    return any(marker in comment for marker in indexed_markers)


def is_source_map_node(path: Path) -> bool:
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:300]
    except Exception:
        return False
    return "type: source_map" in head


def source_is_indexed(path: Path, project_dir: Path, haystack: str) -> bool:
    if not haystack:
        return False
    relative = path.relative_to(project_dir).as_posix()
    if any(needle in haystack for needle in source_index_needles(path, project_dir, relative)):
        return True
    return low_signal_source_is_covered(path, project_dir, haystack)


def source_index_needles(path: Path, project_dir: Path, relative: str) -> tuple[str, ...]:
    needles = {relative, str(path), path.name}
    try:
        code = project_record(project_dir).code
    except Exception:
        code = ""
    code_prefixes = tuple(prefix for prefix in (f"{code} - ", f"{code}_") if code)
    for folder in ("Исходники", "Транскрипции"):
        prefix = f"{folder}/"
        if not relative.startswith(prefix):
            continue
        name = relative[len(prefix):]
        for code_prefix in code_prefixes:
            if name.startswith(code_prefix):
                needles.add(prefix + name[len(code_prefix):])
    try:
        absolute_suffix = str(path.relative_to(project_dir))
    except ValueError:
        absolute_suffix = ""
    if absolute_suffix:
        needles.add(absolute_suffix)
    return tuple(needle for needle in needles if needle)


def low_signal_source_is_covered(path: Path, project_dir: Path, haystack: str) -> bool:
    if not source_name_is_low_signal(path.name):
        return False
    try:
        if path.suffix.lower() in {".txt", ".md"}:
            text = path.read_text(encoding="utf-8", errors="ignore")
        else:
            extracted = extract_text(path)
            if extracted is None:
                return False
            text, _note = extracted
    except Exception:
        return False
    title = content_based_title(text)
    if not title or title == "Контекст источника":
        return False
    code = project_record(project_dir).code
    needles = (
        f"[[{code} - {title}]]",
        f"# {code} - {title}",
        f"{title}: source-summary",
    )
    return any(needle in haystack for needle in needles)


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
    path = state_path(project)
    with path_lock(path):
        try:
            payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            state = payload if isinstance(payload, dict) else {}
        except Exception:
            state = {}
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
    atomic_write_text(state_path(project), json.dumps(state, ensure_ascii=False, indent=2) + "\n")
