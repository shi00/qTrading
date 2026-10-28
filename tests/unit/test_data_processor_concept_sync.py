"""Unit tests for DataProcessor.run_concept_sync orchestration.

Covers:
- Orchestration of the two retained strategies (AKShare → LimitList).
- Structured per-source result (UIR-F14-003): failed/partial sources are explicit and
  ``is_complete`` only reflects「所需来源全部完成」.
- Controlled retry for whole-source failures (backoff), no retry on cancel / EngineDisposed.
- Cancellation skips remaining strategies and is never swallowed.
- Legacy run_doubao_tagging / run_ai_concept_tagging entry points are removed.
"""

import asyncio
import threading

import pytest
from unittest.mock import AsyncMock, patch

from data.data_processor import DataProcessor
from data.persistence.daos.base_dao import EngineDisposedError
from data.sync.base import SyncResult

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
        akshare_result = SyncResult(status="success", added=10)
        limit_result = SyncResult(status="success", added=2)
        with (
            patch("data.sync.concept_sync.AKShareConceptSyncStrategy") as MockAKShare,
            patch("data.sync.concept_sync.LimitListSyncStrategy") as MockLimitList,
        ):
            MockAKShare.return_value.run = AsyncMock(return_value=akshare_result)
            MockLimitList.return_value.run = AsyncMock(return_value=limit_result)

            result = await dp.run_concept_sync()

            MockAKShare.assert_called_once_with(dp.context)
            MockAKShare.return_value.run.assert_awaited_once()
            assert result.sources["akshare"] == "success"
            assert result.is_complete is True
            assert result.added == 12

    @pytest.mark.asyncio
    async def test_calls_limit_list_strategy(self):
        dp = _make_dp()
        dp.clear_cancel()
        akshare_result = SyncResult(status="success", added=10)
        limit_result = SyncResult(status="success", added=5)
        with (
            patch("data.sync.concept_sync.AKShareConceptSyncStrategy") as MockAKShare,
            patch("data.sync.concept_sync.LimitListSyncStrategy") as MockLimitList,
        ):
            MockAKShare.return_value.run = AsyncMock(return_value=akshare_result)
            MockLimitList.return_value.run = AsyncMock(return_value=limit_result)

            result = await dp.run_concept_sync()

            MockLimitList.assert_called_once_with(dp.context)
            MockLimitList.return_value.run.assert_awaited_once()
            assert result.sources["limit_list"] == "success"
            assert result.to_summary() == "akshare=success | limit_list=success"

    @pytest.mark.asyncio
    async def test_no_ai_tag_segment_in_result(self):
        """De-AI: run_concept_sync must not emit an ai_tag segment."""
        dp = _make_dp()
        dp.clear_cancel()
        mock_result = SyncResult(status="success")
        with (
            patch("data.sync.concept_sync.AKShareConceptSyncStrategy") as MockAKShare,
            patch("data.sync.concept_sync.LimitListSyncStrategy") as MockLimitList,
        ):
            MockAKShare.return_value.run = AsyncMock(return_value=mock_result)
            MockLimitList.return_value.run = AsyncMock(return_value=mock_result)

            result = await dp.run_concept_sync()

            assert "ai_tag" not in result.sources

    @pytest.mark.asyncio
    async def test_cancelled_skips_remaining(self):
        dp = _make_dp()
        dp.clear_cancel()
        mock_result = SyncResult(status="success")
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
            assert result.status == "cancelled"
            assert result.is_complete is False
            assert result.sources == {"akshare": "cancelled", "limit_list": "cancelled"}

    @pytest.mark.asyncio
    async def test_cancel_event_propagated_to_context(self):
        """cancel_event 必须设置到 context，供长运行策略轮询取消。"""
        dp = _make_dp()
        dp.clear_cancel()
        mock_result = SyncResult(status="success")
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
    async def test_akshare_exception_retried_then_failed(self):
        """AKShare 整源异常：退避重试一次后仍失败 → akshare=failed，整体 failed 且不完整。"""
        dp = _make_dp()
        dp.clear_cancel()
        with (
            patch("data.sync.concept_sync.AKShareConceptSyncStrategy") as MockAKShare,
            patch("data.sync.concept_sync.LimitListSyncStrategy") as MockLimitList,
            patch("data.data_processor._CONCEPT_SOURCE_RETRY_DELAY_SECONDS", 0.0),
        ):
            MockAKShare.return_value.run = AsyncMock(side_effect=Exception("akshare boom"))
            MockLimitList.return_value.run = AsyncMock(return_value=SyncResult(status="success"))

            result = await dp.run_concept_sync()

        assert MockAKShare.return_value.run.await_count == 2
        assert result.sources["akshare"] == "failed"
        assert result.sources["limit_list"] == "success"
        assert result.failed_sources == ["akshare"]
        assert result.status == "failed"
        assert result.is_complete is False

    @pytest.mark.asyncio
    async def test_limit_list_exception_retried_then_failed(self):
        """LimitList 整源异常：整体 failed 且不完整（停写清理失败不得伪装完成）。"""
        dp = _make_dp()
        dp.clear_cancel()
        with (
            patch("data.sync.concept_sync.AKShareConceptSyncStrategy") as MockAKShare,
            patch("data.sync.concept_sync.LimitListSyncStrategy") as MockLimitList,
            patch("data.data_processor._CONCEPT_SOURCE_RETRY_DELAY_SECONDS", 0.0),
        ):
            MockAKShare.return_value.run = AsyncMock(return_value=SyncResult(status="success"))
            MockLimitList.return_value.run = AsyncMock(side_effect=Exception("limit boom"))

            result = await dp.run_concept_sync()

        assert MockLimitList.return_value.run.await_count == 2
        assert result.sources["limit_list"] == "failed"
        assert result.failed_sources == ["limit_list"]
        assert result.is_complete is False

    @pytest.mark.asyncio
    async def test_failed_status_retried_once_then_success(self):
        """来源返回 failed（非抛异常）也受控重试一次；第二次成功则整体完整。"""
        dp = _make_dp()
        dp.clear_cancel()
        with (
            patch("data.sync.concept_sync.AKShareConceptSyncStrategy") as MockAKShare,
            patch("data.sync.concept_sync.LimitListSyncStrategy") as MockLimitList,
            patch("data.data_processor._CONCEPT_SOURCE_RETRY_DELAY_SECONDS", 0.0),
        ):
            MockAKShare.return_value.run = AsyncMock(
                side_effect=[SyncResult(status="failed"), SyncResult(status="success", added=3)]
            )
            MockLimitList.return_value.run = AsyncMock(return_value=SyncResult(status="success"))

            result = await dp.run_concept_sync()

        assert MockAKShare.return_value.run.await_count == 2
        assert result.sources["akshare"] == "success"
        assert result.is_complete is True
        assert result.added == 3

    @pytest.mark.asyncio
    async def test_partial_source_not_complete(self):
        """来源 partial（部分板块失败）不重试但显式标记未完成（R21）。"""
        dp = _make_dp()
        dp.clear_cancel()
        akshare_result = SyncResult(status="partial", warnings=["board X failed"])
        with (
            patch("data.sync.concept_sync.AKShareConceptSyncStrategy") as MockAKShare,
            patch("data.sync.concept_sync.LimitListSyncStrategy") as MockLimitList,
        ):
            MockAKShare.return_value.run = AsyncMock(return_value=akshare_result)
            MockLimitList.return_value.run = AsyncMock(return_value=SyncResult(status="success"))

            result = await dp.run_concept_sync()

        assert MockAKShare.return_value.run.await_count == 1
        assert result.status == "partial"
        assert result.failed_sources == ["akshare"]
        assert result.is_complete is False
        assert result.warnings == ["board X failed"]

    @pytest.mark.asyncio
    async def test_both_sources_failed_status(self):
        """两个来源均 failed → 整体 failed（failed 优先于 partial）。"""
        dp = _make_dp()
        dp.clear_cancel()
        with (
            patch("data.sync.concept_sync.AKShareConceptSyncStrategy") as MockAKShare,
            patch("data.sync.concept_sync.LimitListSyncStrategy") as MockLimitList,
            patch("data.data_processor._CONCEPT_SOURCE_RETRY_DELAY_SECONDS", 0.0),
        ):
            MockAKShare.return_value.run = AsyncMock(return_value=SyncResult(status="failed"))
            MockLimitList.return_value.run = AsyncMock(return_value=SyncResult(status="failed"))

            result = await dp.run_concept_sync()

        assert result.status == "failed"
        assert result.failed_sources == ["akshare", "limit_list"]
        assert result.is_complete is False

    @pytest.mark.asyncio
    async def test_engine_disposed_propagates_without_retry(self):
        """R5：EngineDisposedError 必须传播且不重试（不得降级为 failed 结果）。"""
        dp = _make_dp()
        dp.clear_cancel()
        with (
            patch("data.sync.concept_sync.AKShareConceptSyncStrategy") as MockAKShare,
            patch("data.sync.concept_sync.LimitListSyncStrategy") as MockLimitList,
        ):
            MockAKShare.return_value.run = AsyncMock(side_effect=EngineDisposedError("engine disposed"))

            with pytest.raises(EngineDisposedError, match="engine disposed"):
                await dp.run_concept_sync()

        assert MockAKShare.return_value.run.await_count == 1
        MockLimitList.return_value.run.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates_not_swallowed(self):
        """R2 红线验证: asyncio.CancelledError 必须传播，不可被 except Exception 吞没"""
        dp = _make_dp()
        dp.clear_cancel()
        mock_result = SyncResult(status="success")
        with (
            patch("data.sync.concept_sync.AKShareConceptSyncStrategy") as MockAKShare,
            patch("data.sync.concept_sync.LimitListSyncStrategy") as MockLimitList,
        ):
            MockAKShare.return_value.run = AsyncMock(side_effect=asyncio.CancelledError())
            MockLimitList.return_value.run = AsyncMock(return_value=mock_result)
            with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion R2 红线契约仅验证 CancelledError 类型传播即可，无有意义 message 可 match
                await dp.run_concept_sync()
