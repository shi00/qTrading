"""EmbeddedPgMaintenanceService 单例：sidecar CLI 离线维护命令包装（Phase 3 §13.2, R-A2）。

职责：
- 调用 sidecar CLI 的 doctor/dump/restore/maintenance-shell 子命令
- 解析 JSON 输出，校验 schema 版本（R-A3: ``qtrading.embedded_postgres.doctor.v1``）
- 通过 ThreadPoolManager 提交同步 subprocess.run 避免阻塞事件循环（R16）
- 错误分类映射（exit code → 错误类型 + 日志级别 + 用户提示）

设计要点：
- ``@register_singleton`` 协议：``_reset_singleton``（无 atexit 资源需清理）
- 路径从 AppConfig + ``ConfigHandler.load_config()`` 实时解析（不在构造期缓存，配合运行时配置切换）
- argv 不含用户直接输入（R-A2 边界：所有路径来自 AppConfig）
- exit code 20 (PG 未运行) 在 doctor 命令中容忍为成功（sidecar doctor 是只读诊断）
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from utils.config_handler import ConfigHandler
from utils.config_models import AppConfig
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.singleton_registry import register_singleton
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger("qtrading.embedded_pg_maintenance")

EXPECTED_DOCTOR_SCHEMA = "qtrading.embedded_postgres.doctor.v1"


class EmbeddedPgMaintenanceError(RuntimeError):
    """sidecar 维护命令失败（非 0 退出 / JSON 解析失败 / schema 不匹配）。"""


@dataclass(frozen=True, slots=True)
class DoctorResult:
    """sidecar doctor 命令解析结果（对齐 sidecar maint.rs DoctorJson struct）。

    R-A3: schema 字段必须等于 ``qtrading.embedded_postgres.doctor.v1``。
    """

    schema: str
    data_dir: str
    initialized: bool
    pg_version: int | None = None
    bundled_pg_major: int | None = None
    version_match: bool = False
    critical_files_missing: list[str] = field(default_factory=list)
    install_dir_complete: bool = False
    missing_tools: list[str] = field(default_factory=list)
    lock_held: bool = False
    postgres_alive: bool = False
    state_file: str = "unknown"
    runtime_status: str | None = None
    last_start_error: str | None = None
    issues: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DumpResult:
    """sidecar dump 命令结果。"""

    output_path: str
    file_size: int
    exit_code: int = 0


@dataclass(frozen=True, slots=True)
class RestoreResult:
    """sidecar restore 命令结果。"""

    target_data_dir: str
    exit_code: int = 0


@dataclass(frozen=True, slots=True)
class MaintenanceShellInfo:
    """sidecar maintenance-shell 命令返回的脱敏连接信息。"""

    psql_path: str
    connection_string_redacted: str
    exit_code: int = 0


# exit code → (错误类型, 日志级别, 用户提示)
# 对齐 sidecar exit_codes.rs 与 §13.2 错误分类映射表
_EXIT_CODE_MAP: dict[int, tuple[str, int, str]] = {
    10: ("sidecar_arg_error", logging.ERROR, "维护命令参数错误"),
    11: ("initdb_failed", logging.ERROR, "数据库初始化失败"),
    12: ("pg_start_failed", logging.ERROR, "数据库启动失败"),
    15: ("disk_full", logging.WARNING, "磁盘空间不足，请清理后重试"),
    20: ("pg_not_running", logging.INFO, ""),  # 幂等，doctor 容忍
    40: ("pgdata_corrupt", logging.ERROR, "数据目录损坏，请使用恢复向导"),
    50: ("lock_conflict", logging.WARNING, "请先关闭 qTrading 再执行维护操作"),
}


@register_singleton
class EmbeddedPgMaintenanceService:
    """sidecar CLI 离线维护命令包装单例（R-A2, 不扩展 EmbeddedPostgresService）。

    所有命令通过 subprocess 调用 sidecar binary，解析 JSON 输出。
    argv 路径来自 AppConfig + ``ConfigHandler.load_config()``，不含用户直接输入。
    """

    _instance: EmbeddedPgMaintenanceService | None = None
    _lock = threading.RLock()

    def __new__(cls) -> EmbeddedPgMaintenanceService:
        with cls._lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance._initialized = False  # type: ignore[attr-defined]
                cls._instance = instance
            return cls._instance

    def __init__(self) -> None:
        with self._lock:
            if self._initialized:
                return
            self._initialized = True

    @classmethod
    def _reset_singleton(cls) -> None:
        """R15: 单例测试隔离。"""
        with cls._lock:
            cls._instance = None

    # ---- 公开命令 ----

    @log_async_operation(
        operation_name="embedded_pg_doctor",
        threshold_ms=PerfThreshold.EXTERNAL_NETWORK,
    )
    async def doctor(self) -> DoctorResult:
        """运行 sidecar doctor 命令，返回诊断结果。

        Raises:
            EmbeddedPgMaintenanceError: sidecar 非 0 退出（exit 20 除外）或 JSON 解析失败
            asyncio.CancelledError: R2 透传（由 ThreadPoolManager.run_async 传播）
        """
        argv = self._build_doctor_argv()
        exit_code, stdout, stderr = await self._run_sidecar(argv)
        # exit 20 (PG 未运行) 容忍为成功：doctor 是只读诊断，PG 未运行时仍可输出 JSON
        if exit_code != 0 and exit_code != 20:
            self._raise_for_exit_code(exit_code, stderr, "doctor")
        try:
            data = json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError as exc:
            raise EmbeddedPgMaintenanceError(f"doctor JSON parse failed: {exc}; stdout={stdout!r}") from exc
        return self._validate_doctor_json(data)

    @log_async_operation(
        operation_name="embedded_pg_dump",
        threshold_ms=PerfThreshold.DB_BULK_IO,
    )
    async def dump(self, output_path: Path) -> DumpResult:
        """运行 sidecar dump 命令，创建数据库备份（PostgreSQL custom format）。

        Raises:
            EmbeddedPgMaintenanceError: dump 失败（exit != 0）
            asyncio.CancelledError: R2 透传
        """
        argv = self._build_dump_argv(output_path)
        exit_code, stdout, stderr = await self._run_sidecar(argv)
        if exit_code != 0:
            self._raise_for_exit_code(exit_code, stderr, "dump")
        path = Path(output_path)
        file_size = path.stat().st_size if path.exists() else 0
        return DumpResult(output_path=str(output_path), file_size=file_size, exit_code=exit_code)

    @log_async_operation(
        operation_name="embedded_pg_restore",
        threshold_ms=PerfThreshold.DB_BULK_IO,
    )
    async def restore(
        self,
        input_path: Path,
        target_data_dir: Path | None = None,
    ) -> RestoreResult:
        """运行 sidecar restore 命令，恢复到新目录（不覆盖原目录，§12.2 原子切换）。

        Raises:
            EmbeddedPgMaintenanceError: restore 失败（exit != 0）
            asyncio.CancelledError: R2 透传
        """
        argv = self._build_restore_argv(input_path, target_data_dir)
        exit_code, stdout, stderr = await self._run_sidecar(argv)
        if exit_code != 0:
            self._raise_for_exit_code(exit_code, stderr, "restore")
        # 无 target_data_dir 时从 sidecar stdout 取（sidecar §12.2 原子切换输出新目录）
        target = str(target_data_dir) if target_data_dir else stdout.strip()
        return RestoreResult(target_data_dir=target, exit_code=exit_code)

    @log_async_operation(
        operation_name="embedded_pg_maintenance_shell",
        threshold_ms=PerfThreshold.EXTERNAL_NETWORK,
    )
    async def maintenance_shell(self) -> MaintenanceShellInfo:
        """启动临时维护实例，返回脱敏连接信息。

        Raises:
            EmbeddedPgMaintenanceError: 维护实例启动失败（exit != 0）或 JSON 解析失败
            asyncio.CancelledError: R2 透传
        """
        argv = self._build_maintenance_shell_argv()
        exit_code, stdout, stderr = await self._run_sidecar(argv)
        if exit_code != 0:
            self._raise_for_exit_code(exit_code, stderr, "maintenance-shell")
        try:
            data = json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError as exc:
            raise EmbeddedPgMaintenanceError(f"maintenance-shell JSON parse failed: {exc}; stdout={stdout!r}") from exc
        return MaintenanceShellInfo(
            psql_path=str(data.get("psql_path", "")),
            connection_string_redacted=str(data.get("connection_string_redacted", "")),
            exit_code=exit_code,
        )

    # ---- argv 构造 ----

    def _get_sidecar_path_and_data_dir(self) -> tuple[str, str]:
        """从 AppConfig 解析 sidecar binary 路径 + data_dir 路径。

        与 ``EmbeddedPostgresService.from_config`` 一致的默认搜索逻辑：
        - sidecar_path 为空时按平台默认搜索 ``sidecars/qtrading-pg-sidecar[.exe]``
        - data_root 为空时用 platformdirs 默认 ``<app data>/postgres/17``
        """
        config = AppConfig.model_validate(ConfigHandler.load_config())
        sidecar_path = config.embedded_pg_sidecar_path
        if not sidecar_path:
            exe_suffix = ".exe" if os.name == "nt" else ""
            sidecar_path = str(Path("sidecars") / f"qtrading-pg-sidecar{exe_suffix}")
        if config.embedded_pg_data_root:
            data_root = Path(config.embedded_pg_data_root)
        else:
            import platformdirs

            app_data = Path(platformdirs.user_data_dir("qTrading"))
            data_root = app_data / "postgres" / "17"
        data_dir = data_root / "data"
        return sidecar_path, str(data_dir)

    def _build_doctor_argv(self) -> list[str]:
        sidecar_path, data_dir = self._get_sidecar_path_and_data_dir()
        return [sidecar_path, "doctor", "--data-dir", data_dir]

    def _build_dump_argv(self, output_path: Path) -> list[str]:
        sidecar_path, data_dir = self._get_sidecar_path_and_data_dir()
        return [
            sidecar_path,
            "dump",
            "--data-dir",
            data_dir,
            "--output",
            str(output_path),
        ]

    def _build_restore_argv(self, input_path: Path, target_data_dir: Path | None) -> list[str]:
        sidecar_path, data_dir = self._get_sidecar_path_and_data_dir()
        argv = [
            sidecar_path,
            "restore",
            "--data-dir",
            data_dir,
            "--input",
            str(input_path),
        ]
        if target_data_dir:
            argv.extend(["--target-data-dir", str(target_data_dir)])
        return argv

    def _build_maintenance_shell_argv(self) -> list[str]:
        sidecar_path, data_dir = self._get_sidecar_path_and_data_dir()
        return [sidecar_path, "maintenance-shell", "--data-dir", data_dir]

    # ---- subprocess 调用 ----

    async def _run_sidecar(self, argv: list[str]) -> tuple[int, str, str]:
        """调用 sidecar CLI，返回 ``(exit_code, stdout, stderr)``。

        通过 ThreadPoolManager 提交同步 subprocess.run（R16: 不阻塞事件循环）。
        timeout=3600s 匹配 sidecar dump/restore 大库耗时。
        """

        def _run() -> tuple[int, str, str]:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=3600)
            return proc.returncode, proc.stdout, proc.stderr

        return await ThreadPoolManager().run_async(TaskType.IO, _run)

    # ---- 错误分类 ----

    def _raise_for_exit_code(self, exit_code: int, stderr: str, command: str) -> None:
        """根据 sidecar exit code 抛出分类错误（对齐 §13.2 错误分类映射表）。

        未知 exit code 按 ERROR 级别 + ``unknown_error`` 类型处理。
        """
        error_type, log_level, user_hint = _EXIT_CODE_MAP.get(
            exit_code,
            ("unknown_error", logging.ERROR, f"未知错误（exit={exit_code}）"),
        )
        logger.log(
            log_level,
            "[embedded_pg_maintenance] %s command failed: exit=%s, type=%s, stderr=%s",
            command,
            exit_code,
            error_type,
            stderr,
        )
        raise EmbeddedPgMaintenanceError(f"{command} failed: exit={exit_code}, type={error_type}; {user_hint}")

    def _validate_doctor_json(self, data: dict[str, Any]) -> DoctorResult:
        """校验 doctor JSON schema 版本并解析为 DoctorResult（R-A3）。

        Raises:
            EmbeddedPgMaintenanceError: schema 不匹配或必需字段缺失
        """
        schema = data.get("schema", "")
        if schema != EXPECTED_DOCTOR_SCHEMA:
            raise EmbeddedPgMaintenanceError(
                f"doctor JSON schema mismatch: expected {EXPECTED_DOCTOR_SCHEMA}, got {schema}"
            )
        # data_dir / initialized 是必需字段；其余字段缺失时使用 dataclass 默认值
        if "data_dir" not in data:
            raise EmbeddedPgMaintenanceError("doctor JSON missing required field: data_dir")
        if "initialized" not in data:
            raise EmbeddedPgMaintenanceError("doctor JSON missing required field: initialized")
        return DoctorResult(
            schema=schema,
            data_dir=data["data_dir"],
            initialized=data["initialized"],
            pg_version=data.get("pg_version"),
            bundled_pg_major=data.get("bundled_pg_major"),
            version_match=data.get("version_match", False),
            critical_files_missing=data.get("critical_files_missing", []),
            install_dir_complete=data.get("install_dir_complete", False),
            missing_tools=data.get("missing_tools", []),
            lock_held=data.get("lock_held", False),
            postgres_alive=data.get("postgres_alive", False),
            state_file=data.get("state_file", "unknown"),
            runtime_status=data.get("runtime_status"),
            last_start_error=data.get("last_start_error"),
            issues=data.get("issues", []),
        )
