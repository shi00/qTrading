"""``app_state`` MCP 配置存储：键格式、往返、损坏载荷、写失败传播、列表逐行容错。"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from core.agent_types import (
    MCP_BUDGET_FIELDS,
    AgentErrorCode,
    AgentServiceError,
    MCPBudgetProfile,
    MCPServiceIdentity,
    MCPTransportKind,
)
from services.agent_service.mcp import MCPServerConfig, MCPTransportConfig
from services.agent_service.mcp_config_codec import encode_mcp_server_config
from services.agent_service.mcp_config_store import (
    LISTING_INVALID,
    LISTING_PATH_UNAVAILABLE,
    LISTING_UNREADABLE,
    AppStateMCPConfigStore,
)

pytestmark = pytest.mark.unit

_IDENTITY = MCPServiceIdentity(service_id="svc-store", config_version=1)


def _config(identity: MCPServiceIdentity = _IDENTITY, *, display_name: str = "存储服务") -> MCPServerConfig:
    return MCPServerConfig(
        identity=identity,
        display_name=display_name,
        transport=MCPTransportConfig(
            kind=MCPTransportKind.STREAMABLE_HTTP,
            url="https://mcp.example.test/tools",
            http_risk_approved=True,
        ),
        budget=MCPBudgetProfile(
            receive_bytes=1,
            decoded_bytes=1,
            structure_depth=1,
            catalog_items=1,
            catalog_bytes=1,
            schema_bytes=1,
            single_result_bytes=1,
            retention_bytes=1,
        ),
    )


class _Memory:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.write_error: Exception | None = None
        self.read_error: Exception | None = None

    async def read(self, key: str) -> str | None:
        if self.read_error is not None:
            raise self.read_error
        return self.values.get(key)

    async def write(self, key: str, value: str) -> None:
        if self.write_error is not None:
            raise self.write_error
        self.values[key] = value

    async def scan(self, prefix: str) -> Mapping[str, str]:
        return {key: value for key, value in self.values.items() if key.startswith(prefix)}


def _store(memory: _Memory, *, with_scan: bool = True) -> AppStateMCPConfigStore:
    return AppStateMCPConfigStore(
        read=memory.read,
        write=memory.write,
        scan=memory.scan if with_scan else None,
    )


class TestAppStateMCPConfigStore:
    async def test_key_format_and_round_trip(self) -> None:
        memory = _Memory()
        store = _store(memory)
        await store.save(_config())

        key = AppStateMCPConfigStore.key_for(_IDENTITY)
        assert key == f"agent.mcp.service.{_IDENTITY.stable_key()}"
        assert key in memory.values
        assert await store.load(_IDENTITY) == _config()

    async def test_missing_returns_none(self) -> None:
        store = _store(_Memory())
        assert await store.load(_IDENTITY) is None

    async def test_write_failure_propagates(self) -> None:
        memory = _Memory()
        memory.write_error = RuntimeError("db down")
        with pytest.raises(RuntimeError) as exc:
            await _store(memory).save(_config())
        assert "db down" in str(exc.value)

    async def test_corrupt_payload_is_explicit_failure(self) -> None:
        memory = _Memory()
        memory.values[AppStateMCPConfigStore.key_for(_IDENTITY)] = "{not-json"
        with pytest.raises(AgentServiceError) as exc:
            await _store(memory).load(_IDENTITY)
        assert exc.value.code is AgentErrorCode.CONFIG_UNAVAILABLE

    async def test_identity_mismatch_rejected(self) -> None:
        memory = _Memory()
        other = MCPServiceIdentity(service_id="svc-other", config_version=1)
        payload: Any = encode_mcp_server_config(_config(other))
        memory.values[AppStateMCPConfigStore.key_for(_IDENTITY)] = json.dumps(payload)
        with pytest.raises(AgentServiceError) as exc:
            await _store(memory).load(_IDENTITY)
        assert exc.value.code is AgentErrorCode.CONFIG_UNAVAILABLE

    async def test_list_configs_sorted(self) -> None:
        memory = _Memory()
        store = _store(memory)
        await store.save(_config(MCPServiceIdentity(service_id="svc-b", config_version=1), display_name="B 服务"))
        await store.save(_config(MCPServiceIdentity(service_id="svc-a", config_version=1), display_name="A 服务"))

        listed = await store.list_configs()
        assert [item.config.identity.service_id for item in listed if item.config is not None] == ["svc-a", "svc-b"]
        assert all(item.problem is None for item in listed)

    async def test_list_without_scan_is_explicit_failure(self) -> None:
        store = _store(_Memory(), with_scan=False)
        with pytest.raises(AgentServiceError) as exc:
            await store.list_configs()
        assert exc.value.code is AgentErrorCode.CONFIG_UNAVAILABLE


def _stdio_wire(service_id: str, *, command: str, cwd: str, display_name: str | None = None) -> dict[str, Any]:
    """stdio 记录的手写 wire（绕开保存期路径校验，模拟「保存后程序被移动」）。"""

    return {
        "identity": {"service_id": service_id, "config_version": 1},
        "display_name": display_name if display_name is not None else service_id,
        "transport": {
            "kind": MCPTransportKind.STDIO.value,
            "command": command,
            "args": [],
            "env": [],
            "cwd": cwd,
            "url": None,
            "sse_compat_approved": False,
            "headers": [],
            "http_risk_approved": False,
        },
        "budget": {name: 1024 for name in MCP_BUDGET_FIELDS},
        "connect_deadline_s": 30,
        "tool_deadline_s": 30,
        "audit": None,
    }


def _http_wire(service_id: str, *, url: str, http_risk_approved: bool) -> dict[str, Any]:
    """streamable_http 记录的手写 wire（用于区分「路径失效」与语义校验失败）。"""

    return {
        "identity": {"service_id": service_id, "config_version": 1},
        "display_name": service_id,
        "transport": {
            "kind": MCPTransportKind.STREAMABLE_HTTP.value,
            "command": None,
            "args": [],
            "env": [],
            "cwd": None,
            "url": url,
            "sse_compat_approved": False,
            "headers": [],
            "http_risk_approved": http_risk_approved,
        },
        "budget": {name: 1024 for name in MCP_BUDGET_FIELDS},
        "connect_deadline_s": 30,
        "tool_deadline_s": 30,
        "audit": None,
    }


class TestListTolerance:
    """逐行容错：单条失效只影响该行，健康配置照常列出（J25 按身份管理）。"""

    async def test_missing_program_marks_only_that_row(self, tmp_path: Path) -> None:
        memory = _Memory()
        healthy = MCPServiceIdentity(service_id="svc-b", config_version=1)
        broken = MCPServiceIdentity(service_id="svc-a", config_version=1)
        memory.values[AppStateMCPConfigStore.key_for(healthy)] = json.dumps(
            _stdio_wire("svc-b", command=sys.executable, cwd=str(tmp_path))
        )
        memory.values[AppStateMCPConfigStore.key_for(broken)] = json.dumps(
            _stdio_wire("svc-a", command=str(tmp_path / "moved-away-server"), cwd=str(tmp_path))
        )

        listed = await _store(memory).list_configs()

        by_service = {entry.config.identity.service_id: entry for entry in listed if entry.config is not None}
        assert set(by_service) == {"svc-a", "svc-b"}
        assert by_service["svc-a"].problem == LISTING_PATH_UNAVAILABLE
        assert by_service["svc-b"].problem is None
        assert by_service["svc-a"].identity_key == broken.stable_key()

    async def test_missing_working_directory_is_path_unavailable(self, tmp_path: Path) -> None:
        memory = _Memory()
        identity = MCPServiceIdentity(service_id="svc-c", config_version=1)
        memory.values[AppStateMCPConfigStore.key_for(identity)] = json.dumps(
            _stdio_wire("svc-c", command=sys.executable, cwd=str(tmp_path / "gone-dir"))
        )

        listed = await _store(memory).list_configs()

        assert [entry.problem for entry in listed] == [LISTING_PATH_UNAVAILABLE]

    async def test_missing_program_still_fails_load(self, tmp_path: Path) -> None:
        """列表容忍不代表使用点放行：读取/启连仍 fail-closed。"""

        memory = _Memory()
        broken = MCPServiceIdentity(service_id="svc-a", config_version=1)
        memory.values[AppStateMCPConfigStore.key_for(broken)] = json.dumps(
            _stdio_wire("svc-a", command=str(tmp_path / "moved-away-server"), cwd=str(tmp_path))
        )

        with pytest.raises(AgentServiceError) as exc:
            await _store(memory).load(broken)
        assert exc.value.code is AgentErrorCode.CONFIG_UNAVAILABLE

    async def test_non_path_transport_failure_is_not_labelled_as_path(self, tmp_path: Path) -> None:
        """语义校验失败（明文 HTTP 未确认风险）不得被标成「路径失效」。"""

        memory = _Memory()
        identity = MCPServiceIdentity(service_id="svc-h", config_version=1)
        memory.values[AppStateMCPConfigStore.key_for(identity)] = json.dumps(
            _http_wire("svc-h", url="http://mcp.example.test/tools", http_risk_approved=False)
        )
        memory.values[AppStateMCPConfigStore.key_for(MCPServiceIdentity("svc-b", 1))] = json.dumps(
            _stdio_wire("svc-b", command=sys.executable, cwd=str(tmp_path))
        )

        listed = await _store(memory).list_configs()

        problems = {entry.config.identity.service_id: entry.problem for entry in listed if entry.config is not None}
        assert problems == {"svc-h": LISTING_INVALID, "svc-b": None}

    async def test_invalid_record_shape_is_marked_not_listed_as_usable(self, tmp_path: Path) -> None:
        memory = _Memory()
        invalid = MCPServiceIdentity(service_id="svc-x", config_version=1)
        memory.values[AppStateMCPConfigStore.key_for(invalid)] = json.dumps(
            _stdio_wire("svc-x", command=sys.executable, cwd=str(tmp_path), display_name="")
        )

        listed = await _store(memory).list_configs()

        entry = next(item for item in listed if item.config is not None)
        assert entry.problem == LISTING_INVALID
        assert entry.config.identity == invalid

    async def test_undecodable_record_does_not_break_others(self, tmp_path: Path) -> None:
        memory = _Memory()
        healthy = MCPServiceIdentity(service_id="svc-b", config_version=1)
        corrupt = MCPServiceIdentity(service_id="svc-z", config_version=1)
        memory.values[AppStateMCPConfigStore.key_for(healthy)] = json.dumps(
            _stdio_wire("svc-b", command=sys.executable, cwd=str(tmp_path))
        )
        memory.values[AppStateMCPConfigStore.key_for(corrupt)] = "{not-json"

        listed = await _store(memory).list_configs()

        assert [entry.config.identity.service_id for entry in listed if entry.config is not None] == ["svc-b"]
        unreadable = [entry for entry in listed if entry.config is None]
        assert len(unreadable) == 1
        assert unreadable[0].problem == LISTING_UNREADABLE
        assert unreadable[0].identity_key == corrupt.stable_key()

    async def test_multiple_failures_coexist_with_healthy_row(self, tmp_path: Path) -> None:
        memory = _Memory()
        memory.values[AppStateMCPConfigStore.key_for(MCPServiceIdentity("svc-b", 1))] = json.dumps(
            _stdio_wire("svc-b", command=sys.executable, cwd=str(tmp_path))
        )
        memory.values[AppStateMCPConfigStore.key_for(MCPServiceIdentity("svc-a", 1))] = json.dumps(
            _stdio_wire("svc-a", command=str(tmp_path / "gone"), cwd=str(tmp_path))
        )
        memory.values[AppStateMCPConfigStore.key_for(MCPServiceIdentity("svc-z", 1))] = json.dumps({"identity": {}})

        listed = await _store(memory).list_configs()

        assert [entry.config.identity.service_id for entry in listed if entry.config is not None] == ["svc-a", "svc-b"]
        assert [entry.problem for entry in listed if entry.config is None] == [LISTING_UNREADABLE]
