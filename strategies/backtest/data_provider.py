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
    ):
        self.cache = cache
        self.data_processor = data_processor
        # 缓存 proxy，避免每次 build_context 都新建实例
        self._quality_proxy = _BacktestQualityProxy() if data_processor is None else None
        self._preloaded: dict | None = None

    async def preload_range(self, start_date: date, end_date: date):
        """一次性预取整个回测区间的各类数据到内存中，提升回测速度"""
        start_str = self._normalize_trade_date(start_date)
        end_str = self._normalize_trade_date(end_date)

        logger.info(f"[BacktestDataProvider] Preloading range {start_str} to {end_str}...")

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
                        f"[BacktestDataProvider] Range preload failed for {key}: {res}. Fallback to daily query."
                    )
                    self._preloaded[key] = None
                elif res is not None and not res.empty:
                    df_copy = res.copy()
                    if "trade_date" in df_copy.columns:
                        # 转换并格式化为 YYYYMMDD
                        def to_str(d):
                            if isinstance(d, date):
                                return d.strftime("%Y%m%d")
                            d_str = str(d).replace("-", "").strip()[:8]
                            return d_str

                        df_copy["trade_date_str"] = df_copy["trade_date"].apply(to_str)
                        # 按 trade_date_str 进行 groupby 并存储为字典
                        self._preloaded[key] = {date_str: grp for date_str, grp in df_copy.groupby("trade_date_str")}
                    else:
                        self._preloaded[key] = df_copy

                    rows = len(res)
                    logger.info(f"[BacktestDataProvider] Preloaded {key}: {rows} rows")
                else:
                    self._preloaded[key] = {}
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[BacktestDataProvider] Failed to preload range: {e}", exc_info=True)
            self._preloaded = None

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
            except Exception as e:
                logger.warning(f"[BacktestDataProvider] Failed to fetch {key}: {e}")
                diagnostics["table_status"][key] = {"ready": False, "rows": 0, "error": str(e)}
                all_aux_ready = False

        diagnostics["strategy_ready"] = base_complete and all_aux_ready
        context["_diagnostics"] = diagnostics

        return context

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
        except Exception as e:
            logger.warning("[BacktestDataProvider] Failed to get screening_data for %s: %s", trade_date, e)
            return None

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
        except Exception as e:
            logger.warning("[BacktestDataProvider] Failed to get fundamental_data for %s: %s", trade_date, e)
            return None

    @staticmethod
    def _normalize_trade_date(value: date | str) -> str:
        if isinstance(value, date):
            return value.strftime("%Y%m%d")
        if isinstance(value, str):
            return value
        return str(value)
