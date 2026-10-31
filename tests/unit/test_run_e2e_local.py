"""根目录 run_e2e_local.py 单测（此前无任何模块覆盖）。

覆盖契约：
1. sidecar binary 缺失 → 退出码 2 且 stderr 指明路径（不得静默启动半个 E2E 环境）；
2. sidecar 存在 → 以 ``python -m pytest`` 启动、``cwd`` 为脚本所在根目录、覆盖 addopts、
   关闭 xdist / randomly，并注入 embedded 模式 + sidecar 路径 / sha256 / artifact / headed
   等环境变量（与 tests/e2e/conftest.py 的读取口径对齐）；
3. 未给目标时默认跑 ``tests/e2e/``；pytest 退出码原样透传。
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.meta]

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import run_e2e_local  # noqa: E402 - sys.path 注入后导入

_SIDECAR_RELPATH = Path("sidecars") / "qtrading-pg-sidecar" / "target" / "release" / "qtrading-pg-sidecar.exe"


def _stage_workroot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, with_sidecar: bool) -> Path:
    """把脚本 ``__file__`` 指向临时根目录，返回该根目录（sidecar 可选落盘）。"""
    workroot = tmp_path / "workroot"
    workroot.mkdir()
    if with_sidecar:
        sidecar = workroot / _SIDECAR_RELPATH
        sidecar.parent.mkdir(parents=True)
        sidecar.write_bytes(b"fake-sidecar-binary")
    monkeypatch.setattr(run_e2e_local, "__file__", str(workroot / "run_e2e_local.py"))
    return workroot


def test_missing_sidecar_returns_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """sidecar binary 缺失时必须拒绝启动（退出码 2）并给出缺失路径。"""
    workroot = _stage_workroot(tmp_path, monkeypatch, with_sidecar=False)
    monkeypatch.setattr(sys, "argv", ["run_e2e_local.py"])

    assert run_e2e_local.main() == 2

    err = capsys.readouterr().err
    assert "[ERROR] sidecar binary not found" in err
    assert str(workroot / _SIDECAR_RELPATH) in err


def test_main_starts_pytest_with_sidecar_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """sidecar 存在时按契约装配 pytest 命令与环境变量，并在脚本根目录执行。"""
    workroot = _stage_workroot(tmp_path, monkeypatch, with_sidecar=True)
    sidecar = workroot / _SIDECAR_RELPATH
    captured: dict[str, Any] = {}

    def _fake_call(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> int:
        captured.update(cmd=cmd, cwd=cwd, env=env)
        return 0

    monkeypatch.setattr(run_e2e_local.subprocess, "call", _fake_call)
    monkeypatch.setattr(sys, "argv", ["run_e2e_local.py", "tests/e2e/test_demo.py"])

    assert run_e2e_local.main() == 0

    cmd = captured["cmd"]
    env = captured["env"]
    assert cmd[:3] == [sys.executable, "-m", "pytest"]
    assert "tests/e2e/test_demo.py" in cmd
    # 覆盖仓库 addopts（含 -n auto）并显式关闭 xdist / randomly，保证本地串行可复现
    assert "-o" in cmd and "addopts=" in cmd
    assert "no:xdist" in cmd
    assert "no:randomly" in cmd
    assert captured["cwd"] == workroot

    assert env["QTRADING_DATABASE_MODE"] == "embedded"
    assert env["SIDECAR_BINARY_PATH"] == str(sidecar)
    assert env["SIDECAR_SHA256"] == hashlib.sha256(b"fake-sidecar-binary").hexdigest()
    assert env["E2E_TIMEOUT_MULTIPLIER"] == "2.0"
    assert env["E2E_ARTIFACT_DIR"] == str(workroot / "e2e-artifacts")
    assert env["E2E_HEADED"] == "1"
    assert (workroot / "e2e-artifacts").is_dir()


def test_main_defaults_to_tests_e2e_and_propagates_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """未给目标时默认 ``tests/e2e/``；pytest 退出码原样透传（不吞没失败）。"""
    _stage_workroot(tmp_path, monkeypatch, with_sidecar=True)
    captured: dict[str, Any] = {}

    def _fake_call(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> int:
        captured.update(cmd=cmd)
        return 3

    monkeypatch.setattr(run_e2e_local.subprocess, "call", _fake_call)
    monkeypatch.setattr(sys, "argv", ["run_e2e_local.py"])

    assert run_e2e_local.main() == 3
    assert "tests/e2e/" in captured["cmd"]
