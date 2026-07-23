"""fake_sidecar fixture 单元测试 (P3-17).

测试策略:
- 用真实 ``subprocess.run`` 调用 fake_sidecar 包装器 (非 mock)，端到端验证脚本行为
- 用 ``tmp_path`` fixture 创建临时目录
- 用 ``json.loads`` 解析 stdout 验证 JSON 输出
- ``run`` 子命令会等 stdin EOF 退出，测试需传 ``stdin=DEVNULL`` 让其立即 EOF

测试用例 (12 个):
- test_create_fake_sidecar_returns_executable_path
- test_run_command_outputs_ready_json
- test_status_command_outputs_running_json
- test_stop_command_exits_zero
- test_doctor_command_outputs_valid_json
- test_dump_command_writes_output_file
- test_restore_command_succeeds_without_env
- test_restore_command_outputs_target_data_dir_with_explicit
- test_restore_command_fails_with_env
- test_maintenance_shell_command_outputs_json
- test_fake_sidecar_exit_code_env_override
- test_fake_sidecar_timezone_env_override
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.e2e.fixtures.fake_sidecar import create_fake_sidecar

pytestmark = pytest.mark.unit


def _run_sidecar(
    wrapper: Path,
    *args: str,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """辅助函数: 调用 fake_sidecar 包装器，返回 CompletedProcess。

    ``run`` 子命令会等 stdin EOF 退出，故传 ``stdin=DEVNULL`` 让其立即 EOF。
    """
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [str(wrapper), *args],
        capture_output=True,
        text=True,
        timeout=10,
        stdin=subprocess.DEVNULL,
        env=env,
    )


def test_create_fake_sidecar_returns_executable_path(tmp_path: Path) -> None:
    """验证 create_fake_sidecar 返回路径存在 + 可执行。"""
    wrapper = create_fake_sidecar(tmp_path)
    assert wrapper.exists(), f"包装器文件不存在: {wrapper}"
    # Unix 需可执行权限；Windows .bat 通过 cmd 自动执行
    if os.name != "nt":
        assert wrapper.stat().st_mode & 0o100, f"包装器无可执行权限: {wrapper}"


def test_run_command_outputs_ready_json(tmp_path: Path) -> None:
    """run 子命令: 输出 ready JSON 到 stdout + 写 password 文件。"""
    wrapper = create_fake_sidecar(tmp_path)
    data_dir = tmp_path / "data"
    pwd_file = tmp_path / "runtime" / "password"

    result = _run_sidecar(
        wrapper,
        "run",
        "--data-dir",
        str(data_dir),
        "--password-file",
        str(pwd_file),
    )

    assert result.returncode == 0, f"run 失败: {result.stderr}"
    ready = json.loads(result.stdout.strip())
    assert ready["schema"] == "qtrading.embedded_postgres.run.ready.v1"
    assert ready["status"] == "running"
    assert ready["port"] == 55432
    assert ready["data_dir"] == str(data_dir)
    # 验证 password 文件已写入
    assert pwd_file.exists(), "password 文件未写入"
    assert pwd_file.read_text(encoding="utf-8") == "fake_pg_password_55432"


def test_status_command_outputs_running_json(tmp_path: Path) -> None:
    """status 子命令: 输出 {"running": true, "pid": 12345} JSON。"""
    wrapper = create_fake_sidecar(tmp_path)
    result = _run_sidecar(wrapper, "status")
    assert result.returncode == 0, f"status 失败: {result.stderr}"
    status = json.loads(result.stdout.strip())
    assert status["running"] is True
    assert status["pid"] == 12345


def test_stop_command_exits_zero(tmp_path: Path) -> None:
    """stop 子命令: exit code 0。"""
    wrapper = create_fake_sidecar(tmp_path)
    result = _run_sidecar(wrapper, "stop")
    assert result.returncode == 0, f"stop 失败: {result.stderr}"


def test_doctor_command_outputs_valid_json(tmp_path: Path) -> None:
    """doctor 子命令: 输出合法 JSON + schema 版本。"""
    wrapper = create_fake_sidecar(tmp_path)
    data_dir = tmp_path / "data"
    result = _run_sidecar(wrapper, "doctor", "--data-dir", str(data_dir))
    assert result.returncode == 0, f"doctor 失败: {result.stderr}"
    doctor = json.loads(result.stdout.strip())
    assert doctor["schema"] == "qtrading.embedded_postgres.doctor.v1"
    assert doctor["data_dir"] == str(data_dir)
    assert doctor["initialized"] is True
    assert doctor["postgres_alive"] is True
    assert doctor["timezone"] == "Asia/Shanghai"


def test_dump_command_writes_output_file(tmp_path: Path) -> None:
    """dump 子命令: 写 dummy bytes 到 --output 路径。"""
    wrapper = create_fake_sidecar(tmp_path)
    output_file = tmp_path / "dump" / "backup.dump"
    result = _run_sidecar(
        wrapper,
        "dump",
        "--data-dir",
        str(tmp_path / "data"),
        "--output",
        str(output_file),
    )
    assert result.returncode == 0, f"dump 失败: {result.stderr}"
    assert output_file.exists(), "dump 输出文件未创建"
    assert output_file.read_bytes() == b"FAKE_PG_DUMP_BYTES"


def test_restore_command_succeeds_without_env(tmp_path: Path, monkeypatch) -> None:
    """restore 子命令: 无 FAKE_SIDECAR_RESTORE_FAIL 时 exit 0 + 输出默认 target_data_dir (P1-10)。"""
    monkeypatch.delenv("FAKE_SIDECAR_RESTORE_FAIL", raising=False)
    wrapper = create_fake_sidecar(tmp_path)
    data_dir = tmp_path / "data"
    result = _run_sidecar(
        wrapper,
        "restore",
        "--data-dir",
        str(data_dir),
        "--input",
        str(tmp_path / "backup.dump"),
    )
    assert result.returncode == 0, f"restore 应 exit 0, 实际: {result.returncode}, stderr: {result.stderr}"
    # P1-10: 验证 stdout 输出默认 target_data_dir (对齐 sidecar §12.2 原子切换)
    expected_target = str(data_dir.parent / "data.restore-fake")
    assert result.stdout.strip() == expected_target


def test_restore_command_outputs_target_data_dir_with_explicit(tmp_path: Path, monkeypatch) -> None:
    """restore 子命令: 显式 --target-data-dir 透传到 stdout (P1-10)。"""
    monkeypatch.delenv("FAKE_SIDECAR_RESTORE_FAIL", raising=False)
    wrapper = create_fake_sidecar(tmp_path)
    target = tmp_path / "new_data"
    result = _run_sidecar(
        wrapper,
        "restore",
        "--data-dir",
        str(tmp_path / "data"),
        "--input",
        str(tmp_path / "backup.dump"),
        "--target-data-dir",
        str(target),
    )
    assert result.returncode == 0, f"restore 应 exit 0, 实际: {result.returncode}, stderr: {result.stderr}"
    # P1-10: 显式 --target-data-dir 透传到 stdout
    assert result.stdout.strip() == str(target)


def test_restore_command_fails_with_env(tmp_path: Path) -> None:
    """restore 子命令: 设置 FAKE_SIDECAR_RESTORE_FAIL=1 时 exit 11。"""
    wrapper = create_fake_sidecar(tmp_path)
    result = _run_sidecar(
        wrapper,
        "restore",
        "--data-dir",
        str(tmp_path / "data"),
        "--input",
        str(tmp_path / "backup.dump"),
        env_overrides={"FAKE_SIDECAR_RESTORE_FAIL": "1"},
    )
    assert result.returncode == 11, f"restore 应 exit 11, 实际: {result.returncode}"


def test_maintenance_shell_command_outputs_json(tmp_path: Path) -> None:
    """maintenance-shell 子命令: 输出 {psql_path, connection_string_redacted} JSON。"""
    wrapper = create_fake_sidecar(tmp_path)
    result = _run_sidecar(wrapper, "maintenance-shell", "--data-dir", str(tmp_path / "data"))
    assert result.returncode == 0, f"maintenance-shell 失败: {result.stderr}"
    info = json.loads(result.stdout.strip())
    assert "psql_path" in info
    assert "connection_string_redacted" in info
    # R9: 验证 connection_string 不含明文密码
    assert "***" in info["connection_string_redacted"]


def test_fake_sidecar_exit_code_env_override(tmp_path: Path) -> None:
    """FAKE_SIDECAR_EXIT_CODE 环境变量: 覆盖任意子命令 exit code。"""
    wrapper = create_fake_sidecar(tmp_path)
    # 用 status 子命令测试 (正常 exit 0)
    result = _run_sidecar(
        wrapper,
        "status",
        env_overrides={"FAKE_SIDECAR_EXIT_CODE": "5"},
    )
    assert result.returncode == 5, f"应 exit 5, 实际: {result.returncode}"


def test_fake_sidecar_timezone_env_override(tmp_path: Path) -> None:
    """FAKE_SIDECAR_TIMEZONE 环境变量: 覆盖 doctor JSON 的 timezone 字段。"""
    wrapper = create_fake_sidecar(tmp_path)
    result = _run_sidecar(
        wrapper,
        "doctor",
        "--data-dir",
        str(tmp_path / "data"),
        env_overrides={"FAKE_SIDECAR_TIMEZONE": "UTC"},
    )
    assert result.returncode == 0, f"doctor 失败: {result.stderr}"
    doctor = json.loads(result.stdout.strip())
    assert doctor["timezone"] == "UTC"
