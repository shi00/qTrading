"""回测引擎核心"""

from __future__ import annotations

import asyncio
import datetime
import logging
import time
import uuid
from collections.abc import Callable
from datetime import date
from typing import TYPE_CHECKING

import polars as pl

from utils.log_decorators import PerfThreshold, log_async_operation
from utils.qfq import qfq_ratio_expr
from utils.sanitizers import DataSanitizer
from core.i18n import Message
from data.domain_services.trade_calendar_service import TradeCalendarService
from data.domain_services.transaction_cost import TransactionCostModel
from strategies.backtest.adapter import BacktestStrategyAdapter
from strategies.backtest.config import BacktestConfig, BacktestResult, DataWarning
from strategies.backtest.data_provider import BacktestDataProvider
from strategies.backtest.metrics import BacktestMetrics
from strategies.backtest.portfolio import PortfolioSimulator

if TYPE_CHECKING:
    from data.cache.cache_manager import CacheManager
    from data.data_processor import DataProcessor
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
        data_processor: DataProcessor | None = None,
    ):
        self.cache = cache
        self.config = config
        self.cost_model = TransactionCostModel(config.get_cost_config())
        self.data_provider = BacktestDataProvider(cache, data_processor, preload_max_days=config.preload_max_days)
        self.strategy_adapter = BacktestStrategyAdapter()
        # D4-8：优先复用策略层同一 TradeCalendarService（DB → API → 离线三级降级），
        # 消除回测层直接查 DB 的第二套日历通路；无 data_processor 时留空，_get_trade_dates 懒构造。
        self.trade_calendar = getattr(data_processor, "trade_calendar", None)

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def run(
        self,
        strategy: BaseStrategy,
        params: dict | None = None,
        progress_callback: Callable[[float, Message], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> BacktestResult:
        start_time = time.perf_counter()
        run_id = uuid.uuid4().hex[:16]

        errors = self.config.validate()
        if errors:
            raise ValueError(f"Invalid backtest config: {errors}")

        if progress_callback:
            progress_callback(0.0, Message("backtest_progress_loading"))

        trade_dates = await self._get_trade_dates()

        if progress_callback:
            progress_callback(0.1, Message("backtest_progress_running_strategy"))

        failed_signal_dates: list[dict] = []

        # D4-9: 先生成信号以收集标的集合，再用 ts_code_list 裁剪行情加载，
        # 避免把区间内全市场标的的行情一次载入内存（按信号标的裁剪）。
        benchmark_df = await self._load_benchmark(trade_dates)

        signals = await self._generate_signals(
            strategy,
            params,
            trade_dates,
            progress_callback,
            failed_signal_dates,
            cancel_check,
        )

        # D4-9: 信号非空时仅加载信号涉及标的；空信号回退全市场（等价旧行为，兼容无信号路径）。
        target_codes = signals["ts_code"].unique().to_list() if not signals.is_empty() else None
        quotes_df, quote_warnings = await self._load_quotes(trade_dates, target_codes)

        if progress_callback:
            progress_callback(0.5, Message("backtest_progress_simulating"))

        # BT-002: 加载 stock_meta（含 delist_date）用于区分退市与临时停牌
        stock_meta = await self.data_provider.get_stock_meta()

        delist_stats: dict[str, float | int] = {
            "delist_liquidation_count": 0,
            "delist_loss_amount": 0.0,
        }
        trades, positions, skipped_orders, sim_warnings = self._simulate_trades(
            signals,
            quotes_df,
            trade_dates,
            stock_meta=stock_meta,
            delist_stats=delist_stats,
        )

        if progress_callback:
            progress_callback(0.7, Message("backtest_progress_calc_returns"))

        nav_curve = BacktestMetrics.calc_nav_curve(
            positions,
            self.config.initial_capital,
            trade_dates,
        )
        daily_returns = BacktestMetrics.calc_daily_returns(nav_curve)

        if progress_callback:
            progress_callback(0.8, Message("backtest_progress_calc_metrics"))

        ic_series, ic_dates = self._calc_ic_series(signals, quotes_df, trade_dates)

        benchmark_returns, benchmark_warning = self._calc_benchmark_returns(benchmark_df, trade_dates)

        metrics = BacktestMetrics.calc_all_metrics(
            nav_curve,
            daily_returns,
            benchmark_returns,
            trades,
            ic_series,
            self.config.risk_free_rate,
        )

        # BT-03: 仓位可见性指标并入 metrics，让「信号稀疏 → 资金闲置」可见
        metrics = {**metrics, **BacktestMetrics.calc_investment_metrics(positions)}

        period_stats = self._calc_period_stats(
            nav_curve,
            daily_returns,
            benchmark_returns,
            trade_dates,
        )

        duration_ms = int((time.perf_counter() - start_time) * 1000)

        all_warnings = [str(w) for w in quote_warnings] + list(sim_warnings)
        # D1-M1: 基准缺失/部分缺失告警接入 all_warnings，自动进入
        # backtest_view_model 的 `unreliable` 判定，让相对指标降级在 UI 可见。
        if benchmark_warning is not None:
            all_warnings.append(str(benchmark_warning))
        # D3-M4: 区间预载降级（区间超限/范围预载失败/护栏超限→逐日慢路径）接入 all_warnings，
        # 与其它 data_warnings 同通道进入 unreliable 判定，让「本次回测走了慢路径」首屏可见。
        all_warnings.extend(self.data_provider.range_preload_warnings)

        # D5-M2: 净值归零（爆仓）检测——不是数值噪声，必须让爆仓在 UI 可见。
        # daily_returns 已把爆仓日转为 null（无定义）供 drop_nulls 剔除，此处显式追加
        # DataWarning 进入 unreliable 判定，避免波动率低估/夏普被高估被静默掩盖。
        if bool((nav_curve == 0).any()):
            all_warnings.append(
                str(
                    DataWarning(
                        warning_type="portfolio_wiped_out",
                        start_date=str(self.config.start_date),
                        end_date=str(self.config.end_date),
                        affected_stock_count=1,
                        error_message="组合净值归零（爆仓）：爆仓日收益无定义被剔除，"
                        "volatility/sharpe 已相应修正，请以 total_return/max_drawdown 的 -100% 为准。",
                    )
                )
            )

        # BT-01: 汇总信号层是否携带独立打分。任一信号日有真实打分即视为 True；
        # 全为排序偏好（无打分列）时为 False，IC 语义退化为「排序 IC」。
        has_real_score = (
            bool(signals["has_real_score"].any())
            if not signals.is_empty() and "has_real_score" in signals.columns
            else False
        )

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
            ic_dates=pl.Series(ic_dates, dtype=pl.Date),
            period_stats=period_stats,
            run_id=run_id,
            executed_at=datetime.datetime.now(),
            duration_ms=duration_ms,
            data_warnings=tuple(all_warnings),
            failed_signal_dates=tuple(failed_signal_dates),
            delist_liquidation_count=delist_stats["delist_liquidation_count"],
            delist_loss_amount=delist_stats["delist_loss_amount"],
            has_real_score=has_real_score,
        )

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def _get_trade_dates(self) -> list[date]:
        # D4-8：统一走 TradeCalendarService 的三级降级链（DB → API → 离线），
        # 消除回测层直接查 stock_dao 的第二套日历通路。DB 缺失/不完整时由
        # service 负责 API 补齐/离线兜底与降级告警，而非回测层得到残缺序列。
        trade_calendar = self.trade_calendar
        if trade_calendar is None:
            # 无 data_processor（测试/独立回测）时以缓存构造：仅 DB + 离线兜底，
            # 不引入 live API（回测确定性诉求）；生产恒走 data_processor.trade_calendar 全链。
            trade_calendar = TradeCalendarService(self.cache, None)
            self.trade_calendar = trade_calendar

        trade_dates = await trade_calendar.get_trade_dates(
            self.config.start_date,
            self.config.end_date,
        )
        if not trade_dates:
            raise ValueError("No trade dates found in the specified range")
        return trade_dates

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def _load_quotes(
        self,
        trade_dates: list[date],
        ts_codes: list[str] | None = None,
    ) -> tuple[pl.DataFrame, list[DataWarning]]:
        start_str = trade_dates[0].strftime("%Y%m%d")
        end_str = trade_dates[-1].strftime("%Y%m%d")

        # D4-9: 传 ts_code_list 裁剪为信号涉及标的，避免全市场行情一次入内存；
        # None/空时由 DAO 判定为不带 ts_code_list 条件（等价全市场旧行为）。
        quotes_pd = await self.cache.quote_dao.get_daily_quotes(
            start_date=start_str,
            end_date=end_str,
            ts_code_list=ts_codes,
        )

        if quotes_pd is None or quotes_pd.empty:
            raise ValueError("No quotes data found")

        quotes_df = pl.from_pandas(quotes_pd)

        data_warnings: list[DataWarning] = []
        quotes_df, suspend_warning = await self._enrich_suspend_status(quotes_df, start_str, end_str)
        if suspend_warning:
            data_warnings.append(suspend_warning)

        quotes_df, limit_warning = await self._enrich_limit_status(quotes_df, start_str, end_str)
        if limit_warning:
            data_warnings.append(limit_warning)

        # D1-m1: 先按 ts_code/trade_date 排序再计算 avg_daily_volume，保证
        # _compute_avg_daily_volume 的 rolling_mean(over ts_code) 与 qfq 计算的行序确定，
        # 消除对上游输入行序的隐式依赖；_apply_qfq 内部另有排序兜底，二者叠加亦幂等。
        quotes_df = quotes_df.sort(["ts_code", "trade_date"])

        quotes_df = self._compute_avg_daily_volume(quotes_df)

        quotes_df = self._apply_qfq(quotes_df)

        return quotes_df, data_warnings

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def _enrich_suspend_status(
        self,
        quotes_df: pl.DataFrame,
        start_date: str,
        end_date: str,
    ) -> tuple[pl.DataFrame, DataWarning | None]:
        """
        为行情数据增加停牌状态 (is_tradable)。

        is_tradable 值：
        - True: 可交易（不在 suspend_d 表中）
        - False: 停牌（在 suspend_d 表中）

        失败时乐观降级策略：
        - 所有股票标记为可交易 (is_tradable=True)
        - 停牌股票可能在撮合时因无成交价被自然跳过
        - 返回 DataWarning 记录失败详情

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
            suspend_pd = await self.cache.quote_dao.get_suspend_d(
                start_date=start_date,
                end_date=end_date,
            )

            if suspend_pd is None or suspend_pd.empty:
                # DATA-03：区分「查询失败（enrich 异常已告警）」与「查询成功但区间内无停牌数据」。
                # 后者多为用户未同步 suspend_d 表，静默乐观降级会让回测在停牌股上成交，结果被美化，
                # 故必须显式告警（逐区间一次）。
                warning = DataWarning(
                    warning_type="suspend_data_absent",
                    start_date=start_date,
                    end_date=end_date,
                    affected_stock_count=quotes_df.height,
                    error_message=(
                        "suspend_d 表在该区间无数据，停牌保护未生效，回测默认全市场可交易。"
                        "若从未同步停牌数据，回测可能在停牌股票上成交，实盘不可执行。"
                    ),
                )
                return quotes_df.with_columns(pl.lit(True).alias("is_tradable")), warning

            suspend_df = pl.from_pandas(suspend_pd)
            suspend_df = suspend_df.select(["ts_code", "trade_date"]).with_columns(pl.lit(False).alias("is_tradable"))

            quotes_df = quotes_df.join(suspend_df, on=["ts_code", "trade_date"], how="left")

            return quotes_df.with_columns(pl.col("is_tradable").fill_null(True)), None
        # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 该 try 块抛出停牌状态补充异常. upgrade: 策略层重构时统一走 classify_error.
        except Exception as e:
            sanitized_msg = DataSanitizer.sanitize_error(e)
            logger.warning("[VectorBacktestEngine] Failed to enrich suspend_status: %s", sanitized_msg)
            warning = DataWarning(
                warning_type="suspend_enrich_failed",
                start_date=start_date,
                end_date=end_date,
                affected_stock_count=quotes_df.height,
                error_message=sanitized_msg,
            )
            # 乐观降级：标记为可交易，避免查询失败导致全市场零交易
            # 停牌股票可能在撮合时因无成交价被自然跳过
            return quotes_df.with_columns(pl.lit(True).alias("is_tradable")), warning

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def _enrich_limit_status(
        self,
        quotes_df: pl.DataFrame,
        start_date: str,
        end_date: str,
    ) -> tuple[pl.DataFrame, DataWarning | None]:
        """
        为行情数据增加当日涨跌停价格（limit_up_price/limit_down_price）。

        涨跌停价格列来自 stk_limit 表（每日 up_limit/down_limit，名义价），
        与 raw_open/raw_close 同口径，供 PortfolioSimulator 撮合层将名义执行价
        （raw_open/raw_close）与涨跌停价格直接比较，判定涨停买入/跌停卖出是否可执行。

        失败时策略：
        - limit_up_price/limit_down_price 保持 None（无涨跌停限制）
        - 但返回 DataWarning 让用户知晓数据质量问题
        - 撮合层行为不变（允许买卖），但报告中有记录
        """
        try:
            stk_limit_pd = await self.cache.stk_limit_dao.get_stk_limit_range(
                start_date=start_date,
                end_date=end_date,
            )

            if stk_limit_pd is None or stk_limit_pd.empty:
                # DATA-03：与 suspend 同理，查询成功但区间内无涨跌停价格数据必须显式告警，
                # 否则默认无涨跌停限制会让回测在涨停板上买入/跌停板上卖出，收益被高估。
                warning = DataWarning(
                    warning_type="limit_data_absent",
                    start_date=start_date,
                    end_date=end_date,
                    affected_stock_count=quotes_df.height,
                    error_message=(
                        "stk_limit 表在该区间无数据，涨跌停撮合保护未生效。"
                        "回测允许了涨停买入/跌停卖出，实盘不可执行，收益被高估。"
                    ),
                )
                return quotes_df.with_columns(
                    [
                        pl.lit(None).alias("limit_up_price"),
                        pl.lit(None).alias("limit_down_price"),
                    ]
                ), warning

            limit_df = pl.from_pandas(stk_limit_pd)
            limit_df = limit_df.select(["ts_code", "trade_date", "up_limit", "down_limit"]).rename(
                {"up_limit": "limit_up_price", "down_limit": "limit_down_price"}
            )
            limit_df = limit_df.with_columns(
                [
                    pl.col("limit_up_price").cast(pl.Float64),
                    pl.col("limit_down_price").cast(pl.Float64),
                ]
            )

            quotes_df = quotes_df.join(limit_df, on=["ts_code", "trade_date"], how="left")
            return quotes_df, None
        # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 该 try 块抛出涨跌停价格补充异常. upgrade: 策略层重构时统一走 classify_error.
        except Exception as e:
            sanitized_msg = DataSanitizer.sanitize_error(e)
            logger.warning("[VectorBacktestEngine] Failed to enrich limit_status: %s", sanitized_msg)
            warning = DataWarning(
                warning_type="limit_enrich_failed",
                start_date=start_date,
                end_date=end_date,
                affected_stock_count=quotes_df.height,
                error_message=sanitized_msg,
            )
            return quotes_df.with_columns(
                [
                    pl.lit(None).alias("limit_up_price"),
                    pl.lit(None).alias("limit_down_price"),
                ]
            ), warning

    def _apply_qfq(self, quotes_df: pl.DataFrame) -> pl.DataFrame:
        """
        计算前复权价格，同时保留原始价格列。

        关键设计：
        1. 原始 open/high/low/close 重命名为 raw_open/raw_high/raw_low/raw_close
        2. 复权价格存储为 qfq_open/qfq_high/qfq_low/qfq_close
        3. 成交金额计算使用 raw_open/raw_close
        4. 收益计算和技术指标使用 qfq_close

        复权公式：
        adjusted_price = raw_price * adj_factor / base_adj_factor

        前视偏差防护（PIT）：
        回测以**回测区间第一交易日**的 adj_factor 为基准（base="first"），
        使历史绝对价格只依赖该时点及之前可得信息，不依赖期末（未来）除权信息，结果可复现。
        活体/技术分析展示仍用 latest-base；两者收益率与归一化绩效口径一致，仅绝对量纲不同。
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

        # Ensure sorted by ts_code and trade_date to guarantee stock contiguous rows and correctness of the first value in expression
        quotes_df = quotes_df.sort(["ts_code", "trade_date"])
        qfq_ratio = qfq_ratio_expr("adj_factor", "ts_code", ref="first")

        return quotes_df.with_columns(
            [
                pl.col("open").alias("raw_open"),
                pl.col("high").alias("raw_high"),
                pl.col("low").alias("raw_low"),
                pl.col("close").alias("raw_close"),
                qfq_ratio,
            ]
        ).with_columns(
            [
                (pl.col("open") * pl.col("qfq_ratio")).alias("qfq_open"),
                (pl.col("high") * pl.col("qfq_ratio")).alias("qfq_high"),
                (pl.col("low") * pl.col("qfq_ratio")).alias("qfq_low"),
                (pl.col("close") * pl.col("qfq_ratio")).alias("qfq_close"),
            ]
        )

    def _compute_avg_daily_volume(self, quotes_df: pl.DataFrame) -> pl.DataFrame:
        """
        计算每只股票的前20日平均成交量。

        avg_daily_volume 用于滑点模型计算交易冲击成本：
        - participation = trade_volume / avg_daily_volume
        - slippage_bps * (1 + participation * factor)

        使用 rolling_mean 窗口计算，min_samples=5 允许部分窗口。
        vol 列的 null/NaN（数据缺失）填 0 后再计算，避免污染窗口均值。

        BT-06：Tushare daily_quotes.vol 单位为「手」（1 手 = 100 股）。此处统一换算为
        「股」，使 avg_daily_volume 与订单股数（shares，单位「股」）口径一致，
        否则 participation 被放大 100 倍，滑点被系统性高估一两个数量级。
        换算因子与 data.constants.DAILY_QUOTES_VOL_UNIT（"lot"）对应：1 lot = 100 股。
        """
        if "vol" not in quotes_df.columns:
            return quotes_df

        # 1 手 = 100 股（BT-06，与 data.constants.DAILY_QUOTES_VOL_UNIT 一致）
        _LOT_TO_SHARE = 100

        avg_vol_expr = (
            pl.col("vol")
            .cast(pl.Float64)
            .fill_null(0.0)
            .fill_nan(0.0)
            .mul(_LOT_TO_SHARE)
            .shift(1)
            .rolling_mean(window_size=20, min_samples=5)
            .over("ts_code")
            .alias("avg_daily_volume")
        )

        return quotes_df.with_columns(avg_vol_expr)

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
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

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def _generate_signals(
        self,
        strategy: BaseStrategy,
        params: dict | None,
        trade_dates: list[date],
        progress_callback: Callable[[float, Message], None] | None = None,
        failed_signal_dates: list[dict] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> pl.DataFrame:
        signals_list = []
        total_dates = len(trade_dates)

        if trade_dates:
            # Preload all required tables for the backtest date range
            await self.data_provider.preload_range(trade_dates[0], trade_dates[-1])

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
            # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 该 try 块抛出信号生成异常. upgrade: 策略层重构时统一走 classify_error.
            except Exception as e:
                sanitized_msg = DataSanitizer.sanitize_error(e)
                logger.warning("[Backtest] Strategy failed on %s: %s", signal_date, sanitized_msg)
                if failed_signal_dates is not None:
                    failed_signal_dates.append({"date": signal_date, "error": sanitized_msg})
                if self.config.fail_fast:
                    raise

            if progress_callback and i % 10 == 0:
                progress = 0.1 + 0.4 * (i / total_dates)
                progress_callback(progress, Message("backtest_progress_processing", {"date": signal_date.isoformat()}))

        if not signals_list:
            return pl.DataFrame()

        return pl.concat(signals_list)

    def _is_rebalance_day(
        self,
        exec_date: date,
        trade_dates: list[date],
        signals_by_date: dict[tuple[date, ...], pl.DataFrame],
        rebalance_freq: str,
    ) -> bool:
        if rebalance_freq == "daily":
            return True

        if rebalance_freq == "signal":
            # D4-3: 复用 _simulate_trades 预构建的按日分区结构，O(1) 判定。
            # 有信号即再平衡，键「执行日」语义与再平衡日严格等价。
            return (exec_date,) in signals_by_date

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
        stock_meta: dict[str, dict] | None = None,
        delist_stats: dict[str, float | int] | None = None,
    ) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, list[str]]:
        if signals.is_empty():
            return (
                pl.DataFrame(),
                pl.DataFrame(),
                pl.DataFrame(),
                [],
            )

        simulator = PortfolioSimulator(self.config, self.cost_model, stock_meta=stock_meta)

        # PERF-C1: Pre-group by date via partition_by to avoid O(N*M) loop filters.
        # partition_by(as_dict=True) returns tuple keys like (date,), so we look up with (exec_date,).
        # Use df.clear() as default to preserve schema (matches original filter behavior on no match).
        signals_by_date = dict(signals.partition_by("execution_date", as_dict=True)) if not signals.is_empty() else {}
        quotes_by_date = dict(quotes_df.partition_by("trade_date", as_dict=True)) if not quotes_df.is_empty() else {}

        for exec_date in trade_dates:
            day_signals = signals_by_date.get((exec_date,), signals.clear())
            day_quotes = quotes_by_date.get((exec_date,), quotes_df.clear())
            is_rebalance = self._is_rebalance_day(
                exec_date,
                trade_dates,
                signals_by_date,
                self.config.rebalance_freq,
            )
            simulator.process_day(exec_date, day_signals, day_quotes, is_rebalance)

        if delist_stats is not None:
            delist_stats["delist_liquidation_count"] = simulator.delist_liquidation_count
            delist_stats["delist_loss_amount"] = simulator.delist_loss_amount

        return simulator.get_results()

    def _calc_ic_series(
        self,
        signals: pl.DataFrame,
        quotes_df: pl.DataFrame,
        trade_dates: list[date],
    ) -> tuple[pl.Series, list[date]]:
        """
        计算 IC 序列（持有期对齐版本）。

        关键设计：
        1. 根据 rebalance_freq 确定持有期
        2. forward_return = 执行价到下一次调仓执行价的收益
        3. 使用复权价格计算收益（qfq_close）

        返回 ``(ic_values, ic_dates)``，``ic_dates[i]`` 为
        ``ic_values[i]`` 对应的信号日期（即 ``trade_dates[i]``，非执行日期），
        供 IC 图横轴显示日期。
        """
        if signals.is_empty():
            return pl.Series([], dtype=pl.Float64), []

        # PERF-C2: Pre-group by date via partition_by to avoid O(N*M) loop filters.
        # Unpack tuple keys (k[0]) to plain date values for direct .get(date) lookup.
        quotes_by_date = (
            {k[0]: v for k, v in quotes_df.partition_by("trade_date", as_dict=True).items()}
            if not quotes_df.is_empty()
            else {}
        )
        signals_by_date = (
            {k[0]: v for k, v in signals.partition_by("signal_date", as_dict=True).items()}
            if not signals.is_empty()
            else {}
        )

        ic_values = []
        ic_dates = []

        for i, signal_date in enumerate(trade_dates[:-1]):
            execution_date = trade_dates[i + 1]

            day_signals = signals_by_date.get(signal_date, pl.DataFrame())

            if day_signals.is_empty():
                continue

            execution_quotes = quotes_by_date.get(execution_date)
            if execution_quotes is None:
                continue

            next_rebalance_date = self._get_next_rebalance_date(execution_date, trade_dates, self.config.rebalance_freq)

            if next_rebalance_date is None:
                continue

            next_rebalance_quotes = quotes_by_date.get(next_rebalance_date)
            if next_rebalance_quotes is None:
                continue

            signal_quotes = day_signals.join(execution_quotes, on="ts_code", how="inner").join(
                next_rebalance_quotes, on="ts_code", how="inner", suffix="_exit"
            )

            if signal_quotes.is_empty() or len(signal_quotes) < 3:
                continue

            # D1-m2: 入场价与出场价均按 execution_price 对称选择列，避免
            # next_open 时「开盘买→收盘卖」的隔夜混合口径与撮合层（portfolio.py 变更同口径）
            # 不一致；next_close 时仍为「收盘买→收盘卖」，行为与修复前一致。
            use_open = self.config.execution_price != "next_close"
            entry_price_col = "qfq_open" if use_open else "qfq_close"
            exit_price_col = "qfq_open_exit" if use_open else "qfq_close_exit"
            forward_return = signal_quotes.select(
                [
                    "ts_code",
                    "signal_rank",
                    ((pl.col(exit_price_col) / pl.col(entry_price_col) - 1) * 100).alias("fwd_ret"),
                ]
            )

            ic = BacktestMetrics.calc_ic(
                forward_return["signal_rank"],
                forward_return["fwd_ret"],
            )
            ic_values.append(ic)
            ic_dates.append(signal_date)  # 与 ic 同步记录信号日期（trade_dates[i]）

        assert len(ic_values) == len(ic_dates)
        return pl.Series(ic_values, dtype=pl.Float64), ic_dates

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
    ) -> tuple[pl.Series, DataWarning | None]:
        """
        计算 Benchmark 日收益序列。

        注意：IndexDaily.pct_chg 字段单位是"百分比"（如 1.5 表示 1.5%），
        需要除以 100 转换为小数形式（如 0.015）。

        这与 DailyQuotes.pct_chg 字段一致，都是百分比单位。

        D1-M1：基准缺失的交易日保留 null（禁止用 0.0 伪装成"基准零涨跌"），
        否则相对指标（信息比率/跟踪误差/月度超额）会把策略自身收益误判为超额收益。
        缺失情况通过返回的 DataWarning 上报，由调用方接入 all_warnings，
        最终驱动 backtest_view_model 的 `unreliable` 判定。
        """
        if benchmark_df.is_empty():
            return (
                pl.Series([None] * len(trade_dates), dtype=pl.Float64),
                DataWarning(
                    warning_type="benchmark_data_absent",
                    start_date=trade_dates[0].strftime("%Y%m%d"),
                    end_date=trade_dates[-1].strftime("%Y%m%d"),
                    affected_stock_count=0,
                    error_message=f"基准 {self.config.benchmark_code} 在该区间无数据，超额收益类指标不可用。",
                ),
            )

        # 标准化 benchmark_df 的 trade_date 列为 date 类型
        bm = benchmark_df
        if bm["trade_date"].dtype == pl.Utf8:
            # str.replace 只替换首个匹配（"2024-01-05"→"202401-05"），必须用 replace_all
            bm = bm.with_columns(pl.col("trade_date").str.replace_all("-", "").str.to_date("%Y%m%d"))

        # 构建 trade_dates DataFrame 并 join；缺失日产生 null，不填充
        trade_dates_df = pl.DataFrame({"trade_date": trade_dates})
        joined = trade_dates_df.join(bm, on="trade_date", how="left")
        returns = joined["pct_chg"] / 100

        missing = int(joined["pct_chg"].null_count())
        warning = None
        if missing:
            warning = DataWarning(
                warning_type="benchmark_data_partial",
                start_date=trade_dates[0].strftime("%Y%m%d"),
                end_date=trade_dates[-1].strftime("%Y%m%d"),
                affected_stock_count=missing,
                error_message=f"基准在 {missing} 个交易日缺失，相对指标按有效交易日子集计算。",
            )

        return returns, warning

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

        # BT-04: 月度聚合必须区分「基准该月完全无数据」与「基准该月确实涨了 0%」。
        # polars `count()` 不计 null，`product()` 会跳过 null（空积为 1.0，减 1 得 0.0），
        # 若直接用 product 会把「基准缺失」伪装成「基准零涨跌」，使月度超额等于策略自身收益。
        # 故将有效样本数 `_bm_valid_days` 与乘积 `_bm_raw` 分开算，全缺失月显式置 None。
        monthly = (
            df.group_by("year_month")
            .agg(
                [
                    ((pl.col("daily_return").fill_nan(0.0) + 1).product() - pl.lit(1.0)).alias("monthly_return"),
                    pl.col("benchmark_return").count().alias("_bm_valid_days"),
                    ((pl.col("benchmark_return").fill_nan(0.0) + 1).product() - pl.lit(1.0)).alias("_bm_raw"),
                    pl.col("nav").first().alias("start_nav"),
                    pl.col("nav").last().alias("end_nav"),
                ]
            )
            .sort("year_month")
            .with_columns(
                pl.when(pl.col("_bm_valid_days") == 0)
                .then(None)
                .otherwise(pl.col("_bm_raw"))
                .alias("benchmark_return"),
            )
            .with_columns((pl.col("monthly_return") - pl.col("benchmark_return")).alias("excess_return"))
            # 丢弃临时聚合列，保持列集与原实现一致（year_month/monthly_return/benchmark_return/excess_return/start_nav/end_nav）
            .drop(["_bm_valid_days", "_bm_raw"])
        )

        return monthly
