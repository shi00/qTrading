import datetime

import numpy as np
import pandas as pd

from strategies.ai_context import _build_history_text
from strategies.ai_context.history import _resolve_name_as_of
import pytest


pytestmark = pytest.mark.unit


def test_ai_macro():
    size = 1250
    dates = [f"2020{(i % 12) + 1:02}{(i % 28) + 1:02}" for i in range(size)]
    close_prices = np.linspace(10, 50, size) + np.random.normal(0, 1, size)

    data = {
        "trade_date": dates,
        "close": close_prices.tolist(),
        "open": close_prices.tolist(),
        "high": (close_prices + 1).tolist(),
        "low": (close_prices - 1).tolist(),
        "vol": np.random.randint(1000, 10000, size=size).tolist(),
        "pct_chg": np.random.normal(0, 2, size=size).tolist(),
    }

    for k, v in data.items():
        assert len(v) == size, f"Mismatch: {k} length is {len(v)}, expected {size}"

    df = pd.DataFrame(data)

    result = _build_history_text(df)
    assert isinstance(result, str)
    assert len(result) > 0
    print("AI STRATEGY PROMPT TEXT:")
    print("-" * 50)
    print(result)
    print("-" * 50)


def _make_history_df(trade_dates, last_pct_chg):
    """构造 >5 行历史 DataFrame，最后一天 pct_chg=last_pct_chg（其余为 0），trade_date 为 date 对象。"""
    n = len(trade_dates)
    closes = list(np.linspace(10, 12, n))
    data = {
        "trade_date": [datetime.date.fromisoformat(d) for d in trade_dates],
        "close": closes,
        "open": closes,
        "high": [c + 1 for c in closes],
        "low": [c - 1 for c in closes],
        "vol": [1000] * n,
        "pct_chg": [0.0] * (n - 1) + [last_pct_chg],
    }
    return pd.DataFrame(data)


class TestResolveNameAsOf:
    """DS-02 P3：_{resolve_name_as_of} 在升序无重叠区间的 as-of 名称解析。"""

    def test_returns_current_for_in_range(self):
        ranges = [
            (datetime.date(2024, 1, 1), datetime.date(2024, 7, 1), "平安银行"),
            (datetime.date(2024, 7, 1), None, "ST平安"),
        ]
        assert _resolve_name_as_of(ranges, datetime.date(2024, 3, 15)) == "平安银行"
        assert _resolve_name_as_of(ranges, datetime.date(2024, 9, 10)) == "ST平安"
        # end 排他：恰在生效起始日归属新区间
        assert _resolve_name_as_of(ranges, datetime.date(2024, 7, 1)) == "ST平安"

    def test_returns_none_for_missing_or_before(self):
        ranges = [
            (datetime.date(2024, 1, 1), datetime.date(2024, 7, 1), "平安银行"),
            (datetime.date(2024, 7, 1), None, "ST平安"),
        ]
        assert _resolve_name_as_of(ranges, datetime.date(2023, 12, 31)) is None
        assert _resolve_name_as_of(ranges, None) is None
        assert _resolve_name_as_of(ranges, "20240101") is None  # 非 date（str）无法解析

    def test_datetime_as_of_is_normalized_to_date(self):
        # datetime 实例先归一化为 date（覆盖 as_of = as_of.date() 分支）
        ranges = [(datetime.date(2024, 1, 1), None, "平安银行")]
        assert _resolve_name_as_of(ranges, datetime.datetime(2024, 6, 15, 10, 0)) == "平安银行"

    def test_last_in_range_after_end_none(self):
        # 多段区间，end 封口后无匹配
        ranges = [
            (datetime.date(2024, 1, 1), datetime.date(2024, 3, 1), "普通一"),
            (datetime.date(2024, 3, 1), datetime.date(2024, 5, 1), "普通二"),
        ]
        assert _resolve_name_as_of(ranges, datetime.date(2024, 4, 15)) == "普通二"
        assert _resolve_name_as_of(ranges, datetime.date(2024, 5, 15)) is None


class TestBuildHistoryTextStLimit:
    """DS-02 P3：_build_history_text 用 as-of 名称判定 ST 涨跌停（ST=5% vs 主板=10%）。"""

    def test_st_name_day_triggers_limit_up_at_5pct(self):
        df = _make_history_df(
            ["2024-06-10", "2024-06-11", "2024-06-12", "2024-06-13", "2024-06-14", "2024-06-17"],
            last_pct_chg=5.2,
        )
        # 最后一天 2024-06-17 处于 ST 名称生效区间 → 5% 涨停判定应触发
        name_ranges = [
            (datetime.date(2024, 6, 1), datetime.date(2024, 6, 17), "XX股份"),
            (datetime.date(2024, 6, 17), None, "STXX"),
        ]
        result = _build_history_text(df, ts_code="000001.SZ", stock_name="STXX", name_ranges=name_ranges)
        assert isinstance(result, str)
        # ST 名 → limit=5，5.2 >= 5-0.5 → 涨停 tag
        assert "涨停" in result or "limit_up" in result or "🔴" in result

    def test_non_st_same_5pct_does_not_trigger_limit(self):
        # 主板（000001.SZ）非 ST，同日 5.2% 距 10% 涨停很远，不应标涨停
        df = _make_history_df(
            ["2024-06-10", "2024-06-11", "2024-06-12", "2024-06-13", "2024-06-14", "2024-06-17"],
            last_pct_chg=5.2,
        )
        name_ranges = [
            (datetime.date(2024, 6, 1), None, "普通股份"),
        ]
        result = _build_history_text(df, ts_code="000001.SZ", stock_name="普通股份", name_ranges=name_ranges)
        assert isinstance(result, str)
        assert "涨停" not in result and "🔴" not in result

    def test_name_ranges_none_falls_back_to_stock_name(self):
        # name_ranges=None 时回退当前 stock_name；stock_name 含 ST 仍按 5% 判定
        df = _make_history_df(
            ["2024-06-10", "2024-06-11", "2024-06-12", "2024-06-13", "2024-06-14", "2024-06-17"],
            last_pct_chg=5.2,
        )
        result = _build_history_text(df, ts_code="000001.SZ", stock_name="ST当前")
        assert isinstance(result, str)
        assert "涨停" in result or "limit_up" in result or "🔴" in result


if __name__ == "__main__":
    test_ai_macro()
