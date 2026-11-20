"""工具白名单装配与完整 schema 程序校验（DD §10.3 / L1784；F15）。

设计合同：

- **完整 schema 先程序校验，再装精确白名单**：``Tool.from_schema`` 不会自动保证
  schema 完整，故安装前必须逐条核验（JSON 合法、``Draft202012Validator.check_schema``
  通过、``type == "object"``、``properties`` 为对象、``additionalProperties is False``）。
  任一不满足即拒绝该工具，不裁剪 schema、不补默认字段；
- **stub 只 ``CallDeferred``**：模型回合内不执行任何 IO；实际读取由工具循环在
  授权/派发门之后执行（:mod:`services.agent_service.tool_loop`）；
- **空白名单确实是空**：``allowed_names`` 为空或与服务工具无交集时返回空元组，
  由调用方装零工具，不解释为「无限制」（R21）。

不在本模块：授权、派发、结果落库、MCP 连接。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from jsonschema.exceptions import SchemaError
from jsonschema.validators import Draft202012Validator
from pydantic_ai import CallDeferred, Tool
from pydantic_ai.tools import RunContext

from core.agent_types import AgentValidationError

__all__ = [
    "ToolSpec",
    "build_deferred_tools",
    "validate_tool_schema",
    "validate_tool_set",
]

_SCHEMA_PATH: Final[str] = "$.tool.schema"
_NAME_PATH: Final[str] = "$.tool.name"
_DESCRIPTION_PATH: Final[str] = "$.tool.description"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """一条待装工具的完整定义（名称 + 说明 + 完整 JSON Schema）。"""

    name: str
    description: str
    json_schema: Mapping[str, Any]


def _require_text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise AgentValidationError("invalid_tool_field", path)
    return value


def validate_tool_schema(spec: ToolSpec) -> None:
    """完整 schema 程序校验（D2）；失败抛 ``AgentValidationError``，不放行半截 schema。

    ``check_schema`` 只证明 schema 自身合法，不证明「完整」；故额外要求对象根、
    ``properties`` 存在且 ``additionalProperties`` 显式为 ``False``——闭合参数是
    参数摘要与授权绑定的前提。
    """

    _require_text(spec.name, _NAME_PATH)
    _require_text(spec.description, _DESCRIPTION_PATH)
    schema = spec.json_schema
    if not isinstance(schema, Mapping):
        raise AgentValidationError("invalid_schema", _SCHEMA_PATH)
    try:
        Draft202012Validator.check_schema(dict(schema))
    except SchemaError as exc:
        raise AgentValidationError("invalid_schema", _SCHEMA_PATH) from exc
    if schema.get("type") != "object":
        raise AgentValidationError("invalid_schema", f"{_SCHEMA_PATH}.type")
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        raise AgentValidationError("invalid_schema", f"{_SCHEMA_PATH}.properties")
    if schema.get("additionalProperties") is not False:
        # 缺省/True 都允许额外字段，参数集合不闭合即无法与授权摘要绑定。
        raise AgentValidationError("invalid_schema", f"{_SCHEMA_PATH}.additionalProperties")


def validate_tool_set(specs: Sequence[ToolSpec], *, allowed_names: Iterable[str]) -> tuple[ToolSpec, ...]:
    """按白名单筛选并逐条校验；名称非空且全目录唯一，空允许集返回空元组。"""

    allowed: set[str] = set()
    for name in allowed_names:
        allowed.add(_require_text(name, "$.tool.allowed_name"))
    seen: set[str] = set()
    kept: list[ToolSpec] = []
    for spec in specs:
        if not isinstance(spec, ToolSpec):
            raise AgentValidationError("invalid_tool", "$.tool")
        if spec.name in seen:
            raise AgentValidationError("duplicate_tool_name", _NAME_PATH)
        seen.add(spec.name)
        if spec.name not in allowed:
            continue
        validate_tool_schema(spec)
        kept.append(spec)
    return tuple(kept)


async def _deferred_stub(ctx: RunContext[Any], **kwargs: Any) -> str:
    """延迟工具 stub：只要求 SDK 挂起本次调用，禁止任何 IO/副作用。"""

    del ctx, kwargs
    raise CallDeferred(metadata={"deferred_by": "qtrading.tools"})


def build_deferred_tools(specs: Sequence[ToolSpec]) -> tuple[Tool[Any], ...]:
    """把已校验的 spec 装成 pydantic-ai 工具；``sequential=True`` 保证逐次授权。"""

    return tuple(
        Tool.from_schema(
            _deferred_stub,
            name=spec.name,
            description=spec.description,
            json_schema=dict(spec.json_schema),
            takes_ctx=True,
            sequential=True,
        )
        for spec in specs
    )
