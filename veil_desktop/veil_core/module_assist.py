"""Compatibility hook: desktop model review is an explicit separate step."""
from __future__ import annotations
from typing import Any


def review_masking_with_local_llm(
    label: str, masked_text: str, rule_summary: dict[str, Any], timeout: int
) -> dict[str, Any] | None:
    return None
