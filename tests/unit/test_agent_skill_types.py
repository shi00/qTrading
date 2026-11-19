"""E-03-1 D02 Skill 预算与角色分配契约单元测试。

覆盖：四阈值恰等/超一（归档字节/展开字节/对象数/预检秒数）、默认⊆启用、
主默认∪手选、子仅自身默认不继承主手选、同 skill_id 多版本冲突拒绝、版本身份稳定键。
"""

from __future__ import annotations

import pytest

from core.agent_types import (
    SKILL_MAX_ARCHIVE_BYTES,
    SKILL_MAX_EXPANDED_BYTES,
    SKILL_MAX_OBJECTS,
    SKILL_MAX_PREFLIGHT_SECONDS,
    AgentValidationError,
    SkillBudget,
    SkillVersionRef,
    resolve_skill_selection,
    validate_skill_assignment,
)


def _ref(skill_id: str, version: int = 1) -> SkillVersionRef:
    return SkillVersionRef(skill_id=skill_id, version=version)


class TestSkillBudget:
    def test_design_thresholds_are_frozen(self) -> None:
        assert SKILL_MAX_ARCHIVE_BYTES == 16_777_216
        assert SKILL_MAX_EXPANDED_BYTES == 64 * 1024 * 1024
        assert SKILL_MAX_OBJECTS == 1024
        assert SKILL_MAX_PREFLIGHT_SECONDS == 10.0

    def test_archive_bytes_exact_passes_and_plus_one_rejected(self) -> None:
        budget = SkillBudget()
        budget.enforce_archive_bytes(SKILL_MAX_ARCHIVE_BYTES)
        with pytest.raises(AgentValidationError) as exc:
            budget.enforce_archive_bytes(SKILL_MAX_ARCHIVE_BYTES + 1)
        assert exc.value.code == "skill_archive_bytes_exceeded"

    def test_expanded_bytes_exact_passes_and_plus_one_rejected(self) -> None:
        budget = SkillBudget()
        budget.enforce_expanded_bytes(SKILL_MAX_EXPANDED_BYTES)
        with pytest.raises(AgentValidationError) as exc:
            budget.enforce_expanded_bytes(SKILL_MAX_EXPANDED_BYTES + 1)
        assert exc.value.code == "skill_expanded_bytes_exceeded"

    def test_objects_exact_passes_and_plus_one_rejected(self) -> None:
        budget = SkillBudget()
        budget.enforce_objects(SKILL_MAX_OBJECTS)
        with pytest.raises(AgentValidationError) as exc:
            budget.enforce_objects(SKILL_MAX_OBJECTS + 1)
        assert exc.value.code == "skill_objects_exceeded"

    def test_elapsed_exact_passes_and_over_rejected(self) -> None:
        budget = SkillBudget()
        budget.enforce_elapsed(SKILL_MAX_PREFLIGHT_SECONDS)
        with pytest.raises(AgentValidationError) as exc:
            budget.enforce_elapsed(SKILL_MAX_PREFLIGHT_SECONDS + 0.001)
        assert exc.value.code == "skill_time_exceeded"

    @pytest.mark.parametrize("value", [True, 1.5, "10"])
    def test_non_integer_counts_rejected(self, value: object) -> None:
        budget: SkillBudget = SkillBudget()
        with pytest.raises(AgentValidationError) as exc:
            budget.enforce_archive_bytes(value)  # type: ignore[arg-type]  # 测试非法类型输入
        assert exc.value.code == "invalid_type"

    def test_negative_count_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            SkillBudget().enforce_archive_bytes(-1)
        assert exc.value.code == "invalid_range"


class TestSkillVersionIdentity:
    def test_stable_key_binds_skill_and_version(self) -> None:
        assert _ref("s1", 1).stable_key() != _ref("s1", 2).stable_key()
        assert _ref("s1", 1).stable_key() == _ref("s1", 1).stable_key()

    def test_version_must_start_at_one(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            _ref("s1", 0).stable_key()
        assert exc.value.code == "invalid_range"


class TestSkillSelection:
    def test_defaults_must_be_subset_of_enabled(self) -> None:
        validate_skill_assignment(enabled=(_ref("a"),), defaults=(_ref("a"),))
        with pytest.raises(AgentValidationError) as exc:
            validate_skill_assignment(enabled=(_ref("a"),), defaults=(_ref("b"),))
        assert exc.value.code == "skill_default_not_enabled"

    def test_main_gets_defaults_union_hand_picked_within_enabled(self) -> None:
        selected = resolve_skill_selection(
            enabled=(_ref("a"), _ref("b"), _ref("c")),
            defaults=(_ref("a"),),
            hand_picked=(_ref("b"), _ref("c")),
            is_child=False,
        )
        assert selected == (_ref("a"), _ref("b"), _ref("c"))

    def test_child_gets_only_own_defaults_and_not_main_hand_picked(self) -> None:
        selected = resolve_skill_selection(
            enabled=(_ref("a"), _ref("b")),
            defaults=(_ref("a"),),
            hand_picked=(_ref("b"),),
            is_child=True,
        )
        assert selected == (_ref("a"),)

    def test_disabled_defaults_are_dropped_not_implicitly_enabled(self) -> None:
        selected = resolve_skill_selection(
            enabled=(_ref("a"),),
            defaults=(_ref("a"), _ref("z")),
            hand_picked=(),
            is_child=False,
        )
        assert selected == (_ref("a"),)

    def test_zero_selection_is_empty_not_all(self) -> None:
        assert resolve_skill_selection(enabled=(_ref("a"),), defaults=(), hand_picked=(), is_child=False) == ()

    def test_duplicates_collapse_and_order_is_deterministic(self) -> None:
        selected = resolve_skill_selection(
            enabled=(_ref("b"), _ref("a"), _ref("a")),
            defaults=(_ref("a"), _ref("b")),
            hand_picked=(_ref("a"),),
            is_child=False,
        )
        assert selected == (_ref("a"), _ref("b"))

    def test_default_and_hand_picked_version_conflict_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            resolve_skill_selection(
                enabled=(_ref("a", 1), _ref("a", 2)),
                defaults=(_ref("a", 1),),
                hand_picked=(_ref("a", 2),),
                is_child=False,
            )
        assert exc.value.code == "skill_version_conflict"

    def test_hand_picked_internal_version_conflict_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            resolve_skill_selection(
                enabled=(_ref("a", 1), _ref("a", 2)),
                defaults=(),
                hand_picked=(_ref("a", 1), _ref("a", 2)),
                is_child=False,
            )
        assert exc.value.code == "skill_version_conflict"

    def test_default_and_hand_picked_same_version_passes(self) -> None:
        selected = resolve_skill_selection(
            enabled=(_ref("a", 1),),
            defaults=(_ref("a", 1),),
            hand_picked=(_ref("a", 1),),
            is_child=False,
        )
        assert selected == (_ref("a", 1),)

    def test_multiple_enabled_versions_of_distinct_skills_pass(self) -> None:
        selected = resolve_skill_selection(
            enabled=(_ref("a", 1), _ref("a", 2), _ref("b", 1), _ref("b", 2)),
            defaults=(_ref("a", 2),),
            hand_picked=(_ref("b", 1),),
            is_child=False,
        )
        assert selected == (_ref("a", 2), _ref("b", 1))

    def test_same_input_repeated_yields_identical_selection(self) -> None:
        kwargs = {
            "enabled": (_ref("b", 1), _ref("a", 2), _ref("a", 1)),
            "defaults": (_ref("a", 2),),
            "hand_picked": (_ref("b", 1),),
            "is_child": False,
        }
        first = resolve_skill_selection(**kwargs)
        second = resolve_skill_selection(**kwargs)
        assert first == second == (_ref("a", 2), _ref("b", 1))

    def test_selection_is_order_independent(self) -> None:
        forward = resolve_skill_selection(
            enabled=(_ref("a", 1), _ref("b", 2), _ref("c", 3)),
            defaults=(_ref("a", 1),),
            hand_picked=(_ref("b", 2), _ref("c", 3)),
            is_child=False,
        )
        backward = resolve_skill_selection(
            enabled=(_ref("c", 3), _ref("b", 2), _ref("a", 1)),
            defaults=(_ref("a", 1),),
            hand_picked=(_ref("c", 3), _ref("b", 2)),
            is_child=False,
        )
        assert forward == backward == (_ref("a", 1), _ref("b", 2), _ref("c", 3))

    def test_defaults_internal_version_conflict_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            resolve_skill_selection(
                enabled=(_ref("a", 1), _ref("a", 2)),
                defaults=(_ref("a", 1), _ref("a", 2)),
                hand_picked=(),
                is_child=False,
            )
        assert exc.value.code == "skill_version_conflict"
        assert exc.value.field_path == "$.agent.skills[a]"

    def test_hand_picked_version_not_enabled_is_rejected_not_silently_replaced(self) -> None:
        # 手选 a@2 未启用时不得静默回落到默认 a@1：显式手选被换版即冲突（I60）。
        with pytest.raises(AgentValidationError) as exc:
            resolve_skill_selection(
                enabled=(_ref("a", 1),),
                defaults=(_ref("a", 1),),
                hand_picked=(_ref("a", 2),),
                is_child=False,
            )
        assert exc.value.code == "skill_version_conflict"
        assert exc.value.field_path == "$.agent.skills[a]"

    def test_hand_picked_two_versions_one_disabled_rejected_for_generator_input(self) -> None:
        # 生成器形态的手选只可消费一次：两版本一启用一未启用仍须判冲突。
        hand_picked = (_ref(skill_id, version) for skill_id, version in (("a", 1), ("a", 2)))
        with pytest.raises(AgentValidationError) as exc:
            resolve_skill_selection(
                enabled=(_ref("a", 1),),
                defaults=(),
                hand_picked=hand_picked,
                is_child=False,
            )
        assert exc.value.code == "skill_version_conflict"

    def test_hand_picked_single_version_not_enabled_still_dropped(self) -> None:
        # 单一手选版本未启用时只按「启用集合内取交集」丢弃，不构成跨版冲突。
        assert (
            resolve_skill_selection(
                enabled=(_ref("a", 1),),
                defaults=(),
                hand_picked=(_ref("a", 2),),
                is_child=False,
            )
            == ()
        )

    def test_hand_picked_enabled_version_replaces_stale_default(self) -> None:
        # 历史默认 a@1 已不在启用集合，手选 a@2 生效：可用版本顶替陈旧默认不算冲突。
        selected = resolve_skill_selection(
            enabled=(_ref("a", 2),),
            defaults=(_ref("a", 1),),
            hand_picked=(_ref("a", 2),),
            is_child=False,
        )
        assert selected == (_ref("a", 2),)
