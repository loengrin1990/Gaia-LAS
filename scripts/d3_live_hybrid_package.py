#!/usr/bin/env python3
"""Build a D3.1A package from existing local storage without invoking models."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from gaia.hybrid_read_path import DeterministicLexicalSourceRetriever, OperationalOverlaySelector, SourceRetrievalRequest, assemble_hybrid_package
from gaia.operational_context import KIND_REGISTRY, OperationalContextStore
from gaia.operational_context_retrieval import OperationalContextReader, RetrievalRequest, TrustedLocalProcessingPolicy
from gaia.source_evidence import SourceEvidenceReader


def workspace_for_project(storage_root: Path, project: str) -> str | None:
    path = storage_root / "metadata" / "intake_operations.json"
    if not project.strip() or not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    workspaces = payload.get("workspaces") if isinstance(payload, dict) else None
    if not isinstance(workspaces, dict):
        return None
    key = hashlib.sha256(project.strip().encode("utf-8")).hexdigest()
    value = workspaces.get(key)
    return str(value) if isinstance(value, str) and value else None


def build_package(storage_root: Path, workspace_id: str, query: str) -> dict:
    materials = SourceEvidenceReader(storage_root, workspace_id).inventory()
    source_result = DeterministicLexicalSourceRetriever().retrieve(SourceRetrievalRequest(query, workspace_id), materials)
    retrieval = OperationalContextReader(OperationalContextStore(storage_root)).retrieve(RetrievalRequest(
        user_ref="local_user", project_ref=workspace_id, system_ref="gaia_local_runtime",
        supported_kinds=frozenset(KIND_REGISTRY), trusted_local_policy=TrustedLocalProcessingPolicy(frozenset(("standard", "restricted", "unknown"))),
        max_items=100, max_chars=60_000,
    ))
    overlay = OperationalOverlaySelector().select(query, retrieval.eligible_items)
    package = assemble_hybrid_package(query, source_result, overlay).as_dict()
    package["diagnostics"]["oc_retrieval"] = retrieval.as_dict()
    return package


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a read-only D3.1A Hybrid evidence package.")
    parser.add_argument("--workspace-id")
    parser.add_argument("--project")
    parser.add_argument("--query", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--storage-root", type=Path, default=Path(__file__).resolve().parents[1] / "storage")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    storage_root = args.storage_root.resolve()
    workspace_id = args.workspace_id or workspace_for_project(storage_root, args.project or "")
    if not workspace_id:
        raise ValueError("Provide --workspace-id or a project registered in existing intake metadata.")
    output = args.output.resolve()
    if output.is_relative_to(storage_root) or not output.parent.is_dir():
        raise ValueError("Output must be in an existing caller-owned directory outside Gaia storage.")
    output.write_text(json.dumps(build_package(storage_root, workspace_id, args.query), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
