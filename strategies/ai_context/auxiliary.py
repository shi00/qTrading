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
            # M17-F10：audit_result 为 None/NaN/空串时该行无信息量（输出「审计意见: nan」
            # 或「未知」均为噪音），跳过该段而非注入占位文案。
            audit_result = audit_df.iloc[0].get("audit_result")
            if audit_result is not None and not pd.isna(audit_result) and str(audit_result).strip():
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
                # M17-F4：bz_sales 为 NaN/非正的行占比无意义（f"{nan:.1f}%" 会输出 "nan%"），
                # 跳过该行；bz_item 缺失时用 i18n 占位而非 str(nan)。全部无效时不注入空段落。
                biz_items = []
                for row in top_business.head(3).to_dict("records"):
                    bz_sales = row.get("bz_sales")
                    if bz_sales is None or pd.isna(bz_sales) or bz_sales <= 0:
                        continue
                    bz_name = row.get("bz_item")
                    if bz_name is None or pd.isna(bz_name):
                        bz_name = I18n.get("ai_unknown")
                    ratio = bz_sales / total_sales * 100
                    biz_items.append(f"{bz_name}({ratio:.1f}%)")
                if biz_items:
                    lines.append(f"- {I18n.get('ai_main_business')}: {', '.join(biz_items)}")
                    has_data = True
                    if labels_out is not None:
                        labels_out.append("ai_label_main_business")

        if prefetched and ts_code in prefetched and "dividend" in prefetched[ts_code]:
            dividend_df = prefetched[ts_code]["dividend"]
        else:
            dividend_df = await cache.get_dividend(ts_code, as_of_date=as_of_date)

        if dividend_df is not None and not dividend_df.empty:
            # M17-F10：end_date/div_proc 任一缺失的记录无法构成有意义的分红描述
            # （str(nan)[:4] → "nan年度nan"），跳过该条目；全部无效时不注入段落。
            div_items = []
            for row in dividend_df.head(3).to_dict("records"):
                end_date = row.get("end_date")
                div_proc = row.get("div_proc")
                if end_date is None or pd.isna(end_date) or div_proc is None or pd.isna(div_proc):
                    continue
                div_items.append(f"{str(end_date)[:4]}{I18n.get('ai_year_suffix')}{div_proc}")
            if div_items:
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
                # M2(review 730)：主时间维度为公告日 ann_date（同步/查询均按 ann_date），
                # end_date 为质押结束日（多为未来），作 stale 基准会显示错误日期。
                date_column="ann_date",
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
            # M17-F2/F10：ann_date/end_date 均缺失或全 NaN 时无法定位「最新期」
            # （缺列时原实现 KeyError 会炸掉整个辅助文本；全 NaN 时 NaN==NaN 恒 False
            # 过滤结果为空），两者都属「无法判定 as-of」，跳过该段而非崩溃。
            if "ann_date" in holders_df.columns and holders_df["ann_date"].notna().any():
                latest_holders = holders_df[holders_df["ann_date"] == holders_df["ann_date"].max()]
            elif "end_date" in holders_df.columns:
                latest_holders = holders_df[holders_df["end_date"] == holders_df["end_date"].max()]
            else:
                latest_holders = holders_df.head(0)
            if not latest_holders.empty:
                # M17-F2：holder_name/hold_ratio 为 NaN 时 str(nan)/f"{nan:.2f}%" 会输出
                # "nan" 文案；字段缺失时 get() 默认值不会生效（键存在值为 NaN）。
                # 任一字段有效即保留该行，无效字段降级为 N/A；两者皆无信息量则跳过。
                top_row = latest_holders.iloc[0]
                top_holder = top_row.get("holder_name")
                top_ratio = top_row.get("hold_ratio")
                holder_valid = top_holder is not None and pd.notna(top_holder)
                ratio_valid = top_ratio is not None and pd.notna(top_ratio)
                if holder_valid or ratio_valid:
                    holder_str = str(top_holder) if holder_valid else "N/A"
                    ratio_str = f"{top_ratio:.2f}%" if ratio_valid else "N/A"
                    lines.append(
                        f"- {I18n.get('ai_top_holder')}: {holder_str} ({I18n.get('ai_holder_share')}{ratio_str})"
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
            # M17-F3：curr_num 为 NaN 时原 `if curr_num:` 因 NaN 为 truthy 而进入
            # int(nan) → ValueError，炸掉整个辅助文本；显式 notna + >0 拦截（0 与
            # 原语义一致：无户数为 0 不展示）。
            if pd.notna(curr_num) and curr_num > 0:
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
