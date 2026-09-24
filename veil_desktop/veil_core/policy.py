from __future__ import annotations

import re


CONCRETE_PII_PATTERNS = [
    r"[\w.+-]+@[\w.-]+\.[A-Za-zА-Яа-я]{2,}",
    r"(?:\+7|8)?[\s(.-]*\d{3}[\s). -]*\d{3}[\s.-]*\d{2}[\s.-]*\d{2}",
    r"\bИНН\s*[:№#-]?\s*[0-9\- ]{6,24}\b",
    r"\b(?:КПП|ОГРН|СНИЛС)\s*[:№#-]?\s*[0-9\- ]{6,24}\b",
    r"\b(?:паспортн(?:ые|ых)?\s+данн(?:ые|ых)|серия\s+(?:и\s+)?номер\s+паспорта|номер\s+паспорта|паспорт)\s*(?:№|N|No|номер|\d)\s*[0-9\- ]{4,24}",
    r"\b(?:номер\s+(?:договора|контракта)|(?:договор|контракт)\s*(?:№|N|No|номер|\d))\s*[A-Za-zА-Яа-я0-9_./\\-]*\d[A-Za-zА-Яа-я0-9_./\\-]*\b",
    r"\b[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ][а-яё]+(?:вич|вна|ич|ична)\b",
]

TOPIC_PII_PATTERNS = [
    r"\b(?:персональн(?:ые|ых|ыми|ым)?\s+данн(?:ые|ых|ыми|ым)|ПДн?|ПИИ|PII)\b",
    r"\b(?:политик[аи]\s+обработки|согласие\s+на\s+обработку|обработка\s+персональных\s+данных)\b",
    r"\b(?:паспорт|паспортные данные|реквизиты|договор|контракт)\s+(?:клиента|участника|заявителя|собственника|пользователя)\b",
]


def detect_concrete_pii(text: str) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in CONCRETE_PII_PATTERNS)


def detect_possible_pii(text: str) -> bool:
    patterns = CONCRETE_PII_PATTERNS + TOPIC_PII_PATTERNS
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def initial_policy_notes() -> list[str]:
    return [
        "Исходные ПД не отправляются во внешний анализ.",
        "Перед использованием Codex/ChatGPT требуется ручное подтверждение очищенного пакета.",
    ]
