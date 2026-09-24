"""Local copy of the rule-based helper used by Gaia Veil.

Project-specific paths and file-writing commands were deliberately excluded.
"""
from __future__ import annotations
import re
from collections import Counter
from dataclasses import dataclass, field

@dataclass
class MaskResult:
    text: str
    counts: Counter = field(default_factory=Counter)
    samples: dict[str, list[str]] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


class PrivacyMasker:
    def __init__(self) -> None:
        self.counters: Counter = Counter()
        self.maps: dict[str, dict[str, str]] = {
            "PERSON": {},
            "EMAIL": {},
            "PHONE": {},
            "ID": {},
            "CONTRACT": {},
            "REQUEST": {},
            "INVOICE": {},
            "LINK": {},
            "CLIENT": {},
            "VENDOR": {},
            "AMOUNT": {},
        }

    def placeholder(self, category: str, value: str) -> str:
        key = normalize_value(value)
        existing = self.maps.setdefault(category, {}).get(key)
        if existing:
            return existing
        self.counters[category] += 1
        token = f"[{category}_{self.counters[category]}]"
        self.maps[category][key] = token
        return token

    def apply(self, text: str) -> MaskResult:
        result = MaskResult(text=text)

        rules = [
            ("EMAIL", re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-zА-Яа-я]{2,}(?![\w.-])")),
            ("PHONE", re.compile(r"(?<!\d)(?:\+7|8)?[\s(.-]*\d{3}[\s). -]*\d{3}[\s.-]*\d{2}[\s.-]*\d{2}(?!\d)")),
            ("ID", re.compile(r"\b(?:ИНН|КПП|ОГРН|СНИЛС|паспорт|паспортные данные)\s*[:№#-]?\s*[0-9\- ]{6,24}\b", re.IGNORECASE)),
            ("ID", re.compile(r"\b\d{3}-\d{3}-\d{3}\s?\d{2}\b")),
            ("CONTRACT", re.compile(r"\b(?:договор|контракт|ДС|доп\.?\s*соглашение)\s*(?:№|N|#)\s*[A-Za-zА-Яа-я0-9_./\\-]{2,}\b", re.IGNORECASE)),
            ("CONTRACT", re.compile(r"\b(?:договор|контракт|ДС|доп\.?\s*соглашение)\s+[A-Za-zА-Яа-я0-9_./\\-]*\d[A-Za-zА-Яа-я0-9_./\\-]*\b", re.IGNORECASE)),
            ("REQUEST", re.compile(r"\b(?:заявка|заявление|обращение|тикет|задача)\s*(?:№|N|#)\s*[A-Za-zА-Яа-я0-9_./\\-]{2,}\b", re.IGNORECASE)),
            ("REQUEST", re.compile(r"\b(?:заявка|заявление|обращение|тикет|задача)\s+[A-Za-zА-Яа-я0-9_./\\-]*\d[A-Za-zА-Яа-я0-9_./\\-]*\b", re.IGNORECASE)),
            ("INVOICE", re.compile(r"\b(?:счет|сч[её]т|инвойс|акт)\s*(?:№|N|#)\s*[A-Za-zА-Яа-я0-9_./\\-]{2,}\b", re.IGNORECASE)),
            ("INVOICE", re.compile(r"\b(?:счет|сч[её]т|инвойс|акт)\s+[A-Za-zА-Яа-я0-9_./\\-]*\d[A-Za-zА-Яа-я0-9_./\\-]*\b", re.IGNORECASE)),
            ("LINK", re.compile(r"\b(?:https?://|www\.)\S+")),
            ("LINK", re.compile(r"\b(?:[A-Za-z]:\\|smb://|afp://|file://|/Users/|/Volumes/)\S+")),
            ("CLIENT", re.compile(r"\b(?:ООО|АО|ПАО|ИП|ЗАО|НКО)\s+(?:[«\"][^»\"\n]{2,80}[»\"]|[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9_-]{1,40}(?:\s+[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9_-]{1,40}){0,3})")),
            ("PERSON", re.compile(r"\b[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ][а-яё]+(?:вич|вна|ич|ична)\b")),
            ("PERSON", re.compile(r"\b[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.")),
            ("PERSON", re.compile(r"\b[А-ЯЁ]\.\s?[А-ЯЁ]\.\s?[А-ЯЁ][а-яё]{2,}\b")),
        ]

        masked = text
        for category, pattern in rules:
            masked = self.replace_pattern(masked, pattern, category, result)

        masked = self.mask_sensitive_amounts(masked, result)
        result.text = masked
        return result

    def replace_pattern(self, text: str, pattern: re.Pattern, category: str, result: MaskResult) -> str:
        def repl(match: re.Match) -> str:
            value = match.group(0)
            token = self.placeholder(category, value)
            result.counts[category] += 1
            add_sample(result, category, value, token)
            return token

        return pattern.sub(repl, text)

    def mask_sensitive_amounts(self, text: str, result: MaskResult) -> str:
        money_pattern = re.compile(
            r"(?P<prefix>.{0,45})(?P<amount>(?:\d{1,3}(?:[ .]\d{3})+|\d+)(?:[,.]\d+)?\s*(?:руб(?:\.|лей|ля|ль)?|₽|млн\s*руб(?:\.|лей)?|тыс\.\s*руб\.?))",
            re.IGNORECASE | re.DOTALL,
        )
        sensitive_words = re.compile(
            r"договор|контракт|счет|сч[её]т|оплат|платеж|клиент|подрядчик|поставщик|контрагент|стоимость работ|цена договора|сумма договора",
            re.IGNORECASE,
        )

        def repl(match: re.Match) -> str:
            prefix = match.group("prefix")
            amount = match.group("amount")
            if not sensitive_words.search(prefix):
                return match.group(0)
            token = self.placeholder("AMOUNT", amount)
            result.counts["AMOUNT"] += 1
            add_sample(result, "AMOUNT", amount, token)
            return prefix + token

        return money_pattern.sub(repl, text)


def normalize_value(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).lower().replace("ё", "е")


def add_sample(result: MaskResult, category: str, value: str, token: str) -> None:
    samples = result.samples.setdefault(category, [])
    clean_value = re.sub(r"\s+", " ", value.strip())
    if len(clean_value) > 80:
        clean_value = clean_value[:77] + "..."
    item = f"{token} <- {clean_value}"
    if item not in samples and len(samples) < 12:
        samples.append(item)
