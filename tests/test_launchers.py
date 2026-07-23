from __future__ import annotations

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

    @patch("gaia.launchers.launch_gaia_window", return_value={"ok": True})
    def test_gaia_module_opens_system_window(self, launch) -> None:
        self.assertEqual(launch_module("gaia"), {"ok": True})
        launch.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
