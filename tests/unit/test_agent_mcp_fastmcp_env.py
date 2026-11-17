"""I42：stdio 子进程环境隔离单元测试。

锁定版 SDK（fastmcp 4.2.0 / mcp 2.3.0）在 spawn 前以 ``get_default_environment() | env``
生成子进程环境（源码：``mcp/client/stdio.py`` 的 ``stdio_client`` 与
``get_default_environment``），不复制父进程完整 ``os.environ``。本文件用合成哨兵变量
与真实子进程读数证明：

1. 父进程环境中的秘密（合成哨兵）不进入子进程；
2. ``TEMP`` / ``TMP`` 指向本次连接专属受管目录，而非应用/用户临时目录；
3. 显式配置项仍生效，但不能把临时目录指回应用目录；
4. 正常关闭与连接建立失败都删除受管目录，不留残留。

哨兵与配置值均为合成字面量（非真实凭证），不触达任何外部服务。
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

import pytest
from mcp.client.stdio import get_default_environment

from core.agent_types import AgentErrorCode, AgentServiceError, MCPBudgetProfile, MCPServiceIdentity, MCPTransportKind
from services.agent_service.mcp import MCPServerConfig, MCPTransportConfig
from services.agent_service.mcp_budget import MCPBudgetGuard, MCPDeadline
from services.agent_service.mcp_fastmcp import (
    FastMCPClientFactory,
    FastMCPSession,
    MCPSendGate,
    MCPSessionDiagnostics,
    _ManagedStdioEnvironment,
    _build_stdio_env,
    _OS_BASELINE_KEYS,
)

#: 合成哨兵变量（模拟应用秘密，如伪造的 API Key）；值非真实凭证。
_SENTINEL_KEY = "SENTINEL_FAKE_OPENAI_KEY"
_SENTINEL_VALUE = "synthetic-sentinel-value-not-a-real-credential"
#: 读取子进程实际环境键的探针（只打印键名，不打印值）。
_ENV_KEYS_PROBE = "import json,os;print(json.dumps(sorted(os.environ)))"


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
        transport=MCPTransportConfig(kind=MCPTransportKind.STDIO, command=str(Path(sys.executable))),
        budget=_profile(),
    )


def _child_env_keys(env: dict[str, str]) -> list[str]:
    """按 SDK 的合并顺序（``default | provided``）spawn 真实子进程并读回环境键。"""
    merged = get_default_environment() | env
    completed = subprocess.run(
        [sys.executable, "-c", _ENV_KEYS_PROBE],
        env=merged,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return cast("list[str]", json.loads(completed.stdout.strip()))


class _FakeClient:
    """只驱动 ``aclose`` 的假客户端（不触达 SDK/进程）。"""

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


class TestStdioEnvironmentBaseline:
    def test_baseline_covers_every_sdk_inherited_key(self) -> None:
        """显式基线须覆盖 SDK 可能隐式注入的全部键，环境才完全由本模块决定。"""
        sdk_keys = {
            "APPDATA",
            "HOMEDRIVE",
            "HOMEPATH",
            "LOCALAPPDATA",
            "PATH",
            "PATHEXT",
            "PROCESSOR_ARCHITECTURE",
            "SYSTEMDRIVE",
            "SYSTEMROOT",
            "TEMP",
            "USERNAME",
            "USERPROFILE",
            "HOME",
            "LOGNAME",
            "SHELL",
            "TERM",
            "USER",
        }
        assert sdk_keys - set(_OS_BASELINE_KEYS) <= {"TEMP"}

    def test_synthetic_secret_is_not_inherited(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv(_SENTINEL_KEY, _SENTINEL_VALUE)

        env = _build_stdio_env((), temp_dir=tmp_path)

        assert _SENTINEL_KEY not in env

    def test_effective_child_environment_excludes_synthetic_secret(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv(_SENTINEL_KEY, _SENTINEL_VALUE)
        monkeypatch.setenv("SENTINEL_FAKE_DB_PASSWORD", _SENTINEL_VALUE)

        keys = _child_env_keys(_build_stdio_env((), temp_dir=tmp_path))

        assert _SENTINEL_KEY not in keys
        assert "SENTINEL_FAKE_DB_PASSWORD" not in keys
        # 白名单不得漏掉解释器启动必需项，否则子进程根本起不来（上方 subprocess 已能启动）。
        assert "PATH" in keys


class TestManagedTempDirectory:
    def test_temp_and_tmp_point_to_managed_directory(self) -> None:
        managed = _ManagedStdioEnvironment.create(())
        try:
            env = managed.env
            assert env["TEMP"] == env["TMP"] == str(managed.temp_dir)
            assert managed.temp_dir.is_dir()
            assert managed.temp_dir != Path(tempfile.gettempdir())
            assert managed.temp_dir.name.startswith("astock-mcp-stdio-")
            if os.name != "nt":
                assert stat.S_IMODE(managed.temp_dir.stat().st_mode) == 0o700
        finally:
            managed.cleanup()

    def test_concurrent_environments_use_distinct_directories(self) -> None:
        first = _ManagedStdioEnvironment.create(())
        second = _ManagedStdioEnvironment.create(())
        try:
            assert first.temp_dir != second.temp_dir
            assert first.temp_dir.is_dir() and second.temp_dir.is_dir()
        finally:
            first.cleanup()
            second.cleanup()

    def test_configured_entries_apply_but_cannot_redirect_temp(self) -> None:
        configured = (("MCP_SERVER_TOKEN", "synthetic-config-value"), ("TEMP", str(Path(tempfile.gettempdir()))))
        managed = _ManagedStdioEnvironment.create(configured)
        try:
            assert managed.env["MCP_SERVER_TOKEN"] == "synthetic-config-value"
            assert managed.env["TEMP"] == str(managed.temp_dir)
            assert managed.env["TMP"] == str(managed.temp_dir)
        finally:
            managed.cleanup()

    def test_cleanup_removes_directory_and_is_idempotent(self) -> None:
        managed = _ManagedStdioEnvironment.create(())
        temp_dir = managed.temp_dir
        (temp_dir / "scratch.tmp").write_text("x", encoding="utf-8")

        managed.cleanup()
        managed.cleanup()

        assert not temp_dir.exists()

    async def test_session_close_removes_managed_directory(self) -> None:
        managed = _ManagedStdioEnvironment.create(())
        session = FastMCPSession(
            cast("Any", _FakeClient()),
            config=_config(),
            guard=MCPBudgetGuard(_profile(), identity="svc-test"),
            gate=MCPSendGate(
                alive=lambda: True,
                deadline_factory=lambda seconds: MCPDeadline(seconds=seconds),
                identity="svc-test",
            ),
            diagnostics=MCPSessionDiagnostics(),
            stderr_drain=None,
            on_close=lambda: None,
            stdio_env=managed,
        )

        await session.aclose()

        assert not managed.temp_dir.exists()

    async def test_transport_setup_failure_leaves_no_temp_directory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        created: list[_ManagedStdioEnvironment] = []
        original_create = _ManagedStdioEnvironment.__dict__["create"].__func__

        def _capture_create(cls: type[_ManagedStdioEnvironment], configured: tuple[tuple[str, str], ...]) -> Any:
            instance = original_create(cls, configured)
            created.append(instance)
            return instance

        def _failing_transport(
            self: FastMCPClientFactory,
            config: MCPServerConfig,
            drain: Any,
            *,
            stdio_env: _ManagedStdioEnvironment | None,
            diagnostics: MCPSessionDiagnostics,
            label: str,
        ) -> Any:
            raise RuntimeError("transport boom")

        monkeypatch.setattr(_ManagedStdioEnvironment, "create", classmethod(_capture_create))
        monkeypatch.setattr(FastMCPClientFactory, "_transport", _failing_transport)

        with pytest.raises(AgentServiceError) as excinfo:
            await FastMCPClientFactory().open(_config())

        assert excinfo.value.code is AgentErrorCode.CONFIG_UNAVAILABLE
        assert created
        assert not created[0].temp_dir.exists()
