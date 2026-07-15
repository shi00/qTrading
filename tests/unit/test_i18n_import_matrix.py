"""i18n 分层 import 矩阵守护测试 (R.5.2)。

守护 i18n 相关 import 的分层约束，防止回归：
1. core/i18n.py 不 import flet/ui/utils/data/services/strategies (§4.2 core 隔离)
2. ui/i18n.py 可 import flet + core.i18n (UI 层对 core.i18n 的薄封装)
3. ui/viewmodels/ import from core.i18n (非 ui.i18n，避免 flet 污染 VM)
4. strategies/services/data/utils import from core.i18n (非 ui.i18n)

与 test_core_i18n_purity.py 互补：
- test_core_i18n_purity.py: 守护 core/i18n.py 不导入 flet + 不定义 Observable state
- 本文件: 守护 i18n import 的分层矩阵 (VM 层 + 业务层从 core.i18n 导入)
"""

import ast
import pathlib

import pytest

PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent

pytestmark = pytest.mark.unit


def _get_module_level_imports(file_path: pathlib.Path) -> list[tuple[str, str]]:
    """提取文件中模块级 import 的 (模块, 符号) 列表。

    仅检查 tree.body 的直接子节点（模块级 import），跳过函数体内的
    lazy import（如 R.4.2 的 MetaDataManager lazy import）。
    """
    tree = ast.parse(file_path.read_text(encoding="utf-8"))
    imports: list[tuple[str, str]] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, alias.asname or alias.name))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append((module, alias.name))
    return imports


def test_ui_i18n_import_matrix():
    """ui/i18n.py 可 import flet + core.i18n (UI 层薄封装)。

    ui/i18n.py 是 UI 层对 core.i18n 的薄封装 (Flet 文本绑定)，
    允许导入 flet + core.i18n，但不导入 data/services/strategies。
    """
    ui_i18n = PROJECT_ROOT / "ui" / "i18n.py"
    imports = _get_module_level_imports(ui_i18n)

    modules = {imp[0].split(".")[0] for imp in imports}

    # 允许 flet + core
    assert "flet" in modules or any("flet" in imp[0] for imp in imports), "ui/i18n.py 应导入 flet (UI 层薄封装)"
    assert "core" in modules, "ui/i18n.py 应导入 core.i18n"

    # 禁止业务层
    forbidden = modules & {"data", "services", "strategies"}
    assert not forbidden, f"ui/i18n.py 不应导入业务层: {forbidden}"


def test_viewmodel_i18n_import():
    """ui/viewmodels/ import from core.i18n (非 ui.i18n，避免 flet 污染 VM)。

    VM 层是纯状态+命令层，禁止 import flet (§3.2 MVVM)。
    如果从 ui.i18n 导入 I18n，会间接引入 flet 依赖。
    """
    vm_dir = PROJECT_ROOT / "ui" / "viewmodels"
    if not vm_dir.exists():
        pytest.skip("viewmodels directory does not exist")

    violations: list[str] = []
    for py_file in vm_dir.rglob("*.py"):
        if py_file.name == "__init__.py":
            continue
        imports = _get_module_level_imports(py_file)
        for module, symbol in imports:
            if module == "ui.i18n" or module.startswith("ui.i18n."):
                violations.append(f"{py_file.name}: from {module} import {symbol}")

    assert not violations, "VM 层应从 core.i18n 导入 (非 ui.i18n)，避免 flet 污染:\n" + "\n".join(violations)


def test_business_layer_i18n_import():
    """strategies/services/data/utils import from core.i18n (非 ui.i18n)。

    业务层不应从 ui.i18n 导入，避免 UI 层 flet 依赖污染。
    """
    business_layers = ["strategies", "services", "data", "utils"]
    violations: list[str] = []

    for layer in business_layers:
        layer_dir = PROJECT_ROOT / layer
        if not layer_dir.exists():
            continue
        for py_file in layer_dir.rglob("*.py"):
            if py_file.name == "__init__.py":
                continue
            imports = _get_module_level_imports(py_file)
            for module, symbol in imports:
                if module == "ui.i18n" or module.startswith("ui.i18n."):
                    violations.append(f"{layer}/{py_file.relative_to(layer_dir)}: from {module} import {symbol}")

    assert not violations, "业务层应从 core.i18n 导入 (非 ui.i18n):\n" + "\n".join(violations)
