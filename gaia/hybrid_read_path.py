"""Typed, bounded, source-first read path for the D3.1A experiment."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Protocol

from .context_chunking import ChunkLimitError, split_context
from .operational_context import authority_scope_matches, normalize_authority_scope
from .source_evidence import MaterialAvailability, SourceMaterial


_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_CURRENT_RE = re.compile(r"\b(?:текущ\w*|сейчас|действующ\w*)\b", re.IGNORECASE)
_HISTORICAL_RE = re.compile(r"\b(?:историческ\w*|предыдущ\w*|замещенн\w*|устаревш\w*)\b", re.IGNORECASE)
_PENDING_RE = re.compile(r"\b(?:будет\s+пересмотрен\w*|пересмотр\w*\s+после|ожида\w*|отлож\w*|планиру\w*)\b", re.IGNORECASE)
_POSITIVE_STATUS_RE = re.compile(r"\b(?:утвержд\w*|финальн\w*|текущ\w*|замещ\w*)\b", re.IGNORECASE)
_UNRESOLVED_STATUS_RE = re.compile(r"\b(?:не\W*согласован\w*|не\W*принят\w*|открыт\w*\W+вопрос|требу\w*\W+уточнен\w*)\b", re.IGNORECASE)


def normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")


def tokens(value: str) -> set[str]:
    return {token for token in _TOKEN_RE.findall(normalize(value)) if len(token) >= 2}


def stems(values: set[str]) -> set[str]:
    suffixes = ("иями", "ями", "ами", "ого", "ему", "ому", "его", "ими", "ых", "ий", "ый", "ой", "ая", "ое", "ые", "ам", "ям", "ах", "ях", "ов", "ев", "ом", "ем", "ую", "юю", "ия", "ие", "ию", "а", "я", "ы", "и", "у", "ю", "е", "о")
    result: set[str] = set()
    for value in values:
        suffix = next((item for item in suffixes if value.endswith(item) and len(value) - len(item) >= 4), "")
        result.add(value[:-len(suffix)] if suffix else value)
    return result


def trigrams(value: str) -> set[str]:
    compact = " ".join(_TOKEN_RE.findall(normalize(value)))
    if len(compact) < 3:
        return {compact} if compact else set()
    return {compact[index:index + 3] for index in range(len(compact) - 2)}


def lexical_score(query: str, text: str) -> float:
    query_tokens, text_tokens = tokens(query), tokens(text)
    if not query_tokens or not text_tokens:
        return 0.0
    direct = len(query_tokens & text_tokens) / len(query_tokens)
    query_stems, text_stems = stems(query_tokens), stems(text_tokens)
    stemmed = len(query_stems & text_stems) / len(query_stems)
    if max(direct, stemmed) <= 0:
        return 0.0
    query_grams, text_grams = trigrams(query), trigrams(text)
    grams = len(query_grams & text_grams) / len(query_grams | text_grams) if query_grams and text_grams else 0.0
    return round(max(direct, stemmed) + 0.35 * grams, 6)


def explicit_status_bonus(text: str) -> float:
    """Prefer literal status evidence without deriving status from dates."""
    return min(2, len(_POSITIVE_STATUS_RE.findall(text))) * 0.25 - min(2, len(_UNRESOLVED_STATUS_RE.findall(text))) * 0.25


def status_alignment_bonus(query: str, text: str) -> float:
    """Use a literal present-state cue only when the query asks for one."""
    asks_for_current = bool({"сейчас", "текущ", "действующ"} & stems(tokens(query)))
    return 0.1 if asks_for_current and _CURRENT_RE.search(text) else 0.0


@dataclass(frozen=True)
class SourceRetrievalRequest:
    query: str
    workspace_id: str
    max_hits: int = 4
    max_chars: int = 20_000

    def __post_init__(self) -> None:
        if not self.query.strip() or not self.workspace_id or self.max_hits < 1 or self.max_chars < 1:
            raise ValueError("Source retrieval request is invalid.")


@dataclass(frozen=True)
class SourceEvidenceHit:
    source_id: str
    sanitized_id: str
    workspace_id: str
    excerpt: str
    start_offset: int
    end_offset: int
    created_at: str
    retrieval_score: float
    literal_annotations: dict[str, Any]
    selection_role: str = "primary_hit"
    expanded_from: dict[str, Any] = field(default_factory=dict)
    expansion_relation: str = ""

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class SourceRetrievalResult:
    hits: tuple[SourceEvidenceHit, ...]
    material_diagnostics: tuple[dict[str, str], ...]
    diagnostics: dict[str, int | str]

    def as_dict(self) -> dict[str, Any]:
        return {"hits": [hit.as_dict() for hit in self.hits], "material_diagnostics": list(self.material_diagnostics), "diagnostics": dict(self.diagnostics)}


class SourceRetriever(Protocol):
    def retrieve(self, request: SourceRetrievalRequest, materials: tuple[SourceMaterial, ...]) -> SourceRetrievalResult: ...


class DeterministicLexicalSourceRetriever:
    """D3.1 implementation, deliberately separate from the retrieval contract."""

    def retrieve(self, request: SourceRetrievalRequest, materials: tuple[SourceMaterial, ...]) -> SourceRetrievalResult:
        candidates: list[SourceEvidenceHit] = []
        diagnostics = [material.diagnostic() for material in materials if material.state is not MaterialAvailability.AVAILABLE]
        scanned_chunks = 0
        for material in materials:
            if material.state is not MaterialAvailability.AVAILABLE:
                continue
            try:
                chunks = split_context(material.text, 5_000, 1, 0, 2_000)
            except ChunkLimitError:
                diagnostics.append({**material.diagnostic(), "detail": "chunk_limit_exceeded"})
                continue
            document_status_bonus = explicit_status_bonus(material.text)
            for index, chunk in enumerate(chunks):
                scanned_chunks += 1
                end = chunks[min(index + 2, len(chunks) - 1)].end
                excerpt = material.text[chunk.start:end]
                base_score = lexical_score(request.query, excerpt)
                if base_score <= 0:
                    continue
                score = round(base_score + document_status_bonus + status_alignment_bonus(request.query, chunk.text), 6)
                candidates.append(SourceEvidenceHit(
                    material.source_id, material.sanitized_id, material.workspace_id, excerpt,
                    chunk.start, end, material.created_at, score, _literal_annotations(chunk.section_heading, chunk.section_stack, chunk.text),
                ))
        candidates.sort(key=lambda item: (-item.retrieval_score, item.source_id, item.start_offset, item.sanitized_id))
        selected: list[SourceEvidenceHit] = []
        selected_per_source: dict[str, int] = {}
        used = 0
        covered_query_stems: set[str] = set()
        query_stems = stems(tokens(request.query))
        coverage_promotions = 0
        first_score = candidates[0].retrieval_score if candidates else 0.0
        remaining = list(candidates)
        while remaining and len(selected) < request.max_hits:
            eligible = [
                item for item in remaining
                if selected_per_source.get(item.source_id, 0) < 2
                and used + len(item.excerpt) <= request.max_chars
            ]
            if not eligible:
                break
            item = eligible[0]
            if selected:
                coverage_candidate = next(
                    (
                        candidate for candidate in eligible
                        if candidate.retrieval_score >= first_score * 0.70
                        and (stems(tokens(candidate.excerpt)) & query_stems) - covered_query_stems
                    ),
                    None,
                )
                if coverage_candidate is not None:
                    item = coverage_candidate
                    if item is not eligible[0]:
                        coverage_promotions += 1
            selected.append(item)
            selected_per_source[item.source_id] = selected_per_source.get(item.source_id, 0) + 1
            used += len(item.excerpt)
            covered_query_stems.update(stems(tokens(item.excerpt)) & query_stems)
            remaining.remove(item)
        expanded, expansion_diagnostics = self._expand_context(selected, materials, request.max_chars - used)
        return SourceRetrievalResult(
            tuple(selected + expanded), tuple(diagnostics),
            {"implementation": "deterministic_lexical_v1", "materials_scanned": len(materials), "chunks_scanned": scanned_chunks, "hits_selected": len(selected), "selected_chars": used, "coverage_promotions": coverage_promotions, "context_expansion_chars": sum(len(item.excerpt) for item in expanded), "context_expansion_count": len(expanded), **expansion_diagnostics},
        )

    def _expand_context(self, primary_hits: list[SourceEvidenceHit], materials: tuple[SourceMaterial, ...], remaining_chars: int) -> tuple[list[SourceEvidenceHit], dict[str, int]]:
        """Add bounded predecessor sections from documents that already won ranking."""
        by_sanitized = {material.sanitized_id: material for material in materials}
        expanded: list[SourceEvidenceHit] = []
        used = 0
        skipped_oversize = 0
        seen_documents: set[str] = set()
        occupied = {(hit.sanitized_id, hit.start_offset, hit.end_offset) for hit in primary_hits}
        for primary in primary_hits:
            if primary.sanitized_id in seen_documents:
                continue
            seen_documents.add(primary.sanitized_id)
            material = by_sanitized.get(primary.sanitized_id)
            if material is None:
                continue
            chunks = split_context(material.text, 5_000, 1, 0, 2_000)
            chunk_index = next((index for index, chunk in enumerate(chunks) if chunk.start <= primary.start_offset < chunk.end), None)
            if chunk_index is None:
                continue
            sections = _structural_sections(chunks)
            section_index = next((index for index, section in enumerate(sections) if section[0] <= chunk_index <= section[1]), None)
            if section_index is None or chunk_index == sections[section_index][0]:
                continue
            for predecessor in reversed(sections[max(0, section_index - 2):section_index]):
                first, last, stack = predecessor
                start, end = chunks[first].start, chunks[last].end
                excerpt = material.text[start:end]
                if len(excerpt) > 600:
                    skipped_oversize += 1
                    continue
                if used + len(excerpt) > min(1_600, remaining_chars):
                    continue
                if any(existing_id == material.sanitized_id and not (end <= existing_start or start >= existing_end) for existing_id, existing_start, existing_end in occupied):
                    continue
                expanded.append(SourceEvidenceHit(material.source_id, material.sanitized_id, material.workspace_id, excerpt, start, end, material.created_at, 0.0, _literal_annotations(chunks[first].section_heading, stack, excerpt), "context_expansion", {"source_id": primary.source_id, "sanitized_id": primary.sanitized_id, "start_offset": primary.start_offset, "end_offset": primary.end_offset}, "preceding_section"))
                occupied.add((material.sanitized_id, start, end)); used += len(excerpt)
        return expanded, {"context_expansion_sections_skipped_oversize": skipped_oversize}


def _structural_sections(chunks: list[Any]) -> list[tuple[int, int, tuple[str, ...]]]:
    sections: list[tuple[int, int, tuple[str, ...]]] = []
    for index, chunk in enumerate(chunks):
        stack = chunk.section_stack
        if sections and sections[-1][2] == stack:
            first, _last, current = sections[-1]; sections[-1] = (first, index, current)
        else:
            sections.append((index, index, stack))
    return sections


@dataclass(frozen=True)
class OperationalOverlayHit:
    item: dict[str, Any]
    relevance_score: float

    def as_dict(self) -> dict[str, Any]:
        return {"item": dict(self.item), "relevance_score": self.relevance_score}


class OperationalOverlaySelector:
    """Pure relevance selection over already eligible Operational Context records."""

    def select(self, query: str, eligible_items: tuple[dict[str, Any], ...], *, max_items: int = 3, max_chars: int = 12_000) -> tuple[OperationalOverlayHit, ...]:
        if max_items < 1 or max_chars < 1:
            raise ValueError("Operational overlay limits are invalid.")
        candidates: list[OperationalOverlayHit] = []
        for item in eligible_items:
            if item.get("lifecycle") != "active" or item.get("confirmation") != "confirmed":
                continue
            searchable = "\n".join(str(item.get(key) or "") for key in ("kind", "subject_ref", "value", "reference"))
            score = lexical_score(query, searchable)
            if score > 0:
                candidates.append(OperationalOverlayHit(dict(item), score))
        candidates.sort(key=lambda hit: (-hit.relevance_score, str(hit.item.get("id") or "")))
        selected: list[OperationalOverlayHit] = []
        used = 0
        for item in candidates:
            cost = len(str(item.as_dict()))
            if used + cost > max_chars:
                continue
            selected.append(item); used += cost
            if len(selected) >= max_items:
                break
        return tuple(selected)


def select_applicable_current_authority(query: str, eligible_items: tuple[dict[str, Any], ...], *, max_items: int = 8, max_chars: int = 12_000) -> tuple[OperationalOverlayHit, ...]:
    """Select only reviewed, query-scoped decision authority from the bounded OC input."""
    if max_items < 1 or max_chars < 1:
        raise ValueError("Current authority limits are invalid.")
    selected: list[OperationalOverlayHit] = []
    used = 0
    for item in sorted(eligible_items, key=lambda value: (str(value.get("kind") or ""), str(value.get("subject_ref") or ""), str(value.get("id") or ""))):
        if item.get("kind") != "decision" or item.get("lifecycle") != "active" or item.get("confirmation") != "confirmed":
            continue
        if normalize_authority_scope(item.get("authority_scope")) is None or not authority_scope_matches(item.get("authority_scope"), query):
            continue
        hit = OperationalOverlayHit(dict(item), 1.0)
        cost = len(str(hit.as_dict()))
        if used + cost > max_chars:
            continue
        selected.append(hit); used += cost
        if len(selected) >= max_items:
            break
    return tuple(selected)


@dataclass(frozen=True)
class HybridEvidencePackage:
    query: str
    source_evidence: tuple[SourceEvidenceHit, ...]
    rehydrated_grounding: tuple[SourceEvidenceHit, ...]
    rehydrated_oc_grounding: tuple[OperationalOverlayHit, ...]
    oc_overlay: tuple[OperationalOverlayHit, ...]
    applicable_current_authority: tuple[OperationalOverlayHit, ...]
    observed_tensions: tuple[dict[str, str], ...]
    unresolved: tuple[str, ...]
    diagnostics: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "gaia.d3.hybrid-evidence-package.v2",
            "query": self.query,
            "source_evidence": [item.as_dict() for item in self.source_evidence],
            "rehydrated_grounding": [item.as_dict() for item in self.rehydrated_grounding],
            "rehydrated_oc_grounding": [item.as_dict() for item in self.rehydrated_oc_grounding],
            "oc_overlay": [item.as_dict() for item in self.oc_overlay],
            "applicable_current_authority": [item.as_dict() for item in self.applicable_current_authority],
            "observed_tensions": list(self.observed_tensions),
            "unresolved": list(self.unresolved),
            "diagnostics": dict(self.diagnostics),
        }


def assemble_hybrid_package(query: str, source_result: SourceRetrievalResult, oc_overlay: tuple[OperationalOverlayHit, ...], applicable_current_authority: tuple[OperationalOverlayHit, ...] = (), rehydrated_grounding: tuple[SourceEvidenceHit, ...] = (), rehydrated_oc_grounding: tuple[OperationalOverlayHit, ...] = ()) -> HybridEvidencePackage:
    unresolved = () if source_result.hits else ("no_matching_queryable_source_evidence",)
    tensions = _observed_tensions(source_result.hits, oc_overlay)
    return HybridEvidencePackage(query, source_result.hits, rehydrated_grounding, rehydrated_oc_grounding, oc_overlay, applicable_current_authority, tensions, unresolved, {
        "source": source_result.as_dict(),
        "oc_selected": len(oc_overlay),
        "applicable_current_authority_selected": len(applicable_current_authority),
        "rehydrated_oc_selected": len(rehydrated_oc_grounding),
        "oc_is_not_exhaustive_project_knowledge": True,
    })


def _literal_annotations(heading: str, section_stack: tuple[str, ...], text: str) -> dict[str, Any]:
    cues: list[str] = []
    if _CURRENT_RE.search(text): cues.append("current")
    if _HISTORICAL_RE.search(text): cues.append("historical")
    return {"heading": heading, "section_stack": list(section_stack), "literal_status_cues": cues}


def _observed_tensions(source_hits: tuple[SourceEvidenceHit, ...], oc_overlay: tuple[OperationalOverlayHit, ...]) -> tuple[dict[str, str], ...]:
    if not any("current" in hit.literal_annotations.get("literal_status_cues", ()) for hit in source_hits):
        return ()
    pending = [item for item in oc_overlay if _PENDING_RE.search("\n".join(str(item.item.get(key) or "") for key in ("value", "reference")))]
    if not pending:
        return ()
    return tuple({"kind": "possible_source_oc_tension", "source_id": hit.source_id, "oc_item_id": str(item.item.get("id") or "")}
                 for hit in source_hits if "current" in hit.literal_annotations.get("literal_status_cues", ()) for item in pending)
