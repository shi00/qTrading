"""AI 辅助数据上下文渲染器（review01-A5a 自 ai_mixin 移出）。

含审计意见、主营构成、分红记录、质押比例、股东信息、业绩预告/快报等辅助段落。
"""

from __future__ import annotations

import logging
import typing

import pandas as pd

from core.i18n import I18n
from strategies.ai_context.common import _build_stale_section
from strategies.ai_context.financials import (
    _format_express_section,
    _format_forecast_section,
    _format_holder_trade_section,
    _format_pledge_detail_section,
    _format_share_float_section,
)
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)


@log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
async def _build_auxiliary_data_text(
    ts_code: str,
    cache: typing.Any,
    prefetched: dict | None = None,
    as_of_date: str | None = None,
    labels_out: list[str] | None = None,
) -> tuple[str, bool]:
    """
    构建辅助数据文本。

    包含审计意见、主营构成、分红记录、质押比例、股东信息等辅助信息。

    Args:
        ts_code: 股票代码
        cache: 数据缓存实例
        prefetched: 预取的辅助数据（避免 N+1 查询）
        as_of_date: 截止日期（含），None 表示不限制，防止前视偏差
        labels_out: 输出参数，收集成功注入的标签 key

    Returns:
        (辅助数据文本, is_valid)：is_valid=False 表示无数据/异常，调用方应跳过注入。
    """

    lines = []
    has_data = False

    try:
        if prefetched and ts_code in prefetched and "audit" in prefetched[ts_code]:
            audit_df = prefetched[ts_code]["audit"]
        else:
            audit_df = await cache.get_fina_audit(ts_code, as_of_date=as_of_date)

        if audit_df is not None and not audit_df.empty:
            latest_audit = audit_df.iloc[0]
            audit_result = latest_audit.get("audit_result", I18n.get("ai_unknown"))
            lines.append(f"- {I18n.get('ai_audit_opinion')}: {audit_result}")
            has_data = True
            if labels_out is not None:
                labels_out.append("ai_label_audit")

        if prefetched and ts_code in prefetched and "mainbz" in prefetched[ts_code]:
            top_business = prefetched[ts_code]["mainbz"]
        else:
            top_business = await cache.get_fina_mainbz(ts_code, as_of_date=as_of_date)
        if top_business is not None and not top_business.empty:
            total_sales = top_business["bz_sales"].sum()
            if total_sales > 0:
                biz_items = []
                for row in top_business.head(3).to_dict("records"):
                    bz_name = row.get("bz_item", I18n.get("ai_unknown"))
                    bz_sales = row.get("bz_sales", 0)
                    ratio = (bz_sales / total_sales * 100) if total_sales > 0 else 0
                    biz_items.append(f"{bz_name}({ratio:.1f}%)")
                lines.append(f"- {I18n.get('ai_main_business')}: {', '.join(biz_items)}")
                has_data = True
                if labels_out is not None:
                    labels_out.append("ai_label_main_business")

        if prefetched and ts_code in prefetched and "dividend" in prefetched[ts_code]:
            dividend_df = prefetched[ts_code]["dividend"]
        else:
            dividend_df = await cache.get_dividend(ts_code, as_of_date=as_of_date)

        if dividend_df is not None and not dividend_df.empty:
            recent_div = dividend_df.head(3)
            div_items = []
            for row in recent_div.to_dict("records"):
                end_date = str(row.get("end_date", ""))[:4]
                div_proc = row.get("div_proc", "")
                div_items.append(f"{end_date}{I18n.get('ai_year_suffix')}{div_proc}")
            lines.append(f"- {I18n.get('ai_recent_dividend')}: {', '.join(div_items)}")
            has_data = True
            if labels_out is not None:
                labels_out.append("ai_label_dividend")

        if prefetched and ts_code in prefetched and "pledge" in prefetched[ts_code]:
            pledge_df = prefetched[ts_code]["pledge"]
        else:
            pledge_df = await cache.get_pledge_stat(ts_code, as_of_date=as_of_date)

        if pledge_df is not None and not pledge_df.empty:
            latest_pledge = pledge_df.iloc[0]
            pledge_ratio = latest_pledge.get("pledge_ratio", 0)
            if pledge_ratio and pledge_ratio > 0:
                warning = f" ⚠️ {I18n.get('ai_pledge_high_warning')}" if pledge_ratio > 30 else ""
                lines.append(f"- {I18n.get('ai_pledge_ratio')}: {pledge_ratio:.1f}%{warning}")
                has_data = True
                if labels_out is not None:
                    labels_out.append("ai_label_pledge")

        # Phase 3B：股权质押明细（pledge_detail）— 与 pledge_stat 互补，提供更细粒度的质押信息
        if prefetched and ts_code in prefetched and "pledge_detail" in prefetched[ts_code]:
            pledge_detail_df = prefetched[ts_code]["pledge_detail"]
        else:
            pledge_detail_df = await cache.get_pledge_detail(ts_code, as_of_date=as_of_date)

        if pledge_detail_df is not None and not pledge_detail_df.empty:
            pledge_detail_line = _build_stale_section(
                "pledge_detail",
                pledge_detail_df,
                _format_pledge_detail_section,
                date_column="end_date",
            )
            if pledge_detail_line:
                lines.append(pledge_detail_line)
                has_data = True
                if labels_out is not None:
                    labels_out.append("ai_label_pledge_detail")

        # Phase 3D：限售解禁（share_float）— 未来解禁压力，与 pledge_stat/pledge_detail 互补
        if prefetched and ts_code in prefetched and "share_float" in prefetched[ts_code]:
            share_float_df = prefetched[ts_code]["share_float"]
        else:
            share_float_df = await cache.get_share_float_upcoming(ts_code, as_of_date=as_of_date)

        if share_float_df is not None and not share_float_df.empty:
            share_float_line = _build_stale_section(
                "share_float",
                share_float_df,
                _format_share_float_section,
                date_column="ann_date",
            )
            if share_float_line:
                lines.append(share_float_line)
                has_data = True
                if labels_out is not None:
                    labels_out.append("ai_label_share_float")

        # Phase 3E：股东增减持（stk_holdertrade）— 产业资本信号，与 share_float 互补
        if prefetched and ts_code in prefetched and "holdertrade" in prefetched[ts_code]:
            holdertrade_df = prefetched[ts_code]["holdertrade"]
        else:
            holdertrade_df = await cache.get_stk_holdertrade(ts_code, as_of_date=as_of_date)

        if holdertrade_df is not None and not holdertrade_df.empty:
            holdertrade_line = _build_stale_section(
                "stk_holdertrade",
                holdertrade_df,
                _format_holder_trade_section,
                date_column="ann_date",
            )
            if holdertrade_line:
                lines.append(holdertrade_line)
                has_data = True
                if labels_out is not None:
                    labels_out.append("ai_label_holder_trade")

        if prefetched and ts_code in prefetched and "holders" in prefetched[ts_code]:
            holders_df = prefetched[ts_code]["holders"]
        else:
            holders_df = await cache.get_top10_holders(ts_code, as_of_date=as_of_date)

        if holders_df is not None and not holders_df.empty:
            if "ann_date" in holders_df.columns and holders_df["ann_date"].notna().any():
                latest_holders = holders_df[holders_df["ann_date"] == holders_df["ann_date"].max()]
            else:
                latest_holders = holders_df[holders_df["end_date"] == holders_df["end_date"].max()]
            if not latest_holders.empty:
                top_holder = latest_holders.iloc[0].get("holder_name", I18n.get("ai_unknown"))
                top_ratio = latest_holders.iloc[0].get("hold_ratio", 0)
                lines.append(
                    f"- {I18n.get('ai_top_holder')}: {top_holder} ({I18n.get('ai_holder_share')}{top_ratio:.2f}%)"
                )
                has_data = True
                if labels_out is not None:
                    labels_out.append("ai_label_top_holder")

        if prefetched and ts_code in prefetched and "holdernumber" in prefetched[ts_code]:
            holder_num = prefetched[ts_code]["holdernumber"]
        else:
            holder_num = await cache.get_stk_holdernumber(ts_code, as_of_date=as_of_date)
        if holder_num is not None and not holder_num.empty:
            if "ann_date" in holder_num.columns and holder_num["ann_date"].notna().any():
                latest_holder_num = holder_num[holder_num["ann_date"] == holder_num["ann_date"].max()]
                latest = latest_holder_num.iloc[0] if not latest_holder_num.empty else holder_num.iloc[0]
            else:
                latest = holder_num.iloc[0]
            curr_num = latest.get("holder_num", 0)
            change_pct = latest.get("holder_num_ratio")
            if curr_num:
                if change_pct is not None and not pd.isna(change_pct):
                    if change_pct < -5:
                        trend = f"↓ {I18n.get('ai_holder_concentrate')}"
                    elif change_pct > 5:
                        trend = f"↑ {I18n.get('ai_holder_disperse')}"
                    else:
                        trend = f"→ {I18n.get('ai_holder_stable')}"
                    lines.append(
                        f"- {I18n.get('ai_holder_count')}: {int(curr_num):,}{I18n.get('ai_households')} ({trend} {change_pct:+.1f}%)"
                    )
                else:
                    lines.append(f"- {I18n.get('ai_holder_count')}: {int(curr_num):,}{I18n.get('ai_households')}")
                has_data = True
                if labels_out is not None:
                    labels_out.append("ai_label_holder_count")

        # Phase 3A：业绩预告（fina_forecast）— 表已建 + DAO 读取已激活，注入 AI
        if prefetched and ts_code in prefetched and "forecast" in prefetched[ts_code]:
            forecast_df = prefetched[ts_code]["forecast"]
        else:
            forecast_df = await cache.get_fina_forecast(ts_code, as_of_date=as_of_date)

        if forecast_df is not None and not forecast_df.empty:
            forecast_line = _build_stale_section(
                "forecast",
                forecast_df,
                _format_forecast_section,
                date_column="ann_date",
            )
            if forecast_line:
                lines.append(forecast_line)
                has_data = True
                if labels_out is not None:
                    labels_out.append("ai_label_forecast")

        # Phase 3F-2：申万行业（sw_industry_member 全局快照，月度更新，无 stale 标注）
        # prefetched[ts_code]["sw_industry"] 为 sw_l2_name 字符串（cache_manager 已分发）
        # 注入前检查档位覆盖：points_120 降级时 index_classify/index_member_all 不在覆盖内，
        # filter_available_labels 已过滤 ai_label_sw_industry 标签；此处同步跳过 body 注入，
        # 避免 <available_data> 块不列但 prompt body 仍注入的设计矛盾。
        if prefetched and ts_code in prefetched and "sw_industry" in prefetched[ts_code]:
            sw_industry_name = prefetched[ts_code]["sw_industry"]
            if sw_industry_name:
                from data.external.tushare_client import TushareClient

                if TushareClient().is_api_covered_by_tier("index_classify"):
                    lines.append(f"- {I18n.get('ai_label_sw_industry')}: {sw_industry_name}")
                    has_data = True
                    if labels_out is not None:
                        labels_out.append("ai_label_sw_industry")

        # Phase 3G §4.3.4：业绩快报（express）— 早于正式财报 30-60 天，提前反应业绩拐点
        if prefetched and ts_code in prefetched and "express" in prefetched[ts_code]:
            express_df = prefetched[ts_code]["express"]
        else:
            express_df = await cache.get_express(ts_code, as_of_date=as_of_date)

        if express_df is not None and not express_df.empty:
            express_line = _build_stale_section(
                "express",
                express_df,
                _format_express_section,
                date_column="ann_date",
            )
            if express_line:
                lines.append(express_line)
                has_data = True
                if labels_out is not None:
                    labels_out.append("ai_label_express")

    # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 该 try 块抛出辅助数据文本构建异常. upgrade: 策略层重构时统一走 classify_error.
    except Exception as e:
        logger.warning(
            "[ai_context] Failed to build auxiliary data for %s: %s", ts_code, DataSanitizer.sanitize_error(e)
        )
        if labels_out is not None:
            labels_out.clear()
        return ("", False)

    if has_data:
        return ("\n".join(lines) + "\n", True)
    return ("", False)
