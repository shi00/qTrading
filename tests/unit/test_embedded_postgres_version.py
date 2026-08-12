"""resolve_pg_major_version 单元测试（嵌入 PostgreSQL 版本去硬编码）。

通过 monkeypatch 替换 version 模块的 subprocess.run，避免发起真实子进程。
测试间清理 functools.cache 缓存避免污染（R7 精神：状态隔离）。
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from data.persistence.embedded_postgres.version import resolve_pg_major_version


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    """每个测试前后清理 functools.cache，避免缓存跨测试污染。"""
    resolve_pg_major_version.cache_clear()
    yield
    resolve_pg_major_version.cache_clear()


@pytest.fixture
def sidecar_binary(tmp_path: Path) -> Path:
    """构造一个真实存在的 sidecar 文件路径（is_file() 校验通过）。"""
    binary = tmp_path / "qtrading-pg-sidecar"
    binary.touch()
    return binary


def _mock_run(stdout: str, returncode: int = 0, stderr: str = "") -> object:
    """构造 mock subprocess.run 返回值。"""
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_resolves_major_version(sidecar_binary: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """正常解析 "16.14.0" → "16"。"""
    monkeypatch.setattr(
        "data.persistence.embedded_postgres.version.subprocess.run",
        lambda *_a, **_k: _mock_run(json.dumps({"postgres_version": "16.14.0"})),
    )
    assert resolve_pg_major_version(sidecar_binary) == "16"


def test_sidecar_missing_raises_file_not_found(tmp_path: Path) -> None:
    """sidecar 不存在 → FileNotFoundError。"""
    missing = tmp_path / "missing-sidecar"
    with pytest.raises(FileNotFoundError, match="sidecar binary not found"):
        resolve_pg_major_version(missing)


def test_timeout_raises_runtime_error(sidecar_binary: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """子进程超时 → RuntimeError。"""

    def _run(*_args: object, **_kwargs: object) -> object:
        raise subprocess.TimeoutExpired(cmd=["sidecar"], timeout=10)

    monkeypatch.setattr("data.persistence.embedded_postgres.version.subprocess.run", _run)
    with pytest.raises(RuntimeError, match="timed out"):
        resolve_pg_major_version(sidecar_binary)


def test_nonzero_exit_raises_runtime_error(sidecar_binary: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """非零退出码 → RuntimeError（带上 stderr）。"""
    monkeypatch.setattr(
        "data.persistence.embedded_postgres.version.subprocess.run",
        lambda *_a, **_k: _mock_run(stdout="", returncode=1, stderr="boom"),
    )
    with pytest.raises(RuntimeError, match="exited 1"):
        resolve_pg_major_version(sidecar_binary)


def test_non_json_stdout_raises_runtime_error(sidecar_binary: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """stdout 非合法 JSON → RuntimeError。"""
    monkeypatch.setattr(
        "data.persistence.embedded_postgres.version.subprocess.run",
        lambda *_a, **_k: _mock_run(stdout="not json"),
    )
    with pytest.raises(RuntimeError, match="non-JSON"):
        resolve_pg_major_version(sidecar_binary)


def test_missing_postgres_version_field_raises(sidecar_binary: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """缺 postgres_version 字段 → RuntimeError。"""
    monkeypatch.setattr(
        "data.persistence.embedded_postgres.version.subprocess.run",
        lambda *_a, **_k: _mock_run(stdout=json.dumps({"schema": "qtrading.embedded_postgres.version.v1"})),
    )
    with pytest.raises(RuntimeError, match="postgres_version"):
        resolve_pg_major_version(sidecar_binary)


def test_invalid_major_version_raises(sidecar_binary: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """postgres_version 主版本非法（非数字）→ RuntimeError。"""
    monkeypatch.setattr(
        "data.persistence.embedded_postgres.version.subprocess.run",
        lambda *_a, **_k: _mock_run(stdout=json.dumps({"postgres_version": "abc.1.0"})),
    )
    with pytest.raises(RuntimeError, match="invalid postgres_version"):
        resolve_pg_major_version(sidecar_binary)


def test_postgres_version_not_str_raises(sidecar_binary: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """postgres_version 非字符串（如 int）→ RuntimeError。"""
    monkeypatch.setattr(
        "data.persistence.embedded_postgres.version.subprocess.run",
        lambda *_a, **_k: _mock_run(stdout=json.dumps({"postgres_version": 16})),
    )
    with pytest.raises(RuntimeError, match="must be a non-empty string"):
        resolve_pg_major_version(sidecar_binary)


def test_postgres_version_no_dot_parses(sidecar_binary: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """postgres_version 无点号（如 "16"）→ 主版本仍为 "16"。"""
    monkeypatch.setattr(
        "data.persistence.embedded_postgres.version.subprocess.run",
        lambda *_a, **_k: _mock_run(stdout=json.dumps({"postgres_version": "16"})),
    )
    assert resolve_pg_major_version(sidecar_binary) == "16"


def test_cache_calls_subprocess_once(sidecar_binary: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """functools.cache：多次调用 resolve_pg_major_version，subprocess.run 只被调用一次。"""
    calls: list[object] = []

    def _run(*args: object, **_k: object) -> subprocess.CompletedProcess[str]:
        calls.append(args[0])
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=json.dumps({"postgres_version": "16.14.0"}))

    monkeypatch.setattr("data.persistence.embedded_postgres.version.subprocess.run", _run)
    assert resolve_pg_major_version(sidecar_binary) == "16"
    assert resolve_pg_major_version(sidecar_binary) == "16"
    assert len(calls) == 1
