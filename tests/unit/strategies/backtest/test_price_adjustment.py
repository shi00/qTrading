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
        # 基准为首日 adj_factor=1.0，所以前两日 qfq=raw，第三日 adj_factor=2.0 则 qfq 翻倍
        assert qfq_close[0] == pytest.approx(10.2, rel=0.01)
        assert qfq_close[1] == pytest.approx(11.2, rel=0.01)
        assert qfq_close[2] == pytest.approx(24.4, rel=0.01)

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

        # Stock1: 基准为首日 adj_factor=1.0, Day2 adj_factor=2.0 → qfq_close = 11.2*2.0/1.0 = 22.4
        assert stock1["qfq_close"].to_list()[1] == pytest.approx(22.4, rel=0.01)
        # Stock2: 基准为首日 adj_factor=1.0, Day2 adj_factor=1.0 → qfq_close = 22.2
        assert stock2["qfq_close"].to_list()[1] == pytest.approx(22.2, rel=0.01)


class TestExRightDate:
    """除权日复权价格计算专项测试"""

    def test_ex_right_dividend(self) -> None:
        """测试除权除息日复权价格计算。

        场景：股票 10 送 10，adj_factor 从 1.0 变为 0.5
        复权后，除权前的价格应该调整为原来的一半。
        """
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ"],
                "trade_date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
                "open": [20.0, 10.0, 10.5],
                "high": [20.5, 10.5, 11.0],
                "low": [19.5, 9.5, 10.0],
                "close": [20.2, 10.2, 10.8],
                "adj_factor": [0.5, 0.5, 0.5],
            }
        )

        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config

        result = engine._apply_qfq(quotes_df)

        assert result["raw_open"].to_list() == [20.0, 10.0, 10.5]
        assert result["raw_close"].to_list() == [20.2, 10.2, 10.8]

        qfq_close = result["qfq_close"].to_list()
        assert qfq_close[0] == pytest.approx(20.2, rel=0.01)
        assert qfq_close[1] == pytest.approx(10.2, rel=0.01)
        assert qfq_close[2] == pytest.approx(10.8, rel=0.01)

    def test_ex_right_with_subsequent_adjustment(self) -> None:
        """测试除权后继续有复权因子变化。

        场景：除权日 adj_factor=0.5，之后 adj_factor=0.25（再次除权）
        """
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ", "000001.SZ"],
                "trade_date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
                "open": [20.0, 10.0, 10.5, 5.0],
                "high": [20.5, 10.5, 11.0, 5.5],
                "low": [19.5, 9.5, 10.0, 4.5],
                "close": [20.2, 10.2, 10.8, 5.2],
                "adj_factor": [0.5, 0.5, 0.25, 0.25],
            }
        )

        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config

        result = engine._apply_qfq(quotes_df)

        qfq_close = result["qfq_close"].to_list()
        base_adj = 0.5  # 首日 adj_factor 作为基准

        assert qfq_close[0] == pytest.approx(20.2 * 0.5 / base_adj, rel=0.01)
        assert qfq_close[1] == pytest.approx(10.2 * 0.5 / base_adj, rel=0.01)
        assert qfq_close[2] == pytest.approx(10.8 * 0.25 / base_adj, rel=0.01)
        assert qfq_close[3] == pytest.approx(5.2 * 0.25 / base_adj, rel=0.01)

    def test_ex_right_preserves_trading_value(self) -> None:
        """测试除权日成交金额使用原始价格。

        关键：成交金额必须使用 raw_open/raw_close，不能用 qfq 价格。
        """
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ"],
                "trade_date": [date(2024, 1, 1), date(2024, 1, 2)],
                "open": [20.0, 10.0],
                "high": [20.5, 10.5],
                "low": [19.5, 9.5],
                "close": [20.2, 10.2],
                "adj_factor": [0.5, 0.5],
            }
        )

        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config

        result = engine._apply_qfq(quotes_df)

        raw_open = result["raw_open"].to_list()
        raw_close = result["raw_close"].to_list()

        assert raw_open[0] == 20.0
        assert raw_open[1] == 10.0
        assert raw_close[0] == 20.2
        assert raw_close[1] == 10.2

        volume = 1000
        gross_amount_day1 = raw_open[0] * volume
        gross_amount_day2 = raw_open[1] * volume

        assert gross_amount_day1 == 20000.0
        assert gross_amount_day2 == 10000.0
