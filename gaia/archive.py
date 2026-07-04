from __future__ import annotations

import argparse
import re
import shutil
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

from .config import SETTINGS, ConfigError, ensure_dirs
from .models import AnalysisPackage


RUN_ID_RE = re.compile(r"^\d{8}-\d{6}(?:-\d{6})?$")


@dataclass
class RetentionReport:
    dry_run: bool
    removed_runs: list[str]
    removed_journals: list[str]
    removed_audits: list[str]
    skipped: list[str]


def journal_path(run_id: str) -> str:
    return str(SETTINGS.run_journal_dir / f"{run_id}.md")


def safety_audit_path(run_id: str) -> str:
    return str(SETTINGS.safety_audit_dir / f"{run_id}.md")


def write_run_journal(package: AnalysisPackage) -> None:
    path = Path(package.journal_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    parts = [
        f"# Запрос {package.run_id}",
        "",
        f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Проект: {package.project}",
        f"Профиль: {package.profile_title} (`{package.profile_id}`)",
        f"Маршрут: {package.route}",
        f"Можно готовить для Codex после подтверждения: {package.safe_for_codex_after_confirmation}",
        f"Требуется локальный fallback: {package.local_fallback_required}",
        f"Lore выбрал разделов: {len(package.memory_sources)} из {package.memory_total_sections}",
        "",
        "## Политика",
        "",
    ]
    parts.extend(f"- {note}" for note in package.policy_notes)
    if package.query_mask_review:
        parts.extend([
            "",
            "## Veil: запрос",
            "",
            f"- Статус: {package.query_mask_review.status}",
            f"- Замен: {package.query_mask_review.total_replacements}",
            f"- Категории: {format_counts(package.query_mask_review.counts)}",
            f"- Неподтвержденный риск ПД: {'да' if package.query_mask_review.unresolved_pii else 'нет'}",
        ])
        if package.query_mask_review.unresolved_reason:
            parts.append(f"- Причина: {package.query_mask_review.unresolved_reason}")
    parts.extend(["", "## Lore: выбранные разделы памяти", ""])
    if package.memory_sources:
        for source in package.memory_sources:
            terms = ", ".join(source.matched_terms) if source.matched_terms else "нет"
            parts.append(
                f"- {source.heading}: строки {source.line_start}-{source.line_end}, "
                f"score {source.score}, совпадения: {terms}"
            )
    else:
        parts.append("- Разделы памяти не выбраны.")
    parts.extend(["", "## Lore: evidence plan", ""])
    if package.evidence_plan:
        for item in package.evidence_plan:
            parts.append(
                f"- {item.status}: {item.heading or '-'} ({item.scope}, {item.source_path or '-'}). "
                f"{item.reason}"
            )
    else:
        parts.append("- Evidence drill-down не запускался или не нашел подтверждений.")
    parts.extend(["", "## Файлы", ""])
    if package.files:
        for item in package.files:
            parts.append(f"- {item.name}: {item.mask_status}, замен {item.mask_replacements}, {item.extraction_note}")
            if item.mask_review:
                parts.append(f"  - Категории: {format_counts(item.mask_review.counts)}")
                if item.mask_review.unresolved_pii:
                    parts.append(f"  - Неподтвержденный риск ПД: {item.mask_review.unresolved_reason}")
    else:
        parts.append("- Файлы не приложены.")
    parts.extend(["", "## Безопасный пакет", "", "```text", package.prompt[:100000], "```", ""])
    path.write_text("\n".join(parts), encoding="utf-8")
    write_safety_audit(package)


def write_safety_audit(package: AnalysisPackage) -> None:
    path = Path(package.safety_audit_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    parts = [
        f"# Safety audit {package.run_id}",
        "",
        f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Проект: {package.project}",
        f"Профиль: {package.profile_title} (`{package.profile_id}`)",
        f"Маршрут: {package.route}",
        f"Внешний маршрут после подтверждения: {package.safe_for_codex_after_confirmation}",
        f"Локальный fallback обязателен: {package.local_fallback_required}",
        f"Lore выбрал разделов: {len(package.memory_sources)} из {package.memory_total_sections}",
        "",
        "## Veil summary",
        "",
        f"- Запрос: {package.query_mask_status}, замен {package.query_mask_replacements}",
    ]
    if package.query_mask_review:
        parts.extend([
            f"- Неподтвержденный риск ПД: {'да' if package.query_mask_review.unresolved_pii else 'нет'}",
            f"- Категории: {format_counts(package.query_mask_review.counts)}",
        ])
    parts.extend(["", "## Файлы", ""])
    if package.files:
        for item in package.files:
            categories = format_counts(item.mask_review.counts) if item.mask_review else "нет"
            parts.append(f"- {item.name}: {item.kind}, {item.mask_status}, замен {item.mask_replacements}, категории: {categories}")
    else:
        parts.append("- Файлы не приложены.")
    parts.extend(["", "## Policy notes", ""])
    parts.extend(f"- {note}" for note in package.policy_notes)
    parts.extend(["", "## Lore evidence summary", ""])
    if package.evidence_plan:
        for item in package.evidence_plan:
            parts.append(f"- {item.status}: {item.heading or '-'}; {item.reason}")
    else:
        parts.append("- Evidence drill-down не запускался или не нашел подтверждений.")
    parts.extend([
        "",
        "## Retention class",
        "",
        "- Audit безопасности не содержит полный prompt, текст вложений или проектную память.",
        "- Рабочий prompt хранится отдельно в журнале запроса и управляется `retention.journals_days`.",
        "",
    ])
    path.write_text("\n".join(parts), encoding="utf-8")


def apply_retention(dry_run: bool = False, now: float | None = None) -> RetentionReport:
    if SETTINGS is None:
        raise ConfigError("Settings are unavailable.")
    current = time.time() if now is None else now
    report = RetentionReport(dry_run=dry_run, removed_runs=[], removed_journals=[], removed_audits=[], skipped=[])
    prune_run_dirs(SETTINGS.runs_dir, SETTINGS.retention_runs_days, current, report, dry_run)
    prune_markdown_files(SETTINGS.run_journal_dir, SETTINGS.retention_journals_days, current, report.removed_journals, report, dry_run)
    prune_markdown_files(SETTINGS.safety_audit_dir, SETTINGS.retention_audit_days, current, report.removed_audits, report, dry_run)
    return report


def prune_run_dirs(root: Path, days: int, now: float, report: RetentionReport, dry_run: bool) -> None:
    if days == 0:
        return
    if not safe_retention_root(root):
        report.skipped.append(f"unsafe runs root: {root}")
        return
    cutoff = now - days * 86400
    if not root.exists():
        return
    for path in sorted(root.iterdir()):
        if not path.is_dir() or not RUN_ID_RE.match(path.name):
            continue
        if path.stat().st_mtime >= cutoff:
            continue
        report.removed_runs.append(str(path))
        if not dry_run:
            shutil.rmtree(path)


def prune_markdown_files(root: Path, days: int, now: float, removed: list[str], report: RetentionReport, dry_run: bool) -> None:
    if days == 0:
        return
    if not safe_retention_root(root):
        report.skipped.append(f"unsafe service root: {root}")
        return
    cutoff = now - days * 86400
    if not root.exists():
        return
    for path in sorted(root.glob("*.md")):
        if not RUN_ID_RE.match(path.stem):
            continue
        if path.stat().st_mtime >= cutoff:
            continue
        removed.append(str(path))
        if not dry_run:
            path.unlink()


def safe_retention_root(root: Path) -> bool:
    resolved = root.resolve()
    allowed_roots = [SETTINGS.runs_dir.resolve(), SETTINGS.service_docs.resolve()]
    project_root = SETTINGS.projects.resolve()
    if resolved == project_root or is_relative_to(resolved, project_root):
        return False
    return any(resolved == allowed or is_relative_to(resolved, allowed) for allowed in allowed_roots)


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        return path.is_relative_to(root)
    except AttributeError:
        return root in path.parents


def format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "нет"
    return ", ".join(f"{category}: {count}" for category, count in sorted(counts.items()))


def main() -> int:
    parser = argparse.ArgumentParser(description="Gaia Archive maintenance")
    parser.add_argument("--cleanup", action="store_true", help="Apply configured retention cleanup.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be removed.")
    args = parser.parse_args()
    try:
        ensure_dirs()
        if args.cleanup or args.dry_run:
            report = apply_retention(dry_run=args.dry_run)
            print(asdict(report))
            return 0
        parser.print_help()
        return 0
    except Exception as exc:
        print(f"Gaia archive error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
