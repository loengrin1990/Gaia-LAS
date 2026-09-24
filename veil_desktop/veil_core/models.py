"""The two Veil result types needed by the desktop copy."""
from __future__ import annotations
from dataclasses import dataclass, field


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
    manual_confirmation_required: bool = False
    unresolved_reason: str = ""
    markdown: str = ""
