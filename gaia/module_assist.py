from __future__ import annotations

import json
from typing import Any

from .local_llm import TASK_PROJECT_HEALTH, TASK_SCRIBE_CLASSIFIER, TASK_VEIL_REVIEW
from .lore_rerank import call_local_llm_with_deadline, parse_json_object


ALLOWED_PII_CATEGORIES = {"PERSON", "PHONE", "EMAIL", "ADDRESS", "PASSPORT", "INN", "ID", "CONTRACT", "BANK", "OTHER"}
ALLOWED_PROJECT_SEVERITY = {"info", "warning", "critical"}
SCRIBE_CATEGORIES = ("decisions", "rules", "risks", "open_questions", "technical_facts", "exclude")


def call_lm_studio_with_deadline(prompt: str, timeout: int, system: str, task: str = TASK_SCRIBE_CLASSIFIER) -> dict[str, Any]:
    return call_local_llm_with_deadline(prompt, timeout, system, task=task)


def review_masking_with_local_llm(label: str, masked_text: str, rule_summary: dict[str, Any], timeout: int) -> dict[str, Any] | None:
    if not masked_text.strip():
        return None
    prompt = "\n".join([
        "Проверь локально, похож ли уже замаскированный текст на содержащий остаточные ПД.",
        "",
        "Жесткие правила:",
        "- Верни только JSON-объект без markdown.",
        "- Не восстанавливай и не угадывай исходные ПД.",
        "- Не снижай риск, найденный правилами. Можно только отметить дополнительный риск.",
        "- unresolved_pii=true только если в masked_text видны похожие на ПД остатки.",
        "- Не ставь unresolved_pii=true только из-за слов о политике ПД, персональных данных, паспортах или договорах без конкретных значений.",
        "- categories выбирай только из разрешенного списка.",
        "",
        'Формат: {"unresolved_pii":false,"reason":"","categories":[]}',
        "",
        f"# Label\n{label}",
        "",
        "# Rule summary",
        json.dumps(rule_summary, ensure_ascii=False),
        "",
        "# Masked text excerpt",
        masked_text[:6000],
    ])
    result = call_lm_studio_with_deadline(
        prompt,
        timeout,
        "Ты локальный safety reviewer Gaia Veil. Ты не отвечаешь пользователю и не раскрываешь ПД.",
        task=TASK_VEIL_REVIEW,
    )
    if not result.get("ok"):
        return None
    payload = parse_json_object(str(result.get("answer") or ""))
    if payload is None:
        return None
    return normalize_veil_review(payload)


def classify_scribe_candidates_with_local_llm(package: dict[str, Any], timeout: int) -> dict[str, list[str]] | None:
    prompt = "\n".join([
        "Классифицируй кандидаты для ручного обновления проектной памяти Gaia.",
        "",
        "Жесткие правила:",
        "- Верни только JSON-объект без markdown.",
        "- Не добавляй факты за пределами prompt.",
        "- Не включай ПД, телефоны, email, адреса, паспортные данные и длинные цитаты.",
        "- Это черновая классификация, не запись в память.",
        "- Формулируй кандидаты как будущие memory nodes, а не как имена файлов.",
        "- Название будущего узла должно быть коротким: максимум 3 смысловых слова.",
        "- Если несколько материалов говорят об одном контексте, предложи один обобщенный кандидат, а не дубли.",
        "- Source-summary должен хранить provenance и durable context, но не сырой transcript/OCR/table dump.",
        "",
        'Формат: {"decisions":[],"rules":[],"risks":[],"open_questions":[],"technical_facts":[],"exclude":[]}',
        "",
        "# Project",
        str(package.get("project") or "-"),
        "",
        "# Safe analytical package excerpt",
        scribe_classifier_excerpt(package),
    ])
    result = call_lm_studio_with_deadline(
        prompt,
        timeout,
        "Ты локальный Scribe classifier Gaia. Ты структурируешь только безопасные кандидаты в память.",
        task=TASK_SCRIBE_CLASSIFIER,
    )
    if not result.get("ok"):
        return None
    payload = parse_json_object(str(result.get("answer") or ""))
    if payload is None:
        return None
    return normalize_scribe_payload(payload)


def diagnose_project_health_with_local_llm(summary: dict[str, Any], timeout: int) -> list[dict[str, str]]:
    prompt = "\n".join([
        "Оцени здоровье проектной памяти Gaia по структурной сводке.",
        "",
        "Жесткие правила:",
        "- Верни только JSON-объект без markdown.",
        "- Не читай и не придумывай содержимое файлов.",
        "- Не предлагай удаление исходников.",
        "- severity может быть только info, warning или critical.",
        "- Каждая рекомендация должна опираться на summary.",
        "",
        'Формат: {"diagnostics":[{"severity":"warning","title":"...","detail":"...","action":"..."}]}',
        "",
        "# Summary",
        json.dumps(summary, ensure_ascii=False, indent=2),
    ])
    result = call_lm_studio_with_deadline(
        prompt,
        timeout,
        "Ты локальный диагност Gaia Project Registry. Ты анализируешь только переданную структурную сводку.",
        task=TASK_PROJECT_HEALTH,
    )
    if not result.get("ok"):
        return []
    payload = parse_json_object(str(result.get("answer") or ""))
    if payload is None:
        return []
    return normalize_project_diagnostics(payload)


def normalize_veil_review(payload: dict[str, Any]) -> dict[str, Any] | None:
    unresolved = payload.get("unresolved_pii")
    if not isinstance(unresolved, bool):
        return None
    reason = clean_text(str(payload.get("reason") or ""), 240)
    categories = []
    raw_categories = payload.get("categories")
    if isinstance(raw_categories, list):
        for item in raw_categories:
            category = str(item).strip().upper()
            if category in ALLOWED_PII_CATEGORIES and category not in categories:
                categories.append(category)
    if unresolved and not reason:
        reason = "Локальная LLM-проверка Veil отметила остаточный риск ПД."
    return {"unresolved_pii": unresolved, "reason": reason, "categories": categories[:8]}


def normalize_scribe_payload(payload: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for category in SCRIBE_CATEGORIES:
        result[category] = clean_list(payload.get(category), 8, 220)
    return result


def normalize_project_diagnostics(payload: dict[str, Any]) -> list[dict[str, str]]:
    raw = payload.get("diagnostics")
    if not isinstance(raw, list):
        return []
    diagnostics: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        severity = str(item.get("severity") or "").strip().lower()
        if severity not in ALLOWED_PROJECT_SEVERITY:
            continue
        title = clean_text(str(item.get("title") or ""), 90)
        detail = clean_text(str(item.get("detail") or ""), 240)
        action = clean_text(str(item.get("action") or ""), 180)
        if not title or not detail:
            continue
        diagnostics.append({"severity": severity, "title": title, "detail": detail, "action": action})
        if len(diagnostics) >= 8:
            break
    return diagnostics


def scribe_classifier_excerpt(package: dict[str, Any], limit: int = 12000) -> str:
    parts: list[str] = []
    instruction = str(package.get("masked_query") or package.get("prompt") or "").strip()
    if instruction:
        parts.extend(["## Запрос", compact_text(instruction, 2400)])
    files = package.get("files") or []
    if isinstance(files, list):
        per_file = max(1800, limit // max(1, len(files)))
        for file_info in files:
            if not isinstance(file_info, dict):
                continue
            name = clean_text(str(file_info.get("name") or "Файл"), 120)
            note = clean_text(str(file_info.get("extraction_note") or ""), 180)
            text = str(file_info.get("masked_text") or "").strip()
            if not text:
                continue
            heading = f"## Файл: {name}"
            if note:
                heading += f"\nИзвлечение: {note}"
            parts.extend([heading, focused_scribe_file_excerpt(text, per_file)])
    excerpt = "\n\n".join(parts).strip()
    if not excerpt:
        excerpt = str(package.get("prompt") or "").strip()
    return compact_text(excerpt, limit)


def compact_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    notice = "\n[...]\n"
    available = max(0, limit - len(notice))
    head = available // 2
    tail = available - head
    return f"{text[:head].rstrip()}{notice}{text[-tail:].lstrip()}"


def focused_scribe_file_excerpt(text: str, limit: int) -> str:
    keywords = (
        "архитект",
        "скуд",
        "face id",
        "синхронизатор",
        "telegrambot",
        "telegram",
        "телеграм",
        "телеграмм",
        "бот",
        "битрикс",
        "база данных",
        "бд",
        "мастер-систем",
        "отчет",
        "отчетность",
        "superset",
        "планируем",
        "текущ",
    )
    lowered = text.lower()
    windows: list[str] = []
    seen: set[tuple[int, int]] = set()
    window_size = max(700, min(1400, limit // 4))
    for keyword in keywords:
        start = 0
        matches = 0
        while True:
            index = lowered.find(keyword, start)
            if index < 0:
                break
            left = max(0, index - window_size // 2)
            right = min(len(text), index + window_size // 2)
            key = (left, right)
            if key not in seen:
                windows.append(text[left:right].strip())
                seen.add(key)
                matches += 1
            start = index + len(keyword)
            if matches >= 1 or len("\n[...]\n".join(windows)) >= limit:
                break
        if len("\n[...]\n".join(windows)) >= limit:
            break
    if windows:
        return compact_text("\n[...]\n".join(windows), limit)
    return compact_text(text, limit)


def clean_list(raw: Any, limit: int, max_len: int) -> list[str]:
    if not isinstance(raw, list):
        return []
    values: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        value = clean_text(item, max_len)
        if value and value not in values:
            values.append(value)
        if len(values) >= limit:
            break
    return values


def clean_text(value: str, max_len: int) -> str:
    cleaned = " ".join(value.strip().split())
    if any(ch in cleaned for ch in "{}[]<>`|"):
        return ""
    if len(cleaned) > max_len:
        return cleaned[: max_len - 3].rstrip() + "..."
    return cleaned
