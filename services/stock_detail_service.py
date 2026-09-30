"""StockDetailService — 按 ts_code 提供个股详情与关注列表行情（UX-09 MAJOR-04）。

背景：原「查看个股」只是把 ts_code 发到选股页做**当前选股结果**内过滤
（``ui/viewmodels/pagination_sorting_mixin._get_filtered_results`` 过滤 ``_full_results``），
在「未运行任何策略」时必然落到空表，用户看不到任何行情。本服务把个股详情从
「选股页私有组件」提升为**按代码全局可达**的数据通路：只要给定 ts_code，即可
从既有 DAO/服务层取到详情与行情，不依赖选股结果集是否存在。

分层（R1：ui → services → data）：本服务位于 services 层，仅经 ``CacheManager``
代理调用 DAO 的批量查询方法，UI 层不得直接访问数据库。

R21（缺失值不伪装）：所有缺失字段以 ``None`` 表达，由展示层渲染为「—」；
禁止用 ``0`` 分 / ``0%`` 等业务上合法的具体值伪造缺失。
"""

from __future__ import annotations

import asyncio
import logging
import math
import typing
from dataclasses import dataclass

import pandas as pd

from data.cache.cache_manager import CacheManager
from utils.error_classifier import classify_error, classify_severity
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StockQuote:
    """关注列表单行行情快照（缺失一律 ``None``，R21 不得填 0 冒充）。"""

    latest_close: float | None = None
    pct_chg: float | None = None
    ai_score: float | None = None


def _to_float(val: typing.Any) -> float | None:
    """归一为有限 float；``None`` / NaN / 非数值 / bool → ``None``（R21）。"""
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, float):
        return None if math.isnan(val) else val
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _normalize_cell(val: typing.Any) -> typing.Any:
    """把 DataFrame 单元格归一：``None``/NaN/NaT/空串 → ``None``，其余原样返回。"""
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(val, str) and not val.strip():
        return None
    return val


def _some_row_dict(df: pd.DataFrame | None) -> dict[str, typing.Any]:
    """取 DataFrame 首行（若存在）为 dict，逐列 ``_normalize_cell``；空表返回 ``{}``。"""
    if df is None or df.empty:
        return {}
    row = df.iloc[0]
    return {str(k): _normalize_cell(v) for k, v in row.items()}


def _index_rows_by_ts_code(df: pd.DataFrame | None) -> dict[str, dict[str, typing.Any]]:
    """把 DataFrame 按 ``ts_code`` 列索引为 ``{ts_code: row_dict}``；缺列/空表返回 ``{}``。"""
    if df is None or df.empty or "ts_code" not in df.columns:
        return {}
    return {
        str(row.get("ts_code")): {str(k): _normalize_cell(v) for k, v in row.items()}
        for row in df.to_dict("records")
        if row.get("ts_code")
    }


def _log_degraded(e: Exception, op: str) -> None:
    """外部 IO 降级统一分类 + 脱敏日志（跨层降级场景，须经 classify_* 分类）。"""
    error_info = classify_error(e, context="db")
    severity = classify_severity(e, context="db")
    sanitized = DataSanitizer.sanitize_error(e)
    if severity == "system":
        logger.critical("[StockDetailService] SYSTEM-LEVEL failure in %s: %s", op, sanitized, exc_info=True)
    else:
        logger.warning(
            "[StockDetailService] Degraded (%s, severity=%s) in %s: %s",
            error_info["code"],
            severity,
            op,
            sanitized,
            exc_info=True,
        )


class StockDetailService:
    """按 ts_code 提供个股详情与关注列表行情（非单例：每次消费方注入 CacheManager）。

    Args:
        cache: CacheManager 实例（DI 注入位）；缺省取模块单例。
    """

    def __init__(self, cache: CacheManager | None = None):
        self.cache = cache or CacheManager()

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def load_watchlist_quotes(self, ts_codes: typing.Sequence[str]) -> dict[str, StockQuote]:
        """批量取关注列表各代码的最新价 / 涨跌幅 / AI 评分（一次批量查询，无 N+1）。

        返回 ``{ts_code: StockQuote}``；无行情/AI 记录的代码对应字段为 ``None``
        （由展示层渲染「—」）。行情查询失败时降级为「无行情」（仅告警日志，
        不整体失败），避免因单次查询失败导致整个关注列表不可用。
        """
        codes = [str(c) for c in (ts_codes or []) if c]
        if not codes:
            return {}
        try:
            quotes_df = await self.cache.get_latest_quotes_bulk(codes)
            ai_df = await self.cache.get_latest_ai_reviews_bulk(codes)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _log_degraded(e, "load_watchlist_quotes")
            return {}

        quote_map = _index_rows_by_ts_code(quotes_df)
        ai_map = _index_rows_by_ts_code(ai_df)
        result: dict[str, StockQuote] = {}
        for code in codes:
            quote = quote_map.get(code, {})
            ai = ai_map.get(code, {})
            result[code] = StockQuote(
                latest_close=_to_float(quote.get("close")),
                pct_chg=_to_float(quote.get("pct_chg")),
                ai_score=_to_float(ai.get("ai_score")),
            )
        return result

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def load_stock_detail(self, ts_code: str, fallback_name: str = "") -> dict[str, typing.Any] | None:
        """按 ts_code 组装个股详情字典（供 ``StockDetailDialog`` 直接渲染）。

        不依赖选股结果集：依次取行情 / 指标 / 财报 / 基础信息 / AI 评分（各传
        ``[ts_code]``，均按代码批量查询），合并为单一字典。``name`` 缺失时回退
        ``fallback_name``（调用方持有的股票名），保证对话框标题为该股名称。

        返回 ``None`` 表示取数失败（无法组装详情）；``ts_code`` 为空亦返回 ``None``。
        """
        code = (ts_code or "").strip()
        if not code:
            return None
        codes = [code]
        try:
            quotes_df = await self.cache.get_latest_quotes_bulk(codes)
            indicators_df = await self.cache.get_latest_indicators_bulk(codes)
            financials_df = await self.cache.get_latest_financials_bulk(codes)
            basic_df = await self.cache.get_stock_basic_bulk(codes)
            ai_df = await self.cache.get_latest_ai_reviews_bulk(codes)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _log_degraded(e, "load_stock_detail")
            return None

        data: dict[str, typing.Any] = {"ts_code": code}
        for df in (quotes_df, indicators_df, financials_df, basic_df, ai_df):
            data.update(_some_row_dict(df))
        if not data.get("name"):
            data["name"] = fallback_name or code
        return data

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def get_stock_history(self, ts_code: str, days: int = 365):
        """取该股最近 ``days`` 个交易日的行情（K 线数据源，与
        ``DataProcessor.get_stock_history`` 同签名同口径）。

        使本服务可直接作为 ``StockDetailDialog`` 的 ``data_processor`` 参数（duck-typed），
        从而无需构造 TushareClient / DataProcessor 即可显示 K 线；无记录时返回空表。
        """
        return await self.cache.get_recent_quotes(ts_code, days)
