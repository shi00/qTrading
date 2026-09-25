import datetime

import numpy as np
import pandas as pd

from core.i18n import I18n
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


class TestBuildHistoryTextLimitStatus:
    """MAJOR-02：涨跌停判定优先用交易所公布价（stk_limit），缺失才降级板块规则。"""

    _DATES = ["2024-06-10", "2024-06-11", "2024-06-12", "2024-06-13", "2024-06-14", "2024-06-17"]
    _LAST3 = _DATES[-3:]

    @staticmethod
    def _df(closes: list[float], last_pct_chg: float) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "trade_date": [datetime.date.fromisoformat(d) for d in TestBuildHistoryTextLimitStatus._DATES],
                "close": closes,
                "open": closes,
                "high": closes,
                "low": closes,
                "vol": [1000.0] * len(closes),
                "pct_chg": [0.0] * (len(closes) - 1) + [last_pct_chg],
            }
        )

    @staticmethod
    def _limit_df(rows: list[tuple[str, float, float]]) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": datetime.date.fromisoformat(d),
                    "up_limit": up,
                    "down_limit": down,
                }
                for d, up, down in rows
            ]
        )

    def test_gain_9_6pct_not_limit_up_with_exchange_price(self):
        # 核心回归：主板涨 9.6% 未封板（close 低于交易所公布涨停价）不得打涨停标签
        closes = [10.4, 10.5, 10.6, 10.7, 10.8, 10.96]
        df = self._df(closes, last_pct_chg=9.6)
        limit_df = self._limit_df([(d, 99.0, 0.01) for d in self._LAST3])
        # 最后一天涨停价为 11.0 > close(10.96)+0.005 → 未封板
        limit_df.loc[limit_df["trade_date"] == datetime.date.fromisoformat(self._LAST3[-1]), "up_limit"] = 11.0
        result = _build_history_text(df, ts_code="000001.SZ", stock_name="普通股份", limit_df=limit_df)
        assert f"🔴{I18n.get('ai_limit_up')}" not in result
        assert I18n.get("ai_limit_price_missing") not in result

    def test_exchange_price_close_at_limit_is_limit_up(self):
        # 同一 9.6% 行情，若交易所公布价显示已封板（close >= up_limit-0.005）→ 打涨停标签
        closes = [10.4, 10.5, 10.6, 10.7, 10.8, 10.96]
        df = self._df(closes, last_pct_chg=9.6)
        limit_df = self._limit_df([(d, 99.0, 0.01) for d in self._LAST3])
        limit_df.loc[limit_df["trade_date"] == datetime.date.fromisoformat(self._LAST3[-1]), "up_limit"] = 10.96
        result = _build_history_text(df, ts_code="000001.SZ", stock_name="普通股份", limit_df=limit_df)
        assert f"🔴{I18n.get('ai_limit_up')}" in result

    def test_missing_limit_row_skips_tag_and_notifies(self):
        # stk_limit 非空但该日无记录 → 该日不打标签，段落只追加一次缺失提示（R21）
        closes = [10.4, 10.5, 10.6, 10.7, 10.8, 10.96]
        df = self._df(closes, last_pct_chg=9.6)
        limit_df = self._limit_df([(d, 99.0, 0.01) for d in self._LAST3[:-1]])  # 缺最后一天
        result = _build_history_text(df, ts_code="000001.SZ", stock_name="普通股份", limit_df=limit_df)
        assert f"🔴{I18n.get('ai_limit_up')}" not in result
        assert result.count(I18n.get("ai_limit_price_missing")) == 1

    def test_empty_limit_df_falls_back_to_board_rule(self):
        # limit_df 为 None/空 → 降级板块规则近似，真封板仍打标签，并整段追加一次近似提示
        closes = [10.4, 10.5, 10.6, 10.7, 10.8, 11.0]
        df = self._df(closes, last_pct_chg=10.0)
        result = _build_history_text(df, ts_code="000001.SZ", stock_name="普通股份", limit_df=None)
        assert f"🔴{I18n.get('ai_limit_up')}" in result
        assert result.count(I18n.get("ai_limit_price_approx")) == 1
        assert I18n.get("ai_limit_price_missing") not in result

    def test_empty_limit_df_no_approx_when_insufficient(self):
        # 数据不足走哨兵时不注册标签，也不追加近似提示
        df = pd.DataFrame(
            {
                "trade_date": [datetime.date.fromisoformat(d) for d in self._DATES[:2]],
                "close": [10.4, 10.5],
                "vol": [1000.0, 1000.0],
                "pct_chg": [0.0, 10.0],
            }
        )
        result = _build_history_text(df, ts_code="000001.SZ", stock_name="普通股份", limit_df=None)
        assert result == I18n.get("ai_history_insufficient")

    def test_window_ex_right_still_tags_historical_limit_up(self):
        """F1：窗口内除权（adj 1.0→1.05）时历史日仍能按名义价正确判定涨停。

        06-14 名义 close = 11.0（涨停价 11.0）；前复权后 close ≈ 11.0 × 0.952 = 10.476，
        若直接用复权 close 与名义涨停价比较会静默漏标（既无标签也无缺失提示）。
        """
        df = self._df([10.0, 10.0, 10.0, 10.0, 11.0, 10.5], last_pct_chg=-4.5)
        df.loc[4, "pct_chg"] = 10.0
        df["adj_factor"] = [1.0, 1.0, 1.0, 1.0, 1.0, 1.05]
        limit_df = self._limit_df([(d, 99.0, 0.01) for d in self._LAST3])
        limit_df.loc[limit_df["trade_date"] == datetime.date.fromisoformat("2024-06-14"), "up_limit"] = 11.0
        result = _build_history_text(df, ts_code="000001.SZ", stock_name="普通股份", limit_df=limit_df)
        assert f"🔴{I18n.get('ai_limit_up')}" in result
        assert I18n.get("ai_limit_price_missing") not in result

    def test_low_price_board_rule_tolerance_tags_limit_up(self):
        """F2：降级路径容差按 0.5/close 反推后，仍能标注取整导致的真实封板（不得过度收紧）。

        pre_close 3.33 → 名义涨停价 3.663 按 0.01 元取整为 3.66 → 实际 pct_chg = 9.91% 略低于
        名义 10%；若容差收紧过度（如取近零值），该真实涨停会被漏标。此处钉住下界。
        """
        df = self._df([3.0, 3.1, 3.2, 3.3, 3.33, 3.66], last_pct_chg=9.91)
        result = _build_history_text(df, ts_code="000001.SZ", stock_name="普通股份", limit_df=None)
        assert f"🔴{I18n.get('ai_limit_up')}" in result
        assert result.count(I18n.get("ai_limit_price_approx")) == 1

    def test_low_price_big_rise_not_tagged_limit_up(self):
        """封顶生效：低价股容差退化为 0.5 个百分点上限，6% 普涨不得误标涨停。

        名义 close = 0.1 元时无界容差 0.5 / close = 5 个百分点，阈值被降到 5，主板涨 6%
        即被误标涨停；封顶后 tol = min(0.6 / 0.1, 0.5) = 0.5，阈值 9.5 不触发。
        adj_factor 使前复权后仍能反推名义 close = 0.1 元（末行 ratio = 1 → 名义价 = 原始价）。
        """
        raw = [0.095, 0.095, 0.095, 0.095, 0.095, 0.1]
        df = self._df(raw, last_pct_chg=6.0)
        df["adj_factor"] = [0.95, 0.95, 0.95, 0.95, 0.95, 1.0]
        result = _build_history_text(df, ts_code="000001.SZ", stock_name="普通股份", limit_df=None)
        assert f"🔴{I18n.get('ai_limit_up')}" not in result
        assert result.count(I18n.get("ai_limit_price_approx")) == 1

    def test_mid_price_true_limit_up_still_tagged(self):
        """封顶未造成漏标：名义 close 3.33（pre_close 3.03 → 涨停价 3.33）涨 9.91% 仍标涨停。

        tol = min(0.6 / 3.33, 0.5) ≈ 0.18 个百分点 → 阈值 ≈ 9.82，9.91 ≥ 9.82 触发。
        """
        df = self._df([3.0, 3.1, 3.2, 3.3, 3.31, 3.33], last_pct_chg=9.91)
        result = _build_history_text(df, ts_code="000001.SZ", stock_name="普通股份", limit_df=None)
        assert f"🔴{I18n.get('ai_limit_up')}" in result
        assert result.count(I18n.get("ai_limit_price_approx")) == 1

    def test_invalid_nominal_close_uses_capped_tolerance(self):
        """F2：名义 close 无效（NaN）时容差回退为封顶值 0.5 个百分点（不得更松）。"""
        df_tag = self._df([10.4, 10.5, 10.6, 10.7, 10.8, 11.0], last_pct_chg=10.0)
        df_tag.loc[5, "close"] = float("nan")
        res_tag = _build_history_text(df_tag, ts_code="000001.SZ", stock_name="普通股份", limit_df=None)
        assert f"🔴{I18n.get('ai_limit_up')}" in res_tag

        # 9.4 < 10 - 0.5 → 未达阈值，不标涨停（回退值封顶于 0.5）
        df_miss = self._df([10.4, 10.5, 10.6, 10.7, 10.8, 11.0], last_pct_chg=9.4)
        df_miss.loc[5, "close"] = float("nan")
        res_miss = _build_history_text(df_miss, ts_code="000001.SZ", stock_name="普通股份", limit_df=None)
        assert f"🔴{I18n.get('ai_limit_up')}" not in res_miss

    def test_null_up_limit_row_treated_as_missing(self):
        """H2：该日有行但 up_limit 为 NULL → 按「无有效记录」处理（不打标签 + 缺失提示，R21）。

        修复前：entry 存在 → classify 返回 None → 既无标签也无缺失提示，等同把「数据缺失」
        伪装成「未涨停」。
        """
        df = self._df([10.4, 10.5, 10.6, 10.7, 10.8, 11.0], last_pct_chg=10.0)
        limit_df = self._limit_df([(d, 99.0, 0.01) for d in self._LAST3])
        last_day = datetime.date.fromisoformat(self._LAST3[-1])
        limit_df.loc[limit_df["trade_date"] == last_day, "up_limit"] = float("nan")
        result = _build_history_text(df, ts_code="000001.SZ", stock_name="普通股份", limit_df=limit_df)
        assert f"🔴{I18n.get('ai_limit_up')}" not in result
        assert result.count(I18n.get("ai_limit_price_missing")) == 1


if __name__ == "__main__":
    test_ai_macro()
