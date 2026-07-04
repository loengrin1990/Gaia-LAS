from __future__ import annotations

import threading
import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Any

from .models import JobRecord
from .orchestrator import create_package


JOBS: dict[str, JobRecord] = {}
JOBS_LOCK = threading.RLock()
RUNNING_JOB_TIMEOUT_SECONDS = 180
TERMINAL_STATUSES = {"done", "failed"}


def utc_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def submit_analyze_job(project: str, query: str, uploaded: list[tuple[str, bytes]], profile_id: str | None = None) -> JobRecord:
    job_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    now = utc_now()
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
        JOBS[job_id] = job
    thread = threading.Thread(target=run_analyze_job, args=(job_id, project, query, uploaded, profile_id), daemon=True)
    thread.start()
    return job


def run_analyze_job(job_id: str, project: str, query: str, uploaded: list[tuple[str, bytes]], profile_id: str | None) -> None:
    update_job(job_id, status="running", message="Gaia собирает пакет.", progress=10)
    try:
        package = create_package(project, query, uploaded, profile_id)
    except Exception as exc:
        update_job(job_id, status="failed", message="Задача завершилась ошибкой.", progress=100, error=str(exc))
        return
    update_job(
        job_id,
        status="done",
        message="Пакет готов.",
        progress=100,
        result=asdict(package),
    )


def update_job(job_id: str, **changes: Any) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return
        if job.status in TERMINAL_STATUSES and changes.get("status") not in {None, job.status}:
            return
        for key, value in changes.items():
            setattr(job, key, value)
        job.updated_at = utc_now()


def get_job(job_id: str) -> JobRecord | None:
    with JOBS_LOCK:
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
    if age < RUNNING_JOB_TIMEOUT_SECONDS:
        return
    job.status = "failed"
    job.progress = 100
    job.message = "Задача остановлена watchdog: обработка заняла слишком много времени."
    job.error = "Job timeout. Проверь LM Studio, semantic rerank или тяжелое извлечение источников; повтори запрос."
    job.updated_at = utc_now()
