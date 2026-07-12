from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gaia.transcription import transcribe_file


class RunningProcess:
    def __init__(self) -> None:
        self.returncode = None
        self.terminated = False

    def poll(self):
        return None if not self.terminated else self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout=None) -> int:
        return self.returncode or 0


class TranscriptionTests(unittest.TestCase):
    def test_cancelled_transcription_terminates_process(self) -> None:
        event = threading.Event()
        event.set()
        process = RunningProcess()
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "input.wav"
            media.write_bytes(b"audio")
            with patch("gaia.transcription.transcriber_path", return_value=media), patch(
                "gaia.transcription.subprocess.Popen", return_value=process
            ), patch("gaia.transcription.SETTINGS", SimpleNamespace(transcription_timeout_seconds=600)):
                transcript, status = transcribe_file(media, Path(tmp), cancel_event=event)

        self.assertEqual(transcript, "")
        self.assertIn("отменена", status)
        self.assertTrue(process.terminated)


if __name__ == "__main__":
    unittest.main()
