from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from gaia.jobs import JOBS, JOBS_LOCK, get_job, submit_analyze_job
from gaia.models import AnalysisPackage, JobRecord


def fake_package() -> AnalysisPackage:
    return AnalysisPackage(
        run_id="test-run",
        project="Автопретензии",
        profile_id="general",
        profile_title="Общий анализ",
        route="Codex/ChatGPT после ручного подтверждения",
        safe_for_codex_after_confirmation=True,
        local_fallback_required=False,
        policy_notes=[],
        memory_chars=0,
        memory_sources=[],
        evidence_plan=[],
        memory_total_sections=0,
        query_mask_status="выполнено",
        query_mask_replacements=0,
        query_mask_review=None,
        masked_query="query",
        files=[],
        prompt="test prompt",
        journal_path="/tmp/test.md",
        safety_audit_path="/tmp/test-audit.md",
    )


class JobQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        with JOBS_LOCK:
            JOBS.clear()

    def test_submit_returns_before_slow_job_finishes(self) -> None:
        def slow_create_package(project, query, uploaded, profile_id=None):
            time.sleep(0.2)
            return fake_package()

        started = time.monotonic()
        with patch("gaia.jobs.create_package", side_effect=slow_create_package):
            job = submit_analyze_job("Автопретензии", "query", [])
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 0.1)
            self.assertIn(job.status, {"created", "running"})
            final = wait_for_job(job.id)

        self.assertEqual(final.status, "done")
        self.assertEqual(final.progress, 100)
        self.assertEqual(final.result["prompt"], "test prompt")

    def test_failed_job_is_available_by_id(self) -> None:
        with patch("gaia.jobs.create_package", side_effect=RuntimeError("boom")):
            job = submit_analyze_job("Автопретензии", "query", [])
            final = wait_for_job(job.id)

        self.assertEqual(final.status, "failed")
        self.assertEqual(final.progress, 100)
        self.assertIn("boom", final.error)

    def test_stale_running_job_is_failed_by_watchdog(self) -> None:
        job = JobRecord(
            id="stale",
            status="running",
            created_at="2000-01-01T00:00:00",
            updated_at="2000-01-01T00:00:00",
            project="Автопретензии",
            message="running",
            progress=10,
        )
        with JOBS_LOCK:
            JOBS[job.id] = job

        stale = get_job("stale")

        self.assertIsNotNone(stale)
        self.assertEqual(stale.status, "failed")
        self.assertEqual(stale.progress, 100)
        self.assertIn("timeout", stale.error.lower())


def wait_for_job(job_id: str, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = get_job(job_id)
        if job and job.status in {"done", "failed"}:
            return job
        time.sleep(0.02)
    raise AssertionError(f"Job did not finish: {job_id}")


if __name__ == "__main__":
    unittest.main()
