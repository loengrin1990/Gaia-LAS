from __future__ import annotations

import unittest

from gaia.context_chunking import MAX_EVIDENCE_SPANS, ChunkLimitError, split_context


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

    def test_limit_counts_semantic_units_and_never_truncates(self) -> None:
        text = "\n\n".join(f"Единица {index}." for index in range(81))
        with self.assertRaisesRegex(ChunkLimitError, "смысловых единиц"):
            split_context(text, 200, 1, 0, 80)

    def test_fenced_headings_do_not_change_structure_or_offsets(self) -> None:
        text = "```\nРЕШЕНИЯ\n# РЕШЕНИЯ\n```\n\nТекст после блока.\n\n~~~python\n# РЕШЕНИЯ\n~~~\n\nЕщё текст."
        units = split_context(text, 300, 1, 0, 10)
        after = next(unit for unit in units if "Текст после" in unit.text)
        later = next(unit for unit in units if "Ещё текст" in unit.text)
        self.assertIsNone(after.section_type_hint)
        self.assertIsNone(later.section_type_hint)
        self.assertEqual(after.text, text[after.start:after.end])
        self.assertEqual(later.text, text[later.start:later.end])

    def test_only_valid_markdown_heading_hides_a_heading_line(self) -> None:
        text = "#hashtag important text\n\nРЕШЕНИЯ ПРОБЛЕМЫ\n\n# РЕШЕНИЯ\n\nРешение принято.\n\n## РЕШЕНИЯ\n\nВторое решение.\n\n####### text"
        units = split_context(text, 300, 1, 0, 10)
        self.assertEqual(units[0].text.strip(), "#hashtag important text")
        self.assertEqual(units[0].start, text.index("#hashtag"))
        self.assertEqual(units[1].text.strip(), "РЕШЕНИЯ ПРОБЛЕМЫ")
        self.assertIsNone(units[1].section_type_hint)
        self.assertEqual(units[2].section_type_hint, "decision")
        self.assertEqual(units[3].section_type_hint, "decision")
        self.assertEqual(units[4].text.strip(), "####### text")
        for unit in units:
            self.assertEqual(unit.text, text[unit.start:unit.end])

    def test_list_items_keep_wrapped_lines_and_prose_uses_sentences(self) -> None:
        text = "# РЕШЕНИЯ\n\n1. Первый пункт\n   продолжение пункта\n2) Второй пункт\n\nОбычное предложение. Ещё одно."
        first_list_unit, second_list_unit, prose_unit = split_context(text, 300, 1, 0, 10)
        spans = first_list_unit.evidence_spans
        self.assertEqual([span.id for span in spans], ["E1"])
        self.assertIn("продолжение", spans[0].text)
        self.assertNotIn("Второй", spans[0].text)
        self.assertEqual(second_list_unit.evidence_spans[0].text, "2) Второй пункт")
        self.assertEqual([span.text for span in prose_unit.evidence_spans], ["Обычное предложение.", "Ещё одно."])
        for span in spans:
            self.assertEqual(span.text, text[span.global_start:span.global_end])
            self.assertEqual(span.text, first_list_unit.text[span.local_start:span.local_end])

    def test_evidence_spans_are_deterministic_bounded_and_do_not_include_headings(self) -> None:
        text = "# РИСКИ\n\n" + " ".join(f"Предложение {index}." for index in range(40))
        first = split_context(text, 2000, 1, 0, 10)[0]
        second = split_context(text, 2000, 1, 0, 10)[0]
        self.assertLessEqual(len(first.evidence_spans), MAX_EVIDENCE_SPANS)
        self.assertEqual(first.evidence_spans, second.evidence_spans)
        self.assertTrue(all(span.text.strip() for span in first.evidence_spans))
        self.assertTrue(all("# РИСКИ" not in span.text for span in first.evidence_spans))
        self.assertEqual("".join("".join(span.text.split()) for span in first.evidence_spans), "".join(first.text.split()))

    def test_adjacent_list_items_are_independent_units_with_continuations(self) -> None:
        text = "## РЕШЕНИЯ\n\n1. Первый пункт\n   продолжение первого пункта.\n2) Второй пункт.\n- Третий пункт.\n  продолжение третьего пункта.\n* Четвёртый пункт.\n+ Пятый пункт."
        units = split_context(text, 300, 1, 0, 10)
        self.assertEqual(len(units), 5)
        self.assertTrue(all(unit.boundary == "list_item" for unit in units))
        self.assertTrue(all(unit.section_type_hint == "decision" for unit in units))
        self.assertIn("продолжение первого", units[0].text)
        self.assertIn("продолжение третьего", units[2].text)
        self.assertEqual([len(unit.evidence_spans) for unit in units], [1, 1, 1, 1, 1])
        for unit in units:
            self.assertEqual(unit.text, text[unit.start:unit.end])
            self.assertEqual(unit.evidence_spans[0].text, unit.text.strip())

    def test_sixty_structured_items_are_independent_typed_units_under_guard(self) -> None:
        sections = (("ТРЕБОВАНИЯ", "requirement"), ("РЕШЕНИЯ", "decision"), ("РИСКИ", "risk"), ("ОТКРЫТЫЕ ВОПРОСЫ", "open_question"), ("ДЕЙСТВИЯ", "action"))
        blocks = []
        number = 1
        for heading, _ in sections:
            items = []
            for item in range(12):
                tail = "\n   продолжение пункта." if item == 0 else ""
                items.append(f"{number}. Синтетический пункт {number}.{tail}")
                number += 1
            blocks.append(heading + "\n\n" + "\n".join(items))
        text = "Вводный абзац.\n\n" + "\n\n".join(blocks)
        units = split_context(text, 1000, 1, 0, 80)
        listed = [unit for unit in units if unit.boundary == "list_item"]
        self.assertEqual(len(listed), 60)
        self.assertLessEqual(len(units), 80)
        self.assertTrue(all(len(unit.evidence_spans) == 1 for unit in listed))
        self.assertTrue(all(unit.text == text[unit.start:unit.end] for unit in listed))
        self.assertTrue(all(unit.evidence_spans[0].text == unit.text.strip() for unit in listed))
        self.assertEqual([unit.section_type_hint for unit in listed], [hint for _, hint in sections for _ in range(12)])
