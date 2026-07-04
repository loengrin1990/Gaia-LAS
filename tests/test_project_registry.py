from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gaia.memory import INDEX_CACHE, select_project_memory
from gaia.projects import (
    ProjectRegistryError,
    create_group,
    create_project,
    list_groups,
    list_projects,
    project_record,
    repair_project,
    update_project,
    validate_project,
)


class ProjectRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        INDEX_CACHE.clear()

    def test_creates_group_and_project_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = test_settings(tmp)
            with patch("gaia.projects.SETTINGS", settings):
                group = create_group("DEV", "Девелопмент", "Общие регламенты.")
                project = create_project("АПР", "Автопретензии", "DEV")
                validation = validate_project("Автопретензии")

        self.assertEqual(group.code, "DEV")
        self.assertEqual(project.group_code, "DEV")
        self.assertTrue(validation["ok"])
        self.assertTrue(Path(project.memory_path).name.endswith("АПР - Память.md"))
        self.assertIn("DEV - Контекст.md", group.context_path)

    def test_repair_restores_missing_project_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = test_settings(tmp)
            with patch("gaia.projects.SETTINGS", settings):
                project = create_project("ХО", "Хаб Обращений")
                Path(project.sources_path).unlink()
                broken = validate_project("Хаб Обращений")
                repaired = repair_project("Хаб Обращений")
                repaired_sources_exists = Path(repaired.sources_path).exists()

        self.assertFalse(broken["ok"])
        self.assertTrue(repaired_sources_exists)

    def test_validate_project_returns_health_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = test_settings(tmp)
            settings.project_health_llm = False
            with patch("gaia.projects.SETTINGS", settings):
                create_project("ТСТ", "Тест")
                source = settings.projects / "Тест" / "Исходники" / "source.txt"
                source.write_text("source", encoding="utf-8")
                sources = settings.projects / "Тест" / "ТСТ - Источники.md"
                sources.write_text("| Файл | Учтен в памяти |\n|---|---|\n| `source.txt` | не упомянут явно |\n", encoding="utf-8")
                validation = validate_project("Тест")

        self.assertIn("health_summary", validation)
        self.assertIn("diagnostics", validation)
        self.assertTrue(any(item["title"] == "Мало source-summary узлов" for item in validation["diagnostics"]))

    def test_group_context_is_inherited_by_lore(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = test_settings(tmp)
            with patch("gaia.projects.SETTINGS", settings):
                create_group("REG", "Регламенты")
                create_project("ТСТ", "Тестовый проект", "REG")
                group_context = settings.vault / "Контексты" / "Группы" / "REG" / "REG - Контекст.md"
                group_context.write_text(
                    "# Регламенты\n\n"
                    "## Шаблон претензии\n"
                    "Все проекты группы используют единый шаблон претензии и правила качества.\n",
                    encoding="utf-8",
                )
                project_memory = settings.projects / "Тестовый проект" / "ТСТ - Память.md"
                project_memory.write_text(
                    "# Тестовый проект\n\n"
                    "## Быстрый вход\n"
                    "Проектная рамка без описания шаблона.\n",
                    encoding="utf-8",
                )
                with patch("gaia.memory.SETTINGS", settings):
                    selection = select_project_memory("Тестовый проект", "Какой шаблон претензии использовать?")

        self.assertIn("единый шаблон претензии", selection.text)
        self.assertEqual(selection.group_code, "REG")
        self.assertTrue(any(source.scope == "group" for source in selection.sources))

    def test_lists_projects_and_groups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = test_settings(tmp)
            with patch("gaia.projects.SETTINGS", settings):
                create_group("CRM", "CRM")
                create_project("CRM1", "Хаб Обращений", "CRM")
                groups = list_groups()
                projects = list_projects()

        self.assertEqual([group.code for group in groups], ["CRM"])
        self.assertEqual(projects[0].group_title, "CRM")

    def test_rename_conflict_does_not_mutate_project_meta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = test_settings(tmp)
            with patch("gaia.projects.SETTINGS", settings):
                create_project("ONE", "Первый")
                create_project("TWO", "Второй")
                with self.assertRaises(ProjectRegistryError):
                    update_project("Первый", {"title": "Второй"})
                record = project_record(settings.projects / "Первый")

        self.assertEqual(record.title, "Первый")

    def test_update_project_code_renames_prefixed_files_and_references(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = test_settings(tmp)
            with patch("gaia.projects.SETTINGS", settings):
                create_project("АВ", "Алгора+Вира")
                graph_file = settings.projects / "Алгора+Вира" / "Память_Graph" / "10_Branches" / "АВ - Договоры.md"
                graph_file.write_text(
                    "# АВ - Договоры\n\nСвязь: [[АВ - Индекс памяти]].\n",
                    encoding="utf-8",
                )
                updated = update_project("Алгора+Вира", {"code": "ДП", "title": "Договорной процесс"})
                project_dir = settings.projects / "Договорной процесс"

                old_files = sorted(item.name for item in project_dir.rglob("АВ - *"))
                new_files = sorted(item.name for item in project_dir.rglob("ДП - *"))
                renamed_graph_text = (project_dir / "Память_Graph" / "10_Branches" / "ДП - Договоры.md").read_text(encoding="utf-8")

        self.assertEqual(updated.code, "ДП")
        self.assertEqual(updated.title, "Договорной процесс")
        self.assertEqual(old_files, [])
        self.assertIn("ДП - Память.md", new_files)
        self.assertIn("ДП - Договоры.md", new_files)
        self.assertIn("# ДП - Договоры", renamed_graph_text)
        self.assertIn("[[ДП - Индекс памяти]]", renamed_graph_text)


def test_settings(tmp: str) -> SimpleNamespace:
    root = Path(tmp)
    vault = root / "Vault"
    projects = vault / "Проекты"
    projects.mkdir(parents=True)
    return SimpleNamespace(projects=projects, vault=vault)


if __name__ == "__main__":
    unittest.main()
