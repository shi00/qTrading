"""角色定义契约（RoleDefinition）单元测试。

覆盖：wire 往返、role_id 语法与预置 id 拒绝、schema_version 约束、额外键拒绝，
以及 display_name/base/status/role_version/capability_mode/model_connection_id 各校验分支。
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from core.agent_types import (
    AgentValidationError,
    RoleDefinition,
    decode_role_definition,
    validate_role_definition,
)


def _wire(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schema_version": 1,
        "role_id": "risk_reviewer",
        "display_name": "风险复核",
        "base": "sub",
        "status": "active",
        "role_version": "r1",
        "prompt_body": "只核对风险，不参与结论。",
        "model_connection_id": None,
        "capability_mode": "explicit",
        "created_at": "2026-11-26T00:00:00Z",
        "updated_at": "2026-11-26T00:00:00Z",
        "updated_by": "local",
    }
    base.update(overrides)
    return base


def test_round_trip() -> None:
    definition = decode_role_definition(_wire())
    assert definition == RoleDefinition(
        schema_version=1,
        role_id="risk_reviewer",
        display_name="风险复核",
        base="sub",
        status="active",
        role_version="r1",
        prompt_body="只核对风险，不参与结论。",
        model_connection_id=None,
        capability_mode="explicit",
        created_at="2026-11-26T00:00:00Z",
        updated_at="2026-11-26T00:00:00Z",
        updated_by="local",
    )
    assert decode_role_definition(definition.wire()) == definition


def test_round_trip_with_model_connection_id() -> None:
    definition = decode_role_definition(_wire(model_connection_id="conn-local"))
    assert definition.model_connection_id == "conn-local"
    assert decode_role_definition(definition.wire()) == definition


@pytest.mark.parametrize("role_id", ["Main", "1abc", "a.b", "a@b", "ab", "main\n", "risk_reviewer\n"])
def test_rejects_invalid_role_id(role_id: str) -> None:
    with pytest.raises(AgentValidationError) as exc:
        decode_role_definition(_wire(role_id=role_id))
    assert type(exc.value) is AgentValidationError
    assert exc.value.code == "invalid_role_id"
    assert exc.value.field_path == "$.role_definition.role_id"


def test_rejects_unknown_schema_version() -> None:
    with pytest.raises(AgentValidationError) as exc:
        decode_role_definition(_wire(schema_version=2))
    assert type(exc.value) is AgentValidationError
    assert exc.value.code == "unsupported_schema_version"
    assert exc.value.field_path == "$.role_definition.schema_version"


def test_rejects_extra_key() -> None:
    wire = _wire()
    wire["extra"] = 1
    with pytest.raises(AgentValidationError) as exc:
        decode_role_definition(wire)
    assert type(exc.value) is AgentValidationError
    assert exc.value.code == "unknown_field"
    assert exc.value.field_path == "$.role_definition.extra"


def test_rejects_reserved_preset_id() -> None:
    with pytest.raises(AgentValidationError) as exc:
        decode_role_definition(_wire(role_id="main"))
    assert type(exc.value) is AgentValidationError
    assert exc.value.code == "invalid_role_id"
    assert exc.value.field_path == "$.role_definition.role_id"


@pytest.mark.parametrize(
    ("overrides", "code", "field_path"),
    [
        ({"display_name": ""}, "invalid_identity", "$.role_definition.display_name"),
        ({"display_name": "长" * 65}, "invalid_display_name", "$.role_definition.display_name"),
        ({"base": "main"}, "invalid_base", "$.role_definition.base"),
        ({"status": "draft"}, "invalid_status", "$.role_definition.status"),
        ({"role_version": "1"}, "invalid_role_version", "$.role_definition.role_version"),
        ({"role_version": "r0"}, "invalid_role_version", "$.role_definition.role_version"),
        ({"role_version": "r1a"}, "invalid_role_version", "$.role_definition.role_version"),
        ({"role_version": "r1\n"}, "invalid_role_version", "$.role_definition.role_version"),
        ({"capability_mode": "all"}, "invalid_capability_mode", "$.role_definition.capability_mode"),
        ({"model_connection_id": "  "}, "invalid_model_connection_id", "$.role_definition.model_connection_id"),
        ({"schema_version": True}, "invalid_type", "$.role_definition.schema_version"),
    ],
)
def test_rejects_invalid_field(overrides: dict[str, object], code: str, field_path: str) -> None:
    with pytest.raises(AgentValidationError) as exc:
        decode_role_definition(_wire(**overrides))
    assert type(exc.value) is AgentValidationError
    assert exc.value.code == code
    assert exc.value.field_path == field_path


@pytest.mark.parametrize(
    ("field_name", "value", "field_path"),
    [
        ("display_name", 123, "$.role_definition.display_name"),
        ("model_connection_id", 123, "$.role_definition.model_connection_id"),
        ("schema_version", True, "$.role_definition.schema_version"),
    ],
)
def test_validate_rejects_invalid_types_on_direct_construction(field_name: str, value: object, field_path: str) -> None:
    """绕过 ``decode`` 直接构造的非法类型 → ``invalid_type``，而非 ``TypeError``。"""

    definition = decode_role_definition(_wire())
    broken = replace(definition, **{field_name: value})

    with pytest.raises(AgentValidationError) as exc:
        validate_role_definition(broken)

    assert type(exc.value) is AgentValidationError
    assert exc.value.code == "invalid_type"
    assert exc.value.field_path == field_path
