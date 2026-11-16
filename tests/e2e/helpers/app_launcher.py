import itertools
import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
import uuid

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[3]
APP_STDOUT_AGGREGATE_NAME = "e2e-flet-app.log"
RUN_LOG_DIR_NAME = "e2e"

logger = logging.getLogger(__name__)

# run 级唯一标识：每次 pytest 进程独立，避免两个会话的实例目录互相覆盖。
# 不自动清理历史 run/实例目录：清理无法安全判定是否属于并行会话，宁可保留证据。
_RUN_ID = f"{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
_ALLOC_LOCK = threading.Lock()
_INSTANCE_SEQ = itertools.count(1)
_AGGREGATE_LOCK = threading.Lock()


@dataclass(frozen=True)
class AppLogPaths:
    """单个 Flet app 实例的诊断产物路径。

    每个实例独占 ``logs/e2e/<run_id>/<seq>-<label>/``，``stdout.log`` 与
    ``main_trace.log`` 只属于该实例；``aggregate_stdout_path`` 是 run 级汇总日志
    （保留给 CI 工件，不得用于断言/启动错误检查，否则会重新引入跨实例串扰）。
    """

    run_id: str
    instance_id: str
    log_dir: Path
    stdout_path: Path
    trace_path: Path
    logs_root: Path

    @property
    def aggregate_stdout_path(self) -> Path:
        return self.logs_root / APP_STDOUT_AGGREGATE_NAME


def _slugify(label: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-._")
    return slug or "app"


def _run_dir(logs_root: Path | None = None) -> Path:
    return (logs_root or (PROJECT_ROOT / "logs")) / RUN_LOG_DIR_NAME / _RUN_ID


def allocate_app_log_paths(label: str, *, logs_root: Path | None = None) -> AppLogPaths:
    """为一次 app 进程启动分配全新的实例日志目录（不触碰任何既有实例产物）。"""
    base = logs_root or (PROJECT_ROOT / "logs")
    with _ALLOC_LOCK:
        seq = next(_INSTANCE_SEQ)
    instance_id = f"{seq:02d}-{_slugify(label)}"
    log_dir = _run_dir(base) / instance_id
    log_dir.mkdir(parents=True, exist_ok=True)
    return AppLogPaths(
        run_id=_RUN_ID,
        instance_id=instance_id,
        log_dir=log_dir,
        stdout_path=log_dir / "stdout.log",
        trace_path=log_dir / "main_trace.log",
        logs_root=base,
    )


def iter_run_log_paths(run_id: str | None = None, *, logs_root: Path | None = None) -> list[AppLogPaths]:
    """枚举当前 run（或指定 run）下已分配的实例日志路径。"""
    base = logs_root or (PROJECT_ROOT / "logs")
    base_run_dir = base / RUN_LOG_DIR_NAME / (run_id or _RUN_ID)
    if not base_run_dir.is_dir():
        return []
    paths: list[AppLogPaths] = []
    for child in sorted(base_run_dir.iterdir()):
        if not child.is_dir():
            continue
        paths.append(
            AppLogPaths(
                run_id=run_id or _RUN_ID,
                instance_id=child.name,
                log_dir=child,
                stdout_path=child / "stdout.log",
                trace_path=child / "main_trace.log",
                logs_root=base,
            )
        )
    return paths


def register_app_instance(paths: AppLogPaths, *, label: str, pid: int, url: str) -> None:
    """把实例写入 run 级汇总索引 ``index.json``（原子替换，失败不阻断启动）。"""
    index_path = paths.log_dir.parent / "index.json"
    entry = {
        "instance_id": paths.instance_id,
        "label": label,
        "pid": pid,
        "url": url,
        "stdout": paths.stdout_path.relative_to(index_path.parent).as_posix(),
        "trace": paths.trace_path.relative_to(index_path.parent).as_posix(),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    try:
        with _ALLOC_LOCK:
            if index_path.exists():
                data = json.loads(index_path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    data = {"run_id": paths.run_id, "instances": []}
            else:
                data = {"run_id": paths.run_id, "instances": []}
            instances = data.setdefault("instances", [])
            if isinstance(instances, list):
                instances.append(entry)
            tmp_path = index_path.with_name(f"{index_path.name}.tmp")
            tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp_path, index_path)
    except (OSError, ValueError) as exc:
        logger.warning("[E2E App Launcher] failed to update run index %s: %s", index_path, exc)


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


def _drain_lines(
    lines: Iterable[str] | None,
    *,
    primary_path: Path,
    aggregate_path: Path | None = None,
    instance_id: str | None = None,
) -> None:
    """把子进程 stdout 写入本实例日志；可选镜像到 run 级汇总日志（带实例前缀）。"""
    if lines is None:
        return
    primary_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(primary_path, "a", encoding="utf-8", newline="") as primary:
            aggregate = None
            try:
                if aggregate_path is not None:
                    try:
                        aggregate_path.parent.mkdir(parents=True, exist_ok=True)
                        aggregate = open(aggregate_path, "a", encoding="utf-8", newline="")
                    except OSError as agg_err:
                        # run 级汇总日志不可写（Windows 文件锁等）不得影响本实例日志。
                        logger.warning("[E2E App Launcher] aggregate log unavailable %s: %s", aggregate_path, agg_err)
                prefix = f"[{instance_id}] " if instance_id else ""
                for line in lines:
                    primary.write(line)
                    primary.flush()
                    if aggregate is not None:
                        try:
                            with _AGGREGATE_LOCK:
                                aggregate.write(prefix + line)
                                aggregate.flush()
                        except OSError as agg_err:
                            logger.warning("[E2E App Launcher] aggregate log write failed: %s", agg_err)
                            aggregate.close()
                            aggregate = None
            finally:
                if aggregate is not None:
                    aggregate.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("[E2E App Launcher] stdout drain error: %s", e, exc_info=True)


def _drain_stdout(proc: subprocess.Popen, paths: AppLogPaths) -> None:
    _drain_lines(
        proc.stdout,
        primary_path=paths.stdout_path,
        aggregate_path=paths.aggregate_stdout_path,
        instance_id=paths.instance_id,
    )


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

    Only checks content written after *log_offset*; E2E 启动器为每个实例分配独立
    stdout 文件，offset 恒为 0，不会读到其他并行实例的输出。

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
    instance_label: str,
) -> tuple[subprocess.Popen, str, AppLogPaths]:
    """单次启动尝试（供 start_flet_app 端口冲突重试复用）。

    _free_port() 探测后端口即释放，子进程 import 数秒后才由 uvicorn 真正 bind，
    期间该端口可能被本机其他本地 TCP 端点抢占（WinError 10048，见 start_flet_app
    docstring 与 PR 557 CI run 32807572004 的 E2E 失败）。

    每次尝试分配全新的实例日志目录：port-bind 重试以及并行 A/B 池的
    stdout/trace 互不共享，且不删除任何既有实例的 trace。
    """
    port = _free_port()
    paths = allocate_app_log_paths(instance_label)
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
        # 覆盖放在最后：实例 trace 路径是诊断归属的唯一来源，不允许被 env_overrides 改写。
        "E2E_MAIN_TRACE_PATH": str(paths.trace_path),
        "E2E_INSTANCE_ID": paths.instance_id,
    }
    proc = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
    )
    url = f"http://127.0.0.1:{port}"
    register_app_instance(paths, label=instance_label, pid=proc.pid, url=url)
    drain_thread = threading.Thread(target=_drain_stdout, args=(proc, paths), daemon=True)
    drain_thread.start()
    try:
        # 实例日志文件为本次启动新建，offset 恒为 0。
        wait_until_ready(url, proc, paths.stdout_path, 0, timeout_s=startup_timeout_s)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[E2E] Flet app not ready at %s, terminating process: %s", url, exc, exc_info=True)
        proc.terminate()
        raise
    # HTTP 200 only means the Flet web server is up; the app may still
    # be failing internally (e.g. DB unreachable).  Poll the log for a
    # few seconds to catch such errors early.
    try:
        _check_startup_errors(paths.stdout_path, 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[E2E] Startup error detected in log %s, terminating process: %s",
            paths.stdout_path,
            exc,
        )
        proc.terminate()
        raise
    return proc, url, paths


def start_flet_app(
    config_file: Path,
    env_overrides: dict[str, str],
    *,
    startup_timeout_s: float = 60.0,
    instance_label: str = "app",
) -> tuple[subprocess.Popen, str, AppLogPaths]:
    """启动 Flet app 子进程；端口绑定冲突自动换新端口重试（上限 _MAX_BIND_RETRIES）。

    Windows 下 ``_free_port()`` 探测端口后即释放，到子进程 uvicorn 真正 bind 之间
    有秒级窗口，本机其他本地 TCP 端点可能抢占该端口，子进程 bind 失败退出
    （code 3 / WinError 10048）。失败发生在 ``main(page)`` 之前，无 DB/sidecar
    副作用，换新端口重试是安全的。

    返回 ``(proc, url, paths)``；``paths`` 保存本实例专属的 stdout/trace 路径，
    调用方必须沿同一实例读取诊断产物。
    """
    last_exc: Exception | None = None
    for attempt in range(_MAX_BIND_RETRIES):
        try:
            return _start_flet_app_once(
                config_file,
                env_overrides,
                startup_timeout_s=startup_timeout_s,
                instance_label=instance_label,
            )
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
