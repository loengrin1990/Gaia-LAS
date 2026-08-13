"""Typed fail-closed input handling for the future Gaia egress boundary.

PB-0 does not route to an external provider.  It records only the minimum
safe facts a later route may use: each required input's handling and, for
``standard``, the controlled evidence that permits disclosure to be considered.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Literal


Handling = Literal["standard", "restricted", "unknown"]
HANDLING_STATES = frozenset(("standard", "restricted", "unknown"))
_RANK = {"standard": 0, "unknown": 1, "restricted": 2}
_SAFE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
STANDARD_EVIDENCE_KINDS = frozenset((
    "operational_context_standard",
    "reviewed_memory_standard",
    "trusted_system_control",
    "derived_from_standard",
))
# Closed, system-owned control templates.  A control identifier alone is not
# evidence: the boundary validates the exact registered instruction as well.
SYSTEM_CONTROL_TEMPLATES = {
    "pb0_response_format_v1": "Сформируй ответ в согласованном формате.",
}


class PrivacyBoundaryError(ValueError):
    """A fail-closed PB-0 input or decision validation error."""


@dataclass(frozen=True)
class HandlingEvidence:
    """Safe typed proof for a ``standard`` input; never raw source material."""

    kind: str
    reference: str

    def __post_init__(self) -> None:
        if self.kind not in STANDARD_EVIDENCE_KINDS:
            raise PrivacyBoundaryError("handling evidence kind is invalid.")
        if not isinstance(self.reference, str) or not _SAFE_REFERENCE.fullmatch(self.reference):
            raise PrivacyBoundaryError("handling evidence reference is invalid.")


@dataclass(frozen=True)
class DisclosureEligibility:
    """Safe result for a later route; it contains no input contents or IDs."""

    eligible_for_external: bool
    decision: Literal["external_allowed", "local_processing_required"]


def strictest_handling(levels: Iterable[str]) -> Handling:
    values = tuple(levels)
    if any(value not in HANDLING_STATES for value in values):
        raise PrivacyBoundaryError("input handling is invalid.")
    return max(values, key=lambda value: _RANK[value], default="standard")  # type: ignore[return-value]


def derived_handling(inputs: Iterable["HandledInput"]) -> Handling:
    """A derivative can only retain or increase its strictest dependency state."""
    return strictest_handling(item.handling for item in inputs)


@dataclass(frozen=True)
class HandledInput:
    """One required material dependency known to the PB-0 boundary."""

    handling: Handling
    evidence: HandlingEvidence | None = None

    def __post_init__(self) -> None:
        if self.handling not in HANDLING_STATES:
            raise PrivacyBoundaryError("input handling is invalid.")
        if self.handling == "standard" and self.evidence is None:
            raise PrivacyBoundaryError("standard input requires typed evidence.")


@dataclass(frozen=True)
class ValidatedPrivacyInput:
    """PB-validated input that alone may reach the final eligibility boundary."""

    handling: Handling
    origin: Literal["operational_context", "reviewed_memory", "session", "free_form", "system_control"]
    evidence: HandlingEvidence | None = None
    canonical_payload: str = ""
    _attestation: object | None = None


_ATTESTATIONS: dict[int, tuple[object, str, str, str, str]] = {}
_PB_VALIDATION_CAPABILITY = object()


def _validated_input(
    *, handling: Handling, origin: ValidatedPrivacyInput.__annotations__["origin"],
    evidence: HandlingEvidence | None = None, canonical_payload: str = "", capability: object,
) -> ValidatedPrivacyInput:
    """Attest an input only after an origin-specific PB validator has run."""
    if capability is not _PB_VALIDATION_CAPABILITY:
        raise PrivacyBoundaryError("validated input requires the PB validation boundary.")
    if handling not in HANDLING_STATES:
        raise PrivacyBoundaryError("input handling is invalid.")
    if handling == "standard" and evidence is None:
        raise PrivacyBoundaryError("standard input requires typed evidence.")
    attestation = object()
    value = ValidatedPrivacyInput(handling, origin, evidence, canonical_payload, attestation)
    _ATTESTATIONS[id(value)] = (attestation, handling, origin, evidence.kind if evidence else "", evidence.reference if evidence else "")
    return value


def is_validated_input(value: object) -> bool:
    """Runtime-check that PB-0, not an arbitrary caller, attested this exact input."""
    if not isinstance(value, ValidatedPrivacyInput):
        return False
    recorded = _ATTESTATIONS.get(id(value))
    if recorded is None:
        return False
    attestation, handling, origin, evidence_kind, evidence_reference = recorded
    if (
        value._attestation is not attestation
        or value.handling != handling
        or value.origin != origin
        or (value.evidence.kind if value.evidence else "") != evidence_kind
        or (value.evidence.reference if value.evidence else "") != evidence_reference
    ):
        return False
    if value.origin == "system_control":
        return (
            value.handling == "standard"
            and is_registered_system_control(value.canonical_payload, value.evidence)
        )
    return value.handling != "standard" or value.evidence is not None


def evaluate_external_eligibility(inputs: Iterable[object]) -> DisclosureEligibility:
    """Grant external eligibility only to runtime-attested PB-validated inputs."""
    required = tuple(inputs)
    if any(not is_validated_input(item) for item in required):
        return DisclosureEligibility(False, "local_processing_required")
    validated = tuple(item for item in required if isinstance(item, ValidatedPrivacyInput))
    if any(item.handling != "standard" or item.evidence is None for item in validated):
        return DisclosureEligibility(False, "local_processing_required")
    return DisclosureEligibility(True, "external_allowed")


def new_user_content(text: str) -> HandledInput:
    """Substantive free-form user material has no disclosure proof by default."""
    if not isinstance(text, str):
        raise PrivacyBoundaryError("user content must be text.")
    return HandledInput("unknown")


def trusted_system_control(control_id: str) -> ValidatedPrivacyInput:
    """Return a closed, system-owned control template and its validated evidence."""
    text = SYSTEM_CONTROL_TEMPLATES.get(control_id)
    if text is None:
        raise PrivacyBoundaryError("system control is not registered.")
    return _validated_input(
        handling="standard", origin="system_control",
        evidence=HandlingEvidence("trusted_system_control", control_id), canonical_payload=text,
        capability=_PB_VALIDATION_CAPABILITY,
    )


def is_registered_system_control(text: str, evidence: HandlingEvidence | None) -> bool:
    """Validate that standard system text is exactly one closed control template."""
    return (
        evidence is not None
        and evidence.kind == "trusted_system_control"
        and SYSTEM_CONTROL_TEMPLATES.get(evidence.reference) == text
    )
