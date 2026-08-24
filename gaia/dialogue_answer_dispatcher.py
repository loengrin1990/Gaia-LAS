"""Feature-flagged answer selection for the existing Dialogue lifecycle."""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import re
import time
from typing import Any, Callable, Protocol
import uuid

from .config import SETTINGS
from .controlled_intake import ControlledIntake
from .hybrid_answer_service import HybridAnswerService
from .local_llm import run_lm_studio
from .models import Conversation
from .operational_context_runtime import run_operational_context_dialogue
from .runtime_diagnostics import emit_dialogue_observation


WORKSPACE_RESOLUTION_FAILED = "WORKSPACE_RESOLUTION_FAILED"
# The current intake mapping is one project key to one workspace.  Keep this
# status reserved so a future multi-value mapping can fail closed explicitly.
WORKSPACE_RESOLUTION_AMBIGUOUS = "WORKSPACE_RESOLUTION_AMBIGUOUS"
_OBSERVATION_FAILURE_REASONS = {
    "INSUFFICIENT_EVIDENCE": "insufficient_evidence",
    "LOCAL_PROVIDER_UNAVAILABLE": "provider_unavailable",
    "LOCAL_REASONING_ROUTE_MISSING": "local_reasoning_route_missing",
    "PROVIDER_INCOMPLETE_RESPONSE": "provider_incomplete_response",
    "PROVENANCE_UNRESOLVED": "provenance_unresolved",
    "USED_FACTS_MALFORMED": "used_facts_malformed",
    "CURRENT_AUTHORITY_NOT_USED": "current_authority_not_used",
    "USER_FACING_PROTOCOL_LEAKAGE": "user_facing_protocol_leakage",
    "SOURCE_REGISTRY_UNAVAILABLE": "source_registry_unavailable",
    "SOURCE_EVIDENCE_UNAVAILABLE": "source_evidence_unavailable",
    "PACKAGE_BUILD_FAILURE": "package_build_failure",
    WORKSPACE_RESOLUTION_FAILED: "workspace_resolution_failed",
    WORKSPACE_RESOLUTION_AMBIGUOUS: "workspace_resolution_ambiguous",
    "INTERNAL_VERIFICATION_FAILURE": "internal_verification_failure",
}


class _ReadOnlyStoreRoot(Protocol):
    root: Path


class _ReadOnlyStore:
    """Minimal storage view accepted by ControlledIntake's read-only lookup."""

    def __init__(self, root: Path) -> None:
        self.root = root


class _HybridDispatchOutcome:
    def __init__(
        self,
        response: dict[str, Any],
        *,
        hybrid_invoked: bool = False,
        hybrid_completed: bool = False,
        evidence_found: bool = False,
        oc_applied: bool = False,
        provenance_complete: bool = False,
        failure_status: str = "",
    ) -> None:
        self.response = response
        self.hybrid_invoked = hybrid_invoked
        self.hybrid_completed = hybrid_completed
        self.evidence_found = evidence_found
        self.oc_applied = oc_applied
        self.provenance_complete = provenance_complete
        self.failure_status = failure_status


def resolve_existing_dialogue_workspace(storage_root: Path, project: str) -> tuple[str, str]:
    """Resolve a project through existing intake metadata without creating state."""
    root = Path(storage_root)
    try:
        # existing_workspace() needs only the root for its read-only mapping
        # lookup.  Do not construct ProvenanceStore here: its initializer may
        # create missing storage directories or a registry.
        read_only_store: _ReadOnlyStoreRoot = _ReadOnlyStore(root)
        workspace_id = ControlledIntake(read_only_store, create_metadata=False).existing_workspace(project)
        if not workspace_id:
            return WORKSPACE_RESOLUTION_FAILED, ""
        registry = json.loads((root / "metadata" / "registry.json").read_text(encoding="utf-8"))
        if workspace_id not in registry.get("workspaces", {}):
            return WORKSPACE_RESOLUTION_FAILED, ""
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return WORKSPACE_RESOLUTION_FAILED, ""
    return "SUCCESS", workspace_id


def build_hybrid_grounding(conversation: Conversation, current_masked_text: str) -> dict[str, Any]:
    """Bounded discourse plus only machine provenance from the prior Hybrid success."""
    messages = conversation.messages[:-1] if conversation.messages and conversation.messages[-1].role == "user" and conversation.messages[-1].masked_text == current_masked_text else conversation.messages
    prior_users = [message.masked_text.strip()[:320] for message in messages if message.role == "user" and message.masked_text.strip()][-2:]
    # Carry-forward is deliberately one hop: an earlier assistant turn cannot
    # become grounding merely because a later turn did not produce Hybrid output.
    prior_assistant = messages[-1] if messages and messages[-1].role == "assistant" else None
    return {"prior_user_turns": tuple(prior_users), "prior_grounding": dict(prior_assistant.hybrid_grounding) if prior_assistant and isinstance(prior_assistant.hybrid_grounding, dict) else {}}


class DialogueAnswerDispatcher:
    """Choose one answer path; conversation persistence remains with the caller."""

    def __init__(
        self,
        hybrid_service_factory: Callable[[Any], HybridAnswerService] = HybridAnswerService,
        workspace_resolver: Callable[[Path, str], tuple[str, str]] = resolve_existing_dialogue_workspace,
        observation_emitter: Callable[..., None] = emit_dialogue_observation,
    ) -> None:
        self._hybrid_service_factory = hybrid_service_factory
        self._workspace_resolver = workspace_resolver
        self._observation_emitter = observation_emitter

    def dispatch(
        self,
        *,
        run_local: bool,
        conversation: Conversation,
        query: str,
        safe_user_query: str,
        analysis_package: Any,
        build_legacy_runtime: Callable[[Conversation, str, Any], Any],
        legacy_plain_answer: Callable[[str], dict[str, Any]] = run_lm_studio,
        legacy_operational_answer: Callable[[Any], Any] = run_operational_context_dialogue,
    ) -> dict[str, Any] | None:
        if not run_local:
            return None
        if self._read_path() == "legacy":
            return self._legacy(conversation, query, analysis_package, build_legacy_runtime, legacy_plain_answer, legacy_operational_answer)
        started = time.monotonic()
        outcome = self._hybrid(conversation, safe_user_query)
        self._observe(outcome, elapsed_ms=round((time.monotonic() - started) * 1000))
        return outcome.response

    @staticmethod
    def _read_path() -> str:
        return getattr(SETTINGS, "dialogue_read_path", "hybrid") if SETTINGS is not None else "hybrid"

    @staticmethod
    def _legacy(
        conversation: Conversation,
        query: str,
        analysis_package: Any,
        build_legacy_runtime: Callable[[Conversation, str, Any], Any],
        legacy_plain_answer: Callable[[str], dict[str, Any]],
        legacy_operational_answer: Callable[[Any], Any],
    ) -> dict[str, Any]:
        runtime_package = build_legacy_runtime(conversation, query, analysis_package)
        if runtime_package is None:
            result = legacy_plain_answer(analysis_package.prompt)
        else:
            result = asdict(legacy_operational_answer(runtime_package))
        return {**result, "read_path": "legacy"}

    def _hybrid(self, conversation: Conversation, safe_user_query: str) -> _HybridDispatchOutcome:
        storage_root = getattr(SETTINGS, "storage_dir", None)
        if storage_root is None:
            return _HybridDispatchOutcome(self._failure("INTERNAL_VERIFICATION_FAILURE"), failure_status="INTERNAL_VERIFICATION_FAILURE")
        resolution_status, workspace_id = self._workspace_resolver(storage_root, conversation.project)
        if resolution_status != "SUCCESS":
            return _HybridDispatchOutcome(self._failure(resolution_status), failure_status=resolution_status)
        try:
            grounding = build_hybrid_grounding(conversation, safe_user_query)
            result = self._hybrid_service_factory(storage_root).answer(safe_user_query, workspace_id, **grounding)
        except Exception:
            return _HybridDispatchOutcome(self._failure("INTERNAL_VERIFICATION_FAILURE"), hybrid_invoked=True, failure_status="INTERNAL_VERIFICATION_FAILURE")
        if result.status != "SUCCESS" or not result.answer.strip():
            return _HybridDispatchOutcome(
                self._failure(result.status),
                hybrid_invoked=True,
                hybrid_completed=True,
                evidence_found=bool(getattr(result, "evidence_found", False)),
                oc_applied=bool(getattr(result, "oc_applied", False)),
                provenance_complete=bool(getattr(result, "provenance_complete", False)),
                failure_status=result.status,
            )
        return _HybridDispatchOutcome(
            {"ok": True, "answer": result.answer, "status": "ok", "read_path": "hybrid", "hybrid_grounding": result.carry_forward_metadata() if callable(getattr(result, "carry_forward_metadata", None)) else {}},
            hybrid_invoked=True,
            hybrid_completed=True,
            evidence_found=bool(getattr(result, "evidence_found", False)),
            oc_applied=bool(getattr(result, "oc_applied", False)),
            provenance_complete=bool(getattr(result, "provenance_complete", False)),
        )

    def _observe(self, outcome: _HybridDispatchOutcome, *, elapsed_ms: int) -> None:
        settings = SETTINGS
        enabled = bool(getattr(settings, "dialogue_observation_enabled", False))
        service_docs = getattr(settings, "service_docs", None)
        if not enabled or service_docs is None:
            return
        observation = {
            "observation_id": f"dialogue-{uuid.uuid4().hex}",
            "dialogue_read_path": "hybrid",
            "hybrid_invoked": outcome.hybrid_invoked,
            "hybrid_completed": outcome.hybrid_completed,
            "evidence_found": outcome.evidence_found,
            "oc_applied": outcome.oc_applied,
            "provenance_complete": outcome.provenance_complete,
            "elapsed_ms": max(0, elapsed_ms),
            "failure_reason": _OBSERVATION_FAILURE_REASONS.get(outcome.failure_status, "internal_verification_failure") if outcome.failure_status else None,
            "eligible_for_adjudication": bool(outcome.response.get("ok")),
        }
        try:
            self._observation_emitter(
                enabled=True,
                path=Path(service_docs) / "dialogue_observation.jsonl",
                observation=observation,
            )
        except Exception:
            # An observation failure is never allowed to alter the Dialogue answer.
            pass

    @staticmethod
    def _failure(status: str) -> dict[str, Any]:
        if status == "INSUFFICIENT_EVIDENCE":
            message = "В доступных подтверждённых данных недостаточно сведений для ответа."
        elif status == "GROUNDING_AMBIGUOUS":
            message = "Уточните, к какому из ранее обсуждавшихся подтверждённых материалов относится вопрос."
        elif status == "CURRENT_AUTHORITY_NOT_USED":
            message = "Не удалось сформировать ответ с опорой на подтверждённое актуальное правило."
        elif status in {"LOCAL_PROVIDER_UNAVAILABLE", "LOCAL_REASONING_ROUTE_MISSING", "PROVIDER_INCOMPLETE_RESPONSE"}:
            message = "Локальный модуль подготовки ответа не вернул полный результат."
        else:
            message = "Не удалось безопасно проверить подтверждённые источники для ответа."
        return {"ok": False, "answer": message, "error": message, "status": "failed", "failure_kind": status, "read_path": "hybrid"}


def dispatch_dialogue_answer(**kwargs: Any) -> dict[str, Any] | None:
    return DialogueAnswerDispatcher().dispatch(**kwargs)
