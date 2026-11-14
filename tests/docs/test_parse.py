"""D3 解析视图测试：对应检视 §6.4「解析」负例清单与 §5.1 职责边界。

五组负例：
1. 引用式死链（未定义引用）必须检出；
2. 重复标题锚点经 slugger 序号正确区分；
3. HTML ID 正确提取，且 ``<a id="x">`` 与标题锚点可区分；
4. 两类围栏（``` 与 ~~~）与行内代码中的假链接不误报为死链；
5. 真缺失锚点/未知语法显式失败或明确「未核」，绝不从全文任取样例代替。
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, cast

import pytest

import docs_markdown as dm

pytestmark = pytest.mark.docs_pure

# 组 1 样例：引用式死链 + 合法引用对照（行号 1-based）
DEAD_REF_DOC = "# 标题\n\n[dead][nope]\n\n[plain][]\n\n[ok][ref]\n\n[ref]: https://example.com/a\n"

# 组 2 样例：重复标题（ASCII 与中文）
DUPLICATE_HEADINGS_DOC = "## Setup\n内容\n## Setup\n## 安装 指南\n尾行\n## 安装 指南\n"

# 组 3 样例：HTML ID（行内 a / 块级 div / 标题内嵌）与标题锚点
# （块级 div 后必须空行：CommonMark type 6 HTML 块仅由空行结束，否则吞掉后续标题行）
HTML_ID_DOC = '## 落款\n正文 <a id="api-ref">说明</a> 续\n\n<div id="box"></div>\n\n## <a id="in-heading"></a> 章节\n'

# 组 4 样例：两类围栏 + 行内代码中的假链接（全部在代码语境，不应判为链接）
FENCED_DOC = "```py\n[a][nope]\n```\n\n~~~py\n[x](y)\n~~~\n\n行内 `[x](y)` 与 `[a][nope]` 均为代码语境\n"


def test_reference_dead_link_detected(view_factory) -> None:
    """组 1：未定义引用（full / collapsed）必须检出；已定义引用不算死链。"""
    view = view_factory(DEAD_REF_DOC)
    unresolved = view.unresolved_references
    labels = [(r.label, r.form, r.line) for r in unresolved]
    assert ("nope", "full", 3) in labels, f"full 引用死链未检出: {labels}"
    assert ("plain", "collapsed", 5) in labels, f"collapsed 引用死链未检出: {labels}"
    assert len(unresolved) == 2, f"应恰有 2 个死引用: {labels}"
    # 已定义引用不误报，且解析为链接（引用式 kind）
    assert all(r.label != "ref" for r in unresolved)
    resolved = [(link.text, link.target, link.kind, link.line) for link in view.links]
    assert resolved == [("ok", "https://example.com/a", "reference", 7)], resolved


def test_duplicate_heading_anchor_slugger_suffix(view_factory) -> None:
    """组 2：重复标题锚点必须带 slugger 序号正确区分（含中文标题）。"""
    view = view_factory(DUPLICATE_HEADINGS_DOC)
    slugs = [h.slug for h in view.headings]
    assert slugs == ["setup", "setup-1", "安装-指南", "安装-指南-1"], slugs
    lines = [h.line for h in view.headings]
    assert lines == [1, 3, 4, 6], lines
    # 每个锚点可独立命中（重复标题不共享同一锚）
    assert view.has_anchor("setup") and view.has_anchor("setup-1")


def test_html_id_extraction_distinguishable(view_factory) -> None:
    """组 3：HTML ID 提取正确，且 <a id="x"> 与标题锚点可区分。"""
    view = view_factory(HTML_ID_DOC)
    ids = {i.id: (i.tag, i.source, i.line) for i in view.html_ids}
    assert ids == {
        "api-ref": ("a", "inline", 2),
        "box": ("div", "block", 4),
        "in-heading": ("a", "inline", 6),
    }, ids
    # 标题文本提取跳过 HTML 子 token：第 6 行标题 slug 来自可见文本「章节」
    heading_slugs = [h.slug for h in view.headings]
    assert heading_slugs == ["落款", "章节"], heading_slugs
    # <a id="x"> 与标题锚点可区分：独立字段且无交叉污染
    assert "in-heading" not in heading_slugs
    assert "章节" not in ids
    # anchor_ids 是标题 slug 与 HTML ID 的并集
    anchors = view.anchor_ids()
    assert anchors == frozenset({"落款", "章节", "api-ref", "box", "in-heading"}), anchors


def test_fences_and_inline_code_no_false_link(view_factory) -> None:
    """组 4：两类围栏与行内代码中的假链接不误报为死链/链接。"""
    view = view_factory(FENCED_DOC)
    assert view.unresolved_references == (), view.unresolved_references
    assert view.links == (), view.links
    blocks = [(b.fence, b.info, b.start_line, b.end_line) for b in view.code_blocks]
    assert blocks == [("```", "py", 1, 3), ("~~~", "py", 5, 7)], blocks


def test_missing_anchor_and_unknown_syntax_explicit(write_doc) -> None:
    """组 5：真缺失锚点显式 False；未知/不可解析输入显式失败或明确「未核」。"""
    view = dm.parse_text(HTML_ID_DOC)
    # 缺失锚点显式返回 False（不伪造、不从全文任取样例代替）
    assert view.has_anchor("never-defined") is False
    assert view.has_anchor("api-ref") is True
    # 未核通道：真实 HTML 原样保留（fragment 由原验证者核验，不在解析层内嵌执行）
    assert len(view.raw_html_tokens) > 0
    assert any("api-ref" in content for content, _line in view.raw_html_tokens)
    # 文件级异常原样传播（显式失败，不吞不降级）；断言异常携带定位信息
    missing = Path(write_doc("absent.sentinel", "x")).parent / "absent.md"
    with pytest.raises(FileNotFoundError) as missing_info:
        dm.load_document_view(missing)
    assert missing_info.value.filename == str(missing)
    bad = write_doc("bad.md", "ok")
    bad.write_bytes(b"# \xff\xfe invalid utf8\n")
    with pytest.raises(UnicodeDecodeError) as decode_info:
        dm.load_document_view(bad)
    assert decode_info.value.encoding == "utf-8"


def test_view_immutable_and_slugger_backend_honest() -> None:
    """视图不可变；slugger 后端声明与实际返回类型一致（不假装用了不可用的包）。"""
    view = dm.parse_text("# t\n")
    with pytest.raises(FrozenInstanceError) as frozen_info:
        # frozen dataclass 禁止就地修改（cast 绕过静态检查，仅验证运行时不可变契约）
        cast(Any, view.headings[0]).slug = "mutated"
    assert "slug" in str(frozen_info.value)
    slugger = dm.create_slugger()
    if dm.SLUGGER_BACKEND == "github-slugger":
        assert type(slugger).__module__.startswith("github_slugger"), type(slugger)
    else:
        assert isinstance(slugger, dm.CompatGithubSlugger), type(slugger)
