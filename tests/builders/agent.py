"""Agent 测试夹具构建器（设计正本 §12.3）。

只构造唯一本文 DTO；会话/路径等上下文由真实服务建立后再用 dataclasses.replace 补齐。
"""

from datetime import date
from uuid import UUID

from core.agent_types import (
    AgentRequest,
    ContextBinding,
    ModelChoice,
    ResourceRef,
    RoleBinding,
    SubmissionKey,
)

_PROMPT_REF = ResourceRef(UUID("10000000-0000-0000-0000-000000000001"), 1)


def make_request() -> AgentRequest:
    role = RoleBinding(
        "main",
        1,
        _PROMPT_REF,
        ModelChoice("explicit", "connection-A", None),
        (),
        (),
        (),
    )
    return AgentRequest(
        key=SubmissionKey("test-domain", "tests", "request-1"),
        conversation_id=None,
        intent_kind="ordinary",
        text="只解释所选资料的缺口",
        mode="discuss",
        as_of=date(2026, 4, 30),
        context=ContextBinding(None, None, None, (), ()),
        sources=(),
        roles=(role,),
        manual_skills=(),
        mcp=(),
        output_contract="answer/1",
        business_owner=None,
        business_run=None,
        business_slot=None,
    )
