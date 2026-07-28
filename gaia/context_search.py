"""Deterministic, local-only search over confirmed project context."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping


ITEM_TYPES = ("requirement", "decision", "risk", "open_question", "action")
SORTS = ("relevance", "updated_desc", "updated_asc", "title_asc")
PRESENCE = ("any", "present", "missing")
RELATED = ("any", "present", "none")
MAX_QUERY_LENGTH = 200
MAX_TERMS = 16
DEFAULT_LIMIT = 50
MAX_LIMIT = 100

# Kept intentionally simple so the ranking is reproducible and testable.
SCORE_EXACT_TITLE = 1000
SCORE_TITLE_TERM = 100
SCORE_STATEMENT_TERM = 50
SCORE_METADATA_TERM = 10
SEARCH_FIELDS = ("title", "statement", "actor_ref", "deadline", "explicit_status", "priority")


class ContextSearchError(ValueError):
    """A safe validation error suitable for an HTTP 400 response."""


@dataclass(frozen=True)
class SearchParams:
    query: str
    terms: tuple[str, ...]
    item_types: tuple[str, ...]
    actor_presence: str
    actor: str
    deadline_presence: str
    related: str
    sort: str
    limit: int
    offset: int


def normalize(value: object) -> str:
    """Normalize text without retaining punctuation, case, or Russian ё differences."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().replace("ё", "е")
    return " ".join("".join(char if char.isalnum() else " " for char in text).split())


def parse_params(query: Mapping[str, list[str]]) -> SearchParams:
    raw_query = _one(query, "q", "")
    if len(raw_query) > MAX_QUERY_LENGTH:
        raise ContextSearchError("Слишком длинный поисковый запрос.")
    normalized_query = normalize(raw_query)
    terms = tuple(normalized_query.split())
    if len(terms) > MAX_TERMS:
        raise ContextSearchError("В поисковом запросе слишком много слов.")

    raw_types = tuple(value for value in query.get("type", []) if value != "")
    if any(value not in ITEM_TYPES for value in raw_types):
        raise ContextSearchError("Указан неизвестный тип элемента.")
    item_types = tuple(dict.fromkeys(raw_types)) or ITEM_TYPES
    actor_presence = _enum(query, "actor_presence", PRESENCE, "any")
    actor = _one(query, "actor", "")
    if actor and actor_presence == "missing":
        raise ContextSearchError("Нельзя искать ответственного среди элементов без ответственного.")
    deadline_presence = _enum(query, "deadline_presence", PRESENCE, "any")
    related = _enum(query, "related", RELATED, "any")
    supplied_sort = _one(query, "sort", "")
    if supplied_sort and supplied_sort not in SORTS:
        raise ContextSearchError("Указан неизвестный способ сортировки.")
    sort = supplied_sort or ("relevance" if terms else "updated_desc")
    return SearchParams(
        query=normalized_query, terms=terms, item_types=item_types,
        actor_presence=actor_presence, actor=normalize(actor),
        deadline_presence=deadline_presence, related=related, sort=sort,
        limit=_integer(query, "limit", DEFAULT_LIMIT, minimum=1, maximum=MAX_LIMIT),
        offset=_integer(query, "offset", 0, minimum=0),
    )


def search(items: list[dict[str, Any]], params: SearchParams) -> dict[str, Any]:
    """Search a pre-isolated workspace corpus and return a safe API projection."""
    corpus = [item for item in items if _eligible(item)]
    facets = _facets(corpus)
    matched = [item for item in corpus if _matches(item, params)]
    scored = [(item, _score(item, params)) for item in matched]
    _sort(scored, params.sort)
    total = len(scored)
    page = scored[params.offset:params.offset + params.limit]
    return {
        "results": [_project(item) for item, _ in page],
        "total": total,
        "returned": len(page),
        "has_more": params.offset + len(page) < total,
        "facets": facets,
        "sort": params.sort,
    }


def _eligible(item: Mapping[str, Any]) -> bool:
    return item.get("kind") == "context" and item.get("status") == "confirmed" and item.get("current") is True and item.get("item_type") in ITEM_TYPES


def _matches(item: Mapping[str, Any], params: SearchParams) -> bool:
    if item.get("item_type") not in params.item_types:
        return False
    actor = normalize(item.get("actor_ref"))
    if params.actor_presence == "present" and not actor:
        return False
    if params.actor_presence == "missing" and actor:
        return False
    if params.actor and actor != params.actor:
        return False
    deadline = normalize(item.get("deadline"))
    if params.deadline_presence == "present" and not deadline:
        return False
    if params.deadline_presence == "missing" and deadline:
        return False
    has_related = bool(item.get("relation_ids"))
    if params.related == "present" and not has_related:
        return False
    if params.related == "none" and has_related:
        return False
    fields = [normalize(item.get(field)) for field in SEARCH_FIELDS]
    return all(any(term in field for field in fields) for term in params.terms)


def _score(item: Mapping[str, Any], params: SearchParams) -> int:
    if not params.terms:
        return 0
    title = normalize(item.get("title"))
    statement = normalize(item.get("statement"))
    metadata = [normalize(item.get(field)) for field in SEARCH_FIELDS[2:]]
    score = SCORE_EXACT_TITLE if title == params.query else 0
    for term in params.terms:
        if term in title:
            score += SCORE_TITLE_TERM
        if term in statement:
            score += SCORE_STATEMENT_TERM
        score += sum(SCORE_METADATA_TERM for field in metadata if term in field)
    return score


def _sort(scored: list[tuple[dict[str, Any], int]], sort: str) -> None:
    def tie(item: Mapping[str, Any]) -> tuple[str, str]:
        return normalize(item.get("title")), str(item.get("id") or "")
    if sort == "relevance":
        scored.sort(key=lambda pair: (-pair[1], *tie(pair[0])))
    elif sort == "updated_desc":
        scored.sort(key=lambda pair: tie(pair[0]))
        scored.sort(key=lambda pair: str(pair[0].get("updated_at") or ""), reverse=True)
    elif sort == "updated_asc":
        scored.sort(key=lambda pair: (str(pair[0].get("updated_at") or ""), *tie(pair[0])))
    else:
        scored.sort(key=lambda pair: tie(pair[0]))


def _project(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "item_type": item.get("item_type", ""), "title": item.get("title", ""),
        "statement": item.get("statement", ""), "actor_ref": item.get("actor_ref", ""),
        "deadline": item.get("deadline", ""), "explicit_status": item.get("explicit_status", ""),
        "priority": item.get("priority", ""), "updated_at": item.get("updated_at", ""),
        "source_count": len(item.get("source_links") or []), "has_related": bool(item.get("relation_ids")),
    }


def _facets(corpus: list[Mapping[str, Any]]) -> dict[str, Any]:
    types = {item_type: sum(1 for item in corpus if item.get("item_type") == item_type) for item_type in ITEM_TYPES}
    actors: dict[str, tuple[str, int]] = {}
    for item in corpus:
        display = str(item.get("actor_ref") or "").strip()
        key = normalize(display)
        if key:
            previous = actors.get(key, (display, 0))
            actors[key] = (previous[0], previous[1] + 1)
    return {"types": types, "actors": [{"value": value, "count": count} for _, (value, count) in sorted(actors.items())]}


def _one(query: Mapping[str, list[str]], key: str, default: str) -> str:
    values = query.get(key, [])
    if len(values) > 1:
        raise ContextSearchError("Параметр запроса указан несколько раз.")
    return str(values[0]) if values else default


def _enum(query: Mapping[str, list[str]], key: str, allowed: tuple[str, ...], default: str) -> str:
    value = _one(query, key, default)
    if value not in allowed:
        raise ContextSearchError("Указан недопустимый фильтр.")
    return value


def _integer(query: Mapping[str, list[str]], key: str, default: int, minimum: int, maximum: int | None = None) -> int:
    raw = _one(query, key, "")
    if raw == "":
        return default
    if not re.fullmatch(r"\d+", raw):
        raise ContextSearchError("Параметры пагинации должны быть целыми неотрицательными числами.")
    value = int(raw)
    if value < minimum or (maximum is not None and value > maximum):
        raise ContextSearchError("Параметр пагинации выходит за допустимый диапазон.")
    return value
