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


class TestUrlDecomposition:
    """Q09：URL 统一分解后解码本地 path——编码/中文/空格/query/fragment 不误报。"""

    def test_chinese_space_and_preencoded_paths_pass(self, doc_repo, monkeypatch):
        doc = doc_repo / "a.md"
        (doc_repo / "中文文档.md").write_text("# 中文标题\n", encoding="utf-8")
        (doc_repo / "real file.md").write_text("# 空格\n", encoding="utf-8")
        doc.write_text(
            "[中文](./中文文档.md)\n\n[空格](<./real file.md>)\n\n[预编码](./real%20file.md)\n",
            encoding="utf-8",
        )
        _register(monkeypatch, doc)
        assert cdc.check_relative_dead_links() == [], "中文/空格/预编码路径解码后应存在"

    def test_query_string_not_part_of_local_path(self, doc_repo, monkeypatch):
        doc = doc_repo / "a.md"
        (doc_repo / "real.md").write_text("# 真实\n", encoding="utf-8")
        doc.write_text("[查询](./real.md?raw=1)\n\n[纯查询](?tab=readme)\n", encoding="utf-8")
        _register(monkeypatch, doc)
        assert cdc.check_relative_dead_links() == [], "query 不得拼入磁盘路径"

    def test_anchor_check_strips_query_before_resolving(self, doc_repo, monkeypatch):
        doc = doc_repo / "a.md"
        target = doc_repo / "中文文档.md"
        target.write_text("# 中文标题\n", encoding="utf-8")
        doc.write_text("[跨文件](./中文文档.md?raw=1#中文标题)\n", encoding="utf-8")
        _register(monkeypatch, doc, target)
        assert cdc.check_anchor_dead_links() == [], "带锚点分支同样需剥离 query 并解码路径"

    def test_encoded_hash_in_filename_not_treated_as_fragment(self, doc_repo, monkeypatch):
        # 先分离 fragment 再解码：`%23` 是文件名中的字面 `#`，不是锚点分隔符
        doc = doc_repo / "a.md"
        (doc_repo / "a#b.md").write_text("# 目标\n", encoding="utf-8")
        doc.write_text("[哈希](./a%23b.md)\n", encoding="utf-8")
        _register(monkeypatch, doc)
        assert cdc.check_relative_dead_links() == []

    def test_single_decode_no_double_decode(self, doc_repo, monkeypatch):
        # `%2520` 只解码一次为字面 `%20`；二次解码会错误命中 `a b.md`
        doc = doc_repo / "a.md"
        doc.write_text("[双编码](./a%2520b.md)\n", encoding="utf-8")
        (doc_repo / "a%20b.md").write_text("# 字面\n", encoding="utf-8")
        _register(monkeypatch, doc)
        assert cdc.check_relative_dead_links() == []
        (doc_repo / "a%20b.md").unlink()
        (doc_repo / "a b.md").write_text("# 空格\n", encoding="utf-8")
        assert cdc.check_relative_dead_links(), "不得二次解码 `%2520` 为空格"

    def test_nul_encoded_path_reports_missing_without_crash(self, doc_repo, monkeypatch):
        # `%00` 解码后不是合法路径；检查器须报缺失而不是让 resolve() 抛 ValueError
        doc = doc_repo / "a.md"
        doc.write_text("[NUL](./a%00b.md)\n", encoding="utf-8")
        _register(monkeypatch, doc)
        errors = cdc.check_relative_dead_links()
        assert errors and any("a%00b.md" in e for e in errors)

    def test_missing_target_still_fails_after_decomposition(self, doc_repo, monkeypatch):
        doc = doc_repo / "a.md"
        doc.write_text("[缺件](./missing.md?raw=1)\n", encoding="utf-8")
        _register(monkeypatch, doc)
        errors = cdc.check_relative_dead_links()
        assert errors and any("missing.md" in e for e in errors)

    def test_unknown_scheme_reported_as_unsupported(self, doc_repo, monkeypatch):
        doc = doc_repo / "a.md"
        doc.write_text(
            "[FTP](ftp://example.com/x.md)\n\n[电话](tel:+8613800000000)\n\n[FTP锚](ftp://example.com/x.md#frag)\n",
            encoding="utf-8",
        )
        _register(monkeypatch, doc)
        errors = cdc.check_relative_dead_links()
        assert len(errors) == 3, errors
        assert all("不支持的链接协议" in e for e in errors)
        assert not any("目标" in e for e in errors), "未知协议不得误报为本地死链"
        assert cdc.check_anchor_dead_links() == [], "未知协议不重复报，也不进入锚点分支"

    def test_scheme_relative_external_link_ignored(self, doc_repo, monkeypatch):
        doc = doc_repo / "a.md"
        doc.write_text("[外站](//example.com/x.md)\n", encoding="utf-8")
        _register(monkeypatch, doc)
        assert cdc.check_relative_dead_links() == []
