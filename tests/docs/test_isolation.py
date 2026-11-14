"""D3 隔离负例（检视 §6.4「依赖/测试」与 AC14）：纯文档测试环境必须被证明不加载业务夹具。

证明手段（对应任务边界约定）：
1. 静态：tests/docs 全部参与收集的 .py（conftest + test_*）AST import 集不含业务模块；
2. 样本有效性：fixtures/injected_business_imports.py 确实注入了业务 import（import flet），
   且其命名保证不被 pytest 收集（非 test_*.py、非 conftest.py）；
3. 运行时：收集/执行后的 sys.modules 与业务顶层模块交集为空 —— 一旦有人把业务夹具
   混入 tests/docs（或去掉 --confcutdir / PYTEST_DISABLE_PLUGIN_AUTOLOAD 运行），
   该断言即失败，隔离被显式证伪。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.docs_pure

DOCS_DIR = Path(__file__).resolve().parent
INJECTED_SAMPLE = DOCS_DIR / "fixtures" / "injected_business_imports.py"

# 业务顶层模块清单：项目分层（app/ui/strategies/services/data/core/config/main/utils）
# 与典型业务第三方库。docs 组允许集之外的任何命中都视为业务加载。
BUSINESS_TOP_LEVELS = frozenset(
    {
        "flet",
        "pandas",
        "polars",
        "sqlalchemy",
        "httpx",
        "pydantic",
        "app",
        "data",
        "services",
        "strategies",
        "ui",
        "core",
        "config",
        "main",
        "utils",
        "tests",
    }
)


def _top_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def _collected_python_files() -> list[Path]:
    """参与收集的文件：tests/docs（含子目录）下的 conftest.py 与 test_*.py。"""
    files: list[Path] = []
    for path in sorted(DOCS_DIR.rglob("*.py")):
        if path.name == "conftest.py" or path.name.startswith("test_"):
            files.append(path)
    return files


def test_collected_files_have_no_business_imports() -> None:
    """静态断言：所有会被 pytest 收集的测试文件与 conftest 不含业务导入。"""
    offenders: dict[str, set[str]] = {}
    for path in _collected_python_files():
        hits = _top_level_imports(path) & BUSINESS_TOP_LEVELS
        if hits:
            offenders[str(path.relative_to(DOCS_DIR))] = hits
    assert offenders == {}, f"纯文档测试文件不得导入业务模块: {offenders}"
    # 允许集兜底核对：conftest 仅注入 docs_markdown / docs_generated（scripts 解析与生成模块）
    assert _top_level_imports(DOCS_DIR / "conftest.py") <= (
        sys.stdlib_module_names | {"pytest", "docs_markdown", "docs_generated"}
    )


def test_injected_business_sample_is_real_and_not_collected() -> None:
    """注入样本必须真实含业务导入，且命名保证不被 pytest 收集。"""
    assert INJECTED_SAMPLE.exists(), "隔离负例注入样本缺失，无法证明收集隔离"
    assert "flet" in _top_level_imports(INJECTED_SAMPLE), "注入样本未含业务导入，隔离证明失效"
    assert not INJECTED_SAMPLE.name.startswith("test_")
    assert INJECTED_SAMPLE.name != "conftest.py"


def test_runtime_import_set_is_business_free() -> None:
    """运行时收集导入集合断言：当前进程 sys.modules 不含任何业务顶层模块。"""
    loaded = sorted(set(sys.modules) & BUSINESS_TOP_LEVELS)
    assert loaded == [], f"纯文档测试环境加载了业务模块: {loaded}"
