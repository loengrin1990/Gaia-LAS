from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from gaia.source_evidence import MaterialAvailability, SourceEvidenceReader


class SourceEvidenceReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "storage"
        (self.root / "metadata").mkdir(parents=True)
        (self.root / "sanitized" / "ws_a").mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_registry(self, objects: dict[str, dict]) -> Path:
        path = self.root / "metadata" / "registry.json"
        path.write_text(json.dumps({"schema_version": 1, "workspaces": {"ws_a": {"workspace_id": "ws_a"}}, "objects": objects}), encoding="utf-8")
        return path

    @staticmethod
    def source(source_id: str, **changes: object) -> dict:
        return {"id": source_id, "kind": "source", "workspace_id": "ws_a", "current": True, "created_at": "2026-08-23T00:00:00", "media_type": "text/plain", **changes}

    @staticmethod
    def extraction(item_id: str, parent: str, **changes: object) -> dict:
        return {"id": item_id, "kind": "extraction", "workspace_id": "ws_a", "current": True, "parents": [parent], **changes}

    @staticmethod
    def sanitized(item_id: str, parent: str, **changes: object) -> dict:
        return {"id": item_id, "kind": "sanitized", "workspace_id": "ws_a", "current": True, "parents": [parent], **changes}

    def test_available_lineage_preserves_exact_sanitized_representation(self) -> None:
        objects = {"src_a": self.source("src_a"), "art_a": self.extraction("art_a", "src_a"), "san_a": self.sanitized("san_a", "art_a")}
        self.write_registry(objects)
        text = "Точное очищенное доказательство."
        (self.root / "sanitized" / "ws_a" / "san_a.txt").write_text(text, encoding="utf-8")
        material = SourceEvidenceReader(self.root, "ws_a").inventory()[0]
        self.assertEqual(material.state, MaterialAvailability.AVAILABLE)
        self.assertEqual((material.source_id, material.sanitized_id, material.text), ("src_a", "san_a", text))

    def test_inventory_distinguishes_unavailable_material_states_without_mutation(self) -> None:
        objects = {
            "src_unsupported": self.source("src_unsupported", media_type="application/vnd.unknown"),
            "src_missing": self.source("src_missing"),
            "art_missing": self.extraction("art_missing", "src_missing"),
            "src_invalid": self.source("src_invalid"),
            "art_one": self.extraction("art_one", "src_invalid"),
            "art_two": self.extraction("art_two", "src_invalid"),
            "src_file": self.source("src_file"),
            "art_file": self.extraction("art_file", "src_file"),
            "san_file": self.sanitized("san_file", "art_file"),
        }
        registry = self.write_registry(objects)
        before = hashlib.sha256(registry.read_bytes()).hexdigest()
        by_id = {item.source_id: item for item in SourceEvidenceReader(self.root, "ws_a").inventory()}
        self.assertEqual(by_id["src_unsupported"].state, MaterialAvailability.UNSUPPORTED)
        self.assertEqual(by_id["src_missing"].state, MaterialAvailability.UNPARSED_OR_MISSING_SANITIZED)
        self.assertEqual(by_id["src_invalid"].state, MaterialAvailability.INVALID_LINEAGE)
        self.assertEqual(by_id["src_file"].state, MaterialAvailability.MISSING_FILE)
        self.assertEqual(before, hashlib.sha256(registry.read_bytes()).hexdigest())

    def test_unknown_workspace_fails_closed_without_creating_storage(self) -> None:
        self.write_registry({})
        with self.assertRaisesRegex(ValueError, "Workspace"):
            SourceEvidenceReader(self.root, "ws_missing").inventory()
        self.assertFalse((self.root / "sources").exists())
