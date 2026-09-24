import unittest

from app import CATEGORY_LABELS


class UserLabelsTests(unittest.TestCase):
    def test_every_masking_category_has_a_readable_label(self) -> None:
        expected = {
            "PERSON", "EMAIL", "PHONE", "ADDRESS", "PASSPORT", "INN", "ID",
            "CONTRACT", "REQUEST", "INVOICE", "LINK", "CLIENT", "VENDOR",
            "AMOUNT", "BANK", "INTERNAL_ID",
        }
        self.assertTrue(expected.issubset(CATEGORY_LABELS))
        self.assertTrue(all("_" not in CATEGORY_LABELS[key] for key in expected))
