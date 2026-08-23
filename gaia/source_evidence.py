"""Read-only access to queryable source-evidence representations.

The canonical project materials remain outside this module.  It reads only the
existing sanitized representation and its registry lineage; it never creates a
workspace, registry, directory, or derived material.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class SourceEvidenceError(ValueError):
    pass


class MaterialAvailability(str, Enum):
    AVAILABLE = "available"
    UNSUPPORTED = "unsupported"
    UNPARSED_OR_MISSING_SANITIZED = "unparsed_or_missing_sanitized"
    INVALID_LINEAGE = "invalid_lineage"
    MISSING_FILE = "missing_file"
    NOT_CURRENT = "not_current"


@dataclass(frozen=True)
class SourceMaterial:
    state: MaterialAvailability
    source_id: str
    workspace_id: str
    sanitized_id: str = ""
    created_at: str = ""
    media_type: str = ""
    text: str = ""
    detail: str = ""

    def diagnostic(self) -> dict[str, str]:
        return {
            "state": self.state.value,
            "source_id": self.source_id,
            "sanitized_id": self.sanitized_id,
            "workspace_id": self.workspace_id,
            "detail": self.detail,
        }


class SourceEvidenceReader:
    """Enumerate existing sanitized evidence for one exact workspace."""

    _SUPPORTED_MEDIA_TYPES = frozenset(("text/plain", "application/pdf"))

    def __init__(self, storage_root: Path, workspace_id: str) -> None:
        self.storage_root = Path(storage_root)
        self.workspace_id = workspace_id

    @property
    def registry_path(self) -> Path:
        return self.storage_root / "metadata" / "registry.json"

    def inventory(self) -> tuple[SourceMaterial, ...]:
        registry = self._registry()
        workspaces = registry.get("workspaces")
        objects = registry.get("objects")
        if not isinstance(workspaces, dict) or not isinstance(objects, dict):
            raise SourceEvidenceError("Source registry shape is invalid.")
        if self.workspace_id not in workspaces:
            raise SourceEvidenceError("Workspace is unavailable in the source registry.")

        records = [item for item in objects.values() if isinstance(item, dict)]
        by_parent: dict[str, list[dict[str, Any]]] = {}
        for item in records:
            for parent in item.get("parents") or ():
                if isinstance(parent, str):
                    by_parent.setdefault(parent, []).append(item)

        sources = [
            item for item in records
            if item.get("workspace_id") == self.workspace_id and item.get("kind") == "source"
        ]
        result = [self._material(source, by_parent) for source in sources]
        return tuple(sorted(result, key=lambda item: (item.created_at, item.source_id)))

    def _material(self, source: dict[str, Any], by_parent: dict[str, list[dict[str, Any]]]) -> SourceMaterial:
        source_id = str(source.get("id") or "")
        created_at = str(source.get("created_at") or "")
        media_type = str(source.get("media_type") or "")
        if not source_id:
            return SourceMaterial(MaterialAvailability.INVALID_LINEAGE, "", self.workspace_id, detail="source_id_missing")
        if not source.get("current", True):
            return SourceMaterial(MaterialAvailability.NOT_CURRENT, source_id, self.workspace_id, created_at=created_at, media_type=media_type, detail="source_not_current")
        if media_type and media_type not in self._SUPPORTED_MEDIA_TYPES:
            return SourceMaterial(MaterialAvailability.UNSUPPORTED, source_id, self.workspace_id, created_at=created_at, media_type=media_type, detail="unsupported_media_type")

        extractions = [item for item in by_parent.get(source_id, ()) if item.get("kind") == "extraction" and item.get("current")]
        if len(extractions) != 1:
            state = MaterialAvailability.UNPARSED_OR_MISSING_SANITIZED if not extractions else MaterialAvailability.INVALID_LINEAGE
            return SourceMaterial(state, source_id, self.workspace_id, created_at=created_at, media_type=media_type, detail="current_extraction_missing_or_ambiguous")
        extraction_id = str(extractions[0].get("id") or "")
        sanitized = [item for item in by_parent.get(extraction_id, ()) if item.get("kind") == "sanitized" and item.get("current")]
        if len(sanitized) != 1:
            state = MaterialAvailability.UNPARSED_OR_MISSING_SANITIZED if not sanitized else MaterialAvailability.INVALID_LINEAGE
            return SourceMaterial(state, source_id, self.workspace_id, created_at=created_at, media_type=media_type, detail="current_sanitized_missing_or_ambiguous")
        sanitized_id = str(sanitized[0].get("id") or "")
        if not sanitized_id:
            return SourceMaterial(MaterialAvailability.INVALID_LINEAGE, source_id, self.workspace_id, created_at=created_at, media_type=media_type, detail="sanitized_id_missing")
        path = self.storage_root / "sanitized" / self.workspace_id / f"{sanitized_id}.txt"
        if not path.is_file():
            return SourceMaterial(MaterialAvailability.MISSING_FILE, source_id, self.workspace_id, sanitized_id, created_at, media_type, detail="sanitized_file_missing")
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SourceEvidenceError("Sanitized source evidence cannot be read.") from exc
        return SourceMaterial(MaterialAvailability.AVAILABLE, source_id, self.workspace_id, sanitized_id, created_at, media_type, text)

    def _registry(self) -> dict[str, Any]:
        if not self.registry_path.is_file():
            raise SourceEvidenceError("Source registry is unavailable.")
        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SourceEvidenceError("Source registry is invalid.") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise SourceEvidenceError("Source registry schema is unsupported.")
        return payload
