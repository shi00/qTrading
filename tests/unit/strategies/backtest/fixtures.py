"""回测测试夹具

提供可复用的测试数据工厂，用于构造：
- 交易日历
- 行情数据（含除权）
- 停牌/涨跌停状态
- 固定信号
"""

from datetime import date, timedelta

import polars as pl

from strategies.backtest.config import BacktestConfig


def make_trade_dates(
    start_date: date,
    num_days: int,
    skip_weekends: bool = True,
) -> list[date]:
    """生成连续交易日列表。

    Args:
        start_date: 起始日期
        num_days: 交易日数量
        skip_weekends: 是否跳过周末

    Returns:
        交易日列表
    """
    dates = []
    current = start_date
    while len(dates) < num_days:
        if skip_weekends and current.weekday() >= 5:
            current += timedelta(days=1)
            continue
        dates.append(current)
        current += timedelta(days=1)
    return dates


def make_quotes_df(
    ts_codes: list[str],
    trade_dates: list[date],
    base_prices: dict[str, float] | None = None,
    daily_returns: dict[str, float] | None = None,
    adj_factors: dict[str, list[float]] | None = None,
    limit_status: dict[str, dict[date, str]] | None = None,
    is_tradable: dict[str, dict[date, bool]] | None = None,
) -> pl.DataFrame:
    """构造行情数据 DataFrame。

    Args:
        ts_codes: 股票代码列表
        trade_dates: 交易日列表
        base_prices: 各股票基准价格 {ts_code: base_price}
        daily_returns: 各股票日收益率 {ts_code: daily_return}
        adj_factors: 各股票复权因子序列 {ts_code: [factor1, factor2, ...]}
        limit_status: 涨跌停状态 {ts_code: {date: "up_limit" | "down_limit"}}
        is_tradable: 是否可交易 {ts_code: {date: bool}}

    Returns:
        包含 raw_open/raw_close/qfq_open/qfq_close 等列的 DataFrame
    """
    base_prices = base_prices or {code: 10.0 + i for i, code in enumerate(ts_codes)}
    daily_returns = daily_returns or {code: 0.01 for code in ts_codes}

    rows = []
    for ts_code in ts_codes:
        base_price = base_prices.get(ts_code, 10.0)
        daily_ret = daily_returns.get(ts_code, 0.01)

        for i, trade_date in enumerate(trade_dates):
            close_price = base_price * ((1 + daily_ret) ** i)
            open_price = close_price * 0.99

            limit = None
            if limit_status and ts_code in limit_status:
                limit = limit_status[ts_code].get(trade_date)

            tradable = True
            if is_tradable and ts_code in is_tradable:
                tradable = is_tradable[ts_code].get(trade_date, True)

            adj_factor = 1.0
            if adj_factors and ts_code in adj_factors:
                adj_factor = adj_factors[ts_code][i] if i < len(adj_factors[ts_code]) else 1.0

            rows.append(
                {
                    "ts_code": ts_code,
                    "trade_date": trade_date,
                    "open": open_price,
                    "high": close_price * 1.02,
                    "low": open_price * 0.98,
                    "close": close_price,
                    "vol": 1000000,
                    "amount": close_price * 1000000,
                    "adj_factor": adj_factor,
                    "limit_status": limit,
                    "is_tradable": tradable,
                }
            )

    df = pl.DataFrame(rows)

    if "adj_factor" in df.columns:
        latest_factors = (
            df.sort("trade_date").group_by("ts_code").agg(pl.col("adj_factor").last().alias("latest_adj_factor"))
        )
        df = df.join(latest_factors, on="ts_code", how="left")
        qfq_ratio = pl.col("adj_factor") / pl.col("latest_adj_factor")
        df = df.with_columns(
            [
                pl.col("open").alias("raw_open"),
                pl.col("high").alias("raw_high"),
                pl.col("low").alias("raw_low"),
                pl.col("close").alias("raw_close"),
                (pl.col("open") * qfq_ratio).alias("qfq_open"),
                (pl.col("high") * qfq_ratio).alias("qfq_high"),
                (pl.col("low") * qfq_ratio).alias("qfq_low"),
                (pl.col("close") * qfq_ratio).alias("qfq_close"),
            ]
        )

    return df


def make_signals_df(
    signals: list[dict],
) -> pl.DataFrame:
    """构造信号 DataFrame。

    Args:
        signals: 信号列表，每个元素包含 signal_date, execution_date, ts_code, rank

    Returns:
        信号 DataFrame
    """
    return pl.DataFrame(signals)


def make_benchmark_df(
    trade_dates: list[date],
    daily_returns: list[float] | None = None,
    ts_code: str = "000300.SH",
) -> pl.DataFrame:
    """构造基准指数数据。

    Args:
        trade_dates: 交易日列表
        daily_returns: 日收益率列表
        ts_code: 指数代码

    Returns:
        基准指数 DataFrame
    """
    if daily_returns is None:
        daily_returns = [0.001] * len(trade_dates)

    rows = []
    for i, trade_date in enumerate(trade_dates):
        pct_chg = daily_returns[i] * 100 if i < len(daily_returns) else 0.1
        rows.append(
            {
                "ts_code": ts_code,
                "trade_date": trade_date,
                "pct_chg": pct_chg,
                "close": 3000.0 * ((1 + daily_returns[0]) ** i) if daily_returns else 3000.0,
            }
        )

    return pl.DataFrame(rows)


def make_backtest_config(
    start_date: date | None = None,
    end_date: date | None = None,
    **kwargs,
) -> BacktestConfig:
    """构造回测配置。

    Args:
        start_date: 开始日期
        end_date: 结束日期
        **kwargs: 其他配置参数

    Returns:
        BacktestConfig 实例
    """
    if start_date is None:
        start_date = date(2024, 1, 1)
    if end_date is None:
        end_date = date(2024, 1, 31)

    return BacktestConfig(
        start_date=start_date,
        end_date=end_date,
        **kwargs,
    )


class BacktestTestFixture:
    """回测测试夹具类，提供完整的测试场景数据。"""

    def __init__(
        self,
        start_date: date = date(2024, 1, 1),
        num_trade_days: int = 10,
        num_stocks: int = 3,
    ):
        self.start_date = start_date
        self.num_trade_days = num_trade_days
        self.num_stocks = num_stocks

        self.trade_dates = make_trade_dates(start_date, num_trade_days)
        self.ts_codes = [f"00000{i}.SZ" for i in range(1, num_stocks + 1)]

    def get_basic_quotes(self) -> pl.DataFrame:
        """获取基础行情数据。"""
        return make_quotes_df(
            ts_codes=self.ts_codes,
            trade_dates=self.trade_dates,
        )

    def get_quotes_with_ex_dividend(
        self,
        ex_div_date: date | None = None,
        adj_ratio: float = 0.5,
    ) -> pl.DataFrame:
        """获取含除权的行情数据。

        Args:
            ex_div_date: 除权日
            adj_ratio: 复权比例（如 0.5 表示 10 送 10）

        Returns:
            含复权因子的行情数据
        """
        if ex_div_date is None:
            ex_div_date = self.trade_dates[len(self.trade_dates) // 2]

        adj_factors = {}
        for ts_code in self.ts_codes:
            factors = []
            for d in self.trade_dates:
                if d < ex_div_date:
                    factors.append(adj_ratio)
                else:
                    factors.append(1.0)
            adj_factors[ts_code] = factors

        return make_quotes_df(
            ts_codes=self.ts_codes,
            trade_dates=self.trade_dates,
            adj_factors=adj_factors,
        )

    def get_quotes_with_suspension(
        self,
        suspended_codes: list[str] | None = None,
        suspended_dates: list[date] | None = None,
    ) -> pl.DataFrame:
        """获取含停牌的行情数据。

        Args:
            suspended_codes: 停牌股票代码
            suspended_dates: 停牌日期

        Returns:
            含停牌状态的行情数据
        """
        if suspended_codes is None:
            suspended_codes = [self.ts_codes[0]]
        if suspended_dates is None:
            suspended_dates = [self.trade_dates[0]]

        is_tradable = {}
        for ts_code in self.ts_codes:
            is_tradable[ts_code] = {
                d: d not in suspended_dates if ts_code in suspended_codes else True for d in self.trade_dates
            }

        return make_quotes_df(
            ts_codes=self.ts_codes,
            trade_dates=self.trade_dates,
            is_tradable=is_tradable,
        )

    def get_quotes_with_limit(
        self,
        up_limit_codes: list[str] | None = None,
        down_limit_codes: list[str] | None = None,
        limit_date: date | None = None,
    ) -> pl.DataFrame:
        """获取含涨跌停的行情数据。

        Args:
            up_limit_codes: 涨停股票代码
            down_limit_codes: 跌停股票代码
            limit_date: 涨跌停日期

        Returns:
            含涨跌停状态的行情数据
        """
        if limit_date is None:
            limit_date = self.trade_dates[0]
        if up_limit_codes is None:
            up_limit_codes = []
        if down_limit_codes is None:
            down_limit_codes = []

        limit_status = {}
        for ts_code in self.ts_codes:
            limit_status[ts_code] = {}
            if ts_code in up_limit_codes:
                limit_status[ts_code][limit_date] = "up_limit"
            if ts_code in down_limit_codes:
                limit_status[ts_code][limit_date] = "down_limit"

        return make_quotes_df(
            ts_codes=self.ts_codes,
            trade_dates=self.trade_dates,
            limit_status=limit_status,
        )

    def get_fixed_signals(
        self,
        signal_dates: list[date] | None = None,
        codes: list[str] | None = None,
    ) -> pl.DataFrame:
        """获取固定信号数据。

        Args:
            signal_dates: 信号日期列表
            codes: 股票代码列表

        Returns:
            信号 DataFrame
        """
        if signal_dates is None:
            signal_dates = self.trade_dates[:-1]
        if codes is None:
            codes = self.ts_codes

        signals = []
        for _i, signal_date in enumerate(signal_dates):
            exec_date = self.trade_dates[self.trade_dates.index(signal_date) + 1]
            for j, ts_code in enumerate(codes):
                signals.append(
                    {
                        "signal_date": signal_date,
                        "execution_date": exec_date,
                        "ts_code": ts_code,
                        "signal_rank": len(codes) - j,
                    }
                )

        return make_signals_df(signals)

    def get_benchmark(self, daily_returns: list[float] | None = None) -> pl.DataFrame:
        """获取基准指数数据。"""
        return make_benchmark_df(self.trade_dates, daily_returns)
