from __future__ import annotations

import unittest

from gaia.models import EvidenceItem, FileArtifact
from gaia.packaging import build_prompt


class PackagingTests(unittest.TestCase):
    def test_attached_file_with_empty_text_is_reported_as_attached(self) -> None:
        prompt = build_prompt(
            "Тест",
            "",
            "Проверь документ",
            [
                FileArtifact(
                    name="Паспорт системы.pdf",
                    kind="pdf",
                    stored_path="/tmp/Паспорт системы.pdf",
                    extraction_note="текст не извлечен",
                    original_chars=0,
                    masked_chars=0,
                    mask_status="ok",
                    mask_replacements=0,
                    masked_text="",
                )
            ],
        )

        self.assertIn("## Файл: Паспорт системы.pdf", prompt)
        self.assertIn("Файл приложен, но текст для анализа пустой.", prompt)
        self.assertNotIn("Файлы не приложены.", prompt)

    def test_evidence_plan_is_included_in_prompt_contract(self) -> None:
        prompt = build_prompt(
            "Тест",
            "## Память\nПодтвержденный контекст.",
            "Как хранится ПДн?",
            [],
            evidence_plan=[
                EvidenceItem(
                    claim="Как хранится ПДн?",
                    status="confirmed",
                    source_id="source-1",
                    source_path="/tmp/passport.pdf",
                    heading="Паспорт системы",
                    excerpt="ПДн хранятся локально.",
                    reason="Первичный источник выбран Lore.",
                )
            ],
        )

        self.assertIn("# Evidence plan Lore", prompt)
        self.assertIn("status: confirmed", prompt)
        self.assertIn("ПДн хранятся локально.", prompt)


if __name__ == "__main__":
    unittest.main()
