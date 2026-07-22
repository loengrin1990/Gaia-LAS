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
from .protection import protect
from .protection import safe_report
from .review import ReviewService
from .context_compiler import ContextCompiler, ContextService


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
        protected_uploads: list[tuple[str, bytes]] = []
        for filename, content in uploaded:
            source = self.store.accept_bytes(workspace_id, content, "application/octet-stream")
            material = {"source_id": source["source_id"], "duplicate": source["duplicate"], "status": "duplicate" if source["duplicate"] else "accepted"}
            if source["duplicate"]:
                # Never fall back to raw bytes for an already admitted source.
                # Reprocessing is a later explicit, managed operation.
                raise ProvenanceError("Такой материал уже добавлен в это рабочее пространство.")
            if not source["duplicate"]:
                source_key = hashlib.sha256(filename.encode("utf-8")).hexdigest()
                prior_id = self._read().get("source_keys", {}).get(f"{workspace_id}:{source_key}")
                if prior_id and prior_id != source["source_id"]:
                    self.store.supersede_source(workspace_id, source["source_id"], prior_id)
                    material["status"] = "new_version"
                extraction = self.store.create_extraction(workspace_id, source["source_id"], "gaia-extractor-v1")
                material["artifact_id"] = extraction["artifact_id"]
                outcome = protect(self.store, workspace_id, extraction["artifact_id"], self.dictionary(project))
                material["sanitized_id"] = outcome["sanitized"]["artifact_id"]
                material["protection"] = {"status": outcome["report"]["status"], "counts": outcome["report"]["counts"]}
                cleaned = (self.store.root / "sanitized" / workspace_id / f"{material['sanitized_id']}.txt").read_text(encoding="utf-8")
                protected_uploads.append((filename, cleaned.encode("utf-8")))
                self._remember_source_key(workspace_id, source_key, source["source_id"])
            materials.append(material)
        self._save_operation(operation_id, workspace_id, materials, "accepted")
        return {"operation_id": operation_id, "workspace_id": workspace_id, "materials": materials, "protected_uploads": protected_uploads}

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

    def materials(self, project: str) -> list[dict[str, Any]]:
        """Return a safe, user-facing index of material progress in one workspace.

        The index intentionally contains no source bytes, checksums, paths, or
        pseudonym dictionary entries. Identifiers are retained only for the UI
        to address existing protected endpoints.
        """
        workspace_id = self._workspace_for(project)
        objects = self.store._registry()["objects"].values()
        by_parent: dict[str, list[dict[str, Any]]] = {}
        for item in objects:
            if item.get("workspace_id") != workspace_id:
                continue
            for parent in item.get("parents") or []:
                by_parent.setdefault(parent, []).append(item)

        records: list[dict[str, Any]] = []
        for source in objects:
            if source.get("workspace_id") != workspace_id or source.get("kind") != "source":
                continue
            extraction = next((item for item in by_parent.get(source["id"], []) if item.get("kind") == "extraction" and item.get("current")), None)
            sanitized = next((item for item in by_parent.get(extraction["id"], []) if item.get("kind") == "sanitized" and item.get("current")), None) if extraction else None
            review: dict[str, Any] = {}
            if sanitized:
                try:
                    review = self.review(project).get(sanitized["id"])
                except ProvenanceError:
                    review = {}
            records.append({
                "source_id": source["id"],
                "created_at": source.get("created_at", ""),
                "source_status": source.get("status", "accepted"),
                "current": bool(source.get("current", True)),
                "version_count": 1 + int(bool(source.get("previous_id"))),
                "sanitized_id": sanitized.get("id", "") if sanitized else "",
                "review_state": review.get("state", "not_started"),
                "review_confirmed": bool(review.get("confirmed")),
            })
        return sorted(records, key=lambda item: str(item["created_at"]), reverse=True)

    def lineage(self, project: str, object_id: str) -> dict[str, Any]:
        return self.store.lineage(self._workspace_for(project), object_id)

    def protection_report(self, project: str, artifact_id: str) -> dict[str, Any]:
        return safe_report(self.store, self._workspace_for(project), artifact_id)

    def protection_metadata(self, project: str, artifact_id: str) -> dict[str, Any]:
        item = self.store.object_metadata(self._workspace_for(project), artifact_id)
        if item.get("kind") != "sanitized":
            raise ProvenanceError("Очищенное представление недоступно в этом рабочем пространстве.")
        return {key: item[key] for key in ("artifact_id", "workspace_id", "status", "processor_version", "rules_version", "current", "previous_id", "export_allowed", "created_at", "checksum", "parents") if key in item}

    def protection_lineage(self, project: str, artifact_id: str) -> dict[str, Any]:
        return self.store.lineage(self._workspace_for(project), artifact_id)

    def review(self, project: str) -> ReviewService:
        return ReviewService(self.store, self._workspace_for(project))

    def compiler(self, project: str) -> ContextCompiler:
        return ContextCompiler(self.store, self._workspace_for(project))

    def context(self, project: str) -> ContextService:
        return ContextService(self.store, self._workspace_for(project))

    def reprocess_protection(self, project: str, extraction_id: str, rules_version: str) -> dict[str, Any]:
        workspace_id = self._workspace_for(project)
        outcome = protect(self.store, workspace_id, extraction_id, self.dictionary(project), rules_version)
        return {"artifact_id": outcome["sanitized"]["artifact_id"], "status": outcome["report"]["status"], "export_allowed": False}

    def set_dictionary(self, project: str, dictionary: dict[str, list[str]]) -> None:
        workspace_id = self._workspace_for(project)
        safe = {str(category): [str(value) for value in values if str(value).strip()] for category, values in dictionary.items() if isinstance(values, list)}
        atomic_write_text(self.store.root / "pseudonyms" / f"{workspace_id}.dictionary.json", json.dumps(safe, ensure_ascii=False, indent=2) + "\n")

    def dictionary(self, project: str) -> dict[str, list[str]]:
        workspace_id = self._workspace_for(project)
        path = self.store.root / "pseudonyms" / f"{workspace_id}.dictionary.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def add_dictionary_value(self, project: str, artifact_id: str, category: str, value: str) -> dict[str, Any]:
        workspace_id = self._workspace_for(project)
        if not value.strip(): raise ProvenanceError("Не удалось добавить пустое значение в локальный словарь.")
        dictionary = self.dictionary(project); dictionary.setdefault(category, [])
        if value not in dictionary[category]: dictionary[category].append(value)
        self.set_dictionary(project, dictionary)
        item = self.store.object_metadata(workspace_id, artifact_id)
        parent = (item.get("parents") or [""])[0]
        if item.get("kind") != "sanitized" or not parent: raise ProvenanceError("Очищенная версия недоступна для повторной очистки.")
        return self.reprocess_protection(project, parent, "dictionary-v2")

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
