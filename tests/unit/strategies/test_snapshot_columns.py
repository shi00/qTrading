"""D2-M5：策略结果列完整性守卫。

review_manager.save_results 逐字段消费筛选结果中的基础快照列
（close/pct_chg/vol/amount/turnover_rate/pe_ttm/pb/dv_ttm/roe 等），
落库到 screening_history。三个市场类策略曾把 base universe 裁剪到
5 列，导致这些快照字段大面积落 NULL。

本测试对三个受影响的注册策略跑最小 fixture，断言输出列包含
ReviewManager 消费的核心快照列集合——任一策略再次裁剪列即失败。
"""

import pandas as pd
import polars as pl
import pytest

from strategies.market import (
    BlockTradeStrategy,
    InstitutionalStrategy,
    NorthboundHoldingStrategy,
)

pytestmark = [pytest.mark.unit]

# 直接引用策略类而非依赖惰性注册表（_import_all_strategies 仅在
# StrategyManager 构造时执行，xdist 并行下 worker 可能未触发注册）。
_STRATEGY_FIXTURES = [
    ("northbound_holding", NorthboundHoldingStrategy),
    ("institutional", InstitutionalStrategy),
    ("block_trade", BlockTradeStrategy),
]

# ReviewManager.save_results 逐字段消费的核心快照列（data/persistence/review_manager.py）
_SNAPSHOT_COLUMNS = {
    "ts_code",
    "name",
    "close",
    "pct_chg",
    "industry_sw_l2",
    "vol",
    "amount",
    "turnover_rate",
    "pe_ttm",
    "pb",
    "ps_ttm",
    "dv_ttm",
    "total_mv",
    "circ_mv",
    "roe",
    "grossprofit_margin",
    "debt_to_assets",
    "or_yoy",
    "netprofit_yoy",
}


def _make_base_df() -> pd.DataFrame:
    """构造含完整快照列的 base universe（与 screener_dao._SCREENING_SQL_TEMPLATE 列一致）。"""
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000002.SZ"],
            "name": ["平安银行", "万科A"],
            "industry_sw_l2": ["银行", "房地产"],
            "close": [12.5, 8.2],
            "pct_chg": [1.2, -0.5],
            "vol": [500000.0, 300000.0],
            "amount": [6.0e8, 2.4e8],
            "turnover_rate": [1.5, 0.9],
            "pe_ttm": [6.5, 8.0],
            "pb": [0.8, 1.2],
            "ps_ttm": [2.0, 3.0],
            "dv_ttm": [5.1, 4.2],
            "total_mv": [2.1e7, 9.9e6],  # 万
            "circ_mv": [1.8e7, 9.5e6],  # 万
            "roe": [11.2, 7.5],
            "grossprofit_margin": [35.0, 28.0],
            "debt_to_assets": [90.0, 78.0],
            "or_yoy": [5.0, -8.0],
            "netprofit_yoy": [3.5, -12.0],
        }
    )


def _make_right_df(key: str) -> pd.DataFrame:
    """按策略构造右表数据，保证至少一只股票通过过滤。"""
    if key == "northbound_holding":
        return pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "name": ["平安银行", "万科A"],
                "vol": [100000.0, 50000.0],  # 北向持股数（与 base 成交量不同语义）
                "ratio": [8.0, 2.0],
                "exchange": ["SZ", "SZ"],
            }
        )
    if key == "institutional":
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "name": ["平安银行", "万科A"],
                "close": [12.5, 8.2],
                "pct_change": [1.2, -0.5],
                "turnover_rate": [1.5, 0.9],
                "amount": [6.0e8, 2.4e8],
                "net_amount": [5.0e7, 1.0e7],  # 元: 5000万 / 1000万(低于默认阈值3000万)
            }
        )
        df.attrs["column_units"] = {"net_amount": "yuan"}
        return df
    # block_trade（vol 单位=万股，amount 万元≈price×vol 自洽）：
    # 000001.SZ 两笔 VWAP=(2000+1500)/(160+119)=12.54 元 → 折价率 0.36% 未超 10% 剔除线，聚合 3500 > 1000 入选；
    # 000002.SZ 单笔 amount=500 < 1000 剔除
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000001.SZ", "000002.SZ"],
            "price": [12.5, 12.6, 8.2],
            "vol": [160.0, 119.0, 61.0],
            "amount": [2000.0, 1500.0, 500.0],
        }
    )


def _make_context(key: str, right_df: pd.DataFrame) -> dict:
    ctx: dict = {"params": {}}
    if key == "northbound_holding":
        ctx["northbound_data"] = right_df
    elif key == "institutional":
        ctx["top_list"] = right_df
    else:
        ctx["block_trade"] = right_df
    return ctx


@pytest.mark.parametrize(("key", "strategy_cls"), _STRATEGY_FIXTURES)
def test_result_keeps_full_snapshot_columns(key: str, strategy_cls: type) -> None:
    """输出列包含 ReviewManager.save_results 消费的核心快照列集合。

    任一策略再次裁剪 base 列（close/vol/amount/pe_ttm/roe 等）即失败。
    """
    strategy = strategy_cls()
    base_df = _make_base_df()
    lf = pl.from_pandas(base_df).lazy()
    result = strategy._filter_logic(lf, _make_context(key, _make_right_df(key))).collect()

    assert result.height > 0, f"{key} 最小 fixture 应至少筛出一只股票"
    missing = _SNAPSHOT_COLUMNS - set(result.columns)
    assert not missing, f"{key} 结果缺失快照列: {sorted(missing)}"


@pytest.mark.parametrize(("key", "strategy_cls"), _STRATEGY_FIXTURES)
def test_snapshot_values_from_base(key: str, strategy_cls: type) -> None:
    """快照值取自 base universe：close/pe_ttm/roe 等与 base 输入一致（未被右表同名列污染）。"""
    strategy = strategy_cls()
    base_df = _make_base_df()
    lf = pl.from_pandas(base_df).lazy()
    result = strategy._filter_logic(lf, _make_context(key, _make_right_df(key))).collect()
    row = result.filter(pl.col("ts_code") == "000001.SZ").to_dicts()[0]
    assert row["close"] == 12.5
    assert row["pe_ttm"] == 6.5
    assert row["roe"] == 11.2
    assert row["industry_sw_l2"] == "银行"
