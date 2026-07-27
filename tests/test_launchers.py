from __future__ import annotations

import subprocess
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gaia.launchers import launch_gaia_window, launch_module, wait_for_runtime


class LauncherTests(unittest.TestCase):
    @patch("gaia.launchers.wait_for_runtime", return_value={"ok": True, "runtime": {"runtime_id": "runtime-a"}})
    @patch("gaia.launchers.subprocess.Popen")
    def test_gaia_window_uses_system_webkit_launcher(self, popen, ready) -> None:
        result = launch_gaia_window()

        self.assertTrue(result["ok"])
        command = popen.call_args.args[0]
        self.assertEqual(command[:3], ["/usr/bin/osascript", "-l", "JavaScript"])
        self.assertIn("gaia_window.js", command[3])
        self.assertIn("runtime=runtime-a", command[4])

    @patch("gaia.launchers.wait_for_runtime", return_value={"ok": True, "runtime": {"runtime_id": "runtime-a"}})
    @patch("gaia.launchers.subprocess.Popen")
    def test_gaia_window_explicitly_passes_opt_in_diagnostics_to_jxa(self, popen, ready) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"GAIA_STAGE6_RUNTIME_DIAGNOSTICS": "1", "GAIA_STAGE6_DIAGNOSTICS_PATH": f"{directory}/events.jsonl"},
            clear=False,
        ):
            result = launch_gaia_window()

        self.assertTrue(result["ok"])
        environment = popen.call_args.kwargs["env"]
        self.assertEqual(environment["GAIA_STAGE6_RUNTIME_DIAGNOSTICS"], "1")
        self.assertIn("GAIA_STAGE6_DIAGNOSTICS_PATH", environment)
        self.assertTrue(environment["GAIA_STAGE6_DIAGNOSTICS_CONFIGURATION_ID"])

    @patch("gaia.launchers.wait_for_runtime", return_value={"ok": False, "error": "сервер Gaia не ответил"})
    @patch("gaia.launchers.subprocess.Popen")
    def test_window_does_not_open_until_gaia_runtime_is_ready(self, popen, ready) -> None:
        result = launch_gaia_window("runtime-a")

        self.assertFalse(result["ok"])
        self.assertIn("сервер Gaia", result["error"])
        popen.assert_not_called()

    def test_runtime_wait_rejects_an_unrelated_http_response(self) -> None:
        with patch("gaia.launchers.urlopen") as open_url:
            open_url.return_value.__enter__.return_value.read.return_value = b'{"ready": false}'
            result = wait_for_runtime("http://127.0.0.1:8787", timeout_seconds=0.01)

        self.assertFalse(result["ok"])

    @patch("gaia.launchers.WINDOW_PROCESS")
    @patch("gaia.launchers.wait_for_runtime", return_value={"ok": True, "runtime": {"runtime_id": "runtime-a"}})
    @patch("gaia.launchers.subprocess.Popen")
    def test_repeated_window_request_reuses_the_current_window(self, popen, ready, window) -> None:
        window.poll.return_value = None
        result = launch_gaia_window("runtime-a")

        self.assertTrue(result["ok"])
        self.assertIn("уже открыто", result["message"])
        popen.assert_not_called()

    def test_system_window_registers_file_panel_delegate(self) -> None:
        script = (Path(__file__).parents[1] / "gaia" / "gaia_window.js").read_text(encoding="utf-8")
        self.assertIn("runOpenPanelWithParameters", script)
        self.assertIn("NSOpenPanel.openPanel", script)
        self.assertIn("$.GaiaFilePanelDelegate.alloc.init", script)
        self.assertNotIn("const FilePanelDelegate", script)
        self.assertIn("let filePanelDelegate = null", script)
        self.assertIn("panel.runModal()", script)
        self.assertIn("webView.setUIDelegate(filePanelDelegate)", script)

    def test_system_window_has_opt_in_runtime_file_picker_diagnostics_contract(self) -> None:
        script = (Path(__file__).parents[1] / "gaia" / "gaia_window.js").read_text(encoding="utf-8")
        for event_code in ("upload_control_pointer_received", "file_input_activation_requested", "file_input_click_event_received", "dom_file_input_click", "webkit_file_picker_request", "wkui_delegate_callback_received", "open_panel_started", "open_panel_result", "completion_handler_called", "upload_flow_started"):
            self.assertIn(f'"{event_code}"', script)
        self.assertIn("GAIA_STAGE6_RUNTIME_DIAGNOSTICS", script)
        self.assertIn("selected_url_count", script)
        self.assertNotIn("panel.URL.path", script)
        self.assertNotIn("panel.URLs.description", script)

    def test_runtime_diagnostics_delegate_exports_wk_script_message_handler_selector(self) -> None:
        script = Path(__file__).parents[1] / "gaia" / "gaia_window.js"
        result = subprocess.run(
            ["/usr/bin/osascript", "-l", "JavaScript", str(script), "--diagnostics-delegate-smoke"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.stdout.strip(), "WKScriptMessageHandler available")

    def test_file_panel_delegate_exports_wkui_open_panel_selector(self) -> None:
        script = Path(__file__).parents[1] / "gaia" / "gaia_window.js"
        result = subprocess.run(
            ["/usr/bin/osascript", "-l", "JavaScript", str(script), "--file-panel-delegate-smoke"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.stdout.strip(), "WKUIDelegate open-panel handler available")

    def test_runtime_diagnostics_jxa_writer_reaches_the_requested_jsonl(self) -> None:
        script = Path(__file__).parents[1] / "gaia" / "gaia_window.js"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            path.touch()
            environment = os.environ | {
                "GAIA_STAGE6_RUNTIME_DIAGNOSTICS": "1",
                "GAIA_STAGE6_DIAGNOSTICS_PATH": str(path),
                "GAIA_STAGE6_DIAGNOSTICS_CONFIGURATION_ID": "test-config-id",
            }
            result = subprocess.run(
                ["/usr/bin/osascript", "-l", "JavaScript", str(script), "--diagnostics-writer-smoke"],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
            event = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(result.stdout.strip(), "Stage 6 diagnostics writer available")
        self.assertIn("gaia_stage6_diagnostics:diagnostics_window_process_enabled", result.stderr)
        self.assertEqual(event["event_code"], "diagnostics_window_process_enabled")
        self.assertNotIn("/", json.dumps(event))

    @patch("gaia.launchers.launch_gaia_window", return_value={"ok": True})
    def test_gaia_module_opens_system_window(self, launch) -> None:
        self.assertEqual(launch_module("gaia"), {"ok": True})
        launch.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
