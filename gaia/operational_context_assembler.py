"""Read-only v0 composition of OC-2, Lore memory, and session context.

This module deliberately has no dependency on the legacy Context assembler,
Dialogue, Heart, Scribe, or Operational Context storage.  OC-2 remains the
only authority-selection boundary; this module only preserves its result in a
bounded, typed package for a later disclosure boundary.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from .models import MemorySelection
from .operational_context import SENSITIVITIES
from .operational_context_retrieval import AuthorityAmbiguity, RetrievalResult


DEFAULT_PACKAGE_CHARS = 60_000


class OperationalContextAssemblyError(ValueError):
    """Fail-closed error for an invalid v0 composition request."""


@dataclass(frozen=True)
class OperationalContextPackageBudget:
    """The total serialized size allowed for one internal v0 package."""

    max_chars: int = DEFAULT_PACKAGE_CHARS

    def __post_init__(self) -> None:
        if not isinstance(self.max_chars, int) or self.max_chars < 1:
            raise OperationalContextAssemblyError("package budget is invalid.")


@dataclass(frozen=True)
class HandledText:
    """Text with a trusted upstream handling classification, never inferred here."""

    text: str
    handling: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or self.handling not in SENSITIVITIES:
            raise OperationalContextAssemblyError("handled text is invalid.")

    def as_dict(self) -> dict[str, str]:
        return {"text": self.text, "handling": self.handling}


@dataclass(frozen=True)
class HandledMemorySelection:
    """Lore-selected memory with handling supplied by its upstream boundary."""

    selection: MemorySelection
    handling: str

    def __post_init__(self) -> None:
        if not isinstance(self.selection, MemorySelection) or self.handling not in SENSITIVITIES:
            raise OperationalContextAssemblyError("handled memory selection is invalid.")

    def as_dict(self) -> dict[str, Any]:
        return {"selection": asdict(self.selection), "handling": self.handling}


@dataclass(frozen=True)
class SessionContextItem:
    """One minimal session unit with handling supplied by its upstream boundary."""

    text: str
    handling: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or self.handling not in SENSITIVITIES:
            raise OperationalContextAssemblyError("handled session context is invalid.")

    def as_dict(self) -> dict[str, str]:
        return {"text": self.text, "handling": self.handling}


@dataclass(frozen=True)
class PackageOmission:
    """A deterministic whole-unit omission; no included authority is truncated."""

    layer: str
    reference: str
    handling: str
    reason: str = "budget_exceeded"

    def __post_init__(self) -> None:
        if self.handling not in SENSITIVITIES:
            raise OperationalContextAssemblyError("omission handling is invalid.")

    def as_dict(self) -> dict[str, str]:
        return {"layer": self.layer, "reference": self.reference, "handling": self.handling, "reason": self.reason}


@dataclass(frozen=True)
class OperationalContextPackageMetadata:
    total_chars: int
    used_chars: int
    handling: str
    query_handling: str
    task_handling: str
    authority_handling: str
    ambiguity_handling: str
    memory_handling: str
    session_handling: str
    included_authority_count: int
    included_ambiguity_count: int
    memory_included: bool
    included_session_count: int


@dataclass(frozen=True)
class OperationalContextPackage:
    """Structured input for a future disclosure/routing boundary, never a prompt."""

    query: HandledText
    task: HandledText
    current_authority: tuple[dict[str, Any], ...]
    ambiguities: tuple[AuthorityAmbiguity, ...]
    memory_selection: HandledMemorySelection | None
    session_context: tuple[SessionContextItem, ...]
    omissions: tuple[PackageOmission, ...]
    metadata: OperationalContextPackageMetadata

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "task": self.task,
            "current_authority": [dict(item) for item in self.current_authority],
            "ambiguities": [item.as_dict() for item in self.ambiguities],
            "memory_selection": self.memory_selection.as_dict() if self.memory_selection else None,
            "session_context": [item.as_dict() for item in self.session_context],
            "omissions": [item.as_dict() for item in self.omissions],
            "metadata": asdict(self.metadata),
        }


def compose_operational_context_package(
    *,
    query: HandledText,
    task: HandledText,
    retrieval_result: RetrievalResult,
    memory_selection: HandledMemorySelection | None,
    session_context: tuple[SessionContextItem, ...] = (),
    budget: OperationalContextPackageBudget = OperationalContextPackageBudget(),
) -> OperationalContextPackage:
    """Compose already-selected inputs without reading or changing any source.

    Current authority and ambiguities are considered before Lore and the
    minimal supplied session units.  Each unit is either retained complete or
    omitted deterministically; neither conflict metadata nor provenance is
    detached from an included authority item.
    """
    if not isinstance(query, HandledText) or not isinstance(task, HandledText):
        raise OperationalContextAssemblyError("query and task must have typed handling.")
    if not isinstance(retrieval_result, RetrievalResult):
        raise OperationalContextAssemblyError("retrieval result must be typed OC-2 output.")
    if memory_selection is not None and not isinstance(memory_selection, HandledMemorySelection):
        raise OperationalContextAssemblyError("memory selection must have typed handling.")
    if not isinstance(session_context, tuple) or not all(isinstance(item, SessionContextItem) for item in session_context):
        raise OperationalContextAssemblyError("session context must have typed handling.")

    authority_handling = _handling_for_authority(retrieval_result.eligible_items)
    ambiguity_handling = _strictest(item.derived_sensitivity for item in retrieval_result.ambiguities)
    memory_handling = memory_selection.handling if memory_selection else "standard"
    session_handling = _strictest(item.handling for item in session_context)
    package_handling = _strictest((
        query.handling, task.handling, authority_handling, ambiguity_handling,
        memory_handling, session_handling,
    ))

    used = _serialized_chars({"query": query, "task": task})
    if used > budget.max_chars:
        raise OperationalContextAssemblyError("query and task exceed the package budget.")
    omissions: list[PackageOmission] = []
    authority, used = _fit_items(retrieval_result.eligible_items, "operational_context", "id", budget.max_chars, used, omissions, _authority_item_handling)
    ambiguities, used = _fit_items(retrieval_result.ambiguities, "operational_context_ambiguity", "subject_ref", budget.max_chars, used, omissions, lambda item: item.derived_sensitivity)

    included_memory = memory_selection
    if memory_selection is not None:
        cost = _serialized_chars(memory_selection)
        if used + cost > budget.max_chars:
            omissions.append(PackageOmission("memory", "lore_selection", memory_selection.handling))
            included_memory = None
        else:
            used += cost

    included_session, used = _fit_items(session_context, "session", "", budget.max_chars, used, omissions, lambda item: item.handling)
    metadata = OperationalContextPackageMetadata(
        total_chars=budget.max_chars, used_chars=used, handling=package_handling,
        query_handling=query.handling, task_handling=task.handling,
        authority_handling=authority_handling, ambiguity_handling=ambiguity_handling,
        memory_handling=memory_handling, session_handling=session_handling,
        included_authority_count=len(authority), included_ambiguity_count=len(ambiguities),
        memory_included=included_memory is not None, included_session_count=len(included_session),
    )
    return OperationalContextPackage(
        query=query, task=task, current_authority=authority, ambiguities=ambiguities,
        memory_selection=included_memory, session_context=included_session,
        omissions=tuple(omissions), metadata=metadata,
    )


def _fit_items(
    items: tuple[Any, ...],
    layer: str,
    reference_key: str,
    capacity: int,
    used: int,
    omissions: list[PackageOmission],
    handling_for_item: Any,
) -> tuple[tuple[Any, ...], int]:
    included: list[Any] = []
    for index, item in enumerate(items):
        cost = _serialized_chars(item)
        reference = _reference(item, reference_key, index)
        if used + cost > capacity:
            omissions.append(PackageOmission(layer, reference, handling_for_item(item)))
            continue
        included.append(item)
        used += cost
    return tuple(included), used


def _reference(item: Any, key: str, index: int) -> str:
    if isinstance(item, dict) and key:
        return str(item.get(key, ""))
    if isinstance(item, AuthorityAmbiguity):
        return item.subject_ref
    return str(index)


def _serialized_chars(value: Any) -> int:
    if hasattr(value, "as_dict"):
        value = value.as_dict()
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def _authority_item_handling(item: dict[str, Any]) -> str:
    return str(item.get("sensitivity", ""))


def _handling_for_authority(items: tuple[dict[str, Any], ...]) -> str:
    return _strictest(_authority_item_handling(item) for item in items)


def _strictest(levels: Any) -> str:
    levels = tuple(levels)
    if any(level not in SENSITIVITIES for level in levels):
        raise OperationalContextAssemblyError("input handling is invalid.")
    return "restricted" if "restricted" in levels else "standard"
