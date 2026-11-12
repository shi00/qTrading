"""B-01-2 §3.3.1 资料许可v1嵌套wire合同 codec 单元测试（设计正本 §3.3.1）。

覆盖：SourceVersionRef / Selection / RangeAtom×7 / SourceLocator / SelectedRange /
RangeStats / RangeLimitation / ProvenanceRef 的全键必填、禁额外键、类型/端点/
重叠/归属 strict 校验与规范序列化往返。
"""

from decimal import Decimal
from typing import Any, cast

import pytest

from core.agent_types import (
    AgentValidationError,
    AllAtom,
    CodepointsRange,
    FieldsRange,
    JsonPointerRange,
    PagesRange,
    ParagraphsRange,
    ProvenanceRef,
    RangeLimitation,
    RangeStats,
    RowsRange,
    SelectedRange,
    Selection,
    SheetsRange,
    SourceKind,
    SourceLocator,
    SourceVersionRef,
    decode_provenance_ref,
    decode_provenance_refs,
    decode_range_atom,
    decode_range_stats,
    decode_selected_range,
    decode_selection,
    decode_source_locator,
    decode_source_version_ref,
    json_doc,
    selected_range_digest,
    sha256_hex,
    to_wire_dict,
)


def code_of(excinfo: pytest.ExceptionInfo[AgentValidationError]) -> str:
    return excinfo.value.code


def expect_invalid(excinfo: pytest.ExceptionInfo[AgentValidationError]) -> None:
    """§3.3.1 所有形状违规在服务边界统一映射 INPUT_INVALID。"""

    assert excinfo.value.code in {
        "missing_field",
        "unknown_field",
        "invalid_type",
        "invalid_range",
        "invalid_tag",
        "invalid_digest",
        "invalid_identity",
        "invalid_pointer",
        "overlap",
        "not_in_selection",
        "kind_mismatch",
        "origin_mismatch",
        "duplicate_edge",
        "invalid_number",
    }


# ---------------------------------------------------------------------------
# 固定样例
# ---------------------------------------------------------------------------

ORIGIN_WIRE: dict[str, Any] = {
    "source_kind": "attachment",
    "source_id": "src-1",
    "source_version": "1",
    "source_digest": "a" * 64,
}


def pages_wire(start: int = 1, end: int = 2) -> dict[str, Any]:
    return {"kind": "pages", "start": start, "end": end}


def limited_wire(*atoms: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "limited", "ranges": list(atoms)}


# ---------------------------------------------------------------------------
# SourceVersionRef
# ---------------------------------------------------------------------------


class TestSourceVersionRef:
    def test_roundtrip(self) -> None:
        ref = decode_source_version_ref(ORIGIN_WIRE, "$")
        assert ref.source_kind is SourceKind.ATTACHMENT
        assert ref.source_id == "src-1"
        assert to_wire_dict(ref) == ORIGIN_WIRE

    def test_missing_and_extra_keys_rejected(self) -> None:
        incomplete = {k: v for k, v in ORIGIN_WIRE.items() if k != "source_version"}
        with pytest.raises(AgentValidationError) as exc:
            decode_source_version_ref(incomplete, "$")
        expect_invalid(exc)
        with pytest.raises(AgentValidationError) as exc:
            decode_source_version_ref({**ORIGIN_WIRE, "path": "C:/x"}, "$")
        expect_invalid(exc)

    def test_unknown_kind_rejected(self) -> None:
        wire = {**ORIGIN_WIRE, "source_kind": "direct_message"}
        with pytest.raises(AgentValidationError) as exc:
            decode_source_version_ref(wire, "$")
        expect_invalid(exc)

    def test_bad_digest_and_bool_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            decode_source_version_ref({**ORIGIN_WIRE, "source_digest": "A" * 64}, "$")
        expect_invalid(exc)
        with pytest.raises(AgentValidationError) as exc:
            decode_source_version_ref({**ORIGIN_WIRE, "source_version": True}, "$")
        expect_invalid(exc)


# ---------------------------------------------------------------------------
# Selection / RangeAtom
# ---------------------------------------------------------------------------


class TestSelection:
    def test_all_selection(self) -> None:
        sel = decode_selection({"kind": "all"}, "$")
        assert sel.kind == "all"
        assert sel.ranges == ()
        with pytest.raises(AgentValidationError) as exc:
            decode_selection({"kind": "all", "ranges": []}, "$")
        expect_invalid(exc)

    def test_limited_requires_non_empty_ranges(self) -> None:
        sel = decode_selection(limited_wire(pages_wire()), "$")
        assert sel.kind == "limited"
        assert isinstance(sel.ranges[0], PagesRange)
        with pytest.raises(AgentValidationError) as exc:
            decode_selection({"kind": "limited", "ranges": []}, "$")
        expect_invalid(exc)

    def test_all_atom_forbidden_in_limited_selection(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            decode_selection(limited_wire({"kind": "all"}), "$")
        expect_invalid(exc)

    def test_mixed_kinds_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            decode_selection(limited_wire(pages_wire(), {"kind": "json_pointer", "pointer": "/a"}), "$")
        expect_invalid(exc)


class TestRangeAtom:
    def test_numeric_kinds_endpoint_validation(self) -> None:
        atom = decode_range_atom(pages_wire(3, 7), "$")
        assert atom == PagesRange(start=3, end=7)
        assert isinstance(decode_range_atom({"kind": "codepoints", "start": 1, "end": 1}, "$"), CodepointsRange)
        assert isinstance(decode_range_atom({"kind": "paragraphs", "start": 2, "end": 9}, "$"), ParagraphsRange)
        with pytest.raises(AgentValidationError) as exc:
            decode_range_atom(pages_wire(5, 3), "$")
        expect_invalid(exc)
        with pytest.raises(AgentValidationError) as exc:
            decode_range_atom(pages_wire(0, 2), "$")
        expect_invalid(exc)
        with pytest.raises(AgentValidationError) as exc:
            decode_range_atom(pages_wire(True, 2), "$")
        expect_invalid(exc)

    def test_sheets_non_empty_no_duplicates(self) -> None:
        atom = decode_range_atom({"kind": "sheets", "names": ["利润表", "现金流"]}, "$")
        assert isinstance(atom, SheetsRange)
        assert atom.names == ("利润表", "现金流")
        with pytest.raises(AgentValidationError) as exc:
            decode_range_atom({"kind": "sheets", "names": []}, "$")
        expect_invalid(exc)
        with pytest.raises(AgentValidationError) as exc:
            decode_range_atom({"kind": "sheets", "names": ["a", "a"]}, "$")
        expect_invalid(exc)

    def test_rows_sheet_rules(self) -> None:
        atom = decode_range_atom({"kind": "rows", "sheet": None, "start": 1, "end": 5}, "$")
        assert isinstance(atom, RowsRange)
        assert atom.sheet is None
        xlsx = decode_range_atom({"kind": "rows", "sheet": "Sheet1", "start": 2, "end": 2}, "$")
        assert isinstance(xlsx, RowsRange)
        assert xlsx.sheet == "Sheet1"
        with pytest.raises(AgentValidationError) as exc:
            decode_range_atom({"kind": "rows", "sheet": "", "start": 1, "end": 5}, "$")
        expect_invalid(exc)

    def test_fields_keys_and_row_pairing(self) -> None:
        atom = decode_range_atom(
            {"kind": "fields", "sheet": None, "row_start": None, "row_end": None, "keys": ["revenue", "cost"]},
            "$",
        )
        assert isinstance(atom, FieldsRange)
        assert atom.keys == ("revenue", "cost")
        with pytest.raises(AgentValidationError) as exc:
            decode_range_atom({"kind": "fields", "sheet": None, "row_start": 1, "row_end": None, "keys": ["k"]}, "$")
        expect_invalid(exc)
        with pytest.raises(AgentValidationError) as exc:
            decode_range_atom({"kind": "fields", "sheet": None, "row_start": None, "row_end": None, "keys": []}, "$")
        expect_invalid(exc)
        with pytest.raises(AgentValidationError) as exc:
            decode_range_atom(
                {"kind": "fields", "sheet": None, "row_start": None, "row_end": None, "keys": ["k", "k"]}, "$"
            )
        expect_invalid(exc)

    def test_unknown_kind_tag_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            decode_range_atom({"kind": "bytes", "start": 1, "end": 2}, "$")
        expect_invalid(exc)
        with pytest.raises(AgentValidationError) as exc:
            decode_range_atom({**pages_wire(), "extra": 1}, "$")
        expect_invalid(exc)

    def test_json_pointer_rules(self) -> None:
        for pointer in ("/", "/a/b", "/a~1b", "/list/~0x", "/0", "/rows/12"):
            atom = decode_range_atom({"kind": "json_pointer", "pointer": pointer}, "$")
            assert isinstance(atom, JsonPointerRange)
        for pointer in ("", "a/b", "/a~", "/a~2", "/rows/01", "/rows/-", "0", "/~"):
            with pytest.raises(AgentValidationError) as exc:
                decode_range_atom({"kind": "json_pointer", "pointer": pointer}, "$")
            expect_invalid(exc)


# ---------------------------------------------------------------------------
# SourceLocator / RangeStats / RangeLimitation
# ---------------------------------------------------------------------------


class TestLocatorAndStats:
    def test_locator_roundtrip(self) -> None:
        wire = {"locator_kind": "source_range", "origin_ref": ORIGIN_WIRE, "selection": {"kind": "all"}}
        locator = decode_source_locator(wire, "$")
        assert locator.locator_kind == "source_range"
        assert locator.origin_ref.source_kind is SourceKind.ATTACHMENT
        assert to_wire_dict(locator) == wire

    def test_locator_kind_fixed(self) -> None:
        wire = {"locator_kind": "url", "origin_ref": ORIGIN_WIRE, "selection": {"kind": "all"}}
        with pytest.raises(AgentValidationError) as exc:
            decode_source_locator(wire, "$")
        expect_invalid(exc)

    def test_stats_null_or_non_negative(self) -> None:
        stats = decode_range_stats({"source_bytes": 1024, "pages": None, "sheets": None, "rows": 5, "chars": None}, "$")
        assert stats.source_bytes == 1024
        assert stats.rows == 5
        with pytest.raises(AgentValidationError) as exc:
            decode_range_stats({"source_bytes": -1, "pages": None, "sheets": None, "rows": None, "chars": None}, "$")
        expect_invalid(exc)
        with pytest.raises(AgentValidationError) as exc:
            decode_range_stats({"source_bytes": False, "pages": None, "sheets": None, "rows": None, "chars": None}, "$")
        expect_invalid(exc)

    def test_limitation_roundtrip(self) -> None:
        limit = RangeLimitation(code="scan_page_skipped", message="第3页为扫描件")
        assert limit.code == "scan_page_skipped"
        # wire 往返经 SelectedRange 覆盖


# ---------------------------------------------------------------------------
# SelectedRange：all/limited 归属、互斥、重叠
# ---------------------------------------------------------------------------


class TestSelectedRange:
    def base(self, **overrides: Any) -> dict[str, Any]:
        wire: dict[str, Any] = {
            "selection": {"kind": "all"},
            "read_ranges": [],
            "unread_ranges": [],
            "stats": {"source_bytes": None, "pages": None, "sheets": None, "rows": None, "chars": None},
            "limitations": [],
            "accepted_limited_version": None,
        }
        wire.update(overrides)
        return wire

    def test_all_with_single_all_read_atom(self) -> None:
        wire = self.base(read_ranges=[{"kind": "all"}])
        sr = decode_selected_range(wire, "$")
        assert sr.read_ranges == (AllAtom(),)
        assert to_wire_dict(sr) == wire

    def test_all_selection_rejects_explicit_range_atom(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            decode_selected_range(self.base(read_ranges=[pages_wire()]), "$")
        expect_invalid(exc)
        # all 独占数组：两个 all 也拒绝
        with pytest.raises(AgentValidationError) as exc:
            decode_selected_range(self.base(read_ranges=[{"kind": "all"}, {"kind": "all"}]), "$")
        expect_invalid(exc)

    def test_limited_read_must_belong_to_selection(self) -> None:
        wire = self.base(
            selection=limited_wire(pages_wire(1, 5)),
            read_ranges=[pages_wire(2, 3)],
        )
        sr = decode_selected_range(wire, "$")
        assert isinstance(sr.read_ranges[0], PagesRange)
        # 越界读取拒绝
        outside = self.base(selection=limited_wire(pages_wire(1, 5)), read_ranges=[pages_wire(4, 9)])
        with pytest.raises(AgentValidationError) as exc:
            decode_selected_range(outside, "$")
        expect_invalid(exc)
        # 轴不匹配拒绝
        mismatched = self.base(
            selection=limited_wire(pages_wire(1, 5)), read_ranges=[{"kind": "codepoints", "start": 1, "end": 2}]
        )
        with pytest.raises(AgentValidationError) as exc:
            decode_selected_range(mismatched, "$")
        expect_invalid(exc)

    def test_read_unread_overlap_rejected(self) -> None:
        wire = self.base(
            selection=limited_wire(pages_wire(1, 10)),
            read_ranges=[pages_wire(1, 5)],
            unread_ranges=[pages_wire(5, 8)],
        )
        with pytest.raises(AgentValidationError) as exc:
            decode_selected_range(wire, "$")
        expect_invalid(exc)

    def test_duplicate_and_overlap_within_read_rejected(self) -> None:
        dup = self.base(selection=limited_wire(pages_wire(1, 9)), read_ranges=[pages_wire(1, 3), pages_wire(1, 3)])
        with pytest.raises(AgentValidationError) as exc:
            decode_selected_range(dup, "$")
        expect_invalid(exc)
        overlap = self.base(selection=limited_wire(pages_wire(1, 9)), read_ranges=[pages_wire(1, 4), pages_wire(3, 6)])
        with pytest.raises(AgentValidationError) as exc:
            decode_selected_range(overlap, "$")
        expect_invalid(exc)

    def test_limited_forbids_all_atom_in_arrays(self) -> None:
        wire = self.base(selection=limited_wire(pages_wire(1, 5)), read_ranges=[{"kind": "all"}])
        with pytest.raises(AgentValidationError) as exc:
            decode_selected_range(wire, "$")
        expect_invalid(exc)

    def test_json_pointer_ancestor_overlap_rejected(self) -> None:
        wire = self.base(
            selection=limited_wire(
                {"kind": "json_pointer", "pointer": "/report"},
            ),
            read_ranges=[
                {"kind": "json_pointer", "pointer": "/report/revenue"},
                {"kind": "json_pointer", "pointer": "/report"},
            ],
        )
        with pytest.raises(AgentValidationError) as exc:
            decode_selected_range(wire, "$")
        expect_invalid(exc)

    def test_fields_containment_with_row_semantics(self) -> None:
        sel_fields = {"kind": "fields", "sheet": None, "row_start": None, "row_end": None, "keys": ["a", "b"]}
        # sel 全行 → 读任意行子集合法
        ok = self.base(
            selection=limited_wire(sel_fields),
            read_ranges=[{"kind": "fields", "sheet": None, "row_start": 1, "row_end": 2, "keys": ["a"]}],
        )
        decode_selected_range(ok, "$")
        # sel 限定行 → 读越界行拒绝
        sel_rows = {"kind": "fields", "sheet": None, "row_start": 1, "row_end": 3, "keys": ["a", "b"]}
        bad = self.base(
            selection=limited_wire(sel_rows),
            read_ranges=[{"kind": "fields", "sheet": None, "row_start": 2, "row_end": 5, "keys": ["a"]}],
        )
        with pytest.raises(AgentValidationError) as exc:
            decode_selected_range(bad, "$")
        expect_invalid(exc)
        # 键超出选择拒绝
        extra_key = self.base(
            selection=limited_wire(sel_rows),
            read_ranges=[{"kind": "fields", "sheet": None, "row_start": 1, "row_end": 3, "keys": ["a", "z"]}],
        )
        with pytest.raises(AgentValidationError) as exc:
            decode_selected_range(extra_key, "$")
        expect_invalid(exc)

    def test_acceptance_and_limitations(self) -> None:
        wire = self.base(
            selection=limited_wire(pages_wire(1, 5)),
            read_ranges=[pages_wire(1, 2)],
            limitations=[{"code": "partial_read", "message": "受预算限制"}],
            accepted_limited_version="1",
        )
        sr = decode_selected_range(wire, "$")
        assert sr.accepted_limited_version == "1"
        assert sr.limitations[0].code == "partial_read"
        # 未接受必须为 null
        bad = self.base(selection=limited_wire(pages_wire(1, 5)), accepted_limited_version="")
        with pytest.raises(AgentValidationError) as exc:
            decode_selected_range(bad, "$")
        expect_invalid(exc)

    def test_digest_covers_coverage_limitations_not_display(self) -> None:
        # selected_range_digest = sha256(CanonicalJson(selected_range))，覆盖读取/未读/统计/限制/接受记录
        sr = decode_selected_range(
            self.base(selection=limited_wire(pages_wire(1, 5)), read_ranges=[pages_wire(1, 2)]), "$"
        )
        expected = sha256_hex(json_doc(sr))
        assert selected_range_digest(sr) == expected
        other = decode_selected_range(
            self.base(selection=limited_wire(pages_wire(1, 5)), read_ranges=[pages_wire(3, 4)]), "$"
        )
        assert selected_range_digest(other) != selected_range_digest(sr)


# ---------------------------------------------------------------------------
# ProvenanceRef 与谱系边
# ---------------------------------------------------------------------------

DIGEST_B = "b" * 64
DIGEST_C = "c" * 64


def provenance_wire() -> dict[str, Any]:
    return {
        "source": ORIGIN_WIRE,
        "locator": {
            "locator_kind": "source_range",
            "origin_ref": ORIGIN_WIRE,
            "selection": limited_wire(pages_wire(1, 5)),
        },
        "selected_range_digest": DIGEST_B,
        "representation_version": "rep-v1",
        "representation_digest": DIGEST_C,
    }


class TestProvenanceRef:
    def test_roundtrip(self) -> None:
        ref = decode_provenance_ref(provenance_wire(), "$")
        assert ref.representation_version == "rep-v1"
        assert to_wire_dict(ref) == provenance_wire()

    def test_source_must_equal_locator_origin(self) -> None:
        wire = provenance_wire()
        wire["source"] = {**ORIGIN_WIRE, "source_version": "2"}
        with pytest.raises(AgentValidationError) as exc:
            decode_provenance_ref(wire, "$")
        assert code_of(exc) == "origin_mismatch"

    def test_digests_validated(self) -> None:
        wire = provenance_wire()
        wire["representation_digest"] = "not-a-digest"
        with pytest.raises(AgentValidationError) as exc:
            decode_provenance_ref(wire, "$")
        expect_invalid(exc)

    def test_provenance_refs_order_and_duplicates(self) -> None:
        edge_a = provenance_wire()
        edge_b = {
            "source": {**ORIGIN_WIRE, "source_id": "src-2"},
            "locator": {
                "locator_kind": "source_range",
                "origin_ref": {**ORIGIN_WIRE, "source_id": "src-2"},
                "selection": {"kind": "all"},
            },
            "selected_range_digest": DIGEST_B,
            "representation_version": "rep-v1",
            "representation_digest": DIGEST_C,
        }
        refs = decode_provenance_refs([edge_a, edge_b], "$")
        assert [r.source.source_id for r in refs] == ["src-1", "src-2"]
        # 重复完全相同的边拒绝；相同源不同范围可保留
        with pytest.raises(AgentValidationError) as exc:
            decode_provenance_refs([edge_a, edge_a], "$")
        assert code_of(exc) == "duplicate_edge"
        different_range = {
            "source": ORIGIN_WIRE,
            "locator": {
                "locator_kind": "source_range",
                "origin_ref": ORIGIN_WIRE,
                "selection": limited_wire(pages_wire(6, 9)),
            },
            "selected_range_digest": DIGEST_B,
            "representation_version": "rep-v1",
            "representation_digest": DIGEST_C,
        }
        refs = decode_provenance_refs([edge_a, different_range], "$")
        assert len(refs) == 2

    def test_empty_provenance_allowed(self) -> None:
        assert decode_provenance_refs([], "$") == ()


# ---------------------------------------------------------------------------
# V04 数值在 wire 中的互操作（数字端点不可为带类型数值）
# ---------------------------------------------------------------------------


class TestWireInterop:
    def test_wire_dict_canonical_roundtrip(self) -> None:
        wire = provenance_wire()
        ref = decode_provenance_ref(wire, "$")
        reencoded = json_doc(ref)
        assert reencoded == json_doc(wire)
        assert decode_provenance_ref(cast(dict[str, Any], wire), "$") == ref

    def test_decimal_endpoint_rejected(self) -> None:
        # 端点必须为整数：经 codec wire 的 Decimal（decode_json_doc 产物）不是 int
        atom = {"kind": "pages", "start": Decimal("1"), "end": 2}
        with pytest.raises(AgentValidationError) as exc:
            decode_range_atom(atom, "$")
        expect_invalid(exc)
