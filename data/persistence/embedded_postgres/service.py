"""EmbeddedPostgresService 单例：sidecar 子进程管理 + URL 注入（Phase 2 §3.1）。

职责：
- Popen 拉起 Rust sidecar 子进程，解析 ready JSON（schema qtrading.embedded_postgres.run.ready.v1）
- 构造 ``postgresql+asyncpg://`` URL 并返回 ConnectionInfo
- 幂等 stop（stdin.close → wait → kill 兜底）
- 收集 sidecar/PG/Python 服务日志便于诊断（用户额外要求）

设计要点：
- ``@register_singleton`` 协议：``_reset_singleton`` + ``_atexit_cleanup``
- atexit 反序清理：本单例必须在 CacheManager 之后注册（先停 sidecar 再 dispose engine）
- ``asyncio.to_thread`` 包装同步 Popen/wait，避免阻塞事件循环（R16）
- ``--parent-pid`` 传递：Python 崩溃时 sidecar 自走 graceful stop 兜底
- stderr 重定向到 ``sidecar.stderr.log`` 二次归档（panic hook 输出在此）
- 独立 FileHandler 写 ``embedded-pg-service.log``，与应用主日志隔离便于问题定位
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shlex
import subprocess
import sys
import threading
from collections import deque
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING

from data.persistence.embedded_postgres.protocol import ConnectionInfo
from data.persistence.embedded_postgres.version import resolve_pg_major_version
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.sanitizers import DataSanitizer
from utils.singleton_registry import register_singleton

if TYPE_CHECKING:
    from utils.config_models import AppConfig

logger = logging.getLogger("qtrading.embedded_postgres")


class EmbeddedPostgresStartError(RuntimeError):
    """sidecar 启动失败（binary 缺失/ready 超时/JSON 无效/password_file 缺失等）。"""


_READY_SCHEMA = "qtrading.embedded_postgres.run.ready.v1"
_EVENT_WARNING_SCHEMA = "qtrading.embedded_postgres.event.warning.v1"
_EVENT_EXIT_SCHEMA = "qtrading.embedded_postgres.event.exit.v1"

# D16-S01：运行期诊断通道容量上限。事件只保留最近 N 条供消费，超出后丢弃最旧
# 事件但继续读 stdout（reader 永不因消费端缺失而阻塞 sidecar）。
_EVENT_BUFFER_MAX = 64
_EVENT_TEXT_MAX = 256
_RAW_LOG_LINE_MAX = 8192
_UNKNOWN_SCHEMA_DIAG_MAX = 8

# D16-S01：Python 侧三份诊断日志（stdout/stderr/service）的容量与轮转策略。
# 与 Rust sidecar.log（10MB×5）保持同量级但独立实现，避免重复造 Rust 轮转。
_PY_LOG_MAX_BYTES = 10 * 1024 * 1024
_PY_LOG_BACKUP_COUNT = 3

# MD13-01（检视 07）：启动失败清理时 stdin EOF graceful 窗口（秒）。
# sidecar supervise 契约为 stdin EOF → graceful stop（连带优雅停止 postgres，§7.3），
# 窗口须覆盖 PG checkpoint + 退出；超时才 kill 兜底。
_CLEANUP_GRACEFUL_TIMEOUT_S = 5.0

# F5（检视 06）：Windows 内侧车以 DPAPI 加密 password 文件。该 ASCII 前缀标记加密格式。
# 无前缀的文件按明文读（Unix，或 Windows 升级前的旧文件）。
_EMBEDDED_PW_DPAPI_PREFIX = "PGPW2:"

# UIR-F15-005：``<binary>.sha256`` 旁文件内容为 sha256sum 标准格式 ``<hex>  <filename>``，
# hex 段按 64 位小写十六进制校验。
_SHA256_HEX_CHARS = frozenset("0123456789abcdef")


def _is_sha256_hex(value: str) -> bool:
    """判断字符串是否为完整 sha256 十六进制摘要（64 位小写 hex）。"""
    return len(value) == 64 and all(ch in _SHA256_HEX_CHARS for ch in value)


@dataclass(frozen=True, slots=True)
class EmbeddedPgEvent:
    """sidecar stdout 中已解码的 JSON-lines 事件（D16-S01）。

    只承载已识别的 ``event.warning.v1`` / ``event.exit.v1``，字段长度有界且
    ``message`` 已经 :class:`DataSanitizer` 脱敏。该对象是线程安全的只读快照，
    供调用方经 ``drain_events()`` / ``recent_events()`` 消费；**不代表 UI 已告警**。
    """

    schema: str
    event: str
    message: str
    code: str | None = None
    phase: str | None = None


class _RotatingTextSink:
    """按行追加、超限轮转的文本 sink（D16-S01）。

    用于 ``sidecar.stdout.log`` / ``sidecar.stderr.log``：与 Rust sidecar.log 的
    轮转互不依赖。写/关闭经同一把锁串行，``stop_sync`` 与 reader 线程并发关闭时
    ``write_line`` 返回 ``False``（正常停机语义），不抛异常刷屏。
    """

    def __init__(
        self, path: Path, *, max_bytes: int = _PY_LOG_MAX_BYTES, backup_count: int = _PY_LOG_BACKUP_COUNT
    ) -> None:
        self._path = path
        self._max_bytes = max(1, int(max_bytes))
        self._backup_count = max(1, int(backup_count))
        self._lock = threading.Lock()
        self._closed = False
        self._rotation_disabled = False
        self._file = self._open()
        try:
            self._size = path.stat().st_size
        except OSError:
            self._size = 0

    @property
    def closed(self) -> bool:
        return self._closed

    def _open(self):
        return open(self._path, "a", encoding="utf-8", errors="replace")

    def _rotated_path(self, index: int) -> Path:
        return self._path.with_name(f"{self._path.name}.{index}")

    def _rotate_locked(self) -> None:
        """关闭当前文件 → 顺移 .1..N → 重开；失败时禁用轮转并继续追加。"""
        try:
            self._file.close()
        except OSError:
            pass
        try:
            for index in range(self._backup_count, 0, -1):
                src = self._path if index == 1 else self._rotated_path(index - 1)
                dst = self._rotated_path(index)
                if index == self._backup_count and dst.exists():
                    dst.unlink()
                if src.exists():
                    os.replace(src, dst)
            self._file = self._open()
            self._size = 0
        except OSError as exc:
            # 轮转失败不能中断诊断：禁用后续轮转，重新打开原文件继续追加。
            self._rotation_disabled = True
            logger.warning("embedded PG log rotation failed for %s: %s", self._path, DataSanitizer.sanitize_error(exc))
            try:
                self._file = self._open()
            except OSError:
                self._closed = True
                raise
            try:
                self._size = self._path.stat().st_size
            except OSError:
                self._size = 0

    def write_line(self, text: str) -> bool:
        """追加一行；已关闭返回 False，OSError 由调用方降级到替代诊断通道。"""
        with self._lock:
            if self._closed:
                return False
            line_bytes = len(text.encode("utf-8"))
            if not self._rotation_disabled and self._size > 0 and self._size + line_bytes > self._max_bytes:
                self._rotate_locked()
            self._file.write(text)
            self._file.flush()
            self._size += line_bytes
            return True

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._file.close()
            except OSError:
                pass


def _open_rotating_sink(path: Path, *, label: str, svc_logger: logging.Logger) -> _RotatingTextSink | None:
    """打开轮转日志 sink；失败时返回 None 并落一条可见告警（不阻断启动/reader）。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return _RotatingTextSink(path)
    except OSError as exc:
        svc_logger.warning(
            "%s log unavailable (%s); diagnostics fall back to embedded-pg-service.log",
            label,
            DataSanitizer.sanitize_error(exc),
        )
        return None


def _unprotect_win32_blob(blob: bytes) -> bytes:  # pragma: no cover -- win-only
    """F5（检视 06）：Windows DPAPI 解密（CryptUnprotectData，绑定当前用户+机器）。

    ``ctypes.windll`` 仅 Windows 可用，Linux CI 无法执行真实体，故整函数经
    ``# pragma: no cover`` 排除；对应分支由测试 mock 该函数覆盖。
    与 sidecar（Rust, CryptProtectData + CRYPTPROTECT_UI_FORBIDDEN, 无 entropy）互通。
    输出 blob 由系统 LocalAlloc 分配，须经 kernel32.LocalFree 释放。
    """
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    in_blob = DATA_BLOB()
    in_blob.cbData = wintypes.DWORD(len(blob))
    in_blob.pbData = ctypes.cast(ctypes.create_string_buffer(blob), ctypes.POINTER(ctypes.c_char))
    out_blob = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32  # type: ignore[reportAttributeAccessIssue]  # Windows-only：ctypes.windll 在 Linux 平台不存在
    ok = crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob))
    if not ok:
        raise OSError(ctypes.get_last_error() or "CryptUnprotectData failed")  # type: ignore[reportAttributeAccessIssue]  # Windows-only：ctypes.get_last_error 在 Linux 平台不存在
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)  # type: ignore[reportAttributeAccessIssue]  # Windows-only：ctypes.windll 在 Linux 平台不存在


def _read_embedded_password(password_file: Path, svc_logger: logging.Logger) -> str:
    """F5（检视 06）：读取内置 PG 密码文件。

    - ``PGPW2:`` 前缀：Windows DPAPI 加密，hex(blob) → 解密；
    - 无前缀：明文（Unix，或 Windows 升级前的旧格式）。
    """
    raw = password_file.read_text(encoding="utf-8").strip()
    if not raw.startswith(_EMBEDDED_PW_DPAPI_PREFIX):
        return raw
    if not sys.platform.startswith("win"):
        raise EmbeddedPostgresStartError("DPAPI-encrypted embedded PG password file on a non-Windows platform")
    try:
        blob = bytes.fromhex(raw[len(_EMBEDDED_PW_DPAPI_PREFIX) :])
        return _unprotect_win32_blob(blob).decode("utf-8").strip()
    except (ValueError, UnicodeDecodeError) as exc:
        svc_logger.error("embedded PG password DPAPI payload invalid: %s", DataSanitizer.sanitize_error(exc))
        raise EmbeddedPostgresStartError(f"embedded PG password DPAPI payload invalid: {exc}") from exc
    except OSError as exc:
        svc_logger.error("embedded PG password DPAPI decrypt failed: %s", DataSanitizer.sanitize_error(exc))
        raise EmbeddedPostgresStartError(f"embedded PG password DPAPI decrypt failed: {exc}") from exc


def _setup_service_logger(log_dir: Path) -> logging.Logger:
    """为 EmbeddedPostgresService 配置独立轮转 FileHandler，写入 embedded-pg-service.log。

    独立 logger 避免应用主日志被 sidecar 噪声淹没，便于问题定位（§3.7）。
    D16-S01：service 日志同样受容量/轮转约束，避免长期运行无限增长。
    重复构造时不重复挂 handler；日志文件不可写时降级为无文件 handler（保留可见告警）。
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    svc_logger = logging.getLogger("qtrading.embedded_postgres")
    svc_logger.setLevel(logging.DEBUG)
    if not any(getattr(h, "_embedded_pg_handler", False) for h in svc_logger.handlers):
        try:
            handler = RotatingFileHandler(
                log_dir / "embedded-pg-service.log",
                maxBytes=_PY_LOG_MAX_BYTES,
                backupCount=_PY_LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("embedded-pg-service.log unavailable: %s", DataSanitizer.sanitize_error(exc))
            return svc_logger
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s"))
        handler._embedded_pg_handler = True  # type: ignore[attr-defined]
        svc_logger.addHandler(handler)
    return svc_logger


@register_singleton
class EmbeddedPostgresService:
    """Embedded PostgreSQL sidecar 适配单例（Phase 2 §3.1）。

    使用双检锁 + ``_initialized`` 守卫保证单例；``from_config(AppConfig)`` 从配置解析路径。
    测试用 ``__init__`` 注入 fake path；生产用 ``from_config``。

    并发契约（review03-C18 修订）：start 与 stop 的状态变迁统一由 ``cls._lock``（RLock）
    串行化——``_start_sync`` 与 ``stop_sync`` 持同一把锁，start/stop 并发时互斥，
    不会返回已被 stop 的进程引用。应用层仅在启动期调用 ``start``、关闭期调用 ``stop``。
    """

    _instance: EmbeddedPostgresService | None = None
    _lock = threading.RLock()

    def __new__(cls, *args: object, **kwargs: object) -> EmbeddedPostgresService:
        with cls._lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance._initialized = False  # type: ignore[attr-defined]
                cls._instance = instance
            return cls._instance

    def __init__(
        self,
        *,
        sidecar_binary: Path,
        data_dir: Path,
        install_dir: Path,
        log_dir: Path | None = None,
        start_timeout: float = 300.0,
        stop_timeout: float = 60.0,
        listen: str = "127.0.0.1",
        username: str = "postgres",
        database: str = "qtrading",
    ) -> None:
        with self._lock:
            if self._initialized:
                return
            if not sidecar_binary:
                raise ValueError("sidecar_binary is required")
            self._sidecar_binary = Path(sidecar_binary)
            self._data_dir = Path(data_dir)
            self._install_dir = Path(install_dir)
            # log_dir 默认从 data_dir 推导：data_dir = <root>/postgres/<pg_major>/data
            # root = data_dir.parent.parent.parent（对应 paths.rs Layout::from_data_dir）
            if log_dir is None:
                root = self._data_dir.parent.parent.parent
                self._log_dir = root / "postgres-logs"
            else:
                self._log_dir = Path(log_dir)
            self._log_dir.mkdir(parents=True, exist_ok=True)
            self._start_timeout = float(start_timeout)
            self._stop_timeout = float(stop_timeout)
            self._listen = listen
            self._username = username
            self._database = database
            self._process: subprocess.Popen[str] | None = None
            self._connection_info: ConnectionInfo | None = None
            self._stderr_file: object | None = None
            # D16-S01：有界事件缓冲与未知 schema 诊断状态（reader 线程写、调用方读）。
            self._events: deque[EmbeddedPgEvent] = deque(maxlen=_EVENT_BUFFER_MAX)
            self._events_lock = threading.Lock()
            self._dropped_events = 0
            self._unknown_schemas: set[str] = set()
            # review03-C18: start 与 stop 的状态变迁统一由 cls._lock（RLock）串行化；
            # 不再使用独立的 _start_lock（原"_start_lock 与 cls._lock 不互锁"的并发缺口）。
            self._svc_logger = _setup_service_logger(self._log_dir)
            self._initialized = True

    @classmethod
    def from_config(cls, config: AppConfig) -> EmbeddedPostgresService:
        """从 AppConfig 解析路径并构造/复用单例。

        - sidecar_binary：显式路径优先；否则按运行模式解析
          - PyInstaller frozen 模式（``sys.frozen``）：``sys._MEIPASS / sidecars / qtrading-pg-sidecar[.exe]``
            （pg_plan §16.2，installer embedded variant 安装到 ``<app>/_internal/sidecars/``）
          - 开发模式：``Path("sidecars") / qtrading-pg-sidecar[.exe]``（cwd-relative）
        - data_dir：embedded_pg_data_root 为空时用 platformdirs 默认 <app data>/postgres/
          <pg_major>/data，其中 <pg_major> 由 ``resolve_pg_major_version(sidecar_binary)``
          动态解析（以 sidecar 二进制为权威来源，见 version.py）
        - install_dir：默认 <data_root>/install
        - log_dir：默认 <app data>/postgres-logs
        """
        if config.embedded_pg_sidecar_path:
            sidecar_binary = Path(config.embedded_pg_sidecar_path)
        elif getattr(sys, "frozen", False):
            # PyInstaller onedir: sys._MEIPASS 指向 _internal/，sidecar 由 AStockScreener.spec 收集到 _internal/sidecars/
            exe_suffix = ".exe" if os.name == "nt" else ""
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass is None:
                raise EmbeddedPostgresStartError(
                    "sys.frozen is True but sys._MEIPASS is None; cannot resolve sidecar binary path"
                )
            sidecar_binary = Path(meipass) / "sidecars" / f"qtrading-pg-sidecar{exe_suffix}"
        else:
            exe_suffix = ".exe" if os.name == "nt" else ""
            sidecar_binary = Path("sidecars") / f"qtrading-pg-sidecar{exe_suffix}"

        if config.embedded_pg_data_root:
            data_root = Path(config.embedded_pg_data_root)
        else:
            import platformdirs

            app_data = Path(platformdirs.user_data_dir("qTrading"))
            data_root = app_data / "postgres" / resolve_pg_major_version(sidecar_binary)
        data_dir = data_root / "data"

        if config.embedded_pg_install_root:
            install_dir = Path(config.embedded_pg_install_root)
        else:
            install_dir = data_root / "install"

        log_dir = Path(config.embedded_pg_log_dir) if config.embedded_pg_log_dir else None

        return cls(
            sidecar_binary=sidecar_binary,
            data_dir=data_dir,
            install_dir=install_dir,
            log_dir=log_dir,
            start_timeout=config.embedded_pg_start_timeout_s,
            stop_timeout=config.embedded_pg_stop_timeout_s,
            listen=config.embedded_pg_listen,
            username=config.embedded_pg_username,
            database=config.embedded_pg_database,
        )

    @log_async_operation(
        operation_name="embedded_pg_start",
        threshold_ms=PerfThreshold.GLOBAL_INIT,
    )
    async def start(self) -> ConnectionInfo:
        """拉起 sidecar 子进程并解析 ready JSON。

        使用 ``asyncio.to_thread`` 包装同步 Popen + readline，避免阻塞事件循环（R16）。
        失败时保证清理子进程（kill + wait）。CancelledError 路径触发 stop 兜底。

        H7: 挂 ``@log_async_operation`` 装饰器记录耗时与异常，threshold=GLOBAL_INIT（15s）
        匹配 sidecar 首次启动 initdb 可能较慢的场景；超过阈值记 WARNING 便于诊断。

        并发契约：不可与 ``stop`` 并发调用（详见类 docstring）。
        """
        import asyncio

        try:
            return await asyncio.to_thread(self._start_sync)
        except BaseException:
            # CancelledError 或其他异常：确保子进程不泄漏
            self._cleanup_failed_start()
            raise

    def _start_sync(self) -> ConnectionInfo:
        """同步启动 sidecar（供 asyncio.to_thread 包装）。

        review03-C18: 持 ``cls._lock``（RLock）串行化并发调用，避免双 Popen。
        快速路径无锁检查已启动状态，避免已启动场景的锁竞争；获取锁后双检锁再次检查。

        并发契约：与 ``stop_sync`` 共享 ``cls._lock``，start/stop 互斥（详见类 docstring）。
        """
        # 快速路径：已启动直接返回（无锁，避免已启动场景的锁竞争）
        # M09-F1: 先取局部引用再 poll。原写法对 self._process 做两次独立属性读取，
        # 与并发 stop 交错时第二次读取可能得到 None → None.poll() AttributeError。
        proc = self._process
        info = self._connection_info
        if info is not None and proc is not None and proc.poll() is None:
            return info
        # review03-C18: 与 stop_sync 共用 cls._lock（RLock）串行化状态变迁
        with self.__class__._lock:
            # 双检锁：获取锁后再次检查（另一线程可能已启动完成）；同样用局部引用防 TOCTOU
            proc = self._process
            info = self._connection_info
            if info is not None and proc is not None and proc.poll() is None:
                return info
            return self._start_sync_impl()

    def _start_sync_impl(self) -> ConnectionInfo:
        """实际启动逻辑（由 _start_sync 在 cls._lock 内调用）。"""
        # M09-F2: 重启路径清理（sidecar 崩溃后 poll 非 None 再次 start）。
        # 此处 _process 必为已退出进程（活进程在双检锁处已被快速路径拦截），
        # kill/wait 为 no-op 或被捕获；关键是关闭上一次残留的 _stderr_file 句柄，
        # 否则下方重新 open 会覆盖引用导致旧句柄泄漏。
        self._cleanup_failed_start()
        # §17.6 #7: Popen 前校验 sidecar binary SHA256，防止版本混搭/损坏
        self._verify_sidecar_sha256()

        runtime_dir = self._data_dir.parent / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        password_file = runtime_dir / "password"
        sidecar_log = self._log_dir / "sidecar.log"

        cmd = [
            str(self._sidecar_binary),
            "run",
            "--data-dir",
            str(self._data_dir),
            "--install-dir",
            str(self._install_dir),
            "--password-file",
            str(password_file),
            "--database",
            self._database,
            "--username",
            self._username,
            "--listen",
            self._listen,
            "--log-file",
            str(sidecar_log),
            "--parent-pid",
            str(os.getpid()),
        ]

        self._svc_logger.info("starting sidecar: %s", self._sidecar_binary)
        # D16-S01：stderr 归档改为容量/轮转 sink；打开失败时降级到 service 日志，
        # 仍继续读取管道（避免 sidecar 因 stderr 管道无人消费而阻塞）。
        self._stderr_file = _open_rotating_sink(
            self._log_dir / "sidecar.stderr.log",
            label="sidecar.stderr.log",
            svc_logger=self._svc_logger,
        )

        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                # D23: stderr 经 reader thread 行级脱敏后写入 _stderr_file，
                # 避免 sidecar panic 输出中含密码/连接串被直接落盘
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except FileNotFoundError as exc:
            self._svc_logger.error("sidecar binary not found: %s", self._sidecar_binary)
            self._cleanup_failed_start()
            raise EmbeddedPostgresStartError(f"sidecar binary not found: {self._sidecar_binary}") from exc
        except PermissionError as exc:
            self._svc_logger.error("sidecar binary not executable: %s", self._sidecar_binary)
            self._cleanup_failed_start()
            raise EmbeddedPostgresStartError(f"sidecar binary not executable: {self._sidecar_binary}") from exc

        # D23: 立即启动 stderr reader，确保 sidecar 启动失败（exit=13 等）时
        # stderr 诊断信息也能被捕获到 sidecar.stderr.log（之前在 ready 后才启动，
        # 失败路径下 sidecar.stderr.log 为空，无法定位根因）
        self._start_stderr_reader_thread()

        ready_line = self._readline_with_timeout(self._process.stdout, self._start_timeout)
        if not ready_line:
            exit_code = self._process.poll()
            self._svc_logger.error("sidecar exited before ready line (exit=%s)", exit_code)
            self._cleanup_failed_start()
            raise EmbeddedPostgresStartError(self._format_sidecar_exit_error(exit_code))

        try:
            ready = json.loads(ready_line)
        except json.JSONDecodeError as exc:
            # M09-F5: ready 行可能含 sidecar 输出的 url/password 等敏感片段，日志与异常消息
            # 均经 sanitize_error 脱敏后再落盘（R9 纵深防御；密码未注册时模式匹配兜底）
            sanitized_line = DataSanitizer.sanitize_error(ready_line)
            self._svc_logger.error(
                "ready JSON parse failed: %s; line=%r", DataSanitizer.sanitize_error(exc), sanitized_line
            )
            self._cleanup_failed_start()
            raise EmbeddedPostgresStartError(f"ready JSON parse failed: {exc}; line={sanitized_line!r}") from exc

        if ready.get("schema") != _READY_SCHEMA:
            self._svc_logger.error("unexpected ready schema: %s", ready.get("schema"))
            self._cleanup_failed_start()
            # §17.6 #33: schema 主版本号不匹配提示重新安装
            raise EmbeddedPostgresStartError(
                f"unexpected ready schema: {ready.get('schema')}; "
                f"expected {_READY_SCHEMA}; please reinstall qTrading to fix version mismatch"
            )
        if ready.get("status") != "running":
            self._svc_logger.error("unexpected ready status: %s", ready.get("status"))
            self._cleanup_failed_start()
            raise EmbeddedPostgresStartError(f"unexpected ready status: {ready.get('status')}")
        try:
            port = int(ready.get("port", 0))
            pid = int(ready.get("pid") or 0)
        except (TypeError, ValueError) as exc:
            # M09-F4: 核心字段 port/pid 非数值时转分类异常（原裸 ValueError 穿透）
            self._svc_logger.error("invalid numeric field in ready JSON: %s", DataSanitizer.sanitize_error(exc))
            self._cleanup_failed_start()
            raise EmbeddedPostgresStartError(f"invalid numeric field in ready JSON: {exc}") from exc
        if port <= 0:
            self._svc_logger.error("invalid port in ready JSON: %s", port)
            self._cleanup_failed_start()
            raise EmbeddedPostgresStartError(f"invalid port in ready JSON: {port}")
        data_dir_str = str(ready.get("data_dir", str(self._data_dir)))

        try:
            # F5（检视 06）：Windows 下自动解密 DPAPI 加密的 password 文件；否则明文
            password = _read_embedded_password(password_file, self._svc_logger)
        except FileNotFoundError as exc:
            self._svc_logger.error("password_file not found: %s", password_file)
            self._cleanup_failed_start()
            raise EmbeddedPostgresStartError(f"password_file not found: {password_file}") from exc
        except OSError as exc:
            self._svc_logger.error("password_file read failed: %s", DataSanitizer.sanitize_error(exc))
            self._cleanup_failed_start()
            raise EmbeddedPostgresStartError(f"password_file read failed: {exc}") from exc

        url = f"postgresql+asyncpg://{self._username}:{password}@{self._listen}:{port}/{self._database}"
        # R9: 注册完整 URL 为 secret，确保日志/异常中带密码的 URL 被 sanitize_error 精确脱敏
        DataSanitizer.register_secret(url)
        # R9: 单独注册裸密码为 secret，防止 sidecar stderr 中裸密码（非完整 URL）未被脱敏（spec.md §1.5）
        DataSanitizer.register_secret(password)

        # M4: 扩展字段从 ready JSON 解析（缺失字段使用 dataclass 默认值）
        postgres_version = str(ready.get("postgres_version", ""))
        host = str(ready.get("host", "")) or self._listen
        # M09-F4: sidecar_pid 为扩展字段，畸形时不杀健康进程，降级为默认 0
        try:
            sidecar_pid = int(ready.get("sidecar_pid", 0) or 0)
        except (TypeError, ValueError):
            self._svc_logger.warning(
                "non-numeric sidecar_pid in ready JSON, fallback to 0: %r", ready.get("sidecar_pid")
            )
            sidecar_pid = 0
        password_source = str(ready.get("password_source", "")) or "password_file"

        self._connection_info = ConnectionInfo(
            url=url,
            port=port,
            pid=pid,
            data_dir=data_dir_str,
            postgres_version=postgres_version,
            host=host,
            sidecar_pid=sidecar_pid,
            password_source=password_source,
        )
        # 日志中 URL 脱敏（R9）：仅记 host:port
        self._svc_logger.info("embedded postgres started on %s:%s (pid=%s)", self._listen, port, pid)
        # M9: 启动 daemon 线程持续读 sidecar stdout 写入日志文件，避免 stdout 缓冲区满
        # 阻塞 sidecar（虽然当前 sidecar ready 后不再写 stdout，但防御未来版本变化）
        # 注意：stderr reader 已在 Popen 后立即启动（见上方），此处不再重复
        self._start_stdout_reader_thread()
        return self._connection_info

    def _start_stdout_reader_thread(self) -> None:
        """D16-S01: 持续读 sidecar stdout → 有界事件 + 容量轮转归档。

        - 线程为 daemon，随进程退出自动终止
        - readline 阻塞直到 EOF（stop_sync 关闭 stdin → sidecar 退出 → stdout 关闭）
        - 已识别的 ``event.warning.v1`` / ``event.exit.v1`` 解码为 ``EmbeddedPgEvent``，
          进入有界缓冲供 ``drain_events()`` / ``recent_events()`` 消费；
          事件顺序与 stdout 行序一致
        - 每行仍脱敏后写入 ``<log_dir>/sidecar.stdout.log``（保留原文诊断）；
          未知 schema / 非 JSON 行只做有界诊断，不进入事件缓冲
        - 文件不可写时不中断读取（避免管道背压阻塞 sidecar），降级写 service 日志
        - 线程异常不影响主流程，仅记 WARNING 便于发现诊断通道失效
        """
        if self._process is None or self._process.stdout is None:
            return

        stdout = self._process.stdout
        self._reset_event_diagnostics()
        svc_logger = self._svc_logger

        def _reader() -> None:
            sink = _open_rotating_sink(
                self._log_dir / "sidecar.stdout.log", label="sidecar.stdout.log", svc_logger=svc_logger
            )
            try:
                while True:
                    line = stdout.readline()
                    if not line:
                        break  # EOF
                    full_line = line.rstrip("\r\n")
                    raw = full_line[:_RAW_LOG_LINE_MAX]
                    sanitized = DataSanitizer.sanitize_error(raw)
                    if sink is not None:
                        try:
                            if not sink.write_line(sanitized + "\n"):
                                # 归档已关闭（如停机）：继续消费管道防背压，转 service 日志。
                                sink.close()
                                sink = None
                        except OSError as exc:
                            sink.close()
                            sink = None
                            svc_logger.warning(
                                "sidecar.stdout.log write failed (%s); falling back to service log",
                                DataSanitizer.sanitize_error(exc),
                            )
                    if sink is None:
                        # 替代诊断：文件不可写时仍把脱敏行写入独立 service 日志。
                        svc_logger.info("sidecar stdout: %s", sanitized)
                    # 事件解码使用完整行：归档截断不得导致合法事件被误判为不可解析。
                    self._handle_stdout_diagnostics(full_line, sanitized)
            except Exception as e:  # pragma: no cover  reader thread stdout fallback
                logger.warning("stdout reader thread fallback: %s", DataSanitizer.sanitize_error(e), exc_info=True)
            finally:
                if sink is not None:
                    sink.close()

        t = threading.Thread(
            target=_reader,
            name="embedded-pg-stdout-reader",
            daemon=True,
        )
        t.start()

    def _start_stderr_reader_thread(self) -> None:
        """D23/D16-S01: daemon 线程逐行读 sidecar stderr → 脱敏 → 容量轮转归档。

        - 线程为 daemon，随进程退出自动终止
        - readline 阻塞直到 EOF（stop_sync 关闭 stdin → sidecar 退出 → stderr 关闭）
        - 每行经 ``DataSanitizer.sanitize_error`` 脱敏后写入 ``_stderr_file``，
          避免 sidecar panic 输出中含密码/连接串被直接落盘（R9）
        - 归档不可写时降级写 service 日志并继续读取，避免 stderr 管道背压
        """
        if self._process is None or self._process.stderr is None:
            return

        stderr = self._process.stderr
        stderr_sink = self._stderr_file if isinstance(self._stderr_file, _RotatingTextSink) else None
        svc_logger = self._svc_logger

        def _reader() -> None:
            nonlocal stderr_sink
            try:
                while True:
                    line = stderr.readline()
                    if not line:
                        break  # EOF
                    raw = line.rstrip("\r\n")[:_RAW_LOG_LINE_MAX]
                    sanitized = DataSanitizer.sanitize_error(raw)
                    # stderr 只归档，不解析；超长行按 _RAW_LOG_LINE_MAX 截断。
                    if stderr_sink is not None:
                        try:
                            if not stderr_sink.write_line(sanitized + "\n"):
                                # stop 关闭归档后继续消费 stderr 管道，避免 kill 兜底前背压。
                                stderr_sink = None
                        except OSError as exc:
                            stderr_sink.close()
                            stderr_sink = None
                            svc_logger.warning(
                                "sidecar.stderr.log write failed (%s); falling back to service log",
                                DataSanitizer.sanitize_error(exc),
                            )
                    if stderr_sink is None:
                        svc_logger.warning("sidecar stderr: %s", sanitized)
            except Exception as e:  # pragma: no cover  reader thread stderr fallback
                logger.warning("stderr reader thread fallback: %s", DataSanitizer.sanitize_error(e), exc_info=True)

        t = threading.Thread(
            target=_reader,
            name="embedded-pg-stderr-reader",
            daemon=True,
        )
        t.start()

    def _reset_event_diagnostics(self) -> None:
        """清空上一轮运行残留的有界事件与未知 schema 诊断状态。"""
        with self._events_lock:
            self._events.clear()
            self._dropped_events = 0
            self._unknown_schemas.clear()

    def _record_event(self, event: EmbeddedPgEvent) -> None:
        """记录已解码事件并输出可见告警；缓冲满时丢弃最旧事件并计数。"""
        buffer_full = False
        with self._events_lock:
            if len(self._events) == _EVENT_BUFFER_MAX:
                self._dropped_events += 1
                buffer_full = self._dropped_events == 1
            self._events.append(event)
        if buffer_full:
            self._svc_logger.warning(
                "sidecar event buffer full (%d); dropping oldest events",
                _EVENT_BUFFER_MAX,
            )
        if event.event == "exit":
            # code=0 且 parent_gone/signal 属正常关停，避免把正常退出记为 ERROR。
            normal = event.code == "0" and event.message in ("parent_gone", "signal")
            self._svc_logger.log(
                logging.INFO if normal else logging.ERROR,
                "sidecar exit event: code=%s reason=%s",
                event.code,
                event.message,
            )
        else:
            self._svc_logger.warning("sidecar warning event: code=%s message=%s", event.code, event.message)

    def _decode_event_line(self, raw: str) -> EmbeddedPgEvent | None:
        """把 stdout JSON-lines 解码为有界事件；未知 schema/非 JSON 返回 None。

        ``event.exit.v1`` 是退出原因的关键通道：即使 schema 未知，只要行内
        ``event`` 字段为 ``exit`` 也解码，避免静默丢关键退出原因。
        """
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None

        schema_value = payload.get("schema")
        raw_schema = schema_value if isinstance(schema_value, str) else ""
        event_value = payload.get("event")
        event_name = event_value if isinstance(event_value, str) else ""
        if raw_schema == _EVENT_WARNING_SCHEMA:
            event_name = "warning"
        elif raw_schema == _EVENT_EXIT_SCHEMA:
            event_name = "exit"
        elif event_name != "exit":
            return None
        # R9：schema 也来自 sidecar 输出，落库/入事件前同样脱敏并限长。
        schema = DataSanitizer.sanitize_error(raw_schema)[:64] if raw_schema else ""

        code_value = payload.get("code")
        code = None if code_value is None else DataSanitizer.sanitize_error(str(code_value))[:64]
        message_value = payload.get("message")
        message = DataSanitizer.sanitize_error(message_value if isinstance(message_value, str) else "")[
            :_EVENT_TEXT_MAX
        ]
        phase_value = payload.get("phase")
        phase = DataSanitizer.sanitize_error(phase_value)[:64] if isinstance(phase_value, str) else None
        return EmbeddedPgEvent(schema=schema, event=event_name, message=message, code=code, phase=phase)

    def _handle_stdout_diagnostics(self, raw: str, sanitized: str) -> None:
        """解码事件；未知 JSON schema/不可解析行保留原文归档并做有界可见诊断。"""
        event = self._decode_event_line(raw)
        if event is not None:
            self._record_event(event)
            return
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            payload = None
        if not isinstance(payload, dict):
            if "exit" in raw.lower():
                # 不可解析但含 exit 标记：关键退出原因不能静默，落可见告警并已归档原文。
                self._svc_logger.warning(
                    "unparseable sidecar stdout line containing exit marker archived: %s",
                    sanitized[:512],
                )
            return

        schema_value = payload.get("schema")
        schema = schema_value if isinstance(schema_value, str) else "<missing>"
        with self._events_lock:
            first_for_schema = (
                schema not in self._unknown_schemas and len(self._unknown_schemas) < _UNKNOWN_SCHEMA_DIAG_MAX
            )
            if first_for_schema:
                self._unknown_schemas.add(schema)
        if first_for_schema:
            # R9：未知 schema 同样可能是 sidecar 输出中的敏感片段，日志前脱敏并限长。
            self._svc_logger.warning(
                "unknown sidecar stdout schema %r archived to sidecar.stdout.log: %s",
                DataSanitizer.sanitize_error(schema)[:64],
                sanitized[:512],
            )

    def recent_events(self, limit: int | None = None) -> tuple[EmbeddedPgEvent, ...]:
        """返回最近事件快照（最多 ``_EVENT_BUFFER_MAX`` 条，按 stdout 行序）。"""
        with self._events_lock:
            events = list(self._events)
        if limit is not None:
            events = events[-max(0, limit) :] if limit > 0 else []
        return tuple(events)

    def drain_events(self) -> list[EmbeddedPgEvent]:
        """取出并清空当前有界事件缓冲（供后续消费方接入，不表示 UI 已告警）。"""
        with self._events_lock:
            events = list(self._events)
            self._events.clear()
        return events

    def _verify_sidecar_sha256(self) -> None:
        """§17.6 #7: Popen 前校验 sidecar binary SHA256，防止版本混搭/损坏。

        读取 ``<sidecar_binary>.sha256`` 文件（每行格式：``<hex>  <filename>``），
        与 sidecar_binary 实际 SHA256 比对。不匹配则抛 EmbeddedPostgresStartError
        提示重新安装。

        UIR-F15-005：校验策略按运行模式显式区分——
        - **打包（frozen）布局**：旁文件由 ``AStockScreener.spec`` 随 binary 同目录交付
          （``_internal/sidecars/<exe>.sha256``），构建时按实际打包字节计算。旁文件
          缺失/不可读/为空/格式非法一律视为发行物损坏，明确失败且不启动 sidecar；
        - **源码开发模式**：未 cargo build 或未注入 ``SIDECAR_SHA256`` 时无旁文件属正常，
          缺失/不可读时跳过校验（宽容），但文件存在且内容不合法仍按损坏失败。
        """
        import hashlib

        packaged = bool(getattr(sys, "frozen", False))
        sha256_path = self._sidecar_binary.with_suffix(self._sidecar_binary.suffix + ".sha256")
        if not sha256_path.exists():
            if packaged:
                self._svc_logger.error("sha256 manifest missing in packaged build: %s", sha256_path)
                raise EmbeddedPostgresStartError(
                    f"sidecar sha256 manifest missing: {sha256_path}; "
                    f"please reinstall qTrading to restore the bundled sidecar checksum"
                )
            self._svc_logger.debug("sha256 file missing, skip verification: %s", sha256_path)
            return

        try:
            expected_line = sha256_path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            if packaged:
                self._svc_logger.error(
                    "sha256 manifest unreadable in packaged build: %s", DataSanitizer.sanitize_error(exc)
                )
                raise EmbeddedPostgresStartError(
                    f"sidecar sha256 manifest unreadable: {DataSanitizer.sanitize_error(exc)}; "
                    f"please reinstall qTrading"
                ) from exc
            self._svc_logger.warning(
                "sha256 file read failed, skip verification: %s", DataSanitizer.sanitize_error(exc)
            )
            return

        # 标准格式："<hex>  <filename>"，取首个空白前部分
        parts = expected_line.split()
        expected = parts[0].lower() if parts else ""
        if not _is_sha256_hex(expected):
            if packaged:
                self._svc_logger.error("sha256 manifest invalid in packaged build: %s", sha256_path)
                raise EmbeddedPostgresStartError(
                    f"sidecar sha256 manifest invalid (expected 64 hex chars at {sha256_path}); "
                    f"please reinstall qTrading"
                )
            if not expected:
                self._svc_logger.warning("sha256 file empty, skip verification: %s", sha256_path)
                return
            self._svc_logger.error("sidecar sha256 invalid: %r; please reinstall qTrading", expected[:16])
            raise EmbeddedPostgresStartError(
                f"sidecar sha256 invalid (expected 64 hex chars, got {expected[:8]!r}); please reinstall qTrading"
            )

        try:
            actual = hashlib.sha256(self._sidecar_binary.read_bytes()).hexdigest().lower()
        except OSError as exc:
            self._svc_logger.error("sidecar binary read failed for sha256: %s", DataSanitizer.sanitize_error(exc))
            raise EmbeddedPostgresStartError(f"sidecar binary read failed during sha256 verification: {exc}") from exc

        if actual != expected:
            self._svc_logger.error(
                "sidecar sha256 mismatch: expected=%s actual=%s; please reinstall qTrading",
                expected,
                actual,
            )
            raise EmbeddedPostgresStartError(
                f"sidecar binary sha256 mismatch (expected={expected[:8]}..., actual={actual[:8]}...); "
                f"please reinstall qTrading to fix version mismatch"
            )

    def _format_sidecar_exit_error(self, exit_code: int | None) -> str:
        """§17.6: 将 sidecar exit code 映射到用户可理解的错误信息。

        映射表与 ``sidecars/qtrading-pg-sidecar/src/exit_codes.rs`` / how-to §9.6 对齐：
        - 2 → argument_error
        - 10 → setup_failed
        - 11 → initdb_failed
        - 12 → start_failed
        - 13 → health_check_failed
        - 14 → create_database_failed
        - 15 → preflight_failed（磁盘空间/目录权限/文件系统/网盘同步路径/密码文件权限）
        - 16 → password_error
        - 20 → stop_failed（进程在分级停止后仍存活；PG 未运行时 stop 返回 0）
        - 30 → dump_restore_failed
        - 40 → data_dir_abnormal
        - 50 → already_running
        - 60 → sidecar_crashed
        """
        if exit_code is None:
            return "sidecar exited before ready line (exit=None; process still running but no output)"
        if exit_code == 2:
            return (
                f"argument error (exit=2); sidecar command line invalid. "
                f"Check the configured data directory/username and retry; data_dir={self._data_dir}"
            )
        if exit_code == 10:
            return (
                f"setup failed (exit=10); bundled PostgreSQL binaries are missing or incomplete. "
                f"Reinstall qTrading and retry; data_dir={self._data_dir}"
            )
        if exit_code == 11:
            return (
                f"initdb failed (exit=11); PostgreSQL data directory initialization failed. "
                f"Check disk space and permissions; data_dir={self._data_dir}"
            )
        if exit_code == 12:
            return (
                f"postgres start failed (exit=12); PostgreSQL instance could not start. "
                f"Check the sidecar log and retry; data_dir={self._data_dir}"
            )
        if exit_code == 13:
            return (
                f"health check failed (exit=13); PostgreSQL did not become healthy after start. "
                f"Check the sidecar log and retry; data_dir={self._data_dir}"
            )
        if exit_code == 14:
            return (
                f"create database failed (exit=14); the application database could not be created. "
                f"Check the sidecar log and retry; data_dir={self._data_dir}"
            )
        if exit_code == 15:
            # 15 是资源预检失败的通用码，原因可能是磁盘空间/目录权限/文件系统/网盘同步路径/
            # 密码文件权限；不得固化为「磁盘空间不足」，也不编造剩余空间数值。
            return (
                f"resource preflight failed (exit=15); disk space, directory permissions, filesystem "
                f"support, cloud-sync path, or password file permissions may be the cause. "
                f"Check the sidecar log for the sanitized reason and retry; data_dir={self._data_dir}"
            )
        if exit_code == 16:
            # M24：自定义超级用户名实例须按需追加 --username，否则用户照提示执行会失败；
            # 用户名/路径均经 shlex.quote 生成可复制的命令字符串（不拼接 shell 注入面）。
            reset_cmd = f"qtrading-pg-sidecar reset-password --data-dir {shlex.quote(str(self._data_dir))}"
            if self._username != "postgres":
                reset_cmd += f" --username {shlex.quote(self._username)}"
            return f"password error (exit=16); run '{reset_cmd}' to reset, then restart qTrading"
        if exit_code == 20:
            return (
                "stop failed (exit=20); PostgreSQL is still alive after graded stop. "
                f"Check the sidecar log and stop the existing instance; data_dir={self._data_dir}"
            )
        if exit_code == 30:
            return (
                "dump/restore failed (exit=30); backup or restore could not complete. "
                f"Check the backup file and sidecar log; data_dir={self._data_dir}"
            )
        if exit_code == 40:
            return (
                "data directory abnormal (exit=40); non-empty without PG_VERSION, version mismatch, "
                "or corrupt/missing critical files. Run doctor and follow the recovery guide; "
                f"data_dir={self._data_dir}"
            )
        if exit_code == 50:
            return (
                "qTrading already running (exit=50); another qTrading instance may be using "
                "the same data directory. Close other instances or use a different data_dir"
            )
        if exit_code == 60:
            return (
                f"sidecar crashed (exit=60); restart qTrading. "
                f"If persistent, check sidecar.log at {self._log_dir / 'sidecar.log'}"
            )
        return f"sidecar exited before ready line (exit={exit_code})"

    def _readline_with_timeout(self, stream: object, timeout: float) -> str:
        """带超时的 readline，避免阻塞事件循环。"""
        q: queue.Queue[str] = queue.Queue(maxsize=1)

        def _reader() -> None:
            try:
                line = stream.readline()  # type: ignore[union-attr]
                q.put(line if line is not None else "")
            except Exception as e:  # pragma: no cover  reader thread stream fallback (竞态难复现)
                logger.debug("reader thread stream fallback: %s", DataSanitizer.sanitize_error(e), exc_info=True)
                q.put("")

        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        try:
            return q.get(timeout=timeout)
        except queue.Empty:
            return ""

    def _cleanup_failed_start(self) -> None:
        """启动失败时清理子进程与文件句柄。

        MD13-01（检视 07）：进程存活时先关 stdin 触发 sidecar supervise 的
        stdin EOF graceful stop（连带优雅停止 postgres），短超时未退出再 kill 兜底
        ——kill 仅终止 sidecar 包装进程，postgres 会孤儿化占用 PGDATA，
        导致下次启动报 exit=50（already_running）。
        """
        if self._process is not None:
            proc = self._process
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except Exception as e:  # stdin close fallback on failed start
                    logger.debug(
                        "cleanup_failed_start stdin close fallback: %s", DataSanitizer.sanitize_error(e), exc_info=True
                    )
            self._terminate_after_graceful(proc)
            self._process = None
        self._connection_info = None
        if self._stderr_file is not None:
            try:
                self._stderr_file.close()  # type: ignore[attr-defined]
            except Exception as e:  # file close fallback on failed start
                logger.debug("cleanup_failed_start close fallback: %s", DataSanitizer.sanitize_error(e), exc_info=True)
            self._stderr_file = None

    def _terminate_after_graceful(self, proc: subprocess.Popen[str]) -> None:
        """等待 stdin EOF graceful 停止短超时后 kill 兜底，确保子进程终止。

        进程已退出时 ``wait`` 立即返回，不触发 kill（等价原清理行为）。
        """
        try:
            proc.wait(timeout=_CLEANUP_GRACEFUL_TIMEOUT_S)
            return
        except subprocess.TimeoutExpired:
            self._svc_logger.warning("sidecar cleanup graceful timeout after %ss, killing", _CLEANUP_GRACEFUL_TIMEOUT_S)
        except Exception as e:  # subprocess wait fallback on failed start
            logger.debug("cleanup_failed_start wait fallback: %s", DataSanitizer.sanitize_error(e), exc_info=True)
        try:
            proc.kill()
        except Exception as e:  # subprocess kill fallback on failed start
            logger.debug("cleanup_failed_start kill fallback: %s", DataSanitizer.sanitize_error(e), exc_info=True)
        try:
            proc.wait(timeout=5)
        except Exception as e:  # subprocess wait fallback on failed start
            logger.debug("cleanup_failed_start wait fallback: %s", DataSanitizer.sanitize_error(e), exc_info=True)

    @log_async_operation(
        operation_name="embedded_pg_stop",
        threshold_ms=PerfThreshold.GLOBAL_INIT,
    )
    async def stop(self) -> None:
        """幂等停止 sidecar：stdin.close → wait(timeout) → kill 兜底。

        清理 _process / _connection_info / _stderr_file。

        H7: 挂 ``@log_async_operation`` 记录 stop 耗时，便于发现 sidecar 关停卡死。

        并发契约：不可与 ``start`` 并发调用（详见类 docstring）。
        """
        import asyncio

        await asyncio.to_thread(self.stop_sync)

    def stop_sync(self) -> None:
        """同步停止 sidecar（供 asyncio.to_thread 包装，shutdown Step 8 使用）。

        M3: 持 ``cls._lock`` 保护，避免 _reset_singleton/_atexit_cleanup 与外部 stop()
        并发执行时双线程同时操作 ``self._process`` / ``self._stderr_file``。
        ``cls._lock`` 为 RLock，_reset_singleton/_atexit_cleanup 持锁调用同线程可重入。

        并发契约：不可与 ``_start_sync`` 并发调用（详见类 docstring）。
        """
        with type(self)._lock:
            if self._process is None:
                return
            proc = self._process
            try:
                if proc.stdin is not None:
                    try:
                        proc.stdin.close()
                    except Exception as e:  # stdin close fallback on stop
                        logger.debug(
                            "stop_sync stdin close fallback: %s", DataSanitizer.sanitize_error(e), exc_info=True
                        )
                try:
                    proc.wait(timeout=self._stop_timeout)
                    self._svc_logger.info("embedded postgres stopped gracefully")
                except subprocess.TimeoutExpired:
                    self._svc_logger.warning("sidecar stop timeout after %ss, killing", self._stop_timeout)
                    proc.kill()
                    try:
                        proc.wait(timeout=5)
                    except Exception as e:  # subprocess wait fallback after kill
                        logger.debug(
                            "stop_sync wait after kill fallback: %s", DataSanitizer.sanitize_error(e), exc_info=True
                        )
            finally:
                self._process = None
                self._connection_info = None
                if self._stderr_file is not None:
                    try:
                        self._stderr_file.close()  # type: ignore[attr-defined]
                    except Exception as e:  # file close fallback on stop
                        logger.debug(
                            "stop_sync stderr_file close fallback: %s", DataSanitizer.sanitize_error(e), exc_info=True
                        )
                    self._stderr_file = None

    def collect_logs_summary(self, tail_bytes: int = 8192) -> dict[str, str]:
        """收集四类日志尾部内容，供 doctor 命令/UI 诊断面板调用（§3.7）。

        - sidecar.log：Rust sidecar tracing 日志
        - sidecar.stderr.log：Rust sidecar stderr 二次归档（panic hook 输出）
        - postgres-start.log：PostgreSQL 启动日志（实际读 <data_dir>/start.log）
        - embedded-pg-service.log：Python 侧 EmbeddedPostgresService 日志

        缺失文件返回 ``<missing>``；读取错误返回 ``<read error: ...>``。
        """
        result: dict[str, str] = {}
        targets = {
            "sidecar.log": self._log_dir / "sidecar.log",
            "sidecar.stderr.log": self._log_dir / "sidecar.stderr.log",
            "postgres-start.log": self._data_dir / "start.log",
            "embedded-pg-service.log": self._log_dir / "embedded-pg-service.log",
        }
        for name, path in targets.items():
            try:
                with open(path, "rb") as f:
                    f.seek(0, 2)
                    size = f.tell()
                    f.seek(max(0, size - tail_bytes))
                    result[name] = f.read().decode("utf-8", errors="replace")
            except FileNotFoundError:
                result[name] = "<missing>"
            except OSError as exc:
                result[name] = f"<read error: {exc}>"
        return result

    @classmethod
    def get_instance(cls) -> EmbeddedPostgresService:
        """获取已注册单例，未构造时 raise（供 ShutdownCoordinator Step 8 使用）。"""
        with cls._lock:
            if cls._instance is None or not cls._instance._initialized:
                raise RuntimeError(f"{cls.__name__} singleton not initialized")
            return cls._instance

    @classmethod
    def _reset_singleton(cls) -> None:
        """重置单例（测试隔离用，R7）。

        先尝试停止 sidecar 子进程，再清空 _instance。
        """
        with cls._lock:
            inst = cls._instance
            if inst is not None and inst._initialized:
                try:
                    inst.stop_sync()
                except Exception as e:
                    logger.warning("_reset_singleton stop failed: %s", DataSanitizer.sanitize_error(e), exc_info=True)
            cls._instance = None

    @classmethod
    def _atexit_cleanup(cls) -> None:
        """atexit 清理（进程退出时停 sidecar，由 SingletonRegistry 反序调用）。

        未启动时安全无操作。
        """
        with cls._lock:
            inst = cls._instance
            if inst is None or not inst._initialized:
                return
            try:
                inst.stop_sync()
            except Exception as e:
                logger.warning("atexit cleanup failed: %s", DataSanitizer.sanitize_error(e), exc_info=True)
