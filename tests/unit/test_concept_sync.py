"""Unit tests for concept sync strategies (AKShare + LimitList + AIConceptTag)."""

import contextlib

import pandas as pd
import pytest
from unittest.mock import AsyncMock, MagicMock

from data.external.akshare_concept_client import AkshareConceptClient
from data.external.tushare_client import TushareAPIPermissionError
from data.sync.base import SyncContext, SyncStatus
from data.sync.concept_sync import (
    AIConceptTagSyncStrategy,
    AKShareConceptSyncStrategy,
    LimitListSyncStrategy,
)

pytestmark = pytest.mark.unit


# --- Helpers ---


def _make_ctx(**overrides):
    """Build a MagicMock-backed SyncContext with all dependencies wired."""
    ctx = MagicMock(spec=SyncContext)
    ctx.cache = MagicMock()
    ctx.cache.stock_dao = MagicMock()
    ctx.api = MagicMock()
    ctx.ai_service = None
    ctx.cancel_event = None
    ctx.processor = None
    # AIConceptTagSyncStrategy now calls these methods; default to no-op AsyncMocks
    # so that tests not explicitly setting them do not break on await.
    ctx.cache.stock_dao.get_ai_concept_failures_for_retry = AsyncMock(return_value=[])
    ctx.cache.stock_dao.upsert_ai_concept_failure = AsyncMock(return_value=1)
    ctx.cache.stock_dao.clear_ai_concept_failure = AsyncMock(return_value=0)
    # T5 fix: 主流程末尾会清理已达 max_retry 的记录
    ctx.cache.stock_dao.delete_expired_failures = AsyncMock(return_value=0)
    # 策略-L1: 显式设置 search_engine 默认值，避免 MagicMock 自动生成非字符串对象
    ctx.config.get_ai_concept_search_engine = MagicMock(return_value="search_std")
    for key, value in overrides.items():
        setattr(ctx, key, value)
    return ctx


def _make_concept_list_df():
    return pd.DataFrame(
        {
            "板块名称": ["锂电池", "光伏"],
            "板块代码": ["BK0123", "BK0456"],
        }
    )


def _make_constituents_df():
    return pd.DataFrame(
        {
            "代码": ["000001", "600000"],
            "名称": ["平安银行", "浦发银行"],
        }
    )


def _make_limit_list_df():
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "600000.SH"],
            "trade_date": ["20240614", "20240614"],
            "name": ["平安银行", "浦发银行"],
        }
    )


# --- AKShareConceptSyncStrategy ---


class TestAKShareConceptSync:
    @pytest.mark.asyncio
    async def test_success(self):
        ctx = _make_ctx()
        ctx.cache.stock_dao.upsert_em_concepts = AsyncMock(return_value=4)

        client = AkshareConceptClient()
        client.get_concept_list = AsyncMock(return_value=_make_concept_list_df())
        client.get_concept_constituents = AsyncMock(return_value=_make_constituents_df())

        strategy = AKShareConceptSyncStrategy(ctx)
        result = await strategy.run()

        assert result.status == SyncStatus.SUCCESS.value
        assert result.added > 0
        ctx.cache.stock_dao.upsert_em_concepts.assert_called()

    @pytest.mark.asyncio
    async def test_cancel_returns_cancelled(self):
        ctx = _make_ctx()
        ctx.cache.stock_dao.upsert_em_concepts = AsyncMock(return_value=0)

        client = AkshareConceptClient()
        client.get_concept_list = AsyncMock(return_value=_make_concept_list_df())
        client.get_concept_constituents = AsyncMock(return_value=_make_constituents_df())

        strategy = AKShareConceptSyncStrategy(ctx)
        strategy.cancel()
        result = await strategy.run()

        assert result.status == SyncStatus.CANCELLED.value

    @pytest.mark.asyncio
    async def test_partial_when_constituents_fail(self):
        ctx = _make_ctx()
        ctx.cache.stock_dao.upsert_em_concepts = AsyncMock(return_value=2)

        client = AkshareConceptClient()
        client.get_concept_list = AsyncMock(return_value=_make_concept_list_df())
        # First call fails, second succeeds (will be retried up to 3 times)
        call_count = 0

        async def _flaky_constituents(symbol):
            nonlocal call_count
            call_count += 1
            if symbol == "锂电池":
                raise ConnectionError("network error")
            return _make_constituents_df()

        client.get_concept_constituents = AsyncMock(side_effect=_flaky_constituents)

        strategy = AKShareConceptSyncStrategy(ctx)
        result = await strategy.run()

        assert result.status in (SyncStatus.PARTIAL.value, SyncStatus.SUCCESS.value)
        assert len(result.errors) > 0 or result.warnings

    @pytest.mark.asyncio
    async def test_empty_concept_list(self):
        ctx = _make_ctx()
        ctx.cache.stock_dao.upsert_em_concepts = AsyncMock(return_value=0)

        client = AkshareConceptClient()
        client.get_concept_list = AsyncMock(return_value=pd.DataFrame())
        client.get_concept_constituents = AsyncMock(return_value=_make_constituents_df())

        strategy = AKShareConceptSyncStrategy(ctx)
        result = await strategy.run()

        assert result.status == SyncStatus.SUCCESS.value
        assert result.added == 0
        ctx.cache.stock_dao.upsert_em_concepts.assert_not_called()

    @pytest.mark.asyncio
    async def test_concept_list_fetch_exception(self):
        ctx = _make_ctx()
        ctx.cache.stock_dao.upsert_em_concepts = AsyncMock(return_value=0)

        client = AkshareConceptClient()
        client.get_concept_list = AsyncMock(side_effect=ConnectionError("network error"))
        client.get_concept_constituents = AsyncMock(return_value=_make_constituents_df())

        strategy = AKShareConceptSyncStrategy(ctx)
        result = await strategy.run()

        assert result.status == SyncStatus.FAILED.value
        assert len(result.errors) > 0


# --- LimitListSyncStrategy ---


class TestLimitListSync:
    @pytest.mark.asyncio
    async def test_success(self):
        ctx = _make_ctx()
        ctx.cache.stock_dao.clear_today_limit_concepts = AsyncMock(return_value=0)
        ctx.cache.stock_dao.upsert_limit_concepts = AsyncMock(return_value=2)
        ctx.api.get_limit_list = AsyncMock(return_value=_make_limit_list_df())

        strategy = LimitListSyncStrategy(ctx)
        result = await strategy.run(trade_date="20240614")

        assert result.status == SyncStatus.SUCCESS.value
        assert result.added > 0
        ctx.cache.stock_dao.clear_today_limit_concepts.assert_called_once()
        ctx.cache.stock_dao.upsert_limit_concepts.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_returns_cancelled(self):
        ctx = _make_ctx()
        ctx.cache.stock_dao.clear_today_limit_concepts = AsyncMock(return_value=0)
        ctx.cache.stock_dao.upsert_limit_concepts = AsyncMock(return_value=0)
        ctx.api.get_limit_list = AsyncMock(return_value=_make_limit_list_df())

        strategy = LimitListSyncStrategy(ctx)
        strategy.cancel()
        result = await strategy.run(trade_date="20240614")

        assert result.status == SyncStatus.CANCELLED.value

    @pytest.mark.asyncio
    async def test_permission_denied_degrades_to_success_with_warning(self):
        ctx = _make_ctx()
        ctx.cache.stock_dao.clear_today_limit_concepts = AsyncMock(return_value=0)
        ctx.cache.stock_dao.upsert_limit_concepts = AsyncMock(return_value=0)
        ctx.api.get_limit_list = AsyncMock(
            side_effect=TushareAPIPermissionError("limit_list", "积分不足"),
        )

        strategy = LimitListSyncStrategy(ctx)
        result = await strategy.run(trade_date="20240614")

        assert result.status == SyncStatus.SUCCESS.value
        assert len(result.warnings) > 0
        ctx.cache.stock_dao.upsert_limit_concepts.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_limit_list(self):
        ctx = _make_ctx()
        ctx.cache.stock_dao.clear_today_limit_concepts = AsyncMock(return_value=0)
        ctx.cache.stock_dao.upsert_limit_concepts = AsyncMock(return_value=0)
        ctx.api.get_limit_list = AsyncMock(return_value=pd.DataFrame())

        strategy = LimitListSyncStrategy(ctx)
        result = await strategy.run(trade_date="20240614")

        assert result.status == SyncStatus.SUCCESS.value
        assert result.added == 0
        ctx.cache.stock_dao.upsert_limit_concepts.assert_not_called()

    @pytest.mark.asyncio
    async def test_general_exception_returns_failed(self):
        ctx = _make_ctx()
        ctx.cache.stock_dao.clear_today_limit_concepts = AsyncMock(return_value=0)
        ctx.cache.stock_dao.upsert_limit_concepts = AsyncMock(return_value=0)
        ctx.api.get_limit_list = AsyncMock(side_effect=RuntimeError("unexpected"))

        strategy = LimitListSyncStrategy(ctx)
        result = await strategy.run(trade_date="20240614")

        assert result.status == SyncStatus.FAILED.value
        assert len(result.errors) > 0


# --- AIConceptTagSyncStrategy ---


def _make_ai_service_mock(available=True, response=None):
    svc = MagicMock()
    svc.is_cloud_available = MagicMock(return_value=available)
    # chat_with_web_search returns {"content": str, "usage": dict, "reasoning_content": str}
    default_content = '{"concepts": ["锂电池", "新能源车"]}'
    svc.chat_with_web_search = AsyncMock(
        return_value=response or {"content": default_content},
    )
    return svc


class TestAIConceptTagSync:
    @pytest.mark.asyncio
    async def test_success(self):
        ctx = _make_ctx(ai_service=_make_ai_service_mock())
        ctx.cache.stock_dao.get_stocks_without_ai_concepts = AsyncMock(
            return_value=[("000001.SZ", "平安银行")],
        )
        ctx.cache.stock_dao.upsert_ai_concepts = AsyncMock(return_value=2)

        strategy = AIConceptTagSyncStrategy(ctx)
        result = await strategy.run(batch_size=10)

        assert result.status == SyncStatus.SUCCESS.value
        assert result.added > 0
        ctx.cache.stock_dao.upsert_ai_concepts.assert_called_once()
        # R3 fix: 验证 T5 清理被调用
        ctx.cache.stock_dao.delete_expired_failures.assert_called_once()

    @pytest.mark.asyncio
    async def test_skip_when_no_llm(self):
        ctx = _make_ctx(ai_service=None)
        ctx.cache.stock_dao.get_stocks_without_ai_concepts = AsyncMock(return_value=[])
        ctx.cache.stock_dao.upsert_ai_concepts = AsyncMock(return_value=0)

        strategy = AIConceptTagSyncStrategy(ctx)
        result = await strategy.run()

        assert result.status == SyncStatus.SUCCESS.value
        assert result.skipped > 0
        ctx.cache.stock_dao.upsert_ai_concepts.assert_not_called()
        # R3 fix: LLM 未配置提前 return，T5 清理不应被调用
        ctx.cache.stock_dao.delete_expired_failures.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_when_llm_unavailable(self):
        ctx = _make_ctx(ai_service=_make_ai_service_mock(available=False))
        ctx.cache.stock_dao.get_stocks_without_ai_concepts = AsyncMock(return_value=[])
        ctx.cache.stock_dao.upsert_ai_concepts = AsyncMock(return_value=0)

        strategy = AIConceptTagSyncStrategy(ctx)
        result = await strategy.run()

        assert result.status == SyncStatus.SUCCESS.value
        assert result.skipped > 0
        ctx.cache.stock_dao.upsert_ai_concepts.assert_not_called()
        # R3 fix: LLM 不可用提前 return，T5 清理不应被调用
        ctx.cache.stock_dao.delete_expired_failures.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancel_returns_cancelled(self):
        ctx = _make_ctx(ai_service=_make_ai_service_mock())
        ctx.cache.stock_dao.get_stocks_without_ai_concepts = AsyncMock(
            return_value=[("000001.SZ", "平安银行"), ("600000.SH", "浦发银行")],
        )
        ctx.cache.stock_dao.upsert_ai_concepts = AsyncMock(return_value=0)

        strategy = AIConceptTagSyncStrategy(ctx)
        strategy.cancel()
        result = await strategy.run(batch_size=10)

        assert result.status == SyncStatus.CANCELLED.value

    @pytest.mark.asyncio
    async def test_empty_pending_stocks(self):
        ctx = _make_ctx(ai_service=_make_ai_service_mock())
        ctx.cache.stock_dao.get_stocks_without_ai_concepts = AsyncMock(return_value=[])
        ctx.cache.stock_dao.upsert_ai_concepts = AsyncMock(return_value=0)

        strategy = AIConceptTagSyncStrategy(ctx)
        result = await strategy.run()

        assert result.status == SyncStatus.SUCCESS.value
        assert result.added == 0
        ctx.cache.stock_dao.upsert_ai_concepts.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_failure_returns_partial(self):
        ctx = _make_ctx(
            ai_service=_make_ai_service_mock(
                response=None,
            ),
        )
        ctx.ai_service.chat_with_web_search = AsyncMock(
            side_effect=RuntimeError("llm error"),
        )
        ctx.cache.stock_dao.get_stocks_without_ai_concepts = AsyncMock(
            return_value=[("000001.SZ", "平安银行")],
        )
        ctx.cache.stock_dao.upsert_ai_concepts = AsyncMock(return_value=0)
        ctx.cache.stock_dao.get_ai_concept_failures_for_retry = AsyncMock(return_value=[])
        ctx.cache.stock_dao.upsert_ai_concept_failure = AsyncMock(return_value=1)

        strategy = AIConceptTagSyncStrategy(ctx)
        result = await strategy.run(batch_size=10)

        assert result.status in (SyncStatus.PARTIAL.value, SyncStatus.FAILED.value)
        assert len(result.errors) > 0

    @pytest.mark.asyncio
    async def test_engine_disposed_propagates(self):
        """R5: EngineDisposedError 必须从策略外层 raise，不能被吞"""
        from data.persistence.daos.base_dao import EngineDisposedError

        ctx = _make_ctx(ai_service=_make_ai_service_mock())
        ctx.cache.stock_dao.get_ai_concept_failures_for_retry = AsyncMock(
            side_effect=EngineDisposedError(),
        )
        ctx.cache.stock_dao.get_stocks_without_ai_concepts = AsyncMock(return_value=[])

        strategy = AIConceptTagSyncStrategy(ctx)
        with pytest.raises(EngineDisposedError):
            await strategy.run(batch_size=10)

    @pytest.mark.asyncio
    async def test_retry_queue_priority_loading(self):
        """错题本优先拉取：失败队列优先于 fresh pending"""
        ctx = _make_ctx(ai_service=_make_ai_service_mock())
        # 失败队列返回 1 只股票，fresh 应拉取 batch_size - 1 = 9
        ctx.cache.stock_dao.get_ai_concept_failures_for_retry = AsyncMock(
            return_value=[("000001.SZ", "平安银行")],
        )
        ctx.cache.stock_dao.get_stocks_without_ai_concepts = AsyncMock(
            return_value=[("600000.SH", "浦发银行")],
        )
        ctx.cache.stock_dao.upsert_ai_concepts = AsyncMock(return_value=2)
        ctx.cache.stock_dao.clear_ai_concept_failure = AsyncMock(return_value=1)

        strategy = AIConceptTagSyncStrategy(ctx)
        result = await strategy.run(batch_size=10)

        # 验证失败队列优先调用
        ctx.cache.stock_dao.get_ai_concept_failures_for_retry.assert_called_once_with(10)
        # 验证 fresh 拉取 batch_size - len(retry_pending) = 9
        ctx.cache.stock_dao.get_stocks_without_ai_concepts.assert_called_once_with(9, [])
        # 成功后从错题本清除（000001.SZ 是 retry_pending 中的）
        ctx.cache.stock_dao.clear_ai_concept_failure.assert_called_once_with("000001.SZ")
        assert result.status == SyncStatus.SUCCESS.value

    @pytest.mark.asyncio
    async def test_failure_persisted_to_retry_queue(self):
        """LLM 失败时调用 upsert_ai_concept_failure 写入错题本"""
        ctx = _make_ctx(ai_service=_make_ai_service_mock())
        ctx.cache.stock_dao.get_ai_concept_failures_for_retry = AsyncMock(return_value=[])
        ctx.cache.stock_dao.get_stocks_without_ai_concepts = AsyncMock(
            return_value=[("000001.SZ", "平安银行")],
        )
        ctx.cache.stock_dao.upsert_ai_concepts = AsyncMock(return_value=0)
        ctx.cache.stock_dao.upsert_ai_concept_failure = AsyncMock(return_value=1)
        ctx.ai_service.chat_with_web_search = AsyncMock(side_effect=RuntimeError("llm error"))

        strategy = AIConceptTagSyncStrategy(ctx)
        result = await strategy.run(batch_size=10)

        ctx.cache.stock_dao.upsert_ai_concept_failure.assert_called_once_with(
            "000001.SZ",
            "平安银行",
            "llm error",
        )
        assert result.status == SyncStatus.PARTIAL.value

    @pytest.mark.asyncio
    async def test_upsert_failure_propagates_engine_disposed(self):
        """P1 R5：upsert_ai_concept_failure 抛 EngineDisposedError 时策略层必须传播，不可被 except Exception 吞"""
        from data.persistence.daos.base_dao import EngineDisposedError

        ctx = _make_ctx(ai_service=_make_ai_service_mock())
        ctx.cache.stock_dao.get_ai_concept_failures_for_retry = AsyncMock(return_value=[])
        ctx.cache.stock_dao.get_stocks_without_ai_concepts = AsyncMock(
            return_value=[("000001.SZ", "平安银行")],
        )
        ctx.cache.stock_dao.upsert_ai_concepts = AsyncMock(return_value=0)
        # LLM 失败 → 触发 upsert_ai_concept_failure → 抛 EngineDisposedError
        ctx.ai_service.chat_with_web_search = AsyncMock(side_effect=RuntimeError("llm error"))
        ctx.cache.stock_dao.upsert_ai_concept_failure = AsyncMock(side_effect=EngineDisposedError())

        strategy = AIConceptTagSyncStrategy(ctx)
        with pytest.raises(EngineDisposedError):
            await strategy.run(batch_size=10)

    @pytest.mark.asyncio
    async def test_clear_failure_propagates_engine_disposed(self):
        """P1 R5：clear_ai_concept_failure 抛 EngineDisposedError 时策略层必须传播，不可被 except Exception 吞"""
        from data.persistence.daos.base_dao import EngineDisposedError

        ctx = _make_ctx(ai_service=_make_ai_service_mock())
        # 000001.SZ 在 retry_pending 中 → 成功后触发 clear_ai_concept_failure
        ctx.cache.stock_dao.get_ai_concept_failures_for_retry = AsyncMock(
            return_value=[("000001.SZ", "平安银行")],
        )
        ctx.cache.stock_dao.get_stocks_without_ai_concepts = AsyncMock(return_value=[])
        ctx.cache.stock_dao.upsert_ai_concepts = AsyncMock(return_value=1)
        # clear_ai_concept_failure 抛 EngineDisposedError
        ctx.cache.stock_dao.clear_ai_concept_failure = AsyncMock(side_effect=EngineDisposedError())

        strategy = AIConceptTagSyncStrategy(ctx)
        with pytest.raises(EngineDisposedError):
            await strategy.run(batch_size=10)

    @pytest.mark.asyncio
    async def test_cancel_event_set_during_iter_breaks_loop(self):
        """context.cancel_event 在迭代中触发，应中止并返回 CANCELLED"""
        import asyncio as _asyncio

        cancel_event = _asyncio.Event()

        ctx = _make_ctx(ai_service=_make_ai_service_mock())
        ctx.cancel_event = cancel_event
        ctx.cache.stock_dao.get_ai_concept_failures_for_retry = AsyncMock(return_value=[])
        ctx.cache.stock_dao.get_stocks_without_ai_concepts = AsyncMock(
            return_value=[("000001.SZ", "平安银行"), ("600000.SH", "浦发银行")],
        )
        ctx.cache.stock_dao.upsert_ai_concepts = AsyncMock(return_value=0)

        # 让第一个 LLM 调用完成后设置 cancel_event
        original_chat = ctx.ai_service.chat_with_web_search

        async def _set_cancel_then_return(*args, **kwargs):
            cancel_event.set()
            return await original_chat(*args, **kwargs)

        ctx.ai_service.chat_with_web_search = AsyncMock(side_effect=_set_cancel_then_return)

        strategy = AIConceptTagSyncStrategy(ctx)
        result = await strategy.run(batch_size=10)

        assert result.status == SyncStatus.CANCELLED.value

    @pytest.mark.asyncio
    async def test_cancellable_llm_call_propagates_cancel_mid_call(self):
        """P0-2 验证：LLM 调用进行中 cancel_event 被设置，应在 2 秒内响应并 raise CancelledError"""
        import asyncio as _asyncio

        cancel_event = _asyncio.Event()
        ctx = _make_ctx(ai_service=_make_ai_service_mock())
        ctx.cancel_event = cancel_event

        # 模拟长时间阻塞的 LLM 调用（10 秒）
        async def _slow_llm(*args, **kwargs):
            await _asyncio.sleep(10)
            return {"content": "{}"}

        ctx.ai_service.chat_with_web_search = AsyncMock(side_effect=_slow_llm)
        ctx.cache.stock_dao.get_ai_concept_failures_for_retry = AsyncMock(return_value=[])
        ctx.cache.stock_dao.get_stocks_without_ai_concepts = AsyncMock(
            return_value=[("000001.SZ", "平安银行")],
        )
        ctx.cache.stock_dao.upsert_ai_concepts = AsyncMock(return_value=0)

        strategy = AIConceptTagSyncStrategy(ctx)

        # 后台 0.5 秒后设置 cancel_event
        async def _set_cancel():
            await _asyncio.sleep(0.5)
            cancel_event.set()

        _asyncio.create_task(_set_cancel())

        start = _asyncio.get_event_loop().time()
        with pytest.raises(_asyncio.CancelledError):
            await strategy.run(batch_size=10)
        elapsed = _asyncio.get_event_loop().time() - start
        # 应在 ~2 秒内（_AI_TAG_CANCEL_POLL_INTERVAL）响应，而不是等 10 秒
        assert elapsed < 3.0, f"取消响应时间 {elapsed}s 超过 3 秒阈值，未满足 2 秒检查要求"

    @pytest.mark.asyncio
    async def test_cancellable_llm_call_no_event_passthrough(self):
        """context.cancel_event is None 时退化为直接调用 LLM"""
        ctx = _make_ctx(ai_service=_make_ai_service_mock())
        ctx.cancel_event = None
        ctx.cache.stock_dao.get_ai_concept_failures_for_retry = AsyncMock(return_value=[])
        ctx.cache.stock_dao.get_stocks_without_ai_concepts = AsyncMock(
            return_value=[("000001.SZ", "平安银行")],
        )
        ctx.cache.stock_dao.upsert_ai_concepts = AsyncMock(return_value=2)
        ctx.cache.stock_dao.clear_ai_concept_failure = AsyncMock(return_value=0)

        strategy = AIConceptTagSyncStrategy(ctx)
        result = await strategy.run(batch_size=10)

        assert result.status == SyncStatus.SUCCESS.value
        ctx.ai_service.chat_with_web_search.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancellable_llm_call_completes_within_poll_interval(self):
        """P1 覆盖：cancel_event 非 None 但未触发，LLM 在 < 2s 内完成，应正常返回结果。

        验证 _cancellable_llm_call 的正常路径：
        - 不进入 cancel_event.is_set() 取消分支
        - 不进入 TimeoutError 重试分支
        - 直接通过 asyncio.wait_for(asyncio.shield(llm_task)) 返回
        """
        import asyncio as _asyncio

        cancel_event = _asyncio.Event()  # 非 None，但从不 set
        ctx = _make_ctx(ai_service=_make_ai_service_mock())
        ctx.cancel_event = cancel_event
        ctx.cache.stock_dao.get_ai_concept_failures_for_retry = AsyncMock(return_value=[])
        ctx.cache.stock_dao.get_stocks_without_ai_concepts = AsyncMock(
            return_value=[("000001.SZ", "平安银行")],
        )
        ctx.cache.stock_dao.upsert_ai_concepts = AsyncMock(return_value=1)
        ctx.cache.stock_dao.clear_ai_concept_failure = AsyncMock(return_value=0)

        # LLM 在 0.1s 内完成（远小于 2s 轮询间隔），确保走正常返回路径
        async def _fast_llm(*args, **kwargs):
            await _asyncio.sleep(0.1)
            return {"content": '{"concepts": ["AI_LLM_test"]}'}

        ctx.ai_service.chat_with_web_search = AsyncMock(side_effect=_fast_llm)

        strategy = AIConceptTagSyncStrategy(ctx)
        start = _asyncio.get_event_loop().time()
        result = await strategy.run(batch_size=10)
        elapsed = _asyncio.get_event_loop().time() - start

        # 正常完成，未触发取消
        assert result.status == SyncStatus.SUCCESS.value
        assert elapsed < 2.0, f"应在 2s 轮询间隔内完成，实际 {elapsed}s"
        assert not cancel_event.is_set()
        ctx.ai_service.chat_with_web_search.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancellable_llm_call_multiple_timeouts_then_completes(self):
        """F4 覆盖：LLM 调用耗时超过 2s（触发 TimeoutError continue），最终完成返回。

        验证 _cancellable_llm_call 的 TimeoutError 循环路径：
        - 第 1 次 wait_for 超时（2s）→ continue
        - 第 2 次 wait_for LLM 已完成 → 正常返回
        - 不进入 cancel_event.is_set() 分支
        """
        import asyncio as _asyncio

        cancel_event = _asyncio.Event()  # 非 None，但从不 set
        ctx = _make_ctx(ai_service=_make_ai_service_mock())
        ctx.cancel_event = cancel_event
        ctx.cache.stock_dao.get_ai_concept_failures_for_retry = AsyncMock(return_value=[])
        ctx.cache.stock_dao.get_stocks_without_ai_concepts = AsyncMock(
            return_value=[("000001.SZ", "平安银行")],
        )
        ctx.cache.stock_dao.upsert_ai_concepts = AsyncMock(return_value=1)
        ctx.cache.stock_dao.clear_ai_concept_failure = AsyncMock(return_value=0)

        # LLM 在 2.5s 完成：第 1 次 wait_for(2s) 超时，第 2 次 wait_for 立即返回
        async def _slow_then_complete_llm(*args, **kwargs):
            await _asyncio.sleep(2.5)
            return {"content": '{"concepts": ["测试概念"]}'}

        ctx.ai_service.chat_with_web_search = AsyncMock(side_effect=_slow_then_complete_llm)

        strategy = AIConceptTagSyncStrategy(ctx)
        start = _asyncio.get_event_loop().time()
        result = await strategy.run(batch_size=10)
        elapsed = _asyncio.get_event_loop().time() - start

        # 应在 ~2.5s 完成（1 次 TimeoutError + 第 2 次立即返回）
        assert result.status == SyncStatus.SUCCESS.value
        assert 2.0 <= elapsed < 4.0, f"应经历 1 次 TimeoutError 后完成，实际 {elapsed}s"
        assert not cancel_event.is_set()
        ctx.ai_service.chat_with_web_search.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancellable_llm_call_propagates_non_cancel_exception(self):
        """F4 覆盖：LLM 抛出非 CancelledError 异常时，应透传到策略层（不被 _cancellable_llm_call 吞掉）。

        验证 _cancellable_llm_call 的 except asyncio.CancelledError 不会捕获 RuntimeError。
        """
        import asyncio as _asyncio

        cancel_event = _asyncio.Event()  # 非 None，从不 set
        ctx = _make_ctx(ai_service=_make_ai_service_mock())
        ctx.cancel_event = cancel_event
        ctx.cache.stock_dao.get_ai_concept_failures_for_retry = AsyncMock(return_value=[])
        ctx.cache.stock_dao.get_stocks_without_ai_concepts = AsyncMock(
            return_value=[("000001.SZ", "平安银行")],
        )
        ctx.cache.stock_dao.upsert_ai_concepts = AsyncMock(return_value=0)
        ctx.cache.stock_dao.upsert_ai_concept_failure = AsyncMock(return_value=1)

        # LLM 立即抛出 ValueError（非 CancelledError）
        ctx.ai_service.chat_with_web_search = AsyncMock(side_effect=ValueError("bad request"))

        strategy = AIConceptTagSyncStrategy(ctx)
        result = await strategy.run(batch_size=10)

        # ValueError 应被策略层 except Exception 捕获，标记为 PARTIAL
        assert result.status == SyncStatus.PARTIAL.value
        assert len(result.errors) > 0
        # 错题本应被写入
        ctx.cache.stock_dao.upsert_ai_concept_failure.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_returns_non_json_string_persists_dummy_concept(self):
        """F5 覆盖：LLM 返回非 JSON 字符串，concepts 应为空，写入 dummy_id "已扫描无强概念"。

        验证 concept_sync.py:392-400 的 JSON 解析 fallback：
        - json.loads 失败
        - content.find("{") 返回 -1
        - concepts 保持为 []
        """
        ctx = _make_ctx(ai_service=_make_ai_service_mock(response={"content": "无法解析这只股票"}))
        ctx.cache.stock_dao.get_ai_concept_failures_for_retry = AsyncMock(return_value=[])
        ctx.cache.stock_dao.get_stocks_without_ai_concepts = AsyncMock(
            return_value=[("000001.SZ", "平安银行")],
        )
        ctx.cache.stock_dao.upsert_ai_concepts = AsyncMock(return_value=1)
        ctx.cache.stock_dao.clear_ai_concept_failure = AsyncMock(return_value=0)

        strategy = AIConceptTagSyncStrategy(ctx)
        result = await strategy.run(batch_size=10)

        assert result.status == SyncStatus.SUCCESS.value
        # 验证 upsert_ai_concepts 收到 concepts=[] 的 entry
        ctx.cache.stock_dao.upsert_ai_concepts.assert_called_once()
        entries = ctx.cache.stock_dao.upsert_ai_concepts.call_args.args[0]
        assert len(entries) == 1
        assert entries[0]["concepts"] == []

    @pytest.mark.asyncio
    async def test_llm_returns_markdown_wrapped_json_extracts_concepts(self):
        """F5 覆盖：LLM 返回 markdown 包裹的 JSON，应通过 find("{") + raw_decode 提取。

        验证 concept_sync.py:395-399 的第 2 层 fallback。
        """
        markdown_content = '```json\n{"concepts": ["锂电池", "光伏"]}\n```'
        ctx = _make_ctx(ai_service=_make_ai_service_mock(response={"content": markdown_content}))
        ctx.cache.stock_dao.get_ai_concept_failures_for_retry = AsyncMock(return_value=[])
        ctx.cache.stock_dao.get_stocks_without_ai_concepts = AsyncMock(
            return_value=[("000001.SZ", "平安银行")],
        )
        ctx.cache.stock_dao.upsert_ai_concepts = AsyncMock(return_value=2)
        ctx.cache.stock_dao.clear_ai_concept_failure = AsyncMock(return_value=0)

        strategy = AIConceptTagSyncStrategy(ctx)
        result = await strategy.run(batch_size=10)

        assert result.status == SyncStatus.SUCCESS.value
        ctx.cache.stock_dao.upsert_ai_concepts.assert_called_once()
        entries = ctx.cache.stock_dao.upsert_ai_concepts.call_args.args[0]
        assert entries[0]["concepts"] == ["锂电池", "光伏"]

    @pytest.mark.asyncio
    async def test_llm_returns_empty_concepts_list(self):
        """F5 覆盖：LLM 返回 {"concepts": []}，应写入 dummy_id "已扫描无强概念"。"""
        ctx = _make_ctx(
            ai_service=_make_ai_service_mock(response={"content": '{"concepts": []}'}),
        )
        ctx.cache.stock_dao.get_ai_concept_failures_for_retry = AsyncMock(return_value=[])
        ctx.cache.stock_dao.get_stocks_without_ai_concepts = AsyncMock(
            return_value=[("000001.SZ", "平安银行")],
        )
        ctx.cache.stock_dao.upsert_ai_concepts = AsyncMock(return_value=1)
        ctx.cache.stock_dao.clear_ai_concept_failure = AsyncMock(return_value=0)

        strategy = AIConceptTagSyncStrategy(ctx)
        result = await strategy.run(batch_size=10)

        assert result.status == SyncStatus.SUCCESS.value
        ctx.cache.stock_dao.upsert_ai_concepts.assert_called_once()
        entries = ctx.cache.stock_dao.upsert_ai_concepts.call_args.args[0]
        assert entries[0]["concepts"] == []

    @pytest.mark.asyncio
    async def test_llm_returns_concepts_with_empty_string_filters(self):
        """F5 覆盖：LLM 返回含空字符串/None 的 concepts 列表，应过滤 falsy 值。

        验证 concept_sync.py:404 的 [str(c) for c in raw if c] 过滤逻辑：
        - "" 被 if c 过滤（空串 falsy）
        - None 被 if c 过滤（None falsy）
        - "概念1" 保留
        """
        content = '{"concepts": ["", "概念1", null, "概念2"]}'
        ctx = _make_ctx(ai_service=_make_ai_service_mock(response={"content": content}))
        ctx.cache.stock_dao.get_ai_concept_failures_for_retry = AsyncMock(return_value=[])
        ctx.cache.stock_dao.get_stocks_without_ai_concepts = AsyncMock(
            return_value=[("000001.SZ", "平安银行")],
        )
        ctx.cache.stock_dao.upsert_ai_concepts = AsyncMock(return_value=1)
        ctx.cache.stock_dao.clear_ai_concept_failure = AsyncMock(return_value=0)

        strategy = AIConceptTagSyncStrategy(ctx)
        result = await strategy.run(batch_size=10)

        assert result.status == SyncStatus.SUCCESS.value
        ctx.cache.stock_dao.upsert_ai_concepts.assert_called_once()
        entries = ctx.cache.stock_dao.upsert_ai_concepts.call_args.args[0]
        # None 被 if c 过滤，不会变成 "None"
        assert entries[0]["concepts"] == ["概念1", "概念2"]

    @pytest.mark.asyncio
    async def test_llm_returns_non_dict_json(self):
        """F5 覆盖：LLM 返回顶层列表（非 dict），parsed 不是 dict，concepts 保持为 []。"""
        ctx = _make_ctx(
            ai_service=_make_ai_service_mock(response={"content": '["概念1", "概念2"]'}),
        )
        ctx.cache.stock_dao.get_ai_concept_failures_for_retry = AsyncMock(return_value=[])
        ctx.cache.stock_dao.get_stocks_without_ai_concepts = AsyncMock(
            return_value=[("000001.SZ", "平安银行")],
        )
        ctx.cache.stock_dao.upsert_ai_concepts = AsyncMock(return_value=1)
        ctx.cache.stock_dao.clear_ai_concept_failure = AsyncMock(return_value=0)

        strategy = AIConceptTagSyncStrategy(ctx)
        result = await strategy.run(batch_size=10)

        assert result.status == SyncStatus.SUCCESS.value
        ctx.cache.stock_dao.upsert_ai_concepts.assert_called_once()
        entries = ctx.cache.stock_dao.upsert_ai_concepts.call_args.args[0]
        # parsed 是 list 不是 dict，concepts 保持 []
        assert entries[0]["concepts"] == []

    @pytest.mark.asyncio
    async def test_llm_returns_empty_content_string(self):
        """F5 覆盖：LLM 返回 {"content": ""}，content 为空串，concepts 保持为 []。"""
        ctx = _make_ctx(ai_service=_make_ai_service_mock(response={"content": ""}))
        ctx.cache.stock_dao.get_ai_concept_failures_for_retry = AsyncMock(return_value=[])
        ctx.cache.stock_dao.get_stocks_without_ai_concepts = AsyncMock(
            return_value=[("000001.SZ", "平安银行")],
        )
        ctx.cache.stock_dao.upsert_ai_concepts = AsyncMock(return_value=1)
        ctx.cache.stock_dao.clear_ai_concept_failure = AsyncMock(return_value=0)

        strategy = AIConceptTagSyncStrategy(ctx)
        result = await strategy.run(batch_size=10)

        assert result.status == SyncStatus.SUCCESS.value
        ctx.cache.stock_dao.upsert_ai_concepts.assert_called_once()
        entries = ctx.cache.stock_dao.upsert_ai_concepts.call_args.args[0]
        assert entries[0]["concepts"] == []

    @pytest.mark.asyncio
    async def test_t5_clear_expired_failures_runtime_error_degraded(self):
        """R3 fix: delete_expired_failures 抛 RuntimeError 时应降级为 warning，不影响主流程成功状态。"""
        ctx = _make_ctx(ai_service=_make_ai_service_mock())
        ctx.cache.stock_dao.get_stocks_without_ai_concepts = AsyncMock(
            return_value=[("000001.SZ", "平安银行")],
        )
        ctx.cache.stock_dao.upsert_ai_concepts = AsyncMock(return_value=2)
        # T5 清理抛通用异常 → 应被降级为 warning，不传播
        ctx.cache.stock_dao.delete_expired_failures = AsyncMock(side_effect=RuntimeError("cleanup failed"))

        strategy = AIConceptTagSyncStrategy(ctx)
        result = await strategy.run(batch_size=10)

        # 主流程仍应成功（清理失败不影响主流程）
        assert result.status == SyncStatus.SUCCESS.value
        ctx.cache.stock_dao.delete_expired_failures.assert_called_once()

    @pytest.mark.asyncio
    async def test_t5_clear_expired_failures_cancelled_propagates(self):
        """R3 fix: delete_expired_failures 抛 CancelledError 时必须传播，不可被降级吞掉。"""
        import asyncio as _asyncio

        ctx = _make_ctx(ai_service=_make_ai_service_mock())
        ctx.cache.stock_dao.get_stocks_without_ai_concepts = AsyncMock(
            return_value=[("000001.SZ", "平安银行")],
        )
        ctx.cache.stock_dao.upsert_ai_concepts = AsyncMock(return_value=2)
        ctx.cache.stock_dao.delete_expired_failures = AsyncMock(side_effect=_asyncio.CancelledError())

        strategy = AIConceptTagSyncStrategy(ctx)
        with pytest.raises(_asyncio.CancelledError):
            await strategy.run(batch_size=10)

    @pytest.mark.asyncio
    async def test_t5_clear_expired_failures_engine_disposed_propagates(self):
        """R3 fix: delete_expired_failures 抛 EngineDisposedError 时必须传播（R5 红线）。"""
        from data.persistence.daos.base_dao import EngineDisposedError

        ctx = _make_ctx(ai_service=_make_ai_service_mock())
        ctx.cache.stock_dao.get_stocks_without_ai_concepts = AsyncMock(
            return_value=[("000001.SZ", "平安银行")],
        )
        ctx.cache.stock_dao.upsert_ai_concepts = AsyncMock(return_value=2)
        ctx.cache.stock_dao.delete_expired_failures = AsyncMock(side_effect=EngineDisposedError())

        strategy = AIConceptTagSyncStrategy(ctx)
        with pytest.raises(EngineDisposedError):
            await strategy.run(batch_size=10)

    @pytest.mark.asyncio
    async def test_l3_cancel_event_with_llm_exception(self):
        """L3 fix: cancel_event.set() + llm_task 抛非 CancelledError 异常的组合场景。

        验证 _cancellable_llm_call 内部：
        - cancel_event 触发后 llm_task.cancel() → llm_task 的 sleep 被 cancel 打断抛 CancelledError
        - mock 捕获 CancelledError 后转换为 RuntimeError（模拟 LLM 内部异常处理路径）
        - 内部 except Exception 应 suppress（logger.debug exc_info=True）
        - 最终 raise asyncio.CancelledError（而非 RuntimeError）
        """
        import asyncio as _asyncio
        import time as _time

        cancel_event = _asyncio.Event()
        ctx = _make_ctx(ai_service=_make_ai_service_mock())
        ctx.cancel_event = cancel_event
        ctx.cache.stock_dao.get_ai_concept_failures_for_retry = AsyncMock(return_value=[])
        ctx.cache.stock_dao.get_stocks_without_ai_concepts = AsyncMock(
            return_value=[("000001.SZ", "平安银行")],
        )
        ctx.cache.stock_dao.upsert_ai_concepts = AsyncMock(return_value=0)
        ctx.cache.stock_dao.upsert_ai_concept_failure = AsyncMock(return_value=1)

        call_count = 0

        async def _llm_raises_after_cancel(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            try:
                await _asyncio.sleep(10)
            except _asyncio.CancelledError as ce:
                # 模拟 LLM 内部异常处理路径：捕获 cancel 后抛 RuntimeError
                raise RuntimeError("llm inner error after cancel") from ce

        ctx.ai_service.chat_with_web_search = AsyncMock(side_effect=_llm_raises_after_cancel)

        async def _set_cancel():
            await _asyncio.sleep(0.5)
            cancel_event.set()

        cancel_task = _asyncio.create_task(_set_cancel())

        start = _time.monotonic()
        strategy = AIConceptTagSyncStrategy(ctx)
        try:
            with pytest.raises(_asyncio.CancelledError):
                await strategy.run(batch_size=10)
        finally:
            cancel_task.cancel()
            with contextlib.suppress(_asyncio.CancelledError):
                await cancel_task
        elapsed = _time.monotonic() - start
        # 时序守卫：取消应在 2s 内（wait_for timeout=2.0）触发，加上清理 < 5s
        assert elapsed < 5.0, f"cancel propagation too slow: {elapsed:.2f}s"
        assert call_count == 1, f"llm_task should be called once, got {call_count}"


# --- search_engine 配置透传 ---


class TestSearchEnginePropagation:
    """验证 ai_concept_search_engine 配置正确透传到 chat_with_web_search。"""

    @pytest.mark.asyncio
    async def test_search_engine_pro_passed_to_chat_with_web_search(self):
        """cancel_event=None 路径：config 返回 search_pro 时，应透传到 chat_with_web_search。"""
        ctx = _make_ctx(
            ai_service=_make_ai_service_mock(
                response={"content": '{"concepts": ["测试"]}'},
            )
        )
        ctx.cancel_event = None
        ctx.config.get_ai_concept_search_engine.return_value = "search_pro"
        ctx.cache.stock_dao.get_ai_concept_failures_for_retry = AsyncMock(return_value=[])
        ctx.cache.stock_dao.get_stocks_without_ai_concepts = AsyncMock(
            return_value=[("000001.SZ", "平安银行")],
        )
        ctx.cache.stock_dao.upsert_ai_concepts = AsyncMock(return_value=1)

        strategy = AIConceptTagSyncStrategy(ctx)
        await strategy.run(batch_size=10)

        ctx.ai_service.chat_with_web_search.assert_called_once()
        assert ctx.ai_service.chat_with_web_search.call_args.kwargs["search_engine"] == "search_pro"

    @pytest.mark.asyncio
    async def test_search_engine_passed_in_cancellable_path(self):
        """cancel_event!=None 路径：应透传 search_engine 到 chat_with_web_search。"""
        import asyncio as _asyncio

        cancel_event = _asyncio.Event()
        ctx = _make_ctx(
            ai_service=_make_ai_service_mock(
                response={"content": '{"concepts": ["测试"]}'},
            )
        )
        ctx.cancel_event = cancel_event
        ctx.config.get_ai_concept_search_engine.return_value = "search_pro"
        ctx.cache.stock_dao.get_ai_concept_failures_for_retry = AsyncMock(return_value=[])
        ctx.cache.stock_dao.get_stocks_without_ai_concepts = AsyncMock(
            return_value=[("000001.SZ", "平安银行")],
        )
        ctx.cache.stock_dao.upsert_ai_concepts = AsyncMock(return_value=1)

        strategy = AIConceptTagSyncStrategy(ctx)
        await strategy.run(batch_size=10)

        ctx.ai_service.chat_with_web_search.assert_called_once()
        assert ctx.ai_service.chat_with_web_search.call_args.kwargs["search_engine"] == "search_pro"

    @pytest.mark.asyncio
    async def test_search_engine_default_std_when_config_returns_std(self):
        """config 返回 search_std 时，应透传 search_std 到 chat_with_web_search。"""
        ctx = _make_ctx(
            ai_service=_make_ai_service_mock(
                response={"content": '{"concepts": ["测试"]}'},
            )
        )
        ctx.cancel_event = None
        ctx.config.get_ai_concept_search_engine.return_value = "search_std"
        ctx.cache.stock_dao.get_ai_concept_failures_for_retry = AsyncMock(return_value=[])
        ctx.cache.stock_dao.get_stocks_without_ai_concepts = AsyncMock(
            return_value=[("000001.SZ", "平安银行")],
        )
        ctx.cache.stock_dao.upsert_ai_concepts = AsyncMock(return_value=1)

        strategy = AIConceptTagSyncStrategy(ctx)
        await strategy.run(batch_size=10)

        ctx.ai_service.chat_with_web_search.assert_called_once()
        assert ctx.ai_service.chat_with_web_search.call_args.kwargs["search_engine"] == "search_std"

    @pytest.mark.asyncio
    async def test_search_engine_passed_through_timeout_retry_path(self):
        """策略-M1: TimeoutError 重试路径下 search_engine 仍应正确透传。

        验证 _cancellable_llm_call 在 wait_for 超时后 continue，最终完成时
        search_engine 参数仍正确传给 chat_with_web_search（不被默认值覆盖）。
        """
        import asyncio as _asyncio

        cancel_event = _asyncio.Event()  # 非 None，从不 set
        ctx = _make_ctx(
            ai_service=_make_ai_service_mock(
                response={"content": '{"concepts": ["测试"]}'},
            )
        )
        ctx.cancel_event = cancel_event
        ctx.config.get_ai_concept_search_engine.return_value = "search_pro"
        ctx.cache.stock_dao.get_ai_concept_failures_for_retry = AsyncMock(return_value=[])
        ctx.cache.stock_dao.get_stocks_without_ai_concepts = AsyncMock(
            return_value=[("000001.SZ", "平安银行")],
        )
        ctx.cache.stock_dao.upsert_ai_concepts = AsyncMock(return_value=1)
        ctx.cache.stock_dao.clear_ai_concept_failure = AsyncMock(return_value=0)

        # LLM 在 2.5s 完成：第 1 次 wait_for(2s) 超时 → continue，第 2 次立即返回
        async def _slow_then_complete_llm(*args, **kwargs):
            await _asyncio.sleep(2.5)
            return {"content": '{"concepts": ["测试概念"]}'}

        ctx.ai_service.chat_with_web_search = AsyncMock(side_effect=_slow_then_complete_llm)

        strategy = AIConceptTagSyncStrategy(ctx)
        start = _asyncio.get_event_loop().time()
        result = await strategy.run(batch_size=10)
        elapsed = _asyncio.get_event_loop().time() - start

        # 验证经历了 TimeoutError 重试路径
        assert 2.0 <= elapsed < 4.0, f"应经历 1 次 TimeoutError 后完成，实际 {elapsed}s"
        assert result.status == SyncStatus.SUCCESS.value
        # 关键断言：search_engine 仍为 config 配置的 "search_pro"，未被默认值覆盖
        ctx.ai_service.chat_with_web_search.assert_called_once()
        assert ctx.ai_service.chat_with_web_search.call_args.kwargs["search_engine"] == "search_pro"


# --- EngineDisposedError propagation (R5) for AKShare / LimitList ---


class TestEngineDisposedPropagation:
    """R5: 三个策略外层 except EngineDisposedError 必须 raise，不得吞"""

    @pytest.mark.asyncio
    async def test_akshare_propagates_engine_disposed(self):
        from data.persistence.daos.base_dao import EngineDisposedError

        ctx = _make_ctx()
        ctx.cache.stock_dao.upsert_em_concepts = AsyncMock(side_effect=EngineDisposedError())

        # 触发 EngineDisposedError 需要在 _run_impl 内部抛出
        # 通过 mock client.get_concept_list 抛出
        from data.external.akshare_concept_client import AkshareConceptClient

        client = AkshareConceptClient()
        client.get_concept_list = AsyncMock(side_effect=EngineDisposedError())
        client.get_concept_constituents = AsyncMock(return_value=_make_constituents_df())

        strategy = AKShareConceptSyncStrategy(ctx)
        with pytest.raises(EngineDisposedError):
            await strategy.run()

    @pytest.mark.asyncio
    async def test_limit_list_propagates_engine_disposed(self):
        from data.persistence.daos.base_dao import EngineDisposedError

        ctx = _make_ctx()
        ctx.cache.stock_dao.clear_today_limit_concepts = AsyncMock(
            side_effect=EngineDisposedError(),
        )
        ctx.cache.stock_dao.upsert_limit_concepts = AsyncMock(return_value=0)
        ctx.api.get_limit_list = AsyncMock(return_value=_make_limit_list_df())

        strategy = LimitListSyncStrategy(ctx)
        with pytest.raises(EngineDisposedError):
            await strategy.run(trade_date="20240614")
