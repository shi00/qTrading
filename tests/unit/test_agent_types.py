"""B-01 core/agent_types.py 公共合同单元测试（设计正本 §3.1/§3.2、V04 数值编码）。

覆盖：CanonicalJson 编解码（键排序/无空白/禁 NaN/拒重复键/精确数值）、
stable_id 稳定身份、Digest/Identity/Revision/Sequence 校验基元、
状态枚举 wire 拼写、agent-number-v1 精确数值编码往返。
"""

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, cast

import pytest

from core.agent_types import (
    AcceptRequest,
    AgentValidationError,
    CanonicalJson,
    CommitState,
    EffectState,
    ExactNumber,
    Mode,
    Purpose,
    ReadState,
    SourceKind,
    VerificationState,
    accept_intent_digest,
    decode_exact_number,
    decode_json_doc,
    derive_accept_identity,
    encode_exact_number,
    frozen_command_input_digest,
    frozen_manifest_inconsistency,
    is_digest,
    json_doc,
    legacy_accept_intent_digest,
    legacy_command_input_digest,
    parse_preserving_numbers,
    require_identity,
    require_revision,
    require_sequence,
    sha256_hex,
    stable_id,
)


def code_of(excinfo: pytest.ExceptionInfo[AgentValidationError]) -> str:
    return excinfo.value.code


def decode_doc(doc: str) -> dict[str, Any]:
    """cast 辅助：decode_json_doc 契约返回 object，测试按对象字面量取值。"""

    return cast(dict[str, Any], decode_json_doc(doc))


def parse_doc(doc: str) -> dict[str, Any]:
    """cast 辅助：parse_preserving_numbers 契约返回 object，测试按对象字面量取值。"""

    return cast(dict[str, Any], parse_preserving_numbers(doc))


class _ExplodingDecimal(Decimal):
    """I09 探针：若定点格式化在长度拒绝前被调用，立即失败（证明未产生巨串）。"""

    def __format__(self, spec: str) -> str:
        raise AssertionError("over-limit decimal must be rejected before format")


# ---------------------------------------------------------------------------
# CanonicalJson（§3.1）：编码
# ---------------------------------------------------------------------------


class TestJsonDocEncode:
    def test_canonical_form_sorted_keys_no_whitespace(self) -> None:
        assert json_doc({"b": 1, "a": 2}) == '{"a":2,"b":1}'

    def test_unicode_not_escaped(self) -> None:
        assert json_doc({"名": "值"}) == '{"名":"值"}'

    def test_tuple_becomes_array(self) -> None:
        assert json_doc((1, 2, 3)) == "[1,2,3]"

    def test_nested_dataclass_becomes_object(self) -> None:
        @dataclass(frozen=True, slots=True)
        class Item:
            name: str
            count: int

        assert json_doc(Item(name="x", count=2)) == '{"count":2,"name":"x"}'

    def test_decimal_serialized_as_exact_decimal_string(self) -> None:
        assert json_doc({"v": Decimal("0.1")}) == '{"v":"0.1"}'
        # 0.100000000000000005 不经 float 丢失
        assert json_doc({"v": Decimal("0.100000000000000005")}) == '{"v":"0.100000000000000005"}'

    def test_float_rejected_must_preencode_agent_number(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            json_doc({"v": 0.1})
        assert code_of(exc) == "invalid_type"

    def test_bool_and_none_and_empty_string_distinct(self) -> None:
        doc = json_doc({"a": None, "b": False, "c": "", "d": 0})
        decoded = decode_doc(doc)
        assert decoded == {"a": None, "b": False, "c": "", "d": 0}
        # None / 空 / 0 / False 保不同含义（§3.1 缺失值）
        assert json_doc([None, False, "", 0]) == '[null,false,"",0]'

    def test_non_str_dict_key_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            json_doc({1: "x"})
        assert code_of(exc) == "invalid_json"

    def test_unsupported_type_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            json_doc({"v": {1, 2}})
        assert code_of(exc) == "invalid_json"

    def test_exact_number_serializes_to_agent_number_record(self) -> None:
        number = encode_exact_number("1e-1")
        doc = json_doc({"v": number})
        assert doc == (
            '{"v":{"exact_decimal":"0.1","lexeme":"1e-1",'
            '"numeric_encoding":"agent-number-v1","raw_type":"json_decimal"}}'
        )

    def test_result_is_valid_utf8_json_string(self) -> None:
        doc = json_doc({"k": "值"})
        assert isinstance(doc, str)
        assert json.loads(doc) == {"k": "值"}


# ---------------------------------------------------------------------------
# CanonicalJson（§3.1）：解码
# ---------------------------------------------------------------------------


class TestDecodeJsonDoc:
    def test_roundtrip_preserves_int_bool_none(self) -> None:
        decoded = decode_doc('{"a":1,"b":true,"c":null,"d":"x"}')
        assert decoded == {"a": 1, "b": True, "c": None, "d": "x"}

    def test_float_parsed_as_decimal_not_float(self) -> None:
        decoded = decode_doc('{"v":0.1}')
        assert isinstance(decoded["v"], Decimal)
        assert decoded["v"] == Decimal("0.1")

    def test_duplicate_key_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            decode_json_doc('{"a":1,"a":2}')
        assert code_of(exc) == "duplicate_key"

    def test_non_finite_rejected(self) -> None:
        for raw in ("NaN", "Infinity", "-Infinity"):
            with pytest.raises(AgentValidationError) as exc:
                decode_json_doc(f'{{"v":{raw}}}')
            assert code_of(exc) == "non_finite_number"

    def test_malformed_json_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            decode_json_doc('{"a":')
        assert code_of(exc) == "invalid_json"

    def test_non_string_input_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            decode_json_doc({"a": 1})  # type: ignore[arg-type]  # 故意传入非 str 验证拒绝路径
        assert code_of(exc) == "invalid_json"


# ---------------------------------------------------------------------------
# stable_id（§3.2）：kind + sha256(canonical_json([1, kind, *parts]))
# ---------------------------------------------------------------------------


class TestStableId:
    def test_deterministic_and_format(self) -> None:
        first = stable_id("intent", ("u1", "m", "r1"))
        second = stable_id("intent", ("u1", "m", "r1"))
        assert first == second
        kind, _, digest = first.partition("_")
        assert kind == "intent"
        assert is_digest(digest)

    def test_kind_and_parts_change_identity(self) -> None:
        base = stable_id("intent", ("u1", "m", "r1"))
        assert stable_id("command", ("u1", "m", "r1")) != base
        assert stable_id("intent", ("u1", "m", "r2")) != base
        # parts 顺序即身份（非集合）
        assert stable_id("intent", ("m", "u1", "r1")) != base

    def test_empty_part_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            stable_id("intent", ("u1", "", "r1"))
        assert code_of(exc) == "invalid_identity"


# ---------------------------------------------------------------------------
# 校验基元（§3.1/§3.2）
# ---------------------------------------------------------------------------


class TestValidationPrimitives:
    def test_sha256_hex_lowercase(self) -> None:
        digest = sha256_hex(b"abc")
        assert digest == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"

    def test_sha256_hex_accepts_str_as_utf8(self) -> None:
        assert sha256_hex("abc") == sha256_hex(b"abc")

    def test_is_digest_accepts_only_lowercase_64_hex(self) -> None:
        assert is_digest("a" * 64)
        assert not is_digest("A" * 64)
        assert not is_digest("a" * 63)
        assert not is_digest("g" * 64)
        assert not is_digest(123)  # type: ignore[arg-type]  # 故意传入非 str 验证拒绝路径

    def test_require_identity_rejects_empty_and_non_str(self) -> None:
        assert require_identity("u1", "$.user_scope") == "u1"
        with pytest.raises(AgentValidationError) as exc:
            require_identity("", "$.user_scope")
        assert code_of(exc) == "invalid_identity"
        with pytest.raises(AgentValidationError) as exc:
            require_identity(None, "$.user_scope")  # type: ignore[arg-type]  # 验证拒绝路径
        assert code_of(exc) == "invalid_identity"

    def test_require_identity_no_trim(self) -> None:
        # 正文原样保留，不 trim（§3.1）
        assert require_identity(" u1 ", "$.x") == " u1 "

    def test_require_revision_one_based_rejects_bool(self) -> None:
        assert require_revision(1, "$.revision") == 1
        with pytest.raises(AgentValidationError) as exc:
            require_revision(0, "$.revision")
        assert code_of(exc) == "invalid_range"
        with pytest.raises(AgentValidationError) as exc:
            require_revision(True, "$.revision")  # type: ignore[arg-type]  # bool 即 int 子类，须拒绝
        assert code_of(exc) == "invalid_type"
        with pytest.raises(AgentValidationError) as exc:
            require_revision(1.0, "$.revision")  # type: ignore[arg-type]  # 验证拒绝路径
        assert code_of(exc) == "invalid_type"

    def test_require_revision_allow_zero_for_expected_and_watermark(self) -> None:
        assert require_revision(0, "$.expected", allow_zero=True) == 0
        with pytest.raises(AgentValidationError) as exc:
            require_revision(-1, "$.expected", allow_zero=True)
        assert code_of(exc) == "invalid_range"

    def test_require_sequence_zero_based(self) -> None:
        assert require_sequence(0, "$.after_seq") == 0
        with pytest.raises(AgentValidationError) as exc:
            require_sequence(-1, "$.after_seq")
        assert code_of(exc) == "invalid_range"
        with pytest.raises(AgentValidationError) as exc:
            require_sequence(False, "$.after_seq")  # type: ignore[arg-type]  # bool 即 int 子类，须拒绝
        assert code_of(exc) == "invalid_type"


# ---------------------------------------------------------------------------
# 状态枚举 wire 拼写（§3.1）
# ---------------------------------------------------------------------------


class TestStateEnums:
    def test_read_state_values(self) -> None:
        assert {item.value for item in ReadState} == {
            "unread",
            "ready",
            "partial",
            "unavailable",
            "disabled",
            "unknown",
        }

    def test_verification_state_values(self) -> None:
        assert {item.value for item in VerificationState} == {
            "program_verified",
            "human_verified",
            "unverified",
            "unknown",
            "source_unavailable",
        }

    def test_source_kind_six_wire_values_no_alias(self) -> None:
        # 唯一 wire 拼写，无 message/direct_message 别名（§3.1）
        assert {item.value for item in SourceKind} == {
            "business_snapshot",
            "attachment",
            "user_message",
            "mcp_return",
            "result",
            "summary",
        }

    def test_commit_state_values(self) -> None:
        assert {item.value for item in CommitState} == {"committed", "not_committed", "commit_unknown"}

    def test_effect_state_values(self) -> None:
        assert {item.value for item in EffectState} == {"not_dispatched", "dispatched", "confirmed", "unknown"}

    def test_mode_values(self) -> None:
        # F21 三模式；直答是循环动作（LoopAction.ANSWER），不是模式。
        assert {item.value for item in Mode} == {"discuss", "plan", "execute"}

    def test_purpose_values(self) -> None:
        assert {item.value for item in Purpose} == {
            "concept",
            "end_of_day_research",
            "historical_decision",
            "post_hoc_explanation",
        }

    def test_enum_member_lookup_by_value(self) -> None:
        assert SourceKind("user_message") is SourceKind.USER_MESSAGE
        assert CommitState("commit_unknown") is CommitState.COMMIT_UNKNOWN

    def test_canonical_json_roundtrip_with_enum(self) -> None:
        doc = json_doc({"kind": SourceKind.ATTACHMENT, "state": ReadState.UNREAD})
        assert doc == '{"kind":"attachment","state":"unread"}'


# ---------------------------------------------------------------------------
# V04 agent-number-v1 精确数值编码（§3.3）
# ---------------------------------------------------------------------------


class TestExactNumberEncoding:
    def test_encode_integer_keeps_lexeme(self) -> None:
        assert encode_exact_number("42") == ExactNumber(raw_type="json_integer", lexeme="42", exact_decimal="42")

    def test_encode_decimal_preserves_full_precision(self) -> None:
        assert encode_exact_number("0.100000000000000005") == ExactNumber(
            raw_type="json_decimal", lexeme="0.100000000000000005", exact_decimal="0.100000000000000005"
        )

    def test_encode_exponent_keeps_lexeme_normalizes_exact(self) -> None:
        number = encode_exact_number("1e-1")
        assert number.lexeme == "1e-1"
        assert number.exact_decimal == "0.1"

    def test_encode_rejects_invalid_number_grammar(self) -> None:
        for bad in ("01", "1.", ".5", "1e", "+1", "1e+", " 1", "1 ", "0x10", ""):
            with pytest.raises(AgentValidationError) as exc:
                encode_exact_number(bad)
            assert code_of(exc) == "invalid_number"

    def test_encode_rejects_non_str(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            encode_exact_number(0.1)  # type: ignore[arg-type]  # 验证拒绝路径
        assert code_of(exc) == "invalid_number"

    def test_negative_and_zero_valid(self) -> None:
        assert encode_exact_number("-0").raw_type == "json_integer"
        assert encode_exact_number("-1.5e2").exact_decimal == "-150"

    def test_decode_roundtrip_exact_decimal(self) -> None:
        record = encode_exact_number("0.100000000000000005")
        value = decode_exact_number(record, "$.v")
        assert value == Decimal("0.100000000000000005")
        assert value != Decimal("0.1")

    def test_decode_1e1_and_0_1_lexeme_differs_value_equal(self) -> None:
        # 1e-1 与 0.1 词法不同而数值相等（§3.3 V04）
        assert encode_exact_number("1e-1").lexeme != encode_exact_number("0.1").lexeme
        assert decode_exact_number(encode_exact_number("1e-1"), "$") == decode_exact_number(
            encode_exact_number("0.1"), "$"
        )

    def test_decode_rejects_wrong_encoding_tag(self) -> None:
        # 未知编码标签拒绝：经 wire dict 直接构造（绕过 encode）
        wire = {
            "numeric_encoding": "other-v9",
            "raw_type": "json_decimal",
            "lexeme": "0.1",
            "exact_decimal": "0.1",
        }
        with pytest.raises(AgentValidationError) as exc:
            decode_exact_number(wire, "$.v")
        assert code_of(exc) == "invalid_number"

    def test_decode_rejects_lexeme_exact_mismatch(self) -> None:
        wire = {
            "numeric_encoding": "agent-number-v1",
            "raw_type": "json_decimal",
            "lexeme": "0.1",
            "exact_decimal": "0.2",
        }
        with pytest.raises(AgentValidationError) as exc:
            decode_exact_number(wire, "$.v")
        assert code_of(exc) == "invalid_number"

    def test_decode_rejects_missing_or_extra_keys(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            decode_exact_number({"raw_type": "json_integer", "lexeme": "1", "exact_decimal": "1"}, "$")
        assert code_of(exc) == "invalid_number"
        full = {
            "numeric_encoding": "agent-number-v1",
            "raw_type": "json_integer",
            "lexeme": "1",
            "exact_decimal": "1",
        }
        with pytest.raises(AgentValidationError) as exc:
            decode_exact_number({**full, "extra": 1}, "$")
        assert code_of(exc) == "invalid_number"

    def test_decode_rejects_wrong_raw_type(self) -> None:
        wire = {
            "numeric_encoding": "agent-number-v1",
            "raw_type": "json_string",
            "lexeme": "0.1",
            "exact_decimal": "0.1",
        }
        with pytest.raises(AgentValidationError) as exc:
            decode_exact_number(wire, "$.v")
        assert code_of(exc) == "invalid_number"

    def test_decode_rejects_non_str_fields(self) -> None:
        wire = {
            "numeric_encoding": "agent-number-v1",
            "raw_type": "json_integer",
            "lexeme": 1,
            "exact_decimal": "1",
        }
        with pytest.raises(AgentValidationError) as exc:
            decode_exact_number(wire, "$.v")
        assert code_of(exc) == "invalid_number"


class TestParsePreservingNumbers:
    def test_number_lexemes_preserved(self) -> None:
        parsed = parse_doc('{"a": 0.1, "b": 1e-1}')
        assert isinstance(parsed["a"], ExactNumber)
        assert parsed["a"].lexeme == "0.1"
        assert parsed["b"].lexeme == "1e-1"
        assert parsed["a"].exact_decimal == parsed["b"].exact_decimal == "0.1"

    def test_integer_lexeme_preserved(self) -> None:
        parsed = parse_doc('{"n": 42}')
        assert parsed["n"].raw_type == "json_integer"
        assert parsed["n"].lexeme == "42"

    def test_numeric_looking_string_stays_string(self) -> None:
        parsed = parse_doc('{"s": "0.1"}')
        assert parsed["s"] == "0.1"
        assert not isinstance(parsed["s"], ExactNumber)

    def test_nested_containers(self) -> None:
        parsed = parse_doc('{"list":[1,2.5],"deep":{"x":3e1}}')
        assert parsed["list"][0].raw_type == "json_integer"
        assert parsed["list"][1].lexeme == "2.5"
        assert parsed["deep"]["x"].exact_decimal == "30"

    def test_non_finite_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            parse_preserving_numbers('{"v": NaN}')
        assert code_of(exc) == "non_finite_number"

    def test_reserialization_roundtrip_keeps_lexemes(self) -> None:
        doc = '{"a": 0.1, "b": 1e-1, "s": "2.5"}'
        first = parse_doc(doc)
        reencoded = json_doc(first)
        second = parse_doc(reencoded)
        # 重读恢复 wire 记录（parse 不把普通对象自动当数值，wire 形状保持）
        assert second["a"] == {
            "numeric_encoding": "agent-number-v1",
            "raw_type": "json_decimal",
            "lexeme": "0.1",
            "exact_decimal": "0.1",
        }
        assert second["b"]["lexeme"] == "1e-1"
        assert second["s"] == "2.5"
        # wire 记录可精确解码
        assert decode_exact_number(second["a"], "$.a") == Decimal("0.1")
        assert decode_exact_number(second["b"], "$.b") == Decimal("0.1")

    def test_wire_record_shape_matches_design_sample(self) -> None:
        # 设计固定样例（§3.3 V04）
        record = encode_exact_number("0.100000000000000005")
        wire = json.loads(json_doc(record))
        assert wire == {
            "numeric_encoding": "agent-number-v1",
            "raw_type": "json_decimal",
            "lexeme": "0.100000000000000005",
            "exact_decimal": "0.100000000000000005",
        }


class TestFixedPointLengthGate:
    """I09：定点格式化在展开前按位数/指数推导长度，超限拒绝且不产生巨串。"""

    def test_huge_positive_exponent_rejected_before_format(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            encode_exact_number("1e10000000")
        assert code_of(exc) == "exact_decimal_length_exceeded"
        # 探针 Decimal 的 __format__ 会抛 AssertionError：未抛即证明未进入展开。
        with pytest.raises(AgentValidationError) as exc:
            json_doc({"v": _ExplodingDecimal("1E+10000000")})
        assert code_of(exc) == "exact_decimal_length_exceeded"

    def test_huge_negative_exponent_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            encode_exact_number("1e-10000000")
        assert code_of(exc) == "exact_decimal_length_exceeded"
        with pytest.raises(AgentValidationError) as exc:
            json_doc({"v": _ExplodingDecimal("1E-10000000")})
        assert code_of(exc) == "exact_decimal_length_exceeded"

    def test_boundary_exact_passes_one_over_rejected(self) -> None:
        # 正指数：1E+99999 → 100000 位（恰等上限）；1E+100000 → 超一位。
        assert encode_exact_number("1e99999").exact_decimal == "1" + "0" * 99_999
        with pytest.raises(AgentValidationError) as exc:
            encode_exact_number("1e100000")
        assert code_of(exc) == "exact_decimal_length_exceeded"
        # 负指数：1E-99998 → "0." + 99997 个 0 + "1" 共 100000 位；1E-99999 超一位。
        assert encode_exact_number("1e-99998").exact_decimal == "0." + "0" * 99_997 + "1"
        with pytest.raises(AgentValidationError) as exc:
            encode_exact_number("1e-99999")
        assert code_of(exc) == "exact_decimal_length_exceeded"

    def test_zero_with_huge_exponent_stays_short(self) -> None:
        # 零的正指数不展开（format 语义），不因指数巨大被误拒或展开。
        assert encode_exact_number("0e10000000").exact_decimal == "0"
        assert json_doc({"v": Decimal("0E-5")}) == '{"v":"0.00000"}'

    def test_non_finite_rejected(self) -> None:
        for value in (Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")):
            with pytest.raises(AgentValidationError) as exc:
                json_doc({"v": value})
            assert code_of(exc) == "non_finite_number"

    def test_invalid_lexeme_still_rejected(self) -> None:
        for bad in ("1e", "01", ".5"):
            with pytest.raises(AgentValidationError) as exc:
                encode_exact_number(bad)
            assert code_of(exc) == "invalid_number"
        # 词法合法但指数超出 Decimal 可承载范围：拒绝而非抛 ArithmeticError。
        with pytest.raises(AgentValidationError) as exc:
            encode_exact_number("1e999999999999999999999")
        assert code_of(exc) == "invalid_number"

    def test_normal_fixed_point_unchanged(self) -> None:
        cases = {
            "0": "0",
            "-0": "-0",
            "1.20": "1.20",
            "-1.5e2": "-150",
            "0.001": "0.001",
            "1e2": "100",
            "0.100000000000000005": "0.100000000000000005",
        }
        for lexeme, expected in cases.items():
            assert encode_exact_number(lexeme).exact_decimal == expected

    def test_parse_rejects_huge_exponent_document(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            parse_preserving_numbers('{"v": 1e10000000}')
        assert code_of(exc) == "exact_decimal_length_exceeded"


class TestFrozenCommandInputDigest:
    """命令冻结输入摘要（检视 D05：正文/manifest/期望版/node 必须同覆盖）。"""

    _MANIFEST_A = '{"purpose":"concept","schema_version":1,"text":"研究三家公司"}'
    _MANIFEST_B = '{"purpose":"decide","schema_version":1,"text":"研究三家公司"}'
    _EXPECTED = 0
    _NODE = "root"

    def test_same_kind_text_manifest_is_stable(self) -> None:
        first = frozen_command_input_digest(
            "accept", "研究三家公司", self._MANIFEST_A, expected_revision=self._EXPECTED, node_id=self._NODE
        )
        second = frozen_command_input_digest(
            "accept", "研究三家公司", self._MANIFEST_A, expected_revision=self._EXPECTED, node_id=self._NODE
        )
        assert first == second
        assert is_digest(first)

    def test_same_text_different_manifest_changes_digest(self) -> None:
        base = frozen_command_input_digest(
            "accept", "研究三家公司", self._MANIFEST_A, expected_revision=self._EXPECTED, node_id=self._NODE
        )
        other = frozen_command_input_digest(
            "accept", "研究三家公司", self._MANIFEST_B, expected_revision=self._EXPECTED, node_id=self._NODE
        )
        assert base != other

    def test_different_text_changes_digest(self) -> None:
        base = frozen_command_input_digest(
            "revise", "研究三家公司", self._MANIFEST_A, expected_revision=self._EXPECTED, node_id=self._NODE
        )
        other = frozen_command_input_digest(
            "revise", "研究两家公司", self._MANIFEST_A, expected_revision=self._EXPECTED, node_id=self._NODE
        )
        assert base != other

    def test_command_kind_is_part_of_digest(self) -> None:
        accept = frozen_command_input_digest(
            "accept", "研究三家公司", self._MANIFEST_A, expected_revision=self._EXPECTED, node_id=self._NODE
        )
        revise = frozen_command_input_digest(
            "revise", "研究三家公司", self._MANIFEST_A, expected_revision=self._EXPECTED, node_id=self._NODE
        )
        assert accept != revise

    def test_expected_revision_changes_digest(self) -> None:
        base = frozen_command_input_digest(
            "accept", "研究三家公司", self._MANIFEST_A, expected_revision=0, node_id=self._NODE
        )
        other = frozen_command_input_digest(
            "accept", "研究三家公司", self._MANIFEST_A, expected_revision=3, node_id=self._NODE
        )
        assert base != other

    def test_node_id_changes_digest(self) -> None:
        base = frozen_command_input_digest(
            "accept", "研究三家公司", self._MANIFEST_A, expected_revision=self._EXPECTED, node_id="root"
        )
        other = frozen_command_input_digest(
            "accept", "研究三家公司", self._MANIFEST_A, expected_revision=self._EXPECTED, node_id="node-2"
        )
        assert base != other

    def test_manifest_is_digested_as_canonical_bytes(self) -> None:
        """契约：manifest 为 CanonicalJson，按规范字节参与摘要（与 intent_digest 同源）。

        因此键序/空白不同的非规范字节判为异输入，而非在摘要内二次规范化——
        与 intent_digest 的判定保持一致，避免两分支结论互斥。
        """
        canonical = '{"purpose":"concept","text":"研究三家公司"}'
        reordered = '{ "text": "研究三家公司", "purpose": "concept" }'
        base = frozen_command_input_digest(
            "accept", "研究三家公司", canonical, expected_revision=self._EXPECTED, node_id=self._NODE
        )
        other = frozen_command_input_digest(
            "accept", "研究三家公司", reordered, expected_revision=self._EXPECTED, node_id=self._NODE
        )
        assert base != other

    def test_legacy_digest_covers_text_only_and_differs(self) -> None:
        legacy = legacy_command_input_digest("研究三家公司")
        assert legacy == sha256_hex("研究三家公司")
        assert legacy != frozen_command_input_digest(
            "accept", "研究三家公司", self._MANIFEST_A, expected_revision=self._EXPECTED, node_id=self._NODE
        )

    def test_revise_declaration_is_part_of_command_digest(self) -> None:
        """R2-I25：同键同正文但修订源不同 → 异输入（不得误判重放）。"""

        base = frozen_command_input_digest(
            "accept", "研究三家公司", self._MANIFEST_A, expected_revision=self._EXPECTED, node_id=self._NODE
        )
        declared = frozen_command_input_digest(
            "accept",
            "研究三家公司",
            self._MANIFEST_A,
            expected_revision=self._EXPECTED,
            node_id=self._NODE,
            revise_source_message_id="m3",
        )
        other_source = frozen_command_input_digest(
            "accept",
            "研究三家公司",
            self._MANIFEST_A,
            expected_revision=self._EXPECTED,
            node_id=self._NODE,
            revise_source_message_id="m2",
        )
        other_cutoff = frozen_command_input_digest(
            "accept",
            "研究三家公司",
            self._MANIFEST_A,
            expected_revision=self._EXPECTED,
            node_id=self._NODE,
            revise_source_message_id="m3",
            revise_causal_cutoff_message_id="m1",
        )
        assert len({base, declared, other_source, other_cutoff}) == 4

    def test_command_digest_without_revise_declaration_is_unchanged(self) -> None:
        """未声明修订时摘要保持既有 v1 形态（历史命令行重放兼容）。"""

        assert (
            frozen_command_input_digest(
                "accept", "研究三家公司", self._MANIFEST_A, expected_revision=self._EXPECTED, node_id=self._NODE
            )
            == "1d2f001f7e0df72e9c4a1188c0910692e46ecf3aa6ddafcb6d2ef88e8cb70d94"
        )


class TestAcceptIntentDigest:
    """受理意图摘要（R2-I14：同 intent 必须比较完整输入，不能只比 manifest 摘要）。"""

    _MANIFEST = json_doc(
        {
            "schema_version": 1,
            "source_module": "ui",
            "mode": "discuss",
            "purpose": "concept",
            "text": "研究三家公司",
        }
    )

    def _request(self, **overrides: Any) -> AcceptRequest:
        values: dict[str, Any] = {
            "user_scope": "u1",
            "session_id": "s1",
            "source_module": "ui",
            "business_request_id": "req-1",
            "client_command_id": "client-1",
            "expected_revision": 0,
            "node_id": "root",
            "workflow_id": "wf-1",
            "mode": Mode.DISCUSS,
            "purpose": Purpose.CONCEPT,
            "text": "研究三家公司",
            "manifest_payload": self._MANIFEST,
            "source_display_name": "用户消息",
        }
        values.update(overrides)
        return AcceptRequest(**values)

    def test_same_full_input_is_stable_across_command_keys(self) -> None:
        first = accept_intent_digest(self._request())
        second = accept_intent_digest(self._request(client_command_id="client-2"))
        assert first == second
        assert is_digest(first)

    def test_digest_is_not_manifest_only(self) -> None:
        """旧实现 = sha256(manifest)；新摘要必须覆盖 manifest 之外的受理输入。"""
        digest = accept_intent_digest(self._request())
        assert digest != sha256_hex(self._MANIFEST)
        assert digest != legacy_accept_intent_digest(self._MANIFEST)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("text", "研究两家公司"),
            ("mode", Mode.EXECUTE),
            ("purpose", Purpose.END_OF_DAY_RESEARCH),
            ("workflow_id", "wf-2"),
            ("node_id", "node-2"),
            ("session_id", "s2"),
            ("expected_revision", 3),
            ("source_display_name", "另一显示名"),
        ],
    )
    def test_any_input_field_change_changes_digest(self, field: str, value: object) -> None:
        base = accept_intent_digest(self._request())
        assert accept_intent_digest(self._request(**{field: value})) != base

    def test_comparison_refs_are_part_of_digest(self) -> None:
        base = accept_intent_digest(self._request())
        grouped = accept_intent_digest(self._request(comparison_group_ref="group-1", comparison_lane_ref="lane-1"))
        other_lane = accept_intent_digest(self._request(comparison_group_ref="group-1", comparison_lane_ref="lane-2"))
        assert base != grouped
        assert grouped != other_lane

    def test_manifest_change_changes_digest(self) -> None:
        other = json_doc({"schema_version": 1, "source_module": "ui", "text": "研究三家公司", "purpose": "decide"})
        assert accept_intent_digest(self._request(manifest_payload=other)) != accept_intent_digest(self._request())

    def test_manifest_must_be_non_empty_text(self) -> None:
        for payload in ("", None, 1):
            with pytest.raises(AgentValidationError) as excinfo:
                accept_intent_digest(self._request(manifest_payload=payload))
            assert code_of(excinfo) == "invalid_value"

    def test_manifest_must_be_json_object(self) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            accept_intent_digest(self._request(manifest_payload="[]"))
        assert code_of(excinfo) == "invalid_type"
        with pytest.raises(AgentValidationError) as excinfo:
            accept_intent_digest(self._request(manifest_payload="not-json"))
        assert code_of(excinfo) == "invalid_json"

    def test_manifest_must_be_canonical_json(self) -> None:
        """非规范字节（空白/键序/重复键）不得与规范字节共享同一内容身份。"""
        with pytest.raises(AgentValidationError) as excinfo:
            accept_intent_digest(self._request(manifest_payload='{ "schema_version": 1 }'))
        assert code_of(excinfo) == "non_canonical_json"
        with pytest.raises(AgentValidationError) as excinfo:
            accept_intent_digest(self._request(manifest_payload='{"a":1,"a":2}'))
        assert code_of(excinfo) == "duplicate_key"

    def test_derive_accept_identity_uses_full_intent_digest(self) -> None:
        request = self._request()
        identity = derive_accept_identity(request)
        assert identity.intent_digest == accept_intent_digest(request)
        # 同 intent + 同 manifest 只换正文：旧实现视为重放（摘要相等），新实现必须不同。
        changed = derive_accept_identity(self._request(text="换一个问题"))
        assert changed.intent_digest != identity.intent_digest
        assert changed.source_digest != identity.source_digest

    def test_legacy_accept_intent_digest_is_manifest_digest(self) -> None:
        """旧行公式（仅 manifest 字节）只用于读历史行，且与新摘要不同。"""
        assert legacy_accept_intent_digest(self._MANIFEST) == sha256_hex(self._MANIFEST)
        assert legacy_accept_intent_digest(self._MANIFEST) != accept_intent_digest(self._request())

    def test_revise_declaration_is_part_of_intent_digest(self) -> None:
        """R2-I25：修订源/截点是受理输入的一部分，异源不得判为重放。"""

        base = accept_intent_digest(self._request())
        declared = accept_intent_digest(self._request(revise_source_message_id="m3"))
        other_source = accept_intent_digest(self._request(revise_source_message_id="m2"))
        other_cutoff = accept_intent_digest(
            self._request(revise_source_message_id="m3", revise_causal_cutoff_message_id="m1")
        )
        assert len({base, declared, other_source, other_cutoff}) == 4

    def test_intent_digest_without_revise_declaration_is_unchanged(self) -> None:
        """未声明修订时摘要保持既有 v2 形态（历史意图行重放兼容）。"""

        assert (
            accept_intent_digest(self._request()) == "1e219a46e8eaf9eb9afd99a5184c2d705f42651c1117305b5ae5e0ee2c342ce0"
        )

    def test_cutoff_requires_revise_source(self) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            derive_accept_identity(self._request(revise_causal_cutoff_message_id="m2"))
        assert code_of(excinfo) == "missing_field"

    def test_revise_cutoff_equal_to_source_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            derive_accept_identity(self._request(revise_source_message_id="m3", revise_causal_cutoff_message_id="m3"))
        assert code_of(excinfo) == "invalid_value"


class TestFrozenManifestInconsistency:
    """R2-I15：冻结 manifest 必须与受理输入一致，比较组冻结段必须齐备。"""

    _MANIFEST = json_doc(
        {
            "schema_version": 1,
            "user_scope": "u1",
            "session_id": "s1",
            "source_module": "ui",
            "business_request_id": "req-1",
            "node_id": "root",
            "workflow_id": "wf-1",
            "mode": "discuss",
            "purpose": "concept",
            "text": "研究三家公司",
        }
    )

    def _request(self, **overrides: Any) -> AcceptRequest:
        values: dict[str, Any] = {
            "user_scope": "u1",
            "session_id": "s1",
            "source_module": "ui",
            "business_request_id": "req-1",
            "client_command_id": "client-1",
            "expected_revision": 1,
            "node_id": "root",
            "workflow_id": "wf-1",
            "mode": Mode.DISCUSS,
            "purpose": Purpose.CONCEPT,
            "text": "研究三家公司",
            "manifest_payload": self._MANIFEST,
            "source_display_name": "用户消息",
        }
        values.update(overrides)
        return AcceptRequest(**values)

    @staticmethod
    def _shared(**overrides: Any) -> dict[str, Any]:
        values: dict[str, Any] = {
            "text": "研究三家公司",
            "mode": "discuss",
            "purpose": "concept",
            "cutoff": None,
            "source_refs": [],
            "visible_history_message_ids": [],
            "visible_history_artifact_refs": [],
            "role_prompt_refs": [],
            "skill_refs": [],
            "mcp_scope_digest": "a" * 64,
            "output_requirements": {},
        }
        values.update(overrides)
        return values

    def _comparison_request(self, **shared_overrides: Any) -> AcceptRequest:
        manifest = json_doc(
            {
                "schema_version": 1,
                "group_ref": "group-1",
                "user_scope": "u1",
                "session_id": "s1",
                "source_module": "ui",
                "input_digest": "b" * 64,
                "shared": self._shared(**shared_overrides),
                "lanes": [],
            }
        )
        return self._request(
            manifest_payload=manifest,
            comparison_group_ref="group-1",
            comparison_lane_ref="lane-1",
        )

    def test_consistent_manifest_has_no_inconsistency(self) -> None:
        assert frozen_manifest_inconsistency(self._request()) is None

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("user_scope", "u2"),
            ("session_id", "s2"),
            ("source_module", "api"),
            ("business_request_id", "req-2"),
            ("node_id", "node-2"),
            ("workflow_id", "wf-2"),
            ("text", "另一份正文"),
            ("mode", "execute"),
            ("purpose", "decide"),
        ],
    )
    def test_declared_request_field_must_match(self, field: str, value: object) -> None:
        manifest = json_doc({**decode_doc(self._MANIFEST), field: value})
        assert frozen_manifest_inconsistency(self._request(manifest_payload=manifest)) == f"$.manifest_payload.{field}"

    def test_undeclared_fields_are_not_inferred(self) -> None:
        """未声明身份字段的 manifest 不做推断（旧行/最小 manifest 兼容）。"""
        minimal = json_doc({"schema_version": 1})
        assert frozen_manifest_inconsistency(self._request(manifest_payload=minimal)) is None

    def test_comparison_manifest_must_carry_full_frozen_sections(self) -> None:
        assert frozen_manifest_inconsistency(self._comparison_request()) is None
        manifest = decode_doc(self._comparison_request().manifest_payload)
        shared = dict(cast(dict[str, Any], manifest["shared"]))
        shared.pop("role_prompt_refs")
        broken = json_doc({**manifest, "shared": shared})
        assert frozen_manifest_inconsistency(self._request(manifest_payload=broken)) == (
            "$.manifest_payload.shared.role_prompt_refs"
        )

    def test_comparison_shared_text_must_match_request(self) -> None:
        assert frozen_manifest_inconsistency(self._comparison_request(text="另一份正文")) == (
            "$.manifest_payload.shared.text"
        )

    def test_comparison_shared_must_be_object(self) -> None:
        manifest = json_doc({"schema_version": 1, "shared": []})
        assert frozen_manifest_inconsistency(self._request(manifest_payload=manifest)) == ("$.manifest_payload.shared")
