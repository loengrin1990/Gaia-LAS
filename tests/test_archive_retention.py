from __future__ import annotations

import os
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from gaia.archive import apply_retention, retention_status, retention_status_path, safety_audit_path, write_safety_audit
from gaia.config import SETTINGS
from gaia.models import AnalysisPackage


def package_fixture(audit_path: Path) -> AnalysisPackage:
    return AnalysisPackage(
        run_id="20260101-120000",
        project="Автопретензии",
        profile_id="general",
        profile_title="Общий анализ",
        route="Codex/ChatGPT после ручного подтверждения",
        safe_for_codex_after_confirmation=True,
        local_fallback_required=False,
        policy_notes=["ПД замаскированы."],
        memory_chars=10,
        memory_sources=[],
        evidence_plan=[],
        memory_total_sections=0,
        query_mask_status="выполнено",
        query_mask_replacements=1,
        query_mask_review=None,
        masked_query="Запрос",
        files=[],
        prompt="SECRET_FULL_PROMPT_SHOULD_NOT_BE_IN_AUDIT",
        journal_path=str(audit_path.parent / "journal.md"),
        safety_audit_path=str(audit_path),
    )


class ArchiveRetentionTests(unittest.TestCase):
    def test_retention_prunes_only_gaia_working_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = replace(
                SETTINGS,
                projects=root / "Проекты",
                service_docs=root / "Сервисы" / "Gaia",
                run_journal_dir=root / "Сервисы" / "Gaia" / "Журнал запросов",
                safety_audit_dir=root / "Сервисы" / "Gaia" / "Аудит безопасности",
                runs_dir=root / "runs",
                uploads_dir=root / "runs" / "uploads",
                retention_runs_days=7,
                retention_journals_days=30,
                retention_audit_days=365,
            )
            for path in [settings.projects, settings.run_journal_dir, settings.safety_audit_dir, settings.runs_dir]:
                path.mkdir(parents=True)
            project_memory = settings.projects / "Автопретензии" / "Память.md"
            project_memory.parent.mkdir()
            project_memory.write_text("project memory", encoding="utf-8")

            old_run = settings.runs_dir / "20260101-120000"
            old_run.mkdir()
            new_run = settings.runs_dir / "20260629-120000"
            new_run.mkdir()
            ignored_dir = settings.runs_dir / "uploads"
            ignored_dir.mkdir()
            old_journal = settings.run_journal_dir / "20260101-120000.md"
            old_journal.write_text("old journal", encoding="utf-8")
            new_journal = settings.run_journal_dir / "20260629-120000.md"
            new_journal.write_text("new journal", encoding="utf-8")
            old_audit = settings.safety_audit_dir / "20250101-120000.md"
            old_audit.write_text("old audit", encoding="utf-8")

            now = time.time()
            old_time = now - 400 * 86400
            new_time = now
            for path in [old_run, old_journal, old_audit]:
                os.utime(path, (old_time, old_time))
            for path in [new_run, new_journal]:
                os.utime(path, (new_time, new_time))

            with patch("gaia.archive.SETTINGS", settings):
                report = apply_retention(now=now)
                status_path = retention_status_path()
                status = retention_status()

            self.assertFalse(old_run.exists())
            self.assertFalse(old_journal.exists())
            self.assertFalse(old_audit.exists())
            self.assertTrue(new_run.exists())
            self.assertTrue(new_journal.exists())
            self.assertTrue(ignored_dir.exists())
            self.assertEqual(project_memory.read_text(encoding="utf-8"), "project memory")
            self.assertEqual(len(report.removed_runs), 1)
            self.assertEqual(len(report.removed_journals), 1)
            self.assertEqual(len(report.removed_audits), 1)
            self.assertTrue(status_path.exists())
            self.assertEqual(status["removed_runs"], 1)

    def test_safety_audit_does_not_include_full_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audit_path = Path(tmp) / "audit.md"
            package = package_fixture(audit_path)
            write_safety_audit(package)

            audit = audit_path.read_text(encoding="utf-8")
            self.assertIn("Safety audit", audit)
            self.assertNotIn("SECRET_FULL_PROMPT_SHOULD_NOT_BE_IN_AUDIT", audit)

    def test_safety_audit_path_uses_configured_audit_dir(self) -> None:
        self.assertIn("Аудит безопасности", safety_audit_path("20260101-120000"))


if __name__ == "__main__":
    unittest.main()
