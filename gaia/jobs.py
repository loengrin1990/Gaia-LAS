from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime
import threading
from typing import Any

from .models import JobRecord
from .orchestrator import PackageCancelledError, create_package


JOBS: dict[str, JobRecord] = {}
JOB_CANCEL_EVENTS: dict[str, threading.Event] = {}
JOBS_LOCK = threading.RLock()
MAX_WORKERS = 4
MAX_QUEUED_JOBS = 8
JOB_EXECUTOR = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="gaia-job")
JOB_CAPACITY = threading.BoundedSemaphore(MAX_WORKERS + MAX_QUEUED_JOBS)
RUNNING_JOB_TIMEOUT_SECONDS = 900
TERMINAL_STATUSES = {"done", "failed", "cancelled"}


class JobQueueFullError(RuntimeError):
    pass


def local_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def submit_analyze_job(project: str, query: str, uploaded: list[tuple[str, bytes]], profile_id: str | None = None) -> JobRecord:
    if not JOB_CAPACITY.acquire(blocking=False):
        raise JobQueueFullError("Очередь обработки занята. Дождись завершения текущих задач и повтори запрос.")
    job_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    now = local_now()
    job = JobRecord(
        id=job_id,
        status="created",
        created_at=now,
        updated_at=now,
        project=project,
        message="Задача создана.",
        progress=0,
    )
    with JOBS_LOCK:
        prune_completed_jobs()
        JOBS[job_id] = job
        JOB_CANCEL_EVENTS[job_id] = threading.Event()
    try:
        JOB_EXECUTOR.submit(run_analyze_job, job_id, project, query, uploaded, profile_id)
    except Exception:
        JOB_CAPACITY.release()
        raise
    return job


def run_analyze_job(job_id: str, project: str, query: str, uploaded: list[tuple[str, bytes]], profile_id: str | None) -> None:
    try:
        _run_analyze_job(job_id, project, query, uploaded, profile_id)
    finally:
        JOB_CAPACITY.release()


def _run_analyze_job(job_id: str, project: str, query: str, uploaded: list[tuple[str, bytes]], profile_id: str | None) -> None:
    cancel_event = cancel_event_for(job_id)
    if cancel_event.is_set():
        return
    update_job(job_id, status="running", message="Gaia собирает пакет.", progress=10)
    timeout_timer = threading.Timer(job_timeout_seconds(), cancel_job, args=(job_id, "timeout"))
    timeout_timer.daemon = True
    timeout_timer.start()
    try:
        package = create_package(project, query, uploaded, profile_id, cancel_event=cancel_event)
    except PackageCancelledError:
        cancel_job(job_id, "timeout" if cancel_event.is_set() else "cancelled")
        return
    except Exception:
        update_job(job_id, status="failed", message="Задача завершилась ошибкой.", progress=100, error="Ошибка локальной обработки. Подробности не сохраняются.")
        return
    finally:
        timeout_timer.cancel()
    if cancel_event.is_set():
        cancel_job(job_id, "timeout")
        return
    update_job(
        job_id,
        status="done",
        message="Пакет готов.",
        progress=100,
        result=asdict(package),
    )


def cancel_event_for(job_id: str) -> threading.Event:
    with JOBS_LOCK:
        return JOB_CANCEL_EVENTS.setdefault(job_id, threading.Event())


def job_timeout_seconds() -> int:
    from .config import SETTINGS

    if SETTINGS is not None:
        return SETTINGS.analyze_job_timeout_seconds
    return RUNNING_JOB_TIMEOUT_SECONDS


def completed_job_retention_seconds() -> int:
    from .config import SETTINGS

    if SETTINGS is not None:
        return SETTINGS.completed_job_retention_seconds
    return 1800


def prune_completed_jobs(now: datetime | None = None) -> None:
    current = now or datetime.now()
    retention_seconds = completed_job_retention_seconds()
    expired_ids = []
    for job_id, job in JOBS.items():
        if job.status not in TERMINAL_STATUSES:
            continue
        try:
            updated_at = datetime.fromisoformat(job.updated_at)
        except ValueError:
            continue
        if (current - updated_at).total_seconds() >= retention_seconds:
            expired_ids.append(job_id)
    for job_id in expired_ids:
        JOBS.pop(job_id, None)
        JOB_CANCEL_EVENTS.pop(job_id, None)


def cancel_job(job_id: str, reason: str = "cancelled") -> JobRecord | None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return None
        if job.status in TERMINAL_STATUSES:
            return job
        cancel_event_for(job_id).set()
        job.cancellation_requested = True
        job.status = "cancelled"
        job.progress = 100
        if reason == "timeout":
            job.message = "Задача остановлена по лимиту времени; активная транскрибация завершена."
            job.error = "Job timeout. Проверь тяжелые вложения или увеличь processing.analyze_job_timeout_seconds."
        else:
            job.message = "Задача отменена; активная транскрибация завершена."
            job.error = "Job cancelled by user."
        job.updated_at = local_now()
        return job


def update_job(job_id: str, **changes: Any) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return
        if job.status in TERMINAL_STATUSES and changes.get("status") not in {None, job.status}:
            return
        for key, value in changes.items():
            setattr(job, key, value)
        job.updated_at = local_now()


def get_job(job_id: str) -> JobRecord | None:
    with JOBS_LOCK:
        prune_completed_jobs()
        job = JOBS.get(job_id)
        if job is not None:
            mark_stale_job_failed(job)
        return job


def job_to_dict(job: JobRecord) -> dict[str, Any]:
    with JOBS_LOCK:
        mark_stale_job_failed(job)
    return asdict(job)


def mark_stale_job_failed(job: JobRecord) -> None:
    if job.status not in {"created", "running"}:
        return
    try:
        created_at = datetime.fromisoformat(job.created_at)
    except ValueError:
        return
    age = (datetime.now() - created_at).total_seconds()
    if age < job_timeout_seconds():
        return
    cancel_job(job.id, "timeout")
