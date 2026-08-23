#!/usr/bin/env python3
"""Build a bounded, read-only source-first retrieval package from a frozen bundle.

The scorer is deliberately generic and deterministic: normalized token overlap
is combined with character trigram Jaccard similarity.  It never uses dates as
relevance, and stable metadata keys resolve score ties.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from gaia.context_chunking import split_context


TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
SUBJECT_PREFIX_RE = re.compile(r"^\s*(?:[-*+]\s*)?([^:\n]{1,100}):", re.MULTILINE)
POSITIVE_STATUS_RE = re.compile(r"утвержд|финальн|текущ|замещ", re.IGNORECASE)
UNRESOLVED_STATUS_RE = re.compile(r"не\W*согласован|не\W*принят|открыт\w*\W+вопрос|требу\w*\W+уточнен", re.IGNORECASE)


@dataclass(frozen=True)
class SourceDocument:
    source_id: str
    sanitized_id: str
    ordinal: int
    created_at: str
    text: str


def normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")


def tokens(value: str) -> set[str]:
    return {token for token in TOKEN_RE.findall(normalize(value)) if len(token) >= 2}


def stems(values: set[str]) -> set[str]:
    """Use a small language-agnostic suffix fallback for inflected tokens."""
    suffixes = ("иями", "ями", "ами", "ого", "ему", "ому", "его", "ими", "ых", "ий", "ый", "ой", "ая", "ое", "ые", "ам", "ям", "ах", "ях", "ов", "ев", "ом", "ем", "ую", "юю", "ия", "ий", "ие", "иям", "ию", "ию", "а", "я", "ы", "и", "у", "ю", "е", "о")
    result = set()
    for value in values:
        suffix = next((item for item in suffixes if value.endswith(item) and len(value) - len(item) >= 4), "")
        result.add(value[:-len(suffix)] if suffix else value)
    return result


def trigrams(value: str) -> set[str]:
    compact = " ".join(TOKEN_RE.findall(normalize(value)))
    if len(compact) < 3:
        return {compact} if compact else set()
    return {compact[index:index + 3] for index in range(len(compact) - 2)}


def lexical_score(query: str, text: str) -> float:
    """Generic relevance score: token recall plus bounded trigram similarity."""
    query_tokens, text_tokens = tokens(query), tokens(text)
    if not query_tokens or not text_tokens:
        return 0.0
    direct_overlap = len(query_tokens & text_tokens) / len(query_tokens)
    stemmed_overlap = len(stems(query_tokens) & stems(text_tokens)) / len(stems(query_tokens))
    overlap = max(direct_overlap, stemmed_overlap)
    query_grams, text_grams = trigrams(query), trigrams(text)
    grams = len(query_grams & text_grams) / len(query_grams | text_grams) if query_grams and text_grams else 0.0
    return round(overlap + 0.35 * grams, 6)


def explicit_status_bonus(text: str) -> float:
    """Prefer literal approved/current evidence; never derive status from dates."""
    positive = min(2, len(POSITIVE_STATUS_RE.findall(text))) * 0.25
    unresolved = min(2, len(UNRESOLVED_STATUS_RE.findall(text))) * 0.25
    return positive - unresolved


def _registry(bundle: Path) -> dict[str, Any]:
    path = bundle / "shared_storage" / "metadata" / "registry.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_sources(bundle: Path) -> list[SourceDocument]:
    registry = _registry(bundle)
    objects = registry.get("objects", {})
    if not isinstance(objects, dict):
        raise ValueError("Frozen registry has no object map.")
    sanitized_root = bundle / "pilot_workspace" / "storage" / "sanitized"
    source_by_sanitized: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for item in objects.values():
        if not isinstance(item, dict) or item.get("kind") != "sanitized":
            continue
        workspace = str(item.get("workspace_id") or "")
        identifier = str(item.get("id") or "")
        if not workspace or not identifier or not (sanitized_root / workspace / f"{identifier}.txt").is_file():
            continue
        extraction_id = next(iter(item.get("parents") or ()), "")
        extraction = objects.get(extraction_id, {})
        source_id = next(iter(extraction.get("parents") or ()), "") if isinstance(extraction, dict) else ""
        source = objects.get(source_id, {})
        if not isinstance(source, dict) or source.get("kind") != "source":
            continue
        source_by_sanitized.append((source, item))
    ordered = sorted(source_by_sanitized, key=lambda pair: (str(pair[0].get("created_at") or ""), str(pair[0].get("id") or "")))
    return [
        SourceDocument(
            source_id=str(source["id"]), sanitized_id=str(sanitized["id"]), ordinal=index,
            created_at=str(source.get("created_at") or ""),
            text=(sanitized_root / str(sanitized["workspace_id"]) / f"{sanitized['id']}.txt").read_text(encoding="utf-8"),
        )
        for index, (source, sanitized) in enumerate(ordered, start=1)
    ]


def literal_subject_scope(excerpt: str) -> str | None:
    match = SUBJECT_PREFIX_RE.search(excerpt)
    return match.group(1).strip() if match else None


def query_subject_scope(query: str, excerpt: str) -> str | None:
    labels = [match.group(1).strip() for match in SUBJECT_PREFIX_RE.finditer(excerpt)]
    if not labels:
        return None
    def affinity(label: str) -> tuple[float, float, str]:
        label_grams = trigrams(label)
        contained = len(label_grams & trigrams(query)) / len(label_grams) if label_grams else 0.0
        return (contained, lexical_score(query, label), label)
    return max(labels, key=affinity)


def literal_status_hint(excerpt: str) -> str | None:
    folded = normalize(excerpt)
    if "историческ" in folded or "замещенн" in folded or "предыдущ" in folded:
        return "historical_superseded"
    if "текущее" in folded:
        return "current"
    return None


def status_alignment_bonus(query: str, excerpt: str) -> float:
    """Reward an explicit present-state cue only when the query asks for it."""
    query_words = tokens(query)
    if not ({"сейчас", "текущ", "действующ"} & stems(query_words)):
        return 0.0
    return 0.1 if literal_status_hint(excerpt) == "current" else 0.0


def source_hits(documents: list[SourceDocument], query: str, top_k: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    candidates: list[dict[str, Any]] = []
    scanned_chunks = 0
    for document in documents:
        document_status_bonus = explicit_status_bonus(document.text)
        chunks = split_context(document.text, 5_000, 1, 0, 2_000)
        for index, chunk in enumerate(chunks):
            scanned_chunks += 1
            # Adjacent semantic units preserve a heading/list item's immediate
            # evidence without reconstructing or paraphrasing source content.
            end = chunks[min(index + 2, len(chunks) - 1)].end
            excerpt = document.text[chunk.start:end]
            local_excerpt = document.text[chunk.start:chunk.end]
            relevance = lexical_score(query, excerpt)
            if relevance <= 0:
                continue
            score = round(relevance + document_status_bonus + status_alignment_bonus(query, local_excerpt), 6)
            candidates.append({
                "excerpt": excerpt,
                "source_id": document.source_id,
                "sanitized_id": document.sanitized_id,
                "material_ordinal": document.ordinal,
                "created_at": document.created_at,
                "start": chunk.start,
                "end": end,
                "score": score,
                "subject_scope": query_subject_scope(query, excerpt),
                "status_hint": literal_status_hint(local_excerpt),
                "selection_reason": "generic_token_overlap_plus_trigram_jaccard_plus_explicit_status_cue",
            })
    candidates.sort(key=lambda item: (-float(item["score"]), int(item["material_ordinal"]), int(item["start"]), str(item["sanitized_id"])))
    # A package should expose competing source materials, not spend its entire
    # bounded allowance on overlapping excerpts from one document.
    selected: list[dict[str, Any]] = []
    seen_materials: set[int] = set()
    for candidate in candidates:
        ordinal = int(candidate["material_ordinal"])
        if ordinal in seen_materials:
            continue
        selected.append(candidate)
        seen_materials.add(ordinal)
        if len(selected) == top_k:
            break
    return selected, {"total_source_materials_scanned": len(documents), "total_source_chunks_scanned": scanned_chunks}


def _oc_partition(bundle: Path) -> Path:
    matches = sorted((bundle / "pilot_workspace" / "operational_context_v0" / "project").glob("**/*.json"))
    if len(matches) != 1:
        raise ValueError("Frozen bundle must contain exactly one project OC partition.")
    return matches[0]


def oc_hits(bundle: Path, query: str, top_k: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    payload = json.loads(_oc_partition(bundle).read_text(encoding="utf-8"))
    raw_items = payload.get("items", {})
    items = list(raw_items.values()) if isinstance(raw_items, dict) else []
    eligible = [item for item in items if item.get("lifecycle") == "active" and item.get("confirmation") == "confirmed"]
    ranked: list[dict[str, Any]] = []
    for item in eligible:
        searchable = "\n".join(str(item.get(key) or "") for key in ("kind", "subject_ref", "value", "reference"))
        score = lexical_score(query, searchable)
        if score <= 0:
            continue
        ranked.append({
            "id": str(item.get("id") or ""), "value": str(item.get("value") or item.get("reference") or ""),
            "lifecycle": str(item.get("lifecycle") or ""), "confirmation": str(item.get("confirmation") or ""),
            "provenance": dict(item.get("provenance") or {}), "score": score,
        })
    ranked.sort(key=lambda item: (-float(item["score"]), str(item["id"])))
    return ranked[:top_k], {"total_oc_scanned": len(items), "eligible_oc": len(eligible)}


def build_package(bundle: Path, query: str, source_top_k: int = 4, oc_top_k: int = 3) -> dict[str, Any]:
    if not query.strip():
        raise ValueError("Query must not be empty.")
    if not 1 <= source_top_k <= 4 or not 1 <= oc_top_k <= 3:
        raise ValueError("Top-k bounds are outside the experimental contract.")
    sources = load_sources(bundle)
    source_evidence, source_diagnostics = source_hits(sources, query, source_top_k)
    overlay, oc_diagnostics = oc_hits(bundle, query, oc_top_k)
    unresolved = [] if source_evidence else ["no_source_evidence_above_generic_relevance_threshold"]
    return {
        "schema": "gaia.d2.hybrid-package.v0",
        "query": query,
        "source_evidence": source_evidence,
        "oc_overlay": overlay,
        "unresolved": unresolved,
        "retrieval_diagnostics": {
            **source_diagnostics,
            **oc_diagnostics,
            "source_chunks_selected": len(source_evidence),
            "distinct_source_materials_selected": len({item["material_ordinal"] for item in source_evidence}),
            "oc_selected": len(overlay),
            "source_scoring": "token_recall + 0.35 * character_trigram_jaccard + literal_status_cue_bonus",
            "oc_is_not_exhaustive_project_truth": True,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build an isolated read-only hybrid retrieval package.")
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--query", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-top-k", type=int, default=4)
    parser.add_argument("--oc-top-k", type=int, default=3)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    bundle = args.bundle.resolve()
    output = args.output.resolve()
    if output.is_relative_to(bundle):
        raise ValueError("Output must not be written inside the frozen bundle.")
    if not output.parent.is_dir():
        raise ValueError("Output parent must already exist and be caller-provided.")
    package = build_package(bundle, args.query, args.source_top_k, args.oc_top_k)
    output.write_text(json.dumps(package, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
