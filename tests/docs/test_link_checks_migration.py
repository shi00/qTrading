"""D5：锚点/相对链接检查迁移到共享解析视图后的行为回归（检视 §6.4 解析负例）。

三件套原则（任务边界）：正常样例、原失败样例、迁移后等价失败样例都必须可运行。
临时仓库夹具经 monkeypatch CHECKED_DOCS 注入，不触碰仓库 docs/ 正文。

关键行为（F17）：
- 真死链（缺文件/缺锚点）仍失败——不得因迁移漏检；
- 围栏 / 行内代码内的假链接不误报（两类围栏 + 缩进块）；
- HTML ID 锚点与重复标题锚点正确；
- 引用式死链（`[t][label]` 无定义）显式失败，不从全文取样例代替。
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

pytestmark = pytest.mark.docs_pure


@pytest.fixture()
def doc_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """建立临时受检仓库：monkeypatch CHECKED_DOCS 指向 tmp_path 下的文档。"""
    monkeypatch.setattr(cdc, "CHECKED_DOCS", [])
    return tmp_path


def _register(monkeypatch: pytest.MonkeyPatch, *docs: Path) -> None:
    monkeypatch.setattr(cdc, "CHECKED_DOCS", sorted(docs))


class TestAnchorDeadLinksAfterMigration:
    def test_real_missing_anchor_still_fails(self, doc_repo, monkeypatch):
        doc = doc_repo / "a.md"
        doc.write_text("# 真标题\n\n[跳转](#不存在的锚点)\n", encoding="utf-8")
        _register(monkeypatch, doc)
        errors = cdc.check_anchor_dead_links()
        assert errors, "真死链必须失败（F17：不得从全文取样例代替缺失输入）"
        assert any("不存在的锚点" in e for e in errors)

    def test_cross_file_missing_target_file_fails(self, doc_repo, monkeypatch):
        doc = doc_repo / "a.md"
        doc.write_text("[跳转](./missing.md#锚)\n", encoding="utf-8")
        _register(monkeypatch, doc)
        errors = cdc.check_anchor_dead_links()
        assert any("不存在" in e for e in errors)

    def test_backtick_fence_fake_link_not_reported(self, doc_repo, monkeypatch):
        doc = doc_repo / "a.md"
        doc.write_text("# 真\n\n```markdown\n[示例](#伪锚点)\n```\n", encoding="utf-8")
        _register(monkeypatch, doc)
        assert cdc.check_anchor_dead_links() == []

    def test_tilde_fence_and_indented_block_fake_link_not_reported(self, doc_repo, monkeypatch):
        # 旧实现仅 ``` 状态机：~~~ 与缩进块内假链接被误报；迁移后必须排除
        doc = doc_repo / "a.md"
        doc.write_text(
            "# 真\n\n~~~\n[波浪号示例](#伪锚点)\n~~~\n\n段落：\n\n    [缩进示例](#伪锚点)\n",
            encoding="utf-8",
        )
        _register(monkeypatch, doc)
        assert cdc.check_anchor_dead_links() == [], "围栏/缩进块内假链接不得误报"

    def test_html_id_anchor_resolves(self, doc_repo, monkeypatch):
        doc = doc_repo / "a.md"
        doc.write_text('<a id="custom-anchor"></a>\n\n[跳转](#custom-anchor)\n', encoding="utf-8")
        _register(monkeypatch, doc)
        assert cdc.check_anchor_dead_links() == []

    def test_duplicate_heading_anchor_sequential(self, doc_repo, monkeypatch):
        doc = doc_repo / "a.md"
        doc.write_text("## 重复\n\n## 重复\n\n[跳转](#重复-1)\n", encoding="utf-8")
        _register(monkeypatch, doc)
        assert cdc.check_anchor_dead_links() == [], "重复标题序号锚点应可解析"


class TestRelativeDeadLinksAfterMigration:
    def test_real_missing_file_still_fails(self, doc_repo, monkeypatch):
        doc = doc_repo / "a.md"
        doc.write_text("[引用](./missing.md)\n", encoding="utf-8")
        _register(monkeypatch, doc)
        errors = cdc.check_relative_dead_links()
        assert errors and any("missing.md" in e for e in errors)

    def test_backtick_fence_fake_link_not_reported(self, doc_repo, monkeypatch):
        doc = doc_repo / "a.md"
        doc.write_text("```text\n[示例](./伪路径.md)\n```\n", encoding="utf-8")
        _register(monkeypatch, doc)
        assert cdc.check_relative_dead_links() == []

    def test_tilde_fence_fake_link_not_reported(self, doc_repo, monkeypatch):
        doc = doc_repo / "a.md"
        doc.write_text("~~~text\n[示例](./伪路径.md)\n~~~\n", encoding="utf-8")
        _register(monkeypatch, doc)
        assert cdc.check_relative_dead_links() == []

    def test_external_link_ignored(self, doc_repo, monkeypatch):
        doc = doc_repo / "a.md"
        doc.write_text("[官网](https://example.com/x.md)\n", encoding="utf-8")
        _register(monkeypatch, doc)
        assert cdc.check_relative_dead_links() == []

    def test_reference_style_dead_link_fails(self, doc_repo, monkeypatch):
        # 引用式死链（label 未定义）：迁移后经 unresolved_references 显式失败
        doc = doc_repo / "a.md"
        doc.write_text("[正则参考][pyre]\n", encoding="utf-8")
        _register(monkeypatch, doc)
        errors = cdc.check_relative_dead_links()
        assert errors and any("pyre" in e for e in errors), "引用式死链须显式失败"

    def test_reference_style_defined_link_passes(self, doc_repo, monkeypatch):
        doc = doc_repo / "a.md"
        doc.write_text("[正则参考][defn]\n\n[defn]: ./real.md\n", encoding="utf-8")
        (doc_repo / "real.md").write_text("# 目标\n", encoding="utf-8")
        _register(monkeypatch, doc, doc_repo / "real.md")
        assert cdc.check_relative_dead_links() == []

    def test_formatted_reference_defined_missing_target_fails(self, doc_repo, monkeypatch):
        # D30：已定义但目标缺失的格式化引用是真实链接，必须失败
        doc = doc_repo / "a.md"
        doc.write_text("[**格式化**][defn]\n\n[defn]: ./missing.md\n", encoding="utf-8")
        _register(monkeypatch, doc)
        errors = cdc.check_relative_dead_links()
        assert errors and any("missing.md" in e for e in errors), errors

    def test_escaped_reference_like_text_passes(self, doc_repo, monkeypatch):
        # D30：转义方括号是普通文字，不得从反转义后的显示文本倒猜引用语法
        doc = doc_repo / "a.md"
        doc.write_text("示例：\\[ok\\]\\[undefined\\]\n", encoding="utf-8")
        _register(monkeypatch, doc)
        assert cdc.check_relative_dead_links() == [], "转义文字不得误报引用死链"


class TestHtmlHrefDeadLinks:
    """Q12：HTML href 进入统一本地 URL 检查（合法过、真死链拒绝、代码 HTML 不参与）。"""

    def test_valid_html_links_pass(self, doc_repo, monkeypatch):
        doc = doc_repo / "a.md"
        target = doc_repo / "real.md"
        doc.write_text(
            '# 标题\n\n<a id="sec"></a>\n\n<a href="#sec">同文件锚</a>\n\n<a href="./real.md#目标">跨文件锚</a>\n',
            encoding="utf-8",
        )
        target.write_text("## 目标\n", encoding="utf-8")
        _register(monkeypatch, doc, target)
        assert cdc.check_anchor_dead_links() == []
        assert cdc.check_relative_dead_links() == []

    def test_html_relative_dead_link_fails(self, doc_repo, monkeypatch):
        doc = doc_repo / "a.md"
        doc.write_text('<a href="./missing.md">跳转</a>\n', encoding="utf-8")
        _register(monkeypatch, doc)
        errors = cdc.check_relative_dead_links()
        assert errors and any("missing.md" in e for e in errors), errors

    def test_html_anchor_dead_link_fails(self, doc_repo, monkeypatch):
        doc = doc_repo / "a.md"
        doc.write_text('# 真标题\n\n<a href="#ghost">跳转</a>\n', encoding="utf-8")
        _register(monkeypatch, doc)
        errors = cdc.check_anchor_dead_links()
        assert errors and any("ghost" in e for e in errors), errors

    def test_code_context_html_not_checked(self, doc_repo, monkeypatch):
        doc = doc_repo / "a.md"
        doc.write_text(
            "# 真\n\n```html\n"
            '<a href="./missing.md">围栏</a>\n'
            '<a href="#ghost">围栏锚</a>\n'
            "```\n\n"
            '行内代码 `<a href="./missing.md">代码</a>` 与 `<a href="#ghost">代码锚</a>`\n',
            encoding="utf-8",
        )
        _register(monkeypatch, doc)
        assert cdc.check_anchor_dead_links() == []
        assert cdc.check_relative_dead_links() == []

    def test_non_local_scheme_html_href_ignored(self, doc_repo, monkeypatch):
        doc = doc_repo / "a.md"
        doc.write_text(
            '<a href="javascript:void(0)">js</a>\n\n'
            '<a href="tel:10086">tel</a>\n\n'
            '<a href="//example.com/x.md">协议相对</a>\n',
            encoding="utf-8",
        )
        _register(monkeypatch, doc)
        assert cdc.check_anchor_dead_links() == []
        assert cdc.check_relative_dead_links() == []

    def test_markdown_and_html_same_line_deduped(self, doc_repo, monkeypatch):
        # Markdown 目标被 markdown-it percent-encode，HTML href 保留原文，去重须归一化
        doc = doc_repo / "a.md"
        doc.write_text('[同名](./缺失.md) <a href="./缺失.md">同名</a>\n', encoding="utf-8")
        _register(monkeypatch, doc)
        errors = cdc.check_relative_dead_links()
        assert len(errors) == 1, errors
