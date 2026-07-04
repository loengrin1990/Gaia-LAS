from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gaia import extraction, masking


class ExternalImportTests(unittest.TestCase):
    def test_privacy_masker_loads_from_current_settings_at_call_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "privacy_masker.py").write_text(
                "class PrivacyMasker:\n"
                "    pass\n",
                encoding="utf-8",
            )
            settings = SimpleNamespace(obsidian_work=work)

            with patch("gaia.masking.SETTINGS", settings):
                privacy_masker = masking.load_privacy_masker()
            if str(work) in masking.sys.path:
                masking.sys.path.remove(str(work))

        self.assertIsNotNone(privacy_masker)
        self.assertEqual(privacy_masker.__name__, "PrivacyMasker")

    def test_extract_text_loads_from_current_settings_at_call_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "update_project_sources.py").write_text(
                "def extract_text(path):\n"
                "    return 'text:' + path.name, 'ok'\n",
                encoding="utf-8",
            )
            settings = SimpleNamespace(obsidian_work=work)

            with patch("gaia.extraction.SETTINGS", settings):
                result = extraction.extract_text(work / "source.pdf")
            if str(work) in extraction.sys.path:
                extraction.sys.path.remove(str(work))

        self.assertEqual(result, ("text:source.pdf", "ok"))


if __name__ == "__main__":
    unittest.main()
