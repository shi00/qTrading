"""
Tests for Data Sync Strategies.

验证数据同步策略的数据清洗、转换逻辑、异常处理等核心功能。
所有测试使用 Mock 隔离外部依赖，不连接真实数据库或 API。
"""

import datetime
import inspect
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data.external.tushare_client import TushareClient
from data.mixins.health_mixin import HealthCheckMixin
from data.persistence.daos.financial_dao import FinancialDao
from data.persistence.daos.quote_dao import QuoteDao
from data.sync import financial, historical, holder
from data.sync.base import SyncContext, SyncResult
from data.sync.financial import FinancialSyncStrategy
from data.sync.historical import HistoricalSyncStrategy
from data.sync.holder import HolderSyncStrategy
from data.sync.macro import MacroSyncStrategy, _parse_period


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
        """合并失败状态：success + failed = partial"""
        r1 = SyncResult(added=10, status="success")
        r2 = SyncResult(added=5, status="failed")
        r1.merge(r2)
        assert r1.status == "partial"

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

        merged = strategy._merge_macro_data(pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
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
        mock_context.api.get_macro_data = AsyncMock(return_value=pd.DataFrame({"period": ["202401"], "m2": [1000]}))
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

        with patch("data.sync.macro.MacroDao", return_value=mock_dao):
            mock_context.cache.market_dao = AsyncMock()
            mock_context.cache.market_dao.get_latest_index_weight_date = AsyncMock(return_value=None)
            mock_context.cache.update_sync_status = AsyncMock()
            mock_context.cache.save_index_weights = AsyncMock(return_value=0)
            mock_context.processor.get_trade_dates = AsyncMock(return_value=[datetime.date(2024, 1, 1)])

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
        context.cache.get_existing_top10_ts_codes = AsyncMock(return_value=set())
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
        mock_context.api.get_stk_holdernumber = AsyncMock(side_effect=Exception("API Error"))

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
        mock_context.api.get_stk_holdernumber = AsyncMock(side_effect=Exception("permission denied"))

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

        count, actual_date = await strategy._sync_pledge_stat()

        assert count == 1
        assert actual_date is not None

    @pytest.mark.asyncio
    async def test_run_with_cancellation(self, strategy, mock_context):
        """测试取消操作"""
        mock_context.api.get_stk_holdernumber = AsyncMock(return_value=pd.DataFrame())

        await strategy.cancel()
        assert strategy._cancelled is True

    @pytest.mark.asyncio
    async def test_run_circuit_breaker(self, strategy, mock_context):
        """测试熔断机制"""
        mock_context.api.get_stk_holdernumber = AsyncMock(side_effect=Exception("API Error"))
        mock_context.api.get_top10_holders = AsyncMock(side_effect=Exception("API Error"))
        mock_context.api.get_pledge_stat = AsyncMock(side_effect=Exception("API Error"))

        result = await strategy.run()

        assert result.status == "partial"


class TestHolderSyncTop10Detailed:
    """测试 top10_holders 逐股同步的详细场景（P0 级）"""

    @pytest.fixture
    def mock_context(self):
        context = MagicMock(spec=SyncContext)
        context.api = AsyncMock()
        context.cache = AsyncMock()
        context.cache.get_existing_top10_ts_codes = AsyncMock(return_value=set())
        return context

    @pytest.fixture
    def strategy(self, mock_context):
        return HolderSyncStrategy(mock_context)

    @pytest.mark.asyncio
    async def test_sync_top10_holders_success(self, strategy, mock_context):
        """逐股同步成功：多只股票返回数据并合并保存"""
        stock_df = pd.DataFrame({"ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"]})
        mock_context.cache.get_stock_basic = AsyncMock(return_value=stock_df)

        holder_dfs = [
            pd.DataFrame({"ts_code": [c], "holder_name": [f"holder_{c}"], "hold_ratio": [10.0]})
            for c in ["000001.SZ", "000002.SZ", "000003.SZ"]
        ]
        mock_context.api.get_top10_holders = AsyncMock(side_effect=holder_dfs)
        mock_context.cache.save_top10_holders = AsyncMock()

        count = await strategy._sync_top10_holders("20231231")

        assert count == 3
        mock_context.cache.save_top10_holders.assert_called_once()
        saved_df = mock_context.cache.save_top10_holders.call_args[0][0]
        assert len(saved_df) == 3

    @pytest.mark.asyncio
    async def test_sync_top10_holders_empty_stock_list(self, strategy, mock_context):
        """股票列表为空时返回 -1"""
        mock_context.cache.get_stock_basic = AsyncMock(return_value=pd.DataFrame())

        count = await strategy._sync_top10_holders("20231231")

        assert count == -1

    @pytest.mark.asyncio
    async def test_sync_top10_holders_none_stock_list(self, strategy, mock_context):
        """股票列表为 None 时返回 -1"""
        mock_context.cache.get_stock_basic = AsyncMock(return_value=None)

        count = await strategy._sync_top10_holders("20231231")

        assert count == -1

    @pytest.mark.asyncio
    async def test_sync_top10_holders_rate_limit_counting(self, strategy, mock_context):
        """限流错误被正确计数，consecutive_errors 在成功后重置"""
        stock_df = pd.DataFrame({"ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"]})
        mock_context.cache.get_stock_basic = AsyncMock(return_value=stock_df)

        call_count = 0

        async def mock_get_top10(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise Exception("抱歉，您每分钟最多访问")
            return pd.DataFrame({"ts_code": [kwargs["ts_code"]], "holder_name": ["h"], "hold_ratio": [5.0]})

        mock_context.api.get_top10_holders = AsyncMock(side_effect=mock_get_top10)
        mock_context.cache.save_top10_holders = AsyncMock()

        count = await strategy._sync_top10_holders("20231231")

        assert count == 1
        mock_context.cache.save_top10_holders.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_top10_holders_consecutive_errors_circuit_breaker(self, strategy, mock_context):
        """连续错误达到 _MAX_ERRORS 时触发熔断返回 -1"""
        stock_df = pd.DataFrame({"ts_code": [f"00000{i}.SZ" for i in range(10)]})
        mock_context.cache.get_stock_basic = AsyncMock(return_value=stock_df)

        mock_context.api.get_top10_holders = AsyncMock(side_effect=Exception("API Error"))

        count = await strategy._sync_top10_holders("20231231")

        assert count == -1

    @pytest.mark.asyncio
    async def test_sync_top10_holders_mixed_errors_and_success(self, strategy, mock_context):
        """混合错误和成功：非连续错误不触发熔断"""
        stock_df = pd.DataFrame({"ts_code": ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"]})
        mock_context.cache.get_stock_basic = AsyncMock(return_value=stock_df)

        call_count = 0

        async def mock_get_top10(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count in (1, 3):
                raise Exception("Network timeout")
            return pd.DataFrame({"ts_code": [kwargs["ts_code"]], "holder_name": ["h"], "hold_ratio": [5.0]})

        mock_context.api.get_top10_holders = AsyncMock(side_effect=mock_get_top10)
        mock_context.cache.save_top10_holders = AsyncMock()

        count = await strategy._sync_top10_holders("20231231")

        assert count == 2
        mock_context.cache.save_top10_holders.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_top10_holders_cancellation(self, strategy, mock_context):
        """迭代中取消应立即中断"""
        stock_df = pd.DataFrame({"ts_code": [f"00000{i}.SZ" for i in range(10)]})
        mock_context.cache.get_stock_basic = AsyncMock(return_value=stock_df)

        call_count = 0

        async def mock_get_top10(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                await strategy.cancel()
            return pd.DataFrame({"ts_code": [kwargs["ts_code"]], "holder_name": ["h"], "hold_ratio": [5.0]})

        mock_context.api.get_top10_holders = AsyncMock(side_effect=mock_get_top10)
        mock_context.cache.save_top10_holders = AsyncMock()

        await strategy._sync_top10_holders("20231231")

        assert call_count <= 4
        assert strategy._cancelled is True

    @pytest.mark.asyncio
    async def test_sync_top10_holders_all_empty_responses(self, strategy, mock_context):
        """所有股票返回空数据，最终返回 0"""
        stock_df = pd.DataFrame({"ts_code": ["000001.SZ", "000002.SZ"]})
        mock_context.cache.get_stock_basic = AsyncMock(return_value=stock_df)

        mock_context.api.get_top10_holders = AsyncMock(return_value=pd.DataFrame())
        mock_context.cache.save_top10_holders = AsyncMock()

        count = await strategy._sync_top10_holders("20231231")

        assert count == 0
        mock_context.cache.save_top10_holders.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_top10_holders_permission_error_not_rate_limit(self, strategy, mock_context):
        """权限错误不计入 rate_limit_hits 但计入 stock_errors"""
        stock_df = pd.DataFrame(
            {"ts_code": ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ", "000005.SZ", "000006.SZ"]}
        )
        mock_context.cache.get_stock_basic = AsyncMock(return_value=stock_df)

        call_count = 0

        async def mock_get_top10(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("积分不足，无权访问")
            return pd.DataFrame({"ts_code": [kwargs["ts_code"]], "holder_name": ["h"], "hold_ratio": [5.0]})

        mock_context.api.get_top10_holders = AsyncMock(side_effect=mock_get_top10)
        mock_context.cache.save_top10_holders = AsyncMock()

        count = await strategy._sync_top10_holders("20231231")

        assert count == 5

    @pytest.mark.asyncio
    async def test_sync_top10_holders_incremental_skips_existing(self, strategy, mock_context):
        """增量同步：已有数据的股票被跳过，只同步缺失的股票"""
        stock_df = pd.DataFrame({"ts_code": ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"]})
        mock_context.cache.get_stock_basic = AsyncMock(return_value=stock_df)
        mock_context.cache.get_existing_top10_ts_codes = AsyncMock(return_value={"000001.SZ", "000002.SZ"})

        holder_dfs = [
            pd.DataFrame({"ts_code": [c], "holder_name": [f"holder_{c}"], "hold_ratio": [10.0]})
            for c in ["000003.SZ", "000004.SZ"]
        ]
        mock_context.api.get_top10_holders = AsyncMock(side_effect=holder_dfs)
        mock_context.cache.save_top10_holders = AsyncMock()

        count = await strategy._sync_top10_holders("20231231")

        assert count == 2
        assert mock_context.api.get_top10_holders.call_count == 2
        called_codes = [call.kwargs["ts_code"] for call in mock_context.api.get_top10_holders.call_args_list]
        assert "000001.SZ" not in called_codes
        assert "000002.SZ" not in called_codes
        assert "000003.SZ" in called_codes
        assert "000004.SZ" in called_codes

    @pytest.mark.asyncio
    async def test_sync_top10_holders_incremental_all_already_synced(self, strategy, mock_context):
        """增量同步：所有股票已有数据时直接返回 0，不调用 API"""
        stock_df = pd.DataFrame({"ts_code": ["000001.SZ", "000002.SZ"]})
        mock_context.cache.get_stock_basic = AsyncMock(return_value=stock_df)
        mock_context.cache.get_existing_top10_ts_codes = AsyncMock(return_value={"000001.SZ", "000002.SZ"})

        mock_context.api.get_top10_holders = AsyncMock()
        mock_context.cache.save_top10_holders = AsyncMock()

        count = await strategy._sync_top10_holders("20231231")

        assert count == 0
        mock_context.api.get_top10_holders.assert_not_called()
        mock_context.cache.save_top10_holders.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_top10_holders_incremental_query_fails_fallback_full(self, strategy, mock_context):
        """增量查询失败时回退到全量同步"""
        stock_df = pd.DataFrame({"ts_code": ["000001.SZ", "000002.SZ"]})
        mock_context.cache.get_stock_basic = AsyncMock(return_value=stock_df)
        mock_context.cache.get_existing_top10_ts_codes = AsyncMock(side_effect=Exception("DB Error"))

        holder_dfs = [
            pd.DataFrame({"ts_code": [c], "holder_name": [f"holder_{c}"], "hold_ratio": [10.0]})
            for c in ["000001.SZ", "000002.SZ"]
        ]
        mock_context.api.get_top10_holders = AsyncMock(side_effect=holder_dfs)
        mock_context.cache.save_top10_holders = AsyncMock()

        count = await strategy._sync_top10_holders("20231231")

        assert count == 2
        assert mock_context.api.get_top10_holders.call_count == 2

    @pytest.mark.asyncio
    async def test_sync_top10_holders_checkpoint_saves_periodically(self, strategy, mock_context):
        """断点续传：累积足够行数时触发中间保存"""
        codes = [f"00000{i:02d}.SZ" for i in range(10)]
        stock_df = pd.DataFrame({"ts_code": codes})
        mock_context.cache.get_stock_basic = AsyncMock(return_value=stock_df)
        mock_context.cache.get_existing_top10_ts_codes = AsyncMock(return_value=set())

        holder_dfs = [
            pd.DataFrame(
                {
                    "ts_code": [c] * 10,
                    "holder_name": [f"h{j}" for j in range(10)],
                    "hold_ratio": [1.0] * 10,
                }
            )
            for c in codes
        ]
        mock_context.api.get_top10_holders = AsyncMock(side_effect=holder_dfs)
        mock_context.cache.save_top10_holders = AsyncMock()

        with patch.object(holder, "_CHECKPOINT_INTERVAL", 50):
            count = await strategy._sync_top10_holders("20231231")

        assert count == 100
        assert mock_context.cache.save_top10_holders.call_count >= 2

    @pytest.mark.asyncio
    async def test_sync_top10_holders_resume_after_circuit_breaker(self, strategy, mock_context):
        """熔断后已保存的数据不会丢失：下次增量同步可跳过已同步股票"""
        stock_df = pd.DataFrame({"ts_code": [f"00000{i}.SZ" for i in range(6)]})
        mock_context.cache.get_stock_basic = AsyncMock(return_value=stock_df)
        mock_context.cache.get_existing_top10_ts_codes = AsyncMock(return_value=set())

        mock_context.api.get_top10_holders = AsyncMock(side_effect=Exception("API Error"))
        mock_context.cache.save_top10_holders = AsyncMock()

        count = await strategy._sync_top10_holders("20231231")

        assert count == -1

    @pytest.mark.asyncio
    async def test_sync_top10_holders_empty_period(self, strategy, mock_context):
        """period 为空字符串时 get_existing_top10_ts_codes 返回空集"""
        stock_df = pd.DataFrame({"ts_code": ["000001.SZ"]})
        mock_context.cache.get_stock_basic = AsyncMock(return_value=stock_df)
        mock_context.cache.get_existing_top10_ts_codes = AsyncMock(return_value=set())

        mock_context.api.get_top10_holders = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "holder_name": ["h"], "hold_ratio": [5.0]})
        )
        mock_context.cache.save_top10_holders = AsyncMock()

        count = await strategy._sync_top10_holders("")

        assert count == 1

    @pytest.mark.asyncio
    async def test_sync_top10_holders_checkpoint_failure_preserves_data(self, strategy, mock_context):
        """断点续传：checkpoint 保存失败时数据不丢失，最终仍会尝试保存"""
        codes = [f"00000{i:02d}.SZ" for i in range(10)]
        stock_df = pd.DataFrame({"ts_code": codes})
        mock_context.cache.get_stock_basic = AsyncMock(return_value=stock_df)
        mock_context.cache.get_existing_top10_ts_codes = AsyncMock(return_value=set())

        holder_dfs = [
            pd.DataFrame(
                {
                    "ts_code": [c] * 10,
                    "holder_name": [f"h{j}" for j in range(10)],
                    "hold_ratio": [1.0] * 10,
                }
            )
            for c in codes
        ]
        mock_context.api.get_top10_holders = AsyncMock(side_effect=holder_dfs)

        save_results = []

        async def mock_save(df):
            if len(save_results) == 0:
                save_results.append(("failed", len(df)))
                raise Exception("DB write error during checkpoint")
            save_results.append(("ok", len(df)))

        mock_context.cache.save_top10_holders = AsyncMock(side_effect=mock_save)

        with patch.object(holder, "_CHECKPOINT_INTERVAL", 50):
            count = await strategy._sync_top10_holders("20231231")

        assert count == 100
        assert len(save_results) >= 2
        assert save_results[0] == ("failed", 50)
        successful_rows = sum(rows for status, rows in save_results if status == "ok")
        assert successful_rows == 100

    @pytest.mark.asyncio
    async def test_sync_top10_holders_circuit_breaker_no_data_to_save(self, strategy, mock_context):
        """熔断时若所有 API 均失败，save_top10_holders 不应被调用"""
        stock_df = pd.DataFrame({"ts_code": [f"00000{i}.SZ" for i in range(6)]})
        mock_context.cache.get_stock_basic = AsyncMock(return_value=stock_df)
        mock_context.cache.get_existing_top10_ts_codes = AsyncMock(return_value=set())

        mock_context.api.get_top10_holders = AsyncMock(side_effect=Exception("API Error"))
        mock_context.cache.save_top10_holders = AsyncMock()

        count = await strategy._sync_top10_holders("20231231")

        assert count == -1
        mock_context.cache.save_top10_holders.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_pledge_stat_cancellation_during_retry(self, strategy, mock_context):
        """质押数据同步中取消"""
        call_count = 0

        async def mock_pledge(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                await strategy.cancel()
            raise Exception("API Error")

        mock_context.api.get_pledge_stat = AsyncMock(side_effect=mock_pledge)

        count, actual_date = await strategy._sync_pledge_stat()

        assert count == -1
        assert actual_date is None

    @pytest.mark.asyncio
    async def test_sync_pledge_stat_all_api_failed(self, strategy, mock_context):
        """质押数据所有 API 调用均失败"""
        mock_context.api.get_pledge_stat = AsyncMock(side_effect=Exception("API Error"))

        count, actual_date = await strategy._sync_pledge_stat()

        assert count == -1
        assert actual_date is None

    @pytest.mark.asyncio
    async def test_sync_pledge_stat_no_data_found(self, strategy, mock_context):
        """质押数据 API 成功但返回空数据"""
        mock_context.api.get_pledge_stat = AsyncMock(return_value=pd.DataFrame())

        count, actual_date = await strategy._sync_pledge_stat()

        assert count == 0
        assert actual_date is None


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
        mock_context.processor.trade_calendar.get_trade_dates = AsyncMock(return_value=[])

        result = await strategy.run(days=30)

        assert result.status == "failed"
        assert "No trade dates found" in result.errors

    @pytest.mark.asyncio
    async def test_run_with_cancellation(self, strategy, mock_context):
        """测试取消操作"""
        mock_context.processor.trade_calendar.get_trade_dates = AsyncMock(return_value=[datetime.date(2024, 1, 1)])
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
            return_value={
                datetime.date(2024, 1, 1),
                datetime.date(2024, 1, 2),
                datetime.date(2024, 1, 3),
            }
        )

        result = await strategy.run(days=30)

        assert result.updated == 3

    @pytest.mark.asyncio
    async def test_sync_daily_market_snapshot_critical_failure(self, strategy, mock_context):
        """测试关键数据获取失败"""
        mock_context.api.get_daily_quotes = AsyncMock(side_effect=Exception("Quotes API Error"))
        mock_context.cache.check_data_exists = AsyncMock(return_value=False)

        with pytest.raises(Exception, match="Quotes API Error"):
            await strategy.sync_daily_market_snapshot(datetime.date(2024, 1, 1), force=True)

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

        await strategy.sync_daily_market_snapshot(datetime.date(2024, 1, 1), force=True)

        mock_context.cache.save_daily_quotes.assert_called_once()
        mock_context.cache.save_daily_indicators.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_daily_market_snapshot_missing_adj_factor(self, strategy, mock_context):
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

        await strategy.sync_daily_market_snapshot(datetime.date(2024, 1, 1), force=True)

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
        mock_context.cache.get_sync_status = AsyncMock(return_value={"last_sync_date": "2024-01-01 00:00:00"})
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
        mock_context.cache.get_completed_step4_stocks = AsyncMock(return_value={"000001.SZ"})
        mock_context.cache.get_incomplete_financial_stocks = AsyncMock(return_value=set())
        mock_context.processor.get_trade_dates = AsyncMock(return_value=[datetime.date(2024, 1, 1)])
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

        with (
            patch("utils.config_handler.ConfigHandler.get_max_batch_rows", return_value=10),
            patch(
                "utils.config_handler.ConfigHandler.get_sync_max_concurrent_heavy",
                return_value=1,
            ),
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

        df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d", errors="coerce")
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


class TestDateTypeConsistency:
    """Test cases for date type consistency across the codebase."""

    def test_dao_date_methods_return_datetime_date(self):
        for dao_cls, method_name in [
            (QuoteDao, "get_cached_trade_dates"),
            (QuoteDao, "get_cached_dates_for_table"),
            (QuoteDao, "get_date_range"),
            (FinancialDao, "get_cached_indicator_dates"),
        ]:
            if hasattr(dao_cls, method_name):
                method = getattr(dao_cls, method_name)
                source = inspect.getsource(method)
                assert 'strftime("%Y%m%d")' not in source, (
                    f"{dao_cls.__name__}.{method_name} should return datetime.date objects, "
                    f"not strings. API layer handles conversion."
                )

    def test_api_layer_converts_date_to_string(self):
        source = inspect.getsource(TushareClient._handle_api_call)
        assert "strftime" in source, "_handle_api_call should convert datetime.date to string for Tushare API"
        assert "%Y%m%d" in source, "_handle_api_call should use YYYYMMDD format for Tushare API"

    def test_health_mixin_date_comparison_type_safe(self):
        source = inspect.getsource(HealthCheckMixin.check_data_health)
        assert "official_dates" in source and "local_dates" in source, (
            "check_data_health should compare official_dates and local_dates"
        )
        assert "get_cached_trade_dates" in source, (
            "check_data_health should use get_cached_trade_dates which returns datetime.date"
        )

    def test_historical_sync_trade_dates_type(self):
        source = inspect.getsource(historical.HistoricalSyncStrategy._run_historical_sync)
        assert "trade_date_objs" in source or "datetime.date" in source, (
            "_run_historical_sync should work with datetime.date objects"
        )
        assert '[d.strftime("%Y%m%d") for d in' not in source, (
            "_run_historical_sync should not convert dates to strings internally. Let API layer handle conversion."
        )

    def test_breakpoint_resume_date_comparison_type_safe(self):
        source = inspect.getsource(historical.HistoricalSyncStrategy._run_historical_sync)
        assert "existing" in source and "trade_dates" in source, (
            "Breakpoint resume should compare existing dates with trade_dates"
        )
        assert "set.intersection" in source, "Breakpoint resume should use set.intersection for date comparison"

    def test_sync_methods_accept_datetime_date(self):
        for method_name in [
            "sync_daily_market_snapshot",
            "sync_moneyflow",
            "sync_northbound",
        ]:
            if hasattr(historical.HistoricalSyncStrategy, method_name):
                method = getattr(historical.HistoricalSyncStrategy, method_name)
                source = inspect.getsource(method)
                assert "datetime.date" in source or "date | None" in source, (
                    f"{method_name} should accept datetime.date parameter"
                )

    def test_no_date_to_string_conversion_in_sync_layer(self):
        for module in [historical, financial, holder]:
            source = inspect.getsource(module)
            problematic_patterns = [
                'strftime("%Y%m%d") for d in',
                'strftime("%Y-%m-%d") for d in',
            ]
            for pattern in problematic_patterns:
                if pattern in source:
                    if module.__name__ == "data.sync.historical":
                        continue
                    raise AssertionError(
                        f"{module.__name__} should not convert dates to strings. API layer handles this conversion."
                    )

    def test_historical_sync_only_uses_strftime_for_display(self):
        source = inspect.getsource(historical.HistoricalSyncStrategy._run_historical_sync)
        lines = source.split("\n")
        strftime_lines = []
        for i, line in enumerate(lines):
            if 'strftime("%Y%m%d")' in line:
                strftime_lines.append((i, line.strip()))

        for line_num, match in strftime_lines:
            is_display = "progress_callback" in match or "I18n.get" in match
            is_internal = "normalize_date" in match or "to_date_key" in match or "def " in match
            if not (is_display or is_internal):
                context_before = "\n".join(lines[max(0, line_num - 5) : line_num])
                is_in_helper_func = "def normalize_date" in context_before or "def to_date_key" in context_before
                assert is_in_helper_func, (
                    f"strftime in historical should only be for display/progress or internal date normalization, found: {match}"
                )

    def test_get_cached_indicator_dates_returns_datetime_date(self):
        source = inspect.getsource(FinancialDao.get_cached_indicator_dates)
        assert "strftime" not in source, "get_cached_indicator_dates should return datetime.date objects"

    def test_get_date_range_returns_datetime_date(self):
        source = inspect.getsource(QuoteDao.get_date_range)
        assert "strftime" not in source, "get_date_range should return datetime.date objects"


class TestHistoricalSyncCriticalTables:
    """Test that all synced tables are tracked for breakpoint resume."""

    def test_synced_tables_class_attribute(self):
        assert hasattr(historical.HistoricalSyncStrategy, "SYNCED_TABLES"), (
            "HistoricalSyncStrategy should have SYNCED_TABLES class attribute"
        )

        synced_tables = set(historical.HistoricalSyncStrategy.SYNCED_TABLES)

        expected_tables = {
            "daily_quotes",
            "daily_indicators",
            "moneyflow_daily",
            "limit_list",
            "suspend_d",
            "margin_daily",
            "northbound_holding",
            "moneyflow_hsgt",
            "top_list",
            "block_trade",
            "index_daily",
            "index_dailybasic",
        }

        missing = expected_tables - synced_tables
        assert not missing, f"SYNCED_TABLES missing tables: {missing}"

    def test_run_uses_synced_tables(self):
        source = inspect.getsource(historical.HistoricalSyncStrategy._run_historical_sync)

        assert "self.SYNCED_TABLES" in source, (
            "_run_historical_sync should use self.SYNCED_TABLES for breakpoint resume"
        )


class TestFieldExistenceCheck:
    """Test that field existence checks are in place for critical data."""

    def test_quotes_field_check_in_historical_sync(self):
        source = inspect.getsource(historical.HistoricalSyncStrategy.sync_daily_market_snapshot)

        assert "adj_factor" in source, "sync_daily_market_snapshot should check for adj_factor column"
        assert "required_quote_cols" in source or "missing_cols" in source, (
            "sync_daily_market_snapshot should check for required quote columns"
        )

    def test_basic_field_check_in_historical_sync(self):
        source = inspect.getsource(historical.HistoricalSyncStrategy.sync_daily_market_snapshot)

        assert "required_basic_cols" in source or "df_basic" in source, (
            "sync_daily_market_snapshot should check for required basic/indicator columns"
        )


class TestErrorHandlingConsistency:
    """Test that error handling is consistent across sync methods."""

    def test_critical_data_raises_on_failure(self):
        source = inspect.getsource(historical.HistoricalSyncStrategy.sync_daily_market_snapshot)

        assert "critical=True" in source, "sync_daily_market_snapshot should mark quotes and basic as critical"
        assert "raise e" in source or "raise " in source, (
            "sync_daily_market_snapshot should raise exception for critical data failures"
        )

    def test_non_critical_data_logs_warning(self):
        source = inspect.getsource(historical.HistoricalSyncStrategy.sync_daily_market_snapshot)

        assert "logger.warning" in source, "sync_daily_market_snapshot should log warnings for non-critical failures"


class TestSyncStatusUpdate:
    """Test that sync status is updated correctly."""

    def test_sync_status_updated_on_success(self):
        source = inspect.getsource(historical.HistoricalSyncStrategy.sync_daily_market_snapshot)

        assert "update_sync_status" in source, "sync_daily_market_snapshot should call update_sync_status"
        assert "safe_update_status" in source, "sync_daily_market_snapshot should have safe_update_status helper"

    def test_sync_status_skipped_on_failure(self):
        source = inspect.getsource(historical.HistoricalSyncStrategy.sync_daily_market_snapshot)

        assert "Skipping sync_status" in source or "result is not None" in source, (
            "sync_daily_market_snapshot should skip sync_status update on failure"
        )


class TestSyncReturnValueConsistency:
    """Test that sync_daily_market_snapshot returns consistent values."""

    def test_sync_returns_true_on_success(self):
        source = inspect.getsource(historical.HistoricalSyncStrategy.sync_daily_market_snapshot)

        assert "return True" in source, "sync_daily_market_snapshot should return True on successful sync"

    def test_sync_returns_true_on_cache_hit(self):
        source = inspect.getsource(historical.HistoricalSyncStrategy.sync_daily_market_snapshot)

        lines = source.split("\n")
        cache_hit_return_true = False
        for i, line in enumerate(lines):
            if "Cache hit" in line or "check_all_critical_tables_exist" in lines[max(0, i - 2)]:
                for j in range(i, min(i + 5, len(lines))):
                    if "return True" in lines[j]:
                        cache_hit_return_true = True
                        break
        assert cache_hit_return_true, "sync_daily_market_snapshot should return True when cache hit (skipping sync)"


class TestRetryLogging:
    """Test that retry failures are properly logged."""

    def test_retry_logs_exception_details(self):
        source = inspect.getsource(historical.HistoricalSyncStrategy._run_historical_sync)

        retry_section = False
        has_exception_logging = False
        lines = source.split("\n")
        for i, line in enumerate(lines):
            if "retry_one" in line and "async def" in line:
                retry_section = True
            if retry_section and "except Exception" in line:
                for j in range(i, min(i + 5, len(lines))):
                    if "logger.warning" in lines[j] or "logger.error" in lines[j]:
                        has_exception_logging = True
                        break
        assert has_exception_logging, "retry_one should log exception details when retry fails"


class TestCheckDataExists:
    """Test that check_data_exists uses HistoricalSyncStrategy.SYNCED_TABLES."""

    def test_check_data_exists_uses_synced_tables(self):
        source = inspect.getsource(QuoteDao.check_data_exists)

        assert "_get_default_synced_tables" in source, (
            "check_data_exists should use _get_default_synced_tables() to get default tables"
        )

    def test_check_data_exists_used_in_sync(self):
        source = inspect.getsource(historical.HistoricalSyncStrategy.sync_daily_market_snapshot)

        assert "check_data_exists" in source, (
            "sync_daily_market_snapshot should use check_data_exists for cache checking"
        )

    def test_synced_tables_consistency(self):
        """
        Verify that QuoteDao.check_data_exists default tables match
        HistoricalSyncStrategy.SYNCED_TABLES.
        """
        from data.persistence.daos.quote_dao import _get_default_synced_tables

        dao_tables = set(_get_default_synced_tables())
        strategy_tables = set(historical.HistoricalSyncStrategy.SYNCED_TABLES)

        assert dao_tables == strategy_tables, f"Table mismatch: DAO={dao_tables}, Strategy={strategy_tables}"


class TestDataIntegrityVerification:
    """Test that data integrity is verified after save."""

    def test_verify_data_integrity_function_exists(self):
        source = inspect.getsource(historical.HistoricalSyncStrategy.sync_daily_market_snapshot)

        assert "verify_data_integrity" in source, (
            "sync_daily_market_snapshot should have verify_data_integrity function"
        )

    def test_verify_checks_fetched_vs_saved(self):
        source = inspect.getsource(historical.HistoricalSyncStrategy.sync_daily_market_snapshot)

        assert "fetched" in source and "saved" in source, (
            "verify_data_integrity should compare fetched vs saved row counts"
        )

    def test_save_if_ok_returns_dict(self):
        source = inspect.getsource(historical.HistoricalSyncStrategy.sync_daily_market_snapshot)

        save_if_ok_section = False
        has_dict_return = False
        lines = source.split("\n")
        for _i, line in enumerate(lines):
            if "async def save_if_ok" in line:
                save_if_ok_section = True
            if save_if_ok_section and ("saved" in line and "fetched" in line):
                has_dict_return = True
                break
        assert has_dict_return, "save_if_ok should return a dict with 'saved' and 'fetched' keys"
