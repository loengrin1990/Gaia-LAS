from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import SETTINGS
from .context_assembler import ContextReader
from .context_compiler import ContextService
from .context_search import MAX_QUERY_LENGTH, MAX_TERMS
from .controlled_intake import ControlledIntake
from .local_llm import run_lm_studio
from .masking import mask_with_review
from .models import Conversation, ConversationMessage
from .orchestrator import create_package
from .operational_context import KIND_REGISTRY, OperationalContextStore
from .operational_context_assembler import (
    HandledMemorySelection,
    OperationalContextPackageBudget,
    SessionContextItem,
    compose_operational_context_package,
    new_free_form_text,
    trusted_system_text,
)
from .operational_context_retrieval import OperationalContextReader, RetrievalRequest, TrustedLocalProcessingPolicy
from .operational_context_runtime import run_operational_context_dialogue
from .projects import project_names
from .provenance import ProvenanceStore
from .storage import atomic_write_text, path_lock


MAX_RECENT_MESSAGES = 8
SUMMARY_CHARS = 2400
DIALOGUE_QUERY_FILLER = {
    "а", "и", "или", "какой", "какая", "какие", "как", "что", "сейчас",
    "его", "ее", "их", "это", "этот", "эта", "эти", "ли", "пожалуйста",
    "мы", "обсуждаем", "говорим", "речь", "we", "are", "discussing", "and", "what", "is", "its",
}


class ConversationError(ValueError):
    pass


def local_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def conversations_root() -> Path:
    if SETTINGS is None:
        raise RuntimeError("Gaia settings are unavailable.")
    return SETTINGS.service_docs / "Диалоги"


def project_conversation_dir(project: str) -> Path:
    if not valid_project(project):
        raise ConversationError("Некорректный проект для диалога.")
    path = conversations_root() / safe_slug(project)
    path.mkdir(parents=True, exist_ok=True)
    return path


def valid_project(project: str) -> bool:
    return bool(project and "/" not in project and "\\" not in project)


def list_conversations(project: str) -> list[Conversation]:
    directory = project_conversation_dir(project)
    conversations = []
    for path in sorted(directory.glob("*.json")):
        try:
            conversation = read_conversation(path)
        except Exception:
            continue
        if conversation.status != "archived":
            conversations.append(conversation)
    return sorted(conversations, key=lambda item: item.updated_at, reverse=True)


def create_conversation(project: str, title: str = "") -> Conversation:
    if project and project_names() and project not in project_names():
        raise ConversationError(f"Проект `{project}` не найден.")
    now = local_now()
    conversation = Conversation(
        id=uuid.uuid4().hex[:12],
        project=project,
        title=title.strip()[:100] or "Новый диалог",
        status="active",
        created_at=now,
        updated_at=now,
        rolling_summary="",
        messages=[],
    )
    write_conversation(conversation)
    return conversation


def get_conversation(conversation_id: str) -> Conversation:
    for path in conversations_root().glob("*/*.json"):
        if path.stem == conversation_id:
            return read_conversation(path)
    raise ConversationError("Диалог не найден.")


def archive_conversation(conversation_id: str) -> Conversation:
    conversation = get_conversation(conversation_id)
    with path_lock(conversation_path(conversation)):
        conversation = get_conversation(conversation_id)
        conversation.status = "archived"
        conversation.updated_at = local_now()
        write_conversation(conversation)
        return conversation


def add_user_turn(
    conversation_id: str,
    text: str,
    uploaded: list[tuple[str, bytes]] | None = None,
    profile_id: str | None = None,
    run_local: bool = False,
) -> dict[str, Any]:
    conversation = get_conversation(conversation_id)
    with path_lock(conversation_path(conversation)):
        return _add_user_turn_locked(conversation_id, text, uploaded, profile_id, run_local)


def _add_user_turn_locked(
    conversation_id: str,
    text: str,
    uploaded: list[tuple[str, bytes]] | None = None,
    profile_id: str | None = None,
    run_local: bool = False,
) -> dict[str, Any]:
    conversation = get_conversation(conversation_id)
    query = text.strip()
    if not query and not uploaded:
        raise ConversationError("Добавь сообщение или файл для продолжения диалога.")
    context_query = build_contextual_query(conversation, query)
    user_mask = mask_with_review("Диалог: сообщение пользователя", query, strict_dialog_privacy=True)
    safe_user_text = user_mask.masked_text.strip() or "Очищенное сообщение не содержит сохраняемого текста."
    context_search_query = build_context_search_query(conversation, safe_user_text)
    package = create_package(
        conversation.project,
        context_query,
        uploaded or [],
        profile_id,
        strict_dialog_privacy=True,
        dialogue_context_reader=existing_dialogue_context_reader(conversation.project),
        dialogue_context_query=context_search_query,
    )
    user_message = ConversationMessage(
        id=uuid.uuid4().hex[:12],
        role="user",
        text="",
        masked_text=safe_user_text,
        created_at=local_now(),
        job_id=package.run_id,
        route=package.route,
        safety_status=user_mask.review.status,
    )
    conversation.messages.append(user_message)
    conversation.last_job_id = package.run_id

    local_result: dict[str, Any] | None = None
    if run_local:
        runtime_package = build_operational_context_runtime_package(conversation, query, package)
        if runtime_package is None:
            local_result = run_lm_studio(package.prompt)
        else:
            local_result = asdict(run_operational_context_dialogue(runtime_package))
        answer = str(local_result.get("answer") or local_result.get("error") or "")
        if answer:
            answer_mask = mask_with_review("Диалог: ответ локальной модели", answer, strict_dialog_privacy=True)
            conversation.messages.append(ConversationMessage(
                id=uuid.uuid4().hex[:12],
                role="assistant",
                text="",
                masked_text=answer_mask.masked_text,
                created_at=local_now(),
                job_id=package.run_id,
                route="local",
                safety_status=str(local_result.get("status") or ("ok" if local_result.get("ok") else "failed")),
                structured_answer=local_result.get("structured_answer") if isinstance(local_result.get("structured_answer"), dict) else None,
            ))

    conversation.rolling_summary = update_summary(conversation)
    if conversation.title == "Новый диалог" and query:
        conversation.title = safe_user_text[:80]
    conversation.updated_at = local_now()
    write_conversation(conversation)
    return {
        "conversation": asdict(conversation),
        "package": asdict(package),
        "local_result": local_result,
    }


def build_operational_context_runtime_package(
    conversation: Conversation,
    query: str,
    analysis_package: Any,
):
    """Bridge the real dialogue turn to OC-2/OC-3 without migrating legacy state."""
    if SETTINGS is None or not getattr(SETTINGS, "storage_dir", None) or not conversation.project.strip():
        return None
    intake = ControlledIntake(ProvenanceStore(SETTINGS.storage_dir), create_metadata=False)
    workspace = intake.existing_workspace(conversation.project)
    if not workspace:
        return None
    retrieval = OperationalContextReader(OperationalContextStore(intake.store.root)).retrieve(RetrievalRequest(
        user_ref="local_user", project_ref=workspace, system_ref="gaia_local_runtime",
        supported_kinds=frozenset(KIND_REGISTRY),
        trusted_local_policy=TrustedLocalProcessingPolicy(frozenset(("standard", "restricted", "unknown"))),
        max_items=100, max_chars=60_000,
    ))
    dialogue_context = getattr(analysis_package, "dialogue_context", None)
    selection = getattr(dialogue_context, "memory_selection", None)
    memory = HandledMemorySelection.legacy(selection) if selection is not None else None
    session = tuple(
        SessionContextItem(message.masked_text or message.text, "unknown")
        for message in conversation.messages[-MAX_RECENT_MESSAGES:]
        if (message.masked_text or message.text).strip()
    )
    return compose_operational_context_package(
        query=new_free_form_text(query), task=trusted_system_text("pb0_response_format_v1"),
        retrieval_result=retrieval, memory_selection=memory, session_context=session,
        budget=OperationalContextPackageBudget(65_536),
    )


def existing_dialogue_context_reader(project: str) -> ContextReader | None:
    """Return a reader for an existing project workspace without creating one."""
    if SETTINGS is None:
        raise RuntimeError("Gaia settings are unavailable.")
    root = getattr(SETTINGS, "storage_dir", None)
    if root is None:
        return None
    required = [root / zone for zone in ("sources", "artifacts", "sanitized", "context", "pseudonyms", "exports", "metadata")]
    if not project.strip() or not (root / "metadata" / "registry.json").is_file() or not all(path.is_dir() for path in required):
        return None
    intake = ControlledIntake(ProvenanceStore(root), create_metadata=False)
    workspace_id = intake.existing_workspace(project)
    return ContextService(intake.store, workspace_id) if workspace_id else None


def build_contextual_query(conversation: Conversation, query: str) -> str:
    parts = []
    if conversation.rolling_summary:
        parts.extend(["# Summary предыдущего диалога", conversation.rolling_summary, ""])
    recent = conversation.messages[-MAX_RECENT_MESSAGES:]
    if recent:
        parts.append("# Последние сообщения диалога")
        for message in recent:
            parts.append(f"{message.role}: {message.masked_text or message.text}")
        parts.append("")
    parts.extend(["# Новое сообщение пользователя", query or ""])
    return "\n".join(parts)


def build_context_search_query(conversation: Conversation, current_message: str, max_length: int = MAX_QUERY_LENGTH) -> str:
    """Project Dialogue intent into the bounded Context Search contract.

    Lore keeps the complete contextual query.  Context Search receives factual
    terms from the current message first, plus only the latest user turn when a
    short follow-up needs its subject to be recoverable.
    """
    if max_length < 1:
        raise ValueError("Лимит поискового запроса должен быть положительным.")
    current_terms = _context_search_terms(current_message)
    prior_terms = _context_search_terms(_latest_user_message(conversation))
    return _bounded_terms(current_terms, prior_terms, max_length)


def _latest_user_message(conversation: Conversation) -> str:
    for message in reversed(conversation.messages):
        if message.role == "user" and (message.masked_text or message.text).strip():
            return message.masked_text or message.text
    return ""


def _context_search_terms(value: str) -> list[str]:
    return [
        token for token in re.findall(r"[A-Za-zА-Яа-яЁё0-9_]+", value.casefold())
        if token not in DIALOGUE_QUERY_FILLER
    ]


def _bounded_terms(primary: list[str], secondary: list[str], max_length: int) -> str:
    selected: list[str] = []
    for terms in (primary, secondary):
        for token in terms:
            if len(selected) >= MAX_TERMS:
                return " ".join(selected)
            if not selected and len(token) > max_length:
                return token[:max_length]
            candidate = " ".join([*selected, token])
            if len(candidate) <= max_length:
                selected.append(token)
    return " ".join(selected)


def update_summary(conversation: Conversation) -> str:
    messages = conversation.messages[-20:]
    lines = []
    for message in messages:
        text = " ".join((message.masked_text or message.text).split())
        if not text:
            continue
        lines.append(f"- {message.role}: {text[:260]}")
    summary = "\n".join(lines)
    if len(summary) > SUMMARY_CHARS:
        summary = summary[-SUMMARY_CHARS:]
    return summary


def conversation_path(conversation: Conversation) -> Path:
    return project_conversation_dir(conversation.project) / f"{conversation.id}.json"


def write_conversation(conversation: Conversation) -> None:
    path = conversation_path(conversation)
    atomic_write_text(path, json.dumps(asdict(conversation), ensure_ascii=False, indent=2) + "\n")


def read_conversation(path: Path) -> Conversation:
    payload = json.loads(path.read_text(encoding="utf-8"))
    messages = [
        ConversationMessage(
            id=str(item.get("id") or ""),
            role=str(item.get("role") or ""),
            text=str(item.get("text") or ""),
            masked_text=str(item.get("masked_text") or ""),
            created_at=str(item.get("created_at") or ""),
            job_id=str(item.get("job_id") or ""),
            route=str(item.get("route") or ""),
            safety_status=str(item.get("safety_status") or ""),
            structured_answer=item.get("structured_answer") if isinstance(item.get("structured_answer"), dict) else None,
        )
        for item in payload.get("messages") or []
        if isinstance(item, dict)
    ]
    return Conversation(
        id=str(payload.get("id") or path.stem),
        project=str(payload.get("project") or path.parent.name),
        title=str(payload.get("title") or "Диалог"),
        status=str(payload.get("status") or "active"),
        created_at=str(payload.get("created_at") or ""),
        updated_at=str(payload.get("updated_at") or ""),
        rolling_summary=str(payload.get("rolling_summary") or ""),
        messages=messages,
        last_job_id=str(payload.get("last_job_id") or ""),
    )


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^0-9A-Za-zА-Яа-яЁё_-]+", "-", value.strip())
    slug = "-".join(part for part in slug.split("-") if part)
    return slug[:90] or "project"
