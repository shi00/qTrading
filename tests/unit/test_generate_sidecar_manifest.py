"""Tests for scripts/generate_sidecar_manifest.py (pg_plan §15.3 / §16.1)。

验证：
- compute_sha256：空文件/已知内容 sha256 正确
- query_sidecar_version：file not found / timeout / non-JSON / exit != 0 / 成功
- build_manifest：schema / generated_at 注入 / 字段映射
- main：argparse / output to file / stdout / sidecar 不存在返回 1 / version --json 失败返回 2
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from generate_sidecar_manifest import (  # noqa: E402
    MANIFEST_SCHEMA,
    build_manifest,
    compute_sha256,
    main,
    query_sidecar_version,
)


class TestComputeSha256:
    """compute_sha256 正确性。"""

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        assert compute_sha256(f) == hashlib.sha256(b"").hexdigest()

    def test_known_content(self, tmp_path: Path) -> None:
        f = tmp_path / "data.bin"
        f.write_bytes(b"hello world\n")
        assert compute_sha256(f) == hashlib.sha256(b"hello world\n").hexdigest()

    def test_large_file_chunked(self, tmp_path: Path) -> None:
        # 3MB 文件，触发多次 chunk 读取（chunk size = 1MB）
        f = tmp_path / "large.bin"
        content = b"x" * (3 * 1024 * 1024 + 17)
        f.write_bytes(content)
        assert compute_sha256(f) == hashlib.sha256(content).hexdigest()


class TestQuerySidecarVersion:
    """query_sidecar_version 错误处理与成功路径。"""

    def test_file_not_found_raises(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / "missing-sidecar.exe"
        with pytest.raises(FileNotFoundError, match="sidecar binary not found"):
            query_sidecar_version(nonexistent)

    def test_timeout_raises_runtime_error(self, tmp_path: Path) -> None:
        sidecar = tmp_path / "sidecar.exe"
        sidecar.write_text("#!/bin/sh\nsleep 30\n")
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="sidecar", timeout=10)
            with pytest.raises(RuntimeError, match="timed out"):
                query_sidecar_version(sidecar)

    def test_non_zero_exit_raises_runtime_error(self, tmp_path: Path) -> None:
        sidecar = tmp_path / "sidecar.exe"
        sidecar.write_text("placeholder")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
            with pytest.raises(RuntimeError, match="exited 1"):
                query_sidecar_version(sidecar)

    def test_non_json_stdout_raises_runtime_error(self, tmp_path: Path) -> None:
        sidecar = tmp_path / "sidecar.exe"
        sidecar.write_text("placeholder")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="not json", stderr="")
            with pytest.raises(RuntimeError, match="non-JSON"):
                query_sidecar_version(sidecar)

    def test_success_returns_dict(self, tmp_path: Path) -> None:
        sidecar = tmp_path / "sidecar.exe"
        sidecar.write_text("placeholder")
        expected = {
            "schema": "qtrading.embedded_postgres.version.v1",
            "sidecar_version": "0.1.0",
            "protocol_version": "v1",
            "postgres_version": "17.2.0",
        }
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(expected), stderr="")
            result = query_sidecar_version(sidecar)
        assert result == expected


class TestBuildManifest:
    """build_manifest schema/字段映射。"""

    def test_schema_constant(self) -> None:
        assert MANIFEST_SCHEMA == "qtrading.sidecar.manifest.v1"

    def test_build_manifest_with_injected_timestamp(self, tmp_path: Path) -> None:
        sidecar = tmp_path / "qtrading-pg-sidecar.exe"
        sidecar.write_bytes(b"binary")
        version_json = {
            "schema": "qtrading.embedded_postgres.version.v1",
            "sidecar_version": "0.1.0",
            "protocol_version": "v1",
            "postgres_version": "17.2.0",
            "postgres_binary_source": "theseus-bundled",
            "postgresql_embedded_version": "0.21.0",
            "rustc_version": "rustc 1.97.0",
            "git_sha": "abc123def456",
            "build_time_utc": "2026-07-20T00:00:00Z",
            "build_time_unix": 1784505600,
            "self_sha256": "deadbeef",
        }
        ts = _dt.datetime(2026, 7, 20, 12, 0, 0, tzinfo=_dt.UTC)
        manifest = build_manifest(
            sidecar=sidecar,
            target="x86_64-pc-windows-msvc",
            version_json=version_json,
            sha256="abc123",
            generated_at=ts,
        )
        assert manifest["schema"] == MANIFEST_SCHEMA
        assert manifest["target"] == "x86_64-pc-windows-msvc"
        assert manifest["binary_name"] == "qtrading-pg-sidecar.exe"
        assert manifest["sha256"] == "abc123"
        assert manifest["sidecar_version"] == "0.1.0"
        assert manifest["protocol_version"] == "v1"
        assert manifest["postgres_version"] == "17.2.0"
        assert manifest["postgres_binary_source"] == "theseus-bundled"
        assert manifest["postgresql_embedded_version"] == "0.21.0"
        assert manifest["rustc_version"] == "rustc 1.97.0"
        assert manifest["git_sha"] == "abc123def456"
        assert manifest["build_time_utc"] == "2026-07-20T00:00:00Z"
        assert manifest["build_time_unix"] == 1784505600
        assert manifest["sidecar_self_sha256"] == "deadbeef"
        assert manifest["generated_at_utc"] == "2026-07-20T12:00:00Z"
        assert manifest["generated_at_unix"] == int(ts.timestamp())

    def test_build_manifest_default_timestamp_uses_utc_now(self, tmp_path: Path) -> None:
        sidecar = tmp_path / "sidecar.exe"
        sidecar.write_bytes(b"x")
        # generated_at_utc 格式截断到秒（%Y-%m-%dT%H:%M:%SZ），before/after 同步截断微秒避免同秒内 before > actual
        before = _dt.datetime.now(_dt.UTC).replace(microsecond=0)
        manifest = build_manifest(
            sidecar=sidecar,
            target="x86_64-unknown-linux-gnu",
            version_json={},
            sha256="abc",
        )
        after = _dt.datetime.now(_dt.UTC).replace(microsecond=0)
        # 解析 generated_at_utc 验证在 [before, after] 区间
        actual = _dt.datetime.strptime(manifest["generated_at_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.UTC)
        assert before <= actual <= after

    def test_build_manifest_missing_version_json_fields_default_empty(self, tmp_path: Path) -> None:
        sidecar = tmp_path / "sidecar.exe"
        sidecar.write_bytes(b"x")
        manifest = build_manifest(
            sidecar=sidecar,
            target="t",
            version_json={},  # 所有字段缺失
            sha256="abc",
        )
        assert manifest["sidecar_version"] == ""
        assert manifest["protocol_version"] == ""
        assert manifest["postgres_version"] == ""
        assert manifest["sidecar_self_sha256"] is None
        assert manifest["build_time_unix"] == 0


class TestMainArgparse:
    """main() argparse 与退出码。"""

    def test_missing_sidecar_returns_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        # 无 --sidecar 参数，argparse 触发 SystemExit(2)
        with patch("sys.argv", ["generate_sidecar_manifest.py"]):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 2

    def test_nonexistent_sidecar_returns_1(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        nonexistent = tmp_path / "missing.exe"
        with patch("sys.argv", ["prog", "--sidecar", str(nonexistent), "--target", "t"]):
            code = main()
        assert code == 1
        err = capsys.readouterr().err
        assert "sidecar binary not found" in err

    def test_version_query_failure_returns_2(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        sidecar = tmp_path / "sidecar.exe"
        sidecar.write_text("placeholder")
        with patch("sys.argv", ["prog", "--sidecar", str(sidecar), "--target", "t"]):
            with patch(
                "generate_sidecar_manifest.query_sidecar_version",
                side_effect=RuntimeError("sidecar version --json exited 1"),
            ):
                code = main()
        assert code == 2
        err = capsys.readouterr().err
        assert "exited 1" in err

    def test_success_writes_to_stdout(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        sidecar = tmp_path / "sidecar.exe"
        sidecar.write_bytes(b"binary content")
        version_json = {
            "sidecar_version": "0.1.0",
            "protocol_version": "v1",
            "postgres_version": "17.2.0",
            "postgres_binary_source": "theseus-bundled",
            "postgresql_embedded_version": "0.21.0",
            "rustc_version": "rustc 1.97.0",
            "git_sha": "abc123",
            "build_time_utc": "2026-07-20T00:00:00Z",
            "build_time_unix": 1784505600,
            "self_sha256": None,
        }
        with patch("sys.argv", ["prog", "--sidecar", str(sidecar), "--target", "t"]):
            with patch("generate_sidecar_manifest.query_sidecar_version", return_value=version_json):
                code = main()
        assert code == 0
        out = capsys.readouterr().out
        manifest = json.loads(out)
        assert manifest["schema"] == MANIFEST_SCHEMA
        assert manifest["sidecar_version"] == "0.1.0"
        assert manifest["binary_name"] == "sidecar.exe"
        # sha256 应为 sidecar 文件实际 sha256
        assert manifest["sha256"] == hashlib.sha256(b"binary content").hexdigest()

    def test_success_writes_to_output_file(self, tmp_path: Path) -> None:
        sidecar = tmp_path / "sidecar.exe"
        sidecar.write_bytes(b"binary")
        output = tmp_path / "manifest.json"
        version_json = {"sidecar_version": "0.1.0"}
        with patch(
            "sys.argv",
            ["prog", "--sidecar", str(sidecar), "--target", "t", "--output", str(output)],
        ):
            with patch("generate_sidecar_manifest.query_sidecar_version", return_value=version_json):
                code = main()
        assert code == 0
        assert output.exists()
        manifest = json.loads(output.read_text(encoding="utf-8"))
        assert manifest["schema"] == MANIFEST_SCHEMA
        assert manifest["sidecar_version"] == "0.1.0"
