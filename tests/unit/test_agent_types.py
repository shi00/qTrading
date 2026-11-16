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
    decode_exact_number,
    decode_json_doc,
    encode_exact_number,
    frozen_command_input_digest,
    is_digest,
    json_doc,
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


class TestFrozenCommandInputDigest:
    """命令冻结输入摘要（检视 D05：正文 + manifest 必须同覆盖）。"""

    _MANIFEST_A = '{"purpose":"concept","schema_version":1,"text":"研究三家公司"}'
    _MANIFEST_B = '{"purpose":"decide","schema_version":1,"text":"研究三家公司"}'

    def test_same_kind_text_manifest_is_stable(self) -> None:
        first = frozen_command_input_digest("accept", "研究三家公司", self._MANIFEST_A)
        second = frozen_command_input_digest("accept", "研究三家公司", self._MANIFEST_A)
        assert first == second
        assert is_digest(first)

    def test_same_text_different_manifest_changes_digest(self) -> None:
        base = frozen_command_input_digest("accept", "研究三家公司", self._MANIFEST_A)
        other = frozen_command_input_digest("accept", "研究三家公司", self._MANIFEST_B)
        assert base != other

    def test_different_text_changes_digest(self) -> None:
        base = frozen_command_input_digest("revise", "研究三家公司", self._MANIFEST_A)
        other = frozen_command_input_digest("revise", "研究两家公司", self._MANIFEST_A)
        assert base != other

    def test_command_kind_is_part_of_digest(self) -> None:
        accept = frozen_command_input_digest("accept", "研究三家公司", self._MANIFEST_A)
        revise = frozen_command_input_digest("revise", "研究三家公司", self._MANIFEST_A)
        assert accept != revise

    def test_legacy_digest_covers_text_only_and_differs(self) -> None:
        legacy = legacy_command_input_digest("研究三家公司")
        assert legacy == sha256_hex("研究三家公司")
        assert legacy != frozen_command_input_digest("accept", "研究三家公司", self._MANIFEST_A)
