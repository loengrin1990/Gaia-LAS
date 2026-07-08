from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gaia.models import AnalysisPackage
from gaia.scribe_inbox import duplicate_inbox_item, index_inbox_item, list_scribe_inbox, package_inbox_item


def fake_package(project: str) -> AnalysisPackage:
    return AnalysisPackage(
        run_id="inbox-run",
        project=project,
        profile_id="general",
        profile_title="Обычный анализ",
        route="Codex/ChatGPT после ручного подтверждения",
        safe_for_codex_after_confirmation=True,
        local_fallback_required=False,
        policy_notes=[],
        memory_chars=0,
        memory_sources=[],
        evidence_plan=[],
        memory_total_sections=0,
        query_mask_status="выполнено",
        query_mask_replacements=0,
        query_mask_review=None,
        masked_query="",
        files=[],
        prompt="prompt",
        journal_path="",
        safety_audit_path="",
    )


class ScribeInboxTests(unittest.TestCase):
    def test_lists_project_files_but_skips_memory_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / "Проекты" / "Автопретензии"
            (project_dir / "Материалы").mkdir(parents=True)
            (project_dir / "Память_Graph" / "20_Decisions").mkdir(parents=True)
            (project_dir / "Материалы" / "source.md").write_text("новый источник", encoding="utf-8")
            (project_dir / "АПР - Память.md").write_text("память", encoding="utf-8")
            (project_dir / "Память_Graph" / "20_Decisions" / "decision.md").write_text("узел", encoding="utf-8")
            settings = SimpleNamespace(projects=root / "Проекты", service_docs=root / "Service")

            with (
                patch("gaia.scribe_inbox.SETTINGS", settings),
                patch("gaia.projects.SETTINGS", settings),
            ):
                items = list_scribe_inbox("Автопретензии")

        self.assertEqual([item.relative_path for item in items], ["Материалы/source.md"])

    def test_package_inbox_item_creates_package_and_marks_prepared(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / "Проекты" / "Автопретензии"
            (project_dir / "Материалы").mkdir(parents=True)
            (project_dir / "Материалы" / "source.md").write_text("новый источник", encoding="utf-8")
            settings = SimpleNamespace(projects=root / "Проекты", service_docs=root / "Service")

            with (
                patch("gaia.scribe_inbox.SETTINGS", settings),
                patch("gaia.projects.SETTINGS", settings),
                patch("gaia.scribe_inbox.create_package", return_value=fake_package("Автопретензии")),
            ):
                result = package_inbox_item("Автопретензии", "Материалы/source.md", "general")
                items = list_scribe_inbox("Автопретензии")

        self.assertEqual(result["package"]["run_id"], "inbox-run")
        self.assertEqual(result["package"]["scribe_origin"]["type"], "inbox")
        self.assertEqual(result["package"]["scribe_origin"]["relative_path"], "Материалы/source.md")
        self.assertEqual(items[0].status, "prepared")

    def test_indexed_inbox_item_is_hidden_from_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / "Проекты" / "Автопретензии"
            (project_dir / "Исходники").mkdir(parents=True)
            (project_dir / "Исходники" / "source.md").write_text("новый источник", encoding="utf-8")
            settings = SimpleNamespace(projects=root / "Проекты", service_docs=root / "Service")

            with (
                patch("gaia.scribe_inbox.SETTINGS", settings),
                patch("gaia.projects.SETTINGS", settings),
            ):
                index_inbox_item("Автопретензии", "Исходники/source.md")
                items = list_scribe_inbox("Автопретензии")

        self.assertEqual(items, [])

    def test_duplicate_inbox_item_is_hidden_from_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / "Проекты" / "Автопретензии"
            (project_dir / "Исходники").mkdir(parents=True)
            (project_dir / "Исходники" / "source.md").write_text("новый источник", encoding="utf-8")
            settings = SimpleNamespace(projects=root / "Проекты", service_docs=root / "Service")

            with (
                patch("gaia.scribe_inbox.SETTINGS", settings),
                patch("gaia.projects.SETTINGS", settings),
            ):
                duplicate_inbox_item("Автопретензии", "Исходники/source.md")
                items = list_scribe_inbox("Автопретензии")

        self.assertEqual(items, [])

    def test_source_with_existing_graph_reference_is_hidden_from_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / "Проекты" / "Автопретензии"
            source = project_dir / "Исходники" / "source.md"
            graph = project_dir / "Память_Graph" / "50_Sources" / "АПР - Source.md"
            source.parent.mkdir(parents=True)
            graph.parent.mkdir(parents=True)
            source.write_text("новый источник", encoding="utf-8")
            graph.write_text(f"Evidence: {source}\n", encoding="utf-8")
            settings = SimpleNamespace(projects=root / "Проекты", service_docs=root / "Service")

            with (
                patch("gaia.scribe_inbox.SETTINGS", settings),
                patch("gaia.projects.SETTINGS", settings),
            ):
                items = list_scribe_inbox("Автопретензии")

        self.assertEqual(items, [])

    def test_source_registry_not_mentioned_row_does_not_hide_inbox_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / "Проекты" / "Автопретензии"
            source = project_dir / "Исходники" / "source.md"
            source.parent.mkdir(parents=True)
            source.write_text("новый источник", encoding="utf-8")
            (project_dir / "АПР - Источники.md").write_text(
                "| Файл | Тип | Режим | Маскирование | Учтен в памяти | Комментарий |\n"
                "|---|---|---|---|---|---|\n"
                "| `Исходники/source.md` | файл | контекст |  | не упомянут явно |  |\n",
                encoding="utf-8",
            )
            settings = SimpleNamespace(projects=root / "Проекты", service_docs=root / "Service")

            with (
                patch("gaia.scribe_inbox.SETTINGS", settings),
                patch("gaia.projects.SETTINGS", settings),
            ):
                items = list_scribe_inbox("Автопретензии")

        self.assertEqual([item.relative_path for item in items], ["Исходники/source.md"])

    def test_source_registry_covered_row_hides_inbox_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / "Проекты" / "Автопретензии"
            source = project_dir / "Исходники" / "source.md"
            source.parent.mkdir(parents=True)
            source.write_text("новый источник", encoding="utf-8")
            (project_dir / "АПР - Источники.md").write_text(
                "| Файл | Тип | Режим | Маскирование | Учтен в памяти | Комментарий |\n"
                "|---|---|---|---|---|---|\n"
                "| `Исходники/source.md` | файл | контекст | выполнено | покрыт в памяти: [[АПР - Source]] |  |\n",
                encoding="utf-8",
            )
            settings = SimpleNamespace(projects=root / "Проекты", service_docs=root / "Service")

            with (
                patch("gaia.scribe_inbox.SETTINGS", settings),
                patch("gaia.projects.SETTINGS", settings),
            ):
                items = list_scribe_inbox("Автопретензии")

        self.assertEqual(items, [])

    def test_source_registry_reference_only_row_hides_inbox_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / "Проекты" / "Автопретензии"
            source = project_dir / "Исходники" / "source.md"
            source.parent.mkdir(parents=True)
            source.write_text("новый источник", encoding="utf-8")
            (project_dir / "АПР - Источники.md").write_text(
                "| Файл | Тип | Режим | Маскирование | Учтен в памяти | Комментарий |\n"
                "|---|---|---|---|---|---|\n"
                "| `Исходники/source.md` | файл | только источник |  | не упомянут явно |  |\n",
                encoding="utf-8",
            )
            settings = SimpleNamespace(projects=root / "Проекты", service_docs=root / "Service")

            with (
                patch("gaia.scribe_inbox.SETTINGS", settings),
                patch("gaia.projects.SETTINGS", settings),
            ):
                items = list_scribe_inbox("Автопретензии")

        self.assertEqual(items, [])

    def test_legacy_unprefixed_source_summary_path_hides_prefixed_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / "Проекты" / "Автопретензии"
            source = project_dir / "Исходники" / "АПР - source.md"
            graph = project_dir / "Память_Graph" / "50_Sources" / "АПР - Source.md"
            source.parent.mkdir(parents=True)
            graph.parent.mkdir(parents=True)
            (project_dir / ".gaia-project.json").write_text('{"code":"АПР","title":"Автопретензии"}', encoding="utf-8")
            source.write_text("новый источник", encoding="utf-8")
            graph.write_text('---\ntype: source_summary\nsource: "Исходники/source.md"\n---\n', encoding="utf-8")
            settings = SimpleNamespace(projects=root / "Проекты", service_docs=root / "Service")

            with (
                patch("gaia.scribe_inbox.SETTINGS", settings),
                patch("gaia.projects.SETTINGS", settings),
            ):
                items = list_scribe_inbox("Автопретензии")

        self.assertEqual(items, [])

    def test_source_map_legacy_path_does_not_hide_prefixed_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / "Проекты" / "Автопретензии"
            source = project_dir / "Исходники" / "АПР - source.md"
            graph = project_dir / "Память_Graph" / "50_Sources" / "АПР - Карта источников.md"
            source.parent.mkdir(parents=True)
            graph.parent.mkdir(parents=True)
            (project_dir / ".gaia-project.json").write_text('{"code":"АПР","title":"Автопретензии"}', encoding="utf-8")
            source.write_text("новый источник", encoding="utf-8")
            graph.write_text("---\ntype: source_map\n---\n- `Исходники/source.md`\n", encoding="utf-8")
            settings = SimpleNamespace(projects=root / "Проекты", service_docs=root / "Service")

            with (
                patch("gaia.scribe_inbox.SETTINGS", settings),
                patch("gaia.projects.SETTINGS", settings),
            ):
                items = list_scribe_inbox("Автопретензии")

        self.assertEqual([item.relative_path for item in items], ["Исходники/АПР - source.md"])

    def test_low_signal_source_with_covered_content_title_is_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / "Проекты" / "Автопретензии"
            source = project_dir / "Исходники" / "999999999_deadbeefdeadbeefdeadbeef.md"
            graph = project_dir / "Память_Graph" / "50_Sources" / "АПР - Интеграционный контур.md"
            source.parent.mkdir(parents=True)
            graph.parent.mkdir(parents=True)
            (project_dir / ".gaia-project.json").write_text('{"code":"АПР","title":"Автопретензии"}', encoding="utf-8")
            source.write_text(
                "5. Интеграционный контур\n"
                "Документ описывает обмен данными между системами.",
                encoding="utf-8",
            )
            graph.write_text("# АПР - Интеграционный контур\n\nИнтеграционный контур: source-summary.\n", encoding="utf-8")
            settings = SimpleNamespace(projects=root / "Проекты", service_docs=root / "Service", obsidian_work=root)

            with (
                patch("gaia.scribe_inbox.SETTINGS", settings),
                patch("gaia.projects.SETTINGS", settings),
                patch("gaia.extraction.SETTINGS", settings),
            ):
                items = list_scribe_inbox("Автопретензии")

        self.assertEqual(items, [])

    def test_auto_extracted_text_file_is_hidden_from_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / "Проекты" / "Автопретензии"
            project_dir.mkdir(parents=True)
            (project_dir / "АПР - Автоизвлеченный текст.md").write_text("служебный текст", encoding="utf-8")
            settings = SimpleNamespace(projects=root / "Проекты", service_docs=root / "Service")

            with (
                patch("gaia.scribe_inbox.SETTINGS", settings),
                patch("gaia.projects.SETTINGS", settings),
            ):
                items = list_scribe_inbox("Автопретензии")

        self.assertEqual(items, [])


if __name__ == "__main__":
    unittest.main()
