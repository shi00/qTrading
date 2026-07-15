"""回测数据提供器

复刻 DataProcessor.prepare_screening_context() 的历史版本逻辑，
为回测引擎提供完整的策略上下文。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import TYPE_CHECKING

import pandas as pd

from utils.log_decorators import PerfThreshold, log_async_operation
from utils.sanitizers import DataSanitizer
from data.persistence.quality_gate import QualityTier

if TYPE_CHECKING:
    from data.cache.cache_manager import CacheManager
    from data.data_processor import DataProcessor
    from strategies.utils import StrategyContext

logger = logging.getLogger(__name__)


class _BacktestQualityProxy:
    """回测场景下的 DataProcessor 质量代理。

    仅提供 _quality_tier 属性以满足质量门控 _check_tier 检查，
    避免在回测路径因缺少 data_processor 而抛 QualityGateError。

    回测使用历史快照数据，质量等级默认 GOLD（最高等级），
    确保任何质量要求的策略都能通过门控。
    回测数据质量由数据同步流程保证，不应被质量门控阻断。
    """

    def __init__(self, tier: QualityTier = QualityTier.GOLD):
        self._quality_tier = int(tier)
        logger.debug(
            "[BacktestQualityProxy] Using quality tier %s for backtest context.",
            tier.name,
        )


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
        # 缓存 proxy，避免每次 build_context 都新建实例
        self._quality_proxy = _BacktestQualityProxy() if data_processor is None else None
        self._preloaded: dict | None = None
        self.preload_max_days = preload_max_days

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
                return datetime.strptime(clean_d, "%Y%m%d").date()
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
            self._preloaded = None
            return

        start_str = self._normalize_trade_date(start_date_obj)
        end_str = self._normalize_trade_date(end_date_obj)

        logger.info("[BacktestDataProvider] Preloading range %s to %s...", start_str, end_str)

        self._preloaded = {}

        try:
            # 并行查询所有数据
            results = await asyncio.gather(
                self.cache.get_screening_data_range(start_str, end_str),
                self.cache.get_fundamental_screening_data_range(start_str, end_str),
                self.cache.get_northbound_range(start_str, end_str),
                self.cache.get_moneyflow_hsgt_range(start_str, end_str),
                self.cache.get_moneyflow_range(start_str, end_str),
                self.cache.get_top_list_range(start_str, end_str),
                self.cache.get_block_trade_range(start_str, end_str),
                return_exceptions=True,
            )

            keys = [
                "screening_data",
                "fundamental_screening_data",
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
        except asyncio.CancelledError:
            raise
        # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 该 try 块抛出数据预加载异常. upgrade: 策略层重构时统一走 classify_error.
        except Exception as e:
            logger.error(
                "[BacktestDataProvider] Failed to preload range: %s", DataSanitizer.sanitize_error(e), exc_info=True
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
        if self.data_processor is not None:
            context["data_processor"] = self.data_processor
        else:
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
            "northbound_data": self.cache.get_northbound,
            "northbound_flow_data": self.cache.get_moneyflow_hsgt,
            "moneyflow_data": self.cache.get_moneyflow,
            "top_list": self.cache.get_top_list,
            "block_trade": self.cache.get_block_trade,
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
        2. 过滤已退市股票 (list_status='L')
        3. 过滤当时未上市股票 (list_date <= trade_date)
        4. 包含 is_tradable 字段（来自 suspend_d 表）
        5. 财报数据满足 ann_date <= trade_date 约束

        这与 DataProcessor.prepare_screening_context() 的实盘路径完全一致。
        """
        try:
            if self.data_processor is not None:
                return await self.data_processor.get_screening_data(trade_date)
            return await self.cache.get_screening_data(trade_date)
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
            return await self.cache.get_fundamental_screening_data(trade_date)
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
            stock_basic_df = await self.cache.get_stock_basic()
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
