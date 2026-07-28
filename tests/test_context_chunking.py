from __future__ import annotations

import unittest

from gaia.context_chunking import ChunkLimitError, split_context


class ContextChunkingTests(unittest.TestCase):
    def test_structural_chunks_cover_source_with_global_offsets(self) -> None:
        text = "# Раздел\n\nПервый абзац. Второй.\n\n- Пункт один\n- Пункт два\n\n" + "Очень длинное предложение. " * 30
        chunks = split_context(text, 140, 4, 20, 40)
        self.assertTrue(chunks)
        self.assertEqual(chunks[0].start, 0)
        self.assertEqual(chunks[-1].end, len(text))
        for chunk in chunks:
            self.assertEqual(chunk.text, text[chunk.start:chunk.end])
            self.assertTrue(chunk.text.strip())
            self.assertLessEqual(len(chunk.text), 140)
        covered = set().union(*(set(range(chunk.start, chunk.end)) for chunk in chunks))
        self.assertEqual(covered, set(range(len(text))))

    def test_limit_is_explicit_and_never_truncates(self) -> None:
        with self.assertRaises(ChunkLimitError):
            split_context("x" * 1000, 100, 2, 0, 2)
