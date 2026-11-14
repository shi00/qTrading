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
from fixtures import generator_fixtures as gf

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


def test_file_index_uses_snapshot_text_for_protected_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """快照通道回归：登记为 text 作者源的文件在更新窗口内经快照字节渲染。

    渲染只读 validated（保护窗口内目标被独占锁定，磁盘直读会 PermissionError）——
    这是独占保护窗口下更新与 --check 两侧渲染一致的根（Windows 共享冲突语义的回归）。
    原位写被拒绝（D28），因此以渲染输入捕获证明快照通道，而非读取落盘结果。
    """
    root = tmp_path
    original = "# 磁盘标题\n<!-- generated:idx -->\n<!-- /generated -->\n"
    _write(root, "docs/README.md", original)
    captured: dict[str, str] = {}

    def render(validated: dict[str, object], root_path: Path) -> list[str]:
        del root_path
        text = str(validated["docs/README.md"])
        captured["rows"] = str(len(text.splitlines()))
        captured["first"] = text.splitlines()[0]
        return [f"rows={captured['rows']}", f"first={captured['first']}"]

    spec = dg.BlockSpec(
        block_id="idx",
        target="docs/README.md",
        sources=(dg.SourceSchema(path="docs/README.md", fmt="text"),),
        render=render,
    )
    before = (root / "docs/README.md").read_bytes()
    monkeypatch.setattr(dg, "WriteGuard", gf.HeldFdWriteGuard)
    result = dg.run_update(root, (spec,))
    assert result.ok is False and result.written == ()
    assert (root / "docs/README.md").read_bytes() == before
    assert captured == {"rows": "3", "first": "# 磁盘标题"}  # 渲染输入 = 保护内核对过的快照字节


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
    _write(root, "docs/flet/README.md", "# Flet\n")
    _write(root, "CLAUDE.md", "# CLAUDE\n")
    _write(root, "docs/guides/testing.md", "# Testing\n")
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
    _write(root, "docs/flet/README.md", "# Flet\n")
    _write(root, "CLAUDE.md", "# CLAUDE\n")
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
    """D6 四个激活条目 + D23 两个 hook 依赖派生条目：接线正确；未接线槽位不伪造生成。"""
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
    for block_id in _HOOK_DEP_BLOCKS:
        spec = by_id[block_id]
        assert spec.active and spec.render is dg._render_precommit_docs_deps
        assert spec.target == ".pre-commit-config.yaml" and spec.block_format == "yaml"
        assert spec.sources == (dg._PYPROJECT_DOCS_SCHEMA,)
    assert by_id["a11y-contrast-reference"].active is False
    assert by_id["a11y-contrast-reference"].render is None
    dg._validate_specs(dg.BUILTIN_REGISTRY)  # 注册表整体跨条目约束（目标格式一致/块 id 不重复）


# ---------------------------------------------------------------------------
# D23：hook docs 依赖派生（pyproject docs 组 → 两个 hook 片段）
# ---------------------------------------------------------------------------

_PYPROJECT_DOCS = """\
[dependency-groups]
docs = [
    "markdown-it-py==4.0.0",
    "github-slugger==0.0.3",
    "pyyaml>=6.0",
    "pytest>=9.2.0",
]
"""

_HOOK_DEP_BLOCKS = ("precommit-docs-deps-verify-versions", "precommit-docs-deps-docs-consistency")


def _precommit_target() -> str:
    """含两个 hook 受控片段的 .pre-commit-config.yaml 最小目标文本。"""
    return (
        "repos:\n"
        "  - repo: local\n"
        "    hooks:\n"
        "      - id: verify-versions\n"
        "        additional_dependencies:\n"
        "          # generated:precommit-docs-deps-verify-versions\n"
        "          # /generated:precommit-docs-deps-verify-versions\n"
        "      - id: docs-consistency\n"
        "        additional_dependencies:\n"
        "          # generated:precommit-docs-deps-docs-consistency\n"
        "          # /generated:precommit-docs-deps-docs-consistency\n"
    )


def _hook_dep_specs() -> tuple[dg.BlockSpec, ...]:
    return tuple(spec for spec in dg.BUILTIN_REGISTRY if spec.target == ".pre-commit-config.yaml")


def _render_hook_deps(root: Path) -> dict[str, list[str]]:
    return {spec.block_id: dg.render_block_lines(root, spec) for spec in _hook_dep_specs()}


def test_precommit_docs_deps_derived_from_docs_group(tmp_path: Path) -> None:
    """D23 正例：两个 hook 片段同源派生，保留版本范围写法、排序稳定、pytest 被排除。"""
    _write(tmp_path, "pyproject.toml", _PYPROJECT_DOCS)
    rendered = _render_hook_deps(tmp_path)
    expected = [
        '          - "github-slugger==0.0.3"',
        '          - "markdown-it-py==4.0.0"',
        '          - "pyyaml>=6.0"',
    ]
    for block_id in _HOOK_DEP_BLOCKS:
        assert rendered[block_id] == expected
        assert all("pytest" not in line for line in rendered[block_id])


def test_docs_group_change_updates_both_hooks_and_is_idempotent(tmp_path: Path) -> None:
    """D23 正例：只改 docs 组一次 → 两个 hook 片段同时变化；二次生成零差异。"""
    _write(tmp_path, "pyproject.toml", _PYPROJECT_DOCS)
    before = _render_hook_deps(tmp_path)
    text = _precommit_target()
    for block_id in _HOOK_DEP_BLOCKS:
        text = dg.replace_block(text, "yaml", block_id, before[block_id])
    once = text
    for block_id in _HOOK_DEP_BLOCKS:
        text = dg.replace_block(text, "yaml", block_id, before[block_id])
    assert text == once, "同一作者源二次生成必须零差异"

    _write(tmp_path, "pyproject.toml", _PYPROJECT_DOCS.replace("markdown-it-py==4.0.0", "markdown-it-py==4.1.0"))
    after = _render_hook_deps(tmp_path)
    for block_id in _HOOK_DEP_BLOCKS:
        assert after[block_id] != before[block_id], "同一 docs 组变化必须同时反映到两个 hook"
        assert any("markdown-it-py==4.1.0" in line for line in after[block_id])


def test_precommit_docs_deps_missing_docs_group_rejected(tmp_path: Path) -> None:
    """D23 负例：pyproject 缺 docs 组 → 显式拒绝（不静默生成空依赖）。"""
    _write(tmp_path, "pyproject.toml", '[project]\nname = "x"\n')
    with pytest.raises(dg.SourceFormatError, match="dependency-groups"):
        dg.render_block_lines(tmp_path, _hook_dep_specs()[0])


def test_precommit_docs_deps_only_pytest_rejected(tmp_path: Path) -> None:
    """D23 负例：docs 组排除 pytest 后为空 → 显式拒绝。"""
    _write(tmp_path, "pyproject.toml", '[dependency-groups]\ndocs = ["pytest>=9.2.0"]\n')
    with pytest.raises(dg.SourceFormatError, match="排除 pytest 后为空"):
        dg.render_block_lines(tmp_path, _hook_dep_specs()[0])


def test_precommit_docs_deps_non_string_entry_rejected(tmp_path: Path) -> None:
    """D23 负例：docs 组含非字符串条目 → 显式拒绝。"""
    _write(tmp_path, "pyproject.toml", "[dependency-groups]\ndocs = [42]\n")
    with pytest.raises(dg.SourceFormatError, match="第 0 条不是非空字符串"):
        dg.render_block_lines(tmp_path, _hook_dep_specs()[0])
