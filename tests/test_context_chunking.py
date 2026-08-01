from __future__ import annotations

import unittest

from gaia.context_chunking import ChunkLimitError, split_context


class ContextChunkingTests(unittest.TestCase):
    def test_structural_chunks_cover_source_with_global_offsets(self) -> None:
        text = "# Раздел\n\nПервый абзац. Второй.\n\n- Пункт один\n- Пункт два\n\n" + "Очень длинное предложение. " * 30
        chunks = split_context(text, 140, 4, 20, 40)
        self.assertTrue(chunks)
        self.assertEqual(chunks[0].start, text.index("Первый"))
        self.assertEqual(chunks[-1].end, len(text))
        for chunk in chunks:
            self.assertEqual(chunk.text, text[chunk.start:chunk.end])
            self.assertTrue(chunk.text.strip())
            self.assertLessEqual(len(chunk.text), 140)
        covered = set().union(*(set(range(chunk.start, chunk.end)) for chunk in chunks))
        self.assertTrue(set(range(text.index("Первый"), len(text))).issubset(covered))

    def test_limit_is_explicit_and_never_truncates(self) -> None:
        with self.assertRaises(ChunkLimitError):
            split_context("x" * 1000, 100, 2, 0, 2)

    def test_typed_markdown_section_is_inherited_through_nested_heading(self) -> None:
        text = "## РЕШЕНИЯ\n\n### План\n\nПилотный запуск назначен на 1 октября 2026 года.\n\n## ВАЖНЫЕ РЕШЕНИЯ\n\nЭто не типизированный раздел."
        units = split_context(text, 200, 1, 0, 10)
        self.assertEqual(units[0].section_stack, ("РЕШЕНИЯ", "План"))
        self.assertEqual(units[0].section_type_hint, "decision")
        self.assertIsNone(units[1].section_type_hint)
        self.assertEqual(units[0].text, text[units[0].start:units[0].end])

    def test_plain_canonical_sections_are_separate_and_keep_offsets(self) -> None:
        text = "РЕШЕНИЯ:\n\nПервое решение.\n\nРИСКИ\n\nВозможна задержка."
        units = split_context(text, 200, 1, 0, 10)
        self.assertEqual([(unit.section_type_hint, unit.text.strip()) for unit in units], [("decision", "Первое решение."), ("risk", "Возможна задержка.")])
        self.assertEqual(units[1].start, text.index("Возможна"))
