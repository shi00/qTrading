"""回测数据提供器

复刻 DataProcessor.prepare_screening_context() 的历史版本逻辑，
为回测引擎提供完整的策略上下文。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from data.cache.cache_manager import CacheManager
    from data.data_processor import DataProcessor

logger = logging.getLogger(__name__)


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

    async def build_context(
        self,
        trade_date: date,
        *,
        disable_ai: bool = True,
    ) -> dict:
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
        return context

    async def _build_historical_screening_context(
        self,
        trade_date: date,
    ) -> dict:
        """
        复刻 DataProcessor.prepare_screening_context() 的历史版本。

        步骤：
        1. 获取当日 screening_data（行情）
        2. 获取当日 fundamental_screening_data（基本面）
        3. 过滤停牌股（is_tradable=True）
        4. 加载辅助表（northbound, moneyflow, top_list, block_trade）
        5. 设置 _diagnostics 用于依赖状态追踪
        """
        context = {}
        diagnostics = {
            "quality_tier": None,
            "trade_date": None,
            "base_complete": False,
            "strategy_ready": False,
            "table_status": {},
        }

        trade_date_str = self._normalize_trade_date(trade_date)

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
        """获取当日 screening_data（行情数据）。"""
        try:
            return await self.cache.get_daily_quotes(
                start_date=trade_date,
                end_date=trade_date,
            )
        except Exception as e:
            logger.warning(f"[BacktestDataProvider] Failed to get screening_data for {trade_date}: {e}")
            return None

    async def _get_fundamental_screening_data(self, trade_date: str) -> pd.DataFrame | None:
        """获取当日 fundamental_screening_data（基本面数据）。"""
        try:
            return await self.cache.get_daily_indicators(
                start_date=trade_date,
                end_date=trade_date,
            )
        except Exception as e:
            logger.warning(f"[BacktestDataProvider] Failed to get fundamental_data for {trade_date}: {e}")
            return None

    @staticmethod
    def _normalize_trade_date(value: date | str) -> str:
        if isinstance(value, date):
            return value.strftime("%Y%m%d")
        if isinstance(value, str):
            return value
        return str(value)
