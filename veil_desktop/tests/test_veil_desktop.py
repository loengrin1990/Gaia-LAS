from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from veil_core.extraction import ExtractionError, read_material
from veil_core.masking import mask_with_review
from veil_core.model_review import review_residual_pii, stop_model


class IndependentVeilTests(unittest.TestCase):
    def test_masking_works_without_gaia_or_model(self) -> None:
        result = mask_with_review(
            "пример", "Иванов Иван Иванович, test@example.com, +7 999 123-45-67.",
            include_llm_review=False,
        )
        self.assertEqual(result.review.total_replacements, 3)
        self.assertNotIn("Иванов", result.masked_text)
        self.assertNotIn("test@example.com", result.masked_text)
        self.assertNotIn("123-45-67", result.masked_text)

    def test_model_gets_only_masked_text_and_unloads(self) -> None:
        requests = []
        def fake_post(path, payload, timeout):
            requests.append(payload)
            return {"done": True, "done_reason": "stop", "response": json.dumps({
                "unresolved_pii": False, "reason": "", "categories": []
            })}
        with patch("veil_core.model_review._post", fake_post):
            result = review_residual_pii("[PERSON_1] записан в список")
        self.assertTrue(result.completed)
        self.assertFalse(result.unresolved_pii)
        self.assertEqual(requests[0]["keep_alive"], 0)
        self.assertNotIn("Иванов", json.dumps(requests, ensure_ascii=False))

    def test_bad_model_reply_needs_manual_review(self) -> None:
        with patch("veil_core.model_review._post", return_value={"done": False, "response": "{}"}):
            result = review_residual_pii("Очищенный текст")
        self.assertFalse(result.completed)

    def test_stop_only_asks_ollama_to_unload_selected_model(self) -> None:
        with patch("veil_core.model_review._post", return_value={"done": True}) as request:
            self.assertTrue(stop_model())
        self.assertEqual(request.call_args.args[0], "/api/generate")
        self.assertEqual(request.call_args.args[1]["keep_alive"], 0)
        self.assertEqual(request.call_args.args[1]["prompt"], "")

    def test_reads_docx_in_memory(self) -> None:
        xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
               '<w:body><w:p><w:r><w:t>test@example.com</w:t></w:r></w:p></w:body></w:document>')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.docx"
            with ZipFile(path, "w") as archive:
                archive.writestr("word/document.xml", xml)
            text, note = read_material(path)
        self.assertEqual(text, "test@example.com")
        self.assertIn("изображений", note)

    def test_text_and_unsupported_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.txt"
            path.write_text("Тест", encoding="utf-8")
            self.assertEqual(read_material(path)[0], "Тест")
            path = Path(directory) / "sample.bin"
            path.write_bytes(b"test")
            with self.assertRaises(ExtractionError):
                read_material(path)

    def test_pdf_without_extractable_text_is_rejected(self) -> None:
        from pypdf import PdfWriter
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scan.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=300, height=300)
            with path.open("wb") as output:
                writer.write(output)
            with self.assertRaisesRegex(ExtractionError, "нет извлекаемого текста"):
                read_material(path)

    def test_reads_pdf_text_without_saving_source(self) -> None:
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.pdf"
            writer = PdfWriter()
            page = writer.add_blank_page(width=300, height=300)
            font = DictionaryObject({
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            })
            font_ref = writer._add_object(font)
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})
            })
            stream = DecodedStreamObject()
            stream.set_data(b"BT /F1 12 Tf 10 150 Td (test@example.com) Tj ET")
            page[NameObject("/Contents")] = writer._add_object(stream)
            with path.open("wb") as output:
                writer.write(output)
            text, _ = read_material(path)
        self.assertEqual(text.strip(), "test@example.com")


if __name__ == "__main__":
    unittest.main()
