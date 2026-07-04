from __future__ import annotations

import sys
from pathlib import Path

from .config import MEDIA_EXTENSIONS, SETTINGS
from .excel_preview import extract_xlsx_normalized
from .transcription import transcribe_file


if SETTINGS is not None and str(SETTINGS.obsidian_work) not in sys.path:
    sys.path.insert(0, str(SETTINGS.obsidian_work))

try:
    from update_project_sources import extract_text  # type: ignore
except Exception:
    extract_text = None  # type: ignore


def safe_filename(name: str) -> str:
    import re

    name = Path(name).name.strip() or "upload.bin"
    return re.sub(r"[^\w.\-а-яА-ЯёЁ +()#]", "_", name)


def extract_upload_text(path: Path, run_dir: Path) -> tuple[str, str, str]:
    suffix = path.suffix.lower()
    if suffix in MEDIA_EXTENSIONS:
        transcript, status = transcribe_file(path, run_dir)
        return transcript, "media", status
    if suffix in {".txt", ".md"}:
        return path.read_text(encoding="utf-8", errors="ignore"), "text", "текст прочитан"
    if suffix == ".xlsx":
        text, note = extract_xlsx_normalized(path)
        return text, "xlsx", note
    if suffix in {".pdf", ".docx"} and extract_text:
        text, note = extract_text(path)
        return text, suffix.lstrip("."), note
    return "", "unknown", "тип файла не поддержан или парсер недоступен"
