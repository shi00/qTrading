"""E-02 显式工具调用门单元测试（静态可验证部分）。

覆盖 DoD：缺参/空工具集 0 调用、撤销先胜 0 效果、派发后超时/断线记 unknown
且不自动重试、取消继续传播并释放在途归属、后效未知的归属保留到会话退出确认、
结果留存超限明示失败。
完整 jsonschema 校验经端口注入（真实适配器仍 needs-spike/待依赖批准）。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any, cast

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
    MCPCallNotDispatched,
    MCPServerConfig,
    MCPToolDescriptor,
    MCPTransportConfig,
    SharedMCPConnectionManager,
)
from services.agent_service.mcp_tools import (
    MCPBudgetExceeded,
    MCPToolDispatcher,
    ToolBoundaryAttestation,
    ToolCallRequest,
    ToolCallState,
    ToolDispatchVerdict,
    canonical_arguments_digest,
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


def _config(
    budget: MCPBudgetProfile | None = None,
    *,
    deadline_s: int = 30,
    transport: MCPTransportConfig | None = None,
) -> MCPServerConfig:
    # 准入要求入站限额有真实插入点（I03）：默认用 HTTP 传输（stdio 无前置插入点，
    # 其缺证据的准入拒绝由 test_agent_mcp_inbound_limits.py 覆盖）。
    return MCPServerConfig(
        identity=_IDENTITY,
        display_name="本地文件服务",
        transport=transport
        or MCPTransportConfig(
            kind=MCPTransportKind.STREAMABLE_HTTP,
            url="https://mcp.example.test/tools",
            http_risk_approved=True,
        ),
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
    """可变的实现指纹探针：记录调用次数，供「每次调用前重核」断言。"""

    def __init__(self, fingerprint: str | None = _FINGERPRINT) -> None:
        self.fingerprint = fingerprint
        self.calls = 0

    async def probe(self, config: MCPServerConfig) -> str | None:
        self.calls += 1
        return self.fingerprint


class _NoFrozenUsers:
    """无研究冻结该配置版（调用门用例不涉及断开/改配置）。"""

    async def frozen_users(self, identity: MCPServiceIdentity) -> tuple[str, ...]:
        return ()


class _Session:
    def __init__(
        self,
        *,
        tools: tuple[MCPToolDescriptor, ...] | None = None,
        raises: BaseException | None = None,
        result: dict[str, Any] | None = None,
        delay: float = 0.0,
    ) -> None:
        self.tools = tools if tools is not None else (MCPToolDescriptor("read_file", _SCHEMA),)
        self.raises = raises
        self.result = result if result is not None else {"content": [{"type": "text", "text": "ok"}]}
        self.delay = delay
        self.calls: list[tuple[str, dict[str, Any], int]] = []
        self.closed = False

    async def list_tools(self, *, deadline_s: int) -> tuple[MCPToolDescriptor, ...]:
        return self.tools

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

    async def open(self, config: MCPServerConfig, *, guard: MCPBudgetGuard | None = None) -> _Session:
        self.guard = guard
        return self.session


class _Validator:
    """严格子集校验替身：缺 required → AgentValidationError；不做真实 jsonschema 声明。"""

    def __init__(self) -> None:
        self.calls = 0

    def validate(self, schema_json: str, arguments: dict[str, Any]) -> None:
        self.calls += 1
        if schema_json == _SCHEMA and "path" not in arguments:
            raise AgentValidationError("invalid_tool_arguments", "$.arguments.path")


class _MutatingValidator(_Validator):
    """校验后篡改快照：模拟校验端口改动参数，门内必须零外发。"""

    def validate(self, schema_json: str, arguments: dict[str, Any]) -> None:
        super().validate(schema_json, arguments)
        arguments["path"] = "C:\\data\\swapped.txt"


class _Grants:
    def __init__(
        self,
        verdict: ToolDispatchVerdict = ToolDispatchVerdict.CLAIMED,
        *,
        on_claim: Callable[[], None] | None = None,
        raises: BaseException | None = None,
    ) -> None:
        self.verdict = verdict
        self.on_claim = on_claim
        self.raises = raises
        #: (tool_name, operation_id, schema_digest, arguments_digest, audit_scope_digest)
        self.claims: list[tuple[str, str, str, str, str]] = []
        self.boundary_digests: list[str] = []

    async def claim(
        self,
        *,
        identity: MCPServiceIdentity,
        tool_name: str,
        operation_id: str,
        schema_digest: str,
        arguments_digest: str,
        audit_scope_digest: str,
        boundary_digest: str,
    ) -> ToolDispatchVerdict:
        if self.raises is not None:
            raise self.raises
        self.claims.append((tool_name, operation_id, schema_digest, arguments_digest, audit_scope_digest))
        self.boundary_digests.append(boundary_digest)
        if self.on_claim is not None:
            self.on_claim()
        return self.verdict


_BOUNDARY_DIGEST = sha256_hex(b"boundary")


class _Boundary:
    """研究边界事实替身：固定摘要与阻断原因（``None`` = 允许派发）。"""

    def __init__(self, *, digest: str = _BOUNDARY_DIGEST, blocked_reason: str | None = None) -> None:
        self.digest = digest
        self.blocked_reason = blocked_reason
        self.calls = 0

    async def attest(self) -> ToolBoundaryAttestation:
        self.calls += 1
        return ToolBoundaryAttestation(self.digest, self.blocked_reason)


async def _dispatcher(
    *,
    session: _Session | None = None,
    verdict: ToolDispatchVerdict = ToolDispatchVerdict.CLAIMED,
    budget: MCPBudgetProfile | None = None,
    deadline_s: int = 30,
    grants: _Grants | None = None,
    validator: _Validator | None = None,
    boundary: _Boundary | None = None,
) -> tuple[MCPToolDispatcher, _Session, _Grants, SharedMCPConnectionManager]:
    config = _config(budget, deadline_s=deadline_s)
    session = session or _Session()
    manager = SharedMCPConnectionManager(
        store=_Store(config), factory=_Factory(session), probe=_Probe(), usage=_NoFrozenUsers()
    )
    await manager.connect(_IDENTITY)
    grants = grants or _Grants(verdict)
    dispatcher = MCPToolDispatcher(
        manager=manager,
        validator=validator or _Validator(),
        grants=grants,
        boundary=boundary or _Boundary(),
    )
    return dispatcher, session, grants, manager


class _CancellingProbe:
    """启连时正常返回，之后的重核抛取消：验证取消沿重核路径传播且零 claim/零外发。"""

    def __init__(self, *, allow: int = 1) -> None:
        self.calls = 0
        self._allow = allow
        self.fingerprint: str | None = _FINGERPRINT

    async def probe(self, config: MCPServerConfig) -> str | None:
        self.calls += 1
        if self.calls > self._allow:
            raise asyncio.CancelledError()
        return self.fingerprint


async def _dispatcher_with_store(
    *,
    config: MCPServerConfig | None = None,
    probe: _Probe | _CancellingProbe | None = None,
) -> tuple[MCPToolDispatcher, _Session, _Grants, _Store, SharedMCPConnectionManager, _Probe | _CancellingProbe]:
    """可观测存储/探针的调用门：用于在启连后改变证据并断言零新调用。"""

    store = _Store(config or _config())
    session = _Session()
    probe = probe or _Probe()
    manager = SharedMCPConnectionManager(store=store, factory=_Factory(session), probe=probe, usage=_NoFrozenUsers())
    await manager.connect(_IDENTITY)
    grants = _Grants()
    dispatcher = MCPToolDispatcher(manager=manager, validator=_Validator(), grants=grants, boundary=_Boundary())
    return dispatcher, session, grants, store, manager, probe


def _request(**overrides: object) -> ToolCallRequest:
    """测试用请求；每个用例新建 manager，首次启连代次恒为 1（旧代次用显式覆盖）。"""

    values: dict[str, object] = {
        "identity": _IDENTITY,
        "tool_name": "read_file",
        "arguments": {"path": "C:\\data\\a.txt"},
        "catalog": (MCPToolDescriptor("read_file", _SCHEMA),),
        "role_tools": ("read_file",),
        "request_tools": ("read_file",),
        "permission_tools": ("read_file",),
        "operation_id": "op-1",
        "connection_generation": 1,
        "boundary_digest": _BOUNDARY_DIGEST,
    }
    values.update(overrides)
    if "arguments_digest" not in overrides:
        values["arguments_digest"] = canonical_arguments_digest(cast(dict[str, Any], values["arguments"]))
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
    async def test_swapped_catalog_is_refused_with_zero_claim(self) -> None:
        """请求自报目录与连接冻结目录不一致：claim 前拒绝，零预留/零执行/零外发。"""

        dispatcher, session, grants, _manager = await _dispatcher()
        swapped = (MCPToolDescriptor("delete_file", _SCHEMA),)
        with pytest.raises(AgentServiceError) as exc:
            await dispatcher.call(
                _request(
                    catalog=swapped,
                    role_tools=("delete_file",),
                    request_tools=("delete_file",),
                    permission_tools=("delete_file",),
                )
            )
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert "catalog" in exc.value.sanitized_message
        assert grants.claims == []
        assert session.calls == []

    @pytest.mark.asyncio
    async def test_schema_drift_is_refused_with_zero_claim(self) -> None:
        dispatcher, session, grants, _manager = await _dispatcher()
        with pytest.raises(AgentServiceError) as exc:
            await dispatcher.call(_request(catalog=(MCPToolDescriptor("read_file", _EMPTY_SCHEMA),)))
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert "schema" in exc.value.sanitized_message
        assert grants.claims == []
        assert session.calls == []

    @pytest.mark.asyncio
    async def test_tool_outside_audit_scope_is_refused_with_zero_claim(self) -> None:
        session = _Session(
            tools=(MCPToolDescriptor("read_file", _SCHEMA), MCPToolDescriptor("write_file", _SCHEMA)),
        )
        dispatcher, session, grants, _manager = await _dispatcher(session=session)
        with pytest.raises(AgentServiceError) as exc:
            await dispatcher.call(
                _request(
                    tool_name="write_file",
                    catalog=session.tools,
                    role_tools=("write_file",),
                    request_tools=("write_file",),
                    permission_tools=("write_file",),
                )
            )
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert "scope" in exc.value.sanitized_message
        assert grants.claims == []
        assert session.calls == []

    @pytest.mark.asyncio
    async def test_stale_connection_generation_is_refused_with_zero_claim(self) -> None:
        dispatcher, session, grants, _manager = await _dispatcher()
        with pytest.raises(AgentServiceError) as exc:
            await dispatcher.call(_request(connection_generation=0))
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert "generation" in exc.value.sanitized_message
        assert grants.claims == []
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
        assert [(claim[0], claim[1]) for claim in _grants.claims] == [("read_file", "op-1")]
        assert _grants.claims[0][2] == sha256_hex(_SCHEMA)
        assert _grants.claims[0][3] == canonical_arguments_digest({"path": "C:\\data\\a.txt"})
        assert _grants.claims[0][4] == sha256_hex('["read_file"]')
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
        # 后效未知：归属保留到所属会话真实退出被确认，不得立即释放（I53）。
        assert manager.in_flight_operations(_IDENTITY) == ("op-1",)

    @pytest.mark.asyncio
    async def test_unknown_ownership_is_released_only_after_confirmed_exit(self) -> None:
        """后效未知不得提前释放占用：确认会话真实退出后才解除归属（I53）。"""

        session = _Session(raises=ConnectionError("dropped"))
        dispatcher, session, _grants, manager = await _dispatcher(session=session)
        outcome = await dispatcher.call(_request())
        assert outcome.state is ToolCallState.UNKNOWN
        assert manager.in_flight_operations(_IDENTITY) == ("op-1",)

        await manager.disconnect(_IDENTITY)

        assert session.closed is True
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
        # 已有的 UNKNOWN（结果不可编码）不得被后续分类覆盖为 FAILED。
        assert outcome.operation_id == "op-1"
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
    async def test_lone_surrogate_result_is_unknown(self) -> None:
        """孤立代理项无法 UTF-8 编码时同样不得裸逃逸。"""

        session = _Session(result={"text": "\ud800"})
        dispatcher, session, _grants, _manager = await _dispatcher(session=session)
        outcome = await dispatcher.call(_request())
        assert outcome.state is ToolCallState.UNKNOWN
        assert outcome.result is None

    @pytest.mark.asyncio
    async def test_refused_before_send_is_failed_with_original_operation(self) -> None:
        """端口在出站前显式拒绝（会话失效/发送前 deadline 已过）：效果确定未发生 → FAILED。"""

        session = _Session(raises=MCPCallNotDispatched(AgentErrorCode.CONFIG_UNAVAILABLE, "mcp session is lost", "svc"))
        dispatcher, session, _grants, manager = await _dispatcher(session=session)
        outcome = await dispatcher.call(_request())
        assert outcome.state is ToolCallState.FAILED
        assert outcome.result is None
        assert outcome.operation_id == "op-1"
        assert [call[0] for call in session.calls] == ["read_file"]
        assert manager.in_flight_operations(_IDENTITY) == ()

    @pytest.mark.asyncio
    async def test_protocol_decode_failure_after_dispatch_is_unknown_with_original_operation(self) -> None:
        """已派发后协议/解码失败不构成「确定未执行」的证据：保持 UNKNOWN 且保留原 operation。"""

        session = _Session(raises=json.JSONDecodeError("Expecting value", "", 0))
        dispatcher, session, _grants, _manager = await _dispatcher(session=session)
        outcome = await dispatcher.call(_request())
        assert outcome.state is ToolCallState.UNKNOWN
        assert outcome.result is None
        assert outcome.operation_id == "op-1"
        assert [call[0] for call in session.calls] == ["read_file"]

    @pytest.mark.asyncio
    async def test_server_error_response_after_dispatch_is_unknown(self) -> None:
        """服务端明确错误响应（JSON-RPC error）证明请求已达服务端，但不证明零副作用：UNKNOWN。"""

        session = _Session(raises=RuntimeError("jsonrpc -32603 internal error"))
        dispatcher, session, _grants, _manager = await _dispatcher(session=session)
        outcome = await dispatcher.call(_request())
        assert outcome.state is ToolCallState.UNKNOWN
        assert outcome.result is None
        assert outcome.operation_id == "op-1"

    @pytest.mark.asyncio
    async def test_general_transport_error_after_dispatch_is_unknown(self) -> None:
        """泛化传输异常不得标 FAILED（无确证即后效未知），保留原 operation 不误导重发。"""

        session = _Session(raises=ValueError("bad arguments"))
        dispatcher, session, _grants, _manager = await _dispatcher(session=session)
        outcome = await dispatcher.call(_request())
        assert outcome.state is ToolCallState.UNKNOWN
        assert outcome.result is None
        assert outcome.operation_id == "op-1"
        assert [call[0] for call in session.calls] == ["read_file"]

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
        """每连接单一账本：管理器注入的 guard 就是会话计量各族所用的同一实例。"""

        session = _Session()
        factory = _Factory(session)
        manager = SharedMCPConnectionManager(
            store=_Store(_config(_budget(retention_bytes=10_000))),
            factory=factory,
            probe=_Probe(),
            usage=_NoFrozenUsers(),
        )
        await manager.connect(_IDENTITY)
        guard = manager.budget_guard(_IDENTITY)
        assert isinstance(guard, MCPBudgetGuard)
        assert factory.guard is guard
        assert guard.used("retention_bytes") == 0

        # 同一账本：单条结果族与留存族累计到同一实例，而非各记一份。
        dispatcher = MCPToolDispatcher(manager=manager, validator=_Validator(), grants=_Grants(), boundary=_Boundary())
        outcome = await dispatcher.call(_request())
        assert outcome.state is ToolCallState.COMPLETED
        assert guard.used("single_result_bytes") > 0
        assert guard.used("retention_bytes") > 0


class TestResearchBoundaryDispatchGate:
    """I43：研究边界（来源撤用）在派发前新鲜重核；禁用源零 claim/零派发。"""

    @pytest.mark.asyncio
    async def test_disabled_source_blocks_with_zero_claim_and_no_send(self) -> None:
        dispatcher, session, grants, _manager = await _dispatcher(boundary=_Boundary(blocked_reason="source_disabled"))
        with pytest.raises(AgentServiceError) as exc:
            await dispatcher.call(_request())
        assert exc.value.code is AgentErrorCode.SOURCE_DISABLED
        assert grants.claims == []
        assert session.calls == []

    @pytest.mark.asyncio
    async def test_boundary_digest_mismatch_refuses_stale_authorization(self) -> None:
        dispatcher, session, grants, _manager = await _dispatcher(boundary=_Boundary(digest=sha256_hex(b"changed")))
        with pytest.raises(AgentServiceError) as exc:
            await dispatcher.call(_request())
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert "boundary" in exc.value.sanitized_message
        assert grants.claims == []
        assert session.calls == []

    @pytest.mark.asyncio
    async def test_matching_boundary_is_bound_into_claim(self) -> None:
        dispatcher, _session, grants, _manager = await _dispatcher()
        outcome = await dispatcher.call(_request())
        assert outcome.state is ToolCallState.COMPLETED
        assert grants.boundary_digests == [_BOUNDARY_DIGEST]

    @pytest.mark.asyncio
    async def test_claim_competition_loser_has_zero_dispatch(self) -> None:
        """claim 竞争：先胜者派发一次，后到者被撤销且零外发（边界事实相同不改变判定）。"""

        class _CompetingGrants(_Grants):
            def __init__(self) -> None:
                super().__init__(ToolDispatchVerdict.CLAIMED)
                self.seen = 0

            async def claim(self, **kwargs: Any) -> ToolDispatchVerdict:
                await super().claim(**kwargs)
                self.seen += 1
                return ToolDispatchVerdict.CLAIMED if self.seen == 1 else ToolDispatchVerdict.REVOKED

        dispatcher, session, _grants, _manager = await _dispatcher(grants=_CompetingGrants())

        first = await dispatcher.call(_request(operation_id="op-1"))
        assert first.state is ToolCallState.COMPLETED
        with pytest.raises(AgentServiceError) as exc:
            await dispatcher.call(_request(operation_id="op-1"))
        assert exc.value.code is AgentErrorCode.SOURCE_DISABLED
        assert len(session.calls) == 1
        assert session.calls[0][0] == "read_file"

    @pytest.mark.asyncio
    async def test_duplicate_in_flight_operation_blocks_before_claim(self) -> None:
        """同一 operation 已在途：重复调用在 claim 前被拒（零 claim/零外发）。"""

        dispatcher, session, grants, manager = await _dispatcher()
        manager.register_in_flight(_IDENTITY, "op-1")

        with pytest.raises(AgentServiceError) as exc:
            await dispatcher.call(_request(operation_id="op-1"))

        assert exc.value.code is AgentErrorCode.INPUT_CONFLICT
        assert grants.claims == []
        assert session.calls == []


class TestFrozenArguments:
    """I05：参数在校验前冻结为规范快照，claim 绑定 schema/参数/审核范围摘要。"""

    @pytest.mark.asyncio
    async def test_nested_mutation_during_claim_sends_frozen_snapshot(self) -> None:
        """claim 的 await 期间改动嵌套参数：出站参数仍是已校验快照值。"""

        arguments: dict[str, Any] = {"path": "C:\\data\\a.txt", "options": {"depth": 1}}
        request = _request(arguments=arguments)
        grants = _Grants(on_claim=lambda: arguments["options"].update({"depth": 99, "extra": True}))
        dispatcher, session, _grants, _manager = await _dispatcher(grants=grants)
        outcome = await dispatcher.call(request)
        assert outcome.state is ToolCallState.COMPLETED
        assert session.calls[0][1] == {"path": "C:\\data\\a.txt", "options": {"depth": 1}}
        assert grants.claims[0][3] == canonical_arguments_digest({"path": "C:\\data\\a.txt", "options": {"depth": 1}})

    @pytest.mark.asyncio
    async def test_argument_digest_mismatch_is_refused_with_zero_claim(self) -> None:
        """请求自报摘要与冻结参数不符：claim 前拒绝，零 claim/零执行。"""

        dispatcher, session, grants, _manager = await _dispatcher()
        with pytest.raises(AgentServiceError) as exc:
            await dispatcher.call(_request(arguments_digest="0" * 64))
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert "digest" in exc.value.sanitized_message
        assert grants.claims == []
        assert session.calls == []

    @pytest.mark.asyncio
    async def test_non_json_arguments_are_refused_before_claim(self) -> None:
        """不可规范化的参数（二进制等）在校验前拒绝，零 claim/零执行。"""

        dispatcher, session, grants, _manager = await _dispatcher()
        with pytest.raises(AgentServiceError) as exc:
            await dispatcher.call(_request(arguments={"path": "x", "blob": b"\x00"}, arguments_digest="0" * 64))
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        assert grants.claims == []
        assert session.calls == []

    @pytest.mark.asyncio
    async def test_validator_mutation_of_snapshot_blocks_dispatch(self) -> None:
        """校验端口改动快照即「校验 A、发送 B」：claim 后、外发前拒绝。"""

        dispatcher, session, grants, _manager = await _dispatcher(validator=_MutatingValidator())
        with pytest.raises(AgentServiceError) as exc:
            await dispatcher.call(_request())
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert "changed" in exc.value.sanitized_message
        assert len(grants.claims) == 1
        assert session.calls == []

    @pytest.mark.asyncio
    async def test_cancellation_during_claim_propagates_without_dispatch(self) -> None:
        """claim 期间取消必须继续传播，且零外发、零在途归属。"""

        grants = _Grants(raises=asyncio.CancelledError())
        dispatcher, session, _grants, manager = await _dispatcher(grants=grants)
        with pytest.raises(asyncio.CancelledError) as excinfo:
            await dispatcher.call(_request())
        assert isinstance(excinfo.value, asyncio.CancelledError)
        assert grants.claims == []
        assert session.calls == []
        assert manager.in_flight_operations(_IDENTITY) == ()

    def test_digest_is_stable_and_key_order_independent(self) -> None:
        """合法路径摘要稳定：同参数同摘要，键序无关，列表顺序仍参与摘要。"""

        first = canonical_arguments_digest({"a": 1, "b": {"c": 2, "d": [1, 2]}})
        second = canonical_arguments_digest({"b": {"d": [1, 2], "c": 2}, "a": 1})
        assert first == second == sha256_hex('{"a":1,"b":{"c":2,"d":[1,2]}}')
        assert canonical_arguments_digest({"a": [1, 2]}) != canonical_arguments_digest({"a": [2, 1]})


class TestAuditEvidenceRecheck:
    """I06：审核证据不是一次性结论，每次调用前重核；失效即零 claim/零外发。"""

    @pytest.mark.asyncio
    async def test_valid_evidence_allows_call_and_is_rechecked_per_call(self) -> None:
        probe = _Probe()
        dispatcher, session, _grants, _store, _manager, probe = await _dispatcher_with_store(probe=probe)

        first = await dispatcher.call(_request())
        second = await dispatcher.call(_request(operation_id="op-2"))

        assert first.state is ToolCallState.COMPLETED
        assert second.state is ToolCallState.COMPLETED
        assert probe.calls == 3  # 启连 1 次 + 每次调用各 1 次
        # 默认传输为 HTTP（I03：stdio 无入站前置插入点、不被准入）：远端实现不可观测，
        # 结果须如实标注信任限制，但证据一致仍准入。
        assert first.audit_trust_limited is True
        assert first.audit_trust_limited is second.audit_trust_limited
        assert [(call[0], call[1]) for call in session.calls] == [
            ("read_file", {"path": "C:\\data\\a.txt"}),
            ("read_file", {"path": "C:\\data\\a.txt"}),
        ]

    @pytest.mark.asyncio
    async def test_fingerprint_change_after_connect_blocks_call_before_claim(self) -> None:
        """磁盘载荷被替换（本机可观测项与审核记录不一致）：零 claim/零外发。"""

        probe = _Probe()
        dispatcher, session, grants, _store, _manager, probe = await _dispatcher_with_store(probe=probe)
        probe.fingerprint = sha256_hex(b"replaced-on-disk-payload")

        with pytest.raises(AgentServiceError) as exc:
            await dispatcher.call(_request())

        assert exc.value.code is AgentErrorCode.CAPABILITY_UNVERIFIED
        assert "implementation_changed" in exc.value.sanitized_message
        assert grants.claims == []
        assert session.calls == []

    @pytest.mark.asyncio
    async def test_unverifiable_evidence_blocks_call_before_claim(self) -> None:
        """实现无法核定（探针返回 None）：不得按旧证据继续，零 claim/零外发。"""

        probe = _Probe()
        dispatcher, session, grants, _store, _manager, probe = await _dispatcher_with_store(probe=probe)
        probe.fingerprint = None

        with pytest.raises(AgentServiceError) as exc:
            await dispatcher.call(_request())

        assert exc.value.code is AgentErrorCode.CAPABILITY_UNVERIFIED
        assert "implementation_unverified" in exc.value.sanitized_message
        assert grants.claims == []
        assert session.calls == []

    @pytest.mark.asyncio
    async def test_withdrawn_audit_record_blocks_call_before_claim(self) -> None:
        """审核记录被撤回（存储值里 audit 为 None）：重核必须拒绝，零 claim/零外发。"""

        dispatcher, session, grants, store, _manager, _probe = await _dispatcher_with_store()
        store.config = replace(store.config, audit=None)

        with pytest.raises(AgentServiceError) as exc:
            await dispatcher.call(_request())

        assert exc.value.code is AgentErrorCode.CAPABILITY_UNVERIFIED
        assert "audit_missing" in exc.value.sanitized_message
        assert grants.claims == []
        assert session.calls == []

    @pytest.mark.asyncio
    async def test_remote_transport_marks_trust_limit_and_never_claims_verified(self) -> None:
        """remote 传输的远端目标状态不可观测：结果标注信任限制，不宣称已验证。"""

        transport = MCPTransportConfig(
            kind=MCPTransportKind.STREAMABLE_HTTP,
            url="https://mcp.example.test/tools",
            http_risk_approved=True,
        )
        dispatcher, session, _grants, _store, _manager, _probe = await _dispatcher_with_store(
            config=_config(transport=transport)
        )

        outcome = await dispatcher.call(_request())

        assert outcome.state is ToolCallState.COMPLETED
        assert outcome.audit_trust_limited is True
        assert [(call[0], call[2]) for call in session.calls] == [("read_file", 30)]

    @pytest.mark.asyncio
    async def test_cancellation_during_recheck_propagates_without_claim(self) -> None:
        """重核期间取消必须继续传播，且零 claim/零外发/零在途归属。"""

        probe = _CancellingProbe()
        dispatcher, session, grants, _store, manager, probe = await _dispatcher_with_store(probe=probe)

        with pytest.raises(asyncio.CancelledError) as excinfo:
            await dispatcher.call(_request())

        assert isinstance(excinfo.value, asyncio.CancelledError)
        assert probe.calls == 2
        assert grants.claims == []
        assert session.calls == []
        assert manager.in_flight_operations(_IDENTITY) == ()
