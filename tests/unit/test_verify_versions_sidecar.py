"""Tests for scripts/verify_versions.py Check 9 sidecar 版本一致性 (pg_plan §15.5 AI-12)。

验证：
- get_sidecar_cargo_version: Cargo.toml [package].version 读取
- get_pyproject_sidecar_config: pyproject.toml [tool.qtrading.sidecar] 读取
- get_sidecar_protocol_version: protocol.rs PROTOCOL_VERSION 常量解析
- _get_cargo_postgresql_embedded_version: postgresql_embedded 依赖版本剥离 = 前缀
- check_sidecar_version_consistency: 三方一致 / 不一致场景 / 缺失文件 / 四方校验 /
  四方校验 payload schema 门（VV-01：缺失/空/错型不得绕过版本证明）/
  源码 schema 门与 9d 依赖不可解析拒绝（VV-02）
- query_sidecar_version_json: 调用 sidecar version --json 并解析 stdout
- _parse_check_sidecar_binary_arg: --check-sidecar-binary 参数解析
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.meta]

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from verify_versions import (  # noqa: E402
    _get_cargo_postgresql_embedded_version,
    _parse_check_sidecar_binary_arg,
    check_sidecar_version_consistency,
    get_pyproject_sidecar_config,
    get_sidecar_cargo_version,
    get_sidecar_protocol_version,
    query_sidecar_version_json,
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
    pg_version: str = "16.14.0",
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
        assert isinstance(cfg, dict)  # 合法形态为 TOML table（VV-02 源码 schema 门的合法输入）
        assert cfg["version"] == "0.1.0"
        assert cfg["protocol_version"] == "v1"
        assert cfg["postgresql_version"] == "16.14.0"
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
                pg_version="16.14.0",
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

    def test_postgresql_version_not_supported_series(self, tmp_path: Path, monkeypatch) -> None:
        _write_sidecar_stubs(tmp_path, pg_version="15.2.0")
        monkeypatch.setattr(
            "verify_versions.SIDECAR_CARGO_PATH", tmp_path / "sidecars" / "qtrading-pg-sidecar" / "Cargo.toml"
        )
        monkeypatch.setattr(
            "verify_versions.SIDECAR_PROTOCOL_PATH",
            tmp_path / "sidecars" / "qtrading-pg-sidecar" / "src" / "protocol.rs",
        )
        monkeypatch.setattr("verify_versions.PYPROJECT_PATH", tmp_path / "pyproject.toml")
        errors = check_sidecar_version_consistency()
        assert any("not in 16-19.x series" in e for e in errors)

    def test_crate_version_mismatch(self, tmp_path: Path, monkeypatch) -> None:
        # Cargo.toml crate_version=0.21.0, pyproject crate_version=0.22.0
        _write_sidecar_stubs(tmp_path, crate_version="0.21.0")
        (tmp_path / "pyproject.toml").write_text(
            _PYPROJECT_SIDECAR_TEMPLATE.format(
                pyproject_version="0.1.0",
                protocol_version="v1",
                pg_version="16.14.0",
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


class TestCheckSidecarSourceSchemaGate:
    """check_sidecar_version_consistency 源码 schema 门（VV-02）。"""

    def _run_check(
        self,
        tmp_path: Path,
        monkeypatch,
        pyproject_text: str,
        sidecar_binary: Path | None = None,
    ) -> list[str]:
        """公共前置：写 sidecar 桩与自定义 pyproject 文本，跑三方/四方校验。"""
        _write_sidecar_stubs(tmp_path)
        (tmp_path / "pyproject.toml").write_text(pyproject_text, encoding="utf-8")
        monkeypatch.setattr(
            "verify_versions.SIDECAR_CARGO_PATH", tmp_path / "sidecars" / "qtrading-pg-sidecar" / "Cargo.toml"
        )
        monkeypatch.setattr(
            "verify_versions.SIDECAR_PROTOCOL_PATH",
            tmp_path / "sidecars" / "qtrading-pg-sidecar" / "src" / "protocol.rs",
        )
        monkeypatch.setattr("verify_versions.PYPROJECT_PATH", tmp_path / "pyproject.toml")
        return check_sidecar_version_consistency(sidecar_binary)

    def test_non_table_sidecar_config_fails(self, tmp_path: Path, monkeypatch) -> None:
        """VV-02: [tool.qtrading] 下 sidecar 为标量（非 table）时报错并短路。"""
        pyproject = (
            '[project]\nname = "AStockScreener"\nversion = "0.9.0"\n\n[tool.qtrading]\nsidecar = "flat-string"\n'
        )
        errors = self._run_check(tmp_path, monkeypatch, pyproject)
        joined = "\n".join(errors)
        assert "must be a table" in joined, f"应报告非 table 配置节, got: {errors}"
        assert "got str" in joined, f"错误信息应含实际类型, got: {errors}"
        # 短路：不得产生 9a version mismatch
        assert all("mismatch" not in e for e in errors)

    def test_missing_version_field_fails(self, tmp_path: Path, monkeypatch) -> None:
        """VV-02: version 字段缺失时报错，不得静默跳过 9a 比对。"""
        pyproject = (
            '[project]\nname = "AStockScreener"\nversion = "0.9.0"\n\n'
            '[tool.qtrading.sidecar]\nprotocol_version = "v1"\n'
            'postgresql_version = "16.14.0"\ncrate_version = "0.21.0"\n'
        )
        errors = self._run_check(tmp_path, monkeypatch, pyproject)
        joined = "\n".join(errors)
        assert "'version'" in joined, f"应报告缺失的 version 字段, got: {errors}"
        assert "missing or not a non-empty string" in joined
        assert all("mismatch" not in e for e in errors)

    def test_empty_version_field_fails(self, tmp_path: Path, monkeypatch) -> None:
        """VV-02: version 字段为空串时报错（.get(key, "") 默认值不得静默通过）。"""
        pyproject = (
            '[project]\nname = "AStockScreener"\nversion = "0.9.0"\n\n'
            '[tool.qtrading.sidecar]\nversion = ""\nprotocol_version = "v1"\n'
            'postgresql_version = "16.14.0"\ncrate_version = "0.21.0"\n'
        )
        errors = self._run_check(tmp_path, monkeypatch, pyproject)
        joined = "\n".join(errors)
        assert "'version'" in joined, f"应报告空串 version 字段, got: {errors}"

    def test_missing_pg_version_skips_series_check(self, tmp_path: Path, monkeypatch) -> None:
        """VV-02: postgresql_version 缺失时由 schema 门报错，9c 前缀校验不再执行。"""
        pyproject = (
            '[project]\nname = "AStockScreener"\nversion = "0.9.0"\n\n'
            '[tool.qtrading.sidecar]\nversion = "0.1.0"\nprotocol_version = "v1"\n'
            'crate_version = "0.21.0"\n'
        )
        errors = self._run_check(tmp_path, monkeypatch, pyproject)
        joined = "\n".join(errors)
        assert "'postgresql_version'" in joined, f"应报告缺失的 postgresql_version 字段, got: {errors}"
        assert "not in 16-19.x series" not in joined, "schema 门短路后不得再执行 9c 前缀校验"

    def test_non_string_field_fails(self, tmp_path: Path, monkeypatch) -> None:
        """VV-02: 字段为非字符串类型（如 int）时报错并给出实际类型。"""
        pyproject = (
            '[project]\nname = "AStockScreener"\nversion = "0.9.0"\n\n'
            '[tool.qtrading.sidecar]\nversion = 123\nprotocol_version = "v1"\n'
            'postgresql_version = "16.14.0"\ncrate_version = "0.21.0"\n'
        )
        errors = self._run_check(tmp_path, monkeypatch, pyproject)
        joined = "\n".join(errors)
        assert "'version'" in joined, f"应报告错型 version 字段, got: {errors}"
        assert "int" in joined, f"错误信息应含实际类型名, got: {errors}"

    def test_multiple_missing_fields_all_reported(self, tmp_path: Path, monkeypatch) -> None:
        """VV-02: 多个字段缺失时逐字段全部报错。"""
        pyproject = (
            '[project]\nname = "AStockScreener"\nversion = "0.9.0"\n\n'
            '[tool.qtrading.sidecar]\npostgresql_version = "16.14.0"\n'
        )
        errors = self._run_check(tmp_path, monkeypatch, pyproject)
        joined = "\n".join(errors)
        assert "'version'" in joined, f"应报告缺失的 version, got: {errors}"
        assert "'protocol_version'" in joined, f"应报告缺失的 protocol_version, got: {errors}"
        assert "'crate_version'" in joined, f"应报告缺失的 crate_version, got: {errors}"

    def test_schema_gate_short_circuits_binary_check(self, tmp_path: Path, monkeypatch) -> None:
        """VV-02: 源码 schema 门失败即短路，四方校验不再查询 binary。"""
        pyproject = (
            '[project]\nname = "AStockScreener"\nversion = "0.9.0"\n\n'
            '[tool.qtrading.sidecar]\nversion = "0.1.0"\nprotocol_version = "v1"\n'
            'postgresql_version = "16.14.0"\n'
        )  # 缺 crate_version
        binary = tmp_path / "sidecar.exe"
        binary.write_text("placeholder", encoding="utf-8")
        with patch("verify_versions.query_sidecar_version_json") as mock_query:
            errors = self._run_check(tmp_path, monkeypatch, pyproject, sidecar_binary=binary)
        joined = "\n".join(errors)
        assert "'crate_version'" in joined, f"应报告缺失的 crate_version, got: {errors}"
        mock_query.assert_not_called()


class TestCheckCrateDependencyUnparseable:
    """9d caller 对 Cargo.toml postgresql_embedded 不可解析状态的显式拒绝（VV-02）。"""

    _VALID_CARGO_HEAD = '[package]\nname = "qtrading-pg-sidecar"\nversion = "0.1.0"\nedition = "2021"\n'

    def _run_check_with_cargo(self, tmp_path: Path, monkeypatch, cargo_text: str) -> list[str]:
        """公共前置：写 sidecar/pyproject 桩与自定义 Cargo.toml 文本，跑三方校验。"""
        _write_sidecar_stubs(tmp_path)
        (tmp_path / "sidecars" / "qtrading-pg-sidecar" / "Cargo.toml").write_text(cargo_text, encoding="utf-8")
        monkeypatch.setattr(
            "verify_versions.SIDECAR_CARGO_PATH", tmp_path / "sidecars" / "qtrading-pg-sidecar" / "Cargo.toml"
        )
        monkeypatch.setattr(
            "verify_versions.SIDECAR_PROTOCOL_PATH",
            tmp_path / "sidecars" / "qtrading-pg-sidecar" / "src" / "protocol.rs",
        )
        monkeypatch.setattr("verify_versions.PYPROJECT_PATH", tmp_path / "pyproject.toml")
        return check_sidecar_version_consistency()

    def test_missing_dependency_reports_unparseable(self, tmp_path: Path, monkeypatch) -> None:
        """VV-02: Cargo.toml 缺 postgresql_embedded 依赖时记 unparseable error。"""
        errors = self._run_check_with_cargo(tmp_path, monkeypatch, self._VALID_CARGO_HEAD)
        joined = "\n".join(errors)
        assert "missing or unparseable" in joined, f"应报告依赖不可解析, got: {errors}"
        assert all("crate_version mismatch" not in e for e in errors), "不可解析不得误报 mismatch"

    def test_string_dependency_reports_unparseable(self, tmp_path: Path, monkeypatch) -> None:
        """VV-02: postgresql_embedded 简写形式（字符串）时记 unparseable error。"""
        cargo = self._VALID_CARGO_HEAD + '\n[dependencies]\npostgresql_embedded = "0.21.0"\n'
        errors = self._run_check_with_cargo(tmp_path, monkeypatch, cargo)
        joined = "\n".join(errors)
        assert "missing or unparseable" in joined, f"应报告依赖不可解析, got: {errors}"

    def test_unparseable_dependency_still_runs_binary_comparison(self, tmp_path: Path, monkeypatch) -> None:
        """VV-02: 9d 记 error 后不短路，四方比较继续执行且其余对齐时仅 1 条 error。"""
        _write_sidecar_stubs(tmp_path)
        (tmp_path / "sidecars" / "qtrading-pg-sidecar" / "Cargo.toml").write_text(
            self._VALID_CARGO_HEAD, encoding="utf-8"
        )
        monkeypatch.setattr(
            "verify_versions.SIDECAR_CARGO_PATH", tmp_path / "sidecars" / "qtrading-pg-sidecar" / "Cargo.toml"
        )
        monkeypatch.setattr(
            "verify_versions.SIDECAR_PROTOCOL_PATH",
            tmp_path / "sidecars" / "qtrading-pg-sidecar" / "src" / "protocol.rs",
        )
        monkeypatch.setattr("verify_versions.PYPROJECT_PATH", tmp_path / "pyproject.toml")
        binary = tmp_path / "sidecar.exe"
        binary.write_text("placeholder", encoding="utf-8")
        version_json = {
            "sidecar_version": "0.1.0",
            "protocol_version": "v1",
            "postgres_version": "16.14.0",
            "postgresql_embedded_version": "0.21.0",
        }
        with patch("verify_versions.query_sidecar_version_json", return_value=version_json):
            errors = check_sidecar_version_consistency(binary)
        assert len(errors) == 1, f"其余四方对齐时应仅剩 unparseable 1 条, got: {errors}"
        assert "missing or unparseable" in errors[0]


class TestCheckSidecarVersionConsistency4Way:
    """check_sidecar_version_consistency 四方校验（--check-sidecar-binary）。"""

    # 与 sidecar Rust 侧 VersionJson（commands.rs build_version_json）契约对齐的四字段 payload
    _VALID_VERSION_JSON = {
        "sidecar_version": "0.1.0",
        "protocol_version": "v1",
        "postgres_version": "16.14.0",
        "postgresql_embedded_version": "0.21.0",
    }

    def _run_check_with_payload(self, tmp_path: Path, monkeypatch, version_json: object) -> list[str]:
        """公共前置：写 sidecar 桩、patch 路径常量，mock query 返回给定 payload 后跑四方校验。"""
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
        sidecar_binary.write_text("placeholder", encoding="utf-8")
        with patch("verify_versions.query_sidecar_version_json", return_value=version_json):
            return check_sidecar_version_consistency(sidecar_binary)

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
            "postgres_version": "16.14.0",
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
            "postgres_version": "16.14.0",
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

    # ---- VV-01：四方校验 payload schema 门（缺失/空/错型不得绕过版本证明） ----

    def test_binary_empty_json_payload_fails(self, tmp_path: Path, monkeypatch) -> None:
        """VV-01: version --json 输出 {} 必须逐字段报错，不得静默通过（缺失不等于相等）。"""
        errors = self._run_check_with_payload(tmp_path, monkeypatch, {})
        assert errors, "空 payload 必须报错而非静默通过"
        joined = "\n".join(errors)
        for field in ("sidecar_version", "protocol_version", "postgres_version", "postgresql_embedded_version"):
            assert f"'{field}'" in joined, f"应报告缺失字段 {field}, got: {errors}"
        # schema 门失败即短路，不得进入版本相等比较
        assert all("mismatch" not in e for e in errors)

    def test_binary_missing_single_field_fails(self, tmp_path: Path, monkeypatch) -> None:
        """VV-01: 单个必需字段缺失时逐字段报错，其余字段不误报。"""
        payload = {k: v for k, v in self._VALID_VERSION_JSON.items() if k != "postgres_version"}
        errors = self._run_check_with_payload(tmp_path, monkeypatch, payload)
        joined = "\n".join(errors)
        assert "'postgres_version'" in joined, f"应报告缺失的 postgres_version, got: {errors}"
        assert "'sidecar_version'" not in joined

    def test_binary_none_field_fails(self, tmp_path: Path, monkeypatch) -> None:
        """VV-01: 必需字段为 None（null）时报错。"""
        payload = {**self._VALID_VERSION_JSON, "sidecar_version": None}
        errors = self._run_check_with_payload(tmp_path, monkeypatch, payload)
        joined = "\n".join(errors)
        assert "'sidecar_version'" in joined, f"应报告 None 字段, got: {errors}"
        assert "None" in joined

    def test_binary_non_string_field_fails(self, tmp_path: Path, monkeypatch) -> None:
        """VV-01: 必需字段为数值等非字符串类型时报错。"""
        payload = {**self._VALID_VERSION_JSON, "postgres_version": 16}
        errors = self._run_check_with_payload(tmp_path, monkeypatch, payload)
        joined = "\n".join(errors)
        assert "'postgres_version'" in joined, f"应报告错型字段, got: {errors}"

    def test_binary_empty_string_field_fails(self, tmp_path: Path, monkeypatch) -> None:
        """VV-01: 必需字段为空字符串时报错（构建元数据注入失败的工件）。"""
        payload = {**self._VALID_VERSION_JSON, "protocol_version": ""}
        errors = self._run_check_with_payload(tmp_path, monkeypatch, payload)
        joined = "\n".join(errors)
        assert "'protocol_version'" in joined, f"应报告空串字段, got: {errors}"

    def test_binary_non_dict_payload_fails(self, tmp_path: Path, monkeypatch) -> None:
        """VV-01: version --json 返回非对象 JSON（如 list）时报错而非 AttributeError。"""
        errors = self._run_check_with_payload(tmp_path, monkeypatch, ["unexpected"])
        joined = "\n".join(errors)
        assert "non-dict JSON payload" in joined, f"应报告非 dict payload, got: {errors}"

    def test_binary_all_fields_match_after_schema_gate(self, tmp_path: Path, monkeypatch) -> None:
        """schema 门通过后仍执行相等比较：值不匹配的完整 payload 仍报 mismatch。"""
        payload = {**self._VALID_VERSION_JSON, "postgresql_embedded_version": "0.22.0"}
        errors = self._run_check_with_payload(tmp_path, monkeypatch, payload)
        assert any("postgresql_embedded_version '0.22.0'" in e for e in errors), (
            f"schema 门不得吞掉相等比较 error, got: {errors}"
        )


class TestQuerySidecarVersionJson:
    """query_sidecar_version_json 调用 sidecar version --json 并解析 stdout。"""

    def _make_binary(self, tmp_path: Path) -> Path:
        binary = tmp_path / "sidecar.exe"
        binary.write_text("placeholder", encoding="utf-8")
        return binary

    def test_valid_json_returns_dict(self, tmp_path: Path) -> None:
        binary = self._make_binary(tmp_path)
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout='{"sidecar_version": "0.1.0"}', stderr="")
        with patch("verify_versions.subprocess.run", return_value=completed):
            result = query_sidecar_version_json(binary)
        assert result == {"sidecar_version": "0.1.0"}

    def test_non_json_stdout_raises(self, tmp_path: Path) -> None:
        binary = self._make_binary(tmp_path)
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="not json", stderr="")
        with patch("verify_versions.subprocess.run", return_value=completed):
            with pytest.raises(RuntimeError, match="non-JSON stdout"):
                query_sidecar_version_json(binary)

    def test_nonzero_exit_raises(self, tmp_path: Path) -> None:
        binary = self._make_binary(tmp_path)
        completed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")
        with patch("verify_versions.subprocess.run", return_value=completed):
            with pytest.raises(RuntimeError, match="exited 1"):
                query_sidecar_version_json(binary)

    def test_timeout_raises(self, tmp_path: Path) -> None:
        binary = self._make_binary(tmp_path)
        with patch(
            "verify_versions.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=str(binary), timeout=10),
        ):
            with pytest.raises(RuntimeError, match="timed out"):
                query_sidecar_version_json(binary)

    def test_missing_binary_raises_filenotfound(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="sidecar binary not found"):
            query_sidecar_version_json(tmp_path / "absent-sidecar.exe")


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
