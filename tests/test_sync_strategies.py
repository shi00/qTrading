"""
Tests for Data Sync Strategies.

验证数据同步策略的数据清洗、转换逻辑、异常处理等核心功能。
所有测试使用 Mock 隔离外部依赖，不连接真实数据库或 API。
"""

import datetime
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data.sync_strategies.base import SyncContext, SyncResult
from data.sync_strategies.financial import FinancialSyncStrategy
from data.sync_strategies.historical import HistoricalSyncStrategy
from data.sync_strategies.holder import HolderSyncStrategy
from data.sync_strategies.macro import MacroSyncStrategy, _parse_period


class TestSyncResult:
    """测试 SyncResult 数据类"""

    def test_default_values(self):
        """默认值测试"""
        result = SyncResult()
        assert result.added == 0
        assert result.updated == 0
        assert result.errors == []
        assert result.status == "success"
        assert result.message == ""

    def test_merge_success_results(self):
        """合并成功结果"""
        r1 = SyncResult(added=10, updated=5)
        r2 = SyncResult(added=20, updated=3)
        r1.merge(r2)
        assert r1.added == 30
        assert r1.updated == 8
        assert r1.status == "success"

    def test_merge_with_errors(self):
        """合并带错误的结果"""
        r1 = SyncResult(added=10)
        r2 = SyncResult(added=5, errors=["error1", "error2"])
        r1.merge(r2)
        assert r1.added == 15
        assert len(r1.errors) == 2
        assert r1.status == "success"

    def test_merge_failed_status(self):
        """合并失败状态"""
        r1 = SyncResult(added=10, status="success")
        r2 = SyncResult(added=5, status="failed")
        r1.merge(r2)
        assert r1.status == "failed"

    def test_merge_cancelled_status(self):
        """合并取消状态"""
        r1 = SyncResult(status="success")
        r2 = SyncResult(status="cancelled")
        r1.merge(r2)
        assert r1.status == "cancelled"

    def test_merge_partial_status(self):
        """合并部分成功状态"""
        r1 = SyncResult(status="success")
        r2 = SyncResult(status="partial")
        r1.merge(r2)
        assert r1.status == "partial"


class TestParsePeriod:
    """测试宏观数据期间解析函数"""

    def test_parse_valid_yyyymm(self):
        """解析有效的 YYYYMM 格式"""
        assert _parse_period("202401") == "2024-01-01"
        assert _parse_period("202312") == "2023-12-01"

    def test_parse_nan_value(self):
        """解析 NaN 值"""
        assert _parse_period(None) is None
        assert _parse_period(pd.NA) is None
        assert _parse_period(float("nan")) is None

    def test_parse_non_yyyymm_format(self):
        """解析非 YYYYMM 格式"""
        assert _parse_period("2024-01-01") == "2024-01-01"
        assert _parse_period("20240101") == "20240101"
        assert _parse_period("invalid") == "invalid"

    def test_parse_whitespace(self):
        """解析带空白的值"""
        assert _parse_period(" 202401 ") == "2024-01-01"


class TestMacroSyncStrategy:
    """测试宏观数据同步策略"""

    @pytest.fixture
    def mock_context(self):
        """创建模拟的同步上下文"""
        context = MagicMock(spec=SyncContext)
        context.api = AsyncMock()
        context.cache = AsyncMock()
        context.processor = AsyncMock()
        context.cache.engine = MagicMock()
        return context

    @pytest.fixture
    def strategy(self, mock_context):
        """创建策略实例"""
        return MacroSyncStrategy(mock_context)

    def test_merge_macro_data_all_sources(self, strategy):
        """测试合并所有宏观数据源"""
        df_m2 = pd.DataFrame(
            {
                "period": ["202401", "202402"],
                "m2": [1000, 1100],
                "m2_yoy": [8.5, 8.2],
            }
        )
        df_cpi = pd.DataFrame(
            {
                "period": ["202401", "202402"],
                "cpi": [0.3, 0.5],
            }
        )
        df_ppi = pd.DataFrame(
            {
                "period": ["202401", "202402"],
                "ppi": [-2.5, -2.3],
            }
        )

        merged = strategy._merge_macro_data(df_m2, df_cpi, df_ppi)

        assert merged is not None
        assert len(merged) == 2
        assert "period" in merged.columns
        assert "m2" in merged.columns
        assert "cpi" in merged.columns
        assert "ppi" in merged.columns

    def test_merge_macro_data_partial_sources(self, strategy):
        """测试部分数据源缺失时的合并"""
        df_m2 = pd.DataFrame(
            {
                "period": ["202401", "202402"],
                "m2": [1000, 1100],
            }
        )
        df_cpi = None
        df_ppi = pd.DataFrame(
            {
                "period": ["202401"],
                "ppi": [-2.5],
            }
        )

        merged = strategy._merge_macro_data(df_m2, df_cpi, df_ppi)

        assert merged is not None
        assert len(merged) == 2
        assert "cpi" not in merged.columns or merged["cpi"].isna().all()

    def test_merge_macro_data_all_empty(self, strategy):
        """测试所有数据源都为空"""
        merged = strategy._merge_macro_data(None, None, None)
        assert merged is None

        merged = strategy._merge_macro_data(
            pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
        )
        assert merged is None or merged.empty

    def test_merge_macro_data_missing_period_column(self, strategy):
        """测试缺少 period 列的情况"""
        df_m2 = pd.DataFrame(
            {
                "period": ["202401", "202402"],
                "m2": [1000, 1100],
            }
        )
        df_cpi = pd.DataFrame(
            {
                "cpi": [0.3],
            }
        )

        merged = strategy._merge_macro_data(df_m2, df_cpi, None)

        assert merged is not None
        assert "cpi" not in merged.columns

    def test_merge_indicator_basic(self, strategy):
        """测试单个指标合并"""
        merged = pd.DataFrame(
            {
                "period": ["202401", "202402"],
                "m2": [1000, 1100],
            }
        )
        df_cpi = pd.DataFrame(
            {
                "period": ["202401", "202402", "202403"],
                "cpi": [0.3, 0.5, 0.4],
            }
        )

        result = MacroSyncStrategy._merge_indicator(merged, df_cpi, "cpi")

        assert result is not None
        assert len(result) == 3
        assert "cpi" in result.columns

    def test_merge_indicator_empty_df(self, strategy):
        """测试合并空 DataFrame"""
        merged = pd.DataFrame(
            {
                "period": ["202401"],
                "m2": [1000],
            }
        )

        result = MacroSyncStrategy._merge_indicator(merged, None, "cpi")
        assert len(result) == 1
        assert "cpi" not in result.columns

        result = MacroSyncStrategy._merge_indicator(merged, pd.DataFrame(), "cpi")
        assert len(result) == 1

    def test_merge_indicator_missing_target_col(self, strategy):
        """测试目标列缺失"""
        merged = pd.DataFrame(
            {
                "period": ["202401"],
                "m2": [1000],
            }
        )
        df = pd.DataFrame(
            {
                "period": ["202401"],
                "other": [1.0],
            }
        )

        result = MacroSyncStrategy._merge_indicator(merged, df, "cpi")
        assert "cpi" not in result.columns

    @pytest.mark.asyncio
    async def test_run_with_cancellation(self, strategy, mock_context):
        """测试取消操作"""
        mock_context.api.get_macro_data = AsyncMock(
            return_value=pd.DataFrame({"period": ["202401"], "m2": [1000]})
        )
        mock_context.cache.engine = MagicMock()

        await strategy.cancel()
        assert strategy._cancelled is True

    @pytest.mark.asyncio
    async def test_run_handles_api_error(self, strategy, mock_context):
        """测试 API 错误处理 - 错误被记录但不会导致整体失败"""
        mock_context.api.get_macro_data = AsyncMock(side_effect=Exception("API Error"))
        mock_context.cache.engine = MagicMock()
        mock_dao = AsyncMock()
        mock_dao.get_macro_latest_date = AsyncMock(return_value=None)
        mock_dao.get_shibor_latest_date = AsyncMock(return_value=None)
        mock_dao.save_macro_economy = AsyncMock(return_value=0)
        mock_dao.save_shibor_daily = AsyncMock(return_value=0)

        with patch("data.sync_strategies.macro.MacroDao", return_value=mock_dao):
            mock_context.cache.market_dao = AsyncMock()
            mock_context.cache.market_dao.get_latest_index_weight_date = AsyncMock(
                return_value=None
            )
            mock_context.cache.update_sync_status = AsyncMock()
            mock_context.cache.save_index_weights = AsyncMock(return_value=0)
            mock_context.processor.get_trade_dates = AsyncMock(
                return_value=[datetime.date(2024, 1, 1)]
            )

            strategy_with_dao = MacroSyncStrategy(mock_context)
            strategy_with_dao.dao = mock_dao

            result = await strategy_with_dao.run()

        assert len(result.errors) > 0


class TestHolderSyncStrategy:
    """测试股东数据同步策略"""

    @pytest.fixture
    def mock_context(self):
        """创建模拟的同步上下文"""
        context = MagicMock(spec=SyncContext)
        context.api = AsyncMock()
        context.cache = AsyncMock()
        return context

    @pytest.fixture
    def strategy(self, mock_context):
        """创建策略实例"""
        return HolderSyncStrategy(mock_context)

    def test_get_recent_quarter_ends(self):
        """测试获取最近季度末日期"""
        quarter_ends = HolderSyncStrategy._get_recent_quarter_ends(count=2)
        assert len(quarter_ends) == 2
        assert len(quarter_ends[0]) == 8
        assert len(quarter_ends[1]) == 8

    def test_get_recent_quarter_ends_beginning_of_year(self):
        """测试年初获取季度末"""
        quarter_ends = HolderSyncStrategy._get_recent_quarter_ends(count=2)
        assert len(quarter_ends) == 2
        assert quarter_ends[0] > quarter_ends[1]

    @pytest.mark.asyncio
    async def test_sync_one_table_success(self, strategy, mock_context):
        """测试单表同步成功"""
        mock_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "holder_num": [100, 200],
            }
        )
        mock_context.api.get_stk_holdernumber = AsyncMock(return_value=mock_df)
        mock_context.cache.save_holder_number = AsyncMock()

        count = await strategy._sync_one_table(
            api_func=mock_context.api.get_stk_holdernumber,
            save_func=mock_context.cache.save_holder_number,
            table_name="stk_holdernumber",
            end_date="20231231",
        )

        assert count == 2
        mock_context.cache.save_holder_number.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_one_table_empty_data(self, strategy, mock_context):
        """测试单表同步返回空数据"""
        mock_context.api.get_stk_holdernumber = AsyncMock(return_value=pd.DataFrame())

        count = await strategy._sync_one_table(
            api_func=mock_context.api.get_stk_holdernumber,
            save_func=mock_context.cache.save_holder_number,
            table_name="stk_holdernumber",
            end_date="20231231",
        )

        assert count == 0

    @pytest.mark.asyncio
    async def test_sync_one_table_api_error(self, strategy, mock_context):
        """测试单表同步 API 错误"""
        mock_context.api.get_stk_holdernumber = AsyncMock(
            side_effect=Exception("API Error")
        )

        count = await strategy._sync_one_table(
            api_func=mock_context.api.get_stk_holdernumber,
            save_func=mock_context.cache.save_holder_number,
            table_name="stk_holdernumber",
            end_date="20231231",
        )

        assert count == -1

    @pytest.mark.asyncio
    async def test_sync_one_table_permission_denied(self, strategy, mock_context):
        """测试单表同步权限不足"""
        mock_context.api.get_stk_holdernumber = AsyncMock(
            side_effect=Exception("permission denied")
        )

        count = await strategy._sync_one_table(
            api_func=mock_context.api.get_stk_holdernumber,
            save_func=mock_context.cache.save_holder_number,
            table_name="stk_holdernumber",
            end_date="20231231",
        )

        assert count == -1

    @pytest.mark.asyncio
    async def test_sync_pledge_stat_success(self, strategy, mock_context):
        """测试质押数据同步成功"""
        mock_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "pledge_count": [10],
            }
        )
        mock_context.api.get_pledge_stat = AsyncMock(return_value=mock_df)
        mock_context.cache.save_pledge_stat = AsyncMock()

        count = await strategy._sync_pledge_stat()

        assert count == 1

    @pytest.mark.asyncio
    async def test_run_with_cancellation(self, strategy, mock_context):
        """测试取消操作"""
        mock_context.api.get_stk_holdernumber = AsyncMock(return_value=pd.DataFrame())

        await strategy.cancel()
        assert strategy._cancelled is True

    @pytest.mark.asyncio
    async def test_run_circuit_breaker(self, strategy, mock_context):
        """测试熔断机制"""
        mock_context.api.get_stk_holdernumber = AsyncMock(
            side_effect=Exception("API Error")
        )
        mock_context.api.get_top10_holders = AsyncMock(
            side_effect=Exception("API Error")
        )
        mock_context.api.get_pledge_stat = AsyncMock(side_effect=Exception("API Error"))

        result = await strategy.run()

        assert result.status == "partial"


class TestHistoricalSyncStrategy:
    """测试历史数据同步策略"""

    @pytest.fixture
    def mock_context(self):
        """创建模拟的同步上下文"""
        context = MagicMock(spec=SyncContext)
        context.api = AsyncMock()
        context.cache = AsyncMock()
        context.processor = AsyncMock()
        return context

    @pytest.fixture
    def strategy(self, mock_context):
        """创建策略实例"""
        return HistoricalSyncStrategy(mock_context)

    @pytest.mark.asyncio
    async def test_run_empty_trade_dates(self, strategy, mock_context):
        """测试无交易日数据"""
        mock_context.processor.trade_calendar.get_trade_dates = AsyncMock(
            return_value=[]
        )

        result = await strategy.run(days=30)

        assert result.status == "failed"
        assert "No trade dates found" in result.errors

    @pytest.mark.asyncio
    async def test_run_with_cancellation(self, strategy, mock_context):
        """测试取消操作"""
        mock_context.processor.trade_calendar.get_trade_dates = AsyncMock(
            return_value=[datetime.date(2024, 1, 1)]
        )
        mock_context.cache.get_cached_dates_for_table = AsyncMock(return_value=set())

        await strategy.cancel()
        assert strategy._shutdown_event.is_set()

    @pytest.mark.asyncio
    async def test_run_breakpoint_resume(self, strategy, mock_context):
        """测试断点续传"""
        mock_context.processor.trade_calendar.get_trade_dates = AsyncMock(
            return_value=[
                datetime.date(2024, 1, 1),
                datetime.date(2024, 1, 2),
                datetime.date(2024, 1, 3),
            ]
        )
        mock_context.cache.get_cached_dates_for_table = AsyncMock(
            return_value={"20240101", "20240102", "20240103"}
        )

        result = await strategy.run(days=30)

        assert result.updated == 3

    @pytest.mark.asyncio
    async def test_sync_daily_market_snapshot_critical_failure(
        self, strategy, mock_context
    ):
        """测试关键数据获取失败"""
        mock_context.api.get_daily_quotes = AsyncMock(
            side_effect=Exception("Quotes API Error")
        )
        mock_context.cache.check_data_exists = AsyncMock(return_value=False)

        with pytest.raises(Exception, match="Quotes API Error"):
            await strategy.sync_daily_market_snapshot("20240101", force=True)

    @pytest.mark.asyncio
    async def test_sync_daily_market_snapshot_success(self, strategy, mock_context):
        """测试日线数据快照同步成功"""
        mock_quotes = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": ["20240101"],
                "open": [10.0],
                "high": [10.5],
                "low": [9.5],
                "close": [10.2],
                "vol": [1000000],
                "amount": [10000000.0],
                "adj_factor": [1.0],
            }
        )
        mock_basic = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": ["20240101"],
                "total_mv": [1000000.0],
            }
        )

        mock_context.api.get_daily_quotes = AsyncMock(return_value=mock_quotes)
        mock_context.api.get_daily_basic = AsyncMock(return_value=mock_basic)
        mock_context.api.get_limit_list = AsyncMock(return_value=pd.DataFrame())
        mock_context.api.get_suspend_d = AsyncMock(return_value=pd.DataFrame())
        mock_context.api.get_margin_detail = AsyncMock(return_value=pd.DataFrame())
        mock_context.api.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
        mock_context.api.get_hk_hold = AsyncMock(return_value=pd.DataFrame())
        mock_context.api.get_moneyflow_hsgt = AsyncMock(return_value=pd.DataFrame())
        mock_context.api.get_top_list = AsyncMock(return_value=pd.DataFrame())
        mock_context.api.get_block_trade = AsyncMock(return_value=pd.DataFrame())
        mock_context.api.get_index_dailybasic = AsyncMock(return_value=pd.DataFrame())
        mock_context.api.get_index_daily = AsyncMock(return_value=pd.DataFrame())

        mock_context.cache.check_data_exists = AsyncMock(return_value=False)
        mock_context.cache.save_daily_quotes = AsyncMock(return_value=1)
        mock_context.cache.save_daily_indicators = AsyncMock(return_value=1)
        mock_context.cache.update_sync_status = AsyncMock()

        await strategy.sync_daily_market_snapshot("20240101", force=True)

        mock_context.cache.save_daily_quotes.assert_called_once()
        mock_context.cache.save_daily_indicators.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_daily_market_snapshot_missing_adj_factor(
        self, strategy, mock_context
    ):
        """测试缺少复权因子列的警告"""
        mock_quotes = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": ["20240101"],
                "open": [10.0],
                "high": [10.5],
                "low": [9.5],
                "close": [10.2],
                "vol": [1000000],
            }
        )
        mock_basic = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": ["20240101"],
                "total_mv": [1000000.0],
            }
        )

        mock_context.api.get_daily_quotes = AsyncMock(return_value=mock_quotes)
        mock_context.api.get_daily_basic = AsyncMock(return_value=mock_basic)
        mock_context.api.get_limit_list = AsyncMock(return_value=pd.DataFrame())
        mock_context.api.get_suspend_d = AsyncMock(return_value=pd.DataFrame())
        mock_context.api.get_margin_detail = AsyncMock(return_value=pd.DataFrame())
        mock_context.api.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
        mock_context.api.get_hk_hold = AsyncMock(return_value=pd.DataFrame())
        mock_context.api.get_moneyflow_hsgt = AsyncMock(return_value=pd.DataFrame())
        mock_context.api.get_top_list = AsyncMock(return_value=pd.DataFrame())
        mock_context.api.get_block_trade = AsyncMock(return_value=pd.DataFrame())
        mock_context.api.get_index_dailybasic = AsyncMock(return_value=pd.DataFrame())
        mock_context.api.get_index_daily = AsyncMock(return_value=pd.DataFrame())

        mock_context.cache.check_data_exists = AsyncMock(return_value=False)
        mock_context.cache.save_daily_quotes = AsyncMock(return_value=1)
        mock_context.cache.save_daily_indicators = AsyncMock(return_value=1)
        mock_context.cache.update_sync_status = AsyncMock()

        await strategy.sync_daily_market_snapshot("20240101", force=True)

        mock_context.cache.save_daily_quotes.assert_called_once()


class TestFinancialSyncStrategy:
    """测试财务数据同步策略"""

    @pytest.fixture
    def mock_context(self):
        """创建模拟的同步上下文"""
        context = MagicMock(spec=SyncContext)
        context.api = AsyncMock()
        context.cache = AsyncMock()
        context.processor = AsyncMock()
        return context

    @pytest.fixture
    def strategy(self, mock_context):
        """创建策略实例"""
        return FinancialSyncStrategy(mock_context)

    @pytest.mark.asyncio
    async def test_run_first_time_full_sync(self, strategy, mock_context):
        """测试首次运行触发全量同步"""
        mock_context.cache.get_sync_status = AsyncMock(return_value={})
        mock_context.cache.get_stock_basic = AsyncMock(return_value=pd.DataFrame())

        result = await strategy.run()

        assert result.status == "failed"
        assert "No stocks found" in result.errors[0]

    @pytest.mark.asyncio
    async def test_run_force_full_sync(self, strategy, mock_context):
        """测试强制全量同步"""
        mock_context.cache.get_stock_basic = AsyncMock(return_value=pd.DataFrame())
        mock_context.cache.clear_step4_sync_status = AsyncMock()

        result = await strategy.run(force=True)

        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_run_with_cancellation(self, strategy, mock_context):
        """测试取消操作"""
        await strategy.cancel()
        assert strategy._shutdown_event.is_set()

    @pytest.mark.asyncio
    async def test_run_incremental_sync(self, strategy, mock_context):
        """测试增量同步"""
        mock_context.cache.get_sync_status = AsyncMock(
            return_value={"last_sync_date": "2024-01-01 00:00:00"}
        )
        mock_context.api.get_disclosure_date = AsyncMock(return_value=pd.DataFrame())

        result = await strategy.run()

        assert result.status == "success"

    @pytest.mark.asyncio
    async def test_full_sync_resume_logic(self, strategy, mock_context):
        """测试全量同步断点续传"""
        mock_context.cache.get_sync_status = AsyncMock(return_value={})
        mock_context.cache.get_stock_basic = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"],
                    "list_status": ["L", "L", "L"],
                }
            )
        )
        mock_context.cache.get_completed_step4_stocks = AsyncMock(
            return_value={"000001.SZ"}
        )
        mock_context.processor.get_trade_dates = AsyncMock(
            return_value=[datetime.date(2024, 1, 1)]
        )
        mock_context.cache.clear_step4_sync_status = AsyncMock()

        mock_context.api.get_income = AsyncMock(return_value=pd.DataFrame())
        mock_context.api.get_balancesheet = AsyncMock(return_value=pd.DataFrame())
        mock_context.api.get_cashflow = AsyncMock(return_value=pd.DataFrame())
        mock_context.api.get_fina_indicator = AsyncMock(return_value=pd.DataFrame())
        mock_context.api.get_fina_audit = AsyncMock(return_value=pd.DataFrame())
        mock_context.api.get_fina_mainbz = AsyncMock(return_value=pd.DataFrame())

        mock_context.cache.save_financial_reports = AsyncMock(return_value=0)
        mock_context.cache.save_fina_audit = AsyncMock(return_value=0)
        mock_context.cache.save_fina_mainbz = AsyncMock(return_value=0)
        mock_context.cache.mark_stock_step4_completed = AsyncMock()

        with patch(
            "utils.config_handler.ConfigHandler.get_max_batch_rows", return_value=10
        ), patch(
            "utils.config_handler.ConfigHandler.get_sync_max_concurrent_heavy",
            return_value=1,
        ):
            result = await strategy.run(force=True)

        assert result.updated == 1


class TestSyncContextDataCleaning:
    """测试数据清洗场景"""

    def test_dirty_dataframe_with_nulls(self):
        """测试含空值的脏数据"""
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", None, "000003.SZ"],
                "trade_date": ["20240101", "20240102", None],
                "close": [10.0, None, 12.0],
            }
        )

        df_clean = df.dropna(subset=["ts_code", "trade_date"])

        assert len(df_clean) == 1
        assert df_clean["ts_code"].iloc[0] == "000001.SZ"

    def test_dataframe_with_duplicate_rows(self):
        """测试含重复行的数据"""
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ", "000002.SZ"],
                "trade_date": ["20240101", "20240101", "20240101"],
                "close": [10.0, 10.0, 12.0],
            }
        )

        df_dedup = df.drop_duplicates(subset=["ts_code", "trade_date"])

        assert len(df_dedup) == 2

    def test_dataframe_with_invalid_dates(self):
        """测试含无效日期的数据"""
        df = pd.DataFrame(
            {
                "trade_date": ["20240101", "invalid", "20240103"],
                "close": [10.0, 11.0, 12.0],
            }
        )

        df["trade_date"] = pd.to_datetime(
            df["trade_date"], format="%Y%m%d", errors="coerce"
        )
        df_valid = df.dropna(subset=["trade_date"])

        assert len(df_valid) == 2

    def test_dataframe_with_negative_values(self):
        """测试含负值的数据（如成交量不应为负）"""
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"],
                "vol": [1000000, -500, 2000000],
            }
        )

        df_valid = df[df["vol"] >= 0]

        assert len(df_valid) == 2

    def test_dataframe_with_outliers(self):
        """测试含异常值的数据"""
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"],
                "pct_chg": [2.5, 500.0, -3.2],
            }
        )

        df_normal = df[(df["pct_chg"] >= -20) & (df["pct_chg"] <= 20)]

        assert len(df_normal) == 2
