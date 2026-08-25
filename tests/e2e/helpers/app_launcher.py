import logging
import os
import socket
import subprocess
import sys
import threading
import time

from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[3]

logger = logging.getLogger(__name__)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _read_log_tail(log_path: Path, log_offset: int = 0, max_chars: int = 4000) -> str:
    """读取日志文件尾部用于错误诊断（避免完整日志过大）。"""
    try:
        with open(log_path, encoding="utf-8") as f:
            f.seek(log_offset)
            content = f.read()
        return content[-max_chars:] if len(content) > max_chars else content
    except (FileNotFoundError, OSError):
        return f"(unable to read log at {log_path})"


def wait_until_ready(
    url: str,
    proc: subprocess.Popen,
    log_path: Path,
    log_offset: int = 0,
    timeout_s: float = 60.0,
) -> None:
    # E2E_TIMEOUT_MULTIPLIER 与 conftest.py 的 FletPage 超时倍数一致，
    # CI 中设为 2.0（60s→120s），吸收 Windows runner 启动慢的抖动。
    multiplier = float(os.environ.get("E2E_TIMEOUT_MULTIPLIER", "1.0"))
    effective_timeout = timeout_s * multiplier
    deadline = time.monotonic() + effective_timeout
    last_err: Exception | None = None
    print(f"[E2E DIAG] wait_until_ready: url={url}, timeout={effective_timeout}s, pid={proc.pid}", flush=True)
    probe_count = 0
    while time.monotonic() < deadline:
        # 子进程崩溃则立即报错，不用空等超时（WinError 10061 的根因之一）
        if proc.poll() is not None:
            log_tail = _read_log_tail(log_path, log_offset)
            print(f"[E2E DIAG] wait_until_ready: proc exited, code={proc.returncode}", flush=True)
            raise RuntimeError(
                f"Flet app process (PID {proc.pid}) exited prematurely with code {proc.returncode} "
                f"before becoming ready at {url}. Log tail:\n{log_tail}"
            )
        try:
            r = httpx.get(url, timeout=3.0)
            if r.status_code == 200:
                print(f"[E2E DIAG] wait_until_ready: HTTP 200 after {probe_count} probes", flush=True)
                return
            if probe_count % 20 == 0:
                print(f"[E2E DIAG] wait_until_ready: probe={probe_count}, status={r.status_code}", flush=True)
        except httpx.HTTPError as e:
            last_err = e
            if probe_count % 20 == 0:
                print(f"[E2E DIAG] wait_until_ready: probe={probe_count}, err={type(e).__name__}: {e}", flush=True)
        probe_count += 1
        time.sleep(0.5)
    print(f"[E2E DIAG] wait_until_ready: TIMEOUT after {effective_timeout}s, probes={probe_count}", flush=True)
    log_tail = _read_log_tail(log_path, log_offset)
    raise RuntimeError(
        f"Flet app not ready at {url} within {effective_timeout}s. Last error: {last_err}. Log tail:\n{log_tail}"
    )


def _drain_stdout(proc: subprocess.Popen) -> None:
    log_path = PROJECT_ROOT / "logs" / "e2e-flet-app.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if proc.stdout:
            with open(log_path, "a", encoding="utf-8") as f:
                for line in proc.stdout:
                    f.write(line)
                    f.flush()
    except Exception as e:  # noqa: BLE001
        logger.warning("[E2E App Launcher] stdout drain error: %s", e, exc_info=True)


_STARTUP_ERROR_PATTERNS = (
    "Connection error getting revision",
    "connection was closed in the middle of operation",
    "[Bootstrap] Database initialization failed",
    "db_init_failed",
)

_STARTUP_SUCCESS_PATTERNS = (
    "[Bootstrap] Loaded",
    "[TaskManager] init_db",
    "Tushare capability warmup",
)


def _check_startup_errors(log_path: Path, log_offset: int = 0, timeout_s: float = 8.0) -> None:
    """Poll the app log for critical startup errors after HTTP ready.

    If the Flet web server responds 200 but the app internally fails
    (e.g. DB unreachable), the error is logged within a few seconds.
    This function catches such errors early instead of waiting for
    Playwright's 45s timeout.

    Only checks content written after *log_offset* to avoid false
    positives from previous app instances that wrote to the same file.

    Exits early if a success pattern is found (app initialized OK).
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with open(log_path, encoding="utf-8") as f:
                f.seek(log_offset)
                content = f.read()
            for pattern in _STARTUP_ERROR_PATTERNS:
                if pattern in content:
                    raise RuntimeError(
                        f"Flet app started (HTTP 200) but DB initialization failed. "
                        f"Error pattern: '{pattern}'. "
                        f"Check {log_path} for details."
                    )
            for pattern in _STARTUP_SUCCESS_PATTERNS:
                if pattern in content:
                    return  # App initialized successfully, no need to wait
        except FileNotFoundError:
            pass
        time.sleep(0.5)


_MAX_BIND_RETRIES = 3


def _is_port_bind_conflict(exc: Exception) -> bool:
    """判定启动失败是否为端口绑定冲突（WinError 10048）。

    wait_until_ready 的 RuntimeError 消息内嵌 app 日志尾部，uvicorn bind 失败
    （OSError 10048）时消息含 "10048"。其他启动失败（DB 初始化/代码异常）与
    端口无关，换端口重试无意义，须保持直接抛出。
    """
    return "10048" in str(exc)


def _start_flet_app_once(
    config_file: Path,
    env_overrides: dict[str, str],
    *,
    startup_timeout_s: float,
) -> tuple[subprocess.Popen, str]:
    """单次启动尝试（供 start_flet_app 端口冲突重试复用）。

    _free_port() 探测后端口即释放，子进程 import 数秒后才由 uvicorn 真正 bind，
    期间该端口可能被本机其他本地 TCP 端点抢占（WinError 10048，见 start_flet_app
    docstring 与 PR 557 CI run 32807572004 的 E2E 失败）。
    """
    port = _free_port()
    env = {
        **os.environ,
        "FLET_FORCE_WEB_SERVER": "true",
        "FLET_SERVER_PORT": str(port),
        "ASTOCK_CONFIG_FILE": str(config_file),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "E2E_TESTING": "true",
        "AUTO_MIGRATE": "true",
        **env_overrides,
    }
    log_path = PROJECT_ROOT / "logs" / "e2e-flet-app.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # 清除上次运行留下的 main_trace.log（诊断专用，每次运行从空开始）
    trace_path = PROJECT_ROOT / "logs" / "main_trace.log"
    try:
        trace_path.unlink()
    except FileNotFoundError:
        pass
    # Record current log size so _check_startup_errors only inspects
    # output from *this* app instance, not leftover from previous runs.
    try:
        log_offset = log_path.stat().st_size
    except FileNotFoundError:
        log_offset = 0
    proc = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
    )
    drain_thread = threading.Thread(target=_drain_stdout, args=(proc,), daemon=True)
    drain_thread.start()
    url = f"http://127.0.0.1:{port}"
    try:
        wait_until_ready(url, proc, log_path, log_offset, timeout_s=startup_timeout_s)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[E2E] Flet app not ready at %s, terminating process: %s", url, exc, exc_info=True)
        proc.terminate()
        raise
    # HTTP 200 only means the Flet web server is up; the app may still
    # be failing internally (e.g. DB unreachable).  Poll the log for a
    # few seconds to catch such errors early.
    try:
        _check_startup_errors(log_path, log_offset)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[E2E] Startup error detected in log %s, terminating process: %s",
            log_path,
            exc,
        )
        proc.terminate()
        raise
    return proc, url


def start_flet_app(
    config_file: Path,
    env_overrides: dict[str, str],
    *,
    startup_timeout_s: float = 60.0,
) -> tuple[subprocess.Popen, str]:
    """启动 Flet app 子进程；端口绑定冲突自动换新端口重试（上限 _MAX_BIND_RETRIES）。

    Windows 下 ``_free_port()`` 探测端口后即释放，到子进程 uvicorn 真正 bind 之间
    有秒级窗口，本机其他本地 TCP 端点可能抢占该端口，子进程 bind 失败退出
    （code 3 / WinError 10048）。失败发生在 ``main(page)`` 之前，无 DB/sidecar
    副作用，换新端口重试是安全的。
    """
    last_exc: Exception | None = None
    for attempt in range(_MAX_BIND_RETRIES):
        try:
            return _start_flet_app_once(config_file, env_overrides, startup_timeout_s=startup_timeout_s)
        except RuntimeError as exc:
            if not _is_port_bind_conflict(exc):
                raise
            last_exc = exc
            logger.warning(
                "[E2E] Flet app 端口绑定冲突（10048），换新端口重试 (attempt %d/%d)",
                attempt + 1,
                _MAX_BIND_RETRIES,
            )
    # 多次换端口重试仍冲突：抛出最后一次原始异常，保持与单次启动一致的报错形态
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("unreachable: _MAX_BIND_RETRIES >= 1 必然执行循环")  # pragma: no cover
