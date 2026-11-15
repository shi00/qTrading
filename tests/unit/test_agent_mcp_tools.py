"""E-02 显式工具调用门单元测试（静态可验证部分）。

覆盖 DoD：缺参/空工具集 0 调用、撤销先胜 0 效果、派发后超时/断线记 unknown
且不自动重试、取消继续传播并释放在途归属、结果留存超限明示失败。
完整 jsonschema 校验经端口注入（真实适配器仍 needs-spike/待依赖批准）。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    MCPBudgetProfile,
    MCPReadOnlyAudit,
    MCPServiceIdentity,
    MCPTransportKind,
    sha256_hex,
)
from services.agent_service.mcp import (
    MCPBudgetGuard,
    MCPServerConfig,
    MCPToolDescriptor,
    MCPTransportConfig,
    SharedMCPConnectionManager,
)
from services.agent_service.mcp_tools import (
    MCPBudgetExceeded,
    MCPToolDispatcher,
    ToolCallRequest,
    ToolCallState,
    ToolDispatchVerdict,
)

_IDENTITY = MCPServiceIdentity(service_id="svc-local-fs", config_version=1)
_FINGERPRINT = sha256_hex(b"impl")
_SCHEMA = '{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}'
_EMPTY_SCHEMA = '{"type":"object","properties":{}}'


def _budget(**overrides: int) -> MCPBudgetProfile:
    values = {
        "receive_bytes": 10_000,
        "decoded_bytes": 10_000,
        "structure_depth": 16,
        "catalog_items": 10,
        "catalog_bytes": 10_000,
        "schema_bytes": 10_000,
        "single_result_bytes": 10_000,
        "retention_bytes": 10_000,
    }
    values.update(overrides)
    return MCPBudgetProfile(**values)


def _config(budget: MCPBudgetProfile | None = None, *, deadline_s: int = 30) -> MCPServerConfig:
    return MCPServerConfig(
        identity=_IDENTITY,
        display_name="本地文件服务",
        transport=MCPTransportConfig(kind=MCPTransportKind.STDIO, command="C:\\tool.exe"),
        budget=budget or _budget(),
        tool_deadline_s=deadline_s,
        audit=MCPReadOnlyAudit(
            service=_IDENTITY,
            tool_scope=("read_file",),
            implementation_fingerprint=_FINGERPRINT,
            evidence_source="local-executable-inspection",
            reviewed_at="2026-11-13T00:00:00.000000Z",
            invalidation_conditions=("executable",),
        ),
    )


class _Store:
    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config

    async def load(self, identity: MCPServiceIdentity) -> MCPServerConfig | None:
        return self.config if identity == _IDENTITY else None

    async def save(self, config: MCPServerConfig) -> None:
        self.config = config


class _Probe:
    async def probe(self, config: MCPServerConfig) -> str | None:
        return _FINGERPRINT


class _Session:
    def __init__(
        self,
        *,
        raises: BaseException | None = None,
        result: dict[str, Any] | None = None,
        delay: float = 0.0,
    ) -> None:
        self.raises = raises
        self.result = result if result is not None else {"content": [{"type": "text", "text": "ok"}]}
        self.delay = delay
        self.calls: list[tuple[str, dict[str, Any], int]] = []
        self.closed = False

    async def list_tools(self, *, deadline_s: int) -> tuple[MCPToolDescriptor, ...]:
        return (MCPToolDescriptor("read_file", _SCHEMA),)

    async def call_tool(self, name: str, arguments: dict[str, Any], *, deadline_s: int) -> dict[str, Any]:
        self.calls.append((name, arguments, deadline_s))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises is not None:
            raise self.raises
        return self.result

    async def aclose(self) -> None:
        self.closed = True


class _Factory:
    def __init__(self, session: _Session) -> None:
        self.session = session

    async def open(self, config: MCPServerConfig) -> _Session:
        return self.session


class _Validator:
    """严格子集校验替身：缺 required → AgentValidationError；不做真实 jsonschema 声明。"""

    def __init__(self) -> None:
        self.calls = 0

    def validate(self, schema_json: str, arguments: dict[str, Any]) -> None:
        self.calls += 1
        if schema_json == _SCHEMA and "path" not in arguments:
            raise AgentValidationError("invalid_tool_arguments", "$.arguments.path")


class _Grants:
    def __init__(self, verdict: ToolDispatchVerdict = ToolDispatchVerdict.CLAIMED) -> None:
        self.verdict = verdict
        self.claims: list[tuple[str, str]] = []

    async def claim(self, *, identity: MCPServiceIdentity, tool_name: str, operation_id: str) -> ToolDispatchVerdict:
        self.claims.append((tool_name, operation_id))
        return self.verdict


async def _dispatcher(
    *,
    session: _Session | None = None,
    verdict: ToolDispatchVerdict = ToolDispatchVerdict.CLAIMED,
    budget: MCPBudgetProfile | None = None,
    deadline_s: int = 30,
) -> tuple[MCPToolDispatcher, _Session, _Grants, SharedMCPConnectionManager]:
    config = _config(budget, deadline_s=deadline_s)
    session = session or _Session()
    manager = SharedMCPConnectionManager(store=_Store(config), factory=_Factory(session), probe=_Probe())
    await manager.connect(_IDENTITY)
    grants = _Grants(verdict)
    dispatcher = MCPToolDispatcher(manager=manager, validator=_Validator(), grants=grants)
    return dispatcher, session, grants, manager


def _request(**overrides: object) -> ToolCallRequest:
    values: dict[str, object] = {
        "identity": _IDENTITY,
        "tool_name": "read_file",
        "arguments": {"path": "C:\\data\\a.txt"},
        "catalog": (MCPToolDescriptor("read_file", _SCHEMA),),
        "role_tools": ("read_file",),
        "request_tools": ("read_file",),
        "permission_tools": ("read_file",),
        "operation_id": "op-1",
    }
    values.update(overrides)
    return ToolCallRequest(**values)  # type: ignore[arg-type]  # 测试构造受控覆盖


class TestDispatchRefusals:
    @pytest.mark.asyncio
    async def test_missing_argument_is_refused_with_zero_calls(self) -> None:
        dispatcher, session, grants, _manager = await _dispatcher()
        with pytest.raises(AgentServiceError) as exc:
            await dispatcher.call(_request(arguments={}))
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        assert session.calls == []
        assert grants.claims == []

    @pytest.mark.asyncio
    async def test_empty_role_scope_means_vacuum_zero_calls(self) -> None:
        dispatcher, session, _grants, _manager = await _dispatcher()
        with pytest.raises(AgentServiceError) as exc:
            await dispatcher.call(_request(role_tools=()))
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert session.calls == []

    @pytest.mark.asyncio
    async def test_undiscovered_tool_is_refused(self) -> None:
        dispatcher, session, _grants, _manager = await _dispatcher()
        with pytest.raises(AgentServiceError) as exc:
            await dispatcher.call(_request(tool_name="delete_file"))
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        assert session.calls == []

    @pytest.mark.asyncio
    async def test_revoked_before_dispatch_has_zero_effect(self) -> None:
        dispatcher, session, _grants, _manager = await _dispatcher(verdict=ToolDispatchVerdict.REVOKED)
        with pytest.raises(AgentServiceError) as exc:
            await dispatcher.call(_request())
        assert exc.value.code is AgentErrorCode.SOURCE_DISABLED
        assert session.calls == []

    @pytest.mark.asyncio
    async def test_unknown_closure_blocks_dispatch(self) -> None:
        dispatcher, session, _grants, _manager = await _dispatcher(verdict=ToolDispatchVerdict.UNKNOWN)
        with pytest.raises(AgentServiceError) as exc:
            await dispatcher.call(_request())
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert session.calls == []


class TestDispatchEffects:
    @pytest.mark.asyncio
    async def test_claim_winner_calls_exactly_once_with_deadline(self) -> None:
        dispatcher, session, _grants, _manager = await _dispatcher()
        outcome = await dispatcher.call(_request())
        assert outcome.state is ToolCallState.COMPLETED
        assert outcome.result == {"content": [{"type": "text", "text": "ok"}]}
        assert len(session.calls) == 1
        assert session.calls[0][0] == "read_file"
        assert session.calls[0][2] == 30

    @pytest.mark.asyncio
    async def test_timeout_after_dispatch_is_unknown_and_not_retried(self) -> None:
        session = _Session(raises=TimeoutError("deadline"))
        dispatcher, session, _grants, manager = await _dispatcher(session=session)
        outcome = await dispatcher.call(_request())
        assert outcome.state is ToolCallState.UNKNOWN
        assert outcome.result is None
        assert [call[0] for call in session.calls] == ["read_file"]
        assert manager.in_flight_operations(_IDENTITY) == ()

    @pytest.mark.asyncio
    async def test_connection_loss_after_dispatch_is_unknown(self) -> None:
        session = _Session(raises=ConnectionError("dropped"))
        dispatcher, session, _grants, _manager = await _dispatcher(session=session)
        outcome = await dispatcher.call(_request())
        assert outcome.state is ToolCallState.UNKNOWN
        assert [call[0] for call in session.calls] == ["read_file"]

    @pytest.mark.asyncio
    async def test_hanging_call_is_cut_by_single_deadline(self) -> None:
        """端口不守时限时由门强制单一 deadline：挂起调用必须被切断为 unknown。"""

        session = _Session(delay=5.0)
        dispatcher, session, _grants, _manager = await _dispatcher(session=session, deadline_s=1)
        started = time.monotonic()
        outcome = await dispatcher.call(_request())
        assert outcome.state is ToolCallState.UNKNOWN
        assert outcome.result is None
        assert time.monotonic() - started < 3
        assert [call[0] for call in session.calls] == ["read_file"]

    @pytest.mark.asyncio
    async def test_unencodable_result_is_unknown_not_success(self) -> None:
        """二进制/自定义对象结果不得以裸异常逃逸，也不得冒完整成功。"""

        session = _Session(result={"content": [{"type": "binary", "data": b"\x00\x01"}]})
        dispatcher, session, _grants, _manager = await _dispatcher(session=session)
        outcome = await dispatcher.call(_request())
        assert outcome.state is ToolCallState.UNKNOWN
        assert outcome.result is None
        assert [call[0] for call in session.calls] == ["read_file"]

    @pytest.mark.asyncio
    async def test_two_small_results_both_pass_single_result_budget(self) -> None:
        """单条结果族按单条比较：连续两条小结果都成功。"""

        dispatcher, session, _grants, _manager = await _dispatcher(budget=_budget(single_result_bytes=60))
        first = await dispatcher.call(_request())
        second = await dispatcher.call(_request(operation_id="op-2"))
        assert first.state is ToolCallState.COMPLETED
        assert second.state is ToolCallState.COMPLETED
        assert [(call[0], call[1]) for call in session.calls] == [
            ("read_file", {"path": "C:\\data\\a.txt"}),
            ("read_file", {"path": "C:\\data\\a.txt"}),
        ]

    @pytest.mark.asyncio
    async def test_cancellation_propagates_and_releases_in_flight(self) -> None:
        session = _Session(raises=asyncio.CancelledError())
        dispatcher, session, _grants, manager = await _dispatcher(session=session)
        with pytest.raises(asyncio.CancelledError) as excinfo:
            await dispatcher.call(_request())
        assert isinstance(excinfo.value, asyncio.CancelledError)
        assert [call[0] for call in session.calls] == ["read_file"]
        assert manager.in_flight_operations(_IDENTITY) == ()

    @pytest.mark.asyncio
    async def test_retention_over_budget_fails_explicitly_after_effect(self) -> None:
        dispatcher, session, _grants, _manager = await _dispatcher(budget=_budget(retention_bytes=5))
        with pytest.raises(MCPBudgetExceeded) as exc:
            await dispatcher.call(_request())
        assert exc.value.family == "retention_bytes"
        assert [call[0] for call in session.calls] == ["read_file"]

    @pytest.mark.asyncio
    async def test_budget_guard_is_shared_per_connection(self) -> None:
        dispatcher, _session, _grants, manager = await _dispatcher(budget=_budget(retention_bytes=10_000))
        guard = manager.budget_guard(_IDENTITY)
        assert isinstance(guard, MCPBudgetGuard)
        assert guard.used("retention_bytes") == 0
