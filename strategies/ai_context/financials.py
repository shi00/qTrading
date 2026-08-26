"""AI 财务上下文渲染器（review01-A5a 自 ai_mixin 移出）。

含多期财务趋势、业绩预告/质押/解禁/增减持/快报格式化、估值摘要。
"""

from __future__ import annotations

import logging
import typing
from datetime import date, datetime

import pandas as pd

from core.i18n import I18n
from strategies.utils import fmt_val, safe_float
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)


@log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
async def _build_multi_period_financials(
    ts_code: str,
    cache: typing.Any,
    prefetched: dict | None = None,
    as_of_date: str | None = None,
    labels_out: list[str] | None = None,
) -> tuple[str, bool]:
    """
    构建多期财务趋势数据。

    获取最近8个季度的财务数据，分析ROE、毛利率、营收/利润增速趋势。

    Args:
        ts_code: 股票代码
        cache: 数据缓存实例
        prefetched: 预取的辅助数据
        as_of_date: 截止日期（含），None 表示不限制，防止前视偏差
        labels_out: 输出参数，收集成功注入的标签 key

    Returns:
        (财务趋势文本, is_valid)：is_valid=False 表示数据不足/失败，调用方应跳过注入。
    """

    try:
        if prefetched and ts_code in prefetched and "financial_history" in prefetched[ts_code]:
            df = prefetched[ts_code]["financial_history"]
        else:
            df = await cache.get_financial_reports_history(ts_code, periods=8, as_of_date=as_of_date)

        if df is None or df.empty:
            return ("", False)

        parts = []

        if "roe" in df.columns:
            roe_values = df["roe"].dropna().tolist()
            if roe_values:
                roe_str = ", ".join([f"{v:.2f}" for v in roe_values[:4]])
                parts.append(
                    I18n.get(
                        "ai_roe_trend_format",
                        label=I18n.get("ai_roe_trend"),
                        count=len(roe_values),
                        unit=I18n.get("ai_recent_quarters"),
                        values=roe_str,
                    )
                )
                if labels_out is not None:
                    labels_out.append("ai_label_roe_trend")

        if "grossprofit_margin" in df.columns:
            margin_values = df["grossprofit_margin"].dropna().tolist()
            if margin_values:
                margin_str = ", ".join([f"{v:.2f}" for v in margin_values[:4]])
                parts.append(f"{I18n.get('ai_gross_margin_trend')}: {margin_str}")
                if labels_out is not None:
                    labels_out.append("ai_label_gross_margin_trend")

        if "or_yoy" in df.columns:
            or_yoy_values = df["or_yoy"].dropna().tolist()
            if or_yoy_values:
                or_yoy_str = ", ".join([f"{v:.2f}" for v in or_yoy_values[:4]])
                parts.append(f"{I18n.get('ai_revenue_growth_trend')}: {or_yoy_str}")
                if labels_out is not None:
                    labels_out.append("ai_label_revenue_growth_trend")

        if "netprofit_yoy" in df.columns:
            profit_yoy_values = df["netprofit_yoy"].dropna().tolist()
            if profit_yoy_values:
                profit_yoy_str = ", ".join([f"{v:.2f}" for v in profit_yoy_values[:4]])
                parts.append(f"{I18n.get('ai_profit_growth_trend')}: {profit_yoy_str}")
                if labels_out is not None:
                    labels_out.append("ai_label_profit_growth_trend")

        if "n_cashflow_act" in df.columns and "n_income_attr_p" in df.columns:
            cf_values = df["n_cashflow_act"].dropna().tolist()
            profit_values = df["n_income_attr_p"].dropna().tolist()
            if cf_values and profit_values:
                latest_cf = cf_values[0] if cf_values else 0
                latest_profit = profit_values[0] if profit_values else 0
                if latest_profit > 0:
                    cf_ratio = latest_cf / latest_profit
                    parts.append(f"{I18n.get('ai_cf_profit_ratio')}: {cf_ratio:.2f}")
                    if labels_out is not None:
                        labels_out.append("ai_label_cf_profit_ratio")

        if "total_assets" in df.columns and "goodwill" in df.columns:
            ta_values = df["total_assets"].dropna().tolist()
            gw_values = df["goodwill"].dropna().tolist()
            if ta_values and gw_values and ta_values[0] and ta_values[0] > 0:
                gw_ratio = (gw_values[0] / ta_values[0]) * 100
                parts.append(f"{I18n.get('ai_goodwill_ratio')}: {gw_ratio:.2f}%")
                if labels_out is not None:
                    labels_out.append("ai_label_goodwill_ratio")

        if "money_cap" in df.columns:
            mc_values = df["money_cap"].dropna().tolist()
            if mc_values:
                parts.append(
                    f"{I18n.get('ai_monetary_capital')}: {mc_values[0] / 1e8:.2f}{I18n.get('ai_unit_billion')}"
                )
                if labels_out is not None:
                    labels_out.append("ai_label_monetary_capital")

        if "accounts_receiv" in df.columns:
            ar_values = df["accounts_receiv"].dropna().tolist()
            if ar_values:
                parts.append(f"{I18n.get('ai_accounts_receiv')}: {ar_values[0] / 1e8:.2f}{I18n.get('ai_unit_billion')}")
                if labels_out is not None:
                    labels_out.append("ai_label_accounts_receiv")

        return ("\n".join(parts), True) if parts else ("", False)

    # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 该 try 块抛出多周期财务构建异常. upgrade: 策略层重构时统一走 classify_error.
    except Exception as e:
        logger.warning(
            "[ai_context] Failed to build multi-period financials for %s: %s", ts_code, DataSanitizer.sanitize_error(e)
        )
        if labels_out is not None:
            labels_out.clear()
        return ("", False)


def _format_forecast_section(df: pd.DataFrame) -> str:
    """格式化业绩预告段落（Phase 3A）。

    入参 df 由 ``get_fina_forecast_batch`` 返回，使用 ``DISTINCT ON (ts_code)``
    仅返回每只股票最新一期预告，故直接取 ``iloc[0]``。

    格式示例：``- 业绩预告: 2024Q3 预增 50.0%-70.0%（公告日 2024-10-15）``
    """
    if df is None or df.empty:
        return ""
    row = df.iloc[0]
    end_date = row.get("end_date")
    ann_date = row.get("ann_date")
    forecast_type = row.get("type") or I18n.get("ai_unknown")
    p_min = row.get("p_change_min")
    p_max = row.get("p_change_max")

    # end_date 为 Date 类型（季度末日期），转换为 "YYYYQN" 格式
    quarter_str = str(end_date) if end_date is not None else I18n.get("ai_unknown")
    try:
        d = pd.to_datetime(str(end_date))
        q = (d.month - 1) // 3 + 1
        quarter_str = f"{d.year}Q{q}"
    except (ValueError, TypeError):
        logger.warning("[ai_context] Failed to parse forecast end_date to quarter: %r", end_date)

    # 拼接预告幅度区间
    range_str = ""
    if p_min is not None and not pd.isna(p_min) and p_max is not None and not pd.isna(p_max):
        range_str = f" {float(p_min):.1f}%-{float(p_max):.1f}%"

    # 公告日格式化为 YYYY-MM-DD
    ann_str = str(ann_date) if ann_date is not None else I18n.get("ai_unknown")
    try:
        ann_str = pd.to_datetime(str(ann_date)).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        logger.warning("[ai_context] Failed to format forecast ann_date: %r", ann_date)

    return (
        f"- {I18n.get('ai_forecast')}: {quarter_str} {forecast_type}{range_str}"
        f"（{I18n.get('ai_forecast_ann_date')}: {ann_str}）"
    )


def _format_pledge_detail_section(df: pd.DataFrame) -> str:
    """格式化股权质押明细段落（Phase 3B）。

    入参 df 由 ``get_pledge_detail_batch`` 返回，使用 ``DISTINCT ON (ts_code)``
    仅返回每只股票最新一期明细，故直接取 ``iloc[0]``。

    格式示例：``- 质押明细: 质押股数 1000.00 万股（无限售 800.00，有限售 200.00），占总股本 35.2%``
    """
    if df is None or df.empty:
        return ""
    row = df.iloc[0]
    pledge_amount = row.get("pledge_amount")
    unlimited = row.get("unlimited_pledge_amount")
    limited = row.get("limited_pledge_amount")
    total_pledge = row.get("total_pledge_amount")
    pledge_ratio = row.get("pledge_ratio")

    parts: list[str] = []
    if pledge_amount is not None and not pd.isna(pledge_amount):
        parts.append(f"{I18n.get('ai_pledge_amount')}: {float(pledge_amount):.2f}")
    if total_pledge is not None and not pd.isna(total_pledge):
        parts.append(f"{I18n.get('ai_pledge_total')}: {float(total_pledge):.2f}")
    if unlimited is not None and not pd.isna(unlimited):
        parts.append(f"{I18n.get('ai_pledge_unlimited')}: {float(unlimited):.2f}")
    if limited is not None and not pd.isna(limited):
        parts.append(f"{I18n.get('ai_pledge_limited')}: {float(limited):.2f}")

    if not parts:
        return ""

    detail_str = "（" + "，".join(parts) + "）"
    ratio_str = (
        f"，{I18n.get('ai_pledge_ratio')} {float(pledge_ratio):.1f}%"
        if pledge_ratio is not None and not pd.isna(pledge_ratio)
        else ""
    )
    return f"- {I18n.get('ai_pledge_detail')}: {detail_str}{ratio_str}"


def _format_share_float_section(df: pd.DataFrame) -> str:
    """格式化限售解禁段落（Phase 3D）。

    入参 df 由 ``get_share_float_upcoming_batch`` 返回，包含未来解禁记录。
    最多展示 3 条最近解禁事件。

    格式示例：``- 限售解禁: 2024-08-15 解禁 1000.00 万股（5.2%）；2024-09-20 解禁 500.00 万股（2.6%）``
    """
    if df is None or df.empty:
        return ""
    items: list[str] = []
    for _, row in df.head(3).iterrows():
        float_date = row.get("float_date")
        float_share = row.get("float_share")
        float_ratio = row.get("float_ratio")
        if isinstance(float_date, (date, datetime)):
            date_str = float_date.strftime("%Y-%m-%d")
        else:
            date_str = str(float_date) if float_date is not None else "N/A"
        share_str = f"{float(float_share):.2f}" if float_share is not None and not pd.isna(float_share) else "N/A"
        ratio_str = f"（{float(float_ratio):.1f}%）" if float_ratio is not None and not pd.isna(float_ratio) else ""
        items.append(f"{date_str} 解禁 {share_str} 万股{ratio_str}")
    if not items:
        return ""
    return f"- {I18n.get('ai_share_float')}: " + "；".join(items)


def _format_holder_trade_section(df: pd.DataFrame) -> str:
    """格式化股东增减持段落（Phase 3E）。

    入参 df 由 ``get_stk_holdertrade_batch`` 返回，包含近期增减持记录。
    最多展示 3 条最近记录。

    格式示例：``- 股东增减持: 2024-06-01 张三 增持 100.00 万股（增持比例 0.5%）``
    """
    if df is None or df.empty:
        return ""
    recent = df.sort_values("ann_date", ascending=False).head(3)
    items: list[str] = []
    for _, row in recent.iterrows():
        ann_date = row.get("ann_date")
        date_str = str(ann_date) if ann_date is not None and not pd.isna(ann_date) else "N/A"
        holder_name = row.get("holder_name")
        name_str = str(holder_name) if holder_name is not None and not pd.isna(holder_name) else "N/A"
        in_de = row.get("in_de")
        if in_de == "IN":
            action_str = I18n.get("ai_holder_trade_increase")
        elif in_de == "DE":
            action_str = I18n.get("ai_holder_trade_decrease")
        else:
            action_str = "N/A"
        change_vol = row.get("change_vol")
        vol_str = f"{float(change_vol):.2f}" if change_vol is not None and not pd.isna(change_vol) else "N/A"
        change_ratio = row.get("change_ratio")
        ratio_str = f"（{float(change_ratio):.2f}%）" if change_ratio is not None and not pd.isna(change_ratio) else ""
        items.append(f"{date_str} {name_str} {action_str} {vol_str} 股{ratio_str}")
    if not items:
        return ""
    return f"- {I18n.get('ai_holder_trade')}: " + "；".join(items)


def _format_express_section(df: pd.DataFrame) -> str:
    """格式化业绩快报段落（Phase 3G §4.3.4）。

    入参 df 由 ``get_express_batch`` 返回，使用 ``DISTINCT ON (ts_code)``
    仅返回每只股票最新一期快报，故直接取 ``iloc[0]``。

    业绩快报早于正式财报 30-60 天公告，AI 可提前反应业绩拐点。
    营收/净利/扣非单位由元转换为亿元（÷1e8）保留 2 位小数。

    格式示例：``- 业绩快报: 2024Q3 营收 50.00亿（+25.0% YoY）、净利 8.00亿（+40.0% YoY）、扣非 7.50亿（+35.0% YoY）（公告日 2024-10-15）``
    """
    if df is None or df.empty:
        return ""
    row = df.iloc[0]
    end_date = row.get("end_date")
    ann_date = row.get("ann_date")

    # end_date 为 Date 类型（季度末日期），转换为 "YYYYQN" 格式
    quarter_str = str(end_date) if end_date is not None else I18n.get("ai_unknown")
    try:
        d = pd.to_datetime(str(end_date))
        q = (d.month - 1) // 3 + 1
        quarter_str = f"{d.year}Q{q}"
    except (ValueError, TypeError):
        logger.warning("[ai_context] Failed to parse express end_date to quarter: %r", end_date)

    # 拼接营收/净利/扣非段落（单位转换：元 → 亿元）
    parts: list[str] = []
    revenue = row.get("revenue")
    yoy_sales = row.get("yoy_sales")
    if revenue is not None and not pd.isna(revenue):
        rev_str = f"{I18n.get('ai_express_revenue')}: {float(revenue) / 1e8:.2f}{I18n.get('ai_billion_yuan')}"
        if yoy_sales is not None and not pd.isna(yoy_sales):
            rev_str += f"（{float(yoy_sales):+.1f}% YoY）"
        parts.append(rev_str)

    n_income = row.get("n_income")
    yoy_profit = row.get("yoy_profit")
    if n_income is not None and not pd.isna(n_income):
        ni_str = f"{I18n.get('ai_express_n_income')}: {float(n_income) / 1e8:.2f}{I18n.get('ai_billion_yuan')}"
        if yoy_profit is not None and not pd.isna(yoy_profit):
            ni_str += f"（{float(yoy_profit):+.1f}% YoY）"
        parts.append(ni_str)

    deduct_profit = row.get("deduct_profit")
    yoy_dedu_np = row.get("yoy_dedu_np")
    if deduct_profit is not None and not pd.isna(deduct_profit):
        dp_str = f"{I18n.get('ai_express_deduct')}: {float(deduct_profit) / 1e8:.2f}{I18n.get('ai_billion_yuan')}"
        if yoy_dedu_np is not None and not pd.isna(yoy_dedu_np):
            dp_str += f"（{float(yoy_dedu_np):+.1f}% YoY）"
        parts.append(dp_str)

    if not parts:
        return ""

    # 公告日格式化为 YYYY-MM-DD
    ann_str = str(ann_date) if ann_date is not None else I18n.get("ai_unknown")
    try:
        ann_str = pd.to_datetime(str(ann_date)).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        logger.warning("[ai_context] Failed to format express ann_date: %r", ann_date)

    return (
        f"- {I18n.get('ai_express')}: {quarter_str} {'、'.join(parts)}（{I18n.get('ai_express_ann_date')}: {ann_str}）"
    )


def _build_financials_text(row: dict, labels_out: list[str] | None = None) -> str:
    """
    Build a human-readable financials summary from the stock_info data.
    The screening data already contains key financial metrics from the join.

    Args:
        row: 筛选数据行（含 PE/PB/ROE 等估值指标）
        labels_out: 输出参数，收集成功注入的标签 key；异常时自动清空
    """
    try:
        parts = []

        parts.append(f"{I18n.get('ai_pe_ttm')}: {fmt_val(row.get('pe_ttm'))}")
        parts.append(f"{I18n.get('ai_pb')}: {fmt_val(row.get('pb'))}")
        parts.append(f"{I18n.get('ai_roe')}: {fmt_val(row.get('roe'), suffix='%')}")
        parts.append(f"{I18n.get('ai_gross_margin')}: {fmt_val(row.get('grossprofit_margin'), suffix='%')}")
        parts.append(f"{I18n.get('ai_debt_ratio')}: {fmt_val(row.get('debt_to_assets'), suffix='%')}")
        parts.append(f"{I18n.get('ai_revenue_yoy')}: {fmt_val(row.get('or_yoy'), suffix='%')}")
        parts.append(f"{I18n.get('ai_profit_yoy')}: {fmt_val(row.get('netprofit_yoy'), suffix='%')}")

        tmv = safe_float(row.get("total_mv"), default=None)  # type: ignore[union-attr]
        if tmv is not None:
            tmv_str = f"{tmv / 10000:.2f}{I18n.get('ai_billion_yuan')}"
        else:
            tmv_str = "N/A"
        parts.append(f"{I18n.get('ai_total_mv')}: {tmv_str}")

        parts.append(f"{I18n.get('ai_dividend_yield_ttm')}: {fmt_val(row.get('dv_ttm'), suffix='%')}")

        pe_val = safe_float(row.get("pe_ttm"), default=None)  # type: ignore[union-attr]
        growth_val = safe_float(row.get("netprofit_yoy"), default=None)  # type: ignore[union-attr]
        if pe_val is not None and growth_val is not None and growth_val > 0:
            peg = pe_val / growth_val
            parts.append(f"{I18n.get('ai_peg')}: {peg:.2f} ({I18n.get('ai_peg_pe_profit_growth')})")
        else:
            parts.append(I18n.get("ai_peg_na"))

        if parts and labels_out is not None:
            labels_out.append("ai_label_valuation")

        return "\n".join(parts)

    # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 该 try 块抛出财务文本构建异常. upgrade: 策略层重构时统一走 classify_error.
    except Exception as e:
        logger.warning("[ai_context] Failed to build financials text: %s", DataSanitizer.sanitize_error(e))
        if labels_out is not None:
            labels_out.clear()
        return I18n.get("ai_financial_insufficient")
