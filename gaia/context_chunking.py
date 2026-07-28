"""Deterministic structural chunks with offsets into the sanitized source text."""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ContextChunk:
    index: int
    text: str
    start: int
    end: int
    boundary: str


class ChunkLimitError(ValueError):
    pass


def split_context(text: str, chunk_char_limit: int, chunk_max_units: int, overlap_chars: int, max_chunks: int) -> list[ContextChunk]:
    if chunk_char_limit <= 0 or chunk_max_units <= 0 or overlap_chars < 0 or overlap_chars >= chunk_char_limit:
        raise ChunkLimitError("Некорректные ограничения фрагментов.")
    units = _units(text, chunk_char_limit)
    chunks: list[ContextChunk] = []
    pending: list[tuple[int, str, str]] = []
    size = 0
    for start, unit, boundary in units:
        if pending and (size + len(unit) > chunk_char_limit or len(pending) >= chunk_max_units):
            chunks.append(_make(chunks, pending))
            pending, size = _overlap(pending, overlap_chars), sum(len(value) for _, value, _ in _overlap(pending, overlap_chars))
        pending.append((start, unit, boundary)); size += len(unit)
    if pending:
        chunks.append(_make(chunks, pending))
    if len(chunks) > max_chunks:
        raise ChunkLimitError("Материал содержит слишком много фрагментов.")
    return chunks


def _make(chunks: list[ContextChunk], units: list[tuple[int, str, str]]) -> ContextChunk:
    start = units[0][0]; text = "".join(value for _, value, _ in units)
    return ContextChunk(len(chunks), text, start, start + len(text), units[0][2])


def _overlap(units: list[tuple[int, str, str]], count: int) -> list[tuple[int, str, str]]:
    if not count:
        return []
    kept: list[tuple[int, str, str]] = []
    total = 0
    for unit in reversed(units):
        if total + len(unit[1]) > count:
            break
        kept.insert(0, unit); total += len(unit[1])
    return kept


def _units(text: str, limit: int) -> list[tuple[int, str, str]]:
    parts = [(match.start(), match.group(), "paragraph") for match in re.finditer(r".*?(?:\n\s*\n|\Z)", text, re.S) if match.group()]
    result: list[tuple[int, str, str]] = []
    for start, value, boundary in parts:
        if len(value) <= limit:
            result.append((start, value, boundary)); continue
        cursor = start
        sentences = re.findall(r"[^.!?\n]+[.!?]?\s*|\n+", value)
        for sentence in sentences:
            if len(sentence) <= limit:
                result.append((cursor, sentence, "sentence")); cursor += len(sentence)
            else:
                for offset in range(0, len(sentence), limit):
                    piece = sentence[offset:offset + limit]
                    result.append((cursor + offset, piece, "character"))
                cursor += len(sentence)
    return result
