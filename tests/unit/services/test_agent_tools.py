"""工具白名单与完整 schema 程序校验（DD §10.3 / L1784）。

- 完整 schema 必须对象根、``properties`` 存在、``additionalProperties is False``；
- 名称非空唯一；``allowed_names`` 为空即零工具；
- 装配出的工具 stub 只 ``CallDeferred``，不做任何 IO。
"""

from __future__ import annotations

import pytest
from pydantic_ai import CallDeferred

from core.agent_types import AgentValidationError
from services.agent_service.tools import (
    ToolSpec,
    build_deferred_tools,
    validate_tool_schema,
    validate_tool_set,
)

pytestmark = pytest.mark.unit

_VALID_SCHEMA = {
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
    "additionalProperties": False,
}


def _spec(name: str = "search", schema: dict | None = None) -> ToolSpec:
    return ToolSpec(name=name, description="search tool", json_schema=schema or dict(_VALID_SCHEMA))


class TestValidateToolSchema:
    def test_valid_closed_object_schema_passes(self) -> None:
        validate_tool_schema(_spec())

    @pytest.mark.parametrize(
        "schema",
        [
            {"type": "object", "properties": {"q": {"type": "string"}}},
            {"type": "object", "properties": {"q": {"type": "string"}}, "additionalProperties": True},
            {"type": "array", "items": {"type": "string"}, "additionalProperties": False},
            {"type": "object", "additionalProperties": False},
            {"type": "object", "properties": {"q": {"type": 123}}, "additionalProperties": False},
        ],
    )
    def test_incomplete_or_invalid_schema_rejected(self, schema: dict) -> None:
        with pytest.raises(AgentValidationError) as exc:
            validate_tool_schema(_spec(schema=schema))
        assert exc.value.code == "invalid_schema"

    def test_empty_name_or_description_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            validate_tool_schema(ToolSpec(name="", description="d", json_schema=dict(_VALID_SCHEMA)))
        assert exc.value.code == "invalid_tool_field"
        with pytest.raises(AgentValidationError) as exc:
            validate_tool_schema(ToolSpec(name="t", description=" ", json_schema=dict(_VALID_SCHEMA)))
        assert exc.value.code == "invalid_tool_field"


class TestValidateToolSet:
    def test_filters_to_allowed_names(self) -> None:
        specs = (_spec("a"), _spec("b"))

        kept = validate_tool_set(specs, allowed_names=["a"])

        assert [spec.name for spec in kept] == ["a"]

    def test_empty_allowlist_is_empty_not_unrestricted(self) -> None:
        assert validate_tool_set((_spec("a"),), allowed_names=()) == ()

    def test_duplicate_name_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            validate_tool_set((_spec("a"), _spec("a")), allowed_names=["a"])
        assert exc.value.code == "duplicate_tool_name"

    def test_non_spec_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            validate_tool_set((object(),), allowed_names=["a"])  # type: ignore[arg-type]
        assert exc.value.code == "invalid_tool"


class TestBuildDeferredTools:
    def test_tools_are_sequential_and_names_preserved(self) -> None:
        tools = build_deferred_tools((_spec("a"), _spec("b")))

        assert [tool.name for tool in tools] == ["a", "b"]
        assert all(tool.sequential is True for tool in tools)
        assert all(tool.takes_ctx is True for tool in tools)

    async def test_stub_only_defers(self) -> None:
        tool = build_deferred_tools((_spec("a"),))[0]

        with pytest.raises(CallDeferred) as exc:
            await tool.function(None, query="x")
        assert exc.value.metadata == {"deferred_by": "qtrading.tools"}
