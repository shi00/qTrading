"""WatchlistViewModel — 关注列表 ViewModel (FR-UX-004, Task 4.2).

遵循项目 MVVM 模式（V1 声明式范式）：
- frozen dataclass WatchlistState + subscribe/_notify
- 调用 CacheManager 代理方法操作 watchlist 表
- VM 只产出 Message (i18n key)，不感知 locale，不 import flet

L771 合规：state 业务数据用 tuple[WatchlistRow, ...]，无 dual-track。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from data.cache.cache_manager import CacheManager
from ui.viewmodels import Message
from ui.viewmodels.observable_mixin import ObservableViewModelMixin
from utils.error_classifier import classify_error, classify_severity
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WatchlistRow:
    """关注列表行数据 (L771 合规: frozen dataclass)."""

    ts_code: str = ""
    stock_name: str = ""
    added_at: str = ""
    note: str = ""


@dataclass(frozen=True)
class StockSearchRow:
    """「添加关注」搜索结果行数据 (L771 合规: frozen dataclass)."""

    ts_code: str = ""
    name: str = ""


@dataclass(frozen=True)
class WatchlistState:
    """WatchlistViewModel 的不可变状态快照 (L771 合规, 无 dual-track)."""

    watchlist_rows: tuple[WatchlistRow, ...] = ()
    is_loading: bool = False
    load_error: Message | None = None
    load_error_detail: str | None = None
    search_results: tuple[StockSearchRow, ...] = ()
    is_searching: bool = False
    search_keyword: str = ""
    search_error: Message | None = None


class WatchlistViewModel(ObservableViewModelMixin[WatchlistState]):
    """关注列表 ViewModel (V1 声明式范式).

    职责：
    1. 管理关注列表状态 (frozen WatchlistState snapshot)
    2. 调用 CacheManager 代理方法 add/remove/get/is_in
    3. add/remove 后自动刷新列表
    """

    def __init__(self, cache: CacheManager | None = None):
        self.cache = cache or CacheManager()
        self._state: WatchlistState = WatchlistState()
        self._subscribers: list[Callable[[WatchlistState], None]] = []
        self._init_mixin_fields()

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def load_watchlist(self) -> None:
        """加载关注列表 (从 DB 读取并转换为 tuple[WatchlistRow, ...])."""
        self._set_state(is_loading=True, load_error=None, load_error_detail=None)
        try:
            df = await self.cache.get_watchlist()
            rows = _df_to_watchlist_rows(df)
            self._set_state(watchlist_rows=rows, is_loading=False)
        except asyncio.CancelledError:
            self._set_state(is_loading=False)
            raise
        except Exception as e:
            _handle_error(e, "load_watchlist", self)

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def add_to_watchlist(
        self,
        ts_code: str,
        stock_name: str,
        note: str | None = None,
    ) -> None:
        """加入关注并刷新列表."""
        try:
            await self.cache.add_to_watchlist(ts_code, stock_name, note)
            await self.load_watchlist()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _handle_error(e, "add_to_watchlist", self)

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def remove_from_watchlist(self, ts_code: str) -> None:
        """移除关注并刷新列表."""
        try:
            await self.cache.remove_from_watchlist(ts_code)
            await self.load_watchlist()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _handle_error(e, "remove_from_watchlist", self)

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def is_in_watchlist(self, ts_code: str) -> bool:
        """检查是否已关注 (不更新 state，直接返回 bool)."""
        try:
            return await self.cache.is_in_watchlist(ts_code)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("[WatchlistVM] is_in_watchlist error: %s", DataSanitizer.sanitize_error(e), exc_info=True)
            return False

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def search_stocks(self, keyword: str) -> None:
        """按代码/名称搜索上市股票，结果写入 state.search_results（添加关注对话框）。

        keyword 为空/全空白时清空搜索结果，不发起查询。
        """
        keyword = (keyword or "").strip()
        if not keyword:
            await self.clear_search()
            return
        self._set_state(is_searching=True, search_error=None)
        try:
            df = await self.cache.search_stocks(keyword)
            rows = _df_to_stock_search_rows(df)
            self._set_state(search_results=rows, search_keyword=keyword, is_searching=False)
        except asyncio.CancelledError:
            self._set_state(is_searching=False)
            raise
        except Exception as e:
            _handle_error(e, "search_stocks", self, search=True)

    async def clear_search(self) -> None:
        """清空搜索状态（添加关注对话框关闭时调用）。"""
        self._set_state(
            search_results=(),
            search_keyword="",
            is_searching=False,
            search_error=None,
        )


# ============================================================
# 纯转换函数 (DataFrame → tuple[WatchlistRow, ...] / tuple[StockSearchRow, ...])
# 模块级, 无副作用, 可独立测试
# ============================================================


def _df_to_watchlist_rows(df: pd.DataFrame | None) -> tuple[WatchlistRow, ...]:
    """DataFrame → tuple[WatchlistRow, ...] (L771 合规).

    fillna("") 统一处理 None/NaN → "" (pandas to_dict 将 None 转为 NaN).
    """
    if df is None or df.empty:
        return ()
    df = df.fillna("")
    return tuple(
        WatchlistRow(
            ts_code=str(row.get("ts_code", "") or ""),
            stock_name=str(row.get("stock_name", "") or ""),
            added_at=str(row.get("added_at", "") or ""),
            note=str(row.get("note", "") or ""),
        )
        for row in df.to_dict("records")
    )


def _df_to_stock_search_rows(df: pd.DataFrame | None) -> tuple[StockSearchRow, ...]:
    """DataFrame → tuple[StockSearchRow, ...] (L771 合规).

    fillna("") 统一处理 None/NaN → "" (pandas to_dict 将 None 转为 NaN).
    """
    if df is None or df.empty:
        return ()
    df = df.fillna("")
    return tuple(
        StockSearchRow(
            ts_code=str(row.get("ts_code", "") or ""),
            name=str(row.get("name", "") or ""),
        )
        for row in df.to_dict("records")
    )


def _handle_error(e: Exception, op: str, vm: WatchlistViewModel, *, search: bool = False) -> None:
    """统一错误处理: classify_error + Message + 日志 (对齐 DataExplorerViewModel).

    search=True 时错误写入 ``search_error``（添加关注对话框独立展示，不影响列表加载）；
    否则写入 ``load_error`` + ``load_error_detail``。
    """
    error_info = classify_error(e, context="db")
    severity = classify_severity(e, context="db")
    sanitized = DataSanitizer.sanitize_error(e)
    if severity == "system":
        logger.critical("[WatchlistVM] SYSTEM-LEVEL failure in %s: %s", op, sanitized, exc_info=True)
    elif severity == "recoverable":
        logger.warning(
            "[WatchlistVM] Recoverable error (%s) in %s: %s",
            error_info["code"],
            op,
            sanitized,
            exc_info=True,
        )
    else:
        logger.error("[WatchlistVM] Operational error in %s: %s", op, sanitized, exc_info=True)
    message = Message(
        error_info.get("message_key", "common_err_unknown"),
        error_info.get("format_args") or {},
    )
    if search:
        vm._set_state(is_searching=False, search_error=message)
    else:
        vm._set_state(is_loading=False, load_error=message, load_error_detail=sanitized)
