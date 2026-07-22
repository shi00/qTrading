"""Tests for scripts/verify_versions.py Check 9 sidecar 版本一致性 (pg_plan §15.5 AI-12)。

验证：
- get_sidecar_cargo_version: Cargo.toml [package].version 读取
- get_pyproject_sidecar_config: pyproject.toml [tool.qtrading.sidecar] 读取
- get_sidecar_protocol_version: protocol.rs PROTOCOL_VERSION 常量解析
- _get_cargo_postgresql_embedded_version: postgresql_embedded 依赖版本剥离 = 前缀
- check_sidecar_version_consistency: 三方一致 / 不一致场景 / 缺失文件 / 四方校验
- _parse_check_sidecar_binary_arg: --check-sidecar-binary 参数解析
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from verify_versions import (  # noqa: E402
    _get_cargo_postgresql_embedded_version,
    _parse_check_sidecar_binary_arg,
    check_sidecar_version_consistency,
    get_pyproject_sidecar_config,
    get_sidecar_cargo_version,
    get_sidecar_protocol_version,
)


# ---- Cargo.toml / pyproject.toml / protocol.rs 测试桩 ----

_CARGO_TOML_TEMPLATE = """\
[package]
name = "qtrading-pg-sidecar"
version = "{cargo_version}"
edition = "2021"

[dependencies]
postgresql_embedded = {{ version = "={crate_version}", default-features = false }}
"""

_PYPROJECT_SIDECAR_TEMPLATE = """\
[project]
name = "AStockScreener"
version = "0.9.0"

[tool.qtrading.sidecar]
version = "{pyproject_version}"
protocol_version = "{protocol_version}"
postgresql_version = "{pg_version}"
crate_version = "{crate_version}"
"""

_PROTOCOL_RS_TEMPLATE = """\
// placeholder
pub const PROTOCOL_VERSION: &str = "{protocol_version}";
"""


def _write_sidecar_stubs(
    root: Path,
    *,
    cargo_version: str = "0.1.0",
    pyproject_version: str = "0.1.0",
    protocol_version: str = "v1",
    pg_version: str = "17.2.0",
    crate_version: str = "0.21.0",
    write_cargo: bool = True,
    write_protocol: bool = True,
) -> None:
    """在 root 下写入 sidecar/Cargo.toml / sidecar/src/protocol.rs / pyproject.toml 桩。"""
    sidecar_dir = root / "sidecars" / "qtrading-pg-sidecar"
    if write_cargo:
        sidecar_dir.mkdir(parents=True, exist_ok=True)
        (sidecar_dir / "Cargo.toml").write_text(
            _CARGO_TOML_TEMPLATE.format(cargo_version=cargo_version, crate_version=crate_version),
            encoding="utf-8",
        )
    if write_protocol:
        src_dir = sidecar_dir / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "protocol.rs").write_text(
            _PROTOCOL_RS_TEMPLATE.format(protocol_version=protocol_version),
            encoding="utf-8",
        )
    (root / "pyproject.toml").write_text(
        _PYPROJECT_SIDECAR_TEMPLATE.format(
            pyproject_version=pyproject_version,
            protocol_version=protocol_version,
            pg_version=pg_version,
            crate_version=crate_version,
        ),
        encoding="utf-8",
    )


class TestGetSidecarCargoVersion:
    """get_sidecar_cargo_version 读取 Cargo.toml [package].version。"""

    def test_reads_version_correctly(self, tmp_path: Path, monkeypatch) -> None:
        _write_sidecar_stubs(tmp_path, cargo_version="0.2.5")
        monkeypatch.setattr(
            "verify_versions.SIDECAR_CARGO_PATH", tmp_path / "sidecars" / "qtrading-pg-sidecar" / "Cargo.toml"
        )
        assert get_sidecar_cargo_version() == "0.2.5"

    def test_missing_package_version_raises_keyerror(self, tmp_path: Path, monkeypatch) -> None:
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text('[package]\nname = "x"\n', encoding="utf-8")
        monkeypatch.setattr("verify_versions.SIDECAR_CARGO_PATH", cargo)
        with pytest.raises(KeyError, match="version") as exc_info:
            get_sidecar_cargo_version()
        assert "version" in str(exc_info.value)


class TestGetPyprojectSidecarConfig:
    """get_pyproject_sidecar_config 读取 [tool.qtrading.sidecar]。"""

    def test_reads_config_correctly(self, tmp_path: Path, monkeypatch) -> None:
        _write_sidecar_stubs(tmp_path)
        monkeypatch.setattr("verify_versions.PYPROJECT_PATH", tmp_path / "pyproject.toml")
        cfg = get_pyproject_sidecar_config()
        assert cfg["version"] == "0.1.0"
        assert cfg["protocol_version"] == "v1"
        assert cfg["postgresql_version"] == "17.2.0"
        assert cfg["crate_version"] == "0.21.0"

    def test_missing_section_raises_keyerror(self, tmp_path: Path, monkeypatch) -> None:
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nname = "x"\nversion = "0.9.0"\n', encoding="utf-8")
        monkeypatch.setattr("verify_versions.PYPROJECT_PATH", pyproject)
        # pyproject 仅有 [project]，缺 [tool.qtrading.sidecar] 层级，KeyError 为最外层缺失的 'tool'
        with pytest.raises(KeyError, match="'tool'") as exc_info:
            get_pyproject_sidecar_config()
        assert "tool" in str(exc_info.value)


class TestGetSidecarProtocolVersion:
    """get_sidecar_protocol_version 解析 protocol.rs PROTOCOL_VERSION 常量。"""

    def test_reads_v1_correctly(self, tmp_path: Path, monkeypatch) -> None:
        _write_sidecar_stubs(tmp_path, protocol_version="v1")
        monkeypatch.setattr(
            "verify_versions.SIDECAR_PROTOCOL_PATH",
            tmp_path / "sidecars" / "qtrading-pg-sidecar" / "src" / "protocol.rs",
        )
        assert get_sidecar_protocol_version() == "v1"

    def test_reads_v2_correctly(self, tmp_path: Path, monkeypatch) -> None:
        _write_sidecar_stubs(tmp_path, protocol_version="v2")
        monkeypatch.setattr(
            "verify_versions.SIDECAR_PROTOCOL_PATH",
            tmp_path / "sidecars" / "qtrading-pg-sidecar" / "src" / "protocol.rs",
        )
        assert get_sidecar_protocol_version() == "v2"

    def test_missing_const_raises_valueerror(self, tmp_path: Path, monkeypatch) -> None:
        protocol = tmp_path / "protocol.rs"
        protocol.write_text("// no const here\n", encoding="utf-8")
        monkeypatch.setattr("verify_versions.SIDECAR_PROTOCOL_PATH", protocol)
        with pytest.raises(ValueError, match="Could not find PROTOCOL_VERSION"):
            get_sidecar_protocol_version()


class TestGetCargoPostgresqlEmbeddedVersion:
    """_get_cargo_postgresql_embedded_version 剥离 = 前缀。"""

    def test_strips_equals_prefix(self, tmp_path: Path, monkeypatch) -> None:
        _write_sidecar_stubs(tmp_path, crate_version="0.21.0")
        monkeypatch.setattr(
            "verify_versions.SIDECAR_CARGO_PATH", tmp_path / "sidecars" / "qtrading-pg-sidecar" / "Cargo.toml"
        )
        assert _get_cargo_postgresql_embedded_version() == "0.21.0"

    def test_no_equals_prefix(self, tmp_path: Path, monkeypatch) -> None:
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text(
            '[dependencies]\npostgresql_embedded = { version = "0.21.0" }\n',
            encoding="utf-8",
        )
        monkeypatch.setattr("verify_versions.SIDECAR_CARGO_PATH", cargo)
        assert _get_cargo_postgresql_embedded_version() == "0.21.0"

    def test_missing_dependency_returns_empty(self, tmp_path: Path, monkeypatch) -> None:
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text('[package]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8")
        monkeypatch.setattr("verify_versions.SIDECAR_CARGO_PATH", cargo)
        assert _get_cargo_postgresql_embedded_version() == ""

    def test_string_dependency_returns_empty(self, tmp_path: Path, monkeypatch) -> None:
        # 简短形式 "postgresql_embedded = \"0.21.0\"" 不被识别（要求 dict 形式）
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text(
            '[dependencies]\npostgresql_embedded = "0.21.0"\n',
            encoding="utf-8",
        )
        monkeypatch.setattr("verify_versions.SIDECAR_CARGO_PATH", cargo)
        assert _get_cargo_postgresql_embedded_version() == ""


class TestCheckSidecarVersionConsistency:
    """check_sidecar_version_consistency 三方校验。"""

    def test_all_consistent_returns_no_errors(self, tmp_path: Path, monkeypatch) -> None:
        _write_sidecar_stubs(tmp_path)
        monkeypatch.setattr(
            "verify_versions.SIDECAR_CARGO_PATH", tmp_path / "sidecars" / "qtrading-pg-sidecar" / "Cargo.toml"
        )
        monkeypatch.setattr(
            "verify_versions.SIDECAR_PROTOCOL_PATH",
            tmp_path / "sidecars" / "qtrading-pg-sidecar" / "src" / "protocol.rs",
        )
        monkeypatch.setattr("verify_versions.PYPROJECT_PATH", tmp_path / "pyproject.toml")
        errors = check_sidecar_version_consistency()
        assert errors == []

    def test_cargo_version_mismatch(self, tmp_path: Path, monkeypatch) -> None:
        _write_sidecar_stubs(tmp_path, cargo_version="0.1.0", pyproject_version="0.2.0")
        monkeypatch.setattr(
            "verify_versions.SIDECAR_CARGO_PATH", tmp_path / "sidecars" / "qtrading-pg-sidecar" / "Cargo.toml"
        )
        monkeypatch.setattr(
            "verify_versions.SIDECAR_PROTOCOL_PATH",
            tmp_path / "sidecars" / "qtrading-pg-sidecar" / "src" / "protocol.rs",
        )
        monkeypatch.setattr("verify_versions.PYPROJECT_PATH", tmp_path / "pyproject.toml")
        errors = check_sidecar_version_consistency()
        assert any("Cargo.toml [package].version '0.1.0'" in e and "pyproject" in e for e in errors)

    def test_protocol_version_mismatch(self, tmp_path: Path, monkeypatch) -> None:
        _write_sidecar_stubs(tmp_path, protocol_version="v1", pyproject_version="0.1.0")
        # 覆盖 pyproject.toml 使 protocol_version != v1
        (tmp_path / "pyproject.toml").write_text(
            _PYPROJECT_SIDECAR_TEMPLATE.format(
                pyproject_version="0.1.0",
                protocol_version="v2",
                pg_version="17.2.0",
                crate_version="0.21.0",
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "verify_versions.SIDECAR_CARGO_PATH", tmp_path / "sidecars" / "qtrading-pg-sidecar" / "Cargo.toml"
        )
        monkeypatch.setattr(
            "verify_versions.SIDECAR_PROTOCOL_PATH",
            tmp_path / "sidecars" / "qtrading-pg-sidecar" / "src" / "protocol.rs",
        )
        monkeypatch.setattr("verify_versions.PYPROJECT_PATH", tmp_path / "pyproject.toml")
        errors = check_sidecar_version_consistency()
        assert any("protocol.rs PROTOCOL_VERSION 'v1'" in e and "pyproject" in e for e in errors)

    def test_postgresql_version_not_17_series(self, tmp_path: Path, monkeypatch) -> None:
        _write_sidecar_stubs(tmp_path, pg_version="16.2.0")
        monkeypatch.setattr(
            "verify_versions.SIDECAR_CARGO_PATH", tmp_path / "sidecars" / "qtrading-pg-sidecar" / "Cargo.toml"
        )
        monkeypatch.setattr(
            "verify_versions.SIDECAR_PROTOCOL_PATH",
            tmp_path / "sidecars" / "qtrading-pg-sidecar" / "src" / "protocol.rs",
        )
        monkeypatch.setattr("verify_versions.PYPROJECT_PATH", tmp_path / "pyproject.toml")
        errors = check_sidecar_version_consistency()
        assert any("not in 17.x series" in e for e in errors)

    def test_crate_version_mismatch(self, tmp_path: Path, monkeypatch) -> None:
        # Cargo.toml crate_version=0.21.0, pyproject crate_version=0.22.0
        _write_sidecar_stubs(tmp_path, crate_version="0.21.0")
        (tmp_path / "pyproject.toml").write_text(
            _PYPROJECT_SIDECAR_TEMPLATE.format(
                pyproject_version="0.1.0",
                protocol_version="v1",
                pg_version="17.2.0",
                crate_version="0.22.0",
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "verify_versions.SIDECAR_CARGO_PATH", tmp_path / "sidecars" / "qtrading-pg-sidecar" / "Cargo.toml"
        )
        monkeypatch.setattr(
            "verify_versions.SIDECAR_PROTOCOL_PATH",
            tmp_path / "sidecars" / "qtrading-pg-sidecar" / "src" / "protocol.rs",
        )
        monkeypatch.setattr("verify_versions.PYPROJECT_PATH", tmp_path / "pyproject.toml")
        errors = check_sidecar_version_consistency()
        assert any("crate_version mismatch" in e and "0.21.0" in e and "0.22.0" in e for e in errors)

    def test_missing_cargo_returns_error(self, tmp_path: Path, monkeypatch) -> None:
        _write_sidecar_stubs(tmp_path, write_cargo=False)
        monkeypatch.setattr(
            "verify_versions.SIDECAR_CARGO_PATH", tmp_path / "sidecars" / "qtrading-pg-sidecar" / "Cargo.toml"
        )
        monkeypatch.setattr(
            "verify_versions.SIDECAR_PROTOCOL_PATH",
            tmp_path / "sidecars" / "qtrading-pg-sidecar" / "src" / "protocol.rs",
        )
        monkeypatch.setattr("verify_versions.PYPROJECT_PATH", tmp_path / "pyproject.toml")
        errors = check_sidecar_version_consistency()
        assert any("sidecar Cargo.toml not found" in e for e in errors)

    def test_missing_protocol_returns_error(self, tmp_path: Path, monkeypatch) -> None:
        _write_sidecar_stubs(tmp_path, write_protocol=False)
        monkeypatch.setattr(
            "verify_versions.SIDECAR_CARGO_PATH", tmp_path / "sidecars" / "qtrading-pg-sidecar" / "Cargo.toml"
        )
        monkeypatch.setattr(
            "verify_versions.SIDECAR_PROTOCOL_PATH",
            tmp_path / "sidecars" / "qtrading-pg-sidecar" / "src" / "protocol.rs",
        )
        monkeypatch.setattr("verify_versions.PYPROJECT_PATH", tmp_path / "pyproject.toml")
        errors = check_sidecar_version_consistency()
        assert any("sidecar protocol.rs not found" in e for e in errors)


class TestCheckSidecarVersionConsistency4Way:
    """check_sidecar_version_consistency 四方校验（--check-sidecar-binary）。"""

    def test_binary_consistent_returns_no_errors(self, tmp_path: Path, monkeypatch) -> None:
        _write_sidecar_stubs(tmp_path)
        monkeypatch.setattr(
            "verify_versions.SIDECAR_CARGO_PATH", tmp_path / "sidecars" / "qtrading-pg-sidecar" / "Cargo.toml"
        )
        monkeypatch.setattr(
            "verify_versions.SIDECAR_PROTOCOL_PATH",
            tmp_path / "sidecars" / "qtrading-pg-sidecar" / "src" / "protocol.rs",
        )
        monkeypatch.setattr("verify_versions.PYPROJECT_PATH", tmp_path / "pyproject.toml")
        sidecar_binary = tmp_path / "sidecar.exe"
        sidecar_binary.write_text("placeholder")
        version_json = {
            "sidecar_version": "0.1.0",
            "protocol_version": "v1",
            "postgres_version": "17.2.0",
            "postgresql_embedded_version": "0.21.0",
        }
        with patch("verify_versions.query_sidecar_version_json", return_value=version_json):
            errors = check_sidecar_version_consistency(sidecar_binary)
        assert errors == []

    def test_binary_sidecar_version_mismatch(self, tmp_path: Path, monkeypatch) -> None:
        _write_sidecar_stubs(tmp_path, cargo_version="0.1.0")
        monkeypatch.setattr(
            "verify_versions.SIDECAR_CARGO_PATH", tmp_path / "sidecars" / "qtrading-pg-sidecar" / "Cargo.toml"
        )
        monkeypatch.setattr(
            "verify_versions.SIDECAR_PROTOCOL_PATH",
            tmp_path / "sidecars" / "qtrading-pg-sidecar" / "src" / "protocol.rs",
        )
        monkeypatch.setattr("verify_versions.PYPROJECT_PATH", tmp_path / "pyproject.toml")
        sidecar_binary = tmp_path / "sidecar.exe"
        sidecar_binary.write_text("placeholder")
        version_json = {
            "sidecar_version": "0.2.0",  # 与 Cargo.toml 0.1.0 不一致
            "protocol_version": "v1",
            "postgres_version": "17.2.0",
            "postgresql_embedded_version": "0.21.0",
        }
        with patch("verify_versions.query_sidecar_version_json", return_value=version_json):
            errors = check_sidecar_version_consistency(sidecar_binary)
        assert any("sidecar binary sidecar_version '0.2.0'" in e for e in errors)

    def test_binary_query_failure_returns_error(self, tmp_path: Path, monkeypatch) -> None:
        _write_sidecar_stubs(tmp_path)
        monkeypatch.setattr(
            "verify_versions.SIDECAR_CARGO_PATH", tmp_path / "sidecars" / "qtrading-pg-sidecar" / "Cargo.toml"
        )
        monkeypatch.setattr(
            "verify_versions.SIDECAR_PROTOCOL_PATH",
            tmp_path / "sidecars" / "qtrading-pg-sidecar" / "src" / "protocol.rs",
        )
        monkeypatch.setattr("verify_versions.PYPROJECT_PATH", tmp_path / "pyproject.toml")
        sidecar_binary = tmp_path / "sidecar.exe"
        sidecar_binary.write_text("placeholder")
        with patch(
            "verify_versions.query_sidecar_version_json",
            side_effect=RuntimeError("sidecar version --json exited 1"),
        ):
            errors = check_sidecar_version_consistency(sidecar_binary)
        assert any("--check-sidecar-binary failed" in e for e in errors)


class TestParseCheckSidecarBinaryArg:
    """_parse_check_sidecar_binary_arg 参数解析。"""

    def test_no_arg_returns_none(self) -> None:
        assert _parse_check_sidecar_binary_arg(["prog"]) is None

    def test_with_path_returns_path(self) -> None:
        result = _parse_check_sidecar_binary_arg(["prog", "--check-sidecar-binary", "/path/to/sidecar"])
        assert result == Path("/path/to/sidecar")

    def test_missing_path_exits_with_code_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc:
            _parse_check_sidecar_binary_arg(["prog", "--check-sidecar-binary"])
        assert exc.value.code == 2
        assert "requires a path argument" in capsys.readouterr().err
