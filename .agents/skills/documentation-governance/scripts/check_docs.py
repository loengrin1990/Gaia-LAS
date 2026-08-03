#!/usr/bin/env python3
"""Validate the self-contained structure of Gaia documentation governance."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SKILL_ROOT.parents[2]
MAP_PATH = SKILL_ROOT / "references" / "document-map.json"
GENRES = {
    "normative_rules",
    "reference",
    "runbook",
    "decision_analysis",
    "handoff_state",
    "agent_instruction",
}
AUTHORITIES = {"normative", "descriptive", "historical", "operational"}
REQUIRED_FILES = (
    "SKILL.md",
    "references/normative-rules.md",
    "references/reference.md",
    "references/runbook.md",
    "references/decision-analysis.md",
    "references/handoff-state.md",
    "references/agent-instruction.md",
    "references/verification.md",
    "references/document-map.json",
    "scripts/check_docs.py",
)
CLAUDE_PATTERN = re.compile(r"(?:\.claude(?:/|$)|claude[-_ ]?(?:hook|config))", re.I)
REFERENCE_PATTERN = re.compile(r"\]\((references/[^)#]+)")


def main() -> int:
    errors: list[str] = []
    for relative_path in REQUIRED_FILES:
        if not (SKILL_ROOT / relative_path).is_file():
            errors.append(f"Отсутствует обязательный файл skill: {relative_path}")

    try:
        mapped_documents = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"Не удалось прочитать document-map.json: {exc}")
        mapped_documents = []

    if not isinstance(mapped_documents, list):
        errors.append("document-map.json должен содержать JSON-массив документов.")
        mapped_documents = []

    paths: set[str] = set()
    for index, document in enumerate(mapped_documents, start=1):
        if not isinstance(document, dict):
            errors.append(f"Запись {index} в document-map.json должна быть объектом.")
            continue
        path = document.get("path")
        genre = document.get("genre")
        authority = document.get("authority")
        purpose = document.get("purpose")
        if not isinstance(path, str) or not path:
            errors.append(f"Запись {index} не содержит непустой path.")
            continue
        if path in paths:
            errors.append(f"Повторяющийся path в document-map.json: {path}")
        paths.add(path)
        if not (REPO_ROOT / path).is_file():
            errors.append(f"Mapped file не существует: {path}")
        if genre not in GENRES:
            errors.append(f"Недопустимый genre для {path}: {genre!r}")
        if authority not in AUTHORITIES:
            errors.append(f"Недопустимый authority для {path}: {authority!r}")
        if not isinstance(purpose, str) or not purpose.strip():
            errors.append(f"Для {path} нужен непустой purpose.")

    try:
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"Не удалось прочитать SKILL.md: {exc}")
        skill_text = ""
    for reference in REFERENCE_PATTERN.findall(skill_text):
        if not (SKILL_ROOT / reference).is_file():
            errors.append(f"SKILL.md ссылается на отсутствующий reference: {reference}")
    for path in SKILL_ROOT.rglob("*"):
        if path.is_file() and CLAUDE_PATTERN.search(path.read_text(encoding="utf-8", errors="ignore")):
            errors.append(f"Обнаружена Claude-specific ссылка: {path.relative_to(SKILL_ROOT)}")

    if errors:
        print("Проверка documentation governance не пройдена:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Проверка documentation governance пройдена: {len(mapped_documents)} документов в карте, структура согласована.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
