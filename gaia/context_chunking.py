"""Deterministic semantic units with offsets into the sanitized source text."""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


SECTION_TYPES = {
    "требования": "requirement", "решения": "decision", "риски": "risk",
    "открытые вопросы": "open_question", "действия": "action",
}
MAX_EVIDENCE_SPANS = 12
LIST_ITEM_RE = re.compile(r"(?m)^[ \t]*(?:[-*+]|\d+[.)])\s+")


@dataclass(frozen=True)
class EvidenceSpan:
    """An ephemeral exact source choice for one semantic unit."""

    id: str
    text: str
    local_start: int
    local_end: int
    global_start: int
    global_end: int


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
    evidence_spans: tuple[EvidenceSpan, ...] = ()


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
    # One returned chunk is exactly one semantic unit and exactly one model call.
    # ``max_chunks`` is kept as the configuration compatibility name.
    if len(units) > max_chunks:
        raise ChunkLimitError("Материал содержит слишком много смысловых единиц.")
    return [ContextChunk(index, value, start, start + len(value), boundary, heading, stack, hint, tuple(_evidence_spans(value, start)))
            for index, (start, value, boundary, heading, stack, hint) in enumerate(units)]


def _evidence_spans(value: str, global_start: int) -> list[EvidenceSpan]:
    """Return exact, bounded choices without modifying sanitized source text.

    List items stay intact with their wrapped continuation lines.  Prose is
    split into sentences.  When there are too many choices, only adjacent
    prose spans are grouped where possible; the final contiguous grouping is
    a deterministic safety valve that retains all source coverage.
    """
    raw: list[tuple[int, int, str]] = []
    lines = list(re.finditer(r".*(?:\n|\Z)", value))
    index = 0
    while index < len(lines):
        line = lines[index]; marker = re.match(r"^[ \t]*(?:[-*+]|\d+[.)])\s+", line.group())
        if marker:
            start, end = line.start(), line.end(); index += 1
            while index < len(lines) and not re.match(r"^[ \t]*(?:[-*+]|\d+[.)])\s+", lines[index].group()) and lines[index].group().strip():
                end = lines[index].end(); index += 1
            raw.append((start, end, "list"))
            continue
        start = line.start(); end = line.end(); index += 1
        while index < len(lines) and not re.match(r"^[ \t]*(?:[-*+]|\d+[.)])\s+", lines[index].group()) and lines[index].group().strip():
            end = lines[index].end(); index += 1
        block = value[start:end]
        for sentence in re.finditer(r"[^.!?\n]+[.!?]?", block):
            raw.append((start + sentence.start(), start + sentence.end(), "prose"))
    cleaned: list[tuple[int, int, str]] = []
    for start, end, kind in raw:
        segment = value[start:end]
        left = len(segment) - len(segment.lstrip())
        right = len(segment.rstrip())
        if segment.strip():
            cleaned.append((start + left, start + right, kind))
    if not cleaned:
        stripped = value.strip()
        start = value.index(stripped)
        cleaned = [(start, start + len(stripped), "prose")]
    cleaned = _bound_evidence_spans(cleaned)
    return [
        EvidenceSpan(f"E{i}", value[start:end], start, end, global_start + start, global_start + end)
        for i, (start, end, _kind) in enumerate(cleaned, 1)
    ]


def _bound_evidence_spans(items: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    if len(items) <= MAX_EVIDENCE_SPANS:
        return items
    list_count = sum(kind == "list" for _, _, kind in items)
    if list_count < MAX_EVIDENCE_SPANS:
        # Preserve each list item, distributing the remaining slots across
        # adjacent prose runs.
        grouped: list[tuple[int, int, str]] = []
        prose: list[tuple[int, int, str]] = []
        remaining_slots = MAX_EVIDENCE_SPANS - list_count
        for item in items:
            if item[2] == "list":
                if prose:
                    groups = min(len(prose), remaining_slots)
                    grouped.extend(_group_prose(prose, groups))
                    remaining_slots -= groups
                    prose = []
                grouped.append(item)
            else:
                prose.append(item)
        if prose:
            grouped.extend(_group_prose(prose, max(1, remaining_slots)))
        if len(grouped) <= MAX_EVIDENCE_SPANS:
            return grouped
    # More than the cap of list items is rare, but no source text may be
    # discarded.  Deterministically group adjacent spans into contiguous
    # choices instead of silently omitting material.
    return _group_prose(items, MAX_EVIDENCE_SPANS)

def _group_prose(items: list[tuple[int,int,str]], slots: int) -> list[tuple[int,int,str]]:
    if not items:
        return []
    slots = max(1, slots)
    size = (len(items) + slots - 1) // slots
    return [
        (group[0][0], group[-1][1], "prose")
        for group in (items[i:i + size] for i in range(0, len(items), size))
    ]


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
        result.extend(_split_list_items(start, value, limit, heading, stack, hint))
    return result


def _split_list_items(start: int, value: str, limit: int, heading: str, stack: tuple[str, ...], hint: str | None) -> list[tuple[int, str, str, str, tuple[str, ...], str | None]]:
    """Keep each independent Markdown or numbered item in its own model unit."""
    markers = list(LIST_ITEM_RE.finditer(value))
    if not markers:
        return _split_value(start, value, limit, heading, stack, hint)
    result: list[tuple[int, str, str, str, tuple[str, ...], str | None]] = []
    if markers[0].start() > 0 and value[:markers[0].start()].strip():
        result.extend(_split_value(start, value[:markers[0].start()], limit, heading, stack, hint))
    for index, marker in enumerate(markers):
        item_start = marker.start()
        item_end = markers[index + 1].start() if index + 1 < len(markers) else len(value)
        item = value[item_start:item_end]
        if len(item) <= limit:
            result.append((start + item_start, item, "list_item", heading, stack, hint))
        else:
            result.extend(_split_value(start + item_start, item, limit, heading, stack, hint))
    return result


def _headings(text: str) -> list[tuple[int, int, str, int, str | None]]:
    result: list[tuple[int, int, str, int, str | None]] = []
    fence_marker = ""
    for match in re.finditer(r"(?m)^.*(?:\n|\Z)", text):
        line = match.group().rstrip("\r\n")
        fence = re.match(r"^[ \t]{0,3}(`{3,}|~{3,})", line)
        if fence:
            marker = fence.group(1)
            if not fence_marker:
                fence_marker = marker[0]
            elif marker[0] == fence_marker:
                fence_marker = ""
            continue
        if fence_marker:
            continue
        heading = re.fullmatch(r"(?P<marker>#{1,6}\s+)?(?P<title>[^\n]+?)\s*", line)
        if not heading:
            continue
        marker, title = heading.group("marker"), heading.group("title")
        typed = canonical_section_type(line)
        # Plain lines are headings only when they are canonical typed headings.
        if not marker and not typed:
            continue
        level = len(marker.strip()) if marker else 1
        result.append((match.start(), match.start() + len(line), title.strip(), level, typed))
    return result


def _is_heading_block(value: str) -> bool:
    stripped = value.strip()
    markdown_heading = re.fullmatch(r"#{1,6}\s+\S[^\n]*", stripped)
    return bool(markdown_heading or canonical_section_type(stripped))


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
