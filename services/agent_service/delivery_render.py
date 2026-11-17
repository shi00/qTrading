"""成果交付三格式渲染与逐字段回读（E-05-3 / DD-F26 / §10.5 / HANDOFF §10.2）。

合同：
- MD / CSV（BOM UTF-8）/ XLSX 三格式都从同一份 :class:`DeliveryField` 序列渲染，
  单元格全部为文本；``=SUM(...)`` 与 ``00123`` 原样保留，不能变成公式或数字；
- CSV 没有单元格格式位，文本保护只能落在文本自身（RFC4180 全字段引号 +
  前导标记转义），规则见 :func:`_encode_csv_value`：会被表格软件重解释的值
  （公式前缀 ``= + - @``、空白前缀、数字词法）加一个前导 ``'``（表格软件的
  文本前缀），重开不执行公式、不改写精度/前导零/合法负号；值本身以 ``'``
  开头时再加一个 ``'``，回读时去掉恰好一个前导 ``'``，任何原值均可逆还原。
  MD（纯文本围栏）与 XLSX（文本类型单元格 + 数字格式 ``@``）不需要该标记；
- 长字段按 30,000 UTF-16 单元分段（``core.agent_types.split_utf16_chunks``），
  分段序号/总数入列，回读拼接即原值，不截断；
- 回读（``read_delivery_fields``）是发布前 ``validated`` 阶段的事实来源：解析失败、
  结构不符或出现公式单元格都报 ``delivery_render_readback_invalid``，不与期望
  字段一致报 ``delivery_render_readback_mismatch``——不能把未确证的内容报告成功。

本模块为同步 CPU/IO 组合（含 openpyxl 保存），调用方须经 ``ThreadPoolManager``
提交，不在事件循环线程直接调用（R16）。
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from collections.abc import Sequence
from typing import cast

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import Cell
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.worksheet.worksheet import Worksheet

from core.agent_types import (
    DELIVERY_FORMATS,
    DELIVERY_RENDER_COLUMNS,
    DeliveryField,
    RenderedDelivery,
    AgentValidationError,
    sha256_hex,
    validate_delivery_fields,
)

_SECTION_TITLES = {"metadata": "元数据", "body": "正文", "payload": "结构字段"}
_XLSX_SHEET = "delivery"
_CSV_BOM = b"\xef\xbb\xbf"
#: 表格软件的文本前缀：前导 ``'`` 让单元格按文本落盘，不按公式/数字解释。
_CSV_TEXT_MARKER = "'"
#: 会触发公式求值或被当作引用/运算符的起始字符（OWASP CSV 注入清单）。
_CSV_FORMULA_PREFIXES = frozenset("=+-@")
#: 会被表格软件吞成数字的完整数字词法（含前导零、正负号、指数、十六进制）。
_CSV_NUMERIC_LEXEME = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?|0[xX][0-9a-fA-F]+")


class DeliveryRenderError(RuntimeError):
    """渲染/回读失败（``code`` 用于回执与 UI；消息不含正文内容）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _invalid(message: str) -> DeliveryRenderError:
    return DeliveryRenderError("delivery_render_readback_invalid", message)


def _require_format(format_name: object) -> str:
    if not isinstance(format_name, str) or format_name not in DELIVERY_FORMATS:
        raise DeliveryRenderError("invalid_format", "交付格式必须为 md/csv/xlsx")
    return format_name


def _markdown_fence(text: str) -> str:
    longest = 0
    current = 0
    for character in text:
        current = current + 1 if character == "`" else 0
        longest = max(longest, current)
    return "`" * max(3, longest + 1)


def _render_markdown(fields: Sequence[DeliveryField]) -> bytes:
    lines = ["# 成果交付", ""]
    current_record = ""
    for field in fields:
        if field.record != current_record:
            current_record = field.record
            lines.extend([f"## {_SECTION_TITLES[current_record]}", ""])
        fence = _markdown_fence(field.value_text)
        lines.extend(
            [
                f"### `{field.path}` ({field.value_type}) {field.chunk_index}/{field.chunk_count}",
                "",
                f"{fence}text",
                field.value_text,
                fence,
                "",
            ]
        )
    return "\n".join(lines).encode("utf-8")


def _csv_value_needs_text_marker(text: str) -> bool:
    """值是否会被表格软件重解释：公式前缀、空白前缀或数字词法（改精度/前导零/类型）。"""

    if text == "":
        return False
    if text[0] in _CSV_FORMULA_PREFIXES or text[0].isspace():
        return True
    return _CSV_NUMERIC_LEXEME.fullmatch(text) is not None


def _encode_csv_value(text: str) -> str:
    """给需保护的值加一个前导 ``'``；原值以 ``'`` 开头时再加一个（写成 ``''``）。

    加固后表格软件重开仍按文本显示，不执行公式、不改写精度/前导零/合法负号；
    与 :func:`_decode_csv_value` 严格互逆（去掉恰好一个前导 ``'``）。
    """

    if text.startswith(_CSV_TEXT_MARKER) or _csv_value_needs_text_marker(text):
        return _CSV_TEXT_MARKER + text
    return text


def _decode_csv_value(text: str) -> str:
    """回读还原：去掉恰好一个前导保护标记 ``'``（无标记即原值）。"""

    if text.startswith(_CSV_TEXT_MARKER):
        return text[1:]
    return text


def _render_csv(fields: Sequence[DeliveryField]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n", quoting=csv.QUOTE_ALL)
    writer.writerow(DELIVERY_RENDER_COLUMNS)
    for field in fields:
        writer.writerow(
            [
                field.path,
                field.value_type,
                str(field.chunk_index),
                str(field.chunk_count),
                _encode_csv_value(field.value_text),
            ]
        )
    return _CSV_BOM + buffer.getvalue().encode("utf-8")


def _write_text_cell(sheet: Worksheet, row: int, column: int, value: str) -> None:
    cell = cast(Cell, sheet.cell(row=row, column=column))
    cell.value = value
    cell.number_format = "@"
    # openpyxl 把以 "=" 开头的字符串推断为公式；显式改回文本，保证公式单元格为 0。
    if cell.data_type == "f":
        cell.data_type = "s"


def _render_xlsx(fields: Sequence[DeliveryField]) -> bytes:
    workbook = Workbook()
    sheet = workbook.worksheets[0]
    sheet.title = _XLSX_SHEET
    for column, name in enumerate(DELIVERY_RENDER_COLUMNS, start=1):
        _write_text_cell(sheet, 1, column, name)
    for row, field in enumerate(fields, start=2):
        values = (
            field.path,
            field.value_type,
            str(field.chunk_index),
            str(field.chunk_count),
            field.value_text,
        )
        for column, value in enumerate(values, start=1):
            _write_text_cell(sheet, row, column, value)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def render_delivery(fields: object, format_name: str) -> RenderedDelivery:
    """渲染三格式之一并返回字节与摘要；契约不符抛 :class:`AgentValidationError`。"""

    name = _require_format(format_name)
    validated = validate_delivery_fields(fields)
    if name == "md":
        content = _render_markdown(validated)
    elif name == "csv":
        content = _render_csv(validated)
    else:
        content = _render_xlsx(validated)
    return RenderedDelivery(
        format=name,
        content=content,
        digest=sha256_hex(content),
        field_count=len(validated),
        chunk_count=sum(1 for field in validated if field.chunk_index == 1),
    )


def _field_from_row(path: str, value_type: str, chunk_index: str, chunk_count: str, value: str) -> DeliveryField:
    try:
        index = int(chunk_index)
        count = int(chunk_count)
    except ValueError as error:
        raise _invalid("分段序号不是整数") from error
    record, _, pointer = path.partition(":")
    return DeliveryField(record, pointer, value_type, value, index, count)


def _read_markdown(content: bytes) -> tuple[DeliveryField, ...]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise _invalid("Markdown 不是 UTF-8") from error
    lines = text.split("\n")
    fields: list[DeliveryField] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.startswith("### `"):
            index += 1
            continue
        try:
            header = line[len("### ") :]
            path, remainder = header.split("` (", 1)
            path = path[1:]
            value_type, remainder = remainder.split(") ", 1)
            chunk_index, chunk_count = remainder.split("/", 1)
        except ValueError as error:
            raise _invalid("字段标题格式不符") from error
        if index + 2 >= len(lines) or lines[index + 1] != "":
            raise _invalid("字段标题后缺少空行")
        opening = lines[index + 2]
        fence = opening[: len(opening) - len(opening.lstrip("`"))]
        if len(fence) < 3 or opening[len(fence) :] != "text":
            raise _invalid("字段值缺少 text 围栏")
        body: list[str] = []
        cursor = index + 3
        while cursor < len(lines) and lines[cursor] != fence:
            body.append(lines[cursor])
            cursor += 1
        if cursor >= len(lines):
            raise _invalid("字段值围栏未闭合")
        fields.append(_field_from_row(path, value_type, chunk_index, chunk_count, "\n".join(body)))
        index = cursor + 1
    return tuple(fields)


def _read_csv(content: bytes) -> tuple[DeliveryField, ...]:
    if not content.startswith(_CSV_BOM):
        raise _invalid("CSV 缺少 UTF-8 BOM")
    try:
        text = content[len(_CSV_BOM) :].decode("utf-8")
    except UnicodeDecodeError as error:
        raise _invalid("CSV 不是 UTF-8") from error
    rows = list(csv.reader(io.StringIO(text, newline="")))
    if not rows or tuple(rows[0]) != DELIVERY_RENDER_COLUMNS:
        raise _invalid("CSV 表头不符")
    fields: list[DeliveryField] = []
    for row in rows[1:]:
        if len(row) != len(DELIVERY_RENDER_COLUMNS):
            raise _invalid("CSV 行列数不符")
        fields.append(_field_from_row(row[0], row[1], row[2], row[3], _decode_csv_value(row[4])))
    return tuple(fields)


def _read_xlsx(content: bytes) -> tuple[DeliveryField, ...]:
    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=False)
    except (zipfile.BadZipFile, InvalidFileException, KeyError, ValueError, OSError) as error:
        raise _invalid("XLSX 无法解析") from error
    try:
        if _XLSX_SHEET not in workbook.sheetnames:
            raise _invalid("XLSX 缺少交付工作表")
        rows = list(workbook[_XLSX_SHEET].iter_rows())
    finally:
        workbook.close()
    if not rows:
        raise _invalid("XLSX 为空")
    header_row = rows[0]
    header = tuple(cell.value for cell in header_row)
    if header != DELIVERY_RENDER_COLUMNS:
        raise _invalid("XLSX 表头不符")
    fields: list[DeliveryField] = []
    for row in rows[1:]:
        cells = list(row)
        if len(cells) != len(DELIVERY_RENDER_COLUMNS):
            raise _invalid("XLSX 行列数不符")
        if any(cell.data_type == "f" for cell in cells):
            raise _invalid("XLSX 含公式单元格")
        path, value_type, chunk_index, chunk_count, value = (cell.value for cell in cells)
        if (
            not isinstance(path, str)
            or not isinstance(value_type, str)
            or not isinstance(chunk_index, str)
            or not isinstance(chunk_count, str)
            or (value is not None and not isinstance(value, str))
        ):
            raise _invalid("XLSX 标识列不是文本")
        fields.append(_field_from_row(path, value_type, chunk_index, chunk_count, value or ""))
    return tuple(fields)


def read_delivery_fields(content: bytes, format_name: str) -> tuple[DeliveryField, ...]:
    """回读已渲染内容为交付字段；任何结构/契约问题都报 ``readback_invalid``。"""

    name = _require_format(format_name)
    if not isinstance(content, bytes):
        raise _invalid("交付内容不是字节")
    if name == "md":
        fields = _read_markdown(content)
    elif name == "csv":
        fields = _read_csv(content)
    else:
        fields = _read_xlsx(content)
    try:
        return validate_delivery_fields(fields)
    except AgentValidationError as error:
        raise _invalid(f"回读字段不合契约：{error.code}") from error


def verify_delivery_readback(expected: object, content: bytes, format_name: str) -> None:
    """发布前逐字段回读核对；不一致即抛错，禁止带病进入 ``validated``。"""

    wanted = validate_delivery_fields(expected)
    readback = read_delivery_fields(content, format_name)
    if readback != wanted:
        raise DeliveryRenderError(
            "delivery_render_readback_mismatch",
            f"回读字段与期望不一致（期望 {len(wanted)} 行，回读 {len(readback)} 行）",
        )
