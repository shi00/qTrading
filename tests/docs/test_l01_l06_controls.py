r"""D12：L01—L06 长期控制合同控制测试（documentation-maintenance 第 2 节）。

控制合同正本：docs/guides/documentation-maintenance.md 第 2 节 L01—L12
（承接 reviews/文档体系检视与改进.md 第 9.2 节）。本文件承载 L01—L06，
每个控制点以「注入违规 → 检查失败 → 清除/恢复 → 恢复通过」闭环证明机制有效：

- L01 双写事实：唯一作者源；修改派生不改源必须失败（check_docs_generated 一致性）；
- L02 漏消费者/身份变更：rename 新旧路径并集，不完整影响清单显式列未核；
- L03 规范/示例矛盾（机器下限）：代码例不冒正文，正文语义不被示例顶替；
- L04 状态/证据夸大：部分完成不认证整体（聚合语义 fail-closed）；
- L05 草案历史混入现行：当前入口指向缺失目标必须失败（可达性下限）；
- L06 多套解析误报漏检：所有 Markdown 消费共用 docs_markdown 单一语法视图。

人工核对项（机器不可判定，不虚报语义全覆盖）：
- L01：作者源值本身的语义正确性（两边一起改错不能以相等认证，须另核来源/语义）；
- L02：动态消费与自然语言关联（图中无边不代表没有语义影响）；
- L03：条件一致性、禁止例能否阻断、完成门按实际触域启用（语义评审层）；
- L04：证据的目标/版本/范围适用性重评（语义评审层）；
- L05：草案/历史角色判定——同名或日期新不能自行升效力。
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import check_docs_consistency as cdc  # noqa: E402
import docs_generated as dg  # noqa: E402
import docs_markdown as dm  # noqa: E402

pytestmark = pytest.mark.docs_pure


# ---------------------------------------------------------------------------
# L01：双写事实——唯一作者源，修改派生不改源必须失败
# ---------------------------------------------------------------------------


def test_l01_hand_edit_derived_block_fails_check_and_recovers(
    generated_block_repo: tuple[Path, tuple[dg.BlockSpec, ...]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """负例注入：手改派生块（修改派生不改源）→ 检查失败归因该块 → 从作者源恢复 → 恢复通过。"""
    root, registry = generated_block_repo
    monkeypatch.setattr(cdc, "ROOT", root)
    monkeypatch.setattr(dg, "BUILTIN_REGISTRY", registry)

    result = dg.run_update(root, registry)
    assert result.ok and "targets/derived.md" in result.written, "基线生成必须成功"
    assert cdc.check_docs_generated() == [], "刚生成的块必须与作者源渲染一致"

    target = root / "targets" / "derived.md"
    text = target.read_text(encoding="utf-8")
    target.write_text(text.replace("- max_rows: 500", "- max_rows: 999"), encoding="utf-8")

    errors = cdc.check_docs_generated()
    assert errors, "修改派生不改源必须失败（L01：重复展示只能引用或生成）"
    assert all("limits" in e for e in errors), f"报错须归因被手改的块身份: {errors}"
    assert any("第 " in e and "现块=" in e and "渲染=" in e for e in errors), (
        f"不一致须给出可核差异行（F17 显式失败原因，不留「不一致」无从定位）: {errors}"
    )

    restored = dg.run_update(root, registry)
    assert restored.ok and "targets/derived.md" in restored.written, "从作者源重新生成恢复"
    assert cdc.check_docs_generated() == [], "恢复后派生与作者源重新一致"


def test_l01_second_render_zero_diff_after_regen(
    generated_block_repo: tuple[Path, tuple[dg.BlockSpec, ...]],
) -> None:
    """作者源单次更新可重建派生；连续两次生成第二次零差异（L01 再现性）。"""
    root, registry = generated_block_repo
    first = dg.run_update(root, registry)
    assert first.ok and first.written == ("targets/derived.md",)
    second = dg.run_update(root, registry)
    assert second.ok and second.unchanged == ("targets/derived.md",), "二次生成必须零差异（幂等）"


# ---------------------------------------------------------------------------
# L02：漏消费者/身份变更——rename 新旧路径并集的临时影响清单
# ---------------------------------------------------------------------------

# 作者源前缀：治理正文与受控块作者源候选（L02 影响清单最小映射）。
_AUTHOR_PATH_PREFIXES: tuple[str, ...] = ("docs/", "CLAUDE.md", "AGENTS.md", "CONTRIBUTING.md")

# 测试组映射规则（按前缀首命中）：输入 changed 路径 → 输出需跑的测试组。
_TEST_GROUP_RULES: tuple[tuple[str, str], ...] = (
    ("tests/docs/", "docs-suite"),
    ("tests/unit/", "unit-suite"),
    ("scripts/check_docs", "docs-gate"),
    ("scripts/docs_", "docs-gate"),
    ("docs/", "docs-suite"),
)


@dataclass(frozen=True)
class ImpactList:
    """临时影响清单（来源第 9.5 节最小输出子集；每次检查临时计算，无持久化状态）。"""

    author_paths: tuple[str, ...]
    old_paths: tuple[str, ...]
    new_paths: tuple[str, ...]
    test_groups: tuple[str, ...]
    unresolved: tuple[str, ...]


def build_impact_list(changes: Iterable[tuple[str, str | None]]) -> ImpactList:
    """从变更对 (new_path, old_path | None) 构建临时影响清单（L02 最小纯函数）。

    rename 以双列输入承载（与 scripts/classify_changed_paths.py 的
    filename/previous_filename 并集语义同源）：旧/新路径**都**参与作者源与测试组
    映射；未命中任何规则的路径显式落 unresolved，不静默丢弃（不完整影响清单显式列未核）。
    只服务本次检查的临时计算，不建永久关系库/manifest 数据库。
    """
    author: set[str] = set()
    old: set[str] = set()
    new: set[str] = set()
    groups: set[str] = set()
    unresolved: set[str] = set()
    for new_path, old_path in changes:
        new.add(new_path)
        if old_path is not None:
            old.add(old_path)
        for path in (new_path, old_path):
            if path is None:
                continue
            if path.startswith(_AUTHOR_PATH_PREFIXES):
                author.add(path)
            hit = next((g for p, g in _TEST_GROUP_RULES if path.startswith(p)), None)
            if hit is not None:
                groups.add(hit)
            elif not path.startswith(_AUTHOR_PATH_PREFIXES):
                unresolved.add(path)
    return ImpactList(
        author_paths=tuple(sorted(author)),
        old_paths=tuple(sorted(old)),
        new_paths=tuple(sorted(new)),
        test_groups=tuple(sorted(groups)),
        unresolved=tuple(sorted(unresolved)),
    )


def test_l02_rename_keeps_old_path_in_scope() -> None:
    """rename 影响清单保留旧/新路径并集：旧路径仍是消费者迁移对象与测试组来源。"""
    impact = build_impact_list([("docs/guides/new-name.md", "docs/guides/old-name.md")])
    assert impact.old_paths == ("docs/guides/old-name.md",)
    assert impact.new_paths == ("docs/guides/new-name.md",)
    assert impact.author_paths == ("docs/guides/new-name.md", "docs/guides/old-name.md"), (
        "rename 后旧路径必须保留在影响范围内（只查新路径不够）"
    )
    assert impact.test_groups == ("docs-suite",)
    assert impact.unresolved == ()


def test_l02_old_path_alone_drives_test_group() -> None:
    """负例证明：若实现只看新路径（rename 幽灵反模式），旧路径驱动的测试组会丢失。"""
    impact = build_impact_list([("untracked-new.md", "docs/guides/old.md")])
    assert impact.test_groups == ("docs-suite",), "旧路径必须独立参与测试组映射（新旧并集语义）"
    assert "untracked-new.md" in impact.unresolved, "未映射的新路径须显式列未核"


def test_l02_unresolved_listed_not_dropped() -> None:
    """不完整影响清单显式列未核：未知路径落 unresolved，不静默丢弃、不伪装已核。"""
    impact = build_impact_list([("assets/logo.png", None)])
    assert impact.unresolved == ("assets/logo.png",)
    assert impact.author_paths == ()
    assert impact.test_groups == ()


def test_l02_impact_list_is_pure_function() -> None:
    """临时影响清单为纯函数：同输入同输出、无隐藏可变状态（不建第二套关系库）。"""
    changes = [("docs/a.md", None), ("tests/docs/test_x.py", None), ("scripts/docs_markdown.py", None)]
    first = build_impact_list(changes)
    second = build_impact_list(changes)
    assert first == second
    assert first.test_groups == ("docs-gate", "docs-suite")


# ---------------------------------------------------------------------------
# L03：规范/示例矛盾（机器下限）——代码例不冒正文
# ---------------------------------------------------------------------------


def test_l03_code_example_does_not_smuggle_prose() -> None:
    """负例注入：把「与正文矛盾的语句」藏进围栏代码块 → 正文视图必须拒绝其为语义来源。

    若解析器把代码块当正文（违规实现），代码例文本会进入有效正文 → 断言失败。
    语义层（条件一致性/禁止例能否阻断）为人工核对项，见模块 docstring。
    """
    text = (
        "# 规范\n\n"
        "正文：金额比较必须先经单位换算。\n\n"
        "```text\n"
        "示例正文：金额比较无需换算（这是代码例，不得冒充规范正文）\n"
        "```\n"
    )
    effective = cdc._view_effective_text(cdc.parse_doc_view(text))
    assert "金额比较必须先经单位换算" in effective, "正文语义必须保留在有效正文中"
    assert "无需换算" not in effective, "代码例不得冒充正文（L03 机器下限）"


# ---------------------------------------------------------------------------
# L04：状态/证据夸大——部分完成不认证整体
# ---------------------------------------------------------------------------


def test_l04_partial_completion_not_certified_as_whole() -> None:
    """部分目标已写 + 部分失败的结果不得标记 ok（已写/已测/已验收是不同事实）。"""
    partial = dg.UpdateResult(
        written=("a.md",),
        unchanged=(),
        conflicts=(),
        failed=("b.md",),
        blocked=None,
        post_check_errors=(),
    )
    assert partial.ok is False, "部分完成不得认证整体完成（L04 聚合语义 fail-closed）"
    fully_ok = dg.UpdateResult(
        written=("a.md",),
        unchanged=(),
        conflicts=(),
        failed=(),
        blocked=None,
        post_check_errors=(),
    )
    assert fully_ok.ok is True, "全部分列成功时 ok 为真（正例不回退）"


# ---------------------------------------------------------------------------
# L05：草案历史混入现行——当前入口可达性下限
# ---------------------------------------------------------------------------


def test_l05_index_link_to_absent_target_fails_and_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """负例注入：当前入口（索引）链接指向缺失目标 → 死链失败归因 → 目标补齐后恢复。

    「草案/历史角色判定」为人工核对项（同名或日期新不能自行升效力），
    机器下限 = 当前入口指向的目标必须实际存在。
    """
    docs = tmp_path / "docs"
    docs.mkdir()
    readme = docs / "README.md"
    readme.write_text("# 索引\n\n- [indexed](./indexed.md)\n- [draft](./draft.md)\n", encoding="utf-8")
    indexed = docs / "indexed.md"
    indexed.write_text("# 已登记\n", encoding="utf-8")
    monkeypatch.setattr(cdc, "CHECKED_DOCS", [readme, indexed])

    errors = cdc.check_relative_dead_links()
    assert any("draft.md" in e for e in errors), "当前入口指向缺失目标必须失败（L05 可达性下限）"

    (docs / "draft.md").write_text("# 草案（角色未定，不升现行）\n", encoding="utf-8")
    assert cdc.check_relative_dead_links() == [], "目标出现后死链消除（注入-恢复闭环）"


# ---------------------------------------------------------------------------
# L06：多套解析误报漏检——所有 Markdown 消费共用单一语法视图
# ---------------------------------------------------------------------------


def test_l06_single_parsing_view_all_md_consumers() -> None:
    """静态源码层证据：docs_markdown 是唯一 markdown_it 实例化点，消费端不得自建解析。"""
    scripts_dir = ROOT / "scripts"
    for consumer in ("check_docs_consistency.py", "verify_versions.py", "docs_generated.py"):
        source = (scripts_dir / consumer).read_text(encoding="utf-8")
        assert "markdown_it" not in source, f"{consumer} 不得绕过 docs_markdown 直用 markdown_it（L06）"


def test_l06_parse_doc_view_delegates_shared_view() -> None:
    """行为层证据：cdc.parse_doc_view 与 docs_markdown.parse_text 对同一文本产出等价视图。"""
    text = "# 标题\n\n[真实链接](./real.md) 与 [引用式][ref]\n\n```markdown\n[示例]()\n```\n\n[ref]: ./real.md\n"
    cdc_view = cdc.parse_doc_view(text)
    dm_view = dm.parse_text(text)
    assert [link.target for link in cdc_view.links] == [link.target for link in dm_view.links], (
        "消费端委托共享视图（若未来自建解析将产生行为分叉，此断言红）"
    )
