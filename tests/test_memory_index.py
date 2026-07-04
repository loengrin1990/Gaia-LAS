from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gaia.memory import INDEX_CACHE, project_names, select_project_memory


class LoreMemoryIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        INDEX_CACHE.clear()

    def test_selects_relevant_sections_and_reports_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp) / "Проекты"
            service_docs = Path(tmp) / "Сервисы" / "Gaia Local Analytics"
            project_dir = projects / "Тест"
            project_dir.mkdir(parents=True)
            service_docs.mkdir(parents=True)
            (project_dir / "Память.md").write_text(
                "# Тест\n\n"
                "## Интеграция договоров\n"
                "Договоры, статусы и события интеграции с БФ.\n\n"
                "## Риски ПД\n"
                "Персональные данные нельзя отправлять во внешний контур.\n\n"
                "## Отчетность\n"
                "Метрики и дашборды.\n",
                encoding="utf-8",
            )
            (service_docs / "Память.md").write_text("## Gaia docs\nСервисная документация", encoding="utf-8")

            settings = SimpleNamespace(projects=projects, vault=Path(tmp) / "Vault")
            with patch("gaia.memory.SETTINGS", settings), patch("gaia.projects.SETTINGS", settings):
                selection = select_project_memory("Тест", "Проверь договоры и события интеграции", "Риск-анализ")
                names = project_names()

        self.assertEqual(names, ["Тест"])
        self.assertIn("Интеграция договоров", selection.text)
        self.assertTrue(selection.sources)
        self.assertTrue(selection.sources[0].id)
        self.assertEqual(selection.sources[0].project, "Тест")
        self.assertIn("договоры", selection.sources[0].matched_terms)
        self.assertNotIn("Gaia docs", selection.text)

    def test_rejects_path_traversal_project_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = SimpleNamespace(projects=Path(tmp) / "Проекты", vault=Path(tmp) / "Vault")
            settings.projects.mkdir(parents=True)
            with patch("gaia.memory.SETTINGS", settings), patch("gaia.projects.SETTINGS", settings):
                selection = select_project_memory("../Сервисы/Gaia Local Analytics", "Gaia")

        self.assertEqual(selection.text, "")
        self.assertEqual(selection.sources, [])

    def test_indexes_graph_memory_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp) / "Проекты"
            project_dir = projects / "Тест"
            graph_dir = project_dir / "Память_Graph" / "00_Core"
            graph_dir.mkdir(parents=True)
            (project_dir / "Память.md").write_text(
                "# Тест\n\n"
                "## Быстрый вход\n"
                "[[Архитектурные решения]]\n",
                encoding="utf-8",
            )
            (graph_dir / "Архитектурные решения.md").write_text(
                "---\n"
                "type: decision\n"
                "priority: 95\n"
                "---\n\n"
                "# Архитектурные решения\n\n"
                "Техзор передает нарушения в 1С Битфинанс в односторонней интеграции.\n",
                encoding="utf-8",
            )

            settings = SimpleNamespace(projects=projects, vault=Path(tmp) / "Vault")
            with patch("gaia.memory.SETTINGS", settings), patch("gaia.projects.SETTINGS", settings):
                selection = select_project_memory("Тест", "односторонняя интеграция Техзор 1С")

        self.assertGreaterEqual(selection.total_sections, 2)
        self.assertIn("Архитектурные решения", selection.text)
        self.assertTrue(any("Память_Graph" in source.path for source in selection.sources))

    def test_discovers_prefixed_project_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp) / "Проекты"
            project_dir = projects / "Тест"
            project_dir.mkdir(parents=True)
            (project_dir / "ТСТ - Память.md").write_text(
                "# Тест\n\n"
                "## Карта памяти\n"
                "Проектная память с кодовым префиксом.\n",
                encoding="utf-8",
            )

            settings = SimpleNamespace(projects=projects, vault=Path(tmp) / "Vault")
            with patch("gaia.memory.SETTINGS", settings), patch("gaia.projects.SETTINGS", settings):
                names = project_names()
                selection = select_project_memory("Тест", "кодовый префикс")

        self.assertEqual(names, ["Тест"])
        self.assertIn("кодовым префиксом", selection.text)

    def test_prefers_prefixed_memory_over_legacy_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp) / "Проекты"
            project_dir = projects / "Тест"
            project_dir.mkdir(parents=True)
            (project_dir / "Память.md").write_text(
                "# Старый файл\n\n"
                "legacy marker should not be selected.\n",
                encoding="utf-8",
            )
            (project_dir / "ТСТ - Память.md").write_text(
                "# Тест\n\n"
                "## Новая карта\n"
                "prefixed marker should be selected.\n",
                encoding="utf-8",
            )

            settings = SimpleNamespace(projects=projects, vault=Path(tmp) / "Vault")
            with patch("gaia.memory.SETTINGS", settings), patch("gaia.projects.SETTINGS", settings):
                selection = select_project_memory("Тест", "prefixed marker")

        self.assertIn("prefixed marker", selection.text)
        self.assertNotIn("legacy marker", selection.text)

    def test_source_summary_anchors_request_context_and_related_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp) / "Проекты"
            project_dir = projects / "Автопретензии"
            source_dir = project_dir / "Память_Graph" / "50_Sources"
            branch_dir = project_dir / "Память_Graph" / "10_Branches"
            risk_dir = project_dir / "Память_Graph" / "40_Risks"
            core_dir = project_dir / "Память_Graph" / "00_Core"
            source_dir.mkdir(parents=True)
            branch_dir.mkdir(parents=True)
            risk_dir.mkdir(parents=True)
            core_dir.mkdir(parents=True)
            (project_dir / "АПР - Память.md").write_text(
                "# Автопретензии\n\n"
                "## Быстрый вход\n"
                "Общий контекст проекта.\n",
                encoding="utf-8",
            )
            (core_dir / "АПР - Ядро проекта.md").write_text(
                "# АПР - Ядро проекта\n\n"
                "Автопретензии автоматизируют претензионную работу.\n",
                encoding="utf-8",
            )
            (source_dir / "АПР - МВП2 расчет компенсаций.md").write_text(
                "# АПР - МВП2 расчет компенсаций\n\n"
                "MVP2 описывает ведомость расчета компенсаций, роли и ИИ-агент цен.\n"
                "Связи: [[АПР - Расчетный контур и ИИ цены]], [[АПР - Риски проекта]].\n",
                encoding="utf-8",
            )
            (source_dir / "АПР - Отчет по переписке исполнителя.md").write_text(
                "# АПР - Отчет по переписке исполнителя\n\n"
                "Общий отчет по backend, OCR и инфраструктуре без сведений о MVP2.\n",
                encoding="utf-8",
            )
            (branch_dir / "АПР - Расчетный контур и ИИ цены.md").write_text(
                "# АПР - Расчетный контур и ИИ цены\n\n"
                "ИИ-агент ищет цены для ведомости компенсаций.\n",
                encoding="utf-8",
            )
            (risk_dir / "АПР - Риски проекта.md").write_text(
                "# АПР - Риски проекта\n\n"
                "Для MVP2 есть риск неполной приемки расчетного контура.\n",
                encoding="utf-8",
            )

            settings = SimpleNamespace(projects=projects, vault=Path(tmp) / "Vault")
            with patch("gaia.memory.SETTINGS", settings), patch("gaia.projects.SETTINGS", settings):
                selection = select_project_memory("Автопретензии", "Дай краткую сводку по мвп2 автопретензий")

        headings = [source.heading for source in selection.sources]
        self.assertIn("АПР - МВП2 расчет компенсаций", headings)
        self.assertIn("АПР - Расчетный контур и ИИ цены", headings)
        self.assertIn("АПР - Риски проекта", headings)
        self.assertLess(
            headings.index("АПР - МВП2 расчет компенсаций"),
            headings.index("АПР - Риски проекта"),
        )

    def test_unknown_specific_topic_returns_no_data_instead_of_generic_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp) / "Проекты"
            project_dir = projects / "Автопретензии"
            graph_dir = project_dir / "Память_Graph" / "50_Sources"
            graph_dir.mkdir(parents=True)
            (project_dir / "АПР - Память.md").write_text(
                "# Автопретензии\n\n"
                "## Быстрый вход\n"
                "Общий контекст проекта без неизвестной темы.\n",
                encoding="utf-8",
            )
            (graph_dir / "АПР - МВП2 расчет компенсаций.md").write_text(
                "# АПР - МВП2 расчет компенсаций\n\n"
                "MVP2 описывает расчет компенсаций.\n",
                encoding="utf-8",
            )

            settings = SimpleNamespace(projects=projects, vault=Path(tmp) / "Vault")
            with patch("gaia.memory.SETTINGS", settings), patch("gaia.projects.SETTINGS", settings):
                selection = select_project_memory("Автопретензии", "Что известно про мвп4 автопретензий")

        self.assertIn("Проверка покрытия Lore", selection.text)
        self.assertIn("нет подтвержденного контекста", selection.text)
        self.assertNotIn("MVP2 описывает расчет компенсаций", selection.text)

    def test_short_system_acronyms_anchor_field_mapping_queries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp) / "Проекты"
            project_dir = projects / "Договорной процесс"
            branch_dir = project_dir / "Память_Graph" / "10_Branches"
            branch_dir.mkdir(parents=True)
            (project_dir / "ДП - Память.md").write_text(
                "# Договорной процесс\n\n"
                "## Быстрый вход\n"
                "Проект интеграции ДО и БФ.\n",
                encoding="utf-8",
            )
            (branch_dir / "ДП - Рабочий маппинг создания проекта договора в БФ.md").write_text(
                "# ДП - Рабочий маппинг создания проекта договора в БФ\n\n"
                "| Поле БФ | Источник в ДО | Правило |\n"
                "|---|---|---|\n"
                "| `Организация` | `Организация` | НСИ должна быть сопоставлена с БФ |\n"
                "| `СтавкаНДС` | `СтавкаНДС` | Передавать при наличии в ДО |\n",
                encoding="utf-8",
            )

            settings = SimpleNamespace(projects=projects, vault=Path(tmp) / "Vault")
            with patch("gaia.memory.SETTINGS", settings), patch("gaia.projects.SETTINGS", settings):
                selection = select_project_memory("Договорной процесс", "какие поля напрямую совпадают в БФ и ДО?")

        self.assertNotIn("Проверка покрытия Lore", selection.text)
        self.assertIn("Рабочий маппинг", selection.text)
        self.assertTrue(selection.sources)
        self.assertEqual(selection.sources[0].heading, "ДП - Рабочий маппинг создания проекта договора в БФ")

    def test_query_rewrite_can_expand_retrieval_terms_without_answering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp) / "Проекты"
            project_dir = projects / "Договорной процесс"
            branch_dir = project_dir / "Память_Graph" / "10_Branches"
            branch_dir.mkdir(parents=True)
            (project_dir / "ДП - Память.md").write_text("# Договорной процесс\n\n## Быстрый вход\nКонтекст.\n", encoding="utf-8")
            (branch_dir / "ДП - Рабочий маппинг.md").write_text(
                "# ДП - Рабочий маппинг\n\n"
                "| Поле БФ | Источник в ДО | Правило |\n"
                "|---|---|---|\n"
                "| `Организация` | `Организация` | Сопоставить НСИ |\n",
                encoding="utf-8",
            )
            settings = SimpleNamespace(
                projects=projects,
                vault=Path(tmp) / "Vault",
                lore_query_rewrite=True,
                lore_query_rewrite_timeout_seconds=1,
                lore_semantic_rerank=False,
                lore_gap_detector=False,
            )
            with (
                patch("gaia.memory.SETTINGS", settings),
                patch("gaia.projects.SETTINGS", settings),
                patch("gaia.memory.rewrite_query_terms_with_local_llm", return_value=["поля", "БФ", "ДО", "маппинг"]),
            ):
                selection = select_project_memory("Договорной процесс", "что у них одинаковое?")

        self.assertNotIn("Проверка покрытия Lore", selection.text)
        self.assertEqual(selection.sources[0].heading, "ДП - Рабочий маппинг")

    def test_gap_detector_adds_diagnostic_block_only_after_sources_are_selected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp) / "Проекты"
            project_dir = projects / "Тест"
            project_dir.mkdir(parents=True)
            (project_dir / "Память.md").write_text(
                "# Тест\n\n"
                "## Интеграция\n"
                "Договоры передаются в БФ.\n",
                encoding="utf-8",
            )
            settings = SimpleNamespace(
                projects=projects,
                vault=Path(tmp) / "Vault",
                lore_query_rewrite=False,
                lore_semantic_rerank=False,
                lore_gap_detector=True,
                lore_gap_detector_timeout_seconds=1,
            )
            with (
                patch("gaia.memory.SETTINGS", settings),
                patch("gaia.projects.SETTINGS", settings),
                patch("gaia.memory.detect_gap_with_local_llm", return_value={
                    "status": "partial",
                    "notes": ["Есть общий контекст, но нет точной таблицы."],
                    "missing_terms": ["таблица соответствия"],
                }),
            ):
                selection = select_project_memory("Тест", "Проверь договоры в БФ")

        self.assertIn("Диагностика покрытия Lore", selection.text)
        self.assertIn("Статус покрытия: partial.", selection.text)
        self.assertIn("не дополнительный источник фактов", selection.text)

    def test_unknown_non_mvp_topic_returns_no_data_too(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp) / "Проекты"
            project_dir = projects / "Тест"
            project_dir.mkdir(parents=True)
            (project_dir / "Память.md").write_text(
                "# Тест\n\n"
                "## Интеграция договоров\n"
                "Договоры и события интеграции с БФ.\n",
                encoding="utf-8",
            )

            settings = SimpleNamespace(projects=projects, vault=Path(tmp) / "Vault")
            with patch("gaia.memory.SETTINGS", settings), patch("gaia.projects.SETTINGS", settings):
                selection = select_project_memory("Тест", "Что известно про квантовый бюджет")

        self.assertIn("Проверка покрытия Lore", selection.text)
        self.assertIn("квантовый", selection.text)
        self.assertNotIn("Договоры и события", selection.text)

    def test_primary_architecture_source_beats_correspondence_report_for_pii_storage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp) / "Проекты"
            project_dir = projects / "Автопретензии"
            source_dir = project_dir / "Память_Graph" / "50_Sources"
            raw_dir = project_dir / "Исходники"
            source_dir.mkdir(parents=True)
            raw_dir.mkdir(parents=True)
            (project_dir / "АПР - Память.md").write_text(
                "# Автопретензии\n\n"
                "## Ядро проекта\n"
                "Общий контекст без деталей хранения ПДн.\n",
                encoding="utf-8",
            )
            (source_dir / "АПР - Отчет по переписке исполнителя.md").write_text(
                "# АПР - Отчет по переписке исполнителя\n\n"
                "В переписке есть открытые вопросы персональных данных и хранения.\n",
                encoding="utf-8",
            )
            passport = raw_dir / "АПР - Паспорт системы.pdf"
            passport.write_bytes(b"%PDF-1.4 fake test fixture")

            settings = SimpleNamespace(projects=projects, vault=Path(tmp) / "Vault")

            def fake_extract_text(path: Path) -> tuple[str, str]:
                if path.name == passport.name:
                    return (
                        "Раздел архитектуры: персональные данные хранятся локально "
                        "в базе проекта и не передаются во внешний контур.",
                        "pdf text extracted",
                    )
                return "", ""

            with (
                patch("gaia.memory.SETTINGS", settings),
                patch("gaia.projects.SETTINGS", settings),
                patch("gaia.memory.extract_text", fake_extract_text),
            ):
                selection = select_project_memory("Автопретензии", "как мы храним персональные данные")

        headings = [source.heading for source in selection.sources]
        self.assertIn("АПР - Паспорт системы", headings)
        self.assertIn("АПР - Отчет по переписке исполнителя", headings)
        self.assertLess(
            headings.index("АПР - Паспорт системы"),
            headings.index("АПР - Отчет по переписке исполнителя"),
        )
        self.assertIn("хранятся локально", selection.text)
        self.assertTrue(selection.evidence_plan)
        self.assertEqual(selection.evidence_plan[0].status, "confirmed")
        self.assertEqual(selection.evidence_plan[0].heading, "АПР - Паспорт системы")
        self.assertIn("хранятся локально", selection.evidence_plan[0].excerpt)

    def test_source_summary_can_trigger_primary_source_drilldown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp) / "Проекты"
            project_dir = projects / "Автопретензии"
            source_dir = project_dir / "Память_Graph" / "50_Sources"
            raw_dir = project_dir / "Исходники"
            source_dir.mkdir(parents=True)
            raw_dir.mkdir(parents=True)
            (project_dir / "АПР - Память.md").write_text(
                "# Автопретензии\n\n"
                "## Ядро проекта\n"
                "Общий контекст проекта.\n",
                encoding="utf-8",
            )
            (source_dir / "АПР - OSMI распознавание АПО.md").write_text(
                "# АПР - OSMI распознавание АПО\n\n"
                "Source-summary: OSMI распознавание АПО описывает OCR контур, входящие экспертизы "
                "и правила обработки дефектов.\n",
                encoding="utf-8",
            )
            raw_source = raw_dir / "OSMI OCR ТЗ.txt"
            raw_source.write_text(
                "OSMI распознавание АПО принимает PDF экспертизы, извлекает дефекты "
                "и возвращает структурированный результат для карточки претензии.",
                encoding="utf-8",
            )

            settings = SimpleNamespace(projects=projects, vault=Path(tmp) / "Vault")
            with patch("gaia.memory.SETTINGS", settings), patch("gaia.projects.SETTINGS", settings):
                selection = select_project_memory(
                    "Автопретензии",
                    "что известно про OSMI распознавание АПО",
                    max_sections=1,
                )

        self.assertEqual(len(selection.sources), 1)
        self.assertEqual(selection.sources[0].heading, "АПР - OSMI распознавание АПО")
        self.assertTrue(selection.evidence_plan)
        self.assertEqual(selection.evidence_plan[0].status, "confirmed")
        self.assertEqual(selection.evidence_plan[0].heading, "OSMI OCR ТЗ")
        self.assertIn("структурированный результат", selection.evidence_plan[0].excerpt)

    def test_new_graph_source_is_indexed_after_memory_update_without_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp) / "Проекты"
            project_dir = projects / "Тест"
            source_dir = project_dir / "Память_Graph" / "50_Sources"
            source_dir.mkdir(parents=True)
            (project_dir / "ТСТ - Память.md").write_text(
                "# Тест\n\n"
                "## Быстрый вход\n"
                "Общий контекст проекта.\n",
                encoding="utf-8",
            )

            settings = SimpleNamespace(projects=projects, vault=Path(tmp) / "Vault")
            with patch("gaia.memory.SETTINGS", settings), patch("gaia.projects.SETTINGS", settings):
                first = select_project_memory("Тест", "Что известно про хранение персональных данных")
                (source_dir / "ТСТ - Паспорт системы.md").write_text(
                    "# ТСТ - Паспорт системы\n\n"
                    "Персональные данные хранятся локально в защищенном контуре проекта.\n",
                    encoding="utf-8",
                )
                second = select_project_memory("Тест", "Что известно про хранение персональных данных")

        self.assertIn("Проверка покрытия Lore", first.text)
        self.assertIn("ТСТ - Паспорт системы", second.text)
        self.assertIn("хранятся локально", second.text)

    def test_semantic_rerank_can_reorder_known_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp) / "Проекты"
            project_dir = projects / "Тест"
            project_dir.mkdir(parents=True)
            (project_dir / "Память.md").write_text(
                "# Тест\n\n"
                "## Общие договоры\n"
                "Договоры упоминаются как общий фон.\n\n"
                "## События интеграции договоров\n"
                "События интеграции договоров являются точным контекстом запроса.\n",
                encoding="utf-8",
            )

            settings = SimpleNamespace(
                projects=projects,
                vault=Path(tmp) / "Vault",
                lore_semantic_rerank=True,
                lore_rerank_candidates=8,
                lore_rerank_timeout_seconds=1,
            )

            def choose_integration(query, profile_text, candidates, max_ids, timeout):
                return [item["id"] for item in candidates if item["heading"] == "События интеграции договоров"]

            with (
                patch("gaia.memory.SETTINGS", settings),
                patch("gaia.projects.SETTINGS", settings),
                patch("gaia.memory.rerank_with_local_llm", side_effect=choose_integration),
            ):
                selection = select_project_memory("Тест", "Проверь договоры и события интеграции")

        self.assertEqual(selection.sources[0].heading, "События интеграции договоров")

    def test_semantic_rerank_without_focus_anchor_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            projects = Path(tmp) / "Проекты"
            project_dir = projects / "Автопретензии"
            source_dir = project_dir / "Память_Graph" / "50_Sources"
            branch_dir = project_dir / "Память_Graph" / "10_Branches"
            source_dir.mkdir(parents=True)
            branch_dir.mkdir(parents=True)
            (project_dir / "АПР - Память.md").write_text("# Автопретензии\n\n## Быстрый вход\nКонтекст.\n", encoding="utf-8")
            (source_dir / "АПР - МВП2 расчет компенсаций.md").write_text(
                "# АПР - МВП2 расчет компенсаций\n\n"
                "MVP2 описывает ведомость компенсаций.\n"
                "Связи: [[АПР - Расчетный контур и ИИ цены]].\n",
                encoding="utf-8",
            )
            (branch_dir / "АПР - Расчетный контур и ИИ цены.md").write_text(
                "# АПР - Расчетный контур и ИИ цены\n\n"
                "ИИ-агент ищет цены для ведомости компенсаций.\n",
                encoding="utf-8",
            )
            settings = SimpleNamespace(
                projects=projects,
                vault=Path(tmp) / "Vault",
                lore_semantic_rerank=True,
                lore_rerank_candidates=8,
                lore_rerank_timeout_seconds=1,
            )

            def choose_related_only(query, profile_text, candidates, max_ids, timeout):
                return [item["id"] for item in candidates if item["heading"] == "АПР - Расчетный контур и ИИ цены"]

            with (
                patch("gaia.memory.SETTINGS", settings),
                patch("gaia.projects.SETTINGS", settings),
                patch("gaia.memory.rerank_with_local_llm", side_effect=choose_related_only),
            ):
                selection = select_project_memory("Автопретензии", "Дай краткую сводку по мвп2")

        self.assertEqual(selection.sources[0].heading, "АПР - МВП2 расчет компенсаций")


if __name__ == "__main__":
    unittest.main()
