"""E-03-4b 角色 Skill 分配契约单元测试（F13 / §9.10）。

覆盖：wire 全键/规范排序、默认⊆启用、空集合真空、重复版本拒绝、
摘要覆盖身份与集合、严格解码（schema_version/额外键/bool 版本）、
Skill 资产登记请求身份。
"""

from __future__ import annotations

import pytest

from core.agent_types import (
    SKILL_ASSET_METADATA_VERSION,
    AgentValidationError,
    RoleSkillAssignment,
    SkillAssetRegistration,
    SkillVersionRef,
    decode_role_skill_assignment,
    encode_skill_version_ref,
    role_skill_assignment_digest,
    validate_role_skill_assignment,
)


def _ref(skill_id: str, version: int = 1) -> SkillVersionRef:
    return SkillVersionRef(skill_id=skill_id, version=version)


def _assignment(
    enabled: tuple[SkillVersionRef, ...] = (_ref("a"), _ref("c", 2)),
    defaults: tuple[SkillVersionRef, ...] = (_ref("a"),),
    *,
    role_id: str = "role-main",
    role_version: str = "3",
) -> RoleSkillAssignment:
    return RoleSkillAssignment(
        role_id=role_id,
        role_version=role_version,
        enabled=enabled,
        defaults=defaults,
    )


class TestRoleSkillAssignmentWire:
    def test_wire_shape_and_canonical_order(self) -> None:
        assignment = _assignment(enabled=(_ref("c", 2), _ref("a")), defaults=(_ref("a"),))
        wire = assignment.wire()
        assert wire["schema_version"] == 1
        assert wire["role_id"] == "role-main"
        assert wire["role_version"] == "3"
        assert wire["enabled"] == [
            {"skill_id": "a", "version": 1},
            {"skill_id": "c", "version": 2},
        ]
        assert wire["defaults"] == [{"skill_id": "a", "version": 1}]

    def test_empty_sets_are_truly_empty(self) -> None:
        wire = _assignment(enabled=(), defaults=()).wire()
        assert wire["enabled"] == []
        assert wire["defaults"] == []

    def test_digest_binds_identity_and_sets(self) -> None:
        base = role_skill_assignment_digest(_assignment())
        assert role_skill_assignment_digest(_assignment()) == base
        assert role_skill_assignment_digest(_assignment(role_version="4")) != base
        assert role_skill_assignment_digest(_assignment(enabled=(_ref("a"), _ref("c", 3)))) != base
        assert role_skill_assignment_digest(_assignment(defaults=(_ref("a"), _ref("c", 2)))) != base

    def test_digest_is_order_insensitive(self) -> None:
        first = _assignment(enabled=(_ref("a"), _ref("c", 2)))
        second = _assignment(enabled=(_ref("c", 2), _ref("a")))
        assert role_skill_assignment_digest(first) == role_skill_assignment_digest(second)


class TestRoleSkillAssignmentValidation:
    def test_defaults_must_be_subset_of_enabled(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            validate_role_skill_assignment(_assignment(defaults=(_ref("b"),)))
        assert exc.value.code == "skill_default_not_enabled"

    def test_default_version_must_match_exact_enabled_version(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            validate_role_skill_assignment(_assignment(defaults=(_ref("a", 2),)))
        assert exc.value.code == "skill_default_not_enabled"

    def test_duplicate_versions_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            validate_role_skill_assignment(_assignment(enabled=(_ref("a"), _ref("a"))))
        assert exc.value.code == "duplicate_skill_version"

    def test_empty_role_identity_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            validate_role_skill_assignment(_assignment(role_id=""))
        assert exc.value.code == "invalid_identity"

    def test_mode_version_bool_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            validate_role_skill_assignment(_assignment(enabled=(SkillVersionRef(skill_id="a", version=True),)))  # type: ignore[arg-type]  # 测试非法 bool 版本
        assert exc.value.code == "invalid_type"


class TestDecodeRoleSkillAssignment:
    def _wire(self) -> dict[str, object]:
        return _assignment().wire()

    def test_round_trip(self) -> None:
        decoded = decode_role_skill_assignment(self._wire())
        assert decoded == _assignment()

    def test_missing_key_rejected(self) -> None:
        wire = self._wire()
        del wire["defaults"]
        with pytest.raises(AgentValidationError) as exc:
            decode_role_skill_assignment(wire)
        assert exc.value.code == "missing_field"

    def test_extra_key_rejected(self) -> None:
        wire = self._wire()
        wire["unexpected"] = 1
        with pytest.raises(AgentValidationError) as exc:
            decode_role_skill_assignment(wire)
        assert exc.value.code == "unknown_field"

    def test_wrong_schema_version_rejected(self) -> None:
        wire = self._wire()
        wire["schema_version"] = 2
        with pytest.raises(AgentValidationError) as exc:
            decode_role_skill_assignment(wire)
        assert exc.value.code == "invalid_schema_version"

    def test_bool_schema_version_rejected(self) -> None:
        wire = self._wire()
        wire["schema_version"] = True
        with pytest.raises(AgentValidationError) as exc:
            decode_role_skill_assignment(wire)
        assert exc.value.code == "invalid_schema_version"

    def test_non_list_sets_rejected(self) -> None:
        wire = self._wire()
        wire["enabled"] = {"skill_id": "a", "version": 1}
        with pytest.raises(AgentValidationError) as exc:
            decode_role_skill_assignment(wire)
        assert exc.value.code == "invalid_type"

    def test_ref_extra_key_rejected(self) -> None:
        wire = self._wire()
        wire["enabled"] = [{"skill_id": "a", "version": 1, "extra": True}]
        with pytest.raises(AgentValidationError) as exc:
            decode_role_skill_assignment(wire)
        assert exc.value.code == "unknown_field"

    def test_default_not_enabled_decoded_rejected(self) -> None:
        wire = self._wire()
        wire["defaults"] = [encode_skill_version_ref(_ref("b"))]
        with pytest.raises(AgentValidationError) as exc:
            decode_role_skill_assignment(wire)
        assert exc.value.code == "skill_default_not_enabled"


class TestSkillAssetRegistrationContract:
    def test_metadata_version_is_frozen(self) -> None:
        assert SKILL_ASSET_METADATA_VERSION == 1

    def test_registration_keeps_lists_ordered_and_empty_defaults(self) -> None:
        request = SkillAssetRegistration(
            user_scope="scope",
            skill_id="skill-1",
            version=2,
            display_name="demo-skill",
            content_digest="0" * 64,
            normalized_name="demo-skill",
            license_id="MIT",
            archive_digest="1" * 64,
            file_count=3,
            expanded_bytes=128,
        )
        assert request.dependencies == ()
        assert request.missing_dependencies == ()
        assert request.ignored_behavioral_fields == ()
