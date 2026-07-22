"""Controlled intake adapter for the existing analysis screen.

New uploads are durably admitted to the provenance store first.  The bytes
remain in memory only while the legacy package builder consumes them; its
temporary run copy is removed by the existing cleanup routine and is never a
second source of truth.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from .provenance import ProvenanceError, ProvenanceStore, default_store
from .storage import atomic_write_text, path_lock


class ControlledIntake:
    def __init__(self, store: ProvenanceStore | None = None) -> None:
        self.store = store or default_store()
        self.path = self.store.root / "metadata" / "intake_operations.json"
        if not self.path.exists():
            atomic_write_text(self.path, json.dumps({"workspaces": {}, "source_keys": {}, "operations": {}}, ensure_ascii=False) + "\n")

    def admit(self, project: str, uploaded: list[tuple[str, bytes]]) -> dict[str, Any]:
        if not project.strip():
            raise ProvenanceError("Выберите рабочее пространство перед добавлением материала.")
        workspace_id = self._workspace_for(project)
        operation_id = f"op_{uuid.uuid4().hex}"
        materials: list[dict[str, Any]] = []
        for filename, content in uploaded:
            source = self.store.accept_bytes(workspace_id, content, "application/octet-stream")
            material = {"source_id": source["source_id"], "duplicate": source["duplicate"], "status": "duplicate" if source["duplicate"] else "accepted"}
            if not source["duplicate"]:
                source_key = hashlib.sha256(filename.encode("utf-8")).hexdigest()
                prior_id = self._read().get("source_keys", {}).get(f"{workspace_id}:{source_key}")
                if prior_id and prior_id != source["source_id"]:
                    self.store.supersede_source(workspace_id, source["source_id"], prior_id)
                    material["status"] = "new_version"
                extraction = self.store.create_extraction(workspace_id, source["source_id"], "gaia-extractor-v1")
                material["artifact_id"] = extraction["artifact_id"]
                self._remember_source_key(workspace_id, source_key, source["source_id"])
            materials.append(material)
        self._save_operation(operation_id, workspace_id, materials, "accepted")
        return {"operation_id": operation_id, "workspace_id": workspace_id, "materials": materials}

    def finish(self, operation_id: str, status: str) -> None:
        with path_lock(self.path):
            payload = self._read()
            if operation_id in payload["operations"]:
                payload["operations"][operation_id]["status"] = status
                atomic_write_text(self.path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    def operation(self, project: str, operation_id: str) -> dict[str, Any]:
        payload = self._read()
        operation = payload["operations"].get(operation_id)
        if not operation or operation["workspace_id"] != self._workspace_for(project):
            raise ProvenanceError("Операция недоступна в этом рабочем пространстве.")
        return dict(operation)

    def metadata(self, project: str, source_id: str) -> dict[str, Any]:
        item = self.store.source_metadata(self._workspace_for(project), source_id)
        return {key: item[key] for key in ("source_id", "workspace_id", "status", "integrity_status", "created_at") if key in item}

    def lineage(self, project: str, object_id: str) -> dict[str, Any]:
        return self.store.lineage(self._workspace_for(project), object_id)

    def _workspace_for(self, project: str) -> str:
        key = hashlib.sha256(project.strip().encode("utf-8")).hexdigest()
        with path_lock(self.path):
            payload = self._read()
            workspace_id = payload["workspaces"].get(key)
            if not workspace_id:
                workspace_id = self.store.create_workspace()
                payload["workspaces"][key] = workspace_id
                atomic_write_text(self.path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            return workspace_id

    def _save_operation(self, operation_id: str, workspace_id: str, materials: list[dict[str, Any]], status: str) -> None:
        with path_lock(self.path):
            payload = self._read()
            payload["operations"][operation_id] = {"operation_id": operation_id, "workspace_id": workspace_id, "materials": materials, "status": status}
            atomic_write_text(self.path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    def _remember_source_key(self, workspace_id: str, source_key: str, source_id: str) -> None:
        with path_lock(self.path):
            payload = self._read()
            payload.setdefault("source_keys", {})[f"{workspace_id}:{source_key}"] = source_id
            atomic_write_text(self.path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    def _read(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8"))
