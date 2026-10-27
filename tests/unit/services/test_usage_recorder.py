"""S3 用量落账 Recorder 单测（模型统一.md §10 / ADR-0014 §6.1）。

覆盖（方案 D6）：
- observe_usage 同步转移不可变数据、按 attempt_id 幂等；
- submit_terminal 正常落账 persisted=True；
- dao_provider 惰性解析：engine 未就绪 → persisted=False / DB_UNAVAILABLE（缺失不伪装）；
- 落账超时 5s → persisted=False（账本不可用降级，D5）；
- EngineDisposedError 传播为 persisted=False；
- 60s 窗口惰性扫描：submit_terminal 顺带关闭到期窗口；
- aclose 兜底 flush 且二次调用幂等。

覆盖（F20 结算失败保全 + 停机 flush）：
- 结算失败保留观察与终态记录（不再以「内存清空」为正确断言），账本恢复后重放移除；
- 重放走 DAO usage_revision 水位（usage_revision 不变），不与 F19 防双计语义冲突；
- 不可恢复错误（METADATA_INCOMPLETE / OWNER_LEASE_LOST = 已结算或旧修订）确认丢弃；
- 保留队列有界（溢出 FIFO 逐出最旧 + 告警），账本持续不可达时不立即重放；
- aclose 全量 flush：保留条目原始重放 / 无终态观察合成 cancelled 补交 / 全窗口收口；
- aclose flush 失败不抛且如实告警（R21），CancelledError 传播（R2）。

覆盖（F33 迟到修订窗口：修订单调推进 + 窗口身份持久化 + 确认关闭才移除）：
- observe 修订接收（revision > 水位覆盖快照 + 推进水位），重复 / 乱序旧修订幂等忽略
  （R22：乱序写入最终值为最大值）；
- 结算 usage_revision 不再固定 1——按修订水位推进；无新修订的幂等重提交水位不变
  （F19 防双计兼容）；
- 窗口截止（usage_window_expires_at）由 Recorder 真实计算持久化（naive UTC，
  修复前 metadata 透传恒 None → DAO 关闭条件对 NULL 永不成立）；
- 窗口扫描改 DAO 批量收口：确认关闭才移除内存扫描项（失败保留下轮重试），
  未到期项保留（截止已持久化，跨重启由下次启动批量收口按期关闭）。
"""

import asyncio
import datetime
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.model_management_types import NormalizedLLMUsage, PersistenceOutcome
from services.ai_service.usage_recorder import (
    _RETRY_BACKOFF_BASE_S,
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


def _revised_usage() -> NormalizedLLMUsage:
    """F33 修订后的用量快照（与 _usage() 可区分的 token 计数）。"""
    return NormalizedLLMUsage(
        input_total_tokens=200,
        uncached_input_tokens=180,
        cache_read_tokens=20,
        cache_write_tokens=None,
        output_tokens=30,
    )


def _observation(attempt_id: str = "att-1", usage_revision: int = 1) -> AttemptUsageObservation:
    return AttemptUsageObservation(
        attempt_id=attempt_id,
        invocation_id="inv-1",
        usage=_usage(),
        actual_model="glm-4-air",
        metadata={"connection_id": "conn-remote", "month": "202610"},
        usage_revision=usage_revision,
    )


def _revised_observation(attempt_id: str = "att-1", usage_revision: int = 2) -> AttemptUsageObservation:
    """F33 修订观察：用量快照与首次观察可区分。"""
    return AttemptUsageObservation(
        attempt_id=attempt_id,
        invocation_id="inv-1",
        usage=_revised_usage(),
        actual_model="glm-4-air",
        metadata={"connection_id": "conn-remote", "month": "202610"},
        usage_revision=usage_revision,
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
    dao.close_expired_usage_windows = AsyncMock(return_value=1)
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
    async def test_submit_without_observation_falls_back_to_record_metadata(self):
        """F18：无观察终态回退记录自带元数据——失败事实与预留收敛可达账本。

        适配器异常 / 授权失败路径没有 observe 先行，终态携带 month / retry_index
        等结算参数即可进账本（usage 缺失仍标 unavailable，R21 不伪装）。
        """
        dao = _dao_mock()
        recorder = UsageRecorder(dao_provider=lambda: dao)
        record = AttemptTerminalRecord(
            attempt_id="att-fail",
            invocation_id="inv-1",
            status="failed",
            usage=None,
            error_code="adapter error: RuntimeError",
            duration_ms=None,
            reservation_id="res-1",
            metadata={
                "connection_id": "conn-remote",
                "connection_revision": 1,
                "adapter_kind": "litellm_chat",
                "scenario_id": "news_sentiment",
                "task_id": "sentiment",
                "candidate_index": 0,
                "retry_index": 0,
                "security_revision": 1,
                "month": "202610",
            },
        )
        outcome = await recorder.submit_terminal(record)
        assert outcome == PersistenceOutcome(persisted=True, error_code=None)
        kwargs = dao.settle_attempt_usage.await_args.kwargs
        assert kwargs["status"] == "failed"
        assert kwargs["usage_status"] == "unavailable"  # R21：无用量不填 0
        assert kwargs["retry_index"] == 0
        assert kwargs["month"] == "202610"
        assert kwargs["reservation_id"] == "res-1"
        assert kwargs["cost_cny"] is None

    @pytest.mark.asyncio
    async def test_lazy_window_scan_closes_expired(self):
        dao = _dao_mock()
        recorder = UsageRecorder(dao_provider=lambda: dao, window_seconds=0.0)
        recorder.observe_usage(_observation("att-1"))
        await recorder.submit_terminal(_terminal("att-1"))
        # window_seconds=0 → 立即到期；submit_terminal 顺带惰性批量收口应触发
        dao.close_expired_usage_windows.assert_awaited()


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


class TestSubmitTerminalRetention:
    """F20：结算失败保全——可恢复失败保留观察与终态记录供重放，不再以
    「内存清空」为结算失败场景的正确断言；重试成功或确认不可恢复后才移除。"""

    @pytest.mark.asyncio
    async def test_recoverable_failure_retains_observation_for_replay(self):
        dao = _dao_mock()
        dao.settle_attempt_usage = AsyncMock(side_effect=RuntimeError("boom"))
        now = 0.0
        recorder = UsageRecorder(dao_provider=lambda: dao, clock=lambda: now)
        recorder.observe_usage(_observation("att-1"))
        outcome = await recorder.submit_terminal(_terminal("att-1"))
        assert outcome == PersistenceOutcome(persisted=False, error_code="DB_SETTLE_TIMEOUT")
        # 观察不丢弃（保留供恢复重放），且不做立即重试（账本大概率仍不可达）
        assert recorder.pending_observation_count() == 1
        assert dao.settle_attempt_usage.await_count == 1

    @pytest.mark.asyncio
    async def test_no_immediate_replay_while_ledger_down(self):
        dao = _dao_mock()
        dao.settle_attempt_usage = AsyncMock(side_effect=RuntimeError("boom"))
        now = 0.0
        recorder = UsageRecorder(dao_provider=lambda: dao, clock=lambda: now)
        recorder.observe_usage(_observation("att-1"))
        await recorder.submit_terminal(_terminal("att-1"))
        recorder.observe_usage(_observation("att-2"))
        await recorder.submit_terminal(_terminal("att-2"))
        # 账本持续不可达：两次提交各自失败，无重放叠加
        assert dao.settle_attempt_usage.await_count == 2

    @pytest.mark.asyncio
    async def test_replay_respects_backoff_window(self):
        dao = _dao_mock()
        dao.settle_attempt_usage = AsyncMock(side_effect=[RuntimeError("boom"), True])
        now = 0.0
        recorder = UsageRecorder(dao_provider=lambda: dao, clock=lambda: now)
        recorder.observe_usage(_observation("att-1"))
        assert (await recorder.submit_terminal(_terminal("att-1"))).persisted is False
        # 退避未到期（now 仍为 0）：新落账成功不触发未到期条目的重放
        recorder.observe_usage(_observation("att-2"))
        assert (await recorder.submit_terminal(_terminal("att-2"))).persisted is True
        assert dao.settle_attempt_usage.await_count == 2
        assert recorder.pending_observation_count() == 1

    @pytest.mark.asyncio
    async def test_retry_succeeds_after_ledger_recovery_and_evicts_entry(self):
        dao = _dao_mock()
        dao.settle_attempt_usage = AsyncMock(side_effect=[RuntimeError("boom"), True, True])
        now = 0.0
        recorder = UsageRecorder(dao_provider=lambda: dao, clock=lambda: now)
        recorder.observe_usage(_observation("att-1"))
        assert (await recorder.submit_terminal(_terminal("att-1"))).persisted is False
        now = _RETRY_BACKOFF_BASE_S  # 推进退避到期
        # 新终态成功落账 = 账本可达 → 顺带重放保留条目
        recorder.observe_usage(_observation("att-2"))
        assert (await recorder.submit_terminal(_terminal("att-2"))).persisted is True
        assert dao.settle_attempt_usage.await_count == 3
        replay_kwargs = dao.settle_attempt_usage.await_args_list[2].kwargs
        assert replay_kwargs["attempt_id"] == "att-1"
        assert replay_kwargs["status"] == "succeeded"
        # F19 防双计兼容：重放 usage_revision 不变（水位幂等由 DAO 兜底）
        assert replay_kwargs["usage_revision"] == 1
        assert recorder.pending_observation_count() == 0

    @pytest.mark.asyncio
    async def test_duplicate_late_success_clears_retained_entry(self):
        """executor 幂等重提交同 attempt 终态成功 → 保留条目与观察一并清除。"""
        dao = _dao_mock()
        dao.settle_attempt_usage = AsyncMock(side_effect=[RuntimeError("boom"), True])
        now = 0.0
        recorder = UsageRecorder(dao_provider=lambda: dao, clock=lambda: now)
        recorder.observe_usage(_observation("att-1"))
        assert (await recorder.submit_terminal(_terminal("att-1"))).persisted is False
        now = 10.0
        assert (await recorder.submit_terminal(_terminal("att-1"))).persisted is True
        assert recorder.pending_observation_count() == 0
        # 队列已空：后续成功落账不产生额外重放调用
        now = 60.0
        recorder.observe_usage(_observation("att-2"))
        await recorder.submit_terminal(_terminal("att-2"))
        assert dao.settle_attempt_usage.await_count == 3

    @pytest.mark.asyncio
    async def test_unrecoverable_lease_lost_drops_observation(self):
        """OWNER_LEASE_LOST（DAO False = 旧修订或已结算 / 无背书拒绝）确认丢弃。"""
        dao = _dao_mock()
        dao.settle_attempt_usage = AsyncMock(return_value=False)
        recorder = UsageRecorder(dao_provider=lambda: dao)
        recorder.observe_usage(_observation("att-1"))
        outcome = await recorder.submit_terminal(_terminal("att-1"))
        assert outcome == PersistenceOutcome(persisted=False, error_code="OWNER_LEASE_LOST")
        assert recorder.pending_observation_count() == 0

    @pytest.mark.asyncio
    async def test_observation_without_month_metadata_dropped_not_retained(self):
        """METADATA_INCOMPLETE（观察缺归属月）重试无新事实来源 → 确认丢弃。"""
        dao = _dao_mock()
        recorder = UsageRecorder(dao_provider=lambda: dao)
        recorder.observe_usage(
            AttemptUsageObservation(
                attempt_id="att-nomonth",
                invocation_id="inv-1",
                usage=_usage(),
                actual_model="glm-4-air",
                metadata={"connection_id": "conn-remote"},  # 缺 month
            )
        )
        outcome = await recorder.submit_terminal(_terminal("att-nomonth"))
        assert outcome == PersistenceOutcome(persisted=False, error_code="METADATA_INCOMPLETE")
        assert recorder.pending_observation_count() == 0


class TestPendingSettleQueueBounded:
    """F20：保留队列有界——溢出 FIFO 逐出最旧并如实告警（R21 不静默丢弃）。"""

    @pytest.mark.asyncio
    async def test_queue_overflow_evicts_oldest_with_warning(self, caplog):
        dao = _dao_mock()
        dao.settle_attempt_usage = AsyncMock(side_effect=[RuntimeError("boom")] * 3 + [True, True, True])
        now = 0.0
        recorder = UsageRecorder(dao_provider=lambda: dao, clock=lambda: now, max_pending_settle=2)
        with caplog.at_level(logging.WARNING):
            for i in range(3):
                aid = f"att-{i}"
                recorder.observe_usage(_observation(aid))
                await recorder.submit_terminal(_terminal(aid))
        # 队列上限 2：最早的 att-0 被逐出（观察一并清除），告警如实记录
        assert recorder.pending_observation_count() == 2
        assert "dropping oldest unresolved usage fact" in caplog.text
        # 账本恢复后仅重放保留的 att-1 / att-2，被逐出的 att-0 不再重放
        now = 60.0
        recorder.observe_usage(_observation("att-3"))
        assert (await recorder.submit_terminal(_terminal("att-3"))).persisted is True
        called_ids = [call.kwargs["attempt_id"] for call in dao.settle_attempt_usage.await_args_list]
        assert called_ids.count("att-0") == 1  # 仅首次失败调用，无重放
        assert called_ids.count("att-1") == 2  # 失败 + 重放成功
        assert called_ids.count("att-2") == 2
        assert dao.settle_attempt_usage.await_count == 6
        assert recorder.pending_observation_count() == 0


class TestAcloseFlushF20:
    """F20：aclose 停机前全量 flush——失败不吞没（R21），取消传播（R2）。"""

    @pytest.mark.asyncio
    async def test_aclose_flushes_retained_queue_with_original_record(self):
        dao = _dao_mock()
        dao.settle_attempt_usage = AsyncMock(side_effect=[RuntimeError("boom"), True])
        now = 0.0
        recorder = UsageRecorder(dao_provider=lambda: dao, clock=lambda: now)
        recorder.observe_usage(_observation("att-1"))
        assert (await recorder.submit_terminal(_terminal("att-1"))).persisted is False
        await recorder.aclose()
        # 保留条目以原始终态记录重放（status=succeeded，非合成 cancelled）
        assert dao.settle_attempt_usage.await_count == 2
        replay_kwargs = dao.settle_attempt_usage.await_args_list[1].kwargs
        assert replay_kwargs["attempt_id"] == "att-1"
        assert replay_kwargs["status"] == "succeeded"
        assert recorder.pending_observation_count() == 0

    @pytest.mark.asyncio
    async def test_aclose_submits_unsettled_observation_as_cancelled(self):
        """从未提交终态的观察：停机合成 cancelled 终态补交（用量事实不丢，R21）。"""
        dao = _dao_mock()
        recorder = UsageRecorder(dao_provider=lambda: dao)
        recorder.observe_usage(_observation("att-1"))
        await recorder.aclose()
        dao.settle_attempt_usage.assert_awaited_once()
        kwargs = dao.settle_attempt_usage.await_args.kwargs
        assert kwargs["attempt_id"] == "att-1"
        assert kwargs["status"] == "cancelled"
        assert kwargs["usage_status"] == "reported"  # 用量已观察 = 消费事实
        assert kwargs["usage"] is not None
        assert kwargs["month"] == "202610"
        assert recorder.pending_observation_count() == 0

    @pytest.mark.asyncio
    async def test_aclose_skips_synthesized_settle_when_original_replay_failed(self):
        """原始终态记录重放失败时不覆盖为合成 cancelled（同 attempt 不二次结算）。"""
        dao = _dao_mock()
        dao.settle_attempt_usage = AsyncMock(side_effect=RuntimeError("boom"))
        now = 0.0
        recorder = UsageRecorder(dao_provider=lambda: dao, clock=lambda: now)
        recorder.observe_usage(_observation("att-1"))
        assert (await recorder.submit_terminal(_terminal("att-1"))).persisted is False
        await recorder.aclose()
        # 恰好两次：submit 首次失败 + ① 原始记录重放失败；② 不合成 cancelled 三次结算
        assert dao.settle_attempt_usage.await_count == 2
        assert all(call.kwargs["status"] == "succeeded" for call in dao.settle_attempt_usage.await_args_list)

    @pytest.mark.asyncio
    async def test_aclose_flush_failure_does_not_raise_and_warns(self, caplog):
        """停机 flush 失败：尽力而为，失败事实告警不吞没（R21），不抛账本异常。"""
        dao = _dao_mock()
        dao.settle_attempt_usage = AsyncMock(side_effect=RuntimeError("boom"))
        recorder = UsageRecorder(dao_provider=lambda: dao)
        recorder.observe_usage(_observation("att-1"))
        with caplog.at_level(logging.WARNING):
            await recorder.aclose()  # 不抛
        assert "usage fact dropped at shutdown" in caplog.text
        assert recorder.pending_observation_count() == 0

    @pytest.mark.asyncio
    async def test_aclose_sweeps_windows_and_unexpired_persisted(self):
        """F33：停机批量收口到期窗口；未到期窗口截止已持久化，跨重启由下次
        启动的惰性批量收口按期关闭（不再滞留未关闭，也不强制提前关闭）。"""
        dao = _dao_mock()
        recorder = UsageRecorder(dao_provider=lambda: dao, window_seconds=3600.0)
        recorder.observe_usage(_observation("att-1"))
        await recorder.aclose()
        dao.close_expired_usage_windows.assert_awaited()

    @pytest.mark.asyncio
    async def test_aclose_cancelled_error_propagates(self):
        """R2：停机 flush 被 cleanup 预算取消时 CancelledError 传播，不吞没。"""
        dao = _dao_mock()

        async def _cancel(*args, **kwargs):
            raise asyncio.CancelledError

        dao.settle_attempt_usage = AsyncMock(side_effect=_cancel)
        recorder = UsageRecorder(dao_provider=lambda: dao)
        recorder.observe_usage(_observation("att-1"))
        with pytest.raises(asyncio.CancelledError) as excinfo:
            await recorder.aclose()
        # R2 语义：精确断言取消类型传播不吞没；取消中断 flush，
        # 条目留存内存不静默销毁（可经下次停机 flush 重试）
        assert excinfo.type is asyncio.CancelledError
        assert recorder.pending_observation_count() == 1


class TestRevisionAdvancement:
    """F33：窗口内迟到修订单调推进——observe 修订接收 / 结算 revision 水位 /
    重复与乱序幂等（R22 最终值为最大值）/ 幂等重提交水位不变（F19 兼容）。"""

    @pytest.mark.asyncio
    async def test_first_settle_uses_revision_one(self):
        dao = _dao_mock()
        recorder = UsageRecorder(dao_provider=lambda: dao)
        recorder.observe_usage(_observation("att-1"))
        await recorder.submit_terminal(_terminal("att-1"))
        assert dao.settle_attempt_usage.await_args.kwargs["usage_revision"] == 1

    @pytest.mark.asyncio
    async def test_revision_observation_advances_settlement(self):
        """窗口内修订观察（revision > 水位）被接收：重提交以新水位 + 修订 usage 结算。"""
        dao = _dao_mock()
        recorder = UsageRecorder(dao_provider=lambda: dao)
        recorder.observe_usage(_observation("att-1"))
        await recorder.submit_terminal(_terminal("att-1"))
        recorder.observe_usage(_revised_observation("att-1", usage_revision=2))
        await recorder.submit_terminal(_terminal("att-1"))
        second = dao.settle_attempt_usage.await_args_list[1].kwargs
        assert second["usage_revision"] == 2
        # 修订快照权威（record 为修订前终态，其 usage 是旧事实）
        assert second["usage"] is not None
        assert second["usage"].input_total_tokens == 200

    @pytest.mark.asyncio
    async def test_duplicate_revision_ignored(self):
        """同 revision 重复观察幂等忽略：水位不重复推进（防双计前置守卫）。"""
        dao = _dao_mock()
        recorder = UsageRecorder(dao_provider=lambda: dao)
        recorder.observe_usage(_observation("att-1"))
        recorder.observe_usage(_revised_observation("att-1", usage_revision=2))
        recorder.observe_usage(_revised_observation("att-1", usage_revision=2))
        # 重复修订不产生重复条目：仅 1 份修订快照
        assert recorder.pending_observation_count() == 1
        await recorder.submit_terminal(_terminal("att-1"))
        assert dao.settle_attempt_usage.await_args.kwargs["usage_revision"] == 2

    @pytest.mark.asyncio
    async def test_out_of_order_revision_keeps_max(self):
        """R22：乱序修订（rev3 先到、rev2 后到）不回退——最终值为最大修订。"""
        dao = _dao_mock()
        recorder = UsageRecorder(dao_provider=lambda: dao)
        recorder.observe_usage(_observation("att-1"))
        await recorder.submit_terminal(_terminal("att-1"))
        recorder.observe_usage(_revised_observation("att-1", usage_revision=3))
        recorder.observe_usage(_revised_observation("att-1", usage_revision=2))
        # 快照保持 rev3 内容（乱序旧修订不覆盖最新事实）
        stored = recorder.observed_usage("att-1")
        assert stored is not None
        assert stored["usage"] is not None
        assert stored["usage"].input_total_tokens == 200
        await recorder.submit_terminal(_terminal("att-1"))
        revisions = [c.kwargs["usage_revision"] for c in dao.settle_attempt_usage.await_args_list]
        assert revisions == [1, 3]

    @pytest.mark.asyncio
    async def test_idempotent_resubmit_keeps_revision(self):
        """F19 防双计兼容：无新修订的幂等重提交水位不变（重放 revision 恒同）。

        重提交形态与 executor F18 一致：record 自带结算元数据（观察已随首次
        成功移除），结算走 record-only 回退路径，revision 取持久水位（仍 1）。
        """
        dao = _dao_mock()
        recorder = UsageRecorder(dao_provider=lambda: dao)
        recorder.observe_usage(_observation("att-1"))
        await recorder.submit_terminal(_terminal("att-1"))
        replay_record = AttemptTerminalRecord(
            attempt_id="att-1",
            invocation_id="inv-1",
            status="succeeded",
            usage=_usage(),
            error_code=None,
            duration_ms=123.4,
            metadata={"connection_id": "conn-remote", "month": "202610"},
        )
        await recorder.submit_terminal(replay_record)
        revisions = [c.kwargs["usage_revision"] for c in dao.settle_attempt_usage.await_args_list]
        assert revisions == [1, 1]

    @pytest.mark.asyncio
    async def test_window_expires_at_persisted_naive_utc(self):
        """F33：窗口截止真实持久化（修复前 metadata 透传恒 None → 永久未关闭）。"""
        dao = _dao_mock()
        recorder = UsageRecorder(dao_provider=lambda: dao)
        recorder.observe_usage(_observation("att-1"))
        await recorder.submit_terminal(_terminal("att-1"))
        expires = dao.settle_attempt_usage.await_args.kwargs["usage_window_expires_at"]
        assert expires is not None
        assert isinstance(expires, datetime.datetime)
        assert expires.tzinfo is None  # naive UTC，与 DAO 侧 now() AT TIME ZONE 'UTC' 同域


class TestUsageWindowScanF33:
    """F33：窗口扫描批量收口——确认关闭才移除扫描项；未到期项保留；
    重启后内存登记已失时由首次落账触发的批量收口恢复扫描。"""

    @pytest.mark.asyncio
    async def test_expired_window_removed_after_confirmed_close(self):
        dao = _dao_mock()
        recorder = UsageRecorder(dao_provider=lambda: dao, window_seconds=0.0)
        recorder.observe_usage(_observation("att-1"))
        await recorder.submit_terminal(_terminal("att-1"))
        dao.close_expired_usage_windows.assert_awaited()
        # 确认关闭（SQL 成功）后内存扫描项移除
        assert recorder._windows == {}

    @pytest.mark.asyncio
    async def test_scan_failure_keeps_window_entry_for_retry(self):
        """收口失败不移除扫描项（修复前先 pop 后 close，失败即永久滞留未关闭）。"""
        dao = _dao_mock()
        dao.close_expired_usage_windows = AsyncMock(side_effect=RuntimeError("boom"))
        recorder = UsageRecorder(dao_provider=lambda: dao, window_seconds=0.0)
        recorder.observe_usage(_observation("att-1"))
        await recorder.submit_terminal(_terminal("att-1"))
        assert "att-1" in recorder._windows

    @pytest.mark.asyncio
    async def test_unexpired_window_kept_until_expiry(self):
        """保护期内窗口不提前关闭：内存项保留，截止已持久化跨重启按期收口。"""
        dao = _dao_mock()
        recorder = UsageRecorder(dao_provider=lambda: dao, window_seconds=3600.0)
        recorder.observe_usage(_observation("att-1"))
        await recorder.submit_terminal(_terminal("att-1"))
        assert "att-1" in recorder._windows

    @pytest.mark.asyncio
    async def test_restarted_recorder_recovers_pending_windows(self):
        """重启后恢复扫描：新实例（内存登记已失）首次落账即触发批量收口，
        DAO 层按持久化截止关闭全部到期未关闭行（含 NULL 历史行兜底）。"""
        dao = _dao_mock()
        recorder = UsageRecorder(dao_provider=lambda: dao)
        recorder.observe_usage(_observation("att-new"))
        await recorder.submit_terminal(_terminal("att-new"))
        dao.close_expired_usage_windows.assert_awaited_once()
