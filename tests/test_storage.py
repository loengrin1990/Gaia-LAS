from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gaia.storage import atomic_write_text


class StorageTests(unittest.TestCase):
    def test_atomic_write_replaces_full_file_contents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("old", encoding="utf-8")
            atomic_write_text(path, "new")

            self.assertEqual(path.read_text(encoding="utf-8"), "new")
            self.assertFalse(any(item.name.startswith(".state.json.") for item in path.parent.iterdir()))


if __name__ == "__main__":
    unittest.main()
