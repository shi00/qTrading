"""架构边界静态测试 - 落实 docs/rr/01.md 后续建议。

用 AST 扫描禁止的跨层导入，把红线 R1 + §4.2 变成自动化门禁。
扫描范围：core/、data/、services/、strategies/（不扫描 tests/ui/utils/app）。

注：services → strategies 存在历史遗留违规（backtest_service.py、ai_service.py），
需单独 PR 修复，暂不纳入本测试覆盖。
"""

import ast
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent

# R1 + §4.2 禁止的跨层导入方向
# key: 源层; value: 该层禁止导入的目标层列表
# 注：services → strategies 暂未纳入（历史遗留，待单独 PR 修复）
FORBIDDEN_IMPORTS: dict[str, list[str]] = {
    "core": ["data", "services", "strategies", "ui", "app", "utils"],
    "data": ["services", "strategies", "ui", "app"],
    "services": ["ui", "app"],
    "strategies": ["ui", "app"],
}


def _get_imported_modules(node: ast.AST) -> list[str]:
    """提取 import 节点中导入的顶层模块名（仅绝对导入）。"""
    modules = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            modules.append(alias.name.split(".")[0])
    elif isinstance(node, ast.ImportFrom):
        if node.module and node.level == 0:  # 仅绝对导入
            modules.append(node.module.split(".")[0])
    return modules


@pytest.mark.unit
@pytest.mark.parametrize(
    "layer,forbidden",
    [(layer, forbidden) for layer, forbidden in FORBIDDEN_IMPORTS.items()],
)
def test_no_forbidden_cross_layer_imports(layer: str, forbidden: list[str]):
    """验证各层不导入禁止的模块（R1 + §4.2）。"""
    layer_dir = PROJECT_ROOT / layer
    if not layer_dir.exists():
        pytest.skip(f"Layer directory {layer} does not exist")

    violations = []
    for py_file in layer_dir.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            for module in _get_imported_modules(node):
                if module in forbidden:
                    rel_path = py_file.relative_to(PROJECT_ROOT)
                    violations.append(f"{rel_path}: imports '{module}'")

    assert not violations, f"Layer '{layer}' has forbidden imports: {forbidden}\n" + "\n".join(violations)
