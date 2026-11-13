"""D4 CLI 契约测试（检视 §5.2 固定接口：--check/--scope/--update 与退出码）。

退出码合同：成功 0、验证/写入失败 1、参数误用 2；无参数等价 --check --scope all；
--update 不得与 --scope/--check 组合；scope 是固定责任映射，不得提供自由跳过失败的途径。
检查计划与更新入口经参数注入（生产路径默认真实实现），本文件不触碰仓库真实正文。
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

import docs_generated as dg
import check_docs_consistency as cdc

pytestmark = pytest.mark.docs_pure


def _fake_check(errors: list[str]) -> Callable[[], list[str]]:
    return lambda: list(errors)


def _plan_with_errors(
    document: list[str], governance: list[str], source: list[str]
) -> list[tuple[frozenset[str], Callable[[], list[str]]]]:
    """构造三个 scope 各挂一个假检查的计划（执行顺序无关紧要，仅验证过滤）。"""
    return [
        (frozenset({"document"}), _fake_check(document)),
        (frozenset({"governance"}), _fake_check(governance)),
        (frozenset({"source"}), _fake_check(source)),
    ]


def _ok_runner() -> dg.UpdateResult:
    return dg.UpdateResult(written=("a.md",), unchanged=(), conflicts=(), failed=(), blocked=None, post_check_errors=())


# ---------------------------------------------------------------------------
# 无参数 = --check --scope all
# ---------------------------------------------------------------------------


def test_no_args_runs_all_checks() -> None:
    """无参数等价 --check --scope all：三个 scope 的检查全部执行。"""
    executed: list[str] = []
    plan = [
        (frozenset({"document"}), lambda: executed.append("d") or []),
        (frozenset({"governance"}), lambda: executed.append("g") or []),
        (frozenset({"source"}), lambda: executed.append("s") or []),
    ]
    assert cdc.main([], plan=plan) == 0
    assert sorted(executed) == ["d", "g", "s"]


def test_no_args_with_failures_exits_1() -> None:
    """all 检查有错误 → 退出 1（不吞错装通过）。"""
    assert cdc.main([], plan=_plan_with_errors(["错误"], [], [])) == 1


def test_scope_flag_without_check_implies_check() -> None:
    """`--scope X` 单独给出等价 `--check --scope X`（固定接口的宽松形态，仍为合法）。"""
    executed: list[str] = []
    plan = [
        (frozenset({"document"}), lambda: executed.append("d") or []),
        (frozenset({"governance"}), lambda: executed.append("g") or []),
    ]
    assert cdc.main(["--scope", "document"], plan=plan) == 0
    assert executed == ["d"]


# ---------------------------------------------------------------------------
# scope 固定责任映射过滤
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scope,expected", [("document", ["d"]), ("governance", ["g"]), ("source", ["s"])])
def test_scope_filters_to_own_checks(scope: str, expected: list[str]) -> None:
    """--check --scope X 只执行该 scope 责任映射内的检查。"""
    executed: list[str] = []
    plan = [
        (frozenset({"document"}), lambda: executed.append("d") or []),
        (frozenset({"governance"}), lambda: executed.append("g") or []),
        (frozenset({"source"}), lambda: executed.append("s") or []),
    ]
    assert cdc.main(["--check", "--scope", scope], plan=plan) == 0
    assert executed == expected


def test_scope_failure_exits_1() -> None:
    """scope 内检查失败 → 退出 1（scope 过滤不是跳过失败的途径）。"""
    assert cdc.main(["--check", "--scope", "source"], plan=_plan_with_errors([], [], ["源码契约错误"])) == 1


def test_real_plan_covers_all_three_scopes() -> None:
    """真实计划（生产默认）必须覆盖 document/governance/source 三个 scope，且条目可调用。"""
    plan = cdc.build_scope_plan()
    labels = frozenset[str]().union(*(labels for labels, _ in plan))
    assert labels == {"document", "governance", "source"}
    assert all(callable(fn) for _, fn in plan)


# ---------------------------------------------------------------------------
# 参数误用 → 退出 2
# ---------------------------------------------------------------------------


def test_unknown_scope_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    """--scope 取值不在封闭枚举 → argparse 退出 2。"""
    with pytest.raises(SystemExit) as excinfo:
        cdc.main(["--check", "--scope", "bogus"])
    assert excinfo.value.code == 2
    assert "bogus" in capsys.readouterr().err


def test_update_with_scope_exit_2() -> None:
    """--update 不得与 --scope 组合 → 退出 2。"""
    with pytest.raises(SystemExit) as excinfo:
        cdc.main(["--update", "--scope", "all"])
    assert excinfo.value.code == 2


def test_update_with_check_exit_2() -> None:
    """--update 与 --check 组合 → 退出 2（update 隐含更新后 all 检查，无独立 check 语义）。"""
    with pytest.raises(SystemExit) as excinfo:
        cdc.main(["--update", "--check"])
    assert excinfo.value.code == 2


# ---------------------------------------------------------------------------
# --update 退出码映射
# ---------------------------------------------------------------------------


def _result_runner(**kwargs: object) -> Callable[[], dg.UpdateResult]:
    """构造假更新入口（UpdateResult 关键字覆盖，默认全成功）；避免 lambda 赋值（E731）。"""
    defaults: dict[str, object] = dict(
        written=(), unchanged=(), conflicts=(), failed=(), blocked=None, post_check_errors=()
    )
    merged = {**defaults, **kwargs}

    def runner() -> dg.UpdateResult:
        return dg.UpdateResult(**merged)  # type: ignore[arg-type]  # 测试负例允许故意传非法组合

    return runner


def test_update_success_exit_0(capsys: pytest.CaptureFixture[str]) -> None:
    """更新成功（含已写/未变明细）→ 退出 0，输出如实列已写目标。"""
    assert cdc.main(["--update"], update_runner=_ok_runner) == 0
    out = capsys.readouterr().out
    assert "a.md" in out


def test_update_conflict_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    """更新检出并发冲突 → 退出 1，如实列出冲突对象与未写目标。"""
    assert cdc.main(["--update"], update_runner=_result_runner(conflicts=("a.md",))) == 1
    out = capsys.readouterr().out
    assert "a.md" in out and "冲突" in out


def test_update_blocked_exit_1() -> None:
    """写入保护/快照校验等整体阻塞 → 退出 1，不得基于旧快照继续。"""
    assert cdc.main(["--update"], update_runner=_result_runner(blocked="无法取得写入保护")) == 1


def test_update_write_failure_exit_1_and_lists(capsys: pytest.CaptureFixture[str]) -> None:
    """部分目标写失败 → 退出 1，如实同时列出已写与未写。"""
    runner = _result_runner(written=("done.md",), failed=("dead.md",))
    assert cdc.main(["--update"], update_runner=runner) == 1
    out = capsys.readouterr().out
    assert "done.md" in out and "dead.md" in out


def test_update_post_check_failure_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    """更新后 all 检查失败 → 退出 1（更新后自动跑 all 检查）。"""
    runner = _result_runner(written=("a.md",), post_check_errors=("更新后检查失败",))
    assert cdc.main(["--update"], update_runner=runner) == 1
    assert "更新后检查失败" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# 生产默认入口的存在性（真实更新入口指向 BUILTIN 注册表与 all 检查）
# ---------------------------------------------------------------------------


def test_builtin_update_runner_uses_closed_registry() -> None:
    """生产 --update 入口必须使用封闭内置注册表（不是扫描到标记就能写）。"""
    registry = cdc._builtin_registry()
    assert registry is dg.BUILTIN_REGISTRY
    targets = {spec.target for spec in registry if spec.active}
    assert targets == {"AGENTS.md", "CLAUDE.md"}
