from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

from .config import SETTINGS, ConfigError, ensure_dirs
from .models import AnalysisPackage
from .storage import atomic_write_text


RUN_ID_RE = re.compile(r"^\d{8}-\d{6}(?:-\d{6})?$")


@dataclass
class RetentionReport:
    dry_run: bool
    removed_runs: list[str]
    removed_journals: list[str]
    removed_audits: list[str]
    removed_temporary: list[str]
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
        f"Рабочее пространство: {safe_identifier(package.project)}",
        f"Профиль: {package.profile_title} (`{package.profile_id}`)",
        f"Маршрут: {package.route}",
        f"Можно готовить для Codex после подтверждения: {package.safe_for_codex_after_confirmation}",
        f"Требуется локальный fallback: {package.local_fallback_required}",
        f"Lore выбрал разделов: {len(package.memory_sources)} из {package.memory_total_sections}",
        "",
        f"Входных файлов: {len(package.files)}",
        f"Всего замен: {sum(item.mask_replacements for item in package.files) + package.query_mask_replacements}",
        "",
        "## Технический результат",
        "",
    ]
    parts.extend([
        f"- Статус маскирования запроса: {package.query_mask_status}",
        f"- Внешний маршрут после подтверждения: {'разрешён' if package.safe_for_codex_after_confirmation else 'заблокирован'}",
    ])
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
    if package.prompt_mask_review:
        parts.extend([
            "",
            "## Veil: итоговый prompt",
            "",
            f"- Статус: {package.prompt_mask_review.status}",
            f"- Замен: {package.prompt_mask_review.total_replacements}",
            f"- Категории: {format_counts(package.prompt_mask_review.counts)}",
            f"- Неподтвержденный риск ПД: {'да' if package.prompt_mask_review.unresolved_pii else 'нет'}",
            f"- Ручное подтверждение: {'да' if package.prompt_mask_review.manual_confirmation_required else 'нет'}",
        ])
    parts.extend(["", "## Выбор контекста", "", f"- Выбрано разделов: {len(package.memory_sources)} из {package.memory_total_sections}"])
    parts.extend(["", "## Файлы", ""])
    if package.files:
        for item in package.files:
            parts.append(f"- Тип: {item.kind}; статус: {item.mask_status}; замен: {item.mask_replacements}")
            if item.mask_review:
                parts.append(f"  - Категории: {format_counts(item.mask_review.counts)}")
                if item.mask_review.unresolved_pii:
                    parts.append("  - Неподтвержденный риск ПД: да")
    else:
        parts.append("- Файлы не приложены.")
    parts.extend(["", "## Пакет", "", "- Содержимое пакета в технический журнал не записывается.", ""])
    path.write_text("\n".join(parts), encoding="utf-8")
    write_safety_audit(package)


def write_safety_audit(package: AnalysisPackage) -> None:
    path = Path(package.safety_audit_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    parts = [
        f"# Safety audit {package.run_id}",
        "",
        f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Рабочее пространство: {safe_identifier(package.project)}",
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
    if package.prompt_mask_review:
        parts.extend([
            f"- Итоговый prompt: {package.prompt_mask_review.status}, замен {package.prompt_mask_review.total_replacements}",
            f"- Prompt категории: {format_counts(package.prompt_mask_review.counts)}",
            f"- Prompt ручное подтверждение: {'да' if package.prompt_mask_review.manual_confirmation_required else 'нет'}",
        ])
    parts.extend(["", "## Файлы", ""])
    if package.files:
        for item in package.files:
            categories = format_counts(item.mask_review.counts) if item.mask_review else "нет"
            parts.append(f"- Тип: {item.kind}; статус: {item.mask_status}; замен: {item.mask_replacements}; категории: {categories}")
    else:
        parts.append("- Файлы не приложены.")
    parts.extend(["", "## Технические сведения", "", f"- Выбрано разделов памяти: {len(package.memory_sources)} из {package.memory_total_sections}"])
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
    report = RetentionReport(dry_run=dry_run, removed_runs=[], removed_journals=[], removed_audits=[], removed_temporary=[], skipped=[])
    prune_temporary_artifacts(SETTINGS.runs_dir, SETTINGS.retention_temporary_hours, current, report, dry_run)
    prune_run_dirs(SETTINGS.runs_dir, SETTINGS.retention_runs_days, current, report, dry_run)
    prune_markdown_files(SETTINGS.run_journal_dir, SETTINGS.retention_journals_days, current, report.removed_journals, report, dry_run)
    prune_markdown_files(SETTINGS.safety_audit_dir, SETTINGS.retention_audit_days, current, report.removed_audits, report, dry_run)
    if not dry_run:
        write_retention_status(report)
    return report


def cleanup_run_temporary_artifacts(run_dir: Path) -> None:
    for name in ("uploads", "transcripts"):
        path = run_dir / name
        if path.exists() and path.is_dir():
            shutil.rmtree(path)


def prune_temporary_artifacts(root: Path, hours: int, now: float, report: RetentionReport, dry_run: bool) -> None:
    if hours == 0 or not safe_retention_root(root) or not root.exists():
        return
    cutoff = now - hours * 3600
    for run_dir in sorted(root.iterdir()):
        if not run_dir.is_dir() or not RUN_ID_RE.match(run_dir.name) or run_dir.stat().st_mtime >= cutoff:
            continue
        for name in ("uploads", "transcripts"):
            path = run_dir / name
            if path.is_dir():
                report.removed_temporary.append(f"{run_dir.name}/{name}")
                if not dry_run:
                    shutil.rmtree(path)


def retention_status_path() -> Path:
    return SETTINGS.service_docs / "Archive" / "retention-status.json"


def write_retention_status(report: RetentionReport) -> None:
    payload = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "removed_runs": len(report.removed_runs),
        "removed_journals": len(report.removed_journals),
        "removed_audits": len(report.removed_audits),
        "skipped": list(report.skipped),
    }
    atomic_write_text(retention_status_path(), json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def retention_status() -> dict[str, object]:
    path = retention_status_path()
    empty = {"checked_at": "", "removed_runs": 0, "removed_journals": 0, "removed_audits": 0, "skipped": []}
    if not path.exists():
        return empty
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {**empty, "skipped": ["retention status unavailable"]}
    return payload if isinstance(payload, dict) else {**empty, "skipped": ["retention status invalid"]}


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


def safe_identifier(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16] if value else "без-рабочего-пространства"


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
