"""S3 ScenarioExecutor 单测（模型统一.md §6.2 / ADR-0014 §6.1，方案 D7）。

覆盖：
- 单候选成功链路：authorize → mark_dispatched → 适配器 → validate → succeeded；
- 首候选失败切换第二候选（candidate_decisions 含 switched）；
- 全候选失败 → status=failed；
- 冷却中候选 skipped（30s 冷却，loop-local，R11）；
- validate_output 失败按失败表格切换；
- 同候选重试：默认 1 次（首次 + 1 重试 = 2 次调用）、上限 2 次；
- CancelledError 始终传播（R2），不包装为失败；
- deadline 耗尽停止遍历；
- 有限额费用型在 mark_dispatched persisted=False 时禁止派发（D5）；
- 并发上限：同连接 concurrency_limit=1 时并发 execute 串行；
- F26 连接资格门控：禁用 / 已确认不支持任务的连接派发前淘汰（零调用 +
  rejected + 原因），unknown / 证据缺失不门控；
- F16 父请求与预算预留：invocation 先于候选遍历建立；逐候选 try_reserve →
  mark_dispatched → authorize → begin_attempt 顺序；费用型预留被拒 / 账本
  不可用零出站；usage_only 降级；预留 ID 贯穿 attempt_row / 终态。
"""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

import dataclasses

import pytest

from core.model_management_types import (
    AdapterResponse,
    AttemptContext,
    ExecutionSnapshot,
    InferenceRequest,
    NormalizedLLMUsage,
    ResolvedConnection,
    ValidatedBusinessOutput,
)
from core.model_management_types import PersistenceOutcome
from services.ai_service.scenario_executor import ScenarioExecutor, ScenarioOutput
from services.ai_service.usage_recorder import ReservationOutcome

pytestmark = pytest.mark.unit


def _remote_conn(connection_id: str = "conn-remote", concurrency: int = 4) -> ResolvedConnection:
    return ResolvedConnection(
        connection_id=connection_id,
        connection_revision=1,
        adapter_kind="litellm_chat",
        provider="zhipu",
        model="glm-4-air",
        api_base=None,
        adapter_options={"temperature": 0.2},
        context_window=8192,
        concurrency_limit=concurrency,
        credential_ref="keyring:item",
        credential_version="v1",
    )


def _candidate(index: int, connection_id: str, task_id: str = "sentiment") -> dict:
    return {
        "candidate_index": index,
        "task_id": task_id,
        "connection_id": connection_id,
        "target_fingerprint": "fp-1",
        "purpose": "sentiment",
    }


def _snapshot(candidates: tuple[dict, ...], connections: tuple[ResolvedConnection | dict, ...]) -> ExecutionSnapshot:
    # 快照 connections 契约为 mapping（S2 快照形态），经 asdict 模拟生产序列化形态；
    # F26：允许直接传入携带资格证据（enabled / capabilities）的原始 mapping。
    serialized = tuple(dataclasses.asdict(c) if isinstance(c, ResolvedConnection) else dict(c) for c in connections)
    return ExecutionSnapshot(
        snapshot_id="snap-1",
        run_id=None,
        config_revision=5,
        routes=(
            {
                "scenario_id": "news_sentiment",
                "route_revision": 1,
                "candidates": candidates,
            },
        ),
        connections=serialized,
        business_contract_versions={"sentiment": "v1"},
        security_scope_version=1,
        created_at="2026-10-16T00:00:00Z",
    )


def _request(deadline: float = 1_000_000.0) -> InferenceRequest:
    return InferenceRequest(
        scenario_id="news_sentiment",
        task_id="sentiment",
        invocation_id="inv-1",
        payload={"text": "测试新闻"},
        deadline=deadline,
    )


def _adapter_ok(content: str = "ok", usage: NormalizedLLMUsage | None = None) -> MagicMock:
    adapter = MagicMock()
    adapter.invoke = AsyncMock(
        return_value=AdapterResponse(
            content=content,
            usage=usage
            or NormalizedLLMUsage(
                input_total_tokens=10,
                uncached_input_tokens=10,
                cache_read_tokens=None,
                cache_write_tokens=None,
                output_tokens=5,
            ),
            actual_model="glm-4-air",
            metadata={},
        )
    )
    return adapter


def _adapter_fail_then_ok(failures: int) -> MagicMock:
    adapter = MagicMock()
    calls = {"n": 0}

    async def _invoke(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] <= failures:
            raise RuntimeError("upstream 5xx")
        return AdapterResponse(
            content="ok",
            usage=None,
            actual_model="glm-4-air",
            metadata={},
        )

    adapter.invoke = AsyncMock(side_effect=_invoke)
    adapter._invoke_calls = calls
    return adapter


def _recorder_mock(persisted: bool = True) -> MagicMock:
    recorder = MagicMock()
    recorder.observe_usage = MagicMock()
    recorder.ensure_invocation = AsyncMock(
        return_value=PersistenceOutcome(persisted=persisted, error_code=None if persisted else "DB_UNAVAILABLE")
    )
    recorder.try_reserve = AsyncMock(
        return_value=ReservationOutcome(
            persisted=persisted,
            error_code=None if persisted else "DB_UNAVAILABLE",
            reservation=MagicMock() if persisted else None,
        )
    )
    recorder.mark_dispatched = AsyncMock(
        return_value=PersistenceOutcome(persisted=persisted, error_code=None if persisted else "DB_UNAVAILABLE")
    )
    recorder.begin_attempt = AsyncMock(
        return_value=PersistenceOutcome(persisted=persisted, error_code=None if persisted else "DB_UNAVAILABLE")
    )
    recorder.submit_terminal = AsyncMock(
        return_value=PersistenceOutcome(persisted=persisted, error_code=None if persisted else "DB_UNAVAILABLE")
    )
    return recorder


def _fee_candidate(index: int, connection_id: str, task_id: str = "sentiment") -> dict:
    """费用型候选（F16）：dispatch 冻结的预算身份（月限额 "50.00"，amount 暂 "0.00"）。"""
    return dict(
        _candidate(index, connection_id, task_id),
        budget_amount_cny="0.00",
        budget_limit_cny="50.00",
        budget_policy_snapshot={"monthly_cny_limit": "50.00"},
    )


def _executor(adapter, recorder, *, validate=None, **overrides) -> ScenarioExecutor:
    config_service = MagicMock()
    config_service.authorize_attempt = AsyncMock(
        return_value=MagicMock(attempt_id="att-x", connection_id="conn-remote", security_revision=1, granted_at="t")
    )
    compatibility = MagicMock()
    compatibility.resolve_execution_params = MagicMock(
        return_value=MagicMock(compatible=True, reason=None, resolved_options={"temperature": 0.2})
    )
    kwargs = {
        "config_service": config_service,
        "recorder": recorder,
        "compatibility": compatibility,
        "adapters": {"litellm_chat": adapter},
        "retry_attempts": 1,
        "retry_max_attempts": 2,
        "cooldown_seconds": 30.0,
    }
    kwargs.update(overrides)
    return ScenarioExecutor(**kwargs)


def _validator_ok(response) -> ValidatedBusinessOutput:
    return ValidatedBusinessOutput(output={"label": "中性"}, metadata={})


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_single_candidate_success(self):
        adapter = _adapter_ok()
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder, validate=_validator_ok)
        result = await executor.execute(
            _request(), _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)), _validator_ok
        )
        assert result.status == "succeeded"
        assert len(result.outputs) == 1
        # F03：成功出口唯一生产形态为 ScenarioOutput（Mapping 兼容 + 属性协议）
        first = result.outputs[0]
        assert isinstance(first, ScenarioOutput)
        assert first.output == {"label": "中性"}
        assert dict(first) == {"output": {"label": "中性"}, "metadata": first.metadata}
        # Mapping 视图仅两键：未知键显式 KeyError（不伪装缺失，R21 邻域语义）
        with pytest.raises(KeyError) as exc_info:
            first["unknown_key"]
        assert exc_info.value.args[0] == "unknown_key"
        # F03：程序生成溯源保留（模型身份 / 连接身份 / 许可安全修订），模型输出不可伪造
        assert first.metadata["actual_model"] == "glm-4-air"
        assert first.metadata["connection_id"] == "conn-remote"
        assert first.metadata["configured_model"] == "glm-4-air"
        assert first.metadata["scenario_id"] == "news_sentiment"
        assert first.metadata["security_revision"] == 1
        config_service = executor._config_service
        config_service.authorize_attempt.assert_awaited_once()
        # F06：许可请求绑定快照冻结的实际执行凭据版本（非磁盘现值）
        # F01：目标 / 用途取候选携带的分派授权身份；远程连接要求 egress grant
        authorize_args = config_service.authorize_attempt.await_args.args
        assert authorize_args[0] == "conn-remote"
        assert authorize_args[1] == "fp-1"
        assert authorize_args[2] == "sentiment"
        assert authorize_args[4] == "v1"
        assert config_service.authorize_attempt.await_args.kwargs["requires_egress_grant"] is True
        # observe 在适配器返回后被同步转移（D6）：校验观察内容归属性与用量来源
        assert recorder.observe_usage.call_count == 1
        obs_arg = recorder.observe_usage.call_args.args[0]
        assert obs_arg.invocation_id == "inv-1"
        assert obs_arg.usage is not None and obs_arg.usage.input_total_tokens == 10

    @pytest.mark.asyncio
    async def test_success_output_merges_validator_metadata_and_provenance(self):
        """F03：校验器 metadata（模型身份等）不丢，Executor 程序溯源为权威（同键覆盖）。"""
        adapter = _adapter_ok()
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder, validate=_validator_ok)
        validator = MagicMock(
            return_value=ValidatedBusinessOutput(
                output={"label": "利好"}, metadata={"actual_model": "glm-4-air", "adapter_extra": "x"}
            )
        )
        result = await executor.execute(
            _request(), _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)), validator
        )
        first = result.outputs[0]
        assert isinstance(first, ScenarioOutput)
        # 校验器独有键保留；Executor 溯源键存在；重叠键（actual_model）同源一致
        assert first.metadata["adapter_extra"] == "x"
        assert first.metadata["actual_model"] == "glm-4-air"
        assert first.metadata["connection_id"] == "conn-remote"
        assert "security_revision" in first.metadata
        assert "run_id" in first.metadata

    @pytest.mark.asyncio
    async def test_local_connection_skips_egress_grant_requirement(self):
        """F01：本地 gguf 连接不外发，authorize 按真实执行位置声明免 egress grant。"""
        adapter = _adapter_ok()
        recorder = _recorder_mock()
        executor = _executor(
            adapter, recorder, validate=_validator_ok, adapters={"litellm_chat": adapter, "gguf_chat": adapter}
        )
        local_conn = replace(_remote_conn("conn-local"), adapter_kind="gguf_chat")
        result = await executor.execute(
            _request(),
            _snapshot((_candidate(0, "conn-local"),), (local_conn,)),
            _validator_ok,
        )
        assert result.status == "succeeded"
        kwargs = executor._config_service.authorize_attempt.await_args.kwargs
        assert kwargs["requires_egress_grant"] is False

    @pytest.mark.asyncio
    async def test_observation_security_revision_from_permit(self):
        """F01：落账 security_revision 取许可线性化点修订，不再混用快照 scope 版本。"""
        adapter = _adapter_ok()
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder, validate=_validator_ok)
        executor._config_service.authorize_attempt = AsyncMock(
            return_value=MagicMock(attempt_id="att-x", security_revision=41, granted_at="t")
        )
        await executor.execute(_request(), _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)), _validator_ok)
        obs_arg = recorder.observe_usage.call_args.args[0]
        assert obs_arg.metadata["security_revision"] == 41

    @pytest.mark.asyncio
    async def test_authorize_failure_rejects_candidate(self):
        adapter = _adapter_ok()
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder, validate=_validator_ok)
        executor._config_service.authorize_attempt = AsyncMock(side_effect=PermissionError("revoked"))
        result = await executor.execute(
            _request(), _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)), _validator_ok
        )
        assert result.status == "failed"
        adapter.invoke.assert_not_awaited()
        decisions = result.candidate_decisions
        assert any(d.decision == "rejected" for d in decisions)


class TestFailover:
    @pytest.mark.asyncio
    async def test_switch_to_second_candidate_on_failure(self):
        adapter_fail = _adapter_fail_then_ok(failures=99)  # 永远失败
        adapter_ok = _adapter_ok()
        recorder = _recorder_mock()
        executor = _executor(
            adapter_fail,
            recorder,
            validate=_validator_ok,
            adapters={"litellm_chat": adapter_fail, "gguf_chat": adapter_ok},
        )
        local_conn = replace(_remote_conn("conn-local"), adapter_kind="gguf_chat")
        result = await executor.execute(
            _request(),
            _snapshot(
                (_candidate(0, "conn-remote"), _candidate(1, "conn-local")),
                (_remote_conn(), local_conn),
            ),
            _validator_ok,
        )
        assert result.status == "succeeded"
        assert any(d.decision == "switched" for d in result.candidate_decisions)

    @pytest.mark.asyncio
    async def test_all_candidates_failed(self):
        adapter = _adapter_fail_then_ok(failures=99)
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder, validate=_validator_ok)
        result = await executor.execute(
            _request(),
            _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)),
            _validator_ok,
        )
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_validate_output_failure_switches_candidate(self):
        adapter = _adapter_ok()
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder, validate=_validator_ok)

        def _validate_fail(response) -> ValidatedBusinessOutput:
            from core.model_management_types import ModelOutputValidationError

            raise ModelOutputValidationError("bad json")

        result = await executor.execute(
            _request(),
            _snapshot(
                (_candidate(0, "conn-remote"), _candidate(1, "conn-b")),
                (_remote_conn(), _remote_conn("conn-b")),
            ),
            _validate_fail,
        )
        assert result.status == "failed" or result.status == "partial"
        decisions = [d for d in result.candidate_decisions if d.decision != "attempted"]
        assert any("output" in d.reason or d.decision == "switched" for d in decisions)

    @pytest.mark.asyncio
    async def test_retry_default_one_within_candidate(self):
        adapter = _adapter_fail_then_ok(failures=1)
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder, validate=_validator_ok)
        result = await executor.execute(
            _request(), _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)), _validator_ok
        )
        assert result.status == "succeeded"
        assert adapter._invoke_calls["n"] == 2  # 首次 + 默认 1 次重试

    @pytest.mark.asyncio
    async def test_retry_upper_bound_two(self):
        adapter = _adapter_fail_then_ok(failures=99)
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder, validate=_validator_ok, retry_attempts=2)
        await executor.execute(_request(), _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)), _validator_ok)
        assert adapter._invoke_calls["n"] == 3  # 首次 + 2 次重试


class TestGuards:
    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self):
        adapter = MagicMock()

        async def _cancel(*args, **kwargs):
            raise asyncio.CancelledError()

        adapter.invoke = AsyncMock(side_effect=_cancel)
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder, validate=_validator_ok)
        with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion R2 断言目标即异常类型本身（取消必须裸传播不被包装为失败）
            await executor.execute(
                _request(), _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)), _validator_ok
            )
        # 取消路径不得把该 attempt 落为失败终态（CancelledError 非业务失败）
        recorder.submit_terminal.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_deadline_exhaustion_stops(self):
        adapter = _adapter_fail_then_ok(failures=99)
        recorder = _recorder_mock()
        loop = asyncio.get_running_loop()
        executor = _executor(adapter, recorder, validate=_validator_ok)
        result = await executor.execute(
            _request(deadline=loop.time() - 1.0),
            _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)),
            _validator_ok,
        )
        assert result.status == "failed"
        adapter.invoke.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cooldown_skips_candidate(self):
        adapter = _adapter_fail_then_ok(failures=99)
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder, validate=_validator_ok)
        # 第一轮把 conn-remote 打入冷却
        await executor.execute(_request(), _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)), _validator_ok)
        # 第二轮：同连接仍在冷却 → skipped，不再 invoke
        adapter2_calls = adapter._invoke_calls["n"]
        result = await executor.execute(
            _request(), _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)), _validator_ok
        )
        assert result.status == "failed"
        assert adapter._invoke_calls["n"] == adapter2_calls
        decisions = result.candidate_decisions
        assert any(d.decision == "skipped" and "cooldown" in d.reason for d in decisions)

    @pytest.mark.asyncio
    async def test_budget_dispatch_forbidden_when_persistence_lost(self):
        """D5：费用型在预留成功后 mark_dispatched persisted=False 时禁止派发（零出站）。"""
        adapter = _adapter_ok()
        recorder = _recorder_mock()
        recorder.mark_dispatched = AsyncMock(
            return_value=PersistenceOutcome(persisted=False, error_code="DB_UNAVAILABLE")
        )
        executor = _executor(adapter, recorder, validate=_validator_ok)
        result = await executor.execute(
            _request(),
            _snapshot((_fee_candidate(0, "conn-remote"),), (_remote_conn(),)),
            _validator_ok,
        )
        adapter.invoke.assert_not_awaited()
        assert result.status == "failed"
        decision = result.candidate_decisions[0]
        assert decision.decision == "rejected"
        assert "budget dispatch forbidden" in decision.reason

    @pytest.mark.asyncio
    async def test_usage_only_persistence_lost_still_dispatches(self):
        """D5：usage_only（无预留金额）在 persisted=False 时允许进程内一次性派发。"""
        adapter = _adapter_ok()
        recorder = _recorder_mock(persisted=False)
        executor = _executor(adapter, recorder, validate=_validator_ok)
        result = await executor.execute(
            _request(), _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)), _validator_ok
        )
        adapter.invoke.assert_awaited_once()
        assert result.status == "succeeded"

    @pytest.mark.asyncio
    async def test_concurrency_limit_serializes_same_connection(self):
        adapter = MagicMock()
        inflight = {"n": 0, "max": 0}

        async def _invoke(*args, **kwargs):
            inflight["n"] += 1
            inflight["max"] = max(inflight["max"], inflight["n"])
            await asyncio.sleep(0.01)
            inflight["n"] -= 1
            return AdapterResponse(content="ok", usage=None, actual_model="m", metadata={})

        adapter.invoke = AsyncMock(side_effect=_invoke)
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder, validate=_validator_ok)
        conn = _remote_conn(concurrency=1)
        await asyncio.gather(
            executor.execute(_request(), _snapshot((_candidate(0, "conn-remote"),), (conn,)), _validator_ok),
            executor.execute(_request(), _snapshot((_candidate(0, "conn-remote"),), (conn,)), _validator_ok),
        )
        assert inflight["max"] == 1


class TestExecutionOrder:
    """方案 §6.2 步骤 3/4 固定执行序：许可 → 派发绑定 → 逐次授权 → 建 attempt 行 → 执行。"""

    @pytest.mark.asyncio
    async def test_mark_dispatched_precedes_authorize(self):
        """mark_dispatched（绑定派发身份）先于逐次 authorize_attempt。"""
        adapter = _adapter_ok()
        recorder = _recorder_mock()
        order: list[str] = []

        async def _track_mark(*args, **kwargs):
            order.append("mark_dispatched")
            return PersistenceOutcome(persisted=True, error_code=None)

        async def _track_authorize(*args, **kwargs):
            order.append("authorize_attempt")
            return MagicMock(attempt_id="att-x", security_revision=1, granted_at="t")

        async def _track_begin(*args, **kwargs):
            order.append("begin_attempt")
            return PersistenceOutcome(persisted=True, error_code=None)

        recorder.mark_dispatched = AsyncMock(side_effect=_track_mark)
        recorder.begin_attempt = AsyncMock(side_effect=_track_begin)
        executor = _executor(adapter, recorder, validate=_validator_ok)
        executor._config_service.authorize_attempt = AsyncMock(side_effect=_track_authorize)
        await executor.execute(_request(), _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)), _validator_ok)
        assert order.index("mark_dispatched") < order.index("authorize_attempt") < order.index("begin_attempt")

    @pytest.mark.asyncio
    async def test_begin_attempt_precedes_adapter_invoke(self):
        """实际尝试记录建立于真正进入适配器执行之前（§6.2 步骤 4）。"""
        adapter = _adapter_ok()
        recorder = _recorder_mock()
        order: list[str] = []

        async def _track_begin(*args, **kwargs):
            order.append("begin_attempt")
            return PersistenceOutcome(persisted=True, error_code=None)

        async def _invoke(*args, **kwargs):
            order.append("invoke")
            return AdapterResponse(content="ok", usage=None, actual_model="m", metadata={})

        recorder.begin_attempt = AsyncMock(side_effect=_track_begin)
        adapter.invoke = AsyncMock(side_effect=_invoke)
        executor = _executor(adapter, recorder, validate=_validator_ok)
        await executor.execute(_request(), _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)), _validator_ok)
        assert order == ["begin_attempt", "invoke"]

    @pytest.mark.asyncio
    async def test_failed_candidate_submits_terminal(self):
        """失败候选收敛错误提交终态（§6.2 步骤 5）：适配器重试耗尽 → submit_terminal(failed)。"""
        adapter = _adapter_always_fail()
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder, validate=_validator_ok)
        result = await executor.execute(
            _request(), _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)), _validator_ok
        )
        assert result.status == "failed"
        terminal_call = recorder.submit_terminal.await_args
        assert terminal_call is not None
        record = terminal_call.args[0]
        assert record.status == "failed"
        assert record.usage is None
        assert record.error_code is not None


def _adapter_always_fail() -> MagicMock:
    adapter = MagicMock()

    async def _invoke(*args, **kwargs):
        raise RuntimeError("upstream 5xx")

    adapter.invoke = AsyncMock(side_effect=_invoke)
    return adapter


def _raw_connection(
    connection_id: str = "conn-remote",
    *,
    enabled: bool = True,
    capabilities: dict | None = None,
) -> dict:
    """F26：携带资格证据的快照连接 mapping（S2 生产序列化形态 + 资格字段）。"""
    raw = dataclasses.asdict(_remote_conn(connection_id))
    raw["enabled"] = enabled
    if capabilities is not None:
        raw["capabilities"] = capabilities
    return raw


class TestEligibilityGate:
    """F26 连接资格门控：派发前按冻结快照证据淘汰禁用 / 已确认不支持任务的连接。

    回归基线（检视 P03）：在放行许可替身（authorize 全放行）下，真实 Executor
    不得调用 disabled+unsupported 连接。
    """

    @pytest.mark.asyncio
    async def test_disabled_connection_not_dispatched(self):
        """禁用连接零调用：不授权、不绑定派发身份、不执行，决策记录 rejected + 原因。"""
        adapter = _adapter_ok()
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder, validate=_validator_ok)
        result = await executor.execute(
            _request(),
            _snapshot((_candidate(0, "conn-remote"),), (_raw_connection(enabled=False),)),
            _validator_ok,
        )
        adapter.invoke.assert_not_awaited()
        executor._config_service.authorize_attempt.assert_not_awaited()
        recorder.mark_dispatched.assert_not_awaited()
        assert result.status == "failed"
        assert not result.outputs
        assert len(result.candidate_decisions) == 1
        decision = result.candidate_decisions[0]
        assert decision.decision == "rejected"
        assert decision.reason == "connection disabled in snapshot"
        assert decision.connection_id == "conn-remote"

    @pytest.mark.asyncio
    async def test_unsupported_task_not_dispatched(self):
        """已确认不支持该任务的连接零调用，原因随决策记录（R21 不静默）。"""
        adapter = _adapter_ok()
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder, validate=_validator_ok)
        result = await executor.execute(
            _request(),
            _snapshot(
                (_candidate(0, "conn-remote"),),
                (_raw_connection(capabilities={"sentiment": {"support": "unsupported", "source": "validation"}}),),
            ),
            _validator_ok,
        )
        adapter.invoke.assert_not_awaited()
        recorder.mark_dispatched.assert_not_awaited()
        assert result.status == "failed"
        decision = result.candidate_decisions[0]
        assert decision.decision == "rejected"
        assert decision.reason == "connection unsupported for task sentiment"

    @pytest.mark.asyncio
    async def test_unknown_capability_still_dispatched(self):
        """unknown 状态不门控（既有确认政策）：连接照常派发执行成功。"""
        adapter = _adapter_ok()
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder, validate=_validator_ok)
        result = await executor.execute(
            _request(),
            _snapshot(
                (_candidate(0, "conn-remote"),),
                (_raw_connection(capabilities={"sentiment": {"support": "unknown", "source": "user_declaration"}}),),
            ),
            _validator_ok,
        )
        adapter.invoke.assert_awaited_once()
        assert result.status == "succeeded"

    @pytest.mark.asyncio
    async def test_other_task_unsupported_does_not_gate(self):
        """任务能力按请求任务核对：其他任务 unsupported 不影响本任务派发。"""
        adapter = _adapter_ok()
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder, validate=_validator_ok)
        result = await executor.execute(
            _request(),
            _snapshot(
                (_candidate(0, "conn-remote"),),
                (_raw_connection(capabilities={"analysis": {"support": "unsupported"}}),),
            ),
            _validator_ok,
        )
        adapter.invoke.assert_awaited_once()
        assert result.status == "succeeded"

    @pytest.mark.asyncio
    async def test_malformed_capability_evidence_ignored(self):
        """畸形证据（非 Mapping 证据 / 缺 support 键）按无证据处理，不门控不伪装。"""
        adapter = _adapter_ok()
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder, validate=_validator_ok)
        result = await executor.execute(
            _request(),
            _snapshot(
                (_candidate(0, "conn-remote"),),
                (_raw_connection(capabilities={"sentiment": "unsupported", "topic": {"source": "validation"}}),),
            ),
            _validator_ok,
        )
        adapter.invoke.assert_awaited_once()
        assert result.status == "succeeded"

    @pytest.mark.asyncio
    async def test_ineligible_first_candidate_switches_to_next(self):
        """首候选禁用被拒后切换下一候选：rejected 与 succeeded 决策并存。"""
        adapter = _adapter_ok()
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder, validate=_validator_ok)
        result = await executor.execute(
            _request(),
            _snapshot(
                (_candidate(0, "conn-disabled"), _candidate(1, "conn-remote")),
                (_raw_connection("conn-disabled", enabled=False), _raw_connection()),
            ),
            _validator_ok,
        )
        assert result.status == "succeeded"
        decisions = result.candidate_decisions
        assert [d.decision for d in decisions] == ["rejected", "succeeded"]
        assert decisions[0].reason == "connection disabled in snapshot"

    @pytest.mark.asyncio
    async def test_connection_without_evidence_fields_unchanged(self):
        """无资格字段的连接（存量快照形态）行为不变：enabled 缺键按启用不门控。"""
        adapter = _adapter_ok()
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder, validate=_validator_ok)
        result = await executor.execute(
            _request(), _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)), _validator_ok
        )
        adapter.invoke.assert_awaited_once()
        assert result.status == "succeeded"


class TestInvocationAndReservation:
    """F16 父请求建立与逐实际尝试原子预留：调用面、顺序、预算门零出站、身份贯穿。"""

    @pytest.mark.asyncio
    async def test_invocation_established_first_with_owner_identity(self):
        """invocation 先于候选遍历建立（零尝试请求亦可查），owner 身份齐传。"""
        adapter = _adapter_ok()
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder, validate=_validator_ok)
        await executor.execute(
            _request(),
            _snapshot((_fee_candidate(0, "conn-remote"),), (_remote_conn(),)),
            _validator_ok,
        )
        recorder.ensure_invocation.assert_awaited_once()
        kwargs = recorder.ensure_invocation.await_args.kwargs
        assert kwargs["invocation_id"] == "inv-1"
        assert kwargs["scenario_id"] == "news_sentiment"
        assert kwargs["trigger"] == "manual"
        # F23 fencing 身份齐传：owner 会话非空、epoch 正数、租约为未来时刻
        assert kwargs["owner_session_id"]
        assert kwargs["owner_epoch"] >= 1
        assert kwargs["owner_lease_expires_at"] is not None
        # 预算政策快照取候选冻结值（dispatch 冻结语义）
        assert kwargs["budget_policy_snapshot"] == {"monthly_cny_limit": "50.00"}
        # 顺序：ensure_invocation 先于首个候选的 try_reserve
        calls = [c[0] for c in recorder.mock_calls]
        assert calls.index("ensure_invocation") < calls.index("try_reserve")

    @pytest.mark.asyncio
    async def test_invocation_established_with_empty_candidates(self):
        """零候选请求同样建立父请求（失败也有记录，R21），无预留发生。"""
        adapter = _adapter_ok()
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder, validate=_validator_ok)
        result = await executor.execute(_request(), _snapshot((), (_remote_conn(),)), _validator_ok)
        assert result.status == "failed"
        recorder.ensure_invocation.assert_awaited_once()
        assert recorder.ensure_invocation.await_args.kwargs["budget_policy_snapshot"] is None
        recorder.try_reserve.assert_not_awaited()
        adapter.invoke.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reserve_precedes_dispatch_binding_with_owner(self):
        """try_reserve → mark_dispatched 顺序；派发绑定携带预留 ID 与 owner 齐传（F23 强门）。"""
        adapter = _adapter_ok()
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder, validate=_validator_ok)
        await executor.execute(
            _request(),
            _snapshot((_fee_candidate(0, "conn-remote"),), (_remote_conn(),)),
            _validator_ok,
        )
        calls = [c[0] for c in recorder.mock_calls]
        assert calls.index("try_reserve") < calls.index("mark_dispatched")
        reserve_kwargs = recorder.try_reserve.await_args.kwargs
        assert reserve_kwargs["invocation_id"] == "inv-1"
        assert reserve_kwargs["amount_cny"] == "0.00"
        assert reserve_kwargs["budget_limit_cny"] == "50.00"
        assert reserve_kwargs["owner_session_id"]
        assert reserve_kwargs["owner_epoch"] >= 1
        dispatch_kwargs = recorder.mark_dispatched.await_args.kwargs
        assert dispatch_kwargs["reservation_id"] is not None
        assert dispatch_kwargs["owner_session_id"] == reserve_kwargs["owner_session_id"]
        assert dispatch_kwargs["owner_epoch"] == reserve_kwargs["owner_epoch"]

    @pytest.mark.asyncio
    async def test_fee_reserve_rejected_zero_egress(self):
        """费用型预留被拒（余额不足 / owner 失权）→ rejected + break，零出站。"""
        adapter = _adapter_ok()
        recorder = _recorder_mock()
        recorder.try_reserve = AsyncMock(
            return_value=ReservationOutcome(persisted=True, error_code="RESERVE_REJECTED", reservation=None)
        )
        executor = _executor(adapter, recorder, validate=_validator_ok)
        result = await executor.execute(
            _request(),
            _snapshot(
                (_fee_candidate(0, "conn-remote"), _candidate(1, "conn-b")), (_remote_conn(), _remote_conn("conn-b"))
            ),
            _validator_ok,
        )
        adapter.invoke.assert_not_awaited()
        recorder.mark_dispatched.assert_not_awaited()
        recorder.begin_attempt.assert_not_awaited()
        recorder.try_reserve.assert_awaited_once()
        assert result.status == "failed"
        decision = result.candidate_decisions[0]
        assert decision.decision == "rejected"
        assert "budget reserve rejected" in decision.reason
        assert "RESERVE_REJECTED" in decision.reason

    @pytest.mark.asyncio
    async def test_fee_reserve_db_unavailable_zero_egress(self):
        """费用型账本不可用（try_reserve persisted=False）→ rejected + break，零出站。"""
        adapter = _adapter_ok()
        recorder = _recorder_mock()
        recorder.try_reserve = AsyncMock(
            return_value=ReservationOutcome(persisted=False, error_code="DB_UNAVAILABLE", reservation=None)
        )
        executor = _executor(adapter, recorder, validate=_validator_ok)
        result = await executor.execute(
            _request(),
            _snapshot((_fee_candidate(0, "conn-remote"),), (_remote_conn(),)),
            _validator_ok,
        )
        adapter.invoke.assert_not_awaited()
        recorder.mark_dispatched.assert_not_awaited()
        assert result.status == "failed"
        assert "budget reserve rejected: DB_UNAVAILABLE" in result.candidate_decisions[0].reason

    @pytest.mark.asyncio
    async def test_usage_only_degrades_when_reserve_unavailable(self):
        """usage_only 预留不可用 → 降级进程内派发身份（reservation_id=None）执行成功。"""
        adapter = _adapter_ok()
        recorder = _recorder_mock()
        recorder.try_reserve = AsyncMock(
            return_value=ReservationOutcome(persisted=False, error_code="DB_UNAVAILABLE", reservation=None)
        )
        executor = _executor(adapter, recorder, validate=_validator_ok)
        result = await executor.execute(
            _request(), _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)), _validator_ok
        )
        adapter.invoke.assert_awaited_once()
        assert result.status == "succeeded"
        # 降级派发：mark_dispatched 以 None 预留 + owner 齐传
        dispatch_kwargs = recorder.mark_dispatched.await_args.kwargs
        assert dispatch_kwargs["reservation_id"] is None
        assert dispatch_kwargs["owner_session_id"]

    @pytest.mark.asyncio
    async def test_reservation_id_flows_through_attempt_row_and_terminal(self):
        """费用型成功链路：预留 ID 贯穿 mark_dispatched → attempt_row → 终态（F22/F23）。"""
        adapter = _adapter_ok()
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder, validate=_validator_ok)
        result = await executor.execute(
            _request(),
            _snapshot((_fee_candidate(0, "conn-remote"),), (_remote_conn(),)),
            _validator_ok,
        )
        assert result.status == "succeeded"
        dispatched_id = recorder.mark_dispatched.await_args.kwargs["reservation_id"]
        assert dispatched_id is not None
        attempt_row = recorder.begin_attempt.await_args.kwargs["attempt_row"]
        assert attempt_row["reservation_id"] == dispatched_id
        # begin_attempt owner 齐传（F23 强门语义）
        assert recorder.begin_attempt.await_args.kwargs["owner_session_id"]
        assert recorder.begin_attempt.await_args.kwargs["owner_epoch"] >= 1
        terminal_record = recorder.submit_terminal.await_args.args[0]
        assert terminal_record.reservation_id == dispatched_id

    @pytest.mark.asyncio
    async def test_degraded_dispatch_terminal_carries_no_reservation(self):
        """usage_only 预留不可用降级派发：终态 / attempt_row 预留 ID 为 None（无预留不伪装，R21）。"""
        adapter = _adapter_ok()
        recorder = _recorder_mock()
        recorder.try_reserve = AsyncMock(
            return_value=ReservationOutcome(persisted=False, error_code="DB_UNAVAILABLE", reservation=None)
        )
        executor = _executor(adapter, recorder, validate=_validator_ok)
        result = await executor.execute(
            _request(), _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)), _validator_ok
        )
        assert result.status == "succeeded"
        terminal_record = recorder.submit_terminal.await_args.args[0]
        assert terminal_record.reservation_id is None
        attempt_row = recorder.begin_attempt.await_args.kwargs["attempt_row"]
        assert attempt_row["reservation_id"] is None

    @pytest.mark.asyncio
    async def test_usage_only_reservation_id_flows_through_terminal(self):
        """usage_only 预留成功（NULL 金额预留）：预留 ID 同样贯穿终态（全链路可追溯）。"""
        adapter = _adapter_ok()
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder, validate=_validator_ok)
        result = await executor.execute(
            _request(), _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)), _validator_ok
        )
        assert result.status == "succeeded"
        dispatched_id = recorder.mark_dispatched.await_args.kwargs["reservation_id"]
        assert dispatched_id is not None
        terminal_record = recorder.submit_terminal.await_args.args[0]
        assert terminal_record.reservation_id == dispatched_id

    @pytest.mark.asyncio
    async def test_authorize_failure_with_reservation_converges_via_terminal(self):
        """预留绑定后 authorize 失败：提交 failed 终态携带预留 ID（F23 背书收敛，不留悬挂）。"""
        adapter = _adapter_ok()
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder, validate=_validator_ok)
        executor._config_service.authorize_attempt = AsyncMock(side_effect=PermissionError("revoked"))
        result = await executor.execute(
            _request(),
            _snapshot((_fee_candidate(0, "conn-remote"),), (_remote_conn(),)),
            _validator_ok,
        )
        assert result.status == "failed"
        recorder.mark_dispatched.assert_awaited_once()
        dispatched_id = recorder.mark_dispatched.await_args.kwargs["reservation_id"]
        assert dispatched_id is not None
        recorder.submit_terminal.assert_awaited_once()
        terminal_record = recorder.submit_terminal.await_args.args[0]
        assert terminal_record.status == "failed"
        assert terminal_record.reservation_id == dispatched_id
        adapter.invoke.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_authorize_failure_after_degraded_dispatch_no_terminal(self):
        """usage_only 降级派发（无预留）authorize 失败：无 attempt 行不补记，零副作用。"""
        adapter = _adapter_ok()
        recorder = _recorder_mock()
        recorder.try_reserve = AsyncMock(
            return_value=ReservationOutcome(persisted=False, error_code="DB_UNAVAILABLE", reservation=None)
        )
        executor = _executor(adapter, recorder, validate=_validator_ok)
        executor._config_service.authorize_attempt = AsyncMock(side_effect=PermissionError("revoked"))
        result = await executor.execute(
            _request(), _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)), _validator_ok
        )
        assert result.status == "failed"
        recorder.submit_terminal.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_authorize_failure_usage_only_with_reservation_converges(self):
        """usage_only 预留成功后 authorize 失败：同样经活跃预留背书终态收敛（F23）。"""
        adapter = _adapter_ok()
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder, validate=_validator_ok)
        executor._config_service.authorize_attempt = AsyncMock(side_effect=PermissionError("revoked"))
        result = await executor.execute(
            _request(), _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)), _validator_ok
        )
        assert result.status == "failed"
        recorder.submit_terminal.assert_awaited_once()
        terminal_record = recorder.submit_terminal.await_args.args[0]
        assert terminal_record.reservation_id is not None

    @pytest.mark.asyncio
    async def test_adapter_kind_mismatch_rejected_before_reserve(self):
        """连接 adapter_kind 无适配器：rejected 决策且零预留 / 零派发。"""
        adapter = _adapter_ok()
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder, validate=_validator_ok, adapters={"gguf_chat": adapter})
        result = await executor.execute(
            _request(), _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)), _validator_ok
        )
        adapter.invoke.assert_not_awaited()
        recorder.try_reserve.assert_not_awaited()
        recorder.mark_dispatched.assert_not_awaited()
        assert result.status == "failed"
        decision = result.candidate_decisions[0]
        assert decision.decision == "rejected"
        assert decision.reason == "no adapter for kind litellm_chat"


def _local_conn(connection_id: str = "conn-local") -> dict:
    """R01-D1：设备内本地 kind（gguf_chat）快照连接 mapping（不外发，免 grant）。"""
    raw = dataclasses.asdict(_remote_conn(connection_id))
    raw["adapter_kind"] = "gguf_chat"
    raw["provider"] = "local"
    raw["api_base"] = None
    return raw


class TestLocalOnlyGate:
    """R01-D1 local-only 政策门控：仅本地模式下需外发候选（adapter_kind_requires_egress）
    派发前拒绝（零授权 / 零派发 / 零外发），本地 kind（gguf_*）照常执行；
    local_only=False 行为与现状完全一致；配置切换下一次 execute 即时生效。
    """

    @pytest.mark.asyncio
    async def test_local_only_rejects_egress_candidate(self):
        """local_only=True：litellm_chat 候选零调用（不授权 / 不派发 / 不执行），
        决策记录 rejected + policy 原因（R21 不静默）。"""
        adapter = _adapter_ok()
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder, validate=_validator_ok, local_only_mode_reader=lambda: True)
        result = await executor.execute(
            _request(), _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)), _validator_ok
        )
        adapter.invoke.assert_not_awaited()
        executor._config_service.authorize_attempt.assert_not_awaited()
        recorder.mark_dispatched.assert_not_awaited()
        assert result.status == "failed"
        assert not result.outputs
        assert len(result.candidate_decisions) == 1
        decision = result.candidate_decisions[0]
        assert decision.decision == "rejected"
        assert "policy" in decision.reason
        assert decision.connection_id == "conn-remote"

    @pytest.mark.asyncio
    async def test_local_only_egress_rejected_then_local_kind_succeeds(self):
        """local_only=True：混合候选下 litellm_chat 被拒后 gguf_chat 照常走完授权链
        （本地 kind 免 grant 语义保留，F01），rejected 与 succeeded 决策并存。"""
        remote_adapter = _adapter_ok()
        local_adapter = _adapter_ok()
        recorder = _recorder_mock()
        executor = _executor(
            remote_adapter,
            recorder,
            validate=_validator_ok,
            adapters={"litellm_chat": remote_adapter, "gguf_chat": local_adapter},
            local_only_mode_reader=lambda: True,
        )
        result = await executor.execute(
            _request(),
            _snapshot(
                (_candidate(0, "conn-remote"), _candidate(1, "conn-local")),
                (_remote_conn(), _local_conn()),
            ),
            _validator_ok,
        )
        remote_adapter.invoke.assert_not_awaited()
        local_adapter.invoke.assert_awaited_once()
        assert result.status == "succeeded"
        decisions = result.candidate_decisions
        assert [d.decision for d in decisions] == ["rejected", "succeeded"]
        assert "policy" in decisions[0].reason
        # F01：本地 kind 不要求 egress grant（禁外发经候选层淘汰实现，非 grant 语义）
        authorize_kwargs = executor._config_service.authorize_attempt.await_args.kwargs
        assert authorize_kwargs["requires_egress_grant"] is False

    @pytest.mark.asyncio
    async def test_local_only_false_zero_regression(self):
        """local_only=False：litellm_chat 候选照常授权并执行成功（零回归）。"""
        adapter = _adapter_ok()
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder, validate=_validator_ok, local_only_mode_reader=lambda: False)
        result = await executor.execute(
            _request(), _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)), _validator_ok
        )
        adapter.invoke.assert_awaited_once()
        assert result.status == "succeeded"
        authorize_kwargs = executor._config_service.authorize_attempt.await_args.kwargs
        assert authorize_kwargs["requires_egress_grant"] is True

    @pytest.mark.asyncio
    async def test_local_only_config_switch_takes_effect_next_execute(self):
        """配置切换即时生效：同 executor 两次 execute 之间政策翻转，第二次按新值判定。"""
        adapter = _adapter_ok()
        recorder = _recorder_mock()
        reader_state = {"local_only": True}
        executor = _executor(
            adapter, recorder, validate=_validator_ok, local_only_mode_reader=lambda: reader_state["local_only"]
        )
        snapshot = _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),))
        request = _request()

        first = await executor.execute(request, snapshot, _validator_ok)
        assert first.status == "failed"
        adapter.invoke.assert_not_awaited()

        reader_state["local_only"] = False
        second = await executor.execute(request, snapshot, _validator_ok)
        assert second.status == "succeeded"
        adapter.invoke.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_default_reader_reads_config_without_caching(self):
        """默认读取器经 ConfigHandler 新鲜读（R01-D1）：每次调用即时反映配置值。"""
        from services.ai_service.scenario_executor import _default_local_only_mode_reader

        with patch("utils.config_handler.ConfigHandler.is_ai_local_only_mode", return_value=True):
            assert _default_local_only_mode_reader() is True
        with patch("utils.config_handler.ConfigHandler.is_ai_local_only_mode", return_value=False):
            assert _default_local_only_mode_reader() is False
