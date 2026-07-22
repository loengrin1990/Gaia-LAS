"""Local deterministic protection of managed extraction artifacts.

The module never persists matched values outside the per-workspace pseudonym
zone.  Reports contain replacement tokens and aggregate metadata only.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .provenance import ProvenanceError, ProvenanceStore
from .storage import atomic_write_text, path_lock

RULES_VERSION = "deterministic-v1"
REQUIRED_CATEGORIES = {"ЭлектроннаяПочта", "Телефон", "Ссылка", "СетевойАдрес", "Секрет"}
RULES = (
    ("ЭлектроннаяПочта", r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-zА-Яа-я]{2,}(?![\w-])"),
    ("Телефон", r"(?<!\d)(?:\+7|8)[\s(.-]*\d{3}[\s). -]*\d{3}[\s.-]*\d{2}[\s.-]*\d{2}(?:\s*(?:доб\.?|ext\.?)\s*\d+)?"),
    ("Ссылка", r"https?://[^\s<>]+"),
    ("СетевойАдрес", r"\b(?!(?:127|0)\.0\.0\.1\b)(?:\d{1,3}\.){3}\d{1,3}\b"),
    ("Секрет", r"(?i)\b(?:bearer\s+\S+|(?:api[_ -]?key|token|password|secret)\s*[:=]\s*\S+)"),
    ("Документ", r"\b(?:\d{3}-\d{3}-\d{3}\s\d{2}|\d{4}\s\d{6}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|(?:договор|заявка)\s*№?\s*[A-Za-zА-Яа-я0-9/-]{4,})\b"),
    ("Реквизит", r"(?i)\b(?:сч[её]т|бик|карт[аы])\s*[:№]?\s*\d[\d ]{8,24}\b"),
    ("Адрес", r"(?i)\b(?:г\.?\s*[^,]+,\s*)?(?:ул\.?|улица|проспект)\s+[^,]+,\s*(?:дом|д\.)\s*\d+(?:\s*(?:кв\.?|корп\.?|стр\.)\s*\d+)?"),
)


def protect(store: ProvenanceStore, workspace_id: str, extraction_id: str, dictionary: dict[str, list[str]] | None = None, rules_version: str = RULES_VERSION) -> dict[str, Any]:
    if store.object_metadata(workspace_id, extraction_id).get("kind") != "extraction":
        raise ProvenanceError("Управляемый результат извлечения не найден.")
    source = store.root / "artifacts" / workspace_id / f"{extraction_id}.txt"
    text = source.read_text(encoding="utf-8")
    mapping = _mapping(store, workspace_id)
    counts: Counter[str] = Counter(); findings: list[dict[str, Any]] = []
    rules = list(RULES) + [(category, re.escape(value)) for category, values in (dictionary or {}).items() for value in values if value]
    try:
        for category, pattern in rules:
            text = re.sub(pattern, _replacement(category, mapping, counts, findings, rules_version), text, flags=re.IGNORECASE if category in {"Секрет", "Реквизит", "Адрес"} else 0)
    except re.error as exc:
        raise ProvenanceError("Не удалось выполнить обязательную локальную очистку.") from exc
    _save_mapping(store, workspace_id, mapping)
    sanitized = store.create_sanitized(workspace_id, extraction_id, rules_version, text)
    report = {"artifact_id": sanitized["artifact_id"], "status": "requires_review" if findings else "ready_for_review", "counts": dict(counts), "findings": findings, "rule_version": rules_version, "export_allowed": False}
    _save_report(store, report)
    return {"sanitized": sanitized, "report": report}


def safe_report(store: ProvenanceStore, workspace_id: str, artifact_id: str) -> dict[str, Any]:
    if store.object_metadata(workspace_id, artifact_id).get("kind") != "sanitized":
        raise ProvenanceError("Очищенное представление не найдено.")
    reports = _reports(store)
    if artifact_id not in reports:
        raise ProvenanceError("Отчёт очистки не найден.")
    return reports[artifact_id]


def _replacement(category: str, mapping: dict[str, str], counts: Counter[str], findings: list[dict[str, Any]], version: str):
    def replace(match: re.Match[str]) -> str:
        key = f"{category}:{match.group(0).casefold()}"; token = mapping.get(key)
        if token is None:
            token = f"{category}-{sum(value.startswith(category + '-') for value in mapping.values()) + 1:02d}"; mapping[key] = token
        counts[category] += 1
        findings.append({"finding_id": f"finding-{len(findings)+1}", "category": category, "count": 1, "pseudonym": token, "safe_location": f"block-{len(findings)+1}", "status": "requires_review" if category in {"Адрес", "Сотрудник"} else "ready_for_review", "confidence": "high", "requires_review": category in {"Адрес", "Сотрудник"}, "rule_version": version})
        return token
    return replace


def _mapping(store: ProvenanceStore, workspace_id: str) -> dict[str, str]:
    path = store.root / "pseudonyms" / f"{workspace_id}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _save_mapping(store: ProvenanceStore, workspace_id: str, mapping: dict[str, str]) -> None:
    atomic_write_text(store.root / "pseudonyms" / f"{workspace_id}.json", json.dumps(mapping, ensure_ascii=False, indent=2) + "\n")


def _reports(store: ProvenanceStore) -> dict[str, Any]:
    path = store.root / "metadata" / "protection_reports.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _save_report(store: ProvenanceStore, report: dict[str, Any]) -> None:
    path = store.root / "metadata" / "protection_reports.json"
    with path_lock(path):
        reports = _reports(store); reports[report["artifact_id"]] = report
        atomic_write_text(path, json.dumps(reports, ensure_ascii=False, indent=2) + "\n")
