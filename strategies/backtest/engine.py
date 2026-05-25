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
from strategies.backtest.portfolio import PortfolioSimulator

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
        cancel_check: Callable[[], bool] | None = None,
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

        failed_signal_dates: list[dict] = []

        signals = await self._generate_signals(
            strategy,
            params,
            trade_dates,
            progress_callback,
            failed_signal_dates,
            cancel_check,
        )

        if progress_callback:
            progress_callback(0.5, "Simulating trades...")

        trades, positions, skipped_orders, sim_warnings = self._simulate_trades(
            signals,
            quotes_df,
            trade_dates,
        )

        if progress_callback:
            progress_callback(0.7, "Calculating portfolio returns...")

        nav_curve = BacktestMetrics.calc_nav_curve(
            positions,
            self.config.initial_capital,
            trade_dates,
        )
        daily_returns = BacktestMetrics.calc_daily_returns(nav_curve)

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
            data_warnings=tuple(sim_warnings),
            failed_signal_dates=tuple(failed_signal_dates),
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

        quotes_df = await self._enrich_suspend_status(quotes_df, start_str, end_str)
        quotes_df = await self._enrich_limit_status(quotes_df, start_str, end_str)

        quotes_df = self._apply_qfq(quotes_df)

        return quotes_df.sort(["ts_code", "trade_date"])

    async def _enrich_suspend_status(
        self,
        quotes_df: pl.DataFrame,
        start_date: str,
        end_date: str,
    ) -> pl.DataFrame:
        """
        为行情数据增加停牌状态 (is_tradable)。

        is_tradable 值：
        - True: 可交易（不在 suspend_d 表中）
        - False: 停牌（在 suspend_d 表中）

        设计说明：
        ========
        本方法与 ScreenerDao.get_screening_data() 中的 is_tradable 来源相同（suspend_d 表），
        但服务于不同的数据流：

        1. 策略筛选路径：
           - 使用 BacktestDataProvider._get_screening_data()
           - 调用 ScreenerDao.get_screening_data()
           - is_tradable 已在 SQL 中通过 LEFT JOIN suspend_d 获取

        2. 撮合执行路径（本方法）：
           - 使用 get_daily_quotes() 获取基础行情
           - get_daily_quotes() 不含 is_tradable 字段
           - 需要单独 enrich 以支持撮合层的停牌判断

        两条路径的数据来源一致（suspend_d 表），但查询时机和方式不同。
        这是为了避免在撮合层重复加载完整的 screening_data（包含大量不需要的字段）。
        """
        try:
            suspend_pd = await self.cache.get_suspend_d(
                start_date=start_date,
                end_date=end_date,
            )

            if suspend_pd is None or suspend_pd.empty:
                return quotes_df.with_columns(pl.lit(True).alias("is_tradable"))

            suspend_df = pl.from_pandas(suspend_pd)
            suspend_df = suspend_df.select(["ts_code", "trade_date"]).with_columns(pl.lit(False).alias("is_tradable"))

            quotes_df = quotes_df.join(suspend_df, on=["ts_code", "trade_date"], how="left")

            return quotes_df.with_columns(pl.col("is_tradable").fill_null(True))
        except Exception as e:
            logger.warning("[VectorBacktestEngine] Failed to enrich suspend_status: %s", e)
            return quotes_df.with_columns(pl.lit(True).alias("is_tradable"))

    async def _enrich_limit_status(
        self,
        quotes_df: pl.DataFrame,
        start_date: str,
        end_date: str,
    ) -> pl.DataFrame:
        """
        为行情数据增加涨跌停状态。

        limit_status 值：
        - 'U' (up_limit): 涨停
        - 'D' (down_limit): 跌停
        - None: 正常交易

        涨跌停数据来自 limit_list 表，用于撮合层判断是否可买卖。
        """
        try:
            limit_list_pd = await self.cache.get_limit_list(
                start_date=start_date,
                end_date=end_date,
            )

            if limit_list_pd is None or limit_list_pd.empty:
                return quotes_df.with_columns(pl.lit(None).alias("limit_status"))

            limit_df = pl.from_pandas(limit_list_pd)
            limit_df = limit_df.select(["ts_code", "trade_date", "limit"]).rename({"limit": "limit_status"})

            quotes_df = quotes_df.join(limit_df, on=["ts_code", "trade_date"], how="left")
            return quotes_df
        except Exception as e:
            logger.warning("[VectorBacktestEngine] Failed to enrich limit_status: %s", e)
            return quotes_df.with_columns(pl.lit(None).alias("limit_status"))

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
        failed_signal_dates: list[dict] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> pl.DataFrame:
        signals_list = []
        total_dates = len(trade_dates)

        for i, signal_date in enumerate(trade_dates[:-1]):
            if cancel_check and cancel_check():
                logger.warning("[VectorBacktestEngine] Cancelled during signal generation at %s", signal_date)
                break

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
                    signals_list.append(signal_df)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("[Backtest] Strategy failed on %s: %s", signal_date, e)
                if failed_signal_dates is not None:
                    failed_signal_dates.append({"date": signal_date, "error": str(e)})
                if self.config.fail_fast:
                    raise

            if progress_callback and i % 10 == 0:
                progress = 0.1 + 0.4 * (i / total_dates)
                progress_callback(progress, f"Processing {signal_date}...")

        if not signals_list:
            return pl.DataFrame()

        return pl.concat(signals_list)

    def _is_rebalance_day(
        self,
        exec_date: date,
        trade_dates: list[date],
        signals: pl.DataFrame,
        rebalance_freq: str,
    ) -> bool:
        if rebalance_freq == "daily":
            return True

        if rebalance_freq == "signal":
            if signals.is_empty():
                return False
            day_signals = signals.filter(pl.col("execution_date") == exec_date)
            return not day_signals.is_empty()

        try:
            idx = trade_dates.index(exec_date)
        except ValueError:
            return False

        if idx == 0:
            return True

        prev_date = trade_dates[idx - 1]

        if rebalance_freq == "weekly":
            return exec_date.isocalendar()[1] != prev_date.isocalendar()[1]

        if rebalance_freq == "monthly":
            return exec_date.month != prev_date.month

        return True

    def _simulate_trades(
        self,
        signals: pl.DataFrame,
        quotes_df: pl.DataFrame,
        trade_dates: list[date],
    ) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, list[str]]:
        if signals.is_empty():
            return (
                pl.DataFrame(),
                pl.DataFrame(),
                pl.DataFrame(),
                [],
            )

        simulator = PortfolioSimulator(self.config, self.cost_model)

        for exec_date in trade_dates:
            day_signals = signals.filter(pl.col("execution_date") == exec_date)
            day_quotes = quotes_df.filter(pl.col("trade_date") == exec_date)
            is_rebalance = self._is_rebalance_day(
                exec_date,
                trade_dates,
                signals,
                self.config.rebalance_freq,
            )
            simulator.process_day(exec_date, day_signals, day_quotes, is_rebalance)

        return simulator.get_results()

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

        quotes_by_date: dict[date, pl.DataFrame] = {}
        for d in trade_dates:
            subset = quotes_df.filter(pl.col("trade_date") == d)
            if not subset.is_empty():
                quotes_by_date[d] = subset

        ic_values = []

        for i, signal_date in enumerate(trade_dates[:-1]):
            execution_date = trade_dates[i + 1]

            day_signals = signals.filter(pl.col("signal_date") == signal_date)

            if day_signals.is_empty():
                ic_values.append(0.0)
                continue

            execution_quotes = quotes_by_date.get(execution_date)
            if execution_quotes is None:
                ic_values.append(0.0)
                continue

            next_rebalance_date = self._get_next_rebalance_date(execution_date, trade_dates, self.config.rebalance_freq)

            if next_rebalance_date is None:
                ic_values.append(0.0)
                continue

            next_rebalance_quotes = quotes_by_date.get(next_rebalance_date)
            if next_rebalance_quotes is None:
                ic_values.append(0.0)
                continue

            signal_quotes = day_signals.join(execution_quotes, on="ts_code", how="inner").join(
                next_rebalance_quotes, on="ts_code", how="inner", suffix="_exit"
            )

            if signal_quotes.is_empty() or len(signal_quotes) < 3:
                ic_values.append(0.0)
                continue

            entry_price_col = "qfq_close" if self.config.execution_price == "next_close" else "qfq_open"
            forward_return = signal_quotes.select(
                [
                    "ts_code",
                    "signal_rank",
                    ((pl.col("qfq_close_exit") / pl.col(entry_price_col) - 1) * 100).alias("fwd_ret"),
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
        try:
            current_idx = trade_dates.index(execution_date)
        except ValueError:
            return None

        if rebalance_freq in ("daily", "signal"):
            next_idx = current_idx + 1
            if next_idx >= len(trade_dates):
                return None
            return trade_dates[next_idx]

        for j in range(current_idx + 1, len(trade_dates)):
            candidate = trade_dates[j]
            prev_date = trade_dates[j - 1]
            if rebalance_freq == "weekly" and candidate.isocalendar()[1] != prev_date.isocalendar()[1]:
                return candidate
            if rebalance_freq == "monthly" and candidate.month != prev_date.month:
                return candidate

        return None

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

        bm_dict: dict[date, float] = {}
        for row in benchmark_df.iter_rows(named=True):
            trade_date_val = row["trade_date"]
            if isinstance(trade_date_val, str):
                trade_date_val = datetime.datetime.strptime(trade_date_val, "%Y%m%d").date()
            bm_dict[trade_date_val] = float(row["pct_chg"]) / 100

        returns = [bm_dict.get(d, 0.0) for d in trade_dates]

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
