"""Deterministic, read-only retrieval for Operational Context v0."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .operational_context import KIND_REGISTRY, OperationalContextError, OperationalContextStore, SCOPES, SENSITIVITIES, _opaque


EXCLUSION_REASONS = frozenset((
    "not_active", "not_confirmed", "invalid_confirmation_evidence", "scope_mismatch",
    "trusted_local_sensitivity_denied", "unsupported_kind", "unresolved_conflict", "budget_exceeded",
    "invalid_record", "not_applicable",
))


class OperationalRetrievalError(ValueError):
    """Fail-closed error for an invalid retrieval request."""


@dataclass(frozen=True)
class TrustedLocalProcessingPolicy:
    """Gaia-internal handling levels permitted for one trusted local read."""

    allowed_sensitivities: frozenset[str] = frozenset(("standard",))

    def __post_init__(self) -> None:
        if (not isinstance(self.allowed_sensitivities, frozenset)
                or not self.allowed_sensitivities
                or not self.allowed_sensitivities.issubset(SENSITIVITIES)):
            raise OperationalRetrievalError("trusted local sensitivity policy is invalid.")


@dataclass(frozen=True)
class RetrievalRequest:
    """Exact identities and Gaia-internal applicability for one OC read."""

    user_ref: str
    project_ref: str
    system_ref: str
    supported_kinds: frozenset[str]
    trusted_local_policy: TrustedLocalProcessingPolicy
    max_items: int
    max_chars: int
    task_subject_refs: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        for name in ("user_ref", "project_ref", "system_ref"):
            _opaque(getattr(self, name), name)
        if not self.supported_kinds.issubset(KIND_REGISTRY):
            raise OperationalRetrievalError("supported_kinds contains an unknown kind.")
        if (not isinstance(self.trusted_local_policy, TrustedLocalProcessingPolicy)
                or self.max_items < 1 or self.max_chars < 1):
            raise OperationalRetrievalError("retrieval budget or trusted local policy is invalid.")
        for subject_ref in self.task_subject_refs:
            _opaque(subject_ref, "task_subject_ref")


@dataclass(frozen=True)
class SafeDiagnostic:
    reason: str
    item_id: str = ""

    def __post_init__(self) -> None:
        if self.reason not in EXCLUSION_REASONS:
            raise OperationalRetrievalError("diagnostic reason is unknown.")

    def as_dict(self) -> dict[str, str]:
        return {"reason": self.reason, **({"item_id": self.item_id} if self.item_id else {})}


@dataclass(frozen=True)
class AuthorityReference:
    """Safe internal linkage retained for an unresolved authority conflict."""

    item_id: str
    scope: str
    scope_ref: str
    provenance: dict[str, str]
    confirmation_ref: str
    content: str

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "AuthorityReference":
        return cls(
            item_id=str(item["id"]), scope=str(item["scope"]), scope_ref=str(item["scope_ref"]),
            provenance=dict(item["provenance"]), confirmation_ref=str(item["confirmation_ref"]),
            content=str(item.get("value") or item.get("reference") or ""),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id, "scope": self.scope, "scope_ref": self.scope_ref,
            "provenance": dict(self.provenance), "confirmation_ref": self.confirmation_ref,
            "content": self.content,
        }


@dataclass(frozen=True)
class AuthorityAmbiguity:
    """Unresolved same-subject authority candidates without a kind composition rule."""

    kind: str
    subject_ref: str
    derived_sensitivity: str
    involved_authorities: tuple[AuthorityReference, ...]

    @classmethod
    def from_items(cls, items: list[dict[str, Any]]) -> "AuthorityAmbiguity":
        ordered = sorted(items, key=lambda item: str(item["id"]))
        return cls(
            kind=str(ordered[0]["kind"]), subject_ref=str(ordered[0]["subject_ref"]),
            derived_sensitivity=(
                "restricted" if any(item["sensitivity"] == "restricted" for item in ordered)
                else "unknown" if any(item["sensitivity"] == "unknown" for item in ordered)
                else "standard"
            ),
            involved_authorities=tuple(AuthorityReference.from_item(item) for item in ordered),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "subject_ref": self.subject_ref,
            "derived_sensitivity": self.derived_sensitivity,
            "involved_authorities": [item.as_dict() for item in self.involved_authorities],
        }


@dataclass(frozen=True)
class RetrievalResult:
    eligible_items: tuple[dict[str, Any], ...]
    exclusions: tuple[SafeDiagnostic, ...]
    ambiguities: tuple[AuthorityAmbiguity, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "eligible_items": [dict(item) for item in self.eligible_items],
            "exclusions": [item.as_dict() for item in self.exclusions],
            "ambiguities": [item.as_dict() for item in self.ambiguities],
        }


class OperationalContextReader:
    """Reads only exact request partitions and never consults legacy Context."""

    def __init__(self, store: OperationalContextStore) -> None:
        self.store = store

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        candidates: list[dict[str, Any]] = []
        exclusions: list[SafeDiagnostic] = []
        for scope, scope_ref in (("user", request.user_ref), ("project", request.project_ref), ("system", request.system_ref)):
            try:
                # The store derives an exact partition path from this pair; no
                # other workspace is opened and there is no enumeration API.
                candidates.extend(self.store.list_scope(scope=scope, scope_ref=scope_ref))
            except OperationalContextError:
                exclusions.append(SafeDiagnostic("invalid_record"))

        applicable: list[dict[str, Any]] = []
        for item in candidates:
            item_id = str(item.get("id", ""))
            reason = self._exclusion_reason(item, request)
            if reason:
                exclusions.append(SafeDiagnostic(reason, item_id))
                continue
            applicable.append(item)

        conflicts = self._conflicts(applicable)
        ambiguous_ids = {str(item["id"]) for group in conflicts for item in group}
        eligible = [item for item in applicable if str(item["id"]) not in ambiguous_ids]
        ambiguities = [AuthorityAmbiguity.from_items(group) for group in conflicts]

        eligible.sort(key=lambda item: (str(item["kind"]), str(item["updated_at"]), str(item["id"])))
        bounded: list[dict[str, Any]] = []
        used_chars = 0
        for item in eligible:
            cost = len(json.dumps(item, ensure_ascii=False, sort_keys=True))
            if len(bounded) >= request.max_items or used_chars + cost > request.max_chars:
                exclusions.append(SafeDiagnostic("budget_exceeded", str(item["id"])))
                continue
            bounded.append(item); used_chars += cost
        return RetrievalResult(tuple(bounded), tuple(exclusions), tuple(ambiguities))

    @staticmethod
    def _conflicts(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for item in items:
            identity = (str(item["kind"]), str(item["subject_ref"]))
            grouped.setdefault(identity, []).append(item)
        return [group for (kind, _), group in grouped.items()
                if len(group) > 1 and KIND_REGISTRY[kind].composition_rule is None]

    @staticmethod
    def _exclusion_reason(item: dict[str, Any], request: RetrievalRequest) -> str:
        if item.get("lifecycle") != "active": return "not_active"
        if item.get("confirmation") != "confirmed": return "not_confirmed"
        if item.get("kind") not in request.supported_kinds: return "unsupported_kind"
        if item.get("sensitivity") not in SENSITIVITIES: return "invalid_record"
        if item.get("sensitivity") not in request.trusted_local_policy.allowed_sensitivities:
            return "trusted_local_sensitivity_denied"
        if request.task_subject_refs and item.get("subject_ref") not in request.task_subject_refs: return "not_applicable"
        return ""
