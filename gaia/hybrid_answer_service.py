"""Production-local orchestration of the proven D3 hybrid read path."""
from __future__ import annotations

import hashlib
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .hybrid_read_path import DeterministicLexicalSourceRetriever, OperationalOverlaySelector, SourceRetrievalRequest, assemble_hybrid_package
from .hybrid_reasoning import LOCAL_REASONING, run_hybrid_reasoning, serialize_reasoning_context
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


def normalize_used_fact(value: str) -> str:
    return value.strip().rstrip(string.punctuation + "…»”）】")


class HybridAnswerService:
    """Read-only service. Conversation referent selection remains a caller concern."""
    def __init__(self, storage_root: Path, *, reasoner: Callable[[dict[str, Any]], dict[str, Any]] = run_hybrid_reasoning) -> None:
        self.storage_root = Path(storage_root); self.reasoner = reasoner

    def answer(self, query: str, workspace_id: str, *, referent_hint: str = "") -> HybridAnswerResult:
        retrieval_query = "\n".join(part for part in (query.strip(), referent_hint.strip()) if part)
        if not retrieval_query or not workspace_id:
            return HybridAnswerResult("PACKAGE_BUILD_FAILURE")
        try:
            materials = SourceEvidenceReader(self.storage_root, workspace_id).inventory()
        except SourceEvidenceError as exc:
            status = "SOURCE_REGISTRY_UNAVAILABLE" if "registry" in str(exc).lower() else "SOURCE_EVIDENCE_UNAVAILABLE"
            return HybridAnswerResult(status, diagnostics={"detail": str(exc)})
        try:
            source = DeterministicLexicalSourceRetriever().retrieve(SourceRetrievalRequest(retrieval_query, workspace_id), materials)
            oc_diagnostics: dict[str, Any] = {}
            try:
                eligible = OperationalContextReader(OperationalContextStore(self.storage_root)).retrieve(RetrievalRequest(
                    user_ref="local_user", project_ref=workspace_id, system_ref="gaia_local_runtime", supported_kinds=frozenset(KIND_REGISTRY), trusted_local_policy=TrustedLocalProcessingPolicy(frozenset(("standard", "restricted", "unknown"))), max_items=100, max_chars=60_000,
                )).eligible_items
                overlay = OperationalOverlaySelector().select(retrieval_query, eligible)
                overlay = self._source_backed_overlay(overlay, materials)
            except Exception as exc:
                overlay = (); oc_diagnostics = {"oc_status": "unavailable", "oc_detail": str(exc)}
            package = assemble_hybrid_package(query.strip(), source, overlay).as_dict()
            package["diagnostics"]["oc_runtime"] = oc_diagnostics
        except Exception as exc:
            return HybridAnswerResult("PACKAGE_BUILD_FAILURE", diagnostics={"detail": str(exc)})
        digest = hashlib.sha256(serialize_reasoning_context(package).encode()).hexdigest()
        evidence_found = bool(package["source_evidence"])
        if not package["source_evidence"] and not package["oc_overlay"]:
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
        oc_applied = any(item["evidence_layer"] == "oc_overlay" for item in provenance)
        return HybridAnswerResult("SUCCESS", str(result.get("answer") or ""), provider=str(result.get("provider") or ""), model=str(result.get("model") or ""), done_reason=str(result.get("done_reason") or ""), elapsed_seconds=result.get("elapsed_seconds"), used_facts_raw=raw, used_facts_normalized=normalized, resolved_provenance=provenance, package_digest=digest, tensions=tuple(package["observed_tensions"]), unresolved=tuple(package["unresolved"]), diagnostics=package["diagnostics"], evidence_found=evidence_found, oc_applied=oc_applied, provenance_complete=True)

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
        for hit in package["oc_overlay"]:
            item = hit["item"]; identifier = str(item.get("id") or "")
            if identifier: lookup[identifier] = {"evidence_layer": "oc_overlay", "id": identifier, "provenance": item.get("provenance", {})}
        return tuple(lookup[x] for x in ids) if all(x in lookup for x in ids) else None
