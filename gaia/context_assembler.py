"""Pure, read-only composition boundary for future Dialogue consumers.

This module deliberately does not render prompts or call the model.  It joins
query-selected, confirmed current Operational Context with the existing Lore
``MemorySelection`` while retaining distinct authority and provenance layers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Protocol

from .context_search import parse_params, select_records
from .models import MemorySelection


DEFAULT_TOTAL_CHARS = 60_000
DEFAULT_RESERVED_CONTEXT_CHARS = 12_000
DEFAULT_CONTEXT_ITEMS = 8
AUTHORITY_POLICY = "operational-context-confirmed-current-v1"


class ContextReader(Protocol):
    """The read-only workspace-scoped part of ``ContextService`` used here."""

    def list(self) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class DialogueContextBudget:
    """Configurable composition budget; values are not a storage contract."""

    total_chars: int = DEFAULT_TOTAL_CHARS
    reserved_context_chars: int = DEFAULT_RESERVED_CONTEXT_CHARS
    max_context_items: int = DEFAULT_CONTEXT_ITEMS

    def __post_init__(self) -> None:
        if self.total_chars < 1:
            raise ValueError("Общий лимит контекста должен быть положительным.")
        if not 0 <= self.reserved_context_chars <= self.total_chars:
            raise ValueError("Резерв оперативного контекста должен входить в общий лимит.")
        if self.max_context_items < 1:
            raise ValueError("Нужно выбрать хотя бы один элемент оперативного контекста.")


@dataclass(frozen=True)
class TrustedContextItem:
    """A current confirmed Operational Context record with native lineage."""

    id: str
    item_type: str
    title: str
    statement: str
    actor_ref: str
    deadline: str
    explicit_status: str
    priority: str
    source_links: tuple[str, ...]
    block_links: tuple[dict[str, Any], ...]
    parents: tuple[str, ...]
    relation_ids: tuple[str, ...]
    confirmed_at: str


@dataclass(frozen=True)
class DialogueContextMetadata:
    authority_policy: str
    total_chars: int
    reserved_context_chars: int
    context_chars: int
    memory_chars: int
    selected_context_count: int
    included_context_count: int
    memory_available: bool


@dataclass(frozen=True)
class DialogueContext:
    """Structured, non-rendered input for a future Dialogue integration."""

    current_authority: tuple[TrustedContextItem, ...]
    memory_selection: MemorySelection | None
    memory_text: str
    metadata: DialogueContextMetadata


def select_trusted_context(reader: ContextReader, query: str, budget: DialogueContextBudget = DialogueContextBudget()) -> tuple[TrustedContextItem, ...]:
    """Read query-scoped trusted Context using ContextService isolation and search rules.

    An empty query intentionally selects nothing: this boundary must never turn
    a Dialogue query into an unscoped dump of a workspace's current Context.
    """
    if not query.strip():
        return ()
    params = parse_params({"q": [query], "limit": [str(budget.max_context_items)]})
    records, _, _ = select_records(reader.list(), params)
    return tuple(_trusted_item(record) for record in records)


def compose_dialogue_context(
    current_authority: Iterable[TrustedContextItem],
    memory_selection: MemorySelection | None,
    budget: DialogueContextBudget = DialogueContextBudget(),
) -> DialogueContext:
    """Compose layers deterministically without changing either source system.

    Context receives its reserved capacity first.  Capacity unused by either
    layer is made available to the other layer; records remain atomic so their
    provenance never becomes detached from a truncated statement.
    """
    selected = tuple(current_authority)[:budget.max_context_items]
    raw_memory = memory_selection.text if memory_selection else ""
    base_memory_capacity = budget.total_chars - budget.reserved_context_chars
    provisional_memory = min(len(raw_memory), base_memory_capacity)
    context_capacity = budget.total_chars - provisional_memory
    included = _fit_context(selected, context_capacity)
    context_chars = sum(_context_chars(item) for item in included)
    memory_text = raw_memory[:budget.total_chars - context_chars]
    metadata = DialogueContextMetadata(
        authority_policy=AUTHORITY_POLICY,
        total_chars=budget.total_chars,
        reserved_context_chars=budget.reserved_context_chars,
        context_chars=context_chars,
        memory_chars=len(memory_text),
        selected_context_count=len(selected),
        included_context_count=len(included),
        memory_available=bool(raw_memory),
    )
    return DialogueContext(included, memory_selection, memory_text, metadata)


def _trusted_item(record: dict[str, Any]) -> TrustedContextItem:
    return TrustedContextItem(
        id=str(record.get("id") or ""), item_type=str(record.get("item_type") or ""),
        title=str(record.get("title") or ""), statement=str(record.get("statement") or ""),
        actor_ref=str(record.get("actor_ref") or ""), deadline=str(record.get("deadline") or ""),
        explicit_status=str(record.get("explicit_status") or record.get("status") or ""),
        priority=str(record.get("priority") or ""), source_links=tuple(record.get("source_links") or ()),
        block_links=tuple(dict(link) for link in record.get("block_links") or ()),
        parents=tuple(record.get("parents") or ()), relation_ids=tuple(record.get("relation_ids") or ()),
        confirmed_at=str(record.get("confirmed_at") or ""),
    )


def _fit_context(items: tuple[TrustedContextItem, ...], capacity: int) -> tuple[TrustedContextItem, ...]:
    included: list[TrustedContextItem] = []
    used = 0
    for item in items:
        cost = _context_chars(item)
        if used + cost > capacity:
            continue
        included.append(item)
        used += cost
    return tuple(included)


def _context_chars(item: TrustedContextItem) -> int:
    return sum(len(value) for value in (
        item.item_type, item.title, item.statement, item.actor_ref, item.deadline,
        item.explicit_status, item.priority,
    ))
