"""EmbeddedPostgresService 覆盖率补齐测试（Phase 2 Step 11-12）。

针对 data/persistence/embedded_postgres/service.py 83% → ≥90% 覆盖率门禁。
覆盖既有测试未触达的分支：
- A 类异常分支：PermissionError / wrong schema / password_file OSError /
  get_instance raise / _reset_singleton 异常 / _atexit_cleanup 异常
- B 类 from_config 分支：显式 sidecar_path / data_root / install_root / log_dir

Mock 策略：
- 独立 _FakePopen 实现（与 test_embedded_postgres_service.py 风格一致）
- monkeypatch + caplog 为主，patch.object 辅助
- 每个测试 finally 清理：await service.stop() + _reset_singleton()

约束：
- 不启动真实 Rust sidecar
- @pytest.mark.asyncio(loop_scope="function") 标记 async 测试
- Unix-only 测试用 pytest.mark.skipif(os.name == "nt")
"""

from __future__ import annotations

import json
import logging
import os
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


class _FakeStdout:
    """Fake stdout that returns preset line on readline."""

    def __init__(self, line: str = "") -> None:
        self._line = line

    def readline(self) -> str:
        return self._line


class _FakePopen:
    """Fake subprocess.Popen for unit tests (coverage variant).

    Independent implementation to avoid fixture conflicts with
    test_embedded_postgres_service.py and test_embedded_postgres_service_failure_injection.py.
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
    """构造 EmbeddedPostgresService 实例（用真实 Popen 但路径为 tmp_path 桩）。"""
    from data.persistence.embedded_postgres.service import EmbeddedPostgresService

    sidecar_binary = tmp_path / "fake_sidecar.exe"
    sidecar_binary.write_text("placeholder", encoding="utf-8")
    data_dir = tmp_path / "postgres" / "17" / "data"
    install_dir = tmp_path / "postgres" / "17" / "install"

    return EmbeddedPostgresService(
        sidecar_binary=sidecar_binary,
        data_dir=data_dir,
        install_dir=install_dir,
    )


class TestEmbeddedPostgresServiceCoverage:
    """覆盖率补齐测试（8 个，对应 service.py 未覆盖分支）。"""

    @pytest.mark.skipif(os.name == "nt", reason="Unix permission model required for PermissionError")
    @pytest.mark.asyncio(loop_scope="function")
    async def test_start_raises_permission_error(self, tmp_path: Path) -> None:
        """sidecar_binary 无执行权限 → PermissionError → EmbeddedPostgresStartError。"""
        from data.persistence.embedded_postgres.service import (
            EmbeddedPostgresService,
            EmbeddedPostgresStartError,
        )

        # 创建无执行权限的 sidecar_binary（chmod 0o644）
        sidecar_binary = tmp_path / "noexec_sidecar.sh"
        sidecar_binary.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
        sidecar_binary.chmod(0o644)  # 无执行位

        service = EmbeddedPostgresService(
            sidecar_binary=sidecar_binary,
            data_dir=tmp_path / "postgres" / "17" / "data",
            install_dir=tmp_path / "postgres" / "17" / "install",
        )
        try:
            with pytest.raises(EmbeddedPostgresStartError, match="sidecar binary not executable"):
                await service.start()
        finally:
            await service.stop()
            EmbeddedPostgresService._reset_singleton()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_start_rejects_wrong_ready_schema(self, tmp_path: Path) -> None:
        """ready JSON schema 不匹配 → EmbeddedPostgresStartError。"""
        from data.persistence.embedded_postgres import service as svc_module
        from data.persistence.embedded_postgres.service import EmbeddedPostgresStartError

        service = _make_service(tmp_path)
        try:
            wrong_ready = {**FAKE_READY, "schema": "wrong.v1"}

            def popen_factory(*args, **kwargs):
                inst = _FakePopen(*args, **kwargs)
                inst.set_stdout_line(json.dumps(wrong_ready) + "\n")
                # 写 password_file（避免触发 FileNotFoundError 分支）
                runtime_dir = service._data_dir.parent / "runtime"
                runtime_dir.mkdir(parents=True, exist_ok=True)
                (runtime_dir / "password").write_text("mock_pg_password_55432", encoding="utf-8")
                return inst

            with patch.object(svc_module.subprocess, "Popen", popen_factory):
                with pytest.raises(EmbeddedPostgresStartError, match="unexpected ready schema"):
                    await service.start()

                # 验证子进程被 kill（_cleanup_failed_start 调用）
                assert len(_FakePopen.instances) == 1
                fake = _FakePopen.instances[0]
                assert fake._kill_calls >= 1
        finally:
            await service.stop()
            svc_module.EmbeddedPostgresService._reset_singleton()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_start_rejects_password_file_oserror(self, tmp_path: Path) -> None:
        """password_file.read_text 抛 OSError（非 FileNotFoundError）→ EmbeddedPostgresStartError。

        构造 password 为目录（非文件），read_text 抛 IsADirectoryError（OSError 子类）。
        """
        from data.persistence.embedded_postgres import service as svc_module
        from data.persistence.embedded_postgres.service import EmbeddedPostgresStartError

        service = _make_service(tmp_path)
        try:

            def popen_factory(*args, **kwargs):
                inst = _FakePopen(*args, **kwargs)
                inst.set_stdout_line(json.dumps(FAKE_READY) + "\n")
                # 创建 password 为目录（非文件）触发 IsADirectoryError（OSError 子类）
                runtime_dir = service._data_dir.parent / "runtime"
                runtime_dir.mkdir(parents=True, exist_ok=True)
                (runtime_dir / "password").mkdir(exist_ok=True)
                return inst

            with patch.object(svc_module.subprocess, "Popen", popen_factory):
                with pytest.raises(EmbeddedPostgresStartError, match="password_file read failed"):
                    await service.start()

                # 验证子进程被 kill
                assert len(_FakePopen.instances) == 1
                fake = _FakePopen.instances[0]
                assert fake._kill_calls >= 1
        finally:
            await service.stop()
            svc_module.EmbeddedPostgresService._reset_singleton()

    def test_get_instance_raises_when_not_initialized(self) -> None:
        """get_instance 未初始化时 raise RuntimeError。"""
        from data.persistence.embedded_postgres.service import EmbeddedPostgresService

        EmbeddedPostgresService._reset_singleton()
        with pytest.raises(RuntimeError, match="singleton not initialized"):
            EmbeddedPostgresService.get_instance()

    def test_reset_singleton_logs_warning_on_stop_failure(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """_reset_singleton 中 stop_sync 抛异常 → 记 WARNING。"""
        from data.persistence.embedded_postgres.service import EmbeddedPostgresService

        service = EmbeddedPostgresService(
            sidecar_binary=tmp_path / "fake.exe",
            data_dir=tmp_path / "postgres" / "17" / "data",
            install_dir=tmp_path / "postgres" / "17" / "install",
        )
        # mock stop_sync 抛异常
        with patch.object(service, "stop_sync", side_effect=RuntimeError("stop failed")):
            with caplog.at_level(logging.WARNING, logger="qtrading.embedded_postgres"):
                EmbeddedPostgresService._reset_singleton()

            # 验证 WARNING 日志含 "_reset_singleton stop failed"
            assert any("_reset_singleton stop failed" in r.message for r in caplog.records), (
                f"期望 WARNING 日志含 '_reset_singleton stop failed'，实际：{[r.message for r in caplog.records]}"
            )

        # _reset_singleton 已将 _instance 清空，二次调用安全
        assert EmbeddedPostgresService._instance is None

    def test_atexit_cleanup_logs_warning_on_stop_failure(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """_atexit_cleanup 中 stop_sync 抛异常 → 记 WARNING。"""
        from data.persistence.embedded_postgres.service import EmbeddedPostgresService

        service = EmbeddedPostgresService(
            sidecar_binary=tmp_path / "fake.exe",
            data_dir=tmp_path / "postgres" / "17" / "data",
            install_dir=tmp_path / "postgres" / "17" / "install",
        )
        # mock stop_sync 抛异常
        with patch.object(service, "stop_sync", side_effect=RuntimeError("stop failed")):
            with caplog.at_level(logging.WARNING, logger="qtrading.embedded_postgres"):
                EmbeddedPostgresService._atexit_cleanup()

            # 验证 WARNING 日志含 "atexit cleanup failed"
            assert any("atexit cleanup failed" in r.message for r in caplog.records), (
                f"期望 WARNING 日志含 'atexit cleanup failed'，实际：{[r.message for r in caplog.records]}"
            )

        # 清理单例
        EmbeddedPostgresService._reset_singleton()

    def test_from_config_uses_explicit_paths(self, tmp_path: Path) -> None:
        """from_config 使用 AppConfig 显式设置的 sidecar_path/data_root/install_root。"""
        from data.persistence.embedded_postgres.service import EmbeddedPostgresService
        from utils.config_models import AppConfig

        sidecar_path = tmp_path / "custom_sidecar.exe"
        sidecar_path.write_text("placeholder", encoding="utf-8")
        data_root = tmp_path / "custom_data_root"
        install_root = tmp_path / "custom_install_root"

        config = AppConfig(
            embedded_pg_sidecar_path=str(sidecar_path),
            embedded_pg_data_root=str(data_root),
            embedded_pg_install_root=str(install_root),
        )
        service = EmbeddedPostgresService.from_config(config)
        try:
            assert service._sidecar_binary == sidecar_path
            assert service._data_dir == data_root / "data"
            assert service._install_dir == install_root
        finally:
            EmbeddedPostgresService._reset_singleton()

    def test_init_accepts_explicit_log_dir(self, tmp_path: Path) -> None:
        """构造时显式传入 log_dir → 不走默认推导分支。"""
        from data.persistence.embedded_postgres.service import EmbeddedPostgresService

        custom_log_dir = tmp_path / "custom-logs"
        service = EmbeddedPostgresService(
            sidecar_binary=tmp_path / "fake.exe",
            data_dir=tmp_path / "postgres" / "17" / "data",
            install_dir=tmp_path / "postgres" / "17" / "install",
            log_dir=custom_log_dir,
        )
        try:
            assert service._log_dir == custom_log_dir
        finally:
            EmbeddedPostgresService._reset_singleton()
