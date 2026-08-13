"""Forward-only deterministic bridge from new material candidates to OC review."""
from __future__ import annotations

import hashlib
import unicodedata
from pathlib import Path
from typing import Any

from .operational_context import KIND_REGISTRY, OperationalContextStore, SENSITIVITIES, SafeProvenance
from .operational_context_review import OperationalContextCandidate, OperationalContextCandidateStore, new_candidate
from .storage import path_lock


COLLIDING_OC_AUTHORITY = object()  # compatibility sentinel; generic bridge no longer uses it
BRIDGE_CONTRACT_VERSION = "material-oc-bridge-v1"


def _normalise_subject(value: str) -> str:
    """Closed deterministic normalisation; never semantic matching."""
    normalised = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    return " ".join("".join(char if char.isalnum() else " " for char in normalised).split())


def _validated_slot_anchor(subject: object, evidence_text: str) -> str:
    if not isinstance(subject, dict) or set(subject) != {"label", "evidence_id", "slot_anchor", "value_anchor"}:
        return ""
    slot, value = subject.get("slot_anchor"), subject.get("value_anchor")
    if not isinstance(slot, str) or not slot.strip() or not isinstance(value, str) or not value.strip():
        return ""
    slot_start, value_start = evidence_text.find(slot), evidence_text.find(value)
    if slot_start < 0 or value_start < 0 or slot_start == value_start:
        return ""
    first_start, first, second_start = (slot_start, slot, value_start) if slot_start < value_start else (value_start, value, slot_start)
    if first_start + len(first) > second_start or any(char.isalnum() for char in evidence_text[first_start + len(first):second_start]):
        return ""
    return _normalise_subject(slot)


def bridge_transaction(root: Any):
    """Serialize material-to-OC routing across compiler jobs.

    This lock covers both same-subject inspection and queue persistence.  It
    prevents two concurrent extractions from creating competing pending OC
    proposals for one authority slot.
    """
    return path_lock(Path(root) / "operational_context_review_v0" / "bridge_transaction.lock")


def build_material_proposal_with_diagnostic(*, root: Any, workspace_id: str, sanitized_id: str, candidate: dict[str, Any], evidence_text: str, sensitivity: str = "unknown") -> tuple[OperationalContextCandidate | object | None, dict[str, str]]:
    """Build an unchanged proposal plus a content-free routing reason.

    The model may propose a source-grounded authority-slot label, but code
    derives the opaque identity.  Without a specific slot the legacy path wins.
    """
    kind = str(candidate.get("type") or "")
    block = candidate.get("block")
    if kind not in KIND_REGISTRY or not isinstance(block, dict):
        return None, {"stage": "bridge_rejected", "reason": "kind_or_block_invalid"}
    start, end = block.get("start"), block.get("end")
    if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start:
        return None, {"stage": "bridge_rejected", "reason": "block_invalid"}
    raw_subject = candidate.get("oc_subject")
    slot = _validated_slot_anchor(raw_subject, evidence_text)
    if not slot:
        return None, {"stage": "bridge_rejected", "reason": "subject_not_grounded"}
    subject = "ocs_" + hashlib.sha256(f"{kind}\x1f{slot}".encode("utf-8")).hexdigest()[:32]
    extraction_identity = f"{sanitized_id}\x1f{kind}\x1f{start}\x1f{end}"
    candidate_ref = f"moc_{hashlib.sha256(extraction_identity.encode('utf-8')).hexdigest()[:32]}"
    candidate_id = f"occ_{hashlib.sha256(extraction_identity.encode('utf-8')).hexdigest()[:32]}"
    if sensitivity not in SENSITIVITIES:
        sensitivity = "unknown"
    current = [item for item in OperationalContextStore(root).list_scope(scope="project", scope_ref=workspace_id) if item["lifecycle"] == "active" and item["kind"] == kind and item["subject_ref"] == subject]
    if len(current) > 1:
        return None, {"stage": "bridge_rejected", "reason": "authority_slot_ambiguous"}
    # A repeated extraction of an equivalent value is not a second proposal.
    pending = OperationalContextCandidateStore(root).list_scope(scope="project", scope_ref=workspace_id, state="pending")
    statement = _normalise_subject(str(candidate["statement"]))
    if any(item.kind == kind and item.subject_ref == subject and _normalise_subject(item.value) == statement for item in pending):
        return COLLIDING_OC_AUTHORITY, {"stage": "bridge_accepted", "reason": "equivalent_pending_noop"}
    if any(_normalise_subject(str(item.get("value") or item.get("reference") or "")) == statement for item in current):
        return COLLIDING_OC_AUTHORITY, {"stage": "bridge_accepted", "reason": "equivalent_current_noop"}
    return new_candidate(
        scope="project", scope_ref=workspace_id, kind=kind,
        subject_ref=subject, value=str(candidate["statement"]),
        provenance=SafeProvenance(source_ref=sanitized_id, candidate_ref=candidate_ref),
        sensitivity=sensitivity, reason="Предложение извлечено из подтверждённого материала.",
        candidate_id=candidate_id, replaces_id=current[0]["id"] if current else "",
    ), {"stage": "bridge_accepted", "reason": "proposal_created"}


def build_material_proposal(*, root: Any, workspace_id: str, sanitized_id: str, candidate: dict[str, Any], evidence_text: str, sensitivity: str = "unknown") -> OperationalContextCandidate | object | None:
    """Build an OC proposal from already-validated extraction facts only.

    The model may propose a source-grounded authority-slot label, but code
    derives the opaque identity.  Without a specific slot the legacy path wins.
    """
    return build_material_proposal_with_diagnostic(
        root=root, workspace_id=workspace_id, sanitized_id=sanitized_id,
        candidate=candidate, evidence_text=evidence_text, sensitivity=sensitivity,
    )[0]


def persist_material_proposals(root: Any, proposals: list[OperationalContextCandidate]) -> None:
    """Persist a successful compiler batch idempotently under one queue lock."""
    OperationalContextCandidateStore(root).add_many_if_absent(proposals)
