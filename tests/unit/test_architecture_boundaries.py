"""架构边界静态测试（E4 后精简版）。

E4 (OSS 检视) 将 import-linter 由 6 条手工 forbidden 契约重构为 1 条 layers +
2 条 forbidden，在本文件退役前的「模块级 import 跨层禁止」AST 扫描已全部上提并
由 import-linter 覆盖（layers/forbidden 契约均会分析函数体内 import，比模块级
AST 扫描更全面）：
- core → data/services/strategies/ui/app 由 layers 契约覆盖
- data → services/strategies/ui/app、services → strategies/ui/app、
  strategies → ui/app、ui → app 由 layers 契约覆盖
- core → utils 由 forbidden 契约覆盖
- utils → data/services/strategies/ui/app 由 forbidden 契约（"R1: utils must not import business layers"，含 ignore_imports
  白名单）覆盖

本文件保留例外注册表（docs/governance/exceptions.yml，rule_id=R1）路径存在性校验
（test_known_exceptions_are_valid），确保例外治理集中化（P1-01）下的路径不悬空。
"""

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).parent.parent.parent

# 例外注册表路径 (P1-01: 集中例外治理, 见 docs/governance/exceptions.yml)
EXCEPTIONS_YAML_PATH = PROJECT_ROOT / "docs" / "governance" / "exceptions.yml"


# P1-01: 例外统一由 docs/governance/exceptions.yml 注册表管理，此处仅读取，不再各自维护。
# 例外原因与审批记录见 exceptions.yml。
def _load_known_exceptions() -> set[str]:
    """从例外注册表加载架构边界例外路径 (rule_id=R1 的 paths)。

    例外治理集中化 (P1-01)：路径不再硬编码于测试文件，而是从 docs/governance/exceptions.yml
    读取，避免多源漂移。import-linter 契约级 ignore_imports 白名单为 R1 例外的另一守护
    （见 pyproject.toml "R1: utils must not import business layers" 契约注释与 check_docs_consistency.py GATE-02/04）。
    """
    data = yaml.safe_load(EXCEPTIONS_YAML_PATH.read_text(encoding="utf-8"))
    paths: set[str] = set()
    for entry in data.get("exceptions", []):
        if entry.get("rule_id") == "R1":
            paths.update(entry.get("paths", []))
    return paths


KNOWN_EXCEPTIONS: set[str] = _load_known_exceptions()


def test_known_exceptions_are_valid():
    """已知例外文件必须仍然存在，避免遗留过期例外。"""
    for except_path in KNOWN_EXCEPTIONS:
        full_path = PROJECT_ROOT / except_path
        assert full_path.exists(), (
            f"exceptions.yml contains non-existent file: {except_path}. "
            "Remove it from docs/governance/exceptions.yml if the file was deleted or renamed."
        )
