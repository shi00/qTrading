"""Unit tests for tests.e2e.helpers.anchor_page.assert_label_independent.

覆盖 D21-OTHER-007 修复后的 fail-closed 契约（无需真实浏览器）：JS 快照必须区分
「目标存在且唯一 / 目标缺失 / 目标歧义 / 父节点缺失 / 自身独立 label 缺失 /
父节点合并子 EID」，任一前提不成立都显式失败。

修复前的旧 JS 在目标或父节点不存在时返回 ``false``，外层 ``assert not
parent_has_eid`` 反而通过（fail-open）；本文件的缺失/歧义用例即为该反例的回归守护。
"""

from __future__ import annotations

import pytest

from tests.e2e.helpers.anchor_label import LABEL_INDEPENDENCE_JS, assert_label_independent

pytestmark = pytest.mark.unit

_EID = "e2e.screener.run_button"


def _snapshot(**overrides: object) -> dict[str, object]:
    """合法快照基线：目标唯一、自身 label 独立、父节点存在且未合并子 EID。"""
    snapshot: dict[str, object] = {
        "count": 1,
        "found": True,
        "own_label_matches": True,
        "parent_found": True,
        "parent_has_eid": False,
    }
    snapshot.update(overrides)
    return snapshot


def test_independent_label_passes() -> None:
    """正常独立结构：目标唯一、自身 aria-label 等于 EID、父节点未合并 → 通过。"""
    assert_label_independent(_snapshot(), _EID)


def test_missing_target_fails_instead_of_fail_open() -> None:
    """目标不存在（count=0）：旧实现返回 false 使否定断言通过，现必须显式失败。"""
    with pytest.raises(AssertionError, match="目标不存在"):
        assert_label_independent(
            _snapshot(count=0, found=False, own_label_matches=False, parent_found=False),
            _EID,
        )


def test_ambiguous_multiple_targets_fail() -> None:
    """同名 identifier 节点多于一个：无法确认 label 独立性归属 → 失败。"""
    with pytest.raises(AssertionError, match="应唯一"):
        assert_label_independent(_snapshot(count=2), _EID)


def test_missing_parent_fails() -> None:
    """父节点不存在：container=True 无法验证，不得当作通过。"""
    with pytest.raises(AssertionError, match="无父语义节点"):
        assert_label_independent(
            _snapshot(own_label_matches=True, parent_found=False, parent_has_eid=False),
            _EID,
        )


def test_missing_own_label_fails() -> None:
    """identifier 仍在但自身 aria-label 不等于 EID：独立名称通道缺失 → 失败。"""
    with pytest.raises(AssertionError, match="独立 label 通道缺失"):
        assert_label_independent(_snapshot(own_label_matches=False), _EID)


def test_parent_merging_child_eid_fails() -> None:
    """父节点 aria-label 含子 EID：container=True 未生效 → 失败。"""
    with pytest.raises(AssertionError, match="container=True 未生效"):
        assert_label_independent(_snapshot(parent_has_eid=True), _EID)


@pytest.mark.parametrize("snapshot", [None, [], "ok", 1])
def test_non_mapping_snapshot_fails(snapshot: object) -> None:
    """快照类型非法（evaluate 返回异常形态）→ 显式失败，而不是静默通过。"""
    with pytest.raises(AssertionError, match="快照类型非法"):
        assert_label_independent(snapshot, _EID)


def test_js_contract_exposes_tristate_keys() -> None:
    """JS 快照契约必须携带三态字段，防止实现与断言键名漂移。"""
    for key in ("count", "found", "own_label_matches", "parent_found", "parent_has_eid"):
        assert key in LABEL_INDEPENDENCE_JS
