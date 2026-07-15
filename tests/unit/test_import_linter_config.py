"""import-linter 配置守护测试 (R1 架构越界自动化门禁).

验证 pyproject.toml 中的 [tool.importlinter] 配置正确且 lint-imports 命令通过。

与 test_architecture_boundaries.py 互补：
- test_architecture_boundaries.py: AST 扫描模块级 import，覆盖 R1 + §4.2 全部方向（含 utils 隔离）
- test_import_linter_config.py: 调用 lint-imports 检查完整导入图（含 lazy import），覆盖 R1 四个禁止方向

import-linter 的优势：能捕获函数体内的延迟导入（lazy import），AST 扫描无法覆盖。
AST 扫描的优势：能检测 utils→业务层等 §4.2 扩展方向，import-linter 配置仅覆盖 R1 红线。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).parent.parent.parent


def test_importlinter_config_exists():
    """验证 pyproject.toml 包含 [tool.importlinter] 配置段。"""
    content = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "[tool.importlinter]" in content, "pyproject.toml missing [tool.importlinter] section"
    assert "exclude_type_checking_imports" in content, "Missing exclude_type_checking_imports setting"


def test_importlinter_contracts_configured():
    """验证 4 个 R1 禁止方向契约均已配置。"""
    content = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    expected_contracts = [
        "R1: core must not import any other layer",
        "R1: data must not import services/strategies/ui",
        "R1: services must not import strategies/ui",
        "R1: strategies must not import ui",
    ]
    for contract in expected_contracts:
        assert contract in content, f"Missing import-linter contract: {contract}"


def test_lint_imports_passes():
    """运行 lint-imports 命令，验证所有 R1 契约通过。

    这是 R1 架构越界的自动化门禁。如果此测试失败，说明存在新的 R1 违规。
    """
    pytest.importorskip("importlinter", reason="import-linter not installed")
    import os

    from importlinter.cli import lint_imports

    original_cwd = os.getcwd()
    os.chdir(PROJECT_ROOT)
    try:
        exit_code = lint_imports()
    finally:
        os.chdir(original_cwd)

    assert exit_code == 0, f"lint-imports failed (exit {exit_code}), R1 契约被打破"
