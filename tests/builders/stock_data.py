"""Test data builders for stock-related test data.

P3-2: 提供最小真实数据集构建器，消除多个测试文件中重复的
stock_basic/trade_cal/daily_quotes 数据构造。

设计原则（§1.3 极简设计）：
- 每个构建器提供合理默认值，调用方只覆盖差异参数
- 返回 dict 或 list[dict]，兼容 INSERT 和 DataFrame 构造
- 不预设未来可能的列组合，只解决当前已存在的重复
"""

import datetime
from collections.abc import Sequence


def make_stock_basic_row(
    ts_code: str = "000001.SZ",
    symbol: str | None = None,
    name: str = "平安银行",
    area: str = "深圳",
    industry: str = "银行",
    market: str = "主板",
    list_date: datetime.date | None = None,
    list_status: str = "L",
    delist_date: datetime.date | None = None,
) -> dict:
    """构造 stock_basic 单行数据。

    默认: 000001.SZ 平安银行, 上市日期 2020-01-01, 状态 L(上市)
    """
    if symbol is None:
        symbol = ts_code.split(".")[0]
    if list_date is None:
        list_date = datetime.date(2020, 1, 1)
    return {
        "ts_code": ts_code,
        "symbol": symbol,
        "name": name,
        "area": area,
        "industry": industry,
        "market": market,
        "list_date": list_date,
        "list_status": list_status,
        "delist_date": delist_date,
    }


def make_trade_cal_rows(
    dates: Sequence[datetime.date],
    is_open: bool = True,
    exchange: str = "SSE",
) -> list[dict]:
    """构造 trade_cal 多行数据。

    Args:
        dates: 交易日历日期序列
        is_open: 是否为交易日（默认 True）
        exchange: 交易所（默认 SSE）
    """
    is_open_val = 1 if is_open else 0
    rows = []
    prev_trade_date = None
    for d in dates:
        rows.append(
            {
                "cal_date": d,
                "exchange": exchange,
                "is_open": is_open_val,
                "pretrade_date": prev_trade_date,
            }
        )
        if is_open:
            prev_trade_date = d
    return rows


def make_daily_quote_row(
    ts_code: str = "000001.SZ",
    trade_date: datetime.date | None = None,
    open: float = 10.0,
    high: float = 10.5,
    low: float = 9.8,
    close: float = 10.2,
    pre_close: float = 10.0,
    change: float = 0.2,
    pct_chg: float = 2.0,
    vol: float = 1000000.0,
    amount: float = 10000000.0,
) -> dict:
    """构造 daily_quotes 单行数据。

    默认: 000001.SZ, 2024-01-15, 收盘价 10.2, 涨跌幅 2%
    """
    if trade_date is None:
        trade_date = datetime.date(2024, 1, 15)
    return {
        "ts_code": ts_code,
        "trade_date": trade_date,
        "open": open,
        "high": high,
        "low": low,
        "close": close,
        "pre_close": pre_close,
        "change": change,
        "pct_chg": pct_chg,
        "vol": vol,
        "amount": amount,
    }
