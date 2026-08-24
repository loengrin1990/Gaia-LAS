"""Production-local orchestration of the proven D3 hybrid read path."""
from __future__ import annotations

import hashlib
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .hybrid_read_path import DeterministicLexicalSourceRetriever, OperationalOverlayHit, OperationalOverlaySelector, SourceEvidenceHit, SourceRetrievalRequest, assemble_hybrid_package, select_applicable_current_authority
from .hybrid_reasoning import LOCAL_REASONING, USER_FACING_PROTOCOL_LEAKAGE, run_hybrid_reasoning, serialize_reasoning_context
from .operational_context import KIND_REGISTRY, OperationalContextStore
from .operational_context_retrieval import OperationalContextReader, RetrievalRequest, TrustedLocalProcessingPolicy
from .source_evidence import MaterialAvailability, SourceEvidenceError, SourceEvidenceReader


@dataclass(frozen=True)
class HybridAnswerResult:
    status: str
    answer: str = ""
    read_path: str = "hybrid"
    capability: str = LOCAL_REASONING
    provider: str = ""
    model: str = ""
    done_reason: str = ""
    elapsed_seconds: float | None = None
    used_facts_raw: tuple[str, ...] = ()
    used_facts_normalized: tuple[str, ...] = ()
    resolved_provenance: tuple[dict[str, Any], ...] = ()
    package_digest: str = ""
    tensions: tuple[dict[str, str], ...] = ()
    unresolved: tuple[str, ...] = ()
    diagnostics: dict[str, Any] | None = None
    evidence_found: bool = False
    oc_applied: bool = False
    provenance_complete: bool = False

    def carry_forward_metadata(self) -> dict[str, Any]:
        # Do not relay rehydrated layers: carry-forward is one bounded hop from
        # newly accepted current evidence, not a growing conversation memory.
        source_refs = [
            {key: item[key] for key in ("source_id", "sanitized_id", "start_offset", "end_offset")}
            for item in self.resolved_provenance
            if item.get("evidence_layer") == "source_evidence"
        ]
        oc_refs = [
            {"id": item["id"], "source_ref": str(item.get("provenance", {}).get("source_ref") or "")}
            for item in self.resolved_provenance
            if item.get("evidence_layer") in {"oc_overlay", "applicable_current_authority"} and item.get("id")
        ]
        return {"version": 1, "successful": self.status == "SUCCESS", "source_refs": source_refs, "oc_refs": oc_refs}


def normalize_used_fact(value: str) -> str:
    return value.strip().rstrip(string.punctuation + "…»”）】")


class HybridAnswerService:
    """Read-only service. Conversation referent selection remains a caller concern."""
    def __init__(self, storage_root: Path, *, reasoner: Callable[[dict[str, Any]], dict[str, Any]] = run_hybrid_reasoning) -> None:
        self.storage_root = Path(storage_root); self.reasoner = reasoner

    def answer(self, query: str, workspace_id: str, *, prior_user_turns: tuple[str, ...] = (), prior_grounding: dict[str, Any] | None = None) -> HybridAnswerResult:
        retrieval_query = query.strip()
        if not retrieval_query or not workspace_id:
            return HybridAnswerResult("PACKAGE_BUILD_FAILURE")
        try:
            materials = SourceEvidenceReader(self.storage_root, workspace_id).inventory()
        except SourceEvidenceError as exc:
            status = "SOURCE_REGISTRY_UNAVAILABLE" if "registry" in str(exc).lower() else "SOURCE_EVIDENCE_UNAVAILABLE"
            return HybridAnswerResult(status, diagnostics={"detail": str(exc)})
        try:
            source = DeterministicLexicalSourceRetriever().retrieve(SourceRetrievalRequest(retrieval_query, workspace_id), materials)
            rehydrated = self._rehydrate_sources(prior_grounding or {}, materials, workspace_id)
            oc_diagnostics: dict[str, Any] = {}
            try:
                eligible = OperationalContextReader(OperationalContextStore(self.storage_root)).retrieve(RetrievalRequest(
                    user_ref="local_user", project_ref=workspace_id, system_ref="gaia_local_runtime", supported_kinds=frozenset(KIND_REGISTRY), trusted_local_policy=TrustedLocalProcessingPolicy(frozenset(("standard", "restricted", "unknown"))), max_items=100, max_chars=60_000,
                )).eligible_items
                overlay = OperationalOverlaySelector().select(retrieval_query, eligible)
                overlay = self._source_backed_overlay(overlay, materials)
                authority = select_applicable_current_authority(retrieval_query, eligible)
                authority = self._source_backed_overlay(authority, materials)
                authority_ids = {str(hit.item.get("id") or "") for hit in authority}
                overlay = tuple(hit for hit in overlay if str(hit.item.get("id") or "") not in authority_ids)
                rehydrated_oc = self._rehydrate_oc(prior_grounding or {}, eligible, materials)
            except Exception as exc:
                overlay = (); authority = (); rehydrated_oc = (); oc_diagnostics = {"oc_status": "unavailable", "oc_detail": str(exc)}
            package = assemble_hybrid_package(query.strip(), source, overlay, authority, rehydrated, rehydrated_oc).as_dict()
            package["untrusted_dialogue_context"] = list(prior_user_turns[-2:])
            package["diagnostics"]["oc_runtime"] = oc_diagnostics
        except Exception as exc:
            return HybridAnswerResult("PACKAGE_BUILD_FAILURE", diagnostics={"detail": str(exc)})
        evidence_found = bool(package["source_evidence"] or package["rehydrated_grounding"])
        grounding_status = self._grounding_status(package)
        if grounding_status == "reset":
            package["rehydrated_grounding"] = []
            package["rehydrated_oc_grounding"] = []
            package["diagnostics"]["grounding_resolution"] = "reset"
        elif grounding_status == "ambiguous":
            package["diagnostics"]["grounding_resolution"] = "ambiguous"
            return HybridAnswerResult("GROUNDING_AMBIGUOUS", package_digest=hashlib.sha256(serialize_reasoning_context(package).encode()).hexdigest(), unresolved=tuple(package["unresolved"]), diagnostics=package["diagnostics"], evidence_found=evidence_found)
        else:
            package["diagnostics"]["grounding_resolution"] = "resolved" if package["rehydrated_grounding"] or package["rehydrated_oc_grounding"] else "none"
        digest = hashlib.sha256(serialize_reasoning_context(package).encode()).hexdigest()
        if not package["source_evidence"] and not package["rehydrated_grounding"] and not package["rehydrated_oc_grounding"] and not package["oc_overlay"] and not package["applicable_current_authority"]:
            return HybridAnswerResult("INSUFFICIENT_EVIDENCE", package_digest=digest, unresolved=tuple(package["unresolved"]), diagnostics=package["diagnostics"], evidence_found=evidence_found)
        try:
            result = self.reasoner(package)
        except Exception as exc:
            return HybridAnswerResult(
                "LOCAL_PROVIDER_UNAVAILABLE",
                package_digest=digest,
                tensions=tuple(package["observed_tensions"]),
                unresolved=tuple(package["unresolved"]),
                diagnostics={"detail": str(exc), **package["diagnostics"]},
                evidence_found=evidence_found,
            )
        status = self._reasoning_status(result)
        if status != "SUCCESS":
            return HybridAnswerResult(status, provider=str(result.get("provider") or ""), model=str(result.get("model") or ""), done_reason=str(result.get("done_reason") or ""), elapsed_seconds=result.get("elapsed_seconds"), package_digest=digest, tensions=tuple(package["observed_tensions"]), unresolved=tuple(package["unresolved"]), diagnostics={"error": result.get("error"), "detail": result.get("detail"), **package["diagnostics"]}, evidence_found=evidence_found)
        raw = tuple(str(x) for x in result.get("used_facts") or ())
        normalized = tuple(normalize_used_fact(x) for x in raw)
        if not normalized or any(not x for x in normalized):
            return HybridAnswerResult("USED_FACTS_MALFORMED", package_digest=digest, diagnostics=package["diagnostics"], evidence_found=evidence_found)
        provenance = self._provenance(normalized, package)
        if provenance is None:
            return HybridAnswerResult("PROVENANCE_UNRESOLVED", package_digest=digest, diagnostics=package["diagnostics"], evidence_found=evidence_found)
        if package["applicable_current_authority"] and not any(item["evidence_layer"] == "applicable_current_authority" for item in provenance):
            return HybridAnswerResult("CURRENT_AUTHORITY_NOT_USED", package_digest=digest, diagnostics=package["diagnostics"], evidence_found=evidence_found)
        oc_applied = any(item["evidence_layer"] in {"oc_overlay", "applicable_current_authority"} for item in provenance)
        return HybridAnswerResult("SUCCESS", str(result.get("answer") or ""), provider=str(result.get("provider") or ""), model=str(result.get("model") or ""), done_reason=str(result.get("done_reason") or ""), elapsed_seconds=result.get("elapsed_seconds"), used_facts_raw=raw, used_facts_normalized=normalized, resolved_provenance=provenance, package_digest=digest, tensions=tuple(package["observed_tensions"]), unresolved=tuple(package["unresolved"]), diagnostics=package["diagnostics"], evidence_found=evidence_found, oc_applied=oc_applied, provenance_complete=True)

    @staticmethod
    def _grounding_status(package: dict[str, Any]) -> str:
        """Use current evidence first; fail closed when prior candidates tie without it."""
        current_sources = {str(item.get("source_id") or "") for item in package.get("source_evidence", [])}
        carried_sources = {str(item.get("source_id") or "") for item in package.get("rehydrated_grounding", [])}
        if current_sources and carried_sources and current_sources.isdisjoint(carried_sources):
            return "reset"
        if not current_sources and len(carried_sources) > 1:
            return "ambiguous"
        return "resolved" if carried_sources or package.get("rehydrated_oc_grounding") else "none"

    @staticmethod
    def _rehydrate_sources(grounding: dict[str, Any], materials: tuple[Any, ...], workspace_id: str) -> tuple[SourceEvidenceHit, ...]:
        refs = grounding.get("source_refs") if grounding.get("successful") is True else []
        by_identity = {(material.source_id, material.sanitized_id): material for material in materials if material.state is MaterialAvailability.AVAILABLE}
        selected: list[SourceEvidenceHit] = []
        for ref in refs if isinstance(refs, list) else []:
            if not isinstance(ref, dict):
                continue
            source_id, sanitized_id = str(ref.get("source_id") or ""), str(ref.get("sanitized_id") or "")
            material = by_identity.get((source_id, sanitized_id))
            start, end = ref.get("start_offset"), ref.get("end_offset")
            if material is None or not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start or end > len(material.text):
                continue
            selected.append(SourceEvidenceHit(source_id, sanitized_id, workspace_id, material.text[start:end], start, end, material.created_at, 0.0, {}, "rehydrated_grounding"))
        unique = {(hit.source_id, hit.sanitized_id, hit.start_offset, hit.end_offset): hit for hit in selected}
        return tuple(unique[key] for key in sorted(unique)[:4])

    @staticmethod
    def _rehydrate_oc(grounding: dict[str, Any], eligible: tuple[dict[str, Any], ...], materials: tuple[Any, ...]) -> tuple[OperationalOverlayHit, ...]:
        """Rehydrate accepted OC facts only as ordinary grounding, never authority."""
        if grounding.get("successful") is not True:
            return ()
        current_by_id = {str(item.get("id") or ""): item for item in eligible}
        source_refs = {
            value
            for material in materials
            if material.state is MaterialAvailability.AVAILABLE
            for value in (material.source_id, material.sanitized_id)
        }
        selected: list[OperationalOverlayHit] = []
        for ref in grounding.get("oc_refs") if isinstance(grounding.get("oc_refs"), list) else []:
            if not isinstance(ref, dict):
                continue
            identifier, source_ref = str(ref.get("id") or ""), str(ref.get("source_ref") or "")
            item = current_by_id.get(identifier)
            if item is None or item.get("lifecycle") != "active" or item.get("confirmation") != "confirmed":
                continue
            if not source_ref or str(item.get("provenance", {}).get("source_ref") or "") != source_ref or source_ref not in source_refs:
                continue
            selected.append(OperationalOverlayHit(dict(item), 0.0))
        unique = {str(hit.item.get("id") or ""): hit for hit in selected}
        return tuple(unique[key] for key in sorted(unique)[:4])

    @staticmethod
    def _source_backed_overlay(overlay: tuple[Any, ...], materials: tuple[Any, ...]) -> tuple[Any, ...]:
        """Only expose OC facts whose safe source reference resolves in current evidence."""
        source_refs = {
            value
            for material in materials
            if material.state is MaterialAvailability.AVAILABLE
            for value in (material.source_id, material.sanitized_id)
        }
        return tuple(
            hit for hit in overlay
            if str(hit.item.get("provenance", {}).get("source_ref") or "") in source_refs
        )

    @staticmethod
    def _reasoning_status(result: dict[str, Any]) -> str:
        if result.get("status") == "PROVIDER_INCOMPLETE_RESPONSE": return "PROVIDER_INCOMPLETE_RESPONSE"
        if result.get("status") == USER_FACING_PROTOCOL_LEAKAGE: return USER_FACING_PROTOCOL_LEAKAGE
        if not result.get("ok"):
            detail = str(result.get("detail") or result.get("error") or "")
            return "LOCAL_REASONING_ROUTE_MISSING" if "route" in detail.lower() else "LOCAL_PROVIDER_UNAVAILABLE"
        return "SUCCESS"

    @staticmethod
    def _provenance(ids: tuple[str, ...], package: dict[str, Any]) -> tuple[dict[str, Any], ...] | None:
        lookup: dict[str, dict[str, Any]] = {}
        for item in package["source_evidence"]:
            entry = {"evidence_layer": "source_evidence", "source_id": item["source_id"], "sanitized_id": item["sanitized_id"], "start_offset": item["start_offset"], "end_offset": item["end_offset"], "section": item.get("literal_annotations", {}).get("heading", "")}
            lookup[item["source_id"]] = entry; lookup[item["sanitized_id"]] = entry
        for item in package.get("rehydrated_grounding", []):
            entry = {"evidence_layer": "rehydrated_grounding", "source_id": item["source_id"], "sanitized_id": item["sanitized_id"], "start_offset": item["start_offset"], "end_offset": item["end_offset"], "section": ""}
            lookup.setdefault(item["source_id"], entry); lookup.setdefault(item["sanitized_id"], entry)
        for layer in ("rehydrated_oc_grounding", "oc_overlay", "applicable_current_authority"):
            for hit in package.get(layer, []):
                item = hit["item"]; identifier = str(item.get("id") or "")
                if identifier: lookup[identifier] = {"evidence_layer": layer, "id": identifier, "provenance": item.get("provenance", {})}
        return tuple(lookup[x] for x in ids) if all(x in lookup for x in ids) else None
