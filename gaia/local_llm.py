from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit
from typing import Any

from .config import SETTINGS

LOCAL_PROMPT_CHAR_LIMIT = 42000
LOCAL_CONTEXT_MARKER = "# Эффективный контекст, выбранный Lore\n"
LOCAL_SOURCES_MARKER = "\n# Источники выбора Lore\n"


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
    return run_lm_studio_prompt(
        prompt,
        "Ты локальный аналитик. Не запрашивай ПД во внешний контур.",
        timeout=180,
        temperature=0.2,
    )


def compact_prompt_for_local_model(prompt: str, limit: int = LOCAL_PROMPT_CHAR_LIMIT) -> tuple[str, bool]:
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
