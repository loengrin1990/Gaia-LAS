from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gaia.memory import INDEX_CACHE, select_project_memory
from gaia.rebuild import rebuild_prompt


class RebuildPromptTests(unittest.TestCase):
    def setUp(self) -> None:
        INDEX_CACHE.clear()

    def test_rebuild_prompt_uses_selected_lore_sections_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp) / "Проекты"
            service_docs = Path(tmp) / "Сервисы" / "Gaia Local Analytics"
            project_dir = projects / "Тест"
            project_dir.mkdir(parents=True)
            service_docs.mkdir(parents=True)
            (project_dir / "Память.md").write_text(
                "## Релевантный раздел\n"
                "Договоры и претензии клиента.\n\n"
                "## Нерелевантный раздел\n"
                "Внутренняя заметка, которую аналитик исключает.\n",
                encoding="utf-8",
            )
            (service_docs / "Память.md").write_text("## Gaia docs\nНе проектная память.", encoding="utf-8")

            settings = SimpleNamespace(projects=projects, vault=Path(tmp) / "Vault")
            with patch("gaia.memory.SETTINGS", settings), patch("gaia.projects.SETTINGS", settings):
                selection = select_project_memory("Тест", "раздел", max_sections=2)
                keep = selection.sources[0]
                rebuilt = rebuild_prompt(package_fixture(selection.sources), [keep.id])

        self.assertIn("Релевантный раздел", rebuilt["prompt"])
        self.assertNotIn("Нерелевантный раздел", rebuilt["prompt"])
        self.assertNotIn("Gaia docs", rebuilt["prompt"])
        self.assertEqual([source["id"] for source in rebuilt["memory_sources"]], [keep.id])

    def test_unknown_lore_source_id_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            rebuild_prompt(package_fixture([]), ["unknown"])


def package_fixture(memory_sources: list) -> dict:
    return {
        "project": "Тест",
        "profile_id": "general",
        "masked_query": "Запрос без ПД.",
        "files": [],
        "memory_sources": [source.__dict__ for source in memory_sources],
        "memory_total_sections": len(memory_sources),
    }


if __name__ == "__main__":
    unittest.main()
