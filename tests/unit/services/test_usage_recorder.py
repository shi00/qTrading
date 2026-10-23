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
    ReservationOutcome,
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


class TestEnsureInvocation:
    """F16 父请求建立包装：DAO 成功 / 账本不可用 / 异常收敛为 PersistenceOutcome。"""

    @pytest.mark.asyncio
    async def test_ensure_invocation_persists(self):
        dao = _dao_mock()
        dao.create_invocation = AsyncMock(return_value=True)
        recorder = UsageRecorder(dao_provider=lambda: dao)
        outcome = await recorder.ensure_invocation(
            invocation_id="inv-1",
            scenario_id="news_sentiment",
            trigger="manual",
            owner_session_id="owner-1",
            owner_epoch=1,
            owner_lease_expires_at="2026-10-23T00:15:00",
            budget_policy_snapshot={"monthly_cny_limit": "50.00"},
        )
        assert outcome == PersistenceOutcome(persisted=True, error_code=None)
        kwargs = dao.create_invocation.await_args.kwargs
        assert kwargs["invocation_id"] == "inv-1"
        assert kwargs["budget_policy_snapshot"] == {"monthly_cny_limit": "50.00"}
        assert kwargs["owner_session_id"] == "owner-1"

    @pytest.mark.asyncio
    async def test_ensure_invocation_dao_unavailable(self):
        recorder = UsageRecorder(dao_provider=lambda: None)
        outcome = await recorder.ensure_invocation(
            invocation_id="inv-1",
            scenario_id="news_sentiment",
            trigger="manual",
            owner_session_id="owner-1",
            owner_epoch=1,
            owner_lease_expires_at="2026-10-23T00:15:00",
            budget_policy_snapshot=None,
        )
        assert outcome == PersistenceOutcome(persisted=False, error_code="DB_UNAVAILABLE")

    @pytest.mark.asyncio
    async def test_ensure_invocation_ledger_error_degrades(self):
        dao = _dao_mock()
        dao.create_invocation = AsyncMock(side_effect=RuntimeError("boom"))
        recorder = UsageRecorder(dao_provider=lambda: dao)
        outcome = await recorder.ensure_invocation(
            invocation_id="inv-1",
            scenario_id="news_sentiment",
            trigger="manual",
            owner_session_id="owner-1",
            owner_epoch=1,
            owner_lease_expires_at="2026-10-23T00:15:00",
            budget_policy_snapshot=None,
        )
        assert outcome == PersistenceOutcome(persisted=False, error_code="DB_SETTLE_TIMEOUT")


class TestTryReserve:
    """F16 预算预留包装：DAO 三态（预留成功 / 被拒 / 不可用）映射 ReservationOutcome。"""

    def _reserve_kwargs(self) -> dict:
        return dict(
            invocation_id="inv-1",
            scenario_id="news_sentiment",
            task_id="sentiment",
            trigger="manual",
            month="202610",
            reservation_id="res-1",
            attempt_id="att-1",
            candidate_index=0,
            retry_index=0,
            amount_cny="0.00",
            budget_limit_cny="50.00",
            budget_policy_snapshot={"monthly_cny_limit": "50.00"},
            owner_session_id="owner-1",
            owner_epoch=1,
            owner_lease_expires_at="2026-10-23T00:15:00",
        )

    @pytest.mark.asyncio
    async def test_try_reserve_success_carries_reservation(self):
        dao = _dao_mock()
        reservation = MagicMock()
        dao.try_reserve = AsyncMock(return_value=reservation)
        recorder = UsageRecorder(dao_provider=lambda: dao)
        outcome = await recorder.try_reserve(**self._reserve_kwargs())
        assert outcome == ReservationOutcome(persisted=True, error_code=None, reservation=reservation)
        kwargs = dao.try_reserve.await_args.kwargs
        # 预留租约与 owner 租约同源（Executor 单一租约语义）
        assert kwargs["lease_expires_at"] == kwargs["owner_lease_expires_at"]
        assert kwargs["amount_cny"] == "0.00"
        assert kwargs["budget_limit_cny"] == "50.00"

    @pytest.mark.asyncio
    async def test_try_reserve_rejected_maps_to_reserve_rejected(self):
        """DAO 返回 None（余额不足 / owner 失权）→ persisted=True + RESERVE_REJECTED。"""
        dao = _dao_mock()
        dao.try_reserve = AsyncMock(return_value=None)
        recorder = UsageRecorder(dao_provider=lambda: dao)
        outcome = await recorder.try_reserve(**self._reserve_kwargs())
        assert outcome == ReservationOutcome(persisted=True, error_code="RESERVE_REJECTED", reservation=None)

    @pytest.mark.asyncio
    async def test_try_reserve_dao_unavailable(self):
        recorder = UsageRecorder(dao_provider=lambda: None)
        outcome = await recorder.try_reserve(**self._reserve_kwargs())
        assert outcome == ReservationOutcome(persisted=False, error_code="DB_UNAVAILABLE", reservation=None)

    @pytest.mark.asyncio
    async def test_try_reserve_usage_only_null_amount(self):
        """usage_only（amount/limit 均 None）同样透传 DAO（NULL 金额预留，不占预算）。"""
        dao = _dao_mock()
        dao.try_reserve = AsyncMock(return_value=MagicMock())
        recorder = UsageRecorder(dao_provider=lambda: dao)
        kwargs = self._reserve_kwargs()
        kwargs["amount_cny"] = None
        kwargs["budget_limit_cny"] = None
        kwargs["budget_policy_snapshot"] = None
        outcome = await recorder.try_reserve(**kwargs)
        assert outcome.persisted is True
        assert outcome.reservation is not None


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


class TestSubmitTerminalCostCalculation:
    """F17：终态落账时点按 metadata 携带的计价冻结快照 × 真实 usage 计算成本。

    cost 与 usage 同源（record.usage 优先回退 obs.usage）；不可计价 → None
    （R21 禁止 0 伪装）；唯一合法零 = 价格事实为 0（免费）。
    """

    _PRICING_META = {
        "mode": "estimate_cost",
        "pricing_unit": "llm_tokens",
        "price_source": "user",
        "input_price_per_m": "2.00",
        "output_price_per_m": "8.00",
        "cache_read_price_per_m": "1.00",
        "cache_write_price_per_m": None,
        "encoder_price_per_m": None,
        "fx_to_cny": None,
        "price_effective_at": None,
    }

    def _observe_with_pricing(self, recorder: UsageRecorder, **pricing_overrides) -> None:
        metadata = {
            "connection_id": "conn-remote",
            "month": "202610",
            "pricing": {**self._PRICING_META, **pricing_overrides},
        }
        recorder.observe_usage(
            AttemptUsageObservation(
                attempt_id="att-1",
                invocation_id="inv-1",
                usage=_usage(),
                actual_model="glm-4-air",
                metadata=metadata,
            )
        )

    @pytest.mark.asyncio
    async def test_cost_computed_from_pricing_snapshot_and_usage(self):
        dao = _dao_mock()
        recorder = UsageRecorder(dao_provider=lambda: dao)
        self._observe_with_pricing(recorder)
        await recorder.submit_terminal(_terminal())
        kwargs = dao.settle_attempt_usage.await_args.kwargs
        # _usage(): uncached=100 / cache_read=20 / output=15（input_total 的缓存
        # 部分已单列，不重复计价）→ 100/1e6*2 + 20/1e6*1 + 15/1e6*8
        # = 0.0002 + 0.00002 + 0.00012 = 0.00034 → HALF_UP 4 位 = 0.0003
        assert kwargs["cost_cny"] == "0.0003"
        assert kwargs["usage_status"] == "reported"

    @pytest.mark.asyncio
    async def test_free_price_zero_is_known_zero_not_none(self):
        """价格事实为 0 → 已知零 "0.0000"（免费），非未知 None。"""
        dao = _dao_mock()
        recorder = UsageRecorder(dao_provider=lambda: dao)
        self._observe_with_pricing(recorder, input_price_per_m="0", output_price_per_m="0", cache_read_price_per_m="0")
        await recorder.submit_terminal(_terminal())
        assert dao.settle_attempt_usage.await_args.kwargs["cost_cny"] == "0.0000"

    @pytest.mark.asyncio
    async def test_no_pricing_snapshot_yields_none_cost(self):
        """billing 缺失（无计价快照）→ cost None（未知，不伪装 0）。"""
        dao = _dao_mock()
        recorder = UsageRecorder(dao_provider=lambda: dao)
        recorder.observe_usage(_observation("att-1"))
        await recorder.submit_terminal(_terminal())
        assert dao.settle_attempt_usage.await_args.kwargs["cost_cny"] is None

    @pytest.mark.asyncio
    async def test_usage_only_mode_yields_none_cost(self):
        dao = _dao_mock()
        recorder = UsageRecorder(dao_provider=lambda: dao)
        self._observe_with_pricing(recorder, mode="usage_only")
        await recorder.submit_terminal(_terminal())
        assert dao.settle_attempt_usage.await_args.kwargs["cost_cny"] is None

    @pytest.mark.asyncio
    async def test_missing_price_yields_none_cost(self):
        dao = _dao_mock()
        recorder = UsageRecorder(dao_provider=lambda: dao)
        self._observe_with_pricing(recorder, output_price_per_m=None)
        await recorder.submit_terminal(_terminal())
        assert dao.settle_attempt_usage.await_args.kwargs["cost_cny"] is None

    @pytest.mark.asyncio
    async def test_failed_terminal_without_usage_yields_none_cost(self):
        """失败终态（record 与观察均无用量）→ cost None + unavailable（R21）。"""
        dao = _dao_mock()
        recorder = UsageRecorder(dao_provider=lambda: dao)
        recorder.observe_usage(
            AttemptUsageObservation(
                attempt_id="att-fail",
                invocation_id="inv-1",
                usage=None,
                actual_model=None,
                metadata={"connection_id": "conn-remote", "month": "202610", "pricing": dict(self._PRICING_META)},
            )
        )
        record = AttemptTerminalRecord(
            attempt_id="att-fail",
            invocation_id="inv-1",
            status="failed",
            usage=None,
            error_code="UPSTREAM_5XX",
            duration_ms=1.0,
        )
        await recorder.submit_terminal(record)
        kwargs = dao.settle_attempt_usage.await_args.kwargs
        assert kwargs["cost_cny"] is None
        assert kwargs["usage_status"] == "unavailable"


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
