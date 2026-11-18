"""受管子进程附件解析 worker（REQ D06 / DD §10.4）。

设计要点：
- 本模块既作为受监督子进程入口（``python -m services.agent_service.attachment_worker``），
  也提供纯解析函数供单元测试直接调用；
- 解析只读取显式选择范围；未选范围不解析、不计数；超限明确拒绝或按超时返回
  真实已读/未读（不截断冒完整）；
- XLSX 先做容器级前置预算与 workbook 元数据结构门（zip 条目数/声明解压量/
  sharedStrings 尺寸等），通过后才创建公式与缓存两个只读视图（共享同一
  sharedStrings 解析），按 ``(sheet, coordinate)`` 逐行逐格核配公式与缓存
  （行数/坐标不一致即如实上报为未读，不静默截断配对），并有界探测 worksheet
  XML 区分缓存不存在/存在但空/错误/类型不可核；单元格类型按 XML ``t``/``<f>``
  判定（文本以 ``=`` 开头仍是文本），数值/日期保留原 lexeme/serial 与 epoch；
- PDF / PNG / JPEG 解析依赖（pypdf / Pillow）已按 Q5/Q3 裁决纳入正式依赖；
  结构不可读仍明确 ``invalid_structure``，不伪造解码结果；图像在容器校验通过后
  按声明尺寸核像素与解码字节预算，再 ``load()`` 真像素解码，解码失败即拒绝；
- 图像字符数不适用（``chars`` 为 ``None``），0 字符不代表图像已核；模型兼容与
  上下文窗口由派发层判定，本层不据此推断已兼容。
"""

from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import itertools
import json
import sys
import time
import unicodedata
import warnings
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time
from pathlib import PurePosixPath
from typing import Any, Final
from xml.etree import ElementTree

from PIL import Image
from pypdf import PdfReader

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


def _csv_header_offset(records: list[list[str]], header_confirmed: bool) -> int:
    """返回应从记录行额度中扣除的表头行数（0 或 1）。

    只有「用户已确认」且「实际存在（首条非空行）」的表头才扣（I23）：注释或
    推断出的「表头」不等于用户确认，无确认时首条按数据行计入，避免 50001 条
    无表头数据被按 50000 条通过限额；首条为空行时没有表头可扣。
    """

    if not header_confirmed or not records or not records[0]:
        return 0
    return 1


def _csv_count_data_rows(records: list[list[str]], start_index: int, end_index: int, header_offset: int) -> int:
    """统计 ``[start_index, end_index)`` 内的数据行：空行不算记录，已确认表头不占额度。"""

    return sum(1 for index in range(start_index, end_index) if index >= header_offset and records[index])


def _parse_csv(data: bytes, selection: Selection, budget: AttachmentBudget, header_confirmed: bool) -> ParsedAttachment:
    _reject_unsupported_selection(selection, (RowsRange,))
    _check_no_overlap(selection, RowsRange)
    records = _csv_records(data)
    # 行号含表头（§3.3.1 rows：「表头不隐式排除」）；记录行限额只扣除已确认且实际存在的表头。
    total_records = len(records)
    header_offset = _csv_header_offset(records, header_confirmed)

    if selection.kind == "all":
        budget.enforce_rows(_csv_count_data_rows(records, 0, total_records, header_offset))
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
            # D06 记录行限额按本文件累计（不按每个行段重置）；仅已确认表头不计入额度。
            selected_data_rows += _csv_count_data_rows(records, atom.start - 1, atom.end, header_offset)
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


#: JSON 根首个非空白字符的合法集合（RFC 8259：对象/数组/字符串/数字/布尔/null）。
_JSON_ROOT_STARTERS: Final = frozenset('{["-0123456789tfn')


def decode_json_text(data: bytes) -> str:
    """统一 JSON 文本解码入口（剥离 UTF-8 BOM）；父进程嗅探与 worker 解析共用（I27）。"""

    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AttachmentParseError("invalid_encoding", "JSON is not valid UTF-8 text") from exc


def validate_json_root(data: bytes) -> None:
    """JSON 根形态核验：对象/数组与标量根（字符串/数字/布尔/null）均合法。

    父进程内容嗅探与 worker 解析共用同一支持范围与同一错误码/消息，避免两套
    判据漂移（I27）：合法 BOM 剥离后前导空白不计；非 UTF-8 与非法结构分别
    报 ``invalid_encoding`` / ``invalid_structure``（与 :func:`_parse_json` 一致）。
    """

    text = decode_json_text(data).lstrip()
    if not text or text[0] not in _JSON_ROOT_STARTERS:
        raise AttachmentParseError("invalid_structure", "JSON structure is invalid")


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
    text = decode_json_text(data)
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
#: 实际行范围核定的行元素扫描上限（Excel 单表最大行数为 1,048,576）。
MAX_PROBED_ROWS: Final = 1_048_576
#: XLSX 容器前置预算（I08）：在 openpyxl 分配前按中央目录声明值拒绝解压炸弹。
MAX_XLSX_ENTRIES: Final = 4_096
MAX_XLSX_ENTRY_BYTES: Final = 64 * 1024 * 1024
MAX_XLSX_TOTAL_BYTES: Final = 256 * 1024 * 1024
MAX_XLSX_METADATA_BYTES: Final = 8 * 1024 * 1024
MAX_XLSX_SHARED_STRINGS_BYTES: Final = 32 * 1024 * 1024
MAX_XLSX_STYLES_BYTES: Final = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class XlsxMetadata:
    """容器预检得到的 workbook 元数据（只在预算门通过后解析一次）。"""

    sheet_paths: dict[str, str]
    date_epoch: str


def _xlsx_entry_key(name: str) -> str:
    """规范化条目名（NFKC + casefold），用于识别大小写/兼容字符变体重名。"""

    return "/".join(unicodedata.normalize("NFKC", part).casefold() for part in PurePosixPath(name).parts)


def _check_xlsx_entry_name(name: str) -> str:
    """拒绝绝对路径、盘符、反斜杠、父目录与空组件；返回规范化的 POSIX 名。"""

    if not name or "\x00" in name or "\\" in name:
        raise AttachmentParseError("invalid_structure", "XLSX container has unsafe entry names")
    pure = PurePosixPath(name)
    if pure.is_absolute() or pure.drive or pure.root:
        raise AttachmentParseError("invalid_structure", "XLSX container has unsafe entry names")
    if any(part in {"..", ""} for part in pure.parts):
        raise AttachmentParseError("invalid_structure", "XLSX container has unsafe entry names")
    first = pure.parts[0]
    # zipfile 会把反斜杠归一化为 "/"，故 "C:/x" 需按盘符前缀拒绝（与 skill 包预检一致）。
    if len(first) == 2 and first[1] == ":" and first[0].isalpha():
        raise AttachmentParseError("invalid_structure", "XLSX container has unsafe entry names")
    return "/".join(pure.parts)


def _reject_xlsx_xml_entities(payload: bytes) -> None:
    """元数据解析前拒绝 DTD/实体声明（与 worksheet 探测的实体门一致）。"""

    if b"<!DOCTYPE" in payload or b"<!ENTITY" in payload:
        raise AttachmentParseError("invalid_structure", "XLSX XML entities are rejected")


def _read_xlsx_part(archive: zipfile.ZipFile, name: str, limit: int) -> bytes:
    """有界读取元数据项：声明尺寸与实读字节均不超过限额。"""

    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise AttachmentParseError("invalid_structure", "XLSX workbook metadata is missing or unreadable") from exc
    if info.file_size > limit:
        raise AttachmentParseError("invalid_structure", "XLSX metadata part exceeds the preflight limit")
    try:
        with archive.open(info) as handle:
            payload = handle.read(limit + 1)
    except (zipfile.BadZipFile, OSError) as exc:
        raise AttachmentParseError("invalid_structure", "XLSX workbook metadata is unreadable") from exc
    if len(payload) > limit:
        raise AttachmentParseError("invalid_structure", "XLSX metadata part exceeds the preflight limit")
    return payload


def _inspect_xlsx_container(data: bytes) -> XlsxMetadata:
    """在 openpyxl 分配前完成容器预算与结构门（I08）。

    仅读中央目录声明值与 workbook 元数据；任一超限/异常结构在展开 worksheet
    之前以 ``invalid_structure`` 拒绝，失败路径不落盘、不产生临时文件。
    """

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise AttachmentParseError("invalid_structure", "XLSX is not a ZIP container") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_XLSX_ENTRIES:
            raise AttachmentParseError("invalid_structure", "XLSX container entry count exceeds the limit")
        by_name: dict[str, zipfile.ZipInfo] = {}
        seen: set[str] = set()
        total_declared = 0
        for info in infos:
            name = _check_xlsx_entry_name(info.filename)
            key = _xlsx_entry_key(name)
            if key in seen:
                raise AttachmentParseError("invalid_structure", "XLSX container has duplicate entry names")
            seen.add(key)
            if info.flag_bits & 0x1:
                raise AttachmentParseError("invalid_structure", "XLSX container has encrypted entries")
            mode = info.external_attr >> 16
            if mode and (mode & 0o170000) == 0o120000:
                raise AttachmentParseError("invalid_structure", "XLSX container has symbolic link entries")
            if info.file_size > MAX_XLSX_ENTRY_BYTES:
                raise AttachmentParseError("invalid_structure", "XLSX entry expands beyond the per-entry limit")
            total_declared += info.file_size
            if total_declared > MAX_XLSX_TOTAL_BYTES:
                raise AttachmentParseError("invalid_structure", "XLSX entries expand beyond the total limit")
            by_name[name] = info

        if "[Content_Types].xml" not in by_name:
            raise AttachmentParseError("invalid_structure", "XLSX workbook metadata is missing or unreadable")
        content_types = by_name["[Content_Types].xml"]
        if content_types.file_size > MAX_XLSX_METADATA_BYTES:
            raise AttachmentParseError("invalid_structure", "XLSX metadata part exceeds the preflight limit")
        shared_strings = by_name.get("xl/sharedStrings.xml")
        if shared_strings is not None and shared_strings.file_size > MAX_XLSX_SHARED_STRINGS_BYTES:
            raise AttachmentParseError("invalid_structure", "XLSX shared strings part exceeds the preflight limit")
        styles = by_name.get("xl/styles.xml")
        if styles is not None and styles.file_size > MAX_XLSX_STYLES_BYTES:
            raise AttachmentParseError("invalid_structure", "XLSX styles part exceeds the preflight limit")

        workbook_bytes = _read_xlsx_part(archive, "xl/workbook.xml", MAX_XLSX_METADATA_BYTES)
        rels_bytes = _read_xlsx_part(archive, "xl/_rels/workbook.xml.rels", MAX_XLSX_METADATA_BYTES)
        _reject_xlsx_xml_entities(workbook_bytes)
        _reject_xlsx_xml_entities(rels_bytes)
        try:
            workbook = ElementTree.fromstring(workbook_bytes)
            rels = ElementTree.fromstring(rels_bytes)
        except ElementTree.ParseError as exc:
            raise AttachmentParseError("invalid_structure", "XLSX workbook metadata is unreadable") from exc

    props = workbook.find(f"{_NS_MAIN}workbookPr")
    date_epoch = "1904" if props is not None and props.get("date1904") in {"1", "true"} else "1900"
    targets = {
        rel.get("Id"): (rel.get("Target", ""), rel.get("Type", "")) for rel in rels.findall(f"{_NS_REL}Relationship")
    }
    sheet_paths: dict[str, str] = {}
    for sheet in workbook.findall(f"{_NS_MAIN}sheets/{_NS_MAIN}sheet"):
        name = sheet.get("name", "")
        rel_id = sheet.get(f"{_NS_OFFICE_REL}id", "")
        target, rel_type = targets.get(rel_id, ("", ""))
        # 图表页不能按 worksheet API 迭代；在结构门拒绝而不是后续 AttributeError。
        if not name or not target or rel_type.endswith("/chartsheet"):
            continue
        normalized = target.lstrip("/")
        if not normalized.startswith("xl/"):
            normalized = f"xl/{normalized}"
        pure = PurePosixPath(normalized)
        if pure.is_absolute() or any(part in {"..", ""} for part in pure.parts):
            raise AttachmentParseError("invalid_structure", "XLSX sheet relationship target is unsafe")
        normalized = "/".join(pure.parts)
        if normalized not in by_name:
            continue
        sheet_paths[name] = normalized
    return XlsxMetadata(sheet_paths=sheet_paths, date_epoch=date_epoch)


class XlsxCacheProbe:
    """有界读取 worksheet XML，区分缓存缺失/存在但空/错误/类型不可核（§10.4）。"""

    def __init__(self, data: bytes, metadata: XlsxMetadata) -> None:
        self._zip = zipfile.ZipFile(io.BytesIO(data))
        self._sheet_paths = dict(metadata.sheet_paths)
        self._cache: dict[tuple[str, str], dict[str, Any] | None] = {}
        self._row_extents: dict[str, int | None] = {}
        self._probed_cells = 0
        self.date_epoch = metadata.date_epoch

    def close(self) -> None:
        self._zip.close()

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

    def row_extent(self, sheet: str) -> int | None:
        """返回工作表实际存在的最大物理行号；结构不可读/超界返回 None（未知）。

        以 worksheet XML 的 ``<row>`` 元素为准，不信任 ``<dimension>`` 声明：
        声明可能大于实际内容（openpyxl 会按声明补空行）或小于实际内容
        （openpyxl 会按声明提前截断）。无法核定时返回 None，调用方必须按
        未读/部分真实上报，不得据此宣称完整读取（I20）。
        """

        if sheet in self._row_extents:
            return self._row_extents[sheet]
        path = self._sheet_paths.get(sheet)
        extent: int | None = None
        if path is not None:
            try:
                info = self._zip.getinfo(path)
                if info.file_size > MAX_SHEET_XML_BYTES:
                    raise KeyError(path)
                with self._zip.open(path) as handle:
                    head = handle.read(8192)
                    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
                        raise AttachmentParseError("invalid_structure", "XLSX XML entities are rejected")
                    handle.seek(0)
                    best = 0
                    current = 0
                    scanned = 0
                    for _event, element in ElementTree.iterparse(handle, events=("end",)):
                        if element.tag != f"{_NS_MAIN}row":
                            continue
                        ref = element.get("r")
                        current = int(ref) if ref and ref.isdigit() else current + 1
                        best = max(best, current)
                        scanned += 1
                        element.clear()
                        if scanned > MAX_PROBED_ROWS:
                            raise KeyError(path)
                    extent = best
            except (KeyError, zipfile.BadZipFile, ElementTree.ParseError, OSError):
                extent = None
        self._row_extents[sheet] = extent
        return extent

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
                        "has_f": element.find(f"{_NS_MAIN}f") is not None,
                        "has_v": value_node is not None,
                        "v_text": None if value_node is None else (value_node.text or ""),
                    }
                    remaining.discard(ref)
                element.clear()
                if not remaining or self._probed_cells > MAX_PROBED_CELLS:
                    break
        return found


#: Excel 错误值词法（openpyxl 以普通字符串返回，XML 以 ``t="e"`` 标注）。
_EXCEL_ERROR_LEXEMES: Final = frozenset(
    {"#NULL!", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A", "#GETTING_DATA", "#SPILL!", "#CALC!"}
)


def _is_error_lexeme(value: Any) -> bool:
    return isinstance(value, str) and value in _EXCEL_ERROR_LEXEMES


def _typed_scalar(
    value: Any,
    *,
    xml_lexeme: str | None = None,
    date_epoch: str | None = None,
    xml_type: str | None = None,
) -> dict[str, Any]:
    """openpyxl 标量 → 带类型精确表示（不引入 float 往返，保留 XML 原证据）。

    - ``xml_type="e"`` 或值本身是 Excel 错误词法 → ``error``（不与 string 混同）；
    - 数值保留 XML ``<v>`` 原 lexeme（``lexeme`` 为 openpyxl 值表示，
      ``unsupported_precision`` 标记其可能已丢精度）；
    - 日期/时间保留序列号（``serial``）与 workbook epoch（``date_epoch``）。
    """

    if xml_type == "e" or _is_error_lexeme(value):
        return {"raw_type": "error", "lexeme": None if value is None else str(value)}
    if value is None:
        return {"raw_type": "null", "lexeme": None}
    if isinstance(value, bool):
        return {"raw_type": "bool", "lexeme": str(value)}
    if isinstance(value, int):
        typed: dict[str, Any] = {"raw_type": "json_integer", "lexeme": str(value)}
        if xml_lexeme:
            typed["xml_lexeme"] = xml_lexeme
        return typed
    if isinstance(value, float):
        typed = {"raw_type": "json_decimal", "lexeme": repr(value), "unsupported_precision": True}
        if xml_lexeme:
            typed["xml_lexeme"] = xml_lexeme
        return typed
    if isinstance(value, (datetime, date, dt_time)):
        typed = {"raw_type": "datetime", "lexeme": value.isoformat()}
        if xml_lexeme:
            typed["serial"] = xml_lexeme
        if date_epoch is not None and not isinstance(value, dt_time):
            typed["date_epoch"] = date_epoch
        return typed
    return {"raw_type": "string", "lexeme": str(value)}


def _formula_text(formula_cell: Any, xml_has_formula: bool | None) -> str | None:
    """判定单元格公式：以 XML ``<f>`` 与 openpyxl ``data_type`` 为证据（I22）。

    文本以 ``=`` 开头但 XML 无 ``<f>`` 时仍是文本，不得误作公式；XML 结构不可核
    （超界/不可读）时退回 openpyxl 的 ``data_type == "f"``（而非字符串前缀猜测）。
    """

    value = formula_cell.value
    if isinstance(value, str):
        candidate = value
    else:
        text = getattr(value, "text", None)
        candidate = text if isinstance(text, str) else None
    if candidate is None or not candidate.startswith("="):
        return None
    is_formula = xml_has_formula
    if is_formula is None:
        is_formula = getattr(formula_cell, "data_type", None) == "f"
    return candidate if is_formula else None


def _row_streams_aligned(formula_row: Any, cached_row: Any) -> bool:
    """按坐标核配同一行的公式视图与缓存视图；长度或坐标不一致即不可核配。"""

    if len(formula_row) != len(cached_row):
        return False
    for formula_cell, cached_cell in zip(formula_row, cached_row, strict=False):
        left = getattr(formula_cell, "coordinate", None)
        right = getattr(cached_cell, "coordinate", None)
        if left is None and right is None:
            # 空单元格填充（openpyxl ``EMPTY_CELL``）无坐标，按索引对齐。
            continue
        if left != right:
            return False
    return True


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
    meta["date_epoch"] = probe.date_epoch if probe is not None else None
    structure = probe.probe(sheet, cell_ref) if probe is not None else None
    if structure is not None:
        # 非公式单元格同样保留 XML 原值证据（数值 lexeme / 日期 serial）。
        meta["xml_value_lexeme"] = structure.get("v_text")
    if formula_text is None:
        return meta

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
    typed = _typed_scalar(cached_value, xml_lexeme=xml_lexeme, date_epoch=probe.date_epoch if probe else None)
    meta["cache_raw_type"] = typed["raw_type"]
    if typed["raw_type"] == "error":
        meta["cache_raw_value"] = json_doc({"raw_type": "error", "lexeme": typed["lexeme"]})
        meta["cache_missing_reason"] = "cache_error"
        return meta
    if typed["raw_type"] in {"json_integer", "json_decimal"}:
        lexeme = xml_lexeme if xml_lexeme not in {None, ""} else typed["lexeme"]
        try:
            meta["cache_raw_value"] = json_doc(encode_exact_number(str(lexeme)))
        except ValueError:
            meta["cache_raw_type"] = None
            meta["cache_missing_reason"] = "cache_value_unreadable"
            return meta
    elif typed["raw_type"] == "datetime":
        cached: dict[str, Any] = {"raw_type": "datetime", "lexeme": typed["lexeme"]}
        if "serial" in typed:
            cached["serial"] = typed["serial"]
        if "date_epoch" in typed:
            cached["date_epoch"] = typed["date_epoch"]
        meta["cache_raw_value"] = json_doc(cached)
    elif typed["raw_type"] == "bool":
        meta["cache_raw_value"] = json_doc({"raw_type": "bool", "lexeme": typed["lexeme"]})
    else:
        meta["cache_raw_value"] = json_doc({"raw_type": "string", "lexeme": typed["lexeme"]})
    return meta


def _load_openpyxl(data: bytes) -> tuple[Any, Any]:
    """打开公式与缓存两个只读视图；sharedStrings 只解析一次并在两者间复用（I08）。"""

    try:
        from openpyxl.reader.excel import ExcelReader  # 延迟导入：缺失时不阻断其它格式
        from openpyxl.styles.stylesheet import apply_stylesheet
    except ImportError as exc:
        raise AttachmentParseError("parser_dependency_missing", "openpyxl is not installed") from exc

    def _open_view(*, data_only: bool, shared_strings: list[Any] | None) -> tuple[Any, list[Any]]:
        # 复用 ExcelReader.read() 的同一读取顺序，但共享表只解析一次（第二次注入结果）。
        reader = ExcelReader(io.BytesIO(data), read_only=True, data_only=data_only, keep_links=False)
        opened = False
        try:
            reader.read_manifest()
            if shared_strings is None:
                reader.read_strings()
                strings = reader.shared_strings
            else:
                reader.shared_strings = shared_strings
                strings = shared_strings
            reader.read_workbook()
            reader.read_properties()
            reader.read_custom()
            reader.read_theme()
            apply_stylesheet(reader.archive, reader.wb)
            reader.read_worksheets()
            reader.parser.assign_names()
            opened = True
        finally:
            # 未完整打开时自身 archive 必须关闭（含取消/中断），不把句柄留给 GC。
            if not opened:
                reader.archive.close()
        return reader.wb, strings

    # 首个视图打开成功即纳入清理：第二个视图构造失败、或本函数后续任何失败
    # （含取消/中断）都在退出 ExitStack 时关闭已打开的 workbook 及其 archive，
    # 不把 zip 句柄留给长期驻留进程的 GC（I38）。两者都成功则 pop_all 撤销登记，
    # 所有权移交调用方（由 _parse_xlsx 的 finally 关闭）。
    with contextlib.ExitStack() as stack:
        try:
            formulas, strings = _open_view(data_only=False, shared_strings=None)
            stack.callback(formulas.close)
            cached, _ = _open_view(data_only=True, shared_strings=strings)
            stack.callback(cached.close)
        except AttachmentParseError:
            raise
        except Exception as exc:  # 第三方解析异常统一转译，不吞
            raise AttachmentParseError("invalid_structure", "XLSX workbook is unreadable") from exc
        stack.pop_all()
    return formulas, cached


def _select_xlsx_sheets(selection: Selection, sheet_names: list[str]) -> list[str]:
    """在展开/分配前完成工作表结构门（不存在的表名直接拒绝）。"""

    if selection.kind == "all":
        return sheet_names
    selected_sheets: list[str] = []
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
    return selected_sheets


def _parse_xlsx(data: bytes, selection: Selection, budget: AttachmentBudget, deadline: float) -> ParsedAttachment:
    _reject_unsupported_selection(selection, (SheetsRange, RowsRange))
    _check_no_overlap(selection, RowsRange)
    metadata = _inspect_xlsx_container(data)
    selected_sheets = _select_xlsx_sheets(selection, list(metadata.sheet_paths))
    budget.enforce_sheets(len(selected_sheets))
    formulas, cached = _load_openpyxl(data)
    probe: XlsxCacheProbe | None = None
    try:
        probe = XlsxCacheProbe(data, metadata)

        blocks: list[str] = []
        limitations: list[dict[str, str]] = []
        total_rows = 0
        frozen_chars = 0
        read_sheets: list[str] = []
        row_read_ends: dict[str, int] = {}
        unverified_sheets: set[str] = set()
        mismatched_sheets: set[str] = set()
        partial_sheet: str | None = None
        partial_last_row: int = 0
        rows_axis = selection.kind == "limited" and isinstance(selection.ranges[0], RowsRange)

        for sheet_name in selected_sheets:
            target_spans = _xlsx_row_spans(selection, sheet_name)
            target_rows = (
                None
                if target_spans is None
                else (min(span[0] for span in target_spans), max(span[1] for span in target_spans))
            )
            extent = probe.row_extent(sheet_name)
            formula_sheet = formulas[sheet_name]
            cached_sheet = cached[sheet_name]
            rows: list[dict[str, Any]] = []
            # 单表冻结块（``json_doc`` 规范 JSON）的空表长度：块长 = 本值 + 各行规范文本
            # 长度 + 行间逗号，与 ``len(block)`` 同一口径（码点）。
            sheet_base_chars = len(json_doc({"kind": "xlsx_sheet", "sheet": sheet_name, "rows": []}))
            rows_chars = 0
            row_number = 0
            last_completed_row = 0
            timed_out = False
            if extent is None:
                # 实际行范围无法核定：按未读上报，不把未知伪装成完整读取（R21/I20）。
                unverified_sheets.add(sheet_name)
                limitations.append(_limitation("sheet_extent_unverified", "工作表实际行范围无法核定，所选范围记为未读"))
                continue
            # 只迭代到 XML 实测的最大物理行，不信任 <dimension> 声明：声明大于实际时
            # openpyxl 会补空行，声明小于实际时会提前截断，两者都会让已读范围失真。
            formula_rows = formula_sheet.iter_rows(max_row=extent) if extent > 0 else ()
            cached_rows = cached_sheet.iter_rows(max_row=extent) if extent > 0 else ()
            stream_mismatch = False
            # 双流必须按坐标逐行核配：任一视图行数不足或单元格坐标不一致都不得
            # 静默截断/错位配对（I22），否则投递值会张冠李戴。
            for formula_row, cached_row in itertools.zip_longest(formula_rows, cached_rows):
                if formula_row is None or cached_row is None or not _row_streams_aligned(formula_row, cached_row):
                    stream_mismatch = True
                    break
                first_cell = formula_row[0] if formula_row else None
                physical_row = getattr(first_cell, "row", None)
                row_number = physical_row if isinstance(physical_row, int) else row_number + 1
                if target_rows is not None and not (target_rows[0] <= row_number <= target_rows[1]):
                    continue
                if time.monotonic() > deadline:
                    timed_out = True
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
                cell_pairs: list[tuple[str, Any, Any]] = []
                for index, (formula_cell, cached_cell) in enumerate(
                    zip(formula_row, cached_row, strict=False), start=1
                ):
                    column = getattr(formula_cell, "column", None)
                    column_index = column if isinstance(column, int) else index
                    cell_pairs.append((f"{_column_letter(column_index)}{row_number}", formula_cell, cached_cell))
                if probe is not None and cell_pairs:
                    # 全部单元格都需要 XML 证据（类型/原 lexeme/serial），不只公式格。
                    probe.prefetch(sheet_name, {cell_ref for cell_ref, _formula, _cached in cell_pairs})
                cells = []
                for cell_ref, formula_cell, cached_cell in cell_pairs:
                    structure = probe.probe(sheet_name, cell_ref) if probe is not None else None
                    xml_type = structure.get("t") if structure is not None else None
                    xml_has_formula = structure.get("has_f") if structure is not None else None
                    formula_value = formula_cell.value
                    formula_text = _formula_text(formula_cell, xml_has_formula)
                    meta = _cache_meta(
                        probe,
                        sheet_name,
                        cell_ref,
                        formula_text,
                        cached_cell.value,
                        getattr(formula_cell, "number_format", None),
                    )
                    payload = _typed_scalar(
                        cached_cell.value if formula_text is not None else formula_value,
                        xml_lexeme=meta["xml_value_lexeme"],
                        date_epoch=probe.date_epoch if probe is not None else None,
                        xml_type=xml_type,
                    )
                    if structure is not None:
                        # t 属性缺失即数值默认类型：如实记录证据（null 表示无该属性）。
                        payload["xml_type"] = xml_type
                    else:
                        # XML 结构不可核（无该单元格元素/探测预算耗尽）：不冒充已取证。
                        payload["xml_evidence_unverified"] = True
                    if formula_text is None and isinstance(formula_value, str) and formula_value.startswith("="):
                        # 文本以「=」开头仍是文本，但保留「看起来像公式」的事实标注。
                        payload["looks_like_formula"] = True
                    cells.append(
                        {
                            "cell": cell_ref,
                            "value": payload,
                            "formula_text": formula_text,
                            "xlsx_cell_meta": meta,
                        }
                    )
                # 字符预算计「冻结完整表示」：整行规范 JSON（含公式文本、缓存表示、
                # 单元格引用与分隔符），不是只计词法单元。与 MAX_RESULT_BYTES 的
                # 传输字节闸门相互独立，谁都不能替代谁。
                row_doc = json_doc({"row": row_number, "cells": cells})
                rows_chars += len(row_doc) + (1 if rows else 0)
                budget.enforce_codepoints(frozen_chars + sheet_base_chars + rows_chars)
                rows.append({"row": row_number, "cells": cells})
                last_completed_row = row_number
            if stream_mismatch:
                # 双流无法按坐标核配：不静默配对/截断，整表内容记为未读并如实上报。
                total_rows -= len(rows)
                unverified_sheets.add(sheet_name)
                mismatched_sheets.add(sheet_name)
                limitations.append(
                    _limitation(
                        "xlsx_dual_stream_mismatch",
                        "公式视图与缓存视图的行或单元格坐标不一致，本表内容记为未读",
                    )
                )
                continue
            if rows_axis and target_spans is not None and len(target_spans) > 1 and rows:
                # 多段选择按选择顺序逐段投递（段内仍按物理行号升序）：反序相邻段
                # （如先 3 再 1—2）不得被 min/max 归并成升序，否则投递内容顺序与
                # read_ranges 原子顺序不一致，原子表示（及由其计算的 digest）失真。
                # 只遍历已读行（不展开段区间），避免超大段号触发无界循环。
                rows = [
                    row for span_start, span_end in target_spans for row in rows if span_start <= row["row"] <= span_end
                ]
            block = json_doc({"kind": "xlsx_sheet", "sheet": sheet_name, "rows": rows})
            if timed_out:
                if rows_axis and rows:
                    # rows 轴：已完成行是真实已读内容，必须与 read_ranges 的前缀一致地投递。
                    blocks.append(block)
                    frozen_chars += len(block)
                else:
                    # sheets 轴/all：整表记未读，不投递半表内容；统计只计已投递行。
                    total_rows -= len(rows)
                break
            if rows_axis:
                assert target_rows is not None
                # 已读区间以实际迭代到的行为准（而非原样回报选择范围）。
                read_end = min(target_rows[1], extent)
                if read_end < target_rows[1]:
                    limitations.append(
                        _limitation("rows_out_of_range", "所选行范围超出工作表实际内容，越界部分记为未读")
                    )
                if read_end >= target_rows[0]:
                    row_read_ends[sheet_name] = read_end
                    read_sheets.append(sheet_name)
                    blocks.append(block)
                    frozen_chars += len(block)
                continue
            read_sheets.append(sheet_name)
            blocks.append(block)
            frozen_chars += len(block)

        read_ranges, unread_ranges = _xlsx_read_unread_atoms(
            selection,
            read_sheets=read_sheets,
            row_read_ends=row_read_ends,
            unverified_sheets=unverified_sheets,
            mismatched_sheets=mismatched_sheets,
            partial_sheet=partial_sheet,
            partial_last_row=partial_last_row,
        )
        if unread_ranges:
            coverage = "limited"
            read_state = ReadState.PARTIAL if (read_ranges or partial_sheet is not None) else ReadState.UNREAD
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
                chars=frozen_chars,
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
    row_read_ends: dict[str, int],
    unverified_sheets: set[str],
    mismatched_sheets: set[str],
    partial_sheet: str | None,
    partial_last_row: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按选择轴与实际迭代结果生成契约合法的已读/未读原子（§3.3.1 / D06）。

    - ``selection=all``：只允许空或恰一个 ``all`` 原子；无法表达部分读取，
      故超时或行范围不可核定都明确拒绝（不把截断/未知伪装成完整读取）。
    - ``selection=sheets``：粒度为整表；未完成的表或不可核定的表记未读（保守，
      不冒充已读）。
    - ``selection=rows``：已读区间按实际迭代到的行核定（``row_read_ends``），
      请求范围越界部分记未读；行范围不可核定的表整表记未读。
    - 原子数组按 **选择顺序** 输出（不按行号重排、不因相邻段归并改序），与
      投递块内的行顺序一致；因此反序相邻段（先 3 再 1—2）的原子表示与其
      digest 覆盖的是实际投递的同一内容。
    """

    if selection.kind == "all":
        if unverified_sheets:
            if mismatched_sheets:
                raise AttachmentParseError("invalid_structure", "XLSX formula and cached views cannot be aligned")
            raise AttachmentParseError("invalid_structure", "XLSX sheet extent cannot be verified")
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
                if name in unverified_sheets:
                    unread_names.append(name)
                elif partial_sheet is None or name in read_sheets:
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
        if atom.sheet in unverified_sheets:
            unread_atoms.append(to_wire_dict(atom))
            continue
        if atom.sheet == partial_sheet:
            read_end = min(atom.end, partial_last_row)
            if partial_last_row >= atom.start:
                read_atoms.append(
                    to_wire_dict(RowsRange(kind="rows", sheet=atom.sheet, start=atom.start, end=read_end))
                )
                if read_end < atom.end:
                    unread_atoms.append(
                        to_wire_dict(RowsRange(kind="rows", sheet=atom.sheet, start=read_end + 1, end=atom.end))
                    )
            else:
                unread_atoms.append(to_wire_dict(atom))
            continue
        read_end = row_read_ends.get(atom.sheet)
        if read_end is None or read_end < atom.start:
            unread_atoms.append(to_wire_dict(atom))
            continue
        clipped_end = min(atom.end, read_end)
        read_atoms.append(to_wire_dict(RowsRange(kind="rows", sheet=atom.sheet, start=atom.start, end=clipped_end)))
        if clipped_end < atom.end:
            unread_atoms.append(
                to_wire_dict(RowsRange(kind="rows", sheet=atom.sheet, start=clipped_end + 1, end=atom.end))
            )
    return read_atoms, unread_atoms


def _xlsx_row_spans(selection: Selection, sheet_name: str) -> list[tuple[int, int]] | None:
    """返回该工作表的行段列表，保持选择顺序（不因归并重排，I21）。

    支持相邻多段（含反序，如先选 3 再选 1—2）：各段合起来必须是连续区间，
    读取与投递按选择顺序逐段进行、段内按物理行号升序，使 ``read_ranges``
    的原子顺序与投递内容顺序一致。存在空档的多段明确拒绝（``invalid_selection``），
    既不静默扩读未选行，也不把空档冒充已读。
    """

    spans = [
        (atom.start, atom.end) for atom in selection.ranges if isinstance(atom, RowsRange) and atom.sheet == sheet_name
    ]
    if not spans:
        return None
    for (_prev_start, prev_end), (start, _end) in itertools.pairwise(sorted(spans)):
        if start != prev_end + 1:
            raise AttachmentParseError(
                "invalid_selection",
                "disjoint XLSX row ranges are not supported; only adjacent segments forming one continuous range",
            )
    return spans


def _column_letter(index: int) -> str:
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


# ---------------------------------------------------------------------------
# PDF / 图片（依赖未装 → 明确能力未核）
# ---------------------------------------------------------------------------

#: 单张图像声明的像素数上限（解码前判定；50M 像素已覆盖常规相机/截图场景）。
MAX_IMAGE_PIXELS: Final = 50_000_000
#: 单张图像解码后像素缓冲的估算上限（解码前按模式估算，256 MiB）。
MAX_IMAGE_DECODED_BYTES: Final = 256 * 1024 * 1024
#: 高位深/浮点模式的每样本字节；其余标准模式按 1 字节计。
_IMAGE_SAMPLE_BYTES: Final[dict[str, int]] = {
    "F": 4,
    "I": 4,
    "I;16": 2,
    "I;16B": 2,
    "I;16L": 2,
    "I;16N": 2,
}


def _open_image(data: bytes) -> Image.Image:
    """打开图像头（惰性，不读像素）；Pillow 的 bomb 告警由本层像素门统一裁决。"""

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", Image.DecompressionBombWarning)
        return Image.open(io.BytesIO(data))


def _image_decode_bytes(mode: str, pixels: int) -> int:
    """按像素数与像素模式估算解码后缓冲字节（通道数 × 每样本字节）。"""

    return pixels * Image.getmodebands(mode) * _IMAGE_SAMPLE_BYTES.get(mode, 1)


def _parse_pdf(data: bytes, selection: Selection, budget: AttachmentBudget) -> ParsedAttachment:
    _reject_unsupported_selection(selection, (PagesRange,))
    _check_no_overlap(selection, PagesRange)
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
    """真解码像素后再报 READY：容器结构（verify）与像素（load）两道门都过才算已核。

    ``verify()`` 只覆盖容器级校验（PNG 的 CRC / JPEG 的 marker 结构），且 JPEG 的
    ``verify`` 是空操作；缺像素解码时，截断或损坏的扫描数据会被当成可读。因此本层
    在声明尺寸通过像素预算后必须 ``load()`` 真解码，解码失败一律拒绝（不报 READY）。

    像素数与解码字节预算在 ``load()`` 之前按声明尺寸判定，巨尺寸不进入解码、不分配
    巨内存。模型兼容与上下文窗口属派发层判定，本层不据此推断已兼容。
    """

    _reject_unsupported_selection(selection, ())
    # 第一遍：容器结构与 CRC（不分配像素缓冲）。
    try:
        with _open_image(data) as image:
            image.verify()
    except Image.DecompressionBombError as exc:
        raise AttachmentParseError(
            "image_pixels_exceeded", "declared image dimensions exceed the pixel budget"
        ) from exc
    except Exception as exc:  # 第三方解码异常统一转译
        raise AttachmentParseError("invalid_structure", "image cannot be decoded") from exc
    # 第二遍：先核像素预算，再真像素解码。
    try:
        with _open_image(data) as image:
            width, height = image.size
            image_format = image.format
            pixels = width * height
            if width <= 0 or height <= 0:
                raise AttachmentParseError("invalid_structure", "image declares a zero-sized frame")
            # 解码前按声明尺寸拒绝：巨尺寸不得进入 load()，不按声明尺寸分配内存。
            if pixels > MAX_IMAGE_PIXELS:
                raise AttachmentParseError(
                    "image_pixels_exceeded",
                    "declared image dimensions exceed the pixel budget",
                )
            if _image_decode_bytes(image.mode, pixels) > MAX_IMAGE_DECODED_BYTES:
                raise AttachmentParseError(
                    "image_decoded_bytes_exceeded",
                    "declared image dimensions exceed the decoded byte budget",
                )
            # 真像素解码：截断/损坏扫描数据在此抛出（LOAD_TRUNCATED_IMAGES 为默认 False）。
            image.load()
    except AttachmentParseError:
        raise
    except Image.DecompressionBombError as exc:
        raise AttachmentParseError(
            "image_pixels_exceeded", "declared image dimensions exceed the pixel budget"
        ) from exc
    except Exception as exc:  # 第三方解码异常统一转译
        raise AttachmentParseError("invalid_structure", "image pixels cannot be decoded") from exc
    block = json_doc(
        {
            "kind": "image",
            "width": width,
            "height": height,
            "format": image_format,
            "pixels": pixels,
            "pixel_decoded": True,
        }
    )
    return ParsedAttachment(
        read_state=ReadState.READY,
        coverage="full",
        body_text=None,
        blocks=[block],
        limitations=[
            _limitation(
                "image_text_not_extracted",
                "图片像素已真解码（尺寸/格式/像素预算已核），未 OCR；字符数不适用于图像，"
                "0 字符不代表图像已核；模型兼容与上下文窗口由派发层判定，本层不作推断",
            )
        ],
        # 图像无可提取字符：字符数如实记为不适用，不用元数据块长度冒充内容字符数（R21）。
        stats=_stats(source_bytes=len(data)),
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
    csv_header_confirmed: bool = False,
) -> ParsedAttachment:
    """按种类解析显式选择范围（纯函数；不读写外部路径）。

    ``csv_header_confirmed`` 由请求方显式传递「用户已确认 CSV 首条为表头」；
    缺省 False 表示未知/未确认，首条按数据行计入记录行限额（I23）。
    """

    try:
        return _dispatch_parse(
            kind=kind,
            data=data,
            selection=selection,
            budget=budget,
            deadline=deadline,
            csv_header_confirmed=csv_header_confirmed,
        )
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
    csv_header_confirmed: bool,
) -> ParsedAttachment:
    knit = kind
    if knit in (AttachmentKind.TXT, AttachmentKind.MD):
        return _parse_text(data, selection, budget)
    if knit is AttachmentKind.CSV:
        return _parse_csv(data, selection, budget, csv_header_confirmed)
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
        csv_header_confirmed = header.get("csv_header_confirmed", False)
        if not isinstance(csv_header_confirmed, bool):
            raise AttachmentParseError("invalid_request", "worker csv_header_confirmed is invalid")
        deadline = time.monotonic() + float(header.get("timeout_s", 30.0))
        parsed = parse_attachment(
            kind=kind,
            data=payload,
            selection=selection,
            budget=budget,
            deadline=deadline,
            csv_header_confirmed=csv_header_confirmed,
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
        # 字节口径：限额只计请求头自身，行尾 ``\n`` 是 framing 分隔符（父进程按同一
        # 口径写入：``header + b"\n" + payload``）。故多读 1 字节识别换行，剥离后再与
        # ``MAX_HEADER_BYTES`` 比较——恰 64KiB 的头加换行必须通过（I36）。
        header_line = sys.stdin.buffer.readline(MAX_HEADER_BYTES + 2)
        if not header_line:
            raise AttachmentParseError("invalid_request", "worker header is missing")
        header_bytes = header_line[:-1] if header_line.endswith(b"\n") else header_line
        if len(header_bytes) > MAX_HEADER_BYTES:
            raise AttachmentParseError("invalid_request", "worker header is too large")
        header = json.loads(header_bytes.decode("utf-8"))
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
