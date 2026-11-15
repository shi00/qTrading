"""Planning / 摘要 / ResearchBoundary 装配（DD §10.3；HANDOFF §7.3，E-04-4）。

验证的是本仓库装配冻结的语义，不重复测试 harness 内部实现：

- Planning 经 deps 取 store，缺 deps 字段即拒绝（不造隐式全局 store）；
- 摘要压缩必须绑定同一模型实例、参数冻结（retries=0、工具返回不截断）；
- ResearchBoundary 三规则：模式裁剪工具、禁用来源阻断、窗口未知/超限阻断；
  ``Mode.EXECUTE`` 不裁剪；放行时不改写请求上下文。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic_ai.models import Model, ModelRequestContext, ModelRequestParameters
from pydantic_ai.tools import ToolDefinition

from core.agent_types import AgentErrorCode, AgentServiceError, Mode
from services.agent_service.capabilities import (
    ResearchBoundary,
    ResearchBoundaryPolicy,
    build_planning_capability,
    build_summarizing_compaction,
    evaluate_research_boundary,
    resolve_plan_store,
)

pytestmark = pytest.mark.unit


def _store_sentinel() -> object:
    return object()


def _ctx_with_deps(**deps: object) -> Any:
    return SimpleNamespace(deps=SimpleNamespace(**deps))


def _context(tool_names: tuple[str, ...]) -> ModelRequestContext:
    return ModelRequestContext(
        model=cast(Model, object()),
        messages=[],
        model_settings=None,
        model_request_parameters=ModelRequestParameters(
            function_tools=[
                ToolDefinition(name=name, description="d", parameters_json_schema={"type": "object"})
                for name in tool_names
            ]
        ),
    )


def _policy(**overrides: Any) -> ResearchBoundaryPolicy:
    defaults: dict[str, Any] = {
        "mode": Mode.EXECUTE,
        "allowed_tool_names": frozenset(),
        "active_source_keys": frozenset({("src-1", "1")}),
        "disabled_source_keys": frozenset(),
        "context_window_tokens": 1000,
        "estimated_input_tokens": 500,
    }
    defaults.update(overrides)
    return ResearchBoundaryPolicy(**defaults)


class TestPlanStoreResolver:
    def test_resolves_store_from_deps(self) -> None:
        store = _store_sentinel()
        assert resolve_plan_store(_ctx_with_deps(plan_store=store)) is store

    def test_missing_plan_store_rejected(self) -> None:
        with pytest.raises(ValueError) as error:
            resolve_plan_store(_ctx_with_deps())
        assert "plan_store" in str(error.value)


class TestPlanningAssembly:
    def test_default_resolver_and_frozen_flags(self) -> None:
        capability = build_planning_capability()
        assert capability.store_resolver is resolve_plan_store
        assert capability.tools is None
        assert capability.enable_subtasks is True
        assert capability.inject is True
        assert capability.store is None

    def test_tools_allowlist_and_custom_resolver(self) -> None:
        store = _store_sentinel()
        capability = build_planning_capability(
            tools=["write_plan", "read_plan"],
            enable_subtasks=False,
            store_resolver=lambda ctx: cast(Any, store),
        )
        assert capability.tools == ["write_plan", "read_plan"]
        assert capability.enable_subtasks is False
        assert capability.store_resolver is not resolve_plan_store


class TestSummarizingCompactionAssembly:
    def test_bound_model_and_frozen_parameters(self) -> None:
        compaction = build_summarizing_compaction(model="gateway:bound-model", max_tokens=8000, keep_messages=6)
        assert compaction.model == "gateway:bound-model"
        assert compaction.max_tokens == 8000
        assert compaction.keep_messages == 6
        assert compaction.retries == 0
        assert compaction.tool_return_max_chars is None
        assert compaction.preserve_first_user_message is True
        assert compaction.incremental is True

    def test_none_inherits_request_model(self) -> None:
        # 正本 §10.3 形态：model=None 继承本次请求模型（必须已是绑定 GatewayModel）
        compaction = build_summarizing_compaction(model=None, max_tokens=8000, keep_messages=6)
        assert compaction.model is None
        assert compaction.retries == 0

    def test_non_model_value_rejected(self) -> None:
        with pytest.raises(ValueError) as error:
            build_summarizing_compaction(model=cast(Any, object()), max_tokens=8000, keep_messages=6)
        assert "bound Model" in str(error.value)

    @pytest.mark.parametrize("max_tokens", [0, -1, True])
    def test_invalid_max_tokens_rejected(self, max_tokens: Any) -> None:
        with pytest.raises(ValueError) as error:
            build_summarizing_compaction(model="gateway:bound-model", max_tokens=max_tokens, keep_messages=6)
        assert "max_tokens" in str(error.value)

    def test_invalid_keep_messages_rejected(self) -> None:
        with pytest.raises(ValueError) as error:
            build_summarizing_compaction(model="gateway:bound-model", max_tokens=8000, keep_messages=-1)
        assert "keep_messages" in str(error.value)


class TestResearchBoundaryDecision:
    def test_allows_known_within_window(self) -> None:
        assert evaluate_research_boundary(_policy()) is None

    def test_disabled_active_source_blocks(self) -> None:
        policy = _policy(disabled_source_keys=frozenset({("src-1", "1")}))
        assert evaluate_research_boundary(policy) == "source_disabled"

    @pytest.mark.parametrize(
        "overrides",
        [
            {"context_window_tokens": None},
            {"estimated_input_tokens": None},
        ],
    )
    def test_unknown_window_blocks(self, overrides: dict[str, Any]) -> None:
        assert evaluate_research_boundary(_policy(**overrides)) == "window_unknown"

    def test_exceeded_window_blocks(self) -> None:
        policy = _policy(context_window_tokens=1000, estimated_input_tokens=1001)
        assert evaluate_research_boundary(policy) == "context_window_exceeded"


class TestResearchBoundaryCapability:
    @pytest.mark.asyncio
    async def test_discuss_mode_narrows_research_tools(self) -> None:
        boundary = ResearchBoundary(
            policy_provider=lambda ctx: _resolved(
                _policy(mode=Mode.DISCUSS, allowed_tool_names=frozenset({"read_plan"}))
            )
        )
        result = await boundary.before_model_request(cast(Any, None), _context(("read_plan", "mcp_search")))

        assert [tool.name for tool in result.model_request_parameters.function_tools] == ["read_plan"]

    @pytest.mark.asyncio
    async def test_plan_mode_narrows_research_tools(self) -> None:
        boundary = ResearchBoundary(
            policy_provider=lambda ctx: _resolved(_policy(mode=Mode.PLAN, allowed_tool_names=frozenset({"write_plan"})))
        )
        result = await boundary.before_model_request(cast(Any, None), _context(("write_plan", "mcp_search")))

        assert [tool.name for tool in result.model_request_parameters.function_tools] == ["write_plan"]

    @pytest.mark.asyncio
    async def test_execute_mode_keeps_tools(self) -> None:
        boundary = ResearchBoundary(policy_provider=lambda ctx: _resolved(_policy(mode=Mode.EXECUTE)))
        request_context = _context(("write_plan", "mcp_search"))

        result = await boundary.before_model_request(cast(Any, None), request_context)

        assert result is request_context
        assert [tool.name for tool in result.model_request_parameters.function_tools] == ["write_plan", "mcp_search"]

    @pytest.mark.asyncio
    async def test_unchanged_when_all_tools_allowed(self) -> None:
        boundary = ResearchBoundary(
            policy_provider=lambda ctx: _resolved(
                _policy(mode=Mode.PLAN, allowed_tool_names=frozenset({"write_plan", "read_plan"}))
            )
        )
        request_context = _context(("write_plan", "read_plan"))

        assert await boundary.before_model_request(cast(Any, None), request_context) is request_context

    @pytest.mark.asyncio
    async def test_disabled_source_raises_policy_denied(self) -> None:
        boundary = ResearchBoundary(
            policy_provider=lambda ctx: _resolved(_policy(disabled_source_keys=frozenset({("src-1", "1")})))
        )

        with pytest.raises(AgentServiceError) as error:
            await boundary.before_model_request(cast(Any, None), _context(()))
        assert error.value.code is AgentErrorCode.POLICY_DENIED
        assert "source disabled" in error.value.sanitized_message

    @pytest.mark.asyncio
    async def test_unknown_window_raises_policy_denied(self) -> None:
        boundary = ResearchBoundary(policy_provider=lambda ctx: _resolved(_policy(context_window_tokens=None)))

        with pytest.raises(AgentServiceError) as error:
            await boundary.before_model_request(cast(Any, None), _context(()))
        assert error.value.code is AgentErrorCode.POLICY_DENIED
        assert "window unknown" in error.value.sanitized_message

    @pytest.mark.asyncio
    async def test_exceeded_window_raises_policy_denied(self) -> None:
        boundary = ResearchBoundary(
            policy_provider=lambda ctx: _resolved(_policy(context_window_tokens=10, estimated_input_tokens=11))
        )

        with pytest.raises(AgentServiceError) as error:
            await boundary.before_model_request(cast(Any, None), _context(()))
        assert error.value.code is AgentErrorCode.POLICY_DENIED
        assert "estimated=11" in error.value.sanitized_message

    @pytest.mark.asyncio
    async def test_history_is_not_mutated(self) -> None:
        boundary = ResearchBoundary(
            policy_provider=lambda ctx: _resolved(_policy(mode=Mode.PLAN, allowed_tool_names=frozenset()))
        )
        request_context = _context(("mcp_search",))

        result = await boundary.before_model_request(cast(Any, None), request_context)

        assert request_context.messages == []
        assert result.messages == []
        assert result.model_request_parameters.function_tools == []


async def _resolved(policy: ResearchBoundaryPolicy) -> ResearchBoundaryPolicy:
    return policy
