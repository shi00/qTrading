"""D5：check_docs_consistency 共享解析迁移的等价性回归（检视 §5.1 / §6.4 解析负例）。

迁移前旧实现为「全文正则再造 Markdown」：手写 ``` fence 状态机 + 行级标题/链接正则。
迁移后 extract_headings / github_anchor 必须委托 docs_markdown 统一解析视图，且行为
对真实语法边界等价或更准确：

- 两类围栏（``` 与 ~~~）内的标题/假链接不计入；
- 缩进代码块内的标记不计入（旧实现漏防，迁移后由 token 精确判定）；
- 行内代码中的 `# xxx` 不算标题、`[t](u)` 不算链接；
- HTML ID 作为锚点可用（与标题 slug 并集）；
- github_anchor 与 slugger 语义对真实文档标题一致（否则真实锚点死链结果翻转）。

本文件只依赖 docs 组与 scripts 解析模块（纯文档环境）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import docs_markdown as dm  # noqa: E402
from check_docs_consistency import extract_headings, github_anchor  # noqa: E402

pytestmark = pytest.mark.docs_pure


class TestGithubAnchorSluggerParity:
    """github_anchor 迁移后与 slugger 语义一致（锚点集合迁移不得翻转真实死链结果）。"""

    def test_matches_slugger_on_plain_heading(self):
        assert github_anchor("概述") == dm.create_slugger().slug("概述")

    def test_matches_slugger_on_punctuation_heading(self):
        # CLAUDE.md §3.1 标题形态：标点/emoji 剔除、空格逐个转连字符
        assert github_anchor("3.1 ❌ 绝对禁止") == dm.create_slugger().slug("3.1 ❌ 绝对禁止")

    def test_matches_slugger_on_english_suffix(self):
        assert github_anchor("已知架构技术债 (Known Technical Debt)") == dm.create_slugger().slug(
            "已知架构技术债 (Known Technical Debt)"
        )

    def test_repeated_heading_collision_sequential(self):
        # 重复标题由 slugger 在视图构建时统一编号；单点调用退化为同值，
        # 锚点存在性判定必须经视图 anchor_ids() 而非本函数（见链接检查测试）。
        assert github_anchor("示例") == dm.create_slugger().slug("示例")


class TestExtractHeadingsDelegatesToView:
    """extract_headings 委托统一解析视图：围栏/缩进块/行内代码边界。"""

    def test_real_heading_collected(self):
        anchors = extract_headings("# 总览\n\n正文\n")
        assert dm.create_slugger().slug("总览") in anchors

    def test_backtick_fence_heading_ignored(self):
        # 旧实现 ``` 状态机已防；迁移后不得回退
        content = "```markdown\n# 这是示例\n```\n"
        assert extract_headings(content) == set()

    def test_tilde_fence_heading_ignored(self):
        # ~~~ 围栏：旧实现 fence 状态机只认 ```.lstrip()，~~~ 内标题会被误判；
        # 迁移后必须正确忽略。
        content = "~~~\n# 波浪号围栏内的伪标题\n~~~\n"
        assert extract_headings(content) == set()

    def test_indented_code_block_heading_ignored(self):
        # 4 空格缩进块：旧实现完全未防（行级正则直接命中）；
        # 迁移后由 CommonMark 缩进代码块语义排除。
        content = "段落：\n\n    # 缩进块内的伪标题\n"
        assert extract_headings(content) == set()

    def test_inline_code_hash_not_heading(self):
        content = "行内代码 `# 不是标题` 保持正文。\n"
        assert extract_headings(content) == set()

    def test_longer_fence_close_requires_matching_length(self):
        # 4 反引号开启的围栏，3 反引号行不关闭（CommonMark 语义）
        content = "````\n# 围栏内\n```\n# 仍在围栏内\n````\n"
        assert extract_headings(content) == set()

    def test_view_anchor_ids_cover_headings_and_html_ids(self):
        # 锚点存在性判定入口为视图 anchor_ids()：标题 slug ∪ HTML id
        content = '<a id="custom-anchor"></a>\n\n## 标题甲\n'
        view = dm.parse_text(content)
        assert "custom-anchor" in view.anchor_ids()
        assert dm.create_slugger().slug("标题甲") in view.anchor_ids()
