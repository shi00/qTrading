"""Planning TextContent 映射回归（HANDOFF §6.3；E-04-4）。

Harness ``Planning`` 通过用户通道投递计划提醒：
``UserPromptPart([TextContent(plan, metadata={'origin': ...})])``。桥必须把
序列内 ``TextContent`` 按文本、保序映射，且不把 Harness 元数据带出站；
非文本内容仍明确拒绝（zero outbound，不静默降级为图片）。

三条路径：首次计划提醒、恢复后计划变更、无计划（行为不变）。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic_ai.messages import BinaryContent, ModelRequest, TextContent, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.settings import ModelSettings

from core.model_management_types import ModelTurnProtocolError, TextContentPart
from services.agent_service.model import build_chat_turn_request

pytestmark = pytest.mark.unit

PLAN_ORIGIN = "pydantic-ai-harness.planning.plan_reminder.v1"


def _settings() -> ModelSettings:
    return ModelSettings(timeout=30.0, temperature=0.2, max_tokens=256)


def _params(**overrides: Any) -> ModelRequestParameters:
    return ModelRequestParameters(**overrides)


def _request(history: list[Any]) -> Any:
    return build_chat_turn_request(history, _settings(), _params(), tool_schema_version="v1", model_call_id="mc-x")


def _reminder(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=[TextContent(text, metadata={"origin": PLAN_ORIGIN})])])


def _user_texts(request: Any) -> list[str]:
    return [
        part.text
        for message in request.messages
        if message.role == "user"
        for part in message.content_parts
        if isinstance(part, TextContentPart)
    ]


class TestPlanningReminderMapping:
    @pytest.mark.asyncio
    async def test_first_plan_reminder_maps_to_user_text(self) -> None:
        request = _request(
            [
                ModelRequest(parts=[UserPromptPart(content="开始研究")]),
                _reminder("<plan_reminder>1</plan_reminder>"),
            ]
        )

        assert _user_texts(request) == ["开始研究", "<plan_reminder>1</plan_reminder>"]
        assert [message.role for message in request.messages] == ["user", "user"]

    @pytest.mark.asyncio
    async def test_harness_metadata_does_not_leak_outbound(self) -> None:
        request = _request([_reminder("<plan_reminder>v1</plan_reminder>")])

        assert "origin" not in json.dumps(request.frozen_params, ensure_ascii=False)
        assert "origin" not in json.dumps(request.instructions, ensure_ascii=False)
        assert all("origin" not in json.dumps(message.metadata, ensure_ascii=False) for message in request.messages)

    @pytest.mark.asyncio
    async def test_recovered_plan_change_keeps_order(self) -> None:
        request = _request(
            [
                _reminder("<plan_reminder>v1</plan_reminder>"),
                _reminder("<plan_reminder>v2</plan_reminder>"),
            ]
        )

        assert _user_texts(request) == ["<plan_reminder>v1</plan_reminder>", "<plan_reminder>v2</plan_reminder>"]

    @pytest.mark.asyncio
    async def test_no_plan_path_is_unchanged(self) -> None:
        request = _request([ModelRequest(parts=[UserPromptPart(content="直接提问")])])

        assert _user_texts(request) == ["直接提问"]
        assert request.instructions == ()
        assert request.tools == ()

    @pytest.mark.asyncio
    async def test_single_text_part_sequence_supported(self) -> None:
        request = _request([ModelRequest(parts=[UserPromptPart(content=[TextContent("单段文本")])])])

        assert _user_texts(request) == ["单段文本"]

    @pytest.mark.asyncio
    async def test_multiple_text_parts_joined_in_order(self) -> None:
        request = _request(
            [ModelRequest(parts=[UserPromptPart(content=[TextContent("第一段"), TextContent("第二段")])])]
        )

        assert _user_texts(request) == ["第一段\n第二段"]

    @pytest.mark.asyncio
    async def test_non_text_content_still_rejected(self) -> None:
        history = [
            ModelRequest(
                parts=[
                    UserPromptPart(
                        content=[TextContent("说明"), BinaryContent(data=b"\x89PNG", media_type="image/png")]
                    )
                ]
            )
        ]

        with pytest.raises(ModelTurnProtocolError) as error:
            _request(history)
        assert error.value.code == "unknown_part"
        assert error.value.field_path.endswith("user_prompt.content[1].BinaryContent")
