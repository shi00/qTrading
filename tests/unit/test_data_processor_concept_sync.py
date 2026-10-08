"""Unit tests for DataProcessor.run_concept_sync orchestration.

Covers:
- Orchestration of the two retained strategies (AKShare → LimitList).
- Cancellation skips remaining strategies.
- Legacy run_doubao_tagging / run_ai_concept_tagging entry points are removed.
"""

import asyncio
import threading

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from data.data_processor import DataProcessor

pytestmark = pytest.mark.unit


def _make_dp():
    DataProcessor._reset_singleton()
    with (
        patch("data.data_processor.CacheManager"),
        patch("data.data_processor.TushareClient"),
        patch("data.data_processor.TradeCalendarService"),
        patch("data.data_processor.ConfigHandler") as mock_ch,
    ):
        mock_ch.get_token.return_value = "test_token"
        dp = DataProcessor()
    return dp


def _make_sync_result(status="success", added=0):
    m = MagicMock()
    m.status = status
    m.added = added
    return m


class TestLegacyEntriesRemoved:
    def test_run_doubao_tagging_removed(self):
        dp = _make_dp()
        assert not hasattr(dp, "run_doubao_tagging"), "run_doubao_tagging must be removed"

    def test_run_ai_concept_tagging_removed(self):
        dp = _make_dp()
        assert not hasattr(dp, "run_ai_concept_tagging"), (
            "run_ai_concept_tagging must be removed; replaced by run_concept_sync"
        )


class TestRunConceptSync:
    @pytest.mark.asyncio
    async def test_calls_akshare_strategy(self):
        dp = _make_dp()
        dp.clear_cancel()
        mock_result = _make_sync_result(status="success", added=10)
        with (
            patch("data.sync.concept_sync.AKShareConceptSyncStrategy") as MockAKShare,
            patch("data.sync.concept_sync.LimitListSyncStrategy") as MockLimitList,
        ):
            MockAKShare.return_value.run = AsyncMock(return_value=mock_result)
            MockLimitList.return_value.run = AsyncMock(return_value=mock_result)

            result = await dp.run_concept_sync()

            MockAKShare.assert_called_once_with(dp.context)
            MockAKShare.return_value.run.assert_awaited_once()
            assert "akshare=success" in result

    @pytest.mark.asyncio
    async def test_calls_limit_list_strategy(self):
        dp = _make_dp()
        dp.clear_cancel()
        mock_result = _make_sync_result(status="success", added=5)
        with (
            patch("data.sync.concept_sync.AKShareConceptSyncStrategy") as MockAKShare,
            patch("data.sync.concept_sync.LimitListSyncStrategy") as MockLimitList,
        ):
            MockAKShare.return_value.run = AsyncMock(return_value=mock_result)
            MockLimitList.return_value.run = AsyncMock(return_value=mock_result)

            result = await dp.run_concept_sync()

            MockLimitList.assert_called_once_with(dp.context)
            MockLimitList.return_value.run.assert_awaited_once()
            assert "limit_list=success" in result

    @pytest.mark.asyncio
    async def test_no_ai_tag_segment_in_result(self):
        """De-AI: run_concept_sync must not emit an ai_tag segment."""
        dp = _make_dp()
        dp.clear_cancel()
        mock_result = _make_sync_result(status="success", added=0)
        with (
            patch("data.sync.concept_sync.AKShareConceptSyncStrategy") as MockAKShare,
            patch("data.sync.concept_sync.LimitListSyncStrategy") as MockLimitList,
        ):
            MockAKShare.return_value.run = AsyncMock(return_value=mock_result)
            MockLimitList.return_value.run = AsyncMock(return_value=mock_result)

            result = await dp.run_concept_sync()

            assert "ai_tag" not in result

    @pytest.mark.asyncio
    async def test_cancelled_skips_remaining(self):
        dp = _make_dp()
        dp.clear_cancel()
        mock_result = _make_sync_result(status="success", added=0)
        cancel_event = threading.Event()
        cancel_event.set()
        with (
            patch("data.sync.concept_sync.AKShareConceptSyncStrategy") as MockAKShare,
            patch("data.sync.concept_sync.LimitListSyncStrategy") as MockLimitList,
        ):
            MockAKShare.return_value.run = AsyncMock(return_value=mock_result)
            MockLimitList.return_value.run = AsyncMock(return_value=mock_result)

            result = await dp.run_concept_sync(cancel_event=cancel_event)

            MockAKShare.return_value.run.assert_not_called()
            MockLimitList.return_value.run.assert_not_called()
            assert "cancelled" in result

    @pytest.mark.asyncio
    async def test_cancel_event_propagated_to_context(self):
        """cancel_event 必须设置到 context，供长运行策略轮询取消。"""
        dp = _make_dp()
        dp.clear_cancel()
        mock_result = _make_sync_result(status="success", added=0)
        cancel_event = threading.Event()
        with (
            patch("data.sync.concept_sync.AKShareConceptSyncStrategy") as MockAKShare,
            patch("data.sync.concept_sync.LimitListSyncStrategy") as MockLimitList,
        ):
            MockAKShare.return_value.run = AsyncMock(return_value=mock_result)
            MockLimitList.return_value.run = AsyncMock(return_value=mock_result)

            await dp.run_concept_sync(cancel_event=cancel_event)

            assert dp.context.cancel_event is cancel_event

    @pytest.mark.asyncio
    async def test_akshare_exception_logged_as_failed(self):
        """AKShare sync 抛 Exception 时记录 failed，LimitList 仍继续。"""
        dp = _make_dp()
        dp.clear_cancel()
        mock_result = _make_sync_result(status="success", added=0)
        with (
            patch("data.sync.concept_sync.AKShareConceptSyncStrategy") as MockAKShare,
            patch("data.sync.concept_sync.LimitListSyncStrategy") as MockLimitList,
        ):
            MockAKShare.return_value.run = AsyncMock(side_effect=Exception("akshare boom"))
            MockLimitList.return_value.run = AsyncMock(return_value=mock_result)
            result = await dp.run_concept_sync()
        assert "akshare=failed" in result
        assert "limit_list=success" in result

    @pytest.mark.asyncio
    async def test_limit_list_exception_logged_as_failed(self):
        """LimitList sync 抛 Exception 时记录 failed。"""
        dp = _make_dp()
        dp.clear_cancel()
        mock_result = _make_sync_result(status="success", added=0)
        with (
            patch("data.sync.concept_sync.AKShareConceptSyncStrategy") as MockAKShare,
            patch("data.sync.concept_sync.LimitListSyncStrategy") as MockLimitList,
        ):
            MockAKShare.return_value.run = AsyncMock(return_value=mock_result)
            MockLimitList.return_value.run = AsyncMock(side_effect=Exception("limit boom"))
            result = await dp.run_concept_sync()
        assert "akshare=success" in result
        assert "limit_list=failed" in result

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates_not_swallowed(self):
        """R2 红线验证: asyncio.CancelledError 必须传播，不可被 except Exception 吞没"""
        dp = _make_dp()
        dp.clear_cancel()
        mock_result = _make_sync_result(status="success", added=0)
        with (
            patch("data.sync.concept_sync.AKShareConceptSyncStrategy") as MockAKShare,
            patch("data.sync.concept_sync.LimitListSyncStrategy") as MockLimitList,
        ):
            MockAKShare.return_value.run = AsyncMock(side_effect=asyncio.CancelledError())
            MockLimitList.return_value.run = AsyncMock(return_value=mock_result)
            with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion R2 红线契约仅验证 CancelledError 类型传播即可，无有意义 message 可 match
                await dp.run_concept_sync()
