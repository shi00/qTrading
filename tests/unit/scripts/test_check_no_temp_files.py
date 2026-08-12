"""Tests for scripts/check_no_temp_files.py 编译/二进制产物拦截钩子。

验证:
- 黑名单扩展名被拦截（SystemExit code=1）
- 扩展名大小写不敏感（.DLL 同样拦截）
- 显式 allowlist 文件放行（正常返回）
- 无扩展名文件、白名单扩展名文件（.py/.wasm/.woff2）不拦截

脚本的 main() 从 sys.argv 读取文件列表，故测试通过 monkeypatch 注入 argv。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.meta]

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from check_no_temp_files import _FORBIDDEN_EXTENSIONS, main  # noqa: E402


def _run_main(monkeypatch: pytest.MonkeyPatch, *files: str) -> str | int | None:
    """以给定文件列表执行 main()，返回退出码（命中拦截为 1，放行为 None）。"""
    monkeypatch.setattr(sys, "argv", ["check_no_temp_files.py", *files])
    try:
        main()
        return None
    except SystemExit as exc:
        return exc.code


def test_forbidden_extensions_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """黑名单扩展名文件必须被拦截。"""
    for f in ("a.dll", "b.exe", "c.pyd", "d.so", "e.zip", "f.tar.gz", "g.pyc", "h.pyo"):
        assert _run_main(monkeypatch, f) == 1, f"{f} 应被拦截"


def test_extension_match_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    """扩展名匹配大小写不敏感。"""
    assert _run_main(monkeypatch, "UPPER.DLL") == 1
    assert _run_main(monkeypatch, "Mixed.EXE") == 1


def test_allowlist_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """显式 allowlist 中的文件放行（正常返回，不抛异常）。"""
    allowlisted = "tests/e2e/mock_assets/allowlisted.dll"
    monkeypatch.setattr("check_no_temp_files._ALLOWLIST", {allowlisted})
    assert _run_main(monkeypatch, allowlisted) is None, f"{allowlisted} 不应被拦截"


def test_normal_source_files_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """普通源码/资源文件不拦截。"""
    for f in ("app/main.py", "tests/test_x.py", "a.wasm", "b.woff2", "c.js", ".gitignore"):
        assert _run_main(monkeypatch, f) is None, f"{f} 不应被拦截"


def test_no_extension_file_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """无扩展名文件不拦截（Unix 可执行如 postgres 的场景，显式记录边界）。"""
    assert _run_main(monkeypatch, "postgres") is None


def test_forbidden_extensions_are_defined() -> None:
    """黑名单包含关键的编译产物扩展名。"""
    assert {".dll", ".exe", ".pyd", ".so", ".zip"} <= _FORBIDDEN_EXTENSIONS
