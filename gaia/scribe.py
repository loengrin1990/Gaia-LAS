from __future__ import annotations

import hashlib
import re
import shutil
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
EXISTING_TARGET_ACTIONS = {"update_existing", "skip_duplicate", "create_linked"}
DESTINATIONS = {
    "decisions": ("20_Decisions", "decision", "high"),
    "rules": ("20_Decisions", "decision", "high"),
    "risks": ("40_Risks", "risk", "medium"),
    "open_questions": ("30_Open_Questions", "open_question", "medium"),
    "technical_facts": ("10_Branches", "requirement", "medium"),
    "source_summary": ("50_Sources", "source_summary", "medium"),
}
EXCLUDED_CATEGORY = "exclude"
MAX_MEMORY_TITLE_TOKENS = 3
MAX_MEMORY_TITLE_CHARS = 34


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
    items.extend(plan_items_from_semantic_enrichment(package, existing_ids=existing_ids))
    items.extend(plan_items_from_files(package, existing_ids=existing_ids))
    apply_quality_gate(project, package, items)
    mark_existing_targets(project, items)
    if not items:
        items.append(no_candidate_item(package))
    preview = build_plan_preview(project, items)
    has_apply_path = any(item.selected or item.operation == "existing_target" for item in items)
    return ScribePlan(
        id=plan_id,
        project=project,
        created_at=created_at,
        status="ready" if has_apply_path else "empty",
        blocked_reason="",
        items=items,
        preview=preview,
        safety_notes=safety_notes,
    )


def apply_scribe_plan(
    package: dict[str, Any],
    selected_item_ids: list[str],
    item_actions: dict[str, str] | None = None,
) -> ScribeApplyResult:
    plan = create_scribe_plan(package)
    if plan.status == "blocked":
        raise ValueError(plan.blocked_reason or BLOCK_REASON)
    actions = item_actions or {}
    selected_ids = set(selected_item_ids)
    selected = [
        item for item in plan.items
        if item.id in selected_ids and (item.selected or actions.get(item.id) in EXISTING_TARGET_ACTIONS)
    ]
    if not selected:
        raise ValueError(APPLY_REQUIRES_SELECTION)

    project_dir = existing_project_dir(plan.project)
    record = project_record(project_dir)
    backup_path = create_memory_backup(project_dir, plan.id)
    changed: list[str] = []
    applied: list[str] = []
    skipped: list[str] = []

    for item in selected:
        action = actions.get(item.id, item.operation)
        if item.destination == "exclude" or action in {"skip", "skip_duplicate"}:
            skipped.append(item.id)
            continue
        target = project_dir / item.target_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if action == "update_existing":
                changed.append(str(append_existing_node_update(target, item, package)))
                applied.append(item.id)
                continue
            if action == "create_linked":
                item = linked_plan_item(project_dir, record.code, item)
                target = project_dir / item.target_path
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    skipped.append(item.id)
                    continue
            else:
                skipped.append(item.id)
                continue
        target.write_text(render_plan_item_node(record.code, item, package), encoding="utf-8")
        changed.append(str(target))
        changed.extend(apply_related_archives(project_dir, plan.id, item))
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
    return []


def plan_items_from_files(package: dict[str, Any], existing_ids: set[str]) -> list[ScribePlanItem]:
    items: list[ScribePlanItem] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for file_info in package.get("files") or []:
        name = clean_source_text(str(file_info.get("name") or "Файл Inbox"))
        masked_text = clean_source_text(str(file_info.get("masked_text") or ""))
        if not name or not masked_text:
            continue
        key = source_context_key(name, masked_text)
        grouped.setdefault(key, []).append(file_info)

    for key, file_infos in grouped.items():
        first = file_infos[0]
        name = clean_source_text(str(first.get("name") or "Файл Inbox"))
        kind = clean_source_text(str(first.get("kind") or "document"))
        title = source_summary_title(key, name, str(first.get("masked_text") or ""))
        source_paths = [
            clean_source_text(str(info.get("stored_path") or info.get("name") or ""))
            for info in file_infos
            if isinstance(info, dict)
        ]
        notes = [
            clean_source_text(str(info.get("extraction_note") or ""))
            for info in file_infos
            if isinstance(info, dict) and info.get("extraction_note")
        ]
        text = source_summary_body(title, key, kind, notes, source_paths, merged=len(file_infos) > 1)
        evidence = {
            "status": "confirmed",
            "heading": title,
            "source_path": "; ".join(source_paths) or name,
            "excerpt": "Источник обработан; сырой текст не переносится в память.",
        }
        item = plan_item(package, "source_summary", text, evidence=evidence)
        if len(file_infos) > 1:
            item.operation = "merge"
            item.related_paths = source_paths
            item.reason = "Несколько источников поддерживают один контекст; Scribe создает один source-summary с несколькими provenance."
        if item.id not in existing_ids:
            items.append(item)
            existing_ids.add(item.id)
    return items


def plan_items_from_semantic_enrichment(package: dict[str, Any], existing_ids: set[str]) -> list[ScribePlanItem]:
    items: list[ScribePlanItem] = []
    combined = "\n".join(
        str(file_info.get("masked_text") or "")
        for file_info in package.get("files") or []
        if isinstance(file_info, dict)
    )
    lower = combined.lower()
    if not lower.strip():
        return items

    candidates: list[tuple[str, str]] = []
    if all(term in lower for term in ("скуд", "синхронизатор")) and any(term in lower for term in ("face id", "фейс")):
        candidates.append((
            "technical_facts",
            semantic_body(
                "Текущий поток данных ГДРС-отчета",
                "ГДРС-отчет опирается на связку СКУД, синхронизатора данных, базы данных и потребителей отчетности/Face ID.",
                [
                    "СКУД выступает исходным контуром для справочных и пропускных данных по проектам, подрядчикам и работникам.",
                    "Синхронизатор выгружает данные из СКУД и складывает их в базу данных, из которой дальше читают отчетные механизмы.",
                    "Face ID Web и отчетность используют эти данные не одинаково, поэтому изменения в СКУД могут распространяться в разные потребители с разной полнотой.",
                ],
                [
                    "Использовать этот узел как стартовый контекст для вопросов об архитектуре ГДРС, источниках данных и расхождениях между системами.",
                    "Не считать этот узел финальным архитектурным решением: конкретные механизмы обновления нужно уточнять по исходникам и коду.",
                ],
                evidence_snippets(combined, ("скуд", "синхронизатор", "face id", "база данных", "отчет")),
            ),
        ))
    if any(term in lower for term in ("telegrambot", "телеграм", "телеграмм", "битрикс")) and any(term in lower for term in ("бот", "отчет")):
        candidates.append((
            "technical_facts",
            semantic_body(
                "Роль Telegram/Bitrix-ботов в отчетности",
                "На встрече обсуждалось разделение функций старого Telegram/Bitrix-бота и целевого контура доставки отчетности.",
                [
                    "Старый бот описан как перегруженный монолит: в нем совмещались задачи отчетности и дополнительные функции управления пропусками/заявками.",
                    "Целевой контур должен отделять генерацию отчета от доставки результата пользователям.",
                    "Для памяти важно хранить не факт разговора о боте, а архитектурную развилку: что остается в боте, а что должно перейти в отчетный/интеграционный контур.",
                ],
                [
                    "Использовать при обсуждении целевой архитектуры доставки ГДРС-отчета и декомпозиции старого бота.",
                    "Перед превращением в decision нужно подтвердить, какая роль Bitrix/Telegram-бота уже принята, а какая была только вариантом.",
                ],
                evidence_snippets(combined, ("telegrambot", "телеграм", "битрикс", "бот", "отчет")),
            ),
        ))
    if "разов" in lower and "пропуск" in lower and "подрядчик" in lower:
        candidates.append((
            "open_questions",
            semantic_body(
                "Учет подрядчика для разовых пропусков",
                "Открыт вопрос, как отражать подрядчика для разовых пропусков в ГДРС-отчете.",
                [
                    "Если человек находится в контуре разового пропуска, его сложнее связать с папкой/названием подрядчика.",
                    "Обсуждались два направления: показывать разовые пропуска отдельной строкой в отчете либо доработать механизм заведения/дополнительное поле подрядчика.",
                    "Качество результата зависит от дисциплины ручного заведения и единообразия названий подрядчиков.",
                ],
                [
                    "Использовать как открытый вопрос при проектировании структуры отчета и правил заполнения данных СБ.",
                    "Не считать выбранным решением до подтверждения владельцем процесса.",
                ],
                evidence_snippets(combined, ("разов", "пропуск", "подрядчик", "кастом", "организация")),
            ),
        ))
    if "журнал" in lower and "охран" in lower and "кнопк" in lower:
        candidates.append((
            "risks",
            semantic_body(
                "Риск расхождений из-за прохода по кнопке охраны",
                "Есть риск расхождения ГДРС-отчета с фактическими проходами, если охрана пропускает людей вручную по кнопке и фиксирует событие в бумажном журнале.",
                [
                    "Такие проходы не создают цифровое событие, которое может быть учтено синхронизатором и отчетностью.",
                    "Бизнес может видеть расхождение между фактическим нахождением людей на площадке и данными отчета.",
                    "Ранее обсуждалась цифровизация этого журнала как отдельный процесс, но это требует обучения охраны и изменения операционной дисциплины.",
                ],
                [
                    "Использовать как риск качества данных и как источник требований к цифровизации ручных проходов.",
                    "При анализе расхождений отчетности проверять, были ли проходы через ручной журнал охраны.",
                ],
                evidence_snippets(combined, ("журнал", "охран", "кнопк", "расход", "отчетность")),
            ),
        ))
    if "мастер" in lower and "систем" in lower and any(term in lower for term in ("название", "проект")):
        candidates.append((
            "open_questions",
            semantic_body(
                "Мастер-система для названий проектов",
                "Открыт вопрос выбора мастер-системы для названий проектов и правил распространения переименований.",
                [
                    "Названия проектов используются в СКУД, Face ID Web и отчетности.",
                    "Встреча зафиксировала риск хардкода/ручной нормализации переименований и разные варианты одного проекта в разных системах.",
                    "Предварительно обсуждалось, что мастер-источником может стать СКУД, но это требует тестирования распространения изменений.",
                ],
                [
                    "Использовать при вопросах о справочниках, нормализации названий и источниках истины.",
                    "Хранить как open question, пока не подтверждено решение о мастер-системе и владельце справочника.",
                ],
                evidence_snippets(combined, ("мастер", "название", "проект", "скуд", "face id", "хардкод")),
            ),
        ))
    if "техник" in lower and "безопас" in lower and any(term in lower for term in ("блок", "пропуск")):
        candidates.append((
            "technical_facts",
            semantic_body(
                "Блокировка пропусков по технике безопасности",
                "Встреча выделила отдельный функциональный контур, связанный с блокировкой пропусков для людей, не прошедших технику безопасности.",
                [
                    "Этот функционал связан со СКУД/пропусками, но может быть реализован не в самом отчете, а в смежном контуре управления доступом.",
                    "Для целевой архитектуры важно определить, где должна жить проверка техники безопасности и как она влияет на пропуск.",
                    "Нужна проверка возможностей СКУД и текущих внешних систем до фиксации требования.",
                ],
                [
                    "Использовать как технический факт/кандидат в требование при декомпозиции функций старого бота и контура пропусков.",
                    "Не превращать в accepted decision без подтверждения владельцев СБ и технической проверки.",
                ],
                evidence_snippets(combined, ("техник", "безопас", "блок", "пропуск", "скуд")),
            ),
        ))
    if all(term in lower for term in ("recognize", "meta.json")) and any(term in lower for term in ("tesseract", "gliner", "osmi")):
        candidates.append((
            "technical_facts",
            semantic_body(
                "Pipeline OCR и OSMI для обработки документов с ПД",
                "Документ описывает двухфазный pipeline: backend запускает OCR-подготовку и ПД-разметку, затем передает в OSMI только очищенные или закрытые материалы для поиска релевантных страниц, VLM-анализа и извлечения дефектов.",
                [
                    "Первая фаза стартует через `POST /recognize`: создается `meta.json`, документ делится на страницы, страницы сохраняются как jpg в S3, выполняются выравнивание/очистка и Tesseract-метрики качества OCR.",
                    "ПД-этап использует GLiNER + regex, сохраняет типы и координаты найденных ПД по страницам, затем выполняет повторный поиск падежных форм ФИО и агрегирует статистику в `meta.json`.",
                    "Backend забирает `/pii`, сохраняет координаты/ПД в БД, получает pdf с закрытыми ПД и сохраняет его в S3.",
                    "Вторая фаза для OSMI состоит из поиска релевантных страниц, VLM и извлечения дефектов; текстовые материалы передаются с заменой ПД на `[REDACTED]`, а изображения страниц - с закрытыми ПД.",
                    "Результаты распознавания сохраняются как `defects.json` и `defects.xlsx`, после чего backend сохраняет дефекты в своей БД.",
                ],
                [
                    "Использовать как архитектурный контекст для вопросов о границах OCR, Veil/PII-redaction, OSMI и backend-хранения результатов.",
                    "При проверке безопасности внешних LLM-этапов сверяться с правилом: исходные сканы не меняются, но наружу в OSMI уходят redacted txt или изображения с закрытыми ПД.",
                    "Не трактовать узел как полное ТЗ API; это краткая карта pipeline и точек хранения.",
                ],
                evidence_snippets(combined, ("recognize", "meta.json", "gliner", "osmi", "defects.json", "redacted")),
            ),
        ))
    if not candidates and any(term in lower for term in ("архитект", "интеграц", "процесс", "система", "отчет")):
        candidates.append((
            EXCLUDED_CATEGORY,
            "Источник содержит общие архитектурные или процессные слова, но Scribe не нашел устойчивых доменных фактов для автоматической записи в память.",
        ))

    for category, text in candidates:
        item = plan_item(package, category, text)
        if item.id not in existing_ids:
            items.append(item)
            existing_ids.add(item.id)
    return items


def semantic_body(
    title: str,
    summary: str,
    context: list[str],
    usage: list[str],
    evidence: list[str],
) -> str:
    lines = [
        f"{title}: {summary}",
        "",
        "## Суть",
        "",
        summary,
        "",
        "## Контекст",
        "",
    ]
    lines.extend(f"- {item}" for item in context)
    lines.extend(["", "## Как использовать в Gaia", ""])
    lines.extend(f"- {item}" for item in usage)
    if evidence:
        lines.extend(["", "## Evidence", ""])
        lines.extend(f"- {item}" for item in evidence)
    return "\n".join(lines)


def evidence_snippets(text: str, keywords: tuple[str, ...], limit: int = 3) -> list[str]:
    snippets: list[str] = []
    normalized = [line.strip() for line in text.splitlines() if line.strip()]
    for line in normalized:
        lower = line.lower()
        if not any(keyword in lower for keyword in keywords):
            continue
        cleaned = " ".join(line.split())
        if looks_like_pii(cleaned):
            continue
        if len(cleaned) > 220:
            cleaned = cleaned[:217].rstrip() + "..."
        if cleaned and cleaned not in snippets:
            snippets.append(cleaned)
        if len(snippets) >= limit:
            break
    return snippets


def source_context_key(name: str, masked_text: str) -> str:
    lower = f"{name}\n{masked_text}".lower()
    if "определ" in lower and "дальн" in lower and "шаг" in lower and "автопретенз" in lower:
        return "launch_steps"
    if any(term in lower for term in ("баг-лист", "баг лист", "backlog", "бэклог")):
        return "bugs_backlog"
    if "ведомост" in lower and "компенсац" in lower and any(term in lower for term in ("ux", "дизайн", "прототип")):
        return "compensation_ux"
    if "mvp2" in lower and any(term in lower for term in ("ролевая модель", "матрица ролей", "перечень ролей")):
        return "mvp2_roles"
    if "mvp2" in lower and any(term in lower for term in ("user story", "user stories", "пользовательск")) and "us-07" in lower:
        return "mvp2_user_stories"
    if "mvp2" in lower and "модель данных" in lower and any(term in lower for term in ("карточк", "фильтр", "сортировк", "таблиц")):
        return "mvp2_data_model"
    if "mvp2" in lower and "статусн" in lower and "модель" in lower:
        return "mvp2_status_model"
    if "компенсац" in lower and any(term in lower for term in ("тз", "расчет", "мвп2")):
        return "compensation_spec"
    if any(term in lower for term in ("персональн", "пдн", "пд ")) and any(term in lower for term in ("иб", "защит", "безопас")):
        return "pii_security"
    if "переписк" in lower and "исполнител" in lower:
        return "executor_mail"
    if "паспорт" in lower and any(term in lower for term in ("ocr", "osmi", "mvp1", "мвп1")):
        return "ocr_passport"
    if any(term in lower for term in ("реестр замечаний", "замечания по коду", "замечаний по коду")):
        return "code_findings"
    if all(term in lower for term in ("recognize", "meta.json")) and any(term in lower for term in ("gliner", "osmi", "tesseract")):
        return "ocr_pii_description"
    if source_name_is_low_signal(name):
        return content_context_key(masked_text)
    return "source:" + "-".join(normalize_title_tokens(name)[:6])


def source_summary_title(key: str, name: str, masked_text: str) -> str:
    mapped = {
        "launch_steps": "Шаги запуска",
        "bugs_backlog": "Баги бэклог",
        "compensation_ux": "UX компенсаций",
        "compensation_spec": "ТЗ компенсаций",
        "pii_security": "Защита ПДн",
        "executor_mail": "Переписка исполнителя",
        "ocr_passport": "Паспорт OCR",
        "code_findings": "Замечания кода",
        "ocr_pii_description": "Описание OCR ПД",
        "mvp2_roles": "Роли MVP2",
        "mvp2_user_stories": "Сценарии MVP2",
        "mvp2_data_model": "Модель данных",
        "mvp2_status_model": "Статусная модель",
    }
    if key in mapped:
        return mapped[key]
    if key.startswith("content:"):
        return key.removeprefix("content:") or content_based_title(masked_text)
    return shorten_memory_title(strip_project_prefix(Path(name).stem), masked_text)


def source_summary_body(
    title: str,
    key: str,
    kind: str,
    notes: list[str],
    source_paths: list[str],
    merged: bool,
) -> str:
    if key == "code_findings":
        return code_findings_source_body(title, kind, notes, source_paths)
    if key == "mvp2_roles":
        return mvp2_roles_source_body(title, kind, notes, source_paths)
    if key == "mvp2_user_stories":
        return mvp2_user_stories_source_body(title, kind, notes, source_paths)
    if key == "mvp2_data_model":
        return mvp2_data_model_source_body(title, kind, notes, source_paths)
    if key == "mvp2_status_model":
        return mvp2_status_model_source_body(title, kind, notes, source_paths)
    if key.startswith("content:"):
        return content_based_source_body(title, kind, notes, source_paths, key)
    lines = [
        f"{title}: source-summary Scribe без переноса сырого текста.",
        "",
        "## Durable memory",
        "",
        f"- Источник обработан Gaia Scribe; тип: {kind or 'document'}.",
        "- Узел хранит provenance и краткий смысловой указатель, а не имя файла и не dump.",
    ]
    if merged:
        lines.append("- Несколько материалов сведены в один контекстный source-summary, чтобы не плодить дубли в памяти.")
    if notes:
        lines.append(f"- Статус извлечения: {'; '.join(dedupe(notes))}.")
    lines.extend(["", "## Provenance", ""])
    if source_paths:
        lines.extend(f"- `{path}`" for path in dedupe(source_paths))
    else:
        lines.append(f"- Тип: {kind or 'document'}")
    return "\n".join(lines)


def code_findings_source_body(title: str, kind: str, notes: list[str], source_paths: list[str]) -> str:
    lines = [
        f"{title}: source-summary реестра замечаний по коду без переноса сырого текста.",
        "",
        "## Durable memory",
        "",
        "- Источник описывает реестр замечаний по пяти сервисам: backend, распознавание, сервис помещений, адаптер Техзора и интерфейс.",
        "- Основные группы замечаний: обработка ошибок, надежность/потеря данных, статусы и синхронизация интерфейса, защита внутренних сервисов, соответствие ТЗ.",
        "- Высокие риски: зависание обработки без финального статуса, отсутствие watchdog на долгий шаг, потеря CRM-событий, застревание данных между сервисом помещений и backend, ошибочная успешная синхронизация Техзора.",
        "- Отдельный UX/архитектурный риск: интерфейс форсирует статус `готов к проверке` перед отправкой в Техзор, хотя готовностью должен управлять серверный pipeline.",
        "- Рекомендованный способ использовать источник: как backlog надежности и приемки перед боевым запуском, а не как перечень пользовательских требований.",
    ]
    if notes:
        lines.append(f"- Статус извлечения: {'; '.join(dedupe(notes))}.")
    lines.extend(["", "## Provenance", ""])
    if source_paths:
        lines.extend(f"- `{path}`" for path in dedupe(source_paths))
    else:
        lines.append(f"- Тип: {kind or 'document'}")
    return "\n".join(lines)


def mvp2_roles_source_body(title: str, kind: str, notes: list[str], source_paths: list[str]) -> str:
    lines = [
        f"{title}: source-summary ролевой модели и процесса MVP2 без переноса сырого текста.",
        "",
        "## Durable memory",
        "",
        "- Источник описывает роли MVP2: Эксперт, Расчетчик и Администратор системы.",
        "- Эксперт работает с заявками и документами, валидирует результат сервиса, передает результат расчетчику, отслеживает статус расчета и получает заполненную форму компенсации.",
        "- Расчетчик работает с формой компенсации: открывает назначенные задачи, заполняет объемы, проверяет автоматически рассчитанные цены, редактирует цены при необходимости, комментирует изменения и отправляет форму основному пользователю.",
        "- Администратор отвечает за пользователей, права доступа, логи, параметры системы, уведомления, метрики и настройку статусов заявок.",
        "- Процесс MVP2 связывает контур претензий, расчет компенсаций, уведомления, экспорт результата и отправку результата в Техзор.",
        "- Рекомендованный способ использовать источник: как матрицу прав и контрольный список приемки ролей/маршрутов MVP2.",
    ]
    if notes:
        lines.append(f"- Статус извлечения: {'; '.join(dedupe(notes))}.")
    lines.extend(["", "## Provenance", ""])
    if source_paths:
        lines.extend(f"- `{path}`" for path in dedupe(source_paths))
    else:
        lines.append(f"- Тип: {kind or 'document'}")
    return "\n".join(lines)


def mvp2_user_stories_source_body(title: str, kind: str, notes: list[str], source_paths: list[str]) -> str:
    lines = [
        f"{title}: source-summary пользовательских сценариев MVP2 без переноса сырого текста.",
        "",
        "## Durable memory",
        "",
        "- Источник описывает User Stories MVP2 для маршрута расчета компенсации после валидации результатов MVP1.",
        "- Сценарии US-07..US-13 покрывают направление результата на Расчетчика, получение задачи, заполнение формы компенсации, валидацию/редактирование цен, отправку формы Эксперту, формирование шаблона соглашения и выгрузку Word/PDF.",
        "- Критичные приемочные критерии: задача создается сразу, уведомления доставляются в течение 1 минуты, исходные данные MVP1 отображаются полностью, изменения цен требуют комментария, история изменений сохраняется.",
        "- Открытая детализация: структура формы компенсации и структура шаблона соглашения отмечены как TBD и требуют уточнения перед реализацией/приемкой.",
        "- Рекомендованный способ использовать источник: как checklist сценариев и acceptance-критериев MVP2 рядом с узлами расчета компенсаций, ролей и приемки.",
    ]
    if notes:
        lines.append(f"- Статус извлечения: {'; '.join(dedupe(notes))}.")
    lines.extend(["", "## Provenance", ""])
    if source_paths:
        lines.extend(f"- `{path}`" for path in dedupe(source_paths))
    else:
        lines.append(f"- Тип: {kind or 'document'}")
    return "\n".join(lines)


def mvp2_data_model_source_body(title: str, kind: str, notes: list[str], source_paths: list[str]) -> str:
    lines = [
        f"{title}: source-summary модели данных MVP2 без переноса сырого текста.",
        "",
        "## Durable memory",
        "",
        "- Источник описывает модель данных MVP2 для карточки заявки, списка заявок и пользовательских операций поиска/фильтрации/сортировки.",
        "- Карточка заявки включает идентификацию, проект/объект/помещение, статус, ответственного, даты, источник создания, комментарий, связанные файлы, результат ИИ, уровень уверенности, признак ручных правок и клиентское ФИО.",
        "- Интеграционный слой фиксируется отдельными полями: статус отправки в Техзор, дата отправки, идентификатор в Техзоре, ошибки/предупреждения, лог интеграций CRM/AI/Техзор и история изменений статуса.",
        "- Списковые сценарии требуют фильтрации по проекту, объекту, помещению, статусу и датам; поиска по ID, проекту, объекту, помещению и ФИО клиента; сортировки по ID и датам создания/обновления.",
        "- Рекомендованный способ использовать источник: как основу для схемы карточки заявки, UI-таблицы, фильтров и приемки полноты данных MVP2.",
    ]
    if notes:
        lines.append(f"- Статус извлечения: {'; '.join(dedupe(notes))}.")
    lines.extend(["", "## Provenance", ""])
    if source_paths:
        lines.extend(f"- `{path}`" for path in dedupe(source_paths))
    else:
        lines.append(f"- Тип: {kind or 'document'}")
    return "\n".join(lines)


def mvp2_status_model_source_body(title: str, kind: str, notes: list[str], source_paths: list[str]) -> str:
    lines = [
        f"{title}: source-summary статусной модели MVP2 без переноса сырого текста.",
        "",
        "## Durable memory",
        "",
        "- Источник описывает статусную модель заявки MVP2 и связь статусов с действиями Эксперта, Расчетчика и системы.",
        "- Целевой сценарий включает статусы: `Отправлено в расчет`, `Расчет в процессе`, `Расчет завершен`, `Формирование соглашения`, `Готово к отправке в Техзор`, `Отправлено в Техзор`.",
        "- Негативные сценарии включают ошибки расчета цен, неполную форму и ошибку формирования соглашения; эти статусы требуют ручного исправления или повторной попытки.",
        "- Триггеры статусов завязаны на действия пользователя и автоматические события: направление на расчет, открытие задачи Расчетчиком, отправка формы, формирование соглашения и отправка результата в Техзор.",
        "- Рекомендованный способ использовать источник: как основу для workflow, UI-индикаторов, проверок приемки и синхронизации статусов MVP2.",
    ]
    if notes:
        lines.append(f"- Статус извлечения: {'; '.join(dedupe(notes))}.")
    lines.extend(["", "## Provenance", ""])
    if source_paths:
        lines.extend(f"- `{path}`" for path in dedupe(source_paths))
    else:
        lines.append(f"- Тип: {kind or 'document'}")
    return "\n".join(lines)


def content_based_source_body(title: str, kind: str, notes: list[str], source_paths: list[str], key: str) -> str:
    lines = [
        f"{title}: source-summary по содержимому файла без переноса сырого текста.",
        "",
        "## Durable memory",
        "",
        f"- Источник обработан Gaia Scribe; тип: {kind or 'document'}.",
        f"- Смысловое имя получено из извлеченного текста, потому что имя файла похоже на hash/служебный идентификатор.",
        "- Узел хранит краткий смысловой указатель и provenance; исходный текст не переносится в память как dump.",
    ]
    if notes:
        lines.append(f"- Статус извлечения: {'; '.join(dedupe(notes))}.")
    lines.extend(["", "## Provenance", ""])
    if source_paths:
        lines.extend(f"- `{path}`" for path in dedupe(source_paths))
    else:
        lines.append(f"- Тип: {kind or 'document'}")
    return "\n".join(lines)


def apply_quality_gate(project: str, package: dict[str, Any], items: list[ScribePlanItem]) -> None:
    code = project_code_for_package(package)
    seen_targets: dict[str, ScribePlanItem] = {}
    for item in items:
        if item.destination == "exclude":
            continue
        original_title = item.title
        item.title = quality_title(item.title, item.category, item.body)
        if item.title != original_title:
            item.safety_notes.append(f"Название нормализовано Quality Gate: `{original_title}` -> `{item.title}`.")
        if title_is_too_long(item.title):
            item.selected = False
            item.operation = "skip"
            item.status = "quality_blocked"
            item.safety_notes.append("Quality Gate: active memory title должен быть максимум 3 смысловых токена.")
        if title_is_low_signal(item.title):
            item.selected = False
            item.operation = "skip"
            item.status = "quality_blocked"
            item.safety_notes.append("Quality Gate: active memory title похож на хэш/служебный идентификатор, а не на смысловое имя.")
        if item.category == "source_summary" and source_summary_looks_like_dump(item.body):
            item.selected = False
            item.operation = "skip"
            item.status = "quality_blocked"
            item.safety_notes.append("Quality Gate: source_summary похож на сырой dump или длинный excerpt.")
        if item.category != "source_summary" and not has_durable_context(item.body):
            item.selected = False
            item.operation = "skip"
            item.status = "quality_blocked"
            item.safety_notes.append("Quality Gate: нет секций/содержания durable context для active memory.")
        if item.target_path:
            folder = item.destination
            item.target_path = str(Path("Память_Graph") / folder / memory_filename(code, item.title))
            if item.target_path in seen_targets:
                previous = seen_targets[item.target_path]
                if item.category == "source_summary":
                    previous.operation = "merge"
                    previous.reason = "Quality Gate объединил дублирующие source-summary в один active node."
                    previous.related_paths = dedupe((previous.related_paths or []) + (item.related_paths or []) + [item.evidence])
                item.selected = False
                item.operation = "skip"
                item.status = "duplicate"
                item.safety_notes.append(f"Quality Gate: duplicate target уже представлен item `{previous.id}`.")
            else:
                seen_targets[item.target_path] = item


def quality_title(title: str, category: str, body: str) -> str:
    if category == "source_summary":
        if not title_is_too_long(title) and not title_is_low_signal(title):
            return title
        return shorten_memory_title(title, body)
    mapped = deterministic_short_title(title, body)
    if mapped:
        return mapped
    return shorten_memory_title(title, body)


def deterministic_short_title(title: str, body: str) -> str:
    lower = f"{title}\n{body}".lower()
    if "pipeline" in lower and "ocr" in lower and "osmi" in lower:
        return "OCR OSMI ПД"
    if "расчет" in lower and "компенсац" in lower:
        return "Расчет компенсаций"
    if "статус" in lower and ("дефект" in lower or "карточ" in lower):
        return "Статусы дефектов"
    if "mvp2" in lower and "post" in lower:
        return "MVP2 PostMVP"
    return ""


def shorten_memory_title(title: str, body: str = "") -> str:
    stripped = strip_project_prefix(title)
    mapped = deterministic_short_title(stripped, body)
    if mapped:
        return mapped
    tokens = normalize_title_tokens(stripped)
    if not tokens:
        return "Контекст"
    short = " ".join(tokens[:MAX_MEMORY_TITLE_TOKENS])
    return short[:MAX_MEMORY_TITLE_CHARS].strip() or "Контекст"


def strip_project_prefix(value: str) -> str:
    text = value.strip().strip("-_ ")
    text = re.sub(r"^(АПР|ХО|АВ|ГДРС|ДП)\s*[-_]\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^(АПР|ХО|АВ|ГДРС|ДП)(?=[-_])", "", text, flags=re.IGNORECASE).strip("-_ ")
    return text


def normalize_title_tokens(value: str) -> list[str]:
    cleaned = value.replace("Post-MVP", "PostMVP")
    raw = [token for token in re.split(r"[\s_\-]+", cleaned) if token]
    ignored = {"и", "для", "по", "с", "со", "в", "на", "от", "из", "к", "the", "of"}
    return [token for token in raw if token.lower() not in ignored]


def source_name_is_low_signal(name: str) -> bool:
    stem = strip_project_prefix(Path(name).stem)
    tokens = normalize_title_tokens(stem)
    if not tokens:
        return True
    signal_tokens = 0
    for token in tokens:
        compact = re.sub(r"[^A-Za-zА-Яа-я0-9]", "", token)
        if not compact:
            continue
        if len(compact) >= 12 and re.fullmatch(r"[0-9a-fA-F]+", compact):
            continue
        if len(compact) >= 9 and sum(ch.isdigit() for ch in compact) >= max(5, len(compact) // 2):
            continue
        signal_tokens += 1
    return signal_tokens == 0


def content_context_key(masked_text: str) -> str:
    return "content:" + content_based_title(masked_text)


def content_based_title(masked_text: str) -> str:
    for line in masked_text.splitlines():
        title = heading_line_to_title(line)
        if title:
            return title
    return "Контекст источника"


def heading_line_to_title(line: str) -> str:
    text = clean_source_text(line)
    if not text:
        return ""
    text = re.sub(r"^\d+(?:\.\d+)*\.?\s*", "", text).strip()
    for marker in (" Документ ", " Источник ", " Ссылка ", " № ", " Таблица "):
        if marker in text:
            text = text.split(marker, 1)[0].strip()
    text = re.sub(r"^(описание|раздел|глава)\s+", "", text, flags=re.IGNORECASE).strip()
    if not text or len(text) < 4:
        return ""
    lower = text.lower()
    if re.fullmatch(r"[\d\s.]+", text):
        return ""
    if any(term in lower for term in ("http://", "https://", "страница ", "page ")):
        return ""
    mapped = deterministic_short_title(text, text)
    if mapped:
        return mapped
    return shorten_memory_title(text)


def title_is_too_long(title: str) -> bool:
    return len(normalize_title_tokens(title)) > MAX_MEMORY_TITLE_TOKENS or len(title) > MAX_MEMORY_TITLE_CHARS


def title_is_low_signal(title: str) -> bool:
    tokens = normalize_title_tokens(strip_project_prefix(title))
    if not tokens:
        return True
    low_signal = 0
    for token in tokens:
        compact = re.sub(r"[^A-Za-zА-Яа-я0-9]", "", token)
        if len(compact) >= 12 and re.fullmatch(r"[0-9a-fA-F]+", compact):
            low_signal += 1
        elif len(compact) >= 9 and sum(ch.isdigit() for ch in compact) >= max(5, len(compact) // 2):
            low_signal += 1
    return low_signal > 0 and low_signal >= max(1, len(tokens) // 2)


def source_summary_looks_like_dump(body: str) -> bool:
    if len(body) > 1800:
        return True
    if "## Durable memory" not in body or "## Provenance" not in body:
        return True
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    return any(len(line) > 320 for line in lines)


def has_durable_context(body: str) -> bool:
    if len(body) < 80:
        return False
    markers = ("## Суть", "## Контекст", "## Как использовать", "## Durable memory")
    return any(marker in body for marker in markers)


def mark_existing_targets(project: str, items: list[ScribePlanItem]) -> None:
    try:
        project_dir = existing_project_dir(project)
    except Exception:
        return
    for item in items:
        if not item.target_path or item.destination == "exclude":
            continue
        if (project_dir / item.target_path).exists():
            item.selected = False
            item.operation = "existing_target"
            item.status = "existing"
            note = "Целевой узел уже существует; выбери: обновить узел, пропустить файл как дубль или создать связанный узел."
            if note not in item.safety_notes:
                item.safety_notes.append(note)


def package_is_inbox(package: dict[str, Any]) -> bool:
    origin = package.get("scribe_origin") or {}
    return isinstance(origin, dict) and origin.get("type") == "inbox"


def plan_item(package: dict[str, Any], category: str, text: str, evidence: dict[str, Any] | None = None) -> ScribePlanItem:
    project = str(package.get("project") or "Без проекта")
    folder, memory_type, confidence = DESTINATIONS.get(category, ("", "draft", "low"))
    excluded = category == EXCLUDED_CATEGORY
    text = normalized_item_body(category, text)
    title = item_title(text, category)
    item_id = stable_item_id(project, category, text)
    target_path = ""
    if not excluded and folder:
        code = project_code_for_package(package)
        target_path = str(Path("Память_Graph") / folder / memory_filename(code, title))
    notes = item_safety_notes(text, evidence, category)
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


def normalized_item_body(category: str, text: str) -> str:
    if category in {EXCLUDED_CATEGORY, "source_summary"} or "## " in text:
        return text
    title = item_title(text, category)
    return "\n".join([
        f"{title}: {text}",
        "",
        "## Durable memory",
        "",
        f"- {text}",
        "- Требует review перед тем, как считать это окончательным решением проекта.",
    ])


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
    links = [f"[[{code} - Индекс памяти]]"]
    lines = [
        "---",
        f"type: {memory_type}",
        "priority: 70",
        f"confidence: {item.confidence}",
        "status: active" if item.category != "open_questions" else "status: open",
        f"source: \"{escape_frontmatter(source)}\"",
        f"last_verified_at: {today}",
        "links: [" + ", ".join(f'"{link}"' for link in links) + "]",
        "---",
        "",
        f"# {code} - {item.title}",
        "",
        item.body,
        "",
        "## Связи",
        "",
        *[f"- {link}" for link in links],
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


def append_existing_node_update(target: Path, item: ScribePlanItem, package: dict[str, Any]) -> Path:
    today = datetime.now().strftime("%Y-%m-%d")
    marker = f"<!-- scribe-update:{item.id}:{package.get('run_id', '-')} -->"
    text = target.read_text(encoding="utf-8", errors="ignore")
    if marker in text:
        return target
    text = re.sub(r"^last_verified_at: .*$", f"last_verified_at: {today}", text, count=1, flags=re.MULTILINE)
    addition = "\n".join([
        "",
        f"## Дополнение Scribe {today}",
        "",
        marker,
        "",
        item.body,
        "",
        "### Provenance",
        "",
        f"- Scribe plan item: `{item.id}`",
        f"- Run: `{package.get('run_id', '-')}`",
        f"- Evidence: {item.evidence or '-'}",
        "",
        "### Safety",
        "",
    ])
    if item.safety_notes:
        addition += "\n".join(f"- {note}" for note in item.safety_notes) + "\n"
    else:
        addition += "- ПД и длинные цитаты не обнаружены в staged item.\n"
    target.write_text(text.rstrip() + "\n" + addition, encoding="utf-8")
    return target


def linked_plan_item(project_dir: Path, code: str, item: ScribePlanItem) -> ScribePlanItem:
    original = Path(item.target_path).stem
    item.title = f"{item.title} дополнение"
    item.operation = "create"
    item.status = "staged"
    item.selected = True
    item.target_path = str(unique_memory_path(project_dir, item.destination, memory_filename(code, item.title)))
    item.body = "\n".join([
        item.body.rstrip(),
        "",
        "## Связь с существующим узлом",
        "",
        f"- Дополняет [[{original}]], но сохранен отдельным узлом после ручного выбора в Scribe.",
    ])
    return item


def unique_memory_path(project_dir: Path, folder: str, filename: str) -> Path:
    base = Path("Память_Graph") / folder / filename
    if not (project_dir / base).exists():
        return base
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    for index in range(2, 100):
        candidate = Path("Память_Graph") / folder / f"{stem}-{index}{suffix}"
        if not (project_dir / candidate).exists():
            return candidate
    return Path("Память_Graph") / folder / f"{stem}-{datetime.now().strftime('%H%M%S')}{suffix}"


def apply_related_archives(project_dir: Path, plan_id: str, item: ScribePlanItem) -> list[str]:
    if item.operation not in {"merge", "archive"}:
        return []
    changed: list[str] = []
    archive_dir = project_dir / "Память_Graph" / "90_Archive" / f"{datetime.now().strftime('%Y-%m-%d')}-scribe-{plan_id}"
    for related in item.related_paths or []:
        related_path = project_dir / related
        if not related_path.exists() or not related_path.is_file():
            continue
        try:
            related_path.relative_to(project_dir / "Память_Graph")
        except ValueError:
            continue
        if "90_Archive" in related_path.relative_to(project_dir / "Память_Graph").parts:
            continue
        archive_dir.mkdir(parents=True, exist_ok=True)
        target = archive_dir / related_path.name
        shutil.move(str(related_path), str(target))
        changed.append(str(target))
    return changed


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


def item_safety_notes(text: str, evidence: dict[str, Any] | None, category: str = "") -> list[str]:
    notes: list[str] = []
    if category == "source_summary" and source_summary_looks_like_dump(text):
        notes.append("Похож на сырой dump: source_summary должен хранить provenance, а не длинный excerpt.")
    if evidence and evidence.get("status") != "confirmed":
        notes.append("Evidence не confirmed; item не должен становиться decision без проверки.")
    if looks_like_pii(text):
        notes.append("Возможный ПД-паттерн; item исключен из применения.")
    return notes


def notes_contains_blocker(notes: list[str]) -> bool:
    return any("ПД" in note or "сырой dump" in note for note in notes)


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


def memory_filename(code: str, title: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in (" ", "-", "_") else " " for ch in title.strip())
    safe = " ".join(safe.split())
    safe = safe[:80].strip() or "Контекст"
    return f"{code} - {safe}.md"


def yes_no(value: Any) -> str:
    return "да" if bool(value) else "нет"
