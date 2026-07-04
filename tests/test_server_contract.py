from __future__ import annotations

import io
import unittest
from email.message import Message
from types import SimpleNamespace

from gaia.server import Handler, MAX_JSON_BODY_SIZE, multipart_files, multipart_value, parse_multipart


class ServerContractTests(unittest.TestCase):
    def test_parse_multipart_reads_fields_and_files_without_cgi(self) -> None:
        boundary = "----gaia-test-boundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="project"\r\n'
            "\r\n"
            "Автопретензии\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="files"; filename="note.txt"\r\n'
            "Content-Type: text/plain\r\n"
            "\r\n"
            "hello file\r\n"
            f"--{boundary}--\r\n"
        ).encode("utf-8")
        handler = fake_handler(
            body,
            f"multipart/form-data; boundary={boundary}",
        )

        fields = parse_multipart(handler)

        self.assertEqual(multipart_value(fields, "project"), "Автопретензии")
        files = multipart_files(fields, "files")
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].filename, "note.txt")
        self.assertEqual(files[0].content, b"hello file")

    def test_read_json_rejects_oversized_body(self) -> None:
        handler = fake_handler(
            b"{}",
            "application/json",
            content_length=MAX_JSON_BODY_SIZE + 1,
        )

        with self.assertRaisesRegex(ValueError, "JSON body is too large"):
            Handler.read_json(handler)


def fake_handler(body: bytes, content_type: str, content_length: int | None = None):
    headers = Message()
    headers["Content-Type"] = content_type
    headers["Content-Length"] = str(len(body) if content_length is None else content_length)
    return SimpleNamespace(headers=headers, rfile=io.BytesIO(body))


if __name__ == "__main__":
    unittest.main()
