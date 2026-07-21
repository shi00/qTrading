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
import subprocess
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from data.persistence.embedded_postgres.protocol import ConnectionInfo
from utils.singleton_registry import register_singleton

if TYPE_CHECKING:
    from utils.config_models import AppConfig

logger = logging.getLogger("qtrading.embedded_postgres")


class EmbeddedPostgresStartError(RuntimeError):
    """sidecar 启动失败（binary 缺失/ready 超时/JSON 无效/password_file 缺失等）。"""


_READY_SCHEMA = "qtrading.embedded_postgres.run.ready.v1"


def _setup_service_logger(log_dir: Path) -> logging.Logger:
    """为 EmbeddedPostgresService 配置独立 FileHandler，写入 embedded-pg-service.log。

    独立 logger 避免应用主日志被 sidecar 噪声淹没，便于问题定位（§3.7）。
    重复构造时不重复挂 handler。
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    svc_logger = logging.getLogger("qtrading.embedded_postgres")
    svc_logger.setLevel(logging.DEBUG)
    if not any(getattr(h, "_embedded_pg_handler", False) for h in svc_logger.handlers):
        handler = logging.FileHandler(log_dir / "embedded-pg-service.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s"))
        handler._embedded_pg_handler = True  # type: ignore[attr-defined]
        svc_logger.addHandler(handler)
    return svc_logger


@register_singleton
class EmbeddedPostgresService:
    """Embedded PostgreSQL sidecar 适配单例（Phase 2 §3.1）。

    使用双检锁 + ``_initialized`` 守卫保证单例；``from_config(AppConfig)`` 从配置解析路径。
    测试用 ``__init__`` 注入 fake path；生产用 ``from_config``。
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
        username: str = "qtrading",
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
            # log_dir 默认从 data_dir 推导：data_dir = <root>/postgres/17/data
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
            self._svc_logger = _setup_service_logger(self._log_dir)
            self._initialized = True

    @classmethod
    def from_config(cls, config: AppConfig) -> EmbeddedPostgresService:
        """从 AppConfig 解析路径并构造/复用单例。

        - sidecar_binary：显式路径优先，否则按平台默认搜索 sidecars/qtrading-pg-sidecar[.exe]
        - data_dir：embedded_pg_data_root 为空时用 platformdirs 默认 <app data>/postgres/17/data
        - install_dir：默认 <data_root>/install
        - log_dir：默认 <app data>/postgres-logs
        """
        if config.embedded_pg_sidecar_path:
            sidecar_binary = Path(config.embedded_pg_sidecar_path)
        else:
            exe_suffix = ".exe" if os.name == "nt" else ""
            sidecar_binary = Path("sidecars") / f"qtrading-pg-sidecar{exe_suffix}"

        if config.embedded_pg_data_root:
            data_root = Path(config.embedded_pg_data_root)
        else:
            import platformdirs

            app_data = Path(platformdirs.user_data_dir("qTrading"))
            data_root = app_data / "postgres" / "17"
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

    async def start(self) -> ConnectionInfo:
        """拉起 sidecar 子进程并解析 ready JSON。

        使用 ``asyncio.to_thread`` 包装同步 Popen + readline，避免阻塞事件循环（R16）。
        失败时保证清理子进程（kill + wait）。CancelledError 路径触发 stop 兜底。
        """
        import asyncio

        try:
            return await asyncio.to_thread(self._start_sync)
        except BaseException:
            # CancelledError 或其他异常：确保子进程不泄漏
            self._cleanup_failed_start()
            raise

    def _start_sync(self) -> ConnectionInfo:
        """同步启动 sidecar（供 asyncio.to_thread 包装）。"""
        if self._connection_info is not None and self._process is not None and self._process.poll() is None:
            return self._connection_info

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
        # 文件句柄需保留到 stop_sync() 关闭，不能用 with；SIM115 不适用
        self._stderr_file = open(  # noqa: SIM115
            self._log_dir / "sidecar.stderr.log", "a", encoding="utf-8"
        )

        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._stderr_file,
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

        ready_line = self._readline_with_timeout(self._process.stdout, self._start_timeout)
        if not ready_line:
            exit_code = self._process.poll()
            self._svc_logger.error("sidecar exited before ready line (exit=%s)", exit_code)
            self._cleanup_failed_start()
            raise EmbeddedPostgresStartError(f"sidecar exited before ready line (exit={exit_code})")

        try:
            ready = json.loads(ready_line)
        except json.JSONDecodeError as exc:
            self._svc_logger.error("ready JSON parse failed: %s; line=%r", exc, ready_line)
            self._cleanup_failed_start()
            raise EmbeddedPostgresStartError(f"ready JSON parse failed: {exc}; line={ready_line!r}") from exc

        if ready.get("schema") != _READY_SCHEMA:
            self._svc_logger.error("unexpected ready schema: %s", ready.get("schema"))
            self._cleanup_failed_start()
            raise EmbeddedPostgresStartError(f"unexpected ready schema: {ready.get('schema')}")
        if ready.get("status") != "running":
            self._svc_logger.error("unexpected ready status: %s", ready.get("status"))
            self._cleanup_failed_start()
            raise EmbeddedPostgresStartError(f"unexpected ready status: {ready.get('status')}")
        port = int(ready.get("port", 0))
        if port <= 0:
            self._svc_logger.error("invalid port in ready JSON: %s", port)
            self._cleanup_failed_start()
            raise EmbeddedPostgresStartError(f"invalid port in ready JSON: {port}")
        pid = int(ready.get("pid") or 0)
        data_dir_str = str(ready.get("data_dir", str(self._data_dir)))

        try:
            password = password_file.read_text(encoding="utf-8").strip()
        except FileNotFoundError as exc:
            self._svc_logger.error("password_file not found: %s", password_file)
            self._cleanup_failed_start()
            raise EmbeddedPostgresStartError(f"password_file not found: {password_file}") from exc
        except OSError as exc:
            self._svc_logger.error("password_file read failed: %s", exc)
            self._cleanup_failed_start()
            raise EmbeddedPostgresStartError(f"password_file read failed: {exc}") from exc

        url = f"postgresql+asyncpg://{self._username}:{password}@{self._listen}:{port}/{self._database}"

        self._connection_info = ConnectionInfo(url=url, port=port, pid=pid, data_dir=data_dir_str)
        # 日志中 URL 脱敏（R9）：仅记 host:port
        self._svc_logger.info("embedded postgres started on %s:%s (pid=%s)", self._listen, port, pid)
        return self._connection_info

    def _readline_with_timeout(self, stream: object, timeout: float) -> str:
        """带超时的 readline，避免阻塞事件循环。"""
        q: queue.Queue[str] = queue.Queue(maxsize=1)

        def _reader() -> None:
            try:
                line = stream.readline()  # type: ignore[union-attr]
                q.put(line if line is not None else "")
            except Exception:
                q.put("")

        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        try:
            return q.get(timeout=timeout)
        except queue.Empty:
            return ""

    def _cleanup_failed_start(self) -> None:
        """启动失败时清理子进程与文件句柄。"""
        if self._process is not None:
            try:
                self._process.kill()
            except Exception:
                pass
            try:
                self._process.wait(timeout=5)
            except Exception:
                pass
            self._process = None
        self._connection_info = None
        if self._stderr_file is not None:
            try:
                self._stderr_file.close()  # type: ignore[attr-defined]
            except Exception:
                pass
            self._stderr_file = None

    async def stop(self) -> None:
        """幂等停止 sidecar：stdin.close → wait(timeout) → kill 兜底。

        清理 _process / _connection_info / _stderr_file。
        """
        import asyncio

        await asyncio.to_thread(self.stop_sync)

    def stop_sync(self) -> None:
        """同步停止 sidecar（供 asyncio.to_thread 包装，shutdown Step 8 使用）。"""
        if self._process is None:
            return
        proc = self._process
        try:
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
            try:
                proc.wait(timeout=self._stop_timeout)
                self._svc_logger.info("embedded postgres stopped gracefully")
            except subprocess.TimeoutExpired:
                self._svc_logger.warning("sidecar stop timeout after %ss, killing", self._stop_timeout)
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
        finally:
            self._process = None
            self._connection_info = None
            if self._stderr_file is not None:
                try:
                    self._stderr_file.close()  # type: ignore[attr-defined]
                except Exception:
                    pass
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
                    logger.warning("_reset_singleton stop failed: %s", e, exc_info=True)
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
                logger.warning("atexit cleanup failed: %s", e, exc_info=True)
