"""F02 回归：生产适配器注册统一为 InferenceAdapter 对象协议（检视 F02）。

缺陷背景：app.bootstrap._build_default_adapters 曾注册裸 async 函数，
而 ScenarioExecutor 以 ``adapter.invoke(...)`` 对象协议调用——授权通过后
触发 AttributeError 并被误判为模型故障重试。

覆盖：
- 生产组合根 _build_default_adapters 三种 kind 全部满足对象协议（可调用 .invoke）；
- 生产组合根 + 真实 ScenarioExecutor + 离线替身传输端到端（三种 kind 各一例），
  不再出现 adapter error: AttributeError；
- 错误形状 adapter（裸函数 / 缺 .invoke 对象）注入 ScenarioExecutor 时组装期
  TypeError 快速失败（非执行期 AttributeError）。
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from typing import Any, Literal, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.bootstrap import _build_default_adapters
from core.model_management_types import (
    AdapterResponse,
    AttemptPermit,
    ExecutionSnapshot,
    InferenceRequest,
    NormalizedLLMUsage,
    PersistenceOutcome,
    ResolvedConnection,
    ValidatedBusinessOutput,
    ensure_inference_adapter,
)
from services.ai_service.scenario_executor import ScenarioExecutor, ScenarioOutput

pytestmark = pytest.mark.unit

_ADAPTER_KINDS = ("litellm_chat", "gguf_chat", "gguf_sentiment")


async def _bare_async_function(
    request: Any, connection: Any, deadline: Any, context: Any
) -> Any:  # pragma: no cover - 形状反例，不被调用
    raise NotImplementedError


class _ObjectWithoutInvoke:
    """缺 .invoke 的普通对象（协议反例形状）。"""


def _response(actual_model: str) -> AdapterResponse:
    return AdapterResponse(
        content="ok",
        usage=NormalizedLLMUsage(
            input_total_tokens=10,
            uncached_input_tokens=10,
            cache_read_tokens=None,
            cache_write_tokens=None,
            output_tokens=5,
        ),
        actual_model=actual_model,
        metadata={},
    )


def _connection(kind: str, connection_id: str = "conn-1") -> ResolvedConnection:
    return ResolvedConnection(
        connection_id=connection_id,
        connection_revision=1,
        adapter_kind=cast(Literal["litellm_chat", "gguf_chat", "gguf_sentiment"], kind),
        provider="zhipu" if kind == "litellm_chat" else "local",
        model="glm-4-air" if kind == "litellm_chat" else "local-gguf",
        api_base=None,
        adapter_options={},
        context_window=8192,
        concurrency_limit=1,
        credential_ref=None,
        credential_version=None,
    )


def _snapshot(kind: str) -> ExecutionSnapshot:
    connection = _connection(kind)
    return ExecutionSnapshot(
        snapshot_id="snap-1",
        run_id=None,
        config_revision=5,
        routes=(
            {
                "scenario_id": "news_sentiment",
                "route_revision": 1,
                "candidates": (
                    {
                        "candidate_index": 0,
                        "task_id": "sentiment",
                        "connection_id": connection.connection_id,
                        "target_fingerprint": "fp-1",
                        "purpose": "sentiment",
                    },
                ),
            },
        ),
        # 快照 connections 契约为 mapping（与生产序列化形态一致），经 asdict 模拟
        connections=(dataclasses.asdict(connection),),
        business_contract_versions={"sentiment": "v1"},
        security_scope_version=1,
        created_at="2026-10-20T00:00:00Z",
    )


def _request() -> InferenceRequest:
    return InferenceRequest(
        scenario_id="news_sentiment",
        task_id="sentiment",
        invocation_id="inv-1",
        payload={"text": "测试新闻"},
        deadline=1_000_000.0,
    )


def _validate_ok(response: AdapterResponse) -> ValidatedBusinessOutput:
    return ValidatedBusinessOutput(output=response.content)


def _executor(adapters: dict[str, Any]) -> ScenarioExecutor:
    config_service = MagicMock()
    config_service.authorize_attempt = AsyncMock(
        return_value=AttemptPermit(
            attempt_id="attempt-1",
            connection_id="conn-1",
            security_revision=1,
            granted_at="2026-10-20T00:00:00Z",
        )
    )
    recorder = MagicMock()
    recorder.observe_usage = MagicMock()
    recorder.mark_dispatched = AsyncMock(return_value=PersistenceOutcome(persisted=True, error_code=None))
    recorder.begin_attempt = AsyncMock(return_value=PersistenceOutcome(persisted=True, error_code=None))
    recorder.submit_terminal = AsyncMock(return_value=PersistenceOutcome(persisted=True, error_code=None))
    compatibility = MagicMock()
    compatibility.resolve_execution_params = MagicMock(
        return_value=SimpleNamespace(compatible=True, resolved_options={})
    )
    return ScenarioExecutor(
        config_service=config_service,
        recorder=recorder,
        compatibility=compatibility,
        adapters=adapters,
    )


@pytest.fixture
def fake_transports(monkeypatch):
    """离线替身传输：只替换适配器惰性解析的底层入口（AIService / LocalModelManager）。

    组合根 _build_default_adapters 与真实 ScenarioExecutor 保持生产原样，
    仅底层传输离线化（检视 F02 回归要求：直接使用生产组合根，只替换底层传输）。
    """
    calls = {"litellm_chat": 0, "gguf_chat": 0, "gguf_sentiment": 0}

    class _FakeLitellmClient:
        async def invoke_single_connection(self, request, resolved_connection, deadline, attempt_context):
            calls["litellm_chat"] += 1
            return _response(resolved_connection.model)

    class _FakeAIService:
        def __init__(self) -> None:
            self._litellm = _FakeLitellmClient()

    class _FakeLocalModelManager:
        async def invoke_for_connection(self, request, resolved_connection, deadline, attempt_context):
            calls[str(resolved_connection.adapter_kind)] += 1
            return _response(resolved_connection.model)

    import services.ai_service as ai_service_pkg
    import services.local_model_manager as lmm_module

    monkeypatch.setattr(ai_service_pkg, "AIService", _FakeAIService)
    monkeypatch.setattr(lmm_module, "LocalModelManager", _FakeLocalModelManager)
    return calls


class TestProductionAdaptersProtocol:
    """生产组合根产出满足 InferenceAdapter 对象协议（F02 核心修复）。"""

    def test_three_kinds_registered_with_invokable_adapters(self):
        adapters = _build_default_adapters()
        assert set(adapters) == set(_ADAPTER_KINDS)
        for kind, adapter in adapters.items():
            invoke = getattr(adapter, "invoke", None)
            assert callable(invoke), f"adapter for {kind} must satisfy InferenceAdapter protocol"

    def test_ensure_rejects_missing_invoke_with_kind_in_message(self):
        with pytest.raises(TypeError, match="gguf_sentiment"):
            ensure_inference_adapter("gguf_sentiment", _ObjectWithoutInvoke())

    def test_ensure_accepts_protocol_conforming_object(self):
        adapters = _build_default_adapters()
        for kind, adapter in adapters.items():
            ensure_inference_adapter(kind, adapter)  # 不抛


class TestProductionAdaptersEndToEnd:
    """生产组合根 + 真实 Executor + 离线替身传输：三种 kind 全链路成功（F02 核心回归）。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", _ADAPTER_KINDS)
    async def test_execute_succeeds_via_production_composition_root(self, fake_transports, kind):
        adapters = _build_default_adapters()
        executor = _executor(adapters)

        result = await executor.execute(_request(), _snapshot(kind), _validate_ok)

        assert result.status == "succeeded"
        assert fake_transports[kind] == 1
        assert all("AttributeError" not in decision.reason for decision in result.candidate_decisions)
        # F03：成功出口唯一生产形态为 ScenarioOutput（业务载荷 + 程序生成溯源，非裸 dict）
        first = result.outputs[0]
        assert isinstance(first, ScenarioOutput)
        assert first.output == {"output": "ok"}
        assert first.metadata["actual_model"]
        assert first.metadata["connection_id"] == "conn-1"


class TestMalformedAdapterRejectedAtAssembly:
    """错误形状 adapter 在组装期 TypeError 快速失败，不再等到执行期 AttributeError。"""

    @pytest.mark.parametrize(
        "bad_adapter",
        [_bare_async_function, _ObjectWithoutInvoke()],
        ids=["bare_async_function", "object_without_invoke"],
    )
    def test_executor_rejects_malformed_adapter(self, bad_adapter):
        with pytest.raises(TypeError, match="InferenceAdapter protocol"):
            _executor({"litellm_chat": bad_adapter})
