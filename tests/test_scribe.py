from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gaia.scribe import (
    BLOCK_REASON,
    apply_scribe_plan,
    create_scribe_draft,
    create_scribe_plan,
    package_has_unresolved_pii,
)


def package_fixture(prompt: str = "Обнови правила проекта.") -> dict:
    return {
        "run_id": "test-run",
        "project": "Автопретензии",
        "route": "Codex/ChatGPT после ручного подтверждения",
        "safe_for_codex_after_confirmation": True,
        "local_fallback_required": False,
        "policy_notes": ["ПД должны быть замаскированы до внешнего анализа."],
        "memory_chars": 123,
        "query_mask_status": "выполнено",
        "query_mask_replacements": 3,
        "query_mask_review": {
            "unresolved_pii": False,
            "status": "выполнено",
            "total_replacements": 3,
            "counts": {"PERSON": 1, "PHONE": 1, "EMAIL": 1},
        },
        "files": [],
        "evidence_plan": [
            {
                "status": "confirmed",
                "heading": "Паспорт системы",
                "source_path": "/tmp/passport.pdf",
                "excerpt": "Система хранит данные локально.",
            }
        ],
        "prompt": prompt,
        "journal_path": "/tmp/test-run.md",
    }


class ScribeTests(unittest.TestCase):
    def test_creates_masked_markdown_draft_and_instruction(self) -> None:
        prompt = "Итоги встречи: Иванов Иван Иванович, телефон +7 999 123-45-67, email test@example.com."
        with tempfile.TemporaryDirectory() as tmp:
            draft = create_scribe_draft(package_fixture(prompt), output_dir=Path(tmp))

            self.assertTrue(Path(draft.draft_path).exists())
            self.assertIn("$update-obsidian-project-memory", draft.instruction)
            self.assertIn("Память.md", draft.instruction)
            self.assertIn("Источники.md", draft.instruction)
            self.assertIn("Журнал памяти.md", draft.instruction)
            self.assertNotIn("Иванов Иван Иванович", draft.markdown)
            self.assertNotIn("+7 999 123-45-67", draft.markdown)
            self.assertNotIn("test@example.com", draft.markdown)
            self.assertIn("[PERSON_", draft.markdown)
            self.assertIn("[PHONE_", draft.markdown)
            self.assertIn("[EMAIL_", draft.markdown)

    def test_does_not_touch_project_memory_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "Проекты" / "Автопретензии"
            project_dir.mkdir(parents=True)
            memory_path = project_dir / "Память.md"
            memory_path.write_text("стабильная память", encoding="utf-8")

            create_scribe_draft(package_fixture(), output_dir=Path(tmp) / "drafts")

            self.assertEqual(memory_path.read_text(encoding="utf-8"), "стабильная память")

    def test_blocks_unresolved_pii_package(self) -> None:
        package = package_fixture()
        package["query_mask_review"]["unresolved_pii"] = True

        self.assertTrue(package_has_unresolved_pii(package))
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, BLOCK_REASON):
                create_scribe_draft(package, output_dir=Path(tmp))

    def test_llm_classifier_adds_candidates_to_draft_only(self) -> None:
        settings = type("Settings", (), {"scribe_candidate_classifier": True, "scribe_classifier_timeout_seconds": 1})()
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("gaia.scribe.SETTINGS", settings),
                patch("gaia.scribe.classify_scribe_candidates_with_local_llm", return_value={
                    "decisions": ["Зафиксировать правило маршрутизации."],
                    "rules": [],
                    "risks": ["Есть риск неполного источника."],
                    "open_questions": [],
                    "technical_facts": [],
                    "exclude": ["Разовые детали встречи."],
                }),
            ):
                draft = create_scribe_draft(package_fixture(), output_dir=Path(tmp))

        self.assertIn("LLM-классификация кандидатов", draft.markdown)
        self.assertIn("Зафиксировать правило маршрутизации.", draft.markdown)
        self.assertIn("Разовые детали встречи.", draft.markdown)

    def test_scribe_plan_builds_staged_items_without_writing_memory(self) -> None:
        settings = type("Settings", (), {"scribe_candidate_classifier": True, "scribe_classifier_timeout_seconds": 1})()
        with (
            patch("gaia.scribe.SETTINGS", settings),
            patch("gaia.scribe.classify_scribe_candidates_with_local_llm", return_value={
                "decisions": ["Принято решение использовать staged review перед записью памяти."],
                "rules": [],
                "risks": [],
                "open_questions": [],
                "technical_facts": [],
                "exclude": ["Разовая фраза встречи."],
            }),
        ):
            plan = create_scribe_plan(package_fixture())

        self.assertEqual(plan.status, "ready")
        self.assertTrue(any(item.destination == "20_Decisions" for item in plan.items))
        self.assertTrue(any(item.destination == "50_Sources" for item in plan.items))
        self.assertTrue(any(item.destination == "exclude" and not item.selected for item in plan.items))
        self.assertIn("Scribe plan", plan.preview)

    def test_inbox_scribe_plan_is_scoped_to_selected_file_not_lore_evidence(self) -> None:
        package = package_fixture()
        package["scribe_origin"] = {
            "type": "inbox",
            "relative_path": "Исходники/АПР - {Бэклог}.xlsx",
            "name": "АПР - {Бэклог}.xlsx",
            "kind": "excel",
        }
        package["files"] = [
            {
                "name": "АПР - {Бэклог}.xlsx",
                "kind": "xlsx",
                "stored_path": "/tmp/АПР - {Бэклог}.xlsx",
                "extraction_note": "структурно нормализован Excel",
                "masked_text": "Excel preview: Бэклог. Заголовки: ID, Экран, Задача, Статус.",
                "mask_review": {"unresolved_pii": False},
            }
        ]

        with patch("gaia.scribe.classify_scribe_candidates_with_local_llm", return_value=None):
            plan = create_scribe_plan(package)

        bodies = "\n".join(item.body for item in plan.items)
        self.assertIn("АПР - {Бэклог}.xlsx", bodies)
        self.assertIn("Excel preview: Бэклог", bodies)
        self.assertNotIn("Паспорт системы", bodies)
        self.assertTrue(all("АПР - {Бэклог}.xlsx" in item.evidence for item in plan.items if item.destination == "50_Sources"))

    def test_scribe_apply_writes_selected_graph_node_with_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            projects = root / "Проекты"
            service_docs = root / "Сервис"
            project_dir = projects / "Автопретензии"
            (project_dir / "Память_Graph" / "20_Decisions").mkdir(parents=True)
            for folder in ("00_Core", "10_Branches", "30_Open_Questions", "40_Risks", "50_Sources", "90_Archive"):
                (project_dir / "Память_Graph" / folder).mkdir(parents=True, exist_ok=True)
            (project_dir / "АПР - Память.md").write_text("# Автопретензии\n", encoding="utf-8")
            (project_dir / "Память_Graph" / "АПР - Индекс памяти.md").write_text("# АПР - Индекс памяти\n\n## Вовлеченные узлы\n\n", encoding="utf-8")
            settings = SimpleNamespace(
                projects=projects,
                service_docs=service_docs,
                scribe_candidate_classifier=True,
                scribe_classifier_timeout_seconds=1,
            )
            package = package_fixture()
            with (
                patch("gaia.scribe.SETTINGS", settings),
                patch("gaia.projects.SETTINGS", settings),
                patch("gaia.scribe.classify_scribe_candidates_with_local_llm", return_value={
                    "decisions": ["Принято решение использовать staged review перед записью памяти."],
                    "rules": [],
                    "risks": [],
                    "open_questions": [],
                    "technical_facts": [],
                    "exclude": [],
                }),
            ):
                plan = create_scribe_plan(package)
                selected = [item.id for item in plan.items if item.destination == "20_Decisions"]
                result = apply_scribe_plan(package, selected)

            self.assertEqual(len(result.applied), 1)
            self.assertTrue(Path(result.backup_path).exists())
            self.assertTrue(any("20_Decisions" in path for path in result.changed_files))
            self.assertTrue((project_dir / "АПР - Источники.md").exists())
            self.assertIn("Scribe apply", (project_dir / "АПР - Журнал памяти.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
