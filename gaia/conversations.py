from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import SETTINGS
from .local_llm import run_lm_studio
from .masking import mask_with_review
from .models import Conversation, ConversationMessage
from .orchestrator import create_package
from .projects import project_names


MAX_RECENT_MESSAGES = 8
SUMMARY_CHARS = 2400


class ConversationError(ValueError):
    pass


def utc_now() -> str:
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
    now = utc_now()
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
    conversation.status = "archived"
    conversation.updated_at = utc_now()
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
    query = text.strip()
    if not query and not uploaded:
        raise ConversationError("Добавь сообщение или файл для продолжения диалога.")
    context_query = build_contextual_query(conversation, query)
    package = create_package(
        conversation.project,
        context_query,
        uploaded or [],
        profile_id,
        strict_dialog_privacy=True,
    )
    user_mask = mask_with_review("Диалог: сообщение пользователя", query, strict_dialog_privacy=True)
    user_message = ConversationMessage(
        id=uuid.uuid4().hex[:12],
        role="user",
        text=query,
        masked_text=user_mask.masked_text,
        created_at=utc_now(),
        job_id=package.run_id,
        route=package.route,
        safety_status=user_mask.review.status,
    )
    conversation.messages.append(user_message)
    conversation.last_job_id = package.run_id

    local_result: dict[str, Any] | None = None
    if run_local:
        local_result = run_lm_studio(package.prompt)
        answer = str(local_result.get("answer") or local_result.get("error") or "")
        if answer:
            conversation.messages.append(ConversationMessage(
                id=uuid.uuid4().hex[:12],
                role="assistant",
                text=answer,
                masked_text=answer,
                created_at=utc_now(),
                job_id=package.run_id,
                route="local",
                safety_status=str(local_result.get("status") or ("ok" if local_result.get("ok") else "failed")),
            ))

    conversation.rolling_summary = update_summary(conversation)
    if conversation.title == "Новый диалог" and query:
        conversation.title = query[:80]
    conversation.updated_at = utc_now()
    write_conversation(conversation)
    return {
        "conversation": asdict(conversation),
        "package": asdict(package),
        "local_result": local_result,
    }


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
    path.write_text(json.dumps(asdict(conversation), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
