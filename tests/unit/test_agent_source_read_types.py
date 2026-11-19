"""E-01 §3.3 来源读取/事实/D06 预算契约单元测试。

覆盖：AttachmentBudget 恰等/超一、SourceRef/FactValue/XlsxCellMeta/SourceRead/
EvidenceLink 全键必填、禁额外键、null 与 bool/整数类型拒绝、规范序列化往返。
"""

from typing import Any

import pytest

from core.agent_types import (
    DEFAULT_ATTACHMENT_BUDGET,
    AgentValidationError,
    AttachmentBudget,
    AttachmentKind,
    EvidenceLink,
    FactValue,
    ProvenanceRef,
    RangeLimitation,
    RangeStats,
    ReadState,
    SelectedRange,
    Selection,
    SourceKind,
    SourceLocator,
    SourceRead,
    SourceRef,
    SourceVersionRef,
    VerificationState,
    XlsxCellMeta,
    decode_evidence_link,
    decode_fact_value,
    decode_source_read,
    decode_source_ref,
    decode_xlsx_cell_meta,
    encode_evidence_link,
    encode_fact_value,
    encode_source_read,
    encode_source_ref,
    json_doc,
    sha256_hex,
    to_wire_dict,
)

_DIGEST = sha256_hex(b"e01-source")


def expect_code(excinfo: pytest.ExceptionInfo[AgentValidationError], code: str) -> None:
    assert excinfo.value.code == code


def _locator(*, kind: SourceKind = SourceKind.ATTACHMENT) -> SourceLocator:
    ref = SourceVersionRef(source_kind=kind, source_id="src-1", source_version="1", source_digest=_DIGEST)
    return SourceLocator(locator_kind="source_range", origin_ref=ref, selection=Selection(kind="all"))


def _selected_range() -> SelectedRange:
    return SelectedRange(
        selection=Selection(kind="all"),
        stats=RangeStats(source_bytes=12, chars=12),
        limitations=(RangeLimitation(code="partial_scan", message="扫描页未 OCR"),),
    )


def _provenance() -> ProvenanceRef:
    locator = _locator()
    return ProvenanceRef(
        source=locator.origin_ref,
        locator=locator,
        selected_range_digest=_DIGEST,
        representation_version="represent-v1",
        representation_digest=_DIGEST,
    )


def _source_ref(**overrides: object) -> SourceRef:
    values: dict[str, object] = {
        "user_scope": "scope-1",
        "source_id": "src-1",
        "version": "1",
        "digest": _DIGEST,
        "source_kind": SourceKind.ATTACHMENT,
        "display_name": "年报.pdf",
        "object_ref": "600000.SH",
        "locator": _locator(),
        "available_at": "2026-01-02T03:04:05.000000Z",
        "period": "2025",
        "unit_note": "金额单位：元",
        "read_state": ReadState.PARTIAL,
        "missing_reason": None,
        "provenance_refs": (_provenance(),),
    }
    values.update(overrides)
    return SourceRef(**values)  # type: ignore[arg-type]  # 测试夹具按字段覆盖构造


def _fact(**overrides: object) -> FactValue:
    values: dict[str, object] = {
        "field_key": "revenue",
        "object_ref": "600000.SH@2025",
        "raw_type": "json_decimal",
        "raw_value": '{"numeric_encoding":"agent-number-v1","raw_type":"json_decimal",'
        '"lexeme":"0.1","exact_decimal":"0.1"}',
        "numeric_lexeme": "0.1",
        "numeric_encoding": "agent-number-v1",
        "source_unit": "yuan",
        "currency": "CNY",
        "display_unit": "yi_cny",
        "conversion_ref": "threshold_in_data_unit@v1",
        "period": "2025",
        "basis": "合并",
        "source_locator": _locator(),
        "verification": VerificationState.PROGRAM_VERIFIED,
        "missing_reason": None,
        "xlsx_cell_meta": None,
    }
    values.update(overrides)
    return FactValue(**values)  # type: ignore[arg-type]  # 测试夹具按字段覆盖构造


class TestAttachmentKindAndBudget:
    def test_kind_values_are_frozen_wire_spellings(self) -> None:
        assert {kind.value for kind in AttachmentKind} == {"txt", "md", "csv", "json", "xlsx", "pdf", "png", "jpeg"}

    def test_default_budget_matches_d06_numbers(self) -> None:
        budget = DEFAULT_ATTACHMENT_BUDGET
        assert (budget.max_files, budget.max_source_bytes) == (5, 20_000_000)
        assert (budget.max_pages, budget.max_sheets) == (100, 10)
        assert (budget.max_rows, budget.max_codepoints) == (50_000, 100_000)
        assert budget.extract_timeout_s == 30.0

    def test_file_count_exact_passes_and_plus_one_rejected(self) -> None:
        budget = AttachmentBudget()
        budget.enforce_file_count(5)
        with pytest.raises(AgentValidationError) as exc:
            budget.enforce_file_count(6)
        expect_code(exc, "attachment_count_exceeded")

    def test_source_bytes_exact_passes_and_plus_one_rejected(self) -> None:
        budget = AttachmentBudget()
        budget.enforce_source_bytes(20_000_000)
        with pytest.raises(AgentValidationError) as exc:
            budget.enforce_source_bytes(20_000_001)
        expect_code(exc, "attachment_bytes_exceeded")

    @pytest.mark.parametrize(
        ("method", "limit", "code"),
        [
            ("enforce_pages", 100, "attachment_pages_exceeded"),
            ("enforce_sheets", 10, "attachment_sheets_exceeded"),
            ("enforce_rows", 50_000, "attachment_rows_exceeded"),
            ("enforce_codepoints", 100_000, "attachment_codepoints_exceeded"),
        ],
    )
    def test_each_extraction_limit_exact_passes_and_plus_one_rejected(self, method: str, limit: int, code: str) -> None:
        budget = AttachmentBudget()
        getattr(budget, method)(limit)
        with pytest.raises(AgentValidationError) as exc:
            getattr(budget, method)(limit + 1)
        expect_code(exc, code)

    def test_elapsed_exact_passes_and_over_rejected(self) -> None:
        budget = AttachmentBudget()
        budget.enforce_elapsed(30.0)
        with pytest.raises(AgentValidationError) as exc:
            budget.enforce_elapsed(30.001)
        expect_code(exc, "attachment_time_exceeded")

    def test_bool_and_negative_are_not_counts(self) -> None:
        budget = AttachmentBudget()
        with pytest.raises(AgentValidationError) as exc_bool:
            budget.enforce_file_count(True)  # type: ignore[arg-type]  # bool 不是整数计数
        expect_code(exc_bool, "invalid_type")
        with pytest.raises(AgentValidationError) as exc_negative:
            budget.enforce_file_count(-1)
        expect_code(exc_negative, "invalid_range")


class TestSourceRefCodec:
    def test_roundtrip_preserves_all_fields_and_provenance_order(self) -> None:
        ref = _source_ref()
        wire = encode_source_ref(ref)
        assert set(wire) == {
            "user_scope",
            "source_id",
            "version",
            "digest",
            "source_kind",
            "display_name",
            "object_ref",
            "locator",
            "available_at",
            "period",
            "unit_note",
            "read_state",
            "missing_reason",
            "provenance_refs",
        }
        assert decode_source_ref(wire) == ref
        assert json_doc(wire) == json_doc(encode_source_ref(decode_source_ref(wire)))

    def test_missing_key_rejected(self) -> None:
        wire = encode_source_ref(_source_ref())
        del wire["unit_note"]
        with pytest.raises(AgentValidationError) as exc:
            decode_source_ref(wire)
        expect_code(exc, "missing_field")

    def test_extra_key_rejected(self) -> None:
        wire = encode_source_ref(_source_ref())
        wire["path"] = "/private/a.pdf"
        with pytest.raises(AgentValidationError) as exc:
            decode_source_ref(wire)
        expect_code(exc, "unknown_field")

    def test_invalid_kind_digest_and_state_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc_kind:
            decode_source_ref({**encode_source_ref(_source_ref()), "source_kind": "message"})
        expect_code(exc_kind, "invalid_source_kind")
        with pytest.raises(AgentValidationError) as exc_digest:
            decode_source_ref({**encode_source_ref(_source_ref()), "digest": "ABC"})
        expect_code(exc_digest, "invalid_digest")
        with pytest.raises(AgentValidationError) as exc_state:
            decode_source_ref({**encode_source_ref(_source_ref()), "read_state": "done"})
        expect_code(exc_state, "invalid_read_state")

    def test_top_level_identity_must_match_locator_origin(self) -> None:
        # 顶层四值与 locator.origin_ref 是同一源版的冗余承载：任一分叉即拒绝
        base = encode_source_ref(_source_ref())
        for mutated in (
            {**base, "source_id": "src-2"},
            {**base, "version": "2"},
            {**base, "digest": "f" * 64},
            {**base, "source_kind": "user_message"},
        ):
            with pytest.raises(AgentValidationError) as exc:
                decode_source_ref(mutated)
            expect_code(exc, "identity_mismatch")

    def test_locator_origin_must_match_top_level_identity(self) -> None:
        base = encode_source_ref(_source_ref())
        origin = {**base["locator"]["origin_ref"], "source_digest": "f" * 64}
        with pytest.raises(AgentValidationError) as exc:
            decode_source_ref({**base, "locator": {**base["locator"], "origin_ref": origin}})
        expect_code(exc, "identity_mismatch")


class TestXlsxCellMetaCodec:
    def test_all_thirteen_keys_required(self) -> None:
        meta = XlsxCellMeta(sheet="Sheet1", cell="B2", formula_text="=A1*2", cache_present=True)
        wire = to_wire_dict(meta)
        assert decode_xlsx_cell_meta(wire) == meta
        del wire["xml_value_lexeme"]
        with pytest.raises(AgentValidationError) as exc:
            decode_xlsx_cell_meta(wire)
        expect_code(exc, "missing_field")

    def test_extra_key_and_schema_version_rejected(self) -> None:
        wire = to_wire_dict(XlsxCellMeta(sheet="Sheet1", cell="A1"))
        with pytest.raises(AgentValidationError) as exc_extra:
            decode_xlsx_cell_meta({**wire, "total": 1})
        expect_code(exc_extra, "unknown_field")
        with pytest.raises(AgentValidationError) as exc_version:
            decode_xlsx_cell_meta({**wire, "schema_version": "2"})
        expect_code(exc_version, "invalid_version")

    def test_cache_flags_must_be_bool_or_null(self) -> None:
        wire = to_wire_dict(XlsxCellMeta(sheet="Sheet1", cell="A1", cache_present=None))
        assert decode_xlsx_cell_meta(wire).cache_present is None
        with pytest.raises(AgentValidationError) as exc:
            decode_xlsx_cell_meta({**wire, "cache_present": 1})
        expect_code(exc, "invalid_type")


class TestFactValueCodec:
    def test_roundtrip_with_xlsx_meta_and_exact_number(self) -> None:
        meta = XlsxCellMeta(
            sheet="Sheet1",
            cell="C3",
            formula_text="=SUM(A1:A2)",
            cache_present=True,
            cache_applicable=False,
            cache_raw_type="json_decimal",
            cache_raw_value='{"numeric_encoding":"agent-number-v1","raw_type":"json_decimal",'
            '"lexeme":"0.1","exact_decimal":"0.1"}',
            cache_verification=VerificationState.UNVERIFIED,
            cache_missing_reason=None,
            date_epoch=None,
            number_format="0.00",
            xml_value_lexeme="0.1",
        )
        fact = _fact(xlsx_cell_meta=json_doc(to_wire_dict(meta)))
        wire = encode_fact_value(fact)
        assert decode_fact_value(wire) == fact

    def test_raw_type_and_raw_value_shape_rejected(self) -> None:
        wire = encode_fact_value(_fact())
        with pytest.raises(AgentValidationError) as exc_type:
            decode_fact_value({**wire, "raw_type": "float"})
        expect_code(exc_type, "invalid_raw_type")
        with pytest.raises(AgentValidationError) as exc_json:
            decode_fact_value({**wire, "raw_value": "{not json"})
        expect_code(exc_json, "invalid_json")
        with pytest.raises(AgentValidationError) as exc_null:
            decode_fact_value({**wire, "raw_value": None})
        expect_code(exc_null, "invalid_type")

    def test_numeric_raw_value_must_be_exact_number_record(self) -> None:
        # I31①：text 形式与 raw_type 不符的伪内层数值（普通数字/字符串）一律拒绝。
        wire = encode_fact_value(_fact())
        for raw_value in ('"0.1"', "0.1", "true", "null"):
            with pytest.raises(AgentValidationError) as exc:
                decode_fact_value({**wire, "raw_value": raw_value})
            expect_code(exc, "invalid_number")

    def test_numeric_raw_type_must_match_inner_record(self) -> None:
        # I31①：外层 json_decimal 与内层 json_integer 记录分叉即拒绝。
        wire = encode_fact_value(_fact())
        inner = json_doc(
            {"numeric_encoding": "agent-number-v1", "raw_type": "json_integer", "lexeme": "1", "exact_decimal": "1"}
        )
        with pytest.raises(AgentValidationError) as exc:
            decode_fact_value({**wire, "raw_value": inner, "numeric_lexeme": "1"})
        expect_code(exc, "invalid_number")

    def test_numeric_encoding_and_lexeme_must_close_with_inner_record(self) -> None:
        wire = encode_fact_value(_fact())
        with pytest.raises(AgentValidationError) as exc_encoding:
            decode_fact_value({**wire, "numeric_encoding": "plain"})
        expect_code(exc_encoding, "invalid_number")
        with pytest.raises(AgentValidationError) as exc_missing_encoding:
            decode_fact_value({**wire, "numeric_encoding": None})
        expect_code(exc_missing_encoding, "invalid_number")
        with pytest.raises(AgentValidationError) as exc_lexeme:
            decode_fact_value({**wire, "numeric_lexeme": "0.2"})
        expect_code(exc_lexeme, "invalid_number")

    def test_non_numeric_raw_type_rejected_with_numeric_metadata_or_mismatch(self) -> None:
        wire = encode_fact_value(
            _fact(raw_type="string", raw_value='"0.1"', numeric_lexeme=None, numeric_encoding=None)
        )
        assert decode_fact_value(wire).raw_type == "string"
        with pytest.raises(AgentValidationError) as exc_meta:
            decode_fact_value({**wire, "numeric_lexeme": "0.1", "numeric_encoding": "agent-number-v1"})
        expect_code(exc_meta, "invalid_raw_type")
        with pytest.raises(AgentValidationError) as exc_shape:
            decode_fact_value({**wire, "raw_value": "0.1"})
        expect_code(exc_shape, "invalid_raw_type")

    def test_bool_and_null_raw_values_must_match_raw_type(self) -> None:
        wire = encode_fact_value(_fact(raw_type="bool", raw_value="true", numeric_lexeme=None, numeric_encoding=None))
        assert decode_fact_value(wire).raw_type == "bool"
        with pytest.raises(AgentValidationError) as exc_bool:
            decode_fact_value({**wire, "raw_value": "1"})
        expect_code(exc_bool, "invalid_raw_type")
        null_wire = encode_fact_value(
            _fact(raw_type="null", raw_value="null", numeric_lexeme=None, numeric_encoding=None)
        )
        assert decode_fact_value(null_wire).raw_type == "null"
        with pytest.raises(AgentValidationError) as exc_null:
            decode_fact_value({**null_wire, "raw_value": '"null"'})
        expect_code(exc_null, "invalid_raw_type")

    def test_xlsx_meta_must_be_complete_and_non_empty(self) -> None:
        # I31②：空对象或缺键 meta 不再被浅 JSON 校验放过。
        wire = encode_fact_value(_fact())
        for meta in ("{}", json_doc({"schema_version": "1"})):
            with pytest.raises(AgentValidationError) as exc:
                decode_fact_value({**wire, "xlsx_cell_meta": meta})
            expect_code(exc, "missing_field")
        base = to_wire_dict(XlsxCellMeta(sheet="Sheet1", cell="A1"))
        with pytest.raises(AgentValidationError) as exc_empty:
            decode_fact_value({**wire, "xlsx_cell_meta": json_doc({**base, "sheet": ""})})
        expect_code(exc_empty, "invalid_identity")

    def test_xlsx_meta_rejects_extra_keys(self) -> None:
        # I31④：meta 额外键拒绝。
        wire = encode_fact_value(_fact())
        base = to_wire_dict(XlsxCellMeta(sheet="Sheet1", cell="A1"))
        with pytest.raises(AgentValidationError) as exc:
            decode_fact_value({**wire, "xlsx_cell_meta": json_doc({**base, "total": 1})})
        expect_code(exc, "unknown_field")

    def test_xlsx_meta_cache_record_closed_by_type(self) -> None:
        # I31③：错 encoding / 错内层 type 的缓存载荷拒绝。
        wire = encode_fact_value(_fact())
        meta = to_wire_dict(
            XlsxCellMeta(
                sheet="Sheet1",
                cell="C3",
                cache_present=True,
                cache_raw_type="json_decimal",
                cache_raw_value='{"numeric_encoding":"agent-number-v1","raw_type":"json_decimal",'
                '"lexeme":"2.50","exact_decimal":"2.50"}',
            )
        )
        with pytest.raises(AgentValidationError) as exc_encoding:
            decode_fact_value(
                {
                    **wire,
                    "xlsx_cell_meta": json_doc(
                        {
                            **meta,
                            "cache_raw_value": '{"numeric_encoding":"plain","raw_type":"json_decimal",'
                            '"lexeme":"2.50","exact_decimal":"2.50"}',
                        }
                    ),
                }
            )
        expect_code(exc_encoding, "invalid_number")
        with pytest.raises(AgentValidationError) as exc_type:
            decode_fact_value({**wire, "xlsx_cell_meta": json_doc({**meta, "cache_raw_type": "json_integer"})})
        expect_code(exc_type, "invalid_number")
        with pytest.raises(AgentValidationError) as exc_unknown_type:
            decode_fact_value({**wire, "xlsx_cell_meta": json_doc({**meta, "cache_raw_type": "float"})})
        expect_code(exc_unknown_type, "invalid_raw_type")

    def test_xlsx_meta_cache_flags_closed(self) -> None:
        # I31③：cache flags 与缓存载荷互相矛盾即拒绝。
        wire = encode_fact_value(_fact())
        meta = to_wire_dict(
            XlsxCellMeta(
                sheet="Sheet1",
                cell="C3",
                cache_present=True,
                cache_raw_type="json_decimal",
                cache_raw_value='{"numeric_encoding":"agent-number-v1","raw_type":"json_decimal",'
                '"lexeme":"2.50","exact_decimal":"2.50"}',
            )
        )
        with pytest.raises(AgentValidationError) as exc_absent:
            decode_fact_value({**wire, "xlsx_cell_meta": json_doc({**meta, "cache_present": False})})
        expect_code(exc_absent, "invalid_cache_state")
        with pytest.raises(AgentValidationError) as exc_unverified_presence:
            decode_fact_value({**wire, "xlsx_cell_meta": json_doc({**meta, "cache_present": None})})
        expect_code(exc_unverified_presence, "invalid_cache_state")
        base = to_wire_dict(XlsxCellMeta(sheet="Sheet1", cell="A1", cache_present=False, cache_applicable=True))
        with pytest.raises(AgentValidationError) as exc_applicable:
            decode_fact_value({**wire, "xlsx_cell_meta": json_doc(base)})
        expect_code(exc_applicable, "invalid_cache_state")

    def test_unverified_closure_cannot_keep_program_verified(self) -> None:
        # I31⑥ / R21：闭合失败即拒绝，program_verified 不随伪内层值/空 meta 幸存。
        wire = encode_fact_value(_fact(verification=VerificationState.PROGRAM_VERIFIED))
        with pytest.raises(AgentValidationError) as exc_value:
            decode_fact_value({**wire, "raw_value": "0.1"})
        expect_code(exc_value, "invalid_number")
        with pytest.raises(AgentValidationError) as exc_meta:
            decode_fact_value({**wire, "xlsx_cell_meta": "{}"})
        expect_code(exc_meta, "missing_field")

    @staticmethod
    def _numeric_cache(cache_type: str, lexeme: str, exact: str) -> str:
        return json_doc(
            {
                "numeric_encoding": "agent-number-v1",
                "raw_type": cache_type,
                "lexeme": lexeme,
                "exact_decimal": exact,
            }
        )

    def test_conflicting_numeric_cache_payload_rejected(self) -> None:
        # I68：同一单元格的 raw_value 与数值缓存互为矛盾承载时，不得标 program_verified。
        wire = encode_fact_value(_fact())
        for cache_type, cache_value in (
            ("json_decimal", self._numeric_cache("json_decimal", "2.50", "2.50")),
            ("json_integer", self._numeric_cache("json_integer", "1", "1")),
            ("string", '{"raw_type":"string","lexeme":"0.1"}'),
        ):
            meta = to_wire_dict(
                XlsxCellMeta(
                    sheet="Sheet1",
                    cell="C3",
                    cache_present=True,
                    cache_raw_type=cache_type,
                    cache_raw_value=cache_value,
                )
            )
            with pytest.raises(AgentValidationError) as exc:
                decode_fact_value({**wire, "xlsx_cell_meta": json_doc(meta)})
            expect_code(exc, "invalid_number")

    def test_matching_numeric_cache_payload_accepted(self) -> None:
        wire = encode_fact_value(_fact())
        meta = to_wire_dict(
            XlsxCellMeta(
                sheet="Sheet1",
                cell="C3",
                cache_present=True,
                cache_raw_type="json_decimal",
                cache_raw_value=self._numeric_cache("json_decimal", "0.1", "0.1"),
                cache_verification=VerificationState.PROGRAM_VERIFIED,
            )
        )
        decoded = decode_fact_value({**wire, "xlsx_cell_meta": json_doc(meta)})
        assert decoded.xlsx_cell_meta == json_doc(meta)
        assert decoded.numeric_lexeme == "0.1"

    def test_cache_verification_without_payload_rejected(self) -> None:
        # R21：缓存无载荷却宣称 program_verified 即拒绝，不把未知伪装成已核。
        wire = encode_fact_value(_fact())
        meta = to_wire_dict(
            XlsxCellMeta(
                sheet="Sheet1",
                cell="C3",
                cache_present=False,
                cache_verification=VerificationState.PROGRAM_VERIFIED,
            )
        )
        with pytest.raises(AgentValidationError) as exc:
            decode_fact_value({**wire, "xlsx_cell_meta": json_doc(meta)})
        expect_code(exc, "invalid_cache_state")


class TestSourceReadAndEvidenceLinkCodec:
    def test_source_read_roundtrip(self) -> None:
        read = SourceRead(
            source=_source_ref(),
            selected_range=_selected_range(),
            read_state=ReadState.PARTIAL,
            body_text="第一段\n第二段",
            structured_blocks=(json_doc({"rows": [["a", "b"]]}),),
            facts=(_fact(),),
            coverage="limited",
            verification=VerificationState.UNVERIFIED,
            limitations=(RangeLimitation(code="unread_pages", message="第 2 页未读"),),
            available=True,
            missing_reason=None,
        )
        wire = encode_source_read(read)
        assert decode_source_read(wire) == read

    def test_source_read_rejects_invalid_coverage_and_block_shape(self) -> None:
        wire = encode_source_read(
            SourceRead(
                source=_source_ref(),
                selected_range=_selected_range(),
                read_state=ReadState.READY,
                body_text=None,
                structured_blocks=(),
                facts=(),
                coverage="full",
                verification=VerificationState.UNVERIFIED,
                limitations=(),
                available=False,
                missing_reason="source_missing",
            )
        )
        with pytest.raises(AgentValidationError) as exc_cov:
            decode_source_read({**wire, "coverage": "partial"})
        expect_code(exc_cov, "invalid_coverage")
        with pytest.raises(AgentValidationError) as exc_block:
            decode_source_read({**wire, "structured_blocks": [{"rows": []}]})
        assert exc_block.value.code in {"invalid_type", "invalid_identity"}

    def _read_wire(self) -> Any:
        return encode_source_read(
            SourceRead(
                source=_source_ref(),
                selected_range=_selected_range(),
                read_state=ReadState.READY,
                body_text=None,
                structured_blocks=(),
                facts=(),
                coverage="full",
                verification=VerificationState.UNVERIFIED,
                limitations=(),
                available=True,
                missing_reason=None,
            )
        )

    def test_selected_range_selection_must_match_source_locator(self) -> None:
        # all 与 limited 是互斥语义：读取结果的冗余 selection 不得互当
        wire = self._read_wire()
        locator = {
            **wire["source"]["locator"],
            "selection": {"kind": "limited", "ranges": [{"kind": "pages", "start": 1, "end": 1}]},
        }
        with pytest.raises(AgentValidationError) as exc:
            decode_source_read({**wire, "source": {**wire["source"], "locator": locator}})
        expect_code(exc, "kind_mismatch")

    def test_accepted_version_must_equal_source_version(self) -> None:
        wire = self._read_wire()
        selected_range = {**wire["selected_range"], "accepted_limited_version": "2"}
        with pytest.raises(AgentValidationError) as exc:
            decode_source_read({**wire, "selected_range": selected_range})
        expect_code(exc, "invalid_accepted_version")

    def test_evidence_link_supports_claim_unknown_is_null_not_false(self) -> None:
        link = EvidenceLink(
            claim_id="claim-1",
            source_id="src-1",
            source_version="1",
            locator=_locator(),
            source_exists=True,
            supports_claim=None,
            verification=VerificationState.UNKNOWN,
            verified_at=None,
            limitations=(),
        )
        wire = encode_evidence_link(link)
        assert decode_evidence_link(wire) == link
        with pytest.raises(AgentValidationError) as exc:
            decode_evidence_link({**wire, "supports_claim": 0})
        expect_code(exc, "invalid_type")
