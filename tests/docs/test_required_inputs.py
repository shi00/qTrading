"""D5：42/43 项检查的必需输入缺失显式失败回归（检视 F17 / AC06）。

检视 §6.1 T5：42 项原检查各有「保留/生成替代/迁移出口」，且「失踪输入空转」必须
显式失败——格式异常不得从全文任取样例代替、文件缺失不得静默通过。

本测试以数据驱动表逐项注入「必需输入缺失」（monkeypatch 模块常量到临时仓库的不存在
路径或空仓库），先确认真实仓库输入下检查零错误（合法基线），再断言每个检查函数
**不得静默返回空错误列表**：
- 显式失败形态一：返回非空 errors（fail-closed 报错），且至少一条错误指向被注入的缺失对象；
- 显式失败形态二：预期 IO 异常（``FileNotFoundError``）向上传播且指向被注入的缺失对象。
两种形态都算显式失败；无关编程异常、指向其他对象或与缺失对象无关的 errors 均使负例
自身失败；返回 [] 即为「失踪输入空转」违规。

覆盖清单与 _SCOPE_CHECK_NAMES 一致（44 项 = 42 项原检查 + D4 新增 check_docs_generated
+ L10 生产集成 check_exceptions_effectiveness）。
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import check_docs_consistency as cdc  # noqa: E402

pytestmark = pytest.mark.docs_pure


def _missing(tmp_path: Path, name: str) -> Path:
    """tmp 下一个必然不存在的路径。"""
    return tmp_path / "_absent" / name


class _Negative(NamedTuple):
    """缺失输入负例：注入器 + 失败时必须指向的缺失对象标记。"""

    inject: Callable[[pytest.MonkeyPatch], None]
    missing_marker: str


def _marker_of(value: Any) -> str:
    """注入值的缺失对象标记（列表取首项，如 FLET_VERSION_DOCS）。"""
    if isinstance(value, list) and value:
        return str(value[0])
    return str(value)


# 数据驱动表：(检查函数名, 负例)。
# 负例注入器签名 (monkeypatch) -> None：把该项检查的**必需输入**缺失化。
# missing_marker 是缺失输入被显式检出时失败信息必须指向的对象（路径或文件名），
# 用于排除「无关异常/无关错误」蒙混为显式失败；每项映射对应检查函数读取治理源的
# 核心模块常量（依赖经 AST 盘点核对）。
def _build_negatives(tmp_path: Path) -> dict[str, _Negative]:
    def patch(const: str, value: Any, *, marker: str | None = None) -> _Negative:
        def _apply(monkeypatch: pytest.MonkeyPatch) -> None:
            monkeypatch.setattr(cdc, const, value)

        return _Negative(_apply, marker if marker is not None else _marker_of(value))

    ghost_path = _missing(tmp_path, "ghost.md")

    def ghost_doc(monkeypatch: pytest.MonkeyPatch) -> None:
        # 受检集指向不存在文件：读取边界必须显式失败而非「空集合自然通过」
        monkeypatch.setattr(cdc, "CHECKED_DOCS", [ghost_path])

    def ghost_docs_and_hub(monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cdc, "CHECKED_DOCS", [ghost_path])
        monkeypatch.setattr(cdc, "FLET_HUB_PATH", _missing(tmp_path, "hub.md"))

    negatives: dict[str, _Negative] = {
        # —— document：结构 / 锚点 / 索引 / 生成 ——
        "check_anchor_dead_links": _Negative(ghost_doc, str(ghost_path)),
        "check_relative_dead_links": _Negative(ghost_doc, str(ghost_path)),
        "check_flet_hub_completeness": patch("FLET_HUB_PATH", _missing(tmp_path, "hub.md")),
        "check_agents_md_sync": patch("AGENTS_PATH", _missing(tmp_path, "AGENTS.md")),
        "check_agents_md_min_verify_commands": patch("AGENTS_PATH", _missing(tmp_path, "AGENTS.md")),
        "check_claude_executive_sync": patch("CLAUDE_PATH", _missing(tmp_path, "CLAUDE.md")),
        "check_claude_self_check_sync": patch("CLAUDE_PATH", _missing(tmp_path, "CLAUDE.md")),
        "check_canonical_completion_criteria": patch(
            "CANONICAL_TOPICS_YAML_PATH", _missing(tmp_path, "canonical-topics.yml"), marker="canonical-topics.yml"
        ),
        "check_canonical_docs_are_gated": patch(
            "CANONICAL_TOPICS_YAML_PATH", _missing(tmp_path, "canonical-topics.yml"), marker="canonical-topics.yml"
        ),
        "check_docs_index_completeness": patch("DOCS_README_PATH", _missing(tmp_path, "README.md")),
        "check_reviews_index_completeness": patch("REVIEWS_README_PATH", _missing(tmp_path, "README.md")),
        "check_adr_index_completeness": patch("CONTRIBUTING_PATH", _missing(tmp_path, "CONTRIBUTING.md")),
        "check_scripts_index_completeness": patch("CI_CD_PATH", _missing(tmp_path, "ci-cd.md")),
        "check_guillemet_references": _Negative(ghost_doc, str(ghost_path)),
        "check_adr_supersede_chain": patch("ADR_DOCS_DIR", _missing(tmp_path, "adr")),
        "check_evals_case_index_consistency": patch("EVALS_README_PATH", _missing(tmp_path, "README.md")),
        "check_docs_generated": patch("ROOT", tmp_path / "_absent_root", marker="登记目标文件不存在"),
        # —— governance：作者源 / 例外 / 路由 / 配置映射 ——
        "check_precommit_hook_count": patch("PRECOMMIT_PATH", _missing(tmp_path, ".pre-commit-config.yaml")),
        "check_precommit_hook_names": patch("CI_CD_PATH", _missing(tmp_path, "ci-cd.md")),
        "check_workflow_enum": patch("CI_CD_PATH", _missing(tmp_path, "ci-cd.md")),
        "check_flet_version_drift": patch("FLET_VERSION_DOCS", [_missing(tmp_path, "CLAUDE.md")]),
        "check_redlines_yaml_consistency": patch("REDLINES_YAML_PATH", _missing(tmp_path, "redlines.yml")),
        "check_redline_range_consistency": patch("REDLINES_YAML_PATH", _missing(tmp_path, "redlines.yml")),
        "check_contract_count_prose": patch("PYPROJECT_PATH", _missing(tmp_path, "pyproject.toml")),
        "check_enforcement_mapping": patch("REDLINES_YAML_PATH", _missing(tmp_path, "redlines.yml")),
        "check_enforcement_reverse_coverage": patch(
            "CHECK_REDLINES_SCRIPT_PATH", _missing(tmp_path, "check_redlines.py")
        ),
        "check_exceptions_yaml_consistency": patch("EXCEPTIONS_YAML_PATH", _missing(tmp_path, "exceptions.yml")),
        "check_exceptions_reverse_coverage": patch("EXCEPTIONS_YAML_PATH", _missing(tmp_path, "exceptions.yml")),
        "check_exceptions_effectiveness": patch("EXCEPTIONS_YAML_PATH", _missing(tmp_path, "exceptions.yml")),
        "check_canonical_topics_consistency": patch(
            "CANONICAL_TOPICS_YAML_PATH", _missing(tmp_path, "canonical-topics.yml")
        ),
        "check_canonical_routing": patch(
            "CANONICAL_TOPICS_YAML_PATH", _missing(tmp_path, "canonical-topics.yml"), marker="canonical-topics.yml"
        ),
        "check_ruleset_metadata_consistency": patch("CLAUDE_PATH", _missing(tmp_path, "CLAUDE.md")),
        "check_ruleset_changelog_version": patch("RULESET_CHANGELOG_PATH", _missing(tmp_path, "ruleset-changelog.md")),
        "check_decision_tree_mapping": patch("CLAUDE_PATH", _missing(tmp_path, "CLAUDE.md")),
        "check_governance_id_references": patch(
            "EXCEPTIONS_YAML_PATH", _missing(tmp_path, "exceptions.yml"), marker="exceptions.yml"
        ),
        "_glossary_check_with_warnings": patch(
            "GOVERNANCE_IDS_PATH", _missing(tmp_path, "governance-ids.md"), marker="governance-ids.md"
        ),
        "check_governance_id_dual_meaning": patch("GOVERNANCE_IDS_PATH", _missing(tmp_path, "governance-ids.md")),
        "check_flet_badge_version": patch("README_PATH", _missing(tmp_path, "README.md")),
        "check_exception_count_prose": patch("EXCEPTIONS_YAML_PATH", _missing(tmp_path, "exceptions.yml")),
        # —— source：NOTE / 模块 / 单例 / 动态文案源码契约 ——
        "check_note_lazy_format": patch("ROOT", tmp_path / "_absent_root"),
        "check_core_modules_completeness": patch(
            "ROOT", tmp_path / "_absent_root", marker="docs/architecture/overview.md"
        ),
        "check_singleton_count_prose": patch("SINGLETON_LIFECYCLE_PATH", _missing(tmp_path, "singleton-lifecycle.md")),
        "check_non_registered_singleton_doc": patch(
            "SINGLETON_LIFECYCLE_PATH", _missing(tmp_path, "singleton-lifecycle.md")
        ),
        "check_strategy_desc_dynamic_consistency": patch("ROOT", tmp_path / "_absent_root"),
    }
    return negatives


_EXPECTED_MISSING_INPUT_ERRORS = (FileNotFoundError,)


def _assert_explicit_failure(check_name: str, fn: Callable[[], list[str]], missing_marker: str) -> None:
    """缺失输入下不得静默返回空错误列表，且失败必须指向被注入的缺失对象（F17）。

    只接受预期 IO 异常（``FileNotFoundError``）或指向缺失对象的非空 errors；
    无关编程异常（如 NameError）与无关错误一律判负例自身失败，避免伪绿。
    """
    try:
        errors = fn()
    except Exception as exc:
        assert isinstance(exc, _EXPECTED_MISSING_INPUT_ERRORS), (
            f"{check_name}: 缺失输入仅允许以 FileNotFoundError 传播，实际 {type(exc).__name__}: {exc}"
        )
        filename = exc.filename or ""
        assert missing_marker in filename or missing_marker in str(exc), (
            f"{check_name}: 读取异常未指向缺失对象 {missing_marker!r}: {exc}"
        )
        return
    assert errors, f"{check_name}: 必需输入缺失后静默通过（失踪输入空转，违反 F17/AC06）"
    assert any(missing_marker in error for error in errors), (
        f"{check_name}: 失败未指向缺失对象 {missing_marker!r}: {errors[:3]}"
    )


def _assert_legal_baseline(check_name: str, fn: Callable[[], list[str]]) -> None:
    """注入缺失输入前先过合法基线：真实仓库输入下不抛异常且零错误。"""
    try:
        errors = fn()
    except Exception as exc:
        pytest.fail(f"{check_name}: 合法基线不应抛异常，实际 {type(exc).__name__}: {exc}")
    assert errors == [], f"{check_name}: 合法基线应零错误，实际: {errors[:3]}"


def test_all_scope_checks_have_required_input_negative(tmp_path: Path):
    """_SCOPE_CHECK_NAMES 的 44 项每一项都有缺失输入负例映射（承接表完整性）。"""
    negatives = _build_negatives(tmp_path)
    scope_names = {name for _, name in cdc._SCOPE_CHECK_NAMES}
    missing = sorted(scope_names - set(negatives))
    assert missing == [], f"以下检查缺少必需输入缺失负例映射: {missing}"
    extra = sorted(set(negatives) - scope_names)
    assert extra == [], f"负例映射含 _SCOPE_CHECK_NAMES 之外的函数: {extra}"


@pytest.mark.parametrize(
    "check_name",
    sorted(name for _, name in cdc._SCOPE_CHECK_NAMES),
)
def test_missing_required_input_fails_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, check_name: str):
    """逐项注入缺失输入：合法基线通过后，显式失败且指向被注入的缺失对象，不得返回 []。"""
    negatives = _build_negatives(tmp_path)
    negative = negatives[check_name]
    fn = getattr(cdc, check_name)
    _assert_legal_baseline(check_name, fn)
    negative.inject(monkeypatch)
    _assert_explicit_failure(check_name, fn, negative.missing_marker)


def test_guard_rejects_unrelated_programming_exception():
    """无关编程异常（NameError）不得被当作缺失输入的显式失败。"""

    def _raise_name_error() -> list[str]:
        raise NameError("name 'undefined_symbol' is not defined")

    with pytest.raises(AssertionError, match="FileNotFoundError"):
        _assert_explicit_failure("synthetic_name_error", _raise_name_error, "/tmp/missing.yml")


def test_guard_rejects_io_exception_for_other_object():
    """FileNotFoundError 指向非注入对象时，不得算作核到缺失对象。"""

    def _raise_other_missing_file() -> list[str]:
        raise FileNotFoundError(2, "No such file or directory", "/tmp/other.yml")

    with pytest.raises(AssertionError, match="未指向缺失对象"):
        _assert_explicit_failure("synthetic_other_file", _raise_other_missing_file, "/tmp/missing.yml")


def test_guard_rejects_unrelated_errors():
    """非空但与缺失对象无关的 errors 不得被当作显式失败。"""
    with pytest.raises(AssertionError, match="未指向缺失对象"):
        _assert_explicit_failure("synthetic_unrelated_errors", lambda: ["无关失败"], "/tmp/missing.yml")


def test_guard_accepts_failures_naming_missing_object():
    """正向对照：预期 IO 异常与指向缺失对象的 errors 均应被接受。"""

    def _raise_expected() -> list[str]:
        raise FileNotFoundError(2, "No such file or directory", "/tmp/missing.yml")

    _assert_explicit_failure("synthetic_io_ok", _raise_expected, "/tmp/missing.yml")
    _assert_explicit_failure("synthetic_errors_ok", lambda: ["必需文件不存在: /tmp/missing.yml"], "/tmp/missing.yml")
