"""D21-OTHER-005: E2E app 日志/trace 按实例隔离，防止并行 A/B 池串扰。"""

import io
import json
from types import SimpleNamespace

import pytest

from tests.e2e.helpers.app_launcher import (
    _check_startup_errors,
    _drain_lines,
    _drain_stdout,
    _read_log_tail,
    allocate_app_log_paths,
    iter_run_log_paths,
    register_app_instance,
)

pytestmark = [pytest.mark.unit]


def test_allocate_distinct_paths_per_instance(tmp_path):
    a = allocate_app_log_paths("ro", logs_root=tmp_path)
    b = allocate_app_log_paths("ro", logs_root=tmp_path)

    assert a.instance_id != b.instance_id
    assert a.stdout_path != b.stdout_path
    assert a.trace_path != b.trace_path
    assert a.log_dir.parent == b.log_dir.parent
    assert a.log_dir.name.endswith("-ro")


def test_allocate_sanitizes_label_and_stays_under_run_root(tmp_path):
    paths = allocate_app_log_paths("pool/../../evil:name", logs_root=tmp_path)

    assert "/" not in paths.instance_id
    assert ":" not in paths.instance_id
    run_root = (tmp_path / "e2e" / paths.run_id).resolve()
    assert paths.log_dir.resolve().is_relative_to(run_root)


def test_parallel_instances_do_not_share_stdout(tmp_path):
    a = allocate_app_log_paths("ro", logs_root=tmp_path)
    b = allocate_app_log_paths("mut", logs_root=tmp_path)

    _drain_lines(
        io.StringIO("ERROR boom\n"),
        primary_path=a.stdout_path,
        aggregate_path=a.aggregate_stdout_path,
        instance_id=a.instance_id,
    )
    _drain_lines(
        io.StringIO("ready\n"),
        primary_path=b.stdout_path,
        aggregate_path=b.aggregate_stdout_path,
        instance_id=b.instance_id,
    )

    assert "ERROR boom" in a.stdout_path.read_text(encoding="utf-8")
    assert "ERROR" not in b.stdout_path.read_text(encoding="utf-8")
    aggregate = a.aggregate_stdout_path.read_text(encoding="utf-8")
    assert f"[{a.instance_id}] ERROR boom" in aggregate
    assert f"[{b.instance_id}] ready" in aggregate


def test_drain_stdout_uses_instance_paths(tmp_path):
    a = allocate_app_log_paths("ro", logs_root=tmp_path)
    proc = SimpleNamespace(stdout=io.StringIO("boot ok\n"))

    _drain_stdout(proc, a)

    assert a.stdout_path.read_text(encoding="utf-8") == "boot ok\n"
    assert f"[{a.instance_id}] boot ok" in a.aggregate_stdout_path.read_text(encoding="utf-8")


def test_startup_error_check_only_reads_own_instance(tmp_path):
    a = allocate_app_log_paths("ro", logs_root=tmp_path)
    b = allocate_app_log_paths("mut", logs_root=tmp_path)
    a.stdout_path.write_text("[Bootstrap] Database initialization failed\n", encoding="utf-8")
    b.stdout_path.write_text("[Bootstrap] Loaded\n", encoding="utf-8")

    # A 的启动错误不得让 B 的检查失败
    _check_startup_errors(b.stdout_path, timeout_s=0.1)
    with pytest.raises(RuntimeError, match="Database initialization failed"):
        _check_startup_errors(a.stdout_path, timeout_s=0.1)


def test_stale_ready_marker_not_visible_to_other_instance(tmp_path):
    a = allocate_app_log_paths("ro", logs_root=tmp_path)
    b = allocate_app_log_paths("mut", logs_root=tmp_path)
    a.stdout_path.write_text("[Bootstrap] embedded postgres ready on 127.0.0.1:5555\n", encoding="utf-8")

    assert "embedded postgres ready" in _read_log_tail(a.stdout_path)
    # B 尚未写出 ready，不得继承 A 的旧 ready 证据
    assert "embedded postgres ready" not in _read_log_tail(b.stdout_path)


def test_new_instance_allocation_keeps_existing_trace(tmp_path):
    a = allocate_app_log_paths("ro", logs_root=tmp_path)
    a.trace_path.write_text("A trace\n", encoding="utf-8")

    _b = allocate_app_log_paths("mut", logs_root=tmp_path)

    assert a.trace_path.read_text(encoding="utf-8") == "A trace\n"
    assert not _b.trace_path.exists()


def test_run_index_lists_instances(tmp_path):
    a = allocate_app_log_paths("ro", logs_root=tmp_path)
    b = allocate_app_log_paths("mut", logs_root=tmp_path)

    register_app_instance(a, label="ro", pid=111, url="http://127.0.0.1:1111")
    register_app_instance(b, label="mut", pid=222, url="http://127.0.0.1:2222")

    index = json.loads((a.log_dir.parent / "index.json").read_text(encoding="utf-8"))
    assert index["run_id"] == a.run_id
    assert [entry["instance_id"] for entry in index["instances"]] == [a.instance_id, b.instance_id]
    assert index["instances"][0]["stdout"] == f"{a.instance_id}/stdout.log"
    assert index["instances"][0]["trace"] == f"{a.instance_id}/main_trace.log"


def test_iter_run_log_paths_scopes_to_run(tmp_path):
    a = allocate_app_log_paths("ro", logs_root=tmp_path)
    b = allocate_app_log_paths("mut", logs_root=tmp_path)

    paths = iter_run_log_paths(a.run_id, logs_root=tmp_path)

    assert {p.instance_id for p in paths} == {a.instance_id, b.instance_id}
