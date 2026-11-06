"""C01：Agent 公共契约（§5）行为测试。

覆盖规范编码、严格解码、请求摘要与技能有效集；不冒充 DB/服务/模型行为。
"""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from core.agent_types import (
    AgentValidationError,
    ResourceRef,
    decode_json_doc,
    effective_skills,
    json_doc,
    request_digest,
    validate_request,
)
from tests.builders.agent import make_request


def test_json_doc_is_canonical_and_unicode_preserving():
    assert json_doc({"b": 1, "a": "中文"}) == '{"a":"中文","b":1}'
    assert json_doc((1, 2)) == "[1,2]"
    assert json_doc(datetime(2026, 4, 30, 12, 0, tzinfo=UTC)) == '"2026-04-30T12:00:00.000000Z"'
    assert json_doc(date(2026, 4, 30)) == '"2026-04-30"'
    assert json_doc(UUID("10000000-0000-0000-0000-000000000001")) == '"10000000-0000-0000-0000-000000000001"'


def test_json_doc_rejects_non_finite_numbers():
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(AgentValidationError) as error:
            json_doc({"amount": value})
        assert error.value.code == "invalid_json"
        assert error.value.field_path == "$.amount"


def test_decode_json_doc_rejects_duplicate_keys_at_both_levels():
    with pytest.raises(AgentValidationError) as error:
        decode_json_doc('{"amount":1,"amount":2}')
    assert error.value.code == "duplicate_key"
    assert error.value.field_path == "$.amount"

    with pytest.raises(AgentValidationError) as error:
        decode_json_doc('{"outer":{"x":1,"x":2}}')
    assert error.value.field_path == "$.x"


def test_decode_json_doc_rejects_non_finite_literals_and_bad_json():
    with pytest.raises(AgentValidationError) as error:
        decode_json_doc('{"amount":NaN}')
    assert error.value.code == "non_finite_number"

    with pytest.raises(AgentValidationError) as error:
        decode_json_doc('{"amount":')
    assert error.value.code == "invalid_json"


def test_decode_json_doc_keeps_decimal_precision():
    assert decode_json_doc('{"price":12.3400}') == {"price": Decimal("12.3400")}


def test_request_digest_is_stable_and_text_sensitive():
    request = make_request()
    assert request_digest(request) == request_digest(replace(request))
    assert request_digest(request) != request_digest(replace(request, text="新的目标"))


def test_validate_request_accepts_the_builder_and_rejects_unknown_enum():
    validate_request(make_request())

    with pytest.raises(AgentValidationError) as error:
        validate_request(replace(make_request(), intent_kind="unknown"))
    assert error.value.code == "invalid_enum"
    assert error.value.field_path == "intent_kind"


def test_validate_request_distinguishes_nullable_as_of():
    discuss = replace(make_request(), as_of=None, as_of_basis="not_applicable")
    validate_request(discuss)

    with pytest.raises(AgentValidationError) as error:
        validate_request(replace(make_request(), as_of=None, mode="execute"))
    assert error.value.field_path == "as_of"


def test_dtos_are_immutable():
    with pytest.raises(FrozenInstanceError):
        make_request().text = "改写"  # type: ignore[misc]  # 验证 frozen 结构在运行期拒绝赋值


def test_effective_skills_is_union_for_main_and_defaults_only_for_child():
    a = ResourceRef(UUID("10000000-0000-0000-0000-000000000001"), 1)
    b = ResourceRef(UUID("10000000-0000-0000-0000-000000000002"), 1)
    c = ResourceRef(UUID("10000000-0000-0000-0000-000000000003"), 1)

    assert effective_skills((a,), (c,), enabled=(a, c), child=False) == (a, c)
    assert effective_skills((b,), (c,), enabled=(b,), child=True) == (b,)

    with pytest.raises(AgentValidationError) as error:
        effective_skills((a,), (c,), enabled=(a,), child=False)
    assert error.value.field_path == "manual_skills"

    with pytest.raises(AgentValidationError) as error:
        effective_skills((c,), (), enabled=(b,), child=True)
    assert error.value.field_path == "default_skills"


def test_effective_skills_dedupes_and_rejects_revision_conflicts():
    a1 = ResourceRef(UUID("10000000-0000-0000-0000-000000000001"), 1)
    a2 = ResourceRef(UUID("10000000-0000-0000-0000-000000000001"), 2)

    assert effective_skills((a1, a1), (), enabled=(a1,), child=False) == (a1,)
    assert effective_skills((a1,), (a1,), enabled=(a1,), child=False) == (a1,)

    with pytest.raises(AgentValidationError) as error:
        effective_skills((a1,), (), enabled=(a1, a2), child=False)
    assert error.value.code == "identity_conflict"
    assert error.value.field_path == "enabled"
