"""import-linter 配置守护测试 (R1 架构越界自动化门禁).

验证 pyproject.toml 中的 [tool.importlinter] 配置正确且 lint-imports 命令通过。

E4 (OSS 检视) 后与 test_architecture_boundaries.py 分工：
- test_architecture_boundaries.py: AST 扫描模块级 import，仅保留例外注册表路径存在性校验
  （R1 方向守护已全部上提至 import-linter，见下）
- test_import_linter_config.py: 调用 lint-imports 检查完整导入图（含 lazy import），守护 R1 全部方向。

import-linter 采用 1 条 layers 契约（层序 app→ui→strategies→services→data→core）覆盖全部
层级禁止方向，另 2 条 forbidden 契约守护 utils 横切叶子层（"R1: utils must not import business layers" 含 ignore_imports 白名单）
与 core 最内层禁 utils（"R1: core must not import utils"）。layers 契约分析函数体内 import，能捕获 lazy import。
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


def test_importlinter_layers_contract_configured():
    """验证导入分层 layers 契约已配置（1 条 layers 覆盖全部层级禁止方向）。"""
    content = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'type = "layers"' in content, "Missing import-linter layers contract"
    assert "R1: layered dependencies (core→data→services→strategies→ui→app)" in content, "Missing layer contract name"
    layers = ["app", "ui", "strategies", "services", "data", "core"]
    for layer in layers:
        assert f'"{layer}"' in content, f"Missing layer in layers contract: {layer}"


def test_importlinter_utils_forbidden_contract_configured():
    """验证 utils 横切叶子 forbidden 契约已配置（含函数体内 ignore_imports 白名单）。"""
    content = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "R1: utils must not import business layers" in content, "Missing utils forbidden contract name"


def test_importlinter_core_utils_forbidden_contract_configured():
    """验证 core 最内层禁 utils 的 forbidden 契约已配置。"""
    content = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "R1: core must not import utils" in content, "Missing core→utils forbidden contract name"


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
