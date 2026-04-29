import pandas as pd
import pytest

from strategies.fundamental import LargePEStrategy
from strategies.oversold_strategy import OversoldStrategy


@pytest.mark.unit
class TestOversoldSortForAi:
    def test_sorts_by_rsi_then_liquidity(self):
        s = OversoldStrategy()
        df = pd.DataFrame(
            {
                "ts_code": ["A", "B", "C"],
                "rsi_14": [25, 15, 20],
                "amount": [500, 100, 300],
                "total_mv": [1000, 2000, 1500],
            },
        )
        result = s._sort_for_ai(df)
        assert list(result["ts_code"]) == ["B", "C", "A"]

    def test_empty_df_returns_empty(self):
        s = OversoldStrategy()
        result = s._sort_for_ai(pd.DataFrame())
        assert result.empty

    def test_no_rsi_column_uses_liquidity(self):
        s = OversoldStrategy()
        df = pd.DataFrame(
            {
                "ts_code": ["A", "B", "C"],
                "amount": [100, 500, 300],
                "total_mv": [1000, 2000, 1500],
            },
        )
        result = s._sort_for_ai(df)
        assert list(result["ts_code"]) == ["B", "C", "A"]

    def test_cap_30_preserves_high_liquidity_over_st(self):
        s = OversoldStrategy()
        rows = []
        for i in range(40):
            rows.append(
                {
                    "ts_code": f"ST{i:03d}.SZ" if i < 20 else f"GOOD{i:03d}.SZ",
                    "rsi_14": 10 + i * 0.5,
                    "amount": 50 if i < 20 else 500,
                    "total_mv": 50 if i < 20 else 2000,
                },
            )
        df = pd.DataFrame(rows)
        result = s._sort_for_ai(df)
        top_30 = result.head(30)
        high_liq_count = len(top_30[top_30["amount"] >= 500])
        assert high_liq_count > 0, "High-liquidity stocks should appear in top 30 after sort"


@pytest.mark.unit
class TestLargePESortForAi:
    def test_sorts_by_pe_ttm_ascending(self):
        s = LargePEStrategy()
        df = pd.DataFrame(
            {
                "ts_code": ["A", "B", "C"],
                "pe_ttm": [15, 5, 10],
                "total_mv": [5000, 1000, 3000],
            },
        )
        result = s._sort_for_ai(df)
        assert list(result["ts_code"]) == ["B", "C", "A"]

    def test_empty_df_returns_empty(self):
        s = LargePEStrategy()
        result = s._sort_for_ai(pd.DataFrame())
        assert result.empty

    def test_no_pe_column_preserves_order(self):
        s = LargePEStrategy()
        df = pd.DataFrame(
            {
                "ts_code": ["A", "B"],
                "total_mv": [5000, 1000],
            },
        )
        result = s._sort_for_ai(df)
        assert list(result["ts_code"]) == ["A", "B"]

    def test_low_pe_stocks_prioritized_over_high_mv(self):
        s = LargePEStrategy()
        df = pd.DataFrame(
            {
                "ts_code": ["HIGH_MV", "LOW_PE"],
                "pe_ttm": [14, 3],
                "total_mv": [50000, 600],
            },
        )
        result = s._sort_for_ai(df)
        assert result.iloc[0]["ts_code"] == "LOW_PE"
