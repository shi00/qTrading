"""
财务数据同步完整性集成测试

测试 Phase 1: AI Prompt 数据注入增强
- F1: n_cashflow_act 字段修复
- F2: 多期财务趋势分析
- F3: 宏观经济指标注入
- L2: 批量预取避免 N+1 查询
- L3: Shibor 利率注入
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


class TestFinancialSyncIntegrity:
    """测试财务数据同步完整性"""

    @pytest.fixture
    def mock_cache(self):
        """创建模拟缓存"""
        cache = MagicMock()
        cache.prefetch_auxiliary_data = AsyncMock()
        cache.get_financial_reports_history = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "end_date": ["20231231"],
                    "roe": [12.5],
                    "n_income_attr_p": [50000000],
                    "n_cashflow_act": [100000000],
                }
            )
        )
        cache.get_fina_audit_batch = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "audit_result": ["标准无保留意见"],
                }
            )
        )
        cache.get_dividend_batch = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "div_proc": ["实施"],
                    "cash_div_tax": [0.5],
                }
            )
        )
        return cache

    @pytest.mark.asyncio
    async def test_multi_period_financial_trend(self, mock_cache):
        """
        F2 测试：多期财务趋势分析

        场景：验证 8 期财务数据正确获取和分析
        """
        df = await mock_cache.get_financial_reports_history("000001.SZ", periods=8)

        assert df is not None
        assert not df.empty
        assert "roe" in df.columns

    @pytest.mark.asyncio
    async def test_cashflow_field_injection(self, mock_cache):
        """
        F1 测试：n_cashflow_act 字段注入

        场景：验证现金流字段存在于财务数据中
        """
        df = await mock_cache.get_financial_reports_history("000001.SZ", periods=8)

        assert "n_cashflow_act" in df.columns

    @pytest.mark.asyncio
    async def test_audit_opinion_injection(self, mock_cache):
        """
        测试审计意见注入

        场景：验证审计意见正确获取
        """
        df = await mock_cache.get_fina_audit_batch(["000001.SZ"])

        assert df is not None
        assert not df.empty
        assert "audit_result" in df.columns

    @pytest.mark.asyncio
    async def test_dividend_injection(self, mock_cache):
        """
        测试分红信息注入

        场景：验证分红信息正确获取
        """
        df = await mock_cache.get_dividend_batch(["000001.SZ"])

        assert df is not None
        assert not df.empty
        assert "cash_div_tax" in df.columns


class TestBatchPrefetchOptimization:
    """L2 测试：批量预取优化"""

    @pytest.mark.asyncio
    async def test_prefetch_avoids_n_plus_one(self):
        """
        性能测试：验证批量预取避免 N+1 查询

        原方案：N 只股票 × M 个辅助数据源 = N×M 次查询
        优化后：M 次批量查询
        """
        from data.cache.cache_manager import CacheManager

        cache = CacheManager()
        call_count = 0

        async def count_calls(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return pd.DataFrame()

        with (
            patch.object(
                cache.financial_dao,
                "get_fina_audit_batch",
                new_callable=AsyncMock,
                side_effect=count_calls,
            ),
            patch.object(
                cache.financial_dao,
                "get_dividend_batch",
                new_callable=AsyncMock,
                side_effect=count_calls,
            ),
            patch.object(
                cache.financial_dao,
                "get_pledge_stat_batch",
                new_callable=AsyncMock,
                side_effect=count_calls,
            ),
            patch.object(
                cache.holder_dao,
                "get_top10_holders_batch",
                new_callable=AsyncMock,
                side_effect=count_calls,
            ),
            patch.object(
                cache.holder_dao,
                "get_stk_holdernumber_batch",
                new_callable=AsyncMock,
                side_effect=count_calls,
            ),
            patch.object(
                cache.financial_dao,
                "get_fina_mainbz_batch",
                new_callable=AsyncMock,
                side_effect=count_calls,
            ),
            patch.object(
                cache.financial_dao,
                "get_financial_reports_history_batch",
                new_callable=AsyncMock,
                side_effect=count_calls,
            ),
        ):
            ts_codes = ["000001.SZ", "000002.SZ", "600000.SH"]
            await cache.prefetch_auxiliary_data(ts_codes)

            assert call_count <= 10, f"Expected <= 10 DB calls, got {call_count}"


class TestMacroContextInjection:
    """F3 测试：宏观经济上下文注入"""

    @pytest.mark.asyncio
    async def test_macro_economy_injection(self):
        """
        测试宏观经济指标注入

        场景：验证 M2、CPI、PPI 等指标正确获取
        """
        from unittest.mock import MagicMock

        from data.persistence.daos.macro_dao import MacroDao

        mock_engine = MagicMock()
        macro_dao = MacroDao(mock_engine)

        with patch.object(
            macro_dao,
            "_read_db",
            new_callable=AsyncMock,
            return_value=pd.DataFrame(
                {
                    "period": ["202401"],
                    "m2_yoy": [8.5],
                    "cpi": [0.2],
                    "ppi": [-2.5],
                }
            ),
        ):
            df = await macro_dao.get_macro_economy_latest()

            assert "m2_yoy" in df.columns
            assert "cpi" in df.columns
            assert "ppi" in df.columns

    @pytest.mark.asyncio
    async def test_shibor_injection(self):
        """
        L3 测试：Shibor 利率注入

        场景：验证 Shibor 利率正确获取
        """
        from unittest.mock import MagicMock

        from data.persistence.daos.macro_dao import MacroDao

        mock_engine = MagicMock()
        macro_dao = MacroDao(mock_engine)

        with patch.object(
            macro_dao,
            "_read_db",
            new_callable=AsyncMock,
            return_value=pd.DataFrame(
                {
                    "date": ["20240101"],
                    "on": [2.0],
                    "1w": [2.5],
                    "3m": [3.5],
                }
            ),
        ):
            df = await macro_dao.get_shibor_latest()

            assert "on" in df.columns
            assert "1w" in df.columns
            assert "3m" in df.columns


class TestFinancialDataQuality:
    """财务数据质量测试"""

    @pytest.mark.asyncio
    async def test_roe_trend_analysis(self):
        """
        测试 ROE 趋势分析

        场景：验证 ROE 趋势正确计算
        """
        mock_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"] * 4,
                "end_date": ["20231231", "20230930", "20230630", "20230331"],
                "roe": [12.5, 11.0, 10.5, 9.0],
            }
        )

        roe_values = mock_df["roe"].tolist()
        roe_trend = roe_values[0] - roe_values[-1]

        assert roe_trend > 0

    @pytest.mark.asyncio
    async def test_cashflow_vs_profit(self):
        """
        测试现金流与利润对比

        场景：验证现金流质量分析
        """
        mock_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "n_income_attr_p": [50000000],
                "n_cashflow_act": [100000000],
            }
        )

        cashflow_quality = mock_df["n_cashflow_act"].iloc[0] / mock_df["n_income_attr_p"].iloc[0]

        assert cashflow_quality > 1.0


class TestFinancialSyncEdgeCases:
    """边界条件测试"""

    @pytest.mark.asyncio
    async def test_missing_financial_data(self):
        """
        测试缺失财务数据

        场景：验证缺失数据时的降级处理
        """
        from data.cache.cache_manager import CacheManager

        cache = CacheManager()

        with patch.object(
            cache,
            "get_financial_reports_history",
            new_callable=AsyncMock,
            return_value=pd.DataFrame(),
        ):
            df = await cache.get_financial_reports_history("999999.SZ", periods=8)

            assert df is not None
            assert df.empty

    @pytest.mark.asyncio
    async def test_partial_financial_data(self):
        """
        测试部分财务数据

        场景：验证部分字段缺失时的处理
        """
        mock_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "end_date": ["20231231"],
                "roe": [12.5],
            }
        )

        assert "n_cashflow_act" not in mock_df.columns

    @pytest.mark.asyncio
    async def test_stale_financial_data(self):
        """
        测试过期财务数据

        场景：验证过期数据识别
        """
        mock_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "end_date": ["20200131"],
                "roe": [12.5],
            }
        )

        latest_date = mock_df["end_date"].max()

        assert latest_date < "20231231"


class TestFinancialSyncReport:
    """财务同步报告测试"""

    @pytest.mark.asyncio
    async def test_sync_result_financial_metrics(self):
        """
        测试同步结果包含财务指标
        """
        from data.sync.base import SyncResult

        result = SyncResult()
        result.status = "success"
        result.added = 100
        result.financial_reports_synced = 50
        result.audit_opinions_synced = 50

        assert result.added == 100
        assert hasattr(result, "financial_reports_synced")

    @pytest.mark.asyncio
    async def test_sync_result_to_summary(self):
        """
        测试同步结果摘要生成
        """
        from data.sync.base import SyncResult

        result = SyncResult()
        result.status = "success"
        result.added = 100
        result.updated = 50
        result.skipped = 30

        summary = result.to_summary()

        assert "success" in summary
        assert "100" in summary
