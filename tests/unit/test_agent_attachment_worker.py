"""E-01-2 附件解析 worker 与 D06 限额单元测试（DD §10.4 / REQ D06）。

覆盖：TXT/MD 码点与段落范围、CSV 多行记录与行限额、JSON 精确数值、
XLSX 同源双表示与缓存适用性（有/无/空/错误/字符串）、sheet/rows 限额、
内容类型嗅探、子进程真实退出/超时/取消。
"""

import asyncio
import hashlib
import io
import json
import sys
import tempfile
import time
import zipfile
from typing import Any

import pytest

import services.agent_service.attachment_worker as attachment_worker

from core.agent_types import (
    AllAtom,
    AttachmentBudget,
    AttachmentKind,
    CodepointsRange,
    JsonPointerRange,
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
)

_NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
_REL_NS = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'
_OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _deadline() -> float:
    return time.monotonic() + 30.0


def _parse(
    kind: AttachmentKind, data: bytes, selection: Selection | None = None, budget: AttachmentBudget | None = None
):
    return parse_attachment(
        kind=kind,
        data=data,
        selection=selection or Selection(kind="all"),
        budget=budget or AttachmentBudget(),
        deadline=_deadline(),
    )


def _parse_expired(
    kind: AttachmentKind, data: bytes, selection: Selection | None = None, budget: AttachmentBudget | None = None
):
    """以已过期的 deadline 解析（模拟 30 秒预算耗尽时的部分读取）。"""

    return parse_attachment(
        kind=kind,
        data=data,
        selection=selection or Selection(kind="all"),
        budget=budget or AttachmentBudget(),
        deadline=time.monotonic() - 1.0,
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


def _limit_of(data: bytes, selection: Selection, budget: AttachmentBudget, kind: AttachmentKind) -> str:
    with pytest.raises(AttachmentParseError) as exc:
        _parse(kind, data, selection, budget)
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

    def test_rows_selection_includes_header_numbering(self) -> None:
        parsed = _parse(
            AttachmentKind.CSV,
            b"h1,h2\na,1\nb,2\n",
            Selection(kind="limited", ranges=(RowsRange(kind="rows", sheet=None, start=2, end=3),)),
        )
        assert json.loads(parsed.blocks[0])["rows"] == [["a", "1"], ["b", "2"]]

    def test_data_row_limit_exact_passes_and_plus_one_rejected(self) -> None:
        data = b"h\n1\n2\n3\n"  # 表头 + 3 条数据记录
        budget = AttachmentBudget(max_rows=3)
        assert _parse(AttachmentKind.CSV, data, budget=budget).stats["rows"] == 4
        assert _limit_of(data, Selection(kind="all"), AttachmentBudget(max_rows=2), AttachmentKind.CSV) == (
            "attachment_rows_exceeded"
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
        assert _limit_of(data, selection, AttachmentBudget(max_rows=3), AttachmentKind.CSV) == (
            "attachment_rows_exceeded"
        )
        exact = _parse(AttachmentKind.CSV, data, selection, AttachmentBudget(max_rows=4))
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
        )
        assert code == "invalid_selection"

    def test_invalid_json_rejected(self) -> None:
        with pytest.raises(AttachmentParseError) as exc:
            _parse(AttachmentKind.JSON, b"{not json")
        assert exc.value.code == "invalid_structure"


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


def _xlsx_bytes(*, sheets: int = 1, date1904: bool = True, shared_strings: str | None = None) -> bytes:
    sheet_entries = "".join(
        f'<sheet name="S{index}" sheetId="{index}" r:id="rId{index}"/>' for index in range(1, sheets + 1)
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
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", _ROOT_RELS)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", _SHEET1)
        if sheets > 1:
            archive.writestr("xl/worksheets/sheet2.xml", _SHEET2)
        if shared_strings is not None:
            archive.writestr("xl/sharedStrings.xml", shared_strings)
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


def _xlsx_sheet1(rows_xml: str, *, dimension: str | None = None) -> bytes:
    """以自定义 sheet1.xml 构造最小样本（可声明与实际内容不符的 dimension）。"""

    dimension_tag = f'<dimension ref="{dimension}"/>' if dimension is not None else ""
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f"<worksheet {_NS}>{dimension_tag}<sheetData>{rows_xml}</sheetData></worksheet>"
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
# 内容类型嗅探
# ---------------------------------------------------------------------------


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
# 子进程生命周期（真实退出 / 超时 / 取消）
# ---------------------------------------------------------------------------


def _request(
    data: bytes, name: str = "a.txt", kind: AttachmentKind = AttachmentKind.TXT
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
        selection=Selection(kind="all"),
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
    async def test_timeout_terminates_worker(self) -> None:
        worker = SubprocessAttachmentWorker(
            command=(sys.executable, "-c", "import time; time.sleep(30)"),
            timeout_s=0.5,
        )
        with pytest.raises(AttachmentWorkerError) as exc:
            await worker.extract(_request(b"hello"))
        assert exc.value.code == "attachment_time_exceeded"
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

    def test_image_malformed_structure_is_explicit(self) -> None:
        with pytest.raises(AttachmentParseError) as exc:
            _parse(AttachmentKind.PNG, b"\x89PNG\r\n\x1a\n")
        assert exc.value.code == "invalid_structure"
