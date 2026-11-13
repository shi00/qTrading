r"""D13：E01—E12 受控维护演习（documentation-maintenance 第 11 节 / LA17）。

演习合同（检视 §9.11 / 指南第 11 节）：E01—E12 必须在临时夹具运行**真实检查入口**
执行「基线绿 → 注入单变因负例 → 失败原因核对 → 恢复 → 重验绿」闭环，不得直接改
生产规则文件造反例。本文件即演习的固化形态：每个案例全部经 monkeypatch 指向临时
仓库，生产治理正本零触碰；报告（reviews/doc-gov/d13-evidence.md，不入库）记录
逐例真实命令、退出码与负例注入/恢复核验。

E09 远端部分只做本地只读静态核验（合成夹具参照实现 + 真实 workflow 只读断言），
不冒充 GitHub 远端强制状态。机器不可判定的语义层在各案例 docstring 如实声明
人工发现路径，不虚报自动覆盖。

各案例 → 真实检查入口映射：
- E01 → cdc.check_docs_index_completeness / cdc.check_canonical_topics_consistency /
         dg.run_update（docs/README.md 文件级目录块） / dg._docs_index_candidates
- E02 → dg.run_update + cdc.check_docs_generated（conftest generated_block_repo 夹具）
- E03 → git mv + cdc.check_docs_index_completeness + cdc.check_relative_dead_links
- E04 → dg.run_update（作者源缺失 / 提取集合为空 / 合法删除顺序）+ cdc.check_docs_generated
- E05 → verify_versions.check_security_supported_version（版本绑定证据机器下限）
- E06 → cdc.check_relative_dead_links + cdc.check_anchor_dead_links（docs_markdown 统一视图）
- E07 → dg.run_update（重复/嵌套/越界/中断/屏障注入/正文保真）
- E08 → cdc._check_enforcement_invariants（触发器感知）+ dg.run_update（active=False 槽位）
- E09 → 合成聚合参照实现 + 真实 .github/workflows/ci_cd.yml 只读静态核验
- E10 → cdc.check_exceptions_yaml_consistency + AND 续效谓词（L10 参照实现，单写复用）
- E11 → cdc._check_enforcement_invariants（声称自动化无证据不装绿）+ warning 分层
- E12 → docs_markdown 统一视图（允许例/禁止例/停止分支保全；代码例不冒正文）
"""

from __future__ import annotations

import dataclasses
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
_DOCS_DIR = Path(__file__).resolve().parent
for _p in (str(SCRIPTS_DIR), str(_DOCS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import check_docs_consistency as cdc  # noqa: E402
import docs_generated as dg  # noqa: E402
import verify_versions as vv  # noqa: E402
from fixtures import generator_fixtures as gf  # noqa: E402
from test_l07_l12_controls import _exception_still_in_effect  # noqa: E402

pytestmark = pytest.mark.docs_pure


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)


@pytest.fixture()
def git_doc_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """最小 git 仓库（与 L08 夹具同模式）：docs/README.md 索引 + 已登记文档，检查常量指向临时仓库。"""
    repo = tmp_path / "repo"
    docs = repo / "docs"
    docs.mkdir(parents=True)
    readme = docs / "README.md"
    readme.write_text("# 索引\n\n- [indexed](./indexed.md)\n", encoding="utf-8")
    (docs / "indexed.md").write_text("# 已登记\n", encoding="utf-8")
    (repo / "CONTRIBUTING.md").write_text(
        "# 贡献\n\n目录结构见 docs/README.md（无链接，避免目录级覆盖干扰）。\n", encoding="utf-8"
    )
    _git(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@local", "-c", "user.name=t", "commit", "-m", "init")
    monkeypatch.setattr(cdc, "ROOT", repo)
    monkeypatch.setattr(cdc, "DOCS_README_PATH", docs / "README.md")
    monkeypatch.setattr(cdc, "CONTRIBUTING_PATH", repo / "CONTRIBUTING.md")
    monkeypatch.setattr(cdc, "_GITIGNORED_ARTIFACT_DIRS", ())
    monkeypatch.setattr(cdc, "_LOCAL_ARTIFACT_DIRS", ())
    return repo


# ---------------------------------------------------------------------------
# E01：新正式文件发现 / 目录登记——正例可发现并入文件级目录；漏登记 / 双 canonical 拒绝
# ---------------------------------------------------------------------------


def test_e01_new_file_discovered_index_registered_then_recovered(
    git_doc_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """正例：新正式文件经独立发现进入文件级目录（run_update 渲染索引块）→ 检查绿；
    负例（单变因）：tracked 新文件漏目录登记 → 失败归因 → 登记后恢复。"""
    readme = git_doc_repo / "docs" / "README.md"
    readme.write_text(
        "# 索引\n\n- [indexed](./indexed.md)\n\n<!-- generated:e01-file-index -->\n<!-- /generated -->\n",
        encoding="utf-8",
    )
    spec = dg.BlockSpec(
        block_id="e01-file-index",
        target="docs/README.md",
        sources=(),
        render=dg._render_docs_readme_file_index,
        block_format="markdown",
        active=True,
    )
    assert dg.run_update(git_doc_repo, (spec,)).ok, "基线索引块生成必须成功"
    assert cdc.check_docs_index_completeness() == [], "基线（无注入）必须通过"

    # 正例：新正式文件 tracked 后，先被独立发现（漏登记即失败），再经生成进入文件级目录
    (git_doc_repo / "docs" / "new-page.md").write_text("# 新页\n", encoding="utf-8")
    _git(git_doc_repo, "add", "-A")
    assert "docs/new-page.md" in {
        p.relative_to(git_doc_repo).as_posix() for p in dg._docs_index_candidates(git_doc_repo)
    }, "文件级目录作者源候选集必须含新文件（独立发现，不依赖目录声明）"
    detected = cdc.check_docs_index_completeness()
    assert any("new-page.md" in e for e in detected), f"漏目录登记必须失败归因新文件: {detected}"
    assert dg.run_update(git_doc_repo, (spec,)).ok
    assert "./new-page.md" in readme.read_text(encoding="utf-8"), "登记后新文件须进入文件级目录块"
    assert cdc.check_docs_index_completeness() == [], "登记后恢复通过（注入-恢复闭环）"


def test_e01_draft_role_not_promoted_into_current_closure(git_doc_repo: Path) -> None:
    """草案角色不误升现行效力（机器下限）：草案留在 docs/ 目录之外（仓库根）→
    不进文件级目录候选集，也不进现行必读闭包；角色语义判定为人工核对项。"""
    (git_doc_repo / "draft-drafts-must-not-be-indexed.md").write_text("# 草案\n", encoding="utf-8")
    _git(git_doc_repo, "add", "-A")
    candidates = {p.relative_to(git_doc_repo).as_posix() for p in dg._docs_index_candidates(git_doc_repo)}
    assert all(c.startswith("docs/") for c in candidates), "文件级目录候选集不得收录 docs/ 之外的草案（不升现行）"
    assert cdc.check_docs_index_completeness() == [], "docs/ 外的草案不得触发现行索引检查（不误扩闭包）"


def test_e01_duplicate_canonical_author_conflict_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """负例：两个 canonical 作者冲突（同一 topic 身份登记两个 canonical 条目）→ 失败归因；去重后恢复。"""
    yml = tmp_path / "canonical-topics.yml"
    yml.write_text(
        "topics:\n"
        "  - id: dao\n    title: A\n    canonical: docs/patterns/dao-pattern.md\n"
        "  - id: dao\n    title: B\n    canonical: docs/patterns/dao-pattern.md\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cdc, "CANONICAL_TOPICS_YAML_PATH", yml)
    errors = cdc.check_canonical_topics_consistency()
    assert any("id 重复" in e for e in errors), f"双 canonical 作者冲突必须拒绝: {errors}"

    yml.write_text(
        "topics:\n  - id: dao\n    title: A\n    canonical: docs/patterns/dao-pattern.md\n", encoding="utf-8"
    )
    assert cdc.check_canonical_topics_consistency() == [], "唯一 canonical 作者后恢复（注入-恢复闭环）"


# ---------------------------------------------------------------------------
# E02：合成作者源单写——只改一次源派生一致；只改副本 / 无作者关系不认证
# ---------------------------------------------------------------------------


def test_e02_author_source_single_edit_propagates_with_zero_diff(
    generated_block_repo: tuple[Path, tuple[dg.BlockSpec, ...]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """作者源改一次（500→300）→ run_update 派生一致 → check 绿 → 二次生成零差异。"""
    root, registry = generated_block_repo
    monkeypatch.setattr(cdc, "ROOT", root)
    monkeypatch.setattr(dg, "BUILTIN_REGISTRY", registry)

    source = root / "sources" / "limits.yml"
    source.write_text("limits:\n  - id: max_rows\n    value: 300\n", encoding="utf-8")
    assert dg.run_update(root, registry).ok
    assert "- max_rows: 300" in (root / "targets" / "derived.md").read_text(encoding="utf-8")
    assert cdc.check_docs_generated() == [], "只改一次源后派生必须一致"
    second = dg.run_update(root, registry)
    assert second.ok and second.unchanged == ("targets/derived.md",), "二次生成必须零差异"


def test_e02_copy_edit_rejected_and_attribution_points_at_block(
    generated_block_repo: tuple[Path, tuple[dg.BlockSpec, ...]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """负例：只改一处 UX 副本（修改派生不改源）→ 失败归因块身份 → 从作者源恢复。"""
    root, registry = generated_block_repo
    monkeypatch.setattr(cdc, "ROOT", root)
    monkeypatch.setattr(dg, "BUILTIN_REGISTRY", registry)
    assert dg.run_update(root, registry).ok

    target = root / "targets" / "derived.md"
    target.write_text(
        target.read_text(encoding="utf-8").replace("- max_rows: 500", "- max_rows: 999"), encoding="utf-8"
    )
    errors = cdc.check_docs_generated()
    assert errors and all("limits" in e for e in errors), f"只改副本必须失败且归因块身份: {errors}"
    assert dg.run_update(root, registry).ok
    assert cdc.check_docs_generated() == [], "从作者源恢复后重新一致"


def test_e02_consistent_copies_without_author_source_rejected(
    generated_block_repo: tuple[Path, tuple[dg.BlockSpec, ...]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """负例：两副本一致但无作者关系——作者源删除后，即便派生副本内容与上次渲染逐字一致，
    检查入口必须显式失败（不认证无来源值）。作者源值本身的语义正确性（两边一起改错
    不能以相等认证）为人工核对项，机器不判业务语义。"""
    root, registry = generated_block_repo
    monkeypatch.setattr(cdc, "ROOT", root)
    monkeypatch.setattr(dg, "BUILTIN_REGISTRY", registry)
    assert dg.run_update(root, registry).ok
    consistent_copy = (root / "targets" / "derived.md").read_text(encoding="utf-8")

    (root / "sources" / "limits.yml").unlink()
    assert (root / "targets" / "derived.md").read_text(encoding="utf-8") == consistent_copy, "前提：副本与历史渲染一致"
    with pytest.raises(dg.SourceMissingError, match="作者源缺失"):
        dg.run_update(root, registry)
    # check_docs_generated 的缺源形态 = fail-closed 错误列表（异常在入口转报错，不静默装通过）
    errors = cdc.check_docs_generated()
    assert errors and any("作者源缺失" in e for e in errors), f"无作者源关系不得认证: {errors}"


# ---------------------------------------------------------------------------
# E03：rename——原身份迁移，旧/新路径与消费者全迁；遗留旧深链明确失败
# ---------------------------------------------------------------------------


def test_e03_rename_old_deeplink_left_behind_fails_then_migrates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rename 后旧/新路径并集检查：索引登记与消费者深链遗留旧路径 → 双检查失败归因；
    全迁（索引 + 消费者）后恢复。身份延续（不另造新 R/topic）为人工核对项（影响清单见 L02）。"""
    repo = tmp_path / "repo"
    docs = repo / "docs"
    docs.mkdir(parents=True)
    readme = docs / "README.md"
    readme.write_text("# 索引\n\n- [old](./old-name.md)\n- [consumer](./consumer.md)\n", encoding="utf-8")
    (docs / "old-name.md").write_text("# 旧名\n", encoding="utf-8")
    (docs / "consumer.md").write_text("# 消费者\n\n[旧文件](./old-name.md)\n", encoding="utf-8")
    (repo / "CONTRIBUTING.md").write_text("# 贡献\n\n目录见 docs/README.md。\n", encoding="utf-8")
    _git(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@local", "-c", "user.name=t", "commit", "-m", "init")
    monkeypatch.setattr(cdc, "ROOT", repo)
    monkeypatch.setattr(cdc, "DOCS_README_PATH", docs / "README.md")
    monkeypatch.setattr(cdc, "CONTRIBUTING_PATH", repo / "CONTRIBUTING.md")
    monkeypatch.setattr(cdc, "_GITIGNORED_ARTIFACT_DIRS", ())
    monkeypatch.setattr(cdc, "_LOCAL_ARTIFACT_DIRS", ())
    monkeypatch.setattr(cdc, "CHECKED_DOCS", [readme, docs / "consumer.md"])
    assert cdc.check_docs_index_completeness() == [] and cdc.check_relative_dead_links() == [], "基线绿"

    _git(repo, "mv", "docs/old-name.md", "docs/new-name.md")  # 原身份 rename，消费者未迁
    index_errors = cdc.check_docs_index_completeness()
    link_errors = cdc.check_relative_dead_links()
    assert any("new-name.md" in e for e in index_errors), f"索引只检旧路径须报新文件未登记: {index_errors}"
    assert any("old-name.md" in e for e in link_errors), f"遗留旧深链必须明确失败: {link_errors}"

    readme.write_text("# 索引\n\n- [new](./new-name.md)\n- [consumer](./consumer.md)\n", encoding="utf-8")
    (docs / "consumer.md").write_text("# 消费者\n\n[新文件](./new-name.md)\n", encoding="utf-8")
    assert cdc.check_docs_index_completeness() == [] and cdc.check_relative_dead_links() == [], "全迁后恢复"


# ---------------------------------------------------------------------------
# E04：先迁消费者再删旧规范——删必需源 / 提取集合为空直接缺件失败；合法顺序可过
# ---------------------------------------------------------------------------


def test_e04_delete_required_source_fails_loudly_then_recovers(
    generated_block_repo: tuple[Path, tuple[dg.BlockSpec, ...]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """负例：删必需作者源 → 提取集合为空 → 显式缺件失败（异常传播，非零语义），不靠其它检查偶然兜底；恢复后绿。"""
    root, registry = generated_block_repo
    monkeypatch.setattr(cdc, "ROOT", root)
    monkeypatch.setattr(dg, "BUILTIN_REGISTRY", registry)
    assert dg.run_update(root, registry).ok and cdc.check_docs_generated() == [], "基线绿"
    backup = (root / "sources" / "limits.yml").read_text(encoding="utf-8")

    (root / "sources" / "limits.yml").unlink()
    with pytest.raises(dg.SourceMissingError, match="作者源缺失"):
        dg.run_update(root, registry)
    errors = cdc.check_docs_generated()
    assert errors and any("作者源缺失" in e for e in errors), f"缺件必须显式失败（错误列表形态）: {errors}"

    (root / "sources" / "limits.yml").write_text(backup, encoding="utf-8")
    assert dg.run_update(root, registry).ok and cdc.check_docs_generated() == [], "恢复后重验绿"


def test_e04_empty_extraction_set_rejected(
    generated_block_repo: tuple[Path, tuple[dg.BlockSpec, ...]],
) -> None:
    """负例：作者源缺目标列表键（提取集合为空）→ run_update 显式拒绝，不静默当空集合装通过。"""
    root, registry = generated_block_repo
    (root / "sources" / "limits.yml").write_text("other_key: []\n", encoding="utf-8")
    with pytest.raises(dg.SourceFormatError, match="缺 'limits' 列表"):
        dg.run_update(root, registry)


def test_e04_migrate_consumers_then_delete_source_passes(
    generated_block_repo: tuple[Path, tuple[dg.BlockSpec, ...]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """正例：先迁消费者（从注册表摘除块登记）再删旧规范（作者源）→ 更新与检查仍绿（合法删除顺序）。"""
    root, registry = generated_block_repo
    monkeypatch.setattr(cdc, "ROOT", root)
    monkeypatch.setattr(dg, "BUILTIN_REGISTRY", registry)
    assert dg.run_update(root, registry).ok

    assert dg.run_update(root, ()).ok, "消费者已迁（登记摘除）后空注册表更新必须成功"
    (root / "sources" / "limits.yml").unlink()
    # 登记摘除 = BUILTIN 注册表不再引用该源（模拟"先迁消费者"完成后的登记状态）
    monkeypatch.setattr(dg, "BUILTIN_REGISTRY", ())
    assert cdc.check_docs_generated() == [], "登记摘除后删源不再缺件（先迁消费者再删旧规范）"


# ---------------------------------------------------------------------------
# E05：API 证据版本——旧版 PASS 不认证当前版；配准确证据后恢复
# ---------------------------------------------------------------------------


def test_e05_old_version_evidence_not_valid_for_current(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """版本绑定证据机器下限：supported 表只登记旧版本 → 对旧版判定成立、对当前版必须失败
    （标缺失，不写当前已验证）；补当前版本准确证据后恢复。API 证据（核验快照）的版本续效
    语义层人工核对（无专用续效检查器，报告如实登记）。"""
    security = tmp_path / "SECURITY.md"
    security.write_text("| 版本 | 支持 |\n|---|---|\n| 1.9.x | :white_check_mark: |\n", encoding="utf-8")
    monkeypatch.setattr(vv, "SECURITY_PATH", security)

    assert vv.check_security_supported_version("1.9.5") == [], "旧版证据对其目标版本成立（旧版 PASS 本身合法）"
    errors = vv.check_security_supported_version("2.0.0")
    assert errors and "2.0.x" in errors[0], f"旧版 PASS 不得认证当前版: {errors}"

    security.write_text(
        "| 版本 | 支持 |\n|---|---|\n| 1.9.x | :white_check_mark: |\n| 2.0.x | :white_check_mark: |\n",
        encoding="utf-8",
    )
    assert vv.check_security_supported_version("2.0.0") == [], "配当前版准确证据后恢复（注入-恢复闭环）"


# ---------------------------------------------------------------------------
# E06：解析器合法性——合法语法不误报；真死链 / 示例标题补必需正文失败
# ---------------------------------------------------------------------------


def _checked_doc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> Path:
    real = tmp_path / "real.md"
    real.write_text("# 真实目标\n", encoding="utf-8")
    doc = tmp_path / "spec.md"
    doc.write_text(text, encoding="utf-8")
    monkeypatch.setattr(cdc, "CHECKED_DOCS", [doc, real])
    return doc


_LEGAL_SYNTAX_DOC = (
    "# 规范标题\n\n"
    "## Section\n\n"
    "[引用式][ref1] 与 [同文件锚](#section) 与 [HTML 锚](#custom-html-anchor) 与 [跨文件](./real.md#真实目标)。\n\n"
    "## Section\n\n"
    "重复标题：slugger 自动追加序号，合法不误报。\n\n"
    '<a id="custom-html-anchor"></a>HTML id 锚点。\n\n'
    "```text\n[伪链接](./missing-fence.md) 与 [引用式][ghost] 不得误报\n```\n\n"
    "~~~text\n~~~ 围栏形态的 [伪链接](./missing-tilde.md) 同样排除\n~~~\n\n"
    "行内代码 `[伪链接](./missing-inline.md)` 与 `[ref1]: ./real.md` 均不误报。\n\n"
    "[ref1]: ./real.md\n"
)


def test_e06_legal_syntax_no_false_positive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """正例：reference 链接 / HTML id / 重复标题 / 两类围栏 / 行内代码 / 跨文件锚点 →
    死链与锚点两检查均不误报（所有 Markdown 消费共用 docs_markdown 单一语法视图）。"""
    _checked_doc(tmp_path, monkeypatch, _LEGAL_SYNTAX_DOC)
    assert cdc.check_relative_dead_links() == [], "合法语法不得误报死链"
    assert cdc.check_anchor_dead_links() == [], "合法锚点语法不得误报"


def test_e06_true_dead_link_fails_with_attribution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """负例：真死链 + 未定义引用 → 失败归因目标 → 修正后恢复。"""
    doc = _checked_doc(
        tmp_path,
        monkeypatch,
        _LEGAL_SYNTAX_DOC + "\n[真死链](./truly-missing.md) 与 [未定义引用][ghost-ref]。\n",
    )
    errors = cdc.check_relative_dead_links()
    assert any("truly-missing.md" in e for e in errors), f"真死链必须失败归因: {errors}"
    assert any("ghost-ref" in e for e in errors), f"未定义引用必须显式失败: {errors}"
    doc.write_text(_LEGAL_SYNTAX_DOC + "\n[修正](./real.md) 与 [已定义][ref1]。\n", encoding="utf-8")
    assert cdc.check_relative_dead_links() == [], "修正后恢复"


def test_e06_example_heading_cannot_supply_required_structure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """负例：必需正文标题缺失、以代码块内示例标题冒充 → 锚点死链失败（代码例不补正文结构）；
    写入真标题后恢复。"""
    doc = _checked_doc(
        tmp_path,
        monkeypatch,
        "# 规范\n\n```text\n## 交付物\n```\n\n[交付物说明](#交付物)\n",
    )
    errors = cdc.check_anchor_dead_links()
    assert any("交付物" in e for e in errors), f"示例标题补必需正文必须失败: {errors}"
    doc.write_text("# 规范\n\n## 交付物\n\n[交付物说明](#交付物)\n", encoding="utf-8")
    assert cdc.check_anchor_dead_links() == [], "真标题写入后恢复"


# ---------------------------------------------------------------------------
# E07：受控块生成保护——重复/嵌套/越界/中断/窗口内外部编辑；正文保真与用户新稿保留
# ---------------------------------------------------------------------------


def test_e07_duplicate_nested_and_out_of_bounds_rejected(tmp_path: Path) -> None:
    """负例：第二重复块 / 嵌套块 / 越界目标 → 显式异常（非零语义），不静默取第一处或写越界。"""
    root = gf.build_demo_tree(tmp_path)
    target = root / gf.DEMO_TARGET_REL

    target.write_text(
        gf.DEMO_TARGET.replace(
            "<!-- /generated -->", "A\n<!-- /generated -->\n<!-- generated:demo-block -->\nB\n<!-- /generated -->"
        ),
        encoding="utf-8",
    )
    with pytest.raises(dg.DuplicateBlockError, match="块身份重复"):
        dg.run_update(root, gf.make_demo_registry())

    target.write_text(
        gf.DEMO_TARGET.replace(
            "<!-- /generated -->", "<!-- generated:inner -->\n<!-- /generated -->\n<!-- /generated -->"
        ),
        encoding="utf-8",
    )
    with pytest.raises(dg.NestedBlockError, match="嵌套"):
        dg.run_update(root, gf.make_demo_registry())

    import dataclasses as _dc

    out_of_bounds = _dc.replace(gf.make_demo_registry()[0], target="../escaped.md")
    with pytest.raises(dg.PathEscapeError, match="越界"):
        dg.run_update(root, (out_of_bounds,))


def test_e07_write_interruption_lists_written_and_unwritten(tmp_path: Path) -> None:
    """负例：写入中断（屏障注入磁盘错误）→ 非零退出语义，已写/未写如实分列，不伪装整体成功。"""
    root = tmp_path
    (root / "sources").mkdir()
    (root / gf.DEMO_SOURCE_REL).write_text(gf.VALID_SOURCE_YAML, encoding="utf-8")
    (root / "a.md").write_text(gf.DEMO_TARGET.replace("demo-block", "demo-block-a"), encoding="utf-8")
    (root / "b.md").write_text(gf.DEMO_TARGET.replace("demo-block", "demo-block-b"), encoding="utf-8")
    spec_a = dataclasses.replace(gf.make_demo_registry()[0], target="a.md", block_id="demo-block-a")
    spec_b = dataclasses.replace(gf.make_demo_registry()[0], target="b.md", block_id="demo-block-b")

    def barrier(phase: str, target_rel: str | None) -> None:
        if phase == "before_replace" and target_rel == "a.md":
            raise OSError("注入的写入中断（disk full）")

    result = dg.run_update(root, (spec_a, spec_b), barrier=barrier)
    assert result.ok is False, "写入中断不得伪装整体成功"
    assert result.failed == ("a.md",) and result.written == ("b.md",), "已写/未写必须如实分列"


def test_e07_edit_in_protection_window_conflicts_and_keeps_user_draft(tmp_path: Path) -> None:
    """核对后、替换前注入外部编辑 → 检出冲突、原位未覆盖、用户新稿窗口保留、无旧快照覆盖。"""
    root = gf.build_demo_tree(tmp_path)
    target = root / gf.DEMO_TARGET_REL
    before = target.read_text(encoding="utf-8")

    def barrier(phase: str, target_rel: str | None) -> None:
        if phase == "before_replace" and target_rel == gf.DEMO_TARGET_REL:
            target.write_text("# 用户新稿\n", encoding="utf-8")

    result = dg.run_update(root, gf.make_demo_registry(), barrier=barrier)
    assert result.ok is False, "窗口内外部编辑必须检出冲突"
    assert result.conflicts == (gf.DEMO_TARGET_REL,) and result.written == ()
    assert "- R1：演示红线一" not in target.read_text(encoding="utf-8"), "旧快照产物不得落盘覆盖"
    assert target.read_text(encoding="utf-8") == before or "用户新稿" in target.read_text(encoding="utf-8"), (
        "原稿或用户新稿必居其一，不得被旧快照产物覆盖"
    )


def test_e07_hand_edited_prose_outside_block_preserved(
    generated_block_repo: tuple[Path, tuple[dg.BlockSpec, ...]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """保护边界 = 生成块：作者/手写正文（块外）在更新中保真，受阻写者的块外编辑不被旧快照回滚。"""
    root, registry = generated_block_repo
    monkeypatch.setattr(cdc, "ROOT", root)
    monkeypatch.setattr(dg, "BUILTIN_REGISTRY", registry)
    assert dg.run_update(root, registry).ok

    target = root / "targets" / "derived.md"
    target.write_text(
        target.read_text(encoding="utf-8").replace("# 派生目标", "# 派生目标（用户新稿）"), encoding="utf-8"
    )
    recovered = dg.run_update(root, registry)
    assert recovered.ok, f"块外手改不触发块内差异，更新须成功（unchanged 或 written 均可）: {recovered}"
    text = target.read_text(encoding="utf-8")
    assert "用户新稿" in text, "块外用户新稿必须保留（无旧快照覆盖或假回滚）"
    assert "- max_rows: 500" in text, "块内从作者源恢复"
    assert cdc.check_docs_generated() == []


# ---------------------------------------------------------------------------
# E08：活示例选择——活证据被选择；有意空 stub 允许；不被选择 / 零提取 / 假迁移标记拒绝
# ---------------------------------------------------------------------------


def _redline() -> dict[str, object]:
    return {
        "id": "R2",
        "enforcement": "CI-test（部分覆盖）",
        "automation_coverage": "partial",
        "human_review_required": True,
    }


def _drill_env(workflow_content: str) -> cdc.EnforcementEnvironment:
    return cdc.EnforcementEnvironment(
        precommit_content="",
        workflow_contents=(workflow_content,),
        pyproject_content="",
        check_redlines_script_exists=False,
        gitleaks_config_exists=False,
    )


def test_e08_live_example_selected(tmp_path: Path) -> None:
    """正例：真实可达的 pytest run 块构成活证据 → 不报 N5（活保护不空转）。"""
    content = "jobs:\n  docs-suite:\n    steps:\n      - run: python -m pytest tests/docs -q\n"
    errors = cdc._check_enforcement_invariants([_redline()], _drill_env(content))
    assert not any("N5" in e for e in errors), f"活示例被选择时不得误报: {errors}"


def test_e08_inactive_stub_slot_allowed(tmp_path: Path) -> None:
    """正例：有意空 stub（active=False 槽位）→ 不参与渲染、不误阻更新（stub 不误阻）。"""
    root = gf.build_demo_tree(tmp_path)
    stub = dataclasses.replace(
        gf.make_demo_registry()[0], block_id="stub-slot", target="stub.md", render=None, active=False
    )
    result = dg.run_update(root, (*gf.make_demo_registry(), stub))
    assert result.ok and result.written == (gf.DEMO_TARGET_REL,), f"空 stub 槽位不得误阻: {result}"
    assert not (root / "stub.md").exists(), "空 stub 不得被伪造生成"


def test_e08_unselected_zero_extraction_and_fake_marker_rejected() -> None:
    """负例：测试不被选择（--collect-only）/ 零提取（无 pytest 块）/ 假迁移标记 + 禁用包裹 → N5 拒绝。"""
    redline = _redline()
    collect_only = "jobs:\n  t:\n    steps:\n      - run: pytest tests/docs --collect-only\n"
    assert any("N5" in e for e in cdc._check_enforcement_invariants([redline], _drill_env(collect_only)))

    empty = ""
    assert any("N5" in e for e in cdc._check_enforcement_invariants([redline], _drill_env(empty))), (
        "零提取（无任何 pytest 证据）不得装通过"
    )

    fake_marker = (
        "# migrated-to-docs-suite: 本组已迁移（假迁移标记，实际被禁用）\n"
        "jobs:\n  docs-suite:\n    if: false\n    steps:\n      - run: pytest tests/docs -q\n"
    )
    errors = cdc._check_enforcement_invariants([redline], _drill_env(fake_marker))
    assert any("N5" in e for e in errors), f"假迁移标记绕不过触发器感知: {errors}"


# ---------------------------------------------------------------------------
# E09：CI 聚合（本地只读模拟，不冒充 GitHub 远端强制状态）
# ---------------------------------------------------------------------------

_LEGAL_AGGREGATE_OUTCOMES = frozenset({"success", "condition-skip"})


def _aggregate_gate_holds(needs_outcomes: dict[str, str], if_expr: str) -> bool:
    """聚合 fail-closed 参照实现（tests/docs 内最小近似，GitHub Actions 默认语义）。

    - needs 引用的 job 缺 outcome（探测缺失）→ 无法认证 → 不绿；
    - 显式条件 skip（condition-skip，如 changes 过滤）为合法跳过 → 不阻断聚合；
    - 所需 job failure / cancelled / 依赖级联 skipped → 聚合不绿；
    - if 表达式含 always() 且无 failure()/cancelled() 收敛 → 失败/取消也继续，判装绿违规。
    """
    if any(outcome not in _LEGAL_AGGREGATE_OUTCOMES for outcome in needs_outcomes.values()):
        return False
    lowered = if_expr.lower()
    return not ("always()" in lowered and "failure()" not in lowered and "cancelled()" not in lowered)


def test_e09_aggregate_gate_only_green_when_required_jobs_pass() -> None:
    """合成夹具：所需成功 + 显式条件 skip → 聚合可绿；失败 / 取消 / 探测缺失 / always() 装绿 → 不绿。"""
    ok_expr = "needs.changes.outputs.code == 'true'"
    assert _aggregate_gate_holds({"ci-checks": "success", "lint-fast": "condition-skip"}, ok_expr), (
        "所需成功 + 显式条件合法 skip = 正例"
    )
    assert not _aggregate_gate_holds({"ci-checks": "failure", "lint-fast": "success"}, ok_expr), "所需失败不得绿"
    assert not _aggregate_gate_holds({"ci-checks": "cancelled", "lint-fast": "success"}, ok_expr), "所需取消不得绿"
    assert not _aggregate_gate_holds({"ci-checks": "skipped", "lint-fast": "success"}, ok_expr), "依赖级联 skip 不得绿"
    assert not _aggregate_gate_holds({"ci-checks": "missing"}, ok_expr), "探测缺失不得绿"
    assert not _aggregate_gate_holds({"ci-checks": "success"}, "always()"), "always() 无收敛表达式 = 装绿路径，拒绝"


def test_e09_real_workflow_aggregation_fail_closed_readonly() -> None:
    """真实 .github/workflows/ci_cd.yml 只读静态核验（不修改、不冒充远端强制状态）：
    发行聚合 job 的 needs 覆盖全部 CI 门 job，且 if 表达式未引入 always() 装绿路径
    （needs 中任一失败/取消时聚合 job 不运行，GitHub 默认语义 fail-closed）。"""
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci_cd.yml").read_text(encoding="utf-8"))
    build = workflow["jobs"]["build-windows"]
    needs = set(build["needs"]) if isinstance(build["needs"], list) else {build["needs"]}
    required_gates = {
        "ci-checks",
        "ci-checks-static",
        "ci-checks-db",
        "ci-checks-windows",
        "e2e-tests-windows",
        "embedded-tests",
    }
    assert required_gates <= needs, f"发行聚合 needs 必须覆盖全部 CI 门 job: 缺 {sorted(required_gates - needs)}"
    if_expr = build.get("if") or ""
    assert "always()" not in if_expr, "聚合 job if 不得引入 always()（失败/取消也继续 = 装绿路径）"


# ---------------------------------------------------------------------------
# E10：例外谓词——原范围/前提成立有效；到期 / 范围已变 / 触发 / 无批准不自动续效
# ---------------------------------------------------------------------------


def _exceptions_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """临时例外注册表环境：exceptions.yml + redlines.yml（rule_id 存在性）+ pyproject（GATE 门降级）。"""
    repo = tmp_path / "gov"
    repo.mkdir()
    (repo / "exceptions.yml").write_text("exceptions: []\n", encoding="utf-8")
    (repo / "redlines.yml").write_text("redlines:\n  - id: R1\n    title: t\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname = 'drill'\n", encoding="utf-8")
    (repo / "present.md").write_text("# 存在\n", encoding="utf-8")
    monkeypatch.setattr(cdc, "ROOT", repo)
    monkeypatch.setattr(cdc, "EXCEPTIONS_YAML_PATH", repo / "exceptions.yml")
    monkeypatch.setattr(cdc, "REDLINES_YAML_PATH", repo / "redlines.yml")
    monkeypatch.setattr(cdc, "PYPROJECT_PATH", repo / "pyproject.toml")
    return repo


def _write_exception(path: Path, **overrides: str) -> None:
    entry: dict[str, str] = {
        "id": "EX-9001",
        "rule_id": "R1",
        "paths": "present.md",
        "reason": "演习",
        "owner": "架构维护者",
        "approved_by": "演习批准（合成）",
        "verification": "演习",
        "validity_criteria": "演习",
        "expires_at": "2099-01-01",
    }
    entry.update(overrides)
    lines = ["exceptions:"]
    for index, (key, value) in enumerate(entry.items()):
        if value:
            rendered = f"[{value}]" if key == "paths" else value  # paths 契约要求 list
            bullet = "  - " if index == 0 else "    "
            lines.append(f"{bullet}{key}: {rendered}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


_E10_TODAY = date(2026, 11, 13)


def test_e10_exception_valid_when_scope_and_premises_hold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """正例：范围内 + 必要前提成立（批准/范围/到期未至）→ 生产 checker 绿；AND 续效谓词沿用。"""
    repo = _exceptions_repo(tmp_path, monkeypatch)
    _write_exception(repo / "exceptions.yml")
    assert cdc.check_exceptions_yaml_consistency() == [], "合法例外不得误报"
    assert _exception_still_in_effect({"expires_at": "2099-01-01"}, _E10_TODAY, removal_trigger_met=False), (
        "范围内且前提成立 → 沿用"
    )


def test_e10_expired_scope_changed_and_unapproved_not_auto_renewed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """负例组：范围已变 / 无批准依据 / 到期 / 移除条件触发 / 未核 → 不自动续效或扩大。

    如实登记的正本缺口：生产 check_exceptions_yaml_consistency 未集成到期/触发续效
    判断（L10 缺口），到期负例下生产 checker 不报错——该断言即缺口证据；续效语义由
    L10 参照实现（与 tests/docs 单写复用）承担，不冒充生产门禁。
    """
    repo = _exceptions_repo(tmp_path, monkeypatch)
    yml = repo / "exceptions.yml"

    # 范围已变（paths 指向不存在路径）→ 生产 checker 拒绝
    _write_exception(yml, paths="vanished.md")
    assert any("路径不存在" in e for e in cdc.check_exceptions_yaml_consistency()), "范围已变必须拒绝"

    # 无批准依据（缺 approved_by 必填字段）→ 生产 checker 拒绝
    _write_exception(yml, approved_by="")
    assert any("缺少必填字段" in e for e in cdc.check_exceptions_yaml_consistency()), "无批准依据必须拒绝"

    # 到期：生产 checker 不判续效（缺口如实断言），AND 谓词拒绝
    _write_exception(yml, paths="present.md", expires_at="2020-01-01")
    assert cdc.check_exceptions_yaml_consistency() == [], "缺口证据：生产 checker 未集成到期判断（如实登记，不掩盖）"
    assert not _exception_still_in_effect({"expires_at": "2020-01-01"}, _E10_TODAY, removal_trigger_met=False), (
        "到期不自动续效"
    )

    # 恢复：未到期 + 规模不变 → 生产 checker 重验绿
    _write_exception(yml, paths="present.md", expires_at="2099-01-01")
    assert cdc.check_exceptions_yaml_consistency() == [], "恢复后重验绿"


def test_e10_removal_trigger_met_or_unverified_rejected(tmp_path: Path) -> None:
    """负例：移除条件已触发 / 满足与否未核 → AND 谓词拒绝（未核批准不得报有效）。"""
    assert not _exception_still_in_effect(
        {"removal_trigger": "替代方案已交付"}, _E10_TODAY, removal_trigger_met=True
    ), "移除条件已触发不续效"
    with pytest.raises(ValueError, match="未核"):
        _exception_still_in_effect({"removal_trigger": "替代方案已交付"}, _E10_TODAY, removal_trigger_met=None)


# ---------------------------------------------------------------------------
# E11：分层诚实——文档已采用 / 静态绿 ≠ 产品已测；声称自动化无证据不装绿
# ---------------------------------------------------------------------------


def test_e11_claimed_full_coverage_without_evidence_not_green() -> None:
    """负例：红线声称 automation_coverage: full 但执行环境无任何可达 pytest 证据 →
    不得以「文档已写 / 配置存在」关闭产品运行层（E11 不关真实剩余门的机器下限）。
    提示代替加密 / 安装代替 CUX / 报告代替 PG/发行属产品运行层，未核（无授权），报告单列。"""
    claimed_full = {
        "id": "R99",
        "enforcement": "CI-test（全量）",
        "automation_coverage": "full",
        "human_review_required": False,
    }
    errors = cdc._check_enforcement_invariants([claimed_full], _drill_env(""))
    assert errors, "声称 full 但零执行证据不得装绿（分层诚实机器下限）"

    live = "jobs:\n  t:\n    steps:\n      - run: python -m pytest tests/unit/ -q\n"
    assert not any("N5" in e for e in cdc._check_enforcement_invariants([claimed_full], _drill_env(live))), (
        "有真实可达证据后恢复（正例不回退）"
    )


def test_e11_warning_layering_does_not_certify_error_free(monkeypatch: pytest.MonkeyPatch) -> None:
    """负例：warning-only 输出与真实错误并存 → 退出 1（warning 分级不吞错误，某层退出 0 不代表下一层通过）。"""
    monkeypatch.setattr(cdc, "check_governance_id_glossary", lambda: (["E1: 所需检查失败"], ["W1: 仅建议警告"]))
    plan = [(frozenset({"governance"}), cdc._glossary_check_with_warnings)]
    assert cdc.main(argv=["--check", "--scope", "governance"], plan=plan) == 1, "warning 不把真实错误装绿"


# ---------------------------------------------------------------------------
# E12：条件规范一致性——允许例 / 禁止例 / 停止分支都保全；矛盾语义人工发现路径明确
# ---------------------------------------------------------------------------


def test_e12_conditional_spec_examples_and_exit_branches_preserved() -> None:
    """正例：有条件规范 + 允许例 + 禁止例 + 停止（失败出口）分支 → 全部保全在有效正文（不丢例）。"""
    text = (
        "# 重试规范\n\n"
        "任务失败后必须先校验水位线再重试。\n\n"
        "允许例：任务处于 pending 且水位线未变时可重试。\n\n"
        "禁止例：任务已处于 completed 时不得重试。\n\n"
        "停止分支：校验失败时中止并保留现场，不得继续执行。\n"
    )
    effective = cdc._view_effective_text(cdc.parse_doc_view(text))
    for fragment in ("允许例：任务处于 pending", "禁止例：任务已处于 completed", "停止分支：校验失败时中止并保留现场"):
        assert fragment in effective, f"正文例与失败出口必须保全: {fragment}"


def test_e12_contradiction_machine_limit_requires_semantic_review() -> None:
    """机器下限声明：正文禁掉同节允许例的语义矛盾机器不可判定——自动检查不误报也不判语义；
    代码例不冒正文（若维护者把允许例误写成代码例，解析视图如实分离，语义矛盾须人工评审发现）。"""
    contradictory = (
        "# 重试规范\n\n"
        "禁止例：任务已处于 completed 时不得重试。\n\n"
        "```text\n允许例：任务已处于 completed 时可重试（这是示例，不构成正文允许路径）\n```\n"
    )
    effective = cdc._view_effective_text(cdc.parse_doc_view(contradictory))
    assert "示例，不构成正文允许路径" not in effective, "代码例不得冒充正文（语义矛盾的载体被如实分离）"
    assert "不得重试" in effective, "正文规范语义保留"
