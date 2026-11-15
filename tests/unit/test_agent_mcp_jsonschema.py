"""MCP 工具参数闭合校验（E-02-5）：真实 jsonschema 适配器契约。

覆盖：合法参数放行；缺必填、额外字段、类型/枚举越界分别拒绝并给出参数路径；
schema 不可解析/非法/非对象一律 ``invalid_schema``（不把坏 schema 当放行）。
"""

from __future__ import annotations

import pytest

from core.agent_types import AgentValidationError
from services.agent_service.mcp_jsonschema import JsonSchemaToolValidator

pytestmark = pytest.mark.unit

_SCHEMA = """
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "properties": {
    "text": {"type": "string"},
    "limit": {"type": "integer", "minimum": 1},
    "mode": {"enum": ["fast", "full"]}
  },
  "required": ["text"],
  "additionalProperties": false
}
"""


def _validate(schema: str, arguments: object) -> None:
    JsonSchemaToolValidator().validate(schema, arguments)  # type: ignore[arg-type]  # 越界形态须被拒绝


class TestJsonSchemaToolValidator:
    def test_valid_arguments_pass(self) -> None:
        _validate(_SCHEMA, {"text": "hi", "limit": 2, "mode": "fast"})

    def test_missing_required_reports_path(self) -> None:
        with pytest.raises(AgentValidationError) as error:
            _validate(_SCHEMA, {"limit": 2})
        assert error.value.code == "invalid_arguments"
        assert error.value.field_path == "$.mcp.arguments"

    def test_additional_property_rejected(self) -> None:
        # jsonschema 对 additionalProperties 的失败定位在对象级（属性名只在 message 中），
        # 本适配器只承诺稳定错误码 + 可用 JSON 路径，不杜撰属性级路径。
        with pytest.raises(AgentValidationError) as error:
            _validate(_SCHEMA, {"text": "hi", "extra": 1})
        assert error.value.code == "invalid_arguments"
        assert error.value.field_path == "$.mcp.arguments"

    def test_type_mismatch_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as error:
            _validate(_SCHEMA, {"text": 1})
        assert error.value.code == "invalid_arguments"
        assert error.value.field_path.endswith(".text")

    def test_enum_violation_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as error:
            _validate(_SCHEMA, {"text": "hi", "mode": "slow"})
        assert error.value.code == "invalid_arguments"
        assert error.value.field_path.endswith(".mode")

    def test_minimum_violation_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as error:
            _validate(_SCHEMA, {"text": "hi", "limit": 0})
        assert error.value.code == "invalid_arguments"
        assert error.value.field_path.endswith(".limit")

    def test_unparsable_schema_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as error:
            _validate("{not json", {"text": "hi"})
        assert error.value.code == "invalid_schema"

    def test_non_object_schema_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as error:
            _validate("[]", {"text": "hi"})
        assert error.value.code == "invalid_schema"

    def test_illegal_schema_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as error:
            _validate('{"type": 123}', {"text": "hi"})
        assert error.value.code == "invalid_schema"

    def test_arguments_must_be_object(self) -> None:
        with pytest.raises(AgentValidationError) as error:
            _validate(_SCHEMA, "text")
        assert error.value.code == "invalid_arguments"
