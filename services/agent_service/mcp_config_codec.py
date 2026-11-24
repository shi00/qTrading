"""MCP 服务配置的持久化编解码（``app_state`` JSON 值 ↔ :class:`MCPServerConfig`）。

边界：

- 只做**编解码与结构校验**，不读写存储、不启连；
- 解码结果必须再经 :func:`validate_mcp_server_config`（与保存/启连同一入口，I18）；
- 未知键、缺失键、类型不符一律拒绝，不静默忽略（避免存储被篡改后静默降级）；
- 不含任何秘密明文：Header 值只允许 ``env:`` / ``keyring:`` 引用（由既有校验保证）。
"""

from __future__ import annotations

from typing import Any

from core.agent_types import (
    MCP_BUDGET_FIELDS,
    AgentValidationError,
    MCPBudgetProfile,
    MCPReadOnlyAudit,
    MCPTransportKind,
    decode_mcp_read_only_audit,
    decode_mcp_service_identity,
    encode_mcp_read_only_audit,
    encode_mcp_service_identity,
    validate_mcp_deadline,
)
from services.agent_service.mcp import MCPServerConfig, MCPTransportConfig, validate_mcp_server_config

__all__ = ["decode_mcp_server_config", "encode_mcp_server_config"]

_TOP_KEYS = frozenset(
    {"identity", "display_name", "transport", "budget", "connect_deadline_s", "tool_deadline_s", "audit"}
)
_TRANSPORT_KEYS = frozenset(
    {"kind", "command", "args", "env", "cwd", "url", "sse_compat_approved", "headers", "http_risk_approved"}
)
_BUDGET_KEYS = frozenset(MCP_BUDGET_FIELDS)


def _require_exact_keys(value: dict[str, Any], expected: frozenset[str], path: str) -> None:
    keys = set(value)
    if keys != expected:
        raise AgentValidationError("invalid_mcp_config", f"{path}: keys mismatch")


def _optional_str(value: object, path: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AgentValidationError("invalid_mcp_config", f"{path}: must be a string or null")
    return value


def _pairs(value: object, path: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        raise AgentValidationError("invalid_mcp_config", f"{path}: must be a list of pairs")
    result: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, list) or len(item) != 2 or not all(isinstance(part, str) for part in item):
            raise AgentValidationError("invalid_mcp_config", f"{path}: must be a list of string pairs")
        result.append((item[0], item[1]))
    return tuple(result)


def _pair_wires(value: tuple[tuple[str, str], ...]) -> list[list[str]]:
    return [[key, item] for key, item in value]


def _str_tuple(value: object, path: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise AgentValidationError("invalid_mcp_config", f"{path}: must be a list of strings")
    return tuple(value)


def encode_mcp_server_config(config: MCPServerConfig) -> dict[str, Any]:
    """配置 → wire 字典（全键；审核为 ``null`` 表示未绑定）。"""

    validate_mcp_server_config(config)
    transport = config.transport
    wire: dict[str, Any] = {
        "identity": encode_mcp_service_identity(config.identity),
        "display_name": config.display_name,
        "transport": {
            "kind": transport.kind.value,
            "command": transport.command,
            "args": list(transport.args),
            "env": _pair_wires(transport.env),
            "cwd": transport.cwd,
            "url": transport.url,
            "sse_compat_approved": transport.sse_compat_approved,
            "headers": _pair_wires(transport.headers),
            "http_risk_approved": transport.http_risk_approved,
        },
        "budget": {name: getattr(config.budget, name) for name in MCP_BUDGET_FIELDS},
        "connect_deadline_s": config.connect_deadline_s,
        "tool_deadline_s": config.tool_deadline_s,
        "audit": encode_mcp_read_only_audit(config.audit) if config.audit is not None else None,
    }
    return wire


def _decode_transport(value: object, path: str) -> MCPTransportConfig:
    if not isinstance(value, dict):
        raise AgentValidationError("invalid_mcp_config", f"{path}: must be an object")
    _require_exact_keys(value, _TRANSPORT_KEYS, path)
    raw_kind = value["kind"]
    try:
        kind = MCPTransportKind(raw_kind)
    except (TypeError, ValueError) as exc:
        raise AgentValidationError("invalid_mcp_config", f"{path}.kind: unknown transport") from exc
    approved = value["sse_compat_approved"]
    risk = value["http_risk_approved"]
    if not isinstance(approved, bool) or not isinstance(risk, bool):
        raise AgentValidationError("invalid_mcp_config", f"{path}: flags must be booleans")
    return MCPTransportConfig(
        kind=kind,
        command=_optional_str(value["command"], f"{path}.command"),
        args=_str_tuple(value["args"], f"{path}.args"),
        env=_pairs(value["env"], f"{path}.env"),
        cwd=_optional_str(value["cwd"], f"{path}.cwd"),
        url=_optional_str(value["url"], f"{path}.url"),
        sse_compat_approved=approved,
        headers=_pairs(value["headers"], f"{path}.headers"),
        http_risk_approved=risk,
    )


def _decode_budget(value: object, path: str) -> MCPBudgetProfile:
    if not isinstance(value, dict):
        raise AgentValidationError("invalid_mcp_config", f"{path}: must be an object")
    _require_exact_keys(value, _BUDGET_KEYS, path)
    return MCPBudgetProfile(**{name: value[name] for name in MCP_BUDGET_FIELDS})


def _decode_audit(value: object, path: str) -> MCPReadOnlyAudit | None:
    if value is None:
        return None
    return decode_mcp_read_only_audit(value, path)


def decode_mcp_server_config(
    value: object, path: str = "$.mcp.config", *, validate_admission: bool = True
) -> MCPServerConfig:
    """wire 字典 → 配置；全键必填、禁额外键，末尾复用统一准入校验。

    ``validate_admission=False`` 只做结构解码（不做程序/目录存在性等准入判定），
    仅供配置列表逐行容错使用：列表须能展示「已保存但程序被移动」的记录，以便用户
    载入后修复路径。使用点（读取/保存/启连）必须保持默认 ``True``（fail-closed）。
    """

    if not isinstance(value, dict):
        raise AgentValidationError("invalid_mcp_config", f"{path}: must be an object")
    _require_exact_keys(value, _TOP_KEYS, path)
    display_name = value["display_name"]
    if not isinstance(display_name, str):
        raise AgentValidationError("invalid_mcp_config", f"{path}.display_name: must be a string")
    try:
        identity = decode_mcp_service_identity(value["identity"], f"{path}.identity")
    except AgentValidationError as exc:
        raise AgentValidationError("invalid_mcp_config", f"{path}.identity: {exc.code}") from exc
    config = MCPServerConfig(
        identity=identity,
        display_name=display_name,
        transport=_decode_transport(value["transport"], f"{path}.transport"),
        budget=_decode_budget(value["budget"], f"{path}.budget"),
        connect_deadline_s=validate_mcp_deadline(value["connect_deadline_s"], f"{path}.connect_deadline_s"),
        tool_deadline_s=validate_mcp_deadline(value["tool_deadline_s"], f"{path}.tool_deadline_s"),
        audit=_decode_audit(value["audit"], f"{path}.audit"),
    )
    if validate_admission:
        validate_mcp_server_config(config, path)
    return config
