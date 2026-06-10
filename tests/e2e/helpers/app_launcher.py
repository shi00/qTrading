import os
import socket
import subprocess
import sys
import threading
import time

from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[3]


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
        except Exception as e:  # noqa: BLE001
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
    proc = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    drain_thread = threading.Thread(target=_drain_stdout, args=(proc,), daemon=True)
    drain_thread.start()
    url = f"http://127.0.0.1:{port}"
    try:
        wait_until_ready(url)
    except Exception:  # noqa: BLE001
        proc.terminate()
        raise
    return proc, url
