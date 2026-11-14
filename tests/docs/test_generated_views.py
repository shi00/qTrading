"""D6 生成视图测试：docs/README 文件级目录/ADR 索引与 governance README 规则参考/任务路由。

覆盖：四个新渲染函数的正例与 fail-closed 负例（缺 H1/非法文件名/空清单显式拒绝）、
快照字节渲染通道（同一文件兼为目标与作者源时，独占保护窗口内经快照读取而非磁盘
直读——防 Windows 共享冲突导致的标题占位漂移）、内置注册表新增激活条目的不变量。
全部在临时目录构造，不触碰仓库真实正文；真实仓库的块一致性由 check_docs_generated 守护。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import docs_generated as dg

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import check_docs_consistency as cdc  # noqa: E402

pytestmark = pytest.mark.docs_pure


# ---------------------------------------------------------------------------
# 文件级目录索引
# ---------------------------------------------------------------------------


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _file_index_registry() -> tuple[dg.BlockSpec, ...]:
    return (
        dg.BlockSpec(
            block_id="docs-readme-file-index",
            target="docs/README.md",
            sources=(
                dg.SourceSchema(path="docs/README.md", fmt="text"),
                dg.SourceSchema(path="docs/governance/redlines.yml", fmt="text"),
            ),
            render=dg._render_docs_readme_file_index,
        ),
    )


def test_file_index_renders_titles_from_h1_and_comments(tmp_path: Path) -> None:
    """正例：md 取 H1、yml 取首注释行、无 H1 md 显式占位、无注释 json 显式 —。"""
    root = tmp_path
    _write(root, "docs/README.md", "# Project Documentation Index\n<!-- generated:idx -->\n<!-- /generated -->\n")
    _write(root, "docs/a.md", "# Alpha 专题\n正文\n")
    _write(root, "docs/sub/b.md", "无标题正文\n")
    _write(root, "docs/governance/redlines.yml", "# 红线正本\nredlines: []\n")
    _write(root, "docs/data.json", '{"k": 1}\n')

    rendered = dg.render_block_lines(root, _file_index_registry()[0])
    assert "| [a.md](./a.md) | Alpha 专题 |" in rendered
    assert "| [sub/b.md](./sub/b.md) | （无 H1 标题） |" in rendered
    assert "| [governance/redlines.yml](./governance/redlines.yml) | 红线正本 |" in rendered
    assert "| [data.json](./data.json) | — |" in rendered
    assert "| [README.md](./README.md) | Project Documentation Index |" in rendered


def test_file_index_uses_snapshot_text_for_protected_files(tmp_path: Path) -> None:
    """快照通道回归：登记为 text 作者源的文件在更新窗口内经快照字节渲染。

    渲染只读 validated（保护窗口内目标被独占锁定，磁盘直读会 PermissionError）——
    这是独占保护窗口下更新与 --check 两侧渲染一致的根（Windows 共享冲突语义的回归）。
    """
    root = tmp_path
    _write(root, "docs/README.md", "# 磁盘标题\n<!-- generated:idx -->\n<!-- /generated -->\n")
    spec = dg.BlockSpec(
        block_id="idx",
        target="docs/README.md",
        sources=(dg.SourceSchema(path="docs/README.md", fmt="text"),),
        render=lambda validated, root_path: [
            f"rows={len(str(validated['docs/README.md']).splitlines())}",
            f"first={str(validated['docs/README.md']).splitlines()[0]}",
        ],
    )
    result = dg.run_update(root, (spec,))
    assert result.ok, result
    written = (root / "docs/README.md").read_text(encoding="utf-8")
    assert "first=# 磁盘标题" in written  # 渲染输入 = 保护内核对过的快照字节
    assert "rows=3" in written


def test_file_index_empty_candidates_rejected(tmp_path: Path) -> None:
    """负例：docs/ 下无候选文件 → 显式拒绝（不静默渲染空表）。"""
    root = tmp_path
    (root / "docs").mkdir()
    spec = dg.BlockSpec(
        block_id="idx",
        target="docs/README.md",
        sources=(),
        render=dg._render_docs_readme_file_index,
    )
    with pytest.raises(dg.SourceFormatError, match="作者源为空"):
        dg.render_block_lines(root, spec)


# ---------------------------------------------------------------------------
# ADR 索引
# ---------------------------------------------------------------------------


def test_adr_index_renders_all_adrs_with_h1_title(tmp_path: Path) -> None:
    """正例：编号取自文件名、标题取自 H1 并剥离 ADR-NNNN 前缀；编号自动补齐（ADR-0017 场景）。"""
    root = tmp_path
    _write(root, "docs/adr/0001-record-decisions.md", "# ADR-0001: 记录架构决策\n\n正文\n")
    _write(root, "docs/adr/0017-version-ssot.md", "# ADR-0017: 版本号单一正本收敛\n")
    _write(root, "docs/README.md", "# Index\n<!-- generated:adr -->\n<!-- /generated -->\n")
    spec = dg.BlockSpec(
        block_id="adr",
        target="docs/README.md",
        sources=(),
        render=dg._render_docs_readme_adr_index,
    )
    rendered = dg.render_block_lines(root, spec)
    assert "| ADR-0001 | 记录架构决策 | [0001-record-decisions.md](./adr/0001-record-decisions.md) |" in rendered
    assert "| ADR-0017 | 版本号单一正本收敛 | [0017-version-ssot.md](./adr/0017-version-ssot.md) |" in rendered


def test_adr_index_missing_h1_rejected(tmp_path: Path) -> None:
    """负例：ADR 缺 H1 → 显式拒绝（文件自己的标题是发现信息正本）。"""
    root = tmp_path
    _write(root, "docs/adr/0001-no-title.md", "没有 H1\n")
    _write(root, "docs/README.md", "# Index\n<!-- generated:adr -->\n<!-- /generated -->\n")
    spec = dg.BlockSpec(
        block_id="adr",
        target="docs/README.md",
        sources=(),
        render=dg._render_docs_readme_adr_index,
    )
    with pytest.raises(dg.SourceFormatError, match="缺 H1 标题"):
        dg.render_block_lines(root, spec)


def test_adr_index_bad_filename_rejected(tmp_path: Path) -> None:
    """负例：文件名不符合 NNNN-slug.md → 显式拒绝。"""
    root = tmp_path
    _write(root, "docs/adr/笔记.md", "# 标题\n")
    _write(root, "docs/README.md", "# Index\n<!-- generated:adr -->\n<!-- /generated -->\n")
    spec = dg.BlockSpec(
        block_id="adr",
        target="docs/README.md",
        sources=(),
        render=dg._render_docs_readme_adr_index,
    )
    with pytest.raises(dg.SourceFormatError, match="NNNN-slug"):
        dg.render_block_lines(root, spec)


def test_adr_index_missing_dir_rejected(tmp_path: Path) -> None:
    """负例：docs/adr/ 缺失 → SourceMissingError。"""
    root = tmp_path
    _write(root, "docs/README.md", "# Index\n<!-- generated:adr -->\n<!-- /generated -->\n")
    spec = dg.BlockSpec(
        block_id="adr",
        target="docs/README.md",
        sources=(),
        render=dg._render_docs_readme_adr_index,
    )
    with pytest.raises(dg.SourceMissingError, match="docs/adr"):
        dg.render_block_lines(root, spec)


# ---------------------------------------------------------------------------
# 规则参考视图（redlines.yml）
# ---------------------------------------------------------------------------


def test_rules_view_renders_redlines_table(tmp_path: Path) -> None:
    """正例：全量红线四列视图；coverage 缺失以 — 显式占位；竖线转义不破表。"""
    root = tmp_path
    _write(
        root,
        "docs/governance/redlines.yml",
        "redlines:\n"
        "  - id: R1\n"
        "    title: 架构越界\n"
        "    rule_type: EXCEPTIONABLE\n"
        "    automation_coverage: pre-commit（import-linter）\n"
        "    description: 不允许越界\n"
        "  - id: R2\n"
        "    title: 异常吞没\n"
        "    rule_type: INVARIANT\n"
        "    description: 含|竖线标题\n",
    )
    spec = dg.BlockSpec(
        block_id="rules",
        target="docs/governance/README.md",
        sources=(dg._REDLINES_SCHEMA,),
        render=dg._render_governance_rules_view,
    )
    rendered = dg.render_block_lines(root, spec)
    assert "| R1 | 架构越界 | EXCEPTIONABLE | pre-commit（import-linter） |" in rendered
    assert "| R2 | 异常吞没 | INVARIANT | — |" in rendered


# ---------------------------------------------------------------------------
# 任务路由视图（canonical-topics.yml）
# ---------------------------------------------------------------------------


def test_topics_view_merges_by_canonical_and_section(tmp_path: Path) -> None:
    """正例：相同 canonical+section 合并一行并保全部 topic id；相对链接指向仓库根。"""
    root = tmp_path
    _write(
        root,
        "docs/governance/canonical-topics.yml",
        "topics:\n"
        "  - id: ui-view\n"
        "    title: 新增/修改 UI 视图\n"
        "    canonical: docs/flet/README.md\n"
        "  - id: ui-layout\n"
        "    title: 修改 UI 布局\n"
        "    canonical: docs/flet/README.md\n"
        "  - id: fallback\n"
        "    title: 未列出的任务类型\n"
        "    canonical: CLAUDE.md\n"
        "  - id: testing\n"
        "    title: 新增测试\n"
        "    canonical: docs/guides/testing.md\n"
        "    section: 测试编写模板\n",
    )
    spec = dg.BlockSpec(
        block_id="topics",
        target="docs/governance/README.md",
        sources=(dg._CANONICAL_TOPICS_SCHEMA,),
        render=dg._render_governance_topics_view,
    )
    rendered = dg.render_block_lines(root, spec)
    assert (
        "| ui-view, ui-layout | 新增/修改 UI 视图 / 修改 UI 布局 | [docs/flet/README.md](../flet/README.md) | — |"
        in rendered
    )
    assert "| fallback | 未列出的任务类型 | [CLAUDE.md](../../CLAUDE.md) | — |" in rendered
    assert "| testing | 新增测试 | [docs/guides/testing.md](../guides/testing.md) | 测试编写模板 |" in rendered


def test_topics_view_identity_duplicate_rejected(tmp_path: Path) -> None:
    """负例：topic id 重复 → 作者源校验显式拒绝（不静默合并），错误信息指认重复 id。"""
    root = tmp_path
    _write(
        root,
        "docs/governance/canonical-topics.yml",
        "topics:\n"
        "  - id: dup\n"
        "    title: A\n"
        "    canonical: docs/flet/README.md\n"
        "  - id: dup\n"
        "    title: B\n"
        "    canonical: CLAUDE.md\n",
    )
    spec = dg.BlockSpec(
        block_id="topics",
        target="docs/governance/README.md",
        sources=(dg._CANONICAL_TOPICS_SCHEMA,),
        render=dg._render_governance_topics_view,
    )
    with pytest.raises(dg.SourceDuplicateIdentityError) as excinfo:
        dg.render_block_lines(root, spec)
    assert "dup" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 内置注册表不变量（D6 新增激活条目）
# ---------------------------------------------------------------------------


def test_builtin_registry_d6_entries_active_and_consistent() -> None:
    """D6 四个激活条目：目标/格式/渲染接线正确；未接线槽位保持 inactive 不伪造生成。"""
    by_id = {spec.block_id: spec for spec in dg.BUILTIN_REGISTRY}
    for block_id, target in (
        ("docs-readme-file-index", "docs/README.md"),
        ("docs-readme-adr-index", "docs/README.md"),
        ("governance-readme-rules", "docs/governance/README.md"),
        ("governance-readme-topics", "docs/governance/README.md"),
    ):
        spec = by_id[block_id]
        assert spec.active and spec.render is not None
        assert spec.target == target and spec.block_format == "markdown"
    for slot in ("a11y-contrast-reference", "precommit-docs-parser-deps"):
        assert by_id[slot].active is False and by_id[slot].render is None
    dg._validate_specs(dg.BUILTIN_REGISTRY)  # 注册表整体跨条目约束（目标格式一致/块 id 不重复）


# ---------------------------------------------------------------------------
# D01：CLAUDE §1.8 决策树 / §3.1 红线表由 YAML 单一作者源生成
# ---------------------------------------------------------------------------


def _builtin(block_id: str) -> dg.BlockSpec:
    return next(spec for spec in dg.BUILTIN_REGISTRY if spec.block_id == block_id)


def test_claude_generated_views_match_author_sources() -> None:
    """真实 CLAUDE.md 的两个生成块与 YAML 渲染结果逐行一致。"""
    content = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    for block_id, tag in (
        ("claude-decision-tree", "<!-- generated:claude-decision-tree -->"),
        ("claude-redlines-table", "<!-- generated:claude-redlines-table -->"),
    ):
        expected = dg.render_block_lines(ROOT, _builtin(block_id))
        errors = cdc._generated_block_sync(content, tag, expected, f"{block_id} 与作者源不一致", f"{block_id} 缺生成块")
        assert errors == [], errors


def test_claude_derived_copy_edit_fails() -> None:
    """只改派生副本（CLAUDE 表）不改作者源必须被一致性检查判红（L01/D01）。"""
    content = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    tag = "<!-- generated:claude-decision-tree -->"
    expected = dg.render_block_lines(ROOT, _builtin("claude-decision-tree"))
    assert cdc._generated_block_sync(content, tag, expected, "mismatch", "missing") == []
    tampered = content.replace("<!-- route: strategy -->", "<!-- route: strategy --> 手改", 1)
    assert tampered != content
    assert cdc._generated_block_sync(tampered, tag, expected, "mismatch", "missing") == ["mismatch"]


def test_claude_redlines_render_tracks_yaml_author_source(tmp_path: Path) -> None:
    """只改一次 redlines.yml（新增红线）即传播到 CLAUDE 红线表，无需手改正文。"""
    source = tmp_path / "docs" / "governance" / "redlines.yml"
    source.parent.mkdir(parents=True)
    original = (ROOT / "docs/governance/redlines.yml").read_text(encoding="utf-8")
    source.write_text(
        original
        + "\n  - id: R25\n"
        + "    title: 演示新增红线\n"
        + "    description: 演示说明\n"
        + "    enforcement: 仅人工评审\n"
        + "    automation_coverage: none\n"
        + "    human_review_required: true\n"
        + "    rule_type: WORKFLOW\n",
        encoding="utf-8",
    )
    lines = dg.render_block_lines(tmp_path, _builtin("claude-redlines-table"))
    assert any("R25" in line and "演示新增红线" in line and "仅人工评审" in line for line in lines)
    assert dg.render_block_lines(tmp_path, _builtin("claude-redlines-table")) == lines, "二次渲染必须零差异"


def test_claude_decision_tree_render_tracks_yaml_author_source(tmp_path: Path) -> None:
    """只改一次 canonical-topics.yml（新增 topic）即传播到 CLAUDE 决策树。"""
    source = tmp_path / "docs" / "governance" / "canonical-topics.yml"
    source.parent.mkdir(parents=True)
    original = (ROOT / "docs/governance/canonical-topics.yml").read_text(encoding="utf-8")
    source.write_text(
        original
        + "\n  - id: demo-topic\n"
        + "    title: 演示新增任务\n"
        + "    canonical: docs/guides/how-to.md\n"
        + "    section: 1. 演示章节\n",
        encoding="utf-8",
    )
    lines = dg.render_block_lines(tmp_path, _builtin("claude-decision-tree"))
    assert any("演示新增任务" in line and "route: demo-topic" in line for line in lines)
    assert any("how-to.md「1. 演示章节」" in line for line in lines)
    assert dg.render_block_lines(tmp_path, _builtin("claude-decision-tree")) == lines, "二次渲染必须零差异"


def test_claude_decision_tree_meta_entry_requires_note(tmp_path: Path) -> None:
    """元条目（fallback）缺 note 时显式拒绝，不生成半行或空入口。"""
    source = tmp_path / "docs" / "governance" / "canonical-topics.yml"
    source.parent.mkdir(parents=True)
    source.write_text(
        "topics:\n  - id: fallback\n    title: 未列出的任务类型\n    canonical: CLAUDE.md\n",
        encoding="utf-8",
    )
    with pytest.raises(dg.SourceFieldError, match="note"):
        dg.render_block_lines(tmp_path, _builtin("claude-decision-tree"))
