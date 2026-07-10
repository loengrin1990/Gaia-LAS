from __future__ import annotations

import argparse
import hashlib
import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Sequence

from .config import SETTINGS
from .local_llm import (
    STRUCTURED_LOCAL_SYSTEM,
    TASK_HEARTH,
    normalize_structured_answer,
    parse_json_object,
    provider_configs,
    run_local_llm_prompt,
)
from .masking import mask_with_review
from .memory import select_project_memory
from .packaging import build_prompt
from .profiles import get_profile


@contextmanager
def deterministic_lore_selection() -> Any:
    setting_names = (
        "lore_query_rewrite",
        "lore_semantic_rerank",
        "lore_gap_detector",
    )
    if SETTINGS is None:
        yield
        return
    previous = {name: getattr(SETTINGS, name) for name in setting_names}
    try:
        for name in setting_names:
            object.__setattr__(SETTINGS, name, False)
        yield
    finally:
        for name, value in previous.items():
            object.__setattr__(SETTINGS, name, value)


def run_controlled_benchmark(
    provider_name: str,
    project: str,
    query: str,
    profile_id: str | None = None,
    timeout: int = 180,
) -> dict[str, Any]:
    if provider_name not in provider_configs():
        raise ValueError(f"Unknown local provider: {provider_name}")
    if not project.strip() or not query.strip():
        raise ValueError("Project and query must be non-empty.")

    profile = get_profile(profile_id)
    started = time.monotonic()
    with deterministic_lore_selection():
        selection = select_project_memory(
            project,
            query,
            profile_text=f"{profile.title}\n{profile.template}",
        )
    lore_seconds = time.monotonic() - started
    prompt = build_prompt(
        project,
        selection.text,
        query,
        [],
        profile.id,
        selection.sources,
        evidence_plan=selection.evidence_plan,
        group_title=selection.group_title,
    )
    masking_started = time.monotonic()
    masked = mask_with_review("Controlled local benchmark", prompt, include_llm_review=False)
    masking_seconds = time.monotonic() - masking_started
    hearth_started = time.monotonic()
    result = run_local_llm_prompt(
        masked.masked_text,
        STRUCTURED_LOCAL_SYSTEM,
        timeout=max(1, int(timeout)),
        temperature=0.2,
        task=TASK_HEARTH,
        provider_name=provider_name,
    )
    hearth_seconds = time.monotonic() - hearth_started
    raw_answer = str(result.get("answer") or "")
    structured = normalize_structured_answer(parse_json_object(raw_answer)) if result.get("ok") else None
    return {
        "provider": str(result.get("provider") or provider_name),
        "model": str(result.get("model") or ""),
        "project": project,
        "profile": profile.id,
        "timeout_seconds": max(1, int(timeout)),
        "timing": {
            "lore_seconds": round(lore_seconds, 3),
            "masking_seconds": round(masking_seconds, 3),
            "hearth_seconds": round(hearth_seconds, 3),
            "total_seconds": round(time.monotonic() - started, 3),
        },
        "context": {
            "memory_chars": len(selection.text),
            "prompt_chars_before_masking": len(prompt),
            "prompt_chars_sent": int(result.get("prompt_chars_sent") or 0),
            "prompt_compacted": bool(result.get("prompt_compacted")),
            "selected_sources": len(selection.sources),
            "selected_headings": [source.heading for source in selection.sources],
            "evidence_items": len(selection.evidence_plan or []),
            "veil_unresolved_pii": bool(masked.review.unresolved_pii),
            "lore_assists_enabled": False,
            "veil_llm_review_enabled": False,
        },
        "response": {
            "ok": bool(result.get("ok")),
            "status": str(result.get("status") or "ok"),
            "error": str(result.get("error") or ""),
            "answer_chars": len(raw_answer),
            "answer_sha256": hashlib.sha256(raw_answer.encode("utf-8")).hexdigest() if raw_answer else "",
            "structured": bool(structured),
            "sections": {
                "summary": bool((structured or {}).get("summary")),
                "observations": len((structured or {}).get("key_observations") or []),
                "risks": len((structured or {}).get("risks") or []),
                "questions": len((structured or {}).get("open_questions") or []),
                "steps": len((structured or {}).get("next_steps") or []),
            },
        },
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only Gaia Lore-to-Hearth local benchmark.")
    parser.add_argument("--provider", required=True, help="Provider name from local_llm.providers.")
    parser.add_argument("--project", required=True, help="Existing Gaia project name.")
    parser.add_argument("--query", required=True, help="Benchmark query; sent only to the selected local provider.")
    parser.add_argument("--profile", default=None, help="Optional Gaia profile id.")
    parser.add_argument("--timeout", type=int, default=180, help="Hearth timeout in seconds.")
    parser.add_argument("--output", default=None, help="Optional path for the JSON report. Parent directory must exist.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run_controlled_benchmark(
            provider_name=args.provider,
            project=args.project,
            query=args.query,
            profile_id=args.profile,
            timeout=args.timeout,
        )
    except ValueError as exc:
        print(f"Gaia local benchmark error: {exc}")
        return 2
    report = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        try:
            Path(args.output).expanduser().write_text(report + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"Gaia local benchmark error: cannot write report: {exc}")
            return 2
    else:
        print(report)
    return 0 if result["response"]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
