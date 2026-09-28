"""文档代码模板符号可落地性契约测试（检视报告 L1）。

test_docs_canonical_examples.py 只做「否定式」检查（禁止 API 不出现），本文件补上「肯定式」
检查：四份 canonical 模板文档的 ```python 代码块中所有绝对 `from X import Y`，断言模块可
import（`importlib.import_module`）且符号存在（`hasattr`）。只验符号，不执行代码，永久锁住
模板的可落地性，避免靠维护者自律。

覆盖文档：docs/patterns/strategy-template.md / polars-vectorized-strategy.md / mvvm.md /
docs/guides/testing.md。
"""

import ast
import importlib
import re
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent.parent

DOCS: list[Path] = [
    ROOT / "docs" / "patterns" / "strategy-template.md",
    ROOT / "docs" / "patterns" / "polars-vectorized-strategy.md",
    ROOT / "docs" / "patterns" / "mvvm.md",
    ROOT / "docs" / "guides" / "testing.md",
]

# 每份文档「核心 import」的最低覆盖要求（doc.name -> 必须被检查到的模块集合）。
# 用于防止 skip 容错静默跳过整块模板导致漏报；模板若确已重写，需同步更新本表。
EXPECTED_MODULES: dict[str, set[str]] = {
    "strategy-template.md": {"data.persistence.quality_gate", "strategies.base_strategy", "strategies.utils"},
    "polars-vectorized-strategy.md": {
        "data.persistence.quality_gate",
        "strategies.base_strategy",
        "strategies.polars_base",
        "strategies.utils",
    },
    "mvvm.md": {"core.i18n", "ui.hooks", "ui.viewmodels", "ui.viewmodels.screener_view_model"},
    "testing.md": {
        "tests.conftest",
        "data.persistence.daos.holder_dao",
        "ui.viewmodels.system_viewmodel",
        "utils.async_utils",
        "data.persistence.quality_gate",
        "strategies.market",
    },
}

# 显式豁免清单：(doc.name, 1-based 代码块序号, 模块) -> 理由。
# 用于语法完整但符号按设计不可解析的示例（如演示错误形态的伪模块）；当前无需豁免。
EXEMPTIONS: dict[tuple[str, int, str], str] = {}

# 提取 ```python ... ``` 代码块（非贪婪匹配），与 test_docs_canonical_examples.py 同模式
PYTHON_BLOCK_RE = re.compile(r"```python\n(.*?)```", re.DOTALL)


def _iter_doc_imports(doc: Path) -> tuple[list[tuple[int, str, str]], int]:
    """返回 ([(代码块序号, 模块, 符号), ...], 不可解析代码块数)。

    跳过相对导入（`from . import`）、`from __future__ import` 与通配符导入；语法不完整或含
    占位符导致 SyntaxError 的代码块按「跳过并计数」处理（由覆盖度断言兜底，不静默漏报）。
    """
    imports: list[tuple[int, str, str]] = []
    skipped = 0
    for block_no, block in enumerate(PYTHON_BLOCK_RE.findall(doc.read_text(encoding="utf-8")), 1):
        try:
            tree = ast.parse(textwrap.dedent(block))
        except SyntaxError:
            skipped += 1
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level != 0 or not node.module:
                continue
            if node.module == "__future__":
                continue
            for alias in node.names:
                if alias.name != "*":
                    imports.append((block_no, node.module, alias.name))
    return imports, skipped


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_doc_template_imported_symbols_exist(doc: Path):
    """文档代码块内每个 `from X import Y` 的模块可 import 且符号存在。"""
    violations: list[str] = []
    for block_no, module, symbol in _iter_doc_imports(doc)[0]:
        if (doc.name, block_no, module) in EXEMPTIONS:
            continue
        try:
            mod = importlib.import_module(module)
        except ImportError as exc:
            violations.append(f"{doc.name} 代码块#{block_no}: 模块 `{module}` 无法导入（{exc}）")
            continue
        if not hasattr(mod, symbol):
            violations.append(f"{doc.name} 代码块#{block_no}: `from {module} import {symbol}` 中符号 `{symbol}` 不存在")
    assert not violations, "文档代码模板符号漂移（模板不可落地）：\n" + "\n".join(violations)


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_doc_template_core_imports_covered(doc: Path):
    """每份文档的核心 import 必须被实际检查到（防 skip 容错静默漏掉整块模板）。"""
    imports, skipped = _iter_doc_imports(doc)
    found = {module for _block_no, module, _symbol in imports}
    missing = EXPECTED_MODULES.get(doc.name, set()) - found
    assert not missing, (
        f"{doc.name} 核心 import 未被覆盖（跳过 {skipped} 个不可解析代码块）：{sorted(missing)}。"
        "若模板确已重写，请同步更新 EXPECTED_MODULES。"
    )
