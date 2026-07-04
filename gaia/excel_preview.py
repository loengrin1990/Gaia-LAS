from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pkgrel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
MAX_SHEETS = 8
MAX_ROWS_PER_SHEET = 16
MAX_COLUMNS = 14
CELL_REF_RE = re.compile(r"([A-Z]+)([0-9]+)")


@dataclass
class ExcelSheetPreview:
    name: str
    rows: int
    columns: int
    header: list[str]
    sample_rows: list[list[str]]
    merged_ranges: list[str]
    formula_cells: list[str]


@dataclass
class ExcelWorkbookPreview:
    file_name: str
    sheet_count: int
    sheets: list[ExcelSheetPreview]
    warnings: list[str]
    normalized_markdown: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def preview_xlsx(path: Path) -> ExcelWorkbookPreview:
    warnings: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            shared_strings = read_shared_strings(archive)
            sheet_refs = workbook_sheet_refs(archive)
            sheets: list[ExcelSheetPreview] = []
            for name, sheet_path in sheet_refs[:MAX_SHEETS]:
                try:
                    sheets.append(read_sheet_preview(archive, name, sheet_path, shared_strings))
                except Exception as exc:
                    warnings.append(f"Лист `{name}` не прочитан: {exc}")
            if len(sheet_refs) > MAX_SHEETS:
                warnings.append(f"Показаны первые {MAX_SHEETS} листов из {len(sheet_refs)}.")
    except zipfile.BadZipFile:
        sheets = []
        warnings.append("Файл не похож на корректный .xlsx архив.")
    except KeyError as exc:
        sheets = []
        warnings.append(f"В книге не найден обязательный XML: {exc}.")
    except Exception as exc:
        sheets = []
        warnings.append(f"Не удалось прочитать Excel: {exc}")
    preview = ExcelWorkbookPreview(
        file_name=path.name,
        sheet_count=len(sheets),
        sheets=sheets,
        warnings=warnings,
        normalized_markdown="",
    )
    preview.normalized_markdown = render_workbook_markdown(preview)
    return preview


def extract_xlsx_normalized(path: Path) -> tuple[str, str]:
    preview = preview_xlsx(path)
    note = f"структурно нормализован Excel: {preview.sheet_count} листов"
    if preview.warnings:
        note += "; " + "; ".join(preview.warnings[:2])
    return preview.normalized_markdown, note


def read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    values: list[str] = []
    for item in root.findall("main:si", NS):
        fragments = [text.text or "" for text in item.findall(".//main:t", NS)]
        values.append("".join(fragments))
    return values


def workbook_sheet_refs(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    rel_targets = {
        rel.attrib.get("Id"): normalize_sheet_target(rel.attrib.get("Target", ""))
        for rel in rels.findall("pkgrel:Relationship", NS)
    }
    refs: list[tuple[str, str]] = []
    for sheet in workbook.findall("main:sheets/main:sheet", NS):
        name = sheet.attrib.get("name") or "Sheet"
        rid = sheet.attrib.get(f"{{{NS['rel']}}}id")
        target = rel_targets.get(rid)
        if target:
            refs.append((name, target))
    return refs


def normalize_sheet_target(target: str) -> str:
    target = target.lstrip("/")
    if target.startswith("xl/"):
        return target
    return "xl/" + target


def read_sheet_preview(
    archive: zipfile.ZipFile,
    name: str,
    sheet_path: str,
    shared_strings: list[str],
) -> ExcelSheetPreview:
    root = ET.fromstring(archive.read(sheet_path))
    rows: list[tuple[int, list[str]]] = []
    max_row = 0
    max_col = 0
    formula_cells: list[str] = []
    for row in root.findall(".//main:sheetData/main:row", NS):
        row_index = int(row.attrib.get("r") or len(rows) + 1)
        values_by_col: dict[int, str] = {}
        for cell in row.findall("main:c", NS):
            ref = cell.attrib.get("r") or ""
            col_index, parsed_row = parse_cell_ref(ref)
            if parsed_row:
                row_index = parsed_row
            value = cell_value(cell, shared_strings)
            formula = cell.find("main:f", NS)
            if formula is not None and len(formula_cells) < 20:
                formula_cells.append(ref or f"R{row_index}C{col_index}")
            if value:
                values_by_col[col_index] = clean_cell(value)
                max_col = max(max_col, col_index)
        if values_by_col:
            max_row = max(max_row, row_index)
            width = min(max(max(values_by_col), 1), MAX_COLUMNS)
            rows.append((row_index, [values_by_col.get(index, "") for index in range(1, width + 1)]))
    non_empty = [row for _index, row in rows if any(cell for cell in row)]
    header = infer_header(non_empty)
    sample = non_empty[1:MAX_ROWS_PER_SHEET] if header and non_empty and non_empty[0] == header else non_empty[:MAX_ROWS_PER_SHEET]
    merged_ranges = [
        merge.attrib.get("ref", "")
        for merge in root.findall(".//main:mergeCells/main:mergeCell", NS)
        if merge.attrib.get("ref")
    ][:20]
    return ExcelSheetPreview(
        name=name,
        rows=max_row or len(non_empty),
        columns=max_col,
        header=header[:MAX_COLUMNS],
        sample_rows=[row[:MAX_COLUMNS] for row in sample],
        merged_ranges=merged_ranges,
        formula_cells=formula_cells,
    )


def parse_cell_ref(ref: str) -> tuple[int, int]:
    match = CELL_REF_RE.match(ref)
    if not match:
        return 1, 0
    letters, row = match.groups()
    col = 0
    for char in letters:
        col = col * 26 + ord(char) - ord("A") + 1
    return col, int(row)


def cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    value_node = cell.find("main:v", NS)
    inline_node = cell.find("main:is/main:t", NS)
    if inline_node is not None:
        return inline_node.text or ""
    value = value_node.text if value_node is not None else ""
    cell_type = cell.attrib.get("t")
    if cell_type == "s" and value:
        try:
            return shared_strings[int(value)]
        except Exception:
            return value
    if cell_type == "b":
        return "TRUE" if value == "1" else "FALSE"
    return value or ""


def clean_cell(value: str) -> str:
    return " ".join(str(value).replace("|", "/").split())


def infer_header(rows: list[list[str]]) -> list[str]:
    if not rows:
        return []
    for row in rows[:5]:
        filled = [cell for cell in row if cell]
        if len(filled) >= 2:
            return row
    return rows[0]


def render_workbook_markdown(preview: ExcelWorkbookPreview) -> str:
    parts = [
        f"# Excel preview: {preview.file_name}",
        "",
        f"Листов обработано: {preview.sheet_count}",
    ]
    if preview.warnings:
        parts.extend(["", "## Предупреждения", ""])
        parts.extend(f"- {warning}" for warning in preview.warnings)
    for sheet in preview.sheets:
        parts.extend([
            "",
            f"## Лист: {sheet.name}",
            "",
            f"- Размер: {sheet.rows} строк x {sheet.columns} колонок",
            f"- Объединенные диапазоны: {', '.join(sheet.merged_ranges[:8]) if sheet.merged_ranges else 'нет'}",
            f"- Формулы: {', '.join(sheet.formula_cells[:12]) if sheet.formula_cells else 'нет'}",
            "",
        ])
        if sheet.header:
            parts.extend(["### Заголовки", "", " | ".join(sheet.header), ""])
        if sheet.sample_rows:
            parts.extend(["### Первые строки", ""])
            for index, row in enumerate(sheet.sample_rows[:MAX_ROWS_PER_SHEET], 1):
                parts.append(f"{index}. " + " | ".join(row))
    return "\n".join(parts).strip() + "\n"
