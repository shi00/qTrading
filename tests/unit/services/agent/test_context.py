"""S01 上下文：因果前缀绑定与 Skill 有效集（§6.3）。

有效集与请求绑定由 C02 已实现，本文件固定合同：主=默认∪手选、子仅默认、
两集合必须在启用集内、同资源异版本冲突；历史重发必须绑定会话。
"""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from core.agent_types import (
    AgentValidationError,
    ResourceRef,
    effective_skills,
    validate_request,
)
from tests.builders.agent import make_request

_REF = ResourceRef(UUID("20000000-0000-0000-0000-000000000001"), 1)
_OTHER = ResourceRef(UUID("20000000-0000-0000-0000-000000000002"), 1)


def test_main_effective_skills_union_default_and_manual() -> None:
    assert effective_skills((_REF,), (_OTHER,), enabled=(_REF, _OTHER), child=False) == (_REF, _OTHER)


def test_duplicate_ref_is_deduped_by_identity() -> None:
    same = ResourceRef(_REF.resource_id, 1)
    assert effective_skills((_REF,), (same,), enabled=(_REF,), child=False) == (_REF,)


def test_same_resource_different_revision_conflicts() -> None:
    with pytest.raises(AgentValidationError) as caught:
        effective_skills(
            (_REF,),
            (_REF, ResourceRef(_REF.resource_id, 2)),
            enabled=(_REF,),
            child=False,
        )
    assert caught.value.code == "identity_conflict"


def test_manual_skill_outside_enabled_rejected() -> None:
    with pytest.raises(AgentValidationError) as caught:
        effective_skills((), (_OTHER,), enabled=(_REF,), child=False)
    assert caught.value.code == "invalid_subset"


def test_default_skill_outside_enabled_rejected() -> None:
    with pytest.raises(AgentValidationError) as caught:
        effective_skills((_OTHER,), (), enabled=(_REF,), child=False)
    assert caught.value.code == "invalid_subset"


def test_child_uses_only_own_defaults() -> None:
    assert effective_skills((_REF,), (_OTHER,), enabled=(_REF, _OTHER), child=True) == (_REF,)


def test_empty_enabled_allows_no_skills() -> None:
    assert effective_skills((), (), enabled=(), child=False) == ()


def test_historical_resend_requires_conversation() -> None:
    request = replace(make_request(), intent_kind="historical_resend", conversation_id=None)
    with pytest.raises(AgentValidationError) as caught:
        validate_request(request)
    assert caught.value.field_path == "conversation_id"


def test_historical_resend_bound_to_conversation_passes() -> None:
    request = replace(make_request(), intent_kind="historical_resend", conversation_id=uuid4())
    validate_request(request)
