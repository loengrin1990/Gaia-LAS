from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import SETTINGS
from .masking import mask_with_review
from .models import ScribeApplyResult, ScribeDraft, ScribePlan, ScribePlanItem
from .module_assist import classify_scribe_candidates_with_local_llm
from .projects import existing_project_dir, project_record


BLOCK_REASON = "Scribe заблокирован: в пакете есть неподтвержденный риск ПД."
LOCAL_FALLBACK_BLOCK_REASON = "Scribe заблокирован: пакет требует локальной обработки или ручной проверки."
APPLY_REQUIRES_SELECTION = "Scribe apply requires at least one selected plan item."
DESTINATIONS = {
    "decisions": ("20_Decisions", "decision", "high"),
    "rules": ("20_Decisions", "decision", "high"),
    "risks": ("40_Risks", "risk", "medium"),
    "open_questions": ("30_Open_Questions", "open_question", "medium"),
    "technical_facts": ("10_Branches", "requirement", "medium"),
    "source_summary": ("50_Sources", "source_summary", "medium"),
}
EXCLUDED_CATEGORY = "exclude"


def create_scribe_draft(package: dict[str, Any], output_dir: Path | None = None) -> ScribeDraft:
    if package_has_unresolved_pii(package):
        raise ValueError(BLOCK_REASON)
    if SETTINGS is None and output_dir is None:
        raise RuntimeError("Gaia settings are unavailable.")

    project = str(package.get("project") or "Без проекта")
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    raw_markdown = build_memory_update_draft(package, created_at)
    masked = mask_with_review("Scribe draft", raw_markdown)
    if masked.review.unresolved_pii:
        raise ValueError(masked.review.unresolved_reason or BLOCK_REASON)

    target_dir = output_dir or SETTINGS.service_docs / "Черновики обновления памяти"
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{safe_slug(project)}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    draft_path = target_dir / filename
    draft_path.write_text(masked.masked_text, encoding="utf-8")

    return ScribeDraft(
        project=project,
        created_at=created_at,
        draft_path=str(draft_path),
        markdown=masked.masked_text,
        instruction=build_update_memory_instruction(project, draft_path),
        mask_review=masked.review,
    )


def create_scribe_plan(package: dict[str, Any]) -> ScribePlan:
    project = str(package.get("project") or "Без проекта")
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    plan_id = plan_identifier(package)
    safety_notes = scribe_safety_notes(package)
    if package_has_unresolved_pii(package):
        return ScribePlan(
            id=plan_id,
            project=project,
            created_at=created_at,
            status="blocked",
            blocked_reason=BLOCK_REASON,
            items=[],
            preview="Scribe plan заблокирован: есть неподтвержденный риск ПД.",
            safety_notes=safety_notes,
        )
    if bool(package.get("local_fallback_required")):
        return ScribePlan(
            id=plan_id,
            project=project,
            created_at=created_at,
            status="blocked",
            blocked_reason=LOCAL_FALLBACK_BLOCK_REASON,
            items=[],
            preview="Scribe plan заблокирован: пакет требует локальной обработки или ручной проверки.",
            safety_notes=safety_notes,
        )

    classifier = scribe_candidate_classifier(package) or {}
    items = plan_items_from_classifier(package, classifier)
    existing_ids = {item.id for item in items}
    if package_is_inbox(package):
        items.extend(plan_items_from_files(package, existing_ids=existing_ids))
    else:
        items.extend(plan_items_from_evidence(package, existing_ids=existing_ids))
    if not items:
        items.append(no_candidate_item(package))
    preview = build_plan_preview(project, items)
    return ScribePlan(
        id=plan_id,
        project=project,
        created_at=created_at,
        status="ready" if any(item.selected for item in items) else "empty",
        blocked_reason="",
        items=items,
        preview=preview,
        safety_notes=safety_notes,
    )


def apply_scribe_plan(package: dict[str, Any], selected_item_ids: list[str]) -> ScribeApplyResult:
    plan = create_scribe_plan(package)
    if plan.status == "blocked":
        raise ValueError(plan.blocked_reason or BLOCK_REASON)
    selected = [item for item in plan.items if item.id in set(selected_item_ids) and item.selected]
    if not selected:
        raise ValueError(APPLY_REQUIRES_SELECTION)

    project_dir = existing_project_dir(plan.project)
    record = project_record(project_dir)
    backup_path = create_memory_backup(project_dir, plan.id)
    changed: list[str] = []
    applied: list[str] = []
    skipped: list[str] = []

    for item in selected:
        if item.destination == "exclude" or item.operation == "skip":
            skipped.append(item.id)
            continue
        target = project_dir / item.target_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            skipped.append(item.id)
            continue
        target.write_text(render_plan_item_node(record.code, item, package), encoding="utf-8")
        changed.append(str(target))
        applied.append(item.id)

    if applied:
        changed.extend(update_memory_bookkeeping(project_dir, record.code, plan, selected, package))

    return ScribeApplyResult(
        plan_id=plan.id,
        project=plan.project,
        applied=applied,
        skipped=skipped,
        changed_files=dedupe(changed),
        backup_path=str(backup_path),
        journal_entry=f"Scribe apply {plan.id}: применено {len(applied)}, пропущено {len(skipped)}.",
        retrieval_check="Запусти целевой Lore-запрос после apply: новые graph nodes уже доступны для индексации.",
    )


def build_memory_update_draft(package: dict[str, Any], created_at: str) -> str:
    project = str(package.get("project") or "Без проекта")
    prompt = str(package.get("prompt") or "")
    files = package.get("files") or []
    notes = package.get("policy_notes") or []

    parts = [
        f"# Scribe draft: обновление памяти проекта {project}",
        "",
        f"Создано Gaia: {created_at}",
        "Статус: черновик, автоматически не применялся к `<код> - Память.md` или `Память_Graph`.",
        "",
        "## Назначение",
        "",
        "Этот файл является подготовкой к ручному обновлению проектной памяти через `$update-obsidian-project-memory`.",
        "Scribe не изменяет `<код> - Память.md`, `Память_Graph`, `<код> - Источники.md` и `<код> - Журнал памяти.md`.",
        "",
        "## Кандидаты для памяти",
        "",
        "- [ ] Выделить устойчивые решения, правила, риски и открытые вопросы из безопасного пакета ниже.",
        "- [ ] Не переносить персональные данные, телефоны, email, адреса, паспортные данные и длинные цитаты.",
        "- [ ] Сверить выводы с текущими `<код> - Память.md`, `Память_Graph`, `<код> - Источники.md` и `<код> - Журнал памяти.md` перед записью.",
        "",
        "## Сводка безопасности",
        "",
        f"- Маршрут пакета: {package.get('route', '-')}",
        f"- Внешний маршрут разрешен после подтверждения: {yes_no(package.get('safe_for_codex_after_confirmation'))}",
        f"- Требуется локальный fallback: {yes_no(package.get('local_fallback_required'))}",
        f"- Статус маскирования запроса: {package.get('query_mask_status', '-')}",
        f"- Замен в запросе: {package.get('query_mask_replacements', 0)}",
    ]
    if notes:
        parts.extend(["", "## Политики и предупреждения", ""])
        parts.extend(f"- {note}" for note in notes)
    classifier = scribe_candidate_classifier(package)
    if classifier:
        parts.extend(["", "## LLM-классификация кандидатов", ""])
        parts.append("Локальная классификация не применялась к памяти автоматически и требует ручной проверки.")
        for title, key in [
            ("Решения", "decisions"),
            ("Правила", "rules"),
            ("Риски", "risks"),
            ("Открытые вопросы", "open_questions"),
            ("Технические факты", "technical_facts"),
            ("Исключить из памяти", "exclude"),
        ]:
            values = classifier.get(key) or []
            if values:
                parts.extend(["", f"### {title}", ""])
                parts.extend(f"- {value}" for value in values)

    parts.extend(["", "## Вложения", ""])
    if files:
        for file_info in files:
            parts.extend([
                f"### {file_info.get('name', 'file')}",
                "",
                f"- Тип: {file_info.get('kind', '-')}",
                f"- Извлечение: {file_info.get('extraction_note', '-')}",
                f"- Статус Veil: {file_info.get('mask_status', '-')}",
                f"- Замен: {file_info.get('mask_replacements', 0)}",
                "",
            ])
    else:
        parts.append("Вложения не передавались.")

    parts.extend([
        "",
        "## Безопасный пакет для анализа",
        "",
        "```text",
        prompt,
        "```",
        "",
        "## Инструкция для ручного применения",
        "",
        build_update_memory_instruction(project, Path("<путь к этому черновику>")),
        "",
    ])
    return "\n".join(parts)


def build_update_memory_instruction(project: str, draft_path: Path) -> str:
    return "\n".join([
        "Используй `$update-obsidian-project-memory` для ручного обновления памяти.",
        f"Целевой проект: `{project}`.",
        f"Источник-черновик: `{draft_path}`.",
        "Сначала прочитай `<код> - Память.md`, активные узлы `Память_Graph`, `<код> - Источники.md` и `<код> - Журнал памяти.md` целевого проекта.",
        "Из черновика перенеси только устойчивые решения, правила, статусы, риски, открытые вопросы и полезные технические факты.",
        "Не переноси ПД, телефоны, email, адреса, паспортные данные, длинные цитаты и сырой текст вложений.",
        "После обновления проверь, что `<код> - Источники.md` и `<код> - Журнал памяти.md` отражают использованный черновик.",
    ])


def scribe_candidate_classifier(package: dict[str, Any]) -> dict[str, list[str]] | None:
    if not getattr(SETTINGS, "scribe_candidate_classifier", False):
        return None
    timeout = int(getattr(SETTINGS, "scribe_classifier_timeout_seconds", 5) or 5)
    classified = classify_scribe_candidates_with_local_llm(package, timeout)
    if not classified:
        return None
    if not any(classified.values()):
        return None
    return classified


def plan_items_from_classifier(package: dict[str, Any], classifier: dict[str, list[str]]) -> list[ScribePlanItem]:
    items: list[ScribePlanItem] = []
    for category, values in classifier.items():
        if not isinstance(values, list):
            continue
        for value in values:
            text = clean_candidate_text(str(value))
            if not text:
                continue
            items.append(plan_item(package, category, text))
    return items


def plan_items_from_evidence(package: dict[str, Any], existing_ids: set[str]) -> list[ScribePlanItem]:
    items: list[ScribePlanItem] = []
    for evidence in package.get("evidence_plan") or []:
        if evidence.get("status") != "confirmed":
            continue
        heading = clean_candidate_text(str(evidence.get("heading") or ""))
        excerpt = clean_candidate_text(str(evidence.get("excerpt") or ""))
        if not heading or not excerpt:
            continue
        text = f"{heading}: {excerpt}"
        item = plan_item(package, "source_summary", text, evidence=evidence)
        if item.id not in existing_ids:
            items.append(item)
            existing_ids.add(item.id)
    return items


def plan_items_from_files(package: dict[str, Any], existing_ids: set[str]) -> list[ScribePlanItem]:
    items: list[ScribePlanItem] = []
    for file_info in package.get("files") or []:
        name = clean_source_text(str(file_info.get("name") or "Файл Inbox"))
        masked_text = clean_source_text(str(file_info.get("masked_text") or ""))
        if not name or not masked_text:
            continue
        note = clean_source_text(str(file_info.get("extraction_note") or ""))
        text = f"{name}: {note}. Краткое содержание источника: {masked_text}"
        evidence = {
            "status": "confirmed",
            "heading": name,
            "source_path": file_info.get("stored_path") or name,
            "excerpt": masked_text[:600],
        }
        item = plan_item(package, "source_summary", text, evidence=evidence)
        if item.id not in existing_ids:
            items.append(item)
            existing_ids.add(item.id)
    return items


def package_is_inbox(package: dict[str, Any]) -> bool:
    origin = package.get("scribe_origin") or {}
    return isinstance(origin, dict) and origin.get("type") == "inbox"


def plan_item(package: dict[str, Any], category: str, text: str, evidence: dict[str, Any] | None = None) -> ScribePlanItem:
    project = str(package.get("project") or "Без проекта")
    folder, memory_type, confidence = DESTINATIONS.get(category, ("", "draft", "low"))
    excluded = category == EXCLUDED_CATEGORY
    title = item_title(text, category)
    item_id = stable_item_id(project, category, text)
    target_path = ""
    if not excluded and folder:
        code = project_code_for_package(package)
        target_path = str(Path("Память_Graph") / folder / f"{code} - {safe_slug(title)}.md")
    notes = item_safety_notes(text, evidence)
    return ScribePlanItem(
        id=item_id,
        category=category,
        title=title,
        body=text,
        destination="exclude" if excluded else folder,
        operation="skip" if excluded else "create",
        target_path=target_path,
        confidence="low" if excluded else confidence,
        status="excluded" if excluded else "staged",
        evidence=evidence_summary(evidence, package),
        reason=destination_reason(category),
        safety_notes=notes,
        selected=not excluded and not notes_contains_blocker(notes),
    )


def no_candidate_item(package: dict[str, Any]) -> ScribePlanItem:
    text = "Scribe не нашел безопасных устойчивых кандидатов для автоматического staged patch."
    return ScribePlanItem(
        id=stable_item_id(str(package.get("project") or ""), "exclude", text),
        category="exclude",
        title="Нет кандидатов",
        body=text,
        destination="exclude",
        operation="skip",
        target_path="",
        confidence="low",
        status="excluded",
        evidence="",
        reason="Нет классифицированных кандидатов или confirmed evidence.",
        safety_notes=["Память не будет изменена."],
        selected=False,
    )


def build_plan_preview(project: str, items: list[ScribePlanItem]) -> str:
    lines = [f"# Scribe plan: {project}", ""]
    for item in items:
        mark = "x" if item.selected else " "
        lines.extend([
            f"- [{mark}] {item.title}",
            f"  - category: {item.category}",
            f"  - destination: {item.destination or '-'}",
            f"  - operation: {item.operation}",
            f"  - target: {item.target_path or '-'}",
            f"  - confidence: {item.confidence}",
        ])
        if item.evidence:
            lines.append(f"  - evidence: {item.evidence}")
        if item.safety_notes:
            lines.append(f"  - safety: {'; '.join(item.safety_notes)}")
    return "\n".join(lines)


def render_plan_item_node(code: str, item: ScribePlanItem, package: dict[str, Any]) -> str:
    memory_type = DESTINATIONS.get(item.category, ("", "draft", "low"))[1]
    today = datetime.now().strftime("%Y-%m-%d")
    source = item.evidence or f"Scribe plan {item.id}"
    lines = [
        "---",
        f"type: {memory_type}",
        "priority: 70",
        f"confidence: {item.confidence}",
        "status: active" if item.category != "open_questions" else "status: open",
        f"source: \"{escape_frontmatter(source)}\"",
        f"last_verified_at: {today}",
        "links: []",
        "---",
        "",
        f"# {code} - {item.title}",
        "",
        item.body,
        "",
        "## Provenance",
        "",
        f"- Scribe plan: `{item.id}`",
        f"- Run: `{package.get('run_id', '-')}`",
        f"- Evidence: {item.evidence or '-'}",
        "",
        "## Safety",
        "",
    ]
    if item.safety_notes:
        lines.extend(f"- {note}" for note in item.safety_notes)
    else:
        lines.append("- ПД и длинные цитаты не обнаружены в staged item.")
    return "\n".join(lines) + "\n"


def update_memory_bookkeeping(
    project_dir: Path,
    code: str,
    plan: ScribePlan,
    selected: list[ScribePlanItem],
    package: dict[str, Any],
) -> list[str]:
    changed: list[str] = []
    sources_path = project_dir / f"{code} - Источники.md"
    journal_path = project_dir / f"{code} - Журнал памяти.md"
    index_path = project_dir / "Память_Graph" / f"{code} - Индекс памяти.md"
    today = datetime.now().strftime("%Y-%m-%d")

    append_once(
        sources_path,
        "\n".join([
            f"| Scribe plan `{plan.id}` | контекст | {today} | staged apply из Gaia; run `{package.get('run_id', '-')}` |",
        ]) + "\n",
        header=f"# {code} - Источники\n\n| Источник | Статус | Дата | Комментарий |\n|---|---|---|---|\n",
    )
    changed.append(str(sources_path))

    node_links = [f"[[{Path(item.target_path).stem}]]" for item in selected if item.target_path]
    append_once(
        journal_path,
        "\n".join([
            f"## {today} - Scribe apply `{plan.id}`",
            "",
            f"- Run: `{package.get('run_id', '-')}`",
            f"- Узлы: {', '.join(node_links) if node_links else '-'}",
            "- Применено после явного review в Gaia Scribe.",
            "",
        ]),
        header=f"# {code} - Журнал памяти\n\n",
    )
    changed.append(str(journal_path))

    append_once(
        index_path,
        "\n".join(f"- {link}" for link in node_links) + "\n",
        header=f"# {code} - Индекс памяти\n\n## Вовлеченные узлы\n\n",
    )
    changed.append(str(index_path))
    return changed


def create_memory_backup(project_dir: Path, plan_id: str) -> Path:
    if SETTINGS is None:
        raise RuntimeError("Gaia settings are unavailable.")
    backup_root = SETTINGS.service_docs / "Scribe Backups" / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{plan_id}"
    backup_root.mkdir(parents=True, exist_ok=True)
    for path in active_project_memory_files(project_dir):
        target = backup_root / path.relative_to(project_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
    return backup_root


def active_project_memory_files(project_dir: Path) -> list[Path]:
    files = []
    for pattern in ["* - Память.md", "* - Источники.md", "* - Журнал памяти.md"]:
        files.extend(project_dir.glob(pattern))
    graph_root = project_dir / "Память_Graph"
    if graph_root.exists():
        files.extend(
            path for path in graph_root.rglob("*.md")
            if path.is_file() and "90_Archive" not in path.relative_to(graph_root).parts
        )
    return sorted(set(files))


def append_once(path: Path, text: str, header: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else header
    if text.strip() not in existing:
        if existing and not existing.endswith("\n"):
            existing += "\n"
        existing += text
        path.write_text(existing, encoding="utf-8")


def scribe_safety_notes(package: dict[str, Any]) -> list[str]:
    notes = [
        f"query mask: {package.get('query_mask_status', '-')}, replacements: {package.get('query_mask_replacements', 0)}",
        "Scribe apply requires explicit selected item ids.",
    ]
    if package.get("evidence_plan"):
        if package_is_inbox(package):
            notes.append("Inbox package: staged items are scoped to selected file, not Lore evidence.")
        else:
            notes.append("Evidence plan available for grounding staged items.")
    origin = package.get("scribe_origin") or {}
    if isinstance(origin, dict) and origin.get("relative_path"):
        notes.append(f"Inbox source: {origin.get('relative_path')}")
    return notes


def clean_candidate_text(value: str) -> str:
    text = " ".join(value.strip().split())
    if not text or len(text) < 8:
        return ""
    if any(marker in text for marker in ("{", "}", "<", ">", "`")):
        return ""
    return text[:800]


def clean_source_text(value: str) -> str:
    text = " ".join(value.strip().split())
    if not text:
        return ""
    return text[:800]


def item_title(text: str, category: str) -> str:
    title = text.split(".", 1)[0].split(":", 1)[0].strip()
    if len(title) > 80:
        title = title[:77].rstrip() + "..."
    return title or category


def stable_item_id(project: str, category: str, text: str) -> str:
    raw = f"{project}\n{category}\n{text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def plan_identifier(package: dict[str, Any]) -> str:
    raw = f"{package.get('run_id', '')}\n{package.get('project', '')}\n{package.get('prompt', '')[:2000]}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def project_code_for_package(package: dict[str, Any]) -> str:
    project = str(package.get("project") or "")
    try:
        record = project_record(existing_project_dir(project))
        return record.code
    except Exception:
        words = [word[0] for word in project.split() if word]
        return "".join(words).upper()[:6] or "PRJ"


def evidence_summary(evidence: dict[str, Any] | None, package: dict[str, Any]) -> str:
    if evidence:
        heading = evidence.get("heading") or "-"
        path = evidence.get("source_path") or "-"
        return f"{heading} ({path})"
    sources = package.get("memory_sources") or []
    if sources:
        first = sources[0]
        return f"{first.get('heading', '-')} ({first.get('path', '-')})"
    return ""


def item_safety_notes(text: str, evidence: dict[str, Any] | None) -> list[str]:
    notes: list[str] = []
    if len(text) > 600:
        notes.append("Кандидат обрезан до краткой формы; проверь, что это не сырой dump.")
    if evidence and evidence.get("status") != "confirmed":
        notes.append("Evidence не confirmed; item не должен становиться decision без проверки.")
    if looks_like_pii(text):
        notes.append("Возможный ПД-паттерн; item исключен из применения.")
    return notes


def notes_contains_blocker(notes: list[str]) -> bool:
    return any("ПД" in note for note in notes)


def looks_like_pii(text: str) -> bool:
    lower = text.lower()
    return "@" in text or "+7" in text or any(marker in lower for marker in ("паспорт", "телефон", "email", "адрес "))


def destination_reason(category: str) -> str:
    return {
        "decisions": "Кандидат классифицирован как решение; destination 20_Decisions.",
        "rules": "Правило хранится как decision/governance node.",
        "risks": "Риск хранится в 40_Risks.",
        "open_questions": "Неопределенность хранится в 30_Open_Questions.",
        "technical_facts": "Технический факт хранится в тематической ветке 10_Branches.",
        "source_summary": "Confirmed evidence оформляется как 50_Sources provenance node.",
        "exclude": "Кандидат явно исключен из памяти.",
    }.get(category, "Scribe выбрал безопасный staged destination.")


def escape_frontmatter(value: str) -> str:
    return value.replace('"', "'")[:240]


def dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def package_has_unresolved_pii(package: dict[str, Any]) -> bool:
    query_review = package.get("query_mask_review") or {}
    if bool(query_review.get("unresolved_pii")):
        return True
    for file_info in package.get("files") or []:
        review = file_info.get("mask_review") or {}
        if bool(review.get("unresolved_pii")):
            return True
    return False


def safe_slug(value: str) -> str:
    slug = "".join(ch if ch.isalnum() else "-" for ch in value.strip())
    slug = "-".join(part for part in slug.split("-") if part)
    return slug[:80] or "project"


def yes_no(value: Any) -> str:
    return "да" if bool(value) else "нет"
