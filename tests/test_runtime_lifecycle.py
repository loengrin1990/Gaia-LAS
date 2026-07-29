from __future__ import annotations

import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from gaia.runtime import runtime_fingerprint
from gaia.server import Handler, main
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

    def test_runtime_response_survives_closed_request_log_stdout(self) -> None:
        class ClosedStdout:
            def write(self, _value: str) -> int:
                raise BrokenPipeError("closed")

            def flush(self) -> None:
                raise BrokenPipeError("closed")

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch("sys.stdout", ClosedStdout()):
                for _ in range(20):
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
                    connection.request("GET", "/api/runtime")
                    response = connection.getresponse()
                    payload = json.loads(response.read())
                    connection.close()
                    self.assertEqual(response.status, 200)
                    self.assertTrue(payload["ready"])
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
