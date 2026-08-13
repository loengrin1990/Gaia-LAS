"""Forward-only deterministic bridge from new material candidates to OC review."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .operational_context import KIND_REGISTRY, OperationalContextStore, SENSITIVITIES, SafeProvenance
from .operational_context_review import OperationalContextCandidate, OperationalContextCandidateStore, new_candidate
from .storage import path_lock


MATERIAL_SUBJECT_MARKERS = {
    "текущий статус поставки": "delivery_status",
    "финальное согласование": "final_approval",
}
COLLIDING_OC_AUTHORITY = object()


def bridge_transaction(root: Any):
    """Serialize material-to-OC routing across compiler jobs.

    This lock covers both same-subject inspection and queue persistence.  It
    prevents two concurrent extractions from creating competing pending OC
    proposals for one authority slot.
    """
    return path_lock(Path(root) / "operational_context_review_v0" / "bridge_transaction.lock")


def build_material_proposal(*, root: Any, workspace_id: str, sanitized_id: str, candidate: dict[str, Any], evidence_text: str, sensitivity: str = "unknown") -> OperationalContextCandidate | object | None:
    """Build an OC proposal from already-validated extraction facts only.

    The subject comes only from a closed mapping of an explicit label in the
    exact cleaned evidence. No LLM-supplied identity or classifier is accepted.
    Without that label, the caller must retain the legacy candidate path.
    """
    kind = str(candidate.get("type") or "")
    block = candidate.get("block")
    if kind not in KIND_REGISTRY or not isinstance(block, dict):
        return None
    start, end = block.get("start"), block.get("end")
    if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start:
        return None
    folded = " ".join(evidence_text.casefold().replace("ё", "е").split())
    subject = next((value for marker, value in MATERIAL_SUBJECT_MARKERS.items() if marker in folded), "")
    if not subject:
        return None
    proposal_identity = f"{workspace_id}\x1f{sanitized_id}\x1f{kind}\x1f{subject}"
    proposal_digest = hashlib.sha256(proposal_identity.encode("utf-8")).hexdigest()
    extraction_identity = f"{sanitized_id}\x1f{kind}\x1f{start}\x1f{end}"
    candidate_ref = f"moc_{hashlib.sha256(extraction_identity.encode('utf-8')).hexdigest()[:32]}"
    candidate_id = f"occ_{proposal_digest[:32]}"
    if sensitivity not in SENSITIVITIES:
        sensitivity = "unknown"
    current = [item for item in OperationalContextStore(root).list_scope(scope="project", scope_ref=workspace_id) if item["lifecycle"] == "active" and item["kind"] == kind and item["subject_ref"] == subject]
    if len(current) > 1:
        return None
    # The project-scope store requires an explicit replacement for one active
    # identity. A second undecided project proposal cannot become a conflict
    # through this producer, so skip it without creating a legacy duplicate.
    pending = OperationalContextCandidateStore(root).list_scope(scope="project", scope_ref=workspace_id, state="pending")
    if any(item.kind == kind and item.subject_ref == subject and item.id != candidate_id for item in pending):
        return COLLIDING_OC_AUTHORITY
    return new_candidate(
        scope="project", scope_ref=workspace_id, kind=kind,
        subject_ref=subject, value=str(candidate["statement"]),
        provenance=SafeProvenance(source_ref=sanitized_id, candidate_ref=candidate_ref),
        sensitivity=sensitivity, reason="Предложение извлечено из подтверждённого материала.",
        candidate_id=candidate_id, replaces_id=current[0]["id"] if current else "",
    )


def persist_material_proposals(root: Any, proposals: list[OperationalContextCandidate]) -> None:
    """Persist a successful compiler batch idempotently under one queue lock."""
    OperationalContextCandidateStore(root).add_many_if_absent(proposals)
