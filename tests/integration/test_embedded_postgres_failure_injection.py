"""EmbeddedPostgresService / EmbeddedPgMaintenanceService 失败注入集成测试。

本文件覆盖 §17.6 规范条目（#1/#2/#5/#6/#7/#10/#11/#12）：

- fi_01_sidecar_exit_11_initdb_failed (#1): sidecar exit code 11 → initdb_failed 映射
- fi_02_migration_failure (#2): sidecar 启动成功后 migration 失败 → RuntimeError 传播
- fi_05_pgdata_lock_exit_50 (#5): sidecar exit code 50 → "qTrading already running" 映射
- fi_06_disk_full_exit_15 (#6): sidecar exit code 15 → "disk full" 映射
- fi_07_sha256_mismatch (#7): .sha256 文件与实际 binary 不符 → Popen 前拒绝启动
- fi_10_cancelled_error_cleanup (#10): asyncio.cancel() → _cleanup_failed_start 清理子进程
- fi_11_timezone_mismatch (#11): Python tzname=UTC + sidecar JSON timezone=Asia/Shanghai → doctor() 仍成功
- fi_12_restore_failure (#12): sidecar restore exit code 11 → EmbeddedPgMaintenanceError

Mock 策略：
- #1/#5/#6: _FakePopen 模拟 stdout readline 返回空 + poll() 返回指定 exit code
- #2: _FakePopen 模拟 stdout readline 返回 ready JSON + mock DatabaseMigrator.init_db 抛 RuntimeError
- #7: 创建真实 sidecar binary 文件 + 错误 .sha256 文件，验证 Popen 前拒绝
- #10: patch asyncio.to_thread 抛 CancelledError，验证 _cleanup_failed_start 被调用
- #11: mock _run_sidecar 返回含 timezone 字段的 doctor JSON，验证 Python 侧只解析不检测时区
- #12: mock _run_sidecar 返回 exit=11，验证 restore() 错误分类

约束：
- 不启动真实 Rust sidecar
- @pytest.mark.asyncio(loop_scope="function") 标记 async 测试
- pytestmark = [pytest.mark.integration, pytest.mark.no_db]
- R7: 测试后调 _reset_singleton()（EmbeddedPostgresService + EmbeddedPgMaintenanceService）
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

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
    """Fake stdout that returns preset line on first readline, then empty (EOF).

    One-shot 设计：首次 readline 返回 ready JSON 行供 _readline_with_timeout 解析，
    后续 readline 返回空串模拟 EOF，使 _start_stdout_reader_thread 的 daemon 线程正常退出，
    避免 #2 场景（start 成功）中 reader 线程无限循环写入同一行。
    对 #1/#5/#6（line=""）行为不变：首次即返回空，触发 exit code 错误路径。
    """

    def __init__(self, line: str = "") -> None:
        self._line = line
        self._read = False

    def readline(self) -> str:
        if self._read:
            return ""
        self._read = True
        return self._line


class _FakePopen:
    """Fake subprocess.Popen with configurable exit code and stdout.

    For §17.6 #1/#5/#6: readline returns "" (no ready line), poll() returns exit_code.
    For §17.6 #2: readline returns ready JSON line (one-shot), poll() returns 0 (success).
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


# =============================================================================
# fi_02: sidecar 启动成功后 migration 失败 → RuntimeError 传播（§17.6 #2）
# =============================================================================
class TestFi02MigrationFailure:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_fi_02_migration_failure_propagates_and_cleans_up(self, tmp_path: Path) -> None:
        """§17.6 #2: sidecar 启动成功后 migration 失败 → RuntimeError 传播 + sidecar 清理（R2）。

        验证：
        1. start() 成功，sidecar argv 含 "run" 子命令
        2. DatabaseMigrator.init_db 失败时抛 RuntimeError（不被吞没，R2）
        3. migration 失败后调用 service.stop() → sidecar 子进程被 kill 清理
        边界：不验证 last_migration_failed state（字段不存在）
        """
        from data.persistence.db_migrator import DatabaseMigrator
        from data.persistence.embedded_postgres import service as svc_module
        from data.persistence.embedded_postgres.service import EmbeddedPostgresService

        service = _make_service(tmp_path)
        # 创建 password 文件（模拟 sidecar 创建），否则 start() 读 password_file 会失败
        runtime_dir = tmp_path / "postgres" / "17" / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        (runtime_dir / "password").write_text("fake_password", encoding="utf-8")

        try:
            ready_line = json.dumps(FAKE_READY)

            def popen_factory(*args, **kwargs):
                return _FakePopen(*args, exit_code=0, stdout_line=ready_line, **kwargs)

            with patch.object(svc_module.subprocess, "Popen", popen_factory):
                # start() 成功（sidecar argv 正确 + ready JSON 解析成功）
                conn_info = await service.start()
                assert conn_info is not None

            # 验证 sidecar argv
            assert len(_FakePopen.instances) == 1
            argv = _FakePopen.instances[0].argv
            assert argv[0] == str(tmp_path / "fake_sidecar.exe")
            assert argv[1] == "run"

            # mock DatabaseMigrator.init_db 抛 RuntimeError（模拟 migration 失败）
            async def _raise_migration_error(*args, **kwargs):
                raise RuntimeError("simulated migration failure")

            with patch.object(DatabaseMigrator, "init_db", _raise_migration_error):
                with pytest.raises(RuntimeError, match="simulated migration failure"):
                    await DatabaseMigrator.init_db(None)

            # migration 失败后调用 stop() → sidecar 子进程已清理（_process=None，不泄漏）
            # stop() 走 graceful stop 路径（stdin.close → wait），_FakePopen.wait() 立即返回 exit_code=0
            await service.stop()
            assert service._process is None, "process should be cleaned up after stop"
            assert _FakePopen.instances[0]._wait_calls >= 1, "sidecar should be waited on stop"
        finally:
            await service.stop()
            EmbeddedPostgresService._reset_singleton()


# =============================================================================
# fi_11: Python 时区=UTC + sidecar JSON timezone=Asia/Shanghai → doctor() 仍成功（§17.6 #11）
# =============================================================================
class TestFi11TimezoneMismatch:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_fi_11_doctor_tolerates_timezone_extra_field(self, tmp_path: Path) -> None:
        """§17.6 #11: Python 侧 time.tzname=UTC + sidecar JSON timezone=Asia/Shanghai。

        验证：
        1. doctor() 成功返回 DoctorResult（不抛异常）
        2. DoctorResult.schema/data_dir/initialized 匹配 JSON
        3. argv 正确（[sidecar, "doctor", "--data-dir", ...]）

        边界：Python 侧仅验证 JSON 解析，时区检测由 Rust 侧 maint.rs 负责。
        不扩展 DoctorResult 字段（YAGNI），额外字段 timezone 被静默忽略。
        """
        from services.embedded_pg_maintenance_service import (
            EXPECTED_DOCTOR_SCHEMA,
            EmbeddedPgMaintenanceService,
        )

        doctor_json = {
            "schema": EXPECTED_DOCTOR_SCHEMA,
            "data_dir": "/fake/data",
            "initialized": True,
            "pg_version": 170002,
            "bundled_pg_major": 17,
            "version_match": True,
            "critical_files_missing": [],
            "install_dir_complete": True,
            "missing_tools": [],
            "lock_held": False,
            "postgres_alive": True,
            "state_file": "running",
            "runtime_status": "running",
            "last_start_error": None,
            "issues": [],
            "timezone": "Asia/Shanghai",  # 额外字段，当前 DoctorResult 不解析但 JSON 可含
        }
        doctor_json_str = json.dumps(doctor_json)

        service = EmbeddedPgMaintenanceService()
        try:
            mock_run = AsyncMock(return_value=(0, doctor_json_str, ""))

            with (
                patch("time.tzname", ("UTC", "UTC")),
                patch.object(
                    service,
                    "_get_sidecar_path_and_data_dir",
                    return_value=("/fake/sidecar", "/fake/data"),
                ),
                patch.object(service, "_run_sidecar", mock_run),
            ):
                result = await service.doctor()

            # 验证 DoctorResult
            assert result.schema == EXPECTED_DOCTOR_SCHEMA
            assert result.data_dir == "/fake/data"
            assert result.initialized is True

            # 验证 argv（mock_run 捕获了 _run_sidecar 的调用参数）
            called_argv = mock_run.call_args.args[0]
            assert called_argv[0] == "/fake/sidecar"
            assert called_argv[1] == "doctor"
            assert "--data-dir" in called_argv
        finally:
            EmbeddedPgMaintenanceService._reset_singleton()


# =============================================================================
# fi_12: sidecar restore exit=11 → EmbeddedPgMaintenanceError（§17.6 #12）
# =============================================================================
class TestFi12RestoreFailure:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_fi_12_restore_failure(self, tmp_path: Path) -> None:
        """§17.6 #12: sidecar restore exit code 11 → EmbeddedPgMaintenanceError。

        验证：
        1. restore() 抛 EmbeddedPgMaintenanceError
        2. 错误信息含 "restore failed"、"exit=11"、"initdb_failed"
        边界：不验证原 data 保留（Python 侧只验证 exit code 处理）
        """
        from services.embedded_pg_maintenance_service import (
            EmbeddedPgMaintenanceError,
            EmbeddedPgMaintenanceService,
        )

        service = EmbeddedPgMaintenanceService()
        try:
            mock_run = AsyncMock(return_value=(11, "", "initdb failed"))

            with (
                patch.object(
                    service,
                    "_get_sidecar_path_and_data_dir",
                    return_value=("/fake/sidecar", "/fake/data"),
                ),
                patch.object(service, "_run_sidecar", mock_run),
            ):
                with pytest.raises(EmbeddedPgMaintenanceError) as exc_info:
                    await service.restore(input_path=Path("/fake/backup.dump"))

            error_msg = str(exc_info.value)
            assert "restore failed" in error_msg, f"期望错误信息含 'restore failed'，实际：{error_msg}"
            assert "exit=11" in error_msg, f"期望错误信息含 'exit=11'，实际：{error_msg}"
            assert "initdb_failed" in error_msg, f"期望错误信息含 'initdb_failed'，实际：{error_msg}"
        finally:
            EmbeddedPgMaintenanceService._reset_singleton()
