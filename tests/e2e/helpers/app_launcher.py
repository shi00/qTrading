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


def wait_until_ready(url: str, timeout_s: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            r = httpx.get(url, timeout=3.0)
            if r.status_code == 200:
                return
        except httpx.HTTPError as e:
            last_err = e
        time.sleep(0.5)
    raise RuntimeError(f"Flet app not ready at {url} within {timeout_s}s. Last error: {last_err}")


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
        sys.stderr.write(f"\n[E2E App Launcher Log Error]: {e}\n")


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


def start_flet_app(config_file: Path, env_overrides: dict[str, str]) -> tuple[subprocess.Popen, str]:
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
        wait_until_ready(url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[E2E] Flet app not ready at %s, terminating process: %s", url, exc)
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
