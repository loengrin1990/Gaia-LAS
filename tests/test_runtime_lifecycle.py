from __future__ import annotations

import unittest
from unittest.mock import patch

from gaia.runtime import runtime_fingerprint
from gaia.server import main
from gaia.ui import INDEX_HTML


class RuntimeLifecycleTests(unittest.TestCase):
    def test_runtime_fingerprint_is_safe_and_stable_within_process(self) -> None:
        first = runtime_fingerprint()
        second = runtime_fingerprint()

        self.assertEqual(first["runtime_id"], second["runtime_id"])
        self.assertTrue(first["ready"])
        self.assertIn("pid", first)
        self.assertIn("git_commit", first)
        self.assertNotIn("storage", first)
        self.assertNotIn("projects", first)

    @patch("gaia.server.launch_gaia_window")
    @patch("gaia.server.ThreadingHTTPServer", side_effect=OSError("port busy"))
    @patch("gaia.server.ensure_dirs")
    def test_busy_port_never_opens_a_window(self, ensure_dirs, server, open_window) -> None:
        self.assertEqual(main(open_window=True), 2)
        open_window.assert_not_called()

    def test_frontend_blocks_an_orphaned_or_replaced_runtime(self) -> None:
        self.assertIn("/api/runtime", INDEX_HTML)
        self.assertIn("expectedRuntimeId", INDEX_HTML)
        self.assertIn("Связь с Gaia потеряна", INDEX_HTML)
        self.assertIn("document.body.innerHTML = ''", INDEX_HTML)


if __name__ == "__main__":
    unittest.main()
