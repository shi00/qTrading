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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Literal

import pandas as pd

from data.cache.cache_manager import CacheManager
from services.stock_detail_service import StockDetailService, StockQuote
from ui.viewmodels import Message
from ui.viewmodels.observable_mixin import ObservableViewModelMixin
from utils.error_classifier import classify_error, classify_severity
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WatchlistRow:
    """关注列表行数据 (L771 合规: frozen dataclass).

    行情列（``latest_close`` / ``pct_chg`` / ``ai_score``）缺失一律为 ``None``，
    由展示层渲染「—」（R21：不得用 0 冒充缺失）。
    """

    ts_code: str = ""
    stock_name: str = ""
    added_at: str = ""
    note: str = ""
    latest_close: float | None = None
    pct_chg: float | None = None
    ai_score: float | None = None


@dataclass(frozen=True)
class StockSearchRow:
    """「添加关注」搜索结果行数据 (L771 合规: frozen dataclass)."""

    ts_code: str = ""
    name: str = ""


@dataclass(frozen=True)
class WatchlistMutationResult:
    """自选股增删结果（F01 局部返回类型，不扩展为全系统 Result 框架）。

    ``status`` 语义（三态区分「写入」与「刷新」两类事实）：
    - ``failed``：持久化写入本身失败，列表未刷新，**不得**提示成功；
    - ``applied``：写入成功且列表刷新成功；
    - ``applied_refresh_failed``：写入成功但列表刷新失败（数据已落库，需提示手动刷新）。

    ``message`` 仅在 ``failed`` 时给出，只含 i18n key 与安全参数，不携带原始 DB 异常字符串。
    ``CancelledError`` 不编码为上述状态，仍向上抛出（R2）。
    """

    status: Literal["failed", "applied", "applied_refresh_failed"]
    message: Message | None = None


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
    # UX-09 MAJOR-04：按 ts_code 打开的个股详情数据（不可变映射）；None 表示当前无详情。
    detail_stock_data: Mapping[str, Any] | None = None


class WatchlistViewModel(ObservableViewModelMixin[WatchlistState]):
    """关注列表 ViewModel (V1 声明式范式).

    职责：
    1. 管理关注列表状态 (frozen WatchlistState snapshot)
    2. 调用 CacheManager 代理方法 add/remove/get/is_in
    3. add/remove 后自动刷新列表
    """

    def __init__(self, cache: CacheManager | None = None):
        self.cache = cache or CacheManager()  # noqa: R16 - 持有注册单例引用（幂等工厂，DI 注入位）
        self.detail_service = StockDetailService(self.cache)
        self._state: WatchlistState = WatchlistState()
        # F03: 搜索请求代次；仅当前代次可写 state（隔离乱序响应/关闭回填）。
        self._search_generation: int = 0
        self._init_mixin_fields()

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def load_watchlist(self) -> bool:
        """加载关注列表 (从 DB 读取并转换为 tuple[WatchlistRow, ...]).

        UX-09 MAJOR-04：加载行后按 ts_code 批量补最新价 / 涨跌幅 / AI 评分；
        行情查询失败降级为「无行情」（对应列显示「—」），不影响列表本身加载。

        Returns:
            成功加载（含空表）返回 True；已分类的普通异常返回 False。
            ``CancelledError`` 清理 loading 后重新抛出（R2），不编码为返回值。
        """
        self._set_state(is_loading=True, load_error=None, load_error_detail=None)
        try:
            df = await self.cache.get_watchlist()
            rows = _df_to_watchlist_rows(df)
            quotes = await self.detail_service.load_watchlist_quotes([r.ts_code for r in rows])
            self._set_state(watchlist_rows=_merge_quotes(rows, quotes), is_loading=False)
            return True
        except asyncio.CancelledError:
            self._set_state(is_loading=False)
            raise
        except Exception as e:
            _handle_error(e, "load_watchlist", self)
            return False

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def add_to_watchlist(
        self,
        ts_code: str,
        stock_name: str,
        note: str | None = None,
    ) -> WatchlistMutationResult:
        """加入关注并刷新列表（F01：写失败不刷新、不伪装成功）。

        Returns:
            写入失败 → ``failed``（不刷新）；写入成功且刷新成功 → ``applied``；
            写入成功但刷新失败 → ``applied_refresh_failed``。
        """
        try:
            await self.cache.add_to_watchlist(ts_code, stock_name, note)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            message = _handle_error(e, "add_to_watchlist", self, write_state=False)
            return WatchlistMutationResult("failed", message)
        refreshed = await self.load_watchlist()
        return WatchlistMutationResult("applied" if refreshed else "applied_refresh_failed")

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def remove_from_watchlist(self, ts_code: str) -> WatchlistMutationResult:
        """移除关注并刷新列表（F01：写失败不刷新、不伪装成功）。"""
        try:
            await self.cache.remove_from_watchlist(ts_code)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            message = _handle_error(e, "remove_from_watchlist", self, write_state=False)
            return WatchlistMutationResult("failed", message)
        refreshed = await self.load_watchlist()
        return WatchlistMutationResult("applied" if refreshed else "applied_refresh_failed")

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

        F03：以实例字段 ``_search_generation`` 做请求代次隔离——只有与当前代次
        相等的请求（含成功/异常/finally 路径）才允许写 state；旧代次响应一律丢弃，
        避免乱序响应覆盖新结果或关闭后回填。
        """
        keyword = (keyword or "").strip()
        if not keyword:
            await self.clear_search()
            return
        # 同一标准化 keyword 正在查询时忽略重复请求（最新请求优先，减少无收益重复查询）。
        if keyword == self._state.search_keyword and self._state.is_searching:
            return
        self._search_generation += 1
        generation = self._search_generation
        self._set_state(
            search_results=(),
            search_keyword=keyword,
            is_searching=True,
            search_error=None,
        )
        try:
            df = await self.cache.search_stocks(keyword)
            if generation != self._search_generation:
                return  # 旧代次：不得覆盖当前候选/错误/loading
            rows = _df_to_stock_search_rows(df)
            self._set_state(search_results=rows, search_keyword=keyword, is_searching=False)
        except asyncio.CancelledError:
            if generation == self._search_generation:
                self._set_state(is_searching=False)
            raise
        except Exception as e:
            if generation == self._search_generation:
                _handle_error(e, "search_stocks", self, search=True)
            else:
                # 旧代次异常只按规范脱敏记录，不覆盖新请求的错误区
                _handle_error(e, "search_stocks", self, search=True, write_state=False)

    async def clear_search(self) -> None:
        """清空搜索状态（添加关注对话框关闭时调用）。

        F03：先推进代次使所有在途请求失效，避免关闭后旧响应回填。
        """
        self._search_generation += 1
        self._set_state(
            search_results=(),
            search_keyword="",
            is_searching=False,
            search_error=None,
        )

    def dispose(self) -> None:
        """清理资源（F03：dispose 使在途搜索代次失效，禁止卸载后回填旧结果）。"""
        self._search_generation += 1
        super().dispose()

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def open_stock_detail(self, ts_code: str) -> bool:
        """按 ts_code 打开个股详情（UX-09 MAJOR-04：不依赖当前选股结果集）。

        取数成功后写入 ``state.detail_stock_data``（不可变映射）并返回 True；
        取数失败或 ts_code 为空返回 False（不写 ``load_error``——避免列表被
        ErrorState 整体替换）。``name`` 缺失时以自选行内股票名回退。
        """
        code = (ts_code or "").strip()
        if not code:
            return False
        fallback_name = next((r.stock_name for r in self._state.watchlist_rows if r.ts_code == code), "")
        try:
            data = await self.detail_service.load_stock_detail(code, fallback_name=fallback_name)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(
                "[WatchlistVM] open_stock_detail failed: %s",
                DataSanitizer.sanitize_error(e),
                exc_info=True,
            )
            return False
        if data is None:
            return False
        self._set_state(detail_stock_data=MappingProxyType(dict(data)))
        return True

    def close_stock_detail(self) -> None:
        """关闭个股详情（清空 ``state.detail_stock_data``，使对话框从控件树移除）。"""
        self._set_state(detail_stock_data=None)


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


def _merge_quotes(
    rows: Sequence[WatchlistRow],
    quotes: Mapping[str, StockQuote],
) -> tuple[WatchlistRow, ...]:
    """把按 ts_code 的行情快照合并进关注行（无行情/无 AI 评分保持 ``None``，R21）。"""
    merged: list[WatchlistRow] = []
    for row in rows:
        quote = quotes.get(row.ts_code)
        if quote is None:
            merged.append(row)
            continue
        merged.append(
            replace(
                row,
                latest_close=quote.latest_close,
                pct_chg=quote.pct_chg,
                ai_score=quote.ai_score,
            )
        )
    return tuple(merged)


def _handle_error(
    e: Exception,
    op: str,
    vm: WatchlistViewModel,
    *,
    search: bool = False,
    write_state: bool = True,
) -> Message:
    """统一错误处理: classify_error + Message + 日志 (对齐 DataExplorerViewModel).

    search=True 时错误写入 ``search_error``（添加关注对话框独立展示，不影响列表加载）；
    否则写入 ``load_error`` + ``load_error_detail``。

    ``write_state=False`` 时只做分类/脱敏/日志并返回 Message（供 F01 写操作失败分支：
    写失败不得污染列表 error 通道，避免空列表被 ErrorState 整体替换），不写 state。

    Returns:
        构建好的 i18n ``Message``（仅含 key 与安全参数，不含原始异常字符串）。
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
    if write_state:
        if search:
            vm._set_state(is_searching=False, search_error=message)
        else:
            vm._set_state(is_loading=False, load_error=message, load_error_detail=sanitized)
    return message
