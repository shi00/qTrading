"""MVVM View ``@ft.component`` 守护测试 (Phase 5 Task 5.1).

遍历 ``ui/views/**/*.py`` (含 ``ui/views/settings_tabs/``)，
验证每个模块级大写开头 def 函数都被 ``@ft.component`` 装饰
(通过 ``__wrapped__`` 属性检测，CLAUDE.md §3.2 MVVM 强制要求)。

白名单：
- 文件级：``__init__.py`` (无 view 定义)

参考: ``tests/unit/test_no_class_attr_asyncio_primitives.py`` 的 AST 扫描风格。
"""

import ast
import importlib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent

SCAN_DIR = PROJECT_ROOT / "ui" / "views"

# 文件级白名单：__init__.py 无 view 定义
FILE_WHITELIST: frozenset[str] = frozenset({"__init__.py"})


def _is_view_function_name(name: str) -> bool:
    """判断函数名是否符合 View 命名约定：首字母大写 (PascalCase)，不以 ``_`` 开头。"""
    return bool(name) and not name.startswith("_") and name[0].isupper()


def _extract_view_function_names(path: Path) -> list[str]:
    """从文件 AST 提取模块级大写开头的 def 函数名（排除 ``_`` 前缀私有函数）。"""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    names: list[str] = []
    for node in tree.body:  # 仅模块级
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if _is_view_function_name(node.name):
                names.append(node.name)
    return names


def _module_dotted_path(path: Path) -> str:
    """将文件路径转为点分模块路径 (``ui/views/screener_view.py`` → ``ui.views.screener_view``)."""
    rel = path.relative_to(PROJECT_ROOT)
    return rel.with_suffix("").as_posix().replace("/", ".")


def _collect_view_files() -> list[Path]:
    """收集 ``ui/views/`` 下所有 .py 文件 (含 ``settings_tabs/``)，排除白名单。"""
    return [p for p in SCAN_DIR.rglob("*.py") if p.name not in FILE_WHITELIST]


@pytest.mark.unit
def test_all_views_are_ft_component() -> None:
    """Phase 5 Task 5.1 MVVM 守护：遍历 ``ui/views/**/*.py``，验证每个模块级
    大写开头 def 函数都被 ``@ft.component`` 装饰 (通过 ``__wrapped__`` 属性检测)。

    若失败，说明存在 View 函数未声明为声明式组件，
    违反 CLAUDE.md §3.2 MVVM 强制要求 (View = ``@ft.component`` 声明式组件)。
    """
    violations: list[str] = []
    files_scanned = 0
    functions_checked = 0

    for path in _collect_view_files():
        files_scanned += 1
        rel_path = path.relative_to(PROJECT_ROOT).as_posix()
        names = _extract_view_function_names(path)
        if not names:
            continue
        module_path = _module_dotted_path(path)
        try:
            module = importlib.import_module(module_path)
        except Exception as e:
            violations.append(f"{rel_path}: 模块 import 失败 ({type(e).__name__}: {e})")
            continue
        for name in names:
            func = getattr(module, name, None)
            if func is None:
                violations.append(f"{rel_path}: 函数 {name} 未在模块中找到")
                continue
            functions_checked += 1
            if not hasattr(func, "__wrapped__"):
                violations.append(f"{rel_path}: 函数 {name} 缺少 @ft.component 装饰器 (没有 __wrapped__ 属性)")

    assert not violations, (
        f"MVVM 守护失败：检测到 {len(violations)} 处 View 函数未声明为 @ft.component "
        f"(扫描 {files_scanned} 个文件, 检查 {functions_checked} 个函数)。\n"
        "CLAUDE.md §3.2 强制：View = @ft.component 声明式组件。\n" + "\n".join(f"  - {v}" for v in violations)
    )
