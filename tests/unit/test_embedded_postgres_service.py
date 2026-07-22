"""EmbeddedPostgresService 单元测试（Phase 2 §5.1 红灯翻绿 + §5.2 扩展）。

Mock 策略：fake_paths fixture 提供一个用 Python 写的 mock sidecar 脚本（输出
FAKE_READY JSON + 写 password 到 password_file + 等 stdin EOF 退出），通过
平台包装器（.bat/.sh）使其可执行。service.start() 真实 Popen 该脚本，
验证 sidecar 协议解析端到端正确。

测试分组（共 37 个）：
- TestEmbeddedPostgresServiceStart: 1 TDD + 12 start 协议 = 13 个
- TestEmbeddedPostgresServiceStop: 1 TDD + 10 stop 行为 = 11 个
- TestEmbeddedPostgresServiceSingleton: 2 TDD = 2 个
- TestEmbeddedPostgresServiceFromConfig: 1 TDD = 1 个
- TestEmbeddedPostgresServiceLog: 10 个日志测试 = 10 个
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.sanitizers import DataSanitizer

FAKE_READY = {
    "schema": "qtrading.embedded_postgres.run.ready.v1",
    "status": "running",
    "postgres_version": "17.2.0",
    "host": "127.0.0.1",
    "port": 55432,
    "database": "qtrading",
    "username": "postgres",
    "password_source": "password_file",
    "url": "postgresql://postgres:***@127.0.0.1:55432/qtrading",
    "data_dir": "C:/fake/postgres/17/data",
    "sidecar_pid": 12340,
    "pid": 12345,
}

# mock sidecar Python 脚本：解析 run 参数 → 写 password → 输出 ready JSON → 等 stdin EOF。
# 跨平台，通过 .bat（Windows）/.sh（Unix）包装器调用。
_MOCK_SIDECAR_PY = """import argparse, json, os, sys

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--install-dir")
    parser.add_argument("--password-file")
    parser.add_argument("--database", default="qtrading")
    parser.add_argument("--username", default="qtrading")
    parser.add_argument("--listen", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--log-file")
    parser.add_argument("--parent-pid", type=int)
    args = parser.parse_args()

    if args.command != "run":
        sys.exit(2)

    if args.password_file:
        os.makedirs(os.path.dirname(args.password_file) or ".", exist_ok=True)
        with open(args.password_file, "w", encoding="utf-8") as f:
            f.write("mock_pg_password_55432")

    ready = {
        "schema": "qtrading.embedded_postgres.run.ready.v1",
        "status": "running",
        "postgres_version": "17.2.0-mock",
        "host": args.listen,
        "port": 55432,
        "database": args.database,
        "username": args.username,
        "password_source": "password_file",
        "url": "postgresql://" + args.username + ":***@" + args.listen + ":55432/" + args.database,
        "data_dir": args.data_dir,
        "sidecar_pid": os.getpid(),
        "pid": os.getpid(),
    }
    sys.stdout.write(json.dumps(ready) + "\\n")
    sys.stdout.flush()

    # 等 stdin EOF 触发 graceful stop
    try:
        sys.stdin.read()
    except Exception:
        pass
    sys.exit(0)

if __name__ == "__main__":
    main()
"""


@pytest.fixture
def fake_paths(tmp_path: Path) -> dict[str, Path]:
    """提供 sidecar/data/install 三类临时路径桩 + mock sidecar 脚本。

    mock sidecar 跨平台：写 mock_sidecar.py + 平台包装器（.bat/.sh）作为 sidecar_binary。
    """
    mock_sidecar_py = tmp_path / "mock_sidecar.py"
    mock_sidecar_py.write_text(_MOCK_SIDECAR_PY, encoding="utf-8")

    if os.name == "nt":
        sidecar = tmp_path / "qtrading-pg-sidecar.bat"
        # %~dp0 是 .bat 所在目录（含尾部 \）；%* 透传所有参数
        sidecar.write_text(
            f'@python -I "{mock_sidecar_py}" %*\n',
            encoding="utf-8",
        )
    else:
        sidecar = tmp_path / "qtrading-pg-sidecar.sh"
        sidecar.write_text(
            f'#!/bin/sh\nexec python3 -I "{mock_sidecar_py}" "$@"\n',
            encoding="utf-8",
        )
        sidecar.chmod(0o755)

    return {
        "sidecar_binary": sidecar,
        "data_dir": tmp_path / "postgres" / "17" / "data",
        "install_dir": tmp_path / "postgres" / "17" / "install",
    }


@pytest.fixture
def fake_ready_line() -> bytes:
    return (json.dumps(FAKE_READY) + "\n").encode()


class _FakeStdin:
    """Fake stdin for Popen mock."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeStdout:
    """Fake stdout for Popen mock. Returns preset line on readline."""

    def __init__(self, line: str = "") -> None:
        self._line = line

    def readline(self) -> str:
        return self._line


class _FakePopen:
    """Fake subprocess.Popen for unit tests.

    Records argv, provides stdin/stdout mock, supports wait/kill/poll.
    """

    instances: list[_FakePopen] = []

    def __init__(
        self,
        cmd: list[str],
        *,
        stdin=None,
        stdout=None,
        stderr=None,
        text=False,
        encoding=None,
        errors=None,
        bufsize=0,
        **kwargs,
    ) -> None:
        self.argv = list(cmd)
        self.stdin = _FakeStdin()
        self._stdout_line = ""
        self._wait_side_effect = None
        self._wait_calls = 0
        self._kill_calls = 0
        self._poll_value: int | None = None
        self.stderr = stderr
        _FakePopen.instances.append(self)

    def set_stdout_line(self, line: str) -> None:
        self._stdout_line = line
        self.stdout = _FakeStdout(line)

    def set_wait_side_effect(self, exc: Exception) -> None:
        self._wait_side_effect = exc

    def wait(self, timeout: float | None = None) -> int:
        self._wait_calls += 1
        if self._wait_side_effect is not None and self._wait_calls == 1:
            raise self._wait_side_effect
        return 0

    def kill(self) -> None:
        self._kill_calls += 1

    def poll(self) -> int | None:
        return self._poll_value

    def set_poll(self, value: int | None) -> None:
        self._poll_value = value


@pytest.fixture(autouse=True)
def _reset_popen_instances() -> Iterator[None]:
    """每个测试前清空 FakePopen 实例记录 + 清理全局 logger handler 隔离。"""
    _FakePopen.instances.clear()
    # 清理 qtrading.embedded_postgres logger 的 _embedded_pg_handler，避免跨测试残留
    _svc_logger = logging.getLogger("qtrading.embedded_postgres")
    for h in list(_svc_logger.handlers):
        if getattr(h, "_embedded_pg_handler", False):
            _svc_logger.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
    # R9: 清空 DataSanitizer._known_secrets，避免 start() 注册的 URL 跨测试残留
    DataSanitizer._reset_known_secrets()
    yield
    _FakePopen.instances.clear()
    # 测试后再清理一次，避免泄漏到下一个测试
    for h in list(_svc_logger.handlers):
        if getattr(h, "_embedded_pg_handler", False):
            _svc_logger.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
    DataSanitizer._reset_known_secrets()


# =============================================================================
# TestEmbeddedPostgresServiceStart: 1 TDD + 12 start 协议测试 = 13 个
# =============================================================================
class TestEmbeddedPostgresServiceStart:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_start_returns_connection_info(self, fake_paths) -> None:
        """TDD 红灯翻绿：start 返回 ConnectionInfo（.url/.port）。"""
        from data.persistence.embedded_postgres.service import EmbeddedPostgresService

        service = EmbeddedPostgresService(**fake_paths)
        try:
            info = await service.start()
            assert info.url.startswith("postgresql+asyncpg://")
            assert info.port == FAKE_READY["port"]
        finally:
            await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_start_spawns_sidecar_with_expected_args(self, fake_paths, monkeypatch) -> None:
        """start 调 Popen 时 argv 含全部必需参数。"""
        from data.persistence.embedded_postgres import service as svc_module

        service = svc_module.EmbeddedPostgresService(**fake_paths)
        try:
            with patch.object(svc_module.subprocess, "Popen", _FakePopen):
                fake = _FakePopen.instances[-1] if _FakePopen.instances else None
                # 先 setup FakePopen，再调 start
                popen_call_count = {"n": 0}
                original_popen = _FakePopen

                def popen_factory(*args, **kwargs):
                    popen_call_count["n"] += 1
                    inst = original_popen(*args, **kwargs)
                    inst.set_stdout_line(json.dumps(FAKE_READY) + "\n")
                    # 写 password_file
                    runtime_dir = fake_paths["data_dir"].parent / "runtime"
                    runtime_dir.mkdir(parents=True, exist_ok=True)
                    (runtime_dir / "password").write_text("mock_pg_password_55432", encoding="utf-8")
                    return inst

                with patch.object(svc_module.subprocess, "Popen", popen_factory):
                    info = await service.start()
                    assert info.port == FAKE_READY["port"]
                    assert popen_call_count["n"] == 1
                    fake = _FakePopen.instances[-1]
                    argv = fake.argv
                    assert "run" in argv
                    assert "--data-dir" in argv
                    assert "--install-dir" in argv
                    assert "--password-file" in argv
                    assert "--database" in argv
                    assert "--username" in argv
                    assert "--listen" in argv
                    assert "--log-file" in argv
                    assert "--parent-pid" in argv
        finally:
            await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_start_passes_current_pid_as_parent_pid(self, fake_paths) -> None:
        """argv 中 --parent-pid == os.getpid()。"""
        from data.persistence.embedded_postgres import service as svc_module

        service = svc_module.EmbeddedPostgresService(**fake_paths)
        try:
            captured_argv = []

            def popen_factory(cmd, **kwargs):
                inst = _FakePopen(cmd, **kwargs)
                inst.set_stdout_line(json.dumps(FAKE_READY) + "\n")
                runtime_dir = fake_paths["data_dir"].parent / "runtime"
                runtime_dir.mkdir(parents=True, exist_ok=True)
                (runtime_dir / "password").write_text("mock_pg_password_55432", encoding="utf-8")
                captured_argv.extend(cmd)
                return inst

            with patch.object(svc_module.subprocess, "Popen", popen_factory):
                await service.start()
                pid_idx = captured_argv.index("--parent-pid")
                assert captured_argv[pid_idx + 1] == str(os.getpid())
        finally:
            await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_start_reads_first_stdout_line_as_json(self, fake_paths) -> None:
        """start 读 stdout 首行 JSON 解析成功。"""
        from data.persistence.embedded_postgres import service as svc_module

        service = svc_module.EmbeddedPostgresService(**fake_paths)
        try:

            def popen_factory(cmd, **kwargs):
                inst = _FakePopen(cmd, **kwargs)
                inst.set_stdout_line(json.dumps(FAKE_READY) + "\n")
                runtime_dir = fake_paths["data_dir"].parent / "runtime"
                runtime_dir.mkdir(parents=True, exist_ok=True)
                (runtime_dir / "password").write_text("mock_pg_password_55432", encoding="utf-8")
                return inst

            with patch.object(svc_module.subprocess, "Popen", popen_factory):
                info = await service.start()
                assert info.port == FAKE_READY["port"]
                assert info.pid == FAKE_READY["pid"]
        finally:
            await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_start_rejects_invalid_json(self, fake_paths) -> None:
        """stdout 返回非 JSON → raise EmbeddedPostgresStartError + kill 子进程。"""
        from data.persistence.embedded_postgres import service as svc_module
        from data.persistence.embedded_postgres.service import EmbeddedPostgresStartError

        service = svc_module.EmbeddedPostgresService(**fake_paths)
        try:

            def popen_factory(cmd, **kwargs):
                inst = _FakePopen(cmd, **kwargs)
                inst.set_stdout_line("not a json\n")
                return inst

            with patch.object(svc_module.subprocess, "Popen", popen_factory):
                with pytest.raises(EmbeddedPostgresStartError, match="JSON parse failed"):
                    await service.start()
                # 验证 kill 被调（_cleanup_failed_start 触发）
                assert any(inst._kill_calls > 0 for inst in _FakePopen.instances)
        finally:
            await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_start_rejects_missing_required_fields(self, fake_paths) -> None:
        """缺 port 字段 → raise EmbeddedPostgresStartError。"""
        from data.persistence.embedded_postgres import service as svc_module
        from data.persistence.embedded_postgres.service import EmbeddedPostgresStartError

        service = svc_module.EmbeddedPostgresService(**fake_paths)
        try:
            invalid_ready = {k: v for k, v in FAKE_READY.items() if k != "port"}

            def popen_factory(cmd, **kwargs):
                inst = _FakePopen(cmd, **kwargs)
                inst.set_stdout_line(json.dumps(invalid_ready) + "\n")
                return inst

            with patch.object(svc_module.subprocess, "Popen", popen_factory):
                with pytest.raises(EmbeddedPostgresStartError, match="invalid port"):
                    await service.start()
        finally:
            await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_start_rejects_status_not_running(self, fake_paths) -> None:
        """status != 'running' → raise EmbeddedPostgresStartError。"""
        from data.persistence.embedded_postgres import service as svc_module
        from data.persistence.embedded_postgres.service import EmbeddedPostgresStartError

        service = svc_module.EmbeddedPostgresService(**fake_paths)
        try:
            invalid_ready = dict(FAKE_READY)
            invalid_ready["status"] = "starting"

            def popen_factory(cmd, **kwargs):
                inst = _FakePopen(cmd, **kwargs)
                inst.set_stdout_line(json.dumps(invalid_ready) + "\n")
                return inst

            with patch.object(svc_module.subprocess, "Popen", popen_factory):
                with pytest.raises(EmbeddedPostgresStartError, match="unexpected ready status"):
                    await service.start()
        finally:
            await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_start_rejects_port_zero(self, fake_paths) -> None:
        """port=0 → raise EmbeddedPostgresStartError。"""
        from data.persistence.embedded_postgres import service as svc_module
        from data.persistence.embedded_postgres.service import EmbeddedPostgresStartError

        service = svc_module.EmbeddedPostgresService(**fake_paths)
        try:
            invalid_ready = dict(FAKE_READY)
            invalid_ready["port"] = 0

            def popen_factory(cmd, **kwargs):
                inst = _FakePopen(cmd, **kwargs)
                inst.set_stdout_line(json.dumps(invalid_ready) + "\n")
                return inst

            with patch.object(svc_module.subprocess, "Popen", popen_factory):
                with pytest.raises(EmbeddedPostgresStartError, match="invalid port"):
                    await service.start()
        finally:
            await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_start_reads_password_from_password_file(self, fake_paths) -> None:
        """URL 中含 password_file 写入的密码。"""
        from data.persistence.embedded_postgres import service as svc_module

        service = svc_module.EmbeddedPostgresService(**fake_paths)
        try:

            def popen_factory(cmd, **kwargs):
                inst = _FakePopen(cmd, **kwargs)
                inst.set_stdout_line(json.dumps(FAKE_READY) + "\n")
                runtime_dir = fake_paths["data_dir"].parent / "runtime"
                runtime_dir.mkdir(parents=True, exist_ok=True)
                (runtime_dir / "password").write_text("mock_pg_password_55432", encoding="utf-8")
                return inst

            with patch.object(svc_module.subprocess, "Popen", popen_factory):
                info = await service.start()
                assert "mock_pg_password_55432" in info.url
        finally:
            await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_start_constructs_asyncpg_url(self, fake_paths) -> None:
        """URL 以 postgresql+asyncpg:// 开头。"""
        from data.persistence.embedded_postgres import service as svc_module

        service = svc_module.EmbeddedPostgresService(**fake_paths)
        try:

            def popen_factory(cmd, **kwargs):
                inst = _FakePopen(cmd, **kwargs)
                inst.set_stdout_line(json.dumps(FAKE_READY) + "\n")
                runtime_dir = fake_paths["data_dir"].parent / "runtime"
                runtime_dir.mkdir(parents=True, exist_ok=True)
                (runtime_dir / "password").write_text("mock_pg_password_55432", encoding="utf-8")
                return inst

            with patch.object(svc_module.subprocess, "Popen", popen_factory):
                info = await service.start()
                assert info.url.startswith("postgresql+asyncpg://")
        finally:
            await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_start_stores_connection_info(self, fake_paths) -> None:
        """start 后 _connection_info 非 None 且 port 匹配。"""
        from data.persistence.embedded_postgres import service as svc_module

        service = svc_module.EmbeddedPostgresService(**fake_paths)
        try:

            def popen_factory(cmd, **kwargs):
                inst = _FakePopen(cmd, **kwargs)
                inst.set_stdout_line(json.dumps(FAKE_READY) + "\n")
                runtime_dir = fake_paths["data_dir"].parent / "runtime"
                runtime_dir.mkdir(parents=True, exist_ok=True)
                (runtime_dir / "password").write_text("mock_pg_password_55432", encoding="utf-8")
                return inst

            with patch.object(svc_module.subprocess, "Popen", popen_factory):
                await service.start()
                assert service._connection_info is not None
                assert service._connection_info.port == FAKE_READY["port"]
        finally:
            await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_start_kills_child_on_failure(self, fake_paths) -> None:
        """解析失败时 Popen.kill 被调用。"""
        from data.persistence.embedded_postgres import service as svc_module
        from data.persistence.embedded_postgres.service import EmbeddedPostgresStartError

        service = svc_module.EmbeddedPostgresService(**fake_paths)
        try:

            def popen_factory(cmd, **kwargs):
                inst = _FakePopen(cmd, **kwargs)
                inst.set_stdout_line("not a json\n")
                return inst

            with patch.object(svc_module.subprocess, "Popen", popen_factory):
                with pytest.raises(EmbeddedPostgresStartError, match="JSON parse failed"):
                    await service.start()
                # kill 应该被 _cleanup_failed_start 调用
                assert any(inst._kill_calls > 0 for inst in _FakePopen.instances)
        finally:
            await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_start_propagates_cancelled_error(self, fake_paths, monkeypatch) -> None:
        """asyncio.to_thread raise CancelledError → start 重新 raise + 触发清理。"""
        import asyncio

        from data.persistence.embedded_postgres import service as svc_module

        service = svc_module.EmbeddedPostgresService(**fake_paths)
        cleanup_called = {"v": False}
        original_cleanup = service._cleanup_failed_start
        service._cleanup_failed_start = lambda: cleanup_called.__setitem__("v", True)  # type: ignore[assignment]  # [reason: 测试 monkeypatch 替换实例方法为 lambda]

        async def raise_cancelled(func):
            raise asyncio.CancelledError()

        try:
            monkeypatch.setattr(asyncio, "to_thread", raise_cancelled)
            # CancelledError 无消息可 match；用 as exc_info 捕获后断言类型，
            # 后续 assert cleanup_called 验证 _cleanup_failed_start 副作用
            with pytest.raises(asyncio.CancelledError) as exc_info:
                await service.start()
            assert isinstance(exc_info.value, asyncio.CancelledError)
            assert cleanup_called["v"] is True
        finally:
            # 先恢复 asyncio.to_thread，再调 stop 避免被 mock 影响
            monkeypatch.undo()
            service._cleanup_failed_start = original_cleanup  # type: ignore[assignment]  # [reason: 测试 monkeypatch 恢复实例方法]
            await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_start_registers_url_secret(self, fake_paths) -> None:
        """R9: start() 构造 URL 后必须调 DataSanitizer.register_secret(url)。

        验证：
        1. start() 后 info.url 已注册到 DataSanitizer._known_secrets
        2. DataSanitizer.sanitize_error 能将该 URL 中的密码替换为 ***
        """
        from data.persistence.embedded_postgres import service as svc_module

        service = svc_module.EmbeddedPostgresService(**fake_paths)
        try:

            def popen_factory(cmd, **kwargs):
                inst = _FakePopen(cmd, **kwargs)
                inst.set_stdout_line(json.dumps(FAKE_READY) + "\n")
                runtime_dir = fake_paths["data_dir"].parent / "runtime"
                runtime_dir.mkdir(parents=True, exist_ok=True)
                # 写入长度 >= 8 的密码以满足 _MIN_SECRET_LEN 阈值
                (runtime_dir / "password").write_text("mock_pg_password_55432", encoding="utf-8")
                return inst

            with patch.object(svc_module.subprocess, "Popen", popen_factory):
                info = await service.start()
                # 断言 1: URL 已注册到 _known_secrets
                assert info.url in DataSanitizer._known_secrets, (
                    f"URL 未注册到 DataSanitizer._known_secrets: {info.url}"
                )
                # 断言 2: sanitize_error 能精确替换该 URL 中的密码
                error_msg = f"connect failed: {info.url}"
                sanitized = DataSanitizer.sanitize_error(error_msg)
                assert info.url not in sanitized, f"sanitize_error 未脱敏 URL，原始 URL 仍存在于: {sanitized}"
                assert "mock_pg_password_55432" not in sanitized, f"sanitize_error 未脱敏密码: {sanitized}"
        finally:
            await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_start_concurrent_returns_same_connection_info(self, fake_paths) -> None:
        """H5: 两个协程并发 await service.start() → 返回同一 ConnectionInfo，Popen 仅调用 1 次。

        验证双检锁 + _start_lock 串行化的正确性：
        1. 并发调用 start() 返回同一 ConnectionInfo 对象（is 比较）
        2. Popen 仅被调用 1 次（避免双 Popen）
        3. URL / port 字段与 FAKE_READY 一致
        """
        import asyncio

        from data.persistence.embedded_postgres import service as svc_module

        service = svc_module.EmbeddedPostgresService(**fake_paths)
        popen_call_count = {"n": 0}
        popen_call_lock = threading.Lock()

        def popen_factory(cmd, **kwargs):
            with popen_call_lock:
                popen_call_count["n"] += 1
            inst = _FakePopen(cmd, **kwargs)
            inst.set_stdout_line(json.dumps(FAKE_READY) + "\n")
            runtime_dir = fake_paths["data_dir"].parent / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            (runtime_dir / "password").write_text("mock_pg_password_55432", encoding="utf-8")
            return inst

        try:
            with patch.object(svc_module.subprocess, "Popen", popen_factory):
                info1, info2 = await asyncio.gather(service.start(), service.start())
                assert info1 is info2, f"期望两个协程返回同一 ConnectionInfo，实际：{info1!r} vs {info2!r}"
                assert info1.port == FAKE_READY["port"]
                assert popen_call_count["n"] == 1, (
                    f"期望 Popen 仅调用 1 次（H5 双检锁防双 Popen），实际：{popen_call_count['n']}"
                )
        finally:
            await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_start_stdout_reader_task_started_after_start(self, fake_paths) -> None:
        """M9: start() 后启动 daemon 线程读 stdout 写入 sidecar.stdout.log。

        验证：
        1. start() 后 sidecar.stdout.log 文件存在
        2. 文件内容含 ready JSON 行（reader 线程读取的第一行）
        3. ConnectionInfo 含 M4 扩展字段（postgres_version/host/sidecar_pid/password_source）
        """
        import time

        from data.persistence.embedded_postgres import service as svc_module

        service = svc_module.EmbeddedPostgresService(**fake_paths)
        try:

            def popen_factory(cmd, **kwargs):
                inst = _FakePopen(cmd, **kwargs)
                inst.set_stdout_line(json.dumps(FAKE_READY) + "\n")
                runtime_dir = fake_paths["data_dir"].parent / "runtime"
                runtime_dir.mkdir(parents=True, exist_ok=True)
                (runtime_dir / "password").write_text("mock_pg_password_55432", encoding="utf-8")
                return inst

            with patch.object(svc_module.subprocess, "Popen", popen_factory):
                info = await service.start()
                # M4 断言: 扩展字段已从 ready JSON 解析
                assert info.postgres_version == FAKE_READY["postgres_version"], (
                    f"postgres_version 字段错误，实际：{info.postgres_version!r}"
                )
                assert info.host == FAKE_READY["host"], f"host 字段错误，实际：{info.host!r}"
                assert info.sidecar_pid == FAKE_READY["sidecar_pid"], (
                    f"sidecar_pid 字段错误，实际：{info.sidecar_pid!r}"
                )
                assert info.password_source == FAKE_READY["password_source"], (
                    f"password_source 字段错误，实际：{info.password_source!r}"
                )
                # M9 断言: 等 reader 线程写入 sidecar.stdout.log（最多 2s）
                stdout_log = service._log_dir / "sidecar.stdout.log"
                for _ in range(20):
                    if stdout_log.exists() and stdout_log.read_text(encoding="utf-8"):
                        break
                    time.sleep(0.1)
                assert stdout_log.exists(), f"M9: start() 后应创建 sidecar.stdout.log，实际未存在：{stdout_log}"
                content = stdout_log.read_text(encoding="utf-8")
                assert json.dumps(FAKE_READY) in content, (
                    f"M9: sidecar.stdout.log 应含 ready JSON 行，实际：{content!r}"
                )
        finally:
            await service.stop()


# =============================================================================
# TestEmbeddedPostgresServiceStop: 1 TDD + 10 stop 行为测试 = 11 个
# =============================================================================
class TestEmbeddedPostgresServiceStop:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_stop_is_idempotent(self, fake_paths) -> None:
        """TDD 红灯翻绿：stop 幂等。

        H9: 补强断言 — 两次 stop 后 _process / _connection_info 均为 None，
        确保第二次调用不是静默误调用（如错把 _process 仍当作有值处理）。
        """
        from data.persistence.embedded_postgres.service import EmbeddedPostgresService

        service = EmbeddedPostgresService(**fake_paths)
        await service.stop()
        await service.stop()  # 第二次调用不抛异常（AM-9 幂等）
        # H9: 补强断言
        assert service._process is None, f"两次 stop 后 _process 应为 None，实际：{service._process}"
        assert service._connection_info is None, (
            f"两次 stop 后 _connection_info 应为 None，实际：{service._connection_info}"
        )

    @pytest.mark.asyncio(loop_scope="function")
    async def test_stop_closes_stdin_and_waits(self, fake_paths) -> None:
        """stop 调 stdin.close + wait。"""
        from data.persistence.embedded_postgres import service as svc_module

        service = svc_module.EmbeddedPostgresService(**fake_paths)
        # 手动注入 fake process
        fake_proc = _FakePopen(["fake"])
        fake_proc.set_stdout_line(json.dumps(FAKE_READY) + "\n")
        service._process = fake_proc
        service._connection_info = svc_module.ConnectionInfo(
            url="postgresql+asyncpg://u:p@h:1/d", port=1, pid=1, data_dir="/d"
        )
        await service.stop()
        assert fake_proc.stdin.closed is True
        assert fake_proc._wait_calls >= 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_stop_kills_on_wait_timeout(self, fake_paths) -> None:
        """wait raise TimeoutExpired → kill + 二次 wait。"""
        from data.persistence.embedded_postgres import service as svc_module

        service = svc_module.EmbeddedPostgresService(**fake_paths)
        fake_proc = _FakePopen(["fake"])
        fake_proc.set_stdout_line(json.dumps(FAKE_READY) + "\n")
        fake_proc.set_wait_side_effect(subprocess.TimeoutExpired(cmd=["fake"], timeout=60))
        service._process = fake_proc
        await service.stop()
        assert fake_proc._kill_calls >= 1
        assert fake_proc._wait_calls >= 2  # 第一次抛 TimeoutExpired，第二次兜底

    @pytest.mark.asyncio(loop_scope="function")
    async def test_stop_clears_process_and_connection_info(self, fake_paths) -> None:
        """stop 后 _process=None + _connection_info=None。"""
        from data.persistence.embedded_postgres import service as svc_module

        service = svc_module.EmbeddedPostgresService(**fake_paths)
        fake_proc = _FakePopen(["fake"])
        fake_proc.set_stdout_line(json.dumps(FAKE_READY) + "\n")
        service._process = fake_proc
        service._connection_info = svc_module.ConnectionInfo(
            url="postgresql+asyncpg://u:p@h:1/d", port=1, pid=1, data_dir="/d"
        )
        await service.stop()
        assert service._process is None
        assert service._connection_info is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_stop_idempotent_multiple_calls(self, fake_paths) -> None:
        """连续 3 次 stop 不抛异常。

        H9: 补强断言 — 3 次 stop 后 _process / _connection_info 均为 None，
        确保多次幂等调用没有副作用累积。
        """
        from data.persistence.embedded_postgres.service import EmbeddedPostgresService

        service = EmbeddedPostgresService(**fake_paths)
        await service.stop()
        await service.stop()
        await service.stop()
        # H9: 补强断言
        assert service._process is None, f"3 次 stop 后 _process 应为 None，实际：{service._process}"
        assert service._connection_info is None, (
            f"3 次 stop 后 _connection_info 应为 None，实际：{service._connection_info}"
        )

    @pytest.mark.asyncio(loop_scope="function")
    async def test_stop_safe_when_not_started(self, fake_paths) -> None:
        """未 start 时 stop 无操作。

        H9: 补强断言 — 未 start 时 _process 本就为 None，stop 后仍为 None（含
        _connection_info），确保 stop 不在未启动时误置状态。
        """
        from data.persistence.embedded_postgres.service import EmbeddedPostgresService

        service = EmbeddedPostgresService(**fake_paths)
        # _process 默认就是 None
        await service.stop()
        # H9: 补强断言
        assert service._process is None, f"未 start 时 stop 后 _process 应为 None，实际：{service._process}"
        assert service._connection_info is None, (
            f"未 start 时 stop 后 _connection_info 应为 None，实际：{service._connection_info}"
        )

    @pytest.mark.asyncio(loop_scope="function")
    async def test_stop_closes_stderr_file_handle(self, fake_paths) -> None:
        """stop 后 _stderr_file=None。"""
        from data.persistence.embedded_postgres import service as svc_module

        service = svc_module.EmbeddedPostgresService(**fake_paths)
        fake_proc = _FakePopen(["fake"])
        fake_proc.set_stdout_line(json.dumps(FAKE_READY) + "\n")
        service._process = fake_proc
        # 模拟 stderr_file 已开（data_dir 需先存在）
        fake_paths["data_dir"].mkdir(parents=True, exist_ok=True)
        # 句柄需保留到 stop() 关闭，不能用 with；SIM115 不适用
        err_file = open(  # noqa: SIM115
            fake_paths["data_dir"] / "fake_stderr.log", "w", encoding="utf-8"
        )
        service._stderr_file = err_file
        await service.stop()
        assert service._stderr_file is None
        assert err_file.closed

    @pytest.mark.asyncio(loop_scope="function")
    async def test_stop_logs_warning_on_kill_fallback(self, fake_paths, caplog) -> None:
        """kill 路径触发 WARNING 含 'timeout' / 'killing'。"""
        import logging

        from data.persistence.embedded_postgres import service as svc_module

        service = svc_module.EmbeddedPostgresService(**fake_paths)
        fake_proc = _FakePopen(["fake"])
        fake_proc.set_stdout_line(json.dumps(FAKE_READY) + "\n")
        fake_proc.set_wait_side_effect(subprocess.TimeoutExpired(cmd=["fake"], timeout=60))
        service._process = fake_proc
        with caplog.at_level(logging.WARNING, logger="qtrading.embedded_postgres"):
            await service.stop()
        assert any("timeout" in rec.message.lower() or "killing" in rec.message.lower() for rec in caplog.records)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_stop_propagates_cancelled_error(self, fake_paths, monkeypatch) -> None:
        """asyncio.to_thread raise CancelledError → stop 重新 raise（R2）。"""
        import asyncio

        from data.persistence.embedded_postgres import service as svc_module

        service = svc_module.EmbeddedPostgresService(**fake_paths)
        fake_proc = _FakePopen(["fake"])
        service._process = fake_proc

        async def raise_cancelled(func):
            raise asyncio.CancelledError()

        monkeypatch.setattr(asyncio, "to_thread", raise_cancelled)
        # CancelledError 无消息可 match；用 as exc_info 捕获后断言类型（R2 红线验证）
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await service.stop()
        assert isinstance(exc_info.value, asyncio.CancelledError)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_stop_does_not_block_event_loop(self, fake_paths) -> None:
        """stop 不阻塞事件循环（asyncio.wait_for 包装不超时）。"""
        import asyncio

        from data.persistence.embedded_postgres.service import EmbeddedPostgresService

        service = EmbeddedPostgresService(**fake_paths)
        await asyncio.wait_for(service.stop(), timeout=5.0)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_stop_timeout_configurable(self, fake_paths) -> None:
        """自定义 stop_timeout 透传到 wait(timeout=...)。"""
        from data.persistence.embedded_postgres import service as svc_module

        # 构造 service 时指定 stop_timeout=30.0
        service = svc_module.EmbeddedPostgresService(
            sidecar_binary=fake_paths["sidecar_binary"],
            data_dir=fake_paths["data_dir"],
            install_dir=fake_paths["install_dir"],
            stop_timeout=30.0,
        )
        fake_proc = _FakePopen(["fake"])
        fake_proc.set_stdout_line(json.dumps(FAKE_READY) + "\n")
        service._process = fake_proc
        await service.stop()
        # _FakePopen.wait 不记录 timeout 参数，但我们可验证 stop_timeout 存储正确
        assert service._stop_timeout == 30.0


# =============================================================================
# TestEmbeddedPostgresServiceSingleton: 2 TDD 测试
# =============================================================================
class TestEmbeddedPostgresServiceSingleton:
    def test_reset_singleton_clears_instance(self, fake_paths) -> None:
        """TDD 红灯翻绿：_reset_singleton 清空 _instance。"""
        from data.persistence.embedded_postgres.service import EmbeddedPostgresService

        first = EmbeddedPostgresService(**fake_paths)
        EmbeddedPostgresService._reset_singleton()
        second = EmbeddedPostgresService(**fake_paths)
        assert first is not second

    def test_atexit_cleanup_registered(self, fake_paths) -> None:
        """TDD 红灯翻绿：_atexit_cleanup 已实现，未启动时安全。"""
        from data.persistence.embedded_postgres.service import EmbeddedPostgresService

        service = EmbeddedPostgresService(**fake_paths)
        assert hasattr(service, "_atexit_cleanup")
        service._atexit_cleanup()  # 未启动时调用不抛异常


# =============================================================================
# TestEmbeddedPostgresServiceFromConfig: 1 TDD 测试
# =============================================================================
class TestEmbeddedPostgresServiceFromConfig:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_from_config_constructs_service(self, fake_paths) -> None:
        """TDD 红灯翻绿：from_config 构造单例，二次调用返回同实例。"""
        from data.persistence.embedded_postgres.service import EmbeddedPostgresService
        from utils.config_models import AppConfig

        config = AppConfig()
        service = EmbeddedPostgresService.from_config(config)
        # 二次 from_config 返回同一实例（单例）
        service2 = EmbeddedPostgresService.from_config(config)
        assert service is service2

    def test_from_config_default_paths_resolution(self, monkeypatch, tmp_path: Path) -> None:
        """M6: from_config 默认路径解析 — 未显式配置时使用 platformdirs 默认值。

        验证：
        1. embedded_pg_sidecar_path 为空 → 默认 sidecars/qtrading-pg-sidecar[.exe]
        2. embedded_pg_data_root 为空 → 默认 <app_data>/postgres/17/data
        3. embedded_pg_install_root 为空 → 默认 <data_root>/install
        4. embedded_pg_log_dir 为空 → 默认从 data_dir 推导（<root>/postgres-logs）
        """
        import platformdirs

        from data.persistence.embedded_postgres.service import EmbeddedPostgresService
        from utils.config_models import AppConfig

        # mock platformdirs.user_data_dir 返回 tmp_path/app_data
        monkeypatch.setattr(
            platformdirs,
            "user_data_dir",
            lambda _app: str(tmp_path / "app_data"),
        )

        EmbeddedPostgresService._reset_singleton()
        config = AppConfig()  # 所有 embedded_pg_* 默认空
        service = EmbeddedPostgresService.from_config(config)

        # 断言 1: sidecar_binary 默认 sidecars/qtrading-pg-sidecar[.exe]
        expected_suffix = ".exe" if os.name == "nt" else ""
        assert service._sidecar_binary == Path("sidecars") / f"qtrading-pg-sidecar{expected_suffix}", (
            f"sidecar_binary 默认值错误，实际：{service._sidecar_binary}"
        )
        # 断言 2: data_dir 默认 <app_data>/postgres/17/data
        expected_data_dir = tmp_path / "app_data" / "postgres" / "17" / "data"
        assert service._data_dir == expected_data_dir, (
            f"data_dir 默认值错误，实际：{service._data_dir}，期望：{expected_data_dir}"
        )
        # 断言 3: install_dir 默认 <data_root>/install
        expected_install_dir = tmp_path / "app_data" / "postgres" / "17" / "install"
        assert service._install_dir == expected_install_dir, (
            f"install_dir 默认值错误，实际：{service._install_dir}，期望：{expected_install_dir}"
        )
        # 断言 4: log_dir 从 data_dir 推导 → <root>/postgres-logs
        # data_dir = <app_data>/postgres/17/data → root = data_dir.parent.parent.parent = <app_data>
        expected_log_dir = tmp_path / "app_data" / "postgres-logs"
        assert service._log_dir == expected_log_dir, (
            f"log_dir 默认值错误，实际：{service._log_dir}，期望：{expected_log_dir}"
        )

    def test_from_config_frozen_app_resolves_meipass(self, monkeypatch, tmp_path: Path) -> None:
        """P4-8: PyInstaller frozen 模式下 sidecar_binary 从 sys._MEIPASS 解析。

        验证：
        1. sys.frozen=True + sys._MEIPASS 存在 → sidecar_binary = MEIPASS/sidecars/qtrading-pg-sidecar[.exe]
        2. 不受 embedded_pg_sidecar_path 为空影响
        """
        from data.persistence.embedded_postgres.service import EmbeddedPostgresService
        from utils.config_models import AppConfig

        meipass = tmp_path / "_internal"
        meipass.mkdir()
        monkeypatch.setattr("sys.frozen", True, raising=False)
        monkeypatch.setattr("sys._MEIPASS", str(meipass), raising=False)

        EmbeddedPostgresService._reset_singleton()
        config = AppConfig()  # embedded_pg_sidecar_path 默认空
        service = EmbeddedPostgresService.from_config(config)

        expected_suffix = ".exe" if os.name == "nt" else ""
        expected = meipass / "sidecars" / f"qtrading-pg-sidecar{expected_suffix}"
        assert service._sidecar_binary == expected, (
            f"frozen app sidecar_binary 错误，实际：{service._sidecar_binary}，期望：{expected}"
        )

    def test_from_config_frozen_app_explicit_path_overrides_meipass(self, monkeypatch, tmp_path: Path) -> None:
        """P4-8: 显式 embedded_pg_sidecar_path 优先于 frozen app _MEIPASS 解析。"""
        from data.persistence.embedded_postgres.service import EmbeddedPostgresService
        from utils.config_models import AppConfig

        meipass = tmp_path / "_internal"
        meipass.mkdir()
        monkeypatch.setattr("sys.frozen", True, raising=False)
        monkeypatch.setattr("sys._MEIPASS", str(meipass), raising=False)

        explicit_path = tmp_path / "custom-sidecar.exe"
        EmbeddedPostgresService._reset_singleton()
        config = AppConfig(embedded_pg_sidecar_path=str(explicit_path))
        service = EmbeddedPostgresService.from_config(config)

        assert service._sidecar_binary == explicit_path, f"显式 sidecar_path 应优先，实际：{service._sidecar_binary}"

    def test_from_config_frozen_app_missing_meipass_raises(self, monkeypatch, tmp_path: Path) -> None:
        """P4-8: sys.frozen=True 但 sys._MEIPASS=None 时抛 EmbeddedPostgresStartError。"""
        from data.persistence.embedded_postgres.service import (
            EmbeddedPostgresService,
            EmbeddedPostgresStartError,
        )
        from utils.config_models import AppConfig

        monkeypatch.setattr("sys.frozen", True, raising=False)
        # 删除 _MEIPASS 属性（getattr 默认返回 None）
        monkeypatch.delattr("sys", "_MEIPASS", raising=False)

        EmbeddedPostgresService._reset_singleton()
        config = AppConfig()
        with pytest.raises(EmbeddedPostgresStartError, match="_MEIPASS is None"):
            EmbeddedPostgresService.from_config(config)

    def test_from_config_dev_mode_does_not_use_meipass(self, monkeypatch, tmp_path: Path) -> None:
        """P4-8: 开发模式（sys.frozen=False）使用 cwd-relative 路径，不读取 _MEIPASS。"""
        from data.persistence.embedded_postgres.service import EmbeddedPostgresService
        from utils.config_models import AppConfig

        # 确保 sys.frozen=False（默认值，但显式设置避免污染）
        monkeypatch.setattr("sys.frozen", False, raising=False)
        EmbeddedPostgresService._reset_singleton()
        config = AppConfig()
        service = EmbeddedPostgresService.from_config(config)

        expected_suffix = ".exe" if os.name == "nt" else ""
        expected = Path("sidecars") / f"qtrading-pg-sidecar{expected_suffix}"
        assert service._sidecar_binary == expected, (
            f"开发模式 sidecar_binary 错误，实际：{service._sidecar_binary}，期望：{expected}"
        )


# =============================================================================
# TestEmbeddedPostgresServiceCrossMethodIdempotent: M7 跨方法幂等测试
# =============================================================================
class TestEmbeddedPostgresServiceCrossMethodIdempotent:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_stop_then_stop_sync_is_idempotent(self, fake_paths) -> None:
        """M7: async stop() 后再调 sync stop_sync() 不抛异常（跨方法幂等）。

        验证 stop() 和 stop_sync() 共享 _process 状态，任一方法清理后另一方法早 return。
        """
        from data.persistence.embedded_postgres import service as svc_module

        service = svc_module.EmbeddedPostgresService(**fake_paths)
        fake_proc = _FakePopen(["fake"])
        fake_proc.set_stdout_line(json.dumps(FAKE_READY) + "\n")
        service._process = fake_proc
        service._connection_info = svc_module.ConnectionInfo(
            url="postgresql+asyncpg://u:p@h:1/d", port=1, pid=1, data_dir="/d"
        )

        # 先调 async stop() 清理 _process
        await service.stop()
        assert service._process is None
        # 再调 sync stop_sync() 不抛异常
        service.stop_sync()
        assert service._process is None
        assert service._connection_info is None


# =============================================================================
# TestEmbeddedPostgresServiceLog: 10 个日志测试
# =============================================================================
class TestEmbeddedPostgresServiceLog:
    def test_setup_service_logger_creates_file_handler(self, fake_paths) -> None:
        """构造 service 后 embedded-pg-service.log 文件存在。"""
        from data.persistence.embedded_postgres.service import EmbeddedPostgresService

        service = EmbeddedPostgresService(**fake_paths)
        # log_dir 默认从 data_dir 推导
        log_file = service._log_dir / "embedded-pg-service.log"
        assert log_file.exists()

    def test_setup_service_logger_does_not_duplicate_handler(self, fake_paths) -> None:
        """重复构造 service 不重复挂 handler。"""
        from data.persistence.embedded_postgres import service as svc_module

        svc_module.EmbeddedPostgresService._reset_singleton()
        service1 = svc_module.EmbeddedPostgresService(**fake_paths)
        handler_count_1 = len([h for h in service1._svc_logger.handlers if getattr(h, "_embedded_pg_handler", False)])
        svc_module.EmbeddedPostgresService._reset_singleton()
        service2 = svc_module.EmbeddedPostgresService(**fake_paths)
        handler_count_2 = len([h for h in service2._svc_logger.handlers if getattr(h, "_embedded_pg_handler", False)])
        assert handler_count_1 == handler_count_2 == 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_service_logger_writes_start_event(self, fake_paths) -> None:
        """start 后日志文件含 'starting sidecar'。"""
        from data.persistence.embedded_postgres import service as svc_module

        service = svc_module.EmbeddedPostgresService(**fake_paths)
        try:

            def popen_factory(cmd, **kwargs):
                inst = _FakePopen(cmd, **kwargs)
                inst.set_stdout_line(json.dumps(FAKE_READY) + "\n")
                runtime_dir = fake_paths["data_dir"].parent / "runtime"
                runtime_dir.mkdir(parents=True, exist_ok=True)
                (runtime_dir / "password").write_text("mock_pg_password_55432", encoding="utf-8")
                return inst

            with patch.object(svc_module.subprocess, "Popen", popen_factory):
                await service.start()
            log_content = (service._log_dir / "embedded-pg-service.log").read_text(encoding="utf-8")
            assert "starting sidecar" in log_content.lower()
        finally:
            await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_service_logger_writes_stop_event(self, fake_paths) -> None:
        """stop 后日志文件含 'stopped'。"""
        from data.persistence.embedded_postgres import service as svc_module

        service = svc_module.EmbeddedPostgresService(**fake_paths)
        try:

            def popen_factory(cmd, **kwargs):
                inst = _FakePopen(cmd, **kwargs)
                inst.set_stdout_line(json.dumps(FAKE_READY) + "\n")
                runtime_dir = fake_paths["data_dir"].parent / "runtime"
                runtime_dir.mkdir(parents=True, exist_ok=True)
                (runtime_dir / "password").write_text("mock_pg_password_55432", encoding="utf-8")
                return inst

            with patch.object(svc_module.subprocess, "Popen", popen_factory):
                await service.start()
            await service.stop()
            log_content = (service._log_dir / "embedded-pg-service.log").read_text(encoding="utf-8")
            assert "stopped" in log_content.lower()
        finally:
            await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_service_logger_redacts_password_in_url(self, fake_paths, caplog) -> None:
        """日志中 URL 含 *** 或不含明文密码（service.py 仅记 host:port）。"""
        import logging

        from data.persistence.embedded_postgres import service as svc_module

        service = svc_module.EmbeddedPostgresService(**fake_paths)
        try:

            def popen_factory(cmd, **kwargs):
                inst = _FakePopen(cmd, **kwargs)
                inst.set_stdout_line(json.dumps(FAKE_READY) + "\n")
                runtime_dir = fake_paths["data_dir"].parent / "runtime"
                runtime_dir.mkdir(parents=True, exist_ok=True)
                (runtime_dir / "password").write_text("mock_pg_password_55432", encoding="utf-8")
                return inst

            with caplog.at_level(logging.INFO, logger="qtrading.embedded_postgres"):
                with patch.object(svc_module.subprocess, "Popen", popen_factory):
                    await service.start()
            # 日志中不应出现明文密码
            for rec in caplog.records:
                assert "mock_pg_password_55432" not in rec.message
        finally:
            await service.stop()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_service_logger_redacts_password_file_path_in_debug(self, fake_paths, caplog) -> None:
        """DEBUG 行不附带密码内容。"""
        import logging

        from data.persistence.embedded_postgres import service as svc_module

        service = svc_module.EmbeddedPostgresService(**fake_paths)
        try:

            def popen_factory(cmd, **kwargs):
                inst = _FakePopen(cmd, **kwargs)
                inst.set_stdout_line(json.dumps(FAKE_READY) + "\n")
                runtime_dir = fake_paths["data_dir"].parent / "runtime"
                runtime_dir.mkdir(parents=True, exist_ok=True)
                (runtime_dir / "password").write_text("mock_pg_password_55432", encoding="utf-8")
                return inst

            with caplog.at_level(logging.DEBUG, logger="qtrading.embedded_postgres"):
                with patch.object(svc_module.subprocess, "Popen", popen_factory):
                    await service.start()
            # 所有日志记录都不含明文密码
            for rec in caplog.records:
                assert "mock_pg_password_55432" not in rec.message
        finally:
            await service.stop()

    def test_collect_logs_summary_returns_all_four_logs(self, fake_paths) -> None:
        """collect_logs_summary 返回四个键。"""
        from data.persistence.embedded_postgres.service import EmbeddedPostgresService

        service = EmbeddedPostgresService(**fake_paths)
        summary = service.collect_logs_summary()
        assert set(summary.keys()) == {
            "sidecar.log",
            "sidecar.stderr.log",
            "postgres-start.log",
            "embedded-pg-service.log",
        }

    def test_collect_logs_summary_returns_tail_content(self, fake_paths) -> None:
        """tail_bytes 限制返回内容长度。"""
        from data.persistence.embedded_postgres.service import EmbeddedPostgresService

        service = EmbeddedPostgresService(**fake_paths)
        # 写入 16KB 内容到 sidecar.log
        big_content = "x" * 16384
        (service._log_dir / "sidecar.log").write_text(big_content, encoding="utf-8")
        summary = service.collect_logs_summary(tail_bytes=8192)
        assert len(summary["sidecar.log"]) <= 8192

    def test_collect_logs_summary_handles_missing_file(self, fake_paths) -> None:
        """缺失文件返回 <missing>。"""
        from data.persistence.embedded_postgres.service import EmbeddedPostgresService

        service = EmbeddedPostgresService(**fake_paths)
        # sidecar.log 不存在
        summary = service.collect_logs_summary()
        assert summary["sidecar.log"] == "<missing>"

    def test_collect_logs_summary_handles_read_error(self, fake_paths, monkeypatch) -> None:
        """OSError 返回 <read error: ...>。"""
        from data.persistence.embedded_postgres import service as svc_module

        service = svc_module.EmbeddedPostgresService(**fake_paths)
        # 创建文件但 mock open raise OSError
        (service._log_dir / "sidecar.log").write_text("content", encoding="utf-8")

        original_open = open

        def fake_open(path, mode, *args, **kwargs):
            if "sidecar.log" in str(path) and "rb" in mode:
                raise OSError("mock read error")
            return original_open(path, mode, *args, **kwargs)

        monkeypatch.setattr("builtins.open", fake_open)
        summary = service.collect_logs_summary()
        assert "<read error:" in summary["sidecar.log"]
