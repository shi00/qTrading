"""EmbeddedPgMaintenanceService 单元测试（P3-8 TDD red→green）。

Mock 策略：
- patch ``ConfigHandler.load_config`` 返回 mock config dict，避免磁盘 IO
- patch ``ThreadPoolManager().run_async`` 直接返回 ``(exit_code, stdout, stderr)`` 元组，
  跳过真实 subprocess.run（单元测试不真实 Popen sidecar）
- 真实 sidecar 端到端验证由 P3-17 ``fake_sidecar.py`` + integration test 负责

测试分组（共 22 个）：
- TestEmbeddedPgMaintenanceServiceDoctor: doctor 命令（7 个）
- TestEmbeddedPgMaintenanceServiceDump: dump 命令（3 个）
- TestEmbeddedPgMaintenanceServiceRestore: restore 命令（3 个）
- TestEmbeddedPgMaintenanceServiceMaintenanceShell: maintenance_shell 命令（3 个）
- TestEmbeddedPgMaintenanceServiceSingleton: 单例协议（2 个）
- TestEmbeddedPgMaintenanceServiceArgv: argv 构造（4 个）
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# mock doctor JSON（对齐 sidecar maint.rs DoctorJson struct）
MOCK_DOCTOR_JSON: dict[str, object] = {
    "schema": "qtrading.embedded_postgres.doctor.v1",
    "data_dir": "/fake/data",
    "initialized": True,
    "pg_version": 16,
    "bundled_pg_major": 16,
    "version_match": True,
    "critical_files_missing": [],
    "install_dir_complete": True,
    "missing_tools": [],
    "lock_held": False,
    "postmaster_pid": 12345,
    "postgres_alive": True,
    "stale_postmaster_pid": False,
    "cluster_state": "shut down",
    "control_data_readable": True,
    "data_checksums": True,
    "hba_matches_template": True,
    "managed_block_drift": [],
    "password_file_present": True,
    "password_file_perms_ok": True,
    "fs_kind": "ntfs",
    "fs_supported": True,
    "free_bytes": 1073741824,
    "cloud_sync_feature": None,
    "state_file": "running",
    "runtime_status": "running",
    "last_start_error": None,
    "last_stop_mode": "graceful",
    "kill_fallback_count": 0,
    # §13.7.44 / §7.5 残留物扫描字段（sidecar v1+）
    "restore_residuals": [],
    "dump_partials": [],
    "issues": [],
}


@pytest.fixture
def mock_config_dict() -> dict[str, object]:
    """mock ConfigHandler.load_config 返回的 config dict。"""
    return {
        "embedded_pg_enabled": True,
        "embedded_pg_sidecar_path": "/fake/sidecar",
        "embedded_pg_data_root": "/fake/data_root",
        "embedded_pg_install_root": "/fake/install_root",
        "embedded_pg_log_dir": "/fake/log_dir",
    }


@pytest.fixture
def maintenance_service(mock_config_dict: dict[str, object]):
    """构造 EmbeddedPgMaintenanceService 实例（patch load_config）。"""
    with patch(
        "services.embedded_pg_maintenance_service.ConfigHandler.load_config",
        return_value=mock_config_dict,
    ):
        from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceService

        svc = EmbeddedPgMaintenanceService()
        yield svc


def _patch_run_async(exit_code: int, stdout: str, stderr: str = ""):
    """patch ThreadPoolManager().run_async 返回固定 (exit_code, stdout, stderr)。"""
    return patch(
        "services.embedded_pg_maintenance_service.ThreadPoolManager",
        return_value=_TPMock(exit_code=exit_code, stdout=stdout, stderr=stderr),
    )


class _DirectTP:
    """ThreadPoolManager 替身：run_async 直接同步执行 func（用于测试闭包内异常路径）。"""

    async def run_async(self, task_type, func, *args, **kwargs):
        return func(*args, **kwargs)


class _TPMock:
    """ThreadPoolManager 替身：run_async 直接返回预设元组。"""

    def __init__(self, *, exit_code: int, stdout: str, stderr: str) -> None:
        self._exit_code = exit_code
        self._stdout = stdout
        self._stderr = stderr
        self.run_async = AsyncMock(return_value=(exit_code, stdout, stderr))


# =============================================================================
# TestEmbeddedPgMaintenanceServiceDoctor: doctor 命令（7 个）
# =============================================================================
class TestEmbeddedPgMaintenanceServiceDoctor:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_doctor_success_returns_doctor_result(self, maintenance_service, mock_config_dict) -> None:
        """doctor 成功返回 DoctorResult，关键字段正确解析。"""
        from services.embedded_pg_maintenance_service import DoctorResult

        with _patch_run_async(0, json.dumps(MOCK_DOCTOR_JSON)):
            result = await maintenance_service.doctor()

        assert isinstance(result, DoctorResult)
        assert result.schema == "qtrading.embedded_postgres.doctor.v1"
        assert result.data_dir == "/fake/data"
        assert result.initialized is True
        assert result.pg_version == 16
        assert result.bundled_pg_major == 16
        assert result.version_match is True
        assert result.install_dir_complete is True
        assert result.postgres_alive is True
        assert result.issues == []
        assert result.runtime_status == "running"
        assert result.last_start_error is None
        # §13.7.44 / §7.5 残留物扫描字段（默认空列表）
        assert result.restore_residuals == []
        assert result.dump_partials == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_doctor_parses_residuals_fields(self, maintenance_service) -> None:
        """doctor JSON 含残留物扫描字段时正确解析（§13.7.44 / §7.5）。"""
        mock_with_residuals = dict(MOCK_DOCTOR_JSON)
        mock_with_residuals["restore_residuals"] = [
            "/fake/data.restore-20260723T120000Z",
            "/fake/data.restore-20260723T130000Z",
        ]
        mock_with_residuals["dump_partials"] = ["/fake/weekly.dump.partial"]
        mock_with_residuals["issues"] = [
            {"code": "restore_residual", "severity": "warning", "message": "检测到 restore 中断残留目录"},
            {"code": "dump_partial", "severity": "warning", "message": "检测到 dump 中断残留 .partial 文件"},
        ]

        with _patch_run_async(0, json.dumps(mock_with_residuals)):
            result = await maintenance_service.doctor()

        assert result.restore_residuals == [
            "/fake/data.restore-20260723T120000Z",
            "/fake/data.restore-20260723T130000Z",
        ]
        assert result.dump_partials == ["/fake/weekly.dump.partial"]
        assert any(i["code"] == "restore_residual" for i in result.issues)
        assert any(i["code"] == "dump_partial" for i in result.issues)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_doctor_backward_compat_missing_residuals_fields(self, maintenance_service) -> None:
        """旧版 sidecar 无 restore_residuals/dump_partials 字段时向后兼容（默认空列表）。"""
        mock_old = dict(MOCK_DOCTOR_JSON)
        del mock_old["restore_residuals"]
        del mock_old["dump_partials"]

        with _patch_run_async(0, json.dumps(mock_old)):
            result = await maintenance_service.doctor()

        assert result.restore_residuals == []
        assert result.dump_partials == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_doctor_schema_mismatch_raises_error(self, maintenance_service) -> None:
        """doctor JSON schema 不匹配时抛 EmbeddedPgMaintenanceError。"""
        from services.embedded_pg_maintenance_service import (
            EmbeddedPgMaintenanceError,
        )

        bad = dict(MOCK_DOCTOR_JSON)
        bad["schema"] = "qtrading.embedded_postgres.doctor.v2"
        with _patch_run_async(0, json.dumps(bad)):
            with pytest.raises(EmbeddedPgMaintenanceError, match="schema mismatch"):
                await maintenance_service.doctor()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_doctor_json_parse_failure_raises_error(self, maintenance_service) -> None:
        """doctor stdout 非法 JSON 时抛 EmbeddedPgMaintenanceError。"""
        from services.embedded_pg_maintenance_service import (
            EmbeddedPgMaintenanceError,
        )

        with _patch_run_async(0, "not-valid-json{"):
            with pytest.raises(EmbeddedPgMaintenanceError, match="JSON parse failed"):
                await maintenance_service.doctor()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_doctor_sidecar_exit_40_pgdata_corrupt(self, maintenance_service) -> None:
        """doctor exit=40 (PGDATA 损坏) 抛错并提示使用恢复向导。"""
        from services.embedded_pg_maintenance_service import (
            EmbeddedPgMaintenanceError,
        )

        with _patch_run_async(40, "", "PGDATA corrupt"):
            with pytest.raises(EmbeddedPgMaintenanceError, match="pgdata_corrupt"):
                await maintenance_service.doctor()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_doctor_sidecar_exit_50_lock_conflict(self, maintenance_service) -> None:
        """doctor exit=50 (锁冲突) 抛错并提示关闭 qTrading。"""
        from services.embedded_pg_maintenance_service import (
            EmbeddedPgMaintenanceError,
        )

        with _patch_run_async(50, "", "lock held"):
            with pytest.raises(EmbeddedPgMaintenanceError, match="lock_conflict"):
                await maintenance_service.doctor()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_doctor_sidecar_exit_20_tolerated_as_success(self, maintenance_service) -> None:
        """doctor exit=20 (PG 未运行) 容忍为成功，继续解析 JSON。

        sidecar doctor 是只读诊断，PG 未运行时仍可输出 doctor JSON。
        """
        with _patch_run_async(20, json.dumps(MOCK_DOCTOR_JSON)):
            result = await maintenance_service.doctor()

        assert result.schema == "qtrading.embedded_postgres.doctor.v1"
        assert result.initialized is True

    @pytest.mark.asyncio(loop_scope="function")
    async def test_doctor_unknown_exit_code_raises_unknown_error(self, maintenance_service) -> None:
        """doctor 未知 exit code 抛 unknown_error。"""
        from services.embedded_pg_maintenance_service import (
            EmbeddedPgMaintenanceError,
        )

        with _patch_run_async(99, "", "weird failure"):
            with pytest.raises(EmbeddedPgMaintenanceError, match="unknown_error"):
                await maintenance_service.doctor()


# =============================================================================
# TestEmbeddedPgMaintenanceServiceDump: dump 命令（3 个）
# =============================================================================
class TestEmbeddedPgMaintenanceServiceDump:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_dump_success_returns_dump_result(self, maintenance_service, tmp_path: Path) -> None:
        """dump 成功返回 DumpResult，含 output_path/file_size/exit_code。"""
        from services.embedded_pg_maintenance_service import DumpResult

        output_path = tmp_path / "backup.dump"
        output_path.write_bytes(b"fake dump content")

        with _patch_run_async(0, "dump ok"):
            result = await maintenance_service.dump(output_path)

        assert isinstance(result, DumpResult)
        assert result.output_path == str(output_path)
        assert result.file_size == len(b"fake dump content")
        assert result.exit_code == 0

    @pytest.mark.asyncio(loop_scope="function")
    async def test_dump_sidecar_failure_raises_error(self, maintenance_service, tmp_path: Path) -> None:
        """dump exit=15 (磁盘空间不足) 抛错并提示清理磁盘。"""
        from services.embedded_pg_maintenance_service import (
            EmbeddedPgMaintenanceError,
        )

        with _patch_run_async(15, "", "disk full"):
            with pytest.raises(EmbeddedPgMaintenanceError, match="disk_full"):
                await maintenance_service.dump(tmp_path / "backup.dump")

    @pytest.mark.asyncio(loop_scope="function")
    async def test_dump_sidecar_exit_50_lock_conflict(self, maintenance_service, tmp_path: Path) -> None:
        """dump exit=50 (qTrading 运行中) 抛 lock_conflict。"""
        from services.embedded_pg_maintenance_service import (
            EmbeddedPgMaintenanceError,
        )

        with _patch_run_async(50, "", "qTrading running"):
            with pytest.raises(EmbeddedPgMaintenanceError, match="lock_conflict"):
                await maintenance_service.dump(tmp_path / "backup.dump")


# =============================================================================
# TestEmbeddedPgMaintenanceServiceRestore: restore 命令（3 个）
# =============================================================================
class TestEmbeddedPgMaintenanceServiceRestore:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_restore_success_without_target_data_dir(self, maintenance_service, tmp_path: Path) -> None:
        """restore 不传 target_data_dir，target_data_dir 从 stdout 取（sidecar §12.2 原子切换）。"""
        from services.embedded_pg_maintenance_service import RestoreResult

        input_path = tmp_path / "backup.dump"
        input_path.write_bytes(b"fake")

        with _patch_run_async(0, "/restored/data/dir"):
            result = await maintenance_service.restore(input_path)

        assert isinstance(result, RestoreResult)
        assert result.target_data_dir == "/restored/data/dir"
        assert result.exit_code == 0

    @pytest.mark.asyncio(loop_scope="function")
    async def test_restore_success_with_target_data_dir(self, maintenance_service, tmp_path: Path) -> None:
        """restore 传 target_data_dir，RestoreResult.target_data_dir 等于传入值。"""
        from services.embedded_pg_maintenance_service import RestoreResult

        input_path = tmp_path / "backup.dump"
        input_path.write_bytes(b"fake")
        target = tmp_path / "new_data"

        with _patch_run_async(0, ""):
            result = await maintenance_service.restore(input_path, target)

        assert isinstance(result, RestoreResult)
        assert result.target_data_dir == str(target)
        assert result.exit_code == 0

    @pytest.mark.asyncio(loop_scope="function")
    async def test_restore_sidecar_failure_raises_error(self, maintenance_service, tmp_path: Path) -> None:
        """restore exit=40 (PGDATA 损坏) 抛 pgdata_corrupt。"""
        from services.embedded_pg_maintenance_service import (
            EmbeddedPgMaintenanceError,
        )

        input_path = tmp_path / "backup.dump"
        input_path.write_bytes(b"fake")

        with _patch_run_async(40, "", "restore failed"):
            with pytest.raises(EmbeddedPgMaintenanceError, match="pgdata_corrupt"):
                await maintenance_service.restore(input_path)


# =============================================================================
# TestEmbeddedPgMaintenanceServiceMaintenanceShell: maintenance_shell 命令（3 个）
# =============================================================================
class TestEmbeddedPgMaintenanceServiceMaintenanceShell:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_maintenance_shell_success_returns_info(self, maintenance_service) -> None:
        """maintenance_shell 成功返回 MaintenanceShellInfo（脱敏连接信息）。"""
        from services.embedded_pg_maintenance_service import MaintenanceShellInfo

        shell_json = {
            "psql_path": "/fake/install/psql",
            "connection_string_redacted": "postgresql://qtrading:***@127.0.0.1:55433/qtrading",
        }
        with _patch_run_async(0, json.dumps(shell_json)):
            result = await maintenance_service.maintenance_shell()

        assert isinstance(result, MaintenanceShellInfo)
        assert result.psql_path == "/fake/install/psql"
        # 脱敏连接串格式：postgresql://user:***@host:port/db（密码字段被 *** 替换）
        assert ":***@" in result.connection_string_redacted
        assert result.exit_code == 0

    @pytest.mark.asyncio(loop_scope="function")
    async def test_maintenance_shell_json_parse_failure_raises_error(self, maintenance_service) -> None:
        """maintenance_shell stdout 非法 JSON 时抛 EmbeddedPgMaintenanceError。"""
        from services.embedded_pg_maintenance_service import (
            EmbeddedPgMaintenanceError,
        )

        with _patch_run_async(0, "not-json"):
            with pytest.raises(EmbeddedPgMaintenanceError, match="JSON parse failed"):
                await maintenance_service.maintenance_shell()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_maintenance_shell_sidecar_failure_raises_error(self, maintenance_service) -> None:
        """maintenance_shell exit=50 抛 lock_conflict。"""
        from services.embedded_pg_maintenance_service import (
            EmbeddedPgMaintenanceError,
        )

        with _patch_run_async(50, "", "qTrading running"):
            with pytest.raises(EmbeddedPgMaintenanceError, match="lock_conflict"):
                await maintenance_service.maintenance_shell()


# =============================================================================
# TestEmbeddedPgMaintenanceServiceSingleton: 单例协议（2 个）
# =============================================================================
class TestEmbeddedPgMaintenanceServiceSingleton:
    def test_singleton_instance_reused(self) -> None:
        """EmbeddedPgMaintenanceService 单例：两次构造返回同一实例。"""
        from services.embedded_pg_maintenance_service import (
            EmbeddedPgMaintenanceService,
        )

        svc1 = EmbeddedPgMaintenanceService()
        svc2 = EmbeddedPgMaintenanceService()
        assert svc1 is svc2

    def test_reset_singleton_clears_instance(self) -> None:
        """_reset_singleton 清空 _instance，下次构造返回新实例。"""
        from services.embedded_pg_maintenance_service import (
            EmbeddedPgMaintenanceService,
        )

        svc1 = EmbeddedPgMaintenanceService()
        EmbeddedPgMaintenanceService._reset_singleton()
        svc2 = EmbeddedPgMaintenanceService()
        assert svc1 is not svc2


# =============================================================================
# TestEmbeddedPgMaintenanceServiceArgv: argv 构造（4 个）
# =============================================================================
class TestEmbeddedPgMaintenanceServiceArgv:
    def test_build_doctor_argv(self, maintenance_service, mock_config_dict) -> None:
        """_build_doctor_argv 含 sidecar path + doctor 命令 + --data-dir。"""
        argv = maintenance_service._build_doctor_argv()
        assert argv[0] == "/fake/sidecar"
        assert argv[1] == "doctor"
        assert "--data-dir" in argv
        # data_dir = <data_root>/data
        data_dir_idx = argv.index("--data-dir") + 1
        assert argv[data_dir_idx].endswith("data")

    def test_build_doctor_argv_resolves_major_when_data_root_empty(self, tmp_path: Path) -> None:
        """data_root 为空时，经 resolve_pg_major_version 动态解析 PG 主版本生成 data_dir。"""
        mock_config = {
            "embedded_pg_enabled": True,
            "embedded_pg_sidecar_path": "/fake/sidecar",
            "embedded_pg_data_root": "",
            "embedded_pg_install_root": "/fake/install_root",
            "embedded_pg_log_dir": "/fake/log_dir",
        }
        app_data = tmp_path / "app_data"
        with (
            patch(
                "services.embedded_pg_maintenance_service.ConfigHandler.load_config",
                return_value=mock_config,
            ),
            patch(
                "services.embedded_pg_maintenance_service.resolve_pg_major_version",
                return_value="16",
            ),
            patch("platformdirs.user_data_dir", return_value=str(app_data)),
        ):
            from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceService

            svc = EmbeddedPgMaintenanceService()
            argv = svc._build_doctor_argv()

        assert argv[0] == "/fake/sidecar"
        assert argv[1] == "doctor"
        data_dir_idx = argv.index("--data-dir") + 1
        assert argv[data_dir_idx] == str(app_data / "postgres" / "16" / "data")

    def test_build_dump_argv(self, maintenance_service, tmp_path: Path) -> None:
        """_build_dump_argv 含 dump + --data-dir + --output。"""
        output_path = tmp_path / "backup.dump"
        argv = maintenance_service._build_dump_argv(output_path)
        assert argv[1] == "dump"
        assert "--data-dir" in argv
        assert "--output" in argv
        output_idx = argv.index("--output") + 1
        assert argv[output_idx] == str(output_path)

    def test_build_restore_argv_without_target(self, maintenance_service, tmp_path: Path) -> None:
        """_build_restore_argv 无 target_data_dir 时不含 --target-data-dir。"""
        input_path = tmp_path / "backup.dump"
        argv = maintenance_service._build_restore_argv(input_path, None)
        assert argv[1] == "restore"
        assert "--input" in argv
        assert "--target-data-dir" not in argv

    def test_build_restore_argv_with_target(self, maintenance_service, tmp_path: Path) -> None:
        """_build_restore_argv 有 target_data_dir 时含 --target-data-dir。"""
        input_path = tmp_path / "backup.dump"
        target = tmp_path / "new_data"
        argv = maintenance_service._build_restore_argv(input_path, target)
        assert "--target-data-dir" in argv
        target_idx = argv.index("--target-data-dir") + 1
        assert argv[target_idx] == str(target)


class TestRunSidecarTimeout:
    """review03-C19: subprocess.TimeoutExpired 必须包装为 EmbeddedPgMaintenanceError（可操作提示）。

    CON-04 起改用 Popen（非 run）：超时路径须 kill 子进程并回收输出，避免泄漏。
    """

    @pytest.mark.asyncio(loop_scope="function")
    async def test_timeout_wrapped_as_business_error(self, maintenance_service) -> None:
        import subprocess

        from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceError

        proc_mock = MagicMock()
        # 第一次 communicate 抛 TimeoutExpired，第二次（kill 后回收输出）返回空
        proc_mock.communicate = MagicMock(
            side_effect=[subprocess.TimeoutExpired(cmd="sidecar", timeout=3600), ("", "")]
        )
        proc_mock.kill = MagicMock()

        with (
            patch(
                "services.embedded_pg_maintenance_service.ThreadPoolManager",
                return_value=_DirectTP(),
            ),
            patch(
                "services.embedded_pg_maintenance_service.subprocess.Popen",
                return_value=proc_mock,
            ),
        ):
            with pytest.raises(EmbeddedPgMaintenanceError, match="超时"):
                await maintenance_service._run_sidecar(["sidecar", "doctor"])

        proc_mock.kill.assert_called_once_with()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_timeout_kill_oserror_still_raises_business_error(self, maintenance_service) -> None:
        """对抗性检视：进程在超时与 kill 竞态窗口自然退出时，kill 抛 OSError 不得掩盖业务异常。"""
        import subprocess

        from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceError

        proc_mock = MagicMock()
        proc_mock.communicate = MagicMock(side_effect=[subprocess.TimeoutExpired(cmd="sidecar", timeout=3600)])
        proc_mock.kill = MagicMock(side_effect=OSError("no such process"))

        with (
            patch(
                "services.embedded_pg_maintenance_service.ThreadPoolManager",
                return_value=_DirectTP(),
            ),
            patch(
                "services.embedded_pg_maintenance_service.subprocess.Popen",
                return_value=proc_mock,
            ),
        ):
            with pytest.raises(EmbeddedPgMaintenanceError, match="超时"):
                await maintenance_service._run_sidecar(["sidecar", "doctor"])

        proc_mock.kill.assert_called_once_with()


# =============================================================================
# TestEmbeddedPgMaintenanceSanitization: R9 脱敏测试（3 个）
# =============================================================================
class TestEmbeddedPgMaintenanceSanitization:
    """R9: stderr/stdout 含路径时经 DataSanitizer.sanitize_error 脱敏。"""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_raise_for_exit_code_stderr_sanitized(self, maintenance_service, caplog) -> None:
        """T2: _raise_for_exit_code 的 stderr 含路径时，caplog 中原始路径不出现。"""
        from services.embedded_pg_maintenance_service import (
            EmbeddedPgMaintenanceError,
        )

        sensitive_path = r"D:\workspace\Quantitative Trading\pgdata\base"
        stderr = f'FATAL: data directory "{sensitive_path}" does not exist'
        with (
            _patch_run_async(40, "", stderr),
            caplog.at_level("ERROR", logger="qtrading.embedded_pg_maintenance"),
        ):
            with pytest.raises(EmbeddedPgMaintenanceError, match="pgdata_corrupt"):
                await maintenance_service.doctor()

        # 反向断言：原始路径字符串不出现在 caplog 记录中
        for record in caplog.records:
            assert sensitive_path not in record.getMessage()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_doctor_json_parse_error_stdout_sanitized(self, maintenance_service) -> None:
        """T3: doctor JSON 解析失败时异常消息中 stdout 路径被脱敏。"""
        from services.embedded_pg_maintenance_service import (
            EmbeddedPgMaintenanceError,
        )

        sensitive_path = r"D:\workspace\Quantitative Trading\pgdata\log"
        stdout = f"not-valid-json{{ path={sensitive_path}"
        with _patch_run_async(0, stdout):
            with pytest.raises(EmbeddedPgMaintenanceError, match="JSON parse failed") as exc_info:
                await maintenance_service.doctor()

        # 反向断言：原始路径字符串不出现在异常消息中
        assert sensitive_path not in str(exc_info.value)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_maintenance_shell_json_parse_error_stdout_sanitized(self, maintenance_service) -> None:
        """T3: maintenance_shell JSON 解析失败时异常消息中 stdout 路径被脱敏。"""
        from services.embedded_pg_maintenance_service import (
            EmbeddedPgMaintenanceError,
        )

        sensitive_path = r"D:\workspace\Quantitative Trading\pgdata\socket"
        stdout = f"not-json path={sensitive_path}"
        with _patch_run_async(0, stdout):
            with pytest.raises(EmbeddedPgMaintenanceError, match="JSON parse failed") as exc_info:
                await maintenance_service.maintenance_shell()

        # 反向断言：原始路径字符串不出现在异常消息中
        assert sensitive_path not in str(exc_info.value)


# =============================================================================
# TestRequestCancel: CON-04 停机终止在途 dump（3 个）
# =============================================================================
class TestRequestCancel:
    """CON-04: request_cancel 终止在途可取消命令（仅 dump 注册），无活动命令返回 False。"""

    def test_request_cancel_no_active_proc_returns_false(self, maintenance_service) -> None:
        """无活动命令（_active_proc 为 None）时返回 False，不抛异常。"""
        assert maintenance_service.request_cancel() is False

    def test_request_cancel_terminates_active_proc(self, maintenance_service) -> None:
        """有活动命令时 terminate 并返回 True。"""
        proc_mock = MagicMock()
        maintenance_service._active_proc = proc_mock
        assert maintenance_service.request_cancel() is True
        proc_mock.terminate.assert_called_once_with()

    def test_request_cancel_oserror_treated_as_terminated(self, maintenance_service) -> None:
        """terminate 抛 OSError（进程已在竞态窗口自然退出）视为已终止，仍返回 True。"""
        proc_mock = MagicMock()
        proc_mock.terminate.side_effect = OSError("no such process")
        maintenance_service._active_proc = proc_mock
        assert maintenance_service.request_cancel() is True
        proc_mock.terminate.assert_called_once_with()


# =============================================================================
# TestRunSidecarCancelAllowed: CON-04 句柄注册/清理 + 并发 guard（3 个）
# =============================================================================
class TestRunSidecarCancelAllowed:
    """CON-04: cancel_allowed=True 时注册/清理 _active_proc，并发 dump 被拒且不误清句柄。"""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_cancel_allowed_registers_and_clears_active_proc(self, maintenance_service) -> None:
        """dump 在途时 _active_proc 已注册（可被 request_cancel 终止），完成后清空。"""
        proc_mock = MagicMock()
        proc_mock.returncode = 0
        proc_mock.stdout = "dump ok"
        proc_mock.stderr = ""
        captured: dict[str, object] = {}

        def _communicate(timeout=None):
            captured["active_during_run"] = maintenance_service._active_proc
            return ("dump ok", "")

        proc_mock.communicate = MagicMock(side_effect=_communicate)

        with (
            patch(
                "services.embedded_pg_maintenance_service.ThreadPoolManager",
                return_value=_DirectTP(),
            ),
            patch(
                "services.embedded_pg_maintenance_service.subprocess.Popen",
                return_value=proc_mock,
            ),
        ):
            exit_code, stdout, stderr = await maintenance_service._run_sidecar(["sidecar", "dump"], cancel_allowed=True)

        assert (exit_code, stdout, stderr) == (0, "dump ok", "")
        assert captured["active_during_run"] is proc_mock
        assert maintenance_service._active_proc is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_concurrent_dump_guard_rejects_and_preserves_active_proc(self, maintenance_service) -> None:
        """已有 dump 在途时，第二次 dump 被拒（kill 新进程）且不误清在途句柄。"""
        from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceError

        first_proc = MagicMock()  # 已在途 dump 句柄
        second_proc = MagicMock()
        maintenance_service._active_proc = first_proc

        with (
            patch(
                "services.embedded_pg_maintenance_service.ThreadPoolManager",
                return_value=_DirectTP(),
            ),
            patch(
                "services.embedded_pg_maintenance_service.subprocess.Popen",
                return_value=second_proc,
            ),
        ):
            with pytest.raises(EmbeddedPgMaintenanceError, match="已有备份任务在进行中"):
                await maintenance_service._run_sidecar(["sidecar", "dump"], cancel_allowed=True)

        second_proc.kill.assert_called_once_with()
        second_proc.communicate.assert_called_once_with()  # 被拒进程被回收，避免僵尸/管道泄漏
        # 回归：guard 拒绝路径的 finally 不得误清在途 dump 句柄
        assert maintenance_service._active_proc is first_proc

    @pytest.mark.asyncio(loop_scope="function")
    async def test_concurrent_dump_guard_kill_oserror_still_raises(self, maintenance_service) -> None:
        """对抗性检视：被拒进程恰在竞态窗口自然退出时 kill 抛 OSError，仍抛并发拒绝业务异常。"""
        from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceError

        first_proc = MagicMock()  # 已在途 dump 句柄
        second_proc = MagicMock()
        second_proc.kill = MagicMock(side_effect=OSError("no such process"))
        maintenance_service._active_proc = first_proc

        with (
            patch(
                "services.embedded_pg_maintenance_service.ThreadPoolManager",
                return_value=_DirectTP(),
            ),
            patch(
                "services.embedded_pg_maintenance_service.subprocess.Popen",
                return_value=second_proc,
            ),
        ):
            with pytest.raises(EmbeddedPgMaintenanceError, match="已有备份任务在进行中"):
                await maintenance_service._run_sidecar(["sidecar", "dump"], cancel_allowed=True)

        second_proc.kill.assert_called_once_with()
        assert maintenance_service._active_proc is first_proc

    @pytest.mark.asyncio(loop_scope="function")
    async def test_cancel_allowed_false_not_registered(self, maintenance_service) -> None:
        """cancel_allowed=False（doctor/restore/shell）不注册 _active_proc，停机不可中断。"""
        proc_mock = MagicMock()
        proc_mock.returncode = 0
        proc_mock.stdout = ""
        proc_mock.stderr = ""
        proc_mock.communicate = MagicMock(return_value=("", ""))

        with (
            patch(
                "services.embedded_pg_maintenance_service.ThreadPoolManager",
                return_value=_DirectTP(),
            ),
            patch(
                "services.embedded_pg_maintenance_service.subprocess.Popen",
                return_value=proc_mock,
            ),
        ):
            await maintenance_service._run_sidecar(["sidecar", "doctor"], cancel_allowed=False)

        assert maintenance_service._active_proc is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_timeout_with_cancel_allowed_clears_active_proc(self, maintenance_service) -> None:
        """cancel_allowed=True 超时路径（kill 后回收输出）也应清空 _active_proc（finally 兜底）。"""
        import subprocess

        from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceError

        proc_mock = MagicMock()
        proc_mock.communicate = MagicMock(
            side_effect=[subprocess.TimeoutExpired(cmd="sidecar", timeout=3600), ("", "")]
        )
        proc_mock.kill = MagicMock()

        with (
            patch(
                "services.embedded_pg_maintenance_service.ThreadPoolManager",
                return_value=_DirectTP(),
            ),
            patch(
                "services.embedded_pg_maintenance_service.subprocess.Popen",
                return_value=proc_mock,
            ),
        ):
            with pytest.raises(EmbeddedPgMaintenanceError, match="超时"):
                await maintenance_service._run_sidecar(["sidecar", "dump"], cancel_allowed=True)

        proc_mock.kill.assert_called_once_with()
        # 注册路径的 finally 在超时异常后仍清空句柄（吸收对抗性检视 P2-4 测试缺口）
        assert maintenance_service._active_proc is None


# =============================================================================
# TestCommandCancelWiring: CON-04 各命令 cancel_allowed 接线（2 个）
# =============================================================================
class TestCommandCancelWiring:
    """CON-04: 各命令的 cancel_allowed 接线——dump 可取消，restore 拒绝取消。"""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_dump_passes_cancel_allowed_true(self, maintenance_service, tmp_path: Path) -> None:
        """dump 调用 _run_sidecar 时传 cancel_allowed=True（备份可安全中断）。"""
        calls: list[bool] = []

        async def fake_run(argv, *, cancel_allowed=False):
            calls.append(cancel_allowed)
            return 0, "dump ok", ""

        with patch.object(maintenance_service, "_run_sidecar", new=fake_run):
            await maintenance_service.dump(tmp_path / "backup.dump")

        assert calls == [True]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_restore_passes_cancel_allowed_false(self, maintenance_service, tmp_path: Path) -> None:
        """restore 调用 _run_sidecar 时传 cancel_allowed=False（数据一致性优先，拒绝取消）。"""
        calls: list[bool] = []

        async def fake_run(argv, *, cancel_allowed=False):
            calls.append(cancel_allowed)
            return 0, "", ""

        input_path = tmp_path / "backup.dump"
        input_path.write_bytes(b"fake")

        with patch.object(maintenance_service, "_run_sidecar", new=fake_run):
            await maintenance_service.restore(input_path)

        assert calls == [False]
