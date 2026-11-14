r"""D5：verify_versions 文档引用检查委托共享解析与必需正文显式失败（检视 F22 / §5.1）。

F22：版本脚本的文档引用检查（Check 5 空链接 / Check 6 CLAUDE 引用）不得维护第二套
文档解析——统一委托 docs_markdown 解析视图；且必需正文（README.md / CLAUDE.md）缺失
时必须显式失败，不得静默跳过装通过（F17 失踪输入空转在同一边界的变体）。

委托的行为证据：围栏代码块 / 行内代码中的 `]()` 示例文本不误报（旧行级子串扫描
`"]()" in line` 会误报，仅共享解析视图可排除）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import docs_markdown  # noqa: E402
import verify_versions as vv  # noqa: E402

pytestmark = pytest.mark.docs_pure


class TestEmptyMarkdownLinksSharedParsing:
    """Check 5：空链接检查经共享解析视图（代码示例不冒正文，F22）。"""

    def test_code_block_and_inline_code_examples_not_reported(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """围栏代码块与行内代码中的 `]()` 是示例文本 → 不误报（委托共享解析的行为证据）."""
        readme = tmp_path / "README.md"
        readme.write_text(
            "# 标题\n\n"
            "正文说明空链接写法见示例 `](）` 与下方代码块：\n\n"
            "```markdown\n"
            "[文字]()\n"
            "```\n\n"
            "- 行内示例：`[x]()` 不构成真实链接\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(vv, "README_PATH", readme)
        assert vv.check_empty_markdown_links() == [], "代码块/行内代码中的示例 ]() 不得误报"

    def test_real_empty_link_reported(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """正文真实空链接 → 报错（负例不回退）."""
        readme = tmp_path / "README.md"
        readme.write_text("# 标题\n\n[中文文档]()\n", encoding="utf-8")
        monkeypatch.setattr(vv, "README_PATH", readme)
        errors = vv.check_empty_markdown_links()
        assert len(errors) == 1, f"正文真实空链接必须报错, got: {errors}"
        assert "empty markdown link" in errors[0]

    def test_missing_readme_fails_loudly(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """README.md 缺失 → 显式失败，不得静默返回 []（F17/F22：失踪输入空转）."""
        monkeypatch.setattr(vv, "README_PATH", tmp_path / "_absent" / "README.md")
        errors = vv.check_empty_markdown_links()
        assert errors, "必需正文 README.md 缺失必须显式失败（不得静默跳过）"
        assert "缺失" in errors[0] or "not exist" in errors[0] or "不存在" in errors[0]

    def test_delegates_to_shared_parser(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """委托证明：空链接检查必须经 docs_markdown.load_document_view（第二套解析回归即红）."""
        readme = tmp_path / "README.md"
        readme.write_text("# 标题\n\n[ok](./x.md)\n", encoding="utf-8")
        monkeypatch.setattr(vv, "README_PATH", readme)
        calls: list[Path] = []
        original = docs_markdown.load_document_view

        def spy(path: str | Path) -> docs_markdown.DocumentView:
            calls.append(Path(path))
            return original(path)

        monkeypatch.setattr(vv.docs_markdown, "load_document_view", spy)
        vv.check_empty_markdown_links()
        assert calls == [readme], "Check 5 须委托共享解析视图（每 README 一次 load_document_view）"


class TestClaudeReferencesSharedParsing:
    """Check 6：CLAUDE.md 引用检查经共享解析视图（code_spans 提取，F22）。"""

    def test_existing_reference_passes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """引用真实存在的文件 → 通过（正例不回退）."""
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "check_redlines.py").write_text("# ok\n", encoding="utf-8")
        claude = tmp_path / "CLAUDE.md"
        claude.write_text("# CLAUDE.md\n\n见 `scripts/check_redlines.py`。\n", encoding="utf-8")
        monkeypatch.setattr(vv, "CLAUDE_PATH", claude)
        monkeypatch.setattr(vv, "ROOT", tmp_path)
        assert vv.check_claude_references() == []

    def test_missing_target_reported(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """引用不存在的 .py 文件 → 报错（真死链负例）."""
        claude = tmp_path / "CLAUDE.md"
        claude.write_text("# CLAUDE.md\n\n见 `scripts/ghost_missing.py`。\n", encoding="utf-8")
        monkeypatch.setattr(vv, "CLAUDE_PATH", claude)
        monkeypatch.setattr(vv, "ROOT", tmp_path)
        errors = vv.check_claude_references()
        assert len(errors) == 1, f"引用缺失文件必须报错, got: {errors}"
        assert "ghost_missing.py" in errors[0]

    def test_command_spans_skipped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """命令形态 code span（含空白，如 `python scripts/x.py`）不是路径引用 → 不误报."""
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "check_redlines.py").write_text("# ok\n", encoding="utf-8")
        claude = tmp_path / "CLAUDE.md"
        claude.write_text(
            "# CLAUDE.md\n\n运行 `python scripts/check_redlines.py`；§ 引用 `§4.1` 跳过。\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(vv, "CLAUDE_PATH", claude)
        monkeypatch.setattr(vv, "ROOT", tmp_path)
        assert vv.check_claude_references() == [], "命令形态 / 章节引用不得按路径校验误报"

    def test_missing_claude_fails_loudly(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """CLAUDE.md 缺失 → 显式失败，不得静默返回 []（F17/F22：失踪输入空转）."""
        monkeypatch.setattr(vv, "CLAUDE_PATH", tmp_path / "_absent" / "CLAUDE.md")
        errors = vv.check_claude_references()
        assert errors, "必需正文 CLAUDE.md 缺失必须显式失败（不得静默跳过）"
        assert "缺失" in errors[0] or "not exist" in errors[0] or "不存在" in errors[0]

    def test_uses_shared_view_code_spans(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """委托证明：引用提取必须经共享解析视图 code_spans（行级正则第二套解析回归即红）."""
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "check_redlines.py").write_text("# ok\n", encoding="utf-8")
        claude = tmp_path / "CLAUDE.md"
        claude.write_text("# CLAUDE.md\n\n见 `scripts/check_redlines.py`。\n", encoding="utf-8")
        monkeypatch.setattr(vv, "CLAUDE_PATH", claude)
        monkeypatch.setattr(vv, "ROOT", tmp_path)
        span_texts: list[str] = []
        original_parse = docs_markdown.parse_text

        def spy_parse(text: str) -> docs_markdown.DocumentView:
            view = original_parse(text)
            span_texts.extend(span.text for span in view.code_spans)
            return view

        monkeypatch.setattr(docs_markdown, "parse_text", spy_parse)
        vv.check_claude_references()
        assert "scripts/check_redlines.py" in span_texts, "Check 6 须经共享解析视图提取 code_spans"
