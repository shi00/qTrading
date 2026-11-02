"""EmbeddedPostgresService.start 取消时序回归测试（D16-R02）。

真实工作线程 + 真实 Popen 覆盖三个取消窗口，不使用“mock asyncio.to_thread
立即抛 CancelledError”的弱模拟：

- W1 已调度未 Popen：取消后不得创建进程；
- W2 已 Popen 未 ready：取消返回后子进程已终止；
- W3 已 ready：取消返回后不得残留进程 / reader / 连接信息；
- W3b 工作线程已完成后取消：由收尾方回收本次尝试拥有的进程；
- 取消第二次 start 不得误杀此前已存在的启动进程。

测试用 mock sidecar（Python 脚本 + 平台包装器）保证跨平台。
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import threading
import time
from pathlib import Path

import pytest

from data.persistence.embedded_postgres import service as svc_module
from data.persistence.embedded_postgres.service import EmbeddedPostgresService

_MOCK_SIDECAR = """import argparse, json, os, sys

MODE = {mode!r}
parser = argparse.ArgumentParser()
parser.add_argument("command")
parser.add_argument("--data-dir", required=True)
parser.add_argument("--install-dir")
parser.add_argument("--password-file")
parser.add_argument("--database", default="qtrading")
parser.add_argument("--username", default="postgres")
parser.add_argument("--listen", default="127.0.0.1")
parser.add_argument("--port", type=int, default=0)
parser.add_argument("--log-file")
parser.add_argument("--parent-pid", type=int)
args = parser.parse_args()
if args.command != "run":
    sys.exit(2)
if MODE == "ready":
    if args.password_file:
        os.makedirs(os.path.dirname(args.password_file) or ".", exist_ok=True)
        with open(args.password_file, "w", encoding="utf-8") as f:
            f.write("mock_pg_password_race")
    ready = {{
        "schema": "qtrading.embedded_postgres.run.ready.v1",
        "status": "running",
        "postgres_version": "16.14.0-mock",
        "host": args.listen,
        "port": 55432,
        "database": args.database,
        "username": args.username,
        "password_source": "password_file",
        "url": "postgresql://x:***@127.0.0.1:55432/x",
        "data_dir": args.data_dir,
        "sidecar_pid": os.getpid(),
        "pid": os.getpid(),
    }}
    sys.stdout.write(json.dumps(ready) + "\\n")
    sys.stdout.flush()
sys.stdin.read()
sys.exit(0)
"""


@pytest.fixture
def race_paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "data_dir": tmp_path / "postgres" / "16" / "data",
        "install_dir": tmp_path / "postgres" / "16" / "install",
        "log_dir": tmp_path / "logs",
        "tmp_path": tmp_path,
    }


def _make_service(paths: dict[str, Path], mode: str) -> EmbeddedPostgresService:
    mock_py = paths["tmp_path"] / f"mock_sidecar_{mode}.py"
    mock_py.write_text(_MOCK_SIDECAR.format(mode=mode), encoding="utf-8")
    if os.name == "nt":
        binary = paths["tmp_path"] / f"sidecar_{mode}.bat"
        binary.write_text(f'@python -I "{mock_py}" %*\n', encoding="utf-8")
    else:
        binary = paths["tmp_path"] / f"sidecar_{mode}.sh"
        binary.write_text(f'#!/bin/sh\nexec python3 -I "{mock_py}" "$@"\n', encoding="utf-8")
        binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    return EmbeddedPostgresService(
        sidecar_binary=binary,
        data_dir=paths["data_dir"],
        install_dir=paths["install_dir"],
        log_dir=paths["log_dir"],
        start_timeout=5.0,
        stop_timeout=5.0,
    )


async def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not met before timeout")


async def _assert_no_reader_threads(timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        leaked = [t.name for t in threading.enumerate() if t.name.startswith("embedded-pg-")]
        if not leaked:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"reader threads leaked: {leaked}")


class _RecordingPopen:
    def __init__(self) -> None:
        self.processes = []

    def __call__(self, *args, **kwargs):
        proc = svc_module.subprocess.Popen(*args, **kwargs)
        self.processes.append(proc)
        return proc


@pytest.mark.asyncio(loop_scope="function")
async def test_cancel_before_popen_does_not_spawn(race_paths, monkeypatch) -> None:
    """W1：工作线程已调度但未 Popen 时取消 → 不创建进程。"""
    service = _make_service(race_paths, "ready")
    recording = _RecordingPopen()
    entered = threading.Event()
    release = threading.Event()

    monkeypatch.setattr(svc_module.subprocess, "Popen", recording)

    def blocked_verify() -> None:
        entered.set()
        assert release.wait(10), "test did not release blocked worker"

    monkeypatch.setattr(service, "_verify_sidecar_sha256", blocked_verify)
    try:
        task = asyncio.create_task(service.start())
        await _wait_until(entered.is_set)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert recording.processes == []
        assert service._process is None
        assert service._connection_info is None
        await _assert_no_reader_threads()
    finally:
        EmbeddedPostgresService._reset_singleton()


@pytest.mark.asyncio(loop_scope="function")
async def test_cancel_after_popen_before_ready_terminates_child(race_paths, monkeypatch) -> None:
    """W2：已 Popen 未 ready 时取消 → 取消返回后子进程已终止。"""
    service = _make_service(race_paths, "never_ready")
    recording = _RecordingPopen()
    monkeypatch.setattr(svc_module.subprocess, "Popen", recording)
    try:
        task = asyncio.create_task(service.start())
        await _wait_until(lambda: service._process is not None)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert recording.processes and recording.processes[0].poll() is not None
        assert service._process is None
        assert service._connection_info is None
        await _assert_no_reader_threads()
    finally:
        await service.stop()
        EmbeddedPostgresService._reset_singleton()


@pytest.mark.asyncio(loop_scope="function")
async def test_cancel_after_ready_terminates_child(race_paths, monkeypatch) -> None:
    """W3：已 ready 且 start 尚未返回时取消 → 无进程/reader/连接信息残留。"""
    service = _make_service(race_paths, "ready")
    recording = _RecordingPopen()
    ready_entered = threading.Event()
    release = threading.Event()

    monkeypatch.setattr(svc_module.subprocess, "Popen", recording)
    original_reader = service._start_stdout_reader_thread

    def blocked_stdout_reader() -> None:
        original_reader()
        ready_entered.set()
        assert release.wait(10), "test did not release blocked worker"

    monkeypatch.setattr(service, "_start_stdout_reader_thread", blocked_stdout_reader)
    try:
        task = asyncio.create_task(service.start())
        await _wait_until(ready_entered.is_set)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert recording.processes and recording.processes[0].poll() is not None
        assert service._process is None
        assert service._connection_info is None
        await _assert_no_reader_threads()
    finally:
        await service.stop()
        EmbeddedPostgresService._reset_singleton()


@pytest.mark.asyncio(loop_scope="function")
async def test_cancel_after_worker_finished_cleans_owned_process(race_paths) -> None:
    """W3b：工作线程已完成（已 ready）后取消 → 收尾方回收本次尝试拥有的进程。"""
    service = _make_service(race_paths, "ready")
    try:
        await service.start()
        proc = service._process
        assert proc is not None and proc.poll() is None
        attempt = svc_module._StartAttempt()
        attempt.mark_started()
        attempt.mark_finished()
        attempt.publish_process(proc)
        await asyncio.get_running_loop().run_in_executor(None, service._finalize_cancelled_start, attempt)
        assert proc.poll() is not None
        assert service._process is None
        assert service._connection_info is None
        await _assert_no_reader_threads()
    finally:
        EmbeddedPostgresService._reset_singleton()


@pytest.mark.asyncio(loop_scope="function")
async def test_cancel_second_start_keeps_existing_process(race_paths, monkeypatch) -> None:
    """重复 start：取消第二次 start 不得误杀第一次启动的进程。"""
    service = _make_service(race_paths, "ready")
    entered = threading.Event()
    release = threading.Event()
    original_start_sync = service._start_sync

    try:
        await service.start()
        assert service._process is not None and service._process.poll() is None

        def delayed_start_sync(attempt):
            entered.set()
            assert release.wait(10), "test did not release blocked worker"
            return original_start_sync(attempt)

        monkeypatch.setattr(service, "_start_sync", delayed_start_sync)
        task = asyncio.create_task(service.start())
        await _wait_until(entered.is_set)
        task.cancel()
        # 必须先释放工作线程：取消收尾在等待 finished 握手，若延后释放会等到
        # blocked_start_sync 的 wait 超时（测试变成 10s 空转并依赖断言失败）
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert service._process is not None and service._process.poll() is None
        assert service._connection_info is not None
    finally:
        await service.stop()
        EmbeddedPostgresService._reset_singleton()
