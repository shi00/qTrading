"""UIR-F15-005：发行物必须交付与 sidecar binary 同目录同名的 ``<exe>.sha256`` 旁文件。

运行前完整性校验（``EmbeddedPostgresService._verify_sidecar_sha256``）在打包（frozen）布局下
读取 ``_internal/sidecars/<exe>.sha256``；本测试守护打包配方确实按实际打包字节生成该旁文件、
随 binary 收集到同一目录，且 installer.iss 将其与 binary 一并安装。同时守护下载阶段的
供应链校验（远端 ``sha256sums-<target>.txt``）未被移除。
"""

from __future__ import annotations

import ast
import hashlib
import shutil
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = PROJECT_ROOT / "AStockScreener.spec"
INSTALLER_PATH = PROJECT_ROOT / "installer.iss"
CI_WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "ci_cd.yml"

_HELPER_NAME = "_stage_sidecar_with_sha256"
_CLEANUP_NAME = "_cleanup_stage_dirs"
_STAGE_DIRS_NAME = "_stage_dirs"
_SIDECAR_NAME = "qtrading-pg-sidecar.exe"


def _function_source(name: str) -> str:
    """从 spec 中提取指定函数源码（spec 依赖 PyInstaller 注入的全局变量，无法直接 import）。"""
    source = SPEC_PATH.read_text(encoding="utf-8")
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            segment = ast.get_source_segment(source, node)
            assert segment is not None
            return segment
    raise AssertionError(f"AStockScreener.spec 缺少函数 {name}")


def _helper_source() -> str:
    return _function_source(_HELPER_NAME)


def _run_helper(binary: Path) -> dict[str, Any]:
    """在注入 binaries/datas/_stage_dirs 的最小命名空间中执行辅助函数，返回该命名空间。"""
    namespace: dict[str, Any] = {
        "binaries": [],
        "datas": [],
        "hashlib": hashlib,
        "tempfile": tempfile,
        "shutil": shutil,
        "Path": Path,
        _STAGE_DIRS_NAME: [],
    }
    exec(compile(_helper_source(), str(SPEC_PATH), "exec"), namespace)
    namespace[_HELPER_NAME](binary)
    return namespace


def test_helper_stages_binary_and_side_file_into_sidecars(tmp_path: Path) -> None:
    """binary 与同名 .sha256 旁文件都收集到 ``sidecars``，旁文件摘要等于 binary 实际字节摘要。"""
    binary = tmp_path / _SIDECAR_NAME
    binary.write_bytes(b"sidecar-binary-bytes")

    namespace = _run_helper(binary)
    binaries, datas = namespace["binaries"], namespace["datas"]

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
    # 临时目录登记待 atexit 清理（构建结束不残留）
    assert namespace[_STAGE_DIRS_NAME] == [side_path.parent]


def test_helper_hash_tracks_exact_binary_bytes(tmp_path: Path) -> None:
    """摘要按实际打包字节计算：binary 内容变化（改一字节）→ 旁文件摘要随之变化。"""
    binary = tmp_path / _SIDECAR_NAME
    binary.write_bytes(b"v1")
    first = _run_helper(binary)["datas"]
    binary.write_bytes(b"v2")
    second = _run_helper(binary)["datas"]

    first_digest = Path(first[0][0]).read_text(encoding="utf-8").split()[0]
    second_digest = Path(second[0][0]).read_text(encoding="utf-8").split()[0]
    assert first_digest == hashlib.sha256(b"v1").hexdigest()
    assert second_digest == hashlib.sha256(b"v2").hexdigest()
    assert second_digest != first_digest


def test_spec_registers_atexit_cleanup_for_staged_tempdirs() -> None:
    """spec 在模块级注册 atexit 清理（构建结束/失败均回收临时目录，不残留）。"""
    tree = ast.parse(SPEC_PATH.read_text(encoding="utf-8"))

    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert {"atexit", "shutil"}.issubset(imported), "spec 未导入 atexit / shutil"

    registrations = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "register"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "atexit"
    ]
    assert registrations, "spec 未调用 atexit.register"
    assert any(isinstance(arg, ast.Name) and arg.id == _CLEANUP_NAME for call in registrations for arg in call.args), (
        f"atexit 注册的函数应为 {_CLEANUP_NAME}"
    )

    assert f"{_STAGE_DIRS_NAME}.append(stage_dir)" in _helper_source(), "辅助函数未登记 mkdtemp 临时目录"


def test_cleanup_removes_staged_tempdirs_after_collect(tmp_path: Path) -> None:
    """atexit 清理函数删除旁文件临时目录，且不影响已写入 datas 的收集项语义。"""
    binary = tmp_path / _SIDECAR_NAME
    binary.write_bytes(b"sidecar-binary-bytes")

    namespace = _run_helper(binary)
    side_path = Path(namespace["datas"][0][0])
    stage_dir = side_path.parent
    assert stage_dir.is_dir()
    assert namespace["datas"][0][1] == "sidecars"  # 清理前收集项仍指向 site 目录

    exec(compile(_function_source(_CLEANUP_NAME), str(SPEC_PATH), "exec"), namespace)
    namespace[_CLEANUP_NAME]()

    assert not stage_dir.exists(), "构建结束临时目录未清理"
    assert namespace[_STAGE_DIRS_NAME] == []


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
