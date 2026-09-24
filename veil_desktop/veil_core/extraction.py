"""In-memory text extraction for the desktop Veil input formats."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_UNPACKED_BYTES = 50 * 1024 * 1024
WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class ExtractionError(ValueError):
    pass


def read_material(path: Path) -> tuple[str, str]:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ExtractionError("Файл больше 10 МБ. Разделите его на меньшие части.")
    data = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md"}:
        try:
            return data.decode("utf-8-sig"), "Текстовый файл прочитан."
        except UnicodeDecodeError as exc:
            raise ExtractionError("Текстовый файл должен быть в кодировке UTF-8.") from exc
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(BytesIO(data), strict=True)
            pages = [page.extract_text() or "" for page in reader.pages]
        except Exception as exc:
            raise ExtractionError("Не удалось прочитать PDF. Проверьте файл и защиту паролем.") from exc
        text = "\n\n".join(pages)
        if not text.strip():
            raise ExtractionError("В PDF нет извлекаемого текста. Для скана требуется распознавание текста (OCR).")
        empty = sum(not page.strip() for page in pages)
        note = "PDF прочитан. Изображения и сканы не распознаются."
        if empty:
            note += f" Страниц без извлечённого текста: {empty}."
        return text, note
    if suffix == ".docx":
        try:
            with ZipFile(BytesIO(data)) as archive:
                names = [item for item in archive.infolist() if item.filename.startswith("word/") and item.filename.endswith(".xml")]
                if sum(item.file_size for item in names) > MAX_UNPACKED_BYTES:
                    raise ExtractionError("Содержимое DOCX слишком велико.")
                if "word/document.xml" not in {item.filename for item in names}:
                    raise ExtractionError("В DOCX не найден текст документа.")
                parts = []
                for item in names:
                    if not (item.filename == "word/document.xml" or any(
                        item.filename.startswith("word/" + prefix)
                        for prefix in ("header", "footer", "footnotes", "endnotes", "comments")
                    )):
                        continue
                    root = ElementTree.fromstring(archive.read(item))
                    lines = []
                    for paragraph in root.iter(WORD_NS + "p"):
                        line = "".join(node.text or "" for node in paragraph.iter(WORD_NS + "t"))
                        if line.strip():
                            lines.append(line)
                    parts.extend(lines)
        except (BadZipFile, ElementTree.ParseError, OSError) as exc:
            raise ExtractionError("Не удалось прочитать DOCX.") from exc
        text = "\n".join(parts)
        if not text.strip():
            raise ExtractionError("В DOCX нет извлекаемого текста.")
        return text, "DOCX прочитан. Текст внутри изображений не распознаётся."
    raise ExtractionError("Поддерживаются TXT, MD, PDF и DOCX.")
