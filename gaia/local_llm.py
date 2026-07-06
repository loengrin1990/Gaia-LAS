from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit
from typing import Any

from .config import SETTINGS

DEFAULT_LOCAL_PROMPT_CHAR_LIMIT = 16000
DEFAULT_LOCAL_MAX_TOKENS = 1200
LOCAL_CONTEXT_MARKER = "# Эффективный контекст, выбранный Lore\n"
LOCAL_SOURCES_MARKER = "\n# Источники выбора Lore\n"
STRUCTURED_LOCAL_SYSTEM = (
    "Ты локальный аналитик. Не запрашивай ПД во внешний контур. "
    "Верни только JSON object без markdown-блока и без текста вокруг. "
    "Схема: {"
    "\"summary\": string, "
    "\"key_observations\": string[], "
    "\"risks\": [{\"title\": string, \"level\": \"high|medium|low\", \"reason\": string, \"mitigation\": string}], "
    "\"open_questions\": string[], "
    "\"next_steps\": string[]"
    "}. "
    "Если данных недостаточно, прямо укажи это в summary и next_steps. "
    "Не добавляй факты за пределами выбранного контекста."
)


def lm_studio_models_endpoint() -> str:
    endpoint = SETTINGS.lm_studio_endpoint
    parts = urlsplit(endpoint)
    path = parts.path
    if "/chat/completions" in path:
        path = path.rsplit("/chat/completions", 1)[0] + "/models"
    else:
        path = "/v1/models"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def is_timeout_error(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        return isinstance(reason, (TimeoutError, socket.timeout))
    return False


def check_lm_studio(timeout: float = 1.5) -> dict[str, Any]:
    request = urllib.request.Request(lm_studio_models_endpoint(), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        models = data.get("data", [])
        model_names = [str(item.get("id", "")) for item in models if isinstance(item, dict) and item.get("id")]
        return {
            "available": True,
            "status": "available",
            "message": "LM Studio доступна.",
            "endpoint": SETTINGS.lm_studio_endpoint,
            "models": model_names,
        }
    except Exception as exc:
        if is_timeout_error(exc):
            return {
                "available": False,
                "status": "timeout",
                "message": "LM Studio не подтвердила готовность за короткий health-check. Сервер может быть запущен, но занят моделью.",
                "endpoint": SETTINGS.lm_studio_endpoint,
                "error": str(exc),
            }
        return {
            "available": False,
            "status": "unavailable",
            "message": "LM Studio не отвечает. Запусти LM Studio или используй внешний маршрут после безопасного review.",
            "endpoint": SETTINGS.lm_studio_endpoint,
            "error": str(exc),
        }


def run_lm_studio_prompt(prompt: str, system: str, timeout: float = 180, temperature: float = 0.2) -> dict[str, Any]:
    local_prompt, prompt_compacted = compact_prompt_for_local_model(prompt)
    payload = {
        "model": "local-model",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": local_prompt},
        ],
        "temperature": temperature,
        "stream": False,
    }
    max_tokens = local_llm_max_tokens()
    if max_tokens > 0:
        payload["max_tokens"] = max_tokens
    request = urllib.request.Request(
        SETTINGS.lm_studio_endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return {
            "ok": True,
            "answer": content,
            "raw": data,
            "prompt_chars_sent": len(local_prompt),
            "prompt_compacted": prompt_compacted,
        }
    except urllib.error.HTTPError as exc:
        details = read_http_error_body(exc)
        hint = " LM Studio отклонила запрос; Gaia уже сократила prompt для локальной модели." if prompt_compacted else ""
        return {
            "ok": False,
            "status": "bad_request" if exc.code == 400 else "http_error",
            "error": f"LM Studio вернула HTTP {exc.code}: {details or exc.reason}.{hint}",
            "prompt_chars_sent": len(local_prompt),
            "prompt_compacted": prompt_compacted,
        }
    except Exception as exc:
        if is_timeout_error(exc):
            return {
                "ok": False,
                "status": "timeout",
                "error": f"LM Studio не успела завершить локальный ответ за {int(timeout)} секунд. Сервер может быть запущен, генерация продолжается или модель занята.",
                "prompt_chars_sent": len(local_prompt),
                "prompt_compacted": prompt_compacted,
            }
        return {
            "ok": False,
            "status": "unavailable",
            "error": f"LM Studio недоступен: {exc}" if isinstance(exc, urllib.error.URLError) else str(exc),
            "prompt_chars_sent": len(local_prompt),
            "prompt_compacted": prompt_compacted,
        }


def run_lm_studio(prompt: str) -> dict[str, Any]:
    result = run_lm_studio_prompt(
        prompt,
        STRUCTURED_LOCAL_SYSTEM,
        timeout=180,
        temperature=0.2,
    )
    if not result.get("ok"):
        return result
    structured = normalize_structured_answer(parse_json_object(str(result.get("answer") or "")))
    if structured:
        result["structured_answer"] = structured
        result["answer"] = structured_answer_to_text(structured)
    return result


def compact_prompt_for_local_model(prompt: str, limit: int | None = None) -> tuple[str, bool]:
    if limit is None:
        limit = local_prompt_char_limit()
    if len(prompt) <= limit:
        return prompt, False
    prefix, separator, rest = prompt.partition(LOCAL_CONTEXT_MARKER)
    if not separator:
        return compact_by_head_and_tail(prompt, limit), True
    memory, sources_separator, suffix = rest.partition(LOCAL_SOURCES_MARKER)
    if not sources_separator:
        return compact_by_head_and_tail(prompt, limit), True
    fixed = f"{prefix}{separator}"
    suffix = f"{sources_separator}{suffix}"
    notice = "\n\n[Gaia сократила эффективный контекст для локальной модели; полный prompt сохранен в Диагностике.]\n"
    available = limit - len(fixed) - len(suffix) - len(notice)
    if available < 4000:
        return compact_by_head_and_tail(prompt, limit), True
    compacted_memory = memory[:available].rstrip()
    return f"{fixed}{compacted_memory}{notice}{suffix}", True


def compact_by_head_and_tail(text: str, limit: int) -> str:
    notice = "\n\n[Gaia сократила prompt для локальной модели; полный prompt сохранен в Диагностике.]\n\n"
    available = max(0, limit - len(notice))
    head = available // 2
    tail = available - head
    return f"{text[:head].rstrip()}{notice}{text[-tail:].lstrip()}"


def local_prompt_char_limit() -> int:
    if SETTINGS is None:
        return DEFAULT_LOCAL_PROMPT_CHAR_LIMIT
    return SETTINGS.local_llm_prompt_char_limit or DEFAULT_LOCAL_PROMPT_CHAR_LIMIT


def local_llm_max_tokens() -> int:
    if SETTINGS is None:
        return DEFAULT_LOCAL_MAX_TOKENS
    return SETTINGS.local_llm_max_tokens


def parse_json_object(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def normalize_structured_answer(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None
    summary = clean_text(payload.get("summary"))
    observations = clean_list(payload.get("key_observations"))
    risks = clean_risks(payload.get("risks"))
    questions = clean_list(payload.get("open_questions"))
    steps = clean_list(payload.get("next_steps"))
    if not any([summary, observations, risks, questions, steps]):
        return None
    return {
        "summary": summary,
        "key_observations": observations,
        "risks": risks,
        "open_questions": questions,
        "next_steps": steps,
    }


def clean_text(value: Any, limit: int = 700) -> str:
    if value is None:
        return ""
    text = " ".join(str(value).split())
    return text[:limit]


def clean_list(value: Any, limit: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    items = [clean_text(item) for item in value]
    return [item for item in items if item][:limit]


def clean_risks(value: Any, limit: int = 8) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    risks = []
    for item in value[:limit]:
        if not isinstance(item, dict):
            continue
        title = clean_text(item.get("title"), 160)
        reason = clean_text(item.get("reason"), 500)
        mitigation = clean_text(item.get("mitigation"), 400)
        level = clean_text(item.get("level"), 20).lower()
        if level not in {"high", "medium", "low"}:
            level = "medium"
        if title or reason:
            risks.append({
                "title": title or "Риск",
                "level": level,
                "reason": reason,
                "mitigation": mitigation,
            })
    return risks


def structured_answer_to_text(answer: dict[str, Any]) -> str:
    parts = []
    if answer.get("summary"):
        parts.extend(["Краткий вывод", str(answer["summary"])])
    for title, key in [
        ("Ключевые наблюдения", "key_observations"),
        ("Открытые вопросы", "open_questions"),
        ("Следующие шаги", "next_steps"),
    ]:
        items = answer.get(key) or []
        if items:
            parts.extend([title, *[f"- {item}" for item in items]])
    risks = answer.get("risks") or []
    if risks:
        parts.append("Риски")
        for risk in risks:
            line = f"- {risk.get('title', 'Риск')} ({risk.get('level', 'medium')})"
            if risk.get("reason"):
                line += f": {risk['reason']}"
            if risk.get("mitigation"):
                line += f" Следующий шаг: {risk['mitigation']}"
            parts.append(line)
    return "\n\n".join(parts)


def read_http_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read(4000).decode("utf-8", errors="replace").strip()
    except Exception:
        body = ""
    if not body:
        return str(exc.reason or "").strip()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body[:700]
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)[:700]
        if error:
            return str(error)[:700]
    return body[:700]
