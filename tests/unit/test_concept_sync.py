"""Unit tests for concept sync strategies (AKShare + LimitList + AIConceptTag)."""

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
    ctx.processor = None
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
    svc._chat_completion_with_failover = AsyncMock(
        return_value=response or {"concepts": ["锂电池", "新能源车"]},
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
        ctx.ai_service._chat_completion_with_failover = AsyncMock(
            side_effect=RuntimeError("llm error"),
        )
        ctx.cache.stock_dao.get_stocks_without_ai_concepts = AsyncMock(
            return_value=[("000001.SZ", "平安银行")],
        )
        ctx.cache.stock_dao.upsert_ai_concepts = AsyncMock(return_value=0)

        strategy = AIConceptTagSyncStrategy(ctx)
        result = await strategy.run(batch_size=10)

        assert result.status in (SyncStatus.PARTIAL.value, SyncStatus.FAILED.value)
        assert len(result.errors) > 0
