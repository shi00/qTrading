"""EmbeddedPgMaintenanceService 单例：sidecar CLI 离线维护命令包装（Phase 3 §13.2, R-A2）。

职责：
- 调用 sidecar CLI 的 doctor/dump/restore/maintenance-shell/reset-password 子命令
- 解析 JSON 输出，校验 schema 版本（R-A3: ``qtrading.embedded_postgres.doctor.v1``）
- 通过 ThreadPoolManager 提交同步 subprocess（R16: 不阻塞事件循环）；dump 类命令暴露句柄可取消（CON-04）
- 错误分类映射（exit code → 错误类型 + 日志级别 + 用户提示）

设计要点：
- ``@register_singleton`` 协议：``_reset_singleton``（无 atexit 资源需清理）
- 路径从 AppConfig + ``ConfigHandler.load_config()`` 实时解析（不在构造期缓存，配合运行时配置切换）
- argv 中的路径、业务库名、超级用户名均来自 AppConfig（R-A2 边界；库名/用户名经白名单校验后透传）
- 超级用户名仅在配置为非 sidecar 默认 ``postgres`` 时透传 ``--username``（自定义用户名实例的
  维护命令必须与 ``run --username`` 一致，否则角色认证/改密作用于错误角色；默认配置不传该参数，
  兼容不支持 ``--username`` 的旧 sidecar）。dump/restore/maintenance-shell 与 reset-password
  均由同一实现透传（M21 / M23）
- 业务库名 ``--database`` 始终由 ``embedded_pg_database`` 显式透传给 dump/restore/maintenance-shell
  （D07-005：sidecar 维护命令不设默认库，避免自定义库部署备份/恢复落空）
- exit code 语义正本为 ``sidecars/qtrading-pg-sidecar/src/exit_codes.rs`` 与 how-to §9.6；
  doctor 只返回 0/2，任何非 0 退出（含 20 STOP_FAILED）均按失败处理
"""

from __future__ import annotations

import asyncio
import ctypes
import enum
import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from data.persistence.embedded_postgres.version import resolve_pg_major_version
from utils.config_handler import ConfigHandler
from utils.config_models import AppConfig
from utils.error_classifier import classify_severity
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.sanitizers import DataSanitizer
from utils.singleton_registry import register_singleton
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger("qtrading.embedded_pg_maintenance")

EXPECTED_DOCTOR_SCHEMA = "qtrading.embedded_postgres.doctor.v1"

# D16-F08: Popen 以 ``errors="replace"`` 解码 sidecar 输出，非法 UTF-8 字节会变成 U+FFFD。
# 机器协议 stdout（doctor/maintenance-shell JSON、restore 目标路径）出现该字符即原始字节已损坏，
# 必须显式拒绝，不能把乱码路径当作成功结果或让 JSON 字符串字段失真。
_INVALID_UTF8_REPLACEMENT = "\ufffd"

# UIR-F16-002: 业务库名白名单（与 sidecar maint.rs validate_database_name 对齐）。
# 库名会进入 sidecar 的 `CREATE DATABASE "<name>"` SQL 文本，只允许安全标识符：
# [A-Za-z_][A-Za-z0-9_]*，长度 ≤ 63（PostgreSQL NAMEDATALEN-1）。
_DATABASE_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}")

# sidecar CLI 的默认超级用户名（cli.rs DEFAULT_USERNAME）。仅在配置非该值时才透传
# ``--username``，保证默认配置下与不支持该参数的旧 sidecar 兼容。
SIDECAR_DEFAULT_USERNAME = "postgres"

# Windows 下主程序为 GUI（windowed）进程，拉起 console 子系统的 sidecar CLI 时若不带
# CREATE_NO_WINDOW 会弹出可见控制台窗口（备份/恢复/doctor 期间黑窗）；开发模式从终端
# 运行时子进程继承终端控制台不弹窗，故仅打包安装版暴露。POSIX 平台传 0。
# D07-007: 另加 CREATE_NEW_PROCESS_GROUP，使 POSIX 侧进程组语义在 Windows 控制台子进程上
# 保持独立；D1-04 起 Windows 取消改由受控 Job Object 承担，不再依赖 taskkill /T。
_SIDECAR_CREATION_FLAGS = (
    subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
)

# D07-007: 取消必须证明 sidecar wrapper 与其 pg_dump/pg_restore 子进程都已退出。
# 先给 SIGTERM 宽限期，未退出再整组强杀；两段都失败即「未知/仍可能运行」，不得报告已取消。
# 上限（3.0 + 1.5s）需留在关机看门狗预算内（默认 10s / 25s）。
_CANCEL_GRACE_S = 3.0
_CANCEL_KILL_GRACE_S = 1.5

# D1-04: Windows 取消以 Job Object 身份（句柄）为准，不再对裸 PID 执行 taskkill：
# ``proc.poll()`` 预检与真正发信号之间存在 PID 复用窗口，强杀裸 PID 可能误杀无关进程。
# 下列常量为 CreateJobObjectW / SetInformationJobObject 所需 winnt.h 取值。
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100


class _JobBasicLimitInformation(ctypes.Structure):
    """``JOBOBJECT_BASIC_LIMIT_INFORMATION`` 的 ctypes 映射（仅字段布局，跨平台可构造）。"""

    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _JobIoCounters(ctypes.Structure):
    """``IO_COUNTERS`` 的 ctypes 映射。"""

    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JobExtendedLimitInformation(ctypes.Structure):
    """``JOBOBJECT_EXTENDED_LIMIT_INFORMATION`` 的 ctypes 映射。"""

    _fields_ = [
        ("BasicLimitInformation", _JobBasicLimitInformation),
        ("IoInfo", _JobIoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _JobBasicAccountingInformation(ctypes.Structure):
    """``JOBOBJECT_BASIC_ACCOUNTING_INFORMATION`` 的 ctypes 映射（``ActiveProcesses`` 为判据）。"""

    _fields_ = [
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", ctypes.c_uint32),
        ("TotalProcesses", ctypes.c_uint32),
        ("ActiveProcesses", ctypes.c_uint32),
        ("TotalTerminatedProcesses", ctypes.c_uint32),
    ]


_JOB_KERNEL32: Any = None


def _job_kernel32() -> Any:
    """懒加载 kernel32 并声明 Job Object API 签名（仅 win32 实际调用）。"""
    global _JOB_KERNEL32
    if _JOB_KERNEL32 is None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[reportAttributeAccessIssue]  # Windows-only：WinDLL 在 Linux 平台不存在
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        kernel32.SetInformationJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        kernel32.SetInformationJobObject.restype = ctypes.c_bool
        kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        kernel32.AssignProcessToJobObject.restype = ctypes.c_bool
        kernel32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.TerminateJobObject.restype = ctypes.c_bool
        kernel32.QueryInformationJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        kernel32.QueryInformationJobObject.restype = ctypes.c_bool
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_bool
        _JOB_KERNEL32 = kernel32
    return _JOB_KERNEL32


class _WindowsJob:
    """维护子进程的受控 Job Object（D1-04，仅 win32 实例化）。

    取消作用于登记到该 Job 的进程集合（句柄身份），而不是裸 PID：``taskkill /PID`` 在
    ``proc.poll()`` 预检与真正发信号之间存在 PID 复用窗口，可能误杀无关进程。
    ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` 保证句柄关闭时残留的 pg 子进程随之终止，不产生
    孤儿继续写目标文件。创建或登记失败时调用方必须按「取消未证明」处理，不得退回裸 PID 强杀。
    登记发生在 ``Popen`` 之后：理论上存在「子进程在登记前自行派生子进程」的极窄窗口；sidecar
    需先解析参数才拉起 pg 工具，该窗口远小于取消阶段（分钟级）的 PID 复用窗口。
    """

    def __init__(self, handle: int, kernel32: Any) -> None:
        self._handle = handle
        self._kernel32 = kernel32
        self._lock = threading.RLock()
        self._closed = False

    @classmethod
    def create(cls) -> _WindowsJob | None:
        """创建带 KILL_ON_JOB_CLOSE 的 Job；创建/设置失败返回 None。"""
        kernel32 = _job_kernel32()
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            return None
        info = _JobExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            kernel32.CloseHandle(handle)
            return None
        return cls(int(handle), kernel32)

    def assign(self, proc: subprocess.Popen[str]) -> bool:
        """把刚启动的 sidecar 纳入 Job。

        ``OpenProcess`` 使用 ``proc.pid``：此刻该 PID 仍由本进程持有存活子进程（刚 Popen），
        不存在「PID 已被复用」的窗口；取消阶段的身份风险由 Job 句柄本身承担。
        """
        with self._lock:
            if self._closed or not self._handle:
                return False
            if proc.poll() is not None:
                # 子进程已退出：无可登记目标（也无需再登记）
                return False
            process_handle = self._kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, proc.pid)
            if not process_handle:
                return False
            try:
                return bool(self._kernel32.AssignProcessToJobObject(self._handle, process_handle))
            finally:
                self._kernel32.CloseHandle(process_handle)

    def terminate(self) -> bool:
        """终止 Job 内全部进程；Job 已关闭或调用失败返回 False。"""
        with self._lock:
            if self._closed or not self._handle:
                return False
            return bool(self._kernel32.TerminateJobObject(self._handle, 1))

    def wait_empty(self, timeout: float) -> bool:
        """在 ``timeout`` 内证明 Job 内活动进程数归零。

        句柄已关闭视为已终止：``KILL_ON_JOB_CLOSE`` 语义保证关闭时残留进程被终止。
        """
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                if self._closed or not self._handle:
                    return True
                info = _JobBasicAccountingInformation()
                ok = self._kernel32.QueryInformationJobObject(
                    self._handle,
                    _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
                    ctypes.byref(info),
                    ctypes.sizeof(info),
                    None,
                )
                if not ok:
                    return False
                if info.ActiveProcesses == 0:
                    return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(remaining, 0.05))

    def close(self) -> None:
        """关闭 Job 句柄（KILL_ON_JOB_CLOSE 使残留进程随之终止）；可重复调用。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._handle:
                self._kernel32.CloseHandle(self._handle)
                self._handle = 0


class EmbeddedPgMaintenanceError(RuntimeError):
    """sidecar 维护命令失败（非 0 退出 / JSON 解析失败 / schema 不匹配）。"""


class EmbeddedPgMaintenanceCancelledError(EmbeddedPgMaintenanceError):
    """在途可取消命令已取消，且已证明产物未生成或不完整（非业务失败）。"""


class EmbeddedPgMaintenanceCancelUnprovenError(EmbeddedPgMaintenanceError):
    """已请求取消但无法证明 sidecar 进程树已停止：状态未知，pg 子进程可能仍在运行。"""


class CancelStatus(enum.Enum):
    """``request_cancel()`` 的可核对结果（替代布尔值，区分已证明与未知）。"""

    NO_ACTIVE = "no_active"
    """无可取消的在途命令（含 Popen 前的预留期）。"""

    CANCELLED = "cancelled"
    """已证明 sidecar 及其 pg 子进程退出。"""

    ALREADY_EXITED = "already_exited"
    """终止信号未能送达——命令已在送达前自然结束（退出码由工作线程按业务语义分类）。"""

    UNPROVEN = "unproven"
    """无法证明进程树停止或无法证明目标身份（如 Windows 未登记受控 Job），可能仍在运行。"""


def _coerce_runtime_port(value: object) -> int | None:
    """校验 doctor JSON 可选 ``port`` 字段：仅接受 1..65535 的整数。

    旧版 sidecar 无该字段、字段为 null、类型非整数或越界时一律返回 ``None``（未知）。
    ``None`` 表示「端口未知」，调用方不得用外置配置端口补位，也不得当作 0。
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 < value <= 65535 else None


def _wait_process_exit(proc: subprocess.Popen[str], timeout: float) -> bool:
    """在 ``timeout`` 内证明单进程已退出（Windows 无受控 Job 时的 wrapper 兜底证明）。

    ``Popen.wait()`` 在另一线程持有 waitpid 锁时可能立即返回 ``None``，因此以
    ``poll()`` 是否给出退出码作为唯一判据，并在剩余时间内小步重试。
    """
    deadline = time.monotonic() + timeout
    while True:
        if proc.poll() is not None:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        try:
            proc.wait(timeout=min(remaining, 0.1))
        except subprocess.TimeoutExpired:
            continue
        except OSError:
            # 进程已被回收（竞态窗口），等待目的已达成
            return True


def _resolve_pgid(proc: subprocess.Popen[str]) -> int:
    """sidecar 以独立 session 启动，pg 子进程继承同一进程组（组 id == 启动时 pid）。"""
    try:
        return os.getpgid(proc.pid)  # type: ignore[reportAttributeAccessIssue]  # POSIX-only：getpgid 在 Windows 平台不存在（本函数仅 POSIX 分支调用）
    except OSError:
        # wrapper 已退出但进程组可能仍被 pg 子进程持有——沿用启动时的组 id
        return proc.pid


def _process_group_alive(proc: subprocess.Popen[str]) -> bool:
    """POSIX：进程组内是否仍有任何进程（含已忽略 SIGTERM 的 pg_dump 子进程）。"""
    pgid = _resolve_pgid(proc)
    try:
        os.killpg(pgid, 0)  # type: ignore[reportAttributeAccessIssue]  # POSIX-only：killpg 在 Windows 平台不存在（本函数仅 POSIX 分支调用）
    except ProcessLookupError:
        return False
    except OSError:
        # EPERM：组仍存在但无权探测，保守视为存活
        return True
    return True


class _SignalOutcome(enum.Enum):
    """``_signal_process_tree`` 的可核对结果。

    ``IDENTITY_UNPROVEN`` 表示无法证明终止作用于目标 sidecar 进程树（如 Windows 未成功登记
    受控 Job）：调用方必须按「取消未确认」上报，不得当作已取消或已自然结束。
    """

    DELIVERED = "delivered"
    """终止信号已送达仍存在的目标进程树。"""

    ALREADY_EXITED = "already_exited"
    """目标已自然结束，信号无处送达（退出码由工作线程按业务语义分类）。"""

    IDENTITY_UNPROVEN = "identity_unproven"
    """无法证明目标进程树身份，未发送任何终止信号。"""


def _wait_process_tree_exit(proc: subprocess.Popen[str], timeout: float, *, job: _WindowsJob | None = None) -> bool:
    """在 ``timeout`` 内证明整棵 sidecar 进程树已退出。

    POSIX 以「进程组为空」为判据——仅 wrapper 退出不足以证明，忽略 SIGTERM 的
    pg_dump/pg_restore 会成为孤儿继续写目标文件；Windows 优先以受控 Job 的活动进程数
    归零为判据（``job`` 为 None 时退化为 wrapper 退出，仅在没有可证明身份时的兜底）。
    """
    if os.name == "nt":
        if job is not None:
            return job.wait_empty(timeout)
        return _wait_process_exit(proc, timeout)
    deadline = time.monotonic() + timeout
    while True:
        if not _process_group_alive(proc):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        # 回收 wrapper，避免僵尸进程让进程组长期「存活」
        try:
            proc.poll()
        except OSError:
            pass
        time.sleep(min(remaining, 0.05))


def _signal_process_tree(proc: subprocess.Popen[str], *, force: bool, job: _WindowsJob | None = None) -> _SignalOutcome:
    """向 sidecar wrapper 及其 pg 子进程发送终止信号。

    - POSIX：sidecar 以 ``start_new_session=True`` 启动，pg_dump/pg_restore 继承同一进程组，
      ``killpg`` 使取消到达真正的 pg 子进程（仅 terminate wrapper 会留下孤儿）。
    - Windows：无信号语义，取消只能作用于启动时登记的受控 Job Object（D1-04）。未登记 Job
      （创建/登记失败）时返回 ``IDENTITY_UNPROVEN``，绝不退回 ``taskkill /PID``：裸 PID 强杀
      在 ``poll()`` 预检后仍有 PID 复用窗口，可能误杀无关进程。
    """
    if os.name == "nt":
        if job is None:
            return _SignalOutcome.IDENTITY_UNPROVEN
        if job.terminate():
            return _SignalOutcome.DELIVERED
        return _SignalOutcome.ALREADY_EXITED if proc.poll() is not None else _SignalOutcome.IDENTITY_UNPROVEN
    try:
        pgid = _resolve_pgid(proc)
        if pgid != proc.pid:
            # 防御：未成为进程组组长（或 pid 已被复用）时不得 killpg，避免误杀；
            # 仅当 wrapper 仍存活时才退化为单进程终止。
            if proc.poll() is None:
                if force:
                    proc.kill()
                else:
                    proc.terminate()
                return _SignalOutcome.DELIVERED
            return _SignalOutcome.ALREADY_EXITED
        os.killpg(pgid, signal.SIGKILL if force else signal.SIGTERM)
        return _SignalOutcome.DELIVERED
    except OSError:
        # 进程组已不存在：命令已自然结束
        return _SignalOutcome.ALREADY_EXITED


@dataclass(frozen=True, slots=True)
class DoctorResult:
    """sidecar doctor 命令解析结果（消费投影，非 wire 全量镜像）。

    R-A3: schema 字段必须等于 ``qtrading.embedded_postgres.doctor.v1``。

    D16-Q01: 本 DTO 有意只镜像 UI/维护包装实际消费的字段；Rust ``DoctorJson`` 中其余
    诊断字段保留在 CLI JSON。字段契约由 ``scripts/check_doctor_schema.py`` 守护
    （显式必需字段 + 允许忽略表），``issues`` 为必需镜像的故障渠道，不得移除或静默忽略。
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
    # 运行中实例的实际监听端口（sidecar v1+ 可选字段；旧版/未运行时为 None = 未知）
    port: int | None = None
    state_file: str = "unknown"
    runtime_status: str | None = None
    last_start_error: str | None = None
    issues: list[dict[str, str]] = field(default_factory=list)
    # §13.7.44 / §7.5 残留物扫描（sidecar v1+，向后兼容：旧版 sidecar 无此字段时为空列表）
    restore_residuals: list[str] = field(default_factory=list)
    dump_partials: list[str] = field(default_factory=list)


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
# 逐项对齐 sidecar ``exit_codes.rs`` 常量与 how-to §9.6；退出码编号 append-only。
# 15 为多种资源预检失败的通用码（磁盘空间 / 目录权限 / 文件系统 / 网盘同步路径 /
# 密码文件权限），不得固化为「磁盘空间不足」或编造剩余空间数值。
_EXIT_CODE_MAP: dict[int, tuple[str, int, str]] = {
    2: ("sidecar_argument_error", logging.ERROR, "维护命令参数错误（请检查用户名、库名、数据目录）"),
    10: ("setup_failed", logging.ERROR, "内置 PostgreSQL 组件安装/解压失败，请重新安装 qTrading 后重试"),
    11: ("initdb_failed", logging.ERROR, "数据库初始化失败"),
    12: ("pg_start_failed", logging.ERROR, "数据库启动失败"),
    13: ("health_check_failed", logging.ERROR, "数据库启动后健康检查失败"),
    14: ("create_database_failed", logging.ERROR, "业务数据库创建失败"),
    15: (
        "preflight_failed",
        logging.WARNING,
        "资源预检失败（磁盘空间、目录权限、文件系统、网盘同步路径、密码文件权限），请按 sidecar 提示处理后重试",
    ),
    16: ("password_failed", logging.ERROR, "密码文件缺失/不可解密或认证不匹配，请按文档执行 reset-password 后重试"),
    20: ("stop_failed", logging.ERROR, "停止 PostgreSQL 失败（进程仍存活），请查看 sidecar 日志后重试"),
    30: (
        "dump_restore_failed",
        logging.ERROR,
        "备份/恢复执行失败：dump 输出已存在或另一任务占用同一输出，或 restore/备份数据异常；请查看 sidecar stderr",
    ),
    40: (
        "data_dir_abnormal",
        logging.ERROR,
        "数据目录状态异常（非空但未初始化、版本不匹配、文件损坏或缺失），请先运行 doctor 并按恢复文档处理",
    ),
    50: ("lock_conflict", logging.WARNING, "请先关闭 qTrading，并确认数据库实例已停止（可执行 stop 命令）后重试"),
    60: ("postgres_died", logging.ERROR, "PostgreSQL 进程意外退出（sidecar 不自动重启），请重启 qTrading"),
}


@register_singleton
class EmbeddedPgMaintenanceService:
    """sidecar CLI 离线维护命令包装单例（R-A2, 不扩展 EmbeddedPostgresService）。

    所有命令通过 subprocess 调用 sidecar binary，解析 JSON 输出。
    argv 路径与业务库名来自 AppConfig + ``ConfigHandler.load_config()``（库名经白名单校验）。
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
            # CON-04: 在途可取消命令句柄（仅 dump 类注册）。__init__ 内初始化，保证
            # "开过备份面板但未执行备份就关机"时 request_cancel() 可安全读取。
            self._active_proc: subprocess.Popen | None = None
            # D16-F06: 单任务预留位。必须在 Popen 之前置位，确保并发第二个 dump
            # 在启动子进程前即被拒绝（旧实现先 Popen 后检查，被拒子进程已可能触碰输出文件）。
            self._dump_task_reserved = False
            # D1-04: 单调递增运行代际令牌。取消标记 ``_cancel_requested_generation`` 绑定
            # 具体代际，新一轮运行即换新代际，旧标记不会污染本轮退出分类。
            self._run_generation = 0
            self._active_generation: int | None = None
            self._cancel_requested_generation: int | None = None
            # D1-04: 当前运行的受控 Windows Job（仅 win32 且成功登记时非 None）；
            # 为 None 时 Windows 取消只能报「未确认」，绝不对裸 PID 强杀。
            self._active_job: _WindowsJob | None = None
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
            EmbeddedPgMaintenanceError: sidecar 非 0 退出（doctor 契约仅 0/2）或 JSON 解析失败
            asyncio.CancelledError: R2 透传（由 ThreadPoolManager.run_async 传播）
        """
        # R16: argv 构建含动态版本解析（resolve_pg_major_version 运行阻塞子进程），
        # 经 to_thread 卸载避免阻塞事件循环。
        argv = await asyncio.to_thread(self._build_doctor_argv)
        exit_code, stdout, stderr = await self._run_sidecar(argv)
        # doctor 的退出码语义正本为 exit_codes.rs/how-to §9.6：诊断成功为 0（结论须解析
        # JSON issues），参数错误/JSON 输出失败为 2；不存在「PG 未运行」的 20 成功语义。
        if exit_code != 0:
            self._raise_for_exit_code(exit_code, stderr, "doctor")
        stdout = self._reject_corrupted_stdout(stdout, "doctor")
        try:
            data = json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError as exc:
            # R9: stdout 可能含 PG 数据目录路径，经 sanitize_error 脱敏后再进入异常消息
            sanitized_stdout = DataSanitizer.sanitize_error(stdout)
            raise EmbeddedPgMaintenanceError(f"doctor JSON parse failed: {exc}; stdout={sanitized_stdout!r}") from exc
        return self._validate_doctor_json(data)

    @log_async_operation(
        operation_name="embedded_pg_dump",
        threshold_ms=PerfThreshold.DB_BULK_IO,
    )
    async def dump(self, output_path: Path) -> DumpResult:
        """运行 sidecar dump 命令，创建数据库备份（PostgreSQL custom format）。

        Raises:
            EmbeddedPgMaintenanceError: dump 失败（exit != 0）
            EmbeddedPgMaintenanceCancelledError: 已取消且产物未生成/不完整（非失败）
            EmbeddedPgMaintenanceCancelUnprovenError: 已请求取消但无法证明进程树停止
            asyncio.CancelledError: R2 透传
        """
        argv = await asyncio.to_thread(self._build_dump_argv, output_path)
        # CON-04: dump 是可安全中断的备份类命令，注册句柄供 request_cancel() 停机终止
        try:
            exit_code, _stdout, stderr = await self._run_sidecar(
                argv, cancel_allowed=True
            )  # 仅需 exit_code/stderr 判定失败，stdout 忽略（_stdout 下划线已豁免 RUF059）
        except EmbeddedPgMaintenanceCancelledError:
            # D07-007: 取消后必须校验目标完整性，不得把半成品或负退出码当业务失败/成功
            return self._resolve_cancelled_dump(Path(output_path))
        if exit_code != 0:
            self._raise_for_exit_code(exit_code, stderr, "dump")
        path = Path(output_path)
        file_size = path.stat().st_size if path.exists() else 0  # noqa: ASYNC240  # dump 完成后一次性 stat/exists 探测（非交互热路径）
        return DumpResult(output_path=str(output_path), file_size=file_size, exit_code=exit_code)

    def _resolve_cancelled_dump(self, output_path: Path) -> DumpResult:
        """取消后判定 dump 产物的可报告状态（D07-007）。

        sidecar 先写唯一 ``.partial`` 再原子改名为最终文件，因此：
        - 最终文件不存在 → 取消成功，且未留下可用/半成品备份；
        - 最终文件存在且非空 → 改名已完成，备份在取消生效前已完整生成，按完成返回；
        - 最终文件存在但为 0 字节 → 不是完整备份，取消按「不完整」报告，不得当成功。

        Raises:
            EmbeddedPgMaintenanceCancelledError: 无产物或产物不完整
            EmbeddedPgMaintenanceCancelUnprovenError: 无法确认产物状态（stat 失败）
        """
        try:
            size = output_path.stat().st_size  # 取消后一次性探测（非交互热路径）
        except FileNotFoundError:
            logger.info(
                "[embedded_pg_maintenance] dump cancelled; no output file produced: %s",
                DataSanitizer.sanitize_error(str(output_path)),  # R9: 路径可能含用户名等敏感信息
            )
            raise EmbeddedPgMaintenanceCancelledError("备份已取消，未生成备份文件") from None
        except OSError as exc:
            # R9: 路径与 OS 原因经脱敏后再进入日志/异常消息
            logger.warning(
                "[embedded_pg_maintenance] dump cancelled but output state unknown: %s (%s)",
                DataSanitizer.sanitize_error(str(output_path)),
                DataSanitizer.sanitize_error(exc),
            )
            raise EmbeddedPgMaintenanceCancelUnprovenError("备份已取消，但无法确认输出文件状态") from exc
        if size > 0:
            logger.info(
                "[embedded_pg_maintenance] dump completed before cancellation took effect: %s",
                DataSanitizer.sanitize_error(str(output_path)),
            )
            return DumpResult(output_path=str(output_path), file_size=size, exit_code=0)
        raise EmbeddedPgMaintenanceCancelledError("备份已取消，但输出文件存在且不完整（0 字节），请删除后重试")

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
        argv = await asyncio.to_thread(self._build_restore_argv, input_path, target_data_dir)
        exit_code, stdout, stderr = await self._run_sidecar(argv)
        if exit_code != 0:
            self._raise_for_exit_code(exit_code, stderr, "restore")
        # 无 target_data_dir 时从 sidecar stdout 取（sidecar §12.2 原子切换输出新目录）
        if target_data_dir:
            target = str(target_data_dir)
        else:
            # D16-F08: stdout 是机器协议（新数据目录路径），损坏的 UTF-8 不得作为成功目录返回。
            target = self._reject_corrupted_stdout(stdout, "restore").strip()
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
        argv = await asyncio.to_thread(self._build_maintenance_shell_argv)
        exit_code, stdout, stderr = await self._run_sidecar(argv)
        if exit_code != 0:
            self._raise_for_exit_code(exit_code, stderr, "maintenance-shell")
        stdout = self._reject_corrupted_stdout(stdout, "maintenance-shell")
        try:
            data = json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError as exc:
            # R9: stdout 可能含 PG 数据目录路径，经 sanitize_error 脱敏后再进入异常消息
            sanitized_stdout = DataSanitizer.sanitize_error(stdout)
            raise EmbeddedPgMaintenanceError(
                f"maintenance-shell JSON parse failed: {exc}; stdout={sanitized_stdout!r}"
            ) from exc
        return MaintenanceShellInfo(
            psql_path=str(data.get("psql_path", "")),
            connection_string_redacted=str(data.get("connection_string_redacted", "")),
            exit_code=exit_code,
        )

    @log_async_operation(
        operation_name="embedded_pg_reset_password",
        threshold_ms=PerfThreshold.EXTERNAL_NETWORK,
    )
    async def reset_password(self) -> None:
        """运行 sidecar reset-password 命令，重置超级用户密码（§13.7.8）。

        新密码由 sidecar 生成并写入密码文件（Windows DPAPI / Unix 0600），不经 stdout
        返回，调用方重启应用后由 ``run`` 读取（R9：密码不落地到日志或返回值）。
        用户名取 ``embedded_pg_username``（与 ``run --username`` 对齐，M23）。

        Raises:
            EmbeddedPgMaintenanceError: 用户名/库名非法、维护锁冲突、数据目录异常、
                临时实例启动或 ALTER USER 失败（exit != 0）
            asyncio.CancelledError: R2 透传
        """
        argv = await asyncio.to_thread(self._build_reset_password_argv)
        exit_code, _stdout, stderr = await self._run_sidecar(argv)
        if exit_code != 0:
            self._raise_for_exit_code(exit_code, stderr, "reset-password")

    # ---- argv 构造 ----

    def _get_sidecar_path_and_data_dir(self) -> tuple[str, str]:
        """从 AppConfig 解析 sidecar binary 路径 + data_dir 路径。

        与 ``EmbeddedPostgresService.from_config`` 一致的默认搜索逻辑：
        - sidecar_path 为空时：
          - PyInstaller frozen 模式（``sys.frozen``）：``sys._MEIPASS / sidecars / qtrading-pg-sidecar[.exe]``
          - 开发模式：按平台默认搜索 ``sidecars/qtrading-pg-sidecar[.exe]``
        - data_root 为空时用 platformdirs 默认 ``<app data>/postgres/<pg_major>``，
          其中 <pg_major> 由 ``resolve_pg_major_version(Path(sidecar_path))`` 动态解析
          （以 sidecar 二进制为权威来源，见 version.py）
        """
        config = AppConfig.model_validate(ConfigHandler.load_config())
        sidecar_path = config.embedded_pg_sidecar_path
        if not sidecar_path:
            import sys

            exe_suffix = ".exe" if os.name == "nt" else ""
            if getattr(sys, "frozen", False):
                meipass = getattr(sys, "_MEIPASS", None)
                if meipass is None:
                    raise EmbeddedPgMaintenanceError(
                        "sys.frozen is True but sys._MEIPASS is None; cannot resolve sidecar binary path"
                    )
                sidecar_path = str(Path(meipass) / "sidecars" / f"qtrading-pg-sidecar{exe_suffix}")
            else:
                sidecar_path = str(Path("sidecars") / f"qtrading-pg-sidecar{exe_suffix}")
        if config.embedded_pg_data_root:
            data_root = Path(config.embedded_pg_data_root)
        else:
            import platformdirs

            app_data = Path(platformdirs.user_data_dir("qTrading"))
            data_root = app_data / "postgres" / resolve_pg_major_version(Path(sidecar_path))
        data_dir = data_root / "data"
        return sidecar_path, str(data_dir)

    def _get_database_name(self) -> str:
        """读取并校验配置中的业务库名（UIR-F16-002：dump/restore 不得硬编码 qtrading）。

        库名来自 ``AppConfig.embedded_pg_database``（与运行时 ``EmbeddedPostgresService``
        使用同一配置项），经白名单校验后透传给 sidecar ``--database``。

        Raises:
            EmbeddedPgMaintenanceError: 库名为空 / 含非法字符 / 超过 63 字符
        """
        config = AppConfig.model_validate(ConfigHandler.load_config())
        database = config.embedded_pg_database
        if not _DATABASE_NAME_PATTERN.fullmatch(database):
            # R9: 非法值可能来自用户输入，经 sanitize_error 脱敏后再进入异常消息
            sanitized = DataSanitizer.sanitize_error(database)
            raise EmbeddedPgMaintenanceError(
                f"embedded_pg_database 非法（须为 [A-Za-z_][A-Za-z0-9_]*，长度 ≤ 63）: {sanitized!r}"
            )
        return database

    def _get_username(self) -> str:
        """读取并校验配置中的超级用户名（dump/restore/maintenance-shell/reset-password
        须与实例用户名一致，M21 / M23）。

        用户名来自 ``AppConfig.embedded_pg_username``（与运行时 ``EmbeddedPostgresService``
        ``run --username`` 使用同一配置项），经白名单校验后透传给 sidecar ``--username``。

        Raises:
            EmbeddedPgMaintenanceError: 用户名为空 / 含非法字符 / 超过 63 字符
        """
        config = AppConfig.model_validate(ConfigHandler.load_config())
        username = config.embedded_pg_username
        if not _DATABASE_NAME_PATTERN.fullmatch(username):
            # R9: 非法值可能来自用户输入，经 sanitize_error 脱敏后再进入异常消息
            sanitized = DataSanitizer.sanitize_error(username)
            raise EmbeddedPgMaintenanceError(
                f"embedded_pg_username 非法（须为 [A-Za-z_][A-Za-z0-9_]*，长度 ≤ 63）: {sanitized!r}"
            )
        return username

    def _username_args(self) -> list[str]:
        """返回 ``--username`` 参数（仅配置非 sidecar 默认值时），兼容旧 sidecar。"""
        username = self._get_username()
        if username == SIDECAR_DEFAULT_USERNAME:
            return []
        return ["--username", username]

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
            "--database",
            self._get_database_name(),
            "--output",
            str(output_path),
            *self._username_args(),
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
            "--database",
            self._get_database_name(),
            *self._username_args(),
        ]
        if target_data_dir:
            argv.extend(["--target-data-dir", str(target_data_dir)])
        return argv

    def _build_maintenance_shell_argv(self) -> list[str]:
        """maintenance-shell argv：库名取 ``embedded_pg_database``。

        D07-005：sidecar 已不提供默认库名，连接串/psql 提示须显示应用实际业务库，
        否则自定义库部署会按 qtrading 连接空库。
        """
        sidecar_path, data_dir = self._get_sidecar_path_and_data_dir()
        return [
            sidecar_path,
            "maintenance-shell",
            "--data-dir",
            data_dir,
            "--database",
            self._get_database_name(),
            *self._username_args(),
        ]

    def _build_reset_password_argv(self) -> list[str]:
        """reset-password argv：自定义超级用户名必须透传，否则 ALTER USER 作用于不存在的角色。"""
        sidecar_path, data_dir = self._get_sidecar_path_and_data_dir()
        return [sidecar_path, "reset-password", "--data-dir", data_dir, *self._username_args()]

    # ---- subprocess 调用 ----

    async def _run_sidecar(self, argv: list[str], *, cancel_allowed: bool = False) -> tuple[int, str, str]:
        """调用 sidecar CLI，返回 ``(exit_code, stdout, stderr)``。

        通过 ThreadPoolManager 提交同步 subprocess（R16: 不阻塞事件循环）。
        - 用 Popen 而非 run：暴露进程句柄供 ``request_cancel()`` 终止在途 dump（CON-04）。
          D07-007: 进程以独立进程组（POSIX ``start_new_session`` / Windows
          ``CREATE_NEW_PROCESS_GROUP``）启动，使取消信号能到达 sidecar 内部再拉起的
          pg_dump/pg_restore，而不是只杀 wrapper 留下孤儿写目标文件。
        - TimeoutExpired 时 kill 子进程（subprocess.run 超时不杀进程会泄漏）；若此前已请求
          取消，则报告「状态未知/仍可能运行」，不按普通超时或成功处理。
        - ``cancel_allowed=True`` 仅 dump 类可安全中断操作（restore 拒绝取消，数据一致性优先）。
        - ``cancel_allowed=True`` 时先在锁内预留单任务状态，再启动子进程；第二个并发调用
          在 Popen 之前即被拒绝，任何情况下（含 Popen 抛错）都会在 ``finally`` 释放预留位。
        """

        def _run() -> tuple[int, str, str]:
            generation: int | None = None
            registered = False
            reserved = False
            job: _WindowsJob | None = None
            if cancel_allowed:
                # D16-F06: 先在锁内预留单任务状态，再 Popen。并发第二个 dump 在此处即被拒绝，
                # 不会启动任何子进程（旧实现先 Popen 后检查，被拒子进程已可能触碰输出文件）。
                with self._lock:
                    if self._active_proc is not None or self._dump_task_reserved:
                        raise EmbeddedPgMaintenanceError("已有备份任务在进行中，请先完成后再试")
                    self._dump_task_reserved = True
                    # D1-04: 每次可取消运行分配唯一代际令牌并清除旧标记；取消标记按代际绑定，
                    # 上一轮的取消请求不可能被本轮误读（跨轮污染）。
                    self._run_generation += 1
                    generation = self._run_generation
                    self._cancel_requested_generation = None
                    reserved = True
            try:
                if reserved:
                    job = self._create_windows_job()
                proc = subprocess.Popen(
                    argv,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    # D16-F08: sidecar（Rust serde/eprintln）固定输出 UTF-8，显式指定解码编码，
                    # 避免非 UTF-8 Windows locale（cp1252/cp936）按系统编码解码导致
                    # UnicodeDecodeError 或中文路径乱码。诊断 stderr 用 replace 容错，
                    # 机器协议 stdout 的损坏字节由 ``_reject_corrupted_stdout`` 显式拒绝。
                    encoding="utf-8",
                    errors="replace",
                    creationflags=_SIDECAR_CREATION_FLAGS,
                    # D07-007: 独立进程组，取消时整组终止（Windows 下该参数被忽略）
                    start_new_session=os.name != "nt",
                )
                if reserved and generation is not None:
                    # D1-04: 先把子进程登记进受控 Job，再向 request_cancel 暴露句柄——保证它
                    # 只可能看到「已归属可证明身份的 Job」与「明确无 Job」两种状态，不会看到
                    # 尚未登记的中间态（否则可能对空 Job 报「已取消」而真实子进程仍在跑）。
                    if job is not None and not job.assign(proc):
                        job.close()
                        job = None
                    with self._lock:
                        # 预留位保证此处无第二个任务；登记句柄供 request_cancel() 终止。
                        self._active_proc = proc
                        self._active_generation = generation
                        self._active_job = job
                        registered = True
                try:
                    stdout, stderr = proc.communicate(timeout=3600)
                except subprocess.TimeoutExpired as exc:
                    # review03-C19: 超时经业务异常包装；同时 kill 子进程回收输出，避免泄漏。
                    # kill/communicate 合并容错：进程在超时与 kill 竞态窗口自然退出时，
                    # kill 抛 ProcessLookupError(OSError)，忽略清理异常、保留超时业务异常。
                    try:
                        proc.kill()
                        proc.communicate()
                    except OSError:
                        pass  # 进程已被回收，忽略输出回收异常
                    if self._is_cancel_requested(generation):
                        # D07-007: 已请求取消但 3600s 内仍未退出——状态未知，不得报超时/成功
                        logger.error(
                            "[embedded_pg_maintenance] cancel unproven: sidecar still running after timeout: %s",
                            DataSanitizer.sanitize_error(str(argv[:2])),
                        )
                        raise EmbeddedPgMaintenanceCancelUnprovenError(
                            "取消未完成：无法证明 sidecar/pg 子进程已停止，进程可能仍在运行；"
                            "请确认备份目标未被继续写入并检查残留进程"
                        ) from exc
                    severity = classify_severity(exc, "general")
                    logger.log(
                        logging.WARNING if severity == "recoverable" else logging.ERROR,
                        "[embedded_pg_maintenance] sidecar command timed out (severity=%s): %s",
                        severity,
                        DataSanitizer.sanitize_error(str(argv[:2])),
                    )
                    raise EmbeddedPgMaintenanceError(
                        f"sidecar 命令超时（3600s）: {argv[0] if argv else 'sidecar'}"
                    ) from exc
                if self._is_cancel_requested(generation):
                    # D07-007/D1-04: 取消后 wrapper 的负退出码不代表业务失败；但「已取消」也必须
                    # 以整棵进程树停止为前提。Windows 有受控 Job 时以 Job 内活动进程数归零为
                    # 判据，POSIX 以进程组为空为判据；两者都无法证明时只能报未知。
                    # 宽限窗口覆盖 request_cancel() 的 SIGTERM + 强杀两段等待。
                    if not _wait_process_tree_exit(proc, _CANCEL_GRACE_S + _CANCEL_KILL_GRACE_S, job=job):
                        logger.error(
                            "[embedded_pg_maintenance] cancel unproven after wrapper exit: %s",
                            DataSanitizer.sanitize_error(str(argv[:2])),
                        )
                        raise EmbeddedPgMaintenanceCancelUnprovenError(
                            "取消未完成：sidecar 已退出但 pg 子进程可能仍在运行，请检查残留进程"
                        )
                    logger.info(
                        "[embedded_pg_maintenance] sidecar command cancelled: %s",
                        DataSanitizer.sanitize_error(str(argv[:2])),
                    )
                    raise EmbeddedPgMaintenanceCancelledError(f"{argv[1] if len(argv) > 1 else 'sidecar'} 已被取消")
                return proc.returncode, stdout, stderr
            finally:
                with self._lock:
                    if registered:
                        self._active_proc = None
                        self._active_generation = None
                        self._active_job = None
                    if reserved:
                        # Popen 失败时也在此释放预留位（失败不得留下永久占用状态）。
                        self._dump_task_reserved = False
                if job is not None:
                    # D1-04: KILL_ON_JOB_CLOSE 保证关闭时残留的 pg 子进程随之终止。
                    job.close()

        return await ThreadPoolManager().run_async(TaskType.IO, _run)

    @staticmethod
    def _create_windows_job() -> _WindowsJob | None:
        """Windows：为可取消维护命令创建受控 Job Object。

        创建失败不阻断命令（备份仍可执行），但取消随之降级为「未证明身份」，
        ``request_cancel`` 只能返回 ``UNPROVEN``，绝不退回裸 PID 强杀。
        """
        if os.name != "nt":
            return None
        try:
            job = _WindowsJob.create()
        except OSError as exc:
            logger.warning(
                "[embedded_pg_maintenance] Windows Job Object 创建失败，取消将按未确认处理: %s",
                DataSanitizer.sanitize_error(exc),
            )
            return None
        if job is None:
            logger.debug("[embedded_pg_maintenance] Windows Job Object 不可用，取消将按未确认处理")
        return job

    def _is_cancel_requested(self, generation: int | None) -> bool:
        """工作线程读取取消标记：仅当标记归属于本次运行代际时生效（D1-04）。"""
        if generation is None:
            return False
        with self._lock:
            return self._cancel_requested_generation == generation

    def request_cancel(self, *, timeout: float = _CANCEL_GRACE_S) -> CancelStatus:
        """终止在途可取消 sidecar 命令的整棵进程树，并证明其已停止（当前仅 dump）。

        同步方法，会阻塞至多 ``timeout + _CANCEL_KILL_GRACE_S`` 秒（D07-007：仅当能证明
        sidecar wrapper 与 pg 子进程都已退出才返回 ``CANCELLED``）；必须在工作线程调用
        （shutdown Step 6 已通过 ``asyncio.to_thread`` 调用），不得在事件循环线程调用。
        无活动命令或 restore/doctor/shell 命令返回 ``NO_ACTIVE``。
        注：``_run_sidecar`` 的 Popen 前预留期尚无进程句柄可终止，此时返回 ``NO_ACTIVE``；
        子进程启动后可再次调用（预留期极短，见 ``_run_sidecar`` 实现）。
        取消信号经独立进程组送达 sidecar 内部拉起的 pg_dump/pg_restore（POSIX ``killpg``），
        Windows 则作用于启动时登记的受控 Job Object（D1-04）；仅 terminate wrapper 会留下
        继续写目标的孤儿进程。
        ``UNPROVEN`` 表示无法证明停止或无法证明目标身份（如 Windows 未成功登记 Job），
        进程可能仍在运行——调用方不得当作已取消，须按「取消未确认」上报。
        """
        with self._lock:
            proc = self._active_proc
            generation = self._active_generation
            job = self._active_job
            if proc is None or generation is None:
                return CancelStatus.NO_ACTIVE
            # D1-04: 取消标记、代际绑定与首个终止信号在同一临界区提交；回滚也在同一临界区
            # 完成，工作线程不可能读到「将被撤销」的中间态（旧实现锁拆分导致误判取消）。
            self._cancel_requested_generation = generation
            outcome = _signal_process_tree(proc, force=False, job=job)
            if outcome is not _SignalOutcome.DELIVERED:
                # 同一临界区内回滚：仅撤销本代际的标记，不触碰已被新一轮写入的标记。
                if self._cancel_requested_generation == generation:
                    self._cancel_requested_generation = None
                if outcome is _SignalOutcome.IDENTITY_UNPROVEN:
                    logger.warning(
                        "[embedded_pg_maintenance] request_cancel: cannot prove target identity; "
                        "cancel not confirmed (pid=%s)",
                        proc.pid,
                    )
                    return CancelStatus.UNPROVEN
                logger.info("[embedded_pg_maintenance] request_cancel: active sidecar command already exited")
                return CancelStatus.ALREADY_EXITED
        # 以「进程组为空 / Job 内活动进程归零」为停止判据（wrapper 退出不等于 pg 子进程退出）
        if _wait_process_tree_exit(proc, timeout, job=job):
            logger.info(
                "[embedded_pg_maintenance] request_cancel: process tree terminated (pid=%s)",
                proc.pid,
            )
            return CancelStatus.CANCELLED
        # pg 子进程可能忽略 SIGTERM：整组强杀后再给一个短的退出证明窗口
        _signal_process_tree(proc, force=True, job=job)
        if _wait_process_tree_exit(proc, _CANCEL_KILL_GRACE_S, job=job):
            logger.info(
                "[embedded_pg_maintenance] request_cancel: process tree force-killed (pid=%s)",
                proc.pid,
            )
            return CancelStatus.CANCELLED
        logger.warning(
            "[embedded_pg_maintenance] request_cancel: cannot prove process tree stopped (pid=%s); "
            "pg child may still be running",
            proc.pid,
        )
        return CancelStatus.UNPROVEN

    @classmethod
    def has_active_operation(cls) -> bool:
        """是否存在在途的可取消 sidecar 命令（备份 dump）。

        只读探测，不构造单例：供设置页判断「备份进行中禁止切换数据库模式」。
        """
        instance = cls._instance
        if instance is None:
            return False
        with cls._lock:
            # D16-F06: Popen 前的预留期同样视为「有在途备份」，避免切换数据库模式与
            # 即将启动（或正在启动）的 dump 子进程交叉。
            return instance._active_proc is not None or instance._dump_task_reserved  # type: ignore[attr-defined]  # [reason: 单例 __init__ 已声明 _active_proc/_dump_task_reserved，但未作为类级注解]

    # ---- 错误分类 ----

    def _reject_corrupted_stdout(self, stdout: str, command: str) -> str:
        """拒绝机器协议 stdout 中的非法 UTF-8 替换字符（D16-F08）。

        Popen 使用 ``errors="replace"`` 保证诊断输出（stderr）不会因损坏字节中断，
        但机器消费的 stdout（JSON / restore 目标路径）若含 U+FFFD，说明原始 UTF-8
        字节不可靠：返回该值会把乱码路径当作恢复成功目录，或让 JSON 字符串字段失真。
        JSON 语法损坏仍由调用方的 ``json.loads`` 拒绝。

        Raises:
            EmbeddedPgMaintenanceError: stdout 含非法 UTF-8 替换字符
        """
        if _INVALID_UTF8_REPLACEMENT in stdout:
            raise EmbeddedPgMaintenanceError(
                f"{command} stdout contains invalid UTF-8 bytes; refusing to use corrupted output"
            )
        return stdout

    def _raise_for_exit_code(self, exit_code: int, stderr: str, command: str) -> None:
        """根据 sidecar exit code 抛出分类错误（对齐 exit_codes.rs / how-to §9.6）。

        未知 exit code 按 ERROR 级别 + ``unknown_error`` 类型处理（仍为非零失败）。
        异常消息保留原始 exit code 与脱敏后的 sidecar 原因，便于用户按真实原因处置。
        """
        error_type, log_level, user_hint = _EXIT_CODE_MAP.get(
            exit_code,
            ("unknown_error", logging.ERROR, f"未知错误（exit={exit_code}），请查看 sidecar 日志"),
        )
        # R9: stderr 来自 sidecar CLI 子进程，可能含 PG 数据目录路径、连接信息，
        # 经 sanitize_error 脱敏后再记录/引用
        sanitized_stderr = DataSanitizer.sanitize_error(stderr).strip()
        logger.log(
            log_level,
            "[embedded_pg_maintenance] %s command failed: exit=%s, type=%s, stderr=%s",
            command,
            exit_code,
            error_type,
            sanitized_stderr,
        )
        reason = f"; reason={sanitized_stderr}" if sanitized_stderr else ""
        raise EmbeddedPgMaintenanceError(f"{command} failed: exit={exit_code}, type={error_type}; {user_hint}{reason}")

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
            port=_coerce_runtime_port(data.get("port")),
            state_file=data.get("state_file", "unknown"),
            runtime_status=data.get("runtime_status"),
            last_start_error=data.get("last_start_error"),
            issues=data.get("issues", []),
            restore_residuals=data.get("restore_residuals", []),
            dump_partials=data.get("dump_partials", []),
        )
