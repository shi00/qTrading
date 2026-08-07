"""Tests for scripts/check_e2e_anchors.py EIDS 引用合法性检查。

验证:
- 纯函数测试：构造 AST 验证 EIDS.X.Y 引用检测逻辑（合法/非法/动态方法/字符串误匹配）
- 集成测试：临时目录构造 EIDS 定义 + 引用文件，验证 check_eids_refs 端到端行为
- 契约测试：当前代码库所有 EIDS 引用合法（main() 返回 0）
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.meta]

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from check_e2e_anchors import (  # noqa: E402 - sys.path 注入后导入
    _check_eids_refs_in_tree,
    _extract_eids_namespaces,
    check_eids_refs,
    main,
)

# 固定的合法命名空间（用于纯函数测试，与真实 EIDS 解耦）
_VALID_NS: dict[str, set[str]] = {
    "SCREENER": {"STRATEGY_DROPDOWN", "RUN_BUTTON", "result_row", "column_header"},
    "NAV": {"MARKET", "SCREENER", "BACKTEST"},
}


# ============================================================================
# _extract_eids_namespaces: EIDS 定义侧静态分析
# ============================================================================


class TestExtractEidsNamespaces:
    """_extract_eids_namespaces: 从 e2e_ids.py 静态分析命名空间。"""

    def test_extracts_real_eids_file(self):
        """对真实 ui/testing/e2e_ids.py 应提取出 9 个命名空间。"""
        eids_path = ROOT / "ui" / "testing" / "e2e_ids.py"
        ns = _extract_eids_namespaces(eids_path)
        assert set(ns.keys()) == {
            "SCREENER",
            "DETAIL_DIALOG",
            "SETTINGS",
            "DATA",
            "BACKTEST",
            "WIZARD",
            "NAV",
            "HOME",
            "TASK_CENTER",
        }
        # SCREENER 应含常量 + 动态方法
        assert "RUN_BUTTON" in ns["SCREENER"]
        assert "STRATEGY_DROPDOWN" in ns["SCREENER"]
        assert "result_row" in ns["SCREENER"]
        assert "column_header" in ns["SCREENER"]
        # SETTINGS 应含 tab 动态方法
        assert "tab" in ns["SETTINGS"]
        # TASK_CENTER 应含 task_row 动态方法
        assert "task_row" in ns["TASK_CENTER"]

    def test_extracts_synthetic_eids(self, tmp_path):
        """构造最小 EIDS 定义文件，验证提取逻辑（含私有属性与 staticmethod）。"""
        eids_file = tmp_path / "e2e_ids.py"
        eids_file.write_text(
            "class _FooIds:\n"
            "    BAR: int = 1\n"
            "    _PRIV: str = 'x'\n"
            "    BAZ = 2\n"
            "    @staticmethod\n"
            "    def gen(s: str) -> int:\n"
            "        return 1\n"
            "class EIDS:\n"
            "    FOO = _FooIds\n",
            encoding="utf-8",
        )
        ns = _extract_eids_namespaces(eids_file)
        assert ns == {"FOO": {"BAR", "_PRIV", "BAZ", "gen"}}

    def test_returns_empty_on_unparseable(self, tmp_path):
        """语法错误的文件返回空 dict（不抛异常）。"""
        bad = tmp_path / "bad.py"
        bad.write_text("class !@#$\n", encoding="utf-8")
        assert _extract_eids_namespaces(bad) == {}

    def test_returns_empty_on_missing_eids_class(self, tmp_path):
        """无 class EIDS: 时返回空 dict。"""
        eids_file = tmp_path / "e2e_ids.py"
        eids_file.write_text("class _FooIds:\n    BAR = 1\n", encoding="utf-8")
        assert _extract_eids_namespaces(eids_file) == {}


# ============================================================================
# _check_eids_refs_in_tree: EIDS 引用检测纯函数
# ============================================================================


class TestCheckEidsRefsInTree:
    """_check_eids_refs_in_tree: 纯函数测试 EIDS.X.Y 引用检测。"""

    def _check(self, code: str) -> list[str]:
        tree = ast.parse(code)
        fake_path = ROOT / "ui" / "fake.py"
        return _check_eids_refs_in_tree(tree, fake_path, _VALID_NS)

    def test_valid_constant_ref_not_flagged(self):
        """EIDS.SCREENER.RUN_BUTTON 合法常量引用不报错。"""
        assert self._check("x = EIDS.SCREENER.RUN_BUTTON\n") == []

    def test_valid_method_call_not_flagged(self):
        """EIDS.SCREENER.result_row('000001.SZ') 动态方法调用不报错。"""
        assert self._check('x = EIDS.SCREENER.result_row("000001.SZ")\n') == []

    def test_valid_method_ref_not_flagged(self):
        """EIDS.SCREENER.column_header 作为回调传递（不调用）不报错。"""
        assert self._check("cb = EIDS.SCREENER.column_header\n") == []

    def test_method_return_subscript_not_flagged(self):
        """EIDS.SCREENER.result_row(...)[0] 方法返回值下标访问不报错。"""
        assert self._check('x = EIDS.SCREENER.result_row("000001.SZ")[0]\n') == []

    def test_nonexistent_attr_flagged(self):
        """EIDS.SCREENER.NONEXISTENT 不存在属性应报错。"""
        errors = self._check("x = EIDS.SCREENER.NONEXISTENT\n")
        assert len(errors) == 1
        assert "EIDS.SCREENER.NONEXISTENT" in errors[0]

    def test_nonexistent_namespace_flagged(self):
        """EIDS.NONEXISTENT.X 不存在命名空间应报错。"""
        errors = self._check("x = EIDS.NONEXISTENT.X\n")
        assert len(errors) == 1
        assert "EIDS.NONEXISTENT.X" in errors[0]

    def test_error_message_contains_file_line(self):
        """错误信息含 file:line 前缀。"""
        errors = self._check("x = EIDS.SCREENER.NONEXISTENT\n")
        assert "fake.py:1" in errors[0]

    def test_string_literal_not_flagged(self):
        """字符串中的 'EIDS.X.Y' 不被误匹配（AST 不进入字符串字面量）。"""
        assert self._check('msg = "EIDS.SCREENER.NONEXISTENT is broken"\n') == []

    def test_comment_not_flagged(self):
        """注释中的 EIDS.X.Y 不被误匹配。"""
        assert self._check("# EIDS.SCREENER.NONEXISTENT\nx = 1\n") == []

    def test_non_eids_attribute_not_flagged(self):
        """非 EIDS 开头的属性访问不报错（如 foo.SCREENER.RUN_BUTTON）。"""
        assert self._check("x = foo.SCREENER.RUN_BUTTON\n") == []

    def test_eids_alone_not_flagged(self):
        """单独 EIDS 引用（无 .X.Y）不报错。"""
        assert self._check("x = EIDS\n") == []

    def test_eids_one_level_not_flagged(self):
        """EIDS.SCREENER（仅一层）不报错。"""
        assert self._check("x = EIDS.SCREENER\n") == []

    def test_multiple_violations_all_reported(self):
        """多个非法引用全部上报。"""
        code = "a = EIDS.SCREENER.BAD1\nb = EIDS.NAV.BAD2\nc = EIDS.BAD_NS.X\n"
        errors = self._check(code)
        assert len(errors) == 3


# ============================================================================
# check_eids_refs: 集成测试（临时目录 + monkeypatch ROOT）
# ============================================================================


def _setup_eids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """在 tmp_path 下构造最小 EIDS 定义文件 + ui/ tests/ 目录，并 monkeypatch ROOT。"""
    import check_e2e_anchors

    monkeypatch.setattr(check_e2e_anchors, "ROOT", tmp_path)
    eids_file = tmp_path / "ui" / "testing" / "e2e_ids.py"
    eids_file.parent.mkdir(parents=True, exist_ok=True)
    eids_file.write_text(
        "class _ScreenerIds:\n"
        "    RUN_BUTTON: int = 1\n"
        "    @staticmethod\n"
        "    def result_row(ts_code: str) -> int:\n"
        "        return 1\n"
        "class EIDS:\n"
        "    SCREENER = _ScreenerIds\n",
        encoding="utf-8",
    )
    (tmp_path / "ui").mkdir(exist_ok=True)
    (tmp_path / "tests").mkdir(exist_ok=True)


class TestCheckEidsRefsIntegration:
    """check_eids_refs: 集成测试（临时目录 + monkeypatch ROOT）。"""

    def test_valid_refs_pass(self, tmp_path, monkeypatch):
        """合法引用文件扫描通过。"""
        _setup_eids(tmp_path, monkeypatch)
        ref_file = tmp_path / "ui" / "view.py"
        ref_file.write_text(
            "from ui.testing.e2e_ids import EIDS\n"
            "x = EIDS.SCREENER.RUN_BUTTON\n"
            "y = EIDS.SCREENER.result_row('000001.SZ')\n",
            encoding="utf-8",
        )
        assert check_eids_refs() == []

    def test_invalid_ref_detected(self, tmp_path, monkeypatch):
        """非法引用文件扫描报错。"""
        _setup_eids(tmp_path, monkeypatch)
        ref_file = tmp_path / "ui" / "view.py"
        ref_file.write_text(
            "from ui.testing.e2e_ids import EIDS\nx = EIDS.SCREENER.NONEXISTENT\n",
            encoding="utf-8",
        )
        errors = check_eids_refs()
        assert len(errors) == 1
        assert "EIDS.SCREENER.NONEXISTENT" in errors[0]

    def test_invalid_namespace_detected(self, tmp_path, monkeypatch):
        """非法命名空间引用报错。"""
        _setup_eids(tmp_path, monkeypatch)
        ref_file = tmp_path / "tests" / "test_x.py"
        ref_file.write_text("x = EIDS.NONEXISTENT.X\n", encoding="utf-8")
        errors = check_eids_refs()
        assert len(errors) == 1
        assert "EIDS.NONEXISTENT.X" in errors[0]

    def test_skips_eids_definition_file(self, tmp_path, monkeypatch):
        """EIDS 定义文件自身的 EIDS.X = _Y 赋值不被扫描（非引用）。"""
        _setup_eids(tmp_path, monkeypatch)
        # 定义文件中 SCREENER = _ScreenerIds 不是引用，不应报错
        assert check_eids_refs() == []

    def test_skips_mock_assets_dir(self, tmp_path, monkeypatch):
        """mock_assets 目录下的引用不被扫描。"""
        _setup_eids(tmp_path, monkeypatch)
        bad_file = tmp_path / "tests" / "e2e" / "mock_assets" / "bad.py"
        bad_file.parent.mkdir(parents=True, exist_ok=True)
        bad_file.write_text("x = EIDS.SCREENER.NONEXISTENT\n", encoding="utf-8")
        assert check_eids_refs() == []

    def test_skips_pycache_dir(self, tmp_path, monkeypatch):
        """__pycache__ 目录下的引用不被扫描。"""
        _setup_eids(tmp_path, monkeypatch)
        bad_file = tmp_path / "ui" / "__pycache__" / "bad.py"
        bad_file.parent.mkdir(parents=True, exist_ok=True)
        bad_file.write_text("x = EIDS.SCREENER.NONEXISTENT\n", encoding="utf-8")
        assert check_eids_refs() == []


# ============================================================================
# 契约测试：当前代码库应通过所有 EIDS 引用检查
# ============================================================================


class TestContract:
    """契约测试：当前代码库所有 EIDS 引用合法。"""

    def test_check_eids_refs_passes_on_clean_repo(self):
        """check_eids_refs() 在当前代码库应返回空 list（无违规）。"""
        errors = check_eids_refs()
        assert errors == [], "EIDS 引用违规:\n  " + "\n  ".join(errors)

    def test_main_returns_zero_on_clean_repo(self):
        """main() 在当前代码库应返回 0。"""
        assert main() == 0, "check_e2e_anchors.py main() 应返回 0"
