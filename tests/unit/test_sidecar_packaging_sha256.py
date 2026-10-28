"""UIR-F15-005：发行物必须交付与 sidecar binary 同目录同名的 ``<exe>.sha256`` 旁文件。

运行前完整性校验（``EmbeddedPostgresService._verify_sidecar_sha256``）在打包（frozen）布局下
读取 ``_internal/sidecars/<exe>.sha256``；本测试守护打包配方确实按实际打包字节生成该旁文件、
随 binary 收集到同一目录，且 installer.iss 将其与 binary 一并安装。同时守护下载阶段的
供应链校验（远端 ``sha256sums-<target>.txt``）未被移除。
"""

from __future__ import annotations

import ast
import hashlib
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = PROJECT_ROOT / "AStockScreener.spec"
INSTALLER_PATH = PROJECT_ROOT / "installer.iss"
CI_WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "ci_cd.yml"

_HELPER_NAME = "_stage_sidecar_with_sha256"
_SIDECAR_NAME = "qtrading-pg-sidecar.exe"


def _helper_source() -> str:
    """从 spec 中提取打包辅助函数源码（spec 依赖 PyInstaller 注入的全局变量，无法直接 import）。"""
    source = SPEC_PATH.read_text(encoding="utf-8")
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name == _HELPER_NAME:
            segment = ast.get_source_segment(source, node)
            assert segment is not None
            return segment
    raise AssertionError(f"AStockScreener.spec 缺少打包辅助函数 {_HELPER_NAME}")


def _run_helper(binary: Path) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """在注入 binaries/datas 的最小命名空间中执行辅助函数，返回两者新增的收集项。"""
    namespace: dict[str, Any] = {
        "binaries": [],
        "datas": [],
        "hashlib": hashlib,
        "tempfile": tempfile,
        "Path": Path,
    }
    exec(compile(_helper_source(), str(SPEC_PATH), "exec"), namespace)
    namespace[_HELPER_NAME](binary)
    return namespace["binaries"], namespace["datas"]


def test_helper_stages_binary_and_side_file_into_sidecars(tmp_path: Path) -> None:
    """binary 与同名 .sha256 旁文件都收集到 ``sidecars``，旁文件摘要等于 binary 实际字节摘要。"""
    binary = tmp_path / _SIDECAR_NAME
    binary.write_bytes(b"sidecar-binary-bytes")

    binaries, datas = _run_helper(binary)

    assert binaries == [(str(binary), "sidecars")]
    assert len(datas) == 1
    side_entry = datas[0]
    assert side_entry[1] == "sidecars"
    side_path = Path(side_entry[0])
    assert side_path.name == f"{_SIDECAR_NAME}.sha256"
    assert side_path.parent != binary.parent  # 构建期旁文件写入独立临时目录，不改动源目录
    digest, filename = side_path.read_text(encoding="utf-8").split()
    assert digest == hashlib.sha256(b"sidecar-binary-bytes").hexdigest()
    assert filename == _SIDECAR_NAME


def test_helper_hash_tracks_exact_binary_bytes(tmp_path: Path) -> None:
    """摘要按实际打包字节计算：binary 内容变化（改一字节）→ 旁文件摘要随之变化。"""
    binary = tmp_path / _SIDECAR_NAME
    binary.write_bytes(b"v1")
    _, first = _run_helper(binary)
    binary.write_bytes(b"v2")
    _, second = _run_helper(binary)

    first_digest = Path(first[0][0]).read_text(encoding="utf-8").split()[0]
    second_digest = Path(second[0][0]).read_text(encoding="utf-8").split()[0]
    assert first_digest == hashlib.sha256(b"v1").hexdigest()
    assert second_digest == hashlib.sha256(b"v2").hexdigest()
    assert second_digest != first_digest


def test_spec_invokes_helper_for_primary_and_fallback_paths() -> None:
    """spec 对主路径与开发兜底路径都调用辅助函数（定义 + 两处调用）。"""
    source = SPEC_PATH.read_text(encoding="utf-8")
    assert source.count(f"{_HELPER_NAME}(") == 3


def test_installer_collects_whole_sidecars_directory() -> None:
    """installer.iss 递归收集 ``_internal\\sidecars`` 整目录，旁文件随 binary 一并安装。"""
    text = INSTALLER_PATH.read_text(encoding="utf-8")
    assert r'Source: "dist\AStockScreener\_internal\sidecars\*"; DestDir: "{app}\_internal\sidecars"' in text
    assert "recursesubdirs" in text


def test_build_workflow_still_verifies_downloaded_sidecar_checksum() -> None:
    """下载阶段仍校验远端 ``sha256sums-<target>.txt``（旁文件摘要来自校验后的字节）。"""
    text = CI_WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "sha256sums-x86_64-pc-windows-msvc.txt" in text
    assert "SHA256 mismatch" in text
