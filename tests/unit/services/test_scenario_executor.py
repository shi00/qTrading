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
- 并发上限：同连接 concurrency_limit=1 时并发 execute 串行。
"""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

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
from services.ai_service.scenario_executor import ScenarioExecutor

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


def _snapshot(candidates: tuple[dict, ...], connections: tuple[ResolvedConnection, ...]) -> ExecutionSnapshot:
    # 快照 connections 契约为 mapping（S2 快照形态），经 asdict 模拟生产序列化形态
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
        connections=tuple(dataclasses.asdict(c) for c in connections),
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
        """D5：有限额费用型在 mark_dispatched persisted=False 时禁止派发。"""
        adapter = _adapter_ok()
        recorder = _recorder_mock(persisted=False)
        executor = _executor(adapter, recorder, validate=_validator_ok)
        result = await executor.execute(
            _request(),
            _snapshot(
                (dict(_candidate(0, "conn-remote"), budget_amount_cny="0.05"),),
                (_remote_conn(),),
            ),
            _validator_ok,
        )
        adapter.invoke.assert_not_awaited()
        assert result.status == "failed"

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
