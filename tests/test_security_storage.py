from __future__ import annotations

import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gaia.archive import apply_retention, cleanup_run_temporary_artifacts, write_run_journal
from gaia.conversations import add_user_turn, create_conversation
from gaia.masking import mask_with_review
from gaia.models import AnalysisPackage


RAW_MARKER = "СекретныйПроект-123"
MASKED_MARKER = "[INTERNAL_ID_01]"


def package_fixture(root: Path) -> AnalysisPackage:
    return AnalysisPackage(
        run_id="20260101-120000", project="synthetic-project", profile_id="test", profile_title="Тест",
        route="local", safe_for_codex_after_confirmation=False, local_fallback_required=True,
        policy_notes=[RAW_MARKER], memory_chars=0, memory_sources=[], evidence_plan=[], memory_total_sections=0,
        query_mask_status="done", query_mask_replacements=1, query_mask_review=None,
        masked_query=MASKED_MARKER, files=[], prompt=MASKED_MARKER,
        journal_path=str(root / "journal.md"), safety_audit_path=str(root / "audit.md"),
    )


class SecurityStorageTests(unittest.TestCase):
    def test_operation_journal_excludes_raw_and_masked_work_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = package_fixture(Path(tmp))
            write_run_journal(package)
            journal = Path(package.journal_path).read_text(encoding="utf-8")
        self.assertNotIn(RAW_MARKER, journal)
        self.assertNotIn(MASKED_MARKER, journal)

    def test_masking_report_has_categories_without_source_fragments(self) -> None:
        raw = "test.person@example.invalid " + RAW_MARKER
        result = mask_with_review("synthetic", raw, include_llm_review=False)
        report = result.review.markdown
        self.assertNotIn("test.person@example.invalid", report)
        self.assertNotIn(RAW_MARKER, report)
        self.assertIn("EMAIL", report)

    def test_dialogue_serialization_excludes_raw_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = SimpleNamespace(service_docs=Path(tmp) / "service", projects=Path(tmp) / "projects")
            package = package_fixture(Path(tmp))
            with (
                patch("gaia.conversations.SETTINGS", settings),
                patch("gaia.projects.SETTINGS", settings),
                patch("gaia.conversations.project_names", return_value=["synthetic-project"]),
                patch("gaia.conversations.create_package", return_value=package),
            ):
                conversation = create_conversation("synthetic-project")
                add_user_turn(conversation.id, "Проверь статус " + RAW_MARKER + " для test.person@example.invalid")
                stored = next((settings.service_docs / "Диалоги").glob("*/*.json")).read_text(encoding="utf-8")
        self.assertNotIn("test.person@example.invalid", stored)
        self.assertNotIn(RAW_MARKER, stored)
        self.assertIn("Проверь статус", stored)
        self.assertIn("[INTERNAL_ID_", stored)

    def test_successful_and_expired_temporary_artifacts_are_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runs"
            successful = root / "20260101-120000"
            (successful / "uploads").mkdir(parents=True)
            (successful / "transcripts").mkdir()
            cleanup_run_temporary_artifacts(successful)
            self.assertFalse((successful / "uploads").exists())
            expired = root / "20260102-120000"
            (expired / "uploads").mkdir(parents=True)
            (expired / "uploads" / "input.txt").write_text(RAW_MARKER, encoding="utf-8")
            import os, time
            old = time.time() - 25 * 3600
            os.utime(expired, (old, old))
            service_docs = Path(tmp) / "service"
            settings = SimpleNamespace(
                runs_dir=root, service_docs=service_docs, projects=Path(tmp) / "projects",
                run_journal_dir=service_docs / "journals", safety_audit_dir=service_docs / "audits",
                retention_temporary_hours=24, retention_runs_days=0, retention_journals_days=0, retention_audit_days=0,
            )
            with patch("gaia.archive.SETTINGS", settings):
                report = apply_retention(now=time.time())
            self.assertFalse((expired / "uploads").exists())
            self.assertEqual(report.removed_temporary, ["20260102-120000/uploads"])


if __name__ == "__main__":
    unittest.main()
