"""Feature-flagged answer selection for the existing Dialogue lifecycle."""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import re
from typing import Any, Callable, Protocol

from .config import SETTINGS
from .controlled_intake import ControlledIntake
from .hybrid_answer_service import HybridAnswerService
from .local_llm import run_lm_studio
from .models import Conversation
from .operational_context_runtime import run_operational_context_dialogue


_FOLLOW_UP = re.compile(r"^(?:а|и)\s+(?:для|у|по|что\s+насч[её]т)\b", re.IGNORECASE)
_MAX_REFERENT_HINT_CHARS = 320
WORKSPACE_RESOLUTION_FAILED = "WORKSPACE_RESOLUTION_FAILED"
# The current intake mapping is one project key to one workspace.  Keep this
# status reserved so a future multi-value mapping can fail closed explicitly.
WORKSPACE_RESOLUTION_AMBIGUOUS = "WORKSPACE_RESOLUTION_AMBIGUOUS"


class _ReadOnlyStoreRoot(Protocol):
    root: Path


class _ReadOnlyStore:
    """Minimal storage view accepted by ControlledIntake's read-only lookup."""

    def __init__(self, root: Path) -> None:
        self.root = root


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


def build_hybrid_referent_hint(conversation: Conversation, current_masked_text: str) -> str:
    """Return one bounded prior user turn only for a clear short follow-up."""
    current = current_masked_text.strip()
    if len(current.split()) > 6 or not _FOLLOW_UP.match(current):
        return ""
    messages = conversation.messages
    if messages and messages[-1].role == "user" and messages[-1].masked_text == current:
        messages = messages[:-1]
    for message in reversed(messages):
        if message.role == "user" and message.masked_text.strip():
            return message.masked_text.strip()[:_MAX_REFERENT_HINT_CHARS]
    return ""


class DialogueAnswerDispatcher:
    """Choose one answer path; conversation persistence remains with the caller."""

    def __init__(
        self,
        hybrid_service_factory: Callable[[Any], HybridAnswerService] = HybridAnswerService,
        workspace_resolver: Callable[[Path, str], tuple[str, str]] = resolve_existing_dialogue_workspace,
    ) -> None:
        self._hybrid_service_factory = hybrid_service_factory
        self._workspace_resolver = workspace_resolver

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
        return self._hybrid(conversation, safe_user_query)

    @staticmethod
    def _read_path() -> str:
        return getattr(SETTINGS, "dialogue_read_path", "legacy") if SETTINGS is not None else "legacy"

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

    def _hybrid(self, conversation: Conversation, safe_user_query: str) -> dict[str, Any]:
        storage_root = getattr(SETTINGS, "storage_dir", None)
        if storage_root is None:
            return self._failure("INTERNAL_VERIFICATION_FAILURE")
        resolution_status, workspace_id = self._workspace_resolver(storage_root, conversation.project)
        if resolution_status != "SUCCESS":
            return self._failure(resolution_status)
        try:
            result = self._hybrid_service_factory(storage_root).answer(
                safe_user_query,
                workspace_id,
                referent_hint=build_hybrid_referent_hint(conversation, safe_user_query),
            )
        except Exception:
            return self._failure("INTERNAL_VERIFICATION_FAILURE")
        if result.status != "SUCCESS" or not result.answer.strip():
            return self._failure(result.status)
        return {"ok": True, "answer": result.answer, "status": "ok", "read_path": "hybrid"}

    @staticmethod
    def _failure(status: str) -> dict[str, Any]:
        if status == "INSUFFICIENT_EVIDENCE":
            message = "В доступных подтверждённых данных недостаточно сведений для ответа."
        elif status in {"LOCAL_PROVIDER_UNAVAILABLE", "LOCAL_REASONING_ROUTE_MISSING", "PROVIDER_INCOMPLETE_RESPONSE"}:
            message = "Локальный модуль подготовки ответа не вернул полный результат."
        else:
            message = "Не удалось безопасно проверить подтверждённые источники для ответа."
        return {"ok": False, "answer": message, "error": message, "status": "failed", "failure_kind": status, "read_path": "hybrid"}


def dispatch_dialogue_answer(**kwargs: Any) -> dict[str, Any] | None:
    return DialogueAnswerDispatcher().dispatch(**kwargs)
