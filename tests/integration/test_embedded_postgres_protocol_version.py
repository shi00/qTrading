"""§17.6 #33: 协议版本混搭集成测试。

验证当 sidecar 返回的 ready JSON schema 主版本号与 Python 适配器期望的
_READY_SCHEMA 不一致时，start() 拒绝启动并提示"重新安装"。

场景：
- sidecar 升级后输出 v2 schema，但 Python 适配器仍期望 v1 → 拒绝启动
- 错误信息必须含 'reinstall' 提示，引导用户重新安装 qTrading 修复版本不匹配

约束：
- 不启动真实 Rust sidecar
- pytestmark = [pytest.mark.integration, pytest.mark.no_db]
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

if TYPE_CHECKING:
    pass

pytestmark = [pytest.mark.integration, pytest.mark.no_db]

FAKE_READY_V2 = {
    "schema": "qtrading.embedded_postgres.run.ready.v2",  # 主版本号 v2，与 Python v1 不匹配
    "status": "running",
    "postgres_version": "17.2.0",
    "host": "127.0.0.1",
    "port": 55432,
    "database": "qtrading",
    "username": "postgres",
    "password_source": "password_file",
    "url": "postgresql://postgres:***@127.0.0.1:55432/qtrading",
    "data_dir": "C:/fake/postgres/17/data",
    "sidecar_pid": 12340,
    "pid": 12345,
}


class _FakeStdin:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeStdout:
    def __init__(self, line: str = "") -> None:
        self._line = line

    def readline(self) -> str:
        return self._line


class _FakePopen:
    """Fake Popen with configurable stdout line."""

    instances: list[_FakePopen] = []

    def __init__(self, cmd, *, stdout_line: str = "", **kwargs) -> None:
        self.argv = list(cmd)
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout(stdout_line)
        self._kill_calls = 0
        self._wait_calls = 0
        _FakePopen.instances.append(self)

    def wait(self, timeout: float | None = None) -> int:
        self._wait_calls += 1
        return 0

    def kill(self) -> None:
        self._kill_calls += 1

    def poll(self) -> int | None:
        return None


@pytest.fixture(autouse=True)
def _reset_popen_instances() -> Iterator[None]:
    _FakePopen.instances.clear()
    _svc_logger = logging.getLogger("qtrading.embedded_postgres")
    for h in list(_svc_logger.handlers):
        if getattr(h, "_embedded_pg_handler", False):
            _svc_logger.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
    yield
    _FakePopen.instances.clear()
    for h in list(_svc_logger.handlers):
        if getattr(h, "_embedded_pg_handler", False):
            _svc_logger.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass


class TestProtocolVersionMismatch:
    """§17.6 #33: 协议版本混搭 → 拒绝启动 + 提示重新安装。"""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_protocol_v2_rejected_with_reinstall_prompt(self, tmp_path: Path) -> None:
        """§17.6 #33: sidecar 返回 v2 schema 但 Python 期望 v1 → 拒绝 + 'reinstall' 提示。

        验证：
        1. start() 抛 EmbeddedPostgresStartError
        2. 错误信息含 'unexpected ready schema' + 期望/实际 schema
        3. 错误信息含 'reinstall' 提示
        4. 子进程被 kill 清理
        """
        from data.persistence.embedded_postgres import service as svc_module
        from data.persistence.embedded_postgres.service import (
            EmbeddedPostgresService,
            EmbeddedPostgresStartError,
        )

        sidecar_binary = tmp_path / "fake_sidecar.exe"
        sidecar_binary.write_text("placeholder", encoding="utf-8")
        service = EmbeddedPostgresService(
            sidecar_binary=sidecar_binary,
            data_dir=tmp_path / "postgres" / "17" / "data",
            install_dir=tmp_path / "postgres" / "17" / "install",
        )
        try:
            v2_ready_line = json.dumps(FAKE_READY_V2) + "\n"

            def popen_factory(*args, **kwargs):
                return _FakePopen(*args, stdout_line=v2_ready_line, **kwargs)

            with patch.object(svc_module.subprocess, "Popen", popen_factory):
                with pytest.raises(EmbeddedPostgresStartError) as exc_info:
                    await service.start()

                error_msg = str(exc_info.value)
                # 验证含 schema 不匹配信息
                assert "unexpected ready schema" in error_msg, (
                    f"期望错误信息含 'unexpected ready schema'，实际：{error_msg}"
                )
                assert "qtrading.embedded_postgres.run.ready.v2" in error_msg, (
                    f"期望错误信息含实际 schema v2，实际：{error_msg}"
                )
                # 验证含 reinstall 提示（§17.6 #33 核心要求）
                assert "reinstall" in error_msg.lower(), f"期望错误信息含 'reinstall' 提示，实际：{error_msg}"

            # 验证子进程被 kill 清理
            assert len(_FakePopen.instances) == 1
            assert _FakePopen.instances[0]._kill_calls >= 1, "期望子进程被 kill 清理，实际 kill 未被调用"
        finally:
            await service.stop()
            EmbeddedPostgresService._reset_singleton()
