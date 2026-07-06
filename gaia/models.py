from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ApiError:
    code: str
    message: str
    details: dict[str, Any]
    trace_id: str


@dataclass
class ProjectGroup:
    code: str
    title: str
    status: str
    path: str
    context_path: str
    sources_path: str
    journal_path: str
    materials_path: str
    project_count: int = 0


@dataclass
class ProjectRecord:
    name: str
    code: str
    title: str
    status: str
    path: str
    memory_path: str
    sources_path: str
    journal_path: str
    graph_index_path: str
    group_code: str = ""
    group_title: str = ""
    context_inheritance: bool = True
    health: str = "ok"
    issues: list[str] | None = None


@dataclass(frozen=True)
class TaskProfile:
    id: str
    title: str
    description: str
    template: str


@dataclass
class MaskFinding:
    category: str
    token: str
    sample: str
    source: str


@dataclass
class MaskReview:
    label: str
    status: str
    total_replacements: int
    counts: dict[str, int]
    findings: list[MaskFinding]
    suspected_pii: bool
    unresolved_pii: bool
    unresolved_reason: str = ""
    markdown: str = ""


@dataclass
class FileArtifact:
    name: str
    kind: str
    stored_path: str
    extraction_note: str
    original_chars: int
    masked_chars: int
    mask_status: str
    mask_replacements: int
    transcript_status: str = ""
    review: str = ""
    mask_review: MaskReview | None = None
    masked_text: str = ""


@dataclass
class MemorySource:
    id: str
    project: str
    path: str
    heading: str
    line_start: int
    line_end: int
    score: int
    matched_terms: list[str]
    scope: str = "project"


@dataclass
class EvidenceItem:
    claim: str
    status: str
    source_id: str
    source_path: str
    heading: str
    excerpt: str
    reason: str
    scope: str = "source"


@dataclass
class MemorySelection:
    text: str
    sources: list[MemorySource]
    total_sections: int
    indexed_projects: list[str]
    evidence_plan: list[EvidenceItem] | None = None
    group_code: str = ""
    group_title: str = ""
    group_sections: int = 0


@dataclass
class AnalysisPackage:
    run_id: str
    project: str
    profile_id: str
    profile_title: str
    route: str
    safe_for_codex_after_confirmation: bool
    local_fallback_required: bool
    policy_notes: list[str]
    memory_chars: int
    memory_sources: list[MemorySource]
    evidence_plan: list[EvidenceItem]
    memory_total_sections: int
    query_mask_status: str
    query_mask_replacements: int
    query_mask_review: MaskReview | None
    masked_query: str
    files: list[FileArtifact]
    prompt: str
    journal_path: str
    safety_audit_path: str
    group_code: str = ""
    group_title: str = ""
    group_sections: int = 0


@dataclass
class ScribeDraft:
    project: str
    created_at: str
    draft_path: str
    markdown: str
    instruction: str
    mask_review: MaskReview


@dataclass
class ScribePlanItem:
    id: str
    category: str
    title: str
    body: str
    destination: str
    operation: str
    target_path: str
    confidence: str
    status: str
    evidence: str
    reason: str
    safety_notes: list[str]
    selected: bool = True


@dataclass
class ScribePlan:
    id: str
    project: str
    created_at: str
    status: str
    blocked_reason: str
    items: list[ScribePlanItem]
    preview: str
    safety_notes: list[str]
    backup_required: bool = True


@dataclass
class ScribeApplyResult:
    plan_id: str
    project: str
    applied: list[str]
    skipped: list[str]
    changed_files: list[str]
    backup_path: str
    journal_entry: str
    retrieval_check: str


@dataclass
class ConversationMessage:
    id: str
    role: str
    text: str
    masked_text: str
    created_at: str
    job_id: str = ""
    route: str = ""
    safety_status: str = ""
    structured_answer: dict[str, Any] | None = None


@dataclass
class Conversation:
    id: str
    project: str
    title: str
    status: str
    created_at: str
    updated_at: str
    rolling_summary: str
    messages: list[ConversationMessage]
    last_job_id: str = ""


@dataclass
class JobRecord:
    id: str
    status: str
    created_at: str
    updated_at: str
    project: str
    message: str
    progress: int = 0
    result: dict[str, Any] | None = None
    error: str = ""
