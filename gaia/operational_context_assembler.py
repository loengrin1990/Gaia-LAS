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
from .operational_context_retrieval import AuthorityAmbiguity, RetrievalResult
from .privacy_boundary import (
    DisclosureEligibility,
    HandledInput,
    Handling,
    HandlingEvidence,
    PrivacyBoundaryError,
    ValidatedPrivacyInput,
    evaluate_external_eligibility,
    is_registered_system_control,
    strictest_handling,
    trusted_system_control,
    _PB_VALIDATION_CAPABILITY,
    _validated_input,
)


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
    handling: Handling
    evidence: HandlingEvidence | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise OperationalContextAssemblyError("handled text is invalid.")
        _validate_handled_input(self.handling, self.evidence)

    def as_dict(self) -> dict[str, str]:
        return {"text": self.text, "handling": self.handling}


@dataclass(frozen=True)
class HandledMemorySelection:
    """Lore-selected memory with handling supplied by its upstream boundary."""

    selection: MemorySelection
    handling: Handling
    evidence: HandlingEvidence | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.selection, MemorySelection):
            raise OperationalContextAssemblyError("handled memory selection is invalid.")
        _validate_handled_input(self.handling, self.evidence)

    @classmethod
    def legacy(cls, selection: MemorySelection) -> "HandledMemorySelection":
        """Legacy Lore data has no reliable PB-0 handling and therefore stays local."""
        return cls(selection, "unknown")

    def as_dict(self) -> dict[str, Any]:
        return {"selection": asdict(self.selection), "handling": self.handling}


@dataclass(frozen=True)
class SessionContextItem:
    """One minimal session unit with handling supplied by its upstream boundary."""

    text: str
    handling: Handling
    evidence: HandlingEvidence | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise OperationalContextAssemblyError("handled session context is invalid.")
        _validate_handled_input(self.handling, self.evidence)

    def as_dict(self) -> dict[str, str]:
        return {"text": self.text, "handling": self.handling}


def new_free_form_text(text: str) -> HandledText:
    """Represent new substantive user content; it is never externally eligible by default."""
    return HandledText(text, "unknown")


def trusted_system_text(control_id: str) -> HandledText:
    """Represent controlled non-semantic instructions from Gaia itself."""
    control = trusted_system_control(control_id)
    return HandledText(control.canonical_payload, control.handling, control.evidence)


def derived_session_context(text: str, contributors: tuple[HandledInput, ...], *, derivation_ref: str) -> SessionContextItem:
    """Carry the strictest contributor state into a session result or turn."""
    handling = strictest_handling(item.handling for item in contributors)
    evidence = HandlingEvidence("derived_from_standard", derivation_ref) if handling == "standard" else None
    return SessionContextItem(text, handling, evidence)


@dataclass(frozen=True)
class PackageOmission:
    """A deterministic whole-unit omission; no included authority is truncated."""

    layer: str
    reference: str
    handling: Handling
    reason: str = "budget_exceeded"

    def __post_init__(self) -> None:
        if self.handling not in {"standard", "restricted", "unknown"}:
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
    disclosure: DisclosureEligibility


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

    query_input = _text_input(query)
    task_input = _text_input(task)
    authority_handling = _handling_for_authority(retrieval_result.eligible_items)
    ambiguity_handling = _strictest(item.derived_sensitivity for item in retrieval_result.ambiguities)
    memory_handling = memory_selection.handling if memory_selection else "standard"
    session_handling = _strictest(item.handling for item in session_context)
    required_inputs = _required_inputs(query, task, retrieval_result, memory_selection, session_context)
    package_handling = strictest_handling(item.handling for item in required_inputs)

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
    disclosure = evaluate_external_eligibility(required_inputs)
    metadata = OperationalContextPackageMetadata(
        total_chars=budget.max_chars, used_chars=used, handling=package_handling,
        query_handling=query_input.handling, task_handling=task_input.handling,
        authority_handling=authority_handling, ambiguity_handling=ambiguity_handling,
        memory_handling=memory_handling, session_handling=session_handling,
        included_authority_count=len(authority), included_ambiguity_count=len(ambiguities),
        memory_included=included_memory is not None, included_session_count=len(included_session),
        disclosure=disclosure,
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
    sensitivity = str(item.get("sensitivity", ""))
    return sensitivity if sensitivity in {"standard", "restricted"} else "unknown"


def _handling_for_authority(items: tuple[dict[str, Any], ...]) -> str:
    return strictest_handling(_authority_item_handling(item) for item in items)


def _strictest(levels: Any) -> str:
    try:
        return strictest_handling(levels)
    except PrivacyBoundaryError as exc:
        raise OperationalContextAssemblyError(str(exc)) from exc


def _validate_handled_input(handling: Handling, evidence: HandlingEvidence | None) -> None:
    try:
        HandledInput(handling, evidence)
    except PrivacyBoundaryError as exc:
        raise OperationalContextAssemblyError(str(exc)) from exc


def _authority_input(item: dict[str, Any]) -> ValidatedPrivacyInput:
    handling = _authority_item_handling(item)
    if handling != "standard":
        return _validated_input(handling=handling, origin="operational_context", capability=_PB_VALIDATION_CAPABILITY)
    confirmation_ref = item.get("confirmation_ref")
    provenance = item.get("provenance")
    if not isinstance(confirmation_ref, str) or not confirmation_ref or not isinstance(provenance, dict):
        return _validated_input(handling="unknown", origin="operational_context", capability=_PB_VALIDATION_CAPABILITY)
    return _validated_input(handling="standard", origin="operational_context", evidence=HandlingEvidence("operational_context_standard", confirmation_ref), capability=_PB_VALIDATION_CAPABILITY)


def _ambiguity_input(item: AuthorityAmbiguity) -> ValidatedPrivacyInput:
    handling = _strictest((item.derived_sensitivity,))
    if handling != "standard":
        return _validated_input(handling=handling, origin="operational_context", capability=_PB_VALIDATION_CAPABILITY)
    if not item.involved_authorities or any(not authority.confirmation_ref for authority in item.involved_authorities):
        return _validated_input(handling="unknown", origin="operational_context", capability=_PB_VALIDATION_CAPABILITY)
    return _validated_input(handling="standard", origin="operational_context", evidence=HandlingEvidence("operational_context_standard", item.involved_authorities[0].confirmation_ref), capability=_PB_VALIDATION_CAPABILITY)


def _required_inputs(
    query: HandledText,
    task: HandledText,
    retrieval_result: RetrievalResult,
    memory_selection: HandledMemorySelection | None,
    session_context: tuple[SessionContextItem, ...],
) -> tuple[ValidatedPrivacyInput, ...]:
    inputs = [
        _text_input(query),
        _text_input(task),
        *(_authority_input(item) for item in retrieval_result.eligible_items),
        *(_ambiguity_input(item) for item in retrieval_result.ambiguities),
        *(_session_input(item) for item in session_context),
    ]
    if memory_selection is not None:
        inputs.append(_memory_input(memory_selection))
    return tuple(inputs)


def _text_input(item: HandledText) -> ValidatedPrivacyInput:
    if item.handling != "standard":
        return _validated_input(handling=item.handling, origin="free_form", capability=_PB_VALIDATION_CAPABILITY)
    if not is_registered_system_control(item.text, item.evidence):
        return _validated_input(handling="unknown", origin="free_form", capability=_PB_VALIDATION_CAPABILITY)
    return _validated_input(handling="standard", origin="system_control", evidence=item.evidence, canonical_payload=item.text, capability=_PB_VALIDATION_CAPABILITY)


def _memory_input(item: HandledMemorySelection) -> ValidatedPrivacyInput:
    handling = item.handling
    if handling == "standard" and (item.evidence is None or item.evidence.kind != "reviewed_memory_standard"):
        handling = "unknown"
    return _validated_input(handling=handling, origin="reviewed_memory", evidence=item.evidence if handling == "standard" else None, capability=_PB_VALIDATION_CAPABILITY)


def _session_input(item: SessionContextItem) -> ValidatedPrivacyInput:
    handling = item.handling
    if handling == "standard" and (item.evidence is None or item.evidence.kind != "derived_from_standard"):
        handling = "unknown"
    return _validated_input(handling=handling, origin="session", evidence=item.evidence if handling == "standard" else None, capability=_PB_VALIDATION_CAPABILITY)
