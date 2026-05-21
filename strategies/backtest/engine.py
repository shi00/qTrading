"""回测引擎核心"""

from __future__ import annotations

import asyncio
import datetime
import logging
import time
import uuid
from collections.abc import Callable
from datetime import date
from typing import TYPE_CHECKING, Any

import polars as pl

from data.domain_services.transaction_cost import TransactionCostModel
from strategies.backtest.adapter import BacktestStrategyAdapter
from strategies.backtest.config import BacktestConfig, BacktestResult
from strategies.backtest.data_provider import BacktestDataProvider
from strategies.backtest.metrics import BacktestMetrics

if TYPE_CHECKING:
    from data.cache.cache_manager import CacheManager
    from strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class VectorBacktestEngine:
    """
    基于 Polars 的向量化回测引擎。

    核心设计原则：
    1. T 日信号 → T+1 开盘成交（未来函数防护）
    2. 使用复权价格计算收益
    3. 信号/撮合允许按交易日滚动，净值与指标尽量批量化计算
    """

    def __init__(
        self,
        cache: CacheManager,
        config: BacktestConfig,
        data_processor: Any = None,
    ):
        self.cache = cache
        self.config = config
        self.cost_model = TransactionCostModel(config.get_cost_config())
        self.data_provider = BacktestDataProvider(cache, data_processor)
        self.strategy_adapter = BacktestStrategyAdapter()

    async def run(
        self,
        strategy: BaseStrategy,
        params: dict | None = None,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> BacktestResult:
        start_time = time.perf_counter()
        run_id = uuid.uuid4().hex[:16]

        errors = self.config.validate()
        if errors:
            raise ValueError(f"Invalid backtest config: {errors}")

        if progress_callback:
            progress_callback(0.0, "Loading historical data...")

        trade_dates = await self._get_trade_dates()
        quotes_df = await self._load_quotes(trade_dates)
        benchmark_df = await self._load_benchmark(trade_dates)

        if progress_callback:
            progress_callback(0.1, "Running strategy on each period...")

        signals = await self._generate_signals(
            strategy,
            params,
            trade_dates,
            progress_callback,
        )

        if progress_callback:
            progress_callback(0.5, "Simulating trades...")

        trades, positions, skipped_orders = self._simulate_trades(
            signals,
            quotes_df,
            trade_dates,
        )

        if progress_callback:
            progress_callback(0.7, "Calculating portfolio returns...")

        nav_curve, daily_returns = self._calc_portfolio_nav(
            positions,
            quotes_df,
            benchmark_df,
            trade_dates,
        )

        if progress_callback:
            progress_callback(0.8, "Calculating metrics...")

        ic_series = self._calc_ic_series(signals, quotes_df, trade_dates)

        benchmark_returns = self._calc_benchmark_returns(benchmark_df, trade_dates)

        metrics = BacktestMetrics.calc_all_metrics(
            nav_curve,
            daily_returns,
            benchmark_returns,
            trades,
            ic_series,
            self.config.risk_free_rate,
        )

        period_stats = self._calc_period_stats(
            nav_curve,
            daily_returns,
            benchmark_returns,
            trade_dates,
        )

        duration_ms = int((time.perf_counter() - start_time) * 1000)

        return BacktestResult(
            config=self.config,
            strategy_name=strategy.name,
            params_snapshot=params or {},
            nav_curve=pl.DataFrame(
                {
                    "trade_date": trade_dates,
                    "nav": nav_curve,
                }
            ),
            daily_returns=daily_returns,
            benchmark_returns=benchmark_returns,
            trades=trades,
            positions=positions,
            skipped_orders=skipped_orders,
            metrics=metrics,
            ic_series=ic_series,
            period_stats=period_stats,
            run_id=run_id,
            executed_at=datetime.datetime.now(),
            duration_ms=duration_ms,
            data_warnings=[],
            failed_signal_dates=[],
        )

    async def _get_trade_dates(self) -> list[date]:
        cal_df = await self.cache.get_trade_cal(
            start_date=self.config.start_date.strftime("%Y%m%d"),
            end_date=self.config.end_date.strftime("%Y%m%d"),
            is_open="1",
        )
        if cal_df is None or cal_df.empty:
            raise ValueError("No trade dates found in the specified range")

        return sorted([datetime.datetime.strptime(str(d), "%Y%m%d").date() for d in cal_df["cal_date"].tolist()])

    async def _load_quotes(self, trade_dates: list[date]) -> pl.DataFrame:
        start_str = trade_dates[0].strftime("%Y%m%d")
        end_str = trade_dates[-1].strftime("%Y%m%d")

        quotes_pd = await self.cache.get_daily_quotes(
            start_date=start_str,
            end_date=end_str,
        )

        if quotes_pd is None or quotes_pd.empty:
            raise ValueError("No quotes data found")

        quotes_df = pl.from_pandas(quotes_pd)

        quotes_df = self._apply_qfq(quotes_df)

        return quotes_df.sort(["ts_code", "trade_date"])

    def _apply_qfq(self, quotes_df: pl.DataFrame) -> pl.DataFrame:
        """
        计算前复权价格，同时保留原始价格列。

        关键设计：
        1. 原始 open/high/low/close 重命名为 raw_open/raw_high/raw_low/raw_close
        2. 复权价格存储为 qfq_open/qfq_high/qfq_low/qfq_close
        3. 成交金额计算使用 raw_open/raw_close
        4. 收益计算和技术指标使用 qfq_close

        复权公式与 TechnicalAnalysis._get_qfq_df() 一致：
        adjusted_price = raw_price * adj_factor / latest_adj_factor
        """
        if "adj_factor" not in quotes_df.columns:
            return quotes_df.with_columns(
                [
                    pl.col("open").alias("raw_open"),
                    pl.col("high").alias("raw_high"),
                    pl.col("low").alias("raw_low"),
                    pl.col("close").alias("raw_close"),
                    pl.col("open").alias("qfq_open"),
                    pl.col("high").alias("qfq_high"),
                    pl.col("low").alias("qfq_low"),
                    pl.col("close").alias("qfq_close"),
                ]
            )

        latest_factors = (
            quotes_df.sort("trade_date").group_by("ts_code").agg(pl.col("adj_factor").last().alias("latest_adj_factor"))
        )

        quotes_df = quotes_df.join(latest_factors, on="ts_code", how="left")

        qfq_ratio = pl.col("adj_factor") / pl.col("latest_adj_factor")

        return quotes_df.with_columns(
            [
                pl.col("open").alias("raw_open"),
                pl.col("high").alias("raw_high"),
                pl.col("low").alias("raw_low"),
                pl.col("close").alias("raw_close"),
                (pl.col("open") * qfq_ratio).alias("qfq_open"),
                (pl.col("high") * qfq_ratio).alias("qfq_high"),
                (pl.col("low") * qfq_ratio).alias("qfq_low"),
                (pl.col("close") * qfq_ratio).alias("qfq_close"),
                qfq_ratio.alias("qfq_ratio"),
            ]
        )

    async def _load_benchmark(self, trade_dates: list[date]) -> pl.DataFrame:
        start_str = trade_dates[0].strftime("%Y%m%d")
        end_str = trade_dates[-1].strftime("%Y%m%d")

        benchmark_pd = await self.cache.get_index_daily_range(
            ts_code_list=[self.config.benchmark_code],
            start_date=start_str,
            end_date=end_str,
        )

        if benchmark_pd is None or benchmark_pd.empty:
            return pl.DataFrame()

        return pl.from_pandas(benchmark_pd)

    async def _generate_signals(
        self,
        strategy: BaseStrategy,
        params: dict | None,
        trade_dates: list[date],
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> pl.DataFrame:
        signals_list = []
        total_dates = len(trade_dates)

        for i, signal_date in enumerate(trade_dates[:-1]):
            try:
                context = await self.data_provider.build_context(
                    signal_date,
                    disable_ai=self.config.disable_ai,
                )
                context["params"] = params or {}

                execution_date = trade_dates[i + 1]
                signal_df = await self.strategy_adapter.generate_signal(
                    strategy=strategy,
                    context=context,
                    signal_date=signal_date,
                    execution_date=execution_date,
                )

                if signal_df is not None and not signal_df.is_empty():
                    signal_df = signal_df.with_columns(pl.col("rank").alias("signal_rank"))
                    signals_list.append(signal_df)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"[Backtest] Strategy failed on {signal_date}: {e}")
                if self.config.fail_fast:
                    raise

            if progress_callback and i % 10 == 0:
                progress = 0.1 + 0.4 * (i / total_dates)
                progress_callback(progress, f"Processing {signal_date}...")

        if not signals_list:
            return pl.DataFrame()

        return pl.concat(signals_list)

    def _simulate_trades(
        self,
        signals: pl.DataFrame,
        quotes_df: pl.DataFrame,
        trade_dates: list[date],
    ) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
        """
        模拟交易执行（涨跌停/停牌处理版本）。

        返回：
        - trades: 交易明细
        - positions: 持仓明细
        - skipped_orders: 跳过订单明细（涨跌停/停牌）
        """
        if signals.is_empty():
            return (
                pl.DataFrame(),
                pl.DataFrame(),
                pl.DataFrame(),
            )

        positions_list = []
        trades_list = []
        skipped_list = []
        current_positions: dict[str, dict] = {}
        cash = self.config.initial_capital

        for exec_date in trade_dates:
            day_signals = signals.filter(pl.col("execution_date") == exec_date)

            day_quotes = quotes_df.filter(pl.col("trade_date") == exec_date)

            if day_signals.is_empty() and not current_positions:
                positions_list.append(
                    {
                        "trade_date": exec_date,
                        "cash": cash,
                        "positions": {},
                        "total_value": cash,
                    }
                )
                continue

            for ts_code, pos in list(current_positions.items()):
                quote = day_quotes.filter(pl.col("ts_code") == ts_code)

                if quote.is_empty():
                    skipped_list.append(
                        {
                            "trade_date": exec_date,
                            "ts_code": ts_code,
                            "direction": "sell",
                            "reason": "no_quote",
                            "intended_volume": pos["volume"],
                        }
                    )
                    continue

                is_tradable = quote.select("is_tradable").item() if "is_tradable" in quote.columns else True
                if is_tradable is False:
                    skipped_list.append(
                        {
                            "trade_date": exec_date,
                            "ts_code": ts_code,
                            "direction": "sell",
                            "reason": "suspended",
                            "intended_volume": pos["volume"],
                        }
                    )
                    continue

                limit_status = quote.select("limit_status").item() if "limit_status" in quote.columns else None
                if limit_status == "down_limit":
                    skipped_list.append(
                        {
                            "trade_date": exec_date,
                            "ts_code": ts_code,
                            "direction": "sell",
                            "reason": "down_limit",
                            "intended_volume": pos["volume"],
                        }
                    )
                    continue

                exit_price = float(quote.select("raw_open").item())
                volume = pos["volume"]

                cost = self.cost_model.calculate(
                    price=exit_price,
                    volume=volume,
                    is_buy=False,
                )

                realized_pnl = cost.net_amount - pos["cost_basis"]

                trades_list.append(
                    {
                        "trade_date": exec_date,
                        "ts_code": ts_code,
                        "action": "sell",
                        "price": exit_price,
                        "volume": volume,
                        "gross_amount": cost.gross_amount,
                        "total_cost": cost.total_cost,
                        "net_amount": cost.net_amount,
                        "realized_pnl": realized_pnl,
                        "hold_days": (exec_date - pos["entry_date"]).days,
                    }
                )

                cash += cost.net_amount
                del current_positions[ts_code]

            if not day_signals.is_empty():
                signals_sorted = day_signals.sort("signal_rank", descending=True)
                signals_sorted = signals_sorted.head(self.config.max_position_count)

                available_cash = cash * (1 - 0.1)
                position_value = available_cash / min(
                    len(signals_sorted),
                    self.config.max_position_count,
                )

                for row in signals_sorted.iter_rows(named=True):
                    ts_code = row["ts_code"]
                    quote = day_quotes.filter(pl.col("ts_code") == ts_code)

                    if quote.is_empty():
                        skipped_list.append(
                            {
                                "trade_date": exec_date,
                                "ts_code": ts_code,
                                "direction": "buy",
                                "reason": "no_quote",
                                "intended_volume": 0,
                            }
                        )
                        continue

                    is_tradable = quote.select("is_tradable").item() if "is_tradable" in quote.columns else True
                    if is_tradable is False:
                        skipped_list.append(
                            {
                                "trade_date": exec_date,
                                "ts_code": ts_code,
                                "direction": "buy",
                                "reason": "suspended",
                                "intended_volume": 0,
                            }
                        )
                        continue

                    limit_status = quote.select("limit_status").item() if "limit_status" in quote.columns else None
                    if limit_status == "up_limit":
                        skipped_list.append(
                            {
                                "trade_date": exec_date,
                                "ts_code": ts_code,
                                "direction": "buy",
                                "reason": "up_limit",
                                "intended_volume": 0,
                            }
                        )
                        continue

                    entry_price = float(quote.select("raw_open").item())

                    volume = int(position_value / entry_price / 100) * 100

                    if volume <= 0:
                        skipped_list.append(
                            {
                                "trade_date": exec_date,
                                "ts_code": ts_code,
                                "direction": "buy",
                                "reason": "insufficient_cash",
                                "intended_volume": 0,
                            }
                        )
                        continue

                    cost = self.cost_model.calculate(
                        price=entry_price,
                        volume=volume,
                        is_buy=True,
                    )

                    if cost.net_amount > cash:
                        skipped_list.append(
                            {
                                "trade_date": exec_date,
                                "ts_code": ts_code,
                                "direction": "buy",
                                "reason": "insufficient_cash",
                                "intended_volume": volume,
                            }
                        )
                        continue

                    trades_list.append(
                        {
                            "trade_date": exec_date,
                            "ts_code": ts_code,
                            "action": "buy",
                            "price": entry_price,
                            "volume": volume,
                            "gross_amount": cost.gross_amount,
                            "total_cost": cost.total_cost,
                            "net_amount": cost.net_amount,
                            "realized_pnl": 0.0,
                            "hold_days": 0,
                        }
                    )

                    current_positions[ts_code] = {
                        "volume": volume,
                        "cost_basis": cost.net_amount,
                        "entry_date": exec_date,
                        "entry_price": entry_price,
                    }

                    cash -= cost.net_amount

            total_value = cash
            positions_detail = {}
            for ts_code, pos in current_positions.items():
                quote = day_quotes.filter(pl.col("ts_code") == ts_code)
                if not quote.is_empty():
                    market_price = float(quote.select("qfq_close").item())
                    market_value = market_price * pos["volume"]
                    total_value += market_value
                    positions_detail[ts_code] = {
                        "volume": pos["volume"],
                        "market_value": market_value,
                        "pnl": market_value - pos["cost_basis"],
                    }

            positions_list.append(
                {
                    "trade_date": exec_date,
                    "cash": cash,
                    "positions": positions_detail,
                    "total_value": total_value,
                }
            )

        return (
            pl.DataFrame(trades_list) if trades_list else pl.DataFrame(),
            pl.DataFrame(positions_list),
            pl.DataFrame(skipped_list) if skipped_list else pl.DataFrame(),
        )

    def _calc_portfolio_nav(
        self,
        positions: pl.DataFrame,
        quotes_df: pl.DataFrame,
        benchmark_df: pl.DataFrame,
        trade_dates: list[date],
    ) -> tuple[pl.Series, pl.Series]:
        if positions.is_empty():
            nav = pl.Series([self.config.initial_capital] * len(trade_dates))
            returns = pl.Series([0.0] * len(trade_dates))
            return nav, returns

        nav = positions["total_value"]
        returns = nav.pct_change()
        returns = returns.fill_null(0.0)

        return nav, returns

    def _calc_ic_series(
        self,
        signals: pl.DataFrame,
        quotes_df: pl.DataFrame,
        trade_dates: list[date],
    ) -> pl.Series:
        """
        计算 IC 序列（持有期对齐版本）。

        关键设计：
        1. 根据 rebalance_freq 确定持有期
        2. forward_return = 执行价到下一次调仓执行价的收益
        3. 使用复权价格计算收益（qfq_close）
        """
        if signals.is_empty():
            return pl.Series([], dtype=pl.Float64)

        ic_values = []

        for i, signal_date in enumerate(trade_dates[:-1]):
            execution_date = trade_dates[i + 1]

            day_signals = signals.filter(pl.col("signal_date") == signal_date)

            if day_signals.is_empty():
                ic_values.append(0.0)
                continue

            execution_quotes = quotes_df.filter(pl.col("trade_date") == execution_date)

            next_rebalance_date = self._get_next_rebalance_date(execution_date, trade_dates, self.config.rebalance_freq)

            if next_rebalance_date is None:
                ic_values.append(0.0)
                continue

            next_rebalance_quotes = quotes_df.filter(pl.col("trade_date") == next_rebalance_date)

            signal_quotes = day_signals.join(execution_quotes, on="ts_code", how="inner").join(
                next_rebalance_quotes, on="ts_code", how="inner", suffix="_exit"
            )

            if signal_quotes.is_empty() or len(signal_quotes) < 3:
                ic_values.append(0.0)
                continue

            forward_return = signal_quotes.select(
                [
                    "ts_code",
                    "signal_rank",
                    ((pl.col("qfq_close_exit") / pl.col("qfq_open") - 1) * 100).alias("fwd_ret"),
                ]
            )

            ic = BacktestMetrics.calc_ic(
                forward_return["signal_rank"],
                forward_return["fwd_ret"],
            )
            ic_values.append(ic)

        return pl.Series(ic_values)

    def _get_next_rebalance_date(
        self,
        execution_date: date,
        trade_dates: list[date],
        rebalance_freq: str,
    ) -> date | None:
        """
        根据调仓频率确定下一个调仓日。

        Args:
            execution_date: 当前执行日
            trade_dates: 交易日列表
            rebalance_freq: 调仓频率 ("daily", "weekly", "monthly", "signal")

        Returns:
            下一个调仓日，如果超出范围则返回 None
        """
        try:
            current_idx = trade_dates.index(execution_date)
        except ValueError:
            return None

        if rebalance_freq == "daily":
            next_idx = current_idx + 1
        elif rebalance_freq == "weekly":
            next_idx = min(current_idx + 5, len(trade_dates) - 1)
        elif rebalance_freq == "monthly":
            next_idx = min(current_idx + 22, len(trade_dates) - 1)
        else:
            next_idx = current_idx + 1

        if next_idx >= len(trade_dates):
            return None

        return trade_dates[next_idx]

    def _calc_benchmark_returns(
        self,
        benchmark_df: pl.DataFrame,
        trade_dates: list[date],
    ) -> pl.Series:
        """
        计算 Benchmark 日收益序列。

        注意：IndexDaily.pct_chg 字段单位是"百分比"（如 1.5 表示 1.5%），
        需要除以 100 转换为小数形式（如 0.015）。

        这与 DailyQuotes.pct_chg 字段一致，都是百分比单位。
        """
        if benchmark_df.is_empty():
            return pl.Series([0.0] * len(trade_dates))

        returns = []
        for _i, d in enumerate(trade_dates):
            day_bm = benchmark_df.filter(pl.col("trade_date") == d)
            if day_bm.is_empty():
                returns.append(0.0)
            else:
                pct_chg = float(day_bm.select("pct_chg").item())
                returns.append(pct_chg / 100)

        return pl.Series(returns)

    def _calc_period_stats(
        self,
        nav_curve: pl.Series,
        daily_returns: pl.Series,
        benchmark_returns: pl.Series,
        trade_dates: list[date],
    ) -> pl.DataFrame:
        year_months = [f"{d.year}-{d.month:02d}" for d in trade_dates]

        df = pl.DataFrame(
            {
                "trade_date": trade_dates,
                "year_month": year_months,
                "nav": nav_curve,
                "daily_return": daily_returns,
                "benchmark_return": benchmark_returns,
            }
        )

        monthly = (
            df.group_by("year_month")
            .agg(
                [
                    pl.col("daily_return").sum().alias("monthly_return"),
                    pl.col("benchmark_return").sum().alias("benchmark_return"),
                    (pl.col("daily_return") - pl.col("benchmark_return")).sum().alias("excess_return"),
                    pl.col("nav").last().alias("end_nav"),
                ]
            )
            .sort("year_month")
        )

        return monthly
