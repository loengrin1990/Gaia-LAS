from __future__ import annotations

import time
import threading
import unittest
from unittest.mock import patch

from gaia.jobs import JOBS, JOBS_LOCK, JOB_CANCEL_EVENTS, JOB_EXECUTOR, MAX_WORKERS, JobQueueFullError, cancel_job, get_job, local_now, prune_completed_jobs, submit_analyze_job, mark_stale_job_failed
from gaia.models import AnalysisPackage, JobRecord
from gaia.orchestrator import PackageCancelledError


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
            JOB_CANCEL_EVENTS.clear()

    def test_submit_returns_before_slow_job_finishes(self) -> None:
        def slow_create_package(project, query, uploaded, profile_id=None, cancel_event=None):
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

    def test_job_executor_is_bounded(self) -> None:
        self.assertEqual(JOB_EXECUTOR._max_workers, MAX_WORKERS)
        self.assertEqual(MAX_WORKERS, 4)

    def test_local_now_is_iso_local_timestamp(self) -> None:
        self.assertRegex(local_now(), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")

    def test_failed_job_is_available_by_id(self) -> None:
        with patch("gaia.jobs.create_package", side_effect=RuntimeError("boom")):
            job = submit_analyze_job("Автопретензии", "query", [])
            final = wait_for_job(job.id)

        self.assertEqual(final.status, "failed")
        self.assertEqual(final.progress, 100)
        self.assertEqual(final.error, "Ошибка локальной обработки. Подробности не сохраняются.")

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
        self.assertEqual(stale.status, "cancelled")
        self.assertEqual(stale.progress, 100)
        self.assertIn("timeout", stale.error.lower())

    def test_context_job_deadline_overrides_general_stale_timeout(self) -> None:
        job=JobRecord(id="context",status="running",created_at="2026-07-28T13:45:00",updated_at="2026-07-28T13:45:00",project="p",message="running",job_type="context_compile",timeout_seconds=1800)
        with JOBS_LOCK: JOBS[job.id]=job; JOB_CANCEL_EVENTS[job.id]=threading.Event()
        with patch("gaia.jobs.datetime") as clock:
            clock.now.return_value=__import__("datetime").datetime(2026,7,28,14,5,1); clock.fromisoformat.side_effect=__import__("datetime").datetime.fromisoformat
            mark_stale_job_failed(job); self.assertEqual(job.status,"running")
            clock.now.return_value=__import__("datetime").datetime(2026,7,28,14,15,1); mark_stale_job_failed(job)
        self.assertEqual(job.status,"cancelled"); self.assertEqual(job.error,"CONTEXT_TIMEOUT")

    def test_cancelling_running_job_signals_worker_and_keeps_terminal_status(self) -> None:
        started = threading.Event()

        def cancellable_create_package(project, query, uploaded, profile_id=None, cancel_event=None):
            started.set()
            self.assertIsNotNone(cancel_event)
            self.assertTrue(cancel_event.wait(1.0))
            raise PackageCancelledError("cancelled")

        with patch("gaia.jobs.create_package", side_effect=cancellable_create_package):
            job = submit_analyze_job("Автопретензии", "query", [])
            self.assertTrue(started.wait(1.0))
            cancelled = cancel_job(job.id)
            self.assertIsNotNone(cancelled)
            final = wait_for_job(job.id)

        self.assertEqual(final.status, "cancelled")
        self.assertTrue(final.cancellation_requested)

    def test_context_compile_cancel_is_rejected_after_persisting_but_analysis_is_unchanged(self) -> None:
        context=JobRecord(id="ctx-late",status="running",created_at=local_now(),updated_at=local_now(),project="p",message="saving",progress=90,job_type="context_compile",phase="persisting")
        analysis=JobRecord(id="analysis-late",status="running",created_at=local_now(),updated_at=local_now(),project="p",message="running",progress=10)
        with JOBS_LOCK:
            JOBS[context.id]=context; JOB_CANCEL_EVENTS[context.id]=threading.Event()
            JOBS[analysis.id]=analysis; JOB_CANCEL_EVENTS[analysis.id]=threading.Event()
        self.assertEqual(cancel_job(context.id).status,"running")
        self.assertFalse(JOB_CANCEL_EVENTS[context.id].is_set())
        self.assertEqual(context.message,"Сохранение контекста уже завершается. Дождитесь результата.")
        self.assertEqual(cancel_job(analysis.id).status,"cancelled")

    def test_context_compile_cancel_before_persisting_remains_cancelled(self) -> None:
        context=JobRecord(id="ctx-early",status="running",created_at=local_now(),updated_at=local_now(),project="p",message="working",progress=20,job_type="context_compile",phase="compiling")
        with JOBS_LOCK:
            JOBS[context.id]=context; JOB_CANCEL_EVENTS[context.id]=threading.Event()
        self.assertEqual(cancel_job(context.id).status,"cancelled")
        self.assertTrue(JOB_CANCEL_EVENTS[context.id].is_set())

    def test_completed_jobs_expire_from_memory(self) -> None:
        old = JobRecord(
            id="old",
            status="done",
            created_at="2000-01-01T00:00:00",
            updated_at="2000-01-01T00:00:00",
            project="Автопретензии",
            message="done",
            progress=100,
        )
        with JOBS_LOCK:
            JOBS[old.id] = old
            JOB_CANCEL_EVENTS[old.id] = threading.Event()
            prune_completed_jobs()

        self.assertNotIn(old.id, JOBS)
        self.assertNotIn(old.id, JOB_CANCEL_EVENTS)

    def test_submit_rejects_when_job_capacity_is_full(self) -> None:
        with patch("gaia.jobs.JOB_CAPACITY", threading.BoundedSemaphore(0)):
            with self.assertRaises(JobQueueFullError):
                submit_analyze_job("Автопретензии", "query", [])


def wait_for_job(job_id: str, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = get_job(job_id)
        if job and job.status in {"done", "failed", "cancelled"}:
            return job
        time.sleep(0.02)
    raise AssertionError(f"Job did not finish: {job_id}")


if __name__ == "__main__":
    unittest.main()
