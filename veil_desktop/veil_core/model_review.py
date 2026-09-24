"""Optional, local-only review of text that Veil has already masked."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

MODEL = "qwen3.5:9b"
OLLAMA_URL = "http://127.0.0.1:11434"
MAX_REVIEW_CHARS = 7000
ALLOWED_CATEGORIES = frozenset({
    "PERSON", "PHONE", "EMAIL", "ADDRESS", "PASSPORT", "INN", "ID",
    "CONTRACT", "BANK", "OTHER",
})


@dataclass(frozen=True)
class ModelReview:
    completed: bool
    unresolved_pii: bool
    message: str


def _post(path: str, payload: dict[str, object], timeout: int) -> dict:
    request = urllib.request.Request(
        OLLAMA_URL + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(65_537)
    if len(raw) > 65_536:
        raise ValueError("Ответ локальной модели слишком велик.")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("Некорректный ответ локальной модели.")
    return result


def review_residual_pii(masked_text: str) -> ModelReview:
    if not masked_text.strip():
        return ModelReview(False, False, "Нет текста для дополнительной проверки.")
    if len(masked_text) > MAX_REVIEW_CHARS:
        return ModelReview(False, False, "Текст слишком длинный для проверки моделью; проверьте его вручную.")
    prompt = (
        "Проверь только приведённый уже очищенный текст на оставшиеся конкретные персональные данные. "
        "Не восстанавливай псевдонимы. Слова о персональных данных без конкретных значений не являются находками. "
        'Верни только JSON: {"unresolved_pii":false,"reason":"","categories":[]}. '
        "Если остаются конкретные данные, поставь unresolved_pii=true, дай краткую причину "
        "без копирования значений и категории из PERSON, PHONE, EMAIL, ADDRESS, PASSPORT, INN, ID, CONTRACT, BANK, OTHER.\n\n"
        "Очищенный текст:\n" + masked_text
    )
    try:
        response = _post("/api/generate", {
            "model": MODEL,
            "system": "Ты локальный проверяющий Veil. Возвращай только JSON.",
            "prompt": prompt,
            "format": "json",
            "stream": False,
            "think": False,
            "keep_alive": 0,
            "options": {"temperature": 0, "num_predict": 200},
        }, 90)
        if response.get("done") is not True or response.get("done_reason") in {"length", "max_tokens"}:
            raise ValueError("Проверка моделью не завершилась.")
        payload = json.loads(response.get("response", ""))
        if not isinstance(payload, dict) or set(payload) != {"unresolved_pii", "reason", "categories"}:
            raise ValueError("Некорректный ответ локальной модели.")
        if type(payload["unresolved_pii"]) is not bool or not isinstance(payload["reason"], str):
            raise ValueError("Некорректный ответ локальной модели.")
        if len(payload["reason"]) > 240 or not isinstance(payload["categories"], list):
            raise ValueError("Некорректный ответ локальной модели.")
        if any(not isinstance(item, str) or item not in ALLOWED_CATEGORIES for item in payload["categories"]):
            raise ValueError("Некорректный ответ локальной модели.")
        if payload["unresolved_pii"]:
            return ModelReview(True, True, "Модель отметила возможные оставшиеся данные. Проверьте текст вручную.")
        return ModelReview(True, False, "Дополнительная проверка завершена; просмотрите результат вручную.")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return ModelReview(False, False, "Модель не установлена. Загрузите её по инструкции, затем повторите проверку.")
        return ModelReview(False, False, "Дополнительная проверка не завершена. Проверьте текст вручную.")
    except urllib.error.URLError:
        return ModelReview(False, False, "Программа локальной модели недоступна. Запустите её или отключите дополнительную проверку.")
    except (OSError, json.JSONDecodeError, ValueError, TimeoutError):
        return ModelReview(False, False, "Дополнительная проверка не завершена. Проверьте текст вручную.")


def stop_model() -> bool:
    """Unload only Veil's model; do not stop the shared Ollama service."""
    try:
        result = _post("/api/generate", {"model": MODEL, "prompt": "", "keep_alive": 0}, 15)
        return result.get("done") is True
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError, TimeoutError):
        return False
