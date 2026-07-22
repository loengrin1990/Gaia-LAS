from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .storage import atomic_write_bytes, atomic_write_text, path_lock
from .config import SETTINGS


SCHEMA_VERSION = 1


def default_store() -> "ProvenanceStore":
    if SETTINGS is None:
        raise ProvenanceError("Хранилище Gaia недоступно.")
    return ProvenanceStore(SETTINGS.storage_dir)


class ProvenanceError(ValueError):
    pass


class ProvenanceStore:
    """Local, file-backed provenance registry for newly admitted materials only."""

    ZONES = ("sources", "artifacts", "sanitized", "context", "pseudonyms", "exports", "metadata")

    def __init__(self, root: Path) -> None:
        self.root = root
        for zone in self.ZONES:
            (root / zone).mkdir(parents=True, exist_ok=True)
        if not self.registry_path.exists():
            self._write_registry({"schema_version": SCHEMA_VERSION, "workspaces": {}, "objects": {}})

    @property
    def registry_path(self) -> Path:
        return self.root / "metadata" / "registry.json"

    def create_workspace(self) -> str:
        workspace_id = self._id("ws")
        with path_lock(self.registry_path):
            registry = self._registry()
            registry["workspaces"][workspace_id] = {"workspace_id": workspace_id, "created_at": self._now()}
            self._write_registry(registry)
        return workspace_id

    def accept_bytes(self, workspace_id: str, content: bytes, media_type: str) -> dict[str, Any]:
        self._workspace(workspace_id)
        checksum = self._checksum(content)
        with path_lock(self.registry_path):
            registry = self._registry()
            for record in registry["objects"].values():
                if record.get("kind") == "source" and record["workspace_id"] == workspace_id and record["checksum"] == checksum:
                    return {**record, "duplicate": True}
            source_id = self._id("src")
            path = self.source_path(workspace_id, source_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(path, content)
            record = self._record(source_id, workspace_id, "source", checksum=checksum, media_type=media_type, integrity_status="intact", current=True)
            registry["objects"][source_id] = record
            try:
                self._write_registry(registry)
            except Exception:
                path.unlink(missing_ok=True)
                raise
            return {**record, "duplicate": False}

    def verify_source(self, workspace_id: str, source_id: str) -> bool:
        record = self.source_metadata(workspace_id, source_id)
        path = self.source_path(workspace_id, source_id)
        intact = path.is_file() and self._checksum(path.read_bytes()) == record["checksum"]
        if not intact:
            self._update(source_id, integrity_status="changed", status="requires_reintake")
        return intact

    def source_metadata(self, workspace_id: str, source_id: str) -> dict[str, Any]:
        return self._object(workspace_id, source_id, "source")

    def supersede_source(self, workspace_id: str, source_id: str, previous_id: str) -> None:
        """Record a new immutable source version without altering its parent."""
        self._object(workspace_id, source_id, "source")
        self._object(workspace_id, previous_id, "source")
        self._update(previous_id, current=False, status="superseded")
        self._update(source_id, previous_id=previous_id, current=True, status="ready")

    def create_extraction(self, workspace_id: str, source_id: str, processor_version: str) -> dict[str, Any]:
        source = self._object(workspace_id, source_id, "source")
        if not self.verify_source(workspace_id, source_id):
            raise ProvenanceError("Материал изменился и требует повторного добавления.")
        content = self.source_path(workspace_id, source_id).read_bytes().decode("utf-8", errors="replace")
        return self._versioned_content(workspace_id, "extraction", "art", content, source_id, processor_version, "")

    def create_sanitized(self, workspace_id: str, artifact_id: str, rules_version: str, content: str) -> dict[str, Any]:
        self._object(workspace_id, artifact_id, "extraction")
        return self._versioned_content(workspace_id, "sanitized", "san", content, artifact_id, "veil-v1", rules_version)

    def create_context_item(self, workspace_id: str, item_type: str, sanitized_ids: list[str]) -> dict[str, Any]:
        if item_type not in {"requirement", "decision", "risk", "open_question"}:
            raise ProvenanceError("Некорректный тип элемента контекста.")
        for artifact_id in sanitized_ids:
            self._object(workspace_id, artifact_id, "sanitized")
        item_id = self._id("ctx")
        record = self._record(item_id, workspace_id, "context", item_type=item_type, parents=list(sanitized_ids), status="draft", user_confirmed=False, current=True)
        self._add(record)
        return record

    def create_export(self, workspace_id: str, context_ids: list[str], template_version: str) -> dict[str, Any]:
        for item_id in context_ids:
            self._object(workspace_id, item_id, "context")
        export_id = self._id("exp")
        record = self._record(export_id, workspace_id, "export", parents=list(context_ids), template_version=template_version, status="draft", user_confirmed=False, export_allowed=False, current=True)
        self._add(record)
        return record

    def export_metadata(self, workspace_id: str, export_id: str) -> dict[str, Any]:
        return self._object(workspace_id, export_id, "export")

    def object_metadata(self, workspace_id: str, object_id: str) -> dict[str, Any]:
        return self._object(workspace_id, object_id)

    def set_pseudonym(self, workspace_id: str, key: str, value: str) -> None:
        self._workspace(workspace_id)
        path = self.root / "pseudonyms" / f"{workspace_id}.json"
        payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        payload[key] = value
        atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    def lineage(self, workspace_id: str, object_id: str) -> dict[str, Any]:
        current = self._object(workspace_id, object_id)
        chain = [current]
        while current.get("parents"):
            current = self._object(workspace_id, current["parents"][0])
            chain.append(current)
        result = {"workspace_id": workspace_id, "chain": [{"id": item["id"], "kind": item["kind"], "processor_version": item.get("processor_version", "")} for item in reversed(chain)]}
        for item in chain:
            result[f"{item['kind']}_id"] = item["id"]
        return result

    def migration_preview(self) -> dict[str, Any]:
        registry = self._registry()
        counts = {"sources": 0, "artifacts": 0, "sanitized": 0, "context": 0, "exports": 0, "without_provenance": 0}
        for item in registry["objects"].values():
            key = {"source": "sources", "extraction": "artifacts", "sanitized": "sanitized", "context": "context", "export": "exports"}.get(item["kind"], "")
            if key in counts:
                counts[key] += 1
            if item["kind"] != "source" and not item.get("parents"):
                counts["without_provenance"] += 1
        return {"read_only": True, "counts": counts, "categories": ["sources", "derived", "legacy_unknown"]}

    def recover(self) -> dict[str, int]:
        registry = self._registry()
        known = {f"{item['id']}.bin" for item in registry["objects"].values() if item["kind"] == "source"}
        removed = 0
        for path in (self.root / "sources").glob("*/*.bin"):
            if path.name not in known:
                path.unlink()
                removed += 1
        return {"removed_orphans": removed}

    def source_zone(self, workspace_id: str) -> Path:
        self._workspace(workspace_id)
        return self.root / "sources" / workspace_id

    def source_path(self, workspace_id: str, source_id: str) -> Path:
        return self.source_zone(workspace_id) / f"{source_id}.bin"

    def _versioned_content(self, workspace_id: str, kind: str, prefix: str, content: str, parent_id: str, processor_version: str, rules_version: str) -> dict[str, Any]:
        parent = self._object(workspace_id, parent_id)
        prior = [item for item in self._registry()["objects"].values() if item["workspace_id"] == workspace_id and item["kind"] == kind and item.get("parents") == [parent_id] and item.get("current")]
        for item in prior:
            self._update(item["id"], current=False, status="superseded")
        artifact_id = self._id(prefix)
        path = self.root / ("artifacts" if kind == "extraction" else "sanitized") / workspace_id / f"{artifact_id}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, content)
        record = self._record(artifact_id, workspace_id, kind, parents=[parent_id], checksum=self._checksum(content.encode()), processor_version=processor_version, rules_version=rules_version, previous_id=prior[0]["id"] if prior else "", status="ready", current=True, export_allowed=False, user_confirmed=False)
        self._add(record)
        return record

    def _record(self, object_id: str, workspace_id: str, kind: str, **values: Any) -> dict[str, Any]:
        aliases = {"source": "source_id", "extraction": "artifact_id", "sanitized": "artifact_id", "context": "context_item_id", "export": "export_id"}
        return {"id": object_id, aliases.get(kind, "object_id"): object_id, "workspace_id": workspace_id, "kind": kind, "created_at": self._now(), "schema_version": SCHEMA_VERSION, **values}

    def _add(self, record: dict[str, Any]) -> None:
        with path_lock(self.registry_path):
            registry = self._registry()
            registry["objects"][record["id"]] = record
            self._write_registry(registry)

    def _update(self, object_id: str, **values: Any) -> None:
        with path_lock(self.registry_path):
            registry = self._registry()
            registry["objects"][object_id].update(values)
            self._write_registry(registry)

    def _workspace(self, workspace_id: str) -> None:
        if workspace_id not in self._registry()["workspaces"]:
            raise ProvenanceError("Рабочее пространство не найдено.")

    def _object(self, workspace_id: str, object_id: str, kind: str = "") -> dict[str, Any]:
        item = self._registry()["objects"].get(object_id)
        if not item or item["workspace_id"] != workspace_id or (kind and item["kind"] != kind):
            raise ProvenanceError("Объект недоступен в этом рабочем пространстве.")
        return dict(item)

    def _registry(self) -> dict[str, Any]:
        return json.loads(self.registry_path.read_text(encoding="utf-8"))

    def _write_registry(self, payload: dict[str, Any]) -> None:
        atomic_write_text(self.registry_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    @staticmethod
    def _id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex}"

    @staticmethod
    def _checksum(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")
