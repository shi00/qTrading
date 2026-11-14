"""D21：示例守卫与生产消费者统一使用 docs_markdown 解析视图的回归。

检视 §二 D21 的反例：合法 `~~~python` 围栏零提取、普通字符串冒充迁移豁免、
移除全部活示例不失败、引用式路由不识别、围栏/行内代码伪结构冒正文、
围栏内生成标记被当作现行块。本文件逐项覆盖「该失败处失败、该通过处通过」，
并用委托探针证明消费者确实经共享视图解析（不是只让 parser 自测转绿）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import check_docs_consistency as cdc  # noqa: E402
import docs_generated as dg  # noqa: E402
import docs_markdown as dm  # noqa: E402
import test_canonical_examples as tce  # noqa: E402

pytestmark = pytest.mark.docs_pure

FORBIDDEN_BODY = "I18n.subscribe()\n"


class TestCanonicalExampleExtraction:
    """两类围栏与长围栏等价提取；迁移豁免只认显式注释；活文档非空。"""

    @pytest.mark.parametrize(
        "fence",
        ["```python", "~~~python", "````python"],
        ids=["backtick", "tilde", "long-backtick"],
    )
    def test_forbidden_api_extracted_from_all_fence_forms(self, fence):
        closing = fence[: fence.index("python")]
        text = f"{fence}\n{FORBIDDEN_BODY}{closing}\n"
        blocks = tce._extract_python_blocks(text)
        assert blocks == [FORBIDDEN_BODY.rstrip("\n")]
        assert tce._live_python_blocks(text, "doc.md") == blocks

    def test_string_literal_does_not_change_code_role(self):
        text = '```python\nnote = "migration reference"\nI18n.subscribe()\n```\n'
        live = tce._live_python_blocks(text, "doc.md")
        assert len(live) == 1
        assert "I18n.subscribe()" in live[0]

    def test_explicit_comment_exempts_block(self):
        annotated = "# migration reference\nI18n.subscribe()"
        assert tce._is_migration_annotated(annotated) is True
        text = f"```python\n{annotated}\n```\n\n```python\npage.update()\n```\n"
        assert tce._live_python_blocks(text, "doc.md") == ["page.update()"]

    def test_active_document_without_examples_fails(self):
        with pytest.raises(AssertionError, match="活文档必须包含 python 代码块"):
            tce._live_python_blocks("# 无示例\n", "doc.md")

    def test_active_document_with_only_migration_examples_fails(self):
        text = "```python\n# migration reference\nx = 1\n```\n"
        with pytest.raises(AssertionError, match="至少保留一个"):
            tce._live_python_blocks(text, "doc.md")

    def test_named_historical_stub_exempt_from_nonempty(self):
        assert tce._live_python_blocks("# stub\n", "flet-best-practices.md", historical_stub=True) == []


class TestCanonicalRoutingSharedView:
    """引用式与内联式链接等价；围栏/行内代码中的伪路由不冒正文。"""

    @pytest.fixture()
    def workflow(self, tmp_path: Path) -> tuple[Path, Path]:
        target = tmp_path / "workflow.md"
        target.write_text("# workflow\n", encoding="utf-8")
        return tmp_path, target.resolve()

    def test_reference_link_is_real_route(self, workflow):
        doc_dir, target = workflow
        content = "# canonical\n\n[工作流][wf]\n\n[wf]: ./workflow.md\n"
        assert cdc._canonical_routes_to_workflow(content, doc_dir, target) is True

    def test_inline_link_is_real_route(self, workflow):
        doc_dir, target = workflow
        assert cdc._canonical_routes_to_workflow("# c\n\n[工作流](./workflow.md)\n", doc_dir, target) is True

    @pytest.mark.parametrize(
        "content",
        [
            "# c\n\n```\n[工作流](./workflow.md)\n```\n",
            "# c\n\n`[工作流](./workflow.md)`\n",
            "# c\n\n纯文本提及 workflow.md\n",
        ],
        ids=["fenced", "inline-code", "plain-text"],
    )
    def test_pseudo_route_is_not_a_real_route(self, workflow, content):
        doc_dir, target = workflow
        assert cdc._canonical_routes_to_workflow(content, doc_dir, target) is False

    def test_consumer_delegates_to_shared_view(self, workflow, monkeypatch):
        doc_dir, target = workflow
        calls: list[str] = []
        original = cdc.parse_doc_view

        def spy(content: str) -> dm.DocumentView:
            calls.append(content)
            return original(content)

        monkeypatch.setattr(cdc, "parse_doc_view", spy)
        assert cdc._canonical_routes_to_workflow("# c\n\n[工作流](./workflow.md)\n", doc_dir, target) is True
        assert calls, "canonical 路由必须经 docs_markdown 共享视图解析"


class TestCompletionSectionSharedView:
    """Setext 标题合法；围栏内伪标题与代码示例不冒完成判定结构。"""

    def test_setext_completion_heading_is_recognized(self):
        block = cdc._completion_section("完成判定\n------\n\n- 判据\n- 最小验证命令\n")
        assert block is not None
        assert "- 最小验证命令" in block or "最小验证命令" in block

    def test_heading_inside_fence_does_not_count(self):
        content = "## 其他\n\n```\n## 完成判定\n\n- 判据\n- 最小验证命令\n```\n"
        assert cdc._completion_section(content) is None

    def test_code_example_does_not_supply_criteria(self):
        content = "## 完成判定\n\n```\n- 判据\n- 最小验证命令\n```\n"
        block = cdc._completion_section(content)
        assert block is not None
        assert "最小验证命令" not in block
        assert not any(line.lstrip().startswith("- ") for line in block.splitlines())

    def test_consumer_delegates_to_shared_view(self, monkeypatch):
        calls: list[str] = []
        original = cdc.parse_doc_view

        def spy(content: str) -> dm.DocumentView:
            calls.append(content)
            return original(content)

        monkeypatch.setattr(cdc, "parse_doc_view", spy)
        assert cdc._completion_section("## 完成判定\n\n- x\n") is not None
        assert calls, "完成判定必须经 docs_markdown 共享视图解析"


class TestChangelogDateSharedView:
    """变更记录日期只取真实标题下的正文表，围栏内伪标题/伪表格无效。"""

    @pytest.fixture()
    def changelog(self, tmp_path: Path, monkeypatch) -> Path:
        path = tmp_path / "ruleset-changelog.md"
        monkeypatch.setattr(cdc, "RULESET_CHANGELOG_PATH", path)
        return path

    def test_real_heading_table_returns_date(self, changelog):
        changelog.write_text(
            "## 变更记录\n\n| ruleset_version | 变更日期 |\n|---|---|\n| 9.9.9 | 2026-11-13 |\n",
            encoding="utf-8",
        )
        assert cdc._extract_changelog_top_date().isoformat() == "2026-11-13"

    def test_missing_real_heading_does_not_scan_other_sections(self, changelog):
        changelog.write_text(
            "## 其他\n\n| ruleset_version | 变更日期 |\n|---|---|\n| 9.9.9 | 2026-11-13 |\n",
            encoding="utf-8",
        )
        assert cdc._extract_changelog_top_date() is None

    def test_fenced_table_does_not_supply_date(self, changelog):
        changelog.write_text(
            "## 变更记录\n\n```\n| ruleset_version | 变更日期 |\n|---|---|\n| 9.9.9 | 2026-11-13 |\n```\n",
            encoding="utf-8",
        )
        assert cdc._extract_changelog_top_date() is None

    def test_consumer_delegates_to_shared_view(self, changelog, monkeypatch):
        changelog.write_text(
            "## 变更记录\n\n| ruleset_version | 变更日期 |\n|---|---|\n| 9.9.9 | 2026-11-13 |\n",
            encoding="utf-8",
        )
        calls: list[Path] = []
        original = dm.load_document_view

        def spy(path: str | Path) -> dm.DocumentView:
            calls.append(Path(path))
            return original(path)

        monkeypatch.setattr(dm, "load_document_view", spy)
        assert cdc._extract_changelog_top_date() is not None
        assert calls, "变更日志日期必须经 docs_markdown 共享视图解析"


class TestEvalsIndexSharedView:
    """eval 索引内联式/引用式等价；围栏内表格不参数。"""

    @pytest.fixture()
    def evals_readme(self, tmp_path: Path, monkeypatch) -> Path:
        evals_dir = tmp_path / "evals"
        (evals_dir / "cat").mkdir(parents=True)
        (evals_dir / "cat" / "case-001.md").write_text("# a", encoding="utf-8")
        readme = evals_dir / "README.md"
        monkeypatch.setattr(cdc, "EVALS_README_PATH", readme)
        return readme

    def test_inline_and_reference_index_are_equivalent(self, evals_readme):
        inline = "| 类别 | 目录 | case |\n|---|---|---|\n| c | [cat/](./cat/) | [case-001](./cat/case-001.md) |\n"
        reference = (
            "| 类别 | 目录 | case |\n|---|---|---|\n"
            "| c | [cat/][d] | [case-001][f] |\n\n[d]: ./cat/\n[f]: ./cat/case-001.md\n"
        )
        for text in (inline, reference):
            evals_readme.write_text(text, encoding="utf-8")
            assert cdc.check_evals_case_index_consistency() == []

    def test_fenced_table_is_not_an_index(self, evals_readme):
        evals_readme.write_text(
            "```\n| 类别 | 目录 | case |\n|---|---|---|\n| c | [cat/](./cat/) | [case-001](./cat/case-001.md) |\n```\n",
            encoding="utf-8",
        )
        errors = cdc.check_evals_case_index_consistency()
        assert any("未解析到任何类别目录" in error for error in errors)

    def test_consumer_delegates_to_shared_view(self, evals_readme, monkeypatch):
        evals_readme.write_text(
            "| 类别 | 目录 | case |\n|---|---|---|\n| c | [cat/](./cat/) | [case-001](./cat/case-001.md) |\n",
            encoding="utf-8",
        )
        calls: list[Path] = []
        original = dm.load_document_view

        def spy(path: str | Path) -> dm.DocumentView:
            calls.append(Path(path))
            return original(path)

        monkeypatch.setattr(dm, "load_document_view", spy)
        assert cdc.check_evals_case_index_consistency() == []
        assert calls, "eval 索引必须经 docs_markdown 共享视图解析"


class TestGeneratedBlockScanSharedView:
    """围栏/缩进块内的生成标记不构成现行块，也不能被替换。"""

    @pytest.mark.parametrize("fence", ["```", "~~~", "````"], ids=["backtick", "tilde", "long-backtick"])
    def test_marker_inside_fence_is_not_a_block(self, fence):
        text = f"{fence}\n<!-- generated:x -->\nold\n<!-- /generated -->\n{fence}\n"
        assert dg.scan_blocks(text, "markdown") == {}

    def test_replace_refuses_fenced_only_marker(self):
        text = "~~~\n<!-- generated:x -->\nold\n<!-- /generated -->\n~~~\n"
        with pytest.raises(dg.MissingBlockError):
            dg.replace_block(text, "markdown", "x", ["changed"])

    def test_replace_leaves_fenced_example_untouched(self):
        text = (
            "```\n<!-- generated:x -->\nexample\n<!-- /generated -->\n```\n\n"
            "<!-- generated:x -->\nold\n<!-- /generated -->\n"
        )
        replaced = dg.replace_block(text, "markdown", "x", ["new"])
        assert "<!-- generated:x -->\nexample\n<!-- /generated -->" in replaced
        assert "<!-- generated:x -->\nnew\n<!-- /generated -->" in replaced

    def test_consumer_delegates_to_shared_view(self, monkeypatch):
        calls: list[str] = []
        original = dm.parse_text

        def spy(text: str) -> dm.DocumentView:
            calls.append(text)
            return original(text)

        monkeypatch.setattr(dm, "parse_text", spy)
        dg.scan_blocks("<!-- generated:x -->\na\n<!-- /generated -->\n", "markdown")
        assert calls, "生成块扫描必须经 docs_markdown 共享视图判定代码边界"
