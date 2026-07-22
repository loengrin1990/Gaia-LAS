from __future__ import annotations

import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .config import SETTINGS
from .models import MaskFinding, MaskReview
from .module_assist import review_masking_with_local_llm
from .policy import detect_concrete_pii, detect_possible_pii


@dataclass(frozen=True)
class MaskRule:
    category: str
    pattern: re.Pattern
    source: str = "gaia"


PRE_BASE_RULES = [
    MaskRule("INN", re.compile(r"\bИНН\s*[:№#-]?\s*[0-9\- ]{6,24}\b", re.IGNORECASE)),
    MaskRule(
        "ADDRESS",
        re.compile(
            r"\b(?:адрес|проживает по адресу|зарегистрирован[а-я ]*по адресу)\s*[:№#-]?\s*"
            r"[^\n;]{8,160}?"
            r"(?=(?:\.\s+(?:Паспорт|Договор|Контракт|ИНН|КПП|ОГРН|СНИЛС)\b)|[;\n]|$)",
            re.IGNORECASE,
        ),
    ),
    MaskRule("PASSPORT", re.compile(r"\b(?:паспорт|паспортные данные|серия|номер паспорта)\s*[:№#-]?\s*[0-9\- ]{4,24}\b", re.IGNORECASE)),
]


GAIA_RULES = [
    MaskRule("EMAIL", re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-zА-Яа-я]{2,}(?![\w-])", re.IGNORECASE)),
    MaskRule("PHONE", re.compile(r"(?<!\d)(?:\+7|8)?[\s(.-]*\d{3}[\s). -]*\d{3}[\s.-]*\d{2}[\s.-]*\d{2}(?!\d)", re.IGNORECASE)),
    MaskRule(
        "PERSON",
        re.compile(
            r"\b[А-ЯЁ][а-яё]{2,}(?:ым|ом|а|у|е|ой|ая|ий|ый)?\s+"
            r"[А-ЯЁ][а-яё]{2,}(?:ым|ом|а|у|е|ой|ая|ий|ый)?\s+"
            r"[А-ЯЁ][а-яё]+(?:вич|вича|вичу|вичем|вичём|вна|вны|вне|вной|ична|ичны|ичне|ичной)\b",
            re.IGNORECASE,
        ),
    ),
    MaskRule("PERSON", re.compile(r"\b[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.", re.IGNORECASE)),
    MaskRule("ID", re.compile(r"\b(?:КПП|ОГРН|СНИЛС)\s*[:№#-]?\s*[0-9\- ]{6,24}\b", re.IGNORECASE)),
    MaskRule("CONTRACT", re.compile(r"\b(?:договор|контракт|доп\.?\s*соглашение)\s*(?:№|N|#)?\s*[A-Za-zА-Яа-я0-9_./\\-]*\d[A-Za-zА-Яа-я0-9_./\\-]*\b", re.IGNORECASE)),
]


STRICT_DIALOG_RULES = [
    MaskRule(
        "PERSON",
        re.compile(
            r"(?:(?<=\bу\s)|(?<=\bдля\s)|(?<=\bк\s)|(?<=\bот\s)|(?<=\bс\s)|(?<=\bпо\s)|(?<=\bпро\s)|(?<=\bо\s)|(?<=\bоб\s))"
            r"[А-ЯЁ][а-яё]{3,}(?:а|у|е|ым|ом|ой)?\b"
        ),
        "gaia-strict-dialog",
    ),
]


def mask_text(label: str, text: str) -> tuple[str, str, int, str]:
    result = mask_with_review(label, text)
    return result.masked_text, result.review.status, result.review.total_replacements, result.review.markdown


@dataclass
class MaskResult:
    masked_text: str
    review: MaskReview


def mask_with_review(
    label: str,
    text: str,
    strict_dialog_privacy: bool = False,
    include_llm_review: bool = True,
) -> MaskResult:
    if not text:
        review = MaskReview(
            label=label,
            status="пустой текст",
            total_replacements=0,
            counts={},
            findings=[],
            suspected_pii=False,
            unresolved_pii=False,
            manual_confirmation_required=False,
            markdown=build_review_markdown(label, "пустой текст", {}, [], False, False, ""),
        )
        return MaskResult("", review)
    privacy_masker = load_privacy_masker()
    if privacy_masker is None:
        suspected = detect_possible_pii(text)
        reason = "Veil недоступен, а текст похож на содержащий ПД." if suspected else ""
        review = MaskReview(
            label=label,
            status="невозможно: модуль Veil недоступен",
            total_replacements=0,
            counts={},
            findings=[],
            suspected_pii=suspected,
            unresolved_pii=suspected,
            manual_confirmation_required=False,
            unresolved_reason=reason,
            markdown=build_review_markdown(label, "невозможно: модуль Veil недоступен", {}, [], suspected, suspected, reason),
        )
        return MaskResult(text, review)

    address_masked, address_counts, address_findings = apply_gaia_rules(text, Counter(), PRE_BASE_RULES)
    base_masked, base_counts, base_findings = apply_base_masker(privacy_masker, address_masked)
    counts = Counter(address_counts)
    counts.update(base_counts)
    masked, gaia_counts, gaia_findings = apply_gaia_rules(base_masked, counts, GAIA_RULES)
    counts.update(gaia_counts)
    strict_counts: Counter = Counter()
    strict_findings: list[MaskFinding] = []
    if strict_dialog_privacy:
        masked, strict_counts, strict_findings = apply_gaia_rules(masked, counts, STRICT_DIALOG_RULES)
        counts.update(strict_counts)
    findings = address_findings + base_findings + gaia_findings + strict_findings
    suspected = detect_possible_pii(text)
    concrete = detect_concrete_pii(text)
    total = sum(counts.values())
    unresolved = concrete and total == 0
    manual_confirmation_required = suspected and not unresolved
    reason = "Текст похож на содержащий ПД, но Veil не выполнил замен." if unresolved else ""
    if unresolved:
        status = "требуется ручная проверка"
    elif manual_confirmation_required and total == 0:
        status = "выполнено, нужен ручной просмотр"
    else:
        status = "выполнено" if total else "выполнено, ПД по правилам не найдены"
    counts_dict = dict(sorted(counts.items()))
    llm_review = veil_llm_review(label, masked, status, counts_dict, suspected, unresolved) if include_llm_review else None
    if llm_review and llm_review.get("unresolved_pii"):
        unresolved = True
        manual_confirmation_required = False
        reason = llm_review.get("reason") or reason or "Локальная LLM-проверка Veil отметила остаточный риск ПД."
        status = "требуется ручная проверка после LLM review"
    markdown = build_review_markdown(label, status, counts_dict, findings, suspected, unresolved, reason, manual_confirmation_required)
    if llm_review:
        markdown += build_llm_review_markdown(llm_review)
    review = MaskReview(
        label=label,
        status=status,
        total_replacements=total,
        counts=counts_dict,
        findings=findings,
        suspected_pii=suspected,
        unresolved_pii=unresolved,
        manual_confirmation_required=manual_confirmation_required,
        unresolved_reason=reason,
        markdown=markdown,
    )
    return MaskResult(masked, review)


def veil_llm_review(label: str, masked_text: str, status: str, counts: dict[str, int], suspected: bool, unresolved: bool) -> dict[str, object] | None:
    if not getattr(SETTINGS, "veil_llm_review", False):
        return None
    timeout = int(getattr(SETTINGS, "veil_llm_review_timeout_seconds", 4) or 4)
    return review_masking_with_local_llm(
        label,
        masked_text,
        {
            "status": status,
            "counts": counts,
            "suspected_pii": suspected,
            "unresolved_pii_before_llm": unresolved,
        },
        timeout,
    )


def build_llm_review_markdown(llm_review: dict[str, object]) -> str:
    categories = llm_review.get("categories") or []
    category_text = ", ".join(str(item) for item in categories) if categories else "нет"
    reason = str(llm_review.get("reason") or "")
    return "\n".join([
        "",
        "## LLM review",
        "",
        f"- Остаточный риск ПД: {'да' if llm_review.get('unresolved_pii') else 'нет'}",
        f"- Категории риска: {category_text}",
        f"- Причина: {reason or '-'}",
        "- Локальная LLM-проверка может только усилить риск; она не отменяет rule-based Veil.",
        "",
    ])


def load_privacy_masker() -> Any | None:
    obsidian_work = getattr(SETTINGS, "obsidian_work", None)
    if obsidian_work is not None and str(obsidian_work) not in sys.path:
        sys.path.insert(0, str(obsidian_work))
    try:
        from privacy_masker import PrivacyMasker  # type: ignore
    except Exception:
        return None
    return PrivacyMasker


def apply_base_masker(privacy_masker: Any, text: str) -> tuple[str, Counter, list[MaskFinding]]:
    masker = privacy_masker()
    result = masker.apply(text)
    findings: list[MaskFinding] = []
    for category, samples in sorted(result.samples.items()):
        for sample in samples:
            token, _, value = sample.partition(" <- ")
            findings.append(MaskFinding(category=category, token=token.strip(), sample="", source="privacy_masker"))
    return result.text, Counter(result.counts), findings


def apply_gaia_rules(text: str, existing_counts: Counter, rules: list[MaskRule]) -> tuple[str, Counter, list[MaskFinding]]:
    counters = Counter(existing_counts)
    replacements: Counter = Counter()
    findings: list[MaskFinding] = []
    masked = text

    for rule in rules:
        def repl(match: re.Match, category: str = rule.category, source: str = rule.source) -> str:
            counters[category] += 1
            replacements[category] += 1
            token = f"[{category}_{counters[category]}]"
            findings.append(MaskFinding(category=category, token=token, sample="", source=source))
            return token

        masked = rule.pattern.sub(repl, masked)
    return masked, replacements, findings


def clean_sample(value: str) -> str:
    sample = re.sub(r"\s+", " ", value.strip())
    if len(sample) > 80:
        return sample[:77] + "..."
    return sample


def build_review_markdown(
    label: str,
    status: str,
    counts: dict[str, int],
    findings: list[MaskFinding],
    suspected: bool,
    unresolved: bool,
    reason: str,
    manual_confirmation_required: bool = False,
) -> str:
    parts = [
        f"# Проверка маскирования: {label}",
        "",
        f"Обновлено: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        f"Статус: {status}",
        f"Есть признаки ПД: {'да' if suspected else 'нет'}",
        f"Есть неподтвержденный риск ПД: {'да' if unresolved else 'нет'}",
        f"Требуется ручное подтверждение очищенного пакета: {'да' if manual_confirmation_required else 'нет'}",
    ]
    if reason:
        parts.append(f"Причина: {reason}")
    parts.extend(["", f"Всего замен: {sum(counts.values())}", "", "| Категория | Количество |", "|---|---:|"])
    if counts:
        for category, count in sorted(counts.items()):
            parts.append(f"| `{category}` | {count} |")
    else:
        parts.append("| нет находок | 0 |")
    parts.extend(["", "## Идентификаторы замен", ""])
    if findings:
        by_category: dict[str, list[MaskFinding]] = {}
        for finding in findings:
            by_category.setdefault(finding.category, []).append(finding)
        for category, items in sorted(by_category.items()):
            parts.extend([f"### {category}", ""])
            for finding in items[:12]:
                parts.append(f"- `{finding.token}` ({finding.source})")
            parts.append("")
    else:
        parts.append("Замен по текущим правилам не найдено.")
        parts.append("")
    parts.extend([
        "## Важно",
        "",
        "- Это локальная проверка по правилам, не юридическая экспертиза.",
        "- Перед отправкой во внешний ChatGPT проверьте безопасный пакет, если документ содержит ПД, договоры или коммерческие детали.",
        "- Исходники не изменяются и не удаляются.",
        "",
    ])
    return "\n".join(parts)
