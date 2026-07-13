from __future__ import annotations

import unittest
from unittest.mock import patch

from gaia.launchers import launch_gaia_window, launch_module


class LauncherTests(unittest.TestCase):
    @patch("gaia.launchers.subprocess.Popen")
    def test_gaia_window_uses_system_webkit_launcher(self, popen) -> None:
        result = launch_gaia_window()

        self.assertTrue(result["ok"])
        command = popen.call_args.args[0]
        self.assertEqual(command[:3], ["/usr/bin/osascript", "-l", "JavaScript"])
        self.assertIn("gaia_window.js", command[3])
        self.assertTrue(command[4].startswith("http://"))

    @patch("gaia.launchers.launch_gaia_window", return_value={"ok": True})
    def test_gaia_module_opens_system_window(self, launch) -> None:
        self.assertEqual(launch_module("gaia"), {"ok": True})
        launch.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
