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


class TestUrlClassificationD31:
    """D31：URL 分类/path-query-fragment 分离/解码，以及 HTML href 进入链接视图。"""

    def _target(self, doc_repo: Path) -> Path:
        target = doc_repo / "README.md"
        target.write_text("# 真标题\n\n## 小节\n", encoding="utf-8")
        return target

    def test_encoded_filename_passes(self, doc_repo, monkeypatch):
        target = self._target(doc_repo)
        doc = doc_repo / "a.md"
        doc.write_text("[x](./README%2Emd)\n", encoding="utf-8")
        _register(monkeypatch, doc, target)
        assert cdc.check_relative_dead_links() == [], "percent-decode 后的真实文件不得误报"

    def test_query_string_not_part_of_filename(self, doc_repo, monkeypatch):
        target = self._target(doc_repo)
        doc = doc_repo / "a.md"
        doc.write_text("[x](./README.md?raw=1)\n", encoding="utf-8")
        _register(monkeypatch, doc, target)
        assert cdc.check_relative_dead_links() == [], "query 不应参与文件存在性判断"

    def test_encoded_path_with_query_and_anchor_passes(self, doc_repo, monkeypatch):
        target = self._target(doc_repo)
        doc = doc_repo / "a.md"
        doc.write_text("[x](./README%2Emd?raw=1#小节)\n", encoding="utf-8")
        _register(monkeypatch, doc, target)
        assert cdc.check_relative_dead_links() == []
        assert cdc.check_anchor_dead_links() == []

    def test_encoded_hash_and_question_are_literal_in_filename(self, doc_repo, monkeypatch):
        # 拆分先于解码：%23 / %3F 解码后是文件名的一部分，不得被当成分隔符
        target = doc_repo / "odd#name?.md"
        target.write_text("# 目标\n", encoding="utf-8")
        doc = doc_repo / "a.md"
        doc.write_text("[x](./odd%23name%3F.md)\n", encoding="utf-8")
        _register(monkeypatch, doc, target)
        assert cdc.check_relative_dead_links() == []

    def test_html_href_missing_target_fails(self, doc_repo, monkeypatch):
        doc = doc_repo / "a.md"
        doc.write_text('<a href="never-exists.md#missing">x</a>\n', encoding="utf-8")
        _register(monkeypatch, doc)
        errors = cdc.check_anchor_dead_links()
        assert errors and any("never-exists.md" in e for e in errors), "HTML href 缺目标不得静默放过"

    def test_html_href_missing_anchor_fails(self, doc_repo, monkeypatch):
        target = self._target(doc_repo)
        doc = doc_repo / "a.md"
        doc.write_text('<a href="README.md#missing">x</a>\n', encoding="utf-8")
        _register(monkeypatch, doc, target)
        errors = cdc.check_anchor_dead_links()
        assert errors and any("missing" in e for e in errors), "HTML href 缺锚点必须失败"

    def test_html_href_existing_target_and_anchor_passes(self, doc_repo, monkeypatch):
        target = self._target(doc_repo)
        doc = doc_repo / "a.md"
        doc.write_text('<a href="README.md#小节">x</a>\n', encoding="utf-8")
        _register(monkeypatch, doc, target)
        assert cdc.check_anchor_dead_links() == []

    def test_html_block_href_missing_target_fails(self, doc_repo, monkeypatch):
        doc = doc_repo / "a.md"
        doc.write_text('<div>\n<a href="never-exists.md">x</a>\n</div>\n', encoding="utf-8")
        _register(monkeypatch, doc)
        errors = cdc.check_relative_dead_links()
        assert errors and any("never-exists.md" in e for e in errors)

    def test_markdown_equivalent_scenario_fails(self, doc_repo, monkeypatch):
        # Markdown 同场景对照：与 HTML href 走同一检查
        doc = doc_repo / "a.md"
        doc.write_text("[x](never-exists.md#missing)\n", encoding="utf-8")
        _register(monkeypatch, doc)
        errors = cdc.check_anchor_dead_links()
        assert errors and any("never-exists.md" in e for e in errors)

    def test_parse_link_target_classification(self):
        external = cdc.docs_markdown.parse_link_target("https://example.com/a.md?x=1#frag")
        assert external.external
        mail = cdc.docs_markdown.parse_link_target("mailto:a@b.com?subject=x")
        assert mail.external
        local = cdc.docs_markdown.parse_link_target("./odd%23name%3F.md?a=1#小节")
        assert (local.external, local.path, local.query, local.fragment) == (False, "./odd#name?.md", "a=1", "小节")
        same_file = cdc.docs_markdown.parse_link_target("#小节")
        assert same_file.is_fragment_only
        windows = cdc.docs_markdown.parse_link_target("./sub\\file.md")
        assert windows.path == "./sub/file.md", "Windows 路径分隔符统一为正斜杠"
