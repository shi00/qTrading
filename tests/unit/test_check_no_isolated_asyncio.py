"""Tests for scripts/check_no_isolated_asyncio.py（pre-commit no-isolated-asyncio-testcase）。

验证：
- 真实继承 + 行尾「manual loop management」备注 → exit 1（不再被短语放行）
- 仅注释引用 → 通过
- 多行 docstring 引用 → 通过
- 无法 tokenize 的文件 → exit 1（fail-closed）
- 无参数 → 通过
"""

import sys
from pathlib import Path

import pytest

# 纯脚本工具测试，不触及外部边界，无需 conftest 的全局自动 mock
pytestmark = [pytest.mark.unit, pytest.mark.meta, pytest.mark.no_auto_mock]

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from check_no_isolated_asyncio import main  # noqa: E402 - sys.path 注入后导入


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def _run(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *paths: Path) -> tuple[int, str]:
    """以给定路径为 CLI 参数运行 main，返回 (退出码, stdout)。"""
    monkeypatch.setattr(sys, "argv", ["check_no_isolated_asyncio.py", *(str(p) for p in paths)])
    code = 0
    try:
        main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    return code, capsys.readouterr().out


class TestCheckNoIsolatedAsyncio:
    def test_real_inheritance_with_note_is_violation(self, tmp_path, monkeypatch, capsys):
        """真实继承即使带「manual loop management」备注也必须 exit 1。"""
        path = _write(
            tmp_path,
            "test_real.py",
            "import unittest\n\n\nclass TestX(unittest.IsolatedAsyncioTestCase):  # manual loop management\n    pass\n",
        )
        code, out = _run(monkeypatch, capsys, path)
        assert code == 1
        assert "IsolatedAsyncioTestCase is forbidden" in out
        assert f"{path}:4" in out

    def test_comment_reference_passes(self, tmp_path, monkeypatch, capsys):
        """仅注释引用 IsolatedAsyncioTestCase 不算违规。"""
        path = _write(
            tmp_path,
            "test_comment.py",
            "# 不使用 IsolatedAsyncioTestCase，改用 pytest-asyncio\nx = 1\n",
        )
        code, out = _run(monkeypatch, capsys, path)
        assert code == 0
        assert out == ""

    def test_multiline_docstring_reference_passes(self, tmp_path, monkeypatch, capsys):
        """多行 docstring 中的引用不产生 NAME token，应通过。"""
        path = _write(
            tmp_path,
            "test_docstring.py",
            '"""模块说明。\n\n禁用 IsolatedAsyncioTestCase，未闭合引号也由 tokenize 正确处理。\n"""\n\n\ndef test_x():\n'
            "    assert True\n",
        )
        code, out = _run(monkeypatch, capsys, path)
        assert code == 0
        assert out == ""

    def test_untokenizable_file_fails_closed(self, tmp_path, monkeypatch, capsys):
        """tokenize 失败（未闭合括号）必须 exit 1 并报告无法完成检查。"""
        path = _write(tmp_path, "test_broken.py", "x = (\n")
        code, out = _run(monkeypatch, capsys, path)
        assert code == 1
        assert "无法完成 Python token 检查" in out

    def test_without_arguments_passes(self, monkeypatch, capsys):
        """无参数时无检查对象，应通过。"""
        code, out = _run(monkeypatch, capsys)
        assert code == 0
        assert out == ""
