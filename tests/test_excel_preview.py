from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from gaia.excel_preview import extract_xlsx_normalized, preview_xlsx


def write_minimal_xlsx(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
</Types>""")
        archive.writestr("_rels/.rels", """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""")
        archive.writestr("xl/workbook.xml", """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Roadmap" sheetId="1" r:id="rId1"/></sheets>
</workbook>""")
        archive.writestr("xl/_rels/workbook.xml.rels", """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>""")
        archive.writestr("xl/sharedStrings.xml", """<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="5" uniqueCount="5">
  <si><t>Этап</t></si>
  <si><t>Статус</t></si>
  <si><t>MVP1</t></si>
  <si><t>Готово</t></si>
  <si><t>MVP2</t></si>
</sst>""")
        archive.writestr("xl/worksheets/sheet1.xml", """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>
    <row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2" t="s"><v>3</v></c></row>
    <row r="3"><c r="A3" t="s"><v>4</v></c><c r="B3"><f>1+1</f><v>2</v></c></row>
  </sheetData>
  <mergeCells count="1"><mergeCell ref="A1:B1"/></mergeCells>
</worksheet>""")


class ExcelPreviewTests(unittest.TestCase):
    def test_preview_xlsx_without_openpyxl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "roadmap.xlsx"
            write_minimal_xlsx(path)

            preview = preview_xlsx(path)

        self.assertEqual(preview.sheet_count, 1)
        self.assertEqual(preview.sheets[0].name, "Roadmap")
        self.assertEqual(preview.sheets[0].header[:2], ["Этап", "Статус"])
        self.assertIn("B3", preview.sheets[0].formula_cells)
        self.assertIn("A1:B1", preview.sheets[0].merged_ranges)
        self.assertIn("## Лист: Roadmap", preview.normalized_markdown)

    def test_extract_xlsx_normalized_returns_memory_ready_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "roadmap.xlsx"
            write_minimal_xlsx(path)

            text, note = extract_xlsx_normalized(path)

        self.assertIn("Excel preview: roadmap.xlsx", text)
        self.assertIn("MVP1", text)
        self.assertIn("структурно нормализован Excel", note)


if __name__ == "__main__":
    unittest.main()
