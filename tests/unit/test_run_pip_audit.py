"""scripts/run_pip_audit.py 传输层故障重试的单元测试。

背景：run 42335350136 中 requirements-dev.txt 因 PyPI 读超时（read timeout=15）
被误报为安全门禁失败，前两个 requirements 文件均通过。重试只应覆盖网络故障，
命中漏洞时必须立刻失败，不能被重试掩盖。
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
