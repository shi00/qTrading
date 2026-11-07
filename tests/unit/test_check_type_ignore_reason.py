"""Tests for scripts/check_type_ignore_reason.py（review07-G5 R3 务实版检查）。

验证：
- 裸 # type: ignore（无 error-code）一律 error：生产代码与 tests/ 同判（R3 INVARIANT）
- 裸 ignore 的行尾空白/tab 变体不得漏判（旧正则 `$` 前有空白即漏）
- tests/ # type: ignore[attr-defined] → 豁免（无 human reason 不报）
- tests/ 非 attr-defined 无 human reason → warning（渐进部署）
- tests/ 非 attr-defined 带 human reason → 通过
- 仅检查 COMMENT token：字符串/文档字符串中的示例文本不误报
- 无法 tokenize 的文件 fail-closed 报错
"""

import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.meta]

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from check_type_ignore_reason import check_file, main  # noqa: E402 - sys.path 注入后导入

# 测试输入经字符串拼接构造规范形态的裸 ignore，避免本文件源码文本被 R3 检查器自拦截（自指场景）
BARE_IGNORE = "# type:" + " ignore"


def _check(content: str, *, test_file: bool = True) -> tuple[list[str], list[str]]:
    """在临时文件上运行 check_file；test_file=True 时路径含 tests/ 前缀。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        if test_file:
            p = Path(tmp) / "tests" / "test_x.py"
            p.parent.mkdir(parents=True, exist_ok=True)
        else:
            p = Path(tmp) / "prod_x.py"
        p.write_text(content, encoding="utf-8")
        return check_file(p)


class TestCheckTypeIgnoreReason:
    def test_prod_bare_ignore_is_error(self):
        """生产代码 # type: ignore（无 error-code）→ error。"""
        errors, warnings = _check(f"x = 1  {BARE_IGNORE}\n", test_file=False)
        assert len(errors) == 1
        assert "R3" in errors[0]
        assert warnings == []

    def test_prod_bare_ignore_with_reason_is_error(self):
        """生产代码裸 ignore 即使带 human reason（无 error-code）→ error。"""
        errors, warnings = _check(f"x = 1  {BARE_IGNORE}  # 临时压制\n", test_file=False)
        assert len(errors) == 1
        assert "R3" in errors[0]
        assert warnings == []

    def test_tests_bare_ignore_is_error(self):
        """tests/ # type: ignore（无 error-code）→ error（R3 INVARIANT，与生产同判，不静默跳过）。"""
        errors, warnings = _check(f"x = 1  {BARE_IGNORE}\n", test_file=True)
        assert len(errors) == 1
        assert "R3" in errors[0]
        assert warnings == []

    def test_tests_bare_ignore_with_reason_is_error(self):
        """tests/ 裸 ignore 即使带 human reason（无 error-code）→ error。"""
        errors, warnings = _check(f"x = 1  {BARE_IGNORE}  # 临时压制\n", test_file=True)
        assert len(errors) == 1
        assert "R3" in errors[0]
        assert warnings == []

    def test_prod_coded_ignore_passes(self):
        """生产代码 # type: ignore[attr-defined] → 通过（非裸 ignore）。"""
        errors, warnings = _check("x = 1  # type: ignore[attr-defined]\n", test_file=False)
        assert errors == []
        assert warnings == []

    def test_tests_attr_defined_exempted(self):
        """tests/ # type: ignore[attr-defined]（mock 替身）→ 豁免，无 warning。"""
        errors, warnings = _check("mock_obj.foo  # type: ignore[attr-defined]\n", test_file=True)
        assert errors == []
        assert warnings == []

    def test_tests_non_attr_without_reason_warns(self):
        """tests/ # type: ignore[arg-type] 无 human reason → warning（渐进部署不阻断）。"""
        errors, warnings = _check("f(x)  # type: ignore[arg-type]\n", test_file=True)
        assert errors == []
        assert len(warnings) == 1
        assert "arg-type" in warnings[0]

    def test_tests_non_attr_with_reason_passes(self):
        """tests/ # type: ignore[arg-type] 带 human reason → 通过。"""
        errors, warnings = _check("f(x)  # type: ignore[arg-type]  # 故意传错类型验证错误处理\n", test_file=True)
        assert errors == []
        assert warnings == []

    def test_plain_line_passes(self):
        """无 type: ignore 的行不影响结果。"""
        errors, warnings = _check("def test_x():\n    assert 1 == 1\n", test_file=True)
        assert errors == []
        assert warnings == []

    def test_bare_ignore_two_trailing_spaces_exit_1(self, tmp_path, monkeypatch):
        """`# type: ignore` 后两个空格（旧正则漏判）→ error 且 exit 1。"""
        p = tmp_path / "prod_x.py"
        p.write_text("x = 1  # type: ignore  \n", encoding="utf-8")
        errors, warnings = check_file(p)
        assert len(errors) == 1
        assert "R3" in errors[0]
        assert warnings == []
        monkeypatch.setattr(sys, "argv", ["check_type_ignore_reason.py", str(p)])
        assert main() == 1
        assert ":1:" in errors[0]

    def test_bare_ignore_trailing_tab_exit_1(self, tmp_path, monkeypatch):
        """`# type: ignore` 后接 tab → error 且 exit 1。"""
        p = tmp_path / "prod_x.py"
        p.write_text("x = 1  # type: ignore\t\n", encoding="utf-8")
        errors, warnings = check_file(p)
        assert len(errors) == 1
        assert "R3" in errors[0]
        assert warnings == []
        monkeypatch.setattr(sys, "argv", ["check_type_ignore_reason.py", str(p)])
        assert main() == 1

    def test_bare_ignore_multiple_trailing_spaces_exit_1(self, tmp_path, monkeypatch):
        """`# type: ignore` 后接多个空白 → error 且 exit 1。"""
        p = tmp_path / "prod_x.py"
        p.write_text("x = 1  # type: ignore     \n", encoding="utf-8")
        errors, warnings = check_file(p)
        assert len(errors) == 1
        assert "R3" in errors[0]
        assert warnings == []
        monkeypatch.setattr(sys, "argv", ["check_type_ignore_reason.py", str(p)])
        assert main() == 1

    def test_bare_ignore_with_inline_reason_exit_1(self, tmp_path, monkeypatch):
        """`# type: ignore  # 原因`（裸压制加人话理由）→ 仍需 error-code，exit 1。"""
        p = tmp_path / "prod_x.py"
        p.write_text("x = 1  # type: ignore  # 原因\n", encoding="utf-8")
        errors, warnings = check_file(p)
        assert len(errors) == 1
        assert "R3" in errors[0]
        assert warnings == []
        monkeypatch.setattr(sys, "argv", ["check_type_ignore_reason.py", str(p)])
        assert main() == 1

    def test_line_number_kept_from_comment_token(self):
        """多行文件中的裸 ignore 按 COMMENT token 行号报错。"""
        errors, _ = _check("a = 1\nb = 2\nc = 3  # type: ignore\n", test_file=False)
        assert len(errors) == 1
        assert ":3:" in errors[0]

    def test_string_literal_example_not_reported(self):
        """字符串/文档字符串中的 `# type: ignore` 示例文本不误报。"""
        content = 'SAMPLE = "# type: ignore"\nDOC = """\n# type: ignore\n"""\ny = 1\n'
        errors, warnings = _check(content, test_file=False)
        assert errors == []
        assert warnings == []

    def test_tests_coded_without_reason_warns_exit_0(self, tmp_path, monkeypatch):
        """tests/ 下 coded 无 reason → WARNING 且 exit 0。"""
        p = tmp_path / "tests" / "test_x.py"
        p.parent.mkdir(parents=True)
        p.write_text("f(x)  # type: ignore[arg-type]\n", encoding="utf-8")
        errors, warnings = check_file(p)
        assert errors == []
        assert len(warnings) == 1
        monkeypatch.setattr(sys, "argv", ["check_type_ignore_reason.py", str(p)])
        assert main() == 0

    def test_untokenizable_file_is_error(self, tmp_path, monkeypatch):
        """无法 tokenize 的文件 fail-closed：报错并 exit 1。"""
        p = tmp_path / "prod_x.py"
        p.write_text("def broken(:\n", encoding="utf-8")
        errors, warnings = check_file(p)
        assert len(errors) == 1
        assert "无法完成 type: ignore 注释检查" in errors[0]
        assert warnings == []
        monkeypatch.setattr(sys, "argv", ["check_type_ignore_reason.py", str(p)])
        assert main() == 1
