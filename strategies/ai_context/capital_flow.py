"""AI 资金流上下文渲染器（review01-A5a 自 ai_mixin 移出）。"""

from __future__ import annotations

import logging

import pandas as pd

from core.i18n import I18n
from data.constants import TOP_LIST_NET_AMOUNT_UNIT, get_column_unit
from strategies.ai_context.common import _build_stale_section
from strategies.utils import safe_float
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)


def _build_capital_flow_text(ts_code: str, prefetched: dict, labels_out: list[str] | None = None) -> str:
    """
    Build a human-readable capital flow summary from pre-fetched batch DataFrames.

    Args:
        ts_code: 股票代码
        prefetched: 预取的资金数据
        labels_out: 输出参数，收集成功注入的标签 key；异常时自动清空
    """
    try:
        sf = safe_float
        parts = []

        def format_amount(amount: float, source_unit: str) -> str:
            amount_yuan = amount * 10000 if source_unit == "wan_yuan" else amount
            abs_amount = abs(amount_yuan)
            if abs_amount >= 1e8:
                return f"{amount_yuan / 1e8:.2f}{I18n.get('ai_unit_billion')}"
            if abs_amount >= 1e4:
                return f"{amount_yuan / 1e4:.2f}{I18n.get('ai_unit_ten_thousand')}"
            return f"{amount_yuan:.0f}{I18n.get('ai_unit_yuan')}"

        mf_df = prefetched.get("moneyflow_df")
        if mf_df is not None and not mf_df.empty:
            stock_mf = mf_df[mf_df["ts_code"] == ts_code]
            if not stock_mf.empty:
                row = stock_mf.iloc[0]
                buy_lg = sf(row.get("buy_lg_amount"))
                sell_lg = sf(row.get("sell_lg_amount"))
                buy_elg = sf(row.get("buy_elg_amount"))
                sell_elg = sf(row.get("sell_elg_amount"))
                net_main = (buy_lg + buy_elg) - (sell_lg + sell_elg)
                net_total = sf(row.get("net_mf_amount"))
                parts.append(
                    f"{I18n.get('ai_main_net_inflow')}: {format_amount(net_main, 'wan_yuan')} ({I18n.get('ai_large_extra_large')})"
                )
                parts.append(f"{I18n.get('ai_total_net_inflow')}: {format_amount(net_total, 'wan_yuan')}")
                if labels_out is not None:
                    labels_out.append("ai_label_main_flow")
            else:
                parts.append(I18n.get("ai_stock_mf_no_record"))
        else:
            parts.append(I18n.get("ai_stock_mf_na"))

        tl_df = prefetched.get("top_list_df")
        if tl_df is not None and not tl_df.empty:
            stock_tl = tl_df[tl_df["ts_code"] == ts_code]
            if not stock_tl.empty:
                row = stock_tl.iloc[0]
                reason = row.get("reason")
                reason = reason if reason else "N/A"
                net_amt = sf(row.get("net_amount"))
                net_amount_unit = get_column_unit(tl_df, "net_amount", TOP_LIST_NET_AMOUNT_UNIT)
                parts.append(
                    f"{I18n.get('ai_top_list_yes')} ({I18n.get('ai_reason')}: {reason}, {I18n.get('ai_net_buy')}: {format_amount(net_amt, net_amount_unit)})"  # type: ignore[arg-type]
                )
                if labels_out is not None:
                    labels_out.append("ai_label_top_list")
            else:
                parts.append(I18n.get("ai_top_list_no"))
        else:
            parts.append(I18n.get("ai_top_list_na"))

        nb_df = prefetched.get("northbound_df")
        if nb_df is not None and not nb_df.empty:
            stock_nb = nb_df[nb_df["ts_code"] == ts_code]
            if not stock_nb.empty:
                row = stock_nb.iloc[0]
                vol = sf(row.get("vol"))
                ratio = sf(row.get("ratio"))
                parts.append(
                    f"{I18n.get('ai_north_holding')}: {vol:.0f}{I18n.get('ai_shares')}, {I18n.get('ai_circulating_ratio')}: {ratio:.2f}%"
                )
                if labels_out is not None:
                    labels_out.append("ai_label_northbound")
            else:
                parts.append(I18n.get("ai_north_no_record"))
        else:
            parts.append(I18n.get("ai_north_na"))

        # Phase 3C：top_inst 龙虎榜机构席位（auxiliary 数据，遵循 §4.4.5 stale 标注）
        # 与 top_list/northbound 不同：top_inst 是 Phase 3C 新增段落，按 §4.4.5 设计
        # 空 df 不注入占位文本（不污染 prompt）；非空但档位不覆盖时由 _build_stale_section 标注。
        ti_df = prefetched.get("top_inst_df")
        if ti_df is not None and not ti_df.empty:
            stock_ti = ti_df[ti_df["ts_code"] == ts_code]
            if not stock_ti.empty:

                def _format_top_inst(df: pd.DataFrame) -> str:
                    row = df.iloc[0]
                    net_buy = sf(row.get("net_buy"))
                    return (
                        f"{I18n.get('ai_top_inst_yes')} ({I18n.get('ai_net_buy')}: "
                        f"{format_amount(net_buy, TOP_LIST_NET_AMOUNT_UNIT)})"
                    )

                section = _build_stale_section("top_inst", stock_ti, _format_top_inst, date_column="trade_date")
                if section:
                    parts.append(section)
                    if labels_out is not None:
                        labels_out.append("ai_label_top_inst")

        return "\n".join(parts)

    # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 该 try 块抛出资金流文本构建异常. upgrade: 策略层重构时统一走 classify_error.
    except Exception as e:
        logger.warning(
            "[ai_context] Failed to build capital flow text for %s: %s",
            ts_code,
            DataSanitizer.sanitize_error(e),
        )
        if labels_out is not None:
            labels_out.clear()
        return I18n.get("ai_capital_flow_fetch_failed")
