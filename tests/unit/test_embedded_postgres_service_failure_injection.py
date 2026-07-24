"""EmbeddedPostgresService 失败注入单元测试（补充覆盖）。

注意：本文件原设计为对应 §17.6 #1/#5/#6/#7/#10，但实际场景与 §17.6 规范条目
不精确对应（见 phase2-review-fix-plan.md Step 5 C2 分析）。真正的 §17.6
对应测试已迁移至 tests/integration/test_embedded_postgres_failure_injection.py。

本文件保留作为补充覆盖，测试场景为：
- fi_01_sidecar_binary_missing: sidecar_binary 不存在 → FileNotFoundError
  （补充覆盖 FileNotFoundError 分支，非 §17.6 #1 的 initdb_failed exit code 映射）
- fi_05_ready_timeout: readline 阻塞 + start_timeout=0.5 → 超时
  （补充覆盖 _readline_with_timeout 超时分支，非 §17.6 #5 的 exit 50 映射）
- fi_06_ready_json_invalid: readline 返回非 JSON → JSON 解析失败
  （补充覆盖 json.JSONDecodeError 分支，非 §17.6 #6 的 exit 15 映射）
- fi_07_password_file_missing: FAKE_READY 返回但不写 password_file
  （补充覆盖 password_file FileNotFoundError 分支，非 §17.6 #7 的 sha256 校验）
- fi_10_stop_kill_fallback: wait raise TimeoutExpired → kill 兜底
  （补充覆盖 stop_sync kill fallback，非 §17.6 #10 的 cancel 传播清理）

Mock 策略：
- 独立 _FakePopen 实现（避免与 test_embedded_postgres_service.py 的 fixture 冲突）
- monkeypatch subprocess.Popen 控制 Popen 行为
- 每个测试断言异常类型 + 子进程清理（kill 调用）

约束：
- 不启动真实 Rust sidecar
- @pytest.mark.asyncio(loop_scope="function") 标记 async 测试
"""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

if TYPE_CHECKING:
    from data.persistence.embedded_postgres.service import EmbeddedPostgresService

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


class _BlockingStdout:
    """Fake stdout whose readline blocks forever (for timeout test)."""

    def readline(self) -> str:
        # 模拟阻塞：永远不返回（service._readline_with_timeout 用 Queue.get(timeout) 兜底）
        import time

        time.sleep(60)
        return ""


class _FakeStdout:
    """Fake stdout that returns preset line on readline."""

    def __init__(self, line: str = "") -> None:
        self._line = line

    def readline(self) -> str:
        return self._line


class _FakePopen:
    """Fake subprocess.Popen for unit tests.

    Records argv, provides stdin/stdout mock, supports wait/kill/poll.
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
        **kwargs,
    ) -> None:
        self.argv = list(cmd)
        self.stdin = _FakeStdin()
        self._stdout_line = ""
        self._wait_side_effect: Exception | None = None
        self._wait_calls = 0
        self._kill_calls = 0
        self._poll_value: int | None = None
        self.stderr = stderr
        _FakePopen.instances.append(self)

    def set_stdout_line(self, line: str) -> None:
        self._stdout_line = line
        self.stdout = _FakeStdout(line)

    def set_blocking_stdout(self) -> None:
        self.stdout = _BlockingStdout()

    def set_wait_side_effect(self, exc: Exception) -> None:
        self._wait_side_effect = exc

    def wait(self, timeout: float | None = None) -> int:
        self._wait_calls += 1
        if self._wait_side_effect is not None and self._wait_calls == 1:
            raise self._wait_side_effect
        return 0

    def kill(self) -> None:
        self._kill_calls += 1

    def poll(self) -> int | None:
        return self._poll_value

    def set_poll(self, value: int | None) -> None:
        self._poll_value = value


@pytest.fixture(autouse=True)
def _reset_popen_instances() -> Iterator[None]:
    """每个测试前清空 FakePopen 实例记录 + 清理 logger handler + 清理 DataSanitizer 隔离。"""
    _FakePopen.instances.clear()
    _svc_logger = logging.getLogger("qtrading.embedded_postgres")
    for h in list(_svc_logger.handlers):
        if getattr(h, "_embedded_pg_handler", False):
            _svc_logger.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
    # R7: 清空 DataSanitizer._known_secrets，避免 start() 注册的 URL/password 跨测试残留
    from utils.sanitizers import DataSanitizer

    DataSanitizer._reset_known_secrets()
    yield
    _FakePopen.instances.clear()
    for h in list(_svc_logger.handlers):
        if getattr(h, "_embedded_pg_handler", False):
            _svc_logger.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
    # 测试后再清理一次，避免泄漏到下一个测试
    DataSanitizer._reset_known_secrets()


def _make_service(
    tmp_path: Path,
    *,
    start_timeout: float = 300.0,
    stop_timeout: float = 60.0,
) -> EmbeddedPostgresService:
    """构造 EmbeddedPostgresService 实例（用真实 Popen 但路径为 tmp_path 桩）。"""
    from data.persistence.embedded_postgres.service import EmbeddedPostgresService

    # sidecar_binary 用 tmp_path 下的占位（具体测试会 monkeypatch Popen）
    sidecar_binary = tmp_path / "fake_sidecar.exe"
    sidecar_binary.write_text("placeholder", encoding="utf-8")
    data_dir = tmp_path / "postgres" / "17" / "data"
    install_dir = tmp_path / "postgres" / "17" / "install"

    return EmbeddedPostgresService(
        sidecar_binary=sidecar_binary,
        data_dir=data_dir,
        install_dir=install_dir,
        start_timeout=start_timeout,
        stop_timeout=stop_timeout,
    )


# =============================================================================
# fi_01: sidecar binary 缺失（§17.6 #1）
# =============================================================================
class TestFi01SidecarBinaryMissing:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_fi_01_sidecar_binary_missing(self, tmp_path: Path) -> None:
        """sidecar_binary 不存在 → FileNotFoundError → EmbeddedPostgresStartError。"""
        from data.persistence.embedded_postgres.service import (
            EmbeddedPostgresService,
            EmbeddedPostgresStartError,
        )

        # 构造 service 用不存在的 sidecar_binary 路径（不 mock Popen，真实 FileNotFoundError）
        service = EmbeddedPostgresService(
            sidecar_binary=tmp_path / "nonexistent.exe",
            data_dir=tmp_path / "postgres" / "17" / "data",
            install_dir=tmp_path / "postgres" / "17" / "install",
        )
        try:
            with pytest.raises(EmbeddedPostgresStartError, match="sidecar binary not found"):
                await service.start()
        finally:
            await service.stop()


# =============================================================================
# fi_05: ready 超时（§17.6 #5）
# =============================================================================
class TestFi05ReadyTimeout:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_fi_05_ready_timeout(self, tmp_path: Path) -> None:
        """readline 阻塞 + start_timeout=0.5 → EmbeddedPostgresStartError，子进程 kill 被调。"""
        from data.persistence.embedded_postgres import service as svc_module
        from data.persistence.embedded_postgres.service import EmbeddedPostgresStartError

        service = _make_service(tmp_path, start_timeout=0.5)
        try:
            with patch.object(svc_module.subprocess, "Popen", _FakePopen):

                def popen_factory(*args, **kwargs):
                    inst = _FakePopen(*args, **kwargs)
                    inst.set_blocking_stdout()
                    return inst

                with patch.object(svc_module.subprocess, "Popen", popen_factory):
                    # ready 超时 → readline 返回 "" → "sidecar exited before ready line"
                    with pytest.raises(EmbeddedPostgresStartError, match="exited before ready"):
                        await service.start()

                # 验证子进程被 kill（_cleanup_failed_start 调 kill + wait）
                assert len(_FakePopen.instances) == 1
                fake = _FakePopen.instances[0]
                assert fake._kill_calls >= 1, f"期望 kill 至少调用 1 次，实际：{fake._kill_calls}"
        finally:
            await service.stop()


# =============================================================================
# fi_06: ready JSON 无效（§17.6 #6）
# =============================================================================
class TestFi06ReadyJsonInvalid:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_fi_06_ready_json_invalid(self, tmp_path: Path) -> None:
        """readline 返回非 JSON → EmbeddedPostgresStartError，子进程 kill 被调。"""
        from data.persistence.embedded_postgres import service as svc_module
        from data.persistence.embedded_postgres.service import EmbeddedPostgresStartError

        service = _make_service(tmp_path)
        try:
            with patch.object(svc_module.subprocess, "Popen", _FakePopen):

                def popen_factory(*args, **kwargs):
                    inst = _FakePopen(*args, **kwargs)
                    inst.set_stdout_line("not a json\n")
                    return inst

                with patch.object(svc_module.subprocess, "Popen", popen_factory):
                    with pytest.raises(EmbeddedPostgresStartError, match="ready JSON parse failed"):
                        await service.start()

                # 验证子进程被 kill
                assert len(_FakePopen.instances) == 1
                fake = _FakePopen.instances[0]
                assert fake._kill_calls >= 1, f"期望 kill 至少调用 1 次，实际：{fake._kill_calls}"
        finally:
            await service.stop()


# =============================================================================
# fi_07: password_file 缺失（§17.6 #7）
# =============================================================================
class TestFi07PasswordFileMissing:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_fi_07_password_file_missing(self, tmp_path: Path) -> None:
        """FAKE_READY 返回但不写 password_file → EmbeddedPostgresStartError。"""
        from data.persistence.embedded_postgres import service as svc_module
        from data.persistence.embedded_postgres.service import EmbeddedPostgresStartError

        service = _make_service(tmp_path)
        try:
            with patch.object(svc_module.subprocess, "Popen", _FakePopen):

                def popen_factory(*args, **kwargs):
                    inst = _FakePopen(*args, **kwargs)
                    # 返回有效 JSON 但故意不创建 password_file
                    inst.set_stdout_line(json.dumps(FAKE_READY) + "\n")
                    return inst

                with patch.object(svc_module.subprocess, "Popen", popen_factory):
                    with pytest.raises(EmbeddedPostgresStartError, match="password_file not found"):
                        await service.start()

                # 验证子进程被 kill
                assert len(_FakePopen.instances) == 1
                fake = _FakePopen.instances[0]
                assert fake._kill_calls >= 1, f"期望 kill 至少调用 1 次，实际：{fake._kill_calls}"
        finally:
            await service.stop()


# =============================================================================
# fi_10: stop kill 兜底（§17.6 #10）
# =============================================================================
class TestFi10StopKillFallback:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_fi_10_stop_kill_fallback(self, tmp_path: Path, caplog) -> None:
        """wait raise TimeoutExpired → stop_sync 调 proc.kill + 二次 wait(timeout=5)，记 WARNING。"""
        from data.persistence.embedded_postgres import service as svc_module
        from data.persistence.embedded_postgres.service import EmbeddedPostgresService

        service = _make_service(tmp_path, stop_timeout=0.1)
        try:
            # 先用 FakePopen + 正常 ready + password_file 启动 service
            with patch.object(svc_module.subprocess, "Popen", _FakePopen):

                def popen_factory(*args, **kwargs):
                    inst = _FakePopen(*args, **kwargs)
                    inst.set_stdout_line(json.dumps(FAKE_READY) + "\n")
                    # 写 password_file
                    runtime_dir = service._data_dir.parent / "runtime"
                    runtime_dir.mkdir(parents=True, exist_ok=True)
                    (runtime_dir / "password").write_text("mock_pg_password_55432", encoding="utf-8")
                    return inst

                with patch.object(svc_module.subprocess, "Popen", popen_factory):
                    await service.start()

                    # 设置 wait 第一次 raise TimeoutExpired，第二次正常返回
                    fake = _FakePopen.instances[-1]
                    fake.set_wait_side_effect(subprocess.TimeoutExpired(cmd=service._sidecar_binary, timeout=0.1))

                    # stop_sync 应触发 kill + 二次 wait
                    with caplog.at_level(logging.WARNING, logger="qtrading.embedded_postgres"):
                        service.stop_sync()

                    # 验证 kill 被调
                    assert fake._kill_calls >= 1, f"期望 kill 至少调用 1 次，实际：{fake._kill_calls}"
                    # 验证 wait 被调用至少 2 次（第一次 TimeoutExpired + 第二次兜底）
                    assert fake._wait_calls >= 2, f"期望 wait 至少调用 2 次，实际：{fake._wait_calls}"
                    # 验证 WARNING 日志含 timeout 信息
                    assert any("stop timeout" in r.message for r in caplog.records), (
                        f"期望 WARNING 日志含 'stop timeout'，实际：{[r.message for r in caplog.records]}"
                    )
        finally:
            try:
                await service.stop()
            except Exception:
                pass
            # 重置单例避免污染后续测试
            EmbeddedPostgresService._reset_singleton()
