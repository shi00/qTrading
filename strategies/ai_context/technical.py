"""AI 技术面上下文渲染器（review01-A5a 自 ai_mixin 移出）。"""

from __future__ import annotations

import logging

import pandas as pd

from core.i18n import I18n
from utils.error_classifier import classify_severity
from utils.technical_analysis import TechnicalAnalysis

logger = logging.getLogger(__name__)


def _compute_technical_structure(history_df, vol_ratio_threshold: float = 1.5) -> dict:
    """
    Compute MA alignment and volume trend from history DataFrame.
    Returns a dict of human-readable technical structure signals.
    """
    result = {}
    if history_df is None or history_df.empty or len(history_df) < 5:
        result["ma_alignment"] = I18n.get("ai_data_insufficient")
        result["volume_trend"] = I18n.get("ai_data_insufficient")
        result["price_trend_5d"] = I18n.get("ai_data_insufficient")
        return result

    try:
        # D11: Apply Forward Adjusted Prices (QFQ) to avoid split/dividend gaps fooling the AI
        df_qfq = TechnicalAnalysis._get_qfq_df(history_df)
        df = df_qfq.sort_values("trade_date", ascending=True).copy()  # type: ignore[union-attr]
        close = df["close"]

        # MA Alignment
        ma5 = close.rolling(5).mean().iloc[-1] if len(close) >= 5 else None
        ma10 = close.rolling(10).mean().iloc[-1] if len(close) >= 10 else None
        ma20 = close.rolling(20).mean().iloc[-1] if len(close) >= 20 else None
        current_price = close.iloc[-1]

        # F4-ST-003: NaN 防御 — MA 值为 NaN 时降级为 i18n 占位，避免输出 "nan"
        if (
            ma5 is not None
            and ma10 is not None
            and ma20 is not None
            and not pd.isna(ma5)
            and not pd.isna(ma10)
            and not pd.isna(ma20)
        ):
            if ma5 > ma10 > ma20:
                result["ma_alignment"] = (
                    f"{I18n.get('ai_ma_bullish')} (MA5={ma5:.2f} > MA10={ma10:.2f} > MA20={ma20:.2f})"
                )
            elif ma5 < ma10 < ma20:
                result["ma_alignment"] = (
                    f"{I18n.get('ai_ma_bearish')} (MA5={ma5:.2f} < MA10={ma10:.2f} < MA20={ma20:.2f})"
                )
            else:
                result["ma_alignment"] = (
                    f"{I18n.get('ai_ma_crossing')} (MA5={ma5:.2f}, MA10={ma10:.2f}, MA20={ma20:.2f})"
                )

            if ma20 != 0 and not pd.isna(ma20) and not pd.isna(current_price):
                deviation = ((current_price - ma20) / ma20) * 100
                result["price_vs_ma20"] = f"{I18n.get('ai_ma20_deviation')} {deviation:+.1f}%"
            else:
                result["price_vs_ma20"] = I18n.get("ai_ma20_zero")
        else:
            result["ma_alignment"] = I18n.get("ai_ma_insufficient")

        # Volume Trend (last 5 days)
        if "vol" in df.columns and len(df) >= 10:
            vol_5d = df["vol"].tail(5).mean()
            vol_10d = df["vol"].tail(10).mean()
            if vol_10d > 0:
                vol_ratio = vol_5d / vol_10d
                if vol_ratio < 0.7:
                    result["volume_trend"] = (
                        f"{I18n.get('ai_vol_shrink')} ({I18n.get('ai_5d_10d_ratio')}: {vol_ratio:.2f})"
                    )
                elif vol_ratio > vol_ratio_threshold:
                    result["volume_trend"] = (
                        f"{I18n.get('ai_vol_expand')} ({I18n.get('ai_5d_10d_ratio')}: {vol_ratio:.2f})"
                    )
                else:
                    result["volume_trend"] = (
                        f"{I18n.get('ai_vol_stable')} ({I18n.get('ai_5d_10d_ratio')}: {vol_ratio:.2f})"
                    )
            else:
                result["volume_trend"] = I18n.get("ai_vol_no_data")
        else:
            result["volume_trend"] = I18n.get("ai_data_insufficient")

        # 5-day Price Trend
        if len(df) >= 5:
            price_5d_ago = close.iloc[-5]
            # F4-ST-003: NaN 防御，避免输出 "nan%" 到 AI prompt
            if price_5d_ago != 0 and not pd.isna(price_5d_ago) and not pd.isna(current_price):
                pct_5d = ((current_price - price_5d_ago) / price_5d_ago) * 100
            else:
                pct_5d = 0.0
            # F4-ST-003: 过滤 NaN closes 避免 "nan" 出现在 close 序列
            closes_5d = ", ".join([f"{c:.2f}" for c in close.tail(5).tolist() if not pd.isna(c)])
            result["price_trend_5d"] = (
                f"{I18n.get('ai_price_trend_5d')} {pct_5d:+.1f}% ({I18n.get('ai_close_series')}: {closes_5d})"
            )
        else:
            result["price_trend_5d"] = I18n.get("ai_data_insufficient")

    # NOTE(lazy): 技术结构计算容错（单股票单次计算失败不影响整体 AI 分析）.
    #   ceiling: 单股票单次计算失败，result 字段降级为 i18n 错误占位.
    #   upgrade: 该方法被 ≥3 处调用方依赖，或单日失败影响 ≥10 只股票时，
    #           升级为按异常类型分类的 fail-fast 或重试机制.
    except Exception as e:
        severity = classify_severity(e)
        if severity == "system":
            logger.error(
                "[ai_context] Technical structure computation system error: %s",
                e,
                exc_info=True,
            )
        else:
            logger.warning(
                "[ai_context] Technical structure computation failed (transient): %s",
                e,
            )
        result["ma_alignment"] = I18n.get("ai_calc_error")
        result["volume_trend"] = I18n.get("ai_calc_error")
        result["price_trend_5d"] = I18n.get("ai_calc_error")

    return result


def _get_limit_pct(ts_code: str, name: str = "") -> float:
    """
    根据股票代码和名称判断涨跌停幅度。

    规则：
    - ST/*ST 股：±5%
    - 北交所 (8开头)：±30%
    - 创业板 (3开头) / 科创板 (68开头)：±20%
    - 主板 (其他)：±10%
    """
    if name and ("ST" in name.upper()):
        return 5.0
    if ts_code.startswith("8"):
        return 30.0
    if ts_code.startswith("3") or ts_code.startswith("68"):
        return 20.0
    return 10.0
