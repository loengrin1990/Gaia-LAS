from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import SETTINGS
from .models import ProjectGroup, ProjectRecord
from .module_assist import diagnose_project_health_with_local_llm


PROJECT_META = ".gaia-project.json"
GROUP_META = ".gaia-group.json"
PROJECT_CODE_RE = re.compile(r"^[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9_-]{1,16}$")
GROUP_CODE_RE = re.compile(r"^[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9_-]{1,16}$")
GRAPH_DIRS = (
    "00_Core",
    "10_Branches",
    "20_Decisions",
    "30_Open_Questions",
    "40_Risks",
    "50_Sources",
    "90_Archive",
)


class ProjectRegistryError(ValueError):
    pass


def groups_root() -> Path:
    return SETTINGS.vault / "Контексты" / "Группы"


def list_projects(include_archived: bool = False) -> list[ProjectRecord]:
    records = [project_record(path) for path in project_dirs()]
    if not include_archived:
        records = [record for record in records if record.status != "archived"]
    return sorted(records, key=lambda item: (item.group_title or "", item.title.lower()))


def project_names(include_archived: bool = False) -> list[str]:
    return [record.name for record in list_projects(include_archived=include_archived)]


def project_dirs() -> list[Path]:
    if not SETTINGS.projects.exists():
        return []
    return sorted(
        path for path in SETTINGS.projects.iterdir()
        if path.is_dir() and project_memory_path(path) is not None
    )


def list_groups(include_archived: bool = False) -> list[ProjectGroup]:
    root = groups_root()
    if not root.exists():
        return []
    groups = [group_record(path) for path in sorted(root.iterdir()) if path.is_dir()]
    if not include_archived:
        groups = [group for group in groups if group.status != "archived"]
    counts: dict[str, int] = {}
    for project in list_projects(include_archived=True):
        if project.group_code:
            counts[project.group_code] = counts.get(project.group_code, 0) + 1
    for group in groups:
        group.project_count = counts.get(group.code, 0)
    return sorted(groups, key=lambda item: item.title.lower())


def create_group(code: str, title: str, description: str = "") -> ProjectGroup:
    code = normalize_code(code, GROUP_CODE_RE, "group code")
    title = normalize_title(title)
    path = safe_group_dir(code)
    if path.exists():
        raise ProjectRegistryError(f"Группа `{code}` уже существует.")
    create_group_structure(path, code, title, description)
    return group_record(path)


def update_group(code: str, payload: dict[str, Any]) -> ProjectGroup:
    path = existing_group_dir(code)
    meta = group_meta(path)
    if "title" in payload:
        meta["title"] = normalize_title(str(payload["title"]))
    if "status" in payload:
        meta["status"] = normalize_status(str(payload["status"]))
    if "description" in payload:
        meta["description"] = str(payload["description"]).strip()
    meta["updated_at"] = now_iso()
    write_meta(path / GROUP_META, meta)
    sync_group_files(path, meta["code"], meta["title"], meta.get("description", ""))
    return group_record(path)


def create_project(code: str, title: str, group_code: str = "", description: str = "") -> ProjectRecord:
    code = normalize_code(code, PROJECT_CODE_RE, "project code")
    title = normalize_title(title)
    if group_code:
        existing_group_dir(group_code)
    path = safe_project_dir(title)
    if path.exists():
        raise ProjectRegistryError(f"Проект `{title}` уже существует.")
    create_project_structure(path, code, title, group_code, description)
    return project_record(path)


def update_project(project: str, payload: dict[str, Any]) -> ProjectRecord:
    path = existing_project_dir(project)
    meta = project_meta(path)
    old_code = meta["code"]
    old_title = meta.get("title") or path.name
    if "code" in payload:
        meta["code"] = normalize_code(str(payload["code"]), PROJECT_CODE_RE, "project code")
    if "title" in payload:
        meta["title"] = normalize_title(str(payload["title"]))
    if "status" in payload:
        meta["status"] = normalize_status(str(payload["status"]))
    if "group_code" in payload:
        group_code = str(payload["group_code"] or "").strip()
        if group_code:
            existing_group_dir(group_code)
        meta["group_code"] = group_code
    if "context_inheritance" in payload:
        meta["context_inheritance"] = bool(payload["context_inheritance"])
    if "description" in payload:
        meta["description"] = str(payload["description"]).strip()
    meta["updated_at"] = now_iso()
    new_path = path
    if meta["title"] != old_title and meta["title"] != path.name:
        new_path = safe_project_dir(meta["title"])
        if new_path.exists():
            raise ProjectRegistryError(f"Папка проекта `{meta['title']}` уже существует.")
    if meta["code"] != old_code:
        validate_project_code_change(path, old_code, meta["code"])
        rename_project_code_files(path, old_code, meta["code"])
        update_project_code_references(path, old_code, meta["code"])
    write_meta(path / PROJECT_META, meta)
    sync_project_files(path, meta["code"], meta["title"], meta.get("group_code", ""), meta.get("description", ""))
    if new_path != path:
        path.rename(new_path)
        path = new_path
    return project_record(path)


def validate_project(project: str) -> dict[str, Any]:
    path = existing_project_dir(project)
    record = project_record(path)
    summary = project_health_summary(path, record)
    diagnostics = project_health_diagnostics(summary)
    return {
        "project": asdict(record),
        "ok": record.health == "ok",
        "issues": record.issues or [],
        "health_summary": summary,
        "diagnostics": diagnostics,
    }


def repair_project(project: str) -> ProjectRecord:
    path = existing_project_dir(project)
    meta = project_meta(path)
    sync_project_files(path, meta["code"], meta["title"], meta.get("group_code", ""), meta.get("description", ""))
    return project_record(path)


def validate_project_code_change(path: Path, old_code: str, new_code: str) -> None:
    for item in prefixed_project_files(path, old_code):
        target = item.with_name(new_code + item.name[len(old_code):])
        if target.exists() and target != item:
            raise ProjectRegistryError(f"Нельзя сменить код: файл уже существует `{target.name}`.")


def rename_project_code_files(path: Path, old_code: str, new_code: str) -> None:
    for item in sorted(prefixed_project_files(path, old_code), key=lambda candidate: len(candidate.parts), reverse=True):
        target = item.with_name(new_code + item.name[len(old_code):])
        item.rename(target)


def prefixed_project_files(path: Path, code: str) -> list[Path]:
    prefix = f"{code} - "
    return sorted(item for item in path.rglob(f"{prefix}*") if item.is_file())


def update_project_code_references(path: Path, old_code: str, new_code: str) -> None:
    old_prefix = f"{old_code} - "
    new_prefix = f"{new_code} - "
    old_code_line = f"Код проекта: {old_code}"
    new_code_line = f"Код проекта: {new_code}"
    for item in sorted(path.rglob("*.md")):
        text = item.read_text(encoding="utf-8", errors="ignore")
        updated = text.replace(old_prefix, new_prefix).replace(old_code_line, new_code_line)
        if updated != text:
            item.write_text(updated, encoding="utf-8")


def project_record(path: Path) -> ProjectRecord:
    meta = project_meta(path)
    code = meta["code"]
    group_code = str(meta.get("group_code", "")).strip()
    group_title = ""
    if group_code:
        group_path = safe_group_dir(group_code)
        if group_path.exists():
            group_title = group_record(group_path).title
    memory_path = project_memory_path(path)
    sources_path = path / f"{code} - Источники.md"
    journal_path = path / f"{code} - Журнал памяти.md"
    graph_index_path = path / "Память_Graph" / f"{code} - Индекс памяти.md"
    issues = project_issues(path, meta)
    return ProjectRecord(
        name=path.name,
        code=code,
        title=meta.get("title") or path.name,
        status=meta.get("status", "active"),
        path=str(path),
        memory_path=str(memory_path or ""),
        sources_path=str(sources_path),
        journal_path=str(journal_path),
        graph_index_path=str(graph_index_path),
        group_code=group_code,
        group_title=group_title,
        context_inheritance=bool(meta.get("context_inheritance", True)),
        health="ok" if not issues else "needs_attention",
        issues=issues,
    )


def group_record(path: Path) -> ProjectGroup:
    meta = group_meta(path)
    code = meta["code"]
    return ProjectGroup(
        code=code,
        title=meta.get("title") or code,
        status=meta.get("status", "active"),
        path=str(path),
        context_path=str(path / f"{code} - Контекст.md"),
        sources_path=str(path / f"{code} - Источники.md"),
        journal_path=str(path / f"{code} - Журнал.md"),
        materials_path=str(path / "Материалы"),
    )


def group_context_files(group_code: str) -> list[Path]:
    if not group_code:
        return []
    path = safe_group_dir(group_code)
    if not path.exists():
        return []
    group = group_record(path)
    files = [Path(group.context_path)]
    graph_root = path / "Память_Graph"
    if graph_root.exists():
        files.extend(sorted(
            item for item in graph_root.rglob("*.md")
            if item.is_file() and "90_Archive" not in item.relative_to(graph_root).parts
        ))
    return [item for item in files if item.exists()]


def group_for_project(project: str) -> ProjectGroup | None:
    try:
        path = safe_project_dir(project)
    except ProjectRegistryError:
        return None
    if not path.exists():
        return None
    meta = project_meta(path)
    if not meta.get("context_inheritance", True):
        return None
    code = str(meta.get("group_code", "")).strip()
    if not code:
        return None
    group_path = safe_group_dir(code)
    if not group_path.exists():
        return None
    return group_record(group_path)


def project_memory_path(project_dir: Path) -> Path | None:
    prefixed = sorted(project_dir.glob("* - Память.md"))
    if prefixed:
        return prefixed[0]
    legacy = project_dir / "Память.md"
    if legacy.exists():
        return legacy
    return None


def project_meta(path: Path) -> dict[str, Any]:
    meta_path = path / PROJECT_META
    if meta_path.exists():
        meta = read_meta(meta_path)
    else:
        meta = {
            "code": infer_project_code(path),
            "title": path.name,
            "status": "active",
            "group_code": "",
            "context_inheritance": True,
            "created_at": "",
            "updated_at": "",
        }
    meta.setdefault("code", infer_project_code(path))
    meta.setdefault("title", path.name)
    meta.setdefault("status", "active")
    meta.setdefault("group_code", "")
    meta.setdefault("context_inheritance", True)
    return meta


def group_meta(path: Path) -> dict[str, Any]:
    meta_path = path / GROUP_META
    if meta_path.exists():
        meta = read_meta(meta_path)
    else:
        meta = {"code": path.name, "title": path.name, "status": "active", "created_at": "", "updated_at": ""}
    meta.setdefault("code", path.name)
    meta.setdefault("title", path.name)
    meta.setdefault("status", "active")
    return meta


def create_group_structure(path: Path, code: str, title: str, description: str) -> None:
    path.mkdir(parents=True)
    (path / "Материалы" / "Регламенты").mkdir(parents=True)
    (path / "Материалы" / "Шаблоны").mkdir(parents=True)
    for folder in GRAPH_DIRS:
        (path / "Память_Graph" / folder).mkdir(parents=True, exist_ok=True)
    meta = {
        "code": code,
        "title": title,
        "status": "active",
        "description": description,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    write_meta(path / GROUP_META, meta)
    sync_group_files(path, code, title, description)


def create_project_structure(path: Path, code: str, title: str, group_code: str, description: str) -> None:
    path.mkdir(parents=True)
    (path / "Исходники").mkdir(exist_ok=True)
    for folder in GRAPH_DIRS:
        (path / "Память_Graph" / folder).mkdir(parents=True, exist_ok=True)
    meta = {
        "code": code,
        "title": title,
        "status": "active",
        "group_code": group_code,
        "context_inheritance": True,
        "description": description,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    write_meta(path / PROJECT_META, meta)
    sync_project_files(path, code, title, group_code, description)


def sync_group_files(path: Path, code: str, title: str, description: str) -> None:
    ensure_text(path / f"{code} - Контекст.md", group_context_template(code, title, description))
    ensure_text(path / f"{code} - Источники.md", f"# {code} - Источники\n\n")
    ensure_text(path / f"{code} - Журнал.md", f"# {code} - Журнал\n\n")
    ensure_text(path / "Память_Graph" / f"{code} - Индекс памяти.md", group_index_template(code, title))


def sync_project_files(path: Path, code: str, title: str, group_code: str, description: str) -> None:
    ensure_text(path / f"{code} - Память.md", project_memory_template(code, title, group_code, description))
    ensure_text(path / f"{code} - Источники.md", f"# {code} - Источники\n\n")
    ensure_text(path / f"{code} - Журнал памяти.md", f"# {code} - Журнал памяти\n\n")
    ensure_text(path / "Память_Graph" / f"{code} - Индекс памяти.md", project_index_template(code, title, group_code))
    for folder in GRAPH_DIRS:
        (path / "Память_Graph" / folder).mkdir(parents=True, exist_ok=True)


def project_issues(path: Path, meta: dict[str, Any]) -> list[str]:
    code = meta["code"]
    checks = [
        path / f"{code} - Память.md",
        path / f"{code} - Источники.md",
        path / f"{code} - Журнал памяти.md",
        path / "Память_Graph" / f"{code} - Индекс памяти.md",
    ]
    issues = [f"missing: {item.name}" for item in checks if not item.exists()]
    for folder in GRAPH_DIRS:
        if not (path / "Память_Graph" / folder).exists():
            issues.append(f"missing graph folder: {folder}")
    group_code = str(meta.get("group_code", "")).strip()
    if group_code and not safe_group_dir(group_code).exists():
        issues.append(f"missing group: {group_code}")
    return issues


def project_health_summary(path: Path, record: ProjectRecord) -> dict[str, Any]:
    graph_root = path / "Память_Graph"
    graph_counts: dict[str, int] = {}
    for folder in GRAPH_DIRS:
        folder_path = graph_root / folder
        graph_counts[folder] = len(list(folder_path.glob("*.md"))) if folder_path.exists() else 0
    source_files = []
    for folder in ("Исходники", "Транскрипции"):
        root = path / folder
        if root.exists():
            source_files.extend(item for item in root.rglob("*") if item.is_file())
    sources_text = ""
    sources_path = Path(record.sources_path)
    if sources_path.exists():
        sources_text = sources_path.read_text(encoding="utf-8", errors="ignore")
    return {
        "project": record.title,
        "code": record.code,
        "status": record.status,
        "group_code": record.group_code,
        "context_inheritance": record.context_inheritance,
        "health": record.health,
        "issues": record.issues or [],
        "source_files_count": len(source_files),
        "source_rows_not_mentioned": sources_text.count("не упомянут явно"),
        "source_rows_context": sources_text.count("| контекст |"),
        "graph_counts": graph_counts,
        "has_memory": bool(record.memory_path and Path(record.memory_path).exists()),
        "has_sources_registry": sources_path.exists(),
        "has_journal": bool(record.journal_path and Path(record.journal_path).exists()),
    }


def project_health_diagnostics(summary: dict[str, Any]) -> list[dict[str, str]]:
    diagnostics = deterministic_project_diagnostics(summary)
    if getattr(SETTINGS, "project_health_llm", False):
        timeout = int(getattr(SETTINGS, "project_health_timeout_seconds", 5) or 5)
        diagnostics.extend(diagnose_project_health_with_local_llm(summary, timeout))
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in diagnostics:
        key = (item.get("severity", ""), item.get("title", ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def deterministic_project_diagnostics(summary: dict[str, Any]) -> list[dict[str, str]]:
    diagnostics: list[dict[str, str]] = []
    if summary.get("issues"):
        diagnostics.append({
            "severity": "critical",
            "title": "Структура проекта требует ремонта",
            "detail": "Project Registry нашел отсутствующие обязательные файлы или graph-папки.",
            "action": "Запусти repair проекта, затем повтори validate.",
        })
    graph_counts = summary.get("graph_counts") or {}
    if graph_counts.get("50_Sources", 0) <= 1 and summary.get("source_files_count", 0) > 0:
        diagnostics.append({
            "severity": "warning",
            "title": "Мало source-summary узлов",
            "detail": "В проекте есть исходники, но почти нет активных узлов 50_Sources для provenance.",
            "action": "Добавь краткие source-summary для durable источников через обновление памяти.",
        })
    if int(summary.get("source_rows_not_mentioned") or 0) > 0:
        diagnostics.append({
            "severity": "info",
            "title": "Есть источники без явного упоминания в памяти",
            "detail": "В Источники.md есть строки со статусом не упомянут явно.",
            "action": "Проверь, какие источники стоит поднять в активную память или оставить только в registry.",
        })
    return diagnostics


def safe_project_dir(project: str) -> Path:
    if not project or "/" in project or "\\" in project:
        raise ProjectRegistryError("Некорректное имя проекта.")
    root = SETTINGS.projects.resolve()
    path = (SETTINGS.projects / project).resolve()
    if not path.is_relative_to(root):
        raise ProjectRegistryError("Имя проекта выходит за пределы каталога проектов.")
    return path


def existing_project_dir(project: str) -> Path:
    path = safe_project_dir(project)
    if not path.exists() or not path.is_dir():
        raise ProjectRegistryError(f"Проект `{project}` не найден.")
    return path


def safe_group_dir(code: str) -> Path:
    if not code or "/" in code or "\\" in code:
        raise ProjectRegistryError("Некорректный код группы.")
    root = groups_root().resolve()
    path = (groups_root() / code).resolve()
    if not path.is_relative_to(root):
        raise ProjectRegistryError("Код группы выходит за пределы каталога групп.")
    return path


def existing_group_dir(code: str) -> Path:
    path = safe_group_dir(code)
    if not path.exists() or not path.is_dir():
        raise ProjectRegistryError(f"Группа `{code}` не найдена.")
    return path


def infer_project_code(path: Path) -> str:
    memory = project_memory_path(path)
    if memory and " - Память" in memory.name:
        return memory.name.split(" - Память", 1)[0]
    return "".join(part[0] for part in path.name.split() if part)[:6].upper() or path.name[:6].upper()


def normalize_code(value: str, pattern: re.Pattern[str], label: str) -> str:
    code = value.strip()
    if not pattern.match(code):
        raise ProjectRegistryError(f"Некорректный {label}: `{value}`.")
    return code


def normalize_title(value: str) -> str:
    title = value.strip()
    if not title or "/" in title or "\\" in title:
        raise ProjectRegistryError("Некорректное название.")
    return title


def normalize_status(value: str) -> str:
    if value not in {"active", "archived", "draft"}:
        raise ProjectRegistryError("Статус должен быть active, archived или draft.")
    return value


def read_meta(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProjectRegistryError(f"Некорректный JSON метаданных: {path}") from exc
    if not isinstance(payload, dict):
        raise ProjectRegistryError(f"Метаданные должны быть объектом: {path}")
    return payload


def write_meta(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ensure_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(text, encoding="utf-8")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def project_memory_template(code: str, title: str, group_code: str, description: str) -> str:
    group_line = f"\n- Группа: {group_code}" if group_code else ""
    description_line = f"\n\n## Описание\n{description}\n" if description else ""
    return f"# {title}\n\n## Быстрый вход\n- Код проекта: {code}{group_line}\n{description_line}\n"


def project_index_template(code: str, title: str, group_code: str) -> str:
    inherited = f"\n- Наследует контекст группы: {group_code}" if group_code else ""
    return f"# {code} - Индекс памяти\n\n- Проект: {title}{inherited}\n\n## Вовлеченные узлы\n\n"


def group_context_template(code: str, title: str, description: str) -> str:
    description_block = f"\n\n## Описание\n{description}\n" if description else ""
    return (
        f"# {title}\n\n"
        f"## Назначение\nГруппа `{code}` хранит надпроектный контекст: регламенты, шаблоны, методики и правила, применимые к нескольким проектам."
        f"{description_block}\n"
        "## Правила наследования\nПроектный контекст имеет приоритет при конфликте с групповым контекстом.\n"
    )


def group_index_template(code: str, title: str) -> str:
    return f"# {code} - Индекс памяти\n\n- Группа: {title}\n\n## Вовлеченные узлы\n\n"
