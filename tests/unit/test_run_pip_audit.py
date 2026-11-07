"""scripts/run_pip_audit.py 的单元测试。

背景一：run 42335350136 中 requirements-dev.txt 因 PyPI 读超时（read timeout=15）
被误报为安全门禁失败，前两个 requirements 文件均通过。重试只应覆盖网络故障，
命中漏洞时必须立刻失败，不能被重试掩盖。

背景二：requirements 输入全部/部分缺失时曾以 WARNING + continue 空转返回 0，
等于「显式要求审计的输入集合全部缺失」仍放行；输入完整性必须 fail-closed。
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "run_pip_audit.py"


@pytest.fixture(scope="module")
def audit_module():
    spec = importlib.util.spec_from_file_location("run_pip_audit", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _patch_runner(
    monkeypatch: pytest.MonkeyPatch, module, results: list[subprocess.CompletedProcess[str]]
) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(cmd, capture_output, text):  # noqa: ANN001 - 与 subprocess.run 同形
        calls.append(list(cmd))
        return results.pop(0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    return calls


def test_retries_transient_network_failure(audit_module, monkeypatch) -> None:
    calls = _patch_runner(
        monkeypatch,
        audit_module,
        [
            subprocess.CompletedProcess([], 1, "", "urllib3.exceptions.ReadTimeoutError: HTTPSConnectionPool"),
            subprocess.CompletedProcess([], 0, "No known vulnerabilities found", ""),
        ],
    )

    assert audit_module.run_pip_audit_with_retry(["pip-audit"], "requirements-dev.txt") == 0
    assert len(calls) == 2


def test_vulnerability_hit_is_not_retried(audit_module, monkeypatch) -> None:
    calls = _patch_runner(
        monkeypatch,
        audit_module,
        [subprocess.CompletedProcess([], 1, "Found 2 known vulnerabilities in 1 package", "")],
    )

    assert audit_module.run_pip_audit_with_retry(["pip-audit"], "requirements.txt") == 1
    assert len(calls) == 1


def test_retry_exhausted_returns_nonzero(audit_module, monkeypatch) -> None:
    transient = subprocess.CompletedProcess([], 1, "", "requests.exceptions.ConnectionError: timed out")
    calls = _patch_runner(monkeypatch, audit_module, [transient, transient, transient])

    assert audit_module.run_pip_audit_with_retry(["pip-audit"], "requirements.txt") == 1
    assert len(calls) == audit_module._MAX_ATTEMPTS


def test_http_timeout_is_passed_to_pip_audit(audit_module) -> None:
    assert audit_module._HTTP_TIMEOUT_SECONDS >= 30


def _make_requirements(tmp_path: Path, *names: str) -> list[str]:
    paths = []
    for name in names:
        path = tmp_path / name
        path.write_text("", encoding="utf-8")
        paths.append(str(path))
    return paths


def test_empty_requirements_fails_without_running_audit(audit_module, monkeypatch) -> None:
    calls = _patch_runner(monkeypatch, audit_module, [])

    assert audit_module.run_pip_audit([], ["pypi"], []) == 1
    assert calls == []


def test_all_requirements_missing_fails_without_running_audit(audit_module, monkeypatch, tmp_path) -> None:
    calls = _patch_runner(monkeypatch, audit_module, [])
    missing = [str(tmp_path / "requirements.txt"), str(tmp_path / "requirements-dev.txt")]

    assert audit_module.run_pip_audit(missing, ["pypi"], []) == 1
    assert calls == []


def test_partially_missing_requirements_fails_without_partial_pass(audit_module, monkeypatch, tmp_path) -> None:
    calls = _patch_runner(monkeypatch, audit_module, [])
    existing = _make_requirements(tmp_path, "requirements.txt")

    assert audit_module.run_pip_audit([*existing, str(tmp_path / "requirements-dev.txt")], ["pypi"], []) == 1
    assert calls == []


def test_directory_path_fails_without_running_audit(audit_module, monkeypatch, tmp_path) -> None:
    calls = _patch_runner(monkeypatch, audit_module, [])

    assert audit_module.run_pip_audit([str(tmp_path)], ["pypi"], []) == 1
    assert calls == []


def test_all_requirements_present_returns_zero(audit_module, monkeypatch, tmp_path) -> None:
    req1, req2 = _make_requirements(tmp_path, "requirements.txt", "requirements-dev.txt")
    calls = _patch_runner(
        monkeypatch,
        audit_module,
        [
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, "", ""),
        ],
    )

    assert audit_module.run_pip_audit([req1, req2], ["pypi"], ["--ignore-vuln", "PYSEC-2024-1"]) == 0
    assert len(calls) == 2
    assert calls[0] == [
        "pip-audit",
        "-s",
        "pypi",
        "-r",
        req1,
        "--desc",
        "--timeout",
        str(audit_module._HTTP_TIMEOUT_SECONDS),
        "--ignore-vuln",
        "PYSEC-2024-1",
    ]
    assert calls[1][4] == req2


def test_all_requirements_present_passes_through_nonzero(audit_module, monkeypatch, tmp_path) -> None:
    req1 = _make_requirements(tmp_path, "requirements.txt")
    calls = _patch_runner(
        monkeypatch,
        audit_module,
        [subprocess.CompletedProcess([], 7, "", "Found 1 known vulnerability")],
    )

    assert audit_module.run_pip_audit(req1, ["pypi"], []) == 7
    assert len(calls) == 1


def test_requirements_file_disappearing_during_audit_fails_closed(audit_module, monkeypatch, tmp_path) -> None:
    req1, req2 = _make_requirements(tmp_path, "requirements.txt", "requirements-dev.txt")
    calls = []

    def fake_run(cmd, capture_output, text):  # noqa: ANN001 - 与 subprocess.run 同形
        calls.append(list(cmd))
        # 模拟第一个文件审计期间第二个文件被删除（预检已通过，循环内需再次拦截）
        Path(req2).unlink()
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(audit_module.subprocess, "run", fake_run)

    assert audit_module.run_pip_audit([req1, req2], ["pypi"], []) == 1
    assert len(calls) == 1
