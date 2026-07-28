"""Run a content-free localhost Ollama smoke for context compilation."""
from __future__ import annotations

import hashlib
import json
import tempfile
from collections import Counter
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gaia.context_chunking import split_context
from gaia.context_compiler import ContextCompiler
from gaia.protection import protect
from gaia.provenance import ProvenanceStore
from gaia.review import ReviewService


def main() -> None:
    material = "\n\n".join(
        f"{index}. Требование: проверить синтетический пункт {index}. "
        "Ответственный: Роль 1. Срок: 2030-01-01. Статус: назначено. Приоритет: высокий. "
        "Решение: использовать локальный маршрут. Риск: задержка. Действие: проверить результат."
        for index in range(1, 66)
    )
    with tempfile.TemporaryDirectory() as folder:
        store = ProvenanceStore(Path(folder) / "storage")
        workspace = store.create_workspace()
        source = store.accept_bytes(workspace, material.encode("utf-8"), "text/plain")
        extraction = store.create_extraction(workspace, source["source_id"], "smoke")
        sanitized = protect(store, workspace, extraction["artifact_id"])["sanitized"]
        review = ReviewService(store, workspace, lambda _: {"status": "completed", "findings": []})
        review.start(sanitized["artifact_id"]); review.confirm(sanitized["artifact_id"])
        chunks = split_context(material, 4000, 12, 250, 80)
        items = ContextCompiler(store, workspace).compile(sanitized["artifact_id"])
        repeated = ContextCompiler(store, workspace).compile(sanitized["artifact_id"])
        summary = {
            "model": "local route context_compiler",
            "input_hash": hashlib.sha256(material.encode("utf-8")).hexdigest(),
            "chunk_count": len(chunks),
            "candidate_count": len(items),
            "candidate_types": dict(Counter(item["item_type"] for item in items)),
            "metadata_present": any(item.get("actor_ref") and item.get("deadline") for item in items),
            "offsets_valid": all(0 <= block["start"] < block["end"] <= len(material) for item in items for block in item["block_links"]),
            "idempotent": [item["id"] for item in items] == [item["id"] for item in repeated],
        }
        print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
