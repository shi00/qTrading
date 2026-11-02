"""D16-S01：sidecar 运行期有界诊断通道测试。

覆盖：已识别 warning/exit 事件解码与顺序、未知 schema/不可解析 exit 的可见诊断、
事件缓冲上限、脱敏、日志轮转、日志不可写降级（reader 继续消费、不阻塞 sidecar）。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import pytest

from data.persistence.embedded_postgres import service as svc_module
from data.persistence.embedded_postgres.service import (
    _EVENT_BUFFER_MAX,
    _EVENT_EXIT_SCHEMA,
    _EVENT_WARNING_SCHEMA,
    EmbeddedPostgresService,
    EmbeddedPgEvent,
    _RotatingTextSink,
)
from utils.sanitizers import DataSanitizer


def _event_line(schema: str, event: str, *, code: str | None = None, message: str = "") -> str:
    payload: dict[str, object] = {"schema": schema, "event": event, "message": message}
    if code is not None:
        payload["code"] = code
    return json.dumps(payload)


class _Lines:
    """模拟 Popen 文本管道：逐行返回后 EOF。"""

    def __init__(self, lines: list[str]) -> None:
        self._lines = list(lines)

    def readline(self) -> str:
        if not self._lines:
            return ""
        return self._lines.pop(0)


class _Closable:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _Proc:
    """只满足 reader/stop_sync 所需接口的最小进程桩。"""

    def __init__(self, stdout_lines: list[str], stderr_lines: list[str]) -> None:
        self.stdin = _Closable()
        self.stdout = _Lines(stdout_lines)
        self.stderr = _Lines(stderr_lines)

    def poll(self) -> int | None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        return None


@pytest.fixture
def service(tmp_path: Path):
    """构造隔离的 service 并清理专用 logger handler/secret，避免跨测试串状态。"""
    svc_logger = logging.getLogger("qtrading.embedded_postgres")
    for handler in list(svc_logger.handlers):
        if getattr(handler, "_embedded_pg_handler", False):
            svc_logger.removeHandler(handler)
            handler.close()
    DataSanitizer._reset_known_secrets()
    EmbeddedPostgresService._reset_singleton()
    instance = EmbeddedPostgresService(
        sidecar_binary=tmp_path / "sidecar",
        data_dir=tmp_path / "postgres" / "17" / "data",
        install_dir=tmp_path / "postgres" / "17" / "install",
        log_dir=tmp_path / "logs",
    )
    try:
        yield instance
    finally:
        instance.stop_sync()
        EmbeddedPostgresService._reset_singleton()
        DataSanitizer._reset_known_secrets()
        for handler in list(svc_logger.handlers):
            if getattr(handler, "_embedded_pg_handler", False):
                svc_logger.removeHandler(handler)
                handler.close()


def _wait_for(predicate, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class TestEventDecoding:
    def test_warning_and_exit_decoded_in_stdout_order(self, service: EmbeddedPostgresService) -> None:
        service._handle_stdout_diagnostics(
            _event_line(_EVENT_WARNING_SCHEMA, "warning", code="disk_low", message="free < 100MB"),
            "sanitized",
        )
        service._handle_stdout_diagnostics(
            _event_line(_EVENT_EXIT_SCHEMA, "exit", code="60", message="postgres_died"),
            "sanitized",
        )
        events = service.recent_events()
        assert [event.event for event in events] == ["warning", "exit"]
        assert events[0].code == "disk_low"
        assert events[1].code == "60"

    def test_exit_decoded_even_with_unknown_schema(self, service: EmbeddedPostgresService) -> None:
        """未知 schema 但 event=exit：不得静默丢关键退出原因。"""
        service._handle_stdout_diagnostics(
            '{"schema": "qtrading.embedded_postgres.event.exit.v2", "event": "exit", '
            '"code": "60", "message": "postgres_died"}',
            "sanitized",
        )
        events = service.recent_events()
        assert len(events) == 1
        assert events[0].event == "exit"
        assert events[0].message == "postgres_died"

    def test_unknown_schema_not_buffered_but_diagnosed(self, service: EmbeddedPostgresService, caplog) -> None:
        with caplog.at_level(logging.WARNING, logger="qtrading.embedded_postgres"):
            service._handle_stdout_diagnostics(
                '{"schema": "qtrading.embedded_postgres.event.other.v1", "event": "other"}', "raw-line"
            )
        assert service.recent_events() == ()
        assert any("unknown sidecar stdout schema" in record.message for record in caplog.records)

    def test_unparseable_exit_marker_logged(self, service: EmbeddedPostgresService, caplog) -> None:
        with caplog.at_level(logging.WARNING, logger="qtrading.embedded_postgres"):
            service._handle_stdout_diagnostics('{"event": "exit", broken', "raw exit line")
        assert service.recent_events() == ()
        assert any("unparseable sidecar stdout line" in record.message for record in caplog.records)

    def test_event_message_sanitized_and_bounded(self, service: EmbeddedPostgresService) -> None:
        secret = "mock_pg_password_55432"
        DataSanitizer.register_secret(secret)
        payload = _event_line(
            _EVENT_WARNING_SCHEMA,
            "warning",
            code="disk_low",
            message=f"postgresql://postgres:{secret}@127.0.0.1:5432/qtrading {'x' * 500}",
        )
        service._handle_stdout_diagnostics(payload, "sanitized")
        event = service.recent_events()[0]
        assert secret not in event.message
        assert "***" in event.message
        assert len(event.message) <= svc_module._EVENT_TEXT_MAX

    def test_event_buffer_is_bounded_and_drops_oldest(self, service: EmbeddedPostgresService) -> None:
        for index in range(_EVENT_BUFFER_MAX + 10):
            service._handle_stdout_diagnostics(
                _event_line(_EVENT_WARNING_SCHEMA, "warning", code="disk_low", message=f"w{index}"),
                "sanitized",
            )
        events = service.recent_events()
        assert len(events) == _EVENT_BUFFER_MAX
        assert events[-1].message == f"w{_EVENT_BUFFER_MAX + 9}"
        assert all(event.message != "w0" for event in events)

    def test_drain_events_clears_buffer(self, service: EmbeddedPostgresService) -> None:
        event = EmbeddedPgEvent(schema=_EVENT_WARNING_SCHEMA, event="warning", message="disk_low")
        service._record_event(event)
        drained = service.drain_events()
        assert drained == [event]
        assert service.recent_events() == ()


class TestRotatingSink:
    def test_rotates_at_capacity_and_keeps_backups(self, tmp_path: Path) -> None:
        path = tmp_path / "sidecar.stdout.log"
        sink = _RotatingTextSink(path, max_bytes=32, backup_count=2)
        for index in range(6):
            assert sink.write_line(f"line-{index}-0123456789\n") is True
        sink.close()
        assert path.exists()
        assert (tmp_path / "sidecar.stdout.log.1").exists()
        assert (tmp_path / "sidecar.stdout.log.2").exists()
        assert not (tmp_path / "sidecar.stdout.log.3").exists()

    def test_write_after_close_returns_false(self, tmp_path: Path) -> None:
        sink = _RotatingTextSink(tmp_path / "sidecar.stderr.log", max_bytes=1024, backup_count=1)
        sink.close()
        assert sink.closed is True
        assert sink.write_line("late line\n") is False


class TestReaderFallbacks:
    def test_stdout_reader_decodes_and_archives(self, service: EmbeddedPostgresService) -> None:
        lines = [
            _event_line(_EVENT_WARNING_SCHEMA, "warning", code="disk_low", message="free < 100MB") + "\n",
            _event_line(_EVENT_EXIT_SCHEMA, "exit", code="60", message="postgres_died") + "\n",
            '{"schema": "qtrading.embedded_postgres.event.unknown.v1", "event": "unknown"}\n',
        ]
        service._process = _Proc(lines, [])  # type: ignore[assignment]  # 进程桩仅需 reader 接口
        service._start_stdout_reader_thread()
        assert _wait_for(lambda: len(service.recent_events()) == 2)
        content = (service._log_dir / "sidecar.stdout.log").read_text(encoding="utf-8")
        assert "unknown.v1" in content
        assert service.recent_events()[-1].message == "postgres_died"

    def test_stdout_reader_falls_back_when_log_unwritable(
        self, service: EmbeddedPostgresService, monkeypatch, caplog
    ) -> None:
        lines = [_event_line(_EVENT_EXIT_SCHEMA, "exit", code="60", message="postgres_died") + "\n"]
        service._process = _Proc(lines, [])  # type: ignore[assignment]  # 进程桩仅需 reader 接口
        monkeypatch.setattr(svc_module, "_open_rotating_sink", lambda *args, **kwargs: None)
        with caplog.at_level(logging.INFO, logger="qtrading.embedded_postgres"):
            service._start_stdout_reader_thread()
            assert _wait_for(lambda: len(service.recent_events()) == 1)
        assert any("sidecar stdout:" in record.message for record in caplog.records)

    def test_stderr_reader_falls_back_when_log_unwritable(
        self, service: EmbeddedPostgresService, monkeypatch, caplog
    ) -> None:
        service._stderr_file = None
        service._process = _Proc([], ["password=secret-value\n"])  # type: ignore[assignment]  # 进程桩仅需 reader 接口
        with caplog.at_level(logging.WARNING, logger="qtrading.embedded_postgres"):
            service._start_stderr_reader_thread()
            assert _wait_for(lambda: any("sidecar stderr:" in record.message for record in caplog.records))

    def test_stderr_reader_keeps_draining_after_sink_closed(
        self, service: EmbeddedPostgresService, tmp_path: Path, caplog
    ) -> None:
        """stop 关闭归档后 reader 仍消费 stderr 管道（防 kill 兜底前管道背压）。"""
        sink = _RotatingTextSink(tmp_path / "sidecar.stderr.log", max_bytes=1024, backup_count=1)
        sink.close()
        service._stderr_file = sink
        service._process = _Proc([], ["late stderr line\n"])  # type: ignore[assignment]  # 进程桩仅需 reader 接口
        with caplog.at_level(logging.WARNING, logger="qtrading.embedded_postgres"):
            service._start_stderr_reader_thread()
            assert _wait_for(lambda: any("late stderr line" in record.message for record in caplog.records))
