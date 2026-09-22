"""回测数据提供器

复刻 DataProcessor.prepare_screening_context() 的历史版本逻辑，
为回测引擎提供完整的策略上下文。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import TYPE_CHECKING, Any

import pandas as pd

from utils.log_decorators import PerfThreshold, log_async_operation
from utils.sanitizers import DataSanitizer
from data.persistence.quality_gate import QualityTier
from data.persistence.daos.screener_dao import _MAX_SCREENING_RANGE_ROWS

# D3-M4: 区间预载行数护栏随真实规模自适应。
# 固定护栏与 A 股扩容后的真实行数（2026 约 5400 存活 × 244 交易日 ≈ 132 万）过于接近，
# 默认 preload_max_days=366 会在 2~3 年内稳定超限并静默降级；故按 count_expected_rows 放大，
# 并对畸形区间（join 爆炸/参数错误）保留绝对上限，避免护栏随错误规模无限膨胀。
_SCREENING_ROWS_SAFETY_FACTOR = 1.5
_SCREENING_MAX_RANGE_ROWS = 5_000_000

if TYPE_CHECKING:
    from data.cache.cache_manager import CacheManager
    from data.data_processor import DataProcessor
    from strategies.utils import StrategyContext

logger = logging.getLogger(__name__)


class _BacktestQualityProxy:
    """回测场景下的 DataProcessor 质量代理。

    双职责：
    1. 覆写质量门控字段 `_quality_tier` / `_scan_missing_dates`，让门控评估
       "回测区间"而非"实盘最新数据"（D3-M2）。`_quality_tier` 恒为区间可用数据的
       质量等级（无缺口日为 GOLD）——区间缺口**不降级 tier**（D3-M2 残留修复）：
       缺口日由 build_context 返回空 screening_data → 策略空输出（无信号），
       若聚合降级 BRONZE 会在 `_check_tier` 逐日消费时把整个区间的每一天都拦截。
       `_scan_missing_dates` 以 frozenset 承载缺口日，供 quality_gate._check_tier
       的连续窗口契约消费（D2-9）与 range_quality_gaps 警告展示。
    2. 其余属性/方法经 `__getattr__` 委托给注入的 data_processor（若有），
       保证策略访问 `context["data_processor"]` 的数据能力（cache / trade_calendar /
       get_screening_data / get_latest_trade_date / is_cancelled 等）不被破坏。

    无 delegate（未注入 data_processor 的纯数据预载场景）时退化为仅含质量字段的
    纯代理，与历史行为一致。

    # NOTE(lazy): 非 preload 路径（宽区间跳过 / daily fallback）仍用默认 GOLD，
    #              区间缺口评估仅在 preload_range 成功路径生效。
    # ceiling: 未预载时无区间数据可评估，无法计算缺口。
    # upgrade: 为非 preload 路径补充区间质量评估（evaluate_historical_window）。
    """

    def __init__(
        self,
        delegate: DataProcessor | None = None,
        tier: QualityTier = QualityTier.GOLD,
        missing_dates: frozenset[str] = frozenset(),
    ):
        self._delegate = delegate
        self._quality_tier = int(tier)
        self._scan_missing_dates = missing_dates
        # 对抗检视 A：显式覆写 `_health_cache`，避免 `_check_tier` 在 BRONZE 降级时经
        # `__getattr__` 转发到实盘 delegate，把"实盘最新数据落后天数(lag_days)"误作回测
        # 区间归因——回测归因应仅由上方 `_scan_missing_dates` 报告的区间缺失交易日承载。
        self._health_cache: dict | None = None
        # DS-03: 显式覆写 per-table 等级表为 None，避免 `_check_tier` 经 `__getattr__`
        # 转发到实盘 delegate 的逐表等级——回测信任本代理固定的 `_quality_tier` 定值
        # （GOLD），不依实盘 per-table 质量判级（None ⇒ 非 dict ⇒ 门控回退全局）。
        self._quality_tier_by_table: dict[str, int] | None = None

    def __getattr__(self, name: str) -> Any:
        # 仅当普通实例属性不存在时被调用：委托给真实 data_processor，
        # 保持策略对 context processor 的数据访问能力（非质量字段）。
        if self._delegate is not None:
            return getattr(self._delegate, name)
        raise AttributeError(name)


class BacktestDataProvider:
    """
    按交易日提供与 DataProcessor.prepare_screening_context() 等价的历史 context。

    关键设计：
    1. 复刻 DataProcessor.prepare_screening_context() 的完整逻辑
    2. 包含 screening_data, fundamental_screening_data, 辅助表
    3. 设置 is_backtest=True 以触发 AI 的 as-of 安全模式
    4. 可选 disable_ai=True 完全关闭 AI 分析
    """

    def __init__(
        self,
        cache: CacheManager,
        data_processor: DataProcessor | None = None,
        preload_max_days: int = 366,
    ):
        self.cache = cache
        self.data_processor = data_processor
        # D3-M2: 恒创建回测区间质量代理，不再因注入 data_processor 而置 None。
        # 有注入时 delegate=真实 processor（策略经 __getattr__ 委托访问其数据能力），
        # 无注入时退化为纯质量字段代理。preload_range 成功后按区间缺口写入
        # `_scan_missing_dates`（等级恒 GOLD，缺口不降级，见 preload_range 注释）。
        self._quality_proxy = _BacktestQualityProxy(delegate=data_processor)
        self._preloaded: dict | None = None
        self.preload_max_days = preload_max_days
        # D3-M4: 本次回测区间预载的降级警告（区间超限 / 范围预载失败 / 护栏超限），
        # 由 engine 在组装 BacktestResult.data_warnings 时并入，让「走了慢路径」在 UI 可见。
        self._range_preload_warnings: list[str] = []

    @property
    def range_preload_warnings(self) -> list[str]:
        """本次回测区间预载的降级警告（D3-M4），供 engine 并入 BacktestResult.data_warnings。"""
        return self._range_preload_warnings

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def preload_range(self, start_date: date, end_date: date):
        """一次性预取整个回测区间的各类数据到内存中，提升回测速度"""
        # 兼容处理输入参数类型并转为 date 对象
        from datetime import datetime

        def to_date_obj(d):
            if isinstance(d, datetime):
                return d.date()
            if isinstance(d, date):
                return d
            if isinstance(d, str):
                clean_d = d.replace("-", "").strip()
                return datetime.strptime(clean_d, "%Y%m%d").date()  # noqa: DTZ007  归一化后的 YYYYMMDD 业务日期字符串无时区语义
            return d

        try:
            start_date_obj = to_date_obj(start_date)
            end_date_obj = to_date_obj(end_date)
        # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 该 try 块抛出数据预加载异常. upgrade: 策略层重构时统一走 classify_error.
        except Exception as e:
            logger.error(
                "[BacktestDataProvider] Invalid date format for preloading: %s", DataSanitizer.sanitize_error(e)
            )
            self._preloaded = None
            return

        # 增加区间保护，限制最长预加载天数（默认 366，可通过 preload_max_days 配置），
        # 防止大范围数据加载导致 OOM/DB 过载
        days_limit = self.preload_max_days
        if (end_date_obj - start_date_obj).days > days_limit:
            logger.warning(
                "[BacktestDataProvider] Preload range too wide (%s days > %s). "
                "Skipping range preloading to prevent OOM/DB overload. Fallback to daily query.",
                (end_date_obj - start_date_obj).days,
                days_limit,
            )
            # D3-M4: 降级必须可见，写入本 provider 供 engine 并入 BacktestResult.data_warnings
            self._range_preload_warnings.append(
                f"preload_range_too_wide: preload_max_days={days_limit}, "
                f"requested={(end_date_obj - start_date_obj).days} days. Fallback to daily query."
            )
            self._preloaded = None
            return

        # 每次预载重置区间质量代理与降级警告列表，避免跨多次 preload_range 调用累积旧状态
        # （重复调用时若本次跳过评估，proxy 不应残留上次区间的 BRONZE/GOLD 判定）。
        self._range_preload_warnings = []
        self._quality_proxy = _BacktestQualityProxy(delegate=self.data_processor)

        start_str = self._normalize_trade_date(start_date_obj)
        end_str = self._normalize_trade_date(end_date_obj)

        logger.info("[BacktestDataProvider] Preloading range %s to %s...", start_str, end_str)

        # D3-M4: 按区间真实规模计算自适应护栏，传给 screening DAO。
        # 固定护栏与 A 股扩容后的真实行数过近会静默触发降级；count_expected_rows 失败（返回 1，
        # 见 stock_dao）时护栏退化回固定常量，行为与现状一致且安全。
        # 注意：不再预置 expected_rows=1（CodeQL CWE-563 冗余赋值告警），成功路径取下限保护、
        # 异常路径在 except 兜底为 1，使用点前的所有路径均已赋值。
        try:
            expected_rows = await self.cache.stock_dao.count_expected_rows(start_date_obj, end_date_obj) or 1
        except asyncio.CancelledError:
            raise
        # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 该 try 块抛出预期行数估算异常. upgrade: 策略层重构时统一走 classify_error.
        except Exception as e:
            logger.warning(
                "[BacktestDataProvider] Failed to estimate range rows, use default guard: %s",
                DataSanitizer.sanitize_error(e),
            )
            expected_rows = 1
        screening_guard_rows = min(
            max(_MAX_SCREENING_RANGE_ROWS, int(expected_rows * _SCREENING_ROWS_SAFETY_FACTOR)),
            _SCREENING_MAX_RANGE_ROWS,
        )

        self._preloaded = {}

        try:
            # D3-M2 区间质量评估：先取区间"全市场交易日"全集（TradeCalendarService，复用 D3-M1 通路），
            # 用于检测 screening_data 的缺失交易日。查询失败/退化时 expected 为 None，保持默认 GOLD 代理。
            from data.domain_services.trade_calendar_service import TradeCalendarService

            expected_dates: set[str] | None = None
            try:
                cal_dates = await TradeCalendarService(self.cache, None).get_trade_dates(start_date_obj, end_date_obj)
                expected_dates = {d.strftime("%Y%m%d") for d in cal_dates}
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "[BacktestDataProvider] Range quality evaluation calendar lookup failed, "
                    "keep default GOLD quality proxy.",
                    exc_info=True,
                )
            # 并行查询所有数据
            results = await asyncio.gather(
                self.cache.screener_dao.get_fundamental_screening_data_range(
                    start_str, end_str, max_rows=screening_guard_rows
                ),
                self.cache.quote_dao.get_northbound_range(start_str, end_str),
                self.cache.market_dao.get_moneyflow_hsgt_range(start_str, end_str),
                self.cache.quote_dao.get_moneyflow_range(start_str, end_str),
                self.cache.quote_dao.get_top_list_range(start_str, end_str),
                self.cache.quote_dao.get_block_trade_range(start_str, end_str),
                return_exceptions=True,
            )
            # DS-05: screening_data 由 fundamental_screening_data 派生（同模板唯差
            # close 条件，SQL 层 close IS NOT NULL ↔ notna() 等价），不再单独跑第二遍
            # 几乎相同的全市场 JOIN（原 gather 双份 150 万行上限 → 收敛为一份）。
            # 派生在 result 异常时原样保留异常，由下方循环统一降级处理。
            # 因 R1（strategies 不得 import data 层）内联同源逻辑，勿单独变更一侧。
            fund_first = results[0]
            if isinstance(fund_first, BaseException) or fund_first is None or fund_first.empty:
                screening_res = fund_first
            elif "close" in fund_first.columns:
                screening_res = fund_first[fund_first["close"].notna()].reset_index(drop=True)
            else:
                screening_res = fund_first
            results = [fund_first, screening_res, *results[1:]]

            keys = [
                "fundamental_screening_data",
                "screening_data",
                "northbound_data",
                "northbound_flow_data",
                "moneyflow_data",
                "top_list",
                "block_trade",
            ]

            for key, res in zip(keys, results, strict=True):
                if isinstance(res, BaseException):
                    if isinstance(res, asyncio.CancelledError):
                        raise res
                    logger.warning(
                        "[BacktestDataProvider] Range preload failed for %s: %s. Fallback to daily query.",
                        key,
                        res,
                    )
                    # D3-M4: 降级必须可见（含护栏超限抛出的 ValueError）
                    # res 为 gather return_exceptions 返回的 BaseException，统一转 str 后经 sanitize_error 脱敏（R9）
                    self._range_preload_warnings.append(
                        f"range_preload_failed:{key}: {DataSanitizer.sanitize_error(str(res))}. Fallback to daily query."
                    )
                    self._preloaded[key] = None
                elif res is not None and not res.empty:
                    df_copy = res.copy()
                    if "trade_date" in df_copy.columns:
                        # 向量化转换并格式化为 YYYYMMDD
                        trade_dates_dt = pd.to_datetime(df_copy["trade_date"], errors="coerce")
                        df_copy["trade_date_str"] = trade_dates_dt.dt.strftime("%Y%m%d").fillna("")
                        # 过滤掉无效或空的交易日期
                        df_copy = df_copy[df_copy["trade_date_str"] != ""].copy()
                        # 按 trade_date_str 进行 groupby 并存储为字典
                        self._preloaded[key] = {date_str: grp for date_str, grp in df_copy.groupby("trade_date_str")}
                    else:
                        self._preloaded[key] = df_copy

                    rows = len(res)
                    logger.info("[BacktestDataProvider] Preloaded %s: %s rows", key, rows)
                else:
                    self._preloaded[key] = {}

            # D3-M2 区间缺口评估：expected=全市场交易日全集，actual=screening_data 实际覆盖日期键。
            # 空 dict（查询成功但零行）同样评估——整段区间无筛选数据等价于全缺口，必须可见。
            # 仅查询失败 fallback daily（_preloaded[key]=None）或 expected 不可得时保持默认 GOLD 代理。
            # D3-M2 残留修复：区间缺口只写入 `_scan_missing_dates` 与下方警告，**不降级 proxy tier**。
            # 区间 tier 是聚合粒度，而 `_check_tier` 逐日消费——若任一缺口降级 BRONZE，默认 SILVER
            # 策略（polars_base 默认等级）会在整个区间的每一天被 QualityGateError 拦截，整段回测零信号；
            # 缺口日本身已由 build_context 返回空 screening_data → 策略空输出（无信号），无需拦截。
            # 缺口可见性由下方 range_quality_gaps 警告承载（入 data_warnings → UI unreliable 判定）。
            screen_pre = self._preloaded.get("screening_data")
            if expected_dates is not None and isinstance(screen_pre, dict):
                actual = set(screen_pre.keys())
                missing = frozenset(sorted(expected_dates - actual))
                self._quality_proxy = _BacktestQualityProxy(
                    delegate=self.data_processor,
                    tier=QualityTier.GOLD,
                    missing_dates=missing,
                )
                if missing:
                    # D3-M2: 区间缺口必须可见——缺口不触发 `_check_tier` 的等级拦截（proxy 恒
                    # GOLD，缺口日策略空输出）；但声明 `require_continuous_window=True` 的策略
                    # （如 OversoldStrategy）仍被 quality_gate 的连续窗口检查拒绝（D2-9 保守
                    # 设计，缺口区间整段 failed 经 failed_signal_dates 可见）。本警告覆盖其余
                    # 静默场景（BRONZE 策略 / 未声明连续窗口的策略），经 _range_preload_warnings
                    # 并入 BacktestResult.data_warnings，触发 backtest_view_model 的 unreliable 判定。
                    # 格式对齐 DataWarning.__str__（[type] start-end: ...），含缺失数量与占比、
                    # 前 5 个缺失日（超长截断防撑爆）。
                    missing_sorted = sorted(missing)
                    sample = ",".join(missing_sorted[:5]) + ("..." if len(missing_sorted) > 5 else "")
                    self._range_preload_warnings.append(
                        f"[range_quality_gaps] {start_str}-{end_str}: {len(missing)} of {len(expected_dates)} "
                        f"trade date(s) missing in screening_data: {sample}"
                    )
                logger.info(
                    "[BacktestDataProvider] Range quality: %s missing trade date(s) in screening_data → tier %s",
                    len(missing),
                    QualityTier(self._quality_proxy._quality_tier).name,
                )
        except asyncio.CancelledError:
            raise
        # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 该 try 块抛出数据预加载异常. upgrade: 策略层重构时统一走 classify_error.
        except Exception as e:
            logger.error(
                "[BacktestDataProvider] Failed to preload range: %s", DataSanitizer.sanitize_error(e), exc_info=True
            )
            # D3-M4: 整体预载失败同样可见，供 UI 提示本次回测走了逐日慢路径
            self._range_preload_warnings.append(
                f"range_preload_error: {DataSanitizer.sanitize_error(e)}. Fallback to daily query."
            )
            self._preloaded = None

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def build_context(
        self,
        trade_date: date,
        *,
        disable_ai: bool = True,
    ) -> StrategyContext:
        """
        构建历史策略上下文。

        必须包含以下字段（与 DataProcessor.prepare_screening_context() 一致）：
        - screening_data: 当日行情数据（已过滤停牌股）
        - fundamental_screening_data: 当日基本面数据
        - trade_date: 交易日期
        - is_backtest: True（触发 AI as-of 模式）
        - _disable_ai: 可选，完全关闭 AI
        - northbound_data, moneyflow_data, top_list, block_trade: 辅助表
        - _diagnostics: 依赖状态追踪

        注意：不能只传 trade_date 和 params，会导致策略依赖检查失败。
        """
        context = await self._build_historical_screening_context(trade_date)
        context["trade_date"] = self._normalize_trade_date(trade_date)
        context["is_backtest"] = True
        if disable_ai:
            context["_disable_ai"] = True
        # 注入 data_processor 以通过质量门控检查
        # PolarsBaseStrategy.filter() 读取 context["data_processor"] 进行 _check_tier，
        # 缺少此键在 STRICT_QUALITY_GATE=true 下会抛 QualityGateError
        # D3-M2: 质量门控口径 = 回测区间数据质量（_quality_proxy，等级恒为区间可用数据的
        # GOLD；区间缺口不降级，缺口日由空 screening_data 自然空输出，缺口信息由
        # `_scan_missing_dates` 承载），不复用实盘最新数据等级（data_processor._quality_tier）——
        # 那会双向错配：实盘落后误杀历史回测、实盘完好漏放区间缺口。代理 __getattr__
        # 委托持有真实 processor，策略对 context processor 的数据访问能力保持可用。
        context["data_processor"] = self._quality_proxy
        return context

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def _build_historical_screening_context(
        self,
        trade_date: date,
    ) -> StrategyContext:
        """
        复刻 DataProcessor.prepare_screening_context() 的历史版本。

        步骤：
        1. 获取当日 screening_data（行情）
        2. 获取当日 fundamental_screening_data（基本面）
        3. 过滤停牌股（is_tradable=True）
        4. 加载辅助表（northbound, moneyflow, top_list, block_trade）
        5. 设置 _diagnostics 用于依赖状态追踪
        """
        context: StrategyContext = {}
        diagnostics = {
            "quality_tier": None,
            "trade_date": None,
            "base_complete": False,
            "strategy_ready": False,
            "table_status": {},
        }

        trade_date_str = self._normalize_trade_date(trade_date)
        preloaded = getattr(self, "_preloaded", None)

        # 1. 获取当日 screening_data
        if preloaded and "screening_data" in preloaded and preloaded["screening_data"] is not None:
            screening_data = preloaded["screening_data"].get(trade_date_str, pd.DataFrame())
        else:
            screening_data = await self._get_screening_data(trade_date_str)

        if screening_data is not None and not screening_data.empty and "is_tradable" in screening_data.columns:
            suspended_count = int((~screening_data["is_tradable"]).sum())
            screening_data = screening_data[screening_data["is_tradable"]].copy()
            if suspended_count > 0:
                diagnostics["suspended_filtered"] = suspended_count
        elif screening_data is not None and not screening_data.empty and "is_tradable" not in screening_data.columns:
            logger.warning(
                "[BacktestDataProvider] is_tradable column missing from screening_data; "
                "suspended stocks will NOT be filtered"
            )

        context["screening_data"] = screening_data
        diagnostics["trade_date"] = trade_date_str

        base_complete = screening_data is not None and not screening_data.empty
        diagnostics["base_complete"] = base_complete

        # 2. 获取当日 fundamental_screening_data
        if (
            preloaded
            and "fundamental_screening_data" in preloaded
            and preloaded["fundamental_screening_data"] is not None
        ):
            fundamental_data = preloaded["fundamental_screening_data"].get(trade_date_str, pd.DataFrame())
        else:
            fundamental_data = await self._get_fundamental_screening_data(trade_date_str)

        if fundamental_data is not None and not fundamental_data.empty:
            if "is_tradable" in fundamental_data.columns:
                fundamental_data = fundamental_data[fundamental_data["is_tradable"]].copy()
            context["fundamental_screening_data"] = fundamental_data
            diagnostics["table_status"]["fundamental_screening_data"] = {
                "ready": not fundamental_data.empty,
                "rows": len(fundamental_data),
            }
        else:
            diagnostics["table_status"]["fundamental_screening_data"] = {"ready": False, "rows": 0}

        diagnostics["table_status"]["screening_data"] = {
            "ready": base_complete,
            "rows": len(screening_data) if screening_data is not None else 0,
        }

        # 3. 加载辅助表
        auxiliary_tables = {
            "northbound_data": self.cache.quote_dao.get_northbound,
            "northbound_flow_data": self.cache.market_dao.get_moneyflow_hsgt,
            "moneyflow_data": self.cache.quote_dao.get_moneyflow,
            "top_list": self.cache.quote_dao.get_top_list,
            "block_trade": self.cache.quote_dao.get_block_trade,
        }

        all_aux_ready = True
        for key, fetch_func in auxiliary_tables.items():
            try:
                if preloaded and key in preloaded and preloaded[key] is not None:
                    data = preloaded[key].get(trade_date_str, pd.DataFrame())
                else:
                    data = await fetch_func(trade_date=trade_date_str)

                if data is not None:
                    context[key] = data
                    is_empty = hasattr(data, "empty") and data.empty
                    diagnostics["table_status"][key] = {"ready": True, "rows": len(data) if not is_empty else 0}
                else:
                    diagnostics["table_status"][key] = {"ready": False, "rows": 0}
                    all_aux_ready = False
            # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 该 try 块抛出历史筛选上下文构建异常. upgrade: 策略层重构时统一走 classify_error.
            except Exception as e:
                sanitized_msg = DataSanitizer.sanitize_error(e)
                logger.warning("[BacktestDataProvider] Failed to fetch %s: %s", key, sanitized_msg)
                diagnostics["table_status"][key] = {"ready": False, "rows": 0, "error": sanitized_msg}
                all_aux_ready = False

        diagnostics["strategy_ready"] = base_complete and all_aux_ready
        context["_diagnostics"] = diagnostics

        return context

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def _get_screening_data(self, trade_date: str) -> pd.DataFrame | None:
        """
        获取当日 screening_data（行情数据）。

        使用 ScreenerDao.get_screening_data() 标准 SQL，确保：
        1. 包含 turnover_rate, pe_ttm, pb, total_mv 等完整字段
        2. 按 as_of 时点 PIT 存活判定过滤（stock_alive_condition：退市日晚于 as_of 的股票
           在 as_of 时可见，避免生存者偏差；DAT-01）
        3. 过滤当时未上市股票 (list_date <= trade_date)
        4. 包含 is_tradable 字段（来自 suspend_d 表）
        5. 财报数据满足 ann_date <= trade_date 约束

        这与 DataProcessor.prepare_screening_context() 的实盘路径完全一致。
        """
        try:
            if self.data_processor is not None:
                return await self.data_processor.get_screening_data(trade_date)
            return await self.cache.screener_dao.get_screening_data(trade_date)
        # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 该 try 块抛出筛选数据获取异常. upgrade: 策略层重构时统一走 classify_error.
        except Exception as e:
            logger.warning(
                "[BacktestDataProvider] Failed to get screening_data for %s: %s",
                trade_date,
                DataSanitizer.sanitize_error(e),
            )
            return None

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def _get_fundamental_screening_data(self, trade_date: str) -> pd.DataFrame | None:
        """
        获取当日 fundamental_screening_data（基本面数据）。

        使用 ScreenerDao.get_fundamental_screening_data() 标准 SQL，确保：
        1. 包含 roe, or_yoy, netprofit_yoy, grossprofit_margin, debt_to_assets 等字段
        2. 财报数据满足 ann_date <= trade_date 约束（防止未来函数）
        3. 使用 ROW_NUMBER() 窗口函数获取最新一期财报

        这与 DataProcessor.prepare_screening_context() 的实盘路径完全一致。
        """
        try:
            if self.data_processor is not None:
                return await self.data_processor.get_fundamental_screening_data(trade_date)
            return await self.cache.screener_dao.get_fundamental_screening_data(trade_date)
        # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 该 try 块抛出基本面筛选数据获取异常. upgrade: 策略层重构时统一走 classify_error.
        except Exception as e:
            logger.warning(
                "[BacktestDataProvider] Failed to get fundamental_data for %s: %s",
                trade_date,
                DataSanitizer.sanitize_error(e),
            )
            return None

    @staticmethod
    def _normalize_trade_date(value: date | str) -> str:
        if isinstance(value, date):
            return value.strftime("%Y%m%d")
        if isinstance(value, str):
            return value
        return str(value)

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def get_stock_meta(self) -> dict[str, dict]:
        """BT-002: 加载 stock_basic 元数据，包含 delist_date 字段。

        用于 PortfolioSimulator 区分退市与临时停牌：
        - delist_date 非空且 exec_date >= delist_date → 退市，按最后已知价清算
        - delist_date 为空或 exec_date < delist_date → 临时停牌，保留持仓

        Returns:
            {ts_code: {"delist_date": date | None}}
        """
        try:
            stock_basic_df = await self.cache.stock_dao.get_stock_basic()
        # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 该 try 块抛出股票元数据获取异常. upgrade: 策略层重构时统一走 classify_error.
        except Exception as e:
            logger.warning(
                "[BacktestDataProvider] Failed to load stock_basic for stock_meta: %s", DataSanitizer.sanitize_error(e)
            )
            return {}

        if stock_basic_df is None or stock_basic_df.empty:
            return {}

        meta: dict[str, dict] = {}
        for row in stock_basic_df.itertuples(index=False):
            ts_code = getattr(row, "ts_code", None)
            if ts_code is None:
                continue
            delist_date = getattr(row, "delist_date", None)
            # pandas 可能返回 Timestamp/NaT/None，统一转换为 date | None
            if delist_date is None or pd.isna(delist_date):
                delist_date = None
            elif hasattr(delist_date, "date"):
                delist_date = delist_date.date()
            meta[ts_code] = {"delist_date": delist_date}
        return meta
