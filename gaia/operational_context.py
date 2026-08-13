"""Isolated, scope-partitioned persistence for Operational Context v0.

This module intentionally has no dependency on the legacy ``context/current``
records or on Dialogue.  Its public write operations are controlled lifecycle
transitions; semantic fields are never updated in place.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from .storage import atomic_write_text, path_lock


Scope = Literal["user", "project", "system"]
Lifecycle = Literal["active", "superseded", "retired"]
Sensitivity = Literal["standard", "restricted", "unknown"]
EvidenceAction = Literal["promotion", "replacement", "retirement"]

SCOPES = frozenset(("user", "project", "system"))
LIFECYCLES = frozenset(("active", "superseded", "retired"))
SENSITIVITIES = frozenset(("standard", "restricted", "unknown"))
EVIDENCE_ACTIONS = frozenset(("promotion", "replacement", "retirement"))
_OPAQUE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class OperationalContextError(ValueError):
    """Fail-closed validation or controlled-transition error."""


@dataclass(frozen=True)
class KindDefinition:
    id: str
    allowed_scopes: frozenset[str]
    subject_semantics: str
    composition_rule: str | None = None


# These are the five existing canonical Stage 7 vocabulary identifiers.  OC-1
# assigns no universal cross-scope precedence to any of them.
KIND_REGISTRY: dict[str, KindDefinition] = {
    "requirement": KindDefinition("requirement", frozenset(("project", "system")), "opaque identifier of the requirement"),
    "decision": KindDefinition("decision", frozenset(("project", "system")), "opaque identifier of the decision or constraint"),
    "risk": KindDefinition("risk", frozenset(("project", "system")), "opaque identifier of the risk"),
    "open_question": KindDefinition("open_question", frozenset(("project",)), "opaque identifier of the unresolved question"),
    "action": KindDefinition("action", frozenset(("user", "project")), "opaque identifier of the action or commitment"),
}


def _opaque(value: str, field: str) -> str:
    if not isinstance(value, str) or not _OPAQUE_REF.fullmatch(value):
        raise OperationalContextError(f"{field} must be a safe opaque reference.")
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class SafeProvenance:
    """Minimal references only; source material and paths have no field here."""

    source_ref: str = ""
    candidate_ref: str = ""
    memory_ref: str = ""

    def __post_init__(self) -> None:
        if not self.source_ref and not self.candidate_ref:
            raise OperationalContextError("provenance requires a source_ref or candidate_ref.")
        for name, value in asdict(self).items():
            if value:
                _opaque(value, name)

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "SafeProvenance":
        if set(payload) - {"source_ref", "candidate_ref", "memory_ref"}:
            raise OperationalContextError("provenance contains unsupported fields.")
        return cls(**payload)


@dataclass(frozen=True)
class ConfirmationEvidence:
    id: str
    scope: Scope
    scope_ref: str
    action: EvidenceAction
    target_item_id: str
    actor_ref: str
    created_at: str
    source_ref: str = ""
    candidate_ref: str = ""
    prior_item_id: str = ""

    def __post_init__(self) -> None:
        _opaque(self.id, "evidence.id"); _scope(self.scope); _opaque(self.scope_ref, "evidence.scope_ref")
        if self.action not in EVIDENCE_ACTIONS:
            raise OperationalContextError("evidence action is unknown.")
        _opaque(self.target_item_id, "evidence.target_item_id"); _opaque(self.actor_ref, "evidence.actor_ref")
        if not self.created_at:
            raise OperationalContextError("evidence created_at is required.")
        if self.action in {"promotion", "replacement"} and not (self.source_ref or self.candidate_ref):
            raise OperationalContextError("promotion evidence requires a candidate or source reference.")
        for name in ("source_ref", "candidate_ref", "prior_item_id"):
            value = getattr(self, name)
            if value:
                _opaque(value, f"evidence.{name}")


def _scope(scope: str) -> None:
    if scope not in SCOPES:
        raise OperationalContextError("scope is unknown.")


@dataclass(frozen=True)
class OperationalItem:
    id: str
    scope: Scope
    scope_ref: str
    kind: str
    subject_ref: str
    value: str = ""
    reference: str = ""
    provenance: SafeProvenance | None = None
    created_at: str = ""
    updated_at: str = ""
    lifecycle: Lifecycle = "active"
    confirmation: str = "confirmed"
    confirmation_ref: str = ""
    sensitivity: Sensitivity = "standard"
    supersedes_id: str = ""

    def __post_init__(self) -> None:
        _opaque(self.id, "item.id"); _scope(self.scope); _opaque(self.scope_ref, "item.scope_ref")
        definition = KIND_REGISTRY.get(self.kind)
        if definition is None:
            raise OperationalContextError("kind is not in the closed registry.")
        if self.scope not in definition.allowed_scopes:
            raise OperationalContextError("scope is not allowed for kind.")
        _opaque(self.subject_ref, "item.subject_ref")
        if not isinstance(self.value, str) or not isinstance(self.reference, str) or not (self.value.strip() or self.reference.strip()):
            raise OperationalContextError("item requires value or reference.")
        if self.reference:
            _opaque(self.reference, "item.reference")
        if not isinstance(self.provenance, SafeProvenance):
            raise OperationalContextError("item provenance must be structured safe provenance.")
        if self.lifecycle not in LIFECYCLES:
            raise OperationalContextError("item lifecycle is invalid.")
        if self.confirmation != "confirmed" or not self.confirmation_ref:
            raise OperationalContextError("operational item requires confirmed immutable evidence.")
        _opaque(self.confirmation_ref, "item.confirmation_ref")
        if self.sensitivity not in SENSITIVITIES:
            raise OperationalContextError("sensitivity is invalid.")
        if not self.created_at or not self.updated_at:
            raise OperationalContextError("item timestamps are required.")
        if self.supersedes_id:
            _opaque(self.supersedes_id, "item.supersedes_id")

    @property
    def identity(self) -> tuple[str, str, str, str]:
        return self.scope, self.scope_ref, self.kind, self.subject_ref


def new_item(*, scope: Scope, scope_ref: str, kind: str, subject_ref: str, value: str = "", reference: str = "", provenance: SafeProvenance, confirmation_ref: str, sensitivity: Sensitivity = "standard", item_id: str | None = None, supersedes_id: str = "") -> OperationalItem:
    now = _now()
    return OperationalItem(item_id or f"oc_{uuid.uuid4().hex}", scope, scope_ref, kind, subject_ref, value, reference, provenance, now, now, "active", "confirmed", confirmation_ref, sensitivity, supersedes_id)


def new_evidence(*, scope: Scope, scope_ref: str, action: EvidenceAction, target_item_id: str, actor_ref: str, source_ref: str = "", candidate_ref: str = "", prior_item_id: str = "", evidence_id: str | None = None) -> ConfirmationEvidence:
    return ConfirmationEvidence(evidence_id or f"oce_{uuid.uuid4().hex}", scope, scope_ref, action, target_item_id, actor_ref, _now(), source_ref, candidate_ref, prior_item_id)


class OperationalContextStore:
    """An OC-only store partitioned by exact scope and opaque scope_ref."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root) / "operational_context_v0"

    def create(self, item: OperationalItem, evidence: ConfirmationEvidence) -> dict[str, Any]:
        if evidence.action != "promotion":
            raise OperationalContextError("new item requires promotion evidence.")
        return self._write_new(item, evidence, None)

    def replace(self, old_id: str, item: OperationalItem, evidence: ConfirmationEvidence) -> dict[str, Any]:
        if evidence.action != "replacement" or evidence.prior_item_id != old_id or item.supersedes_id != old_id:
            raise OperationalContextError("replacement evidence and supersedes_id must name the old item.")
        return self._write_new(item, evidence, old_id)

    def retire(self, *, scope: Scope, scope_ref: str, item_id: str, evidence: ConfirmationEvidence) -> dict[str, Any]:
        _scope(scope); _opaque(scope_ref, "scope_ref"); _opaque(item_id, "item_id")
        if evidence.action != "retirement" or evidence.target_item_id != item_id or evidence.scope != scope or evidence.scope_ref != scope_ref:
            raise OperationalContextError("retirement evidence does not match the item.")
        path = self._partition_path(scope, scope_ref)
        with path_lock(path):
            state = self._read(path, scope, scope_ref); old = state["items"].get(item_id)
            if not old or old["lifecycle"] != "active":
                raise OperationalContextError("only an active item can be retired.")
            self._assert_new_evidence(state, evidence)
            old["lifecycle"] = "retired"; old["updated_at"] = evidence.created_at
            state["evidence"][evidence.id] = asdict(evidence); self._write(path, state)
            return dict(old)

    def get(self, *, scope: Scope, scope_ref: str, item_id: str) -> dict[str, Any]:
        _scope(scope); _opaque(scope_ref, "scope_ref"); _opaque(item_id, "item_id")
        item = self._read(self._partition_path(scope, scope_ref), scope, scope_ref)["items"].get(item_id)
        if item is None:
            raise OperationalContextError("item is unavailable in this exact scope.")
        return dict(item)

    def list_scope(self, *, scope: Scope, scope_ref: str) -> list[dict[str, Any]]:
        _scope(scope); _opaque(scope_ref, "scope_ref")
        state = self._read(self._partition_path(scope, scope_ref), scope, scope_ref)
        return [dict(item) for item in state["items"].values()]

    def evidence(self, *, scope: Scope, scope_ref: str, evidence_id: str) -> dict[str, Any]:
        _scope(scope); _opaque(scope_ref, "scope_ref"); _opaque(evidence_id, "evidence_id")
        evidence = self._read(self._partition_path(scope, scope_ref), scope, scope_ref)["evidence"].get(evidence_id)
        if evidence is None:
            raise OperationalContextError("evidence is unavailable in this exact scope.")
        return dict(evidence)

    def lineage(self, *, scope: Scope, scope_ref: str, item_id: str) -> dict[str, Any]:
        item = self.get(scope=scope, scope_ref=scope_ref, item_id=item_id)
        state = self._read(self._partition_path(scope, scope_ref), scope, scope_ref)
        newer = sorted(x["id"] for x in state["items"].values() if x.get("supersedes_id") == item_id)
        return {"item": item, "supersedes": item.get("supersedes_id", ""), "superseded_by": newer}

    def set_authority_ambiguity(self, *, scope: Scope, scope_ref: str, active_item_id: str, candidate_id: str, kind: str, subject_ref: str, value: str, provenance: SafeProvenance, sensitivity: Sensitivity) -> None:
        """Persist a reviewed unresolved alternative without creating another active item."""
        _scope(scope); _opaque(scope_ref, "scope_ref"); _opaque(active_item_id, "active_item_id"); _opaque(candidate_id, "candidate_id")
        if kind not in KIND_REGISTRY or sensitivity not in SENSITIVITIES:
            raise OperationalContextError("authority ambiguity is invalid.")
        path = self._partition_path(scope, scope_ref)
        with path_lock(path):
            state = self._read(path, scope, scope_ref); active = state["items"].get(active_item_id)
            if not active or active.get("lifecycle") != "active" or (active.get("kind"), active.get("subject_ref")) != (kind, subject_ref):
                raise OperationalContextError("authority ambiguity target is invalid.")
            values = state.setdefault("ambiguities", {})
            values[f"{kind}:{subject_ref}"] = {"active_item_id": active_item_id, "candidate_id": candidate_id, "kind": kind, "subject_ref": subject_ref, "value": value, "provenance": asdict(provenance), "sensitivity": sensitivity}
            self._write(path, state)

    def authority_ambiguities(self, *, scope: Scope, scope_ref: str) -> list[dict[str, Any]]:
        state = self._read(self._partition_path(scope, scope_ref), scope, scope_ref)
        return [dict(value) for value in state.get("ambiguities", {}).values()]

    def clear_authority_ambiguity(self, *, scope: Scope, scope_ref: str, kind: str, subject_ref: str) -> None:
        path = self._partition_path(scope, scope_ref)
        with path_lock(path):
            state = self._read(path, scope, scope_ref); state.get("ambiguities", {}).pop(f"{kind}:{subject_ref}", None); self._write(path, state)

    def _write_new(self, item: OperationalItem, evidence: ConfirmationEvidence, old_id: str | None) -> dict[str, Any]:
        if evidence.scope != item.scope or evidence.scope_ref != item.scope_ref or evidence.target_item_id != item.id or item.confirmation_ref != evidence.id:
            raise OperationalContextError("confirmation evidence does not match the new item.")
        path = self._partition_path(item.scope, item.scope_ref)
        with path_lock(path):
            state = self._read(path, item.scope, item.scope_ref)
            if item.id in state["items"]:
                raise OperationalContextError("item id already exists.")
            self._assert_new_evidence(state, evidence)
            self._assert_evidence_matches_provenance(item, evidence)
            same_identity = [x for x in state["items"].values() if tuple(x[k] for k in ("scope", "scope_ref", "kind", "subject_ref")) == item.identity and x["lifecycle"] == "active"]
            if old_id is None and same_identity:
                raise OperationalContextError("active identity requires explicit replacement.")
            if old_id is not None:
                old = state["items"].get(old_id)
                if not old or old["lifecycle"] != "active" or tuple(old[k] for k in ("scope", "scope_ref", "kind", "subject_ref")) != item.identity:
                    raise OperationalContextError("replacement target is not the active item of the same identity.")
                old["lifecycle"] = "superseded"; old["updated_at"] = evidence.created_at
            state["evidence"][evidence.id] = asdict(evidence); state["items"][item.id] = _item_dict(item); self._write(path, state)
            return dict(state["items"][item.id])

    @staticmethod
    def _assert_new_evidence(state: dict[str, Any], evidence: ConfirmationEvidence) -> None:
        if evidence.id in state["evidence"]:
            raise OperationalContextError("evidence is immutable and already exists.")

    @staticmethod
    def _assert_evidence_matches_provenance(item: OperationalItem, evidence: ConfirmationEvidence) -> None:
        """Evidence must prove the exact safe source/candidate lineage of its item."""
        provenance = item.provenance
        if provenance is None or evidence.candidate_ref != provenance.candidate_ref or evidence.source_ref != provenance.source_ref:
            raise OperationalContextError("confirmation evidence does not match item provenance.")

    def _partition_path(self, scope: str, scope_ref: str) -> Path:
        digest = hashlib.sha256(f"{scope}:{scope_ref}".encode()).hexdigest()
        return self.root / scope / digest[:2] / f"{digest}.json"

    @classmethod
    def _read(cls, path: Path, scope: Scope, scope_ref: str) -> dict[str, Any]:
        if not path.exists():
            return {"schema_version": 1, "items": {}, "evidence": {}}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise OperationalContextError("operational context partition is invalid.") from exc
        if (not isinstance(payload, dict) or payload.get("schema_version") != 1
                or not isinstance(payload.get("items"), dict) or not isinstance(payload.get("evidence"), dict) or ("ambiguities" in payload and not isinstance(payload["ambiguities"], dict))):
            raise OperationalContextError("operational context partition is invalid.")
        evidence: dict[str, ConfirmationEvidence] = {}
        try:
            for key, raw in payload["evidence"].items():
                if not isinstance(key, str) or not isinstance(raw, dict):
                    raise OperationalContextError("persisted evidence is invalid.")
                parsed = ConfirmationEvidence(**raw)
                if parsed.id != key or parsed.scope != scope or parsed.scope_ref != scope_ref:
                    raise OperationalContextError("persisted evidence is invalid.")
                evidence[key] = parsed
            for key, raw in payload["items"].items():
                if not isinstance(key, str) or not isinstance(raw, dict):
                    raise OperationalContextError("persisted item is invalid.")
                raw = dict(raw); raw["provenance"] = SafeProvenance.from_mapping(raw.get("provenance", {}))
                parsed = OperationalItem(**raw)
                if parsed.id != key or parsed.scope != scope or parsed.scope_ref != scope_ref:
                    raise OperationalContextError("persisted item is invalid.")
                confirmation = evidence.get(parsed.confirmation_ref)
                required_action = "replacement" if parsed.supersedes_id else "promotion"
                if confirmation is None or confirmation.action != required_action or confirmation.target_item_id != parsed.id:
                    raise OperationalContextError("persisted item confirmation is invalid.")
                cls._assert_evidence_matches_provenance(parsed, confirmation)
        except (TypeError, OperationalContextError) as exc:
            raise OperationalContextError("operational context partition is invalid.") from exc
        return payload

    @staticmethod
    def _write(path: Path, state: dict[str, Any]) -> None:
        atomic_write_text(path, json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def _item_dict(item: OperationalItem) -> dict[str, Any]:
    payload = asdict(item)
    payload["provenance"] = asdict(item.provenance) if item.provenance else {}
    return payload
