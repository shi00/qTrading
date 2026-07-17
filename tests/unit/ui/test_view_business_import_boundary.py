"""ui/views/ 业务对象导入边界守护测试 (Task 5.1).

CLAUDE.md §3.2 MVVM 契约: View = f(ViewModel.state), 不应直接调用 data/strategies
业务对象。本测试扫描 ``ui/views/`` 下所有 .py 源码（含函数体 lazy import + 模块级
import + 符号引用），强制 View 通过 ViewModel command/state 消费业务逻辑。

与 ``tests/unit/test_architecture_boundaries.py`` 的区别：
- 后者仅检查模块级 import，且 R1 允许 ui → strategies 反向依赖
- 本测试进一步约束 MVVM 契约：View 不能直接 import/use 业务对象，必须经 VM 转发

禁止的业务对象导入/使用模式：
- ``from data.persistence.metadata_manager import MetaDataManager`` 或 ``MetaDataManager.``
- ``from strategies.strategy_prompts import`` (任意名) 或 ``get_base_prompt(``
- ``from data.external.tushare_client import TushareClient`` 或 ``TushareClient(``/``TushareClient.``
- ``from data.constants import TUSHARE_POINT_TIERS`` 或 ``TUSHARE_POINT_TIERS``
- ``from data.data_processor import DataProcessor`` 或 ``DataProcessor(``

白名单 ``ALLOWED_VIEW_BUSINESS_IMPORTS`` 仅在以下场景允许：
- 纯展示常量（不是业务对象）
- View 自身 ViewModels（如 ``DataSourceViewModel`` 不在禁止列表）

白名单条目必须配套注释说明原因，避免变相绕过 MVVM 契约。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
VIEWS_DIR = PROJECT_ROOT / "ui" / "views"


# 禁止的业务对象模式 (regex)。
# 同时覆盖模块级 import / lazy import / 符号引用 三种形式。
# 对 ``get_base_prompt`` 使用 ``(?<!\.)`` 负向后行断言, 允许 ``vm.get_base_prompt(...)``
# (VM 方法调用) 但捕获直接调用 ``get_base_prompt(...)`` (绕过 VM)。
FORBIDDEN_PATTERNS: dict[str, str] = {
    "MetaDataManager": r"(from\s+data\.persistence\.metadata_manager\s+import\s+MetaDataManager)"
    r"|(\bMetaDataManager\s*\.)",
    "strategy_prompts.get_base_prompt": r"(from\s+strategies\.strategy_prompts\s+import\s+)"
    r"|((?<!\.)get_base_prompt\s*\()",
    "TushareClient": r"(from\s+data\.external\.tushare_client\s+import\s+TushareClient)"
    r"|(\bTushareClient\s*[\(\.])",
    "TUSHARE_POINT_TIERS": r"(from\s+data\.constants\s+import\s+[^\n]*TUSHARE_POINT_TIERS)"
    r"|(\bTUSHARE_POINT_TIERS\b)",
    "DataProcessor": r"(from\s+data\.data_processor\s+import\s+DataProcessor)"
    r"|(\bDataProcessor\s*\()",
}

# UI lazy import 白名单：允许 ``ui/views/`` 在特定场景下保留对 data/strategies 的导入。
# 每个条目: (relative_path, forbidden_key, 原因)
# 白名单条目必须配套注释说明原因，避免变相绕过 MVVM 契约。
ALLOWED_VIEW_BUSINESS_IMPORTS: set[tuple[str, str]] = set()
# 当前为空集：Task 5.1 完成后所有迁移点都应迁入 VM, 无保留 lazy import。


def _view_python_files() -> list[Path]:
    """收集 ``ui/views/`` 下所有 .py 文件 (递归)。"""
    if not VIEWS_DIR.exists():
        return []
    return sorted(VIEWS_DIR.rglob("*.py"))


def _source_without_docstrings(source: str) -> str:
    """移除模块/函数/类 docstring 后的源码, 避免文档字符串中提及被禁止的符号导致误判。"""
    import ast

    tree = ast.parse(source)
    docstring_lines: set[int] = set()

    def _collect(
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Module,
    ) -> None:
        body = getattr(node, "body", None)
        if not body:
            return
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            end_lineno = first.end_lineno or first.lineno
            docstring_lines.update(range(first.lineno, end_lineno + 1))

    _collect(tree)  # type: ignore[arg-type]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _collect(node)  # type: ignore[arg-type]

    lines = source.splitlines()
    code_lines = [line for i, line in enumerate(lines, 1) if i not in docstring_lines]
    return "\n".join(code_lines)


def _strip_comments(source: str) -> str:
    """移除 ``#`` 注释行, 避免注释中提及被禁止符号导致误判。

    简单实现: 按行处理, 移除 ``#`` 后内容 (字符串内的 ``#`` 不处理, 可接受近似)。
    """
    out_lines: list[str] = []
    for line in source.splitlines():
        # 移除 # 后内容, 保留代码部分
        hash_idx = line.find("#")
        if hash_idx >= 0:
            line = line[:hash_idx]
        out_lines.append(line)
    return "\n".join(out_lines)


@pytest.mark.unit
def test_no_view_directly_imports_business_objects() -> None:
    """验证 ``ui/views/`` 下所有 .py 文件不直接 import/use 业务对象 (Task 5.1 DoD)。

    扫描源码 (含函数体 lazy import + 模块级 import + 符号引用)，
    禁止 ``MetaDataManager`` / ``get_base_prompt`` / ``TushareClient`` /
    ``TUSHARE_POINT_TIERS`` / ``DataProcessor`` 等业务对象的直接引用。
    View 应通过对应 ViewModel command/state 消费业务逻辑。

    白名单 ``ALLOWED_VIEW_BUSINESS_IMPORTS`` 仅在特定场景允许 (需配套原因注释)。
    """
    violations: list[str] = []
    for py_file in _view_python_files():
        rel_path = py_file.relative_to(PROJECT_ROOT).as_posix()
        raw_source = py_file.read_text(encoding="utf-8")
        # 移除 docstring + 注释, 避免文档/注释中提及被禁止符号导致误判
        source = _strip_comments(_source_without_docstrings(raw_source))

        for forbidden_key, pattern in FORBIDDEN_PATTERNS.items():
            # 白名单检查
            if (rel_path, forbidden_key) in ALLOWED_VIEW_BUSINESS_IMPORTS:
                continue
            matches = re.findall(pattern, source)
            if matches:
                violations.append(
                    f"{rel_path}: forbidden business object '{forbidden_key}' ({len(matches)} occurrence(s))"
                )

    assert not violations, (
        "ui/views/ 直接调用 data/strategies 业务对象, 违反 MVVM 契约 (CLAUDE.md §3.2).\n"
        "应通过对应 ViewModel command/state 消费业务逻辑.\n" + "\n".join(violations)
    )


@pytest.mark.unit
def test_allowed_view_business_imports_are_valid() -> None:
    """白名单条目必须指向真实存在的文件, 避免遗留过期条目。"""
    for rel_path, _forbidden_key in ALLOWED_VIEW_BUSINESS_IMPORTS:
        full_path = PROJECT_ROOT / rel_path
        assert full_path.exists(), (
            f"ALLOWED_VIEW_BUSINESS_IMPORTS contains non-existent file: {rel_path}. "
            "Remove it if the file was deleted or renamed."
        )
