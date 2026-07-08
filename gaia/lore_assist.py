from __future__ import annotations

import json
from typing import Any

from .local_llm import TASK_LORE_GAP_DETECTOR, TASK_LORE_QUERY_REWRITE
from .lore_rerank import call_local_llm_with_deadline, parse_json_object


ALLOWED_GAP_STATUSES = {"ok", "partial", "weak", "none"}
MAX_REWRITE_TERMS = 16
MAX_GAP_NOTES = 4


def call_lm_studio_with_deadline(prompt: str, timeout: int, system: str, task: str = TASK_LORE_QUERY_REWRITE) -> dict[str, Any]:
    return call_local_llm_with_deadline(prompt, timeout, system, task=task)


def rewrite_query_terms_with_local_llm(
    query: str,
    project: str,
    profile_text: str,
    file_hints: list[str],
    timeout: int,
) -> list[str]:
    if not query.strip():
        return []
    prompt = build_query_rewrite_prompt(query, project, profile_text, file_hints)
    result = call_lm_studio_with_deadline(
        prompt,
        timeout,
        (
            "Ты локальный помощник Gaia Lore для расширения поисковых терминов. "
            "Ты не отвечаешь пользователю и не создаешь факты."
        ),
        task=TASK_LORE_QUERY_REWRITE,
    )
    if not result.get("ok"):
        return []
    payload = parse_json_object(str(result.get("answer") or ""))
    if payload is None:
        return []
    return normalize_rewrite_terms(payload)


def detect_gap_with_local_llm(
    query: str,
    sources: list[dict[str, Any]],
    focus_terms: list[str],
    timeout: int,
) -> dict[str, Any] | None:
    if not query.strip():
        return None
    prompt = build_gap_prompt(query, sources, focus_terms)
    result = call_lm_studio_with_deadline(
        prompt,
        timeout,
        (
            "Ты локальный помощник Gaia Lore для диагностики покрытия retrieval. "
            "Ты не отвечаешь пользователю и не добавляешь факты."
        ),
        task=TASK_LORE_GAP_DETECTOR,
    )
    if not result.get("ok"):
        return None
    payload = parse_json_object(str(result.get("answer") or ""))
    if payload is None:
        return None
    return normalize_gap_payload(payload)


def build_query_rewrite_prompt(query: str, project: str, profile_text: str, file_hints: list[str]) -> str:
    return "\n".join([
        "Расширь пользовательский запрос в короткие поисковые термины для Gaia Lore.",
        "",
        "Жесткие правила:",
        "- Верни только JSON-объект без markdown.",
        "- Не отвечай на запрос пользователя.",
        "- Не добавляй факты, даты, имена, выводы или источники.",
        "- Термины нужны только для retrieval по уже существующей памяти.",
        "- Сохраняй короткие системные аббревиатуры в верхнем регистре: БФ, ДО, ИИ, CRM.",
        "- Если полезных расширений нет, верни пустой массив.",
        "",
        "Формат ответа:",
        '{"terms":["термин1","термин2"],"notes":"short local reason"}',
        "",
        f"# Проект\n{project or '-'}",
        "",
        f"# Запрос\n{query}",
        "",
        f"# Профиль\n{profile_text or '-'}",
        "",
        "# Имена приложенных файлов",
        json.dumps(file_hints[:12], ensure_ascii=False),
    ])


def build_gap_prompt(query: str, sources: list[dict[str, Any]], focus_terms: list[str]) -> str:
    safe_sources = []
    for source in sources[:12]:
        safe_sources.append({
            "heading": source.get("heading", ""),
            "scope": source.get("scope", ""),
            "score": source.get("score", 0),
            "matched_terms": source.get("matched_terms", []),
        })
    return "\n".join([
        "Оцени покрытие запроса выбранными источниками Gaia Lore.",
        "",
        "Жесткие правила:",
        "- Верни только JSON-объект без markdown.",
        "- Не отвечай на запрос пользователя.",
        "- Не добавляй факты или источники.",
        "- Оцени только достаточность уже выбранных sources.",
        "- status может быть только: ok, partial, weak, none.",
        "- notes должны описывать только пробелы покрытия или причину уверенности.",
        "",
        "Формат ответа:",
        '{"status":"ok","notes":["short note"],"missing_terms":["term"]}',
        "",
        f"# Запрос\n{query}",
        "",
        "# Focus terms",
        json.dumps(focus_terms[:24], ensure_ascii=False),
        "",
        "# Sources JSON",
        json.dumps(safe_sources, ensure_ascii=False, indent=2),
    ])


def normalize_rewrite_terms(payload: dict[str, Any]) -> list[str]:
    raw = payload.get("terms")
    if not isinstance(raw, list):
        return []
    terms: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        value = " ".join(item.strip().split())
        if not value or len(value) > 80:
            continue
        if any(ch in value for ch in "{}[]<>`|"):
            continue
        if value not in terms:
            terms.append(value)
        if len(terms) >= MAX_REWRITE_TERMS:
            break
    return terms


def normalize_gap_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    status = str(payload.get("status") or "").strip().lower()
    if status not in ALLOWED_GAP_STATUSES:
        return None
    notes = normalize_string_list(payload.get("notes"), MAX_GAP_NOTES, 180)
    missing_terms = normalize_string_list(payload.get("missing_terms"), 12, 80)
    return {"status": status, "notes": notes, "missing_terms": missing_terms}


def normalize_string_list(raw: Any, limit: int, max_len: int) -> list[str]:
    if not isinstance(raw, list):
        return []
    values: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        value = " ".join(item.strip().split())
        if not value or len(value) > max_len:
            continue
        if any(ch in value for ch in "{}[]<>`|"):
            continue
        if value not in values:
            values.append(value)
        if len(values) >= limit:
            break
    return values
