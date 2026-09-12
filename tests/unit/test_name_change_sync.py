"""DATA-04 L3：NameChangeSyncStrategy 同步策略单元测试。

覆盖 `_run_impl` 主流程（全量拉取 → 清洗 → UPSERT → sync_status）、空数据降级、
异常分层（CancelledError / EngineDisposedError / TushareAPIPermissionError / generic）
与 `_record_skipped_permission` 正常路径与异常吞没。
"""

# pyright: reportAttributeAccessIssue=false

import asyncio

import pandas as pd
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from data.constants import SYNC_RESULT_SKIPPED_PERMISSION
from data.external.tushare_client import TushareAPIPermissionError
from data.persistence.daos.base_dao import EngineDisposedError
from data.sync.base import SyncContext, SyncStatus
from data.sync.name_change import NameChangeSyncStrategy

pytestmark = pytest.mark.unit


def _make_strategy() -> NameChangeSyncStrategy:
    """Build a NameChangeSyncStrategy with mocked API + DAO + sync_dao."""
    ctx = MagicMock(spec=SyncContext)
    ctx.cache = MagicMock()
    ctx.cache.engine = MagicMock()
    ctx.cache.sync_dao.update_sync_status = AsyncMock(return_value=None)
    ctx.api = MagicMock()
    ctx.processor = None

    strategy = NameChangeSyncStrategy(ctx)
    strategy.dao = MagicMock()
    strategy.dao.save_stock_name_history = AsyncMock(return_value=3)
    strategy._check_cancelled = MagicMock(return_value=False)
    return strategy


def _make_name_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000001.SZ"],
            "name": ["平安银行", "*ST平安"],
            "start_date": ["2010-01-01", "2020-06-30"],
            "end_date": ["2020-06-29", None],
            "ann_date": ["2010-06-29", "2020-06-29"],
            "change_reason": ["", "连续亏损实行退市风险警示"],
        }
    )


class TestNameChangeSyncRun:
    @pytest.mark.asyncio
    async def test_success_full_flow(self):
        """全量拉取 → UPSERT → sync_status 记录，result.added 累加。"""
        strategy = _make_strategy()
        strategy.context.api.get_namechange = AsyncMock(return_value=_make_name_df())

        result = await strategy._run_impl()

        assert result.added == 3
        strategy.dao.save_stock_name_history.assert_awaited_once()
        strategy.context.cache.sync_dao.update_sync_status.assert_awaited_once_with(
            "stock_name_history",
            strategy.context.cache.sync_dao.update_sync_status.call_args.args[1],
            3,
        )

    @pytest.mark.asyncio
    async def test_empty_df_returns_early(self):
        """空 DataFrame 直接返回，不触发 UPSERT 与 sync_status。"""
        strategy = _make_strategy()
        strategy.context.api.get_namechange = AsyncMock(return_value=pd.DataFrame())

        result = await strategy._run_impl()

        assert result.added == 0
        strategy.dao.save_stock_name_history.assert_not_awaited()
        strategy.context.cache.sync_dao.update_sync_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_none_df_returns_early(self):
        """None 返回直接返回，不触发 UPSERT 与 sync_status。"""
        strategy = _make_strategy()
        strategy.context.api.get_namechange = AsyncMock(return_value=None)

        result = await strategy._run_impl()

        assert result.added == 0
        strategy.dao.save_stock_name_history.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cancelled_before_fetch_returns(self):
        """_check_cancelled 在拉取前为 True 时直接返回 result。"""
        strategy = _make_strategy()
        strategy._check_cancelled = MagicMock(return_value=True)
        strategy.context.api.get_namechange = AsyncMock()

        await strategy._run_impl()

        strategy.context.api.get_namechange.assert_not_awaited()


class TestNameChangeSyncErrors:
    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self):
        """CancelledError 必须传播（R2）。"""
        strategy = _make_strategy()
        strategy.context.api.get_namechange = AsyncMock(side_effect=asyncio.CancelledError())
        with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion R2 红线契约仅需验证类型传播
            await strategy._run_impl()

    @pytest.mark.asyncio
    async def test_engine_disposed_records_error(self):
        """EngineDisposedError 记录 error 后返回（R5，不抛出）。"""
        strategy = _make_strategy()
        strategy.context.api.get_namechange = AsyncMock(side_effect=EngineDisposedError("disposed"))
        result = await strategy._run_impl()
        assert result.errors == ["engine disposed"]

    @pytest.mark.asyncio
    async def test_permission_denied_records_skipped(self):
        """TushareAPIPermissionError 记录权限跳过并调用 _record_skipped_permission。"""
        strategy = _make_strategy()
        strategy.context.api.get_namechange = AsyncMock(
            side_effect=TushareAPIPermissionError("namechange", "no permission")
        )
        strategy._record_skipped_permission = AsyncMock()

        result = await strategy._run_impl()

        assert result.errors == ["NameChange: permission denied"]
        strategy._record_skipped_permission.assert_awaited_once_with("stock_name_history")

    @pytest.mark.asyncio
    async def test_generic_error_marks_failed(self):
        """generic 异常（非 system 级别）记录 error 并置 FAILED。"""
        strategy = _make_strategy()
        strategy.context.api.get_namechange = AsyncMock(side_effect=RuntimeError("boom"))
        result = await strategy._run_impl()
        assert result.status == SyncStatus.FAILED.value
        assert any("NameChange: " in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_system_error_raises(self):
        """system 级别异常必须重抛。"""
        strategy = _make_strategy()
        strategy.context.api.get_namechange = AsyncMock(side_effect=RuntimeError("system failure"))
        with patch("data.sync.name_change.classify_severity", return_value="system"):
            with pytest.raises(RuntimeError):  # noqa: weak-assertion system 级异常重抛契约仅需验证类型
                await strategy._run_impl()


class TestRecordSkippedPermission:
    @pytest.mark.asyncio
    async def test_calls_update_sync_status_with_skipped_result(self):
        strategy = _make_strategy()
        await strategy._record_skipped_permission("stock_name_history")
        strategy.context.cache.sync_dao.update_sync_status.assert_awaited_once()
        call_args = strategy.context.cache.sync_dao.update_sync_status.call_args
        assert call_args.kwargs["status"] == "skipped_permission"
        assert call_args.kwargs["last_result_status"] == SYNC_RESULT_SKIPPED_PERMISSION

    @pytest.mark.asyncio
    async def test_engine_disposed_propagates(self):
        strategy = _make_strategy()
        strategy.context.cache.sync_dao.update_sync_status = AsyncMock(side_effect=EngineDisposedError("disposed"))
        with pytest.raises(EngineDisposedError):  # noqa: weak-assertion 异常传播契约仅需验证类型
            await strategy._record_skipped_permission("stock_name_history")
