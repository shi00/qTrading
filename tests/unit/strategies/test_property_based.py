"""属性测试：使用 hypothesis 验证核心不变量。

覆盖三个不变量：
1. 复权幂等性：qfq(qfq(x)) == qfq(x)
2. NAV 一致性：期末 NAV == 现金 + Σ持仓市值，费用恒 ≥ 0
3. 策略过滤不变量：输出行集 ⊆ 输入行集

所有测试标注 @pytest.mark.slow（hypothesis 默认 100 个示例，属于慢速测试）。
"""

from datetime import date

import pandas as pd
import polars as pl
import pytest
from hypothesis import given, settings, strategies as st

from data.domain_services.transaction_cost import (
    TransactionCostConfig,
    TransactionCostModel,
)
from strategies.backtest.config import BacktestConfig
from strategies.backtest.portfolio import PortfolioSimulator
from strategies.fundamental import ValueStrategy
from utils.qfq import qfq_ratio_series

pytestmark = [pytest.mark.slow, pytest.mark.unit]


# ============================================================================
# 不变量 1：复权幂等性 qfq(qfq(x)) == qfq(x)
# ============================================================================

# 生成合法的 adj_factor 序列：正值、非空、有限
_adj_factor_lists = st.lists(
    st.floats(min_value=0.01, max_value=1000.0, allow_nan=False, allow_infinity=False),
    min_size=1,
    max_size=20,
)


@given(_adj_factor_lists)
def test_qfq_ratio_series_idempotent(factors: list[float]) -> None:
    """qfq_ratio_series 幂等性：qfq(qfq(x)) == qfq(x)。

    qfq_ratio_series 将 adj_factor 归一化到最新一日（base="latest"），
    结果序列最后一个元素恒为 1.0。再次应用 qfq 时，latest=1.0，
    除以 1.0 不改变值，故幂等。

    None 情况（空/全相同/latest=0）也满足幂等：qfq(None) == None。
    """
    series = pd.Series(factors)
    once = qfq_ratio_series(series)
    twice = qfq_ratio_series(once) if once is not None else None
    if once is None:
        # qfq(None) == None，幂等成立
        assert twice is None
    else:
        assert twice is not None
        # 除以 1.0 精确，无浮点误差
        assert once.tolist() == pytest.approx(twice.tolist())


# ============================================================================
# 不变量 2a：NAV 一致性 期末NAV == 现金 + Σ持仓市值
# ============================================================================

# 生成合法的价格（确保买入成功：价格适中、资金充足）
_nav_price = st.floats(min_value=5.0, max_value=50.0, allow_nan=False, allow_infinity=False)


@given(_nav_price)
@settings(max_examples=50)
def test_nav_total_value_equals_cash_plus_market_value(price: float) -> None:
    """NAV 一致性：total_value == cash + Σ(market_value)。

    PortfolioSimulator._record_daily_positions 按定义计算：
    total_value = self.cash + Σ(qfq_market_value)。
    此属性测试验证该不变量在随机价格下始终成立。
    """
    config = BacktestConfig(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        initial_capital=1_000_000.0,
        max_position_count=10,
        cash_reserve_pct=0.1,
    )
    cost_model = TransactionCostModel(TransactionCostConfig())
    simulator = PortfolioSimulator(config, cost_model)

    signals = pl.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "signal_rank": [1.0],
        }
    )
    quotes = pl.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "raw_open": [price],
            "raw_close": [price],
            "qfq_open": [price],
            "qfq_close": [price],
        }
    )

    simulator.process_day(
        exec_date=date(2024, 1, 2),
        day_signals=signals,
        day_quotes=quotes,
        is_rebalance=True,
    )

    positions_df = simulator.get_results()[1]
    assert not positions_df.is_empty()

    last_record = positions_df.row(-1, named=True)
    total_value = last_record["total_value"]
    cash = last_record["cash"]
    positions_detail = last_record["positions"]

    mv_sum = sum(p["market_value"] for p in positions_detail.values())
    assert total_value == pytest.approx(cash + mv_sum, rel=1e-6)


# ============================================================================
# 不变量 2b：费用恒 ≥ 0
# ============================================================================

_fee_price = st.floats(min_value=1.0, max_value=100.0, allow_nan=False, allow_infinity=False)
_fee_volume = st.integers(min_value=100, max_value=10000)
_fee_is_buy = st.booleans()


@given(_fee_price, _fee_volume, _fee_is_buy)
@settings(max_examples=50)
def test_transaction_cost_fees_non_negative(price: float, volume: int, is_buy: bool) -> None:
    """交易费用恒 ≥ 0。

    TransactionCost 的所有费用分量（commission/stamp_duty/transfer_fee/slippage_cost）
    均应非负。total_cost = Σ(各分量) 也应 ≥ 0。
    """
    config = TransactionCostConfig()
    model = TransactionCostModel(config)
    cost = model.calculate(
        price=price,
        volume=volume,
        is_buy=is_buy,
        trade_date=date(2024, 6, 1),
    )
    assert cost.commission >= 0
    assert cost.stamp_duty >= 0
    assert cost.transfer_fee >= 0
    assert cost.slippage_cost >= 0
    assert cost.total_cost >= 0


# ============================================================================
# 不变量 3：策略过滤不变量 输出行集 ⊆ 输入行集
# ============================================================================

# 生成合法的基本面数据行
_fundamental_row = st.builds(
    dict,
    ts_code=st.sampled_from(["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ", "000005.SZ"]),
    pe_ttm=st.floats(min_value=-50.0, max_value=100.0, allow_nan=False, allow_infinity=False),
    pb=st.floats(min_value=-5.0, max_value=20.0, allow_nan=False, allow_infinity=False),
    dv_ttm=st.floats(min_value=-5.0, max_value=20.0, allow_nan=False, allow_infinity=False),
)
_fundamental_dfs = st.lists(_fundamental_row, min_size=1, max_size=20).map(lambda rows: pd.DataFrame(rows))


@given(_fundamental_dfs)
@settings(max_examples=50)
def test_strategy_filter_output_subset_of_input(df: pd.DataFrame) -> None:
    """策略过滤不变量：输出行集 ⊆ 输入行集。

    ValueStrategy._filter_logic 仅使用 drop_nulls + filter + sort，
    这些操作只移除或重排行，不会新增行。
    验证输出 ts_code 集合是输入的子集。
    """
    strategy = ValueStrategy()
    lf = pl.from_pandas(df).lazy()
    context = {"params": {"pe_min": 5, "pe_max": 20, "pb_max": 3, "dv_min": 2}}
    result = strategy._filter_logic(lf, context).collect()

    input_codes = set(df["ts_code"].tolist())
    output_codes = set(result["ts_code"].to_list())
    assert output_codes <= input_codes
