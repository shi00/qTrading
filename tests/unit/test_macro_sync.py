import pytest
import datetime
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
import pandas as pd

from data.sync.macro import (
    MacroSyncStrategy,
    _parse_period,
    _quarter_to_period_end,
    _compute_gdp_publish_date,
    _latest_quarter_before,
)
from data.sync.base import SyncContext, SyncResult
from data.persistence.daos.base_dao import EngineDisposedError
from data.external.tushare_client import TushareAPIPermissionError

pytestmark = pytest.mark.unit


class TestParsePeriod:
    def test_valid_yyyymm(self):
        assert _parse_period("202406") == "2024-06-01"

    def test_nan_returns_none(self):
        assert _parse_period(float("nan")) is None

    def test_none_returns_none(self):
        assert _parse_period(None) is None

    def test_non_yyyymm_returns_original(self):
        assert _parse_period("2024-06-01") == "2024-06-01"

    def test_short_string_returns_original(self):
        assert _parse_period("2024") == "2024"


class TestMacroSyncCancel:
    @pytest.mark.asyncio
    async def test_cancel(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        strategy = MacroSyncStrategy(ctx)
        assert not strategy._cancelled
        strategy.cancel()
        assert strategy._cancelled


class TestMacroSyncGetEffectiveTradeDate:
    @pytest.mark.asyncio
    async def test_with_processor(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.processor = MagicMock()
        ctx.processor.trade_calendar.get_latest_trade_date = AsyncMock(return_value=datetime.datetime(2024, 6, 14))
        strategy = MacroSyncStrategy(ctx)
        result = await strategy._get_effective_trade_date()
        assert result == datetime.date(2024, 6, 14)

    @pytest.mark.asyncio
    async def test_fallback_on_error(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.processor = MagicMock()
        ctx.processor.trade_calendar.get_latest_trade_date = AsyncMock(side_effect=Exception("test error"))
        strategy = MacroSyncStrategy(ctx)
        result = await strategy._get_effective_trade_date()
        assert isinstance(result, datetime.date)

    @pytest.mark.asyncio
    async def test_none_return_falls_back_to_today(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.processor = MagicMock()
        ctx.processor.trade_calendar.get_latest_trade_date = AsyncMock(return_value=None)
        strategy = MacroSyncStrategy(ctx)
        result = await strategy._get_effective_trade_date()
        assert isinstance(result, datetime.date)


class TestMacroSyncMergeMacroData:
    def test_all_none(self):
        result = MacroSyncStrategy._merge_macro_data(None, None, None)
        assert result is None

    def test_m2_only(self):
        df_m2 = pd.DataFrame({"period": ["202406"], "m2": [100.0], "m2_yoy": [5.0]})
        result = MacroSyncStrategy._merge_macro_data(df_m2, None, None)
        assert result is not None
        assert "m2" in result.columns

    def test_merge_all(self):
        df_m2 = pd.DataFrame({"period": ["202406"], "m2": [100.0]})
        df_cpi = pd.DataFrame({"period": ["202406"], "cpi": [2.0]})
        df_ppi = pd.DataFrame({"period": ["202406"], "ppi": [1.0]})
        result = MacroSyncStrategy._merge_macro_data(df_m2, df_cpi, df_ppi)
        assert result is not None
        assert "m2" in result.columns
        assert "cpi" in result.columns
        assert "ppi" in result.columns

    def test_merge_missing_period_in_indicator(self):
        df_m2 = pd.DataFrame({"period": ["202406"], "m2": [100.0]})
        df_cpi = pd.DataFrame({"value": [2.0]})
        result = MacroSyncStrategy._merge_macro_data(df_m2, df_cpi, None)
        assert result is not None
        assert "cpi" not in result.columns

    def test_merge_missing_target_col(self):
        df_m2 = pd.DataFrame({"period": ["202406"], "m2": [100.0]})
        df_cpi = pd.DataFrame({"period": ["202406"], "other": [2.0]})
        result = MacroSyncStrategy._merge_macro_data(df_m2, df_cpi, None)
        assert result is not None


class TestMacroSyncQuarterHelpers:
    """Phase 2D §3.2.6：cn_gdp quarter 转换辅助函数测试。"""

    def test_quarter_to_period_end_q1(self):
        assert _quarter_to_period_end("2024Q1") == datetime.date(2024, 3, 31)

    def test_quarter_to_period_end_q2(self):
        assert _quarter_to_period_end("2024Q2") == datetime.date(2024, 6, 30)

    def test_quarter_to_period_end_q3(self):
        assert _quarter_to_period_end("2024Q3") == datetime.date(2024, 9, 30)

    def test_quarter_to_period_end_q4(self):
        assert _quarter_to_period_end("2024Q4") == datetime.date(2024, 12, 31)

    def test_quarter_to_period_end_lowercase_q(self):
        assert _quarter_to_period_end("2024q4") == datetime.date(2024, 12, 31)

    def test_quarter_to_period_end_invalid_returns_none(self):
        assert _quarter_to_period_end("invalid") is None
        assert _quarter_to_period_end("2024") is None
        assert _quarter_to_period_end("2024Q5") is None
        assert _quarter_to_period_end(None) is None

    def test_compute_gdp_publish_date_q4_crosses_year(self):
        """Q4 季度末日 12-31，发布日期为次年 1 月 20 日。"""
        period = datetime.date(2024, 12, 31)
        assert _compute_gdp_publish_date(period) == datetime.date(2025, 1, 20)

    def test_compute_gdp_publish_date_q1(self):
        period = datetime.date(2024, 3, 31)
        assert _compute_gdp_publish_date(period) == datetime.date(2024, 4, 20)

    def test_latest_quarter_before_q1_month(self):
        """1-3 月应返回去年 Q4。"""
        assert _latest_quarter_before(datetime.date(2024, 2, 15)) == "2023Q4"
        assert _latest_quarter_before(datetime.date(2024, 3, 31)) == "2023Q4"

    def test_latest_quarter_before_q2_month(self):
        """4-6 月应返回当年 Q1。"""
        assert _latest_quarter_before(datetime.date(2024, 5, 15)) == "2024Q1"

    def test_latest_quarter_before_q3_month(self):
        """7-9 月应返回当年 Q2。"""
        assert _latest_quarter_before(datetime.date(2024, 8, 15)) == "2024Q2"

    def test_latest_quarter_before_q4_month(self):
        """10-12 月应返回当年 Q3。"""
        assert _latest_quarter_before(datetime.date(2024, 11, 15)) == "2024Q3"


class TestMacroSyncMergeMacroDataWithGdp:
    """Phase 2D §3.2.6：_merge_macro_data 第 4 个 df 参数（GDP）测试。"""

    def test_merge_with_gdp_appends_gdp_rows(self):
        """GDP 数据应作为独立行 concat（period 为季度末日）。"""
        df_m2 = pd.DataFrame({"period": ["202412"], "m2": [100.0], "m2_yoy": [5.0]})
        df_gdp = pd.DataFrame(
            {
                "period": ["2024Q4"],
                "gdp": [35000000.0],
                "gdp_yoy": [5.2],
                "pi": [2500000.0],
                "pi_yoy": [3.1],
                "si": [14000000.0],
                "si_yoy": [5.0],
                "ti": [18500000.0],
                "ti_yoy": [5.8],
            }
        )
        result = MacroSyncStrategy._merge_macro_data(df_m2, None, None, df_gdp)

        assert result is not None
        assert not result.empty
        # 应有 2 行：月度行 + GDP 行
        assert len(result) == 2
        # GDP 字段应存在
        assert "gdp_yoy" in result.columns
        # GDP 行的 period 应为季度末日 2024-12-31
        gdp_row = result[result["gdp_yoy"].notna()]
        assert len(gdp_row) == 1
        assert gdp_row.iloc[0]["period"] == datetime.date(2024, 12, 31)
        # GDP 行的 publish_date 应为 2025-01-20（季度结束后次月 20 日）
        assert gdp_row.iloc[0]["publish_date"] == datetime.date(2025, 1, 20)

    def test_merge_with_gdp_only(self):
        """仅 GDP 数据（无 m2/cpi/ppi）时应返回 GDP 行。"""
        df_gdp = pd.DataFrame(
            {
                "period": ["2024Q4"],
                "gdp": [35000000.0],
                "gdp_yoy": [5.2],
            }
        )
        result = MacroSyncStrategy._merge_macro_data(None, None, None, df_gdp)

        assert result is not None
        assert not result.empty
        assert "gdp_yoy" in result.columns
        assert result.iloc[0]["period"] == datetime.date(2024, 12, 31)

    def test_merge_with_none_gdp_falls_back_to_original_behavior(self):
        """df_gdp=None 时应与原 3 参数行为一致。"""
        df_m2 = pd.DataFrame({"period": ["202412"], "m2": [100.0]})
        result = MacroSyncStrategy._merge_macro_data(df_m2, None, None, None)

        assert result is not None
        assert "m2" in result.columns
        # 不应有 GDP 字段（因为 df_gdp 为 None）
        assert "gdp_yoy" not in result.columns

    def test_merge_with_empty_gdp_falls_back_to_original_behavior(self):
        """df_gdp 为空 DataFrame 时应与原 3 参数行为一致。"""
        df_m2 = pd.DataFrame({"period": ["202412"], "m2": [100.0]})
        result = MacroSyncStrategy._merge_macro_data(df_m2, None, None, pd.DataFrame())

        assert result is not None
        assert "m2" in result.columns
        assert "gdp_yoy" not in result.columns


class TestMacroSyncCancelSemantics:
    @pytest.mark.asyncio
    async def test_cancel_sets_status(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        strategy = MacroSyncStrategy(ctx)
        strategy._sync_macro_monthly = AsyncMock()
        strategy._sync_shibor_daily = AsyncMock()
        strategy._sync_index_weights = AsyncMock()

        async def cancel_after_first(result):
            strategy._cancelled = True

        strategy._sync_macro_monthly = cancel_after_first
        result = await strategy.run()
        assert result.status == "cancelled"

    @pytest.mark.asyncio
    async def test_complete_sets_no_cancel_status(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        strategy = MacroSyncStrategy(ctx)
        strategy._sync_macro_monthly = AsyncMock()
        strategy._sync_shibor_daily = AsyncMock()
        strategy._sync_index_weights = AsyncMock()
        result = await strategy.run()
        assert result.status != "cancelled"


class TestMacroSyncMergeIndicator:
    def test_none_df(self):
        df = pd.DataFrame({"period": ["202406"], "cpi": [2.0]})
        result = MacroSyncStrategy._merge_indicator(None, df, "cpi")
        assert result is not None
        assert "cpi" in result.columns

    def test_empty_df(self):
        merged = pd.DataFrame({"period": ["202406"], "m2": [100.0]})
        result = MacroSyncStrategy._merge_indicator(merged, pd.DataFrame(), "cpi")
        assert "cpi" not in result.columns

    def test_none_merged(self):
        result = MacroSyncStrategy._merge_indicator(None, None, "cpi")
        assert result is None

    def test_missing_target_col(self):
        merged = pd.DataFrame({"period": ["202406"], "m2": [100.0]})
        df = pd.DataFrame({"period": ["202406"], "other": [2.0]})
        result = MacroSyncStrategy._merge_indicator(merged, df, "cpi")
        assert "cpi" not in result.columns

    def test_missing_period_in_df(self):
        merged = pd.DataFrame({"period": ["202406"], "m2": [100.0]})
        df = pd.DataFrame({"cpi": [2.0]})
        result = MacroSyncStrategy._merge_indicator(merged, df, "cpi")
        assert "cpi" not in result.columns


class TestMacroSyncSyncMacroMonthly:
    @pytest.mark.asyncio
    async def test_with_data(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.cache.update_sync_status = AsyncMock()
        ctx.api = MagicMock()
        ctx.api.get_macro_data = AsyncMock(return_value=pd.DataFrame({"period": ["202406"], "m2": [100.0]}))
        strategy = MacroSyncStrategy(ctx)
        strategy.dao = MagicMock()
        strategy.dao.get_macro_latest_date = AsyncMock(return_value=None)
        strategy.dao.save_macro_economy = AsyncMock(return_value=1)
        result = SyncResult()
        await strategy._sync_macro_monthly(result)
        assert result.added >= 0

    @pytest.mark.asyncio
    async def test_error(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_macro_data = AsyncMock(side_effect=Exception("API error"))
        strategy = MacroSyncStrategy(ctx)
        strategy.dao = MagicMock()
        strategy.dao.get_macro_latest_date = AsyncMock(return_value=None)
        result = SyncResult()
        await strategy._sync_macro_monthly(result)
        assert len(result.errors) > 0

    @pytest.mark.asyncio
    async def test_sync_macro_monthly_includes_cn_gdp(self):
        """Phase 2D §3.2.6：_sync_macro_monthly 应调用 get_cn_gdp 拉取 GDP 数据。"""
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.cache.update_sync_status = AsyncMock()
        ctx.api = MagicMock()
        ctx.api.get_macro_data = AsyncMock(
            return_value=pd.DataFrame({"period": ["202412"], "m2": [100.0], "m2_yoy": [5.0]})
        )
        ctx.api.get_cn_gdp = AsyncMock(
            return_value=pd.DataFrame(
                {
                    # latest 为 2024-12-01 时，Q4 未结束，_latest_quarter_before 返回 "2024Q3"
                    "period": ["2024Q3"],
                    "gdp": [35000000.0],
                    "gdp_yoy": [5.2],
                    "pi": [2500000.0],
                    "pi_yoy": [3.1],
                    "si": [14000000.0],
                    "si_yoy": [5.0],
                    "ti": [18500000.0],
                    "ti_yoy": [5.8],
                }
            )
        )
        strategy = MacroSyncStrategy(ctx)
        strategy.dao = MagicMock()
        # latest 为 2024-12-01（12 月），Q4 未结束，_latest_quarter_before 返回 "2024Q3"
        strategy.dao.get_macro_latest_date = AsyncMock(return_value=datetime.date(2024, 12, 1))
        strategy.dao.save_macro_economy = AsyncMock(return_value=2)

        result = SyncResult()
        await strategy._sync_macro_monthly(result)

        # 验证 get_cn_gdp 被调用，quarter 参数为 "2024Q3"
        ctx.api.get_cn_gdp.assert_called_once_with(quarter="2024Q3")
        # 验证 save_macro_economy 被调用（merged 含 GDP 行）
        strategy.dao.save_macro_economy.assert_called_once()
        saved_df = strategy.dao.save_macro_economy.call_args.args[0]
        # 应包含 GDP 字段
        assert "gdp_yoy" in saved_df.columns

    @pytest.mark.asyncio
    async def test_sync_macro_monthly_gdp_permission_denied_does_not_block_m2(self):
        """Phase 2D：cn_gdp 权限不足不应阻断 m2/cpi/ppi 同步。"""
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.cache.update_sync_status = AsyncMock()
        ctx.api = MagicMock()
        ctx.api.get_macro_data = AsyncMock(return_value=pd.DataFrame({"period": ["202412"], "m2": [100.0]}))
        ctx.api.get_cn_gdp = AsyncMock(side_effect=TushareAPIPermissionError("cn_gdp", "no permission"))
        strategy = MacroSyncStrategy(ctx)
        strategy.dao = MagicMock()
        strategy.dao.get_macro_latest_date = AsyncMock(return_value=datetime.date(2024, 12, 1))
        strategy.dao.save_macro_economy = AsyncMock(return_value=1)

        result = SyncResult()
        await strategy._sync_macro_monthly(result)

        # m2 数据应正常保存（不被 GDP 权限错误阻断）
        strategy.dao.save_macro_economy.assert_called_once()
        # 不应有 fatal error（GDP 权限不足只记录 warning，不写入 result.errors）
        assert all("Macro Monthly" not in e or "permission" not in e.lower() for e in result.errors)


class TestMacroSyncSyncShiborDaily:
    @pytest.mark.asyncio
    async def test_with_data(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.cache.update_sync_status = AsyncMock()
        ctx.api = MagicMock()
        ctx.api.get_shibor = AsyncMock(return_value=pd.DataFrame({"date": ["20240614"], "shibor": [2.0]}))
        strategy = MacroSyncStrategy(ctx)
        strategy.dao = MagicMock()
        strategy.dao.get_shibor_latest_date = AsyncMock(return_value=None)
        strategy.dao.save_shibor_daily = AsyncMock(return_value=1)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        with patch("utils.config_handler.ConfigHandler.get_init_history_years", return_value=1):
            ctx.processor = MagicMock()
            ctx.processor.trade_calendar.get_trade_dates = AsyncMock(
                return_value=[datetime.date(2023, 1, 1), datetime.date(2024, 6, 14)]
            )
            result = SyncResult()
            await strategy._sync_shibor_daily(result)
            assert result.added >= 0

    @pytest.mark.asyncio
    async def test_already_up_to_date(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        strategy = MacroSyncStrategy(ctx)
        strategy.dao = MagicMock()
        strategy.dao.get_shibor_latest_date = AsyncMock(return_value="2024-06-15")
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        result = SyncResult()
        await strategy._sync_shibor_daily(result)
        assert result.added == 0

    @pytest.mark.asyncio
    async def test_error(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        strategy = MacroSyncStrategy(ctx)
        strategy.dao = MagicMock()
        strategy.dao.get_shibor_latest_date = AsyncMock(side_effect=Exception("DB error"))
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        result = SyncResult()
        await strategy._sync_shibor_daily(result)
        assert len(result.errors) > 0


class TestMacroSyncSyncShiborDailyWithLpr:
    """Phase 3G §4.3.4：_sync_shibor_daily 同时拉取 LPR 并按 date 合并入库。"""

    @pytest.mark.asyncio
    async def test_sync_shibor_daily_includes_lpr(self):
        """shibor + LPR 数据同时拉取时，按 date merge 后传入 save_shibor_daily。"""
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.cache.update_sync_status = AsyncMock()
        ctx.api = MagicMock()
        # shibor 返回 on/1w/3m 列
        ctx.api.get_shibor = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "date": ["20240614"],
                    "on": [1.8],
                    "1w": [1.9],
                    "3m": [2.0],
                }
            )
        )
        # LPR 返回 lpr_1y/lpr_5y 列
        ctx.api.get_shibor_lpr = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "date": ["20240614"],
                    "lpr_1y": [3.45],
                    "lpr_5y": [3.95],
                }
            )
        )
        strategy = MacroSyncStrategy(ctx)
        strategy.dao = MagicMock()
        strategy.dao.get_shibor_latest_date = AsyncMock(return_value=None)
        strategy.dao.save_shibor_daily = AsyncMock(return_value=1)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        with patch("utils.config_handler.ConfigHandler.get_init_history_years", return_value=1):
            ctx.processor = MagicMock()
            ctx.processor.trade_calendar.get_trade_dates = AsyncMock(
                return_value=[datetime.date(2023, 1, 1), datetime.date(2024, 6, 14)]
            )
            result = SyncResult()
            await strategy._sync_shibor_daily(result)

        # 验证 save_shibor_daily 被调用，且传入的 df 包含 LPR 列
        strategy.dao.save_shibor_daily.assert_awaited_once()
        saved_df: pd.DataFrame = strategy.dao.save_shibor_daily.call_args.args[0]
        assert "lpr_1y" in saved_df.columns
        assert "lpr_5y" in saved_df.columns
        # 验证 merge 后 LPR 值正确
        row = saved_df[saved_df["date"] == "20240614"].iloc[0]
        assert row["lpr_1y"] == 3.45
        assert row["lpr_5y"] == 3.95

    @pytest.mark.asyncio
    async def test_sync_shibor_daily_lpr_permission_denied_fallback(self):
        """LPR 权限不足时降级为仅同步 shibor，不阻断主流程。"""
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.cache.update_sync_status = AsyncMock()
        ctx.api = MagicMock()
        ctx.api.get_shibor = AsyncMock(return_value=pd.DataFrame({"date": ["20240614"], "on": [1.8]}))
        ctx.api.get_shibor_lpr = AsyncMock(side_effect=TushareAPIPermissionError("shibor_lpr", "denied"))
        strategy = MacroSyncStrategy(ctx)
        strategy.dao = MagicMock()
        strategy.dao.get_shibor_latest_date = AsyncMock(return_value=None)
        strategy.dao.save_shibor_daily = AsyncMock(return_value=1)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        with patch("utils.config_handler.ConfigHandler.get_init_history_years", return_value=1):
            ctx.processor = MagicMock()
            ctx.processor.trade_calendar.get_trade_dates = AsyncMock(
                return_value=[datetime.date(2023, 1, 1), datetime.date(2024, 6, 14)]
            )
            result = SyncResult()
            await strategy._sync_shibor_daily(result)

        # 验证 shibor 仍被入库（降级路径）
        strategy.dao.save_shibor_daily.assert_awaited_once()
        saved_df: pd.DataFrame = strategy.dao.save_shibor_daily.call_args.args[0]
        # LPR 列不应出现（未 merge）
        assert "lpr_1y" not in saved_df.columns
        # result 不应包含 LPR 权限错误（降级处理，不抛错）
        assert not any("shibor_lpr" in err for err in result.errors)


class TestMacroSyncSyncIndexWeights:
    @pytest.mark.asyncio
    async def test_already_up_to_date(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.cache.market_dao = MagicMock()
        ctx.cache.market_dao.get_latest_index_weight_date = AsyncMock(return_value="2024-06-01")
        ctx.cache.update_sync_status = AsyncMock()
        strategy = MacroSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        result = SyncResult()
        await strategy._sync_index_weights(result)
        assert result.added == 0

    @pytest.mark.asyncio
    async def test_no_latest_date(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.cache.market_dao = MagicMock()
        ctx.cache.market_dao.get_latest_index_weight_date = AsyncMock(return_value=None)
        ctx.cache.save_index_weights = AsyncMock(return_value=1)
        ctx.cache.update_sync_status = AsyncMock()
        ctx.api = MagicMock()
        ctx.api.get_index_weight = AsyncMock(return_value=pd.DataFrame({"index_code": ["399300.SZ"]}))
        strategy = MacroSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        with patch("utils.config_handler.ConfigHandler.get_init_history_years", return_value=1):
            ctx.processor = MagicMock()
            ctx.processor.trade_calendar.get_trade_dates = AsyncMock(
                return_value=[datetime.date(2023, 1, 1), datetime.date(2024, 6, 14)]
            )
            result = SyncResult()
            await strategy._sync_index_weights(result)

    @pytest.mark.asyncio
    async def test_index_weight_sync_status_uses_independent_count(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.cache.market_dao = MagicMock()
        ctx.cache.market_dao.get_latest_index_weight_date = AsyncMock(return_value=None)
        ctx.cache.save_index_weights = AsyncMock(return_value=3)
        ctx.cache.update_sync_status = AsyncMock()
        ctx.api = MagicMock()
        ctx.api.get_index_weight = AsyncMock(return_value=pd.DataFrame({"index_code": ["399300.SZ"]}))
        strategy = MacroSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        with patch("utils.config_handler.ConfigHandler.get_init_history_years", return_value=1):
            ctx.processor = MagicMock()
            ctx.processor.trade_calendar.get_trade_dates = AsyncMock(
                return_value=[datetime.date(2023, 1, 1), datetime.date(2024, 6, 14)]
            )
            result = SyncResult()
            await strategy._sync_index_weights(result)
            ctx.cache.update_sync_status.assert_called_once()
            call_args = ctx.cache.update_sync_status.call_args
            assert call_args[0][0] == "index_weight"
            assert call_args[0][2] == 21

    @pytest.mark.asyncio
    async def test_index_weight_count_excludes_other_api_counts(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.cache.market_dao = MagicMock()
        ctx.cache.market_dao.get_latest_index_weight_date = AsyncMock(return_value=None)
        ctx.cache.save_index_weights = AsyncMock(return_value=2)
        ctx.cache.update_sync_status = AsyncMock()
        ctx.api = MagicMock()
        ctx.api.get_index_weight = AsyncMock(return_value=pd.DataFrame({"index_code": ["399300.SZ"]}))
        strategy = MacroSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        with patch("utils.config_handler.ConfigHandler.get_init_history_years", return_value=1):
            ctx.processor = MagicMock()
            ctx.processor.trade_calendar.get_trade_dates = AsyncMock(
                return_value=[datetime.date(2023, 1, 1), datetime.date(2024, 6, 14)]
            )
            result = SyncResult()
            result.added = 100
            await strategy._sync_index_weights(result)
            call_args = ctx.cache.update_sync_status.call_args
            assert call_args[0][2] == 14

    @pytest.mark.asyncio
    async def test_error(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.cache.market_dao = MagicMock()
        ctx.cache.market_dao.get_latest_index_weight_date = AsyncMock(side_effect=Exception("DB error"))
        strategy = MacroSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        result = SyncResult()
        await strategy._sync_index_weights(result)
        assert len(result.errors) > 0


class TestMacroSyncRun:
    @pytest.mark.asyncio
    async def test_run_success(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        strategy = MacroSyncStrategy(ctx)
        strategy._sync_macro_monthly = AsyncMock()
        strategy._sync_shibor_daily = AsyncMock()
        strategy._sync_index_weights = AsyncMock()
        result = await strategy.run()
        assert result is not None

    @pytest.mark.asyncio
    async def test_run_cancelled_after_monthly(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        strategy = MacroSyncStrategy(ctx)

        async def mock_monthly(result):
            strategy._cancelled = True

        strategy._sync_macro_monthly = mock_monthly
        strategy._sync_shibor_daily = AsyncMock()
        strategy._sync_index_weights = AsyncMock()
        result = await strategy.run()
        assert result is not None

    @pytest.mark.asyncio
    async def test_run_exception(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        strategy = MacroSyncStrategy(ctx)

        async def mock_monthly(result):
            raise Exception("test error")

        strategy._sync_macro_monthly = mock_monthly
        strategy._sync_shibor_daily = AsyncMock()
        strategy._sync_index_weights = AsyncMock()
        result = await strategy.run()
        assert result.status == "failed"


class TestMacroMergeIndicator:
    def test_none_df(self):
        result = MacroSyncStrategy._merge_indicator(None, None, "cpi")
        assert result is None

    def test_empty_df(self):
        result = MacroSyncStrategy._merge_indicator(None, pd.DataFrame(), "cpi")
        assert result is None

    def test_missing_target_col(self):
        df = pd.DataFrame({"period": ["202406"], "other": [1.0]})
        result = MacroSyncStrategy._merge_indicator(None, df, "cpi")
        assert result is None

    def test_missing_period_col(self):
        df = pd.DataFrame({"cpi": [2.1]})
        result = MacroSyncStrategy._merge_indicator(None, df, "cpi")
        assert result is None

    def test_valid_indicator_new(self):
        df = pd.DataFrame({"period": ["202406"], "cpi": [2.1]})
        result = MacroSyncStrategy._merge_indicator(None, df, "cpi")
        assert result is not None
        assert "cpi" in result.columns

    def test_valid_indicator_merge(self):
        merged = pd.DataFrame({"period": ["202406"], "m2": [100]})
        df_cpi = pd.DataFrame({"period": ["202406"], "cpi": [2.1]})
        result = MacroSyncStrategy._merge_indicator(merged, df_cpi, "cpi")
        assert "m2" in result.columns
        assert "cpi" in result.columns


class TestMacroMergeMacroData:
    @patch("data.sync.macro.MacroDao")
    def test_all_none(self, mock_dao):
        ctx = MagicMock(spec=SyncContext)
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        result = MacroSyncStrategy._merge_macro_data(None, None, None)
        assert result is None

    @patch("data.sync.macro.MacroDao")
    def test_m2_only(self, mock_dao):
        ctx = MagicMock(spec=SyncContext)
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        df_m2 = pd.DataFrame({"period": ["202406"], "m2": [100], "m2_yoy": [5.0]})
        result = MacroSyncStrategy._merge_macro_data(df_m2, None, None)
        assert result is not None
        assert "m2" in result.columns

    @patch("data.sync.macro.MacroDao")
    def test_merge_all(self, mock_dao):
        ctx = MagicMock(spec=SyncContext)
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        df_m2 = pd.DataFrame({"period": ["202406"], "m2": [100], "m2_yoy": [5.0]})
        df_cpi = pd.DataFrame({"period": ["202406"], "cpi": [2.1]})
        df_ppi = pd.DataFrame({"period": ["202406"], "ppi": [-1.5]})
        result = MacroSyncStrategy._merge_macro_data(df_m2, df_cpi, df_ppi)
        assert result is not None
        assert "m2" in result.columns
        assert "cpi" in result.columns
        assert "ppi" in result.columns

    @patch("data.sync.macro.MacroDao")
    def test_no_period_column(self, mock_dao):
        ctx = MagicMock(spec=SyncContext)
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        df_m2 = pd.DataFrame({"m2": [100]})
        result = MacroSyncStrategy._merge_macro_data(df_m2, None, None)
        assert result is None


class TestMacroSyncStrategyRun:
    @pytest.mark.asyncio
    @patch("data.sync.macro.MacroDao")
    async def test_run_cancelled_after_monthly(self, mock_dao):
        ctx = MagicMock(spec=SyncContext)
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        strategy = MacroSyncStrategy(ctx)
        strategy._cancelled = True
        mock_dao_instance = MagicMock()
        mock_dao_instance.get_macro_latest_date = AsyncMock(return_value=None)
        mock_dao_instance.save_macro_economy = AsyncMock(return_value=0)
        strategy.dao = mock_dao_instance
        ctx.api = MagicMock()
        ctx.api.get_macro_data = AsyncMock(return_value=None)
        result = await strategy.run()
        assert isinstance(result, SyncResult)

    @pytest.mark.asyncio
    @patch("data.sync.macro.MacroDao")
    async def test_run_exception(self, mock_dao):
        ctx = MagicMock(spec=SyncContext)
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        strategy = MacroSyncStrategy(ctx)
        mock_dao_instance = MagicMock()
        mock_dao_instance.get_macro_latest_date = AsyncMock(return_value=None)
        mock_dao_instance.save_macro_economy = AsyncMock(return_value=0)
        mock_dao_instance.get_shibor_latest_date = AsyncMock(side_effect=RuntimeError("fatal db error"))
        strategy.dao = mock_dao_instance
        ctx.api = MagicMock()
        ctx.api.get_macro_data = AsyncMock(return_value=None)
        ctx.api.get_shibor = AsyncMock(side_effect=RuntimeError("fatal db error"))
        result = await strategy.run()
        assert result.status == "failed" or len(result.errors) > 0


class TestMacroSyncStrategySyncIndexWeights:
    @pytest.mark.asyncio
    @patch("data.sync.macro.MacroDao")
    async def test_index_weights_already_uptodate(self, mock_dao):
        import datetime

        ctx = MagicMock(spec=SyncContext)
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        strategy = MacroSyncStrategy(ctx)
        today = datetime.date(2024, 6, 15)
        strategy._get_effective_trade_date = AsyncMock(return_value=today)
        mock_market_dao = MagicMock()
        mock_market_dao.get_latest_index_weight_date = AsyncMock(return_value="2024-06-10")
        ctx.cache.market_dao = mock_market_dao
        result = SyncResult()
        await strategy._sync_index_weights(result)
        assert result.added == 0


class TestMacroSyncStrategySyncShibor:
    @pytest.mark.asyncio
    @patch("data.sync.macro.MacroDao")
    async def test_shibor_already_uptodate(self, mock_dao):
        ctx = MagicMock(spec=SyncContext)
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        strategy = MacroSyncStrategy(ctx)
        mock_dao_instance = MagicMock()
        today = datetime.date(2024, 6, 15)
        mock_dao_instance.get_shibor_latest_date = AsyncMock(return_value="20240616")
        strategy.dao = mock_dao_instance
        strategy._get_effective_trade_date = AsyncMock(return_value=today)
        result = SyncResult()
        await strategy._sync_shibor_daily(result)
        assert len(result.errors) == 0

    @pytest.mark.asyncio
    @patch("utils.config_handler.ConfigHandler")
    @patch("data.sync.macro.MacroDao")
    async def test_shibor_no_latest(self, mock_dao, mock_ch):
        mock_ch.get_init_history_years.return_value = 1
        ctx = MagicMock(spec=SyncContext)
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        strategy = MacroSyncStrategy(ctx)
        mock_dao_instance = MagicMock()
        mock_dao_instance.get_shibor_latest_date = AsyncMock(return_value=None)
        mock_dao_instance.save_shibor_daily = AsyncMock(return_value=10)
        strategy.dao = mock_dao_instance
        today = datetime.date(2024, 6, 15)
        strategy._get_effective_trade_date = AsyncMock(return_value=today)
        ctx.api = MagicMock()
        ctx.api.get_shibor = AsyncMock(return_value=pd.DataFrame({"date": ["20240615"], "on": [2.0]}))
        ctx.processor = MagicMock()
        ctx.processor.trade_calendar.get_trade_dates = AsyncMock(return_value=[datetime.date(2024, 1, 1)])
        result = SyncResult()
        await strategy._sync_shibor_daily(result)
        assert result.added == 10


class TestMacroSyncIndexWeightCounterIsolation:
    @pytest.mark.asyncio
    async def test_index_weight_counter_excludes_prior_added(self):
        from data.constants import MAJOR_INDICES

        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.cache.market_dao = MagicMock()
        ctx.cache.market_dao.get_latest_index_weight_date = AsyncMock(return_value=None)
        ctx.cache.save_index_weights = AsyncMock(return_value=3)
        ctx.cache.update_sync_status = AsyncMock()
        ctx.api = MagicMock()
        ctx.api.get_index_weight = AsyncMock(
            return_value=pd.DataFrame({"index_code": ["000001.SH"], "con_code": ["600000.SH"]}),
        )
        strategy = MacroSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        with patch("utils.config_handler.ConfigHandler.get_init_history_years", return_value=1):
            ctx.processor = MagicMock()
            ctx.processor.trade_calendar.get_trade_dates = AsyncMock(
                return_value=[datetime.date(2023, 1, 1), datetime.date(2024, 6, 14)],
            )
            result = SyncResult()
            result.added = 50
            await strategy._sync_index_weights(result)
            call_args = ctx.cache.update_sync_status.call_args
            assert call_args[0][0] == "index_weight"
            expected_iw_count = 3 * len(MAJOR_INDICES)
            assert call_args[0][2] == expected_iw_count
            assert call_args[0][2] != result.added


class TestMacroSyncEngineDisposedError:
    @pytest.mark.asyncio
    async def test_get_effective_trade_date_reraises_engine_disposed(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.processor = MagicMock()
        ctx.processor.trade_calendar.get_latest_trade_date = AsyncMock(
            side_effect=EngineDisposedError("Engine disposed")
        )
        strategy = MacroSyncStrategy(ctx)
        with pytest.raises(EngineDisposedError):
            await strategy._get_effective_trade_date()

    @pytest.mark.asyncio
    async def test_run_handles_engine_disposed_error(self):
        # R5 举一反三 fix: EngineDisposedError 必须 raise 让调用方感知，不可 swallow
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        strategy = MacroSyncStrategy(ctx)

        async def mock_monthly(result):
            raise EngineDisposedError("Engine disposed")

        strategy._sync_macro_monthly = mock_monthly
        strategy._sync_shibor_daily = AsyncMock()
        strategy._sync_index_weights = AsyncMock()
        with pytest.raises(EngineDisposedError):
            await strategy.run()

    @pytest.mark.asyncio
    async def test_sync_macro_monthly_reraises_engine_disposed(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_macro_data = AsyncMock(side_effect=EngineDisposedError("Engine disposed"))
        strategy = MacroSyncStrategy(ctx)
        strategy.dao = MagicMock()
        strategy.dao.get_macro_latest_date = AsyncMock(return_value=None)
        result = SyncResult()
        with pytest.raises(EngineDisposedError):
            await strategy._sync_macro_monthly(result)

    @pytest.mark.asyncio
    async def test_sync_shibor_reraises_engine_disposed(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_shibor = AsyncMock(side_effect=EngineDisposedError("Engine disposed"))
        strategy = MacroSyncStrategy(ctx)
        strategy.dao = MagicMock()
        strategy.dao.get_shibor_latest_date = AsyncMock(return_value=None)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        with patch("utils.config_handler.ConfigHandler.get_init_history_years", return_value=1):
            ctx.processor = MagicMock()
            ctx.processor.trade_calendar.get_trade_dates = AsyncMock(return_value=[datetime.date(2023, 1, 1)])
            result = SyncResult()
            with pytest.raises(EngineDisposedError):
                await strategy._sync_shibor_daily(result)

    @pytest.mark.asyncio
    async def test_sync_index_weights_reraises_engine_disposed(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.cache.market_dao = MagicMock()
        ctx.cache.market_dao.get_latest_index_weight_date = AsyncMock(
            side_effect=EngineDisposedError("Engine disposed")
        )
        strategy = MacroSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        result = SyncResult()
        with pytest.raises(EngineDisposedError):
            await strategy._sync_index_weights(result)


class TestMacroSyncCancelledError:
    @pytest.mark.asyncio
    async def test_run_propagates_cancelled_error(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        strategy = MacroSyncStrategy(ctx)

        async def mock_monthly(result):
            raise asyncio.CancelledError()

        strategy._sync_macro_monthly = mock_monthly
        strategy._sync_shibor_daily = AsyncMock()
        strategy._sync_index_weights = AsyncMock()
        with pytest.raises(asyncio.CancelledError):
            await strategy.run()

    @pytest.mark.asyncio
    async def test_run_sets_cancelled_status_on_cancelled_flag(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        strategy = MacroSyncStrategy(ctx)

        async def mock_monthly(result):
            pass

        async def mock_shibor(result):
            strategy._cancelled = True

        strategy._sync_macro_monthly = mock_monthly
        strategy._sync_shibor_daily = mock_shibor
        strategy._sync_index_weights = AsyncMock()
        result = await strategy.run()
        assert result.status == "cancelled"


class TestMacroSyncTusharePermissionError:
    @pytest.mark.asyncio
    async def test_sync_macro_monthly_handles_permission_error(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.cache.update_sync_status = AsyncMock()
        ctx.api = MagicMock()
        ctx.api.get_macro_data = AsyncMock(side_effect=TushareAPIPermissionError("macro_data", "Permission denied"))
        strategy = MacroSyncStrategy(ctx)
        strategy.dao = MagicMock()
        strategy.dao.get_macro_latest_date = AsyncMock(return_value="20240601")
        result = SyncResult()
        await strategy._sync_macro_monthly(result)
        assert len(result.errors) == 1
        assert "permission denied" in result.errors[0].lower()
        ctx.cache.update_sync_status.assert_called_once()
        call_kwargs = ctx.cache.update_sync_status.call_args
        assert call_kwargs[1].get("status") == "skipped_permission"

    @pytest.mark.asyncio
    async def test_sync_shibor_handles_permission_error(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.cache.update_sync_status = AsyncMock()
        ctx.api = MagicMock()
        ctx.api.get_shibor = AsyncMock(side_effect=TushareAPIPermissionError("shibor", "Permission denied"))
        strategy = MacroSyncStrategy(ctx)
        strategy.dao = MagicMock()
        strategy.dao.get_shibor_latest_date = AsyncMock(return_value=None)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        with patch("utils.config_handler.ConfigHandler.get_init_history_years", return_value=1):
            ctx.processor = MagicMock()
            ctx.processor.trade_calendar.get_trade_dates = AsyncMock(return_value=[datetime.date(2023, 1, 1)])
            result = SyncResult()
            await strategy._sync_shibor_daily(result)
            assert len(result.errors) == 1
            assert "permission denied" in result.errors[0].lower()

    @pytest.mark.asyncio
    async def test_sync_index_weights_handles_permission_error(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.cache.market_dao = MagicMock()
        ctx.cache.market_dao.get_latest_index_weight_date = AsyncMock(return_value=None)
        ctx.cache.update_sync_status = AsyncMock()
        ctx.api = MagicMock()
        ctx.api.get_index_weight = AsyncMock(side_effect=TushareAPIPermissionError("index_weight", "Permission denied"))
        strategy = MacroSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        with patch("utils.config_handler.ConfigHandler.get_init_history_years", return_value=1):
            ctx.processor = MagicMock()
            ctx.processor.trade_calendar.get_trade_dates = AsyncMock(
                return_value=[datetime.date(2023, 1, 1), datetime.date(2024, 6, 14)]
            )
            result = SyncResult()
            await strategy._sync_index_weights(result)
            assert result.added == 0

    @pytest.mark.asyncio
    async def test_sync_index_weights_handles_outer_permission_error(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.cache.market_dao = MagicMock()
        ctx.cache.market_dao.get_latest_index_weight_date = AsyncMock(
            side_effect=TushareAPIPermissionError("index_weight", "Permission denied")
        )
        ctx.cache.update_sync_status = AsyncMock()
        strategy = MacroSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        result = SyncResult()
        await strategy._sync_index_weights(result)
        assert result.added == 0


class TestMacroSyncShiborDateParsing:
    @pytest.mark.asyncio
    async def test_sync_shibor_invalid_date_fallback(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.cache.update_sync_status = AsyncMock()
        ctx.api = MagicMock()
        ctx.api.get_shibor = AsyncMock(return_value=pd.DataFrame({"date": ["20240614"], "on": [2.0]}))
        strategy = MacroSyncStrategy(ctx)
        strategy.dao = MagicMock()
        strategy.dao.get_shibor_latest_date = AsyncMock(return_value="invalid-date-format")
        strategy.dao.save_shibor_daily = AsyncMock(return_value=1)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        result = SyncResult()
        await strategy._sync_shibor_daily(result)
        assert result.added == 1


class TestMacroSyncIndexWeightsIndividualError:
    @pytest.mark.asyncio
    async def test_index_weight_individual_index_error_continues(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.cache.market_dao = MagicMock()
        ctx.cache.market_dao.get_latest_index_weight_date = AsyncMock(return_value=None)
        ctx.cache.save_index_weights = AsyncMock(return_value=5)
        ctx.cache.update_sync_status = AsyncMock()

        call_count = 0

        def mock_get_index_weight(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Individual index error")
            return pd.DataFrame({"index_code": ["000001.SH"]})

        ctx.api = MagicMock()
        ctx.api.get_index_weight = AsyncMock(side_effect=mock_get_index_weight)
        strategy = MacroSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        with patch("utils.config_handler.ConfigHandler.get_init_history_years", return_value=1):
            ctx.processor = MagicMock()
            ctx.processor.trade_calendar.get_trade_dates = AsyncMock(
                return_value=[datetime.date(2023, 1, 1), datetime.date(2024, 6, 14)]
            )
            result = SyncResult()
            await strategy._sync_index_weights(result)
            assert call_count >= 2

    @pytest.mark.asyncio
    async def test_index_weight_individual_engine_disposed_reraises(self):

        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.cache.market_dao = MagicMock()
        ctx.cache.market_dao.get_latest_index_weight_date = AsyncMock(return_value=None)
        ctx.cache.save_index_weights = AsyncMock()
        ctx.cache.update_sync_status = AsyncMock()

        call_count = 0

        def mock_get_index_weight(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise EngineDisposedError("Engine disposed")
            return pd.DataFrame()

        ctx.api = MagicMock()
        ctx.api.get_index_weight = AsyncMock(side_effect=mock_get_index_weight)
        strategy = MacroSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        with patch("utils.config_handler.ConfigHandler.get_init_history_years", return_value=1):
            ctx.processor = MagicMock()
            ctx.processor.trade_calendar.get_trade_dates = AsyncMock(
                return_value=[datetime.date(2023, 1, 1), datetime.date(2024, 6, 14)]
            )
            result = SyncResult()
            with pytest.raises(EngineDisposedError):
                await strategy._sync_index_weights(result)


class TestMacroSyncGetEffectiveTradeDateEdgeCases:
    @pytest.mark.asyncio
    async def test_trade_date_returns_date_object(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.processor = MagicMock()
        ctx.processor.trade_calendar.get_latest_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        strategy = MacroSyncStrategy(ctx)
        result = await strategy._get_effective_trade_date()
        assert result == datetime.date(2024, 6, 14)

    @pytest.mark.asyncio
    async def test_trade_date_string_parsing(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.processor = MagicMock()
        ctx.processor.trade_calendar.get_latest_trade_date = AsyncMock(return_value="2024-06-14")
        strategy = MacroSyncStrategy(ctx)
        result = await strategy._get_effective_trade_date()
        assert isinstance(result, datetime.date)


class TestMacroSyncMergeMacroDataEdgeCases:
    def test_merge_macro_data_missing_period_column_after_merge(self):
        df_m2 = pd.DataFrame({"m2": [100.0]})
        result = MacroSyncStrategy._merge_macro_data(df_m2, None, None)
        assert result is None

    def test_merge_macro_data_with_empty_indicator(self):
        df_m2 = pd.DataFrame({"period": ["202406"], "m2": [100.0]})
        df_cpi = pd.DataFrame()
        result = MacroSyncStrategy._merge_macro_data(df_m2, df_cpi, None)
        assert result is not None
        assert "m2" in result.columns

    def test_merge_indicator_missing_target_col_logs_warning(self):
        df = pd.DataFrame({"period": ["202406"], "other": [1.0]})
        result = MacroSyncStrategy._merge_indicator(None, df, "cpi")
        assert result is None


class TestMacroSyncRunSuccessPath:
    @pytest.mark.asyncio
    async def test_run_success_with_all_syncs(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        strategy = MacroSyncStrategy(ctx)
        strategy._sync_macro_monthly = AsyncMock()
        strategy._sync_shibor_daily = AsyncMock()
        strategy._sync_index_weights = AsyncMock()
        result = await strategy.run()
        assert result.status != "failed"
        assert result.status != "cancelled"


class TestMacroSyncMonthlyWithSkippedPermission:
    @pytest.mark.asyncio
    async def test_sync_macro_monthly_skipped_permission_no_latest(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.cache.update_sync_status = AsyncMock()
        ctx.api = MagicMock()
        ctx.api.get_macro_data = AsyncMock(side_effect=TushareAPIPermissionError("macro_data", "Permission denied"))
        strategy = MacroSyncStrategy(ctx)
        strategy.dao = MagicMock()
        strategy.dao.get_macro_latest_date = AsyncMock(return_value=None)
        result = SyncResult()
        await strategy._sync_macro_monthly(result)
        assert len(result.errors) == 1

    @pytest.mark.asyncio
    async def test_sync_shibor_skipped_permission_exception_in_status_update(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.cache.update_sync_status = AsyncMock(side_effect=Exception("Status update failed"))
        ctx.api = MagicMock()
        ctx.api.get_shibor = AsyncMock(side_effect=TushareAPIPermissionError("shibor", "Permission denied"))
        strategy = MacroSyncStrategy(ctx)
        strategy.dao = MagicMock()
        strategy.dao.get_shibor_latest_date = AsyncMock(return_value=None)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        with patch("utils.config_handler.ConfigHandler.get_init_history_years", return_value=1):
            ctx.processor = MagicMock()
            ctx.processor.trade_calendar.get_trade_dates = AsyncMock(return_value=[datetime.date(2023, 1, 1)])
            result = SyncResult()
            await strategy._sync_shibor_daily(result)
            assert len(result.errors) == 1


class TestPeriodToYyyymm:
    def test_none_returns_none(self):
        from data.sync.macro import _period_to_yyyymm

        assert _period_to_yyyymm(None) is None

    def test_date_object(self):
        from data.sync.macro import _period_to_yyyymm

        result = _period_to_yyyymm(datetime.date(2024, 3, 1))
        assert result == "202403"

    def test_timestamp(self):
        from data.sync.macro import _period_to_yyyymm

        result = _period_to_yyyymm(pd.Timestamp("2024-03-01"))
        assert result == "202403"

    def test_yyyymm_string(self):
        from data.sync.macro import _period_to_yyyymm

        result = _period_to_yyyymm("202403")
        assert result == "202403"

    def test_yyyymmdd_string(self):
        from data.sync.macro import _period_to_yyyymm

        result = _period_to_yyyymm("20240301")
        assert result == "202403"

    def test_iso_date_string(self):
        """ISO format string like '2024-03-01' should parse correctly, not truncate to '2024-0'."""
        from data.sync.macro import _period_to_yyyymm

        result = _period_to_yyyymm("2024-03-01")
        assert result == "202403"

    def test_december_wraps_year(self):
        from data.sync.macro import _period_to_yyyymm

        result = _period_to_yyyymm(datetime.date(2024, 12, 1))
        assert result == "202412"


class TestComputePublishDate:
    def test_regular_month(self):
        from data.sync.macro import _compute_publish_date

        result = _compute_publish_date(datetime.date(2024, 3, 1))
        assert result == datetime.date(2024, 4, 16)

    def test_december_wraps_year(self):
        from data.sync.macro import _compute_publish_date

        result = _compute_publish_date(datetime.date(2024, 12, 1))
        assert result == datetime.date(2025, 1, 16)

    def test_january(self):
        from data.sync.macro import _compute_publish_date

        result = _compute_publish_date(datetime.date(2024, 1, 1))
        assert result == datetime.date(2024, 2, 16)


class TestMacroSyncMonthlyUsesYyyymm:
    @pytest.mark.asyncio
    async def test_start_m_is_yyyymm_format(self):
        """MD-003: start_m should be YYYYMM, not YYYYMMDD"""
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.cache.update_sync_status = AsyncMock()
        ctx.api = MagicMock()
        ctx.api.get_macro_data = AsyncMock(return_value=pd.DataFrame({"period": ["202406"], "m2": [100.0]}))
        strategy = MacroSyncStrategy(ctx)
        strategy.dao = MagicMock()
        strategy.dao.get_macro_latest_date = AsyncMock(return_value=pd.Timestamp("2024-06-01"))
        strategy.dao.save_macro_economy = AsyncMock(return_value=1)
        result = SyncResult()
        await strategy._sync_macro_monthly(result)
        # Verify get_macro_data was called with YYYYMM format start_m
        for call in ctx.api.get_macro_data.call_args_list:
            start_m = call.kwargs.get("start_m")
            if start_m is not None:
                assert len(start_m) == 6, f"start_m should be YYYYMM format, got '{start_m}'"


class TestMergeMacroDataPublishDate:
    def test_publish_date_computed(self):
        """MD-002: _merge_macro_data should compute publish_date from period"""
        df_m2 = pd.DataFrame({"period": ["202406"], "m2": [100.0], "m2_yoy": [5.0]})
        result = MacroSyncStrategy._merge_macro_data(df_m2, None, None)
        assert result is not None
        assert "publish_date" in result.columns
        # period 2024-06-01 -> publish_date should be 2024-07-16
        assert result["publish_date"].iloc[0] == datetime.date(2024, 7, 16)


class TestMacroSyncPartialFailure:
    """Partial-failure boundary tests: some sub-syncs fail, some succeed,
    sync continues and preserves successfully fetched data."""

    @pytest.mark.asyncio
    async def test_partial_failure_monthly_fails_shibor_succeeds(self):
        """When macro monthly sync fails but shibor sync succeeds, the sync
        should continue, collect errors, and preserve successfully fetched data."""
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.cache.update_sync_status = AsyncMock()
        ctx.api = MagicMock()
        ctx.api.get_macro_data = AsyncMock(side_effect=RuntimeError("Macro API error"))
        ctx.api.get_shibor = AsyncMock(return_value=pd.DataFrame({"date": ["20240614"], "on": [2.0]}))
        strategy = MacroSyncStrategy(ctx)
        strategy.dao = MagicMock()
        strategy.dao.get_macro_latest_date = AsyncMock(return_value=None)
        strategy.dao.get_shibor_latest_date = AsyncMock(return_value=None)
        strategy.dao.save_shibor_daily = AsyncMock(return_value=5)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        ctx.cache.market_dao = MagicMock()
        ctx.cache.market_dao.get_latest_index_weight_date = AsyncMock(return_value="2024-06-10")
        with patch("utils.config_handler.ConfigHandler.get_init_history_years", return_value=1):
            ctx.processor = MagicMock()
            ctx.processor.trade_calendar.get_trade_dates = AsyncMock(
                return_value=[datetime.date(2024, 1, 1), datetime.date(2024, 6, 14)]
            )
            result = await strategy.run()

        # Sync should not crash
        assert result is not None
        assert isinstance(result, SyncResult)
        # Monthly error should be collected
        assert len(result.errors) >= 1
        assert any("Macro Monthly" in e for e in result.errors)
        # Shibor data should be saved
        assert result.added >= 5
        # Sync should not be marked as fully failed
        assert result.status != "failed"
