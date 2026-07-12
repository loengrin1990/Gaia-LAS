from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

from .config import SETTINGS


def transcriber_path() -> Path:
    return SETTINGS.transcriber_path


def transcribe_file(
    path: Path,
    run_dir: Path,
    cancel_event: threading.Event | None = None,
) -> tuple[str, str]:
    binary = transcriber_path()
    if not binary.exists():
        return "", f"не выполнена: не найден транскрибатор {binary}"
    output_dir = run_dir / "transcripts"
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [str(binary), str(path), "--language", "ru", "-o", str(output_dir)]
    process = subprocess.Popen(command)
    deadline = time.monotonic() + SETTINGS.transcription_timeout_seconds
    while process.poll() is None:
        if cancel_event is not None and cancel_event.is_set():
            stop_process(process)
            return "", "транскрибация отменена: процесс остановлен"
        if time.monotonic() >= deadline:
            stop_process(process)
            return "", f"превышен лимит транскрибации: {SETTINGS.transcription_timeout_seconds} с"
        time.sleep(0.1)
    if process.returncode:
        return "", f"ошибка транскрибации: код {process.returncode}"
    candidates = sorted(output_dir.glob(f"{path.stem}*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return "", "транскрибация завершилась, но txt не найден"
    return candidates[0].read_text(encoding="utf-8", errors="ignore"), f"готово: {candidates[0]}"


def stop_process(process: subprocess.Popen[object]) -> None:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
