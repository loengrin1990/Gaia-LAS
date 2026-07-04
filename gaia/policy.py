from __future__ import annotations

import re


def detect_possible_pii(text: str) -> bool:
    patterns = [
        r"[\w.+-]+@[\w.-]+\.[A-Za-zА-Яа-я]{2,}",
        r"(?:\+7|8)?[\s(.-]*\d{3}[\s). -]*\d{3}[\s.-]*\d{2}[\s.-]*\d{2}",
        r"\b(?:ИНН|КПП|ОГРН|СНИЛС|паспорт|договор|контракт)\b",
        r"\b[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ][а-яё]+(?:вич|вна|ич|ична)\b",
    ]
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def initial_policy_notes() -> list[str]:
    return [
        "Исходные ПД не отправляются во внешний анализ.",
        "Перед использованием Codex/ChatGPT требуется ручное подтверждение очищенного пакета.",
    ]

