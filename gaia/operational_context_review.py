"""Human review boundary for Operational Context v0.

Candidates are isolated from legacy Context records and can become runtime
authority only through the controlled OC-1 store transitions.
"""
from __future__ import annotations

import json
import hashlib
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from .operational_context import (
    KIND_REGISTRY,
    SCOPES,
    SENSITIVITIES,
    OperationalContextError,
    OperationalContextStore,
    SafeProvenance,
    _opaque,
    _scope,
    new_evidence,
    new_item,
)
from .operational_context_retrieval import AuthorityAmbiguity
from .storage import atomic_write_text, path_lock


class OperationalContextReviewError(ValueError):
    """Fail-closed error for an invalid v0 review action or candidate."""


KIND_LABELS = {
    "requirement": "Требование", "decision": "Решение", "risk": "Риск",
    "open_question": "Открытый вопрос", "action": "Действие",
}
SENSITIVITY_LABELS = {"standard": "Обычный", "restricted": "Ограниченный", "unknown": "Не определено — только локально"}


@dataclass(frozen=True)
class OperationalContextCandidate:
    """A proposed OC value; never a retrieval-visible operational item."""

    id: str
    scope: str
    scope_ref: str
    kind: str
    subject_ref: str
    value: str = ""
    reference: str = ""
    provenance: SafeProvenance | None = None
    sensitivity: str = "standard"
    reason: str = ""
    replaces_id: str = ""
    state: str = "pending"

    def __post_init__(self) -> None:
        _opaque(self.id, "candidate.id"); _scope(self.scope); _opaque(self.scope_ref, "candidate.scope_ref")
        definition = KIND_REGISTRY.get(self.kind)
        if definition is None or self.scope not in definition.allowed_scopes:
            raise OperationalContextReviewError("candidate kind or scope is invalid.")
        _opaque(self.subject_ref, "candidate.subject_ref")
        if not isinstance(self.value, str) or not isinstance(self.reference, str) or not (self.value.strip() or self.reference.strip()):
            raise OperationalContextReviewError("candidate requires value or reference.")
        if self.reference:
            _opaque(self.reference, "candidate.reference")
        if not isinstance(self.provenance, SafeProvenance) or self.sensitivity not in SENSITIVITIES:
            raise OperationalContextReviewError("candidate provenance or sensitivity is invalid.")
        if not isinstance(self.reason, str) or self.state not in {"pending", "confirmed", "rejected"}:
            raise OperationalContextReviewError("candidate state is invalid.")
        if self.replaces_id:
            _opaque(self.replaces_id, "candidate.replaces_id")

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "OperationalContextCandidate":
        payload = dict(raw)
        payload["provenance"] = SafeProvenance.from_mapping(payload.get("provenance", {}))
        return cls(**payload)


def new_candidate(*, scope: str, scope_ref: str, kind: str, subject_ref: str,
                  value: str = "", reference: str = "", provenance: SafeProvenance,
                  sensitivity: str = "standard", reason: str = "", replaces_id: str = "",
                  candidate_id: str | None = None) -> OperationalContextCandidate:
    return OperationalContextCandidate(
        candidate_id or f"occ_{uuid.uuid4().hex}", scope, scope_ref, kind, subject_ref,
        value, reference, provenance, sensitivity, reason, replaces_id,
    )


class OperationalContextCandidateStore:
    """Small isolated queue; it neither reads nor writes legacy Context records."""

    def __init__(self, root: Path) -> None:
        self.path = Path(root) / "operational_context_review_v0" / "candidates.json"
        self.deferred_path = self.path.with_name("deferred_ambiguities.json")

    def add(self, candidate: OperationalContextCandidate) -> dict[str, Any]:
        with path_lock(self.path):
            state = self._read()
            if candidate.id in state:
                raise OperationalContextReviewError("candidate already exists.")
            state[candidate.id] = _candidate_dict(candidate); self._write(state)
        return _candidate_dict(candidate)

    def add_if_absent(self, candidate: OperationalContextCandidate) -> dict[str, Any]:
        """Idempotent bridge insert; an existing same deterministic proposal wins."""
        with path_lock(self.path):
            state = self._read()
            raw = state.get(candidate.id)
            if raw is not None:
                return _candidate_dict(self._parse(raw, candidate.id))
            state[candidate.id] = _candidate_dict(candidate); self._write(state)
        return _candidate_dict(candidate)

    def add_many_if_absent(self, candidates: list[OperationalContextCandidate]) -> None:
        """Atomically add a completed compiler batch without duplicate proposals."""
        with path_lock(self.path):
            state = self._read()
            for candidate in candidates:
                raw = state.get(candidate.id)
                if raw is not None:
                    self._parse(raw, candidate.id)
                    continue
                state[candidate.id] = _candidate_dict(candidate)
            self._write(state)

    def get(self, candidate_id: str) -> OperationalContextCandidate:
        _opaque(candidate_id, "candidate_id")
        raw = self._read().get(candidate_id)
        if raw is None:
            raise OperationalContextReviewError("candidate is unavailable.")
        return self._parse(raw, candidate_id)

    def list_scope(self, *, scope: str, scope_ref: str, state: str | None = None) -> list[OperationalContextCandidate]:
        _scope(scope); _opaque(scope_ref, "scope_ref")
        candidates = [self._parse(raw, candidate_id) for candidate_id, raw in self._read().items()]
        return sorted((item for item in candidates if item.scope == scope and item.scope_ref == scope_ref and (state is None or item.state == state)), key=lambda item: item.id)

    def set_state(self, candidate: OperationalContextCandidate, state: str) -> OperationalContextCandidate:
        if state not in {"confirmed", "rejected"}:
            raise OperationalContextReviewError("candidate state transition is invalid.")
        with path_lock(self.path):
            values = self._read(); current = self._parse(values.get(candidate.id), candidate.id)
            if current.state != "pending":
                raise OperationalContextReviewError("candidate was already decided.")
            updated = replace(current, state=state)
            values[updated.id] = _candidate_dict(updated); self._write(values)
        return updated

    def defer_ambiguity(self, review_ref: str) -> None:
        _opaque(review_ref, "review_ref")
        with path_lock(self.deferred_path):
            values = self._read_deferred(); values[review_ref] = {"state": "deferred"}; self._write_deferred(values)

    def is_deferred(self, review_ref: str) -> bool:
        return self._read_deferred().get(review_ref, {}).get("state") == "deferred"

    def _read(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise OperationalContextReviewError("candidate queue is invalid.") from exc
        if not isinstance(raw, dict):
            raise OperationalContextReviewError("candidate queue is invalid.")
        return raw

    @staticmethod
    def _parse(raw: Any, candidate_id: str) -> OperationalContextCandidate:
        if not isinstance(raw, dict):
            raise OperationalContextReviewError("candidate is invalid.")
        try:
            candidate = OperationalContextCandidate.from_mapping(raw)
        except (TypeError, OperationalContextError, OperationalContextReviewError) as exc:
            raise OperationalContextReviewError("candidate is invalid.") from exc
        if candidate.id != candidate_id:
            raise OperationalContextReviewError("candidate is invalid.")
        return candidate

    def _write(self, values: dict[str, dict[str, Any]]) -> None:
        atomic_write_text(self.path, json.dumps(values, ensure_ascii=False, sort_keys=True, indent=2) + "\n")

    def _read_deferred(self) -> dict[str, dict[str, str]]:
        if not self.deferred_path.exists(): return {}
        try: raw = json.loads(self.deferred_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc: raise OperationalContextReviewError("deferred review state is invalid.") from exc
        if not isinstance(raw, dict): raise OperationalContextReviewError("deferred review state is invalid.")
        return raw

    def _write_deferred(self, values: dict[str, dict[str, str]]) -> None:
        atomic_write_text(self.deferred_path, json.dumps(values, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


class OperationalContextReviewService:
    """Presents safe local review cards and delegates all authority changes to OC-1."""

    def __init__(self, store: OperationalContextStore, candidates: OperationalContextCandidateStore) -> None:
        self.store = store
        self.candidates = candidates

    def view(
        self,
        *,
        scope: str,
        scope_ref: str,
        ambiguities: tuple[AuthorityAmbiguity, ...] = (),
        history_scopes: tuple[tuple[str, str], ...] = (),
    ) -> dict[str, Any]:
        _scope(scope); _opaque(scope_ref, "scope_ref")
        ambiguity_cards = [self._ambiguity_card(item) for item in ambiguities]
        active_ambiguities = [item for item in ambiguity_cards if not self.candidates.is_deferred(item["review_ref"])]
        deferred_ambiguities = [item for item in ambiguity_cards if self.candidates.is_deferred(item["review_ref"])]
        history = [self._candidate_history(item) for item in self.candidates.list_scope(scope=scope, scope_ref=scope_ref) if item.state != "pending"]
        history.extend(self._lifecycle_history(item) for item in self.store.list_scope(scope=scope, scope_ref=scope_ref) if item["lifecycle"] in {"superseded", "retired"})
        for history_scope, history_scope_ref in history_scopes:
            if (history_scope, history_scope_ref) != (scope, scope_ref):
                history.extend(
                    self._lifecycle_history(item)
                    for item in self.store.list_scope(scope=history_scope, scope_ref=history_scope_ref)
                    if item["lifecycle"] in {"superseded", "retired"}
                )
        return {
            "requires_decision": [self._candidate_card(item) for item in self.candidates.list_scope(scope=scope, scope_ref=scope_ref, state="pending")],
            "current_context": [self._current_card(item) for item in self.store.list_scope(scope=scope, scope_ref=scope_ref) if item["lifecycle"] == "active" and item["confirmation"] == "confirmed"],
            "ambiguities": active_ambiguities, "deferred_ambiguities": deferred_ambiguities,
            "history": sorted(history, key=lambda item: (item["status"], item["content"])),
        }

    def confirm(self, candidate_id: str, *, actor_ref: str) -> dict[str, Any]:
        candidate = self.candidates.get(candidate_id)
        if candidate.state != "pending":
            raise OperationalContextReviewError("candidate was already decided.")
        item_id = f"oc_{uuid.uuid4().hex}"
        evidence_id = f"oce_{uuid.uuid4().hex}"
        action = "replacement" if candidate.replaces_id else "promotion"
        evidence = new_evidence(
            scope=candidate.scope, scope_ref=candidate.scope_ref, action=action,
            target_item_id=item_id, actor_ref=actor_ref, source_ref=candidate.provenance.source_ref,
            candidate_ref=candidate.provenance.candidate_ref, prior_item_id=candidate.replaces_id,
            evidence_id=evidence_id,
        )
        item = new_item(
            scope=candidate.scope, scope_ref=candidate.scope_ref, kind=candidate.kind,
            subject_ref=candidate.subject_ref, value=candidate.value, reference=candidate.reference,
            provenance=candidate.provenance, confirmation_ref=evidence.id,
            sensitivity=candidate.sensitivity, item_id=item_id, supersedes_id=candidate.replaces_id,
        )
        saved = self.store.replace(candidate.replaces_id, item, evidence) if candidate.replaces_id else self.store.create(item, evidence)
        self.candidates.set_state(candidate, "confirmed")
        return self._current_card(saved)

    def reject(self, candidate_id: str) -> None:
        candidate = self.candidates.get(candidate_id)
        self.candidates.set_state(candidate, "rejected")

    def retire(self, *, scope: str, scope_ref: str, item_id: str, actor_ref: str) -> dict[str, Any]:
        evidence = new_evidence(scope=scope, scope_ref=scope_ref, action="retirement", target_item_id=item_id, actor_ref=actor_ref)
        return self._current_card(self.store.retire(scope=scope, scope_ref=scope_ref, item_id=item_id, evidence=evidence))

    def defer_ambiguity(self, review_ref: str, ambiguities: tuple[AuthorityAmbiguity, ...]) -> None:
        if review_ref not in {self._ambiguity_ref(item) for item in ambiguities}:
            raise OperationalContextReviewError("ambiguity is unavailable.")
        self.candidates.defer_ambiguity(review_ref)

    def retire_ambiguity_alternative(self, review_ref: str, alternative_index: int, ambiguities: tuple[AuthorityAmbiguity, ...], *, actor_ref: str) -> None:
        ambiguity = next((item for item in ambiguities if self._ambiguity_ref(item) == review_ref), None)
        if ambiguity is None or not isinstance(alternative_index, int) or not 0 <= alternative_index < len(ambiguity.involved_authorities):
            raise OperationalContextReviewError("ambiguity alternative is unavailable.")
        authority = ambiguity.involved_authorities[alternative_index]
        self.retire(scope=authority.scope, scope_ref=authority.scope_ref, item_id=authority.item_id, actor_ref=actor_ref)

    def _candidate_card(self, candidate: OperationalContextCandidate) -> dict[str, str]:
        previous_content = ""
        if candidate.replaces_id:
            previous = self.store.get(scope=candidate.scope, scope_ref=candidate.scope_ref, item_id=candidate.replaces_id)
            previous_content = str(previous["value"] or previous["reference"])
        return {
            "id": candidate.id, "content": candidate.value or candidate.reference,
            "kind": KIND_LABELS[candidate.kind], "context": "Проект" if candidate.scope == "project" else "Локальный контекст",
            "source": "Локальный подтверждаемый источник", "sensitivity": SENSITIVITY_LABELS[candidate.sensitivity],
            "reason": candidate.reason or "Требуется подтверждение человека.",
            "replacement_of": candidate.replaces_id,
            "previous_content": previous_content,
        }

    @staticmethod
    def _current_card(item: dict[str, Any]) -> dict[str, str]:
        return {
            "id": str(item["id"]), "content": str(item["value"] or item["reference"]),
            "kind": KIND_LABELS[str(item["kind"])], "context": "Проект" if item["scope"] == "project" else "Локальный контекст",
            "sensitivity": SENSITIVITY_LABELS[str(item["sensitivity"])],
        }

    @staticmethod
    def _candidate_history(candidate: OperationalContextCandidate) -> dict[str, str]:
        return {"content": candidate.value or candidate.reference, "status": "Отклонено" if candidate.state == "rejected" else "Подтверждено", "source": "Локальный подтверждаемый источник", "sensitivity": SENSITIVITY_LABELS[candidate.sensitivity]}

    @staticmethod
    def _lifecycle_history(item: dict[str, Any]) -> dict[str, str]:
        return {"content": str(item["value"] or item["reference"]), "status": "Заменено" if item["lifecycle"] == "superseded" else "Больше не актуально", "source": "Локальный подтверждённый источник", "sensitivity": SENSITIVITY_LABELS[str(item["sensitivity"])]}

    def _ambiguity_card(self, ambiguity: AuthorityAmbiguity) -> dict[str, Any]:
        alternatives = []
        for authority in ambiguity.involved_authorities:
            item = self.store.get(scope=authority.scope, scope_ref=authority.scope_ref, item_id=authority.item_id)
            alternatives.append({
                "content": str(item["value"] or item["reference"]),
                "source": "Локальный подтверждённый источник",
            })
        return {
            "kind": KIND_LABELS[ambiguity.kind], "sensitivity": SENSITIVITY_LABELS[ambiguity.derived_sensitivity],
            "message": "Нужно уточнение", "hint": "Чтобы разрешить противоречие, отметьте неактуальный вариант.",
            "review_ref": self._ambiguity_ref(ambiguity), "alternatives": alternatives,
        }

    @staticmethod
    def _ambiguity_ref(ambiguity: AuthorityAmbiguity) -> str:
        identity = ":".join(sorted(authority.item_id for authority in ambiguity.involved_authorities))
        return "ocr_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _candidate_dict(candidate: OperationalContextCandidate) -> dict[str, Any]:
    payload = asdict(candidate)
    payload["provenance"] = asdict(candidate.provenance) if candidate.provenance else {}
    return payload
