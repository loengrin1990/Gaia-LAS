"""Deterministic semantic units with offsets into the sanitized source text."""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


SECTION_TYPES = {
    "требования": "requirement", "решения": "decision", "риски": "risk",
    "открытые вопросы": "open_question", "действия": "action",
}


@dataclass(frozen=True)
class ContextChunk:
    index: int
    text: str
    start: int
    end: int
    boundary: str
    section_heading: str = ""
    section_stack: tuple[str, ...] = ()
    section_type_hint: str | None = None


class ChunkLimitError(ValueError):
    pass


def split_context(text: str, chunk_char_limit: int, chunk_max_units: int, overlap_chars: int, max_chunks: int) -> list[ContextChunk]:
    """Return one model-safe semantic unit per chunk.

    ``chunk_max_units`` and ``overlap_chars`` remain validated for compatibility,
    but units are deliberately not batched: type evidence is meaningful only in
    the section in which it appears.
    """
    if chunk_char_limit <= 0 or chunk_max_units <= 0 or overlap_chars < 0 or overlap_chars >= chunk_char_limit:
        raise ChunkLimitError("Некорректные ограничения фрагментов.")
    units = _semantic_units(text, chunk_char_limit)
    if len(units) > max_chunks:
        raise ChunkLimitError("Материал содержит слишком много фрагментов.")
    return [ContextChunk(index, value, start, start + len(value), boundary, heading, stack, hint)
            for index, (start, value, boundary, heading, stack, hint) in enumerate(units)]


def canonical_section_type(value: str) -> str | None:
    """Classify only a complete, canonical section heading; never a substring."""
    normalized = unicodedata.normalize("NFKC", value).strip()
    normalized = re.sub(r"^#{1,6}\s*", "", normalized)
    normalized = normalized.rstrip().removesuffix(":").rstrip()
    normalized = " ".join(normalized.casefold().replace("ё", "е").split())
    return SECTION_TYPES.get(normalized)


def _semantic_units(text: str, limit: int) -> list[tuple[int, str, str, str, tuple[str, ...], str | None]]:
    headings = _headings(text)
    result: list[tuple[int, str, str, str, tuple[str, ...], str | None]] = []
    for match in re.finditer(r".*?(?:\n\s*\n|\Z)", text, re.S):
        start, value = match.start(), match.group()
        if not value or not value.strip() or _is_heading_block(value):
            continue
        heading, stack, hint = _section_context(headings, start)
        result.extend(_split_value(start, value, limit, heading, stack, hint))
    return result


def _headings(text: str) -> list[tuple[int, int, str, int, str | None]]:
    result: list[tuple[int, int, str, int, str | None]] = []
    for match in re.finditer(r"(?m)^(?P<marker>#{1,6}\s+)?(?P<title>[^\n]+?)\s*$", text):
        marker, title = match.group("marker"), match.group("title")
        typed = canonical_section_type(match.group())
        # Plain lines are headings only when they are canonical typed headings.
        if not marker and not typed:
            continue
        level = len(marker.strip()) if marker else 1
        result.append((match.start(), match.end(), title.strip(), level, typed))
    return result


def _is_heading_block(value: str) -> bool:
    stripped = value.strip()
    return bool(re.fullmatch(r"(?:#{1,6}\s+)?[^\n]+", stripped) and (stripped.startswith("#") or canonical_section_type(stripped)))


def _section_context(headings: list[tuple[int, int, str, int, str | None]], position: int) -> tuple[str, tuple[str, ...], str | None]:
    active: list[tuple[str, int, str | None]] = []
    for start, _end, title, level, typed in headings:
        if start >= position:
            break
        while active and active[-1][1] >= level:
            active.pop()
        active.append((title, level, typed))
    stack = tuple(item[0] for item in active)
    heading = stack[-1] if stack else ""
    hint = next((item[2] for item in reversed(active) if item[2]), None)
    return heading, stack, hint


def _split_value(start: int, value: str, limit: int, heading: str, stack: tuple[str, ...], hint: str | None) -> list[tuple[int, str, str, str, tuple[str, ...], str | None]]:
    if len(value) <= limit:
        return [(start, value, "paragraph", heading, stack, hint)]
    result = []
    cursor = start
    for sentence in re.findall(r"[^.!?\n]+[.!?]?\s*|\n+", value):
        if len(sentence) <= limit:
            result.append((cursor, sentence, "sentence", heading, stack, hint))
        else:
            for offset in range(0, len(sentence), limit):
                result.append((cursor + offset, sentence[offset:offset + limit], "character", heading, stack, hint))
        cursor += len(sentence)
    return result
