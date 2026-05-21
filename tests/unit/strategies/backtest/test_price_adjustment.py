"""复权价格处理单元测试"""

from datetime import date

import polars as pl
import pytest

from strategies.backtest.engine import VectorBacktestEngine
from strategies.backtest.config import BacktestConfig


class TestPriceAdjustment:
    def test_apply_qfq_with_adj_factor(self) -> None:
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ"],
                "trade_date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
                "open": [10.0, 11.0, 12.0],
                "high": [10.5, 11.5, 12.5],
                "low": [9.5, 10.5, 11.5],
                "close": [10.2, 11.2, 12.2],
                "adj_factor": [1.0, 1.0, 2.0],
            }
        )

        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config

        result = engine._apply_qfq(quotes_df)

        assert "raw_open" in result.columns
        assert "raw_high" in result.columns
        assert "raw_low" in result.columns
        assert "raw_close" in result.columns
        assert "qfq_open" in result.columns
        assert "qfq_high" in result.columns
        assert "qfq_low" in result.columns
        assert "qfq_close" in result.columns
        assert "qfq_ratio" in result.columns

        assert result["raw_open"].to_list() == [10.0, 11.0, 12.0]
        assert result["raw_close"].to_list() == [10.2, 11.2, 12.2]

        qfq_close = result["qfq_close"].to_list()
        assert qfq_close[0] == pytest.approx(5.1, rel=0.01)
        assert qfq_close[1] == pytest.approx(5.6, rel=0.01)
        assert qfq_close[2] == pytest.approx(12.2, rel=0.01)

    def test_apply_qfq_without_adj_factor(self) -> None:
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ"],
                "trade_date": [date(2024, 1, 1), date(2024, 1, 2)],
                "open": [10.0, 11.0],
                "high": [10.5, 11.5],
                "low": [9.5, 10.5],
                "close": [10.2, 11.2],
            }
        )

        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config

        result = engine._apply_qfq(quotes_df)

        assert "raw_open" in result.columns
        assert "qfq_open" in result.columns
        assert result["raw_open"].to_list() == [10.0, 11.0]
        assert result["qfq_open"].to_list() == [10.0, 11.0]

    def test_apply_qfq_multiple_stocks(self) -> None:
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ", "000002.SZ", "000002.SZ"],
                "trade_date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 1), date(2024, 1, 2)],
                "open": [10.0, 11.0, 20.0, 22.0],
                "high": [10.5, 11.5, 20.5, 22.5],
                "low": [9.5, 10.5, 19.5, 21.5],
                "close": [10.2, 11.2, 20.2, 22.2],
                "adj_factor": [1.0, 2.0, 1.0, 1.0],
            }
        )

        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config

        result = engine._apply_qfq(quotes_df)

        stock1 = result.filter(pl.col("ts_code") == "000001.SZ")
        stock2 = result.filter(pl.col("ts_code") == "000002.SZ")

        assert stock1["qfq_close"].to_list()[1] == pytest.approx(11.2, rel=0.01)
        assert stock2["qfq_close"].to_list()[1] == pytest.approx(22.2, rel=0.01)
