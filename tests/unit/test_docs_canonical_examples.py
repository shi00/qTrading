"""Tests for canonical UI examples in docs (Task 4.1 + Phase 2.5 迁移后扩展)。

验证文档中的 canonical 示例不包含禁止 API：
- CONTRIBUTING.md python 代码块内不出现 `I18n.subscribe()` / `page.show_dialog(` / `page.pop_dialog(`
- docs/flet/ 子文档（v1-api-constraints / project-differences）python 代码块内 Dropdown 使用 `on_select` 而非 `on_change`
- docs/flet/ 子文档 python 代码块内不出现 `page.show_dialog(` / `page.pop_dialog(`

man/flet-best-practices.md Phase 2.5 后改为薄 stub 无 python 代码块，本测试自然通过；
canonical 示例契约迁移到 docs/flet/ 子文档（v1-api-constraints.md / project-differences.md）。

仅检查 ```python 代码块内的内容，不检查表格或行内代码（V0→V1 迁移 API 表中合法引用这些 API）。
如文档确实需要在代码块中展示禁止 API（如对比示例），可用注释 `# migration reference` 跳过。
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent.parent
CONTRIBUTING_PATH = ROOT / "CONTRIBUTING.md"
MAN_FLET_BEST_PRACTICES_PATH = ROOT / "man" / "flet-best-practices.md"  # Phase 2.5 后为 stub，0 代码块
FLET_V1_API_CONSTRAINTS_PATH = ROOT / "docs" / "flet" / "v1-api-constraints.md"
FLET_PROJECT_DIFFERENCES_PATH = ROOT / "docs" / "flet" / "project-differences.md"

# docs/flet/ 下含 python 代码块的子文档（Phase 2.5 迁移目标）
FLET_DOCS_WITH_PYTHON_BLOCKS: list[Path] = [
    FLET_V1_API_CONSTRAINTS_PATH,
    FLET_PROJECT_DIFFERENCES_PATH,
]

# 提取 ```python ... ``` 代码块（非贪婪匹配）
PYTHON_BLOCK_RE = re.compile(r"```python\n(.*?)```", re.DOTALL)
# 跳过含此注释的代码块（用于 V0→V1 迁移对比示例）
MIGRATION_REFERENCE_MARKER = "migration reference"


def _extract_python_blocks(text: str) -> list[str]:
    """从 markdown 文本中提取所有 ```python ... ``` 代码块内容。"""
    return PYTHON_BLOCK_RE.findall(text)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _filter_non_migration(blocks: list[str]) -> list[str]:
    """过滤掉含 '# migration reference' 注释的代码块（迁移对比示例允许展示禁止 API）。"""
    return [b for b in blocks if MIGRATION_REFERENCE_MARKER not in b]


class TestContributingCanonicalExamples:
    """CONTRIBUTING.md canonical 示例契约。"""

    def test_contributing_no_i18n_subscribe_in_code_blocks(self):
        """CONTRIBUTING.md python 代码块内不应出现 I18n.subscribe() 调用。

        契约：声明式组件内禁止手动订阅 locale（见 CONTRIBUTING.md §4 i18n canonical 模式）。
        表格/行内代码中提及 `I18n.subscribe` 仅作迁移参考，不在代码块内即不违反契约。
        """
        content = _read(CONTRIBUTING_PATH)
        blocks = _filter_non_migration(_extract_python_blocks(content))
        violations: list[str] = []
        for i, block in enumerate(blocks, 1):
            if re.search(r"I18n\.subscribe\s*\(", block):
                violations.append(f"code block #{i} contains 'I18n.subscribe()' call:\n{block}")
        assert not violations, (
            "CONTRIBUTING.md python code blocks must not contain I18n.subscribe() "
            "(declarative components forbid manual locale subscription). Violations:\n" + "\n---\n".join(violations)
        )

    def test_contributing_no_page_show_dialog_in_declarative_examples(self):
        """CONTRIBUTING.md python 代码块内不应出现 page.show_dialog( / page.pop_dialog(。

        契约：声明式组件内唯一 Dialog 契约为 ft.use_dialog()，命令式 page.show_dialog/pop_dialog
        仅作为 V0→V1 迁移入口在表格中参考，不应在 canonical 代码块中出现。
        """
        content = _read(CONTRIBUTING_PATH)
        blocks = _filter_non_migration(_extract_python_blocks(content))
        violations: list[str] = []
        for i, block in enumerate(blocks, 1):
            for forbidden in (r"page\.show_dialog\s*\(", r"page\.pop_dialog\s*\("):
                if re.search(forbidden, block):
                    violations.append(f"code block #{i} contains '{forbidden}':\n{block}")
        assert not violations, (
            "CONTRIBUTING.md python code blocks must not contain page.show_dialog()/page.pop_dialog() "
            "(declarative components must use ft.use_dialog()). Violations:\n" + "\n---\n".join(violations)
        )


class TestManFletBestPracticesCanonicalExamples:
    """man/flet-best-practices.md canonical 示例契约。

    Phase 2.5 后 man/flet-best-practices.md 改为薄 stub 无 python 代码块，
    本类测试自然通过；canonical 示例契约迁移到 docs/flet/ 子文档，
    由 TestFletDocsCanonicalExamples 守护。
    """

    def test_man_no_on_change_in_dropdown_examples(self):
        """man/flet-best-practices.md python 代码块内 Dropdown 示例使用 on_select 而非 on_change。

        契约：项目 ft.Dropdown 事件统一用 on_select（见 docs/flet/project-differences.md §4.2）。
        若代码块含 Dropdown( 构造，则不应同时含 on_change= 关键字参数。
        """
        content = _read(MAN_FLET_BEST_PRACTICES_PATH)
        blocks = _filter_non_migration(_extract_python_blocks(content))
        violations: list[str] = []
        for i, block in enumerate(blocks, 1):
            if re.search(r"ft\.Dropdown\s*\(", block) and re.search(r"\bon_change\s*=", block):
                violations.append(f"code block #{i} contains Dropdown( with on_change=:\n{block}")
        assert not violations, (
            "man/flet-best-practices.md Dropdown examples must use on_select, not on_change. "
            "Violations:\n" + "\n---\n".join(violations)
        )

    def test_man_no_page_show_dialog_in_declarative_examples(self):
        """man/flet-best-practices.md python 代码块内不应出现 page.show_dialog(。

        契约：声明式组件内唯一 Dialog 契约为 ft.use_dialog()（见 docs/flet/project-differences.md §4.1）。
        page.pop_dialog( 同理禁用，但本测试仅检查 page.show_dialog( 以与 CONTRIBUTING 测试对称；
        page.pop_dialog( 由文末 §4.1 契约文字守护，不在代码块中出现。
        """
        content = _read(MAN_FLET_BEST_PRACTICES_PATH)
        blocks = _filter_non_migration(_extract_python_blocks(content))
        violations: list[str] = []
        for i, block in enumerate(blocks, 1):
            if re.search(r"page\.show_dialog\s*\(", block):
                violations.append(f"code block #{i} contains 'page.show_dialog():\n{block}")
        assert not violations, (
            "man/flet-best-practices.md python code blocks must not contain page.show_dialog() "
            "(declarative components must use ft.use_dialog()). Violations:\n" + "\n---\n".join(violations)
        )


class TestFletDocsCanonicalExamples:
    """docs/flet/ 子文档 canonical 示例契约（Phase 2.5 迁移后守护）。

    守护范围：v1-api-constraints.md / project-differences.md 内 python 代码块。
    迁移来源：原 man/flet-best-practices.md §4.1/4.2 与 CONTRIBUTING.md §Flet V1 声明式模板。
    """

    @pytest.mark.parametrize("doc_path", FLET_DOCS_WITH_PYTHON_BLOCKS, ids=lambda p: p.name)
    def test_flet_docs_no_on_change_in_dropdown_examples(self, doc_path: Path):
        """docs/flet/ python 代码块内 Dropdown 示例使用 on_select 而非 on_change。

        契约：项目 ft.Dropdown 事件统一用 on_select
        （见 docs/flet/project-differences.md §4.2 / v1-api-constraints.md §V0→V1 迁移 API 表第 13 项）。
        """
        content = _read(doc_path)
        blocks = _filter_non_migration(_extract_python_blocks(content))
        violations: list[str] = []
        for i, block in enumerate(blocks, 1):
            if re.search(r"ft\.Dropdown\s*\(", block) and re.search(r"\bon_change\s*=", block):
                violations.append(f"{doc_path.name} code block #{i} contains Dropdown( with on_change=:\n{block}")
        assert not violations, (
            f"{doc_path.name} Dropdown examples must use on_select, not on_change. "
            "Violations:\n" + "\n---\n".join(violations)
        )

    @pytest.mark.parametrize("doc_path", FLET_DOCS_WITH_PYTHON_BLOCKS, ids=lambda p: p.name)
    def test_flet_docs_no_page_show_dialog_in_declarative_examples(self, doc_path: Path):
        """docs/flet/ python 代码块内不应出现 page.show_dialog( / page.pop_dialog(。

        契约：声明式组件内唯一 Dialog 契约为 ft.use_dialog()
        （见 docs/flet/project-differences.md §4.1 / v1-api-constraints.md §声明式组件内 API 契约）。
        """
        content = _read(doc_path)
        blocks = _filter_non_migration(_extract_python_blocks(content))
        violations: list[str] = []
        for i, block in enumerate(blocks, 1):
            for forbidden in (r"page\.show_dialog\s*\(", r"page\.pop_dialog\s*\("):
                if re.search(forbidden, block):
                    violations.append(f"{doc_path.name} code block #{i} contains '{forbidden}':\n{block}")
        assert not violations, (
            f"{doc_path.name} python code blocks must not contain page.show_dialog()/page.pop_dialog() "
            "(declarative components must use ft.use_dialog()). Violations:\n" + "\n---\n".join(violations)
        )

    @pytest.mark.parametrize("doc_path", FLET_DOCS_WITH_PYTHON_BLOCKS, ids=lambda p: p.name)
    def test_flet_docs_no_i18n_subscribe_in_code_blocks(self, doc_path: Path):
        """docs/flet/ python 代码块内不应出现 I18n.subscribe() 调用。

        契约：声明式组件内禁止手动订阅 locale
        （见 docs/flet/v1-api-constraints.md §4 i18n canonical 模式）。
        表格/行内代码中提及 I18n.subscribe 仅作迁移参考，不在代码块内即不违反契约。
        """
        content = _read(doc_path)
        blocks = _filter_non_migration(_extract_python_blocks(content))
        violations: list[str] = []
        for i, block in enumerate(blocks, 1):
            if re.search(r"I18n\.subscribe\s*\(", block):
                violations.append(f"{doc_path.name} code block #{i} contains 'I18n.subscribe()' call:\n{block}")
        assert not violations, (
            f"{doc_path.name} python code blocks must not contain I18n.subscribe() "
            "(declarative components forbid manual locale subscription). Violations:\n" + "\n---\n".join(violations)
        )
