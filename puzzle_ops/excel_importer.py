from __future__ import annotations

import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from puzzle_ops.models import HistoricalRecord, JS_CATEGORIES


DISPIMG_RE = re.compile(r'DISPIMG\("([^"]+)"')
JS_CATEGORY_ALIASES = {
    "house": "houses",
    "object": "objects",
    "flower": "flowers",
}


class ExcelImageExtractor:
    """Extract WPS/Excel cell images referenced by DISPIMG formulas."""

    def __init__(self, workbook_path: Path | str):
        self.workbook_path = Path(workbook_path)

    def extract(self, output_dir: Path | str) -> dict[str, str]:
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        with ZipFile(self.workbook_path) as archive:
            if "xl/cellimages.xml" not in archive.namelist():
                return {}
            id_to_rid = self._read_cell_image_ids(archive.read("xl/cellimages.xml"))
            rid_to_target = self._read_relationships(archive.read("xl/_rels/cellimages.xml.rels"))
            mapping: dict[str, str] = {}
            for image_id, rel_id in id_to_rid.items():
                target = rid_to_target[rel_id]
                source_name = f"xl/{target}"
                suffix = Path(target).suffix or ".png"
                local_name = f"{image_id}{suffix}"
                destination = output / local_name
                with archive.open(source_name) as source, destination.open("wb") as target_file:
                    shutil.copyfileobj(source, target_file)
                mapping[image_id] = str(destination)
            return mapping

    def _read_cell_image_ids(self, xml_bytes: bytes) -> dict[str, str]:
        root = ET.fromstring(xml_bytes)
        ids: dict[str, str] = {}
        for cell_image in root:
            name = ""
            rel_id = ""
            for element in cell_image.iter():
                if element.tag.endswith("cNvPr"):
                    name = element.attrib.get("name", "")
                if element.tag.endswith("blip"):
                    rel_id = element.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed", "")
            if name and rel_id:
                ids[name] = rel_id
        return ids

    def _read_relationships(self, xml_bytes: bytes) -> dict[str, str]:
        root = ET.fromstring(xml_bytes)
        return {element.attrib["Id"]: element.attrib["Target"] for element in root}


def import_history_workbook(workbook_path: Path | str, country: str, image_output_dir: Path | str) -> tuple[HistoricalRecord, ...]:
    workbook_path = Path(workbook_path)
    image_map = ExcelImageExtractor(workbook_path).extract(image_output_dir)
    rows = _read_first_sheet(workbook_path)
    headers = [str(value).strip() for value in rows[0]]
    records: list[HistoricalRecord] = []
    for row in rows[1:]:
        values = dict(zip(headers, row))
        if not any(str(value or "").strip() for value in values.values()):
            continue
        row_country = str(values.get("国家") or country).strip()
        if row_country and row_country != country:
            continue
        js_category = _normalize_js_category(str(values["JS分类"]).strip())
        if js_category not in JS_CATEGORIES:
            raise ValueError(f"未知 JS分类：{js_category}")
        image_formula = str(values["图片本身"] or "")
        formula_id = _dispimg_id(image_formula)
        local_image_path = image_map.get(formula_id, "")
        records.append(
            HistoricalRecord(
                grade=str(values["图片等级"]),
                image_formula=image_formula,
                image_id=str(values["图片ID"]),
                image_url=str(values["图片URL"] or ""),
                local_image_path=local_image_path,
                thumbnail_path=local_image_path,
                position=int(values["分发位置"]),
                dimension_grade=str(values["多维度等级"]),
                open_rate=float(values["开图率"]),
                completion_rate=float(values["完成率"]),
                avg_finish_time=float(values["平均完成时长"]),
                operation_tag=str(values["运营tag"]),
                subject_tag=str(values["主体tag"]),
                js_category=js_category,
                source=str(values["图片来源"]),
                remark=str(values["备注"] or ""),
                distribution_date=_as_text(values["分发日期"]),
                distribution_cycle=str(values["分发周期"]),
                country=row_country or country,
            )
        )
    return tuple(records)


def _normalize_js_category(value: str) -> str:
    normalized = value.strip()
    return JS_CATEGORY_ALIASES.get(normalized, normalized)


def _dispimg_id(formula: str) -> str:
    match = DISPIMG_RE.search(formula)
    return match.group(1) if match else ""


def _as_text(value: object) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int) and value > 30000:
        return (datetime(1899, 12, 30) + timedelta(days=value)).date().isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value or "")


def _read_first_sheet(workbook_path: Path) -> list[list[object]]:
    with ZipFile(workbook_path) as archive:
        shared_strings = _shared_strings(archive)
        sheet_name = _first_sheet_path(archive)
        root = ET.fromstring(archive.read(sheet_name))
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    parsed_rows: list[list[object]] = []
    for row in root.findall(".//x:sheetData/x:row", ns):
        values: dict[int, object] = {}
        max_col = 0
        for cell in row.findall("x:c", ns):
            ref = cell.attrib.get("r", "")
            col = _column_index(ref)
            max_col = max(max_col, col)
            values[col] = _cell_value(cell, shared_strings, ns)
        parsed_rows.append([values.get(index, "") for index in range(1, max_col + 1)])
    width = max(len(row) for row in parsed_rows)
    return [row + [""] * (width - len(row)) for row in parsed_rows]


def _shared_strings(archive: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    strings = []
    for item in root.findall("x:si", ns):
        strings.append("".join(node.text or "" for node in item.findall(".//x:t", ns)))
    return strings


def _first_sheet_path(archive: ZipFile) -> str:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    ns = {
        "x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    }
    rel_ns = "{http://schemas.openxmlformats.org/package/2006/relationships}"
    first_sheet = workbook.find(".//x:sheet", ns)
    rel_id = first_sheet.attrib[f"{{{ns['r']}}}id"]
    for rel in rels:
        if rel.attrib["Id"] == rel_id:
            return "xl/" + rel.attrib["Target"]
    raise ValueError("无法定位第一个工作表")


def _cell_value(cell: ET.Element, shared_strings: list[str], ns: dict[str, str]) -> object:
    cell_type = cell.attrib.get("t")
    value_node = cell.find("x:v", ns)
    formula_node = cell.find("x:f", ns)
    if cell_type == "str" and formula_node is not None:
        return "=" + (formula_node.text or "")
    if value_node is None:
        return ""
    raw = value_node.text or ""
    if cell_type == "s":
        return shared_strings[int(raw)]
    try:
        number = float(raw)
        return int(number) if number.is_integer() else number
    except ValueError:
        return raw


def _column_index(ref: str) -> int:
    letters = "".join(ch for ch in ref if ch.isalpha())
    index = 0
    for letter in letters:
        index = index * 26 + (ord(letter.upper()) - ord("A") + 1)
    return index
