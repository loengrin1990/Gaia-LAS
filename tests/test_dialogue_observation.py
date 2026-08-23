from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from gaia.runtime_diagnostics import emit_dialogue_observation


class DialogueObservationTests(unittest.TestCase):
    def test_disabled_observation_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "dialogue_observation.jsonl"
            emit_dialogue_observation(enabled=False, path=path, observation={})
            self.assertFalse(path.exists())

    def test_enabled_observation_writes_only_the_allow_list(self) -> None:
        observation = {
            "observation_id": "dialogue-1",
            "dialogue_read_path": "hybrid",
            "hybrid_invoked": True,
            "hybrid_completed": True,
            "evidence_found": True,
            "oc_applied": False,
            "provenance_complete": True,
            "elapsed_ms": 12,
            "failure_reason": None,
            "eligible_for_adjudication": True,
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "dialogue_observation.jsonl"
            emit_dialogue_observation(enabled=True, path=path, observation=observation)
            lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        payload = json.loads(lines[0])
        self.assertEqual(set(payload), {"timestamp", *observation})
        self.assertNotIn("query", payload)
        self.assertNotIn("answer", payload)
        self.assertNotIn("prompt", payload)


if __name__ == "__main__":
    unittest.main()
