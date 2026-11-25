"""E-05-3 三格式渲染与逐字段回读（DD-F26 / §10.5 / HANDOFF §10.2）。

覆盖：
- 成果读取 → 交付字段：元数据显式 null、正文完整（分页未取全即拒绝）、
  ``result_payload`` 缺失 ≠ 空容器、数值原词法保真、None/0/False/空串互不混同；
- 30,000 UTF-16 单元分段：恰等通过、超一完整分条、非 BMP 不拆代理对；
- MD / CSV(BOM UTF-8) / XLSX 三格式渲染后逐字段回读一致；
- XLSX 公式单元格 0（``=SUM(...)`` 与 ``00123`` 均作为文本原样保留）；
- CSV 文本保护：公式前缀/空白前缀/前导零/高精度数值/合法负数加前导 ``'`` 后
  重开不按公式或数字解释，回读去掉恰好一个 ``'`` 即还原原值（可逆）；
- 回读比对能发现篡改，渲染摘要确定且与格式绑定。
"""

from __future__ import annotations

import io

import pytest
from openpyxl import load_workbook

from core.agent_types import (
    DELIVERY_CHUNK_MAX_UTF16,
    AgentValidationError,
    ArtifactRead,
    ArtifactSourceState,
    DeliveryField,
    aggregate_delivery_source_state,
    build_delivery_fields,
    json_doc,
    validate_delivery_fields,
)
from services.agent_service.delivery_render import (
    DeliveryRenderError,
    read_delivery_fields,
    render_delivery,
    verify_delivery_readback,
)

_USER_PAYLOAD = '{"a":[],"b":false,"f":1.50,"i":2,"n":null,"nested":{"x":[1,{"y":"z"}]},"o":{},"s":"00123","t":""}'


def _read(**overrides: object) -> ArtifactRead:
    values: dict[str, object] = {
        "artifact_id": "art-1",
        "artifact_version": 1,
        "session_id": "s1",
        "execution_id": "exec-1",
        "round_id": "round-1",
        "artifact_kind": "research",
        "input_revision": 3,
        "actual_input_digest": "a" * 64,
        "actual_model_ref": "model/main",
        "result_digest": "b" * 64,
        "source_available_state": "available",
        "summary": "成果摘要",
        "result_payload": _USER_PAYLOAD,
        "published_at": None,
        "cleaned_at": None,
        "updated_at": "2026-11-15T08:00:00.000000Z",
        "revision": 2,
        "body_text": "成果正文",
        "next_cursor": None,
        "body_available": True,
        "unavailable_reason": None,
        "question_public_text": "如何评估该策略的风险？",
    }
    values.update(overrides)
    return ArtifactRead(**values)  # pyright: ignore[reportArgumentType]


def _by_path(fields: tuple[DeliveryField, ...]) -> dict[str, DeliveryField]:
    return {field.path: field for field in fields}


def _ref_state(
    source_id: str,
    state: str,
    *,
    version: str = "1",
    kind: str = "attachment",
) -> ArtifactSourceState:
    return ArtifactSourceState(
        source_kind=kind,
        source_id=source_id,
        source_version=version,
        source_digest="c" * 64,
        state=state,
    )


class TestBuildDeliveryFields:
    def test_metadata_and_body_are_explicit(self) -> None:
        fields = build_delivery_fields(_read(published_at=None, cleaned_at=None))
        index = _by_path(fields)

        assert index["metadata:artifact_id"].value_type == "string"
        assert index["metadata:artifact_id"].value_text == "art-1"
        assert index["metadata:artifact_version"].value_type == "int"
        assert index["metadata:artifact_version"].value_text == "1"
        assert index["metadata:revision"].value_text == "2"
        assert index["metadata:body_available"].value_type == "bool"
        assert index["metadata:body_available"].value_text == "true"
        assert index["metadata:published_at"].value_type == "null"
        assert index["metadata:published_at"].value_text == ""
        assert index["metadata:cleaned_at"].value_type == "null"
        assert index["metadata:summary"].value_text == "成果摘要"
        assert index["metadata:source_available_state"].value_text == "available"
        assert index["metadata:current_source_state"].value_text == "unknown"
        assert index["body:"].value_type == "string"
        assert index["body:"].value_text == "成果正文"
        assert index["metadata:question_public"].value_text == "如何评估该策略的风险？"

    def test_question_public_is_read_back_in_all_formats(self) -> None:
        fields = build_delivery_fields(_read())

        for format_name in ("md", "csv", "xlsx"):
            rendered = render_delivery(fields, format_name)
            verify_delivery_readback(fields, rendered.content, format_name)
            readback = _by_path(read_delivery_fields(rendered.content, format_name))
            assert readback["metadata:question_public"].value_text == "如何评估该策略的风险？"
            assert readback["metadata:question_public"].value_type == "string"

    @pytest.mark.parametrize("question", [None, ""])
    def test_missing_public_question_is_never_exported_as_complete(self, question: str | None) -> None:
        with pytest.raises(AgentValidationError) as error:
            build_delivery_fields(_read(question_public_text=question))
        assert error.value.code == "question_public_unavailable"

    def test_source_state_columns_are_split_and_canonical(self) -> None:
        index = _by_path(
            build_delivery_fields(_read(source_available_state="current"), current_source_state="disabled")
        )

        assert index["metadata:source_available_state"].value_text == "unknown"
        assert index["metadata:current_source_state"].value_text == "disabled"

    def test_each_source_ref_state_is_written_and_unknown_is_not_masked(self) -> None:
        fields = build_delivery_fields(
            _read(),
            current_source_state="unknown",
            current_source_states=(_ref_state("src-a", "available"), _ref_state("src-b", "unknown")),
        )
        index = _by_path(fields)

        assert index["metadata:current_source_state"].value_text == "unknown"
        assert index["source:/0/source_kind"].value_text == "attachment"
        assert index["source:/0/source_id"].value_text == "src-a"
        assert index["source:/0/source_version"].value_text == "1"
        assert index["source:/0/source_digest"].value_text == "c" * 64
        assert index["source:/0/state"].value_text == "available"
        assert index["source:/1/source_id"].value_text == "src-b"
        assert index["source:/1/state"].value_text == "unknown"
        # 记录域顺序：metadata → source → body → payload（渲染按记录分段）。
        records = [field.record for field in fields]
        metadata_count = records.count("metadata")
        source_count = records.count("source")
        assert records.index("source") == metadata_count
        assert source_count == 10
        assert records[metadata_count : metadata_count + source_count] == ["source"] * source_count
        assert records.index("body") == metadata_count + source_count

    def test_source_state_aggregate_mismatch_is_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as error:
            build_delivery_fields(
                _read(),
                current_source_state="available",
                current_source_states=(_ref_state("src-a", "unknown"),),
            )
        assert error.value.code == "inconsistent_source_state"

    def test_aggregate_never_upgrades_any_single_state(self) -> None:
        assert aggregate_delivery_source_state(()) == "unknown"
        assert aggregate_delivery_source_state((_ref_state("a", "available"), _ref_state("b", "unknown"))) == "unknown"
        assert (
            aggregate_delivery_source_state((_ref_state("a", "available"), _ref_state("b", "disabled"))) == "disabled"
        )
        assert (
            aggregate_delivery_source_state((_ref_state("a", "unavailable"), _ref_state("b", "disabled"))) == "disabled"
        )
        assert aggregate_delivery_source_state((_ref_state("a", "available"),)) == "available"

    def test_metadata_order_is_stable(self) -> None:
        fields = build_delivery_fields(_read())
        metadata = [field.pointer for field in fields if field.record == "metadata"]
        assert metadata[0] == "artifact_id"
        assert metadata[-1] == "unavailable_reason"
        assert metadata.index("artifact_version") == metadata.index("artifact_id") + 1

    def test_paged_body_without_full_text_is_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as error:
            build_delivery_fields(_read(next_cursor="b:art-1:1:2001"))
        assert error.value.code == "page_not_complete"

    def test_full_body_override_is_rendered_in_full(self) -> None:
        full = "正文" * 3000
        fields = build_delivery_fields(_read(next_cursor="b:art-1:1:2001"), body_text=full)
        body = [field for field in fields if field.record == "body"]
        assert "".join(field.value_text for field in body) == full

    def test_absent_payload_emits_no_payload_fields(self) -> None:
        fields = build_delivery_fields(_read(result_payload=None))
        assert all(field.record != "payload" for field in fields)

    def test_empty_payload_container_is_explicit(self) -> None:
        fields = _by_path(build_delivery_fields(_read(result_payload="{}")))
        assert fields["payload:"].value_type == "object"
        assert fields["payload:"].value_text == ""

    def test_payload_keeps_types_and_number_lexemes(self) -> None:
        index = _by_path(build_delivery_fields(_read()))

        assert index["payload:"].value_type == "object"
        assert index["payload:/n"].value_type == "null"
        assert index["payload:/n"].value_text == ""
        assert index["payload:/b"].value_type == "bool"
        assert index["payload:/b"].value_text == "false"
        assert index["payload:/i"].value_type == "int"
        assert index["payload:/i"].value_text == "2"
        assert index["payload:/f"].value_type == "float"
        assert index["payload:/f"].value_text == "1.50"
        assert index["payload:/s"].value_type == "string"
        assert index["payload:/s"].value_text == "00123"
        assert index["payload:/t"].value_type == "string"
        assert index["payload:/t"].value_text == ""
        assert index["payload:/o"].value_type == "object"
        assert index["payload:/a"].value_type == "array"
        assert index["payload:/nested/x/1/y"].value_text == "z"
        assert index["payload:/nested/x/0"].value_type == "int"

    def test_unavailable_body_reports_reason_without_body_rows(self) -> None:
        fields = build_delivery_fields(_read(body_text=None, body_available=False, unavailable_reason="body_cleaned"))
        index = _by_path(fields)
        assert index["metadata:body_available"].value_text == "false"
        assert index["metadata:unavailable_reason"].value_text == "body_cleaned"
        assert "body:" not in index

    def test_validation_rejects_broken_field_contracts(self) -> None:
        good = build_delivery_fields(_read())
        broken_cases = [
            (DeliveryField("metadata", "artifact:id", "string", "x"), "invalid_pointer"),
            (DeliveryField("payload", "a/b", "string", "x"), "invalid_pointer"),
            (DeliveryField("body", "x", "string", "x"), "invalid_pointer"),
            (DeliveryField("body", "", "text", "x"), "invalid_value_type"),
            (DeliveryField("body", "", "string", "x", chunk_index=0), "invalid_chunk_range"),
            (DeliveryField("body", "", "string", "x", chunk_index=2, chunk_count=1), "invalid_chunk_range"),
            (DeliveryField("body", "", "string", "x" * (DELIVERY_CHUNK_MAX_UTF16 + 1)), "chunk_too_large"),
        ]
        for field, code in broken_cases:
            with pytest.raises(AgentValidationError) as error:
                validate_delivery_fields((*good, field))
            assert error.value.code == code

    def test_validation_requires_complete_chunk_sequence(self) -> None:
        long_text = "x" * (DELIVERY_CHUNK_MAX_UTF16 + 1)
        fields = build_delivery_fields(_read(body_text=long_text, body_available=True))
        body = tuple(field for field in fields if field.record == "body")
        assert len(body) == 2
        without_tail = tuple(field for field in fields if not (field.record == "body" and field.chunk_index == 2))
        with pytest.raises(AgentValidationError) as error:
            validate_delivery_fields(without_tail)
        assert error.value.code == "missing_chunk"


class TestChunking:
    def test_exact_limit_fits_single_chunk(self) -> None:
        fields = build_delivery_fields(_read(body_text="x" * DELIVERY_CHUNK_MAX_UTF16))
        body = [field for field in fields if field.record == "body"]
        assert len(body) == 1
        assert body[0].chunk_count == 1

    def test_over_limit_splits_without_truncation(self) -> None:
        text = "x" * (DELIVERY_CHUNK_MAX_UTF16 + 1)
        body = [field for field in build_delivery_fields(_read(body_text=text)) if field.record == "body"]
        assert [field.chunk_count for field in body] == [2, 2]
        assert "".join(field.value_text for field in body) == text
        assert all(len(field.value_text) <= DELIVERY_CHUNK_MAX_UTF16 for field in body)

    def test_non_bmp_counts_two_units_and_is_not_split(self) -> None:
        text = "😀" * (DELIVERY_CHUNK_MAX_UTF16 // 2 + 1)
        body = [field for field in build_delivery_fields(_read(body_text=text)) if field.record == "body"]
        assert len(body) == 2
        assert body[0].chunk_count == 2
        assert all("\ud800" not in field.value_text and "\udc00" not in field.value_text for field in body)
        assert "".join(field.value_text for field in body) == text


class TestRenderRoundTrip:
    def _fields(self) -> tuple[DeliveryField, ...]:
        body = '开头\n```\n嵌套围栏\n```\n结尾,逗号\n"引号"\n'
        return build_delivery_fields(_read(body_text=body, summary="摘要|含竖线"))

    def test_markdown_roundtrip_is_verbatim(self) -> None:
        fields = self._fields()
        rendered = render_delivery(fields, "md")
        assert rendered.format == "md"
        assert rendered.field_count == len(fields)
        readback = read_delivery_fields(rendered.content, "md")
        assert readback == fields

    def test_markdown_hostile_field_keys_are_escaped_and_roundtrip(self) -> None:
        """I86：字段键同属不可信数据——反引号/换行/"` (" 不得破坏标题或回读。"""

        payload = {"a`b": 1, "line\nbreak": 2, "paren` (x": 3, "back\\slash": 4}
        fields = build_delivery_fields(_read(result_payload=json_doc(payload)))
        rendered = render_delivery(fields, "md")

        lines = rendered.content.decode("utf-8").split("\n")
        headings = [line for line in lines if line.startswith("### `")]
        assert len(headings) == len(fields)
        assert read_delivery_fields(rendered.content, "md") == fields

    def test_table_formats_keep_hostile_keys_verbatim(self) -> None:
        """CSV/XLSX 为纯文本单元格，恶意字段键同样原值往返。"""

        fields = build_delivery_fields(_read(result_payload=json_doc({"a`b": 1, "line\nbreak": 2})))
        for format_name in ("csv", "xlsx"):
            assert read_delivery_fields(render_delivery(fields, format_name).content, format_name) == fields

    def test_csv_has_utf8_bom_and_roundtrip(self) -> None:
        fields = self._fields()
        rendered = render_delivery(fields, "csv")
        assert rendered.content.startswith(b"\xef\xbb\xbf")
        text = rendered.content.decode("utf-8-sig")
        assert "00123" in text
        assert read_delivery_fields(rendered.content, "csv") == fields

    def test_xlsx_has_zero_formula_cells_and_roundtrip(self) -> None:
        fields = build_delivery_fields(
            _read(result_payload=json_doc({"formula": "=SUM(A1:A2)", "code": "00123", "long": "值" * 20}))
        )
        rendered = render_delivery(fields, "xlsx")

        workbook = load_workbook(io.BytesIO(rendered.content), read_only=True, data_only=False)
        formula_cells = [
            cell.coordinate for row in workbook.worksheets[0].iter_rows() for cell in row if cell.data_type == "f"
        ]
        assert formula_cells == []
        workbook.close()

        readback = read_delivery_fields(rendered.content, "xlsx")
        assert readback == fields
        index = _by_path(readback)
        assert index["payload:/formula"].value_text == "=SUM(A1:A2)"
        assert index["payload:/code"].value_text == "00123"

    def test_xlsx_long_field_survives_chunking(self) -> None:
        text = "长" * (DELIVERY_CHUNK_MAX_UTF16 + 5) + "😀"
        fields = build_delivery_fields(_read(body_text=text))
        readback = read_delivery_fields(render_delivery(fields, "xlsx").content, "xlsx")
        body = [field for field in readback if field.record == "body"]
        assert len(body) == 2
        assert "".join(field.value_text for field in body) == text

    def test_digest_is_deterministic_and_format_bound(self) -> None:
        fields = self._fields()
        first = render_delivery(fields, "csv")
        second = render_delivery(fields, "csv")
        markdown = render_delivery(fields, "md")
        assert first.digest == second.digest
        assert first.digest != markdown.digest
        assert len(first.digest) == 64

    def test_unknown_format_is_rejected(self) -> None:
        with pytest.raises(DeliveryRenderError) as error:
            render_delivery(self._fields(), "pdf")
        assert error.value.code == "invalid_format"

    def test_source_records_roundtrip_in_all_formats(self) -> None:
        fields = build_delivery_fields(
            _read(),
            current_source_state="disabled",
            current_source_states=(
                _ref_state("src-a", "available"),
                _ref_state("src-b", "disabled", version="2", kind="mcp_return"),
            ),
        )
        for format_name in ("md", "csv", "xlsx"):
            rendered = render_delivery(fields, format_name)
            verify_delivery_readback(fields, rendered.content, format_name)
            index = _by_path(read_delivery_fields(rendered.content, format_name))
            assert index["source:/1/source_kind"].value_text == "mcp_return"
            assert index["source:/1/source_version"].value_text == "2"
            assert index["source:/1/state"].value_text == "disabled"


class TestCsvTextProtection:
    """CSV 无单元格格式位：用前导 ``'`` 把会被重解释的值钉成文本，且可逆还原。"""

    _MARK = "'"

    @staticmethod
    def _fields(pairs: list[tuple[str, str]]) -> tuple[DeliveryField, ...]:
        return tuple(
            DeliveryField("payload", f"/{index}", value_type, text) for index, (value_type, text) in enumerate(pairs)
        )

    def test_formula_and_whitespace_prefixes_are_marked(self) -> None:
        values = ["=SUM(A1:A2)", "+1+1", "-2+3", "@SUM(A1:A2)", " 前导空格", "\t制表符", "\r回车"]
        fields = self._fields([("string", value) for value in values])
        rendered = render_delivery(fields, "csv")
        text = rendered.content.decode("utf-8-sig")

        for value in values:
            assert f'"{self._MARK}{value}"' in text
        assert read_delivery_fields(rendered.content, "csv") == fields

    def test_numeric_lexemes_and_leading_zeros_are_marked(self) -> None:
        cases = [
            ("string", "00123"),
            ("string", "0x1F"),
            ("float", "0.1234567890123456789"),
            ("float", "1.50"),
            ("float", "1e400"),
            ("int", "12345678901234567890"),
        ]
        fields = self._fields(cases)
        rendered = render_delivery(fields, "csv")
        text = rendered.content.decode("utf-8-sig")

        for _value_type, value in cases:
            assert f'"{self._MARK}{value}"' in text
        assert read_delivery_fields(rendered.content, "csv") == fields

    def test_legal_negative_number_keeps_value_and_type(self) -> None:
        fields = self._fields([("int", "-5")])
        rendered = render_delivery(fields, "csv")

        assert f'"{self._MARK}-5"' in rendered.content.decode("utf-8-sig")
        readback = read_delivery_fields(rendered.content, "csv")
        assert readback == fields
        assert readback[0].value_type == "int"
        assert readback[0].value_text == "-5"

    def test_apostrophe_leading_value_is_doubled_and_restored(self) -> None:
        fields = self._fields([("string", "'纯文本")])
        rendered = render_delivery(fields, "csv")

        assert f'"\'{self._MARK}纯文本"' in rendered.content.decode("utf-8-sig")
        assert read_delivery_fields(rendered.content, "csv") == fields

    def test_plain_text_is_not_marked(self) -> None:
        fields = self._fields([("string", "普通文本"), ("string", "")])
        rendered = render_delivery(fields, "csv")
        text = rendered.content.decode("utf-8-sig")

        assert f'"{self._MARK}普通文本"' not in text
        assert '"普通文本"' in text
        assert read_delivery_fields(rendered.content, "csv") == fields

    def test_risky_values_survive_verification_in_all_formats(self) -> None:
        fields = self._fields(
            [
                ("string", "=SUM(A1:A2)"),
                ("string", "00123"),
                ("float", "0.1234567890123456789"),
                ("int", "-5"),
                ("string", " 前导空格"),
                ("string", "'已带前缀"),
            ]
        )
        for format_name in ("md", "csv", "xlsx"):
            rendered = render_delivery(fields, format_name)
            verify_delivery_readback(fields, rendered.content, format_name)
            assert read_delivery_fields(rendered.content, format_name) == fields


class TestVerifyReadback:
    def test_own_output_passes_verification(self) -> None:
        fields = build_delivery_fields(_read(result_payload=json_doc({"code": "00123"})))
        for format_name in ("md", "csv", "xlsx"):
            rendered = render_delivery(fields, format_name)
            verify_delivery_readback(fields, rendered.content, format_name)

    def test_tampered_csv_value_is_detected(self) -> None:
        fields = build_delivery_fields(_read(result_payload=json_doc({"code": "00123"})))
        rendered = render_delivery(fields, "csv")
        tampered = rendered.content.replace(b"00123", b"123")
        with pytest.raises(DeliveryRenderError) as error:
            verify_delivery_readback(fields, tampered, "csv")
        assert error.value.code == "delivery_render_readback_mismatch"

    def test_corrupt_xlsx_is_reported_not_swallowed(self) -> None:
        fields = build_delivery_fields(_read())
        rendered = render_delivery(fields, "xlsx")
        with pytest.raises(DeliveryRenderError) as error:
            verify_delivery_readback(fields, rendered.content[: len(rendered.content) // 2], "xlsx")
        assert error.value.code == "delivery_render_readback_invalid"
