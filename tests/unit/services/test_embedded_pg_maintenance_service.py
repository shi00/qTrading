"""EmbeddedPgMaintenanceService 单元测试（P3-8 TDD red→green）。

Mock 策略：
- patch ``ConfigHandler.load_config`` 返回 mock config dict，避免磁盘 IO
- patch ``ThreadPoolManager().run_async`` 直接返回 ``(exit_code, stdout, stderr)`` 元组，
  跳过真实 subprocess.run（单元测试不真实 Popen sidecar）
- 真实 sidecar 端到端验证由 P3-17 ``fake_sidecar.py`` + integration test 负责

测试分组：
- TestEmbeddedPgMaintenanceServiceDoctor: doctor 命令（7 个）
- TestEmbeddedPgMaintenanceServiceDump: dump 命令（3 个）
- TestEmbeddedPgMaintenanceServiceRestore: restore 命令（3 个）
- TestEmbeddedPgMaintenanceServiceMaintenanceShell: maintenance_shell 命令（3 个）
- TestEmbeddedPgMaintenanceServiceResetPassword: reset_password 命令与 --username 透传（M23）
- TestEmbeddedPgMaintenanceServiceSingleton: 单例协议（2 个）
- TestEmbeddedPgMaintenanceServiceArgv: argv 构造（UIR-F16-002 库名透传与拒绝、
  M21 用户名透传与白名单校验、M23 reset-password argv 与错误码）
- TestSidecarUtf8Decoding: D16-F08 显式 UTF-8 解码与非 UTF-8 locale 回归
- TestRunSidecarCreationFlags: Windows 防弹控制台窗（Popen creationflags）
- TestRequestCancel / TestDumpCancellationIntegrity / TestProcessTreeProof: D07-007
  取消到达 pg 子进程、停止证明与 target 完整性
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

# os.getpgid / os.killpg 仅 POSIX 存在；Windows 上 monkeypatch.setattr(os, ...) 会 AttributeError。
_requires_posix_group_api = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX 进程组语义（os.getpgid/os.killpg），Windows 不适用",
)


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
    # UIR-F15-003: 运行中实例的实际监听端口（随机端口，与配置端口无关）
    "port": 55432,
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
        assert result.port == 55432
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
    async def test_doctor_backward_compat_missing_port(self, maintenance_service) -> None:
        """旧版 sidecar 无 port 字段时解析为 None（未知），不伪造端口。"""
        mock_old = dict(MOCK_DOCTOR_JSON)
        del mock_old["port"]

        with _patch_run_async(0, json.dumps(mock_old)):
            result = await maintenance_service.doctor()

        assert result.port is None

    @pytest.mark.parametrize("bad_port", [0, -1, 65536, True, "55432", None])
    @pytest.mark.asyncio(loop_scope="function")
    async def test_doctor_rejects_invalid_port(self, maintenance_service, bad_port: object) -> None:
        """端口越界/类型非法/为 null 时解析为 None（未知），不当作 0 或有效端口。"""
        mock_bad = dict(MOCK_DOCTOR_JSON)
        mock_bad["port"] = bad_port

        with _patch_run_async(0, json.dumps(mock_bad)):
            result = await maintenance_service.doctor()

        assert result.port is None

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
    async def test_doctor_sidecar_exit_40_data_dir_abnormal(self, maintenance_service) -> None:
        """doctor exit=40 (DATA_DIR_ABNORMAL) 抛错并提示先运行 doctor/按恢复文档处理。"""
        from services.embedded_pg_maintenance_service import (
            EmbeddedPgMaintenanceError,
        )

        with _patch_run_async(40, "", "PGDATA corrupt"):
            with pytest.raises(EmbeddedPgMaintenanceError, match="data_dir_abnormal"):
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
    async def test_doctor_sidecar_exit_20_stop_failed_raises(self, maintenance_service) -> None:
        """doctor exit=20 (STOP_FAILED) 不得容忍为成功。

        doctor 契约只返回 0/2；20 表示停止 PostgreSQL 失败，不是「PG 未运行」。
        """
        from services.embedded_pg_maintenance_service import (
            EmbeddedPgMaintenanceError,
        )

        with _patch_run_async(20, json.dumps(MOCK_DOCTOR_JSON), "stop failed"):
            with pytest.raises(EmbeddedPgMaintenanceError, match="stop_failed") as exc_info:
                await maintenance_service.doctor()

        assert "exit=20" in str(exc_info.value)
        assert "未运行" not in str(exc_info.value)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_doctor_exit_0_with_error_issue_returns_diagnostic_result(self, maintenance_service) -> None:
        """doctor exit 0 + issues.severity=error 仍是诊断结果，不是健康证书、也不抛错。"""
        mock_with_error = dict(MOCK_DOCTOR_JSON)
        mock_with_error["issues"] = [
            {"code": "nonempty_no_pg_version", "severity": "error", "message": "run 将返回 exit 40"}
        ]

        with _patch_run_async(0, json.dumps(mock_with_error)):
            result = await maintenance_service.doctor()

        assert result.issues == mock_with_error["issues"]
        assert result.issues[0]["severity"] == "error"

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
# TestExitCodeMapping: exit_codes.rs 常量逐项对齐（参数化）
# =============================================================================
class TestExitCodeMapping:
    """sidecar exit code → 错误类型/用户提示必须与 exit_codes.rs / how-to §9.6 对齐。"""

    @pytest.mark.parametrize(
        ("exit_code", "error_type", "hint_fragment"),
        [
            (2, "sidecar_argument_error", "参数错误"),
            (10, "setup_failed", "安装/解压失败"),
            (11, "initdb_failed", "数据库初始化失败"),
            (12, "pg_start_failed", "数据库启动失败"),
            (13, "health_check_failed", "健康检查失败"),
            (14, "create_database_failed", "业务数据库创建失败"),
            (15, "preflight_failed", "资源预检失败"),
            (16, "password_failed", "reset-password"),
            (20, "stop_failed", "停止 PostgreSQL 失败"),
            (30, "dump_restore_failed", "备份/恢复执行失败"),
            (40, "data_dir_abnormal", "数据目录状态异常"),
            (50, "lock_conflict", "关闭 qTrading"),
            (60, "postgres_died", "意外退出"),
        ],
    )
    def test_known_exit_code_mapping(
        self, maintenance_service, exit_code: int, error_type: str, hint_fragment: str
    ) -> None:
        """每个常量返回对应错误类型与提示，且保留原始 exit code。"""
        from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceError

        with pytest.raises(EmbeddedPgMaintenanceError) as exc_info:
            maintenance_service._raise_for_exit_code(exit_code, "sidecar reason", "dump")

        message = str(exc_info.value)
        assert f"type={error_type}" in message
        assert f"exit={exit_code}" in message
        assert hint_fragment in message
        assert "reason=sidecar reason" in message

    def test_user_hints_survive_log_sanitization(self, maintenance_service) -> None:
        """用户提示经日志脱敏后不得被误判为路径而截断（如「用户名/库名/数据目录」）。"""
        from services.embedded_pg_maintenance_service import (
            _EXIT_CODE_MAP,
            EmbeddedPgMaintenanceError,
        )
        from utils.sanitizers import DataSanitizer

        for exit_code in _EXIT_CODE_MAP:
            with pytest.raises(EmbeddedPgMaintenanceError) as exc_info:
                maintenance_service._raise_for_exit_code(exit_code, "", "dump")
            message = str(exc_info.value)
            assert DataSanitizer.sanitize_error(message) == message, f"exit={exit_code} 的提示被脱敏截断"

    def test_exit_10_is_setup_failed_not_argument_error(self, maintenance_service) -> None:
        """10=SETUP_FAILED 不得说成参数错误。"""
        from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceError

        with pytest.raises(EmbeddedPgMaintenanceError, match="setup_failed") as exc_info:
            maintenance_service._raise_for_exit_code(10, "install dir incomplete", "dump")

        assert "参数错误" not in str(exc_info.value)

    def test_exit_15_preflight_does_not_claim_disk_full(self, maintenance_service) -> None:
        """15=PREFLIGHT_FAILED 为多原因通用码，不得声称磁盘满或编造磁盘量。"""
        from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceError

        with pytest.raises(EmbeddedPgMaintenanceError, match="preflight_failed") as exc_info:
            maintenance_service._raise_for_exit_code(15, "password file 权限过宽 /fake/password", "run")

        message = str(exc_info.value)
        assert "资源预检失败" in message
        assert "磁盘空间不足" not in message
        assert "500" not in message
        assert "password file 权限过宽" in message

    def test_exit_20_stop_failed_not_pg_not_running(self, maintenance_service) -> None:
        """20=STOP_FAILED 不得映射为 pg_not_running 或视为成功。"""
        from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceError

        with pytest.raises(EmbeddedPgMaintenanceError, match="stop_failed") as exc_info:
            maintenance_service._raise_for_exit_code(20, "kill fallback failed", "stop")

        message = str(exc_info.value)
        assert "pg_not_running" not in message
        assert "未运行" not in message

    def test_unknown_exit_code_stays_nonzero_failure(self, maintenance_service) -> None:
        """未知码仍抛业务异常（unknown_error），不因未知而放行。"""
        from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceError

        with pytest.raises(EmbeddedPgMaintenanceError, match="unknown_error") as exc_info:
            maintenance_service._raise_for_exit_code(99, "weird failure", "restore")

        assert "exit=99" in str(exc_info.value)


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
    async def test_dump_sidecar_preflight_failure_not_claimed_as_disk_full(
        self, maintenance_service, tmp_path: Path
    ) -> None:
        """dump exit=15 为资源预检通用码，不得声称磁盘空间不足或编造磁盘量。"""
        from services.embedded_pg_maintenance_service import (
            EmbeddedPgMaintenanceError,
        )

        with _patch_run_async(15, "", "目录不可写：/fake/data"):
            with pytest.raises(EmbeddedPgMaintenanceError, match="preflight_failed") as exc_info:
                await maintenance_service.dump(tmp_path / "backup.dump")

        message = str(exc_info.value)
        assert "目录不可写" in message  # 引用脱敏后的真实原因
        assert "磁盘空间不足" not in message
        assert "500" not in message

    @pytest.mark.asyncio(loop_scope="function")
    async def test_dump_sidecar_exit_50_lock_conflict(self, maintenance_service, tmp_path: Path) -> None:
        """dump exit=50 (qTrading 运行中) 抛 lock_conflict。"""
        from services.embedded_pg_maintenance_service import (
            EmbeddedPgMaintenanceError,
        )

        with _patch_run_async(50, "", "qTrading running"):
            with pytest.raises(EmbeddedPgMaintenanceError, match="lock_conflict"):
                await maintenance_service.dump(tmp_path / "backup.dump")

    @pytest.mark.asyncio(loop_scope="function")
    async def test_dump_sidecar_exit_30_output_conflict(self, maintenance_service, tmp_path: Path) -> None:
        """D16-F06: 输出已存在/另一任务占用同一输出（exit 30）→ dump_restore_failed。"""
        from services.embedded_pg_maintenance_service import (
            EmbeddedPgMaintenanceError,
        )

        with _patch_run_async(30, "", "output exists or locked"):
            with pytest.raises(EmbeddedPgMaintenanceError, match="dump_restore_failed"):
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
        """restore exit=40 (DATA_DIR_ABNORMAL) 抛 data_dir_abnormal。"""
        from services.embedded_pg_maintenance_service import (
            EmbeddedPgMaintenanceError,
        )

        input_path = tmp_path / "backup.dump"
        input_path.write_bytes(b"fake")

        with _patch_run_async(40, "", "restore failed"):
            with pytest.raises(EmbeddedPgMaintenanceError, match="data_dir_abnormal"):
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

    # ---- UIR-F16-002：库名来自配置（不得硬编码 qtrading）----

    def test_build_dump_argv_default_database_is_qtrading(self, maintenance_service, tmp_path: Path) -> None:
        """默认配置（未显式设置 embedded_pg_database）仍透传 qtrading，兼容既有部署。"""
        argv = maintenance_service._build_dump_argv(tmp_path / "backup.dump")
        database_idx = argv.index("--database") + 1
        assert argv[database_idx] == "qtrading"

    def test_build_restore_argv_default_database_is_qtrading(self, maintenance_service, tmp_path: Path) -> None:
        """默认配置下 restore 同样透传 qtrading。"""
        argv = maintenance_service._build_restore_argv(tmp_path / "backup.dump", None)
        database_idx = argv.index("--database") + 1
        assert argv[database_idx] == "qtrading"

    def test_build_dump_argv_passes_configured_database(
        self, maintenance_service, mock_config_dict: dict[str, object], tmp_path: Path
    ) -> None:
        """自定义 embedded_pg_database 时 dump 透传该库名（备份作用域正确）。"""
        config = {**mock_config_dict, "embedded_pg_database": "stock_db"}
        with patch(
            "services.embedded_pg_maintenance_service.ConfigHandler.load_config",
            return_value=config,
        ):
            argv = maintenance_service._build_dump_argv(tmp_path / "backup.dump")
        database_idx = argv.index("--database") + 1
        assert argv[database_idx] == "stock_db"

    def test_build_restore_argv_passes_configured_database(
        self, maintenance_service, mock_config_dict: dict[str, object], tmp_path: Path
    ) -> None:
        """自定义库名时 restore 透传该库名（恢复目标库正确）。"""
        config = {**mock_config_dict, "embedded_pg_database": "stock_db"}
        target = tmp_path / "new_data"
        with patch(
            "services.embedded_pg_maintenance_service.ConfigHandler.load_config",
            return_value=config,
        ):
            argv = maintenance_service._build_restore_argv(tmp_path / "backup.dump", target)
        database_idx = argv.index("--database") + 1
        assert argv[database_idx] == "stock_db"
        assert "--target-data-dir" in argv

    def test_build_maintenance_shell_argv_passes_configured_database(
        self, maintenance_service, mock_config_dict: dict[str, object]
    ) -> None:
        """D07-005：maintenance-shell 连接串/psql 提示须使用配置库名，不得回落 qtrading。"""
        config = {**mock_config_dict, "embedded_pg_database": "stock_db"}
        with patch(
            "services.embedded_pg_maintenance_service.ConfigHandler.load_config",
            return_value=config,
        ):
            argv = maintenance_service._build_maintenance_shell_argv()
        assert argv[1] == "maintenance-shell"
        database_idx = argv.index("--database") + 1
        assert argv[database_idx] == "stock_db"

    def test_build_maintenance_shell_argv_default_database_is_qtrading(self, maintenance_service) -> None:
        """默认配置下 maintenance-shell 仍显式透传 qtrading（不是依赖 sidecar 默认值）。"""
        argv = maintenance_service._build_maintenance_shell_argv()
        database_idx = argv.index("--database") + 1
        assert argv[database_idx] == "qtrading"

    @pytest.mark.parametrize(
        "bad_database",
        ["", " bad", "bad name", "a;drop", "a-b", "1abc", "a" * 64],
    )
    def test_build_dump_argv_rejects_invalid_database(
        self,
        maintenance_service,
        mock_config_dict: dict[str, object],
        tmp_path: Path,
        bad_database: str,
    ) -> None:
        """非法库名（空 / 特殊字符 / 注入片段 / 数字开头 / 超长）在 Python 侧即拒绝。"""
        from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceError

        config = {**mock_config_dict, "embedded_pg_database": bad_database}
        with (
            patch(
                "services.embedded_pg_maintenance_service.ConfigHandler.load_config",
                return_value=config,
            ),
            pytest.raises(EmbeddedPgMaintenanceError, match="embedded_pg_database 非法"),
        ):
            maintenance_service._build_dump_argv(tmp_path / "backup.dump")

    def test_build_restore_argv_rejects_invalid_database(
        self, maintenance_service, mock_config_dict: dict[str, object], tmp_path: Path
    ) -> None:
        """restore 与 dump 共用同一校验：非法库名拒绝。"""
        from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceError

        config = {**mock_config_dict, "embedded_pg_database": "drop;--"}
        with (
            patch(
                "services.embedded_pg_maintenance_service.ConfigHandler.load_config",
                return_value=config,
            ),
            pytest.raises(EmbeddedPgMaintenanceError, match="embedded_pg_database 非法"),
        ):
            maintenance_service._build_restore_argv(tmp_path / "backup.dump", None)

    # ---- M21：超级用户名来自配置（不得硬编码 postgres）----

    @pytest.mark.parametrize("builder", ["dump", "restore", "maintenance-shell"])
    def test_build_argv_default_username_omits_flag(
        self, maintenance_service, mock_config_dict, tmp_path: Path, builder: str
    ) -> None:
        """默认 embedded_pg_username=postgres 时不透传 --username（兼容不支持该参数的旧 sidecar）。"""
        argv = self._build(maintenance_service, builder, tmp_path)
        assert "--username" not in argv

    @pytest.mark.parametrize("builder", ["dump", "restore", "maintenance-shell"])
    def test_build_argv_passes_configured_username(
        self, maintenance_service, mock_config_dict: dict[str, object], tmp_path: Path, builder: str
    ) -> None:
        """自定义 embedded_pg_username 时透传 --username（与 run --username 一致，避免角色失配）。"""
        config = {**mock_config_dict, "embedded_pg_username": "qtrading"}
        with patch(
            "services.embedded_pg_maintenance_service.ConfigHandler.load_config",
            return_value=config,
        ):
            argv = self._build(maintenance_service, builder, tmp_path)
        username_idx = argv.index("--username") + 1
        assert argv[username_idx] == "qtrading"

    @staticmethod
    def _build(maintenance_service, builder: str, tmp_path: Path) -> list[str]:
        input_path = tmp_path / "backup.dump"
        if builder == "dump":
            return maintenance_service._build_dump_argv(input_path)
        if builder == "restore":
            return maintenance_service._build_restore_argv(input_path, None)
        return maintenance_service._build_maintenance_shell_argv()

    @pytest.mark.parametrize(
        "bad_username",
        ["", " bad", "bad name", "a;drop", "a-b", "1abc", "a" * 64],
    )
    def test_build_dump_argv_rejects_invalid_username(
        self,
        maintenance_service,
        mock_config_dict: dict[str, object],
        tmp_path: Path,
        bad_username: str,
    ) -> None:
        """非法用户名（空 / 特殊字符 / 注入片段 / 数字开头 / 超长）在 Python 侧即拒绝。"""
        from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceError

        config = {**mock_config_dict, "embedded_pg_username": bad_username}
        with (
            patch(
                "services.embedded_pg_maintenance_service.ConfigHandler.load_config",
                return_value=config,
            ),
            pytest.raises(EmbeddedPgMaintenanceError, match="embedded_pg_username 非法"),
        ):
            maintenance_service._build_dump_argv(tmp_path / "backup.dump")

    def test_build_doctor_argv_frozen_resolves_meipass_sidecar(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """B08: sys.frozen=True 且 embedded_pg_sidecar_path 为空时，应从 sys._MEIPASS/sidecars 解析 sidecar。"""
        import os
        import sys

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

        mock_config = {
            "embedded_pg_enabled": True,
            "embedded_pg_sidecar_path": "",
            "embedded_pg_data_root": str(tmp_path / "data_root"),
            "embedded_pg_install_root": str(tmp_path / "install_root"),
            "embedded_pg_log_dir": str(tmp_path / "log_dir"),
        }
        with patch(
            "services.embedded_pg_maintenance_service.ConfigHandler.load_config",
            return_value=mock_config,
        ):
            from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceService

            svc = EmbeddedPgMaintenanceService()
            argv = svc._build_doctor_argv()

        exe_suffix = ".exe" if os.name == "nt" else ""
        expected_sidecar = str(tmp_path / "sidecars" / f"qtrading-pg-sidecar{exe_suffix}")
        assert argv[0] == expected_sidecar

    def test_build_doctor_argv_frozen_without_meipass_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """B08: sys.frozen=True 但 sys._MEIPASS 为 None 且未配置 sidecar_path 时，应抛 EmbeddedPgMaintenanceError。"""
        import sys

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", None, raising=False)

        mock_config = {
            "embedded_pg_enabled": True,
            "embedded_pg_sidecar_path": "",
            "embedded_pg_data_root": str(tmp_path / "data_root"),
            "embedded_pg_install_root": str(tmp_path / "install_root"),
            "embedded_pg_log_dir": str(tmp_path / "log_dir"),
        }
        with patch(
            "services.embedded_pg_maintenance_service.ConfigHandler.load_config",
            return_value=mock_config,
        ):
            from services.embedded_pg_maintenance_service import (
                EmbeddedPgMaintenanceError,
                EmbeddedPgMaintenanceService,
            )

            svc = EmbeddedPgMaintenanceService()
            with pytest.raises(EmbeddedPgMaintenanceError, match="_MEIPASS is None"):
                svc._build_doctor_argv()


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


class TestRunSidecarCreationFlags:
    """Windows 下 Popen 传 CREATE_NO_WINDOW，防止 GUI 主程序拉起 console 子系统 sidecar 弹控制台窗。"""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_run_sidecar_popen_passes_creation_flags(self, maintenance_service) -> None:
        from services import embedded_pg_maintenance_service as _svc

        proc_mock = MagicMock()
        proc_mock.communicate.return_value = ("", "")
        proc_mock.returncode = 0

        with (
            patch(
                "services.embedded_pg_maintenance_service.ThreadPoolManager",
                return_value=_DirectTP(),
            ),
            patch(
                "services.embedded_pg_maintenance_service.subprocess.Popen",
                return_value=proc_mock,
            ) as popen_mock,
        ):
            code, _stdout, _stderr = await maintenance_service._run_sidecar(["sidecar", "doctor"])

        assert code == 0
        kwargs = popen_mock.call_args.kwargs
        assert kwargs.get("creationflags") == _svc._SIDECAR_CREATION_FLAGS

    @pytest.mark.skipif(os.name != "nt", reason="CREATE_NO_WINDOW/CREATE_NEW_PROCESS_GROUP 语义仅 Windows")
    def test_sidecar_creation_flags_on_windows(self) -> None:
        """Windows 平台常量必须同时含 CREATE_NO_WINDOW 与 CREATE_NEW_PROCESS_GROUP。"""
        import subprocess as _subprocess

        from services import embedded_pg_maintenance_service as _svc

        assert _svc._SIDECAR_CREATION_FLAGS == (_subprocess.CREATE_NO_WINDOW | _subprocess.CREATE_NEW_PROCESS_GROUP)


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
            with pytest.raises(EmbeddedPgMaintenanceError, match="data_dir_abnormal") as exc_info:
                await maintenance_service.doctor()

        # 反向断言：原始路径字符串不出现在 caplog 记录与异常消息中
        for record in caplog.records:
            assert sensitive_path not in record.getMessage()
        assert sensitive_path not in str(exc_info.value)

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
# TestRequestCancel: D07-007 取消须到达 pg 子进程并证明已停止（原 CON-04）
# =============================================================================
class TestRequestCancel:
    """D07-007: request_cancel 以可核对状态区分「已证明停止 / 已自然结束 / 未知」。"""

    def test_request_cancel_no_active_proc_returns_false(self, maintenance_service) -> None:
        """无活动命令（_active_proc 为 None）时返回 NO_ACTIVE，不抛异常。"""
        from services.embedded_pg_maintenance_service import CancelStatus

        assert maintenance_service.request_cancel() is CancelStatus.NO_ACTIVE

    def test_request_cancel_terminates_process_tree_and_proves_exit(self, maintenance_service) -> None:
        """正常取消：向整棵进程树发终止信号，并以退出证明返回 CANCELLED。"""
        from services.embedded_pg_maintenance_service import CancelStatus, _SignalOutcome

        proc_mock = MagicMock()
        proc_mock.pid = 4321
        proc_mock.poll.return_value = 0
        maintenance_service._active_proc = proc_mock
        maintenance_service._active_generation = 1

        with (
            patch(
                "services.embedded_pg_maintenance_service._signal_process_tree",
                return_value=_SignalOutcome.DELIVERED,
            ) as signal_mock,
            patch(
                "services.embedded_pg_maintenance_service._wait_process_tree_exit",
                return_value=True,
            ),
        ):
            status = maintenance_service.request_cancel()

        assert status is CancelStatus.CANCELLED
        signal_mock.assert_called_once_with(proc_mock, force=False, job=None)
        assert maintenance_service._is_cancel_requested(1) is True
        proc_mock.terminate.assert_not_called()

    def test_request_cancel_escalates_to_kill_when_child_ignores_signal(self, maintenance_service) -> None:
        """pg 子进程忽略 SIGTERM：升级整组强杀，强杀后退出仍按 CANCELLED 报告。"""
        from services.embedded_pg_maintenance_service import CancelStatus, _SignalOutcome

        proc_mock = MagicMock()
        proc_mock.pid = 4321
        maintenance_service._active_proc = proc_mock
        maintenance_service._active_generation = 1

        with (
            patch(
                "services.embedded_pg_maintenance_service._signal_process_tree",
                return_value=_SignalOutcome.DELIVERED,
            ) as signal_mock,
            patch(
                "services.embedded_pg_maintenance_service._wait_process_tree_exit",
                side_effect=[False, True],
            ),
        ):
            status = maintenance_service.request_cancel(timeout=0.01)

        assert status is CancelStatus.CANCELLED
        assert [c.kwargs for c in signal_mock.call_args_list] == [
            {"force": False, "job": None},
            {"force": True, "job": None},
        ]

    def test_request_cancel_unproven_when_tree_never_exits(self, maintenance_service, caplog) -> None:
        """宽限期与强杀窗口都未退出：返回 UNPROVEN，日志明示可能仍在运行。"""
        from services.embedded_pg_maintenance_service import CancelStatus, _SignalOutcome

        proc_mock = MagicMock()
        proc_mock.pid = 4321
        maintenance_service._active_proc = proc_mock
        maintenance_service._active_generation = 1

        with (
            patch(
                "services.embedded_pg_maintenance_service._signal_process_tree",
                return_value=_SignalOutcome.DELIVERED,
            ),
            patch(
                "services.embedded_pg_maintenance_service._wait_process_tree_exit",
                return_value=False,
            ),
            caplog.at_level(logging.WARNING),
        ):
            status = maintenance_service.request_cancel(timeout=0.01)

        assert status is CancelStatus.UNPROVEN
        assert any("cannot prove process tree stopped" in r.message for r in caplog.records)

    def test_request_cancel_oserror_treated_as_already_exited(self, maintenance_service) -> None:
        """终止信号无法送达（进程组已消失）→ ALREADY_EXITED，不把取消标记带入后续退出分类。"""
        from services.embedded_pg_maintenance_service import CancelStatus, _SignalOutcome

        proc_mock = MagicMock()
        proc_mock.pid = 4321
        maintenance_service._active_proc = proc_mock
        maintenance_service._active_generation = 1

        with patch(
            "services.embedded_pg_maintenance_service._signal_process_tree",
            return_value=_SignalOutcome.ALREADY_EXITED,
        ):
            status = maintenance_service.request_cancel()

        assert status is CancelStatus.ALREADY_EXITED
        assert maintenance_service._cancel_requested_generation is None


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
        assert maintenance_service._dump_task_reserved is False

    @pytest.mark.asyncio(loop_scope="function")
    async def test_concurrent_dump_rejected_before_popen(self, maintenance_service) -> None:
        """D16-F06: 已有 dump 在途时，第二次调用在 Popen 之前即被拒绝（不得启动子进程）。"""
        from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceError

        first_proc = MagicMock()  # 已在途 dump 句柄
        maintenance_service._active_proc = first_proc
        popen_mock = MagicMock()

        with (
            patch(
                "services.embedded_pg_maintenance_service.ThreadPoolManager",
                return_value=_DirectTP(),
            ),
            patch(
                "services.embedded_pg_maintenance_service.subprocess.Popen",
                popen_mock,
            ),
        ):
            with pytest.raises(EmbeddedPgMaintenanceError, match="已有备份任务在进行中"):
                await maintenance_service._run_sidecar(["sidecar", "dump"], cancel_allowed=True)

        popen_mock.assert_not_called()
        # 拒绝路径不得误清在途句柄，也不得遗留预留位
        assert maintenance_service._active_proc is first_proc
        assert maintenance_service._dump_task_reserved is False

    @pytest.mark.asyncio(loop_scope="function")
    async def test_reservation_set_before_popen(self, maintenance_service) -> None:
        """D16-F06: 预留位必须在 Popen 调用时已置位（并发探测据此拒绝）。"""
        proc_mock = MagicMock()
        proc_mock.returncode = 0
        proc_mock.communicate = MagicMock(return_value=("", ""))
        captured: dict[str, object] = {}

        def _popen(*args, **kwargs):
            captured["reserved"] = maintenance_service._dump_task_reserved
            captured["active"] = maintenance_service.has_active_operation()
            return proc_mock

        with (
            patch(
                "services.embedded_pg_maintenance_service.ThreadPoolManager",
                return_value=_DirectTP(),
            ),
            patch(
                "services.embedded_pg_maintenance_service.subprocess.Popen",
                side_effect=_popen,
            ),
        ):
            await maintenance_service._run_sidecar(["sidecar", "dump"], cancel_allowed=True)

        assert captured == {"reserved": True, "active": True}
        assert maintenance_service._dump_task_reserved is False
        assert maintenance_service._active_proc is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_popen_failure_releases_reservation(self, maintenance_service) -> None:
        """D16-F06: Popen 抛错时预留位在 finally 释放，后续调用可正常执行。"""
        proc_mock = MagicMock()
        proc_mock.returncode = 0
        proc_mock.communicate = MagicMock(return_value=("", ""))

        with (
            patch(
                "services.embedded_pg_maintenance_service.ThreadPoolManager",
                return_value=_DirectTP(),
            ),
            patch(
                "services.embedded_pg_maintenance_service.subprocess.Popen",
                side_effect=OSError("spawn failed"),
            ),
        ):
            with pytest.raises(OSError, match="spawn failed"):
                await maintenance_service._run_sidecar(["sidecar", "dump"], cancel_allowed=True)

        assert maintenance_service._dump_task_reserved is False
        assert maintenance_service._active_proc is None

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
            exit_code, _stdout, _stderr = await maintenance_service._run_sidecar(
                ["sidecar", "dump"], cancel_allowed=True
            )

        assert exit_code == 0
        assert maintenance_service._dump_task_reserved is False

    @pytest.mark.asyncio(loop_scope="function")
    async def test_reservation_without_proc_blocks_has_active_operation(self, maintenance_service) -> None:
        """D16-F06: 仅预留（Popen 前）也应视为有在途备份，禁止切换数据库模式。"""
        assert maintenance_service.has_active_operation() is False
        maintenance_service._dump_task_reserved = True
        assert maintenance_service.has_active_operation() is True
        maintenance_service._dump_task_reserved = False

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
        assert maintenance_service._dump_task_reserved is False


# =============================================================================
# TestDumpCancellationIntegrity: D07-007 取消分类 + target 完整性
# =============================================================================
class TestDumpCancellationIntegrity:
    """D07-007: 取消后不得按负退出码报 unknown_error，也不得把半成品当成功。

    假进程在 ``communicate`` 内模拟「取消信号已到达」；target 是否出现/是否完整
    由本服务在 dump 返回前显式校验。
    """

    @pytest.fixture(autouse=True)
    def _prove_tree_stopped(self):
        """假进程无真实进程组：默认证明「树已停止」，个别用例再覆盖为未证明。"""
        with patch(
            "services.embedded_pg_maintenance_service._wait_process_tree_exit",
            return_value=True,
        ):
            yield

    @staticmethod
    def _proc_requesting_cancel(maintenance_service, returncode: int) -> MagicMock:
        proc_mock = MagicMock()
        proc_mock.pid = 4321
        proc_mock.returncode = returncode

        def _communicate(timeout=None):
            maintenance_service._cancel_requested_generation = maintenance_service._active_generation
            return ("", "")

        proc_mock.communicate = MagicMock(side_effect=_communicate)
        return proc_mock

    @pytest.mark.asyncio(loop_scope="function")
    async def test_run_sidecar_raises_cancelled_not_unknown_exit(self, maintenance_service) -> None:
        """取消后的 sidecar 负退出码归为取消异常，而非 unknown_error 业务失败。"""
        from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceCancelledError

        proc_mock = self._proc_requesting_cancel(maintenance_service, returncode=-15)

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
            with pytest.raises(EmbeddedPgMaintenanceCancelledError, match="已被取消"):
                await maintenance_service._run_sidecar(["sidecar", "dump"], cancel_allowed=True)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_timeout_after_cancel_reports_unproven(self, maintenance_service) -> None:
        """已请求取消但超时仍未退出：报「状态未知/仍可能运行」，不报普通超时。"""
        import subprocess

        from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceCancelUnprovenError

        proc_mock = MagicMock()
        calls = {"n": 0}

        def _communicate(timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                maintenance_service._cancel_requested_generation = maintenance_service._active_generation
                raise subprocess.TimeoutExpired(cmd="sidecar", timeout=3600)
            return ("", "")

        proc_mock.communicate = MagicMock(side_effect=_communicate)
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
            with pytest.raises(EmbeddedPgMaintenanceCancelUnprovenError, match="可能仍在运行"):
                await maintenance_service._run_sidecar(["sidecar", "dump"], cancel_allowed=True)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_cancel_unproven_after_wrapper_exit_raises_unproven(self, maintenance_service) -> None:
        """wrapper 已退出但无法证明 pg 子进程停止：报未知，不得谎报「已取消」。"""
        from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceCancelUnprovenError

        proc_mock = self._proc_requesting_cancel(maintenance_service, returncode=-15)

        with (
            patch(
                "services.embedded_pg_maintenance_service.ThreadPoolManager",
                return_value=_DirectTP(),
            ),
            patch(
                "services.embedded_pg_maintenance_service.subprocess.Popen",
                return_value=proc_mock,
            ),
            patch(
                "services.embedded_pg_maintenance_service._wait_process_tree_exit",
                return_value=False,
            ),
        ):
            with pytest.raises(EmbeddedPgMaintenanceCancelUnprovenError, match="可能仍在运行"):
                await maintenance_service._run_sidecar(["sidecar", "dump"], cancel_allowed=True)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_cancel_proven_after_wrapper_exit_reports_cancelled(self, maintenance_service) -> None:
        """整树停止证明成立（含 Windows 受控 Job 归零）时按已取消报告，不再跳过证明。"""
        from services.embedded_pg_maintenance_service import (
            _CANCEL_GRACE_S,
            _CANCEL_KILL_GRACE_S,
            EmbeddedPgMaintenanceCancelledError,
        )

        proc_mock = self._proc_requesting_cancel(maintenance_service, returncode=-15)

        with (
            patch(
                "services.embedded_pg_maintenance_service.ThreadPoolManager",
                return_value=_DirectTP(),
            ),
            patch(
                "services.embedded_pg_maintenance_service.subprocess.Popen",
                return_value=proc_mock,
            ),
            patch(
                "services.embedded_pg_maintenance_service._wait_process_tree_exit",
                return_value=True,
            ) as tree_wait,
        ):
            with pytest.raises(EmbeddedPgMaintenanceCancelledError, match="已被取消"):
                await maintenance_service._run_sidecar(["sidecar", "dump"], cancel_allowed=True)

        tree_wait.assert_called_once_with(proc_mock, _CANCEL_GRACE_S + _CANCEL_KILL_GRACE_S, job=None)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_dump_cancel_without_target_raises_cancelled(self, maintenance_service, tmp_path: Path) -> None:
        """取消且 target 未出现：报已取消，并断言目标文件确实不存在。"""
        from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceCancelledError

        output_path = tmp_path / "cancelled.dump"
        proc_mock = self._proc_requesting_cancel(maintenance_service, returncode=-15)

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
            with pytest.raises(EmbeddedPgMaintenanceCancelledError, match="未生成备份文件"):
                await maintenance_service.dump(output_path)

        assert not output_path.exists()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_dump_cancel_after_completion_returns_success(self, maintenance_service, tmp_path: Path) -> None:
        """取消生效前 dump 已原子改名完成：target 非空即按完成返回，不谎报已取消。"""
        from services.embedded_pg_maintenance_service import DumpResult

        output_path = tmp_path / "completed.dump"
        output_path.write_bytes(b"PGDMP-complete")
        proc_mock = self._proc_requesting_cancel(maintenance_service, returncode=0)

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
            result = await maintenance_service.dump(output_path)

        assert isinstance(result, DumpResult)
        assert result.file_size == len(b"PGDMP-complete")
        assert result.exit_code == 0

    @pytest.mark.asyncio(loop_scope="function")
    async def test_dump_cancel_with_zero_byte_target_reports_incomplete(
        self, maintenance_service, tmp_path: Path
    ) -> None:
        """取消后 target 存在但为 0 字节：按「不完整」报告，不得当成功。"""
        from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceCancelledError

        output_path = tmp_path / "incomplete.dump"
        output_path.touch()
        proc_mock = self._proc_requesting_cancel(maintenance_service, returncode=-15)

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
            with pytest.raises(EmbeddedPgMaintenanceCancelledError, match="不完整"):
                await maintenance_service.dump(output_path)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_popen_starts_sidecar_in_own_process_group(self, maintenance_service) -> None:
        """Popen 必须让 sidecar 独立成组，使取消信号能到达其 pg 子进程。"""
        proc_mock = MagicMock()
        proc_mock.returncode = 0
        proc_mock.communicate = MagicMock(return_value=("", ""))

        with (
            patch(
                "services.embedded_pg_maintenance_service.ThreadPoolManager",
                return_value=_DirectTP(),
            ),
            patch(
                "services.embedded_pg_maintenance_service.subprocess.Popen",
                return_value=proc_mock,
            ) as popen_mock,
        ):
            await maintenance_service._run_sidecar(["sidecar", "doctor"])

        assert popen_mock.call_args.kwargs.get("start_new_session") is (os.name != "nt")


class TestProcessTreeHelpersUnit:
    """D07-007 进程树工具函数分支单测（diff coverage 补强，全 mock 无真实进程）。"""

    # ---- _wait_process_exit（Windows 证明函数，分支覆盖） ----
    def test_wait_process_exit_proves_exit_after_retry(self) -> None:
        from services.embedded_pg_maintenance_service import _wait_process_exit

        proc = MagicMock()
        proc.poll = MagicMock(side_effect=[None, 7])
        proc.wait = MagicMock(side_effect=[subprocess.TimeoutExpired(cmd="x", timeout=0.1)])

        assert _wait_process_exit(proc, 5.0) is True

    def test_wait_process_exit_treats_recycled_process_as_exit(self) -> None:
        """waitpid 竞态（OSError: 进程已被他线程回收）视为等待目的已达成。"""
        from services.embedded_pg_maintenance_service import _wait_process_exit

        proc = MagicMock()
        proc.poll = MagicMock(side_effect=[None, None, None])
        proc.wait = MagicMock(side_effect=OSError("process already reaped"))

        assert _wait_process_exit(proc, 5.0) is True

    def test_wait_process_exit_returns_false_on_timeout(self) -> None:
        from services.embedded_pg_maintenance_service import _wait_process_exit

        proc = MagicMock()
        proc.poll = MagicMock(return_value=None)
        proc.wait = MagicMock(side_effect=subprocess.TimeoutExpired(cmd="x", timeout=0.1))

        assert _wait_process_exit(proc, 0.15) is False

    # ---- _process_group_alive 探测异常分支 ----
    @_requires_posix_group_api
    def test_process_group_alive_false_when_group_gone(self, monkeypatch) -> None:
        from services.embedded_pg_maintenance_service import _process_group_alive

        proc = MagicMock()
        proc.pid = 4321
        monkeypatch.setattr(os, "getpgid", lambda pid: pid, raising=True)
        monkeypatch.setattr(os, "killpg", lambda pgid, sig: (_ for _ in ()).throw(ProcessLookupError()))

        assert _process_group_alive(proc) is False

    @_requires_posix_group_api
    def test_process_group_alive_true_on_eperm(self, monkeypatch) -> None:
        """EPERM：组仍存在但无权探测，保守视为存活。"""
        from services.embedded_pg_maintenance_service import _process_group_alive

        proc = MagicMock()
        proc.pid = 4321
        monkeypatch.setattr(os, "getpgid", lambda pid: pid)
        monkeypatch.setattr(os, "killpg", lambda pgid, sig: (_ for _ in ()).throw(PermissionError()))

        assert _process_group_alive(proc) is True

    # ---- _wait_process_tree_exit 平台分流与回收竞态 ----
    def test_wait_process_tree_exit_windows_routes_to_wait_process_exit(self, monkeypatch) -> None:
        from services.embedded_pg_maintenance_service import _wait_process_tree_exit

        monkeypatch.setattr(os, "name", "nt")
        proc = MagicMock()
        # POSIX 平台：防御性断言 nt 分支不得触碰 os.getpgid；Windows 无该属性（POSIX
        # 路径物理不可达），patch 不存在目标反而 AttributeError，故仅 POSIX 需要。
        posix_guard = (
            patch(
                "services.embedded_pg_maintenance_service.os.getpgid",
                side_effect=AssertionError("POSIX 路径不得执行"),
            )
            if sys.platform != "win32"
            else nullcontext()
        )
        with (
            patch(
                "services.embedded_pg_maintenance_service._wait_process_exit",
                return_value=False,
            ) as routed,
            posix_guard,
        ):
            assert _wait_process_tree_exit(proc, 0.2) is False
        routed.assert_called_once_with(proc, 0.2)

    @_requires_posix_group_api
    def test_wait_process_tree_exit_tolerates_poll_oserror(self, monkeypatch) -> None:
        """回收 wrapper 的 poll() 竞态（OSError）不得中断等待循环。"""
        from services.embedded_pg_maintenance_service import _wait_process_tree_exit

        proc = MagicMock()
        proc.pid = 4321
        monkeypatch.setattr(os, "getpgid", lambda pid: pid)
        killpg_calls = {"n": 0}

        def fake_killpg(pgid: int, sig: int) -> None:
            killpg_calls["n"] += 1
            if killpg_calls["n"] == 1:
                return  # 首轮：组仍存活
            raise ProcessLookupError()  # 次轮：组已消失

        monkeypatch.setattr(os, "killpg", fake_killpg)
        monkeypatch.setattr("services.embedded_pg_maintenance_service.time.sleep", lambda s: None)
        proc.poll = MagicMock(side_effect=OSError("race with reaper"))

        assert _wait_process_tree_exit(proc, 1.0) is True

    def test_wait_process_tree_exit_windows_prefers_job_proof(self, monkeypatch) -> None:
        """Windows 有受控 Job 时以 Job 内活动进程归零为判据，不再依赖 wrapper 退出。"""
        from services.embedded_pg_maintenance_service import _wait_process_tree_exit

        monkeypatch.setattr(os, "name", "nt")
        job = MagicMock()
        job.wait_empty.return_value = True
        proc = MagicMock()

        with patch("services.embedded_pg_maintenance_service._wait_process_exit") as fallback:
            assert _wait_process_tree_exit(proc, 0.5, job=job) is True

        job.wait_empty.assert_called_once_with(0.5)
        fallback.assert_not_called()

    # ---- _signal_process_tree Windows Job Object / POSIX 分支 ----
    def test_signal_process_tree_windows_without_job_is_identity_unproven(self, monkeypatch) -> None:
        """D1-04: 无受控 Job 时不得改用 taskkill /PID 强杀，只能报「身份未证明」。"""
        from services.embedded_pg_maintenance_service import _SignalOutcome, _signal_process_tree

        monkeypatch.setattr(os, "name", "nt")
        proc = MagicMock(pid=99, poll=MagicMock(return_value=None))
        with patch("services.embedded_pg_maintenance_service.subprocess.run") as run_mock:
            assert _signal_process_tree(proc, force=False, job=None) is _SignalOutcome.IDENTITY_UNPROVEN

        run_mock.assert_not_called()

    def test_signal_process_tree_windows_terminates_job(self, monkeypatch) -> None:
        """D1-04: 取消作用于 Job 句柄（TerminateJobObject），不以裸 PID 定位目标。"""
        from services.embedded_pg_maintenance_service import _SignalOutcome, _signal_process_tree

        monkeypatch.setattr(os, "name", "nt")
        job = MagicMock()
        job.terminate.return_value = True
        with patch("services.embedded_pg_maintenance_service.subprocess.run") as run_mock:
            assert _signal_process_tree(MagicMock(pid=99), force=False, job=job) is _SignalOutcome.DELIVERED

        job.terminate.assert_called_once_with()
        run_mock.assert_not_called()

    def test_signal_process_tree_windows_job_terminate_failure(self, monkeypatch) -> None:
        """Job 终止失败：wrapper 已退出报 ALREADY_EXITED，否则只能报身份未证明。"""
        from services.embedded_pg_maintenance_service import _SignalOutcome, _signal_process_tree

        monkeypatch.setattr(os, "name", "nt")
        job = MagicMock()
        job.terminate.return_value = False

        exited = MagicMock(pid=99, poll=MagicMock(return_value=0))
        assert _signal_process_tree(exited, force=False, job=job) is _SignalOutcome.ALREADY_EXITED

        alive = MagicMock(pid=99, poll=MagicMock(return_value=None))
        assert _signal_process_tree(alive, force=False, job=job) is _SignalOutcome.IDENTITY_UNPROVEN

    @_requires_posix_group_api
    def test_signal_process_tree_terminates_single_proc_when_not_leader(self, monkeypatch) -> None:
        """未成为进程组长（或 pid 复用）时不得 killpg；wrapper 存活则单进程终止。"""
        from services.embedded_pg_maintenance_service import _SignalOutcome, _signal_process_tree

        proc = MagicMock()
        proc.pid = 4321
        proc.poll = MagicMock(return_value=None)
        monkeypatch.setattr(os, "getpgid", lambda pid: pid + 7)

        assert _signal_process_tree(proc, force=False) is _SignalOutcome.DELIVERED
        proc.terminate.assert_called_once_with()
        proc.kill.assert_not_called()

    @_requires_posix_group_api
    def test_signal_process_tree_force_kills_single_proc(self, monkeypatch) -> None:
        from services.embedded_pg_maintenance_service import _SignalOutcome, _signal_process_tree

        proc = MagicMock()
        proc.pid = 4321
        proc.poll = MagicMock(return_value=None)
        monkeypatch.setattr(os, "getpgid", lambda pid: pid + 7)

        assert _signal_process_tree(proc, force=True) is _SignalOutcome.DELIVERED
        proc.kill.assert_called_once_with()

    @_requires_posix_group_api
    def test_signal_process_tree_refuses_when_wrapper_exited(self, monkeypatch) -> None:
        """wrapper 已退出且组 id 不可信：放弃信号，交由上层证明逻辑。"""
        from services.embedded_pg_maintenance_service import _SignalOutcome, _signal_process_tree

        proc = MagicMock()
        proc.pid = 4321
        proc.poll = MagicMock(return_value=0)
        monkeypatch.setattr(os, "getpgid", lambda pid: pid + 7)

        assert _signal_process_tree(proc, force=False) is _SignalOutcome.ALREADY_EXITED

    @_requires_posix_group_api
    def test_signal_process_tree_killpg_group_graceful_and_force(self, monkeypatch) -> None:
        from services.embedded_pg_maintenance_service import _SignalOutcome, _signal_process_tree

        proc = MagicMock()
        proc.pid = 4321
        monkeypatch.setattr(os, "getpgid", lambda pid: pid)
        sent: list[tuple[int, int]] = []
        monkeypatch.setattr(os, "killpg", lambda pgid, sig: sent.append((pgid, sig)))

        assert _signal_process_tree(proc, force=False) is _SignalOutcome.DELIVERED
        assert _signal_process_tree(proc, force=True) is _SignalOutcome.DELIVERED
        assert sent == [(4321, signal.SIGTERM), (4321, signal.SIGKILL)]

    @_requires_posix_group_api
    def test_signal_process_tree_oserror_returns_already_exited(self, monkeypatch) -> None:
        """进程组已不存在（OSError）：命令已自然结束。"""
        from services.embedded_pg_maintenance_service import _SignalOutcome, _signal_process_tree

        proc = MagicMock()
        proc.pid = 4321
        monkeypatch.setattr(os, "getpgid", lambda pid: pid)
        monkeypatch.setattr(os, "killpg", lambda pgid, sig: (_ for _ in ()).throw(OSError("no group")))

        assert _signal_process_tree(proc, force=False) is _SignalOutcome.ALREADY_EXITED

    # ---- 取消后产物状态未知（stat OSError → Unproven） ----
    @pytest.mark.asyncio(loop_scope="function")
    async def test_dump_cancel_stat_oserror_raises_unproven(self, maintenance_service, tmp_path: Path) -> None:
        """取消后 target stat 失败：只能报「无法确认输出文件状态」。"""
        import errno

        from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceCancelUnprovenError

        output_path = tmp_path / "unknown_state.dump"
        proc_mock = TestDumpCancellationIntegrity._proc_requesting_cancel(maintenance_service, returncode=-15)

        real_stat = Path.stat

        def fake_stat(self: Path, *args: object, **kwargs: object):
            if str(self) == str(output_path):
                raise OSError(errno.EIO, "simulated io failure")
            return real_stat(self, *args, **kwargs)

        with (
            patch(
                "services.embedded_pg_maintenance_service.ThreadPoolManager",
                return_value=_DirectTP(),
            ),
            patch(
                "services.embedded_pg_maintenance_service.subprocess.Popen",
                return_value=proc_mock,
            ),
            # 假进程（pid=4321）没有真实进程组：POSIX 下真实探测会命中宿主同号进程组，
            # 使「取消未完成（进程树未证明停止）」分支先于 target 状态判定触发。
            # 本用例只验证 target stat 失败后的分类，故固定「整树已停止」这一前提。
            patch(
                "services.embedded_pg_maintenance_service._wait_process_tree_exit",
                return_value=True,
            ),
            patch.object(Path, "stat", fake_stat),
        ):
            with pytest.raises(EmbeddedPgMaintenanceCancelUnprovenError, match="无法确认输出文件状态"):
                await maintenance_service.dump(output_path)

    # ---- 单例/解析补充分支 ----
    def test_has_active_operation_false_without_instance(self) -> None:
        """未构造单例时只读探测必须返回 False（不触发构造）。"""
        from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceService

        with patch.object(EmbeddedPgMaintenanceService, "_instance", None):
            assert EmbeddedPgMaintenanceService.has_active_operation() is False

    def test_doctor_json_missing_required_fields_rejected(self) -> None:
        """data_dir / initialized 缺失必须显式报错，不得静默默认值。"""
        from services.embedded_pg_maintenance_service import (
            EXPECTED_DOCTOR_SCHEMA,
            EmbeddedPgMaintenanceError,
            EmbeddedPgMaintenanceService,
        )

        svc = EmbeddedPgMaintenanceService.__new__(EmbeddedPgMaintenanceService)
        with pytest.raises(EmbeddedPgMaintenanceError, match="data_dir"):
            svc._validate_doctor_json({"schema": EXPECTED_DOCTOR_SCHEMA})
        with pytest.raises(EmbeddedPgMaintenanceError, match="initialized"):
            svc._validate_doctor_json({"schema": EXPECTED_DOCTOR_SCHEMA, "data_dir": "/fake/data"})


@pytest.mark.skipif(os.name == "nt", reason="进程组/信号语义仅 POSIX")
class TestProcessTreeProof:
    """D07-007: 真实进程验证「wrapper 退出 ≠ pg 子进程退出」。

    用可控假 sidecar（Python 脚本）拉起一个忽略 SIGTERM 的假 pg 子进程：
    只终止 wrapper 时进程组仍存活，只有整组强杀后 ``_wait_process_tree_exit`` 才为真。
    """

    def test_wrapper_exit_is_not_tree_exit(self, tmp_path: Path) -> None:
        from services.embedded_pg_maintenance_service import (
            _process_group_alive,
            _wait_process_tree_exit,
        )

        sentinel = tmp_path / "child_ready"
        child_code = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
        wrapper_code = (
            "import pathlib, subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
            f"pathlib.Path({str(sentinel)!r}).write_text('ok')\n"
            "time.sleep(60)\n"
        )
        proc = subprocess.Popen([sys.executable, "-c", wrapper_code], start_new_session=True)
        try:
            deadline = time.monotonic() + 10
            while not sentinel.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert sentinel.exists(), "假 sidecar 未拉起假 pg 子进程"

            # 只杀 wrapper：进程组仍被忽略 SIGTERM 的子进程持有
            proc.terminate()
            proc.wait(timeout=10)
            assert _process_group_alive(proc) is True
            assert _wait_process_tree_exit(proc, 0.2) is False

            # 整组强杀后才证明树已停止
            os.killpg(proc.pid, signal.SIGKILL)
            assert _wait_process_tree_exit(proc, 10) is True
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)


# =============================================================================
# D1-04: 取消代际隔离（锁拆分误判 / 跨轮污染）
# =============================================================================
class TestCancelGenerationIsolation:
    """D1-04: 取消标记绑定单调递增代际，并与终止信号在同一临界区内提交/回滚。"""

    def test_rollback_shares_critical_section_with_commit(self, maintenance_service) -> None:
        """信号未送达时回滚与置位同临界区：并发读者在临界区结束前不可能读到 True。"""
        from services.embedded_pg_maintenance_service import CancelStatus, _SignalOutcome

        proc_mock = MagicMock()
        proc_mock.pid = 4321
        proc_mock.poll.return_value = 0
        maintenance_service._active_proc = proc_mock
        maintenance_service._active_generation = 7

        observed: dict[str, bool] = {}
        reader_done = threading.Event()

        def _reader() -> None:
            observed["cancel"] = maintenance_service._is_cancel_requested(7)
            reader_done.set()

        def _fake_signal(proc, *, force, job=None):
            # 此处 request_cancel 仍持有 _lock：读者线程必须阻塞到临界区结束
            threading.Thread(target=_reader, daemon=True).start()
            time.sleep(0.05)
            assert not reader_done.is_set(), "读者在临界区结束前读到了取消标记"
            return _SignalOutcome.ALREADY_EXITED

        with patch(
            "services.embedded_pg_maintenance_service._signal_process_tree",
            side_effect=_fake_signal,
        ):
            status = maintenance_service.request_cancel()

        assert reader_done.wait(timeout=5)
        assert observed["cancel"] is False
        assert status is CancelStatus.ALREADY_EXITED
        assert maintenance_service._is_cancel_requested(7) is False

    def test_cancel_flag_is_scoped_to_generation(self, maintenance_service) -> None:
        """标记只对写入它的代际生效，后续轮次不会被上一轮的取消污染。"""
        maintenance_service._cancel_requested_generation = 3
        assert maintenance_service._is_cancel_requested(3) is True
        assert maintenance_service._is_cancel_requested(4) is False
        assert maintenance_service._is_cancel_requested(None) is False

    @pytest.mark.asyncio(loop_scope="function")
    async def test_new_run_allocates_new_generation_and_clears_flag(self, maintenance_service) -> None:
        """新一轮可取消命令分配新代际并清除旧标记，成功退出不得被误判为取消。"""
        maintenance_service._run_generation = 41
        maintenance_service._cancel_requested_generation = 1
        proc_mock = MagicMock()
        proc_mock.returncode = 0
        captured: dict[str, object] = {}

        def _communicate(timeout=None):
            captured["flag"] = maintenance_service._cancel_requested_generation
            captured["generation"] = maintenance_service._active_generation
            return ("", "")

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
            exit_code, _stdout, _stderr = await maintenance_service._run_sidecar(
                ["sidecar", "dump"], cancel_allowed=True
            )

        assert exit_code == 0
        assert captured["flag"] is None
        assert captured["generation"] == 42


# =============================================================================
# D1-04: Windows 受控 Job Object（身份绑定，替代裸 PID taskkill）
# =============================================================================
class _FakeJobKernel32:
    """按脚本返回 Job 活动进程数的 kernel32 替身（仅覆盖测试用到的 API）。"""

    def __init__(self, active_processes: list[int]) -> None:
        self._active = list(active_processes)
        self.closed_handles: list[object] = []
        self.terminated = 0

    def QueryInformationJobObject(self, handle, info_class, info_ptr, size, ret):
        from services.embedded_pg_maintenance_service import _JobBasicAccountingInformation

        info = ctypes.cast(info_ptr, ctypes.POINTER(_JobBasicAccountingInformation)).contents
        info.ActiveProcesses = self._active.pop(0) if len(self._active) > 1 else self._active[0]
        return True

    def TerminateJobObject(self, handle, code):
        self.terminated += 1
        return True

    def CloseHandle(self, handle):
        self.closed_handles.append(handle)
        return True


class TestWindowsJobObject:
    """D1-04: 受控 Job Object 的停止证明与句柄生命周期。"""

    def test_wait_empty_true_when_no_active_processes(self) -> None:
        from services.embedded_pg_maintenance_service import _WindowsJob

        job = _WindowsJob(1234, _FakeJobKernel32([0]))
        assert job.wait_empty(0.2) is True

    def test_wait_empty_false_on_timeout(self) -> None:
        from services.embedded_pg_maintenance_service import _WindowsJob

        job = _WindowsJob(1234, _FakeJobKernel32([2]))
        assert job.wait_empty(0.05) is False

    def test_closed_job_counts_as_terminated_and_rejects_terminate(self) -> None:
        from services.embedded_pg_maintenance_service import _WindowsJob

        fake = _FakeJobKernel32([3])
        job = _WindowsJob(1234, fake)
        job.close()
        job.close()  # 幂等
        assert fake.closed_handles == [1234]
        assert job.wait_empty(0.2) is True
        assert job.terminate() is False

    def test_create_sets_kill_on_job_close(self, monkeypatch) -> None:
        import services.embedded_pg_maintenance_service as module

        captured: dict[str, object] = {}

        class _FakeApi:
            def CreateJobObjectW(self, attrs, name):
                return 5678

            def SetInformationJobObject(self, handle, info_class, info_ptr, size):
                info = ctypes.cast(info_ptr, ctypes.POINTER(module._JobExtendedLimitInformation)).contents
                captured["limit_flags"] = info.BasicLimitInformation.LimitFlags
                return True

            def CloseHandle(self, handle):
                return True

        monkeypatch.setattr(module, "_job_kernel32", lambda: _FakeApi())
        job = module._WindowsJob.create()

        assert isinstance(job, module._WindowsJob)
        assert job._handle == 5678
        assert captured["limit_flags"] == module._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

    def test_create_returns_none_when_limit_set_fails(self, monkeypatch) -> None:
        import services.embedded_pg_maintenance_service as module

        class _FakeApi:
            def CreateJobObjectW(self, attrs, name):
                return 1

            def SetInformationJobObject(self, *args):
                return False

            def CloseHandle(self, handle):
                return True

        monkeypatch.setattr(module, "_job_kernel32", lambda: _FakeApi())
        assert module._WindowsJob.create() is None

    def test_request_cancel_without_job_reports_unproven(self, monkeypatch, maintenance_service, caplog) -> None:
        """D1-04: Windows 无受控 Job 时只能报「取消未确认」，不得强杀裸 PID。"""
        from services.embedded_pg_maintenance_service import CancelStatus

        monkeypatch.setattr(os, "name", "nt")
        maintenance_service._active_proc = MagicMock(pid=99)
        maintenance_service._active_generation = 5
        maintenance_service._active_job = None

        with caplog.at_level(logging.WARNING):
            status = maintenance_service.request_cancel()

        assert status is CancelStatus.UNPROVEN
        assert maintenance_service._is_cancel_requested(5) is False
        assert any("cannot prove target identity" in r.message for r in caplog.records)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_run_sidecar_assigns_job_before_registration(self, maintenance_service, monkeypatch) -> None:
        """D1-04: 先登记 Job 再暴露句柄；运行结束关闭 Job（KILL_ON_JOB_CLOSE 兜底）。"""
        from services.embedded_pg_maintenance_service import _WindowsJob

        monkeypatch.setattr(os, "name", "nt")
        job = MagicMock()
        job.assign.return_value = True
        proc_mock = MagicMock()
        proc_mock.returncode = 0
        captured: dict[str, object] = {}

        def _communicate(timeout=None):
            captured["job"] = maintenance_service._active_job
            return ("", "")

        proc_mock.communicate = MagicMock(side_effect=_communicate)

        with (
            patch.object(_WindowsJob, "create", return_value=job),
            patch(
                "services.embedded_pg_maintenance_service.ThreadPoolManager",
                return_value=_DirectTP(),
            ),
            patch(
                "services.embedded_pg_maintenance_service.subprocess.Popen",
                return_value=proc_mock,
            ),
        ):
            await maintenance_service._run_sidecar(["sidecar", "dump"], cancel_allowed=True)

        job.assign.assert_called_once_with(proc_mock)
        assert captured["job"] is job
        job.close.assert_called_once_with()
        assert maintenance_service._active_job is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_assign_failure_exposes_no_job(self, maintenance_service, monkeypatch) -> None:
        """D1-04: Job 登记失败时显式降级（不暴露 Job），取消只能报未确认。"""
        from services.embedded_pg_maintenance_service import _WindowsJob

        monkeypatch.setattr(os, "name", "nt")
        job = MagicMock()
        job.assign.return_value = False
        proc_mock = MagicMock()
        proc_mock.returncode = 0
        captured: dict[str, object] = {}

        def _communicate(timeout=None):
            captured["job"] = maintenance_service._active_job
            captured["proc"] = maintenance_service._active_proc
            return ("", "")

        proc_mock.communicate = MagicMock(side_effect=_communicate)

        with (
            patch.object(_WindowsJob, "create", return_value=job),
            patch(
                "services.embedded_pg_maintenance_service.ThreadPoolManager",
                return_value=_DirectTP(),
            ),
            patch(
                "services.embedded_pg_maintenance_service.subprocess.Popen",
                return_value=proc_mock,
            ),
        ):
            await maintenance_service._run_sidecar(["sidecar", "dump"], cancel_allowed=True)

        assert captured["job"] is None
        assert captured["proc"] is proc_mock
        job.close.assert_called_once_with()


# =============================================================================
# TestSidecarUtf8Decoding: D16-F08 维护客户端按 UTF-8 显式解码
# =============================================================================
class TestSidecarUtf8Decoding:
    """D16-F08: sidecar 输出固定 UTF-8，维护客户端不得按系统 locale 解码。

    - Popen 必须显式 ``encoding="utf-8", errors="replace"``
    - 非 UTF-8 locale（cp1252/cp936）下中文路径与诊断信息逐字一致
    - 机器协议 stdout 的损坏字节（U+FFFD）显式拒绝，诊断 stderr 容错
    """

    @pytest.mark.asyncio(loop_scope="function")
    async def test_popen_passes_explicit_utf8_replace(self, maintenance_service) -> None:
        """Popen 显式指定 UTF-8 + replace，不依赖系统 locale。"""
        proc_mock = MagicMock()
        proc_mock.returncode = 0
        proc_mock.communicate = MagicMock(return_value=("", ""))
        popen_mock = MagicMock(return_value=proc_mock)

        with (
            patch(
                "services.embedded_pg_maintenance_service.ThreadPoolManager",
                return_value=_DirectTP(),
            ),
            patch(
                "services.embedded_pg_maintenance_service.subprocess.Popen",
                popen_mock,
            ),
        ):
            await maintenance_service._run_sidecar(["sidecar", "doctor"])

        _args, kwargs = popen_mock.call_args
        assert kwargs["text"] is True
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"

    @pytest.mark.parametrize("locale_encoding", ["cp1252", "cp936"])
    @pytest.mark.asyncio(loop_scope="function")
    async def test_chinese_output_decoded_verbatim_under_non_utf8_locale(
        self, maintenance_service, monkeypatch, locale_encoding: str
    ) -> None:
        """cp1252/cp936 系统 locale 下，UTF-8 中文路径与 stderr 不被误解码。

        模拟方式：把 Popen 默认文本编码来源（``subprocess._text_encoding``）改为非 UTF-8；
        真实子进程直接写 UTF-8 字节（不受子进程自身 locale 影响）。
        """
        import sys

        stdout_text = "备份完成: D:/数据/备份.dump"
        stderr_text = "备份完成"
        script = (
            "import sys;"
            f"sys.stdout.buffer.write({stdout_text.encode('utf-8')!r});"
            f"sys.stderr.buffer.write({stderr_text.encode('utf-8')!r})"
        )

        with (
            patch("subprocess._text_encoding", return_value=locale_encoding),
            patch(
                "services.embedded_pg_maintenance_service.ThreadPoolManager",
                return_value=_DirectTP(),
            ),
        ):
            exit_code, stdout, stderr = await maintenance_service._run_sidecar([sys.executable, "-c", script])

        assert exit_code == 0
        assert stdout == stdout_text
        assert stderr == stderr_text

    @pytest.mark.asyncio(loop_scope="function")
    async def test_doctor_rejects_corrupted_stdout(self, maintenance_service) -> None:
        """doctor JSON 字符串字段含 U+FFFD（非法 UTF-8）时显式拒绝，不当作有效诊断。"""
        from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceError

        corrupted = dict(MOCK_DOCTOR_JSON)
        corrupted["data_dir"] = "D:/\ufffd/data"
        # ensure_ascii=False：默认 ensure_ascii=True 会把 U+FFFD 转义成 ASCII 序列
        # ``\ufffd``，stdout 里就不存在真实替换字符，无法模拟 Popen(errors="replace")
        # 解码损坏字节后的现场。
        with _patch_run_async(0, json.dumps(corrupted, ensure_ascii=False)):
            with pytest.raises(EmbeddedPgMaintenanceError, match="invalid UTF-8"):
                await maintenance_service.doctor()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_maintenance_shell_rejects_corrupted_stdout(self, maintenance_service) -> None:
        """maintenance-shell JSON 含 U+FFFD 时显式拒绝，避免返回乱码 psql 路径。"""
        from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceError

        shell_json = {
            "psql_path": "D:/\ufffd/psql.exe",
            "connection_string_redacted": "postgresql://postgres:***@127.0.0.1:55433/qtrading",
        }
        # 同上：ensure_ascii=False 保留真实 U+FFFD，匹配 Popen 解码损坏字节后的 stdout。
        with _patch_run_async(0, json.dumps(shell_json, ensure_ascii=False)):
            with pytest.raises(EmbeddedPgMaintenanceError, match="invalid UTF-8"):
                await maintenance_service.maintenance_shell()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_restore_rejects_corrupted_stdout_target_path(self, maintenance_service, tmp_path: Path) -> None:
        """restore 从 stdout 取目标路径时，损坏的 UTF-8 不得作为成功目录返回。"""
        from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceError

        input_path = tmp_path / "backup.dump"
        input_path.write_bytes(b"fake")

        with _patch_run_async(0, "D:/\ufffd/restored"):
            with pytest.raises(EmbeddedPgMaintenanceError, match="invalid UTF-8"):
                await maintenance_service.restore(input_path)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_restore_explicit_target_ignores_corrupted_stdout(self, maintenance_service, tmp_path: Path) -> None:
        """restore 显式指定 target_data_dir 时不消费 stdout，损坏字节不影响成功结果。"""
        from services.embedded_pg_maintenance_service import RestoreResult

        input_path = tmp_path / "backup.dump"
        input_path.write_bytes(b"fake")
        target = tmp_path / "new_data"

        with _patch_run_async(0, "D:/\ufffd/ignored"):
            result = await maintenance_service.restore(input_path, target)

        assert isinstance(result, RestoreResult)
        assert result.target_data_dir == str(target)
        assert result.exit_code == 0

    @pytest.mark.asyncio(loop_scope="function")
    async def test_dump_ignores_corrupted_stdout(self, maintenance_service, tmp_path: Path) -> None:
        """dump 不消费 stdout，诊断噪声中的替换字符不得把成功备份误判为失败。"""
        from services.embedded_pg_maintenance_service import DumpResult

        output_path = tmp_path / "backup.dump"
        output_path.write_bytes(b"fake dump content")

        with _patch_run_async(0, "D:/\ufffd/noise"):
            result = await maintenance_service.dump(output_path)

        assert isinstance(result, DumpResult)
        assert result.exit_code == 0
        assert result.file_size == len(b"fake dump content")

    @pytest.mark.asyncio(loop_scope="function")
    async def test_stderr_replacement_still_classified_by_exit_code(self, maintenance_service, tmp_path: Path) -> None:
        """诊断 stderr 使用 replace：损坏字节不阻断退出码分类（exit 30 仍映射 dump_restore_failed）。"""
        from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceError

        with _patch_run_async(30, "", "dump failed: \ufffd"):
            with pytest.raises(EmbeddedPgMaintenanceError, match="dump_restore_failed"):
                await maintenance_service.dump(tmp_path / "backup.dump")


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


# =============================================================================
# TestEmbeddedPgMaintenanceServiceResetPassword: reset-password 用户名透传（M23）
# =============================================================================
class TestEmbeddedPgMaintenanceServiceResetPassword:
    """M23：reset-password 须按 embedded_pg_username 透传 --username（默认 postgres 不传）。"""

    def test_build_reset_password_argv_default_username_omits_flag(self, maintenance_service) -> None:
        """默认配置（embedded_pg_username=postgres）不传 --username，兼容旧 sidecar。"""
        argv = maintenance_service._build_reset_password_argv()
        assert argv[0] == "/fake/sidecar"
        assert argv[1] == "reset-password"
        assert "--data-dir" in argv
        assert "--username" not in argv
        assert "postgres" not in argv

    def test_build_reset_password_argv_passes_custom_username(
        self, maintenance_service, mock_config_dict: dict[str, object]
    ) -> None:
        """自定义超级用户名实例：reset-password 透传 --username，ALTER USER 指向正确角色。"""
        config = {**mock_config_dict, "embedded_pg_username": "qtrading"}
        with patch(
            "services.embedded_pg_maintenance_service.ConfigHandler.load_config",
            return_value=config,
        ):
            argv = maintenance_service._build_reset_password_argv()
        assert "--username" in argv
        assert argv[argv.index("--username") + 1] == "qtrading"

    @pytest.mark.parametrize(
        "bad_username",
        ["", " bad", "bad name", "a;drop", "a-b", "1abc", "a" * 64],
    )
    def test_build_reset_password_argv_rejects_invalid_username(
        self,
        maintenance_service,
        mock_config_dict: dict[str, object],
        bad_username: str,
    ) -> None:
        """非法用户名（空/特殊字符/注入片段/数字开头/超长）在 Python 侧即拒绝。

        该值会进入 sidecar `ALTER USER <name>` 的 SQL 文本，白名单校验是注入防护（R4）。
        """
        from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceError

        config = {**mock_config_dict, "embedded_pg_username": bad_username}
        with (
            patch(
                "services.embedded_pg_maintenance_service.ConfigHandler.load_config",
                return_value=config,
            ),
            pytest.raises(EmbeddedPgMaintenanceError, match="embedded_pg_username 非法"),
        ):
            maintenance_service._build_reset_password_argv()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_reset_password_success(self, maintenance_service) -> None:
        """sidecar exit 0 → 不抛异常（新密码由 sidecar 写入密码文件，不返回给调用方）。"""
        with _patch_run_async(0, "", ""):
            result = await maintenance_service.reset_password()
        assert result is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_reset_password_exit_2_raises_argument_error(self, maintenance_service) -> None:
        """sidecar exit 2（用户名非法）映射为 sidecar_argument_error。"""
        from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceError

        with _patch_run_async(2, "", "用户名非法（须为 [A-Za-z_][A-Za-z0-9_]*，长度 ≤ 63）"):
            with pytest.raises(EmbeddedPgMaintenanceError, match="sidecar_argument_error"):
                await maintenance_service.reset_password()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_reset_password_lock_conflict_raises(self, maintenance_service) -> None:
        """sidecar exit 50（qTrading 运行中）映射为 lock_conflict。"""
        from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceError

        with _patch_run_async(50, "", "qTrading running"):
            with pytest.raises(EmbeddedPgMaintenanceError, match="lock_conflict"):
                await maintenance_service.reset_password()
