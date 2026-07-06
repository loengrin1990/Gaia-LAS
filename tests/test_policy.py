from __future__ import annotations

import unittest

from gaia.policy import detect_possible_pii


class PolicyPiiDetectionTests(unittest.TestCase):
    def test_project_terms_do_not_block_as_pii_by_themselves(self) -> None:
        self.assertFalse(detect_possible_pii("Обсудили паспорт проекта и договорной процесс."))

    def test_document_identifiers_still_block_as_possible_pii(self) -> None:
        self.assertTrue(detect_possible_pii("Нужно проверить паспортные данные участника."))
        self.assertTrue(detect_possible_pii("Договор № 123 требует ручной проверки."))


if __name__ == "__main__":
    unittest.main()
