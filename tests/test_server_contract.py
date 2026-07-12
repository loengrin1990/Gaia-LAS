from __future__ import annotations

import io
import unittest
from email.message import Message
from types import SimpleNamespace

from gaia.server import Handler, MAX_JSON_BODY_SIZE, MAX_MULTIPART_BODY_SIZE, MAX_UPLOAD_FILE_SIZE, MAX_UPLOAD_FILES, MULTIPART_READ_CHUNK_SIZE, SESSION_COOKIE_NAME, SESSION_TOKEN, multipart_files, multipart_value, mutation_is_authorized, parse_multipart


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

    def test_server_supports_cancelling_analyze_jobs(self) -> None:
        self.assertIn("cancel", Handler.handle_job_action.__code__.co_consts)

    def test_mutating_api_requires_loopback_origin_and_session_cookie(self) -> None:
        headers = Message()
        headers["Host"] = "127.0.0.1:8787"
        headers["Origin"] = "http://127.0.0.1:8787"
        headers["Cookie"] = f"{SESSION_COOKIE_NAME}={SESSION_TOKEN}"
        request = SimpleNamespace(headers=headers, client_address=("127.0.0.1", 50000))

        self.assertTrue(mutation_is_authorized(request))
        del headers["Origin"]
        headers["Origin"] = "https://example.com"
        self.assertFalse(mutation_is_authorized(request))

    def test_upload_limits_are_bounded(self) -> None:
        self.assertLessEqual(MAX_MULTIPART_BODY_SIZE, 25_000_000)
        self.assertLess(MAX_UPLOAD_FILE_SIZE, MAX_MULTIPART_BODY_SIZE)
        self.assertEqual(MAX_UPLOAD_FILES, 8)

    def test_parse_multipart_reads_body_in_bounded_chunks(self) -> None:
        boundary = "----gaia-streaming-boundary"
        file_content = b"x" * (MULTIPART_READ_CHUNK_SIZE * 2)
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="files"; filename="large.txt"\r\n'
            "Content-Type: text/plain\r\n\r\n"
        ).encode("ascii") + file_content + f"\r\n--{boundary}--\r\n".encode("ascii")
        stream = CappedReadStream(body)
        handler = SimpleNamespace(
            headers=headers_for(f"multipart/form-data; boundary={boundary}", len(body)),
            rfile=stream,
        )

        fields = parse_multipart(handler)

        self.assertEqual(multipart_files(fields, "files")[0].content, file_content)
        self.assertLessEqual(stream.max_read, MULTIPART_READ_CHUNK_SIZE)


def fake_handler(body: bytes, content_type: str, content_length: int | None = None):
    headers = headers_for(content_type, len(body) if content_length is None else content_length)
    return SimpleNamespace(headers=headers, rfile=io.BytesIO(body))


def headers_for(content_type: str, content_length: int) -> Message:
    headers = Message()
    headers["Content-Type"] = content_type
    headers["Content-Length"] = str(content_length)
    return headers


class CappedReadStream(io.BytesIO):
    def __init__(self, body: bytes) -> None:
        super().__init__(body)
        self.max_read = 0

    def read(self, size: int = -1) -> bytes:
        self.max_read = max(self.max_read, size)
        return super().read(size)


if __name__ == "__main__":
    unittest.main()
