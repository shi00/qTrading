"""D21-OTHER-005: E2E app trace 支持每实例路径，同时保留 run 级汇总镜像。"""

import app.application as app_main


def test_trace_log_writes_instance_path_and_legacy_mirror(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    instance_path = tmp_path / "logs" / "e2e" / "run-1" / "01-ro" / "main_trace.log"
    monkeypatch.setenv("E2E_MAIN_TRACE_PATH", str(instance_path))
    monkeypatch.setenv("E2E_INSTANCE_ID", "01-ro")

    app_main._trace_log("page.render start")

    instance_text = instance_path.read_text(encoding="utf-8")
    assert "page.render start" in instance_text
    assert "[01-ro]" not in instance_text

    legacy_text = (tmp_path / "logs" / "main_trace.log").read_text(encoding="utf-8")
    assert "[01-ro]" in legacy_text
    assert "page.render start" in legacy_text


def test_trace_log_defaults_to_single_file_without_override(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("E2E_MAIN_TRACE_PATH", raising=False)
    monkeypatch.delenv("E2E_INSTANCE_ID", raising=False)

    app_main._trace_log("plain")

    legacy_text = (tmp_path / "logs" / "main_trace.log").read_text(encoding="utf-8")
    assert "plain" in legacy_text
    assert "[None]" not in legacy_text
