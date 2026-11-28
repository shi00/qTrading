"""Planning / 摘要 / ResearchBoundary 装配（DD §10.3；HANDOFF §7.3，E-04-4）。

验证的是本仓库装配冻结的语义，不重复测试 harness 内部实现：

- Planning 经 deps 取 store，缺 deps 字段即拒绝（不造隐式全局 store）；
- 摘要压缩必须继承运行自己的绑定模型（显式模型/模型 id 一律拒绝）、参数冻结
  （retries=0、工具返回不截断、回执开启）；
- ResearchBoundary 三规则：模式裁剪工具、禁用来源阻断、窗口未知/超限阻断；
  ``Mode.EXECUTE`` 不裁剪；放行时不改写请求上下文。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic_ai.messages import BinaryContent, ModelRequest, UserPromptPart
from pydantic_ai.models import Model, ModelRequestContext, ModelRequestParameters
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import ToolDefinition

from core.agent_types import AgentErrorCode, AgentServiceError, Mode
from services.agent_service.capabilities import (
    ResearchBoundary,
    ResearchBoundaryPolicy,
    _contains_binary_content,
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
    def test_inherits_run_bound_model_and_frozen_parameters(self) -> None:
        compaction = build_summarizing_compaction(max_tokens=8000, keep_messages=6)
        # 摘要必须继承被压缩运行自己的已绑定模型（None → ctx.model），不得钉死另一实例。
        assert compaction.model is None
        assert compaction.max_tokens == 8000
        assert compaction.keep_messages == 6
        assert compaction.retries == 0
        assert compaction.tool_return_max_chars is None
        assert compaction.receipts is True
        assert compaction.preserve_first_user_message is True
        assert compaction.incremental is True

    def test_none_inherits_request_model(self) -> None:
        # 正本 §10.3 形态：model=None 继承本次请求模型（即该运行的绑定 GatewayModel）
        compaction = build_summarizing_compaction(model=None, max_tokens=8000, keep_messages=6)
        assert compaction.model is None
        assert compaction.retries == 0

    @pytest.mark.parametrize("model", ["gateway:bound-model", object(), TestModel()])
    def test_explicit_model_rejected(self, model: Any) -> None:
        # 模型 id 会造未治理模型；模型实例会钉死构造时那一次运行的身份（跨角色混账）。
        with pytest.raises(ValueError) as error:
            build_summarizing_compaction(model=model, max_tokens=8000, keep_messages=6)
        assert "inherit the run's bound" in str(error.value)

    @pytest.mark.parametrize("max_tokens", [0, -1, True])
    def test_invalid_max_tokens_rejected(self, max_tokens: Any) -> None:
        with pytest.raises(ValueError) as error:
            build_summarizing_compaction(max_tokens=max_tokens, keep_messages=6)
        assert "max_tokens" in str(error.value)

    def test_invalid_keep_messages_rejected(self) -> None:
        with pytest.raises(ValueError) as error:
            build_summarizing_compaction(max_tokens=8000, keep_messages=-1)
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

    def test_strictest_candidate_window_wins(self) -> None:
        # I47：窗口取所有候选可用限制中最严者，小窗口备用模型拖低上限。
        blocked = _policy(
            context_window_tokens=128_000,
            candidate_context_windows=(1_000,),
            estimated_input_tokens=1_500,
        )
        assert evaluate_research_boundary(blocked) == "context_window_exceeded"
        within = _policy(
            context_window_tokens=128_000,
            candidate_context_windows=(2_000,),
            estimated_input_tokens=1_500,
        )
        assert evaluate_research_boundary(within) is None

    def test_unknown_current_window_not_rescued_by_candidates(self) -> None:
        # I47：本候选窗口未知即拒绝，不能用其它候选窗口替代（当前请求就发往本候选）。
        policy = _policy(context_window_tokens=None, candidate_context_windows=(128_000,))
        assert evaluate_research_boundary(policy, measured_input_tokens=500) == "window_unknown"

    def test_measured_estimate_substitutes_missing_external(self) -> None:
        # I47：外部估计缺失时由自测兜底；两者皆缺失才拒绝。
        policy = _policy(estimated_input_tokens=None)
        assert evaluate_research_boundary(policy) == "window_unknown"
        assert evaluate_research_boundary(policy, measured_input_tokens=500) is None
        assert evaluate_research_boundary(policy, measured_input_tokens=1001) == "context_window_exceeded"

    def test_output_reserve_is_subtracted_from_window(self) -> None:
        # 输出预留计入窗口预算：赢余只够输入、不够输出时同样阻断（不留整窗给输入）。
        policy = _policy(context_window_tokens=1000, estimated_input_tokens=900, output_reserve_tokens=200)
        assert evaluate_research_boundary(policy) == "context_window_exceeded"
        within = _policy(context_window_tokens=1000, estimated_input_tokens=800, output_reserve_tokens=200)
        assert evaluate_research_boundary(within) is None

    @pytest.mark.parametrize("reserve", [-1, True, 1.5])
    def test_invalid_output_reserve_rejected(self, reserve: Any) -> None:
        with pytest.raises(ValueError) as exc:
            _policy(output_reserve_tokens=reserve)
        assert "output_reserve_tokens" in str(exc.value)


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

    @pytest.mark.asyncio
    async def test_oversized_tool_schema_rejected_before_dispatch(self, monkeypatch) -> None:
        # I47：正文极短但工具 schema 极大，外部低估值不能放宽闸门。
        monkeypatch.setattr(
            "services.ai_service.token_budget._estimate_tokens",
            lambda text, model=None: len(text or ""),
        )
        big_schema = {"type": "object", "properties": {f"field_{i}": {"type": "string"} for i in range(200)}}
        request_context = ModelRequestContext(
            model=cast(Model, object()),
            messages=[],
            model_settings=None,
            model_request_parameters=ModelRequestParameters(
                function_tools=[ToolDefinition(name="search", description="d", parameters_json_schema=big_schema)]
            ),
        )
        boundary = ResearchBoundary(
            policy_provider=lambda ctx: _resolved(_policy(context_window_tokens=500, estimated_input_tokens=1))
        )

        with pytest.raises(AgentServiceError) as error:
            await boundary.before_model_request(cast(Any, None), request_context)
        assert error.value.code is AgentErrorCode.POLICY_DENIED
        assert "context window exceeded" in error.value.sanitized_message

    @pytest.mark.asyncio
    async def test_narrowed_tools_measured_after_trim(self, monkeypatch) -> None:
        # I47：按最终待发请求计量——被模式裁剪掉的工具 schema 不得计入，避免误拒。
        monkeypatch.setattr(
            "services.ai_service.token_budget._estimate_tokens",
            lambda text, model=None: len(text or ""),
        )
        big_schema = {"type": "object", "properties": {f"field_{i}": {"type": "string"} for i in range(200)}}
        request_context = ModelRequestContext(
            model=cast(Model, object()),
            messages=[],
            model_settings=None,
            model_request_parameters=ModelRequestParameters(
                function_tools=[
                    ToolDefinition(name="mcp_search", description="d", parameters_json_schema=big_schema),
                    ToolDefinition(name="read_plan", description="d", parameters_json_schema={"type": "object"}),
                ]
            ),
        )
        boundary = ResearchBoundary(
            policy_provider=lambda ctx: _resolved(
                _policy(mode=Mode.DISCUSS, allowed_tool_names=frozenset({"read_plan"}))
            )
        )

        result = await boundary.before_model_request(cast(Any, None), request_context)
        assert [tool.name for tool in result.model_request_parameters.function_tools] == ["read_plan"]

    @pytest.mark.asyncio
    async def test_binary_input_unestimable_blocks(self) -> None:
        # I47 / R21：二进制（图片）无法以文本计量 → 显式不可估，派发前拒绝。
        request_context = ModelRequestContext(
            model=cast(Model, object()),
            messages=[
                ModelRequest(
                    parts=[UserPromptPart(content=[BinaryContent(data=b"\x89PNG\r\n", media_type="image/png")])]
                )
            ],
            model_settings=None,
            model_request_parameters=ModelRequestParameters(),
        )
        boundary = ResearchBoundary(policy_provider=lambda ctx: _resolved(_policy()))

        with pytest.raises(AgentServiceError) as error:
            await boundary.before_model_request(cast(Any, None), request_context)
        assert error.value.code is AgentErrorCode.POLICY_DENIED
        assert "unestimable" in error.value.sanitized_message

    def test_binary_inside_data_capability_part_is_detected(self) -> None:
        """B2：CapabilityPart 家族（含 Harness 压缩回执）内的二进制须递归识别。"""

        import dataclasses

        from pydantic_ai.messages import DataCapabilityPart, UserSource

        @dataclasses.dataclass
        class BinPart(  # type: ignore[reportCallIssue]  # pydantic-ai 能力部件以类关键字参数声明命名空间/标签，类型存根未建模
            DataCapabilityPart, namespace="test_cap", tag="bin"
        ):
            pass

        messages = [
            ModelRequest(
                parts=[
                    UserPromptPart(content="说明"),
                    BinPart(source=UserSource(), content=BinaryContent(data=b"x", media_type="image/png")),
                ]
            )
        ]

        assert _contains_binary_content(messages) is True
        assert _contains_binary_content([ModelRequest(parts=[UserPromptPart(content="纯文本")])]) is False


class TestBoundaryTokenCountingModel:
    """N36：窗口门按冻结连接的实际模型 tokenizer 计数，不按叶模型桥别名。"""

    @pytest.mark.asyncio
    async def test_counts_with_frozen_model_not_bridge_alias(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from services.ai_service import token_budget

        monkeypatch.setattr(token_budget, "_litellm_token_counter_error", False)
        seen: list[str | None] = []

        def _counter(text: object, model: str | None = None) -> int:
            seen.append(model)
            return 1

        monkeypatch.setattr(token_budget, "_estimate_tokens", _counter)
        boundary = ResearchBoundary(policy_provider=lambda ctx: _resolved(_policy(tokenizer_model="openai/gpt-4o")))

        await boundary.before_model_request(cast(Any, None), _context(()))

        assert seen
        assert set(seen) == {"openai/gpt-4o"}
        assert "qtrading:bound" not in seen

    @pytest.mark.asyncio
    async def test_same_window_different_frozen_models_use_own_tokenizer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from services.ai_service import token_budget

        monkeypatch.setattr(token_budget, "_litellm_token_counter_error", False)

        def _counter(text: object, model: str | None = None) -> int:
            if not text:
                return 0
            return 100 if model == "openai/gpt-4o" else 200

        monkeypatch.setattr(token_budget, "_estimate_tokens", _counter)
        request_context = _context(())

        def _boundary(model_ref: str) -> ResearchBoundary:
            return ResearchBoundary(
                policy_provider=lambda ctx: _resolved(
                    _policy(
                        context_window_tokens=300,
                        estimated_input_tokens=None,
                        output_reserve_tokens=0,
                        tokenizer_model=model_ref,
                    )
                )
            )

        # 同一窗口：gpt-4o 计 2×100=200 放行；另一冻结模型计 2×200=400 阻断。
        assert (
            await _boundary("openai/gpt-4o").before_model_request(cast(Any, None), request_context) is request_context
        )
        with pytest.raises(AgentServiceError) as error:
            await _boundary("deepseek/deepseek-chat").before_model_request(cast(Any, None), request_context)
        assert error.value.code is AgentErrorCode.POLICY_DENIED
        assert "measured_basis=verified" in error.value.sanitized_message

    @pytest.mark.asyncio
    async def test_unprovable_tokenizer_marked_as_estimate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from services.ai_service import token_budget

        # tokenizer 不可用 → 回退启发式，计数不得伪装成已核。
        monkeypatch.setattr(token_budget, "_litellm_token_counter_error", True)
        boundary = ResearchBoundary(
            policy_provider=lambda ctx: _resolved(
                _policy(
                    context_window_tokens=1,
                    estimated_input_tokens=None,
                    output_reserve_tokens=0,
                    tokenizer_model="openai/gpt-4o",
                )
            )
        )

        with pytest.raises(AgentServiceError) as error:
            await boundary.before_model_request(cast(Any, None), _context(()))
        assert "measured_basis=estimated" in error.value.sanitized_message

    @pytest.mark.asyncio
    async def test_unknown_model_marked_as_estimate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from services.ai_service import token_budget

        monkeypatch.setattr(token_budget, "_litellm_token_counter_error", False)
        boundary = ResearchBoundary(
            policy_provider=lambda ctx: _resolved(
                _policy(context_window_tokens=1, estimated_input_tokens=None, output_reserve_tokens=0)
            )
        )

        with pytest.raises(AgentServiceError) as error:
            await boundary.before_model_request(cast(Any, None), _context(()))
        assert "measured_basis=estimated" in error.value.sanitized_message

    def test_blank_tokenizer_model_rejected(self) -> None:
        with pytest.raises(ValueError) as error:
            _policy(tokenizer_model="")
        assert "tokenizer_model" in str(error.value)


async def _resolved(policy: ResearchBoundaryPolicy) -> ResearchBoundaryPolicy:
    return policy
