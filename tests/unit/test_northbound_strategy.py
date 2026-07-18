"""
NorthboundFlowStrategy 单元测试 (Phase 6 Task 6.2).

覆盖 DoD 场景:
- 北向资金净流入阈值 (gating): north_money > nb_flow_min 才放行
- 空结果: flow_data 为 None / 空 DataFrame / screening_data 为空
- 缺失列异常处理: north_money 列缺失 → 异常被捕获, 返回空
- 按 total_mv 降序: 任务描述"按净流入降序"映射到策略实际排序字段
- required_quality_tier 类属性验证
- R2 CancelledError 传播

注: NorthboundFlowStrategy 实际不处理"持股比例变化"/"连续增持"逻辑
(那是 NorthboundHoldingStrategy 的职责). 本策略将 north_money 作为
市场级 gating 信号, 通过 fundamental criteria (total_mv, pe_ttm) 筛选
基础宇宙. 本测试按实际行为覆盖, 不臆造不存在的功能 (CLAUDE.md §1.10).
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import polars as pl
import pytest

from data.persistence.quality_gate import QualityTier
from strategies.market import NorthboundFlowStrategy
from utils.thread_pool import ThreadPoolManager

pytestmark = pytest.mark.unit


def _make_dp(tier: QualityTier = QualityTier.GOLD) -> MagicMock:
    """构造 mock DataProcessor, 满足 _check_tier 要求."""
    dp = MagicMock()
    dp._quality_tier = tier
    return dp


def _make_flow_df(north_money: float | None, trade_date: str = "20240101") -> pd.DataFrame:
    return pd.DataFrame([{"trade_date": trade_date, "north_money": north_money}])


def _make_screening_df() -> pd.DataFrame:
    """构造多只股票的 screening_data, 用于过滤+排序测试."""
    return pd.DataFrame(
        [
            {"ts_code": "000001.SZ", "name": "stock_a", "industry": "Bank", "pe_ttm": 5.5, "total_mv": 4000.0},
            {"ts_code": "600000.SH", "name": "stock_b", "industry": "Bank", "pe_ttm": 4.5, "total_mv": 3000.0},
            {"ts_code": "000002.SZ", "name": "small_cap", "industry": "Tech", "pe_ttm": 20.0, "total_mv": 50.0},
        ]
    )


class TestNorthboundFlowStrategy:
    """NorthboundFlowStrategy 单元测试 (Phase 6 Task 6.2)."""

    # ----- 类属性声明 -----

    def test_required_quality_tier_is_silver(self):
        """验证 required_quality_tier 类属性声明 (继承 PolarsBaseStrategy 默认值 SILVER)."""
        assert NorthboundFlowStrategy.required_quality_tier == QualityTier.SILVER

    def test_required_context_keys_declared(self):
        """验证 required_context_keys 声明 northbound_flow_data."""
        assert "northbound_flow_data" in NorthboundFlowStrategy.required_context_keys

    def test_required_tables_declared(self):
        """验证 required_tables 声明 moneyflow_hsgt."""
        assert "moneyflow_hsgt" in NorthboundFlowStrategy.required_tables

    def test_enable_ai_analysis_disabled(self):
        """验证 enable_ai_analysis = False (跳过 Phase 2 AI 分析)."""
        strat = NorthboundFlowStrategy()
        assert strat.enable_ai_analysis is False

    def test_get_parameters_declares_nb_flow_and_total_mv(self):
        """验证 get_parameters 声明 nb_flow_min 与 total_mv_min 两个参数."""
        strat = NorthboundFlowStrategy()
        params = strat.get_parameters()
        names = {p["name"] for p in params}
        assert names == {"nb_flow_min", "total_mv_min"}

    # ----- 北向资金净流入阈值 (gating) -----

    async def test_gating_passes_when_north_money_above_threshold(self):
        """north_money > nb_flow_min → gating 通过, 执行 _filter_logic 返回股票."""
        flow_df = _make_flow_df(north_money=120.0)
        strat = NorthboundFlowStrategy()
        ctx = {
            "northbound_flow_data": flow_df,
            "screening_data": _make_screening_df(),
            "params": {"nb_flow_min": 50, "total_mv_min": 100},
            "data_processor": _make_dp(),
        }
        out = await strat.filter(ctx)
        # total_mv >= 100 → 排除 small_cap (50.0); pe_ttm > 0 → 都通过
        assert len(out) == 2
        ts_codes = set(out["ts_code"].tolist())
        assert ts_codes == {"000001.SZ", "600000.SH"}

    async def test_gating_returns_empty_when_north_money_below_threshold(self):
        """north_money < nb_flow_min → gating 失败, 返回空 DataFrame."""
        flow_df = _make_flow_df(north_money=30.0)
        strat = NorthboundFlowStrategy()
        ctx = {
            "northbound_flow_data": flow_df,
            "screening_data": _make_screening_df(),
            "params": {"nb_flow_min": 50},
            "data_processor": _make_dp(),
        }
        out = await strat.filter(ctx)
        assert out.empty

    async def test_gating_returns_empty_when_north_money_equals_threshold(self):
        """north_money == nb_flow_min → 边界判断使用 <=, 返回空."""
        flow_df = _make_flow_df(north_money=50.0)
        strat = NorthboundFlowStrategy()
        ctx = {
            "northbound_flow_data": flow_df,
            "screening_data": _make_screening_df(),
            "params": {"nb_flow_min": 50},
            "data_processor": _make_dp(),
        }
        out = await strat.filter(ctx)
        assert out.empty

    async def test_gating_returns_empty_when_north_money_is_none(self):
        """north_money 为 None → gating 失败, 返回空 DataFrame."""
        flow_df = _make_flow_df(north_money=None)
        strat = NorthboundFlowStrategy()
        ctx = {
            "northbound_flow_data": flow_df,
            "screening_data": _make_screening_df(),
            "params": {"nb_flow_min": 50},
            "data_processor": _make_dp(),
        }
        out = await strat.filter(ctx)
        assert out.empty

    async def test_gating_uses_latest_trade_date_north_money(self):
        """gating 按 trade_date 降序取第一行的 north_money (最新交易日)."""
        flow_df = pd.DataFrame(
            [
                {"trade_date": "20240101", "north_money": 10.0},
                {"trade_date": "20240103", "north_money": 120.0},  # 最新, 应取此
                {"trade_date": "20240102", "north_money": 30.0},
            ]
        )
        strat = NorthboundFlowStrategy()
        ctx = {
            "northbound_flow_data": flow_df,
            "screening_data": _make_screening_df(),
            "params": {"nb_flow_min": 50, "total_mv_min": 100},
            "data_processor": _make_dp(),
        }
        out = await strat.filter(ctx)
        # 最新 trade_date 的 north_money=120.0 > 50, gating 通过
        assert not out.empty

    # ----- 空结果 -----

    async def test_filter_returns_empty_when_flow_data_is_none(self):
        """northbound_flow_data 为 None → 返回空 (不抛异常)."""
        strat = NorthboundFlowStrategy()
        ctx = {
            "northbound_flow_data": None,
            "screening_data": _make_screening_df(),
            "params": {"nb_flow_min": 50},
            "data_processor": _make_dp(),
        }
        out = await strat.filter(ctx)
        assert out.empty

    async def test_filter_returns_empty_when_flow_data_is_empty_df(self):
        """northbound_flow_data 为空 DataFrame → 返回空 (不抛异常)."""
        strat = NorthboundFlowStrategy()
        ctx = {
            "northbound_flow_data": pd.DataFrame(),
            "screening_data": _make_screening_df(),
            "params": {"nb_flow_min": 50},
            "data_processor": _make_dp(),
        }
        out = await strat.filter(ctx)
        assert out.empty

    async def test_filter_returns_empty_when_screening_data_is_empty(self):
        """gating 通过但 screening_data 为空 → 返回空 (不抛异常)."""
        flow_df = _make_flow_df(north_money=120.0)
        strat = NorthboundFlowStrategy()
        ctx = {
            "northbound_flow_data": flow_df,
            "screening_data": pd.DataFrame(),
            "params": {"nb_flow_min": 50},
            "data_processor": _make_dp(),
        }
        out = await strat.filter(ctx)
        assert out.empty

    # ----- 缺失列异常处理 -----

    async def test_gating_returns_empty_when_north_money_column_missing(self):
        """northbound_flow_data 缺少 north_money 列 → 异常被 except 捕获, 返回空."""
        flow_df = pd.DataFrame([{"trade_date": "20240101", "other_col": 100.0}])
        strat = NorthboundFlowStrategy()
        ctx = {
            "northbound_flow_data": flow_df,
            "screening_data": _make_screening_df(),
            "params": {"nb_flow_min": 50},
            "data_processor": _make_dp(),
        }
        out = await strat.filter(ctx)
        assert out.empty

    # ----- 按 total_mv 降序 (实际排序字段) -----

    async def test_filter_sorts_by_total_mv_descending(self):
        """多只股票通过过滤 → 按 total_mv 降序排列.

        注: 任务描述"按净流入降序"在 NorthboundFlowStrategy 中实际排序字段
        为 total_mv (基础宇宙排序), 因为 north_money 是市场级 gating 信号,
        不是 per-stock 字段. 本测试验证实际行为 (CLAUDE.md §1.10).
        """
        flow_df = _make_flow_df(north_money=120.0)
        strat = NorthboundFlowStrategy()
        ctx = {
            "northbound_flow_data": flow_df,
            "screening_data": _make_screening_df(),
            "params": {"nb_flow_min": 50, "total_mv_min": 100},
            "data_processor": _make_dp(),
        }
        out = await strat.filter(ctx)
        total_mv_values = out["total_mv"].tolist()
        assert total_mv_values == sorted(total_mv_values, reverse=True)

    # ----- _filter_logic 单元测试 (直接调用, 隔离 gating) -----

    def test_filter_logic_filters_by_total_mv_threshold(self):
        """_filter_logic 按 total_mv >= mv_min 过滤."""
        strat = NorthboundFlowStrategy()
        lf = pl.from_pandas(_make_screening_df()).lazy()
        ctx = {"params": {"total_mv_min": 100}}
        result = strat._filter_logic(lf, ctx).collect()
        ts_codes = result["ts_code"].to_list()
        assert "000001.SZ" in ts_codes  # total_mv=4000
        assert "600000.SH" in ts_codes  # total_mv=3000
        assert "000002.SZ" not in ts_codes  # total_mv=50 < 100

    def test_filter_logic_filters_by_pe_ttm_positive(self):
        """_filter_logic 按 pe_ttm > 0 过滤 (排除亏损股)."""
        strat = NorthboundFlowStrategy()
        df = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "name": "profitable", "industry": "Tech", "pe_ttm": 10.0, "total_mv": 1000.0},
                {"ts_code": "000002.SZ", "name": "loss_making", "industry": "Tech", "pe_ttm": -5.0, "total_mv": 1000.0},
            ]
        )
        lf = pl.from_pandas(df).lazy()
        ctx = {"params": {"total_mv_min": 100}}
        result = strat._filter_logic(lf, ctx).collect()
        ts_codes = result["ts_code"].to_list()
        assert "000001.SZ" in ts_codes
        assert "000002.SZ" not in ts_codes

    def test_filter_logic_sorts_by_total_mv_descending(self):
        """_filter_logic 直接调用 → 按 total_mv 降序."""
        strat = NorthboundFlowStrategy()
        lf = pl.from_pandas(_make_screening_df()).lazy()
        ctx = {"params": {"total_mv_min": 0}}
        result = strat._filter_logic(lf, ctx).collect()
        total_mv_values = result["total_mv"].to_list()
        assert total_mv_values == sorted(total_mv_values, reverse=True)

    # ----- R2 CancelledError 传播 -----

    async def test_cancelled_error_propagates_from_threadpool(self):
        """R2: ThreadPoolManager 抛 CancelledError 时必须传播, 不得被 except Exception 吞没."""
        flow_df = _make_flow_df(north_money=120.0)
        strat = NorthboundFlowStrategy()
        ctx = {
            "northbound_flow_data": flow_df,
            "screening_data": _make_screening_df(),
            "params": {"nb_flow_min": 50},
            "data_processor": _make_dp(),
        }
        with patch.object(ThreadPoolManager, "run_async", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = asyncio.CancelledError()
            with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion R2 守卫：验证 CancelledError 不被 except Exception 吞没，raises 本身即充分断言
                await strat.filter(ctx)
