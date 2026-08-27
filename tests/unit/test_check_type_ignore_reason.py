"""Tests for scripts/check_type_ignore_reason.py（review07-G5 R3 务实版检查）。

验证：
- 生产代码裸 # type: ignore（无 error-code）→ error
- tests/ # type: ignore[attr-defined] → 豁免（无 human reason 不报）
- tests/ 非 attr-defined 无 human reason → warning（渐进部署）
- tests/ 非 attr-defined 带 human reason → 通过
"""

import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.meta]

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from check_type_ignore_reason import check_file  # noqa: E402 - sys.path 注入后导入


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
        errors, warnings = _check("x = 1  # type: ignore\n", test_file=False)
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
