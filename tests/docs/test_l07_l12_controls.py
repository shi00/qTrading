r"""D12：L07—L12 长期控制合同控制测试（documentation-maintenance 第 2 节）。

控制合同正本：docs/guides/documentation-maintenance.md 第 2 节 L01—L12。
本文件承载 L07—L12，每个控制点以「注入违规 → 检查失败 → 清除/恢复 → 恢复通过」
闭环证明机制有效（L10 为 tests/docs 内 AND 谓词参照实现 + 负例验证）：

- L07 生成吞错/越界/改正文：生成保护边界 = 生成块（块内手改失败、块外正文保真）；
- L08 文件发现独立/必需输入非空：新文件漏登记失败（注入-恢复闭环）；零提取不静默；
- L09 自动化覆盖越界：分层报告——任一层失败不得跳过其它层实跑、聚合不绿；
- L10 例外永续/授权误推：AND 谓词续效（expires_at 未到期 且 removal_trigger 未触发）；
- L11 散文守卫元控制：负例必须单变因归因；控制证据映射防控制测试被静默移除；
- L12 配置存在冒充执行：聚合 fail-closed——失败/冲突/阻塞/后检查错误不装绿。

人工核对项（机器不可判定，不虚报语义全覆盖）：
- L07：并发冲突的进程级真实性（测试以屏障注入模拟外部写者）；
- L09：语义评审层与真实运行层的证据（结构绿不免语义或运行层）；
- L10：removal_trigger 文本的满足判定由评审者显式传入，机器不判自由文本语义；
- L12：远端 job 强制（required status check）与真实 CI 平台行为；
- L10 正本缺口（如实登记）：生产 check_exceptions_yaml_consistency 仅校验
  expires_at/removal_trigger 二选一必填，未集成到期/触发续效判断；本文件
  _exception_still_in_effect 为 tests/docs 内参照实现，不冒充生产门禁。
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Mapping
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import check_docs_consistency as cdc  # noqa: E402
import docs_generated as dg  # noqa: E402

pytestmark = pytest.mark.docs_pure


# ---------------------------------------------------------------------------
# L07：生成吞错/越界/改正文——生成保护边界
# ---------------------------------------------------------------------------


def test_l07_hand_edit_inside_block_rejected_outside_allowed(
    generated_block_repo: tuple[Path, tuple[dg.BlockSpec, ...]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """保护边界 = 生成块：块内手改必须失败；块外作者正文允许手改且在更新中保真。"""
    root, registry = generated_block_repo
    monkeypatch.setattr(cdc, "ROOT", root)
    monkeypatch.setattr(dg, "BUILTIN_REGISTRY", registry)
    assert dg.run_update(root, registry).ok
    assert cdc.check_docs_generated() == [], "基线一致"

    target = root / "targets" / "derived.md"

    # 负例注入 1：手改生成块内（越界改正文）→ 失败并归因块身份
    text = target.read_text(encoding="utf-8")
    target.write_text(text.replace("- max_rows: 500", "- max_rows: 1"), encoding="utf-8")
    errors = cdc.check_docs_generated()
    assert errors and all("limits" in e for e in errors), f"生成块内手改必须失败（L07）: {errors}"

    # 注入 2：块外正文手改（保护边界外，不吞改正文）——与块内违规并存
    current = target.read_text(encoding="utf-8")
    target.write_text(current.replace("# 派生目标", "# 派生目标（手改标题）"), encoding="utf-8")

    # 恢复：run_update 只重建块内，块外正文保真
    recovered = dg.run_update(root, registry)
    assert recovered.ok and "targets/derived.md" in recovered.written
    restored = target.read_text(encoding="utf-8")
    assert "- max_rows: 500" in restored, "生成块从作者源恢复"
    assert "手改标题" in restored, "块外正文在更新中保真（不吞改正文）"
    assert cdc.check_docs_generated() == [], "恢复后块内一致、块外保留"


# ---------------------------------------------------------------------------
# L08：文件发现独立/必需输入非空
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=True,
    )


@pytest.fixture()
def git_doc_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """最小 git 仓库（与 test_index_gitignore 同模式）：docs/README.md 自引用 + 已登记文档。"""
    repo = tmp_path / "repo"
    docs = repo / "docs"
    docs.mkdir(parents=True)
    (docs / "README.md").write_text("# 索引\n\n- [indexed](./indexed.md)\n", encoding="utf-8")
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


def test_l08_unregistered_new_file_detected_then_recovered(git_doc_repo: Path) -> None:
    """负例注入：tracked 新文件漏登记 → 索引覆盖检查失败归因 → 登记后恢复通过。"""
    assert cdc.check_docs_index_completeness() == [], "基线（无注入）必须通过"

    (git_doc_repo / "docs" / "new-page.md").write_text("# 新页\n", encoding="utf-8")
    _git(git_doc_repo, "add", "-A")
    errors = cdc.check_docs_index_completeness()
    assert any("new-page.md" in e for e in errors), "新文件漏登记必须失败（L08 独立发现）"

    readme = git_doc_repo / "docs" / "README.md"
    readme.write_text("# 索引\n\n- [indexed](./indexed.md)\n- [new](./new-page.md)\n", encoding="utf-8")
    assert cdc.check_docs_index_completeness() == [], "登记后恢复（注入-恢复闭环）"


def test_l08_empty_extraction_rejected(tmp_path: Path) -> None:
    """零提取必须拒绝：作者源缺目标列表键时显式失败，不得静默当空集合装通过。"""
    (tmp_path / "empty.yml").write_text("other_key: []\n", encoding="utf-8")
    schema = dg.SourceSchema(
        path="empty.yml",
        fmt="yaml",
        item_key="limits",
        required_fields=frozenset({"id", "value"}),
        identity_field="id",
    )
    with pytest.raises(dg.SourceFormatError, match="缺 'limits' 列表"):
        dg.load_validated_source(tmp_path, schema)


# ---------------------------------------------------------------------------
# L09：自动化覆盖越界——分层报告
# ---------------------------------------------------------------------------


def test_l09_layers_all_run_and_aggregate_stays_red() -> None:
    """分层报告：一层失败聚合不绿，且失败层不得跳过其它层的实跑（各层独立认证）。"""
    executed: list[str] = []
    plan: list[tuple[frozenset[str], Callable[[], list[str]]]] = [
        (frozenset({"document"}), lambda: executed.append("d") or ["结构错误"]),
        (frozenset({"governance"}), lambda: executed.append("g") or []),
        (frozenset({"source"}), lambda: executed.append("s") or []),
    ]
    assert cdc.main([], plan=plan) == 1, "任一层失败聚合不得绿（结构绿不免其它层）"
    assert sorted(executed) == ["d", "g", "s"], "失败层不得跳过其它层的实跑（分层独立报告）"


# ---------------------------------------------------------------------------
# L10：例外永续/授权误推——AND 谓词续效（tests/docs 内参照实现）
# ---------------------------------------------------------------------------

_BASE_DATE = date(2027, 1, 1)


def _exception_still_in_effect(
    entry: Mapping[str, object],
    today: date,
    *,
    removal_trigger_met: bool | None,
) -> bool:
    """例外 AND 谓词续效的参照实现（正本第 8 节 ADR-0018 C15；最小实现）。

    仅在原范围及全部必要前提未变，且所有已声明退出条件均未触发时沿用：
    - 有 expires_at 须未到期（expires_at >= today 视为未到期，次日即过期）；
    - 有 removal_trigger 时由评审者显式判定是否满足（None = 未核 → 拒绝报有效）；
    - 同时声明时两者都须成立（OR 续效被拒绝）；两者皆缺 = 无界永续，直接拒绝。
    """
    expires_raw = entry.get("expires_at")
    if not isinstance(expires_raw, str) or not expires_raw:
        expires_raw = None
    trigger_raw = entry.get("removal_trigger")
    if not isinstance(trigger_raw, str) or not trigger_raw:
        trigger_raw = None
    if expires_raw is None and trigger_raw is None:
        raise ValueError("例外须声明 expires_at 或 removal_trigger（二选一必填，不得无界永续）")

    in_effect = True
    if expires_raw is not None:
        try:
            expires_on = date.fromisoformat(expires_raw)
        except ValueError as exc:
            raise ValueError(f"expires_at 非法日期: {expires_raw!r}") from exc
        if expires_on < today:
            in_effect = False
    if trigger_raw is not None:
        if removal_trigger_met is None:
            raise ValueError("removal_trigger 已声明但满足与否未核（未核批准不得报有效）")
        if removal_trigger_met:
            in_effect = False
    return in_effect


def test_l10_valid_when_all_premises_hold() -> None:
    """正例：未到期且移除条件未触发 → 沿用。"""
    assert _exception_still_in_effect({"expires_at": "2027-06-30"}, _BASE_DATE, removal_trigger_met=False)


def test_l10_trigger_only_valid_when_not_met() -> None:
    """正例：仅声明 removal_trigger 且未触发 → 沿用。"""
    entry = {"removal_trigger": "替代方案交付后移除"}
    assert _exception_still_in_effect(entry, _BASE_DATE, removal_trigger_met=False)


def test_l10_expired_exception_rejected() -> None:
    """负例：expires_at 已过 → 不自动续效。"""
    entry = {"expires_at": "2026-12-31"}
    assert not _exception_still_in_effect(entry, _BASE_DATE, removal_trigger_met=False)


def test_l10_removal_trigger_met_rejected() -> None:
    """负例：移除条件已触发 → 停止沿用。"""
    entry = {"removal_trigger": "替代方案已交付"}
    assert not _exception_still_in_effect(entry, _BASE_DATE, removal_trigger_met=True)


def test_l10_or_renewal_rejected_when_both_declared() -> None:
    """关键负例：同时声明时 AND 谓词——未到期但移除条件已触发，OR 续效语义会误判有效。"""
    entry = {"expires_at": "2027-06-30", "removal_trigger": "替代方案已交付"}
    assert not _exception_still_in_effect(entry, _BASE_DATE, removal_trigger_met=True), (
        "同时声明时两者都须成立，不能用 OR 续效"
    )


def test_l10_expiry_boundary_day() -> None:
    """到期日边界：expires_at == today 视为未到期；today 前一日即过期。"""
    same_day = {"expires_at": _BASE_DATE.isoformat()}
    assert _exception_still_in_effect(same_day, _BASE_DATE, removal_trigger_met=False), (
        "到期日当天仍未到期（合同口径：expires_at < today 即过期）"
    )
    day_before = {"expires_at": (_BASE_DATE - timedelta(days=1)).isoformat()}
    assert not _exception_still_in_effect(day_before, _BASE_DATE, removal_trigger_met=False)


def test_l10_missing_both_rejected() -> None:
    """负例：两者皆缺 = 无界永续，直接拒绝（与生产 checker 二选一必填一致）。"""
    with pytest.raises(ValueError, match="二选一必填"):
        _exception_still_in_effect({}, _BASE_DATE, removal_trigger_met=False)


def test_l10_invalid_expiry_rejected() -> None:
    """负例：expires_at 非法日期显式失败，不得静默当有效。"""
    entry = {"expires_at": "not-a-date"}
    with pytest.raises(ValueError, match="非法日期"):
        _exception_still_in_effect(entry, _BASE_DATE, removal_trigger_met=False)


def test_l10_unverified_approval_not_valid() -> None:
    """负例：removal_trigger 已声明但满足与否未核（None）→ 不得报有效（授权误推）。"""
    entry = {"removal_trigger": "替代方案已交付"}
    with pytest.raises(ValueError, match="未核"):
        _exception_still_in_effect(entry, _BASE_DATE, removal_trigger_met=None)


# ---------------------------------------------------------------------------
# L11：散文守卫元控制——单变因归因 + 控制证据映射
# ---------------------------------------------------------------------------


def test_l11_negative_attribution_single_cause(git_doc_repo: Path) -> None:
    """元控制：负例注入必须单变因——失败报错全部归因注入变量，排除偶然其它原因。"""
    assert cdc.check_docs_index_completeness() == [], "负例前提：干净输入下检查必须为绿"

    (git_doc_repo / "docs" / "only-cause.md").write_text("# 唯一变因\n", encoding="utf-8")
    _git(git_doc_repo, "add", "-A")
    errors = cdc.check_docs_index_completeness()
    assert errors, "注入单一违规后必须失败"
    assert all("only-cause.md" in e for e in errors), f"每条报错必须归因注入变量（单变因）: {errors}"

    (git_doc_repo / "docs" / "only-cause.md").unlink()
    _git(git_doc_repo, "add", "-A")
    assert cdc.check_docs_index_completeness() == [], "清除注入后恢复通过（机制有效性证据）"


# L01—L12 控制证据映射：每个控制点 → 承载其控制测试的 (文件, 测试函数)。
# 本映射由 test_l11_control_evidence_map_resolves 静态核对，防止控制测试被静默
# 移除而失去等价承接（LA16：移除旧检查须有等价承接）。
_CONTROL_EVIDENCE: dict[str, tuple[tuple[str, str], ...]] = {
    "L01": (
        ("test_l01_l06_controls.py", "test_l01_hand_edit_derived_block_fails_check_and_recovers"),
        ("test_l01_l06_controls.py", "test_l01_second_render_zero_diff_after_regen"),
    ),
    "L02": (
        ("test_l01_l06_controls.py", "test_l02_rename_keeps_old_path_in_scope"),
        ("test_l01_l06_controls.py", "test_l02_old_path_alone_drives_test_group"),
        ("test_enforcement_triggers.py", "test_ghost_registered_workflow_reported"),
    ),
    "L03": (
        ("test_l01_l06_controls.py", "test_l03_code_example_does_not_smuggle_prose"),
        ("test_parse.py", "test_fences_and_inline_code_no_false_link"),
    ),
    "L04": (
        ("test_l01_l06_controls.py", "test_l04_partial_completion_not_certified_as_whole"),
        ("test_cli_layering.py", "test_warning_only_exit_zero"),
        ("test_cli_layering.py", "test_error_exit_one"),
    ),
    "L05": (
        ("test_l01_l06_controls.py", "test_l05_index_link_to_absent_target_fails_and_recovers"),
        ("test_index_gitignore.py", "test_tracked_file_missing_from_index_still_fails"),
    ),
    "L06": (
        ("test_l01_l06_controls.py", "test_l06_single_parsing_view_all_md_consumers"),
        ("test_l01_l06_controls.py", "test_l06_parse_doc_view_delegates_shared_view"),
        ("test_verify_versions_migration.py", "test_code_block_and_inline_code_examples_not_reported"),
    ),
    "L07": (
        ("test_l07_l12_controls.py", "test_l07_hand_edit_inside_block_rejected_outside_allowed"),
        ("test_generate.py", "test_duplicate_block_rejected"),
        ("test_generate.py", "test_only_outside_registry_rejected"),
        ("test_concurrency.py", "test_barrier_edit_before_replace_detected"),
    ),
    "L08": (
        ("test_l07_l12_controls.py", "test_l08_unregistered_new_file_detected_then_recovered"),
        ("test_l07_l12_controls.py", "test_l08_empty_extraction_rejected"),
        ("test_required_inputs.py", "test_missing_required_input_fails_loudly"),
        ("test_index_gitignore.py", "test_tracked_file_missing_from_index_still_fails"),
    ),
    "L09": (
        ("test_l07_l12_controls.py", "test_l09_layers_all_run_and_aggregate_stays_red"),
        ("test_cli.py", "test_scope_failure_exits_1"),
    ),
    "L10": (
        ("test_l07_l12_controls.py", "test_l10_or_renewal_rejected_when_both_declared"),
        ("test_l07_l12_controls.py", "test_l10_expired_exception_rejected"),
        ("test_l07_l12_controls.py", "test_l10_unverified_approval_not_valid"),
    ),
    "L11": (
        ("test_l07_l12_controls.py", "test_l11_negative_attribution_single_cause"),
        ("test_l07_l12_controls.py", "test_l11_control_evidence_map_resolves"),
    ),
    "L12": (
        ("test_l07_l12_controls.py", "test_l12_partial_scope_failure_not_masked"),
        ("test_l07_l12_controls.py", "test_l12_update_result_aggregate_not_green"),
        ("test_enforcement_triggers.py", "test_disabled_evidence_alone_fails"),
        ("test_cli.py", "test_update_conflict_exit_1"),
    ),
}


def test_l11_control_evidence_map_resolves() -> None:
    """控制证据映射自检：L01—L12 每项至少一个测试函数真实存在（防控制被静默移除）。"""
    tests_dir = Path(__file__).resolve().parent
    assert set(_CONTROL_EVIDENCE) == {f"L{i:02d}" for i in range(1, 13)}, "映射表须覆盖 L01—L12 全部"
    for control, evidences in _CONTROL_EVIDENCE.items():
        assert evidences, f"{control} 无控制测试证据"
        for filename, test_name in evidences:
            source = (tests_dir / filename).read_text(encoding="utf-8")
            assert f"def {test_name}(" in source, f"{control}: {filename} 缺控制测试 {test_name}"


# ---------------------------------------------------------------------------
# L12：配置存在冒充执行——聚合 fail-closed
# ---------------------------------------------------------------------------


def test_l12_partial_scope_failure_not_masked() -> None:
    """聚合不绿：任一 scope 失败 → 退出 1，其它 scope 全部通过不掩盖（所需失败不绿）。"""
    plan: list[tuple[frozenset[str], Callable[[], list[str]]]] = [
        (frozenset({"document"}), lambda: []),
        (frozenset({"governance"}), lambda: []),
        (frozenset({"source"}), lambda: ["所需检查失败"]),
    ]
    assert cdc.main([], plan=plan) == 1, "部分失败聚合不得绿（配置存在不冒充执行通过）"


def test_l12_update_result_aggregate_not_green() -> None:
    """聚合语义：冲突/写失败/阻塞/后检查错误任一非空 → ok=False，不伪装支持。"""
    assert (
        dg.UpdateResult(
            written=("a.md",), unchanged=(), conflicts=("b.md",), failed=(), blocked=None, post_check_errors=()
        ).ok
        is False
    ), "冲突目标未写，聚合不得绿"
    assert (
        dg.UpdateResult(
            written=("a.md",), unchanged=(), conflicts=(), failed=("b.md",), blocked=None, post_check_errors=()
        ).ok
        is False
    ), "写失败如实分列，聚合不得绿"
    assert (
        dg.UpdateResult(
            written=(), unchanged=(), conflicts=(), failed=(), blocked="无法取得写入保护", post_check_errors=()
        ).ok
        is False
    ), "整体阻塞（探测缺失）记受阻，不伪装支持"
    assert (
        dg.UpdateResult(
            written=("a.md",),
            unchanged=(),
            conflicts=(),
            failed=(),
            blocked=None,
            post_check_errors=("更新后检查失败",),
        ).ok
        is False
    ), "更新后检查错误不装绿"
