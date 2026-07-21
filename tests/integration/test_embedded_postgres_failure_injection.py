"""EmbeddedPostgresService 失败注入集成测试（§17.6 #1/#5/#6/#7/#10）。

本文件重写自 tests/unit/test_embedded_postgres_service_failure_injection.py，
确保 5 个测试场景真正对应 §17.6 规范条目（而非旧版的近似场景）：

- fi_01_sidecar_exit_11_initdb_failed (#1): sidecar exit code 11 → initdb_failed 映射
- fi_05_pgdata_lock_exit_50 (#5): sidecar exit code 50 → "qTrading already running" 映射
- fi_06_disk_full_exit_15 (#6): sidecar exit code 15 → "disk full" 映射
- fi_07_sha256_mismatch (#7): .sha256 文件与实际 binary 不符 → Popen 前拒绝启动
- fi_10_cancelled_error_cleanup (#10): asyncio.cancel() → _cleanup_failed_start 清理子进程

Mock 策略：
- #1/#5/#6: _FakePopen 模拟 stdout readline 返回空 + poll() 返回指定 exit code
- #7: 创建真实 sidecar binary 文件 + 错误 .sha256 文件，验证 Popen 前拒绝
- #10: patch asyncio.to_thread 抛 CancelledError，验证 _cleanup_failed_start 被调用

约束：
- 不启动真实 Rust sidecar
- @pytest.mark.asyncio(loop_scope="function") 标记 async 测试
- pytestmark = [pytest.mark.integration, pytest.mark.no_db]
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

if TYPE_CHECKING:
    from data.persistence.embedded_postgres.service import EmbeddedPostgresService

pytestmark = [pytest.mark.integration, pytest.mark.no_db]

FAKE_READY = {
    "schema": "qtrading.embedded_postgres.run.ready.v1",
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
    """Fake stdin for Popen mock."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeStdout:
    """Fake stdout that returns preset line on readline."""

    def __init__(self, line: str = "") -> None:
        self._line = line

    def readline(self) -> str:
        return self._line


class _FakePopen:
    """Fake subprocess.Popen with configurable exit code and stdout.

    For §17.6 #1/#5/#6: readline returns "" (no ready line), poll() returns exit_code.
    """

    instances: list[_FakePopen] = []

    def __init__(
        self,
        cmd: list[str],
        *,
        stdin=None,
        stdout=None,
        stderr=None,
        text=False,
        encoding=None,
        errors=None,
        bufsize=0,
        exit_code: int = 0,
        stdout_line: str = "",
        **kwargs,
    ) -> None:
        self.argv = list(cmd)
        self.stdin = _FakeStdin()
        self._exit_code = exit_code
        self._stdout_line = stdout_line
        self.stdout = _FakeStdout(stdout_line)
        self._wait_calls = 0
        self._kill_calls = 0
        self.stderr = stderr
        _FakePopen.instances.append(self)

    def wait(self, timeout: float | None = None) -> int:
        self._wait_calls += 1
        return self._exit_code

    def kill(self) -> None:
        self._kill_calls += 1

    def poll(self) -> int | None:
        return self._exit_code


@pytest.fixture(autouse=True)
def _reset_popen_instances() -> Iterator[None]:
    """每个测试前清空 FakePopen 实例记录 + 清理 logger handler 隔离。"""
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


def _make_service(tmp_path: Path) -> EmbeddedPostgresService:
    """构造 EmbeddedPostgresService 实例（路径用 tmp_path 桩）。"""
    from data.persistence.embedded_postgres.service import EmbeddedPostgresService

    sidecar_binary = tmp_path / "fake_sidecar.exe"
    sidecar_binary.write_text("placeholder", encoding="utf-8")
    return EmbeddedPostgresService(
        sidecar_binary=sidecar_binary,
        data_dir=tmp_path / "postgres" / "17" / "data",
        install_dir=tmp_path / "postgres" / "17" / "install",
    )


# =============================================================================
# fi_01: sidecar exit code 11 → initdb_failed（§17.6 #1）
# =============================================================================
class TestFi01SidecarExit11InitdbFailed:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_fi_01_sidecar_exit_11_initdb_failed(self, tmp_path: Path) -> None:
        """§17.6 #1: sidecar 返回 exit code 11 → EmbeddedPostgresStartError 含 'initdb failed'。"""
        from data.persistence.embedded_postgres import service as svc_module
        from data.persistence.embedded_postgres.service import (
            EmbeddedPostgresService,
            EmbeddedPostgresStartError,
        )

        service = _make_service(tmp_path)
        try:
            # mock Popen：stdout readline 返回空（无 ready line），poll 返回 11
            def popen_factory(*args, **kwargs):
                return _FakePopen(*args, exit_code=11, stdout_line="", **kwargs)

            with patch.object(svc_module.subprocess, "Popen", popen_factory):
                with pytest.raises(EmbeddedPostgresStartError, match="initdb failed"):
                    await service.start()

            # 验证子进程被 kill（_cleanup_failed_start 调用）
            assert len(_FakePopen.instances) == 1
            assert _FakePopen.instances[0]._kill_calls >= 1
        finally:
            await service.stop()
            EmbeddedPostgresService._reset_singleton()


# =============================================================================
# fi_05: sidecar exit code 50 → qTrading already running（§17.6 #5）
# =============================================================================
class TestFi05PgdataLockExit50:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_fi_05_pgdata_lock_exit_50(self, tmp_path: Path) -> None:
        """§17.6 #5: sidecar 返回 exit code 50 → 错误信息含 'qTrading already running'。"""
        from data.persistence.embedded_postgres import service as svc_module
        from data.persistence.embedded_postgres.service import (
            EmbeddedPostgresService,
            EmbeddedPostgresStartError,
        )

        service = _make_service(tmp_path)
        try:

            def popen_factory(*args, **kwargs):
                return _FakePopen(*args, exit_code=50, stdout_line="", **kwargs)

            with patch.object(svc_module.subprocess, "Popen", popen_factory):
                with pytest.raises(EmbeddedPostgresStartError, match="qTrading already running"):
                    await service.start()

            assert len(_FakePopen.instances) == 1
            assert _FakePopen.instances[0]._kill_calls >= 1
        finally:
            await service.stop()
            EmbeddedPostgresService._reset_singleton()


# =============================================================================
# fi_06: sidecar exit code 15 → disk full（§17.6 #6）
# =============================================================================
class TestFi06DiskFullExit15:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_fi_06_disk_full_exit_15(self, tmp_path: Path) -> None:
        """§17.6 #6: sidecar 返回 exit code 15 → 错误信息含 'disk full'。"""
        from data.persistence.embedded_postgres import service as svc_module
        from data.persistence.embedded_postgres.service import (
            EmbeddedPostgresService,
            EmbeddedPostgresStartError,
        )

        service = _make_service(tmp_path)
        try:

            def popen_factory(*args, **kwargs):
                return _FakePopen(*args, exit_code=15, stdout_line="", **kwargs)

            with patch.object(svc_module.subprocess, "Popen", popen_factory):
                with pytest.raises(EmbeddedPostgresStartError, match="disk full"):
                    await service.start()

            assert len(_FakePopen.instances) == 1
            assert _FakePopen.instances[0]._kill_calls >= 1
        finally:
            await service.stop()
            EmbeddedPostgresService._reset_singleton()


# =============================================================================
# fi_07: SHA256 校验失败 → Popen 前拒绝启动（§17.6 #7）
# =============================================================================
class TestFi07Sha256Mismatch:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_fi_07_sha256_mismatch_rejects_before_popen(self, tmp_path: Path) -> None:
        """§17.6 #7: .sha256 文件与实际 binary 不符 → Popen 前抛 EmbeddedPostgresStartError。

        验证：
        1. start() 在 Popen 前抛 EmbeddedPostgresStartError
        2. 错误信息含 'sha256 mismatch' 和 'reinstall'
        3. subprocess.Popen 从未被调用（Popen 前已拒绝）
        """
        from data.persistence.embedded_postgres import service as svc_module
        from data.persistence.embedded_postgres.service import (
            EmbeddedPostgresService,
            EmbeddedPostgresStartError,
        )

        sidecar_binary = tmp_path / "fake_sidecar.exe"
        sidecar_binary.write_bytes(b"real binary content")
        # 写错误的 .sha256 文件（实际 hash 不匹配）
        wrong_hash = hashlib.sha256(b"different content").hexdigest()
        sha256_file = sidecar_binary.with_suffix(".exe.sha256")
        sha256_file.write_text(f"{wrong_hash}  {sidecar_binary.name}", encoding="utf-8")

        service = EmbeddedPostgresService(
            sidecar_binary=sidecar_binary,
            data_dir=tmp_path / "postgres" / "17" / "data",
            install_dir=tmp_path / "postgres" / "17" / "install",
        )
        popen_call_count = {"n": 0}
        try:

            def popen_factory(*args, **kwargs):
                popen_call_count["n"] += 1
                return _FakePopen(*args, **kwargs)

            with patch.object(svc_module.subprocess, "Popen", popen_factory):
                with pytest.raises(EmbeddedPostgresStartError) as exc_info:
                    await service.start()

                # 验证错误信息含 sha256 mismatch + reinstall 提示
                error_msg = str(exc_info.value)
                assert "sha256 mismatch" in error_msg, f"期望错误信息含 'sha256 mismatch'，实际：{error_msg}"
                assert "reinstall" in error_msg.lower(), f"期望错误信息含 'reinstall' 提示，实际：{error_msg}"

            # 验证 Popen 从未被调用（sha256 校验在 Popen 前）
            assert popen_call_count["n"] == 0, f"期望 Popen 未被调用，实际调用 {popen_call_count['n']} 次"
        finally:
            await service.stop()
            EmbeddedPostgresService._reset_singleton()


# =============================================================================
# fi_10: asyncio.cancel() → _cleanup_failed_start 清理子进程（§17.6 #10）
# =============================================================================
class TestFi10CancelledErrorCleanup:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_fi_10_cancelled_error_cleanup(self, tmp_path: Path) -> None:
        """§17.6 #10: asyncio.cancel() 启动协程 → _cleanup_failed_start 清理 sidecar。

        验证：
        1. CancelledError 被 start() 重新 raise（R2 合规）
        2. _cleanup_failed_start 被调用（子进程不泄漏）
        3. 无遗留进程（_process 在 cancel 后应被清理或为 None）
        """
        import asyncio

        from data.persistence.embedded_postgres.service import EmbeddedPostgresService

        service = _make_service(tmp_path)
        cleanup_called = {"v": False}
        original_cleanup = service._cleanup_failed_start

        def tracking_cleanup():
            cleanup_called["v"] = True
            original_cleanup()

        service._cleanup_failed_start = tracking_cleanup  # type: ignore[assignment]  # [reason: 测试 monkeypatch 替换实例方法]

        # mock asyncio.to_thread 抛 CancelledError（模拟取消传播）
        async def raise_cancelled(func, *args, **kwargs):
            raise asyncio.CancelledError()

        try:
            with patch("asyncio.to_thread", raise_cancelled):
                # CancelledError 无消息可 match；用 as exc_info 捕获后断言类型（R2 红线验证）
                with pytest.raises(asyncio.CancelledError) as exc_info:
                    await service.start()
                assert isinstance(exc_info.value, asyncio.CancelledError)

            # 验证 _cleanup_failed_start 被调用
            assert cleanup_called["v"] is True, "_cleanup_failed_start 未被调用，子进程可能泄漏"
            # 验证无遗留进程（cancel 后 _process 应为 None，因 _cleanup_failed_start 清空）
            # 注：由于 mock to_thread 抛 CancelledError，_start_sync 从未执行，
            # _process 仍为 None（构造时初值）；但 _cleanup_failed_start 仍应被调用确保安全
            assert service._process is None, f"cancel 后 _process 应为 None，实际：{service._process}"
        finally:
            service._cleanup_failed_start = original_cleanup  # type: ignore[assignment]  # [reason: 测试 monkeypatch 恢复实例方法]
            await service.stop()
            EmbeddedPostgresService._reset_singleton()
