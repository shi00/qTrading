"""受管子进程附件解析 worker（REQ D06 / DD §10.4）。

设计要点：
- 本模块既作为受监督子进程入口（``python -m services.agent_service.attachment_worker``），
  也提供纯解析函数供单元测试直接调用；
- 解析只读取显式选择范围；未选范围不解析、不计数；超限明确拒绝或按超时返回
  真实已读/未读（不截断冒完整）；
- XLSX 使用同一不可变 snapshot 字节的两次独立只读打开（``data_only=False`` /
  ``data_only=True``），按 ``(sheet, coordinate)`` 配对公式与缓存，并有界探测
  worksheet XML 区分缓存不存在/存在但空/错误/类型不可核；
- PDF / PNG / JPEG 依赖（pypdf / Pillow）未安装时明确 ``parser_dependency_missing``，
  不伪造解码结果。
"""

from __future__ import annotations

import csv
import hashlib
import io
import itertools
import json
import sys
import time
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time
from typing import Any, Final
from xml.etree import ElementTree

from core.agent_types import (
    AgentValidationError,
    AllAtom,
    AttachmentBudget,
    AttachmentKind,
    CodepointsRange,
    JsonPointerRange,
    PagesRange,
    ParagraphsRange,
    ReadState,
    RowsRange,
    Selection,
    SheetsRange,
    decode_selection,
    encode_exact_number,
    json_doc,
    parse_preserving_numbers,
    to_wire_dict,
)

#: worker 单次结果 JSON 上限（父进程同样复核；超限不发布结果）。
MAX_RESULT_BYTES: Final = 64 * 1024 * 1024
#: 请求头 JSON 上限（防止无界读）。
MAX_HEADER_BYTES: Final = 64 * 1024

_NS_MAIN: Final = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_NS_REL: Final = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_NS_OFFICE_REL: Final = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

#: 可被解析的种类（PDF/图片依赖未装时由 dispatch 明确报依赖缺失）。


class AttachmentParseError(Exception):
    """解析失败（稳定 code + 已脱敏 message；不携正文/路径）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass
class ParsedAttachment:
    """解析产物（wire 前的中间结构；全部为已选范围）。"""

    read_state: ReadState
    coverage: str
    body_text: str | None
    blocks: list[str] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)
    limitations: list[dict[str, str]] = field(default_factory=list)
    stats: dict[str, int | None] = field(default_factory=dict)
    read_ranges: list[dict[str, Any]] = field(default_factory=list)
    unread_ranges: list[dict[str, Any]] = field(default_factory=list)

    def to_wire(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "read_state": self.read_state.value,
            "coverage": self.coverage,
            "body_text": self.body_text,
            "blocks": self.blocks,
            "facts": self.facts,
            "limitations": self.limitations,
            "stats": self.stats,
            "read_ranges": self.read_ranges,
            "unread_ranges": self.unread_ranges,
        }


def _limitation(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _stats(**values: int | None) -> dict[str, int | None]:
    base: dict[str, int | None] = {"source_bytes": None, "pages": None, "sheets": None, "rows": None, "chars": None}
    base.update(values)
    return base


# ---------------------------------------------------------------------------
# 选择范围校验（F20：PDF 页码 / XLSX 工作表·行 / CSV 行 / 文本段落 / JSON 子树）
# ---------------------------------------------------------------------------


def _reject_unsupported_selection(selection: Selection, allowed: tuple[type, ...]) -> None:
    if selection.kind == "all":
        return
    for atom in selection.ranges:
        if not isinstance(atom, allowed):
            raise AttachmentParseError(
                "invalid_selection",
                f"selection kind {atom.kind!r} is not supported for this attachment type",
            )


def _check_no_overlap(
    selection: Selection,
    atom_type: type[PagesRange] | type[RowsRange] | type[CodepointsRange] | type[ParagraphsRange],
) -> None:
    # RowsRange 的行号按工作表独立编号：不同表可共享行号，只有同表行段才构成重叠。
    groups: dict[str | None, list[tuple[int, int]]] = {}
    for atom in selection.ranges:
        if isinstance(atom, atom_type):
            sheet = atom.sheet if isinstance(atom, RowsRange) else None
            groups.setdefault(sheet, []).append((atom.start, atom.end))
    for spans in groups.values():
        spans.sort()
        for (_prev_start, prev_end), (start, _end) in itertools.pairwise(spans):
            if start <= prev_end:
                raise AttachmentParseError("invalid_selection", "overlapping ranges in selection")


def _require_ranges(selection: Selection, atom_type: type) -> list[Any]:
    return [atom for atom in selection.ranges if isinstance(atom, atom_type)]


# ---------------------------------------------------------------------------
# TXT / MD
# ---------------------------------------------------------------------------


def _parse_text(data: bytes, selection: Selection, budget: AttachmentBudget) -> ParsedAttachment:
    _reject_unsupported_selection(selection, (CodepointsRange, ParagraphsRange))
    _check_no_overlap(selection, CodepointsRange)
    _check_no_overlap(selection, ParagraphsRange)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AttachmentParseError("invalid_encoding", "attachment is not valid UTF-8 text") from exc
    lines = text.split("\n")

    if selection.kind == "all":
        selected = text
        read_ranges: list[dict[str, Any]] = [to_wire_dict(AllAtom())]
    else:
        parts: list[str] = []
        for atom in selection.ranges:
            if isinstance(atom, CodepointsRange):
                if atom.end > len(text):
                    raise AttachmentParseError("invalid_selection", "codepoints range exceeds selected source")
                parts.append(text[atom.start - 1 : atom.end])
            elif isinstance(atom, ParagraphsRange):
                if atom.end > len(lines):
                    raise AttachmentParseError("invalid_selection", "paragraph range exceeds selected source")
                parts.append("\n".join(lines[atom.start - 1 : atom.end]))
        selected = "\n".join(parts)
        read_ranges = [to_wire_dict(atom) for atom in selection.ranges]

    budget.enforce_codepoints(len(selected))
    return ParsedAttachment(
        read_state=ReadState.READY,
        coverage="full",
        body_text=selected,
        limitations=[],
        stats=_stats(source_bytes=len(data), chars=len(selected)),
        read_ranges=read_ranges,
    )


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


def _csv_records(data: bytes) -> list[list[str]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AttachmentParseError("invalid_encoding", "CSV is not valid UTF-8 text") from exc
    try:
        return list(csv.reader(io.StringIO(text, newline="")))
    except csv.Error as exc:
        raise AttachmentParseError("invalid_structure", "CSV structure is invalid") from exc


def _parse_csv(data: bytes, selection: Selection, budget: AttachmentBudget) -> ParsedAttachment:
    _reject_unsupported_selection(selection, (RowsRange,))
    _check_no_overlap(selection, RowsRange)
    records = _csv_records(data)
    # 行号含表头（§3.3.1 rows：「表头不隐式排除」）；D06 的记录行限额只排除已确认表头。
    total_records = len(records)
    data_rows = max(0, total_records - 1)

    if selection.kind == "all":
        budget.enforce_rows(data_rows)
        selected = records
        read_ranges: list[dict[str, Any]] = [to_wire_dict(AllAtom())]
    else:
        selected = []
        read_ranges = []
        selected_data_rows = 0
        for atom in selection.ranges:
            assert isinstance(atom, RowsRange)
            if atom.sheet is not None:
                raise AttachmentParseError("invalid_selection", "CSV rows must not name a sheet")
            if atom.end > total_records:
                raise AttachmentParseError("invalid_selection", "rows range exceeds selected source")
            chunk = records[atom.start - 1 : atom.end]
            # D06 记录行限额按本文件累计（不按每个行段重置）；表头只在被选中时不计。
            selected_data_rows += max(0, len(chunk) - (1 if atom.start == 1 else 0))
            budget.enforce_rows(selected_data_rows)
            selected.extend(chunk)
            read_ranges.append(to_wire_dict(atom))

    representation = "\n".join(",".join(record) for record in selected)
    budget.enforce_codepoints(len(representation))
    block = json_doc({"kind": "csv", "rows": selected})
    return ParsedAttachment(
        read_state=ReadState.READY,
        coverage="full",
        body_text=representation,
        blocks=[block],
        limitations=[],
        stats=_stats(source_bytes=len(data), rows=len(selected), chars=len(representation)),
        read_ranges=read_ranges,
    )


# ---------------------------------------------------------------------------
# JSON（agent-number-v1：保留 lexeme/精确值）
# ---------------------------------------------------------------------------


def _json_pointer_tokens(pointer: str) -> list[str]:
    if pointer == "":
        return []
    return [token.replace("~1", "/").replace("~0", "~") for token in pointer.split("/")[1:]]


def _resolve_pointer(document: Any, pointer: str) -> Any:
    current = document
    for token in _json_pointer_tokens(pointer):
        if isinstance(current, dict):
            if token not in current:
                raise AttachmentParseError("invalid_selection", "json_pointer does not exist in source")
            current = current[token]
        elif isinstance(current, list):
            if not token.isdigit():
                raise AttachmentParseError("invalid_selection", "json_pointer array index is invalid")
            index = int(token)
            if index >= len(current):
                raise AttachmentParseError("invalid_selection", "json_pointer is out of range")
            current = current[index]
        else:
            raise AttachmentParseError("invalid_selection", "json_pointer crosses a scalar value")
    return current


def _parse_json(data: bytes, selection: Selection, budget: AttachmentBudget) -> ParsedAttachment:
    _reject_unsupported_selection(selection, (JsonPointerRange,))
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AttachmentParseError("invalid_encoding", "JSON is not valid UTF-8 text") from exc
    try:
        document = parse_preserving_numbers(text)
    except ValueError as exc:
        raise AttachmentParseError("invalid_structure", "JSON structure is invalid") from exc

    if selection.kind == "all":
        value = document
        read_ranges: list[dict[str, Any]] = [to_wire_dict(AllAtom())]
    else:
        values = []
        read_ranges = []
        for atom in selection.ranges:
            assert isinstance(atom, JsonPointerRange)
            values.append(_resolve_pointer(document, atom.pointer))
            read_ranges.append(to_wire_dict(atom))
        value = values[0] if len(values) == 1 else values

    block = json_doc({"kind": "json", "value": value})
    budget.enforce_codepoints(len(block))
    return ParsedAttachment(
        read_state=ReadState.READY,
        coverage="full",
        body_text=None,
        blocks=[block],
        limitations=[],
        stats=_stats(source_bytes=len(data), chars=len(block)),
        read_ranges=read_ranges,
    )


# ---------------------------------------------------------------------------
# XLSX（同源双表示 + 缓存适用性 + 有界 XML 探测）
# ---------------------------------------------------------------------------

#: 单个 worksheet XML 解压后允许的最大字节（超限时缓存存在性保持 unknown）。
MAX_SHEET_XML_BYTES: Final = 64 * 1024 * 1024
#: XML 探测扫描的单元格上限（超过后其余单元格缓存保持 unknown）。
MAX_PROBED_CELLS: Final = 200_000


class XlsxCacheProbe:
    """有界读取 worksheet XML，区分缓存缺失/存在但空/错误/类型不可核（§10.4）。"""

    def __init__(self, data: bytes) -> None:
        self._zip = zipfile.ZipFile(io.BytesIO(data))
        self._sheet_paths = self._map_sheets()
        self._cache: dict[tuple[str, str], dict[str, Any] | None] = {}
        self._probed_cells = 0
        self.date_epoch = self._date_epoch()

    def close(self) -> None:
        self._zip.close()

    def _date_epoch(self) -> str:
        try:
            with self._zip.open("xl/workbook.xml") as handle:
                root = ElementTree.parse(handle).getroot()
        except (KeyError, ElementTree.ParseError):
            return "1900"
        props = root.find(f"{_NS_MAIN}workbookPr")
        if props is not None and props.get("date1904") in {"1", "true"}:
            return "1904"
        return "1900"

    def _map_sheets(self) -> dict[str, str]:
        try:
            with self._zip.open("xl/workbook.xml") as handle:
                workbook = ElementTree.parse(handle).getroot()
            with self._zip.open("xl/_rels/workbook.xml.rels") as handle:
                rels = ElementTree.parse(handle).getroot()
        except (KeyError, ElementTree.ParseError) as exc:
            raise AttachmentParseError("invalid_structure", "XLSX workbook structure is unreadable") from exc
        targets = {rel.get("Id"): rel.get("Target", "") for rel in rels.findall(f"{_NS_REL}Relationship")}
        paths: dict[str, str] = {}
        for sheet in workbook.findall(f"{_NS_MAIN}sheets/{_NS_MAIN}sheet"):
            name = sheet.get("name", "")
            rel_id = sheet.get(f"{_NS_OFFICE_REL}id", "")
            target = targets.get(rel_id, "")
            if not name or not target:
                continue
            normalized = target.lstrip("/")
            if not normalized.startswith("xl/"):
                normalized = f"xl/{normalized}"
            paths[name] = normalized
        return paths

    def probe(self, sheet: str, cell: str) -> dict[str, Any] | None:
        """返回 ``{"t":..., "has_v":..., "v_text":...}``；结构不可读/超界返回 None。"""

        key = (sheet, cell)
        if key in self._cache:
            return self._cache[key]
        if self._probed_cells > MAX_PROBED_CELLS:
            self._cache[key] = None
            return None
        path = self._sheet_paths.get(sheet)
        if path is None:
            self._cache[key] = None
            return None
        try:
            info = self._zip.getinfo(path)
            if info.file_size > MAX_SHEET_XML_BYTES:
                self._cache[key] = None
                return None
            found = self._scan_sheet(path, {cell})
        except (KeyError, zipfile.BadZipFile, ElementTree.ParseError, OSError):
            self._cache[key] = None
            return None
        self._cache[key] = found.get(cell)
        return self._cache[key]

    def prefetch(self, sheet: str, cells: set[str]) -> None:
        """一次扫描批量填充（行读取时调用，避免逐格重扫）。"""

        pending = {cell for cell in cells if (sheet, cell) not in self._cache}
        if not pending:
            return
        path = self._sheet_paths.get(sheet)
        if path is None:
            for cell in pending:
                self._cache[(sheet, cell)] = None
            return
        try:
            info = self._zip.getinfo(path)
            if info.file_size > MAX_SHEET_XML_BYTES:
                raise KeyError(path)
            found = self._scan_sheet(path, pending)
        except (KeyError, zipfile.BadZipFile, ElementTree.ParseError, OSError):
            for cell in pending:
                self._cache[(sheet, cell)] = None
            return
        for cell in pending:
            self._cache[(sheet, cell)] = found.get(cell)

    def _scan_sheet(self, path: str, targets: set[str]) -> dict[str, dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        remaining = set(targets)
        with self._zip.open(path) as handle:
            head = handle.read(8192)
            if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
                raise AttachmentParseError("invalid_structure", "XLSX XML entities are rejected")
            handle.seek(0)
            for _event, element in ElementTree.iterparse(handle, events=("end",)):
                if element.tag != f"{_NS_MAIN}c":
                    continue
                self._probed_cells += 1
                ref = element.get("r", "")
                if ref in remaining:
                    value_node = element.find(f"{_NS_MAIN}v")
                    found[ref] = {
                        "t": element.get("t"),
                        "has_v": value_node is not None,
                        "v_text": None if value_node is None else (value_node.text or ""),
                    }
                    remaining.discard(ref)
                element.clear()
                if not remaining or self._probed_cells > MAX_PROBED_CELLS:
                    break
        return found


def _typed_scalar(value: Any) -> dict[str, Any]:
    """openpyxl 标量 → 带类型精确表示（不引入 float 往返）。"""

    if value is None:
        return {"raw_type": "null", "lexeme": None}
    if isinstance(value, bool):
        return {"raw_type": "bool", "lexeme": str(value)}
    if isinstance(value, int):
        return {"raw_type": "json_integer", "lexeme": str(value)}
    if isinstance(value, float):
        return {"raw_type": "json_decimal", "lexeme": repr(value), "unsupported_precision": True}
    if isinstance(value, (datetime, date, dt_time)):
        return {"raw_type": "datetime", "lexeme": value.isoformat()}
    return {"raw_type": "string", "lexeme": str(value)}


def _cache_meta(
    probe: XlsxCacheProbe | None,
    sheet: str,
    cell_ref: str,
    formula_text: str | None,
    cached_value: Any,
    number_format: str | None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "schema_version": "1",
        "sheet": sheet,
        "cell": cell_ref,
        "formula_text": formula_text,
        "cache_present": False,
        "cache_applicable": False,
        "cache_raw_type": None,
        "cache_raw_value": None,
        "cache_verification": None,
        "cache_missing_reason": "not_applicable",
        "date_epoch": None,
        "number_format": number_format,
        "xml_value_lexeme": None,
    }
    if formula_text is None:
        return meta

    meta["date_epoch"] = probe.date_epoch if probe is not None else None
    structure = probe.probe(sheet, cell_ref) if probe is not None else None
    if structure is None:
        meta["cache_present"] = None
        meta["cache_missing_reason"] = "cache_presence_unverified"
        return meta
    if not structure["has_v"]:
        meta["cache_present"] = False
        meta["cache_missing_reason"] = "cache_absent"
        return meta

    meta["cache_present"] = True
    meta["cache_applicable"] = None
    meta["cache_missing_reason"] = "cache_applicability_unverified"
    meta["cache_verification"] = "unverified"
    xml_lexeme = structure.get("v_text")
    meta["xml_value_lexeme"] = xml_lexeme
    error_type = structure.get("t")
    if cached_value is None or cached_value == "":
        meta["cache_missing_reason"] = "cache_value_empty" if xml_lexeme in {"", None} else "cache_value_unreadable"
        return meta
    if error_type == "e":
        meta["cache_raw_type"] = "error"
        meta["cache_raw_value"] = json_doc({"raw_type": "error", "lexeme": str(cached_value)})
        meta["cache_missing_reason"] = "cache_error"
        return meta
    if error_type == "s":
        # 共享字符串索引需要共享表才能还原；不猜值，保存在但值 unknown。
        meta["cache_missing_reason"] = "cache_shared_string_unresolved"
        return meta
    typed = _typed_scalar(cached_value)
    meta["cache_raw_type"] = typed["raw_type"]
    if typed["raw_type"] in {"json_integer", "json_decimal"}:
        lexeme = xml_lexeme if xml_lexeme not in {None, ""} else typed["lexeme"]
        try:
            meta["cache_raw_value"] = json_doc(encode_exact_number(str(lexeme)))
        except ValueError:
            meta["cache_raw_type"] = None
            meta["cache_missing_reason"] = "cache_value_unreadable"
            return meta
    elif typed["raw_type"] == "datetime":
        meta["cache_raw_value"] = json_doc({"raw_type": "datetime", "lexeme": typed["lexeme"]})
    elif typed["raw_type"] == "bool":
        meta["cache_raw_value"] = json_doc({"raw_type": "bool", "lexeme": typed["lexeme"]})
    else:
        meta["cache_raw_value"] = json_doc({"raw_type": "string", "lexeme": typed["lexeme"]})
    return meta


def _load_openpyxl(data: bytes) -> tuple[Any, Any]:
    try:
        from openpyxl import load_workbook  # 延迟导入：缺失时不阻断其它格式
    except ImportError as exc:
        raise AttachmentParseError("parser_dependency_missing", "openpyxl is not installed") from exc
    try:
        formulas = load_workbook(io.BytesIO(data), read_only=True, data_only=False, keep_links=False)
        cached = load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
    except Exception as exc:  # 第三方解析异常统一转译，不吞
        raise AttachmentParseError("invalid_structure", "XLSX workbook is unreadable") from exc
    return formulas, cached


def _parse_xlsx(data: bytes, selection: Selection, budget: AttachmentBudget, deadline: float) -> ParsedAttachment:
    _reject_unsupported_selection(selection, (SheetsRange, RowsRange))
    _check_no_overlap(selection, RowsRange)
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise AttachmentParseError("invalid_structure", "XLSX is not a ZIP container")
    formulas, cached = _load_openpyxl(data)
    probe: XlsxCacheProbe | None = None
    try:
        probe = XlsxCacheProbe(data)
        sheet_names = list(formulas.sheetnames)
        if selection.kind == "all":
            selected_sheets = sheet_names
        else:
            selected_sheets = []
            for atom in selection.ranges:
                if isinstance(atom, SheetsRange):
                    for name in atom.names:
                        if name not in sheet_names:
                            raise AttachmentParseError("invalid_selection", "sheet name does not exist in source")
                        if name not in selected_sheets:
                            selected_sheets.append(name)
                else:
                    assert isinstance(atom, RowsRange)
                    if atom.sheet is None or atom.sheet not in sheet_names:
                        raise AttachmentParseError("invalid_selection", "XLSX rows must name an existing sheet")
                    if atom.sheet not in selected_sheets:
                        selected_sheets.append(atom.sheet)
        budget.enforce_sheets(len(selected_sheets))

        blocks: list[str] = []
        limitations: list[dict[str, str]] = []
        total_rows = 0
        total_chars = 0
        read_sheets: list[str] = []
        partial_sheet: str | None = None
        partial_last_row: int = 0
        rows_axis = selection.kind == "limited" and isinstance(selection.ranges[0], RowsRange)

        for sheet_name in selected_sheets:
            target_rows = _xlsx_row_range(selection, sheet_name)
            formula_sheet = formulas[sheet_name]
            cached_sheet = cached[sheet_name]
            rows: list[dict[str, Any]] = []
            row_number = 0
            last_completed_row = 0
            for formula_row, cached_row in zip(formula_sheet.iter_rows(), cached_sheet.iter_rows(), strict=False):
                first_cell = formula_row[0] if formula_row else None
                physical_row = getattr(first_cell, "row", None)
                row_number = physical_row if isinstance(physical_row, int) else row_number + 1
                if target_rows is not None and not (target_rows[0] <= row_number <= target_rows[1]):
                    continue
                if time.monotonic() > deadline:
                    partial_sheet = sheet_name
                    partial_last_row = last_completed_row
                    limitations.append(
                        _limitation(
                            "extract_timeout",
                            "所选范围未在 30 秒内完成，已完成行保留"
                            if rows_axis
                            else "所选范围未在 30 秒内完成，未完成的表记为未读",
                        )
                    )
                    break
                total_rows += 1
                budget.enforce_rows(total_rows)
                formula_refs: set[str] = set()
                for index, formula_cell in enumerate(formula_row, start=1):
                    column = getattr(formula_cell, "column", None)
                    column_index = column if isinstance(column, int) else index
                    value = formula_cell.value
                    if isinstance(value, str) and value.startswith("="):
                        formula_refs.add(f"{_column_letter(column_index)}{row_number}")
                if probe is not None and formula_refs:
                    probe.prefetch(sheet_name, formula_refs)
                cells = []
                for index, (formula_cell, cached_cell) in enumerate(
                    zip(formula_row, cached_row, strict=False), start=1
                ):
                    column = getattr(formula_cell, "column", None)
                    column_index = column if isinstance(column, int) else index
                    cell_ref = f"{_column_letter(column_index)}{row_number}"
                    formula_value = formula_cell.value
                    formula_text = (
                        formula_value if isinstance(formula_value, str) and formula_value.startswith("=") else None
                    )
                    meta = _cache_meta(
                        probe,
                        sheet_name,
                        cell_ref,
                        formula_text,
                        cached_cell.value,
                        getattr(formula_cell, "number_format", None),
                    )
                    payload = _typed_scalar(cached_cell.value if formula_text else formula_value)
                    total_chars += len(str(payload.get("lexeme")))
                    cells.append(
                        {
                            "cell": cell_ref,
                            "value": payload,
                            "formula_text": formula_text,
                            "xlsx_cell_meta": meta,
                        }
                    )
                budget.enforce_codepoints(total_chars)
                rows.append({"row": row_number, "cells": cells})
                last_completed_row = row_number
            if partial_sheet is not None:
                if rows_axis and rows:
                    # rows 轴：已完成行是真实已读内容，必须与 read_ranges 的前缀一致地投递。
                    blocks.append(json_doc({"kind": "xlsx_sheet", "sheet": sheet_name, "rows": rows}))
                else:
                    # sheets 轴/all：整表记未读，不投递半表内容；统计只计已投递行。
                    total_rows -= len(rows)
                break
            read_sheets.append(sheet_name)
            blocks.append(json_doc({"kind": "xlsx_sheet", "sheet": sheet_name, "rows": rows}))

        read_ranges, unread_ranges = _xlsx_read_unread_atoms(
            selection,
            read_sheets=read_sheets,
            partial_sheet=partial_sheet,
            partial_last_row=partial_last_row,
        )
        if partial_sheet is not None:
            read_state = ReadState.PARTIAL
            coverage = "limited"
        else:
            read_state = ReadState.READY
            coverage = "full"

        return ParsedAttachment(
            read_state=read_state,
            coverage=coverage,
            body_text=None,
            blocks=blocks,
            facts=[],
            limitations=limitations,
            stats=_stats(
                source_bytes=len(data),
                sheets=len(read_sheets),
                rows=total_rows,
                chars=total_chars,
            ),
            read_ranges=read_ranges,
            unread_ranges=unread_ranges,
        )
    finally:
        if probe is not None:
            probe.close()
        formulas.close()
        cached.close()


def _xlsx_read_unread_atoms(
    selection: Selection,
    *,
    read_sheets: list[str],
    partial_sheet: str | None,
    partial_last_row: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按选择轴生成契约合法的已读/未读原子（§3.3.1 / D06）。

    - ``selection=all``：只允许空或恰一个 ``all`` 原子；无法表达部分读取，
      故超时时明确拒绝（不把截断伪装成完整读取）。
    - ``selection=sheets``：粒度为整表；未完成的表记未读（保守，不冒充已读）。
    - ``selection=rows``：按每段准确行区间切分已完成前缀与剩余未读。
    """

    if selection.kind == "all":
        if partial_sheet is None:
            return [to_wire_dict(AllAtom())], []
        raise AttachmentParseError(
            "attachment_time_exceeded",
            "所选范围未在 30 秒内完成，且 selection=all 无法表达部分读取",
        )
    read_atoms: list[dict[str, Any]] = []
    unread_atoms: list[dict[str, Any]] = []
    seen_sheets: set[str] = set()
    for atom in selection.ranges:
        if isinstance(atom, SheetsRange):
            read_names: list[str] = []
            unread_names: list[str] = []
            for name in atom.names:
                if name in seen_sheets:
                    continue
                seen_sheets.add(name)
                if partial_sheet is None or name in read_sheets:
                    read_names.append(name)
                else:
                    unread_names.append(name)
            if read_names:
                read_atoms.append(to_wire_dict(SheetsRange(kind="sheets", names=tuple(read_names))))
            if unread_names:
                unread_atoms.append(to_wire_dict(SheetsRange(kind="sheets", names=tuple(unread_names))))
            continue
        assert isinstance(atom, RowsRange)
        assert atom.sheet is not None
        if partial_sheet is None or atom.sheet in read_sheets:
            read_atoms.append(to_wire_dict(atom))
            continue
        if atom.sheet == partial_sheet and partial_last_row >= atom.start:
            read_end = min(atom.end, partial_last_row)
            read_atoms.append(to_wire_dict(RowsRange(kind="rows", sheet=atom.sheet, start=atom.start, end=read_end)))
            if read_end < atom.end:
                unread_atoms.append(
                    to_wire_dict(RowsRange(kind="rows", sheet=atom.sheet, start=read_end + 1, end=atom.end))
                )
        else:
            unread_atoms.append(to_wire_dict(atom))
    return read_atoms, unread_atoms


def _xlsx_row_range(selection: Selection, sheet_name: str) -> tuple[int, int] | None:
    spans = [
        (atom.start, atom.end) for atom in selection.ranges if isinstance(atom, RowsRange) and atom.sheet == sheet_name
    ]
    if not spans:
        return None
    start = min(span[0] for span in spans)
    end = max(span[1] for span in spans)
    selected = sum(span[1] - span[0] + 1 for span in spans)
    if selected != end - start + 1:
        # 非连续多段：读取并集会包含未选行；本版仅支持整段（拒绝而非静默扩读）。
        raise AttachmentParseError("invalid_selection", "non-contiguous XLSX row ranges are not supported")
    return (start, end)


def _column_letter(index: int) -> str:
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


# ---------------------------------------------------------------------------
# PDF / 图片（依赖未装 → 明确能力未核）
# ---------------------------------------------------------------------------


def _parse_pdf(data: bytes, selection: Selection, budget: AttachmentBudget) -> ParsedAttachment:
    _reject_unsupported_selection(selection, (PagesRange,))
    _check_no_overlap(selection, PagesRange)
    try:
        from pypdf import PdfReader  # type: ignore[reportMissingImports]  # 可选依赖：未安装时明确能力未核
    except ImportError as exc:
        raise AttachmentParseError("parser_dependency_missing", "pypdf is not installed") from exc
    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
        total_pages = len(reader.pages)
        if selection.kind == "all":
            budget.enforce_pages(total_pages)
            pages = list(range(total_pages))
            read_ranges: list[dict[str, Any]] = [to_wire_dict(AllAtom())]
        else:
            pages = []
            read_ranges = []
            for atom in selection.ranges:
                assert isinstance(atom, PagesRange)
                if atom.end > total_pages:
                    raise AttachmentParseError("invalid_selection", "pages range exceeds selected source")
                pages.extend(range(atom.start - 1, atom.end))
                read_ranges.append(to_wire_dict(atom))
            budget.enforce_pages(len(pages))
        texts: list[str] = []
        limitations: list[dict[str, str]] = []
        for page_index in pages:
            text = reader.pages[page_index].extract_text() or ""
            if not text.strip():
                limitations.append(_limitation("page_text_unavailable", f"第 {page_index + 1} 页无可提取文本"))
            texts.append(text)
        body = "\n".join(texts)
        budget.enforce_codepoints(len(body))
        return ParsedAttachment(
            read_state=ReadState.PARTIAL if limitations else ReadState.READY,
            coverage="limited" if limitations else "full",
            body_text=body,
            limitations=limitations,
            stats=_stats(source_bytes=len(data), pages=len(pages), chars=len(body)),
            read_ranges=read_ranges,
        )
    except AttachmentParseError:
        raise
    except Exception as exc:  # 第三方解析异常统一转译
        raise AttachmentParseError("invalid_structure", "PDF structure is unreadable") from exc


def _parse_image(data: bytes, selection: Selection) -> ParsedAttachment:
    _reject_unsupported_selection(selection, ())
    try:
        from PIL import Image  # 延迟导入：未安装时明确能力未核
    except ImportError as exc:
        raise AttachmentParseError("parser_dependency_missing", "Pillow is not installed") from exc
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            image_format = image.format
    except Exception as exc:  # 第三方解码异常统一转译
        raise AttachmentParseError("invalid_structure", "image cannot be decoded") from exc
    block = json_doc({"kind": "image", "width": width, "height": height, "format": image_format})
    return ParsedAttachment(
        read_state=ReadState.READY,
        coverage="full",
        body_text=None,
        blocks=[block],
        limitations=[_limitation("image_text_not_extracted", "图片仅核验可解码，未 OCR")],
        stats=_stats(source_bytes=len(data), chars=len(block)),
        read_ranges=[to_wire_dict(AllAtom())],
    )


# ---------------------------------------------------------------------------
# 分发与 worker 入口
# ---------------------------------------------------------------------------


def parse_attachment(
    *,
    kind: AttachmentKind,
    data: bytes,
    selection: Selection,
    budget: AttachmentBudget,
    deadline: float,
) -> ParsedAttachment:
    """按种类解析显式选择范围（纯函数；不读写外部路径）。"""

    try:
        return _dispatch_parse(kind=kind, data=data, selection=selection, budget=budget, deadline=deadline)
    except AgentValidationError as exc:
        # D06 限额违规统一转为解析错误码（保持稳定 code，不冒充完整读取）。
        raise AttachmentParseError(exc.code, "attachment exceeds D06 limit") from exc


def _dispatch_parse(
    *,
    kind: AttachmentKind,
    data: bytes,
    selection: Selection,
    budget: AttachmentBudget,
    deadline: float,
) -> ParsedAttachment:
    knit = kind
    if knit in (AttachmentKind.TXT, AttachmentKind.MD):
        return _parse_text(data, selection, budget)
    if knit is AttachmentKind.CSV:
        return _parse_csv(data, selection, budget)
    if knit is AttachmentKind.JSON:
        return _parse_json(data, selection, budget)
    if knit is AttachmentKind.XLSX:
        return _parse_xlsx(data, selection, budget, deadline)
    if knit is AttachmentKind.PDF:
        return _parse_pdf(data, selection, budget)
    if knit in (AttachmentKind.PNG, AttachmentKind.JPEG):
        return _parse_image(data, selection)
    raise AttachmentParseError("unsupported_kind", f"attachment kind {kind.value!r} is not supported")


def _read_exact(stream: Any, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            raise AttachmentParseError("invalid_request", "attachment payload is truncated")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _handle_request(header: dict[str, Any], payload: bytes) -> dict[str, Any]:
    try:
        expected_digest = header["source_digest"]
        if not isinstance(expected_digest, str) or not expected_digest:
            raise AttachmentParseError("invalid_request", "worker source_digest is invalid")
        if hashlib.sha256(payload).hexdigest() != expected_digest:
            raise AttachmentParseError(
                "attachment_digest_mismatch", "attachment payload does not match declared source digest"
            )
        kind = AttachmentKind(header["kind"])
        selection = decode_selection(header["selection"], "$.selection")
        budget = AttachmentBudget(**header.get("budget", {}))
        deadline = time.monotonic() + float(header.get("timeout_s", 30.0))
        parsed = parse_attachment(
            kind=kind,
            data=payload,
            selection=selection,
            budget=budget,
            deadline=deadline,
        )
        return {"ok": True, "schema_version": 1, "result": parsed.to_wire()}
    except AttachmentParseError as exc:
        return {"ok": False, "schema_version": 1, "error": {"code": exc.code, "message": exc.message}}
    except (KeyError, TypeError, ValueError) as exc:
        return {
            "ok": False,
            "schema_version": 1,
            "error": {"code": "invalid_request", "message": f"worker request is invalid: {type(exc).__name__}"},
        }


def main() -> int:
    """子进程入口：stdin 头 JSON（单行）+ 原始字节；stdout 单行结果 JSON。"""

    try:
        header_line = sys.stdin.buffer.readline(MAX_HEADER_BYTES + 1)
        if len(header_line) > MAX_HEADER_BYTES:
            raise AttachmentParseError("invalid_request", "worker header is too large")
        if not header_line:
            raise AttachmentParseError("invalid_request", "worker header is missing")
        header = json.loads(header_line.decode("utf-8"))
        if not isinstance(header, dict):
            raise AttachmentParseError("invalid_request", "worker header must be an object")
        payload_size = header.get("payload_bytes")
        if not isinstance(payload_size, int) or payload_size < 0:
            raise AttachmentParseError("invalid_request", "worker payload_bytes is invalid")
        payload = _read_exact(sys.stdin.buffer, payload_size)
        response = _handle_request(header, payload)
    except AttachmentParseError as exc:
        response = {"ok": False, "schema_version": 1, "error": {"code": exc.code, "message": exc.message}}
    except (json.JSONDecodeError, UnicodeDecodeError):
        response = {
            "ok": False,
            "schema_version": 1,
            "error": {"code": "invalid_request", "message": "worker header is not valid JSON"},
        }
    encoded = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_RESULT_BYTES:
        encoded = json.dumps(
            {
                "ok": False,
                "schema_version": 1,
                "error": {"code": "result_too_large", "message": "worker result exceeds bounded size"},
            },
            separators=(",", ":"),
        ).encode("utf-8")
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":  # pragma: no cover - 子进程入口
    sys.exit(main())
