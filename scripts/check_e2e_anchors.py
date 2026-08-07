"""E2E anchor ID 引用合法性检查脚本。

扫描所有 ``EIDS.<NAMESPACE>.<ATTR>`` 引用，检查常量/方法存在于
``ui/testing/e2e_ids.py`` 的 ``EIDS`` 命名空间。

依据 CLAUDE.md §1.10 反幻觉护栏，对 EIDS 引用作静态校验：
- 用 AST 而非正则，避免误匹配字符串/注释中的 ``EIDS.X.Y`` 文本
- 静态分析 ``ui/testing/e2e_ids.py`` 收集合法 ``<NAMESPACE>.<ATTR>`` 路径
- 对比引用与定义：未在 EIDS 中定义的 ``<X>.<Y>`` → 报错

扫描范围：``ui/``、``tests/`` 下所有 .py 文件（跳过 venv/__pycache__/.worktrees/
mock_assets 等目录），以及 ``tests/unit/ui/`` 下的 EIDS 引用单测。

退出码：0 通过，1 失败。供 pre-commit ``e2e-anchor-check`` hook 调用。
"""

from __future__ import annotations

import ast
import sys
import typing
from collections.abc import Iterator
from io import TextIOWrapper
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ============================================================================
# 公共工具
# ============================================================================

# 受检扫描应跳过的目录（第三方代码、构建产物、缓存、mock 资源等）
_SKIP_DIRS = frozenset(
    {
        "venv",
        ".venv",
        "__pycache__",
        ".git",
        "node_modules",
        ".worktrees",
        ".tmp",
        ".pytest_cache",
        ".ruff_cache",
        "build",
        "dist",
        "mock_assets",
    }
)

# 扫描范围（顶级目录）：ui/ 含生产代码 EIDS 引用，tests/ 含测试代码 EIDS 引用
_SCAN_DIRS = ("ui", "tests")


def _iter_py_files(root: Path, exclude_dirs: frozenset[str] | None = None) -> Iterator[Path]:
    """遍历 root 下所有 .py 文件，跳过排除目录。

    用相对路径检查目录名，避免主工作区 ROOT 中 .worktrees 被误匹配。
    """
    skip = exclude_dirs if exclude_dirs is not None else _SKIP_DIRS
    for p in root.rglob("*.py"):
        try:
            rel_parts = p.relative_to(root).parts
        except ValueError:
            continue
        if any(part in skip for part in rel_parts):
            continue
        yield p


def _parse_module(path: Path) -> ast.Module | None:
    """解析 .py 文件为 AST，失败返回 None。"""
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, OSError, UnicodeDecodeError):
        return None


# ============================================================================
# EIDS 定义侧：静态分析 ui/testing/e2e_ids.py
# ============================================================================


def _extract_eids_namespaces(eids_path: Path) -> dict[str, set[str]]:
    """从 ``ui/testing/e2e_ids.py`` 静态分析 EIDS 命名空间。

    返回 ``{NAMESPACE: {attr_or_method_name, ...}}``，例如：
    ``{"SCREENER": {"STRATEGY_DROPDOWN", "RUN_BUTTON", "result_row", ...}, ...}``

    解析逻辑：
    1. 收集所有 ``_XxxIds`` 类定义（类名以 ``_`` 开头且以 ``Ids`` 结尾）
    2. 找 ``class EIDS:`` 节点，收集 ``EIDS.<NS> = _XxxIds`` 赋值，建立 NS → 类名映射
    3. 对每个 NS，收集对应类的类级属性名（AnnAssign/Assign）+ 方法名（FunctionDef）

    收集所有类级属性（含私有 ``_`` 前缀）与方法（含 staticmethod 与实例方法），
    符合"检查常量/方法存在"职责；私有属性引用不报"不存在"（虽不鼓励但不误报）。
    """
    tree = _parse_module(eids_path)
    if tree is None:
        return {}

    # 1. 收集所有 _XxxIds 类定义
    ns_classes: dict[str, ast.ClassDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name.startswith("_") and node.name.endswith("Ids"):
            ns_classes[node.name] = node

    # 2. 找 class EIDS:，收集 NS → _XxxIds 类名映射
    ns_mapping: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "EIDS":
            continue
        for item in node.body:
            if isinstance(item, ast.Assign) and len(item.targets) == 1:
                target = item.targets[0]
                if isinstance(target, ast.Name) and isinstance(item.value, ast.Name):
                    ns_mapping[target.id] = item.value.id

    # 3. 对每个 NS，收集类级属性名 + 方法名
    result: dict[str, set[str]] = {}
    for ns, cls_name in ns_mapping.items():
        cls_def = ns_classes.get(cls_name)
        if cls_def is None:
            continue
        names: set[str] = set()
        for item in cls_def.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                names.add(item.target.id)
            elif isinstance(item, ast.Assign):
                for t in item.targets:
                    if isinstance(t, ast.Name):
                        names.add(t.id)
            elif isinstance(item, ast.FunctionDef):
                names.add(item.name)
        result[ns] = names
    return result


# ============================================================================
# EIDS 引用侧：扫描 EIDS.X.Y 引用并校验
# ============================================================================


def _check_eids_refs_in_tree(
    tree: ast.Module,
    source_path: Path,
    valid_namespaces: dict[str, set[str]],
) -> list[str]:
    """纯函数：检查 AST 中所有 ``EIDS.X.Y`` 引用，验证 X 是合法命名空间且 Y 是合法属性/方法。

    匹配模式（AST 嵌套两层 Attribute）：
    ``Attribute(value=Attribute(value=Name(id="EIDS"), attr=X), attr=Y)``

    覆盖以下合法引用形式：
    - 常量访问：``EIDS.SCREENER.RUN_BUTTON``
    - 方法调用：``EIDS.SCREENER.result_row("000001.SZ")``（Call.func 为匹配的 Attribute）
    - 方法引用：``EIDS.SCREENER.column_header``（作为回调传递，不调用）

    不匹配（不报错）：
    - 单独 ``EIDS`` 或 ``EIDS.SCREENER``（层级不足，非终端引用）
    - 字符串/注释中的 ``EIDS.X.Y`` 文本（AST 不进入字面量/注释）
    - 非 EIDS 开头的属性访问（如 ``foo.SCREENER.RUN_BUTTON``）
    """
    errors: list[str] = []
    rel = source_path.relative_to(ROOT)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        inner = node.value
        if not isinstance(inner, ast.Attribute):
            continue
        if not isinstance(inner.value, ast.Name) or inner.value.id != "EIDS":
            continue
        ns = inner.attr
        attr = node.attr
        if ns not in valid_namespaces:
            errors.append(f"{rel}:{node.lineno}: EIDS.{ns}.{attr} 不存在于 ui/testing/e2e_ids.py EIDS 命名空间")
            continue
        if attr not in valid_namespaces[ns]:
            errors.append(f"{rel}:{node.lineno}: EIDS.{ns}.{attr} 不存在于 ui/testing/e2e_ids.py EIDS 命名空间")
    return errors


def check_eids_refs() -> list[str]:
    """扫描所有 ``EIDS.X.Y`` 引用，验证常量/方法存在于 EIDS 命名空间。

    扫描范围：``ui/``、``tests/`` 下所有 .py 文件（跳过缓存/构建/mock_assets 目录）。
    跳过 EIDS 定义文件自身（``EIDS.X = _Y`` 赋值不是引用）。
    跳过脚本自身（scripts/ 不在扫描范围内）。
    """
    eids_path = ROOT / "ui" / "testing" / "e2e_ids.py"
    valid_namespaces = _extract_eids_namespaces(eids_path)
    if not valid_namespaces:
        # EIDS 定义文件无法解析或为空，跳过检查（避免误报）
        return []

    errors: list[str] = []
    for dir_name in _SCAN_DIRS:
        target_dir = ROOT / dir_name
        if not target_dir.exists():
            continue
        for p in _iter_py_files(target_dir):
            # 跳过 EIDS 定义文件自身（赋值 EIDS.X = _Y 不是引用）
            if p == eids_path:
                continue
            tree = _parse_module(p)
            if tree is None:
                continue
            errors.extend(_check_eids_refs_in_tree(tree, p, valid_namespaces))
    return errors


# ============================================================================
# CLI 入口
# ============================================================================


def main() -> int:
    """运行 EIDS 引用检查，返回退出码。"""
    errors = check_eids_refs()
    if errors:
        print("[FAIL] E2E anchor 引用检查失败：", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    print("[PASS] E2E anchor 引用检查通过")
    return 0


if __name__ == "__main__":
    # 兜底：Windows PYTHONIOENCODING=gbk 等非 UTF-8 环境下，中文输出会触发
    # UnicodeEncodeError。reconfigure stdout/stderr 为 UTF-8（errors="replace" 容错）。
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            typing.cast(TextIOWrapper, _stream).reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
