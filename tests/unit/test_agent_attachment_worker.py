"""E-01-2 附件解析 worker 与 D06 限额单元测试（DD §10.4 / REQ D06）。

覆盖：TXT/MD 码点与段落范围、CSV 多行记录与行限额、JSON 精确数值、
XLSX 同源双表示与缓存适用性（有/无/空/错误/字符串）、sheet/rows 限额、
内容类型嗅探、子进程真实退出/超时/取消。
"""

import asyncio
import csv
import hashlib
import io
import json
import struct
import sys
from pathlib import Path
import tempfile
import time
import zipfile
import zlib
from dataclasses import replace
from typing import Any, cast
from xml.etree import ElementTree

import pytest
from PIL import Image
from pypdf import PageObject, PdfWriter

import services.agent_service.attachment_worker as attachment_worker
import services.agent_service.attachments as attachments

from core.agent_types import (
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
    decode_selected_range,
    selected_range_digest,
    to_wire_dict,
)
from services.agent_service.attachment_worker import AttachmentParseError, parse_attachment
from services.agent_service.attachments import (
    AttachmentExtractionRequest,
    AttachmentWorkerError,
    SubprocessAttachmentWorker,
    detect_attachment_kind,
    worker_command,
)

_NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
_REL_NS = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'
_OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _deadline() -> float:
    return time.monotonic() + 30.0


def _parse(
    kind: AttachmentKind,
    data: bytes,
    selection: Selection | None = None,
    budget: AttachmentBudget | None = None,
    *,
    csv_header_confirmed: bool = False,
    read_expansion_consent: bool = False,
):
    return parse_attachment(
        kind=kind,
        data=data,
        selection=selection or Selection(kind="all"),
        budget=budget or AttachmentBudget(),
        deadline=_deadline(),
        timeout_s=30.0,
        csv_header_confirmed=csv_header_confirmed,
        read_expansion_consent=read_expansion_consent,
    )


def _parse_expired(
    kind: AttachmentKind,
    data: bytes,
    selection: Selection | None = None,
    budget: AttachmentBudget | None = None,
    *,
    timeout_s: float = 30.0,
):
    """以已过期的 deadline 解析（模拟预算耗尽时的部分读取）。"""

    return parse_attachment(
        kind=kind,
        data=data,
        selection=selection or Selection(kind="all"),
        budget=budget or AttachmentBudget(),
        deadline=time.monotonic() - 1.0,
        timeout_s=timeout_s,
    )


def _assert_selected_range_contract(selection: Selection, parsed: Any) -> None:
    decoded = decode_selected_range(
        {
            "selection": to_wire_dict(selection),
            "read_ranges": parsed.read_ranges,
            "unread_ranges": parsed.unread_ranges,
            "stats": parsed.stats,
            "limitations": parsed.limitations,
            "accepted_limited_version": None,
        },
        "$.result.selected_range",
    )
    assert decoded.selection == selection


def _limit_of(
    data: bytes,
    selection: Selection,
    budget: AttachmentBudget,
    kind: AttachmentKind,
    *,
    csv_header_confirmed: bool = False,
    read_expansion_consent: bool = False,
) -> str:
    with pytest.raises(AttachmentParseError) as exc:
        _parse(
            kind,
            data,
            selection,
            budget,
            csv_header_confirmed=csv_header_confirmed,
            read_expansion_consent=read_expansion_consent,
        )
    return exc.value.code


# ---------------------------------------------------------------------------
# TXT / MD
# ---------------------------------------------------------------------------


class TestTextExtraction:
    def test_all_reads_whole_text_and_strips_bom(self) -> None:
        data = "\ufeff第一段\n第二段".encode("utf-8")
        parsed = _parse(AttachmentKind.TXT, data)
        assert parsed.read_state is ReadState.READY
        assert parsed.coverage == "full"
        assert parsed.body_text == "第一段\n第二段"
        assert parsed.stats["chars"] == len("第一段\n第二段")
        assert parsed.stats["source_bytes"] == len(data)
        assert parsed.read_ranges == [to_wire_dict(AllAtom())]
        assert parsed.unread_ranges == []

    def test_codepoints_selection_reads_exact_span(self) -> None:
        parsed = _parse(
            AttachmentKind.MD,
            b"abcdef",
            Selection(kind="limited", ranges=(CodepointsRange(kind="codepoints", start=2, end=4),)),
        )
        assert parsed.body_text == "bcd"
        assert parsed.stats["chars"] == 3

    def test_paragraph_selection_uses_line_numbers(self) -> None:
        parsed = _parse(
            AttachmentKind.TXT,
            b"a\nb\nc",
            Selection(kind="limited", ranges=(ParagraphsRange(kind="paragraphs", start=2, end=3),)),
        )
        assert parsed.body_text == "b\nc"

    def test_codepoints_limit_exact_passes_and_plus_one_rejected(self) -> None:
        budget = AttachmentBudget(max_codepoints=5)
        assert _parse(AttachmentKind.TXT, b"12345", budget=budget).stats["chars"] == 5
        assert (
            _limit_of(b"123456", Selection(kind="all"), budget, AttachmentKind.TXT) == "attachment_codepoints_exceeded"
        )

    def test_non_utf8_text_rejected(self) -> None:
        with pytest.raises(AttachmentParseError) as exc:
            _parse(AttachmentKind.TXT, b"\xff\xfe\x00")
        assert exc.value.code == "invalid_encoding"


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


class TestCsvExtraction:
    def test_multiline_field_is_one_record(self) -> None:
        parsed = _parse(AttachmentKind.CSV, b'name,note\nalice,"line1\nline2"\n')
        assert parsed.read_state is ReadState.READY
        assert parsed.stats["rows"] == 2
        block = json.loads(parsed.blocks[0])
        assert block["rows"] == [["name", "note"], ["alice", "line1\nline2"]]

    def test_body_text_is_frozen_by_standard_writer(self) -> None:
        # I24：逗号/引号/CRLF/多行字段必须按标准 writer 冻结，正文可被 csv.reader 还原。
        data = b'name,note\r\nalice,"a,b"\r\nbob,"say ""hi"""\r\ncarol,"line1\r\nline2"\r\n'
        parsed = _parse(AttachmentKind.CSV, data)
        assert parsed.body_text == 'name,note\nalice,"a,b"\nbob,"say ""hi"""\ncarol,"line1\r\nline2"'
        assert list(csv.reader(io.StringIO(parsed.body_text, newline=""))) == [
            ["name", "note"],
            ["alice", "a,b"],
            ["bob", 'say "hi"'],
            ["carol", "line1\r\nline2"],
        ]
        assert parsed.stats["chars"] == len(parsed.body_text)

    def test_codepoint_budget_counts_frozen_representation(self) -> None:
        data = b'a,b\n"x,y",z\n'
        parsed = _parse(AttachmentKind.CSV, data)
        assert parsed.body_text == 'a,b\n"x,y",z'
        assert parsed.stats["chars"] == len('a,b\n"x,y",z')
        with pytest.raises(AttachmentParseError) as exc:
            _parse(AttachmentKind.CSV, data, budget=AttachmentBudget(max_codepoints=len(parsed.body_text) - 1))
        assert exc.value.code == "attachment_codepoints_exceeded"

    def test_rows_selection_includes_header_numbering(self) -> None:
        parsed = _parse(
            AttachmentKind.CSV,
            b"h1,h2\na,1\nb,2\n",
            Selection(kind="limited", ranges=(RowsRange(kind="rows", sheet=None, start=2, end=3),)),
            read_expansion_consent=True,
        )
        assert json.loads(parsed.blocks[0])["rows"] == [["a", "1"], ["b", "2"]]

    def test_data_row_limit_exact_passes_and_plus_one_rejected(self) -> None:
        data = b"h\n1\n2\n3\n"  # 已确认表头 + 3 条数据记录
        budget = AttachmentBudget(max_rows=3)
        parsed = _parse(AttachmentKind.CSV, data, budget=budget, csv_header_confirmed=True)
        assert parsed.stats["rows"] == 4
        assert (
            _limit_of(
                data, Selection(kind="all"), AttachmentBudget(max_rows=2), AttachmentKind.CSV, csv_header_confirmed=True
            )
            == "attachment_rows_exceeded"
        )

    def test_unconfirmed_first_record_counts_against_limit(self) -> None:
        # 50001 条记录、无「用户已确认表头」：首条同样是数据，必须拒绝（I23）。
        data = b"x\n" * 50_001
        assert _limit_of(data, Selection(kind="all"), AttachmentBudget(), AttachmentKind.CSV) == (
            "attachment_rows_exceeded"
        )

    def test_unconfirmed_50000_rows_passes(self) -> None:
        data = b"x\n" * 50_000
        assert _parse(AttachmentKind.CSV, data, budget=AttachmentBudget()).stats["rows"] == 50_000

    def test_confirmed_header_is_not_counted_as_data_row(self) -> None:
        data = b"h\n" + b"x\n" * 50_000  # 已确认表头 + 50000 条数据
        budget = AttachmentBudget(max_codepoints=10_000_000)
        parsed = _parse(AttachmentKind.CSV, data, budget=budget, csv_header_confirmed=True)
        assert parsed.stats["rows"] == 50_001

    def test_empty_first_record_is_neither_data_nor_header(self) -> None:
        data = b"\na\nb\n"  # 空首条（空行）+ 2 条数据
        assert _parse(AttachmentKind.CSV, data, budget=AttachmentBudget(max_rows=2)).stats["rows"] == 3
        assert (
            _limit_of(data, Selection(kind="all"), AttachmentBudget(max_rows=1), AttachmentKind.CSV)
            == "attachment_rows_exceeded"
        )
        # 首条为空行时即使声明已确认表头也无表头可扣，仍按 2 条数据计。
        assert (
            _parse(AttachmentKind.CSV, data, budget=AttachmentBudget(max_rows=2), csv_header_confirmed=True).stats[
                "rows"
            ]
            == 3
        )

    def test_limited_range_deducts_confirmed_header_only_once(self) -> None:
        data = b"h\n1\n2\n3\n4\n"  # 已确认表头 + 4 条数据
        selection = Selection(
            kind="limited",
            ranges=(
                RowsRange(kind="rows", sheet=None, start=1, end=3),
                RowsRange(kind="rows", sheet=None, start=4, end=5),
            ),
        )
        # 未确认：5 条记录全为数据，max_rows=4 拒绝。
        assert _limit_of(data, selection, AttachmentBudget(max_rows=4), AttachmentKind.CSV) == (
            "attachment_rows_exceeded"
        )
        # 已确认：表头只扣一次，合计 4 条数据，恰等通过；超一拒绝。
        parsed = _parse(AttachmentKind.CSV, data, selection, AttachmentBudget(max_rows=4), csv_header_confirmed=True)
        assert parsed.stats["rows"] == 5
        assert (
            _limit_of(data, selection, AttachmentBudget(max_rows=3), AttachmentKind.CSV, csv_header_confirmed=True)
            == "attachment_rows_exceeded"
        )

    def test_multi_range_rows_limit_is_cumulative_across_segments(self) -> None:
        data = b"h\n1\n2\n3\n4\n"  # 表头 + 4 条数据记录
        selection = Selection(
            kind="limited",
            ranges=(
                RowsRange(kind="rows", sheet=None, start=2, end=3),
                RowsRange(kind="rows", sheet=None, start=4, end=5),
            ),
        )
        # 两段各 2 条，合计 4 条：max_rows=3 必须拒绝（不能按段重置）。
        assert (
            _limit_of(data, selection, AttachmentBudget(max_rows=3), AttachmentKind.CSV, read_expansion_consent=True)
            == "attachment_rows_exceeded"
        )
        exact = _parse(AttachmentKind.CSV, data, selection, AttachmentBudget(max_rows=4), read_expansion_consent=True)
        assert exact.stats["rows"] == 4


# ---------------------------------------------------------------------------
# JSON（agent-number-v1 精确词法）
# ---------------------------------------------------------------------------


class TestJsonExtraction:
    def test_exact_decimal_lexeme_is_preserved(self) -> None:
        parsed = _parse(AttachmentKind.JSON, b'{"a": 0.100000000000000005}')
        block = json.loads(parsed.blocks[0])
        typed = block["value"]["a"]
        assert typed == {
            "numeric_encoding": "agent-number-v1",
            "raw_type": "json_decimal",
            "lexeme": "0.100000000000000005",
            "exact_decimal": "0.100000000000000005",
        }

    def test_json_pointer_selects_subtree(self) -> None:
        parsed = _parse(
            AttachmentKind.JSON,
            b'{"a": {"b": 2}, "c": 3}',
            Selection(kind="limited", ranges=(JsonPointerRange(kind="json_pointer", pointer="/a"),)),
            read_expansion_consent=True,
        )
        assert json.loads(parsed.blocks[0])["value"] == {
            "b": {
                "numeric_encoding": "agent-number-v1",
                "raw_type": "json_integer",
                "lexeme": "2",
                "exact_decimal": "2",
            }
        }

    def test_missing_pointer_is_rejected_not_guessed(self) -> None:
        code = _limit_of(
            b'{"a": 1}',
            Selection(kind="limited", ranges=(JsonPointerRange(kind="json_pointer", pointer="/missing"),)),
            AttachmentBudget(),
            AttachmentKind.JSON,
            read_expansion_consent=True,
        )
        assert code == "invalid_selection"

    def test_object_key_with_leading_zero_resolves(self) -> None:
        # "01" 是合法对象键，不是数组索引（I67）。
        parsed = _parse(
            AttachmentKind.JSON,
            b'{"01": 1}',
            Selection(kind="limited", ranges=(JsonPointerRange(kind="json_pointer", pointer="/01"),)),
            read_expansion_consent=True,
        )
        assert json.loads(parsed.blocks[0])["value"]["lexeme"] == "1"

    def test_array_index_with_leading_zero_is_rejected(self) -> None:
        code = _limit_of(
            b'{"a": [1, 2]}',
            Selection(kind="limited", ranges=(JsonPointerRange(kind="json_pointer", pointer="/a/01"),)),
            AttachmentBudget(),
            AttachmentKind.JSON,
            read_expansion_consent=True,
        )
        assert code == "invalid_selection"

    def test_invalid_json_rejected(self) -> None:
        with pytest.raises(AttachmentParseError) as exc:
            _parse(AttachmentKind.JSON, b"{not json")
        assert exc.value.code == "invalid_structure"


class TestJsonRawPreview:
    """N40：结构化覆盖之外，选中子树必须给出原文表示（原空白/转义/指数写法）与码点偏移。"""

    _DOC = (
        b'{\n  "a" : { "b" : 1.50e+10, "s" : "x\\u00e9\\n\\"q\\"" },\n  "c" : [ { "d" : -0.0 }, 2 ],\n  "e" : "tail"\n}'
    )

    def test_all_keeps_exact_source_characters(self) -> None:
        parsed = _parse(AttachmentKind.JSON, self._DOC)
        text = self._DOC.decode("utf-8")
        assert parsed.body_text == text
        assert parsed.preview_ranges == [{"start": 0, "end": len(text)}]
        # 结构值仍可独立读：指数写法与转义都不因预览而改变
        assert json.loads(parsed.blocks[0])["value"]["a"]["b"]["lexeme"] == "1.50e+10"

    def test_pointer_preview_is_exact_subtree_slice_with_offsets(self) -> None:
        text = self._DOC.decode("utf-8")
        parsed = _parse(
            AttachmentKind.JSON,
            self._DOC,
            Selection(kind="limited", ranges=(JsonPointerRange(kind="json_pointer", pointer="/a"),)),
            read_expansion_consent=True,
        )
        expected = '{ "b" : 1.50e+10, "s" : "x\\u00e9\\n\\"q\\"" }'
        start = text.index('{ "b"')
        assert parsed.body_text == expected
        assert parsed.preview_ranges == [{"start": start, "end": start + len(expected)}]
        assert text[start : start + len(expected)] == parsed.body_text
        # 区外内容不混入预览
        assert '"c"' not in parsed.body_text
        assert "tail" not in parsed.body_text
        # 结构值仍可独立读（含指数词法）
        assert json.loads(parsed.blocks[0])["value"]["b"]["lexeme"] == "1.50e+10"

    def test_multiple_pointers_keep_each_slice_and_offsets(self) -> None:
        text = self._DOC.decode("utf-8")
        parsed = _parse(
            AttachmentKind.JSON,
            self._DOC,
            Selection(
                kind="limited",
                ranges=(
                    JsonPointerRange(kind="json_pointer", pointer="/a"),
                    JsonPointerRange(kind="json_pointer", pointer="/e"),
                ),
            ),
            read_expansion_consent=True,
        )
        assert parsed.preview_ranges is not None
        assert parsed.body_text is not None
        parts = parsed.body_text.split("\n")
        assert len(parts) == len(parsed.preview_ranges) == 2
        for part, span in zip(parts, parsed.preview_ranges, strict=True):
            assert text[span["start"] : span["end"]] == part
        assert parts[0].startswith('{ "b"')
        assert parts[1] == '"tail"'

    def test_bom_stripped_offsets_are_relative_to_decoded_text(self) -> None:
        parsed = _parse(AttachmentKind.JSON, b'\xef\xbb\xbf{ "a" : 1 }')
        assert parsed.body_text == '{ "a" : 1 }'
        assert parsed.preview_ranges == [{"start": 0, "end": len('{ "a" : 1 }')}]

    def test_empty_selection_yields_empty_preview(self) -> None:
        parsed = _parse(AttachmentKind.JSON, b'{"a": 1}', Selection(kind="limited", ranges=()))
        assert parsed.body_text == ""
        assert parsed.preview_ranges == []

    def test_preview_outside_budget_is_rejected(self) -> None:
        # 结构化表示会压掉空白；原文预览超预算即拒绝，不以截断冒充完整预览。
        data = b'{"a": ' + b" " * 200 + b"1}"
        block_chars = len(_parse(AttachmentKind.JSON, data).blocks[0])
        assert block_chars < len(data.decode("utf-8"))
        assert (
            _limit_of(
                data,
                Selection(kind="all"),
                AttachmentBudget(max_codepoints=block_chars),
                AttachmentKind.JSON,
            )
            == "attachment_codepoints_exceeded"
        )


# ---------------------------------------------------------------------------
# I70：未选正文零读取（CSV 流式按行 / JSON pointer 定向 + 扩张同意）
# ---------------------------------------------------------------------------


class TestSelectionReadIsolation:
    def test_csv_stops_reading_after_last_selected_row(self) -> None:
        # 选区之后的字节非法 UTF-8：整份解码会报 invalid_encoding，流式读取不得触碰。
        data = b"a,1\nb,2\n\xff\xfe"
        parsed = _parse(
            AttachmentKind.CSV,
            data,
            Selection(kind="limited", ranges=(RowsRange(kind="rows", sheet=None, start=1, end=2),)),
        )
        assert json.loads(parsed.blocks[0])["rows"] == [["a", "1"], ["b", "2"]]
        assert parsed.limitations == []

    def test_csv_prefix_read_requires_explicit_consent_and_reports_amount(self) -> None:
        data = b"h\n1\n2\n3\n"
        selection = Selection(kind="limited", ranges=(RowsRange(kind="rows", sheet=None, start=3, end=4),))
        assert _limit_of(data, selection, AttachmentBudget(), AttachmentKind.CSV) == "read_expansion_not_permitted"
        parsed = _parse(AttachmentKind.CSV, data, selection, read_expansion_consent=True)
        assert json.loads(parsed.blocks[0])["rows"] == [["2"], ["3"]]
        assert [item["code"] for item in parsed.limitations] == ["read_expansion"]
        # 前置两条记录各 2 字节：扩张量必须等于真实读到的区外字节数。
        assert "read 4 bytes" in parsed.limitations[0]["message"]

    def test_csv_gap_between_ranges_counts_as_expansion(self) -> None:
        data = b"a\nb\nc\nd\n"
        selection = Selection(
            kind="limited",
            ranges=(
                RowsRange(kind="rows", sheet=None, start=1, end=1),
                RowsRange(kind="rows", sheet=None, start=3, end=3),
            ),
        )
        assert _limit_of(data, selection, AttachmentBudget(), AttachmentKind.CSV) == "read_expansion_not_permitted"
        parsed = _parse(AttachmentKind.CSV, data, selection, read_expansion_consent=True)
        assert json.loads(parsed.blocks[0])["rows"] == [["a"], ["c"]]
        assert "read 2 bytes" in parsed.limitations[0]["message"]

    def test_csv_quoted_multiline_field_survives_streaming(self) -> None:
        data = b'id,note\n1,"l1\nl2"\n2,x\n'
        parsed = _parse(
            AttachmentKind.CSV,
            data,
            Selection(kind="limited", ranges=(RowsRange(kind="rows", sheet=None, start=1, end=2),)),
        )
        assert json.loads(parsed.blocks[0])["rows"] == [["id", "note"], ["1", "l1\nl2"]]

    def test_csv_bom_is_stripped_in_streaming_reader(self) -> None:
        parsed = _parse(AttachmentKind.CSV, b"\xef\xbb\xbfa,b\n")
        assert json.loads(parsed.blocks[0])["rows"] == [["a", "b"]]

    def test_csv_cr_only_line_endings_still_split_records(self) -> None:
        # 仅 \r 行尾时单条物理行含多条记录，读取区间仍须按记录连续推进。
        parsed = _parse(AttachmentKind.CSV, b"a\rb\r")
        assert json.loads(parsed.blocks[0])["rows"] == [["a"], ["b"]]

    def test_csv_range_beyond_source_is_rejected_while_streaming(self) -> None:
        selection = Selection(kind="limited", ranges=(RowsRange(kind="rows", sheet=None, start=2, end=3),))
        assert (
            _limit_of(b"a\nb\n", selection, AttachmentBudget(), AttachmentKind.CSV, read_expansion_consent=True)
            == "invalid_selection"
        )

    def test_json_pointer_does_not_parse_unselected_tail(self) -> None:
        # 选区之后的尾部结构非法（未闭合数组）：整份解析会报 invalid_structure，定向扫描必须成功。
        data = b'{"a": {"b": 1}, "c": [1,'
        parsed = _parse(
            AttachmentKind.JSON,
            data,
            Selection(kind="limited", ranges=(JsonPointerRange(kind="json_pointer", pointer="/a"),)),
            read_expansion_consent=True,
        )
        assert json.loads(parsed.blocks[0])["value"] == {"b": _exact("json_integer", "1")}
        assert [item["code"] for item in parsed.limitations] == ["read_expansion"]

    def test_json_pointer_requires_explicit_consent(self) -> None:
        selection = Selection(kind="limited", ranges=(JsonPointerRange(kind="json_pointer", pointer="/a"),))
        assert (
            _limit_of(b'{"a": 1}', selection, AttachmentBudget(), AttachmentKind.JSON) == "read_expansion_not_permitted"
        )

    def test_json_skips_deep_unselected_sibling_without_recursion(self) -> None:
        depth = 5_000
        data = ('{"skip": ' + "[" * depth + "]" * depth + ', "keep": 7}').encode()
        parsed = _parse(
            AttachmentKind.JSON,
            data,
            Selection(kind="limited", ranges=(JsonPointerRange(kind="json_pointer", pointer="/keep"),)),
            read_expansion_consent=True,
        )
        assert json.loads(parsed.blocks[0])["value"] == _exact("json_integer", "7")

    def test_json_array_pointer_with_bom(self) -> None:
        parsed = _parse(
            AttachmentKind.JSON,
            b'\xef\xbb\xbf[{"k": 1}, {"k": 2}]',
            Selection(kind="limited", ranges=(JsonPointerRange(kind="json_pointer", pointer="/1/k"),)),
            read_expansion_consent=True,
        )
        assert json.loads(parsed.blocks[0])["value"] == _exact("json_integer", "2")

    def test_json_pointer_crossing_scalar_is_rejected(self) -> None:
        selection = Selection(kind="limited", ranges=(JsonPointerRange(kind="json_pointer", pointer="/a/b"),))
        assert (
            _limit_of(b'{"a": 1}', selection, AttachmentBudget(), AttachmentKind.JSON, read_expansion_consent=True)
            == "invalid_selection"
        )

    def test_json_array_index_out_of_range_is_rejected(self) -> None:
        selection = Selection(kind="limited", ranges=(JsonPointerRange(kind="json_pointer", pointer="/2"),))
        assert (
            _limit_of(b"[1, 2]", selection, AttachmentBudget(), AttachmentKind.JSON, read_expansion_consent=True)
            == "invalid_selection"
        )


# ---------------------------------------------------------------------------
# XLSX（同源双表示 + 缓存）
# ---------------------------------------------------------------------------

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""

_ROOT_RELS = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships {_REL_NS}>
<Relationship Id="rId1" Type="{_OFFICE_REL}/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

_SHEET1 = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet {_NS}><sheetData>
<row r="1"><c r="A1" t="inlineStr"><is><t>00123</t></is></c></row>
<row r="2">
<c r="B2"><f>A1</f><v>123</v></c>
<c r="C2"><f>1+1</f></c>
<c r="D2"><f>1+1</f><v></v></c>
<c r="E2" t="e"><f>1/0</f><v>#DIV/0!</v></c>
<c r="F2" t="str"><f>"a"</f><v>a</v></c>
</row>
<row r="3"><c r="A3"><v>7</v></c></row>
</sheetData></worksheet>"""

_SHEET2 = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet {_NS}><sheetData><row r="1"><c r="A1"><v>9</v></c></row></sheetData></worksheet>"""


_SHARED_STRINGS = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><sst {_NS}><si><t>shared</t></si></sst>'


def _xlsx_bytes(
    *,
    sheets: int = 1,
    date1904: bool = True,
    shared_strings: str | None = None,
    styles: str | None = None,
    sheet1: str | None = None,
    sheet_state: str | None = None,
) -> bytes:
    state = f' state="{sheet_state}"' if sheet_state else ""
    sheet_entries = "".join(
        f'<sheet name="S{index}" sheetId="{index}"{state} r:id="rId{index}"/>' for index in range(1, sheets + 1)
    )
    workbook = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook {_NS} xmlns:r="{_OFFICE_REL}">
{'<workbookPr date1904="1"/>' if date1904 else ""}
<sheets>{sheet_entries}</sheets></workbook>"""
    rels = "".join(
        f'<Relationship Id="rId{index}" Type="{_OFFICE_REL}/worksheet" Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, sheets + 1)
    )
    workbook_rels = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships {_REL_NS}>{rels}</Relationships>"""
    content_types = _CONTENT_TYPES
    if shared_strings is not None:
        content_types = _CONTENT_TYPES.replace(
            "</Types>",
            '<Override PartName="/xl/sharedStrings.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
            "</Types>",
        )
    if styles is not None:
        content_types = content_types.replace(
            "</Types>",
            '<Override PartName="/xl/styles.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            "</Types>",
        )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", _ROOT_RELS)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", _SHEET1 if sheet1 is None else sheet1)
        if sheets > 1:
            archive.writestr("xl/worksheets/sheet2.xml", _SHEET2)
        if shared_strings is not None:
            archive.writestr("xl/sharedStrings.xml", shared_strings)
        if styles is not None:
            archive.writestr("xl/styles.xml", styles)
    return buffer.getvalue()


def _rewrite_xlsx(
    data: bytes,
    *,
    drop: frozenset[str] = frozenset(),
    replace: dict[str, bytes] | None = None,
) -> bytes:
    """按条目改写容器（用于构造缺失/损坏/异常结构的最小样本）。"""

    replacements = replace or {}
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            if info.filename in drop:
                continue
            target.writestr(info, replacements.get(info.filename, source.read(info)))
    return buffer.getvalue()


def _xlsx_cells(parsed: Any) -> dict[str, dict[str, Any]]:
    block = json.loads(parsed.blocks[0])
    return {cell["cell"]: cell for row in block["rows"] for cell in row["cells"]}


def _xlsx_single_cell(value: str, *, formula: str | None = None) -> bytes:
    """单行单单元格的最小样本（用于字符预算边界与长公式/短缓存场景）。"""

    if formula is None:
        cell = f'<c r="A1" t="inlineStr"><is><t>{value}</t></is></c>'
    else:
        cell = f'<c r="A1"><f>{formula}</f><v>{value}</v></c>'
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<worksheet {_NS}><sheetData><row r="1">{cell}</row></sheetData></worksheet>'
    )
    return _rewrite_xlsx(_xlsx_bytes(), replace={"xl/worksheets/sheet1.xml": sheet.encode("utf-8")})


class TestXlsxExtraction:
    def test_dual_representation_and_cache_applicability(self) -> None:
        parsed = _parse(AttachmentKind.XLSX, _xlsx_bytes())
        assert parsed.read_state is ReadState.READY
        assert parsed.stats["sheets"] == 1
        assert parsed.stats["rows"] == 3
        cells = _xlsx_cells(parsed)

        assert cells["A1"]["value"]["lexeme"] == "00123"
        assert cells["A1"]["xlsx_cell_meta"]["cache_present"] is False
        assert cells["A1"]["xlsx_cell_meta"]["cache_missing_reason"] == "not_applicable"
        # I69：版本字段必须是严格整数，不得以字符串 "1" 写入。
        assert cells["A1"]["xlsx_cell_meta"]["schema_version"] == 1
        assert isinstance(cells["A1"]["xlsx_cell_meta"]["schema_version"], int)

        cached = cells["B2"]
        assert cached["formula_text"] == "=A1"
        meta = cached["xlsx_cell_meta"]
        assert meta["cache_present"] is True
        assert meta["cache_applicable"] is None
        assert meta["cache_verification"] == "unverified"
        assert meta["cache_raw_type"] == "json_integer"
        assert json.loads(meta["cache_raw_value"])["lexeme"] == "123"
        assert meta["xml_value_lexeme"] == "123"
        assert meta["date_epoch"] == "1904"

    def test_absent_empty_error_and_string_caches_are_distinguished(self) -> None:
        cells = _xlsx_cells(_parse(AttachmentKind.XLSX, _xlsx_bytes()))
        assert cells["C2"]["xlsx_cell_meta"]["cache_present"] is False
        assert cells["C2"]["xlsx_cell_meta"]["cache_missing_reason"] == "cache_absent"
        assert cells["D2"]["xlsx_cell_meta"]["cache_present"] is True
        assert cells["D2"]["xlsx_cell_meta"]["cache_missing_reason"] == "cache_value_empty"
        assert cells["E2"]["xlsx_cell_meta"]["cache_raw_type"] == "error"
        assert cells["E2"]["xlsx_cell_meta"]["cache_missing_reason"] == "cache_error"
        assert cells["F2"]["xlsx_cell_meta"]["cache_present"] is True
        assert cells["F2"]["xlsx_cell_meta"]["cache_raw_type"] == "string"

    def test_chars_count_frozen_representation_not_lexeme_only(self) -> None:
        # I19：字符预算与 stats.chars 必须按投递的冻结完整表示（码点）计，而不是只计词法单元。
        parsed = _parse(AttachmentKind.XLSX, _xlsx_bytes(sheets=2))
        assert parsed.stats["chars"] == sum(len(block) for block in parsed.blocks)
        lexeme_total = sum(
            len(str(cell["value"]["lexeme"]))
            for block in parsed.blocks
            for row in json.loads(block)["rows"]
            for cell in row["cells"]
        )
        chars = parsed.stats["chars"]
        assert chars is not None
        assert chars > lexeme_total

    def test_long_formula_with_short_cache_counts_formula_text(self) -> None:
        formula = "=" + "1+" * 400 + "1"
        data = _xlsx_single_cell("1", formula=formula)
        parsed = _parse(AttachmentKind.XLSX, data)
        assert formula in parsed.blocks[0]
        assert parsed.stats["chars"] == len(parsed.blocks[0])
        lexeme_total = sum(len(str(cell["value"]["lexeme"])) for cell in _xlsx_cells(parsed).values())
        # 只计词法时该预算会「通过」，按完整表示必须拒绝（公式文本参与计数）。
        code = _limit_of(
            data,
            Selection(kind="all"),
            AttachmentBudget(max_codepoints=lexeme_total + 10),
            AttachmentKind.XLSX,
        )
        assert code == "attachment_codepoints_exceeded"

    def test_codepoints_limit_boundary_on_frozen_representation(self) -> None:
        base = len(_parse(AttachmentKind.XLSX, _xlsx_single_cell("")).blocks[0])
        exact = _parse(AttachmentKind.XLSX, _xlsx_single_cell("x" * (100_000 - base)))
        assert exact.stats["chars"] == 100_000
        assert sum(len(block) for block in exact.blocks) == 100_000
        over = _xlsx_single_cell("x" * (100_001 - base))
        assert _limit_of(over, Selection(kind="all"), AttachmentBudget(), AttachmentKind.XLSX) == (
            "attachment_codepoints_exceeded"
        )

    def test_whitespace_and_newline_escaping_participate_in_codepoint_count(self) -> None:
        parsed = _parse(AttachmentKind.XLSX, _xlsx_single_cell(" a \n b "))
        block = parsed.blocks[0]
        assert parsed.stats["chars"] == len(block)
        assert "\\n" in block
        chars = parsed.stats["chars"]
        assert chars is not None
        assert chars > len(" a \n b ")

    def test_empty_sheet_block_participates_in_codepoint_budget(self) -> None:
        # I71：零行工作表的投递块（{"kind":"xlsx_sheet","rows":[]...}）没有逐行增量检查，
        # 其块长同样必须计入字符预算，否则「送出/保存的全部表示」可绕过限额。
        empty_sheet = (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<worksheet {_NS}><sheetData/></worksheet>'
        )
        data = _xlsx_bytes(sheet1=empty_sheet)
        parsed = _parse(AttachmentKind.XLSX, data)
        assert len(parsed.blocks) == 1
        block_chars = len(parsed.blocks[0])
        assert parsed.stats["chars"] == block_chars
        assert block_chars > 0
        # 恰等通过、少一码点拒绝（此前该块完全不经预算判定，少一也会通过）。
        assert (
            _parse(AttachmentKind.XLSX, data, budget=AttachmentBudget(max_codepoints=block_chars)).stats["chars"]
            == block_chars
        )
        assert (
            _limit_of(
                data, Selection(kind="all"), AttachmentBudget(max_codepoints=block_chars - 1), AttachmentKind.XLSX
            )
            == "attachment_codepoints_exceeded"
        )

    def test_sheet_limit_exact_passes_and_plus_one_rejected(self) -> None:
        data = _xlsx_bytes(sheets=2)
        exact = _parse(
            AttachmentKind.XLSX,
            data,
            Selection(kind="limited", ranges=(SheetsRange(kind="sheets", names=("S1",)),)),
            AttachmentBudget(max_sheets=1),
        )
        assert exact.stats["sheets"] == 1
        code = _limit_of(
            data,
            Selection(kind="limited", ranges=(SheetsRange(kind="sheets", names=("S1", "S2")),)),
            AttachmentBudget(max_sheets=1),
            AttachmentKind.XLSX,
        )
        assert code == "attachment_sheets_exceeded"

    def test_rows_limit_exact_passes_and_plus_one_rejected(self) -> None:
        data = _xlsx_bytes()
        assert _parse(AttachmentKind.XLSX, data, budget=AttachmentBudget(max_rows=3)).stats["rows"] == 3
        code = _limit_of(data, Selection(kind="all"), AttachmentBudget(max_rows=2), AttachmentKind.XLSX)
        assert code == "attachment_rows_exceeded"

    def test_rows_selection_across_sheets_does_not_count_as_overlap(self) -> None:
        # B2：不同工作表的相同行号互不重叠，不能被跨表误判为重叠选择。
        data = _xlsx_bytes(sheets=2)
        selection = Selection(
            kind="limited",
            ranges=(
                RowsRange(kind="rows", sheet="S1", start=1, end=3),
                RowsRange(kind="rows", sheet="S2", start=1, end=1),
            ),
        )
        parsed = _parse(AttachmentKind.XLSX, data, selection)
        assert parsed.read_state is ReadState.READY
        assert parsed.stats["rows"] == 4
        assert parsed.unread_ranges == []
        _assert_selected_range_contract(selection, parsed)

    def test_rows_selection_same_sheet_overlap_is_rejected(self) -> None:
        selection = Selection(
            kind="limited",
            ranges=(
                RowsRange(kind="rows", sheet="S1", start=1, end=2),
                RowsRange(kind="rows", sheet="S1", start=2, end=3),
            ),
        )
        assert _limit_of(_xlsx_bytes(), selection, AttachmentBudget(), AttachmentKind.XLSX) == "invalid_selection"

    def test_timeout_partial_rows_reports_complete_read_and_unread(self) -> None:
        # B3：超时部分读取必须生成 decode_selected_range 可接受的已读/未读原子。
        data = _xlsx_bytes(sheets=2)
        selection = Selection(
            kind="limited",
            ranges=(
                RowsRange(kind="rows", sheet="S1", start=1, end=3),
                RowsRange(kind="rows", sheet="S2", start=1, end=1),
            ),
        )
        parsed = _parse_expired(AttachmentKind.XLSX, data, selection)
        assert parsed.read_state is ReadState.PARTIAL
        assert parsed.read_ranges == []
        assert parsed.unread_ranges == [to_wire_dict(atom) for atom in selection.ranges]
        assert parsed.limitations[0]["code"] == "extract_timeout"
        _assert_selected_range_contract(selection, parsed)

    def test_timeout_limitation_reports_actual_injected_timeout(self) -> None:
        """超时文案必须反映实际注入时限，不硬编码 30 秒。"""

        data = _xlsx_bytes(sheets=2)
        selection = Selection(
            kind="limited",
            ranges=(
                RowsRange(kind="rows", sheet="S1", start=1, end=3),
                RowsRange(kind="rows", sheet="S2", start=1, end=1),
            ),
        )
        parsed = _parse_expired(AttachmentKind.XLSX, data, selection, timeout_s=7.5)
        assert parsed.limitations[0]["code"] == "extract_timeout"
        assert "7.5 秒" in parsed.limitations[0]["message"]
        assert "30 秒" not in parsed.limitations[0]["message"]

    def test_timeout_partial_sheets_marks_incomplete_sheet_unread(self) -> None:
        data = _xlsx_bytes(sheets=2)
        selection = Selection(kind="limited", ranges=(SheetsRange(kind="sheets", names=("S1", "S2")),))
        parsed = _parse_expired(AttachmentKind.XLSX, data, selection)
        assert parsed.read_state is ReadState.PARTIAL
        assert parsed.read_ranges == []
        assert parsed.unread_ranges == [to_wire_dict(SheetsRange(kind="sheets", names=("S1", "S2")))]
        # sheets 轴不投递半表内容，统计也不把未投递行计入。
        assert parsed.blocks == []
        assert parsed.stats["rows"] == 0
        _assert_selected_range_contract(selection, parsed)

    def test_timeout_partial_rows_delivers_prefix_block(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # N1：rows 轴声明为已读的前缀行必须有对应投递内容，且统计只计已投递行。
        data = _xlsx_bytes()
        selection = Selection(kind="limited", ranges=(RowsRange(kind="rows", sheet="S1", start=1, end=3),))
        ticks = iter([0.0, 100.0])
        monkeypatch.setattr("services.agent_service.attachment_worker.time.monotonic", lambda: next(ticks, 100.0))
        parsed = parse_attachment(
            kind=AttachmentKind.XLSX,
            data=data,
            selection=selection,
            budget=AttachmentBudget(),
            deadline=10.0,
            timeout_s=30.0,
        )
        assert parsed.read_state is ReadState.PARTIAL
        assert parsed.read_ranges == [to_wire_dict(RowsRange(kind="rows", sheet="S1", start=1, end=1))]
        assert parsed.unread_ranges == [to_wire_dict(RowsRange(kind="rows", sheet="S1", start=2, end=3))]
        block = json.loads(parsed.blocks[0])
        assert block["sheet"] == "S1"
        assert [row["row"] for row in block["rows"]] == [1]
        assert parsed.stats["rows"] == 1
        _assert_selected_range_contract(selection, parsed)

    def test_timeout_all_selection_is_explicitly_rejected(self) -> None:
        # all 分型无法表达部分读取：明确拒绝，不把截断伪装成完整读取。
        with pytest.raises(AttachmentParseError) as exc:
            _parse_expired(AttachmentKind.XLSX, _xlsx_bytes())
        assert exc.value.code == "attachment_time_exceeded"


# ---------------------------------------------------------------------------
# XLSX 行选择按实际迭代坐标核定（I20）
# ---------------------------------------------------------------------------


def _xlsx_sheet1(rows_xml: str, *, dimension: str | None = None, cols_xml: str = "") -> bytes:
    """以自定义 sheet1.xml 构造最小样本（可声明与实际内容不符的 dimension）。"""

    dimension_tag = f'<dimension ref="{dimension}"/>' if dimension is not None else ""
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f"<worksheet {_NS}>{dimension_tag}{cols_xml}<sheetData>{rows_xml}</sheetData></worksheet>"
    )
    return _rewrite_xlsx(_xlsx_bytes(), replace={"xl/worksheets/sheet1.xml": sheet.encode("utf-8")})


def _three_rows() -> str:
    return "".join(f'<row r="{index}"><c r="A{index}"><v>{index}</v></c></row>' for index in range(1, 4))


class TestXlsxRowExtentCertification:
    def test_fully_out_of_range_rows_are_unread_not_full(self) -> None:
        # I20：请求范围完全越界时不得回报 READY/full（原样回报 RowsRange）。
        atom = RowsRange(kind="rows", sheet="S1", start=10, end=12)
        selection = Selection(kind="limited", ranges=(atom,))
        parsed = _parse(AttachmentKind.XLSX, _xlsx_bytes(), selection)
        assert parsed.read_state is ReadState.UNREAD
        assert parsed.coverage == "limited"
        assert parsed.read_ranges == []
        assert parsed.unread_ranges == [to_wire_dict(atom)]
        assert parsed.blocks == []
        assert parsed.stats["rows"] == 0
        _assert_selected_range_contract(selection, parsed)

    def test_partially_out_of_range_rows_read_only_actual_rows(self) -> None:
        # 部分越界：已读为实际存在的行，其余记未读。
        selection = Selection(kind="limited", ranges=(RowsRange(kind="rows", sheet="S1", start=2, end=9),))
        parsed = _parse(AttachmentKind.XLSX, _xlsx_bytes(), selection)
        assert parsed.read_state is ReadState.PARTIAL
        assert parsed.coverage == "limited"
        assert parsed.read_ranges == [to_wire_dict(RowsRange(kind="rows", sheet="S1", start=2, end=3))]
        assert parsed.unread_ranges == [to_wire_dict(RowsRange(kind="rows", sheet="S1", start=4, end=9))]
        block = json.loads(parsed.blocks[0])
        assert [row["row"] for row in block["rows"]] == [2, 3]
        assert parsed.stats["rows"] == 2
        _assert_selected_range_contract(selection, parsed)

    def test_empty_sheet_rows_selection_is_unread(self) -> None:
        # 空表：请求的行一个都不存在，全部记未读，不伪 READY/full。
        data = _xlsx_sheet1("", dimension="A1:A3")
        atom = RowsRange(kind="rows", sheet="S1", start=1, end=2)
        selection = Selection(kind="limited", ranges=(atom,))
        parsed = _parse(AttachmentKind.XLSX, data, selection)
        assert parsed.read_state is ReadState.UNREAD
        assert parsed.coverage == "limited"
        assert parsed.read_ranges == []
        assert parsed.unread_ranges == [to_wire_dict(atom)]
        assert parsed.blocks == []
        assert parsed.stats["rows"] == 0
        _assert_selected_range_contract(selection, parsed)

    def test_dimension_larger_than_content_does_not_fake_full(self) -> None:
        # dimension 声明 A1:A100 但实际只有 3 行：不得按声明补空行冒充已读。
        data = _xlsx_sheet1(_three_rows(), dimension="A1:A100")
        selection = Selection(kind="limited", ranges=(RowsRange(kind="rows", sheet="S1", start=1, end=5),))
        parsed = _parse(AttachmentKind.XLSX, data, selection)
        assert parsed.read_state is ReadState.PARTIAL
        assert parsed.read_ranges == [to_wire_dict(RowsRange(kind="rows", sheet="S1", start=1, end=3))]
        assert parsed.unread_ranges == [to_wire_dict(RowsRange(kind="rows", sheet="S1", start=4, end=5))]
        assert parsed.stats["rows"] == 3
        all_parsed = _parse(AttachmentKind.XLSX, data)
        assert all_parsed.read_state is ReadState.READY
        assert all_parsed.stats["rows"] == 3
        assert [row["row"] for row in json.loads(all_parsed.blocks[0])["rows"]] == [1, 2, 3]

    def test_in_range_rows_selection_stays_ready_full(self) -> None:
        # 正常范围：实际端点均存在，仍为 READY/full。
        atom = RowsRange(kind="rows", sheet="S1", start=1, end=2)
        selection = Selection(kind="limited", ranges=(atom,))
        parsed = _parse(AttachmentKind.XLSX, _xlsx_bytes(), selection)
        assert parsed.read_state is ReadState.READY
        assert parsed.coverage == "full"
        assert parsed.read_ranges == [to_wire_dict(atom)]
        assert parsed.unread_ranges == []
        assert parsed.stats["rows"] == 2
        _assert_selected_range_contract(selection, parsed)


# ---------------------------------------------------------------------------
# XLSX 多段行选择的原子保序与边界（I21）
# ---------------------------------------------------------------------------


def _selected_range_digest(selection: Selection, parsed: Any) -> str:
    decoded = decode_selected_range(
        {
            "selection": to_wire_dict(selection),
            "read_ranges": parsed.read_ranges,
            "unread_ranges": parsed.unread_ranges,
            "stats": parsed.stats,
            "limitations": parsed.limitations,
            "accepted_limited_version": None,
        },
        "$.result.selected_range",
    )
    return selected_range_digest(decoded)


def _rows_of_blocks(parsed: Any) -> list[int]:
    return [row["row"] for block in parsed.blocks for row in json.loads(block)["rows"]]


class TestXlsxMultiSegmentRowOrder:
    def test_reversed_adjacent_segments_keep_selection_order(self) -> None:
        # I21：先选 3 再选 1—2，原子不得被 min/max 归并重排成升序。
        data = _xlsx_sheet1(_three_rows())
        selection = Selection(
            kind="limited",
            ranges=(
                RowsRange(kind="rows", sheet="S1", start=3, end=3),
                RowsRange(kind="rows", sheet="S1", start=1, end=2),
            ),
        )
        parsed = _parse(AttachmentKind.XLSX, data, selection)

        assert parsed.read_state is ReadState.READY
        assert parsed.coverage == "full"
        assert parsed.read_ranges == [
            to_wire_dict(RowsRange(kind="rows", sheet="S1", start=3, end=3)),
            to_wire_dict(RowsRange(kind="rows", sheet="S1", start=1, end=2)),
        ]
        assert parsed.unread_ranges == []
        assert parsed.stats["rows"] == 3
        # 投递内容顺序与原子顺序一致，覆盖不重不漏。
        assert _rows_of_blocks(parsed) == [3, 1, 2]
        _assert_selected_range_contract(selection, parsed)

        # 与规范化（升序）选择语义一致：覆盖集合相同。
        ascending = Selection(
            kind="limited",
            ranges=(
                RowsRange(kind="rows", sheet="S1", start=1, end=2),
                RowsRange(kind="rows", sheet="S1", start=3, end=3),
            ),
        )
        assert sorted(_rows_of_blocks(_parse(AttachmentKind.XLSX, data, ascending))) == sorted(_rows_of_blocks(parsed))

        # digest 稳定：同一选择重复解析结果一致。
        again = _parse(AttachmentKind.XLSX, data, selection)
        assert again.read_ranges == parsed.read_ranges
        assert _selected_range_digest(selection, again) == _selected_range_digest(selection, parsed)

    def test_unordered_multi_segment_keeps_selection_order(self) -> None:
        data = _xlsx_sheet1(_three_rows())
        selection = Selection(
            kind="limited",
            ranges=(
                RowsRange(kind="rows", sheet="S1", start=2, end=2),
                RowsRange(kind="rows", sheet="S1", start=1, end=1),
                RowsRange(kind="rows", sheet="S1", start=3, end=3),
            ),
        )
        parsed = _parse(AttachmentKind.XLSX, data, selection)

        assert parsed.read_ranges == [to_wire_dict(atom) for atom in selection.ranges]
        assert parsed.unread_ranges == []
        assert parsed.coverage == "full"
        assert _rows_of_blocks(parsed) == [2, 1, 3]
        assert parsed.stats["rows"] == 3
        _assert_selected_range_contract(selection, parsed)

    def test_ascending_multi_segment_unchanged(self) -> None:
        data = _xlsx_sheet1(_three_rows())
        selection = Selection(
            kind="limited",
            ranges=(
                RowsRange(kind="rows", sheet="S1", start=1, end=2),
                RowsRange(kind="rows", sheet="S1", start=3, end=3),
            ),
        )
        parsed = _parse(AttachmentKind.XLSX, data, selection)

        assert parsed.read_state is ReadState.READY
        assert parsed.read_ranges == [to_wire_dict(atom) for atom in selection.ranges]
        assert parsed.unread_ranges == []
        assert _rows_of_blocks(parsed) == [1, 2, 3]
        _assert_selected_range_contract(selection, parsed)

    def test_disjoint_segments_are_rejected(self) -> None:
        # 支持边界：仅相邻多段（合起来连续）；存在空档即明确拒绝，不静默扩读。
        data = _xlsx_sheet1(_three_rows())
        selection = Selection(
            kind="limited",
            ranges=(
                RowsRange(kind="rows", sheet="S1", start=1, end=1),
                RowsRange(kind="rows", sheet="S1", start=3, end=3),
            ),
        )
        with pytest.raises(AttachmentParseError) as exc:
            _parse(AttachmentKind.XLSX, data, selection)
        assert exc.value.code == "invalid_selection"

    def test_reversed_adjacent_out_of_range_stays_unread(self) -> None:
        # 与 I20 越界语义共存：越界部分仍记未读，已读按实际存在的行核定。
        data = _xlsx_sheet1('<row r="1"><c r="A1"><v>1</v></c></row>')
        selection = Selection(
            kind="limited",
            ranges=(
                RowsRange(kind="rows", sheet="S1", start=2, end=3),
                RowsRange(kind="rows", sheet="S1", start=1, end=1),
            ),
        )
        parsed = _parse(AttachmentKind.XLSX, data, selection)

        assert parsed.read_state is ReadState.PARTIAL
        assert parsed.coverage == "limited"
        assert parsed.read_ranges == [to_wire_dict(RowsRange(kind="rows", sheet="S1", start=1, end=1))]
        assert parsed.unread_ranges == [to_wire_dict(RowsRange(kind="rows", sheet="S1", start=2, end=3))]
        assert _rows_of_blocks(parsed) == [1]
        assert parsed.stats["rows"] == 1
        _assert_selected_range_contract(selection, parsed)


# ---------------------------------------------------------------------------
# XLSX 单元格类型证据与双流坐标核配（I22）
# ---------------------------------------------------------------------------

_DATE_STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet {_NS}>
<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="2">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="14" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
</cellXfs>
</styleSheet>"""


def _typed_rows(rows_xml: str, *, styles: str | None = None) -> bytes:
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f"<worksheet {_NS}><sheetData>{rows_xml}</sheetData></worksheet>"
    )
    return _xlsx_bytes(sheet1=sheet, styles=styles)


class _FakeCell:
    """最小单元格替身（只暴露迭代与类型证据需要的属性）。"""

    def __init__(self, row: int, column: int, value: Any, data_type: str = "n") -> None:
        self.row = row
        self.column = column
        self._value = value
        self.data_type = data_type
        self.number_format = "General"

    @property
    def value(self) -> Any:
        return self._value

    @property
    def coordinate(self) -> str:
        return f"{attachment_worker._column_letter(self.column)}{self.row}"  # noqa: SLF001  # 测试内复用列字母


class _FakeSheet:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def iter_rows(self, max_row: int | None = None) -> Any:
        return iter(self._rows)

    def close(self) -> None:
        pass


class _FakeWorkbook(dict[str, _FakeSheet]):
    def close(self) -> None:
        pass


def _patch_dual_views(
    monkeypatch: pytest.MonkeyPatch,
    formula_rows: list[tuple[Any, ...]],
    cached_rows: list[tuple[Any, ...]],
) -> None:
    monkeypatch.setattr(
        attachment_worker,
        "_load_openpyxl",
        lambda data: (
            _FakeWorkbook(S1=_FakeSheet(formula_rows)),
            _FakeWorkbook(S1=_FakeSheet(cached_rows)),
        ),
    )


class TestXlsxCellTypeEvidence:
    def test_text_starting_with_equals_stays_text(self) -> None:
        # 文本以「=」开头不是公式：仍是 string，但保留「看起来像公式」的事实标注。
        data = _typed_rows('<row r="1"><c r="A1" t="inlineStr"><is><t>=SUM(B1:B2)</t></is></c></row>')
        cell = _xlsx_cells(_parse(AttachmentKind.XLSX, data))["A1"]
        assert cell["formula_text"] is None
        assert cell["value"]["raw_type"] == "string"
        assert cell["value"]["lexeme"] == "=SUM(B1:B2)"
        assert cell["value"]["looks_like_formula"] is True
        assert cell["value"]["xml_type"] == "inlineStr"
        assert cell["xlsx_cell_meta"]["cache_missing_reason"] == "not_applicable"

    def test_error_value_is_error_type_not_string(self) -> None:
        data = _typed_rows(
            '<row r="1"><c r="A1" t="e"><v>#DIV/0!</v></c><c r="B1" t="e"><f>1/0</f><v>#DIV/0!</v></c></row>'
        )
        cells = _xlsx_cells(_parse(AttachmentKind.XLSX, data))
        assert cells["A1"]["formula_text"] is None
        assert cells["A1"]["value"]["raw_type"] == "error"
        assert cells["A1"]["value"]["lexeme"] == "#DIV/0!"
        assert cells["B1"]["formula_text"] == "=1/0"
        assert cells["B1"]["value"]["raw_type"] == "error"
        assert cells["B1"]["xlsx_cell_meta"]["cache_missing_reason"] == "cache_error"

    def test_date_keeps_serial_and_epoch(self) -> None:
        data = _typed_rows('<row r="1"><c r="A1" s="1"><v>45000</v></c></row>', styles=_DATE_STYLES)
        cell = _xlsx_cells(_parse(AttachmentKind.XLSX, data))["A1"]
        assert cell["value"]["raw_type"] == "datetime"
        assert cell["value"]["serial"] == "45000"
        assert cell["value"]["date_epoch"] == "1904"
        assert cell["value"]["xml_type"] is None
        assert cell["xlsx_cell_meta"]["date_epoch"] == "1904"
        assert cell["xlsx_cell_meta"]["xml_value_lexeme"] == "45000"

    def test_exact_numeric_keeps_xml_lexeme(self) -> None:
        data = _typed_rows('<row r="1"><c r="A1"><v>1.500</v></c><c r="B1"><v>0012</v></c></row>')
        cells = _xlsx_cells(_parse(AttachmentKind.XLSX, data))
        assert cells["A1"]["value"]["raw_type"] == "json_decimal"
        assert cells["A1"]["value"]["xml_lexeme"] == "1.500"
        assert cells["B1"]["value"]["raw_type"] == "json_integer"
        assert cells["B1"]["value"]["lexeme"] == "12"
        assert cells["B1"]["value"]["xml_lexeme"] == "0012"

    def test_formula_with_cache_still_paired(self) -> None:
        data = _typed_rows('<row r="1"><c r="A1"><v>2</v></c><c r="B1"><f>A1*2</f><v>4</v></c></row>')
        cells = _xlsx_cells(_parse(AttachmentKind.XLSX, data))
        assert cells["A1"]["formula_text"] is None
        assert cells["B1"]["formula_text"] == "=A1*2"
        assert cells["B1"]["value"]["raw_type"] == "json_integer"
        assert cells["B1"]["value"]["xml_lexeme"] == "4"
        assert cells["B1"]["xlsx_cell_meta"]["cache_present"] is True
        assert json.loads(cells["B1"]["xlsx_cell_meta"]["cache_raw_value"])["lexeme"] == "4"

    def test_unverified_structure_falls_back_without_faking_evidence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # XML 探测不可核时不伪造类型证据：退回 openpyxl data_type，并标注证据未核。
        monkeypatch.setattr(attachment_worker.XlsxCacheProbe, "prefetch", lambda self, sheet, cells: None)
        monkeypatch.setattr(attachment_worker.XlsxCacheProbe, "probe", lambda self, sheet, cell: None)
        data = _typed_rows('<row r="1"><c r="A1"><v>1.500</v></c></row>')
        cell = _xlsx_cells(_parse(AttachmentKind.XLSX, data))["A1"]
        assert cell["value"]["raw_type"] == "json_decimal"
        assert "xml_lexeme" not in cell["value"]
        assert cell["value"]["xml_evidence_unverified"] is True
        assert cell["xlsx_cell_meta"]["xml_value_lexeme"] is None

    def test_dual_stream_length_mismatch_rejected_for_all_selection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 行长度不一致：不得静默按索引配对，all 轴无法表达部分读取须明确拒绝。
        _patch_dual_views(
            monkeypatch,
            [(_FakeCell(1, 1, "=1+1", "f"),)],
            [(_FakeCell(1, 1, 2), _FakeCell(1, 2, 3))],
        )
        data = _xlsx_sheet1('<row r="1"><c r="A1"><v>2</v></c></row>')
        with pytest.raises(AttachmentParseError) as exc:
            _parse(AttachmentKind.XLSX, data)
        assert exc.value.code == "invalid_structure"

    def test_dual_stream_coordinate_mismatch_marks_rows_unread(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 同一行坐标不一致（公式视图 A1 vs 缓存视图 B1）：如实上报为未读并给出限制。
        _patch_dual_views(monkeypatch, [(_FakeCell(1, 1, "=1+1", "f"),)], [(_FakeCell(1, 2, 2),)])
        data = _xlsx_sheet1('<row r="1"><c r="A1"><v>2</v></c></row>')
        atom = RowsRange(kind="rows", sheet="S1", start=1, end=1)
        selection = Selection(kind="limited", ranges=(atom,))
        parsed = _parse(AttachmentKind.XLSX, data, selection)
        assert parsed.read_state is ReadState.UNREAD
        assert parsed.coverage == "limited"
        assert parsed.blocks == []
        assert parsed.read_ranges == []
        assert parsed.unread_ranges == [to_wire_dict(atom)]
        assert any(item["code"] == "xlsx_dual_stream_mismatch" for item in parsed.limitations)
        _assert_selected_range_contract(selection, parsed)


# ---------------------------------------------------------------------------
# XLSX 容器前置预算与结构门（I08）
# ---------------------------------------------------------------------------


def _assert_rejected_before_load(
    monkeypatch: pytest.MonkeyPatch,
    data: bytes,
    *,
    selection: Selection | None = None,
    code: str = "invalid_structure",
) -> None:
    """断言拒绝发生在 openpyxl 分配/展开之前（加载函数一次都未被调用）。"""

    calls: list[bytes] = []

    def _forbidden(payload: bytes) -> tuple[Any, Any]:
        calls.append(payload)
        raise AssertionError("openpyxl load must not run before the XLSX preflight gate")

    monkeypatch.setattr(attachment_worker, "_load_openpyxl", _forbidden)
    with pytest.raises(AttachmentParseError) as exc:
        _parse(AttachmentKind.XLSX, data, selection)
    assert exc.value.code == code
    assert calls == []


class TestXlsxPreflightBudget:
    def test_entry_count_limit_rejected_before_load(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(attachment_worker, "MAX_XLSX_ENTRIES", 3)
        _assert_rejected_before_load(monkeypatch, _xlsx_bytes())

    def test_total_expanded_limit_rejected_before_load(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(attachment_worker, "MAX_XLSX_TOTAL_BYTES", 64)
        _assert_rejected_before_load(monkeypatch, _xlsx_bytes())

    def test_single_entry_limit_rejected_before_load(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(attachment_worker, "MAX_XLSX_ENTRY_BYTES", 100)
        _assert_rejected_before_load(monkeypatch, _xlsx_bytes())

    def test_malicious_shared_strings_size_rejected_before_load(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(attachment_worker, "MAX_XLSX_SHARED_STRINGS_BYTES", 8)
        data = _xlsx_bytes(shared_strings=_SHARED_STRINGS + " " * 64)
        _assert_rejected_before_load(monkeypatch, data)

    def test_missing_workbook_metadata_rejected_before_load(self, monkeypatch: pytest.MonkeyPatch) -> None:
        data = _rewrite_xlsx(_xlsx_bytes(), drop=frozenset({"xl/_rels/workbook.xml.rels"}))
        _assert_rejected_before_load(monkeypatch, data)

    def test_unreadable_workbook_metadata_rejected_before_load(self, monkeypatch: pytest.MonkeyPatch) -> None:
        data = _rewrite_xlsx(_xlsx_bytes(), replace={"xl/workbook.xml": b"<not-xml"})
        _assert_rejected_before_load(monkeypatch, data)

    def test_duplicate_case_variant_entry_rejected_before_load(self, monkeypatch: pytest.MonkeyPatch) -> None:
        data = _rewrite_xlsx(_xlsx_bytes())
        buffer = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
            for info in source.infolist():
                target.writestr(info, source.read(info))
            target.writestr("XL/WORKBOOK.XML", b"<workbook/>")
        _assert_rejected_before_load(monkeypatch, buffer.getvalue())

    def test_symlink_entry_rejected_before_load(self, monkeypatch: pytest.MonkeyPatch) -> None:
        data = _rewrite_xlsx(_xlsx_bytes())
        buffer = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
            for info in source.infolist():
                target.writestr(info, source.read(info))
            link = zipfile.ZipInfo("xl/link.xml")
            link.external_attr = 0o120777 << 16
            target.writestr(link, "xl/workbook.xml")
        _assert_rejected_before_load(monkeypatch, buffer.getvalue())

    def test_unknown_sheet_rejected_before_load(self, monkeypatch: pytest.MonkeyPatch) -> None:
        selection = Selection(kind="limited", ranges=(SheetsRange(kind="sheets", names=("Nope",)),))
        _assert_rejected_before_load(monkeypatch, _xlsx_bytes(), selection=selection, code="invalid_selection")

    def test_preflight_rejection_leaves_no_temp_files(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
        monkeypatch.setattr(attachment_worker, "MAX_XLSX_ENTRIES", 3)
        with pytest.raises(AttachmentParseError) as exc:
            _parse(AttachmentKind.XLSX, _xlsx_bytes())
        assert exc.value.code == "invalid_structure"
        assert list(tmp_path.iterdir()) == []

    def test_shared_strings_parsed_once_for_both_views(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from openpyxl.reader import excel as openpyxl_excel

        calls: list[int] = []
        # openpyxl 在 excel 模块内以模块级名字引用该函数；经模块字典取原实现避免私有属性告警。
        original = vars(openpyxl_excel)["read_string_table"]

        def _counting(xml_source: Any) -> Any:
            calls.append(1)
            return original(xml_source)

        monkeypatch.setattr(openpyxl_excel, "read_string_table", _counting)
        parsed = _parse(AttachmentKind.XLSX, _xlsx_bytes(shared_strings=_SHARED_STRINGS))
        assert parsed.read_state is ReadState.READY
        assert parsed.stats["rows"] == 3
        assert len(calls) == 1


# ---------------------------------------------------------------------------
# XLSX XML 实体门与坏缓存稳定错误码（I85）
# ---------------------------------------------------------------------------

_ENTITY_SHARED_STRINGS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<!DOCTYPE sst [<!ENTITY injected "INJECTED">]>\n'
    f"<sst {_NS}><si><t>&injected;</t></si></sst>"
)

_SHARED_STRING_CELL = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    f'<worksheet {_NS}><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c>'
    "</row></sheetData></worksheet>"
)

_ENTITY_SHEET_AFTER_HEADER = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    "<!--" + "p" * 9000 + "-->\n"
    '<!DOCTYPE worksheet [<!ENTITY injected "INJECTED">]>\n'
    f'<worksheet {_NS}><sheetData><row r="1">'
    '<c r="A1" t="inlineStr"><is><t>&injected;</t></is></c>'
    "</row></sheetData></worksheet>"
)


class TestXlsxXmlEntityGate:
    def test_shared_strings_entity_rejected_before_load(self, monkeypatch: pytest.MonkeyPatch) -> None:
        data = _xlsx_bytes(shared_strings=_ENTITY_SHARED_STRINGS)
        _assert_rejected_before_load(monkeypatch, data)

    def test_sheet_entity_declaration_after_header_window_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 实体声明可放在长注释之后：只扫前 8 KiB 的检查拦不住，必须在完整部件上拒绝。
        data = _rewrite_xlsx(
            _xlsx_bytes(),
            replace={"xl/worksheets/sheet1.xml": _ENTITY_SHEET_AFTER_HEADER.encode("utf-8")},
        )
        _assert_rejected_before_load(monkeypatch, data)

    def test_external_entity_rejected_before_load(self, monkeypatch: pytest.MonkeyPatch) -> None:
        shared_strings = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<!DOCTYPE sst [<!ENTITY ext SYSTEM "file:///C:/Windows/win.ini">]>\n'
            f"<sst {_NS}><si><t>&ext;</t></si></sst>"
        )
        _assert_rejected_before_load(monkeypatch, _xlsx_bytes(shared_strings=shared_strings))

    def test_rejection_does_not_depend_on_optional_parser(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 模拟运行期缺少 defusedxml（openpyxl 回落到标准库 iterparse，会展开内部实体）：
        # 本层必须在自身边界内拒绝，而不是依赖 openpyxl 的可选依赖。
        import openpyxl.reader.strings as ox_strings
        import openpyxl.worksheet._reader as ox_sheet_reader

        for module in (ox_sheet_reader, ox_strings):
            monkeypatch.setattr(module, "iterparse", ElementTree.iterparse)
        data = _xlsx_bytes(shared_strings=_ENTITY_SHARED_STRINGS, sheet1=_SHARED_STRING_CELL)
        with pytest.raises(AttachmentParseError) as exc:
            _parse(AttachmentKind.XLSX, data)
        assert exc.value.code == "invalid_structure"
        assert "INJECTED" not in str(exc.value)

    def test_cumulative_declared_size_limit_covers_all_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        data = _xlsx_bytes(shared_strings=_SHARED_STRINGS)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            declared_total = sum(info.file_size for info in archive.infolist())
        monkeypatch.setattr(attachment_worker, "MAX_XLSX_TOTAL_BYTES", declared_total - 1)
        _assert_rejected_before_load(monkeypatch, data)


class TestXlsxBadCacheStableErrors:
    def test_out_of_range_shared_string_index_is_stable_error(self) -> None:
        # openpyxl 惰性迭代时抛 IndexError；不得把裸第三方异常泄漏出解析边界。
        sheet = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<worksheet {_NS}><sheetData><row r="1"><c r="A1" t="s"><v>99</v></c>'
            "</row></sheetData></worksheet>"
        )
        data = _xlsx_bytes(shared_strings=_SHARED_STRINGS, sheet1=sheet)
        with pytest.raises(AttachmentParseError) as exc:
            _parse(AttachmentKind.XLSX, data)
        assert exc.value.code == "invalid_structure"
        assert "cached cell values" in exc.value.message

    def test_non_numeric_cached_value_is_stable_error(self) -> None:
        # XML 声明数值但缓存值非数值：openpyxl 抛 ValueError，同样必须转译为稳定码。
        sheet = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<worksheet {_NS}><sheetData><row r="1"><c r="A1"><f>1+1</f><v>abc</v></c>'
            "</row></sheetData></worksheet>"
        )
        with pytest.raises(AttachmentParseError) as exc:
            _parse(AttachmentKind.XLSX, _xlsx_bytes(sheet1=sheet))
        assert exc.value.code == "invalid_structure"
        assert "cached cell values" in exc.value.message

    def test_out_of_range_date_serial_marked_error_not_applicable(self) -> None:
        # 日期序列越界：openpyxl 以 Excel 错误词法返回 #VALUE!，缓存必须记为 error 且不宣称可适用。
        rows = '<row r="1"><c r="A1" s="1"><f>1+1</f><v>999999999999999</v></c></row>'
        cell = _xlsx_cells(_parse(AttachmentKind.XLSX, _typed_rows(rows, styles=_DATE_STYLES)))["A1"]
        meta = cell["xlsx_cell_meta"]
        assert meta["cache_present"] is True
        assert meta["cache_applicable"] is None
        assert meta["cache_missing_reason"] == "cache_error"
        assert meta["cache_raw_type"] == "error"


# ---------------------------------------------------------------------------
# XLSX 双视图打开的清理契约（I38）
# ---------------------------------------------------------------------------


class _ProbeArchive:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class _ProbeWorkbook:
    """只读视图替身：``close()`` 与 openpyxl 一致地关闭其 archive。"""

    def __init__(self, archive: _ProbeArchive) -> None:
        self._archive = archive
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        self._archive.close()


class _ProbeParser:
    def assign_names(self) -> None:
        pass


class _ProbeSheet:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def iter_rows(self, max_row: int | None = None) -> Any:
        return iter(self._rows)


class _CountingWorkbook(dict[str, _ProbeSheet]):
    def __init__(self, **sheets: _ProbeSheet) -> None:
        super().__init__(**sheets)
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


def _install_probe_excel_reader(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail_step: str | None = None,
    fail_data_only: bool | None = None,
    error_type: type[BaseException] = RuntimeError,
) -> list[Any]:
    """用替身 ExcelReader 逐视图记录构造/关闭，并按需在指定读取步骤注入失败。"""

    from openpyxl.reader import excel as openpyxl_excel
    from openpyxl.styles import stylesheet as openpyxl_stylesheet

    created: list[Any] = []

    class _Reader:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.data_only = bool(kwargs.get("data_only"))
            self.archive = _ProbeArchive()
            self.wb = _ProbeWorkbook(self.archive)
            self.shared_strings: list[Any] = []
            self.parser = _ProbeParser()
            created.append(self)

        def _maybe_fail(self, step: str) -> None:
            if fail_step == step and (fail_data_only is None or fail_data_only == self.data_only):
                raise error_type(f"probe failure at {step}")

        def read_manifest(self) -> None:
            self._maybe_fail("read_manifest")

        def read_strings(self) -> None:
            self.shared_strings = ["probe-string"]

        def read_workbook(self) -> None:
            self._maybe_fail("read_workbook")

        def read_properties(self) -> None:
            self._maybe_fail("read_properties")

        def read_custom(self) -> None:
            self._maybe_fail("read_custom")

        def read_theme(self) -> None:
            self._maybe_fail("read_theme")

        def read_worksheets(self) -> None:
            self._maybe_fail("read_worksheets")

    monkeypatch.setattr(openpyxl_excel, "ExcelReader", _Reader)
    monkeypatch.setattr(openpyxl_stylesheet, "apply_stylesheet", lambda archive, workbook: None)
    return created


class TestXlsxDualViewCleanup:
    def test_second_view_failure_closes_first_view(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 第二个视图构造失败：首个已打开的视图及其 archive 必须被关闭。
        readers = _install_probe_excel_reader(monkeypatch, fail_step="read_workbook", fail_data_only=True)
        with pytest.raises(AttachmentParseError) as exc:
            attachment_worker._load_openpyxl(b"probe")  # noqa: SLF001  # 直测双视图清理契约
        assert exc.value.code == "invalid_structure"
        assert isinstance(exc.value.__cause__, RuntimeError)
        formula_view, cached_view = readers
        assert formula_view.wb.close_calls == 1
        assert formula_view.archive.close_calls == 1
        assert cached_view.archive.close_calls == 1

    def test_second_view_base_exception_also_closes_first_view(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 取消/中断同属「后续失败」：首个视图不得留给 GC 回收。
        readers = _install_probe_excel_reader(
            monkeypatch, fail_step="read_worksheets", fail_data_only=True, error_type=KeyboardInterrupt
        )
        with pytest.raises(KeyboardInterrupt):  # noqa: weak-assertion 关闭断言在 with 块之后（核首个视图与两个 archive）
            attachment_worker._load_openpyxl(b"probe")  # noqa: SLF001  # 直测双视图清理契约
        formula_view, cached_view = readers
        assert formula_view.wb.close_calls == 1
        assert formula_view.archive.close_calls == 1
        assert cached_view.archive.close_calls == 1

    def test_first_view_failure_leaves_no_open_handle(self, monkeypatch: pytest.MonkeyPatch) -> None:
        readers = _install_probe_excel_reader(monkeypatch, fail_step="read_workbook", fail_data_only=False)
        with pytest.raises(AttachmentParseError) as exc:
            attachment_worker._load_openpyxl(b"probe")  # noqa: SLF001  # 直测双视图清理契约
        assert exc.value.code == "invalid_structure"
        assert len(readers) == 1
        assert readers[0].archive.close_calls == 1
        assert readers[0].wb.close_calls == 0

    def test_both_views_open_ownership_goes_to_caller(self, monkeypatch: pytest.MonkeyPatch) -> None:
        readers = _install_probe_excel_reader(monkeypatch)
        formulas, cached = attachment_worker._load_openpyxl(b"probe")  # noqa: SLF001  # 直测双视图清理契约
        assert formulas is readers[0].wb
        assert cached is readers[1].wb
        assert readers[0].wb.close_calls == 0
        assert readers[1].wb.close_calls == 0

    def test_successful_parse_closes_both_views(self, monkeypatch: pytest.MonkeyPatch) -> None:
        formula_view = _CountingWorkbook(S1=_ProbeSheet([(_FakeCell(1, 1, "=1+1", "f"),)]))
        cached_view = _CountingWorkbook(S1=_ProbeSheet([(_FakeCell(1, 1, 2),)]))
        monkeypatch.setattr(attachment_worker, "_load_openpyxl", lambda data: (formula_view, cached_view))
        parsed = _parse(AttachmentKind.XLSX, _xlsx_sheet1('<row r="1"><c r="A1"><v>2</v></c></row>'))
        assert parsed.read_state is ReadState.READY
        assert formula_view.close_calls == 1
        assert cached_view.close_calls == 1

    def test_normal_parse_is_not_regressed(self) -> None:
        parsed = _parse(AttachmentKind.XLSX, _xlsx_bytes())
        assert parsed.read_state is ReadState.READY
        assert parsed.coverage == "full"
        assert parsed.stats["rows"] == 3


# ---------------------------------------------------------------------------
# XLSX 双视图按坐标配对与隐藏属性取证（I72）
# ---------------------------------------------------------------------------


class _CloseFailingWorkbook(_CountingWorkbook):
    def close(self) -> None:
        super().close()
        raise RuntimeError("first view close failed")


class TestXlsxDualViewClosePropagation:
    def test_probe_close_error_still_closes_both_views(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 探针关闭抛错不得跳过两个 workbook 的释放，且异常要上报（不吞）。
        formula_view = _CountingWorkbook(S1=_ProbeSheet([(_FakeCell(1, 1, "=1+1", "f"),)]))
        cached_view = _CountingWorkbook(S1=_ProbeSheet([(_FakeCell(1, 1, 2),)]))

        def _boom(self: Any) -> None:
            raise RuntimeError("probe close failed")

        monkeypatch.setattr(attachment_worker, "_load_openpyxl", lambda data: (formula_view, cached_view))
        monkeypatch.setattr(attachment_worker.XlsxCacheProbe, "close", _boom)
        with pytest.raises(RuntimeError, match="probe close failed"):
            _parse(AttachmentKind.XLSX, _xlsx_sheet1('<row r="1"><c r="A1"><v>2</v></c></row>'))
        assert formula_view.close_calls == 1
        assert cached_view.close_calls == 1

    def test_first_view_close_error_still_closes_second(self, monkeypatch: pytest.MonkeyPatch) -> None:
        formula_view = _CloseFailingWorkbook(S1=_ProbeSheet([(_FakeCell(1, 1, "=1+1", "f"),)]))
        cached_view = _CountingWorkbook(S1=_ProbeSheet([(_FakeCell(1, 1, 2),)]))
        monkeypatch.setattr(attachment_worker, "_load_openpyxl", lambda data: (formula_view, cached_view))
        with pytest.raises(RuntimeError, match="first view close failed"):
            _parse(AttachmentKind.XLSX, _xlsx_sheet1('<row r="1"><c r="A1"><v>2</v></c></row>'))
        assert cached_view.close_calls == 1


class TestXlsxDualViewCoordinates:
    def test_missing_rows_do_not_shift_following_row_numbers(self) -> None:
        # 物理行 2/3 不存在、行 4 首格不在 A 列：不得补造空行，也不得把 C4 记成 C3。
        data = _xlsx_sheet1('<row r="1"><c r="A1"><v>1</v></c></row><row r="4"><c r="C4"><v>4</v></c></row>')
        parsed = _parse(AttachmentKind.XLSX, data)
        block = json.loads(parsed.blocks[0])
        assert [row["row"] for row in block["rows"]] == [1, 4]
        assert [cell["cell"] for cell in block["rows"][1]["cells"]] == ["C4"]
        assert block["rows"][1]["cells"][0]["value"]["lexeme"] == "4"
        assert parsed.stats["rows"] == 2
        assert parsed.read_state is ReadState.READY

    def test_row_starting_below_column_a_keeps_physical_row(self) -> None:
        data = _xlsx_sheet1('<row r="2"><c r="B2"><v>2</v></c></row>')
        parsed = _parse(AttachmentKind.XLSX, data)
        block = json.loads(parsed.blocks[0])
        assert [row["row"] for row in block["rows"]] == [2]
        assert [cell["cell"] for cell in block["rows"][0]["cells"]] == ["B2"]

    def test_extra_trailing_cell_is_not_silently_truncated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 行尾多出一格：按坐标无法一对一核配，必须整表记未读而不是截短配对。
        _patch_dual_views(
            monkeypatch,
            [(_FakeCell(1, 1, "=1+1", "f"),)],
            [(_FakeCell(1, 1, 2), _FakeCell(1, 2, 3))],
        )
        atom = RowsRange(kind="rows", sheet="S1", start=1, end=1)
        selection = Selection(kind="limited", ranges=(atom,))
        parsed = _parse(AttachmentKind.XLSX, _xlsx_sheet1('<row r="1"><c r="A1"><v>2</v></c></row>'), selection)
        assert parsed.read_state is ReadState.UNREAD
        assert parsed.blocks == []
        assert parsed.unread_ranges == [to_wire_dict(atom)]
        assert any(item["code"] == "xlsx_dual_stream_mismatch" for item in parsed.limitations)


class TestXlsxHiddenAttributes:
    def _codes(self, parsed: Any) -> list[str]:
        return [item["code"] for item in parsed.limitations]

    def test_hidden_sheet_is_reported_without_blocking_read(self) -> None:
        parsed = _parse(AttachmentKind.XLSX, _xlsx_bytes(sheet_state="hidden"))
        assert parsed.read_state is ReadState.READY
        assert parsed.stats["rows"] == 3
        assert "xlsx_sheet_hidden" in self._codes(parsed)
        limitation = next(item for item in parsed.limitations if item["code"] == "xlsx_sheet_hidden")
        assert "hidden" in limitation["message"]

    def test_hidden_rows_and_columns_are_reported(self) -> None:
        rows = (
            '<row r="1"><c r="A1"><v>1</v></c></row>'
            '<row r="2" hidden="1"><c r="A2"><v>2</v></c></row>'
            '<row r="3"><c r="A3"><v>3</v></c></row>'
        )
        cols = '<cols><col min="2" max="3" hidden="1"/></cols>'
        parsed = _parse(AttachmentKind.XLSX, _xlsx_sheet1(rows, cols_xml=cols))
        codes = self._codes(parsed)
        assert "xlsx_hidden_rows" in codes
        assert "xlsx_hidden_columns" in codes
        row_message = next(item["message"] for item in parsed.limitations if item["code"] == "xlsx_hidden_rows")
        column_message = next(item["message"] for item in parsed.limitations if item["code"] == "xlsx_hidden_columns")
        assert "隐藏行：2" in row_message
        assert "隐藏列：B-C" in column_message

    def test_hidden_row_outside_selected_rows_is_not_reported(self) -> None:
        rows = (
            '<row r="1"><c r="A1"><v>1</v></c></row>'
            '<row r="2" hidden="1"><c r="A2"><v>2</v></c></row>'
            '<row r="3"><c r="A3"><v>3</v></c></row>'
        )
        selection = Selection(kind="limited", ranges=(RowsRange(kind="rows", sheet="S1", start=3, end=3),))
        parsed = _parse(AttachmentKind.XLSX, _xlsx_sheet1(rows), selection)
        assert "xlsx_hidden_rows" not in self._codes(parsed)

    def test_hidden_rows_outside_selection_do_not_consume_report_sample(self) -> None:
        # N41：区外隐藏行先占满 256 个上报样本时，选区内的隐藏行仍必须上报。
        rows = "".join(
            f'<row r="{index}" hidden="1"><c r="A{index}"><v>{index}</v></c></row>' for index in range(1, 301)
        )
        rows += '<row r="301"><c r="A301"><v>301</v></c></row>'
        rows += '<row r="302" hidden="1"><c r="A302"><v>302</v></c></row>'
        selection = Selection(kind="limited", ranges=(RowsRange(kind="rows", sheet="S1", start=301, end=302),))
        parsed = _parse(AttachmentKind.XLSX, _xlsx_sheet1(rows), selection)
        assert parsed.read_state is ReadState.READY
        assert "xlsx_hidden_rows" in self._codes(parsed)
        row_message = next(item["message"] for item in parsed.limitations if item["code"] == "xlsx_hidden_rows")
        assert "隐藏行：302" in row_message
        assert "1-300" not in row_message

    def test_hidden_rows_truncation_is_scoped_to_selection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 样本上限后仍须明确标注「仅列前 N 个」，不得因区外样本占满而静默丢弃选区内行。
        monkeypatch.setattr(attachment_worker, "MAX_REPORTED_HIDDEN_ROWS", 2)
        rows = "".join(f'<row r="{index}" hidden="1"><c r="A{index}"><v>{index}</v></c></row>' for index in range(1, 4))
        rows += '<row r="4"><c r="A4"><v>4</v></c></row>'
        rows += "".join(
            f'<row r="{index}" hidden="1"><c r="A{index}"><v>{index}</v></c></row>' for index in range(5, 7)
        )
        selection = Selection(kind="limited", ranges=(RowsRange(kind="rows", sheet="S1", start=1, end=6),))
        parsed = _parse(AttachmentKind.XLSX, _xlsx_sheet1(rows), selection)
        row_message = next(item["message"] for item in parsed.limitations if item["code"] == "xlsx_hidden_rows")
        assert "隐藏行：1-2" in row_message
        assert "仅列前 2 个" in row_message

    def test_unverifiable_hidden_attributes_are_reported_as_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 结构超界时不能把「隐藏属性未知」当成「全部可见」。
        monkeypatch.setattr(attachment_worker, "MAX_SHEET_XML_BYTES", 10)
        atom = RowsRange(kind="rows", sheet="S1", start=1, end=1)
        parsed = _parse(
            AttachmentKind.XLSX,
            _xlsx_sheet1('<row r="1"><c r="A1"><v>1</v></c></row>'),
            Selection(kind="limited", ranges=(atom,)),
        )
        assert parsed.read_state is ReadState.UNREAD
        assert "xlsx_hidden_attributes_unverified" in self._codes(parsed)


# ---------------------------------------------------------------------------
# 内容类型嗅探
# ---------------------------------------------------------------------------


def _encode_image(fmt: str, size: tuple[int, int], mode: str = "RGB") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, "red" if mode == "RGB" else 128).save(buffer, format=fmt)
    return buffer.getvalue()


def _png_with_declared_size(width: int, height: int) -> bytes:
    """改写合法 PNG 的 IHDR 声明尺寸（重算 CRC），用于验证解码前按声明尺寸拒绝。"""

    data = bytearray(_encode_image("PNG", (8, 8)))
    struct.pack_into(">II", data, 16, width, height)  # 签名 8 + 长度 4 + 类型 4
    struct.pack_into(">I", data, 29, zlib.crc32(bytes(data[12:29])))  # IHDR 的 type+data 校验
    return bytes(data)


def _image_block(parsed: Any) -> dict[str, Any]:
    assert len(parsed.blocks) == 1
    return json.loads(parsed.blocks[0])


class TestImageExtraction:
    @pytest.mark.parametrize(
        ("fmt", "kind", "size"), [("PNG", AttachmentKind.PNG, (123, 45)), ("JPEG", AttachmentKind.JPEG, (64, 32))]
    )
    def test_image_ready_after_real_pixel_decode(self, fmt: str, kind: AttachmentKind, size: tuple[int, int]) -> None:
        data = _encode_image(fmt, size)
        parsed = _parse(kind, data)
        block = _image_block(parsed)
        assert parsed.read_state is ReadState.READY
        assert parsed.coverage == "full"
        assert (block["width"], block["height"]) == size
        assert block["format"] == fmt
        assert block["pixels"] == size[0] * size[1]
        # 真解码已发生，是本层给出 READY 的证据。
        assert block["pixel_decoded"] is True

    def test_truncated_png_is_rejected(self) -> None:
        data = _encode_image("PNG", (128, 96))
        truncated = data[: len(data) // 2]
        with pytest.raises(AttachmentParseError) as exc:
            _parse(AttachmentKind.PNG, truncated)
        assert exc.value.code == "invalid_structure"

    def test_truncated_jpeg_pixels_are_rejected(self) -> None:
        # JPEG 的 verify() 是空操作，截断只能由真像素解码发现。
        data = _encode_image("JPEG", (256, 192))
        truncated = data[: len(data) // 2]
        with pytest.raises(AttachmentParseError) as exc:
            _parse(AttachmentKind.JPEG, truncated)
        assert exc.value.code == "invalid_structure"

    def test_declared_huge_dimensions_rejected_before_decode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        data = _png_with_declared_size(100_000, 100_000)
        load_calls: list[str] = []
        original_load = Image.Image.load

        def _spy_load(self: Image.Image, *args: Any, **kwargs: Any) -> None:
            load_calls.append(self.mode)
            original_load(self, *args, **kwargs)

        monkeypatch.setattr(Image.Image, "load", _spy_load)
        with pytest.raises(AttachmentParseError) as exc:
            _parse(AttachmentKind.PNG, data)
        assert exc.value.code == "image_pixels_exceeded"
        assert load_calls == []

    def test_dimensions_over_pixel_budget_rejected_before_decode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        data = _png_with_declared_size(attachment_worker.MAX_IMAGE_PIXELS + 1, 1)
        load_calls: list[str] = []
        original_load = Image.Image.load

        def _spy_load(self: Image.Image, *args: Any, **kwargs: Any) -> None:
            load_calls.append(self.mode)
            original_load(self, *args, **kwargs)

        monkeypatch.setattr(Image.Image, "load", _spy_load)
        with pytest.raises(AttachmentParseError) as exc:
            _parse(AttachmentKind.PNG, data)
        assert exc.value.code == "image_pixels_exceeded"
        assert load_calls == []

    def test_decoded_bytes_budget_rejected_before_decode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        data = _encode_image("PNG", (16, 16))
        load_calls: list[str] = []
        original_load = Image.Image.load

        def _spy_load(self: Image.Image, *args: Any, **kwargs: Any) -> None:
            load_calls.append(self.mode)
            original_load(self, *args, **kwargs)

        monkeypatch.setattr(attachment_worker, "MAX_IMAGE_DECODED_BYTES", 1)
        monkeypatch.setattr(Image.Image, "load", _spy_load)
        with pytest.raises(AttachmentParseError) as exc:
            _parse(AttachmentKind.PNG, data)
        assert exc.value.code == "image_decoded_bytes_exceeded"
        assert load_calls == []

    def test_cmyk_jpeg_decoded_bytes_counted_by_mode_channels(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CMYK（4 通道）真实 JPEG：解码预算按 ``getmodebands`` 通道数核算，不按 RGB=3 硬编码。"""

        size = (32, 24)
        pixels = size[0] * size[1]
        data = _encode_image("JPEG", size, mode="CMYK")
        assert Image.getmodebands("CMYK") == 4
        load_calls: list[str] = []
        original_load = Image.Image.load

        def _spy_load(self: Image.Image, *args: Any, **kwargs: Any) -> None:
            load_calls.append(self.mode)
            original_load(self, *args, **kwargs)

        monkeypatch.setattr(Image.Image, "load", _spy_load)
        monkeypatch.setattr(attachment_worker, "MAX_IMAGE_DECODED_BYTES", pixels * 4 - 1)
        with pytest.raises(AttachmentParseError) as exc:
            _parse(AttachmentKind.JPEG, data)
        assert exc.value.code == "image_decoded_bytes_exceeded"
        assert load_calls == []

        # 预算恰为 4 通道字节数时通过并真解码：若按 RGB=3 估算则不会拦下上一步。
        monkeypatch.setattr(attachment_worker, "MAX_IMAGE_DECODED_BYTES", pixels * 4)
        parsed = _parse(AttachmentKind.JPEG, data)
        assert parsed.read_state is ReadState.READY
        assert load_calls and set(load_calls) == {"CMYK"}

    def test_zero_char_stats_does_not_change_image_state(self) -> None:
        parsed = _parse(AttachmentKind.PNG, _encode_image("PNG", (10, 10)))
        # 图像无可提取字符：chars 如实记为不适用，0 字符不改写已核状态。
        assert parsed.stats["chars"] is None
        assert parsed.stats["source_bytes"] is not None
        assert parsed.read_state is ReadState.READY
        assert [item["code"] for item in parsed.limitations] == ["image_text_not_extracted"]


def _pdf_bytes(pages: int, *, password: str | None = None) -> bytes:
    """生成指定页数的空白 PDF（password 非空时加密），用于 PDF 覆盖缺口用例。"""

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    if password is not None:
        writer.encrypt(password)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class TestPdfExtraction:
    def test_encrypted_pdf_is_explicit_error(self) -> None:
        # 需口令的加密 PDF 不是「结构损坏」：明确 pdf_encrypted，不伪装成不可读结构。
        with pytest.raises(AttachmentParseError) as exc:
            _parse(AttachmentKind.PDF, _pdf_bytes(1, password="secret"))
        assert exc.value.code == "pdf_encrypted"

    def test_empty_password_encryption_is_still_readable(self) -> None:
        # 空口令加密由 pypdf 自动解密：可读页面照常逐页处理，不得误报 pdf_encrypted。
        parsed = _parse(AttachmentKind.PDF, _pdf_bytes(1, password=""))
        assert parsed.read_state is ReadState.PARTIAL
        assert [item["code"] for item in parsed.limitations] == ["page_text_unavailable"]
        _assert_selected_range_contract(Selection(kind="all"), parsed)

    def test_zero_page_pdf_reports_gap_not_ready(self) -> None:
        parsed = _parse(AttachmentKind.PDF, _pdf_bytes(0))
        assert parsed.read_state is ReadState.PARTIAL
        assert parsed.coverage == "limited"
        assert parsed.body_text == ""
        assert [item["code"] for item in parsed.limitations] == ["pdf_no_pages"]
        assert parsed.stats["pages"] == 0
        assert parsed.read_ranges == []
        assert parsed.unread_ranges == []
        _assert_selected_range_contract(Selection(kind="all"), parsed)

    def test_single_page_failure_is_recorded_as_unread(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = {"count": 0}

        def _fake_extract_text(self: PageObject, *args: Any, **kwargs: Any) -> str:
            calls["count"] += 1
            if calls["count"] == 2:
                raise ValueError("page content stream is broken")
            return "hello"

        monkeypatch.setattr(PageObject, "extract_text", _fake_extract_text)
        selection = Selection(kind="limited", ranges=(PagesRange(kind="pages", start=1, end=3),))
        parsed = _parse(AttachmentKind.PDF, _pdf_bytes(3), selection)
        # 单页失败只记该页未读，其余页照常给出文本，不把整份 PDF 判为无效。
        assert parsed.read_state is ReadState.PARTIAL
        assert parsed.body_text == "hello\n\nhello"
        assert [item["code"] for item in parsed.limitations] == ["page_text_unreadable"]
        assert parsed.read_ranges == [
            {"kind": "pages", "start": 1, "end": 1},
            {"kind": "pages", "start": 3, "end": 3},
        ]
        assert parsed.unread_ranges == [{"kind": "pages", "start": 2, "end": 2}]
        _assert_selected_range_contract(selection, parsed)

    def test_mixed_pages_all_selection_is_contract_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = {"count": 0}

        def _fake_extract_text(self: PageObject, *args: Any, **kwargs: Any) -> str:
            calls["count"] += 1
            return "" if calls["count"] == 2 else "text"

        monkeypatch.setattr(PageObject, "extract_text", _fake_extract_text)
        parsed = _parse(AttachmentKind.PDF, _pdf_bytes(3))
        # 全选混合页：缺口如实记录，但 all 的 read/unread 只能为空（§3.3.1）。
        assert parsed.read_state is ReadState.PARTIAL
        assert parsed.coverage == "limited"
        assert parsed.body_text == "text\n\ntext"
        assert [item["code"] for item in parsed.limitations] == ["page_text_unavailable"]
        assert parsed.read_ranges == []
        assert parsed.unread_ranges == []
        _assert_selected_range_contract(Selection(kind="all"), parsed)


class TestKindSniffing:
    def test_magic_mismatch_rejected(self) -> None:
        with pytest.raises(AttachmentWorkerError) as exc:
            detect_attachment_kind("a.pdf", b"not a pdf")
        assert exc.value.code == "attachment_type_mismatch"

    def test_path_in_name_rejected(self) -> None:
        with pytest.raises(AttachmentWorkerError) as exc:
            detect_attachment_kind("../a.txt", b"text")
        assert exc.value.code == "attachment_name_invalid"

    def test_unsupported_extension_rejected(self) -> None:
        with pytest.raises(AttachmentWorkerError) as exc:
            detect_attachment_kind("a.exe", b"MZ")
        assert exc.value.code == "attachment_type_unsupported"

    def test_zip_without_workbook_is_not_xlsx(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("hello.txt", "x")
        with pytest.raises(AttachmentWorkerError) as exc:
            detect_attachment_kind("a.xlsx", buffer.getvalue())
        assert exc.value.code == "attachment_type_mismatch"


# ---------------------------------------------------------------------------
# I27：父子统一 JSON 支持范围（BOM / 标量根 / 空键 Pointer / 非法输入）
# ---------------------------------------------------------------------------


def _exact(raw_type: str, lexeme: str) -> dict[str, str]:
    return {
        "numeric_encoding": "agent-number-v1",
        "raw_type": raw_type,
        "lexeme": lexeme,
        "exact_decimal": lexeme,
    }


class _FakeBinaryStream:
    """注入 worker ``main()`` 的 stdin/stdout 替身（只提供 ``buffer``）。"""

    def __init__(self, buffer: io.BytesIO) -> None:
        self.buffer = buffer


def _run_worker_main(monkeypatch: pytest.MonkeyPatch, wire: bytes) -> dict[str, Any]:
    """把 ``wire`` 作为 stdin 注入真实 worker 模块入口 ``main()``，返回 wire 响应。"""

    stdout = io.BytesIO()
    monkeypatch.setattr(sys, "stdin", cast(Any, _FakeBinaryStream(io.BytesIO(wire))))
    monkeypatch.setattr(sys, "stdout", cast(Any, _FakeBinaryStream(stdout)))
    assert attachment_worker.main() == 0
    response: dict[str, Any] = json.loads(stdout.getvalue().decode("utf-8"))
    return response


def _worker_entry(
    monkeypatch: pytest.MonkeyPatch,
    data: bytes,
    *,
    kind: AttachmentKind,
    selection: Selection,
    read_expansion_consent: bool = False,
) -> dict[str, Any]:
    """经真实 worker 模块入口 ``main()`` 提取，返回 wire 响应。

    ``main()`` 即 ``python -m services.agent_service.attachment_worker`` 的入口函数；
    进程生命周期（真实退出/超时/取消）由 ``TestWorkerLifecycle`` 覆盖，此处按进程内
    stdio 注入以复用同一判定路径。
    """

    header = {
        "schema_version": 1,
        "kind": kind.value,
        "selection": to_wire_dict(selection),
        "csv_header_confirmed": False,
        "read_expansion_consent": read_expansion_consent,
        "source_digest": hashlib.sha256(data).hexdigest(),
        "timeout_s": 30.0,
        "payload_bytes": len(data),
    }
    header_bytes = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return _run_worker_main(monkeypatch, header_bytes + b"\n" + data)


def _padded_wire_header(data: bytes, total_header_bytes: int) -> bytes:
    """构造恰 ``total_header_bytes`` 字节的请求头（``pad`` 键补齐），返回含 framing 换行的线格式。"""

    header: dict[str, Any] = {
        "schema_version": 1,
        "kind": AttachmentKind.TXT.value,
        "selection": to_wire_dict(Selection(kind="all")),
        "csv_header_confirmed": False,
        "source_digest": hashlib.sha256(data).hexdigest(),
        "timeout_s": 30.0,
        "payload_bytes": len(data),
        "pad": "",
    }
    base = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    header["pad"] = "x" * (total_header_bytes - len(base))
    raw = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert len(raw) == total_header_bytes
    return raw + b"\n" + data


class TestHeaderByteBoundary:
    """I36：请求头上限按 header 自身计量，framing 换行不计入。"""

    def test_header_exactly_at_limit_with_newline_is_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        data = b"hello"
        response = _run_worker_main(monkeypatch, _padded_wire_header(data, attachment_worker.MAX_HEADER_BYTES))
        assert response["ok"] is True
        assert response["result"]["body_text"] == "hello"

    def test_header_one_byte_over_limit_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        data = b"hello"
        response = _run_worker_main(monkeypatch, _padded_wire_header(data, attachment_worker.MAX_HEADER_BYTES + 1))
        assert response["ok"] is False
        assert response["error"] == {"code": "invalid_request", "message": "worker header is too large"}

    def test_worker_rejects_non_bool_read_expansion_consent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        data = b"hello"
        header = {
            "schema_version": 1,
            "kind": AttachmentKind.TXT.value,
            "selection": to_wire_dict(Selection(kind="all")),
            "csv_header_confirmed": False,
            "read_expansion_consent": "yes",
            "source_digest": hashlib.sha256(data).hexdigest(),
            "timeout_s": 30.0,
            "payload_bytes": len(data),
        }
        header_bytes = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        response = _run_worker_main(monkeypatch, header_bytes + b"\n" + data)
        assert response["ok"] is False
        assert response["error"] == {
            "code": "invalid_request",
            "message": "worker read_expansion_consent is invalid",
        }


_JSON_ACCEPTED_CASES = [
    (b'\xef\xbb\xbf{"a": 1}', {"a": _exact("json_integer", "1")}),
    (b'\xef\xbb\xbf[1, "x"]', [_exact("json_integer", "1"), "x"]),
    (b"42", _exact("json_integer", "42")),
    (b'"hi"', "hi"),
    (b"true", True),
    (b"null", None),
]

#: (原始字节, 错误码, 错误消息, 父进程嗅探是否前置拒绝)
_JSON_REJECTED_CASES = [
    (b'{"a":', "invalid_structure", "JSON structure is invalid", False),
    (b"not json", "invalid_structure", "JSON structure is invalid", False),
    (b"\xef\xbb\xbf", "invalid_structure", "JSON structure is invalid", True),
    (b"  \n\t", "invalid_structure", "JSON structure is invalid", True),
    (b"\xff\xfe\x00", "invalid_encoding", "JSON is not valid UTF-8 text", True),
]


class TestJsonSupportParity:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("data", "expected_value"), _JSON_ACCEPTED_CASES)
    async def test_bom_and_scalar_roots_accepted_by_both_entries(
        self, monkeypatch: pytest.MonkeyPatch, data: bytes, expected_value: Any
    ) -> None:
        selection = Selection(kind="all")
        parent = await SubprocessAttachmentWorker().extract(_request(data, name="a.json", kind=AttachmentKind.JSON))
        worker = _worker_entry(monkeypatch, data, kind=AttachmentKind.JSON, selection=selection)
        assert worker["ok"] is True
        assert parent.blocks == tuple(worker["result"]["blocks"])
        assert json.loads(parent.blocks[0])["value"] == expected_value

    @pytest.mark.asyncio
    async def test_empty_key_pointer_selects_member_in_both_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        data = b'{"": {"x": 1}, "a": 2}'
        selection = Selection(kind="limited", ranges=(JsonPointerRange(kind="json_pointer", pointer="/"),))
        parent = await SubprocessAttachmentWorker().extract(
            replace(
                _request(data, name="a.json", kind=AttachmentKind.JSON),
                selection=selection,
                read_expansion_consent=True,
            )
        )
        worker = _worker_entry(
            monkeypatch,
            data,
            kind=AttachmentKind.JSON,
            selection=selection,
            read_expansion_consent=True,
        )
        assert worker["ok"] is True
        assert parent.blocks == tuple(worker["result"]["blocks"])
        assert json.loads(parent.blocks[0])["value"] == {"x": _exact("json_integer", "1")}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("data", "code", "message", "sniff_rejects"), _JSON_REJECTED_CASES)
    async def test_invalid_json_same_verdict_in_both_entries(
        self,
        monkeypatch: pytest.MonkeyPatch,
        data: bytes,
        code: str,
        message: str,
        sniff_rejects: bool,
    ) -> None:
        # 父入口（真实提取）与 worker 入口对同一输入同一错误码。
        with pytest.raises(AttachmentWorkerError) as exc:
            await SubprocessAttachmentWorker().extract(_request(data, name="a.json", kind=AttachmentKind.JSON))
        assert exc.value.code == code
        worker = _worker_entry(monkeypatch, data, kind=AttachmentKind.JSON, selection=Selection(kind="all"))
        assert worker["ok"] is False
        assert worker["error"] == {"code": code, "message": message}
        # 父进程嗅探先行拒绝时沿用同一错误码与消息（协议层归一前）。
        if sniff_rejects:
            with pytest.raises(AttachmentWorkerError) as sniffed:
                detect_attachment_kind("a.json", data)
            assert (sniffed.value.code, sniffed.value.message) == (code, message)
        else:
            # 根起始符合法：父进程不越权判定，结论由 worker 同一判据给出。
            assert detect_attachment_kind("a.json", data) is AttachmentKind.JSON

    @pytest.mark.asyncio
    async def test_json_preview_slice_and_offsets_reach_parent_port(self) -> None:
        """N40：父端口必须带出选中子树的原文切片与码点偏移，供原选区预览核对。"""

        data = b'{ "a" : { "b" : 1.5e+3 }, "c" : [1] }'
        text = data.decode("utf-8")
        selection = Selection(kind="limited", ranges=(JsonPointerRange(kind="json_pointer", pointer="/a"),))
        # 子进程冷启动的模块导入开销占满默认 30 秒预算与环境无关；此处放宽 worker 时限，
        # 只验证原文切片与偏移是否经父端口透传（与既有扩张同意用例同口径）。
        worker = SubprocessAttachmentWorker(
            budget=AttachmentBudget(extract_timeout_s=180.0),
            timeout_s=180.0,
        )
        result = await worker.extract(
            replace(
                _request(data, name="a.json", kind=AttachmentKind.JSON),
                selection=selection,
                read_expansion_consent=True,
            )
        )
        expected = '{ "b" : 1.5e+3 }'
        assert result.body_text == expected
        assert result.preview_ranges == ((text.index(expected), text.index(expected) + len(expected)),)
        assert json.loads(result.blocks[0])["value"]["b"]["lexeme"] == "1.5e+3"

    @pytest.mark.asyncio
    async def test_old_receipt_without_preview_ranges_is_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """旧 worker 回执没有 ``preview_ranges`` 键：协议不得因此判失败（向后兼容）。"""

        data = b'{"a": 1}'
        payload = _worker_entry(monkeypatch, data, kind=AttachmentKind.JSON, selection=Selection(kind="all"))
        assert "preview_ranges" in payload["result"]
        payload["result"].pop("preview_ranges")
        script = f"import sys\nsys.stdout.buffer.write({json.dumps(payload).encode('utf-8')!r})\n"
        worker = SubprocessAttachmentWorker(command=(sys.executable, "-c", script), timeout_s=5.0)
        result = await worker.extract(_request(data, name="a.json", kind=AttachmentKind.JSON))
        assert result.preview_ranges == ()

    @pytest.mark.asyncio
    async def test_preview_ranges_outside_body_text_are_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """偏移必须落在原文预览内：越界回执按协议失败处理，不静默截断。"""

        data = b'{"a": 1}'
        payload = _worker_entry(monkeypatch, data, kind=AttachmentKind.JSON, selection=Selection(kind="all"))
        body_text = payload["result"]["body_text"]
        assert isinstance(body_text, str)
        payload["result"]["preview_ranges"] = [{"start": 0, "end": len(body_text) + 1}]
        script = f"import sys\nsys.stdout.buffer.write({json.dumps(payload).encode('utf-8')!r})\n"
        worker = SubprocessAttachmentWorker(command=(sys.executable, "-c", script), timeout_s=5.0)
        with pytest.raises(AttachmentWorkerError) as exc:
            await worker.extract(_request(data, name="a.json", kind=AttachmentKind.JSON))
        assert exc.value.code == "worker_protocol_invalid"


# ---------------------------------------------------------------------------
# 子进程生命周期（真实退出 / 超时 / 取消）
# ---------------------------------------------------------------------------


def _request(
    data: bytes, name: str = "a.txt", kind: AttachmentKind = AttachmentKind.TXT, selection: Selection | None = None
) -> AttachmentExtractionRequest:
    return AttachmentExtractionRequest(
        user_scope="scope-1",
        attachment_id="att-1",
        file_name=name,
        source_id="src-1",
        source_version="1",
        source_digest=hashlib.sha256(data).hexdigest(),
        kind=kind,
        data=data,
        selection=selection or Selection(kind="all"),
    )


class TestWorkerLifecycle:
    @pytest.mark.asyncio
    async def test_worker_runs_in_subprocess_and_really_exits(self) -> None:
        worker = SubprocessAttachmentWorker()
        result = await worker.extract(_request("hello 世界".encode()))
        assert result.worker_exited is True
        assert result.worker_pid > 0
        assert result.read_state is ReadState.READY
        assert result.body_text == "hello 世界"
        assert worker.active_pids() == ()

    @pytest.mark.asyncio
    async def test_worker_subprocess_environment_strips_business_secrets(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """解析不可信附件的子进程不得继承父进程业务/数据库/AI 环境变量（R9）。"""

        monkeypatch.setenv("DATABASE_URL", "postgresql://synthetic:secret@db.invalid/app")
        monkeypatch.setenv("TUSHARE_TOKEN", "synthetic-tushare-token")
        monkeypatch.setenv("OPENAI_API_KEY", "synthetic-openai-key")
        probe = tmp_path / "probe.json"
        valid = json.dumps(
            _worker_entry(monkeypatch, b"hello", kind=AttachmentKind.TXT, selection=Selection(kind="all"))
        ).encode("utf-8")
        script = (
            "import json,os,sys\n"
            f"probe = {str(probe)!r}\n"
            "open(probe, 'w', encoding='utf-8').write(json.dumps({"
            "'keys': sorted(os.environ), 'cwd': os.getcwd(), "
            "'temp': os.environ.get('TEMP'), 'tmp': os.environ.get('TMP')}))\n"
            "sys.stdin.buffer.read()\n"
            f"sys.stdout.buffer.write({valid!r})\n"
        )
        worker = SubprocessAttachmentWorker(command=(sys.executable, "-c", script), timeout_s=30.0)

        result = await worker.extract(_request(b"hello"))

        observed = json.loads(probe.read_text(encoding="utf-8"))
        assert result.body_text == "hello"
        for key in ("DATABASE_URL", "TUSHARE_TOKEN", "OPENAI_API_KEY"):
            assert key not in observed["keys"]
        # 解释器启动所必需的显式项仍在，否则子进程根本起不来（上方已成功产出结果）。
        assert "PYTHONPATH" in observed["keys"]
        assert all(isinstance(key, str) for key in observed["keys"])

    @pytest.mark.asyncio
    async def test_worker_runs_in_controlled_empty_temp_workspace(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """子进程工作目录/TEMP/TMP 指向本次提取专属目录，且提取结束后被删除。"""

        probe = tmp_path / "probe.json"
        valid = json.dumps(
            _worker_entry(monkeypatch, b"hello", kind=AttachmentKind.TXT, selection=Selection(kind="all"))
        ).encode("utf-8")
        script = (
            "import json,os,sys\n"
            f"probe = {str(probe)!r}\n"
            "open(probe, 'w', encoding='utf-8').write(json.dumps({"
            "'cwd': os.getcwd(), "
            "'temp': os.environ.get('TEMP'), 'tmp': os.environ.get('TMP')}))\n"
            "sys.stdin.buffer.read()\n"
            f"sys.stdout.buffer.write({valid!r})\n"
        )
        worker = SubprocessAttachmentWorker(command=(sys.executable, "-c", script), timeout_s=30.0)

        await worker.extract(_request(b"hello"))

        observed = json.loads(probe.read_text(encoding="utf-8"))
        workspace = Path(observed["cwd"])
        assert workspace.name.startswith("astock-attachment-worker-")
        assert observed["temp"] == observed["tmp"] == str(workspace)
        assert workspace != Path(__file__).resolve().parents[2]
        # 提取结束即清理：受管目录（含解析库可能写下的临时文件）不留残留。
        assert not workspace.exists()

    @pytest.mark.asyncio
    async def test_timeout_terminates_worker(self) -> None:
        worker = SubprocessAttachmentWorker(
            command=(sys.executable, "-c", "import time; time.sleep(30)"),
            timeout_s=0.5,
            supervision_margin_s=0.5,
        )
        with pytest.raises(AttachmentWorkerError) as exc:
            await worker.extract(_request(b"hello"))
        assert exc.value.code == "attachment_time_exceeded"
        assert worker.active_pids() == ()

    @pytest.mark.asyncio
    async def test_worker_exceeding_raw_timeout_but_within_deadline_is_not_killed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # worker 时限自其读完请求头起算；父侧计时含启动/回收开销，必须加余量后比较。
        # 子进程耗时 1.2s 超过 ``timeout_s=0.5`` 但仍在「时限 + 默认余量」内：不得被杀（I36）。
        valid = json.dumps(
            _worker_entry(monkeypatch, b"hello", kind=AttachmentKind.TXT, selection=Selection(kind="all"))
        ).encode("utf-8")
        script = f"import sys,time\ntime.sleep(1.2)\nsys.stdout.buffer.write({valid!r})\n"
        worker = SubprocessAttachmentWorker(command=(sys.executable, "-c", script), timeout_s=0.5)
        result = await worker.extract(_request(b"hello"))
        assert result.worker_exited is True
        assert result.body_text == "hello"
        assert worker.active_pids() == ()

    @pytest.mark.asyncio
    async def test_partial_result_on_timeout_is_not_published(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 已写出结果但未退出：超时路径只报时限，不发布部分结果（既有语义）。
        valid = json.dumps(
            _worker_entry(monkeypatch, b"hello", kind=AttachmentKind.TXT, selection=Selection(kind="all"))
        ).encode("utf-8")
        script = f"import sys,time\nsys.stdout.buffer.write({valid!r})\nsys.stdout.flush()\ntime.sleep(30)\n"
        worker = SubprocessAttachmentWorker(
            command=(sys.executable, "-c", script),
            timeout_s=0.5,
            supervision_margin_s=0.5,
        )
        with pytest.raises(AttachmentWorkerError) as exc:
            await worker.extract(_request(b"hello"))
        assert exc.value.code == "attachment_time_exceeded"
        assert worker.active_pids() == ()

    @pytest.mark.asyncio
    async def test_stdout_overflow_is_bounded_and_terminates_worker(self) -> None:
        # 超长单行（无换行）且随后长睡：父进程必须流式超限即停并终止，而不是等 30s
        # 超时或把输出全部缓存后才发现超限（I74）。
        script = "import sys, time\nsys.stdout.buffer.write(b'x' * 4096)\nsys.stdout.buffer.flush()\ntime.sleep(30)\n"
        worker = SubprocessAttachmentWorker(
            command=(sys.executable, "-c", script),
            timeout_s=30.0,
            max_result_bytes=1024,
        )
        started = time.monotonic()
        with pytest.raises(AttachmentWorkerError) as exc:
            await worker.extract(_request(b"hello"))
        assert exc.value.code == "result_too_large"
        assert time.monotonic() - started < 15.0
        assert worker.active_pids() == ()
        assert worker.residual_threads() == ()

    @pytest.mark.asyncio
    async def test_stderr_flood_is_bounded_and_terminates_worker(self) -> None:
        script = "import sys, time\nsys.stderr.buffer.write(b'e' * 8192)\nsys.stderr.buffer.flush()\ntime.sleep(30)\n"
        worker = SubprocessAttachmentWorker(
            command=(sys.executable, "-c", script),
            timeout_s=30.0,
            max_stderr_bytes=1024,
        )
        started = time.monotonic()
        with pytest.raises(AttachmentWorkerError) as exc:
            await worker.extract(_request(b"hello"))
        assert exc.value.code == "worker_stderr_exceeded"
        assert time.monotonic() - started < 15.0
        assert worker.active_pids() == ()
        assert worker.residual_threads() == ()

    @pytest.mark.asyncio
    async def test_complete_result_then_stderr_flood_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # stderr 超限不因 stdout 已有完整结果而被放过：先终止，再按协议失败。
        valid = json.dumps(
            _worker_entry(monkeypatch, b"hello", kind=AttachmentKind.TXT, selection=Selection(kind="all"))
        ).encode("utf-8")
        script = (
            f"import sys, time\nsys.stdout.buffer.write({valid!r})\nsys.stdout.buffer.flush()\n"
            "sys.stderr.buffer.write(b'e' * 8192)\nsys.stderr.buffer.flush()\ntime.sleep(30)\n"
        )
        worker = SubprocessAttachmentWorker(
            command=(sys.executable, "-c", script),
            timeout_s=30.0,
            max_stderr_bytes=1024,
        )
        with pytest.raises(AttachmentWorkerError) as exc:
            await worker.extract(_request(b"hello"))
        assert exc.value.code == "worker_stderr_exceeded"
        assert worker.active_pids() == ()

    @pytest.mark.asyncio
    async def test_large_valid_result_streams_through_bounded_reader(self) -> None:
        data = b"a" * 90_000
        worker = SubprocessAttachmentWorker()
        result = await worker.extract(_request(data))
        assert result.body_text == "a" * 90_000
        assert worker.residual_threads() == ()

    @pytest.mark.asyncio
    async def test_cancel_confirms_process_and_threads_really_exit(self) -> None:
        worker = SubprocessAttachmentWorker(
            command=(sys.executable, "-c", "import time; time.sleep(30)"),
            timeout_s=30.0,
        )
        task = asyncio.create_task(worker.extract(_request(b"hello")))
        await asyncio.sleep(0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await task
        assert isinstance(exc_info.value, asyncio.CancelledError)
        assert task.cancelled() is True
        # 取消只取消等待；worker 线程必须随后真正终止进程并 join 管道线程。
        for _ in range(50):
            if worker.active_pids() == ():
                break
            await asyncio.sleep(0.1)
        assert worker.active_pids() == ()
        assert worker.residual_threads() == ()

    @pytest.mark.asyncio
    async def test_residual_pipe_thread_is_recorded_and_fails_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            SubprocessAttachmentWorker,
            "_join_threads",
            lambda self, _threads: ("attachment-worker-stdout",),
        )
        worker = SubprocessAttachmentWorker(command=(sys.executable, "-c", "pass"), timeout_s=5.0)
        with pytest.raises(AttachmentWorkerError) as exc:
            await worker.extract(_request(b"hello"))
        # 进程已退出但管道线程未收尾：作为残留事实失败，不得静默当成完整结果。
        assert exc.value.code == "worker_thread_residual"
        assert worker.residual_threads() == ("attachment-worker-stdout",)

    @pytest.mark.asyncio
    async def test_nonzero_exit_with_complete_result_keeps_current_semantics(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 「非 0 退出 + 完整结果」无冻结合同：保持既有语义（以结果协议为准，不新造错误码）。
        valid = json.dumps(
            _worker_entry(monkeypatch, b"hello", kind=AttachmentKind.TXT, selection=Selection(kind="all"))
        ).encode("utf-8")
        script = f"import sys\nsys.stdout.buffer.write({valid!r})\nraise SystemExit(3)\n"
        worker = SubprocessAttachmentWorker(command=(sys.executable, "-c", script), timeout_s=5.0)
        result = await worker.extract(_request(b"hello"))
        assert result.worker_exited is True
        assert result.body_text == "hello"

    @pytest.mark.asyncio
    async def test_nonzero_exit_without_result_fails(self) -> None:
        worker = SubprocessAttachmentWorker(
            command=(sys.executable, "-c", "raise SystemExit(3)"),
            timeout_s=5.0,
        )
        with pytest.raises(AttachmentWorkerError) as exc:
            await worker.extract(_request(b"hello"))
        assert exc.value.code == "worker_failed"
        assert worker.active_pids() == ()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("version", [1.0, "1", True])
    async def test_worker_result_schema_version_must_be_strict_int(self, version: object) -> None:
        """I69：worker 响应版本为严格整数，浮点/字符串/布尔不得被当作合法协议版本。"""

        response = json.dumps({"ok": True, "schema_version": version, "result": {}}).encode("utf-8")
        script = f"import sys\nsys.stdout.buffer.write({response!r})\n"
        worker = SubprocessAttachmentWorker(command=(sys.executable, "-c", script), timeout_s=5.0)
        with pytest.raises(AttachmentWorkerError) as exc:
            await worker.extract(_request(b"hello"))
        assert exc.value.code == "worker_protocol_invalid"

    @pytest.mark.asyncio
    async def test_header_carries_injected_worker_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """I36：下发 worker 的 timeout_s 必须等于父侧监督的有效时限。

        此前下发的是 budget 值而父侧按可注入 ``timeout_s`` 监督，两者不等时父进程会
        先杀仍在自身时限内的 worker。
        """

        valid = json.dumps(
            _worker_entry(monkeypatch, b"hello", kind=AttachmentKind.TXT, selection=Selection(kind="all"))
        ).encode("utf-8")
        script = (
            "import json,sys\n"
            "header = json.loads(sys.stdin.buffer.readline())\n"
            "matched = abs(float(header['timeout_s']) - 0.7) < 1e-9\n"
            f"if matched:\n    sys.stdout.buffer.write({valid!r})\n"
            "else:\n    sys.exit(3)\n"
        )
        worker = SubprocessAttachmentWorker(command=(sys.executable, "-c", script), timeout_s=0.7)

        result = await worker.extract(_request(b"hello"))

        assert result.body_text == "hello"
        assert worker.active_pids() == ()

    @pytest.mark.asyncio
    async def test_parent_header_exact_limit_is_accepted_and_one_over_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """I36：父侧限额只计请求头自身（不含 framing 换行）；恰上限通过，超 1 字节即拒。"""

        valid = json.dumps(
            _worker_entry(monkeypatch, b"hello", kind=AttachmentKind.TXT, selection=Selection(kind="all"))
        ).encode("utf-8")
        script = f"import sys\nsys.stdout.buffer.write({valid!r})\n"
        worker = SubprocessAttachmentWorker(command=(sys.executable, "-c", script), timeout_s=5.0)
        request = _request(b"hello")
        header_size = len(worker._build_worker_header(request, 5.0))

        monkeypatch.setattr(attachments, "MAX_HEADER_BYTES", header_size)
        result = await worker.extract(request)
        assert result.body_text == "hello"

        monkeypatch.setattr(attachments, "MAX_HEADER_BYTES", header_size - 1)
        with pytest.raises(AttachmentWorkerError) as exc:
            await worker.extract(request)
        assert exc.value.code == "attachment_request_too_large"

    @pytest.mark.asyncio
    async def test_read_range_outside_selection_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """I36 负向：worker 伪造越界 read_range，父进程按 §3.3.1 拒绝。"""

        selection = Selection(kind="limited", ranges=(CodepointsRange(kind="codepoints", start=1, end=2),))
        data = b"abcdef"
        response = _worker_entry(monkeypatch, data, kind=AttachmentKind.TXT, selection=selection)
        response["result"]["read_ranges"] = [to_wire_dict(CodepointsRange(kind="codepoints", start=1, end=3))]
        script = f"import sys\nsys.stdout.buffer.write({json.dumps(response).encode('utf-8')!r})\n"
        worker = SubprocessAttachmentWorker(command=(sys.executable, "-c", script), timeout_s=5.0)

        with pytest.raises(AttachmentWorkerError) as exc:
            await worker.extract(_request(data, selection=selection))

        assert exc.value.code == "worker_protocol_invalid"
        assert "not_in_selection" in exc.value.message
        assert worker.active_pids() == ()

    @pytest.mark.asyncio
    async def test_overlapping_read_and_unread_ranges_are_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """I36 负向：read 与 unread 重叠（同一行既已读又未读）即协议失败。"""

        selection = Selection(kind="limited", ranges=(CodepointsRange(kind="codepoints", start=1, end=4),))
        data = b"abcdef"
        response = _worker_entry(monkeypatch, data, kind=AttachmentKind.TXT, selection=selection)
        response["result"]["read_ranges"] = [to_wire_dict(CodepointsRange(kind="codepoints", start=2, end=3))]
        response["result"]["unread_ranges"] = [to_wire_dict(CodepointsRange(kind="codepoints", start=3, end=4))]
        script = f"import sys\nsys.stdout.buffer.write({json.dumps(response).encode('utf-8')!r})\n"
        worker = SubprocessAttachmentWorker(command=(sys.executable, "-c", script), timeout_s=5.0)

        with pytest.raises(AttachmentWorkerError) as exc:
            await worker.extract(_request(data, selection=selection))

        assert exc.value.code == "worker_protocol_invalid"
        assert "overlap" in exc.value.message
        assert worker.active_pids() == ()

    @pytest.mark.asyncio
    async def test_cancel_terminates_worker_and_reraises(self) -> None:
        worker = SubprocessAttachmentWorker(
            command=(sys.executable, "-c", "import time; time.sleep(30)"),
            timeout_s=30.0,
        )
        task = asyncio.create_task(worker.extract(_request(b"hello")))
        await asyncio.sleep(0.5)
        task.cancel()
        cancelled = False
        try:
            await task
        except asyncio.CancelledError:
            cancelled = True
        assert cancelled is True
        assert task.cancelled() is True
        for _ in range(50):
            if worker.active_pids() == ():
                break
            await asyncio.sleep(0.1)
        assert worker.active_pids() == ()

    @pytest.mark.asyncio
    async def test_cancel_only_terminates_its_own_worker(self) -> None:
        worker = SubprocessAttachmentWorker(
            command=(sys.executable, "-c", "import time; time.sleep(30)"),
            timeout_s=30.0,
        )
        first = asyncio.create_task(worker.extract(_request(b"one")))
        second = asyncio.create_task(worker.extract(_request(b"two")))
        for _ in range(50):
            if len(worker.active_pids()) == 2:
                break
            await asyncio.sleep(0.1)
        assert len(worker.active_pids()) == 2
        first.cancel()
        try:
            await first
        except asyncio.CancelledError:
            pass
        # 取消 A 不得误杀同实例的 B：A 退出后仍应恰有一个在途子进程。
        for _ in range(50):
            if len(worker.active_pids()) == 1:
                break
            await asyncio.sleep(0.1)
        assert len(worker.active_pids()) == 1
        second.cancel()
        try:
            await second
        except asyncio.CancelledError:
            pass
        for _ in range(50):
            if worker.active_pids() == ():
                break
            await asyncio.sleep(0.1)
        assert worker.active_pids() == ()

    @pytest.mark.asyncio
    async def test_csv_header_confirmation_is_carried_to_worker(self) -> None:
        data = b"h\n" + b"x\n" * 50_000  # 未确认时 50001 条都算数据 → 超限；确认表头后恰等通过
        request = _request(data, "a.csv", AttachmentKind.CSV)
        with pytest.raises(AttachmentWorkerError) as exc:
            await SubprocessAttachmentWorker().extract(request)
        assert exc.value.code == "attachment_rows_exceeded"
        worker = SubprocessAttachmentWorker(budget=AttachmentBudget(max_codepoints=10_000_000))
        result = await worker.extract(replace(request, csv_header_confirmed=True))
        assert result.read_state is ReadState.READY
        assert result.stats.rows == 50_001

    @pytest.mark.asyncio
    async def test_read_expansion_consent_is_carried_to_worker(self) -> None:
        # 子进程冷启动的模块导入开销占满默认 30 秒预算与环境无关；此处放宽 worker 时限，
        # 只验证同意字段是否真正到达 worker 解析入口。
        worker = SubprocessAttachmentWorker(
            budget=AttachmentBudget(extract_timeout_s=180.0),
            timeout_s=180.0,
        )
        data = b"h\n1\n2\n"
        selection = Selection(kind="limited", ranges=(RowsRange(kind="rows", sheet=None, start=2, end=2),))
        with pytest.raises(AttachmentWorkerError) as exc:
            await worker.extract(replace(_request(data, "a.csv", AttachmentKind.CSV), selection=selection))
        assert exc.value.code == "read_expansion_not_permitted"
        result = await worker.extract(
            replace(
                _request(data, "a.csv", AttachmentKind.CSV),
                selection=selection,
                read_expansion_consent=True,
            )
        )
        assert result.body_text == "1"

    def test_parent_rejects_non_bool_read_expansion_consent(self) -> None:
        worker = SubprocessAttachmentWorker()
        with pytest.raises(AttachmentWorkerError) as exc:
            # noqa: SLF001  # 直接验证请求前置校验路径
            worker._validate_request(replace(_request(b"x"), read_expansion_consent=cast(Any, "yes")))
        assert exc.value.code == "attachment_request_invalid"

    @pytest.mark.asyncio
    async def test_worker_rejects_digest_mismatch(self) -> None:
        request = _request(b"hello")
        mismatched = AttachmentExtractionRequest(
            user_scope=request.user_scope,
            attachment_id=request.attachment_id,
            file_name=request.file_name,
            source_id=request.source_id,
            source_version=request.source_version,
            source_digest="0" * 64,
            kind=request.kind,
            data=request.data,
            selection=request.selection,
        )
        with pytest.raises(AttachmentWorkerError) as exc:
            await SubprocessAttachmentWorker().extract(mismatched)
        assert exc.value.code == "attachment_digest_mismatch"

    def test_parent_rejects_oversize_before_spawn(self) -> None:
        worker = SubprocessAttachmentWorker()
        with pytest.raises(AttachmentWorkerError) as exc:
            worker._validate_request(_request(b"x" * 20_000_001))  # noqa: SLF001  # 直接验证前置限额路径
        assert exc.value.code == "attachment_bytes_exceeded"

    def test_pdf_malformed_structure_is_explicit(self) -> None:
        with pytest.raises(AttachmentParseError) as exc:
            _parse(AttachmentKind.PDF, b"%PDF-" + b"1.4\n")
        assert exc.value.code == "invalid_structure"

    def test_pdf_all_lists_unreadable_pages(self) -> None:
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        writer.add_blank_page(width=200, height=200)
        buffer = io.BytesIO()
        writer.write(buffer)
        parsed = _parse(AttachmentKind.PDF, buffer.getvalue())
        # I25/I73：all 不得把无文本页伪装成已读，但 all 的 read/unread 只允许为空或
        # 单个 all 分型（§3.3.1）；缺口经 limitations + PARTIAL/limited 表达。
        assert parsed.read_state is ReadState.PARTIAL
        assert parsed.coverage == "limited"
        assert parsed.read_ranges == []
        assert parsed.unread_ranges == []
        assert [item["code"] for item in parsed.limitations] == [
            "page_text_unavailable",
            "page_text_unavailable",
        ]
        _assert_selected_range_contract(Selection(kind="all"), parsed)

    def test_image_malformed_structure_is_explicit(self) -> None:
        with pytest.raises(AttachmentParseError) as exc:
            _parse(AttachmentKind.PNG, b"\x89PNG\r\n\x1a\n")
        assert exc.value.code == "invalid_structure"


# ---------------------------------------------------------------------------
# T-12：附件与交付兼容（类型证据 / 跨表秩序 / 不可定位表 / PDF 超时 / 冻结入口）
# ---------------------------------------------------------------------------

_SST_ERROR_LOOKALIKE = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    f'<sst {_NS} count="1" uniqueCount="1"><si><t>#N/A</t></si></sst>'
)


def _shared_string_sheet(value_index: int = 0) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<worksheet {_NS}><sheetData><row r="1"><c r="A1" t="s"><v>{value_index}</v></c>'
        "</row></sheetData></worksheet>"
    ).encode()


class TestAttachmentCompatibility:
    """T-12：显式文本与错误值、跨表秩序、不可定位表、PDF 超时、冻结 EXE 入口。"""

    def test_text_error_lookalike_stays_string(self) -> None:
        # 显式文本 "#N/A"（t="s"）是字符串；错误值只认 XML t="e" / openpyxl data_type="e"。
        data = _rewrite_xlsx(
            _xlsx_bytes(shared_strings=_SST_ERROR_LOOKALIKE),
            replace={"xl/worksheets/sheet1.xml": _shared_string_sheet()},
        )
        cell = _xlsx_cells(_parse(AttachmentKind.XLSX, data))["A1"]
        assert cell["value"]["raw_type"] == "string"
        assert cell["value"]["lexeme"] == "#N/A"
        assert cell["value"]["xml_type"] == "s"

    def test_interleaved_cross_sheet_rows_are_rejected(self) -> None:
        # S1 1—2、S2 1—1、S1 3—3：块顺序与 read_ranges 原子顺序无法同时如实表达，明确拒绝。
        selection = Selection(
            kind="limited",
            ranges=(
                RowsRange(kind="rows", sheet="S1", start=1, end=2),
                RowsRange(kind="rows", sheet="S2", start=1, end=1),
                RowsRange(kind="rows", sheet="S1", start=3, end=3),
            ),
        )
        with pytest.raises(AttachmentParseError) as exc:
            _parse(AttachmentKind.XLSX, _xlsx_bytes(sheets=2), selection)
        assert exc.value.code == "invalid_selection"

    def test_contiguous_cross_sheet_rows_keep_block_and_atom_order(self) -> None:
        # 每表的选择段必须在选择中连续：块顺序与原子顺序一致，正文与范围不冲突。
        selection = Selection(
            kind="limited",
            ranges=(
                RowsRange(kind="rows", sheet="S1", start=1, end=2),
                RowsRange(kind="rows", sheet="S2", start=1, end=1),
            ),
        )
        parsed = _parse(AttachmentKind.XLSX, _xlsx_bytes(sheets=2), selection)
        assert [json.loads(block)["sheet"] for block in parsed.blocks] == ["S1", "S2"]
        assert [atom["sheet"] for atom in parsed.read_ranges] == ["S1", "S2"]
        _assert_selected_range_contract(selection, parsed)

    def test_missing_worksheet_part_is_rejected_for_all_selection(self) -> None:
        # workbook 声明 S2 但容器缺其部件：不能读也不能证明为空，全选不得报完整读取。
        data = _rewrite_xlsx(_xlsx_bytes(sheets=2), drop=frozenset({"xl/worksheets/sheet2.xml"}))
        with pytest.raises(AttachmentParseError) as exc:
            _parse(AttachmentKind.XLSX, data)
        assert exc.value.code == "invalid_structure"

    def test_missing_worksheet_part_keeps_selected_sheet_readable(self) -> None:
        data = _rewrite_xlsx(_xlsx_bytes(sheets=2), drop=frozenset({"xl/worksheets/sheet2.xml"}))
        selection = Selection(kind="limited", ranges=(SheetsRange(kind="sheets", names=("S1",)),))
        parsed = _parse(AttachmentKind.XLSX, data, selection)
        assert parsed.read_state is ReadState.READY
        assert parsed.stats["sheets"] == 1
        assert parsed.stats["rows"] == 3
        _assert_selected_range_contract(selection, parsed)

    def test_rows_absent_inside_range_are_verified_blank(self) -> None:
        # 行 2 在 worksheet XML 中不存在（不是被跳过）：范围已读、块只列真实存在的行。
        sparse = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<worksheet {_NS}><sheetData><row r="1"><c r="A1"><v>1</v></c></row>'
            '<row r="3"><c r="A3"><v>3</v></c></row></sheetData></worksheet>'
        ).encode()
        data = _rewrite_xlsx(_xlsx_bytes(), replace={"xl/worksheets/sheet1.xml": sparse})
        selection = Selection(kind="limited", ranges=(RowsRange(kind="rows", sheet="S1", start=1, end=3),))
        parsed = _parse(AttachmentKind.XLSX, data, selection)
        assert parsed.read_ranges == [to_wire_dict(selection.ranges[0])]
        assert parsed.unread_ranges == []
        assert _rows_of_blocks(parsed) == [1, 3]
        _assert_selected_range_contract(selection, parsed)

    def test_pdf_timeout_keeps_completed_pages_readable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 超时保住本次范围：已完成页记已读、未开始页记未读，不整份丢弃也不冒充全读。
        monkeypatch.setattr(PageObject, "extract_text", lambda self, *args, **kwargs: "hello")
        ticks = iter([0.0, 0.0, 100.0])
        monkeypatch.setattr(attachment_worker.time, "monotonic", lambda: next(ticks, 100.0))
        selection = Selection(kind="limited", ranges=(PagesRange(kind="pages", start=1, end=3),))
        parsed = parse_attachment(
            kind=AttachmentKind.PDF,
            data=_pdf_bytes(3),
            selection=selection,
            budget=AttachmentBudget(),
            deadline=10.0,
            timeout_s=30.0,
        )
        assert parsed.read_state is ReadState.PARTIAL
        assert parsed.read_ranges == [{"kind": "pages", "start": 1, "end": 2}]
        assert parsed.unread_ranges == [{"kind": "pages", "start": 3, "end": 3}]
        assert [item["code"] for item in parsed.limitations] == ["extract_timeout"]
        _assert_selected_range_contract(selection, parsed)

    def test_pdf_expired_deadline_marks_all_pages_unread(self) -> None:
        selection = Selection(kind="limited", ranges=(PagesRange(kind="pages", start=1, end=3),))
        parsed = parse_attachment(
            kind=AttachmentKind.PDF,
            data=_pdf_bytes(3),
            selection=selection,
            budget=AttachmentBudget(),
            deadline=time.monotonic() - 1.0,
            timeout_s=30.0,
        )
        assert parsed.read_state is ReadState.PARTIAL
        assert parsed.coverage == "limited"
        assert parsed.read_ranges == []
        assert parsed.unread_ranges == [to_wire_dict(selection.ranges[0])]
        assert [item["code"] for item in parsed.limitations] == ["extract_timeout"]
        _assert_selected_range_contract(selection, parsed)

    def test_worker_command_uses_module_entry_in_source_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delattr(sys, "frozen", raising=False)
        assert worker_command() == (sys.executable, "-m", "services.agent_service.attachment_worker")

    def test_worker_command_uses_single_executable_in_frozen_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 冻结 EXE 里 sys.executable 是应用本身：-m 不可用，改用同一入口的 worker 子命令。
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        assert worker_command() == (sys.executable, attachment_worker.ATTACHMENT_WORKER_FLAG)

    def test_frozen_entry_dispatches_attachment_worker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import main as app_entry

        calls: list[str] = []

        def _fake_worker_main() -> int:
            calls.append("worker")
            return 0

        # main.py 为保持首帧最小 import 用字面量判定，此处守护与 worker 常量一致。
        monkeypatch.setattr(attachment_worker, "main", _fake_worker_main)
        monkeypatch.setattr(sys, "argv", ["AStockScreener.exe", attachment_worker.ATTACHMENT_WORKER_FLAG])
        with pytest.raises(SystemExit) as exc:
            app_entry.main()
        assert exc.value.code == 0
        assert calls == ["worker"]
        assert attachment_worker.ATTACHMENT_WORKER_FLAG == "--attachment-worker"
