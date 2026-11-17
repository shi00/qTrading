"""I02：stdio stderr 诊断的有界脱敏排空单元测试。

覆盖四类风险场景：合成秘密不出现在诊断属性、超长行截断、持续输出有界、
关闭失败仍清理；并验证正常诊断尾部可用。排空器不再落盘，故不存在「原文落盘」。
"""

from __future__ import annotations

import os
from typing import Any, cast

import pytest

from core.agent_types import MCPBudgetProfile, MCPServiceIdentity, MCPTransportKind
from services.agent_service.mcp import MCPServerConfig, MCPTransportConfig
from services.agent_service.mcp_budget import MCPBudgetGuard, MCPDeadline
from services.agent_service.mcp_fastmcp import (
    _STDERR_TRUNCATED_MARKER,
    FastMCPSession,
    MCPSendGate,
    MCPSessionDiagnostics,
    _SanitizedStderrDrain,
)

# 合成秘密（非真实凭证）：用于断言脱敏后不以原文出现。
_SECRET = "sk-live-ABCDEF0123456789SECRET"


class _FakeClient:
    """只驱动 ``aclose`` 的假客户端；可选在退出时抛错。"""

    def __init__(self, drain: _SanitizedStderrDrain, *, fail: bool) -> None:
        self._drain = drain
        self._fail = fail

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        os.write(self._drain.fileno(), f"shutdown token={_SECRET}\n".encode())
        if self._fail:
            raise RuntimeError("client close failed")
        return False


class _RetryClient:
    """首次退出失败、后续成功：用于验证失败后不把会话标记为已关闭。"""

    def __init__(self, *, failures: int) -> None:
        self.failures = failures
        self.exit_calls = 0

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        self.exit_calls += 1
        if self.failures:
            self.failures -= 1
            raise RuntimeError("client close failed")
        return False


def _profile() -> MCPBudgetProfile:
    return MCPBudgetProfile(
        receive_bytes=10_000,
        decoded_bytes=10_000,
        structure_depth=8,
        catalog_items=8,
        catalog_bytes=10_000,
        schema_bytes=10_000,
        single_result_bytes=10_000,
        retention_bytes=10_000,
    )


def _config() -> MCPServerConfig:
    return MCPServerConfig(
        identity=MCPServiceIdentity(service_id="svc-test", config_version=1),
        display_name="svc-test",
        transport=MCPTransportConfig(kind=MCPTransportKind.STDIO, command="server.exe"),
        budget=_profile(),
    )


def _session(drain: _SanitizedStderrDrain, client: Any) -> FastMCPSession:
    return FastMCPSession(
        cast(Any, client),
        config=_config(),
        guard=MCPBudgetGuard(_profile(), identity="svc-test"),
        gate=MCPSendGate(
            alive=lambda: True,
            deadline_factory=lambda seconds: MCPDeadline(seconds=seconds),
            identity="svc-test",
        ),
        diagnostics=MCPSessionDiagnostics(),
        stderr_drain=drain,
        on_close=lambda: None,
    )


class TestSanitizedStderrDrain:
    def test_synthetic_secret_is_sanitized_in_tail(self) -> None:
        drain = _SanitizedStderrDrain()
        try:
            os.write(drain.fileno(), f"boot-ok\ntoken={_SECRET}\n".encode())
        finally:
            drain.close()

        tail = drain.tail()
        assert "boot-ok" in tail
        assert _SECRET not in tail
        assert "token=***" in tail

    def test_overlong_line_is_truncated(self) -> None:
        drain = _SanitizedStderrDrain(max_bytes=256, max_line_bytes=64)
        try:
            os.write(drain.fileno(), b"x" * 4096 + b"\nnext-line\n")
        finally:
            drain.close()

        tail = drain.tail()
        assert _STDERR_TRUNCATED_MARKER in tail
        assert "next-line" in tail
        assert len(tail.encode("utf-8")) <= 256

    def test_continuous_output_stays_bounded(self) -> None:
        drain = _SanitizedStderrDrain(max_bytes=512)
        try:
            for index in range(200):
                os.write(drain.fileno(), f"line-{index:04d}\n".encode())
        finally:
            drain.close()

        tail = drain.tail()
        assert len(tail.encode("utf-8")) <= 512
        assert "line-0199" in tail
        assert drain.retained_bytes <= 512


class TestSessionStderrLifecycle:
    async def test_successful_close_exposes_sanitized_tail_and_clears_buffer(self) -> None:
        drain = _SanitizedStderrDrain()
        session = _session(drain, _FakeClient(drain, fail=False))

        await session.aclose()

        assert "shutdown" in session.diagnostics.stderr_tail
        assert _SECRET not in session.diagnostics.stderr_tail
        assert drain.retained_bytes == 0

    async def test_failed_close_is_retried_instead_of_reporting_closed(self) -> None:
        drain = _SanitizedStderrDrain()
        client = _RetryClient(failures=1)
        session = _session(drain, client)

        with pytest.raises(RuntimeError) as excinfo:
            await session.aclose()
        assert "client close failed" in str(excinfo.value)
        assert client.exit_calls == 1

        await session.aclose()
        assert client.exit_calls == 2  # 失败后未标记已关闭，重试真正退出
        await session.aclose()
        assert client.exit_calls == 2  # 确认退出后才短路

    async def test_close_failure_still_sanitizes_and_clears_runtime_retention(self) -> None:
        drain = _SanitizedStderrDrain()
        session = _session(drain, _FakeClient(drain, fail=True))

        with pytest.raises(RuntimeError) as error:
            await session.aclose()

        assert str(error.value) == "client close failed"
        assert "shutdown" in session.diagnostics.stderr_tail
        assert _SECRET not in session.diagnostics.stderr_tail
        assert drain.retained_bytes == 0
