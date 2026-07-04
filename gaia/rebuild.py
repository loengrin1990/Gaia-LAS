from __future__ import annotations

import uuid
from typing import Any

from .memory import (
    build_evidence_plan,
    clip_section,
    group_memory_sections,
    project_memory_sections,
    query_focus_terms,
    section_identifier,
)
from .projects import group_for_project
from .models import FileArtifact, MemorySource
from .packaging import build_prompt


def rebuild_prompt(package: dict[str, Any], selected_source_ids: list[str]) -> dict[str, Any]:
    original_sources = package.get("memory_sources") or []
    allowed_ids = {str(source.get("id", "")) for source in original_sources if source.get("id")}
    requested_ids = [str(source_id) for source_id in selected_source_ids if str(source_id)]
    unknown_ids = sorted(set(requested_ids) - allowed_ids)
    if unknown_ids:
        raise ValueError("unknown memory source id")
    selected_ids = [source_id for source_id in requested_ids if source_id in allowed_ids]
    source_metadata = {
        str(source.get("id", "")): source
        for source in original_sources
        if source.get("id")
    }
    project = str(package.get("project") or "")
    sections = project_memory_sections(project)
    group = group_for_project(project)
    if group:
        sections = group_memory_sections(group.code, group.title) + sections
    sections_by_id = {section_identifier(section): section for section in sections}

    memory_parts: list[str] = []
    rebuilt_sources: list[MemorySource] = []
    for source_id in selected_ids:
        section = sections_by_id.get(source_id)
        metadata = source_metadata.get(source_id) or {}
        if section is None:
            continue
        memory_parts.append(f"## {section.heading}\n{clip_section(section.text)}")
        rebuilt_sources.append(MemorySource(
            id=source_id,
            project=section.project,
            path=section.path,
            heading=section.heading,
            line_start=section.line_start,
            line_end=section.line_end,
            score=int(metadata.get("score") or 0),
            matched_terms=list(metadata.get("matched_terms") or []),
            scope=section.scope,
        ))

    memory_text = "\n\n".join(memory_parts)
    masked_query = str(package.get("masked_query") or "")
    terms = query_focus_terms(project, masked_query)
    evidence_plan = build_evidence_plan(
        query=masked_query,
        selected_sections=[sections_by_id[source_id] for source_id in selected_ids if source_id in sections_by_id],
        all_sections=sections,
        terms=terms,
        focus_terms=terms,
    )
    prompt = build_prompt(
        str(package.get("project") or ""),
        memory_text,
        masked_query,
        file_artifacts_from_payload(package.get("files") or []),
        str(package.get("profile_id") or ""),
        rebuilt_sources,
        evidence_plan=evidence_plan,
        group_title=str(package.get("group_title") or ""),
    )
    return {
        "rebuild_id": uuid.uuid4().hex[:12],
        "prompt": prompt,
        "memory_chars": len(memory_text),
        "memory_sources": [source.__dict__ for source in rebuilt_sources],
        "evidence_plan": [item.__dict__ for item in evidence_plan],
        "memory_total_sections": package.get("memory_total_sections") or 0,
        "group_code": package.get("group_code") or "",
        "group_title": package.get("group_title") or "",
        "group_sections": package.get("group_sections") or 0,
        "selected_memory_source_ids": selected_ids,
    }


def file_artifacts_from_payload(files: list[dict[str, Any]]) -> list[FileArtifact]:
    artifacts: list[FileArtifact] = []
    for item in files:
        artifacts.append(FileArtifact(
            name=str(item.get("name") or ""),
            kind=str(item.get("kind") or ""),
            stored_path=str(item.get("stored_path") or ""),
            extraction_note=str(item.get("extraction_note") or ""),
            original_chars=int(item.get("original_chars") or 0),
            masked_chars=int(item.get("masked_chars") or 0),
            mask_status=str(item.get("mask_status") or ""),
            mask_replacements=int(item.get("mask_replacements") or 0),
            transcript_status=str(item.get("transcript_status") or ""),
            review=str(item.get("review") or ""),
            masked_text=str(item.get("masked_text") or ""),
        ))
    return artifacts
