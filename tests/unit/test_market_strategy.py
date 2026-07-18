"""Unit tests for strategies/market.py (Phase 6 Task 6.1).

覆盖 VolumeBreakoutStrategy 6 大核心场景 + 其他策略基础冒烟,
以满足 market.py 覆盖率 >=80% 的 DoD 要求。

场景清单:
1. 市场趋势筛选 - pct_chg 区间过滤
2. 板块轮动 - 多行业同时入选信号
3. 成交量异常 - turnover_rate 阈值过滤
4. 空结果 - 空 DataFrame 不抛异常
5. 缺失列抛异常 - 缺少必需列时 filter() 包装为 RuntimeError
6. 按字段降序 - VolumeBreakoutStrategy 按 pct_chg 降序
   (Plans.md Task 6.1 表述为"按市值降序",但代码现状按 pct_chg 降序,
    本测试以代码事实为准,差异在交付报告中说明)

附加验证:
- required_quality_tier 类属性声明 (DoD 设计要点 4)
- R2 CancelledError 传播不被吞没
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import polars as pl
import pytest

from data.persistence.quality_gate import QualityTier
from strategies.market import (
    BlockTradeStrategy,
    InstitutionalStrategy,
    NorthboundFlowStrategy,
    NorthboundHoldingStrategy,
    VolumeBreakoutStrategy,
)
from utils.thread_pool import ThreadPoolManager

pytestmark = pytest.mark.unit


def _make_dp(tier: QualityTier = QualityTier.GOLD) -> MagicMock:
    """构造满足质量门控的 DataProcessor mock。"""
    dp = MagicMock()
    dp._quality_tier = tier
    dp.cache = MagicMock()
    return dp


# ============================================================================
# VolumeBreakoutStrategy — 主测目标, 覆盖 DoD 6 大场景
# ============================================================================


class TestVolumeBreakoutStrategy:
    """覆盖市场趋势筛选/板块轮动/成交量异常/空结果/缺失列/按字段降序 6 大场景。"""

    def test_required_quality_tier_is_silver(self) -> None:
        """验证 required_quality_tier 类属性声明 (DoD 设计要点 4)。"""
        assert VolumeBreakoutStrategy.required_quality_tier == QualityTier.SILVER

    def test_enable_ai_analysis_is_false(self) -> None:
        """enable_ai_analysis=False, 走非 AI 路径直接返回 candidates。"""
        assert VolumeBreakoutStrategy.enable_ai_analysis is False

    def test_market_trend_filter_selects_in_range(self) -> None:
        """场景 1: 市场趋势筛选 — pct_chg 在 [2, 7] 且 turnover_rate > 3 的股票被选中。"""
        strategy = VolumeBreakoutStrategy()
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"],
                "name": ["A", "B", "C"],
                "pct_chg": [3.0, 8.0, 5.0],  # 8.0 超出默认 max=7
                "turnover_rate": [5.0, 5.0, 5.0],
                "total_mv": [100.0, 200.0, 300.0],
            }
        )
        lf = pl.from_pandas(df).lazy()
        result = strategy._filter_logic(lf, {"params": {}}).collect()
        codes = set(result["ts_code"].to_list())
        assert codes == {"000001.SZ", "000003.SZ"}
        assert "000002.SZ" not in codes

    def test_sector_rotation_signals_multi_industries(self) -> None:
        """场景 2: 板块轮动 — 多行业股票同时入选,代表板块轮动信号。"""
        strategy = VolumeBreakoutStrategy()
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ", "600001.SH"],
                "name": ["A银行", "B科技", "C钢铁"],
                "industry": ["银行", "科技", "钢铁"],
                "pct_chg": [3.0, 4.0, 5.0],
                "turnover_rate": [5.0, 5.0, 5.0],
            }
        )
        lf = pl.from_pandas(df).lazy()
        result = strategy._filter_logic(lf, {"params": {}}).collect()
        industries = set(result["industry"].to_list())
        assert industries == {"银行", "科技", "钢铁"}

    def test_volume_anomaly_high_turnover_passes(self) -> None:
        """场景 3: 成交量异常 — turnover_rate 高于阈值的入选,低值被剔除。"""
        strategy = VolumeBreakoutStrategy()
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "name": ["高换手", "低换手"],
                "pct_chg": [3.0, 3.0],
                "turnover_rate": [10.0, 1.0],  # turnover_min=3, 1.0 不达标
            }
        )
        lf = pl.from_pandas(df).lazy()
        result = strategy._filter_logic(lf, {"params": {}}).collect()
        codes = result["ts_code"].to_list()
        assert "000001.SZ" in codes
        assert "000002.SZ" not in codes

    async def test_empty_dataframe_returns_empty(self) -> None:
        """场景 4: 空结果 — 空 DataFrame 通过 filter() 早返回空 (不抛异常)。"""
        strategy = VolumeBreakoutStrategy()
        context = {
            "screening_data": pd.DataFrame(),
            "data_processor": _make_dp(),
            "params": {},
        }
        with patch.object(
            strategy,
            "check_dependencies",
            return_value={"status": "ready", "missing_keys": [], "missing_tables": []},
        ):
            result = await strategy.filter(context)
            assert result.empty

    async def test_missing_column_raises_runtime_error(self) -> None:
        """场景 5: 缺失列抛异常 — 缺少 pct_chg 列时 filter() 包装为 RuntimeError。"""
        strategy = VolumeBreakoutStrategy()
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "turnover_rate": [5.0],  # 缺少 pct_chg 列
            }
        )
        context = {
            "screening_data": df,
            "data_processor": _make_dp(),
            "params": {},
        }
        with patch.object(
            strategy,
            "check_dependencies",
            return_value={"status": "ready", "missing_keys": [], "missing_tables": []},
        ):
            with pytest.raises(RuntimeError):  # noqa: weak-assertion 缺失列守卫：验证 PolarsBaseStrategy.filter 对缺失列抛 RuntimeError，raises 本身即充分断言
                await strategy.filter(context)

    def test_sort_descending_by_pct_chg(self) -> None:
        """场景 6: 按字段降序 — VolumeBreakoutStrategy 实际按 pct_chg 降序排列。

        Plans.md Task 6.1 表述为"按市值降序",但代码现状 (market.py VolumeBreakoutStrategy
        .sort("pct_chg", descending=True)) 是按 pct_chg 降序,非 total_mv。本测试以代码事实为准。
        """
        strategy = VolumeBreakoutStrategy()
        df = pd.DataFrame(
            {
                "ts_code": ["A.SZ", "B.SZ", "C.SZ"],
                "name": ["A", "B", "C"],
                "pct_chg": [3.0, 7.0, 5.0],
                "turnover_rate": [5.0, 5.0, 5.0],
            }
        )
        lf = pl.from_pandas(df).lazy()
        result = strategy._filter_logic(lf, {"params": {}}).collect()
        pct_chgs = result["pct_chg"].to_list()
        assert pct_chgs == sorted(pct_chgs, reverse=True)

    async def test_filter_propagates_cancelled_error(self) -> None:
        """R2 验证: ThreadPoolManager 抛 CancelledError 时 filter() 必须传播 (不吞没)。

        Python 3.13+ asyncio.CancelledError 继承 BaseException, 不会被 PolarsBaseStrategy
        的 except Exception 捕获, 会自然传播。
        """
        strategy = VolumeBreakoutStrategy()
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "pct_chg": [3.0],
                "turnover_rate": [5.0],
            }
        )
        context = {
            "screening_data": df,
            "data_processor": _make_dp(),
            "params": {},
        }
        with patch.object(
            strategy,
            "check_dependencies",
            return_value={"status": "ready", "missing_keys": [], "missing_tables": []},
        ):
            with patch.object(ThreadPoolManager, "run_async", new_callable=AsyncMock) as mock_run:
                mock_run.side_effect = asyncio.CancelledError("test cancel")
                with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion R2 守卫：验证 CancelledError 不被 except Exception 吞没，raises 本身即充分断言
                    await strategy.filter(context)

    def test_pct_chg_min_ge_max_auto_adjusts(self) -> None:
        """边界: pct_chg_min >= pct_chg_max 时自动调整 pct_chg_max = pct_chg_min + 0.5。"""
        strategy = VolumeBreakoutStrategy()
        df = pd.DataFrame(
            {
                "ts_code": ["A.SZ", "B.SZ"],
                "pct_chg": [5.0, 6.0],
                "turnover_rate": [5.0, 5.0],
            }
        )
        lf = pl.from_pandas(df).lazy()
        # 5.0 >= 5.0 触发自动调整, chg_max = 5.5, 6.0 被剔除
        result = strategy._filter_logic(lf, {"params": {"pct_chg_min": 5.0, "pct_chg_max": 5.0}}).collect()
        codes = result["ts_code"].to_list()
        assert "A.SZ" in codes
        assert "B.SZ" not in codes
        assert strategy._data_warnings, "auto-adjust 应写入 _data_warnings"

    def test_drop_nulls_in_required_columns(self) -> None:
        """边界: pct_chg 或 turnover_rate 为 null 的行被丢弃。"""
        strategy = VolumeBreakoutStrategy()
        df = pd.DataFrame(
            {
                "ts_code": ["A.SZ", "B.SZ", "C.SZ"],
                "pct_chg": [3.0, None, 5.0],
                "turnover_rate": [5.0, 5.0, None],
            }
        )
        lf = pl.from_pandas(df).lazy()
        result = strategy._filter_logic(lf, {"params": {}}).collect()
        codes = result["ts_code"].to_list()
        assert codes == ["A.SZ"]

    def test_get_parameters_returns_three_params(self) -> None:
        """get_parameters 返回 pct_chg_min / pct_chg_max / turnover_min 三个参数。"""
        strategy = VolumeBreakoutStrategy()
        params = strategy.get_parameters()
        names = {p["name"] for p in params}
        assert names == {"pct_chg_min", "pct_chg_max", "turnover_min"}


# ============================================================================
# NorthboundHoldingStrategy — 基础冒烟
# ============================================================================


class TestNorthboundHoldingStrategy:
    """NorthboundHoldingStrategy 基础冒烟, 提升 market.py 覆盖率。"""

    def test_required_quality_tier_is_bronze(self) -> None:
        assert NorthboundHoldingStrategy.required_quality_tier == QualityTier.BRONZE

    def test_empty_northbound_data_returns_empty(self) -> None:
        """空 northbound_data 返回空 LazyFrame (lf.head(0))。"""
        strategy = NorthboundHoldingStrategy()
        base_df = pd.DataFrame(
            {"ts_code": ["000001.SZ"], "name": ["A"], "industry": ["x"], "pe_ttm": [10.0], "total_mv": [100.0]}
        )
        lf = pl.from_pandas(base_df).lazy()
        result = strategy._filter_logic(lf, {"northbound_data": pd.DataFrame(), "params": {}}).collect()
        assert result.height == 0

    def test_filter_by_ratio_threshold(self) -> None:
        """ratio > target_ratio 且 ts_code 后缀 .SH/.SZ 的入选, 按 ratio 降序。"""
        strategy = NorthboundHoldingStrategy()
        base_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "600001.SH"],
                "name": ["A", "B"],
                "industry": ["银行", "钢铁"],
                "pe_ttm": [10.0, 20.0],
                "total_mv": [100.0, 200.0],
            }
        )
        nb_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "600001.SH", "000002.SZ"],
                "ratio": [5.0, 2.0, 10.0],  # 2.0 < target_ratio=3 不入选
            }
        )
        lf = pl.from_pandas(base_df).lazy()
        result = strategy._filter_logic(lf, {"northbound_data": nb_df, "params": {"nb_ratio_min": 3}}).collect()
        codes = result["ts_code"].to_list()
        assert "000001.SZ" in codes
        assert "600001.SH" not in codes  # ratio=2.0 被剔除
        ratios = result["ratio"].to_list()
        assert ratios == sorted(ratios, reverse=True)

    def test_get_parameters_returns_one_param(self) -> None:
        strategy = NorthboundHoldingStrategy()
        params = strategy.get_parameters()
        assert {p["name"] for p in params} == {"nb_ratio_min"}


# ============================================================================
# InstitutionalStrategy — 基础冒烟
# ============================================================================


class TestInstitutionalStrategy:
    """InstitutionalStrategy 基础冒烟。"""

    def test_required_quality_tier_is_bronze(self) -> None:
        assert InstitutionalStrategy.required_quality_tier == QualityTier.BRONZE

    def test_empty_top_list_returns_empty(self) -> None:
        strategy = InstitutionalStrategy()
        base_df = pd.DataFrame(
            {"ts_code": ["000001.SZ"], "name": ["A"], "industry": ["x"], "pe_ttm": [10.0], "total_mv": [100.0]}
        )
        lf = pl.from_pandas(base_df).lazy()
        result = strategy._filter_logic(lf, {"top_list": pd.DataFrame(), "params": {}}).collect()
        assert result.height == 0

    def test_filter_by_net_amount_threshold(self) -> None:
        """net_amount > target_net 的入选, 按 net_amount 降序。"""
        strategy = InstitutionalStrategy()
        base_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "name": ["A", "B"],
                "industry": ["x", "y"],
                "pe_ttm": [10.0, 20.0],
                "total_mv": [100.0, 200.0],
            }
        )
        lhb_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "net_amount": [5000.0, 1000.0],  # 1000 < target=3000 不入选
            }
        )
        lf = pl.from_pandas(base_df).lazy()
        result = strategy._filter_logic(lf, {"top_list": lhb_df, "params": {"inst_net_min": 3000}}).collect()
        codes = result["ts_code"].to_list()
        assert "000001.SZ" in codes
        assert "000002.SZ" not in codes

    def test_get_parameters_returns_one_param(self) -> None:
        strategy = InstitutionalStrategy()
        params = strategy.get_parameters()
        assert {p["name"] for p in params} == {"inst_net_min"}

    def test_missing_net_amount_column_returns_empty(self) -> None:
        """边界: top_list 缺 net_amount 列时返回空 (early return lf.head(0))。"""
        strategy = InstitutionalStrategy()
        base_df = pd.DataFrame(
            {"ts_code": ["000001.SZ"], "name": ["A"], "industry": ["x"], "pe_ttm": [10.0], "total_mv": [100.0]}
        )
        lhb_df = pd.DataFrame({"ts_code": ["000001.SZ"]})  # 缺 net_amount 列
        lf = pl.from_pandas(base_df).lazy()
        result = strategy._filter_logic(lf, {"top_list": lhb_df, "params": {}}).collect()
        assert result.height == 0


# ============================================================================
# BlockTradeStrategy — 基础冒烟
# ============================================================================


class TestBlockTradeStrategy:
    """BlockTradeStrategy 基础冒烟。"""

    def test_required_quality_tier_is_bronze(self) -> None:
        assert BlockTradeStrategy.required_quality_tier == QualityTier.BRONZE

    def test_empty_block_trade_returns_empty(self) -> None:
        strategy = BlockTradeStrategy()
        base_df = pd.DataFrame(
            {"ts_code": ["000001.SZ"], "name": ["A"], "industry": ["x"], "pe_ttm": [10.0], "total_mv": [100.0]}
        )
        lf = pl.from_pandas(base_df).lazy()
        result = strategy._filter_logic(lf, {"block_trade": pd.DataFrame(), "params": {}}).collect()
        assert result.height == 0

    def test_aggregate_by_ts_code_and_sort_by_amount(self) -> None:
        """block_trade 先按单笔 amount > target 过滤, 再 group_by ts_code 聚合, 按 amount 降序。"""
        strategy = BlockTradeStrategy()
        base_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "name": ["A", "B"],
                "industry": ["x", "y"],
                "pe_ttm": [10.0, 20.0],
                "total_mv": [100.0, 200.0],
            }
        )
        # 000001.SZ 两笔 amount=1200/1500 均 > 1000 入选, 聚合=2700;
        # 000002.SZ amount=500 < 1000 被剔除 (单笔过滤, 非聚合后过滤)
        block_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ", "000002.SZ"],
                "amount": [1200.0, 1500.0, 500.0],
                "vol": [100.0, 200.0, 50.0],
                "price": [10.0, 11.0, 20.0],
            }
        )
        lf = pl.from_pandas(base_df).lazy()
        result = strategy._filter_logic(lf, {"block_trade": block_df, "params": {"block_amount_min": 1000}}).collect()
        assert result["ts_code"].to_list() == ["000001.SZ"]
        assert result["amount"].to_list() == [2700.0]

    def test_missing_amount_column_returns_empty(self) -> None:
        """边界: block_trade 缺少 amount 列时返回空 (early return lf.head(0))。"""
        strategy = BlockTradeStrategy()
        base_df = pd.DataFrame(
            {"ts_code": ["000001.SZ"], "name": ["A"], "industry": ["x"], "pe_ttm": [10.0], "total_mv": [100.0]}
        )
        block_df = pd.DataFrame({"ts_code": ["000001.SZ"], "vol": [100.0]})  # 缺 amount 列
        lf = pl.from_pandas(base_df).lazy()
        result = strategy._filter_logic(lf, {"block_trade": block_df, "params": {}}).collect()
        assert result.height == 0

    def test_get_parameters_returns_one_param(self) -> None:
        strategy = BlockTradeStrategy()
        params = strategy.get_parameters()
        assert {p["name"] for p in params} == {"block_amount_min"}


# ============================================================================
# NorthboundFlowStrategy — 最少冒烟, 详细覆盖由 Task 6.2 负责
# ============================================================================


class TestNorthboundFlowStrategySmoke:
    """NorthboundFlowStrategy 最少冒烟, 详细覆盖由 Task 6.2 负责。"""

    def test_filter_logic_filters_by_market_cap(self) -> None:
        """_filter_logic 直接调用: total_mv >= mv_min 且 pe_ttm > 0, 按 total_mv 降序。"""
        strategy = NorthboundFlowStrategy()
        df = pd.DataFrame(
            {
                "ts_code": ["A.SZ", "B.SZ", "C.SZ"],
                "name": ["A", "B", "C"],
                "total_mv": [200.0, 50.0, 100.0],  # 50 < mv_min=100 不入选
                "pe_ttm": [10.0, 5.0, -1.0],  # -1 不入选
            }
        )
        lf = pl.from_pandas(df).lazy()
        result = strategy._filter_logic(lf, {"params": {"total_mv_min": 100}}).collect()
        codes = result["ts_code"].to_list()
        assert "A.SZ" in codes
        assert "B.SZ" not in codes
        assert "C.SZ" not in codes
        mvs = result["total_mv"].to_list()
        assert mvs == sorted(mvs, reverse=True)

    async def test_filter_returns_empty_when_no_flow_data(self) -> None:
        """gating: northbound_flow_data 缺失 (None) -> 直接返回空 pd.DataFrame。"""
        strategy = NorthboundFlowStrategy()
        context = {
            "data_processor": _make_dp(),
            "params": {},
        }
        with patch.object(
            strategy,
            "check_dependencies",
            return_value={"status": "ready", "missing_keys": [], "missing_tables": []},
        ):
            result = await strategy.filter(context)
            assert result.empty

    async def test_filter_returns_empty_when_flow_below_threshold(self) -> None:
        """gating: north_money <= target_flow -> 返回空, 不调用 super().filter()。"""
        strategy = NorthboundFlowStrategy()
        flow_df = pd.DataFrame(
            {
                "trade_date": ["20260101", "20260102"],
                "north_money": [30.0, 40.0],  # 最新=40, target=50, 40<=50 不达标
            }
        )
        context = {
            "northbound_flow_data": flow_df,
            "data_processor": _make_dp(),
            "params": {"nb_flow_min": 50},
        }
        with patch.object(
            strategy,
            "check_dependencies",
            return_value={"status": "ready", "missing_keys": [], "missing_tables": []},
        ):
            result = await strategy.filter(context)
            assert result.empty

    def test_get_parameters_returns_two_params(self) -> None:
        strategy = NorthboundFlowStrategy()
        params = strategy.get_parameters()
        assert {p["name"] for p in params} == {"nb_flow_min", "total_mv_min"}

    async def test_filter_handles_gating_exception(self) -> None:
        """边界: flow_df 缺 north_money 列时, gating except 分支返回空。"""
        strategy = NorthboundFlowStrategy()
        # 缺 north_money 列, sort/select 抛 ColumnNotFoundError 进入 except 分支
        flow_df = pd.DataFrame({"trade_date": ["20260101", "20260102"]})
        context = {
            "northbound_flow_data": flow_df,
            "data_processor": _make_dp(),
            "params": {"nb_flow_min": 50},
        }
        with patch.object(
            strategy,
            "check_dependencies",
            return_value={"status": "ready", "missing_keys": [], "missing_tables": []},
        ):
            result = await strategy.filter(context)
            assert result.empty
