from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from gaia.window_runtime_diagnostics import (
    DIAGNOSTICS_CONFIGURATION_ID,
    DIAGNOSTICS_FLAG,
    DIAGNOSTICS_PATH,
    window_diagnostics_environment,
    write_event,
)


class WindowRuntimeDiagnosticsTests(unittest.TestCase):
    def test_disabled_writer_does_not_create_a_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostics.jsonl"
            result = write_event("diagnostics_python_enabled", environment={DIAGNOSTICS_PATH: str(path)})
            self.assertEqual(result, {"enabled": False, "written": False})
            self.assertFalse(path.exists())

    def test_writer_appends_to_a_precreated_empty_file_without_user_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostics.jsonl"
            path.touch()
            environment = {DIAGNOSTICS_FLAG: "1", DIAGNOSTICS_PATH: str(path)}
            result = write_event(
                "diagnostics_python_enabled",
                {"diagnostics_flag_present": True, "file_name": "private.txt"},
                environment,
            )
            self.assertTrue(result["written"])
            event = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(event["event_code"], "diagnostics_python_enabled")
            self.assertTrue(event["diagnostics_flag_present"])
            self.assertNotIn("file_name", event)
            self.assertNotIn(str(path), path.read_text(encoding="utf-8"))

    def test_writer_creates_missing_file_in_existing_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "new.jsonl"
            result = write_event("diagnostics_python_enabled", environment={DIAGNOSTICS_FLAG: "1", DIAGNOSTICS_PATH: str(path)})
            self.assertTrue(result["written"])
            self.assertTrue(path.exists())

    def test_writer_reports_missing_parent_with_stable_code(self) -> None:
        path = Path(tempfile.gettempdir()) / "gaia-no-parent-for-test" / "diagnostics.jsonl"
        result = write_event("diagnostics_python_enabled", environment={DIAGNOSTICS_FLAG: "1", DIAGNOSTICS_PATH: str(path)})
        self.assertEqual(result["error_code"], "diagnostics_parent_missing")
        self.assertFalse(path.exists())

    def test_launcher_environment_copies_a_safe_configuration_identifier(self) -> None:
        environment = {DIAGNOSTICS_FLAG: "1", DIAGNOSTICS_PATH: "/private/tmp/diagnostics.jsonl"}
        child = window_diagnostics_environment(environment)
        self.assertEqual(child[DIAGNOSTICS_PATH], environment[DIAGNOSTICS_PATH])
        self.assertTrue(child[DIAGNOSTICS_CONFIGURATION_ID])
        self.assertNotEqual(child[DIAGNOSTICS_CONFIGURATION_ID], environment[DIAGNOSTICS_PATH])


if __name__ == "__main__":
    unittest.main()
