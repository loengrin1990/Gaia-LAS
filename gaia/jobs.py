from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime
import threading
from typing import Any

from .models import JobRecord
from .orchestrator import PackageCancelledError, create_package
from .controlled_intake import ControlledIntake
from .context_compiler import ContextCompileError
from .local_llm import TASK_CONTEXT_COMPILER, resolve_route
from .context_attempts import ContextAttemptStore


JOBS: dict[str, JobRecord] = {}
JOB_CANCEL_EVENTS: dict[str, threading.Event] = {}
JOBS_LOCK = threading.RLock()
MAX_WORKERS = 4
MAX_QUEUED_JOBS = 8
JOB_EXECUTOR = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="gaia-job")
JOB_CAPACITY = threading.BoundedSemaphore(MAX_WORKERS + MAX_QUEUED_JOBS)
CONTEXT_COMPILE_LOCK = threading.Lock()
RUNNING_JOB_TIMEOUT_SECONDS = 900
TERMINAL_STATUSES = {"done", "complete_empty", "failed", "cancelled", "interrupted"}


class JobQueueFullError(RuntimeError):
    pass


def local_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def submit_analyze_job(project: str, query: str, uploaded: list[tuple[str, bytes]], profile_id: str | None = None, intake: dict[str, Any] | None = None) -> JobRecord:
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
        timeout_seconds=job_timeout_seconds(),
    )
    with JOBS_LOCK:
        prune_completed_jobs()
        JOBS[job_id] = job
        JOB_CANCEL_EVENTS[job_id] = threading.Event()
    try:
        JOB_EXECUTOR.submit(run_analyze_job, job_id, project, query, uploaded, profile_id, intake)
    except Exception:
        JOB_CAPACITY.release()
        raise
    return job

def submit_context_compile_job(project: str, artifact_id: str) -> JobRecord:
    if not JOB_CAPACITY.acquire(blocking=False):
        raise JobQueueFullError("Очередь обработки занята. Дождись завершения текущих задач и повтори запрос.")
    job_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-ctx-" + uuid.uuid4().hex[:8]
    now = local_now()
    timeout_seconds = int(resolve_route(TASK_CONTEXT_COMPILER).get("job_timeout_seconds", 1800))
    workspace_id = ControlledIntake().existing_workspace(project) or ""
    job = JobRecord(id=job_id, status="created", created_at=now, updated_at=now, project=project, message="Подготавливаем материал…", progress=0, job_type="context_compile", timeout_seconds=timeout_seconds, workspace_id=workspace_id, artifact_id=artifact_id)
    with JOBS_LOCK:
        prune_completed_jobs(); JOBS[job_id] = job; JOB_CANCEL_EVENTS[job_id] = threading.Event()
    _persist_context_attempt(job)
    try:
        JOB_EXECUTOR.submit(_run_context_compile_job, job_id, project, artifact_id)
    except Exception:
        JOB_CAPACITY.release(); raise
    return job

def _run_context_compile_job(job_id: str, project: str, artifact_id: str) -> None:
    event=cancel_event_for(job_id)
    try:
        with CONTEXT_COMPILE_LOCK:
            update_job(job_id,status="running",message="Собираем контекст: подготавливаем фрагменты…",progress=0,phase="compiling",started_at=local_now(),last_activity_at=local_now())
            timeout_timer = threading.Timer(job_timeout_for(get_job(job_id)), cancel_job, args=(job_id, "timeout"))
            timeout_timer.daemon = True; timeout_timer.start()
            def progress(done: int,total: int,count: int):
                update_job(job_id,message=f"Собираем контекст: обработан фрагмент {done} из {total}…",progress=min(95,5+int(90*done/max(1,total))),completed_chunks=done,total_chunks=total,candidate_count=count,last_activity_at=local_now())
            def activity(current: int,total: int,attempts: int):
                if current == 0:
                    update_job(job_id,message="Загружаем локальную модель. Первый запуск может занять несколько минут…",phase="loading_model",current_chunk=0,total_chunks=0,model_attempts=0,last_activity_at=local_now())
                elif current == -1:
                    update_job(job_id,message="Проверяем собранный контекст…",phase="validating",last_activity_at=local_now())
                elif current == -2:
                    update_job(job_id,message="Сохраняем проверенный контекст…",phase="persisting",last_activity_at=local_now())
                elif current == -3:
                    update_job(job_id,message="Завершаем сохранение контекста…",phase="finalizing",last_activity_at=local_now())
                else:
                    update_job(job_id,message=f"Собираем контекст: фрагмент {current} из {total}…",phase="compiling",current_chunk=current,total_chunks=total,model_attempts=attempts,last_activity_at=local_now())
            try:
                candidates=ControlledIntake().compiler(project).compile(artifact_id,cancel_event=event,progress=progress,activity=activity)
            finally:
                timeout_timer.cancel()
            if event.is_set(): cancel_job(job_id); return
            update_job(job_id,status="complete_empty" if not candidates else "done",message="Сборка контекста завершена: проектный контекст не найден." if not candidates else "Контекст собран. Проверьте кандидатов.",progress=100,result={"candidates":candidates,"context_status":"complete_empty" if not candidates else "ready"},candidate_count=len(candidates),finished_at=local_now())
    except ContextCompileError as exc:
        if exc.code=="cancelled": cancel_job(job_id)
        else:
            current = get_job(job_id)
            late = current is not None and current.phase in {"persisting", "finalizing"}
            code=f"CONTEXT_{(exc.diagnostic_code or exc.code).upper()}"; update_job(job_id,status="failed",message="Не удалось завершить сохранение контекста. Проверьте результаты контекста перед повторной сборкой." if late else "Не удалось собрать контекст для одного из фрагментов. Данные не изменены.",progress=100,error_code=code,error=code,diagnostic_code=str(exc.diagnostic_code or ""),finished_at=local_now())
    except Exception:
        current = get_job(job_id)
        late = current is not None and current.phase in {"persisting", "finalizing"}
        update_job(job_id,status="failed",message="Не удалось завершить сохранение контекста. Проверьте результаты контекста перед повторной сборкой." if late else "Не удалось собрать контекст. Данные не изменены.",progress=100,error_code="CONTEXT_INTERNAL_ERROR",error="CONTEXT_INTERNAL_ERROR",finished_at=local_now())
    finally:
        JOB_CAPACITY.release()


def run_analyze_job(job_id: str, project: str, query: str, uploaded: list[tuple[str, bytes]], profile_id: str | None, intake: dict[str, Any] | None = None) -> None:
    try:
        _run_analyze_job(job_id, project, query, uploaded, profile_id, intake)
    finally:
        JOB_CAPACITY.release()


def _run_analyze_job(job_id: str, project: str, query: str, uploaded: list[tuple[str, bytes]], profile_id: str | None, intake: dict[str, Any] | None = None) -> None:
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
        if intake:
            ControlledIntake().finish(intake["operation_id"], "failed")
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
        result={**asdict(package), "controlled_intake": intake or {}},
    )
    if intake:
        ControlledIntake().finish(intake["operation_id"], "done")


def cancel_event_for(job_id: str) -> threading.Event:
    with JOBS_LOCK:
        return JOB_CANCEL_EVENTS.setdefault(job_id, threading.Event())


def job_timeout_seconds() -> int:
    from .config import SETTINGS

    if SETTINGS is not None:
        return SETTINGS.analyze_job_timeout_seconds
    return RUNNING_JOB_TIMEOUT_SECONDS


def job_timeout_for(job: JobRecord | None) -> int:
    if job is not None and isinstance(job.timeout_seconds, int) and job.timeout_seconds > 0:
        return job.timeout_seconds
    return job_timeout_seconds()


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
        if job.job_type == "context_compile" and job.phase in {"persisting", "finalizing"}:
            job.message = "Сохранение контекста уже завершается. Дождитесь результата."
            job.updated_at = local_now()
            return job
        cancel_event_for(job_id).set()
        job.cancellation_requested = True
        job.status = "cancelled"
        job.progress = 100
        if reason == "timeout":
            job.message = "Сборка контекста остановлена по лимиту времени. Данные не изменены." if job.job_type == "context_compile" else "Задача остановлена по лимиту времени; активная транскрибация завершена."
            job.error = "CONTEXT_TIMEOUT" if job.job_type == "context_compile" else "Job timeout. Проверь тяжелые вложения или увеличь processing.analyze_job_timeout_seconds."
        else:
            job.message = "Сборка контекста отменена. Данные не изменены." if job.job_type == "context_compile" else "Задача отменена; активная транскрибация завершена."
            job.error = "CONTEXT_CANCELLED" if job.job_type == "context_compile" else "Job cancelled by user."
        job.updated_at = local_now()
        if job.job_type == "context_compile":
            job.finished_at = job.updated_at; _persist_context_attempt(job)
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
        if job.job_type == "context_compile": _persist_context_attempt(job)

def _persist_context_attempt(job: JobRecord) -> None:
    if job.workspace_id and job.artifact_id:
        ContextAttemptStore(ControlledIntake().store).save(job.workspace_id, job.artifact_id, job.__dict__)

def active_context_job(workspace_id: str, artifact_id: str) -> JobRecord | None:
    with JOBS_LOCK:
        return next((job for job in JOBS.values() if job.job_type == "context_compile" and job.workspace_id == workspace_id and job.artifact_id == artifact_id and job.status not in TERMINAL_STATUSES), None)


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
    if age < job_timeout_for(job):
        return
    cancel_job(job.id, "timeout")
