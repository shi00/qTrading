"""AI 历史行情上下文渲染器（review01-A5a 自 ai_mixin 移出）。"""

from __future__ import annotations

import logging

import pandas as pd

from core.i18n import I18n
from strategies.ai_context.technical import _get_limit_pct
from utils.sanitizers import DataSanitizer
from utils.technical_analysis import TechnicalAnalysis

logger = logging.getLogger(__name__)


def _build_history_text(
    history_df: pd.DataFrame,
    ts_code: str = "",
    stock_name: str = "",
    vol_ratio_threshold: float = 1.5,
    labels_out: list[str] | None = None,
) -> str:
    """
    Build a semantic summary of recent price action using quantitative factor extraction.
    This provides the LLM with "vision" into the actual OHLCV structure.

    NOTE: Output intentionally excludes XML wrapper tags because the caller
          (ai_service.py) already wraps this in <recent_price_action>.

    Args:
        labels_out: 输出参数，收集成功注入的标签 key；哨兵/异常时不注册
    """
    if history_df is None or history_df.empty:
        return I18n.get("ai_history_insufficient")

    try:
        # D11: Apply Forward Adjusted Prices (QFQ) to avoid split/dividend gaps fooling the AI
        df_qfq = TechnicalAnalysis._get_qfq_df(history_df)
        # Ensure chronological order
        df = df_qfq.sort_values("trade_date", ascending=True).reset_index(drop=True)  # type: ignore[union-attr]

        # Compute Macro Horizon
        macro_cagr = "N/A"
        macro_mdd = "N/A"
        if len(df) > 60:
            # Compute long-term CAGR and Max Drawdown on `df`
            first_close_macro = df["close"].iloc[0]
            if first_close_macro > 0:
                macro_cagr = f"{((df['close'].iloc[-1] / first_close_macro) - 1) * 100:.1f}%"
            roll_max = df["close"].cummax()
            drawdown = (df["close"] - roll_max) / roll_max
            macro_mdd = f"{drawdown.min() * 100:.1f}%"

            # Slice for short-term K-line context
            df = df.tail(60).reset_index(drop=True)

        if len(df) < 5:
            # 哨兵：数据不足，不注册标签
            return I18n.get("ai_history_insufficient")

        # 1. Extract Base Series
        close = df["close"]

        # 全 NaN close → 无有效价格数据，返回哨兵不注册标签
        if close.isna().all():
            return I18n.get("ai_history_insufficient")
        has_vol = "vol" in df.columns
        has_pct_chg = "pct_chg" in df.columns

        # 2. Trend & Swing Factors (with division-by-zero guards)
        first_close = close.iloc[0]
        fifth_ago_close = close.iloc[-5]
        pct_all = ((close.iloc[-1] / first_close) - 1) * 100 if first_close > 0 else 0.0
        pct_5d = ((close.iloc[-1] / fifth_ago_close) - 1) * 100 if fifth_ago_close > 0 else 0.0

        # 20-Day MA Bias
        bias_str = "N/A (insufficient data)"
        if len(df) >= 20:
            ma20 = close.tail(20).mean()
            if ma20 > 0:
                bias = ((close.iloc[-1] - ma20) / ma20) * 100
                bias_str = f"{bias:+.2f}%"

        # Consecutive streaks (with NaN guard)
        consec_str = "N/A"
        if has_pct_chg:
            last_pct = df["pct_chg"].iloc[-1]
            # Guard against NaN
            if pd.isna(last_pct):
                last_pct = 0.0
            sign_last = 1 if last_pct > 0 else -1 if last_pct < 0 else 0
            consec_days = 0
            if sign_last != 0:
                for p in reversed(df["pct_chg"].tolist()):
                    if pd.isna(p):
                        break
                    if (p > 0 and sign_last > 0) or (p < 0 and sign_last < 0):
                        consec_days += 1
                    else:
                        break
            consec_str = (
                f"{I18n.get('ai_consecutive_up') if sign_last > 0 else I18n.get('ai_consecutive_down')} {consec_days} {I18n.get('ai_day_unit')}"
                if consec_days > 1
                else I18n.get("ai_sideways")
            )

        # 3. Drawdown Factor
        rolling_max = close.cummax()
        drawdowns = (close - rolling_max) / rolling_max
        mdd = drawdowns.min() * 100

        # 4. Volume Factor (graceful fallback if 'vol' column is missing)
        vol_line = f"- {I18n.get('ai_vol_unavailable')}"
        if has_vol:
            vol = df["vol"]
            vol_5d_avg = vol.tail(5).mean()
            vol_older_avg = vol.iloc[:-5].mean() if len(df) > 5 else 0.0
            # Guard NaN from mean of NaN-containing series
            if pd.isna(vol_5d_avg):
                vol_5d_avg = 0.0
            if pd.isna(vol_older_avg):
                vol_older_avg = 0.0
            vol_ratio_5d = vol_5d_avg / vol_older_avg if vol_older_avg > 0 else 1.0
            vol_desc = (
                I18n.get("ai_vol_significant_expand")
                if vol_ratio_5d > vol_ratio_threshold
                else I18n.get("ai_vol_significant_shrink")
                if vol_ratio_5d < 0.7
                else I18n.get("ai_vol_stable")
            )
            vol_line = I18n.get(
                "ai_vol_line_format",
                label=I18n.get("ai_vol_status_label"),
                desc=vol_desc,
                baseline=I18n.get("ai_vol_relative_base"),
                ratio_label=I18n.get("ai_vol_ratio_label"),
                ratio=f"{vol_ratio_5d:.2f}",
            )

        limit_pct = _get_limit_pct(ts_code, stock_name)

        lines = [
            I18n.get(
                "ai_macro_cycle_header", title=I18n.get("ai_macro_cycle"), baseline=I18n.get("ai_config_baseline")
            ),
            f"- {I18n.get('ai_long_term')}: {I18n.get('ai_total_return')} {macro_cagr}，{I18n.get('ai_max_drawdown')} {macro_mdd}。",
            "",
            I18n.get(
                "ai_trend_vol_header",
                title=I18n.get("ai_trend_volatility"),
                days=len(df),
                unit=I18n.get("ai_trading_days"),
            ),
            f"- {I18n.get('ai_volatility')}: {I18n.get('ai_total_return')} {pct_all:+.2f}%，{I18n.get('ai_max_drawdown')} {mdd:.2f}%。",
            f"- {I18n.get('ai_short_momentum')}: {I18n.get('ai_5d_return')} {pct_5d:+.2f}%，{I18n.get('ai_current')} {consec_str}。",
            f"- {I18n.get('ai_ma20_bias')}: {bias_str}。",
            "",
            I18n.get("ai_section_wrapper", title=I18n.get("ai_volume_price")),
            vol_line,
            "",
            I18n.get("ai_section_wrapper", title=I18n.get("ai_recent_3d_kline")),
            I18n.get("ai_kline_header"),
        ]

        import datetime

        for r in df.tail(3).to_dict("records"):
            td = r.get("trade_date")
            if isinstance(td, (datetime.date, datetime.datetime)):
                d = td.strftime("%m%d")
            else:
                d = str(td or "")[-4:]
            c = f"{r.get('close', 0):.2f}"
            p_val = r.get("pct_chg", 0)
            p = f"{p_val:+.2f}%" if not pd.isna(p_val) else "N/A"
            v_val = r.get("vol", 0)
            v = f"{v_val:.0f}" if (has_vol and not pd.isna(v_val)) else "N/A"

            limit_tag = ""
            if not pd.isna(p_val):
                if p_val >= limit_pct - 0.5:
                    limit_tag = f" 🔴{I18n.get('ai_limit_up')}"
                elif p_val <= -(limit_pct - 0.5):
                    limit_tag = f" 🟢{I18n.get('ai_limit_down')}"

            lines.append(f"{d} | {c} | {p}{limit_tag} | {v}")

        # 实际数据产出，注册标签
        if labels_out is not None:
            labels_out.append("ai_label_kline")

        return "\n".join(lines)

    # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 该 try 块抛出历史文本构建异常. upgrade: 策略层重构时统一走 classify_error.
    except Exception as e:
        logger.warning("[ai_context] Failed to build history text: %s", DataSanitizer.sanitize_error(e))
        if labels_out is not None:
            labels_out.clear()
        return I18n.get("ai_history_extract_error")
