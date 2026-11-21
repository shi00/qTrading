"""MCP 工具参数闭合校验（真实 jsonschema 适配器，E-02-5）。

合同：MCP 目录 schema 必须可解析且本身合法；参数不闭合（缺必填、额外字段、
类型/枚举越界）一律抛 :class:`AgentValidationError`，由调用门转译为
``INPUT_INVALID``——不静默放行、不裁剪参数、不猜测默认值。
"""

from __future__ import annotations

import json
from typing import Any

from jsonschema.exceptions import SchemaError
from jsonschema.validators import Draft202012Validator, validator_for

from core.agent_types import AgentValidationError

_ARGUMENTS_PATH = "$.mcp.arguments"
_SCHEMA_PATH = "$.mcp.tool.schema"


class JsonSchemaToolValidator:
    """``MCPToolValidatorPort`` 的真实实现（Draft 2020-12 起按 ``$schema`` 自动选择）。"""

    def validate(self, schema_json: str, arguments: dict[str, Any]) -> None:
        try:
            schema = json.loads(schema_json)
        except (TypeError, ValueError) as exc:
            raise AgentValidationError("invalid_schema", _SCHEMA_PATH) from exc
        if not isinstance(schema, dict):
            raise AgentValidationError("invalid_schema", _SCHEMA_PATH)
        validator_cls = validator_for(schema, default=Draft202012Validator)
        try:
            validator_cls.check_schema(schema)
        except SchemaError as exc:
            raise AgentValidationError("invalid_schema", _SCHEMA_PATH) from exc
        for error in validator_cls(schema).iter_errors(arguments):
            json_path = getattr(error, "json_path", "$")
            suffix = json_path[1:] if isinstance(json_path, str) and json_path.startswith("$") else ""
            raise AgentValidationError("invalid_arguments", f"{_ARGUMENTS_PATH}{suffix}")
