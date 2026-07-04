from __future__ import annotations

import json
import queue
import re
import threading
from typing import Any

from .local_llm import run_lm_studio_prompt


MAX_EXCERPT_CHARS = 1800


def rerank_with_local_llm(
    query: str,
    profile_text: str,
    candidates: list[dict[str, Any]],
    max_ids: int,
    timeout: int,
) -> list[str] | None:
    if not query.strip() or not candidates or max_ids <= 0:
        return None
    allowed_ids = {str(item.get("id", "")) for item in candidates if item.get("id")}
    if not allowed_ids:
        return None
    prompt = build_rerank_prompt(query, profile_text, candidates, max_ids)
    result = call_lm_studio_with_deadline(
        prompt,
        timeout,
        (
            "Ты локальный rerank-модуль Gaia Lore. "
            "Ты не отвечаешь пользователю и не создаешь новые факты. "
            "Твоя единственная задача - выбрать id из переданного списка."
        ),
    )
    if not result.get("ok"):
        return None
    payload = parse_json_object(str(result.get("answer") or ""))
    if payload is None:
        return None
    selected = normalize_selected_ids(payload)
    if not selected:
        return None
    if any(source_id not in allowed_ids for source_id in selected):
        return None
    deduped: list[str] = []
    for source_id in selected:
        if source_id not in deduped:
            deduped.append(source_id)
        if len(deduped) >= max_ids:
            break
    return deduped or None


def call_lm_studio_with_deadline(prompt: str, timeout: int, system: str) -> dict[str, Any]:
    deadline = max(1, int(timeout or 1))
    result_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

    def worker() -> None:
        result = run_lm_studio_prompt(
            prompt,
            system,
            timeout=deadline,
            temperature=0.0,
        )
        try:
            result_queue.put_nowait(result)
        except queue.Full:
            pass

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        return result_queue.get(timeout=deadline)
    except queue.Empty:
        return {"ok": False, "status": "timeout", "error": f"Lore semantic rerank timed out after {deadline} seconds."}


def build_rerank_prompt(query: str, profile_text: str, candidates: list[dict[str, Any]], max_ids: int) -> str:
    safe_candidates = []
    for item in candidates:
        safe_candidates.append({
            "id": item.get("id", ""),
            "heading": item.get("heading", ""),
            "scope": item.get("scope", ""),
            "path_hint": item.get("path_hint", ""),
            "score": item.get("score", 0),
            "matched_terms": item.get("matched_terms", []),
            "excerpt": str(item.get("excerpt", ""))[:MAX_EXCERPT_CHARS],
        })
    return "\n".join([
        "Выбери наиболее релевантные разделы памяти Gaia Lore.",
        "",
        "Жесткие правила:",
        "- Возвращай только JSON-объект без markdown.",
        "- Нельзя добавлять новые id, источники, факты или заголовки.",
        "- Можно выбирать только id из `candidates`.",
        "- Если раздел звучит похоже, но не подтверждает тему запроса, не выбирай его.",
        "- Если релевантных разделов нет, верни пустой массив.",
        "- Не переноси сведения из соседних MVP, похожих рисков или общих регламентов на конкретную тему.",
        "- Source-summary, решения и core-узлы предпочтительнее сырой переписки при равной релевантности.",
        "",
        "Формат ответа:",
        '{"selected_ids":["id1","id2"],"notes":"short local reason"}',
        "",
        f"Максимум id: {max_ids}",
        "",
        "# Запрос",
        query or "Запрос пуст.",
        "",
        "# Профиль задачи",
        profile_text or "-",
        "",
        "# Candidates JSON",
        json.dumps(safe_candidates, ensure_ascii=False, indent=2),
    ])


def parse_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped).strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if not match:
            return None
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return payload if isinstance(payload, dict) else None


def normalize_selected_ids(payload: dict[str, Any]) -> list[str]:
    raw = payload.get("selected_ids")
    if raw is None:
        raw = payload.get("ids")
    if not isinstance(raw, list):
        return []
    selected: list[str] = []
    for item in raw:
        if isinstance(item, str):
            value = item.strip()
        elif isinstance(item, dict):
            value = str(item.get("id", "")).strip()
        else:
            value = ""
        if value:
            selected.append(value)
    return selected
