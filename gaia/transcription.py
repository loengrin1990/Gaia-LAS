from __future__ import annotations

import subprocess
from pathlib import Path

from .config import SETTINGS


def transcriber_path() -> Path:
    return SETTINGS.transcriber_path


def transcribe_file(path: Path, run_dir: Path) -> tuple[str, str]:
    binary = transcriber_path()
    if not binary.exists():
        return "", f"не выполнена: не найден транскрибатор {binary}"
    output_dir = run_dir / "transcripts"
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [str(binary), str(path), "--language", "ru", "-o", str(output_dir)]
    try:
        subprocess.run(command, check=True, timeout=None)
    except subprocess.CalledProcessError as exc:
        return "", f"ошибка транскрибации: код {exc.returncode}"
    candidates = sorted(output_dir.glob(f"{path.stem}*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return "", "транскрибация завершилась, но txt не найден"
    return candidates[0].read_text(encoding="utf-8", errors="ignore"), f"готово: {candidates[0]}"
