"""S3 用量落账 Recorder 单测（模型统一.md §10 / ADR-0014 §6.1）。

覆盖（方案 D6）：
- observe_usage 同步转移不可变数据、按 attempt_id 幂等；
- submit_terminal 正常落账 persisted=True；
- dao_provider 惰性解析：engine 未就绪 → persisted=False / DB_UNAVAILABLE（缺失不伪装）；
- 落账超时 5s → persisted=False（账本不可用降级，D5）；
- EngineDisposedError 传播为 persisted=False；
- 60s 窗口惰性扫描：submit_terminal 顺带关闭到期窗口；
- aclose 兜底 flush 且二次调用幂等。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.model_management_types import NormalizedLLMUsage, PersistenceOutcome
from services.ai_service.usage_recorder import (
    AttemptTerminalRecord,
    AttemptUsageObservation,
    UsageRecorder,
)

pytestmark = pytest.mark.unit


def _usage() -> NormalizedLLMUsage:
    return NormalizedLLMUsage(
        input_total_tokens=120,
        uncached_input_tokens=100,
        cache_read_tokens=20,
        cache_write_tokens=None,
        output_tokens=15,
    )


def _observation(attempt_id: str = "att-1") -> AttemptUsageObservation:
    return AttemptUsageObservation(
        attempt_id=attempt_id,
        invocation_id="inv-1",
        usage=_usage(),
        actual_model="glm-4-air",
        metadata={"connection_id": "conn-remote", "month": "202610"},
    )


def _terminal(attempt_id: str = "att-1", status: str = "succeeded") -> AttemptTerminalRecord:
    return AttemptTerminalRecord(
        attempt_id=attempt_id,
        invocation_id="inv-1",
        status=status,
        usage=_usage(),
        error_code=None if status == "succeeded" else "UPSTREAM_5XX",
        duration_ms=123.4,
    )


def _dao_mock() -> MagicMock:
    dao = MagicMock()
    dao.settle_attempt_usage = AsyncMock(return_value=True)
    dao.mark_dispatched = AsyncMock(return_value=True)
    dao.close_usage_window = AsyncMock(return_value=True)
    return dao


class TestObserveUsage:
    def test_transfer_is_synchronous_and_idempotent_by_attempt_id(self):
        recorder = UsageRecorder(dao_provider=lambda: _dao_mock())
        recorder.observe_usage(_observation("att-1"))
        # 同 attempt_id 二次 observe 不追加（幂等）
        recorder.observe_usage(_observation("att-1"))
        recorder.observe_usage(_observation("att-2"))
        assert recorder.pending_observation_count() == 2

    def test_transfer_copies_mapping_not_alias(self):
        recorder = UsageRecorder(dao_provider=lambda: _dao_mock())
        obs = _observation()
        recorder.observe_usage(obs)
        stored = recorder.observed_usage("att-1")
        assert stored is not None
        # metadata 转移为不可变快照：观察后篡改原 mapping 不得影响已存快照
        obs.metadata["connection_id"] = "tampered"
        assert stored["connection_id"] == "conn-remote"
        assert stored["month"] == "202610"


class TestMarkDispatched:
    @pytest.mark.asyncio
    async def test_mark_dispatched_with_dao(self):
        dao = _dao_mock()
        dao.mark_dispatched = AsyncMock(return_value=True)
        recorder = UsageRecorder(dao_provider=lambda: dao)
        outcome = await recorder.mark_dispatched(
            invocation_id="inv-1",
            reservation_id="res-1",
            owner_session_id="owner-1",
            owner_epoch=1,
        )
        assert outcome == PersistenceOutcome(persisted=True, error_code=None)

    @pytest.mark.asyncio
    async def test_mark_dispatched_dao_unavailable(self):
        recorder = UsageRecorder(dao_provider=lambda: None)
        outcome = await recorder.mark_dispatched(
            invocation_id="inv-1",
            reservation_id="res-1",
            owner_session_id="owner-1",
            owner_epoch=1,
        )
        assert outcome == PersistenceOutcome(persisted=False, error_code="DB_UNAVAILABLE")

    @pytest.mark.asyncio
    async def test_mark_dispatched_owner_lease_lost(self):
        """rowcount=0（失权）→ persisted=False + OWNER_LEASE_LOST，不伪装成功。"""
        dao = _dao_mock()
        dao.mark_dispatched = AsyncMock(return_value=False)
        recorder = UsageRecorder(dao_provider=lambda: dao)
        outcome = await recorder.mark_dispatched(
            invocation_id="inv-1",
            reservation_id="res-1",
            owner_session_id="owner-1",
            owner_epoch=1,
        )
        assert outcome == PersistenceOutcome(persisted=False, error_code="OWNER_LEASE_LOST")


class TestBeginAttempt:
    @pytest.mark.asyncio
    async def test_begin_attempt_inserts_attempt_row(self):
        dao = _dao_mock()
        dao.insert_attempt = AsyncMock(return_value=None)
        recorder = UsageRecorder(dao_provider=lambda: dao)
        outcome = await recorder.begin_attempt(
            attempt_id="att-1",
            invocation_id="inv-1",
            configured_model="glm-4-air",
            attempt_row={"task_id": "t", "candidate_index": 0},
        )
        assert outcome == PersistenceOutcome(persisted=True, error_code=None)
        dao.insert_attempt.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_begin_attempt_unavailable_degrades(self):
        """attempt 行建立失败降级 persisted=False（执行已授权开始，不阻断）。"""
        recorder = UsageRecorder(dao_provider=lambda: None)
        outcome = await recorder.begin_attempt(
            attempt_id="att-1",
            invocation_id="inv-1",
            configured_model="glm-4-air",
        )
        assert outcome == PersistenceOutcome(persisted=False, error_code="DB_UNAVAILABLE")


class TestSubmitTerminal:
    @pytest.mark.asyncio
    async def test_persists_with_dao_available(self):
        dao = _dao_mock()
        recorder = UsageRecorder(dao_provider=lambda: dao)
        recorder.observe_usage(_observation("att-1"))
        outcome = await recorder.submit_terminal(_terminal())
        assert outcome == PersistenceOutcome(persisted=True, error_code=None)
        dao.settle_attempt_usage.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unavailable_when_dao_provider_returns_none(self):
        recorder = UsageRecorder(dao_provider=lambda: None)
        recorder.observe_usage(_observation("att-1"))
        outcome = await recorder.submit_terminal(_terminal())
        assert outcome == PersistenceOutcome(persisted=False, error_code="DB_UNAVAILABLE")

    @pytest.mark.asyncio
    async def test_engine_disposed_maps_to_persisted_false(self):
        from data.persistence.daos.base_dao import EngineDisposedError

        dao = _dao_mock()
        dao.settle_attempt_usage = AsyncMock(side_effect=EngineDisposedError("disposed"))
        recorder = UsageRecorder(dao_provider=lambda: dao)
        recorder.observe_usage(_observation("att-1"))
        outcome = await recorder.submit_terminal(_terminal())
        assert outcome.persisted is False
        assert outcome.error_code == "DB_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_timeout_5s_maps_to_persisted_false(self):
        dao = _dao_mock()

        async def _hang(*args, **kwargs):
            await asyncio.sleep(30)

        dao.settle_attempt_usage = AsyncMock(side_effect=_hang)
        recorder = UsageRecorder(dao_provider=lambda: dao, settle_timeout_s=0.05)
        recorder.observe_usage(_observation("att-1"))
        outcome = await recorder.submit_terminal(_terminal())
        assert outcome.persisted is False
        assert outcome.error_code == "DB_SETTLE_TIMEOUT"

    @pytest.mark.asyncio
    async def test_usage_null_semantics_no_fabricated_zero(self):
        """usage=None（不可得）时落账记录 usage_status=unavailable，不填 0（R21）。"""
        dao = _dao_mock()
        recorder = UsageRecorder(dao_provider=lambda: dao)
        # 观察期即无用量（适配器未返回 usage），终态也不可得 → unavailable
        obs = _observation("att-9")
        recorder.observe_usage(
            AttemptUsageObservation(
                attempt_id=obs.attempt_id,
                invocation_id=obs.invocation_id,
                usage=None,
                actual_model=obs.actual_model,
                metadata=obs.metadata,
            )
        )
        record = AttemptTerminalRecord(
            attempt_id="att-9",
            invocation_id="inv-1",
            status="succeeded",
            usage=None,
            error_code=None,
            duration_ms=10.0,
        )
        await recorder.submit_terminal(record)
        kwargs = dao.settle_attempt_usage.await_args.kwargs
        assert kwargs["usage_status"] == "unavailable"

    @pytest.mark.asyncio
    async def test_submit_without_observation_metadata_rejected(self):
        """observe 未先行（无 month 元数据）→ METADATA_INCOMPLETE，不进账本不误报 DB 超时。"""
        dao = _dao_mock()
        recorder = UsageRecorder(dao_provider=lambda: dao)
        outcome = await recorder.submit_terminal(_terminal("att-no-obs"))
        assert outcome == PersistenceOutcome(persisted=False, error_code="METADATA_INCOMPLETE")
        dao.settle_attempt_usage.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_lazy_window_scan_closes_expired(self):
        dao = _dao_mock()
        recorder = UsageRecorder(dao_provider=lambda: dao, window_seconds=0.0)
        recorder.observe_usage(_observation("att-1"))
        await recorder.submit_terminal(_terminal("att-1"))
        # window_seconds=0 → 立即到期；submit_terminal 顺带惰性扫描应关闭该窗口
        dao.close_usage_window.assert_awaited()


class TestAclos:
    @pytest.mark.asyncio
    async def test_aclose_flushes_pending_and_idempotent(self):
        dao = _dao_mock()
        recorder = UsageRecorder(dao_provider=lambda: dao)
        recorder.observe_usage(_observation("att-1"))
        await recorder.aclose()
        assert recorder.pending_observation_count() == 0
        # 二次 aclose 安全（幂等）
        await recorder.aclose()

    @pytest.mark.asyncio
    async def test_aclose_with_unavailable_dao_does_not_raise(self):
        recorder = UsageRecorder(dao_provider=lambda: None)
        recorder.observe_usage(_observation("att-1"))
        await recorder.aclose()
        assert recorder.pending_observation_count() == 0
