from __future__ import annotations

import re
import sys
import threading
from pathlib import Path
from typing import Callable

from .config import MEDIA_EXTENSIONS, SETTINGS
from .excel_preview import extract_xlsx_normalized
from .transcription import transcribe_file


ExtractText = Callable[[Path], tuple[str, str]]


def safe_filename(name: str) -> str:
    name = Path(name).name.strip() or "upload.bin"
    return re.sub(r"[^\w.\-а-яА-ЯёЁ +()#]", "_", name)


def load_extract_text() -> ExtractText | None:
    obsidian_work = getattr(SETTINGS, "obsidian_work", None)
    if obsidian_work is not None and str(obsidian_work) not in sys.path:
        sys.path.insert(0, str(obsidian_work))
    try:
        from update_project_sources import extract_text as external_extract_text  # type: ignore
    except Exception:
        return None
    return external_extract_text


def extract_text(path: Path) -> tuple[str, str] | None:
    external_extract_text = load_extract_text()
    if external_extract_text is None:
        return None
    return external_extract_text(path)


def extract_upload_text(
    path: Path,
    run_dir: Path,
    cancel_event: threading.Event | None = None,
) -> tuple[str, str, str]:
    suffix = path.suffix.lower()
    if suffix in MEDIA_EXTENSIONS:
        transcript, status = transcribe_file(path, run_dir, cancel_event=cancel_event)
        return transcript, "media", status
    if suffix in {".txt", ".md"}:
        return path.read_text(encoding="utf-8", errors="ignore"), "text", "текст прочитан"
    if suffix == ".xlsx":
        text, note = extract_xlsx_normalized(path)
        return text, "xlsx", note
    if suffix in {".pdf", ".docx"}:
        extracted = extract_text(path)
        if extracted is not None:
            text, note = extracted
            return text, suffix.lstrip("."), note
    return "", "unknown", "тип файла не поддержан или парсер недоступен"
