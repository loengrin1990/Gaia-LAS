from __future__ import annotations

import unittest

from gaia.server import api_error_payload


class ApiContractTests(unittest.TestCase):
    def test_api_error_payload_has_stable_shape(self) -> None:
        payload = api_error_payload(
            "job_not_ready",
            "job is not ready",
            {"job_id": "abc"},
            trace_id="gaia-test",
        )

        self.assertEqual(payload["error"]["code"], "job_not_ready")
        self.assertEqual(payload["error"]["message"], "job is not ready")
        self.assertEqual(payload["error"]["details"], {"job_id": "abc"})
        self.assertEqual(payload["error"]["trace_id"], "gaia-test")

    def test_api_error_payload_generates_trace_id(self) -> None:
        payload = api_error_payload("not_found", "not found")

        self.assertRegex(payload["error"]["trace_id"], r"^gaia-[0-9a-f]{12}$")


if __name__ == "__main__":
    unittest.main()
